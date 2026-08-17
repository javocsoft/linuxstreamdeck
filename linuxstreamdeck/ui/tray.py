"""Status area icon (StatusNotifierItem) and its menu.

GTK 4 removed the old tray API, so the icon is published directly on D-Bus with
the two interfaces every modern status area speaks: `org.kde.StatusNotifierItem`
for the icon itself and `com.canonical.dbusmenu` for its menu. This needs no
dependency beyond the GIO bindings the application already uses, and works on
COSMIC, KDE and any GNOME session with an AppIndicator extension.

Despite living in `ui/`, this module creates no GTK widgets. D-Bus method calls
arrive on the thread-default main context captured at registration time, which
is the GTK main thread; every user action is still handed back through
`GLib.idle_add` so a callback can touch the UI without a second thought.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gio, GLib  # noqa: E402

from .. import APP_ID, APP_NAME

log = logging.getLogger(__name__)

WATCHER_NAME = "org.kde.StatusNotifierWatcher"
WATCHER_PATH = "/StatusNotifierWatcher"
ITEM_PATH = "/StatusNotifierItem"
MENU_PATH = "/MenuBar"

# Menu entry identifiers. Profile entries are allocated from PROFILE_ID_BASE so
# they never collide with the fixed ones, whatever the profile count is.
ROOT_ID = 0
OPEN_ID = 1
PROFILES_ID = 2
QUIT_ID = 3
SEPARATOR_BEFORE_PROFILES_ID = 4
SEPARATOR_BEFORE_QUIT_ID = 5
GAMES_ID = 6
MOLE_SMASH_ID = 7
STOP_GAME_ID = 8
PROFILE_ID_BASE = 100

# Status areas choose their closest representation rather than scaling one
# large bitmap. These cover compact and expanded panels without making every
# D-Bus property read needlessly large.
TRAY_ICON_SIZES = (16, 22, 24, 32, 48)
IconPixmap = tuple[int, int, bytes]

_ITEM_XML = """
<node><interface name="org.kde.StatusNotifierItem">
  <property name="Category" type="s" access="read"/>
  <property name="Id" type="s" access="read"/>
  <property name="Title" type="s" access="read"/>
  <property name="Status" type="s" access="read"/>
  <property name="WindowId" type="i" access="read"/>
  <property name="IconName" type="s" access="read"/>
  <property name="IconPixmap" type="a(iiay)" access="read"/>
  <property name="IconThemePath" type="s" access="read"/>
  <property name="OverlayIconName" type="s" access="read"/>
  <property name="OverlayIconPixmap" type="a(iiay)" access="read"/>
  <property name="AttentionIconName" type="s" access="read"/>
  <property name="AttentionIconPixmap" type="a(iiay)" access="read"/>
  <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
  <property name="ItemIsMenu" type="b" access="read"/>
  <property name="Menu" type="o" access="read"/>
  <method name="ContextMenu">
    <arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/>
  </method>
  <method name="Activate">
    <arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/>
  </method>
  <method name="SecondaryActivate">
    <arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/>
  </method>
  <method name="Scroll">
    <arg name="delta" type="i" direction="in"/>
    <arg name="orientation" type="s" direction="in"/>
  </method>
  <signal name="NewTitle"/>
  <signal name="NewIcon"/>
  <signal name="NewOverlayIcon"/>
  <signal name="NewAttentionIcon"/>
  <signal name="NewToolTip"/>
  <signal name="NewStatus"><arg name="status" type="s"/></signal>
</interface></node>
"""


def _icon_candidates(icon_name: str) -> tuple[Path, ...]:
    """Every location used by a source, Debian, Flatpak or AppImage run."""
    relative = (
        Path("icons") / "hicolor" / "scalable" / "apps" / f"{icon_name}.svg"
    )
    candidates = [
        Path(__file__).resolve().parents[2] / "packaging" / f"{icon_name}.svg"
    ]
    if appdir := os.environ.get("APPDIR"):
        appdir_path = Path(appdir)
        candidates.extend(
            (appdir_path / "usr" / "share" / relative, appdir_path / f"{icon_name}.svg")
        )
    candidates.extend(
        Path(directory) / relative
        for directory in (GLib.get_user_data_dir(), *GLib.get_system_data_dirs())
    )

    # XDG paths can repeat. Avoid decoding the same SVG more than once when an
    # earlier candidate exists but its loader rejects it.
    return tuple(dict.fromkeys(candidates))


def _argb32(pixbuf: GdkPixbuf.Pixbuf) -> bytes:
    """Serialize a GdkPixbuf as the network-order ARGB32 required by SNI."""
    width = pixbuf.get_width()
    height = pixbuf.get_height()
    channels = pixbuf.get_n_channels()
    if channels not in (3, 4):
        raise ValueError(f"Unsupported status icon channel count: {channels}")

    source = bytes(pixbuf.get_pixels())
    stride = pixbuf.get_rowstride()
    has_alpha = pixbuf.get_has_alpha()
    result = bytearray(width * height * 4)
    target = 0
    for y in range(height):
        row = y * stride
        for x in range(width):
            offset = row + x * channels
            red, green, blue = source[offset : offset + 3]
            alpha = source[offset + 3] if has_alpha else 255
            result[target : target + 4] = bytes((alpha, red, green, blue))
            target += 4
    return bytes(result)


def _visible_bounds(pixbuf: GdkPixbuf.Pixbuf) -> tuple[int, int, int, int]:
    """Return the smallest rectangle containing every non-transparent pixel."""
    width = pixbuf.get_width()
    height = pixbuf.get_height()
    channels = pixbuf.get_n_channels()
    if not pixbuf.get_has_alpha() or channels < 4:
        return 0, 0, width, height

    source = bytes(pixbuf.get_pixels())
    stride = pixbuf.get_rowstride()
    left, top, right, bottom = width, height, -1, -1
    for y in range(height):
        row = y * stride
        for x in range(width):
            if source[row + x * channels + 3] == 0:
                continue
            left = min(left, x)
            top = min(top, y)
            right = max(right, x)
            bottom = max(bottom, y)

    # A fully transparent asset is still valid input. Leaving its dimensions
    # alone is safer than asking GdkPixbuf for a zero-sized subpixbuf.
    if right < left or bottom < top:
        return 0, 0, width, height
    return left, top, right - left + 1, bottom - top + 1


def _fit_inside(width: int, height: int, size: int) -> tuple[int, int]:
    """Fit a rectangle within one status-area size without changing its shape."""
    scale = min(size / width, size / height)
    return max(1, round(width * scale)), max(1, round(height * scale))


@lru_cache(maxsize=4)
def _load_icon_pixmaps(
    icon_name: str, sizes: tuple[int, ...] = TRAY_ICON_SIZES
) -> tuple[IconPixmap, ...]:
    """Rasterize the application SVG for hosts that cannot resolve its name."""
    for icon_path in _icon_candidates(icon_name):
        if not icon_path.is_file():
            continue
        try:
            source = GdkPixbuf.Pixbuf.new_from_file(str(icon_path))
            x, y, width, height = _visible_bounds(source)
            visible = source.new_subpixbuf(x, y, width, height)
            pixmaps = []
            for size in sizes:
                target_width, target_height = _fit_inside(width, height, size)
                pixbuf = visible.scale_simple(
                    target_width, target_height, GdkPixbuf.InterpType.BILINEAR
                )
                pixmaps.append(
                    (pixbuf.get_width(), pixbuf.get_height(), _argb32(pixbuf))
                )
            return tuple(pixmaps)
        except (GLib.Error, OSError, ValueError):
            log.debug("Could not rasterize status icon %s", icon_path, exc_info=True)
    return ()

_MENU_XML = """
<node><interface name="com.canonical.dbusmenu">
  <property name="Version" type="u" access="read"/>
  <property name="TextDirection" type="s" access="read"/>
  <property name="Status" type="s" access="read"/>
  <property name="IconThemePath" type="as" access="read"/>
  <method name="GetLayout">
    <arg name="parentId" type="i" direction="in"/>
    <arg name="recursionDepth" type="i" direction="in"/>
    <arg name="propertyNames" type="as" direction="in"/>
    <arg name="revision" type="u" direction="out"/>
    <arg name="layout" type="(ia{sv}av)" direction="out"/>
  </method>
  <method name="GetGroupProperties">
    <arg name="ids" type="ai" direction="in"/>
    <arg name="propertyNames" type="as" direction="in"/>
    <arg name="properties" type="a(ia{sv})" direction="out"/>
  </method>
  <method name="GetProperty">
    <arg name="id" type="i" direction="in"/>
    <arg name="name" type="s" direction="in"/>
    <arg name="value" type="v" direction="out"/>
  </method>
  <method name="Event">
    <arg name="id" type="i" direction="in"/>
    <arg name="eventId" type="s" direction="in"/>
    <arg name="data" type="v" direction="in"/>
    <arg name="timestamp" type="u" direction="in"/>
  </method>
  <method name="EventGroup">
    <arg name="events" type="a(isvu)" direction="in"/>
    <arg name="idErrors" type="ai" direction="out"/>
  </method>
  <method name="AboutToShow">
    <arg name="id" type="i" direction="in"/>
    <arg name="needUpdate" type="b" direction="out"/>
  </method>
  <method name="AboutToShowGroup">
    <arg name="ids" type="ai" direction="in"/>
    <arg name="updatesNeeded" type="ai" direction="out"/>
    <arg name="idErrors" type="ai" direction="out"/>
  </method>
  <signal name="ItemsPropertiesUpdated">
    <arg name="updatedProps" type="a(ia{sv})"/>
    <arg name="removedProps" type="a(ias)"/>
  </signal>
  <signal name="LayoutUpdated">
    <arg name="revision" type="u"/><arg name="parent" type="i"/>
  </signal>
  <signal name="ItemActivationRequested">
    <arg name="id" type="i"/><arg name="timestamp" type="u"/>
  </signal>
</interface></node>
"""


def is_supported() -> bool:
    """Whether a status area able to show the icon is running right now."""
    try:
        connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        reply = connection.call_sync(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "NameHasOwner",
            GLib.Variant("(s)", (WATCHER_NAME,)),
            GLib.VariantType("(b)"),
            Gio.DBusCallFlags.NONE,
            2000,
            None,
        )
        return bool(reply[0])
    except GLib.Error:
        log.debug("Could not query the status notifier watcher", exc_info=True)
        return False


def menu_items(
    profiles: list[str],
    current_profile: int,
    game_active: bool = False,
) -> list[dict]:
    """Flat description of the menu, used to build the layout and for tests."""
    items: list[dict] = [
        {"id": OPEN_ID, "label": "Open"},
        {"id": SEPARATOR_BEFORE_PROFILES_ID, "separator": True},
        {
            "id": PROFILES_ID,
            "label": "Profile",
            "enabled": bool(profiles) and not game_active,
            "children": [
                {
                    "id": PROFILE_ID_BASE + index,
                    "label": name,
                    "toggle": "radio",
                    "toggled": index == current_profile,
                }
                for index, name in enumerate(profiles)
            ],
        },
        {
            "id": GAMES_ID,
            "label": "Games",
            "children": [
                {
                    "id": STOP_GAME_ID if game_active else MOLE_SMASH_ID,
                    "label": "Stop Mole Smash" if game_active else "Mole Smash",
                }
            ],
        },
        {"id": SEPARATOR_BEFORE_QUIT_ID, "separator": True},
        {"id": QUIT_ID, "label": "Quit"},
    ]
    return items


def _properties(item: dict) -> dict[str, GLib.Variant]:
    """D-Bus properties of one menu entry."""
    if item.get("separator"):
        return {
            "type": GLib.Variant("s", "separator"),
            "visible": GLib.Variant("b", True),
        }
    properties = {
        "label": GLib.Variant("s", item.get("label", "")),
        "enabled": GLib.Variant("b", item.get("enabled", True)),
        "visible": GLib.Variant("b", True),
    }
    if item.get("children"):
        properties["children-display"] = GLib.Variant("s", "submenu")
    if toggle := item.get("toggle"):
        properties["toggle-type"] = GLib.Variant("s", toggle)
        properties["toggle-state"] = GLib.Variant(
            "i", 1 if item.get("toggled") else 0
        )
    return properties


def _flatten(items: list[dict]):
    for item in items:
        yield item
        yield from _flatten(item.get("children", []))


def build_layout(
    items: list[dict], parent_id: int = ROOT_ID, depth: int = -1
) -> GLib.Variant:
    """Build the `(ia{sv}av)` layout a status area asks for."""
    if parent_id == ROOT_ID:
        children = items
        properties = {"children-display": GLib.Variant("s", "submenu")}
    else:
        match = next(
            (item for item in _flatten(items) if item["id"] == parent_id), None
        )
        if match is None:
            return GLib.Variant("(ia{sv}av)", (parent_id, {}, []))
        children = match.get("children", [])
        properties = _properties(match)
    if depth == 0:
        child_variants = []
    else:
        child_variants = [
            build_layout(items, child["id"], depth - 1) for child in children
        ]
    return GLib.Variant("(ia{sv}av)", (parent_id, properties, child_variants))


class TrayIcon:
    """Publishes the status icon and routes its menu back to the application."""

    def __init__(
        self,
        on_open: Callable[[], None],
        on_quit: Callable[[], None],
        on_select_profile: Callable[[int], None],
        profiles: Callable[[], tuple[list[str], int]],
        icon_name: str = APP_ID,
        on_start_game: Callable[[], None] | None = None,
        on_stop_game: Callable[[], None] | None = None,
        game_active: Callable[[], bool] | None = None,
    ) -> None:
        self._on_open = on_open
        self._on_quit = on_quit
        self._on_select_profile = on_select_profile
        self._profiles = profiles
        self._on_start_game = on_start_game or (lambda: None)
        self._on_stop_game = on_stop_game or (lambda: None)
        self._game_active = game_active or (lambda: False)
        self._icon_name = icon_name
        self._icon_pixmaps = _load_icon_pixmaps(icon_name)
        # Hosts are encouraged to prefer IconName when both forms exist. Keep
        # it empty when pixels are available so COSMIC cannot replace a valid
        # pixmap with a placeholder after a failed icon-theme lookup.
        self._published_icon_name = "" if self._icon_pixmaps else icon_name
        self._icon_pixmap_variant = GLib.Variant(
            "a(iiay)", self._icon_pixmaps
        )
        self._empty_pixmap_variant = GLib.Variant("a(iiay)", [])

        self._connection: Gio.DBusConnection | None = None
        self._item_registration = 0
        self._menu_registration = 0
        self._name_id = 0
        self._watch_id = 0
        self._bus_name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        self._revision = 1
        self._registered = False

    # ---------- lifecycle ----------

    def start(self) -> bool:
        """Export the icon and follow the status area across restarts."""
        try:
            self._connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            item_info = Gio.DBusNodeInfo.new_for_xml(_ITEM_XML).interfaces[0]
            menu_info = Gio.DBusNodeInfo.new_for_xml(_MENU_XML).interfaces[0]
            self._item_registration = self._connection.register_object(
                ITEM_PATH, item_info, self._on_item_call, self._item_property, None
            )
            self._menu_registration = self._connection.register_object(
                MENU_PATH, menu_info, self._on_menu_call, self._menu_property, None
            )
        except (GLib.Error, IndexError):
            log.warning("Could not export the status icon", exc_info=True)
            self.stop()
            return False

        self._name_id = Gio.bus_own_name_on_connection(
            self._connection,
            self._bus_name,
            Gio.BusNameOwnerFlags.NONE,
            None,
            None,
        )
        # Registering again whenever the watcher reappears keeps the icon alive
        # across a panel restart, and covers starting before the status area.
        self._watch_id = Gio.bus_watch_name_on_connection(
            self._connection,
            WATCHER_NAME,
            Gio.BusNameWatcherFlags.NONE,
            lambda *_a: self._register_with_watcher(),
            lambda *_a: self._on_watcher_vanished(),
        )
        return True

    def stop(self) -> None:
        self._registered = False
        if self._watch_id:
            Gio.bus_unwatch_name(self._watch_id)
            self._watch_id = 0
        if self._name_id:
            Gio.bus_unown_name(self._name_id)
            self._name_id = 0
        if self._connection is not None:
            for registration in (
                self._item_registration,
                self._menu_registration,
            ):
                if registration:
                    self._connection.unregister_object(registration)
            self._item_registration = self._menu_registration = 0
        self._connection = None

    @property
    def registered(self) -> bool:
        return self._registered

    def refresh(self) -> None:
        """Tell the status area the menu changed (profiles added or switched)."""
        if self._connection is None or not self._registered:
            return
        self._revision += 1
        try:
            self._connection.emit_signal(
                None,
                MENU_PATH,
                "com.canonical.dbusmenu",
                "LayoutUpdated",
                GLib.Variant("(ui)", (self._revision, ROOT_ID)),
            )
        except GLib.Error:
            log.debug("Could not publish the menu update", exc_info=True)

    # ---------- watcher registration ----------

    def _register_with_watcher(self) -> None:
        if self._connection is None:
            return
        # Asynchronous on purpose: this runs on the GTK main thread, and a slow
        # or wedged status area (very possible while a session is still logging
        # in) would otherwise freeze the window until the call timed out.
        self._connection.call(
            WATCHER_NAME,
            WATCHER_PATH,
            WATCHER_NAME,
            "RegisterStatusNotifierItem",
            GLib.Variant("(s)", (self._bus_name,)),
            None,
            Gio.DBusCallFlags.NONE,
            3000,
            None,
            self._on_registration_finished,
        )

    def _on_registration_finished(self, connection, result) -> None:
        try:
            connection.call_finish(result)
        except GLib.Error:
            self._registered = False
            log.warning("Could not register the status icon", exc_info=True)
            return
        self._registered = True
        log.info("Status icon registered as %s", self._bus_name)

    def _on_watcher_vanished(self) -> None:
        self._registered = False
        log.info("The status area went away; the icon will return with it")

    # ---------- StatusNotifierItem ----------

    def _item_property(self, _connection, _sender, _path, _interface, name):
        return {
            "Category": GLib.Variant("s", "ApplicationStatus"),
            "Id": GLib.Variant("s", APP_ID),
            "Title": GLib.Variant("s", APP_NAME),
            "Status": GLib.Variant("s", "Active"),
            "WindowId": GLib.Variant("i", 0),
            "IconName": GLib.Variant("s", self._published_icon_name),
            "IconPixmap": self._icon_pixmap_variant,
            "IconThemePath": GLib.Variant("s", ""),
            "OverlayIconName": GLib.Variant("s", ""),
            "OverlayIconPixmap": self._empty_pixmap_variant,
            "AttentionIconName": GLib.Variant("s", ""),
            "AttentionIconPixmap": self._empty_pixmap_variant,
            "ToolTip": GLib.Variant(
                "(sa(iiay)ss)",
                (
                    self._published_icon_name,
                    self._icon_pixmaps,
                    APP_NAME,
                    "Elgato Stream Deck controller",
                ),
            ),
            # A menu-only item: a plain click opens the menu instead of needing
            # a separate right click, which is what the status area expects.
            "ItemIsMenu": GLib.Variant("b", True),
            "Menu": GLib.Variant("o", MENU_PATH),
        }.get(name)

    def _on_item_call(
        self, _connection, _sender, _path, _interface, method, _params, invocation
    ):
        if method in ("Activate", "SecondaryActivate"):
            GLib.idle_add(self._invoke, self._on_open)
        invocation.return_value(None)

    # ---------- com.canonical.dbusmenu ----------

    def _menu_property(self, _connection, _sender, _path, _interface, name):
        return {
            "Version": GLib.Variant("u", 3),
            "TextDirection": GLib.Variant("s", "ltr"),
            "Status": GLib.Variant("s", "normal"),
            "IconThemePath": GLib.Variant("as", []),
        }.get(name)

    def _items(self) -> list[dict]:
        try:
            profiles, current = self._profiles()
        except Exception:
            log.debug("Could not read the profile list", exc_info=True)
            profiles, current = [], 0
        try:
            game_active = bool(self._game_active())
        except Exception:
            log.debug("Could not read the game state", exc_info=True)
            game_active = False
        return menu_items(profiles, current, game_active)

    def _on_menu_call(
        self, _connection, _sender, _path, _interface, method, params, invocation
    ):
        items = self._items()
        if method == "GetLayout":
            parent_id, depth = params[0], params[1]
            # The layout is already a variant, so the reply is assembled with
            # new_tuple: nesting it inside a format string would make PyGObject
            # try to rebuild it from its unpacked value.
            invocation.return_value(
                GLib.Variant.new_tuple(
                    GLib.Variant("u", self._revision),
                    build_layout(items, parent_id, depth),
                )
            )
        elif method == "GetGroupProperties":
            requested = list(params[0])
            invocation.return_value(
                GLib.Variant(
                    "(a(ia{sv}))",
                    (
                        [
                            (item["id"], _properties(item))
                            for item in _flatten(items)
                            if not requested or item["id"] in requested
                        ],
                    ),
                )
            )
        elif method == "GetProperty":
            item = next(
                (i for i in _flatten(items) if i["id"] == params[0]), None
            )
            value = _properties(item).get(params[1]) if item else None
            invocation.return_value(
                GLib.Variant("(v)", (value or GLib.Variant("s", ""),))
            )
        elif method == "Event":
            self._handle_event(params[0], params[1])
            invocation.return_value(None)
        elif method == "EventGroup":
            for event in params[0]:
                self._handle_event(event[0], event[1])
            invocation.return_value(GLib.Variant("(ai)", ([],)))
        elif method == "AboutToShow":
            invocation.return_value(GLib.Variant("(b)", (False,)))
        elif method == "AboutToShowGroup":
            invocation.return_value(GLib.Variant("(aiai)", ([], [])))
        else:
            invocation.return_value(None)

    def _handle_event(self, item_id: int, event_id: str) -> None:
        if event_id != "clicked":
            return
        if item_id == OPEN_ID:
            GLib.idle_add(self._invoke, self._on_open)
        elif item_id == QUIT_ID:
            GLib.idle_add(self._invoke, self._on_quit)
        elif item_id == MOLE_SMASH_ID:
            GLib.idle_add(self._invoke, self._on_start_game)
        elif item_id == STOP_GAME_ID:
            GLib.idle_add(self._invoke, self._on_stop_game)
        elif item_id >= PROFILE_ID_BASE:
            index = item_id - PROFILE_ID_BASE
            GLib.idle_add(self._invoke, self._on_select_profile, index)

    @staticmethod
    def _invoke(callback: Callable, *args) -> bool:
        try:
            callback(*args)
        except Exception:
            log.exception("A status icon action failed")
        return False

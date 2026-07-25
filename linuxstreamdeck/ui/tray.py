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

from gi.repository import Gio, GLib

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
PROFILE_ID_BASE = 100

_ITEM_XML = """
<node><interface name="org.kde.StatusNotifierItem">
  <property name="Category" type="s" access="read"/>
  <property name="Id" type="s" access="read"/>
  <property name="Title" type="s" access="read"/>
  <property name="Status" type="s" access="read"/>
  <property name="WindowId" type="i" access="read"/>
  <property name="IconName" type="s" access="read"/>
  <property name="IconThemePath" type="s" access="read"/>
  <property name="OverlayIconName" type="s" access="read"/>
  <property name="AttentionIconName" type="s" access="read"/>
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
  <signal name="NewToolTip"/>
  <signal name="NewStatus"><arg name="status" type="s"/></signal>
</interface></node>
"""

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


def menu_items(profiles: list[str], current_profile: int) -> list[dict]:
    """Flat description of the menu, used to build the layout and for tests."""
    items: list[dict] = [
        {"id": OPEN_ID, "label": f"Open {APP_NAME}"},
        {"id": SEPARATOR_BEFORE_PROFILES_ID, "separator": True},
        {
            "id": PROFILES_ID,
            "label": "Profile",
            "enabled": bool(profiles),
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
        {"id": SEPARATOR_BEFORE_QUIT_ID, "separator": True},
        {"id": QUIT_ID, "label": f"Quit {APP_NAME}"},
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
    ) -> None:
        self._on_open = on_open
        self._on_quit = on_quit
        self._on_select_profile = on_select_profile
        self._profiles = profiles
        self._icon_name = icon_name

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
            "IconName": GLib.Variant("s", self._icon_name),
            "IconThemePath": GLib.Variant("s", ""),
            "OverlayIconName": GLib.Variant("s", ""),
            "AttentionIconName": GLib.Variant("s", ""),
            "ToolTip": GLib.Variant(
                "(sa(iiay)ss)",
                (self._icon_name, [], APP_NAME, "Elgato Stream Deck controller"),
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
        return menu_items(profiles, current)

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

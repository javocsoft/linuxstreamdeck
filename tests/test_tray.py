from __future__ import annotations

import unittest

from unittest.mock import patch

from gi.repository import Gio, GLib

from linuxstreamdeck import APP_ID
from linuxstreamdeck.ui import tray

PROFILES = ["Streaming", "Podcast", "Gaming"]


class MenuItemTests(unittest.TestCase):
    def test_menu_offers_open_profiles_and_quit(self) -> None:
        items = tray.menu_items(PROFILES, 0)

        labels = [item.get("label") for item in items if not item.get("separator")]
        self.assertEqual(labels, ["Open", "Profile", "Quit"])

    def test_every_profile_becomes_a_radio_entry(self) -> None:
        items = tray.menu_items(PROFILES, 1)

        submenu = next(item for item in items if item["id"] == tray.PROFILES_ID)
        self.assertEqual([c["label"] for c in submenu["children"]], PROFILES)
        self.assertEqual(
            [c["toggled"] for c in submenu["children"]], [False, True, False]
        )
        self.assertTrue(all(c["toggle"] == "radio" for c in submenu["children"]))

    def test_profile_identifiers_never_clash_with_the_fixed_entries(self) -> None:
        items = tray.menu_items(PROFILES, 0)
        fixed = {
            tray.OPEN_ID,
            tray.QUIT_ID,
            tray.PROFILES_ID,
            tray.SEPARATOR_BEFORE_PROFILES_ID,
            tray.SEPARATOR_BEFORE_QUIT_ID,
        }

        profile_ids = {
            item["id"]
            for item in tray._flatten(items)
            if item["id"] >= tray.PROFILE_ID_BASE
        }
        self.assertEqual(len(profile_ids), len(PROFILES))
        self.assertFalse(profile_ids & fixed)

    def test_identifiers_are_unique(self) -> None:
        ids = [item["id"] for item in tray._flatten(tray.menu_items(PROFILES, 0))]

        self.assertEqual(len(ids), len(set(ids)))

    def test_the_profile_entry_is_disabled_without_profiles(self) -> None:
        items = tray.menu_items([], 0)

        submenu = next(item for item in items if item["id"] == tray.PROFILES_ID)
        self.assertFalse(submenu["enabled"])
        self.assertEqual(submenu["children"], [])

    def test_an_out_of_range_selection_marks_nothing(self) -> None:
        items = tray.menu_items(PROFILES, 99)

        submenu = next(item for item in items if item["id"] == tray.PROFILES_ID)
        self.assertFalse(any(c["toggled"] for c in submenu["children"]))


class PropertyTests(unittest.TestCase):
    def test_a_separator_declares_its_type(self) -> None:
        properties = tray._properties({"id": 1, "separator": True})

        self.assertEqual(properties["type"].unpack(), "separator")
        self.assertNotIn("label", properties)

    def test_a_submenu_declares_children_display(self) -> None:
        properties = tray._properties(
            {"id": 1, "label": "Profile", "children": [{"id": 2, "label": "A"}]}
        )

        self.assertEqual(properties["children-display"].unpack(), "submenu")

    def test_a_radio_entry_carries_its_state(self) -> None:
        on = tray._properties({"id": 1, "label": "A", "toggle": "radio", "toggled": True})
        off = tray._properties({"id": 2, "label": "B", "toggle": "radio"})

        self.assertEqual(on["toggle-type"].unpack(), "radio")
        self.assertEqual(on["toggle-state"].unpack(), 1)
        self.assertEqual(off["toggle-state"].unpack(), 0)

    def test_every_value_is_a_variant(self) -> None:
        properties = tray._properties({"id": 1, "label": "A"})

        self.assertTrue(
            all(isinstance(v, GLib.Variant) for v in properties.values())
        )


class IconPixmapTests(unittest.TestCase):
    class _Pixbuf:
        def __init__(
            self,
            pixels: bytes,
            width: int,
            height: int,
            channels: int,
            stride: int,
            alpha: bool,
        ) -> None:
            self._pixels = pixels
            self._width = width
            self._height = height
            self._channels = channels
            self._stride = stride
            self._alpha = alpha

        def get_pixels(self):
            return self._pixels

        def get_width(self):
            return self._width

        def get_height(self):
            return self._height

        def get_n_channels(self):
            return self._channels

        def get_rowstride(self):
            return self._stride

        def get_has_alpha(self):
            return self._alpha

    def test_rgba_is_serialized_as_network_order_argb_without_row_padding(self):
        pixbuf = self._Pixbuf(
            bytes(
                (
                    10,
                    20,
                    30,
                    40,
                    0,
                    0,
                    0,
                    0,
                    50,
                    60,
                    70,
                    80,
                    0,
                    0,
                    0,
                    0,
                )
            ),
            width=1,
            height=2,
            channels=4,
            stride=8,
            alpha=True,
        )

        self.assertEqual(
            tray._argb32(pixbuf),
            bytes((40, 10, 20, 30, 80, 50, 60, 70)),
        )

    def test_rgb_gets_an_opaque_alpha_channel(self):
        pixbuf = self._Pixbuf(
            bytes((10, 20, 30)),
            width=1,
            height=1,
            channels=3,
            stride=3,
            alpha=False,
        )

        self.assertEqual(tray._argb32(pixbuf), bytes((255, 10, 20, 30)))

    def test_application_svg_is_published_in_every_requested_size(self):
        pixmaps = tray._load_icon_pixmaps(APP_ID, (16, 24, 32))

        self.assertEqual(
            [max(width, height) for width, height, _data in pixmaps], [16, 24, 32]
        )
        self.assertTrue(all(width > 0 and height > 0 for width, height, _ in pixmaps))
        self.assertTrue(
            all(len(data) == width * height * 4 for width, height, data in pixmaps)
        )

    def test_application_svg_does_not_publish_transparent_outer_padding(self):
        for width, height, data in tray._load_icon_pixmaps(APP_ID, (16, 24, 32)):
            alpha = data[0::4]
            occupied_columns = {
                x
                for y in range(height)
                for x in range(width)
                if alpha[y * width + x]
            }
            occupied_rows = {
                y
                for y in range(height)
                for x in range(width)
                if alpha[y * width + x]
            }

            self.assertEqual(
                (min(occupied_columns), max(occupied_columns)), (0, width - 1)
            )
            self.assertEqual(
                (min(occupied_rows), max(occupied_rows)), (0, height - 1)
            )

    def test_pixmap_is_preferred_over_the_theme_name(self):
        icon = tray.TrayIcon(
            on_open=lambda: None,
            on_quit=lambda: None,
            on_select_profile=lambda _index: None,
            profiles=lambda: ([], 0),
        )

        icon_name = icon._item_property(None, None, None, None, "IconName")
        pixmaps = icon._item_property(None, None, None, None, "IconPixmap")
        tooltip = icon._item_property(None, None, None, None, "ToolTip")

        self.assertEqual(icon_name.unpack(), "")
        self.assertEqual(pixmaps.get_type_string(), "a(iiay)")
        self.assertEqual(len(pixmaps.unpack()), len(tray.TRAY_ICON_SIZES))
        self.assertEqual(len(tooltip.unpack()[1]), len(tray.TRAY_ICON_SIZES))

    def test_missing_asset_falls_back_to_the_theme_name(self):
        icon = tray.TrayIcon(
            on_open=lambda: None,
            on_quit=lambda: None,
            on_select_profile=lambda _index: None,
            profiles=lambda: ([], 0),
            icon_name="org.example.MissingStatusIcon",
        )

        icon_name = icon._item_property(None, None, None, None, "IconName")
        pixmaps = icon._item_property(None, None, None, None, "IconPixmap")

        self.assertEqual(icon_name.unpack(), "org.example.MissingStatusIcon")
        self.assertEqual(pixmaps.unpack(), [])


class LayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.items = tray.menu_items(PROFILES, 1)

    def test_layout_matches_the_dbusmenu_signature(self) -> None:
        layout = tray.build_layout(self.items)

        self.assertEqual(layout.get_type_string(), "(ia{sv}av)")

    def test_the_reply_tuple_matches_the_declared_signature(self) -> None:
        # GetLayout returns (u, layout); building it must not rebuild the
        # already-typed layout variant.
        reply = GLib.Variant.new_tuple(
            GLib.Variant("u", 1), tray.build_layout(self.items)
        )

        self.assertEqual(reply.get_type_string(), "(u(ia{sv}av))")

    def test_the_root_lists_every_top_level_entry(self) -> None:
        identifier, properties, children = tray.build_layout(self.items).unpack()

        self.assertEqual(identifier, tray.ROOT_ID)
        self.assertEqual(properties["children-display"], "submenu")
        self.assertEqual([child[0] for child in children], [i["id"] for i in self.items])

    def test_profiles_are_nested_under_their_entry(self) -> None:
        _identifier, _properties, children = tray.build_layout(self.items).unpack()

        submenu = next(c for c in children if c[0] == tray.PROFILES_ID)
        self.assertEqual([grandchild[0] for grandchild in submenu[2]], [100, 101, 102])
        self.assertEqual(
            [grandchild[1]["label"] for grandchild in submenu[2]], PROFILES
        )

    def test_a_depth_of_zero_returns_no_children(self) -> None:
        _identifier, _properties, children = tray.build_layout(
            self.items, tray.ROOT_ID, 0
        ).unpack()

        self.assertEqual(children, [])

    def test_a_submenu_can_be_requested_on_its_own(self) -> None:
        identifier, properties, children = tray.build_layout(
            self.items, tray.PROFILES_ID
        ).unpack()

        self.assertEqual(identifier, tray.PROFILES_ID)
        self.assertEqual(properties["label"], "Profile")
        self.assertEqual(len(children), len(PROFILES))

    def test_an_unknown_parent_returns_an_empty_entry(self) -> None:
        identifier, properties, children = tray.build_layout(self.items, 999).unpack()

        self.assertEqual((identifier, properties, children), (999, {}, []))


class EventRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.calls: list = []
        self.icon = tray.TrayIcon(
            on_open=lambda: self.calls.append("open"),
            on_quit=lambda: self.calls.append("quit"),
            on_select_profile=lambda index: self.calls.append(("profile", index)),
            profiles=lambda: (PROFILES, 0),
        )

    def _click(self, item_id: int, event: str = "clicked") -> None:
        # _handle_event defers through GLib.idle_add; run the callback directly
        # so the routing is verified without a main loop.
        self.icon._handle_event(item_id, event)
        context = GLib.MainContext.default()
        while context.pending():
            context.iteration(False)

    def test_open_is_routed(self) -> None:
        self._click(tray.OPEN_ID)

        self.assertEqual(self.calls, ["open"])

    def test_quit_is_routed(self) -> None:
        self._click(tray.QUIT_ID)

        self.assertEqual(self.calls, ["quit"])

    def test_a_profile_is_routed_with_its_index(self) -> None:
        self._click(tray.PROFILE_ID_BASE + 2)

        self.assertEqual(self.calls, [("profile", 2)])

    def test_other_events_are_ignored(self) -> None:
        self._click(tray.OPEN_ID, event="hovered")

        self.assertEqual(self.calls, [])

    def test_a_failing_callback_is_logged_instead_of_escaping(self) -> None:
        def explode() -> None:
            raise RuntimeError("boom")

        with self.assertLogs("linuxstreamdeck.ui.tray", level="ERROR"):
            self.assertFalse(tray.TrayIcon._invoke(explode))

    def test_refresh_is_a_no_op_before_registration(self) -> None:
        self.icon.refresh()  # must not raise without a connection

        self.assertFalse(self.icon.registered)

    def test_stop_is_safe_before_start(self) -> None:
        self.icon.stop()

        self.assertFalse(self.icon.registered)


def _session_bus():
    try:
        return Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except GLib.Error:
        return None


@unittest.skipUnless(_session_bus() is not None, "no session bus available")
class LateStatusAreaTests(unittest.TestCase):
    """A panel that starts after the application must still get the icon.

    This is the normal situation at session login, when the autostart entry
    launches LinuxStreamDeck while the desktop is still coming up.
    """

    WATCHER = "org.example.LinuxStreamDeckTestWatcher"
    XML = f"""
    <node><interface name="{WATCHER}">
      <method name="RegisterStatusNotifierItem">
        <arg name="service" type="s" direction="in"/>
      </method>
    </interface></node>
    """

    def setUp(self) -> None:
        self.connection = _session_bus()
        self.received: list[str] = []
        self.name_id = 0
        self.registration = 0
        self.icon: tray.TrayIcon | None = None
        patcher = patch.object(tray, "WATCHER_NAME", self.WATCHER)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        if self.icon is not None:
            self.icon.stop()
        if self.name_id:
            Gio.bus_unown_name(self.name_id)
        if self.registration:
            self.connection.unregister_object(self.registration)

    def _start_fake_status_area(self) -> None:
        def on_call(_c, _s, _p, _i, method, params, invocation):
            if method == "RegisterStatusNotifierItem":
                self.received.append(params[0])
            invocation.return_value(None)

        info = Gio.DBusNodeInfo.new_for_xml(self.XML).interfaces[0]
        self.registration = self.connection.register_object(
            tray.WATCHER_PATH, info, on_call, None, None
        )
        self.name_id = Gio.bus_own_name_on_connection(
            self.connection, self.WATCHER, Gio.BusNameOwnerFlags.NONE, None, None
        )

    def _run_until(self, predicate, timeout_seconds: float = 5.0) -> bool:
        context = GLib.MainContext.default()
        deadline = GLib.get_monotonic_time() + int(timeout_seconds * 1_000_000)
        while GLib.get_monotonic_time() < deadline:
            if predicate():
                return True
            context.iteration(False)
        return predicate()

    def test_icon_registers_when_the_status_area_appears_later(self) -> None:
        self.icon = tray.TrayIcon(
            on_open=lambda: None,
            on_quit=lambda: None,
            on_select_profile=lambda _index: None,
            profiles=lambda: (["General"], 0),
        )

        self.assertFalse(tray.is_supported())
        self.assertTrue(self.icon.start())
        self.assertFalse(self.icon.registered)

        self._start_fake_status_area()

        self.assertTrue(
            self._run_until(lambda: self.icon.registered),
            "the icon did not register once the status area appeared",
        )
        self.assertEqual(self.received, [self.icon._bus_name])

    def test_registration_does_not_block_the_main_thread(self) -> None:
        # A synchronous call here would deadlock against an in-process status
        # area, which is exactly how a slow panel would freeze the window.
        self.icon = tray.TrayIcon(
            on_open=lambda: None,
            on_quit=lambda: None,
            on_select_profile=lambda _index: None,
            profiles=lambda: ([], 0),
        )
        self.icon.start()
        self._start_fake_status_area()

        ticked = []
        GLib.idle_add(lambda: (ticked.append(True), False)[1])

        self.assertTrue(self._run_until(lambda: bool(ticked)))


if __name__ == "__main__":
    unittest.main()

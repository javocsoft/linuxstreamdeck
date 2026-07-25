from __future__ import annotations

import unittest
from types import SimpleNamespace

from linuxstreamdeck import basic_actions as _basic_actions  # noqa: F401
from linuxstreamdeck.core import actions as action_registry
from linuxstreamdeck.core.config import (
    ActionStep,
    KIND_MULTI,
    KIND_SINGLE,
    KIND_TOGGLE,
    Config,
    KeyConfig,
    Page,
)
from linuxstreamdeck.core.controller import DeckController
from linuxstreamdeck.core.events import EventBus
from linuxstreamdeck.ui.steps import StepEditor


class FakeController:
    def __init__(self) -> None:
        self.config = Config()
        self.config.pages.extend((Page(name="Page 2"), Page(name="Page 3")))
        self.selected = []

    @property
    def current_page(self) -> int:
        return self.config.current_page

    def set_page(self, index: int) -> None:
        self.config.current_page = index
        self.selected.append(index)

    def set_page_by_name(self, name: str) -> bool:
        for index, page in enumerate(self.config.pages):
            if page.name == name:
                self.set_page(index)
                return True
        return False


class PageNavigationActionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = FakeController()
        self.bus = EventBus()
        self.context = SimpleNamespace(
            controller=self.controller,
            bus=self.bus,
        )

    def test_navigation_category_exposes_the_explicit_page_actions(self) -> None:
        identifiers = [
            action.id for action in action_registry.by_category()["Navigation"]
        ]

        self.assertEqual(
            identifiers[:3],
            ["nav.page.next", "nav.page.previous", "nav.page.go"],
        )
        # The legacy combined action must never come back into the catalogue.
        self.assertNotIn("nav.page", identifiers)
        go_to = action_registry.get("nav.page.go")
        self.assertEqual(go_to.params[0].choices_source, "pages")
        self.assertEqual(go_to.params[0].label, "Destination page")

    def test_next_and_previous_wrap_at_page_boundaries(self) -> None:
        self.controller.config.current_page = 2
        action_registry.get("nav.page.next").execute(self.context, {})
        self.assertEqual(self.controller.current_page, 0)

        action_registry.get("nav.page.previous").execute(self.context, {})
        self.assertEqual(self.controller.current_page, 2)
        self.assertEqual(self.controller.selected, [0, 2])

    def test_go_to_page_uses_the_selected_page_name(self) -> None:
        action_registry.get("nav.page.go").execute(
            self.context,
            {"page": "Page 2"},
        )

        self.assertEqual(self.controller.current_page, 1)

    def test_page_selector_reads_the_current_profile_pages(self) -> None:
        editor = SimpleNamespace(
            app=SimpleNamespace(
                config=self.controller.config,
                obs=SimpleNamespace(connected=False),
            ),
        )

        self.assertEqual(
            StepEditor._fetch_choices(editor, "pages"),
            ["Page 1", "Page 2", "Page 3"],
        )

    def test_missing_destination_reports_a_status_message(self) -> None:
        messages = []
        self.bus.subscribe(
            "status",
            lambda _topic, data: messages.append(data["text"]),
        )

        action_registry.get("nav.page.go").execute(
            self.context,
            {"page": "Removed page"},
        )

        self.assertEqual(messages, ["Page not found: Removed page"])


class LegacyPageNavigationMigrationTests(unittest.TestCase):
    def test_single_next_page_action_is_migrated(self) -> None:
        key = KeyConfig.from_dict({
            "kind": KIND_SINGLE,
            "action": "nav.page",
            "params": {"mode": "next", "page": "Page 3"},
        })

        self.assertEqual(key.action, "nav.page.next")
        self.assertEqual(key.params, {})

    def test_multi_and_toggle_page_actions_are_migrated(self) -> None:
        key = KeyConfig.from_dict({
            "kind": KIND_MULTI,
            "steps": [{
                "action": "nav.page",
                "params": {"mode": "previous"},
            }],
            "steps_on": [{
                "action": "nav.page",
                "params": {"mode": "go to", "page": "Page 2"},
            }],
            "steps_off": [{
                "action": "nav.page",
                "params": {"mode": "next"},
            }],
        })

        self.assertEqual(key.steps[0].action, "nav.page.previous")
        self.assertEqual(key.steps_on[0].action, "nav.page.go")
        self.assertEqual(key.steps_on[0].params, {"page": "Page 2"})
        self.assertEqual(key.steps_off[0].action, "nav.page.next")

    def test_toggle_kind_value_is_unchanged_by_action_migration(self) -> None:
        key = KeyConfig.from_dict({
            "kind": KIND_TOGGLE,
            "steps_on": [{
                "action": "nav.page",
                "params": {"mode": "next"},
            }],
        })

        self.assertEqual(key.kind, KIND_TOGGLE)


class PageNavigationReferenceTests(unittest.TestCase):
    def test_renaming_a_page_updates_go_to_references_in_the_profile(self) -> None:
        config = Config()
        config.pages.extend((Page(name="Page 2"), Page(name="Page 3")))
        config.pages[0].set_key(
            0,
            KeyConfig(
                kind=KIND_SINGLE,
                action="nav.page.go",
                params={"page": "Page 2"},
            ),
        )
        config.pages[1].set_key(
            1,
            KeyConfig(
                kind=KIND_MULTI,
                steps=[
                    ActionStep(
                        action="nav.page.go",
                        params={"page": "Page 2"},
                    ),
                    ActionStep(action="nav.page.next"),
                ],
            ),
        )
        config.current_page = 1
        config.save = lambda: None
        bus = EventBus()
        changes = []
        bus.subscribe(
            "page.changed",
            lambda _topic, data: changes.append(data["name"]),
        )
        controller = SimpleNamespace(
            config=config,
            page=config.pages[1],
            current_page=1,
            bus=bus,
        )

        DeckController.rename_page(controller, "Studio")

        self.assertEqual(
            config.pages[0].key(0).params["page"],
            "Studio",
        )
        self.assertEqual(
            config.pages[1].key(1).steps[0].params["page"],
            "Studio",
        )
        self.assertEqual(changes, ["Studio"])

    def test_duplicate_page_names_are_rejected_for_unambiguous_targets(self) -> None:
        config = Config()
        config.pages.append(Page(name="Page 2"))
        config.save = lambda: self.fail("Duplicate page names must not be saved")
        bus = EventBus()
        messages = []
        bus.subscribe(
            "status",
            lambda _topic, data: messages.append(data["text"]),
        )
        controller = SimpleNamespace(
            config=config,
            page=config.pages[0],
            current_page=0,
            bus=bus,
        )

        DeckController.add_page(controller, "Page 2")
        DeckController.rename_page(controller, "Page 2")

        self.assertEqual(
            [page.name for page in config.pages],
            ["Page 1", "Page 2"],
        )
        self.assertEqual(
            messages,
            [
                "A page named Page 2 already exists",
                "A page named Page 2 already exists",
            ],
        )


if __name__ == "__main__":
    unittest.main()

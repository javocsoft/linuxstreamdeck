"""The keys a brand new configuration is offered.

A fresh install used to open on an empty deck and an editor to decipher. What
makes these keys worth offering is that every one of them works the moment it
arrives, so the rule they all have to pass is that none needs configuring.
"""

from __future__ import annotations

import unittest

from linuxstreamdeck import basic_actions as _basic_actions  # noqa: F401
from linuxstreamdeck.core import actions as action_registry
from linuxstreamdeck.core.config import KIND_SINGLE, Config, KeyConfig
from linuxstreamdeck.core.starter import (
    STARTER_KEYS,
    apply_starter_keys,
    is_first_run,
    starter_keys,
)
from linuxstreamdeck.obs import actions as _obs_actions  # noqa: F401


class StarterKeyTests(unittest.TestCase):
    def test_every_action_exists(self) -> None:
        for action_id, _params, _label in STARTER_KEYS:
            with self.subTest(action=action_id):
                self.assertIsNotNone(action_registry.get(action_id))

    def test_none_of_them_needs_configuring(self) -> None:
        """The whole selection rule: a key that arrives broken teaches nothing.

        A parameter passes when the starter key fills it in, when the action
        gives it a usable default, or when it carries a `placeholder` — which
        is exactly the marker for a blank that means something rather than a
        blank that is unfinished. A chapter with no name is numbered by OBS,
        and a blank disk folder is the home folder.
        """
        for action_id, params, _label in STARTER_KEYS:
            action = action_registry.get(action_id)
            for param in action.params:
                with self.subTest(action=action_id, param=param.name):
                    self.assertTrue(
                        param.name in params
                        or param.default not in (None, "")
                        or bool(param.placeholder),
                        f"{action_id}.{param.name} would arrive unset",
                    )

    def test_every_chosen_value_is_one_the_action_offers(self) -> None:
        for action_id, params, _label in STARTER_KEYS:
            action = action_registry.get(action_id)
            declared = {p.name: p for p in action.params}
            for name, value in params.items():
                with self.subTest(action=action_id, param=name):
                    self.assertIn(name, declared)
                    if declared[name].choices:
                        self.assertIn(value, declared[name].choices)

    def test_every_key_is_labelled(self) -> None:
        """An icon-only starter key would say nothing about what it is."""
        for _action_id, _params, label in STARTER_KEYS:
            self.assertTrue(label.strip())

    def test_they_are_single_action_keys(self) -> None:
        for key in starter_keys(15).values():
            self.assertEqual(key.kind, KIND_SINGLE)

    def test_a_full_size_deck_gets_them_all(self) -> None:
        self.assertEqual(len(starter_keys(15)), len(STARTER_KEYS))

    def test_a_small_deck_gets_only_what_fits(self) -> None:
        """A Mini has six keys; storing more would be invisible state."""
        keys = starter_keys(6)

        self.assertEqual(len(keys), 6)
        self.assertEqual(max(keys), 5)

    def test_a_deck_with_no_keys_gets_nothing(self) -> None:
        self.assertEqual(starter_keys(0), {})

    def test_they_start_at_the_first_slot(self) -> None:
        self.assertEqual(min(starter_keys(15)), 0)

    def test_the_most_wanted_key_comes_first(self) -> None:
        """A small deck takes the first few, so the order is the priority."""
        self.assertEqual(STARTER_KEYS[0][0], "obs.record")

    def test_each_key_gets_its_own_parameters(self) -> None:
        """Two statistics keys must not share one dict."""
        keys = list(starter_keys(15).values())

        keys[0].params["poisoned"] = True

        self.assertFalse(any("poisoned" in k.params for k in keys[1:]))
        self.assertNotIn("poisoned", STARTER_KEYS[0][1])


class ApplyStarterKeyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = Config()

    def test_it_fills_an_empty_page(self) -> None:
        added = apply_starter_keys(self.config, 15)

        self.assertEqual(added, len(STARTER_KEYS))
        self.assertEqual(
            self.config.pages[0].configured_keys(), len(STARTER_KEYS)
        )

    def test_it_refuses_a_page_that_already_holds_something(self) -> None:
        """The answer can arrive long after the question was asked."""
        self.config.pages[0].set_key(
            3, KeyConfig(kind=KIND_SINGLE, action="sys.stopwatch")
        )

        added = apply_starter_keys(self.config, 15)

        self.assertEqual(added, 0)
        self.assertEqual(self.config.pages[0].configured_keys(), 1)

    def test_it_saves_what_it_added(self) -> None:
        from linuxstreamdeck.core.config import CONFIG_FILE

        apply_starter_keys(self.config, 15)

        self.assertTrue(CONFIG_FILE.exists())

    def test_a_deck_with_no_room_saves_nothing(self) -> None:
        self.assertEqual(apply_starter_keys(self.config, 0), 0)


class FirstRunTests(unittest.TestCase):
    def test_it_is_a_first_run_when_no_configuration_was_ever_written(self) -> None:
        from linuxstreamdeck.core import config as config_module

        original = config_module.CONFIG_FILE
        try:
            config_module.CONFIG_FILE = original.with_name("never-written.json")
            import linuxstreamdeck.core.starter as starter_module

            starter_module.CONFIG_FILE = config_module.CONFIG_FILE
            self.assertTrue(is_first_run())
        finally:
            config_module.CONFIG_FILE = original
            import linuxstreamdeck.core.starter as starter_module

            starter_module.CONFIG_FILE = original

    def test_an_existing_configuration_is_not_a_first_run(self) -> None:
        Config().save()

        self.assertFalse(is_first_run())


if __name__ == "__main__":
    unittest.main()

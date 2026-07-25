from __future__ import annotations

import json
import unittest

from linuxstreamdeck import basic_actions as _basic_actions  # noqa: F401
from linuxstreamdeck.ai.service import (
    AIResponseError,
    AIService,
    ANTHROPIC_URL,
    MAX_STEP_LABEL_CHARS,
    OPENAI_URL,
    _STEP_SCHEMA,
    _action_catalog,
    collect_generation_context,
    format_proposal,
)
from linuxstreamdeck.core.config import Config, KIND_SINGLE
from linuxstreamdeck.obs import actions as _obs_actions  # noqa: F401


def proposal(
    action: str = "obs.scene_switch",
    parameters: list[dict[str, str]] | None = None,
) -> dict:
    return {
        "kind": "single",
        "summary": "Switch to the Live scene.",
        "steps": [{
            "action": action,
            "parameters": parameters or [{"name": "scene", "value": "Live"}],
        }],
        "steps_on": [],
        "steps_off": [],
        "label": "Live",
        "icon": "",
        "bg_color": "#1e1e28",
        "label_off": "",
        "icon_off": "",
        "bg_color_off": "#1e1e28",
    }


class FakeHTTP:
    def __init__(self, value: dict) -> None:
        self.value = value
        self.calls: list[tuple[str, dict, dict, float]] = []

    def __call__(self, url, headers, payload, timeout):
        self.calls.append((url, headers, payload, timeout))
        return self.value


class FakeOBS:
    connected = True

    class State:
        current_scene = "Live"

    state = State()

    def get_scenes(self):
        return ["Starting Soon", "Live"]

    def get_inputs(self):
        return ["Mic/Aux"]

    def get_media_inputs(self):
        return ["Intro"]

    def get_transitions(self):
        return ["Fade"]

    def get_scene_collections(self):
        return ["Streaming"]

    def get_profiles(self):
        return ["Default"]

    def get_hotkeys(self):
        return ["OBSBasic.StartStreaming"]

    def get_sources_in_scene(self, scene):
        return [f"{scene} Camera"]


class AIServiceTests(unittest.TestCase):
    def test_openai_uses_strict_non_stored_tool_call(self) -> None:
        raw = proposal()
        http = FakeHTTP({
            "output": [{
                "type": "function_call",
                "name": "propose_key_configuration",
                "arguments": json.dumps(raw),
            }],
        })
        generated = AIService(http_post=http).generate(
            provider="openai",
            model="gpt-test",
            api_key="secret-openai-key",
            prompt="Switch to Live",
            context={"scenes": ["Live"]},
        )

        self.assertEqual(generated.key.kind, KIND_SINGLE)
        self.assertEqual(generated.key.action, "obs.scene_switch")
        self.assertEqual(generated.key.params, {"scene": "Live"})
        url, headers, payload, _timeout = http.calls[0]
        self.assertEqual(url, OPENAI_URL)
        self.assertEqual(headers["Authorization"], "Bearer secret-openai-key")
        self.assertFalse(payload["store"])
        self.assertTrue(payload["tools"][0]["strict"])
        self.assertNotIn("secret-openai-key", json.dumps(payload))

    def test_claude_uses_strict_forced_tool_call(self) -> None:
        raw = proposal()
        http = FakeHTTP({
            "content": [{
                "type": "tool_use",
                "name": "propose_key_configuration",
                "input": raw,
            }],
        })
        generated = AIService(http_post=http).generate(
            provider="anthropic",
            model="claude-test",
            api_key="secret-claude-key",
            prompt="Switch to Live",
            context={"scenes": ["Live"]},
        )

        self.assertEqual(generated.key.action, "obs.scene_switch")
        url, headers, payload, _timeout = http.calls[0]
        self.assertEqual(url, ANTHROPIC_URL)
        self.assertEqual(headers["x-api-key"], "secret-claude-key")
        self.assertEqual(
            payload["tool_choice"],
            {"type": "tool", "name": "propose_key_configuration"},
        )
        self.assertTrue(payload["tools"][0]["strict"])
        self.assertNotIn("secret-claude-key", json.dumps(payload))

    def test_blocked_actions_are_rejected(self) -> None:
        raw = proposal(
            action="sys.command",
            parameters=[{"name": "command", "value": "touch /tmp/unsafe"}],
        )
        http = FakeHTTP({
            "content": [{
                "type": "tool_use",
                "name": "propose_key_configuration",
                "input": raw,
            }],
        })
        with self.assertRaisesRegex(AIResponseError, "unavailable action"):
            AIService(http_post=http).generate(
                "anthropic",
                "claude-test",
                "secret",
                "Run a command",
            )

    def test_blocked_actions_are_not_sent_to_providers(self) -> None:
        catalog = _action_catalog({})
        action_ids = {item["id"] for item in catalog}

        self.assertNotIn("sys.command", action_ids)
        self.assertNotIn("obs.raw", action_ids)
        self.assertIn("obs.scene_switch", action_ids)
        self.assertIn("nav.page.next", action_ids)
        self.assertIn("nav.page.previous", action_ids)
        self.assertIn("nav.page.go", action_ids)
        self.assertIn("sys.timer", action_ids)
        self.assertIn("sys.stopwatch", action_ids)
        self.assertNotIn("nav.page", action_ids)

    def test_context_choices_are_validated(self) -> None:
        raw = proposal()
        http = FakeHTTP({
            "output": [{
                "type": "function_call",
                "name": "propose_key_configuration",
                "arguments": json.dumps(raw),
            }],
        })
        with self.assertRaisesRegex(AIResponseError, "not an available value"):
            AIService(http_post=http).generate(
                "openai",
                "gpt-test",
                "secret",
                "Switch scene",
                context={"scenes": ["Starting Soon"]},
            )

    def test_unknown_built_in_icon_is_rejected(self) -> None:
        raw = {
            **proposal(),
            "icon": "mdi:this-icon-does-not-exist",
        }
        http = FakeHTTP({
            "output": [{
                "type": "function_call",
                "name": "propose_key_configuration",
                "arguments": json.dumps(raw),
            }],
        })

        with self.assertRaisesRegex(AIResponseError, "does not exist"):
            AIService(http_post=http).generate(
                "openai",
                "gpt-test",
                "secret",
                "Switch scene",
                context={"scenes": ["Live"]},
            )

    def test_wait_duration_is_normalized(self) -> None:
        raw = {
            **proposal(),
            "kind": "multi",
            "summary": "Switch, wait, then start streaming.",
            "steps": [
                {
                    "action": "obs.scene_switch",
                    "parameters": [{"name": "scene", "value": "Live"}],
                },
                {
                    "action": "sys.wait",
                    "parameters": [{"name": "duration", "value": "5"}],
                },
                {"action": "obs.stream", "parameters": []},
            ],
        }
        http = FakeHTTP({
            "content": [{
                "type": "tool_use",
                "name": "propose_key_configuration",
                "input": raw,
            }],
        })

        generated = AIService(http_post=http).generate(
            "anthropic",
            "claude-test",
            "secret",
            "Switch to Live, wait five seconds, then stream",
            context={"scenes": ["Live"]},
        )

        self.assertEqual(generated.key.steps[1].params["duration"], "00:05")

    def test_audio_parameters_are_validated_and_optional_duration_stays_empty(
        self,
    ) -> None:
        raw = proposal(
            action="sys.audio",
            parameters=[
                {"name": "file", "value": "/home/user/sound.mp3"},
                {"name": "volume", "value": "75"},
                {"name": "duration", "value": ""},
            ],
        )
        http = FakeHTTP({
            "output": [{
                "type": "function_call",
                "name": "propose_key_configuration",
                "arguments": json.dumps(raw),
            }],
        })

        generated = AIService(http_post=http).generate(
            "openai",
            "gpt-test",
            "secret",
            "Play my sound",
        )

        self.assertEqual(generated.key.params["volume"], 75)
        self.assertEqual(generated.key.params["duration"], "")

    def test_audio_volume_outside_the_ui_range_is_rejected(self) -> None:
        raw = proposal(
            action="sys.audio",
            parameters=[
                {"name": "file", "value": "/home/user/sound.wav"},
                {"name": "volume", "value": "101"},
                {"name": "duration", "value": "00:05"},
            ],
        )
        http = FakeHTTP({
            "content": [{
                "type": "tool_use",
                "name": "propose_key_configuration",
                "input": raw,
            }],
        })

        with self.assertRaisesRegex(AIResponseError, "must be at most 100"):
            AIService(http_post=http).generate(
                "anthropic",
                "claude-test",
                "secret",
                "Play my sound",
            )

    def test_timer_parameters_are_validated(self) -> None:
        raw = proposal(
            action="sys.timer",
            parameters=[
                {"name": "duration", "value": "1:02"},
                {"name": "sound", "value": "/home/user/finished.wav"},
                {"name": "volume", "value": "40"},
            ],
        )
        http = FakeHTTP({
            "output": [{
                "type": "function_call",
                "name": "propose_key_configuration",
                "arguments": json.dumps(raw),
            }],
        })

        generated = AIService(http_post=http).generate(
            "openai",
            "gpt-test",
            "secret",
            "Create a timer",
        )

        self.assertEqual(generated.key.params["duration"], "01:02")
        self.assertEqual(generated.key.params["volume"], 40)

    def test_context_is_bounded_and_contains_no_credentials(self) -> None:
        config = Config()
        context = collect_generation_context(config, FakeOBS())
        self.assertEqual(context["scenes"], ["Starting Soon", "Live"])
        self.assertEqual(context["inputs"], ["Mic/Aux"])
        self.assertEqual(context["sources_by_scene"]["Live"], ["Live Camera"])
        self.assertEqual(context["current_scene"], "Live")
        self.assertNotIn("obs", context)
        self.assertNotIn("password", json.dumps(context).lower())

    def test_unrecognized_context_fields_are_never_sent(self) -> None:
        raw = proposal()
        http = FakeHTTP({
            "output": [{
                "type": "function_call",
                "name": "propose_key_configuration",
                "arguments": json.dumps(raw),
            }],
        })

        AIService(http_post=http).generate(
            "openai",
            "gpt-test",
            "secret",
            "Switch scene",
            context={
                "scenes": ["Live"],
                "password": "must-not-leave-the-computer",
                "unknown_names": ["private"],
            },
        )

        payload = json.dumps(http.calls[0][2])
        self.assertNotIn("must-not-leave-the-computer", payload)
        self.assertNotIn("private", payload)

    def test_ai_settings_round_trip_without_api_keys(self) -> None:
        config = Config()
        config.ai.provider = "anthropic"
        config.ai.openai_model = "gpt-custom"
        config.ai.anthropic_model = "claude-custom"
        config.ai.include_obs_context = True

        raw = config._serializable_dict()
        restored = Config.from_dict(raw)

        self.assertEqual(restored.ai.provider, "anthropic")
        self.assertEqual(restored.ai.openai_model, "gpt-custom")
        self.assertEqual(restored.ai.anthropic_model, "claude-custom")
        self.assertTrue(restored.ai.include_obs_context)
        self.assertNotIn("api_key", json.dumps(raw).lower())


class StepLabelTests(unittest.TestCase):
    """The model may name each step; the name is bounded, untrusted text."""

    def _generate(self, steps: list[dict]):
        raw = {
            **proposal(),
            "kind": "multi",
            "summary": "Go live in two steps.",
            "steps": steps,
        }
        http = FakeHTTP({
            "content": [{
                "type": "tool_use",
                "name": "propose_key_configuration",
                "input": raw,
            }],
        })
        return AIService(http_post=http).generate(
            "anthropic",
            "claude-test",
            "secret",
            "Go live",
            context={"scenes": ["Live"]},
        )

    def test_a_proposed_step_name_reaches_the_key(self) -> None:
        generated = self._generate([
            {
                "action": "obs.scene_switch",
                "parameters": [{"name": "scene", "value": "Live"}],
                "label": "Show the live scene",
            },
            {"action": "obs.stream", "parameters": [], "label": ""},
        ])

        self.assertEqual(
            [step.label for step in generated.key.steps],
            ["Show the live scene", ""],
        )

    def test_a_missing_step_name_is_simply_empty(self) -> None:
        generated = self._generate([
            {
                "action": "obs.scene_switch",
                "parameters": [{"name": "scene", "value": "Live"}],
            },
        ])

        self.assertEqual(generated.key.steps[0].label, "")

    def test_a_step_name_is_flattened_to_one_line(self) -> None:
        generated = self._generate([
            {
                "action": "obs.scene_switch",
                "parameters": [{"name": "scene", "value": "Live"}],
                "label": "  Show   the\nlive\tscene  ",
            },
        ])

        self.assertEqual(generated.key.steps[0].label, "Show the live scene")

    def test_an_over_long_step_name_is_rejected(self) -> None:
        with self.assertRaisesRegex(AIResponseError, "too long"):
            self._generate([
                {
                    "action": "obs.scene_switch",
                    "parameters": [{"name": "scene", "value": "Live"}],
                    "label": "x" * (MAX_STEP_LABEL_CHARS + 1),
                },
            ])

    def test_a_step_name_that_is_not_text_is_rejected(self) -> None:
        with self.assertRaisesRegex(AIResponseError, "must be text"):
            self._generate([
                {
                    "action": "obs.scene_switch",
                    "parameters": [{"name": "scene", "value": "Live"}],
                    "label": {"unexpected": "object"},
                },
            ])

    def test_a_step_name_never_chooses_the_action(self) -> None:
        """It is descriptive only: the action id alone decides what runs."""
        generated = self._generate([
            {
                "action": "obs.scene_switch",
                "parameters": [{"name": "scene", "value": "Live"}],
                "label": "sys.command rm -rf /",
            },
        ])

        self.assertEqual(generated.key.steps[0].action, "obs.scene_switch")
        self.assertEqual(generated.key.steps[0].label, "sys.command rm -rf /")

    def test_providers_are_asked_for_a_step_name(self) -> None:
        self.assertIn("label", _STEP_SCHEMA["properties"])
        self.assertIn("label", _STEP_SCHEMA["required"])

    def test_the_preview_shows_the_name_and_the_action(self) -> None:
        generated = self._generate([
            {
                "action": "obs.scene_switch",
                "parameters": [{"name": "scene", "value": "Live"}],
                "label": "Show the live scene",
            },
            {"action": "obs.stream", "parameters": [], "label": ""},
        ])

        preview = format_proposal(generated)

        self.assertIn("1. Show the live scene — Switch scene", preview)
        # An unnamed step still previews as its action alone.
        self.assertIn("2. Stream on/off", preview)


if __name__ == "__main__":
    unittest.main()

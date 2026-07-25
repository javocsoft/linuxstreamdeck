"""OBS action parameters: recording modes and scene-scoped audio inputs."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from linuxstreamdeck.core import actions as action_registry
from linuxstreamdeck.obs import actions as _obs_actions  # noqa: F401
from linuxstreamdeck.obs.client import OBSClient


class RecordingObs:
    def __init__(self) -> None:
        self.requests: list[tuple[str, dict | None]] = []
        self.state = SimpleNamespace(recording=False, record_paused=False)

    def request(self, name: str, data: dict | None = None) -> dict:
        self.requests.append((name, data))
        return {}


def context(obs):
    return SimpleNamespace(obs=obs, bus=SimpleNamespace(emit=lambda *a, **k: None))


class RecordModeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.action = action_registry.get("obs.record")
        self.obs = RecordingObs()

    def _run(self, params: dict) -> str:
        self.action.execute(context(self.obs), params)
        return self.obs.requests[-1][0]

    def test_start_and_stop_are_explicit_requests(self) -> None:
        self.assertEqual(self._run({"mode": "start"}), "StartRecord")
        self.assertEqual(self._run({"mode": "stop"}), "StopRecord")

    def test_toggle_switches_between_them(self) -> None:
        self.assertEqual(self._run({"mode": "toggle"}), "ToggleRecord")

    def test_a_key_saved_before_the_mode_existed_still_toggles(self) -> None:
        self.assertEqual(self._run({}), "ToggleRecord")

    def test_an_unknown_mode_falls_back_to_toggle(self) -> None:
        self.assertEqual(self._run({"mode": "nonsense"}), "ToggleRecord")

    def test_the_mode_is_offered_as_a_choice(self) -> None:
        mode = next(p for p in self.action.params if p.name == "mode")
        self.assertEqual(mode.choices, ["toggle", "start", "stop"])
        self.assertEqual(mode.default, "toggle")

    def test_recording_feedback_is_unchanged(self) -> None:
        self.obs.state.recording = True
        self.assertEqual(
            self.action.feedback(context(self.obs), {"mode": "start"})["badge"],
            "●",
        )


class AudioActionParameterTests(unittest.TestCase):
    AUDIO_ACTIONS = ("obs.mute", "obs.volume_adjust", "obs.volume_set")

    def test_every_audio_action_picks_a_scene_then_an_input(self) -> None:
        for action_id in self.AUDIO_ACTIONS:
            with self.subTest(action=action_id):
                params = {p.name: p for p in action_registry.get(action_id).params}
                self.assertEqual(params["scene"].choices_source, "scenes")
                self.assertEqual(
                    params["input"].choices_source, "audio_sources_in_scene"
                )

    def test_the_scene_is_optional_so_older_keys_still_load(self) -> None:
        for action_id in self.AUDIO_ACTIONS:
            with self.subTest(action=action_id):
                params = {p.name: p for p in action_registry.get(action_id).params}
                self.assertEqual(params["scene"].default, "")

    def test_muting_still_targets_the_input_alone(self) -> None:
        """The scene only narrows the editor list; it never reaches OBS."""
        obs = RecordingObs()
        action_registry.get("obs.mute").execute(
            context(obs), {"scene": "Live", "input": "Mic/Aux", "mode": "mute"}
        )
        self.assertEqual(
            obs.requests,
            [("SetInputMute", {"inputName": "Mic/Aux", "inputMuted": True})],
        )


class AudioSourceLookupTests(unittest.TestCase):
    """Only sources that answer an audio request are offered."""

    def setUp(self) -> None:
        self.client = OBSClient.__new__(OBSClient)
        self.asked: list[tuple[str, dict | None]] = []

    def _install(
        self,
        items: dict[str, list[str]],
        audio: set[str],
        specials: dict | None = None,
    ) -> None:
        def try_request(name: str, data: dict | None = None):
            self.asked.append((name, data))
            if name == "GetSceneItemList":
                return {
                    "sceneItems": [
                        {"sourceName": source}
                        for source in items.get(data["sceneName"], [])
                    ]
                }
            if name == "GetSpecialInputs":
                return specials if specials is not None else {}
            if name == "GetInputMute":
                return {"inputMuted": False} if data["inputName"] in audio else None
            return None

        self.client.try_request = try_request  # type: ignore[method-assign]
        self.client.state = SimpleNamespace(current_scene="Live")

    def test_video_only_sources_are_left_out(self) -> None:
        self._install(
            {"Live": ["Camera", "Overlay", "Music"]}, audio={"Music"}
        )

        self.assertEqual(
            self.client.get_audio_sources_in_scene("Live"), ["Music"]
        )

    def test_the_global_audio_devices_are_always_offered(self) -> None:
        """Mic and Desktop Audio belong to no scene but are always audible."""
        self._install(
            {"Live": ["Camera"]},
            audio={"Mic/Aux", "Desktop Audio"},
            specials={"desktop1": "Desktop Audio", "mic1": "Mic/Aux", "mic2": ""},
        )

        self.assertEqual(
            self.client.get_audio_sources_in_scene("Live"),
            ["Desktop Audio", "Mic/Aux"],
        )

    def test_a_source_is_never_probed_twice(self) -> None:
        self._install(
            {"Live": ["Mic/Aux"]},
            audio={"Mic/Aux"},
            specials={"mic1": "Mic/Aux"},
        )

        result = self.client.get_audio_sources_in_scene("Live")

        self.assertEqual(result, ["Mic/Aux"])
        probes = [data["inputName"] for name, data in self.asked
                  if name == "GetInputMute"]
        self.assertEqual(probes, ["Mic/Aux"])

    def test_a_scene_without_audio_returns_nothing(self) -> None:
        self._install({"Live": ["Camera"]}, audio=set())
        self.assertEqual(self.client.get_audio_sources_in_scene("Live"), [])


if __name__ == "__main__":
    unittest.main()

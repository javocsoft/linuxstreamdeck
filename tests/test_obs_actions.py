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


class GroupedSources:
    """A scene tree the way obs-websocket v5 actually reports one.

    A group is a scene item of the scene, but its children are not: they are
    only reachable through GetGroupSceneItemList, keyed by the group's name,
    and GetSceneItemId only answers for the container that directly holds the
    source.
    """

    def __init__(self, scenes: dict[str, list], groups: dict[str, list[str]]):
        self.scenes = scenes
        self.groups = groups
        self.asked: list[tuple[str, dict | None]] = []
        self.state = SimpleNamespace(current_scene="Live")
        self.connected = True
        self._next_id = 100

    def request(self, name: str, data: dict | None = None):
        self.asked.append((name, data))
        data = data or {}
        if name == "GetSceneItemList":
            return {"sceneItems": self._items(data["sceneName"])}
        if name == "GetGroupSceneItemList":
            group = data["sceneName"]
            if group not in self.groups:
                raise RuntimeError(f"{group} is not a group")
            return {
                "sceneItems": [
                    {"sourceName": child} for child in self.groups[group]
                ]
            }
        if name == "GetSceneItemId":
            container, source = data["sceneName"], data["sourceName"]
            held = (
                self.groups[container]
                if container in self.groups
                else [
                    item["sourceName"] for item in self._items(container)
                ]
            )
            if source not in held:
                # What OBS answers for a source the container does not hold.
                raise RuntimeError(f"{source} not found in {container}")
            self._next_id += 1
            return {"sceneItemId": self._next_id}
        if name in ("GetSceneItemEnabled",):
            return {"sceneItemEnabled": True}
        return {}

    def try_request(self, name: str, data: dict | None = None):
        try:
            return self.request(name, data)
        except Exception:
            return None

    def _items(self, scene: str) -> list[dict]:
        return [
            {"sourceName": entry, "isGroup": entry in self.groups}
            for entry in self.scenes.get(scene, [])
        ]


class GroupedSourceTests(unittest.TestCase):
    """A source inside a group must be listed, and must still be operable.

    Only the group used to appear in the editor's Source dropdown, so nothing
    inside it could be picked at all.
    """

    def setUp(self) -> None:
        self.obs = GroupedSources(
            scenes={"Live": ["Camera", "Overlays", "Music"]},
            groups={"Overlays": ["Logo", "Lower third"]},
        )
        self.client = OBSClient(SimpleNamespace())
        self.client.request = self.obs.request        # type: ignore[method-assign]
        self.client.try_request = self.obs.try_request  # type: ignore[method-assign]
        self.client.state = self.obs.state
        # `feedback()` returns early on a disconnected client.
        self.client.connected = True

    def test_sources_inside_a_group_are_listed(self) -> None:
        self.assertEqual(
            self.client.get_sources_in_scene("Live"),
            ["Camera", "Overlays", "Logo", "Lower third", "Music"],
        )

    def test_the_group_itself_is_still_offered(self) -> None:
        """Toggling a whole group is a normal thing to want."""
        self.assertIn("Overlays", self.client.get_sources_in_scene("Live"))

    def test_children_follow_their_group_in_the_list(self) -> None:
        listed = self.client.get_sources_in_scene("Live")
        self.assertEqual(
            listed.index("Logo") - listed.index("Overlays"), 1
        )

    def test_a_scene_with_no_groups_is_unchanged(self) -> None:
        self.obs.scenes["Plain"] = ["Camera", "Music"]
        self.assertEqual(
            self.client.get_sources_in_scene("Plain"), ["Camera", "Music"]
        )

    def test_a_source_in_two_groups_is_listed_once(self) -> None:
        self.obs.scenes["Live"] = ["Overlays", "More"]
        self.obs.groups["More"] = ["Logo", "Ticker"]
        self.assertEqual(
            self.client.get_sources_in_scene("Live"),
            ["Overlays", "Logo", "Lower third", "More", "Ticker"],
        )

    def test_a_top_level_source_resolves_against_the_scene(self) -> None:
        container, item_id = self.client.find_scene_item("Live", "Camera")

        self.assertEqual(container, "Live")
        self.assertIsInstance(item_id, int)

    def test_a_grouped_source_resolves_against_its_group(self) -> None:
        """OBS addresses group children by the group name, not the scene."""
        container, item_id = self.client.find_scene_item("Live", "Logo")

        self.assertEqual(container, "Overlays")
        self.assertIsInstance(item_id, int)

    def test_an_unknown_source_is_reported_clearly(self) -> None:
        with self.assertRaises(LookupError) as caught:
            self.client.find_scene_item("Live", "Nothing")

        self.assertIn("Nothing", str(caught.exception))

    def test_an_empty_scene_falls_back_to_the_current_one(self) -> None:
        container, _ = self.client.find_scene_item("", "Camera")

        self.assertEqual(container, "Live")

    def test_showing_a_grouped_source_targets_its_group(self) -> None:
        """The whole point: the action has to work on what it now lists."""
        action = action_registry.get("obs.source_visibility")

        action.execute(
            context(self.client),
            {"scene": "Live", "source": "Logo", "mode": "show"},
        )

        applied = [
            data for name, data in self.obs.asked if name == "SetSceneItemEnabled"
        ]
        self.assertEqual(len(applied), 1)
        self.assertEqual(applied[0]["sceneName"], "Overlays")
        self.assertTrue(applied[0]["sceneItemEnabled"])

    def test_feedback_reads_a_grouped_source_without_raising(self) -> None:
        action = action_registry.get("obs.source_visibility")

        result = action.feedback(
            context(self.client), {"scene": "Live", "source": "Logo"}
        )

        self.assertEqual(result, {"active": True})

    def test_feedback_on_a_missing_source_is_silent(self) -> None:
        action = action_registry.get("obs.source_visibility")

        self.assertIsNone(
            action.feedback(
                context(self.client), {"scene": "Live", "source": "Gone"}
            )
        )

    def test_audio_inputs_inside_a_group_are_offered(self) -> None:
        """The audio list is built on the same walk, so it inherits the fix."""
        asked: list[str] = []

        def try_request(name: str, data: dict | None = None):
            if name == "GetInputMute":
                asked.append(data["inputName"])
                return {"inputMuted": False} if data["inputName"] == "Logo" else None
            if name == "GetSpecialInputs":
                return {}
            return self.obs.try_request(name, data)

        self.client.try_request = try_request  # type: ignore[method-assign]

        self.assertEqual(
            self.client.get_audio_sources_in_scene("Live"), ["Logo"]
        )
        self.assertIn("Logo", asked)


if __name__ == "__main__":
    unittest.main()

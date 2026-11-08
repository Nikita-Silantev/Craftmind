import copy
import unittest
from unittest.mock import patch

from craftmind.cli import ACTIONS, describe_state, execute, ollama_plan, run_task, scripted_action


class ScriptedActionTests(unittest.TestCase):
    def test_russian_commands(self):
        self.assertEqual(scripted_action("иди вперёд"), "forward")
        self.assertEqual(scripted_action("пробеги вперёд"), "sprint_forward")
        self.assertEqual(scripted_action("поверни налево"), "turn_left")
        self.assertEqual(scripted_action("прыгни"), "jump")
        self.assertEqual(scripted_action("остановись"), "stop")

    def test_unknown_task(self):
        self.assertIsNone(scripted_action("построй дом"))


class AgentTests(unittest.TestCase):
    def test_movement_is_followed_by_stop(self):
        calls = []
        def fake_request(base, path, payload=None):
            calls.append((path, payload))
            return {"ok": True}
        with patch("craftmind.cli.bridge_request", side_effect=fake_request), patch("craftmind.cli.time.sleep"):
            execute("http://localhost", "forward", 1400)
        self.assertEqual(calls, [
            ("/action", {"action": "forward", "duration_ms": 1400}),
            ("/action", {"action": "stop"}),
        ])

    def test_creative_mine_uses_one_command_without_hold(self):
        calls = []
        def fake_request(base, path, payload=None):
            calls.append((path, payload))
            return {"ok": True}
        with patch("craftmind.cli.bridge_request", side_effect=fake_request), patch("craftmind.cli.time.sleep") as sleep:
            execute("http://localhost", "mine", 1200, "creative")
        self.assertEqual(len(calls), 1)
        sleep.assert_not_called()

    def test_ollama_plan_uses_schema_and_can_disable_thinking(self):
        import json
        captured = {}
        class FakeResponse:
            def __enter__(self): return self
            def __exit__(self, *_): return None
        def fake_urlopen(request, timeout):
            captured.update(json.loads(request.data))
            captured["timeout"] = timeout
            return FakeResponse()
        output = {"message": {"content": '{"status":"ready","reason":"ок","missing_capability":"none","steps":[{"action":"forward","duration_ms":1000}]}'}}
        with patch("craftmind.cli.urlopen", side_effect=fake_urlopen), patch("craftmind.cli.json.load", return_value=output):
            plan = ollama_plan("иди", {"bot": "present"}, "qwen3:14b", "http://localhost:11434", False)
        self.assertEqual(plan["steps"][0]["action"], "forward")
        self.assertFalse(captured["think"])
        self.assertEqual(captured["format"]["properties"]["steps"]["items"]["properties"]["action"]["enum"], sorted(ACTIONS - {"stop"}))
        self.assertEqual(captured["timeout"], 300)
        self.assertIn("Наблюдение сейчас:", captured["messages"][1]["content"])
        self.assertNotIn('"state":', captured["messages"][1]["content"])

    def test_repeated_movement_is_merged_and_bounded(self):
        output = {"message": {"content": '{"status":"ready","reason":"ок","missing_capability":"none","steps":[{"action":"forward","duration_ms":2000},{"action":"forward","duration_ms":2000},{"action":"turn_left","duration_ms":200}]}'}}
        class FakeResponse:
            def __enter__(self): return self
            def __exit__(self, *_): return None
        with patch("craftmind.cli.urlopen", return_value=FakeResponse()), patch("craftmind.cli.json.load", return_value=output):
            plan = ollama_plan("иди и поверни", {"bot": "present"}, "qwen3:14b", "http://localhost:11434")
        self.assertEqual(plan["steps"], [
            {"action": "forward", "duration_ms": 3000},
            {"action": "turn_left", "duration_ms": 200},
        ])

    def test_plan_is_called_once_and_steps_execute_in_order(self):
        states = iter([
            {"bot": "present", "x": 0, "y": 64, "z": 0},
            {"bot": "present", "x": 0, "y": 64, "z": 0},
            {"bot": "present", "x": 2, "y": 64, "z": 0},
            {"bot": "present", "x": 2, "y": 64, "z": 0},
            {"bot": "present", "x": 2, "y": 64, "z": 0},
        ])
        actions = []
        def fake_request(base, path, payload=None):
            if path == "/state": return next(states)
            if path == "/action": actions.append(payload["action"])
            return {"ok": True}
        plan = {"status": "ready", "reason": "ок", "steps": [
            {"action": "forward", "duration_ms": 1000},
            {"action": "turn_left", "duration_ms": 200},
        ]}
        with patch("craftmind.cli.bridge_request", side_effect=fake_request), \
             patch("craftmind.cli.ollama_plan", return_value=plan) as planner, \
             patch("craftmind.cli.time.sleep"):
            run_task("http://localhost", "иди и поверни", "ollama", "qwen3:14b", "http://localhost:11434", 6)
        planner.assert_called_once()
        self.assertEqual(actions, ["forward", "stop", "turn_left"])

    def test_air_is_not_mined(self):
        actions = []
        def fake_request(base, path, payload=None):
            if path == "/state": return {"bot": "present", "target_block": {"id": "minecraft:air"}}
            if path == "/action": actions.append(payload)
            return {"ok": True}
        plan = {"status": "ready", "reason": "ок", "steps": [{"action": "mine", "duration_ms": 1000}]}
        with patch("craftmind.cli.bridge_request", side_effect=fake_request), patch("craftmind.cli.ollama_plan", return_value=plan):
            run_task("http://localhost", "сломай", "ollama", "qwen3:14b", "http://localhost:11434", 6)
        self.assertEqual(actions, [])

    def test_observation_explains_ground_and_target_without_raw_json(self):
        description = describe_state({
            "bot": "present", "x": 4, "y": 65, "z": 7, "game_mode": "survival", "on_ground": True,
            "block_below": {"id": "minecraft:snow_block"},
            "target_block": {"id": "minecraft:dirt", "x": 4, "y": 64, "z": 7},
            "inventory": {}, "nearby_blocks": [],
        })
        self.assertIn("Непосредственно под ногами: minecraft:snow_block", description)
        self.assertIn("Под прицелом: minecraft:dirt", description)
        self.assertIn("режим survival", description)

    def test_unverified_refusal_is_replanned_as_action(self):
        state = {"bot": "present", "x": 0, "y": 64, "z": 0, "game_mode": "survival"}
        actions = []
        def fake_request(base, path, payload=None):
            if path == "/state": return copy.deepcopy(state)
            if path == "/action":
                actions.append(payload["action"])
                if payload["action"] == "forward": state["x"] = 2
            return {"ok": True}
        blocked = {"status": "blocked", "reason": "Не знаю блок под ногами.",
                   "missing_capability": "other", "steps": []}
        ready = {"status": "ready", "reason": "Начну движение.", "missing_capability": "none",
                 "steps": [{"action": "forward", "duration_ms": 1000}]}
        with patch("craftmind.cli.bridge_request", side_effect=fake_request), \
             patch("craftmind.cli.ollama_plan", side_effect=[blocked, ready]) as planner, \
             patch("craftmind.cli.time.sleep"):
            run_task("http://localhost", "пройди вперёд", "ollama", "qwen3:14b", "http://localhost:11434", 6)
        self.assertEqual(actions, ["forward", "stop"])
        self.assertTrue(planner.call_args_list[1].kwargs["force_action"])

    def test_confirmed_missing_tool_stops_without_action(self):
        actions = []
        def fake_request(base, path, payload=None):
            if path == "/state": return {"bot": "present"}
            if path == "/action": actions.append(payload)
            return {"ok": True}
        blocked = {"status": "blocked", "reason": "Нужна установка блоков.",
                   "missing_capability": "place", "steps": []}
        with patch("craftmind.cli.bridge_request", side_effect=fake_request), \
             patch("craftmind.cli.ollama_plan", return_value=blocked) as planner:
            run_task("http://localhost", "построй дом", "ollama", "qwen3:14b", "http://localhost:11434", 6)
        self.assertEqual(actions, [])
        planner.assert_called_once()

    def test_available_prefix_runs_before_missing_tool_is_reported(self):
        state = {"bot": "present", "x": 0, "y": 64, "z": 0}
        actions = []
        replies = []
        def fake_request(base, path, payload=None):
            if path == "/state": return copy.deepcopy(state)
            if path == "/action":
                actions.append(payload["action"])
                if payload["action"] == "forward": state["x"] = 2
            if path == "/reply": replies.append(payload["text"])
            return {"ok": True}
        blocked = {"status": "blocked", "reason": "Нужна установка.",
                   "missing_capability": "place", "steps": []}
        partial = {"status": "ready", "reason": "Сначала иду.", "missing_capability": "none", "steps": [
            {"action": "forward", "duration_ms": 1000},
        ]}
        with patch("craftmind.cli.bridge_request", side_effect=fake_request), \
             patch("craftmind.cli.ollama_plan", side_effect=[blocked, partial, blocked]) as planner, \
             patch("craftmind.cli.time.sleep"):
            run_task("http://localhost", "иди вперёд и построй дом", "ollama",
                     "qwen3:14b", "http://localhost:11434", 6)
        self.assertEqual(actions, ["forward", "stop"])
        self.assertEqual(planner.call_count, 3)
        self.assertIn("действие установки пока не реализовано", replies[-1])

    def test_looks_down_then_replans_with_new_target(self):
        state = {"bot": "present", "x": 0, "y": 65, "z": 0, "game_mode": "survival"}
        actions = []
        def fake_request(base, path, payload=None):
            if path == "/state": return copy.deepcopy(state)
            if path == "/action":
                actions.append(payload["action"])
                if payload["action"] == "forward": state["x"] = 2
                if payload["action"] == "look_down":
                    state["target_block"] = {"id": "minecraft:dirt", "x": 2, "y": 64, "z": 0}
            return {"ok": True}
        first = {"status": "ready", "reason": "Выполню действия.", "missing_capability": "none", "steps": [
            {"action": "forward", "duration_ms": 1000},
            {"action": "look_down", "duration_ms": 200},
            {"action": "mine", "duration_ms": 1200},
        ]}
        second = {"status": "ready", "reason": "Блок под прицелом.", "missing_capability": "none", "steps": [
            {"action": "mine", "duration_ms": 1200},
        ]}
        with patch("craftmind.cli.bridge_request", side_effect=fake_request), \
             patch("craftmind.cli.ollama_plan", side_effect=[first, second]) as planner, \
             patch("craftmind.cli.time.sleep"):
            run_task("http://localhost", "пройди вперёд и сломай блок под собой", "ollama",
                     "qwen3:14b", "http://localhost:11434", 6)
        self.assertEqual(actions, ["forward", "stop", "look_down", "mine", "stop"])
        self.assertEqual(planner.call_count, 2)
        self.assertEqual(planner.call_args_list[1].args[1]["target_block"]["id"], "minecraft:dirt")

    def test_continue_status_requests_next_phase_after_movement(self):
        state = {"bot": "present", "x": 0, "y": 64, "z": 0}
        actions = []
        def fake_request(base, path, payload=None):
            if path == "/state": return copy.deepcopy(state)
            if path == "/action":
                actions.append(payload["action"])
                if payload["action"] == "forward": state["x"] = 2
            return {"ok": True}
        first = {"status": "continue", "reason": "После движения проверю мир.", "steps": [
            {"action": "forward", "duration_ms": 1000},
        ]}
        second = {"status": "ready", "reason": "Теперь поверну.", "steps": [
            {"action": "turn_right", "duration_ms": 200},
        ]}
        with patch("craftmind.cli.bridge_request", side_effect=fake_request), \
             patch("craftmind.cli.ollama_plan", side_effect=[first, second]) as planner, \
             patch("craftmind.cli.time.sleep"):
            run_task("http://localhost", "иди вперёд и поверни направо", "ollama",
                     "qwen3:14b", "http://localhost:11434", 6)
        self.assertEqual(actions, ["forward", "stop", "turn_right"])
        self.assertEqual(planner.call_count, 2)
        self.assertEqual(planner.call_args_list[1].args[1]["x"], 2)


if __name__ == "__main__":
    unittest.main()

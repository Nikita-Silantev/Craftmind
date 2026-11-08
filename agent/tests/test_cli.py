import unittest
from unittest.mock import patch

from craftmind.cli import ACTIONS, execute, ollama_plan, run_task, scripted_action


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
        output = {"message": {"content": '{"status":"ready","reason":"ок","steps":[{"action":"forward","duration_ms":1000}]}'}}
        with patch("craftmind.cli.urlopen", side_effect=fake_urlopen), patch("craftmind.cli.json.load", return_value=output):
            plan = ollama_plan("иди", {"bot": "present"}, "qwen3:14b", "http://localhost:11434", False)
        self.assertEqual(plan["steps"][0]["action"], "forward")
        self.assertFalse(captured["think"])
        self.assertEqual(captured["format"]["properties"]["steps"]["items"]["properties"]["action"]["enum"], sorted(ACTIONS - {"stop"}))
        self.assertEqual(captured["timeout"], 300)

    def test_repeated_movement_is_merged_and_bounded(self):
        output = {"message": {"content": '{"status":"ready","reason":"ок","steps":[{"action":"forward","duration_ms":2000},{"action":"forward","duration_ms":2000},{"action":"turn_left","duration_ms":200}]}'}}
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


if __name__ == "__main__":
    unittest.main()

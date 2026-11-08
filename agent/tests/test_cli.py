import unittest
from craftmind.cli import scripted_action


class ScriptedActionTests(unittest.TestCase):
    def test_russian_commands(self):
        self.assertEqual(scripted_action("иди вперёд"), "forward")
        self.assertEqual(scripted_action("пробеги вперёд"), "sprint_forward")
        self.assertEqual(scripted_action("поверни налево"), "turn_left")
        self.assertEqual(scripted_action("прыгни"), "jump")
        self.assertEqual(scripted_action("остановись"), "stop")

    def test_unknown_task(self):
        self.assertIsNone(scripted_action("построй дом"))


if __name__ == "__main__":
    unittest.main()

class BridgeProtocolTests(unittest.TestCase):
    def test_movement_is_followed_by_stop(self):
        from unittest.mock import patch
        from craftmind.cli import execute
        calls = []

        def fake_request(base, path, payload=None):
            calls.append((path, payload))
            return {"ok": True}

        with patch("craftmind.cli.bridge_request", side_effect=fake_request), patch("craftmind.cli.time.sleep"):
            execute("http://127.0.0.1:8765", "forward")
        self.assertEqual(calls, [
            ("/action", {"action": "forward"}),
            ("/action", {"action": "stop"}),
        ])

    def test_ollama_request_uses_thinking_and_action_schema(self):
        import json
        from unittest.mock import patch
        from craftmind.cli import PLANNER_ACTIONS, ollama_decision

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

        captured = {}

        def fake_urlopen(request, timeout):
            captured.update(json.loads(request.data))
            captured["timeout"] = timeout
            return FakeResponse()

        with patch("craftmind.cli.urlopen", side_effect=fake_urlopen), \
             patch("craftmind.cli.json.load", return_value={"message": {"content": '{"action":"back","reason":"шаг назад"}'}}):
            decision = ollama_decision("вернись назад", {"bot": "present"}, [], "qwen3:14b", "http://localhost:11434")
        self.assertEqual(decision["action"], "back")
        self.assertTrue(captured["think"])
        self.assertEqual(captured["format"]["properties"]["action"]["enum"], sorted(PLANNER_ACTIONS))
        self.assertEqual(captured["timeout"], 300)

    def test_task_observes_again_after_each_action(self):
        from unittest.mock import patch
        from craftmind.cli import run_task
        states = iter([
            {"bot": "present", "x": 0, "y": 64, "z": 0},
            {"bot": "present", "x": 1, "y": 64, "z": 0},
            {"bot": "present", "x": 1, "y": 64, "z": 0},
        ])
        decisions = iter([
            {"action": "forward", "reason": "шаг"},
            {"action": "finish", "reason": "дошёл"},
        ])
        replies = []

        def fake_request(base, path, payload=None):
            if path == "/state":
                return next(states)
            if path == "/reply":
                replies.append(payload["text"])
                return {"ok": True}
            return {"ok": True}

        with patch("craftmind.cli.bridge_request", side_effect=fake_request), \
             patch("craftmind.cli.ollama_decision", side_effect=lambda *args: next(decisions)), \
             patch("craftmind.cli.time.sleep"):
            run_task("http://localhost", "сделай шаг", "ollama", "qwen3:14b", "http://localhost:11434", 6)
        self.assertTrue(any("Готово" in text for text in replies))

    def test_cancelled_task_does_not_act_after_model_reply(self):
        from unittest.mock import patch
        from craftmind.cli import run_task
        checks = iter([False, True])
        replies = []

        def fake_request(base, path, payload=None):
            if path == "/state":
                return {"bot": "present"}
            if path == "/reply":
                replies.append(payload["text"])
                return {"ok": True}
            self.fail("Cancelled task sent an action")

        with patch("craftmind.cli.bridge_request", side_effect=fake_request), \
             patch("craftmind.cli.ollama_decision", return_value={"action": "forward", "reason": "идти"}):
            run_task("http://localhost", "иди", "ollama", "qwen3:14b", "http://localhost:11434", 6,
                     cancelled=lambda: next(checks))
        self.assertTrue(any("остановлена" in text for text in replies))

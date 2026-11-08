import unittest
from craftmind.cli import scripted_action


class ScriptedActionTests(unittest.TestCase):
    def test_russian_commands(self):
        self.assertEqual(scripted_action("иди вперёд"), "forward")
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
        from craftmind.cli import ACTIONS, ollama_action

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
             patch("craftmind.cli.json.load", return_value={"message": {"content": '{"action":"back"}'}}):
            self.assertEqual(ollama_action("вернись назад", {"bot": "present"}, "qwen3:14b", "http://localhost:11434"), "back")
        self.assertTrue(captured["think"])
        self.assertEqual(captured["format"]["properties"]["action"]["enum"], sorted(ACTIONS))
        self.assertEqual(captured["timeout"], 300)

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

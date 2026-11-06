"""Poll the Minecraft bridge and execute bounded first-test commands."""

import argparse
import json
import logging
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

LOG = logging.getLogger("craftmind")
ACTIONS = {"forward", "back", "left", "right", "jump", "stop", "state"}


def bridge_request(base: str, path: str, payload: dict | None = None) -> dict:
    body = json.dumps(payload).encode() if payload is not None else None
    request = Request(base + path, data=body, headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=5) as response:
        return json.load(response)


def scripted_action(task: str) -> str | None:
    words = task.lower().strip()
    if any(word in words for word in ("стоп", "останов", "stop")):
        return "stop"
    if any(word in words for word in ("прыг", "jump")):
        return "jump"
    if any(word in words for word in ("налево", "left")):
        return "left"
    if any(word in words for word in ("направо", "right")):
        return "right"
    if any(word in words for word in ("назад", "back")):
        return "back"
    if any(word in words for word in ("вперёд", "вперед", "иди", "forward")):
        return "forward"
    if any(word in words for word in ("статус", "где ты", "state")):
        return "state"
    return None


def ollama_action(task: str, state: dict, model: str, url: str) -> str:
    prompt = ("Ты выбираешь ОДНО простое действие бота Minecraft. "
              'Ответь JSON-объектом {"action":"..."}. '
              "Допустимые действия: forward, back, left, right, jump, stop, state. "
              "Поворот пока не реализован: left/right означает шаг в сторону. "
              "Если задача сложнее одного действия, выбери state. "
              "Любое движение длится 2 секунды. Не обещай добычу или строительство.")
    body = json.dumps({"model": model, "stream": False, "format": "json", "messages": [
        {"role": "system", "content": prompt},
        {"role": "user", "content": json.dumps({"task": task, "state": state}, ensure_ascii=False)},
    ]}).encode()
    with urlopen(Request(url.rstrip("/") + "/api/chat", data=body,
                         headers={"Content-Type": "application/json"}), timeout=120) as response:
        data = json.load(response)
    action = json.loads(data["message"]["content"])["action"]
    if action not in ACTIONS:
        raise ValueError(f"Model returned unsupported action: {action}")
    return action


def execute(base: str, action: str, seconds: float = 2.0) -> dict:
    if action == "state":
        return bridge_request(base, "/state")
    if action == "stop":
        return bridge_request(base, "/action", {"action": "stop"})
    result = bridge_request(base, "/action", {"action": action})
    if action in {"forward", "back", "left", "right"}:
        try:
            time.sleep(seconds)
        finally:
            bridge_request(base, "/action", {"action": "stop"})
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Craftmind agent")
    parser.add_argument("--mode", choices=("scripted", "ollama"), default="scripted")
    parser.add_argument("--model", default="qwen3:8b")
    parser.add_argument("--bridge", default="http://127.0.0.1:8765")
    parser.add_argument("--ollama", default="http://127.0.0.1:11434")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    LOG.info("Craftmind agent started in %s mode", args.mode)
    while True:
        try:
            for event in bridge_request(args.bridge, "/events")["events"]:
                task = event["task"]
                try:
                    state = bridge_request(args.bridge, "/state")
                    action = scripted_action(task) if args.mode == "scripted" else ollama_action(task, state, args.model, args.ollama)
                    if action is None:
                        LOG.info("Unsupported task: %s", task)
                        bridge_request(args.bridge, "/reply", {"text": "Пока не умею это делать. Попробуй: вперёд, назад, налево, направо, прыгни, стоп или статус."})
                        continue
                    LOG.info("%s -> %s", task, action)
                    result = execute(args.bridge, action)
                    LOG.info("Result: %s", result)
                    bridge_request(args.bridge, "/reply", {"text": f"Выполнил: {action}. {result if action == 'state' else ''}"[:200]})
                except (URLError, HTTPError, TimeoutError, ValueError, KeyError) as error:
                    LOG.warning("Task failed: %s", error)
                    try:
                        bridge_request(args.bridge, "/reply", {"text": f"Не смог выполнить задачу: {error}"[:200]})
                    except (URLError, HTTPError, TimeoutError):
                        pass
            time.sleep(0.5)
        except KeyboardInterrupt:
            LOG.info("Stopping agent")
            break
        except (URLError, HTTPError, TimeoutError, ValueError, KeyError) as error:
            LOG.warning("Agent error: %s", error)
            time.sleep(2)


if __name__ == "__main__":
    main()

"""Local agent that observes Minecraft and executes bounded tasks."""

import argparse
import json
import logging
import queue
import threading
import time
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

LOG = logging.getLogger("craftmind")
MOVEMENT = {"forward", "back", "left", "right", "sprint_forward"}
ACTIONS = MOVEMENT | {"turn_left", "turn_right", "jump", "stop", "attack", "mine", "use", "state"}
PLANNER_ACTIONS = ACTIONS | {"finish", "blocked"}


def bridge_request(base: str, path: str, payload: dict | None = None) -> dict:
    body = json.dumps(payload).encode() if payload is not None else None
    request = Request(base + path, data=body, headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=5) as response:
        return json.load(response)


def scripted_action(task: str) -> str | None:
    words = task.lower().strip()
    if any(word in words for word in ("стоп", "останов", "stop")):
        return "stop"
    if any(word in words for word in ("поверни налево", "turn left")):
        return "turn_left"
    if any(word in words for word in ("поверни направо", "turn right")):
        return "turn_right"
    if any(word in words for word in ("беги", "пробеги", "sprint")):
        return "sprint_forward"
    if any(word in words for word in ("прыг", "jump")):
        return "jump"
    if any(word in words for word in ("сломай", "копай", "добудь", "mine")):
        return "mine"
    if any(word in words for word in ("ударь", "attack")):
        return "attack"
    if any(word in words for word in ("используй", "use")):
        return "use"
    if any(word in words for word in ("налево", "left")):
        return "left"
    if any(word in words for word in ("направо", "right")):
        return "right"
    if any(word in words for word in ("назад", "back")):
        return "back"
    if any(word in words for word in ("вперёд", "вперед", "иди", "forward")):
        return "forward"
    if any(word in words for word in ("статус", "где ты", "осмотр", "state")):
        return "state"
    return None


def ollama_decision(task: str, state: dict, history: list[dict], model: str, url: str) -> dict:
    prompt = (
        "Ты управляешь ботом Minecraft. Выполняй задачу несколькими короткими шагами. "
        "Перед каждым шагом ты получишь НОВОЕ состояние мира. Блоки и сущности рядом — датчик близости, "
        "не изображение; он может показывать объекты за стеной. Не считай путь свободным без проверки. "
        "Выбери РОВНО ОДНО действие. Движение/спринт/ломание длятся максимум 2 секунды. "
        "turn_left/right поворачивают на 90 градусов; left/right — шаг вбок. "
        "mine ломает только блок под прицелом, use использует предмет в руке. "
        "После достижения цели выбери finish. Если цель требует установки блоков, крафта, поиска пути или "
        "действий, которых нет в списке, выбери blocked и кратко объясни причину. "
        "Не заявляй об успехе без подтверждения в состоянии. Не повторяй действие, если результат не изменился."
    )
    schema = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": sorted(PLANNER_ACTIONS)},
            "reason": {"type": "string"},
        },
        "required": ["action", "reason"],
        "additionalProperties": False,
    }
    body = json.dumps({
        "model": model, "stream": False, "think": True, "format": schema,
        "options": {"num_ctx": 4096},
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps({"goal": task, "state": state, "recent_steps": history[-4:]}, ensure_ascii=False)},
        ],
    }).encode()
    LOG.info("Thinking about: %s", task)
    with urlopen(Request(url.rstrip("/") + "/api/chat", data=body,
                         headers={"Content-Type": "application/json"}), timeout=300) as response:
        data = json.load(response)
    decision = json.loads(data["message"]["content"])
    if decision["action"] not in PLANNER_ACTIONS:
        raise ValueError(f"Model returned unsupported action: {decision['action']}")
    return decision


def execute(base: str, action: str, seconds: float = 2.0) -> dict:
    if action == "state":
        return bridge_request(base, "/state")
    if action in {"finish", "blocked"}:
        raise ValueError("Decision is not a game action")
    result = bridge_request(base, "/action", {"action": action})
    if action in MOVEMENT | {"mine"}:
        try:
            time.sleep(seconds)
        finally:
            bridge_request(base, "/action", {"action": "stop"})
    return result


def reply(base: str, text: str) -> None:
    bridge_request(base, "/reply", {"text": text[:200]})


def run_task(base: str, task: str, mode: str, model: str, ollama: str, max_steps: int,
             cancelled: Callable[[], bool] = lambda: False) -> None:
    history: list[dict] = []
    reply(base, f"Начинаю: {task}")
    for step in range(1, max_steps + 1):
        if cancelled():
            reply(base, "Задача остановлена.")
            return
        before = bridge_request(base, "/state")
        if before.get("bot") != "present":
            reply(base, "Бот не появился в мире. Введи /craftmind spawn.")
            return
        if mode == "scripted":
            action = scripted_action(task)
            decision = {"action": action or "blocked", "reason": "Команда пока не поддерживается."}
        else:
            decision = ollama_decision(task, before, history, model, ollama)
        action = decision["action"]
        if cancelled():
            reply(base, "Задача остановлена.")
            return
        if action in {"finish", "blocked"}:
            reply(base, ("Готово: " if action == "finish" else "Пока не могу: ") + decision["reason"])
            return
        execute(base, action)
        after = bridge_request(base, "/state")
        history.append({"action": action, "before": {k: before.get(k) for k in ("x", "y", "z", "health")},
                        "after": {k: after.get(k) for k in ("x", "y", "z", "health")}})
        LOG.info("Step %d: %s; state: %s", step, action, after)
        if mode == "scripted" or action == "state":
            reply(base, f"Выполнил: {action}. {after if action == 'state' else ''}")
            return
        reply(base, f"Шаг {step}: {action}")
    reply(base, f"Остановился после {max_steps} шагов. Цель ещё не подтверждена.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Craftmind agent")
    parser.add_argument("--mode", choices=("scripted", "ollama"), default="scripted")
    parser.add_argument("--model", default="qwen3:14b")
    parser.add_argument("--bridge", default="http://127.0.0.1:8765")
    parser.add_argument("--ollama", default="http://127.0.0.1:11434")
    parser.add_argument("--max-steps", type=int, default=6)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    pending: queue.Queue[tuple[str, int]] = queue.Queue()
    generation = [0]
    busy = threading.Event()

    def worker() -> None:
        while True:
            task, token = pending.get()
            busy.set()
            try:
                run_task(args.bridge, task, args.mode, args.model, args.ollama, args.max_steps,
                         cancelled=lambda: generation[0] != token)
            except (URLError, HTTPError, TimeoutError, ValueError, KeyError, TypeError) as error:
                LOG.exception("Task failed: %s", error)
                try:
                    reply(args.bridge, f"Ошибка задачи: {error}")
                except (URLError, HTTPError, TimeoutError):
                    pass
            finally:
                busy.clear()
                pending.task_done()

    threading.Thread(target=worker, daemon=True).start()
    LOG.info("Craftmind agent started in %s mode", args.mode)
    while True:
        try:
            for event in bridge_request(args.bridge, "/events")["events"]:
                task = event["task"]
                if scripted_action(task) == "stop":
                    generation[0] += 1
                    execute(args.bridge, "stop")
                    while not pending.empty():
                        try:
                            pending.get_nowait()
                            pending.task_done()
                        except queue.Empty:
                            break
                    reply(args.bridge, "Остановился. Текущая задача и очередь отменены.")
                    continue
                if busy.is_set() or pending.qsize() > 0:
                    reply(args.bridge, f"Поставил задачу в очередь: {task}")
                pending.put((task, generation[0]))
            time.sleep(0.5)
        except KeyboardInterrupt:
            generation[0] += 1
            LOG.info("Stopping agent")
            break
        except (URLError, HTTPError, TimeoutError, ValueError, KeyError) as error:
            LOG.warning("Bridge error: %s", error)
            time.sleep(2)


if __name__ == "__main__":
    main()

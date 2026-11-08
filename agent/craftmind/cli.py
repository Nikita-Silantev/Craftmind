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
ACTIONS = MOVEMENT | {"turn_left", "turn_right", "look_down", "look_up", "jump", "stop", "attack", "mine", "use", "state"}


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


def ollama_plan(task: str, state: dict, model: str, url: str, think: bool = True) -> dict:
    prompt = (
        "Ты управляешь ботом Minecraft. Составь ОДИН короткий план максимум из 6 действий. "
        "План выполняется последовательно; после каждого действия программа проверяет состояние мира. "
        "Движение задаётся длительностью в миллисекундах от 200 до 3000. "
        "Для 'пройди немного вперёд' нужен ОДИН forward около 1000-1500 мс, не повторяй его. "
        "turn_left/right поворачивает на 90 градусов; left/right — шаг вбок. "
        "look_down направляет взгляд вниз. mine ломает ОДИН блок в творческом режиме и держит атаку "
        "до 2 секунд в выживании. Чтобы сломать два блока под собой, используй look_down, mine, mine. "
        "Не добавляй действия после выполнения запроса. Блоки рядом — датчик близости, не камера; "
        "он может показывать объекты за стеной. Если цель требует установки блоков, крафта, "
        "поиска пути или других отсутствующих функций, поставь status=blocked и объясни причину. "
        "Если задача только про статус/осмотр, используй state один раз."
    )
    schema = {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["ready", "blocked"]},
            "reason": {"type": "string"},
            "steps": {"type": "array", "minItems": 0, "maxItems": 6, "items": {
                "type": "object", "properties": {
                    "action": {"type": "string", "enum": sorted(ACTIONS - {"stop"})},
                    "duration_ms": {"type": "integer", "minimum": 200, "maximum": 3000},
                }, "required": ["action", "duration_ms"], "additionalProperties": False,
            }},
        },
        "required": ["status", "reason", "steps"],
        "additionalProperties": False,
    }
    body = json.dumps({
        "model": model, "stream": False, "think": think, "format": schema,
        "options": {"num_ctx": 4096},
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps({"goal": task, "state": state}, ensure_ascii=False)},
        ],
    }).encode()
    LOG.info("Planning (%s): %s", "thinking" if think else "fast", task)
    with urlopen(Request(url.rstrip("/") + "/api/chat", data=body,
                         headers={"Content-Type": "application/json"}), timeout=300) as response:
        data = json.load(response)
    plan = json.loads(data["message"]["content"])
    if plan["status"] not in {"ready", "blocked"}:
        raise ValueError("Model returned invalid plan status")
    if len(plan["steps"]) > 6:
        raise ValueError("Model returned too many steps")
    for step in plan["steps"]:
        if step["action"] not in ACTIONS or not 200 <= step["duration_ms"] <= 3000:
            raise ValueError(f"Invalid plan step: {step}")
    merged = []
    for step in plan["steps"]:
        if merged and step["action"] in MOVEMENT and merged[-1]["action"] == step["action"]:
            merged[-1]["duration_ms"] = min(3000, merged[-1]["duration_ms"] + step["duration_ms"])
        else:
            merged.append(step.copy())
    plan["steps"] = merged
    return plan


def execute(base: str, action: str, duration_ms: int = 1200, game_mode: str = "survival") -> dict:
    if action == "state":
        return bridge_request(base, "/state")
    if action not in ACTIONS:
        raise ValueError(f"Unknown game action: {action}")
    duration_ms = max(200, min(3000, duration_ms))
    result = bridge_request(base, "/action", {"action": action, "duration_ms": duration_ms})
    if action in MOVEMENT or (action == "mine" and game_mode != "creative"):
        try:
            time.sleep(duration_ms / 1000)
        finally:
            bridge_request(base, "/action", {"action": "stop"})
    return result


def reply(base: str, text: str) -> None:
    bridge_request(base, "/reply", {"text": text[:200]})


def run_task(base: str, task: str, mode: str, model: str, ollama: str, max_steps: int,
             cancelled: Callable[[], bool] = lambda: False, think: bool = True) -> None:
    reply(base, f"Составляю план: {task}")
    before = bridge_request(base, "/state")
    if before.get("bot") != "present":
        reply(base, "Бот не появился в мире. Введи /craftmind spawn.")
        return
    if mode == "scripted":
        action = scripted_action(task)
        plan = {"status": "ready" if action else "blocked", "reason": "Команда пока не поддерживается.",
                "steps": [{"action": action, "duration_ms": 1200}] if action else []}
    else:
        plan = ollama_plan(task, before, model, ollama, think)
    if cancelled():
        reply(base, "Задача остановлена.")
        return
    if plan["status"] == "blocked" or not plan["steps"]:
        reply(base, "Пока не могу: " + plan["reason"])
        return
    steps = plan["steps"][:max_steps]
    LOG.info("Plan: %s", steps)
    reply(base, f"План: {', '.join(step['action'] for step in steps)}")
    for number, step in enumerate(steps, 1):
        if cancelled():
            reply(base, "Задача остановлена.")
            return
        action = step["action"]
        before = bridge_request(base, "/state")
        if before.get("bot") != "present":
            reply(base, "Бот пропал из мира. План остановлен.")
            return
        if action == "mine" and before.get("target_block", {}).get("id", "minecraft:air") == "minecraft:air":
            reply(base, "План остановлен: под прицелом воздух, ломать нечего.")
            return
        execute(base, action, step["duration_ms"], before.get("game_mode", "survival"))
        after = bridge_request(base, "/state")
        LOG.info("Step %d/%d: %s; state: %s", number, len(steps), action, after)
        if action == "state":
            reply(base, f"Состояние: {after}")
        else:
            reply(base, f"Шаг {number}/{len(steps)}: {action}")
        if action in MOVEMENT and all(before.get(k) == after.get(k) for k in ("x", "y", "z")):
            reply(base, "План остановлен: бот не сдвинулся.")
            return
    reply(base, "Действия по плану выполнены; результат задачи проверь в мире.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Craftmind agent")
    parser.add_argument("--mode", choices=("scripted", "ollama"), default="scripted")
    parser.add_argument("--model", default="qwen3:14b")
    parser.add_argument("--bridge", default="http://127.0.0.1:8765")
    parser.add_argument("--ollama", default="http://127.0.0.1:11434")
    parser.add_argument("--max-steps", type=int, default=6)
    parser.add_argument("--no-think", action="store_true", help="Ask the model for a plan without reasoning")
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
                         cancelled=lambda: generation[0] != token, think=not args.no_think)
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

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
MISSING_CAPABILITIES = ("none", "place", "craft", "pathfind", "other")


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


def describe_state(state: dict) -> str:
    """Give the model a short factual observation instead of a raw world snapshot."""
    if state.get("bot") != "present":
        return "Бот отсутствует в мире."
    lines = [
        f"Бот в точке x={state.get('x', '?')}, y={state.get('y', '?')}, z={state.get('z', '?')}; "
        f"направление {state.get('facing_degrees', '?')}°; режим {state.get('game_mode', '?')}.",
        f"Здоровье {state.get('health', '?')}; сытость {state.get('food', '?')}; "
        f"на земле: {'да' if state.get('on_ground') else 'нет' if state.get('on_ground') is False else 'неизвестно'}.",
    ]
    below = state.get("block_below")
    lines.append(f"Непосредственно под ногами: {below['id']} в x={below.get('x', '?')}, "
                 f"y={below.get('y', '?')}, z={below.get('z', '?')}."
                 if below else "Блок под ногами пока не определён.")
    target = state.get("target_block")
    lines.append(f"Под прицелом: {target['id']} в x={target['x']}, y={target['y']}, z={target['z']}."
                 if target else "Под прицелом нет блока; можно посмотреть вниз.")
    inventory = state.get("inventory", {})
    lines.append("Инвентарь: " + (", ".join(f"{item} ×{count}" for item, count in sorted(inventory.items())[:8])
                                  if inventory else "пуст") + ".")
    blocks = state.get("nearby_blocks", [])[:8]
    if blocks:
        lines.append("Рядом (датчик, объекты могут быть за стеной): " + "; ".join(
            f"{block['id']} ×{block['count']}, ближайший x={block['x']}, y={block['y']}, z={block['z']}"
            for block in blocks) + ".")
    entities = state.get("nearby_entities", [])[:6]
    if entities:
        lines.append("Существа рядом: " + "; ".join(
            f"{entity['id']} x={entity['x']}, y={entity['y']}, z={entity['z']}" for entity in entities) + ".")
    return "\n".join(lines)


def verified_missing_capability(task: str, capability: str) -> bool:
    """Accept a refusal only when the goal explicitly asks for an unavailable tool."""
    words = task.lower()
    examples = {
        "place": ("построй", "построить", "поставь блок", "установи блок", "размести блок", "build", "place"),
        "craft": ("скрафт", "крафти", "создай предмет", "сделай предмет", "craft"),
        "pathfind": ("найди путь", "дойди до", "подойди к", "иди к ", "find path", "go to "),
    }
    return any(example in words for example in examples.get(capability, ()))


def ollama_plan(task: str, state: dict, model: str, url: str, think: bool = True,
                history: list[str] | None = None, force_action: bool = False) -> dict:
    prompt = (
        "Ты управляешь ботом Minecraft. Составь следующую короткую фазу плана максимум из 6 действий. "
        "ready означает, что после этих действий цель выполнена; continue — что после них нужно новое наблюдение "
        "и следующая фаза. Уже выполненные действия не повторяй. "
        "После каждого действия программа проверяет состояние мира. "
        "Движение задаётся длительностью в миллисекундах от 200 до 3000. "
        "Для 'пройди немного вперёд' нужен ОДИН forward около 1000-1500 мс, не повторяй его. "
        "turn_left/right поворачивает на 90 градусов; left/right — шаг вбок. "
        "look_down направляет взгляд вниз. mine ломает ОДИН блок в творческом режиме и держит атаку "
        "до 2 секунд в выживании. Чтобы сломать два блока под собой, используй look_down, mine, mine. "
        "В творческом режиме можно ходить и ломать блоки пустой рукой. "
        "Неизвестный блок под ногами не мешает начать движение: посмотри вниз после движения. "
        "Если добыча зависит от того, что окажется под прицелом после look_down, закончи фазу "
        "на look_down со статусом continue. Не добавляй действия после выполнения запроса. "
        "Блоки рядом — датчик близости, не камера; он может показывать объекты за стеной. "
        "blocked допустим только если НЕТ ни одного полезного доступного действия и цель явно требует "
        "недоступную возможность place, craft или pathfind. Укажи её в missing_capability и назови "
        "конкретную причину. Недостаток сведений о будущем состоянии — не повод для blocked. "
        "Если задача только про статус/осмотр, используй state один раз."
    )
    if force_action:
        prompt += " Предыдущий отказ не подтверждён: выбери хотя бы одно доступное действие; blocked запрещён."
    schema = {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["ready", "continue"] if force_action else ["ready", "continue", "blocked"]},
            "reason": {"type": "string"},
            "missing_capability": {"type": "string", "enum": MISSING_CAPABILITIES},
            "steps": {"type": "array", "minItems": 1 if force_action else 0, "maxItems": 6, "items": {
                "type": "object", "properties": {
                    "action": {"type": "string", "enum": sorted(ACTIONS - {"stop"})},
                    "duration_ms": {"type": "integer", "minimum": 200, "maximum": 3000},
                }, "required": ["action", "duration_ms"], "additionalProperties": False,
            }},
        },
        "required": ["status", "reason", "missing_capability", "steps"],
        "additionalProperties": False,
    }
    completed = "; ".join(history or []) or "пока ничего"
    body = json.dumps({
        "model": model, "stream": False, "think": think, "format": schema,
        "options": {"num_ctx": 4096},
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": f"Задача: {task}\nУже выполнено: {completed}\nНаблюдение сейчас:\n{describe_state(state)}"},
        ],
    }).encode()
    LOG.info("Planning (%s): %s", "thinking" if think else "fast", task)
    with urlopen(Request(url.rstrip("/") + "/api/chat", data=body,
                         headers={"Content-Type": "application/json"}), timeout=300) as response:
        data = json.load(response)
    plan = json.loads(data["message"]["content"])
    if plan["status"] not in ({"ready", "continue"} if force_action else {"ready", "continue", "blocked"}):
        raise ValueError("Model returned invalid plan status")
    if plan.get("missing_capability") not in MISSING_CAPABILITIES:
        raise ValueError("Model returned invalid missing capability")
    if len(plan["steps"]) > 6:
        raise ValueError("Model returned too many steps")
    if plan["status"] != "blocked" and not plan["steps"]:
        raise ValueError("Model returned an empty actionable plan")
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
    LOG.info("Model plan: status=%s missing=%s reason=%s steps=%s", plan["status"],
             plan["missing_capability"], plan["reason"], plan["steps"])
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
    history: list[str] = []
    while len(history) < max_steps:
        observation = bridge_request(base, "/state")
        if observation.get("bot") != "present":
            reply(base, "Бот не появился в мире. Введи /craftmind spawn.")
            return
        if mode == "scripted":
            action = scripted_action(task)
            plan = {"status": "ready" if action else "blocked", "reason": "Команда пока не поддерживается.",
                    "steps": [{"action": action, "duration_ms": 1200}] if action else []}
        else:
            plan = ollama_plan(task, observation, model, ollama, think, history)
        if cancelled():
            reply(base, "Задача остановлена.")
            return
        deferred_capability = None
        if plan["status"] == "blocked":
            capability = plan.get("missing_capability", "none")
            if mode == "scripted":
                reply(base, "Пока не могу: " + plan["reason"])
                return
            confirmed = not plan["steps"] and verified_missing_capability(task, capability)
            if confirmed and (history or not scripted_action(task)):
                reasons = {
                    "place": "Нужно ставить блоки, а действие установки пока не реализовано.",
                    "craft": "Нужен крафт, а действие создания предметов пока не реализовано.",
                    "pathfind": "Нужен поиск пути до цели, а такого действия пока нет.",
                }
                reply(base, "Пока не могу: " + reasons[capability])
                return
            if confirmed:
                deferred_capability = capability
                LOG.info("Deferring unavailable %s until useful actions run", capability)
            else:
                LOG.warning("Unverified model refusal; requesting an actionable plan: %s", plan["reason"])
            plan = ollama_plan(task, observation, model, ollama, think, history, force_action=True)
            if cancelled():
                reply(base, "Задача остановлена.")
                return
        steps = plan["steps"][:max_steps - len(history)]
        LOG.info("Executing phase: %s", steps)
        reply(base, f"План: {', '.join(step['action'] for step in steps)}")
        needs_replan = False
        for index, step in enumerate(steps):
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
            history.append(f"{action}: x={after.get('x', '?')}, y={after.get('y', '?')}, z={after.get('z', '?')}")
            LOG.info("Step %d/%d: %s; state: %s", len(history), max_steps, action, after)
            if action == "state":
                reply(base, f"Состояние: {describe_state(after)}")
            else:
                reply(base, f"Шаг {len(history)}: {action}")
            if action in MOVEMENT and all(before.get(k) == after.get(k) for k in ("x", "y", "z")):
                reply(base, "План остановлен: бот не сдвинулся.")
                return
            if action == "look_down" and any(next_step["action"] == "mine" for next_step in steps[index + 1:]):
                needs_replan = True
                break
        if not needs_replan and plan["status"] != "continue" and deferred_capability is None:
            reply(base, "Действия по плану выполнены; результат задачи проверь в мире.")
            return
    reply(base, "Достигнут лимит действий для одной задачи. Выполненное проверь в мире.")


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

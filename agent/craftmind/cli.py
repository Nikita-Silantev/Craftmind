"""Local agent that observes Minecraft and executes bounded tasks."""

import argparse
import json
import logging
import math
import queue
import re
import threading
import time
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

LOG = logging.getLogger("craftmind")
MOVEMENT = {"forward", "back", "left", "right", "sprint_forward"}
ACTIONS = MOVEMENT | {"turn_left", "turn_right", "look_down", "look_up", "jump", "stop", "attack", "mine", "use", "state", "select_block", "place", "place_at", "place_line"}
MISSING_CAPABILITIES = ("none", "craft", "pathfind", "other")
MAX_DISTANCE_BLOCKS = 128
PLACEMENT_ACTIONS = {"place", "place_at", "place_line"}


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
    if any(word in words for word in ("поставь", "размести", "place")):
        return "place"
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
        f"направление {state.get('facing_cardinal', '?')} ({state.get('facing_degrees', '?')}°); "
        f"режим {state.get('game_mode', '?')}.",
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
    lines.append(f"В руке: {state.get('held_item', 'неизвестно')}; выбранный слот: {state.get('selected_slot', '?')}.")
    if state.get("hotbar"):
        occupied = [f"{slot}: {item}" for slot, item in enumerate(state["hotbar"]) if item != "minecraft:air"]
        lines.append("Панель быстрого доступа: " + (", ".join(occupied) if occupied else "пуста") + ".")
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
        "craft": ("скрафт", "крафти", "создай предмет", "сделай предмет", "craft"),
        "pathfind": ("найди путь", "дойди до", "подойди к", "иди к ", "find path", "go to "),
    }
    return any(example in words for example in examples.get(capability, ()))


def normalize_explicit_distance(task: str, steps: list[dict], history: list[str]) -> None:
    """Keep a stated distance as one movement goal, even if the model repeats forward."""
    if history and not is_simple_distance_goal(task):
        return
    matches = list(re.finditer(r"(\d+(?:[.,]\d+)?)\s*(?:блок(?:ов|а)?|шаг(?:ов|а)?|blocks?|steps?)\b",
                               task.lower()))
    if len(matches) != 1:
        return
    distance = float(matches[0].group(1).replace(",", "."))
    if not 0.25 <= distance <= MAX_DISTANCE_BLOCKS:
        return
    movement = [step for step in steps if step["action"] in MOVEMENT]
    if not movement:
        return
    tail = task.lower()[matches[0].end():matches[0].end() + 16]
    head = task.lower()[max(0, matches[0].start() - 20):matches[0].start()]
    if not any(word in head + tail for word in ("пройди", "иди", "пробеги", "беги", "впер", "назад", "влево", "вправо", "walk", "go", "run", "forward", "back", "left", "right")):
        return
    nearby = tail if any(word in tail for word in ("впер", "назад", "влево", "вправо", "forward", "back", "left", "right")) else head
    if "впер" in nearby or "forward" in nearby:
        candidates = {"forward", "sprint_forward"}
    elif "назад" in nearby or "back" in nearby:
        candidates = {"back"}
    elif "влево" in nearby or "left" in nearby:
        candidates = {"left"}
    elif "вправо" in nearby or "right" in nearby:
        candidates = {"right"}
    else:
        candidates = {movement[0]["action"]} if len(movement) == 1 else set()
    for step in movement:
        if step["action"] in candidates:
            if any(f" {step['action']} " in entry and "distance=" in entry for entry in history):
                return
            step["distance_blocks"] = distance
            return


def is_simple_distance_goal(task: str) -> bool:
    return re.fullmatch(
        r"\s*(?:пройди|иди|пробеги|беги|walk|go|run)\s+\d+(?:[.,]\d+)?\s+"
        r"(?:блок(?:ов|а)?|шаг(?:ов|а)?|blocks?|steps?)\s+"
        r"(?:вперёд|вперед|назад|влево|вправо|forward|back|left|right)"
        r"[.!? ]*(?:пожалуйста[.!? ]*)?",
        task.lower()) is not None


def row_block_count(task: str) -> int | None:
    words = task.lower()
    if not any(phrase in words for phrase in ("в ряд", "линию", "in a row", "line of")):
        return None
    number = re.search(r"\b(\d+|два|две|три|четыре|пять|two|three|four|five)\b", words)
    if not number:
        return None
    count = {"два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5,
             "two": 2, "three": 3, "four": 4, "five": 5}.get(number.group(1))
    count = count if count is not None else int(number.group(1))
    return count if 2 <= count <= 5 else None


def is_pure_placement_goal(task: str) -> bool:
    words = task.lower()
    return (any(verb in words for verb in ("поставь", "размести", "установи", "place"))
            and not any(verb in words for verb in ("пройди", "иди ", "беги", "пробеги", "поверни",
                                                    "потом", "затем", "walk", "turn", "then")))


def row_direction(task: str, fallback: str) -> str:
    words = task.lower()
    if any(phrase in words for phrase in ("справа", "вправо", "to the right")):
        return "right"
    if any(phrase in words for phrase in ("слева", "влево", "to the left")):
        return "left"
    if any(phrase in words for phrase in ("сзади", "позади", "behind you")):
        return "back"
    if any(phrase in words for phrase in ("перед собой", "впереди", "in front")):
        return "front"
    return fallback if fallback in {"front", "back", "left", "right"} else "front"


def ollama_plan(task: str, state: dict, model: str, url: str, think: bool = True,
                history: list[str] | None = None, force_action: bool = False,
                force_placement: bool = False) -> dict:
    prompt = (
        "Ты управляешь ботом Minecraft. Составь следующую короткую фазу плана максимум из 6 действий. "
        "ready означает, что после этих действий цель выполнена; continue — что после них нужно новое наблюдение "
        "и следующая фаза. Уже выполненные действия не повторяй. "
        "После каждого действия программа проверяет состояние мира. "
        "Для движения на заданное расстояние используй ОДИН forward/back/left/right/sprint_forward "
        "с distance_blocks от 0.25 до 128. Например, 'пройди 15 блоков вперёд' — ОДИН forward "
        "с distance_blocks=15, а не 15 вызовов. Программа сама измеряет пройденное расстояние и останавливает бота. "
        "Если расстояние не задано, используй duration_ms от 200 до 3000 для короткого движения. "
        "turn_left/right поворачивает на 90 градусов; left/right — шаг вбок. "
        "look_down направляет взгляд вниз. mine ломает ОДИН блок в творческом режиме и держит атаку "
        "до 2 секунд в выживании. Чтобы сломать два блока под собой, используй look_down, mine, mine. "
        "В творческом режиме можно ходить и ломать блоки пустой рукой. "
        "select_block с block_id (например minecraft:oak_planks) выбирает блок в панели; в творческом "
        "режиме бот может взять любой доступный блок Minecraft, в выживании только имеющийся в инвентаре. "
        "Для установки блока НЕ нужно вручную поворачивать камеру или вызывать look_down: мод "
        "сам вычисляет угол, находит видимую опору, наводит прицел и восстанавливает прежний взгляд. "
        "place ставит один блок в соседнюю клетку; placement=front/back/left/right, по умолчанию front. "
        "place_at ставит блок в выбранную клетку мира с целыми x,y,z в пределах досягаемости. "
        "place_line ставит ряд из count от 2 до 5 блоков в направлении placement=front/back/left/right; "
        "мод сам вычисляет клетки и ставит дальние блоки первыми. "
        "Например, 'поставь три железных блока в ряд' — ОДИН place_line с "
        "block_id=minecraft:iron_block, count=3, placement=front. "
        "В каждом place/place_at/place_line можно указать block_id, тогда бот сначала выберет материал. "
        "Если материал не назван и рука пуста, "
        "в творческом режиме по умолчанию будут выбраны дубовые доски. "
        "Не ставь блок в minecraft:air без соседнего твёрдого блока-опоры. "
        "После установки проверяй результат по наблюдению. "
        "Неизвестный блок под ногами не мешает начать движение: посмотри вниз после движения. "
        "Если добыча зависит от того, что окажется под прицелом после look_down, закончи фазу "
        "на look_down со статусом continue. Не добавляй действия после выполнения запроса. "
        "Блоки рядом — датчик близости, не камера; он может показывать объекты за стеной. "
        "blocked допустим только если НЕТ ни одного полезного доступного действия и цель явно требует "
        "недоступную возможность craft или pathfind. Установка блоков доступна и не является причиной blocked. "
        "Укажи недоступную возможность в missing_capability и назови "
        "конкретную причину. Недостаток сведений о будущем состоянии — не повод для blocked. "
        "Если задача только про статус/осмотр, используй state один раз."
    )
    if force_action:
        prompt += " Предыдущий отказ не подтверждён: выбери хотя бы одно доступное действие; blocked запрещён."
    if force_placement:
        prompt += (" Следующий план должен содержать установку блока без поворотов и look_down. "
                   "Для ряда используй один place_line с count и block_id; для одного блока place или place_at.")
    allowed_actions = (["place_line"] if row_block_count(task) else ["place", "place_at"])
    if not force_placement:
        allowed_actions = sorted(ACTIONS - {"stop"})
    schema = {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["ready", "continue"] if force_action else ["ready", "continue", "blocked"]},
            "reason": {"type": "string"},
            "missing_capability": {"type": "string", "enum": MISSING_CAPABILITIES},
            "steps": {"type": "array", "minItems": 1 if force_action else 0,
                      "maxItems": 1 if force_placement else 6, "items": {
                "type": "object", "properties": {
                    "action": {"type": "string", "enum": allowed_actions},
                    "duration_ms": {"type": "integer", "minimum": 200, "maximum": 3000},
                    "distance_blocks": {"type": "number", "minimum": 0.25, "maximum": MAX_DISTANCE_BLOCKS},
                    "block_id": {"type": "string"},
                    "placement": {"type": "string", "enum": ["front", "back", "left", "right", "target"]},
                    "count": {"type": "integer", "minimum": 2, "maximum": 5},
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                    "z": {"type": "integer"},
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
    for step in plan["steps"]:
        if step["action"] == "place_line" and "count" not in step and row_block_count(task):
            step["count"] = row_block_count(task)
    if plan["status"] not in ({"ready", "continue"} if force_action else {"ready", "continue", "blocked"}):
        raise ValueError("Model returned invalid plan status")
    if plan.get("missing_capability") not in MISSING_CAPABILITIES:
        raise ValueError("Model returned invalid missing capability")
    if len(plan["steps"]) > 6:
        raise ValueError("Model returned too many steps")
    if force_placement and (len(plan["steps"]) != 1 or plan["steps"][0]["action"] not in allowed_actions):
        raise ValueError("Model did not return the required placement action")
    if plan["status"] != "blocked" and not plan["steps"]:
        raise ValueError("Model returned an empty actionable plan")
    for step in plan["steps"]:
        if step["action"] not in ACTIONS or not 200 <= step["duration_ms"] <= 3000:
            raise ValueError(f"Invalid plan step: {step}")
        if "distance_blocks" in step and (step["action"] not in MOVEMENT or
                                          not 0.25 <= step["distance_blocks"] <= MAX_DISTANCE_BLOCKS):
            raise ValueError(f"Invalid movement distance: {step}")
        if step["action"] == "select_block" and not step.get("block_id"):
            raise ValueError("select_block requires block_id")
        if step["action"] == "place_at" and not all(isinstance(step.get(axis), int) for axis in ("x", "y", "z")):
            raise ValueError("place_at requires integer x, y, z")
        if step["action"] == "place_line" and (not isinstance(step.get("count"), int)
                                                or not 2 <= step["count"] <= 5
                                                or step.get("placement", "front") == "target"):
            raise ValueError("place_line requires count 2-5 and a relative direction")
    merged = []
    for step in plan["steps"]:
        if merged and step["action"] in MOVEMENT and merged[-1]["action"] == step["action"]:
            if "distance_blocks" in step and "distance_blocks" in merged[-1]:
                merged[-1]["distance_blocks"] = min(MAX_DISTANCE_BLOCKS,
                    merged[-1]["distance_blocks"] + step["distance_blocks"])
            elif "distance_blocks" not in step and "distance_blocks" not in merged[-1]:
                merged[-1]["duration_ms"] = min(3000, merged[-1]["duration_ms"] + step["duration_ms"])
            else:
                merged.append(step.copy())
        else:
            merged.append(step.copy())
    if any(step["action"] in {"place", "place_at", "place_line"} for step in merged) and "mine" not in {
            step["action"] for step in merged} and not any(phrase in task.lower() for phrase in (
            "смотри вниз", "посмотри вниз", "look down")):
        merged = [step for step in merged if step["action"] not in {"look_down", "look_up"}]
    if is_pure_placement_goal(task) and any(step["action"] in PLACEMENT_ACTIONS for step in merged):
        merged = [step for step in merged if step["action"] not in {"turn_left", "turn_right"}]
    row_count = row_block_count(task)
    if row_count and is_pure_placement_goal(task):
        placement = next((step for step in merged if step["action"] in PLACEMENT_ACTIONS), None)
        if placement is not None:
            material = placement.get("block_id") or next((step.get("block_id") for step in merged
                if step["action"] == "select_block" and step.get("block_id")), None)
            line = {"action": "place_line", "duration_ms": placement["duration_ms"],
                    "count": row_count, "placement": row_direction(task, placement.get("placement", "front"))}
            if material:
                line["block_id"] = material
            merged = [line]
            plan["status"] = "ready"
    normalize_explicit_distance(task, merged, history or [])
    plan["steps"] = merged
    LOG.info("Model plan: status=%s missing=%s reason=%s steps=%s", plan["status"],
             plan["missing_capability"], plan["reason"], plan["steps"])
    return plan


def move_distance(base: str, action: str, distance_blocks: float,
                  cancelled: Callable[[], bool] = lambda: False) -> dict:
    start = bridge_request(base, "/state")
    if start.get("bot") != "present":
        raise ValueError("Бот отсутствует в мире")
    speed = 4.3 if action != "sprint_forward" else 5.6
    previous_distance = 0.0
    stalled = 0
    for _ in range(128):
        if cancelled():
            return {"ok": True, "cancelled": True, "distance_moved": round(previous_distance, 2)}
        remaining = distance_blocks - previous_distance
        if remaining <= 0.3:
            return {"ok": True, "distance_moved": round(previous_distance, 2)}
        duration_ms = max(200, min(1500, round((remaining - 0.15) / speed * 1000)))
        result = bridge_request(base, "/action", {"action": action, "duration_ms": duration_ms})
        if not result.get("ok", True):
            raise ValueError(result.get("error", "Движение не началось"))
        try:
            time.sleep(duration_ms / 1000)
        finally:
            bridge_request(base, "/action", {"action": "stop"})
        current = bridge_request(base, "/state")
        if current.get("bot") != "present":
            raise ValueError("Бот пропал из мира во время движения")
        progress = math.hypot(float(current["x"]) - float(start["x"]),
                              float(current["z"]) - float(start["z"]))
        delta = progress - previous_distance
        stalled = stalled + 1 if delta < 0.1 else 0
        if stalled >= 2:
            raise ValueError(f"Бот упёрся в препятствие после {progress:.1f} из {distance_blocks:g} блоков")
        if delta > 0.05:
            speed = max(0.5, min(8.0, delta / (duration_ms / 1000)))
        previous_distance = progress
    raise ValueError("Движение прервано: слишком много коротких отрезков")


def execute(base: str, action: str, duration_ms: int = 1200, game_mode: str = "survival",
            distance_blocks: float | None = None, block_id: str | None = None,
            placement: str = "front", count: int | None = None,
            x: int | None = None, y: int | None = None, z: int | None = None,
            cancelled: Callable[[], bool] = lambda: False) -> dict:
    if action == "state":
        return bridge_request(base, "/state")
    if action not in ACTIONS:
        raise ValueError(f"Unknown game action: {action}")
    if action in MOVEMENT and distance_blocks is not None:
        return move_distance(base, action, distance_blocks, cancelled)
    duration_ms = max(200, min(3000, duration_ms))
    payload = {"action": action, "duration_ms": duration_ms}
    if block_id and action in {"select_block", "place", "place_at", "place_line"}:
        payload["block_id"] = block_id
    if action in {"place", "place_line"}:
        payload["placement"] = placement
    if action == "place_line":
        if count is None:
            raise ValueError("place_line требует count")
        payload["count"] = count
    if action == "place_at":
        if any(axis is None for axis in (x, y, z)):
            raise ValueError("place_at требует x, y, z")
        payload.update({"x": x, "y": y, "z": z})
    result = bridge_request(base, "/action", payload)
    if not result.get("ok", True):
        raise ValueError(result.get("error", "Действие не выполнено"))
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
    recent_actions: list[str] = []
    completed_count = 0
    repeated_failure = None
    failure_count = 0
    while completed_count < max_steps:
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
            plan = ollama_plan(task, observation, model, ollama, think, history,
                               force_action=True, force_placement=is_pure_placement_goal(task))
            if cancelled():
                reply(base, "Задача остановлена.")
                return
        if mode == "ollama" and is_pure_placement_goal(task) and not any(
                step["action"] in PLACEMENT_ACTIONS for step in plan["steps"]):
            LOG.warning("Planner chose no placement action; requesting a placement plan")
            plan = ollama_plan(task, observation, model, ollama, think, history,
                               force_action=True, force_placement=True)
            if cancelled():
                reply(base, "Задача остановлена.")
                return
        steps = plan["steps"][:max_steps - completed_count]
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
            try:
                if action == "mine" and before.get("target_block", {}).get("id", "minecraft:air") == "minecraft:air":
                    raise ValueError("Под прицелом воздух, ломать нечего")
                result = execute(base, action, step["duration_ms"], before.get("game_mode", "survival"),
                                 distance_blocks=step.get("distance_blocks"), block_id=step.get("block_id"),
                                 placement=step.get("placement", "front"), count=step.get("count"),
                                 x=step.get("x"), y=step.get("y"), z=step.get("z"), cancelled=cancelled)
            except ValueError as error:
                if cancelled():
                    return
                completed_count += 1
                failure = f"{action}: {error}"
                failure_count = failure_count + 1 if failure == repeated_failure else 1
                repeated_failure = failure
                history.append(f"{completed_count}. {failure}; x={before.get('x', '?')}, z={before.get('z', '?')}")
                history = history[-16:]
                LOG.warning("Step %d failed: %s", completed_count, failure)
                if failure_count >= 3:
                    reply(base, f"Остановился после трёх одинаковых неудач: {error}")
                    return
                reply(base, f"Не получилось: {error}. Перепланирую.")
                needs_replan = True
                break
            if cancelled() or result.get("cancelled"):
                return
            repeated_failure = None
            failure_count = 0
            after = bridge_request(base, "/state")
            completed_count += 1
            detail = f" distance={result['distance_moved']:.1f} blocks" if "distance_moved" in result else ""
            if action in {"select_block", "place", "place_at", "place_line"}:
                detail += f" block={result.get('block_id', result.get('placed_block', step.get('block_id', '?')))}"
            if action in {"place", "place_at"} and all(axis in result for axis in ("x", "y", "z")):
                detail += f" at=({result['x']},{result['y']},{result['z']})"
            if action == "place_line":
                detail += f" count={result.get('placed_count', step.get('count', '?'))} positions={result.get('placements', [])}"
            history.append(f"{completed_count}. {action} {detail} x={after.get('x', '?')}, y={after.get('y', '?')}, z={after.get('z', '?')}")
            history = history[-16:]
            recent_actions.append(action)
            recent_actions = recent_actions[-6:]
            LOG.info("Step %d/%d: %s; result: %s; state: %s", completed_count, max_steps, action, result, after)
            if action == "state":
                reply(base, f"Состояние: {describe_state(after)}")
            else:
                reply(base, f"Шаг {completed_count}: {action}{detail}")
            if action in MOVEMENT and all(before.get(k) == after.get(k) for k in ("x", "y", "z")):
                reply(base, "План остановлен: бот не сдвинулся.")
                return
            if len(recent_actions) == 6 and recent_actions in (
                ["turn_left", "turn_right"] * 3, ["turn_right", "turn_left"] * 3,
                ["look_up", "look_down"] * 3, ["look_down", "look_up"] * 3,
            ):
                reply(base, "План остановлен: бот повторяет повороты без прогресса.")
                return
            if action in MOVEMENT and "distance_blocks" in step and is_simple_distance_goal(task):
                reply(base, f"Пройдено {result['distance_moved']:.1f} из {step['distance_blocks']:g} блоков.")
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
    parser.add_argument("--max-steps", type=int, default=128)
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

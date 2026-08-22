from __future__ import annotations

from typing import Any

from ..models import AGENT_KINDS, NODE_COLORS, NODE_LABELS, FlowNode, Workflow

DESCRIPTIONS: dict[str, str] = {
    "entry": "Вхідний промпт користувача та вкладення. Кореневий блок маршруту.",
    "tasks_manager": (
        "Черга послідовних завдань. Вихід NEXT віддає активне завдання, "
        "вихід DONE спрацьовує, коли завдань не лишилось. Обов'язково потребує "
        "повернення виходу TRUE блока Result назад у себе."
    ),
    "prompt_reviewer": "Агент, який уточнює промпт перед виконанням.",
    "executor": "Агент, який виконує задачу і створює файли.",
    "task_reviewer": (
        "Агент-контролер. Має повертати JSON із полем verdict — саме на нього "
        "спирається розгалуження блока Result."
    ),
    "result": (
        "Розгалуження. TRUE — робота прийнята, FALSE — на переробку, "
        "EXHAUSTED (жовтий) — активне завдання вичерпало власний ліміт спроб "
        "і має бути позначене провальним."
    ),
    "work_reviewer": (
        "Аналітик протоколу роботи. Не має портів і не бере участі в маршруті."
    ),
    "calibrator": (
        "Зупиняє Flow після K-го FALSE, продовжує тред Task Reviewer і "
        "готує пояснення та конкретні правки. Вихідних портів немає."
    ),
}

CONFIG_HINTS: dict[str, dict[str, str]] = {
    "calibrator": {
        "false_threshold": (
            "Після якого за рахунком FALSE зупинити Flow і показати "
            "рекомендації. За замовчуванням 1."
        ),
        "skills": (
            "Скіли, закріплені за нодою: список {name, path}. Codex "
            "завантажує їх до першого кроку агента."
        ),
        "thread_source": (
            "id ноди Task Reviewer, чий Codex-тред продовжує аналіз. "
            "Рушій підставляє його сам."
        ),
    }
}


def _config_fields(kind: str) -> dict[str, Any]:
    node = FlowNode.create(kind)
    return {
        name: {"type": type(value).__name__, "default": value}
        for name, value in node.config.items()
    }


def describe_kind(kind: str) -> dict[str, Any]:
    if kind not in NODE_LABELS:
        raise ValueError(f"Невідомий тип ноди: {kind}")
    node = FlowNode.create(kind)
    workflow = Workflow(nodes=[node])
    return {
        "kind": kind,
        "label": NODE_LABELS[kind],
        "color": NODE_COLORS[kind],
        "is_agent": kind in AGENT_KINDS,
        "ports": list(workflow.ports_of(node.id)),
        "description": DESCRIPTIONS[kind],
        "config_fields": _config_fields(kind),
        "config": CONFIG_HINTS.get(kind, {}),
    }


def node_kinds() -> list[dict[str, Any]]:
    return [describe_kind(kind) for kind in NODE_LABELS]

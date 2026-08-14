from collections.abc import Mapping
from dataclasses import dataclass
from importlib.resources import files
from math import isfinite
from pathlib import Path
from typing import Any

import yaml

from .target_schema import is_safe_daily_name


class ConfigError(ValueError):
    """Raised when automation configuration is malformed or unsafe."""


@dataclass(frozen=True)
class BotConfig:
    window_title: str
    width: int
    height: int
    min_confidence: float
    max_unchanged_actions: int
    action_timeout_seconds: float
    battle_timeout_seconds: float
    imessage_recipient: str
    blocked_states: frozenset[str]
    daily_whitelist: tuple[str, ...]
    window_owner: str | None = None
    keymap: tuple[tuple[str, str], ...] = (
        ("story_skip", "`"),
        ("task_pathfind", "t"),
    )


DEFAULTS = {
    "window": {"title": "问道", "width": 886, "height": 672},
    "safety": {"min_confidence": 0.88, "max_unchanged_actions": 3},
    "timeouts": {"action": 12.0, "battle": 120.0},
    "notification": {"recipient": "wendao-owner@example.com"},
    "blocked_states": [
        "payment",
        "captcha",
        "blocked_choice",
        "dead",
        "bag_full",
        "disconnected",
    ],
    "daily_whitelist": ["师门", "除暴", "修行"],
    "keymap": {"story_skip": "`", "task_pathfind": "t"},
}

_MAPPING_SECTIONS = ("window", "safety", "timeouts", "notification")


def _merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        current = result.get(key)
        result[key] = (
            _merge(current, value)
            if isinstance(current, Mapping) and isinstance(value, Mapping)
            else value
        )
    return result


def _require_mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ConfigError(f"{name} must be a mapping")
    return value


def _require_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{name} must be a non-empty string")
    return value


def _require_positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ConfigError(f"{name} must be a positive integer")
    return value


def _require_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{name} must be numeric")
    number = float(value)
    if not isfinite(number):
        raise ConfigError(f"{name} must be finite")
    return number


def _require_string_list(value: Any, name: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ConfigError(f"{name} must be a list of strings")
    return value


def _require_daily_names(value: Any) -> tuple[str, ...]:
    names = _require_string_list(value, "daily_whitelist")
    if not names:
        raise ConfigError("daily_whitelist must not be empty")
    if any(not is_safe_daily_name(name) for name in names):
        raise ConfigError(
            "daily_whitelist names must be 1-32 alphanumeric, underscore, or hyphen characters"
        )
    if len(set(names)) != len(names):
        raise ConfigError("daily_whitelist must contain unique names")
    return tuple(names)


def _require_keymap(value: Any) -> tuple[tuple[str, str], ...]:
    mapping = _require_mapping(value, "keymap")
    for name, key in mapping.items():
        if not is_safe_daily_name(name):
            raise ConfigError(
                "keymap names must be 1-32 alphanumeric, underscore, or hyphen characters"
            )
        if not (
            isinstance(key, str)
            and len(key) == 1
            and key.isascii()
            and key.isprintable()
        ):
            raise ConfigError("keymap values must be single printable ASCII characters")
    return tuple(sorted((str(name), str(key)) for name, key in mapping.items()))


def _read_config(path: Path | None) -> str:
    if path is None:
        return files("wendao_bot").joinpath("default.yaml").read_text("utf-8")
    return path.read_text(encoding="utf-8")


def load_config(path: Path | None = None) -> BotConfig:
    try:
        loaded = yaml.safe_load(_read_config(path))
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML: {exc}") from exc

    if loaded is None:
        raw = {}
    else:
        raw = _require_mapping(loaded, "configuration")

    for section in _MAPPING_SECTIONS:
        if section in raw:
            _require_mapping(raw[section], section)

    for field in ("blocked_states", "daily_whitelist"):
        if field in raw:
            _require_string_list(raw[field], field)

    data = _merge(DEFAULTS, raw)
    window = _require_mapping(data["window"], "window")
    safety = _require_mapping(data["safety"], "safety")
    timeouts = _require_mapping(data["timeouts"], "timeouts")
    notification = _require_mapping(data["notification"], "notification")

    confidence = _require_number(safety["min_confidence"], "safety.min_confidence")
    if not 0.5 <= confidence <= 1.0:
        raise ConfigError("safety.min_confidence must be between 0.5 and 1.0")
    action_timeout = _require_number(timeouts["action"], "timeouts.action")
    battle_timeout = _require_number(timeouts["battle"], "timeouts.battle")
    if action_timeout <= 0:
        raise ConfigError("timeouts.action must be positive")
    if battle_timeout <= 0:
        raise ConfigError("timeouts.battle must be positive")

    owner = window.get("owner")
    if owner is not None:
        owner = _require_string(owner, "window.owner")

    return BotConfig(
        window_owner=owner,
        window_title=_require_string(window["title"], "window.title"),
        width=_require_positive_int(window["width"], "window.width"),
        height=_require_positive_int(window["height"], "window.height"),
        min_confidence=confidence,
        max_unchanged_actions=_require_positive_int(
            safety["max_unchanged_actions"], "safety.max_unchanged_actions"
        ),
        action_timeout_seconds=action_timeout,
        battle_timeout_seconds=battle_timeout,
        imessage_recipient=_require_string(
            notification["recipient"], "notification.recipient"
        ),
        blocked_states=frozenset(
            _require_string_list(data["blocked_states"], "blocked_states")
        ),
        daily_whitelist=_require_daily_names(data["daily_whitelist"]),
        keymap=_require_keymap(data["keymap"]),
    )

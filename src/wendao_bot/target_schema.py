"""Shared privacy-safe schema for configured daily activity names."""
from __future__ import annotations

MAX_DAILY_NAME_LENGTH = 32


def is_safe_daily_name(name: object) -> bool:
    return bool(
        isinstance(name, str)
        and 0 < len(name) <= MAX_DAILY_NAME_LENGTH
        and all(
            character.isalnum() or character in {"-", "_"}
            for character in name
        )
    )

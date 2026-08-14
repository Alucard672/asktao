"""Simple AFK loop driven by OCR word coordinates."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from .recognizer import BLOCKED_KEYWORDS, ScreenRecognizer, merge_adjacent_words

AFK_CLICK_TEXTS = ("领取奖励", "领取", "点击继续", "确定", "领奖", "继续任务")
AFK_BLOCKED_KEYWORDS = BLOCKED_KEYWORDS
AFK_STORY_HINTS = ("剧情", "跳过")
AFK_SCREEN_SLOTS = 20
MAX_CONSECUTIVE_ERRORS = 10

OcrWords = Callable[[Path], "list[tuple[str, int, int, int, int]]"]


@dataclass(frozen=True)
class AfkStatus:
    state: str
    reason: str | None
    last_action: str | None
    cycles: int


class AfkLoop:
    def __init__(
        self,
        session,
        ocr_words: OcrWords,
        keymap: Mapping[str, str],
        click_texts: tuple[str, ...] = AFK_CLICK_TEXTS,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        stop_event: threading.Event | None = None,
        on_status: Callable[[AfkStatus], None] | None = None,
        pathfind_cooldown_seconds: float = 20.0,
        interval_seconds: float = 2.0,
        runtime_dir: Path = Path("runtime"),
    ) -> None:
        for name in ("story_skip", "task_pathfind"):
            if name not in keymap:
                raise ValueError(f"keymap is missing required entry {name!r}")
        if not click_texts:
            raise ValueError("click_texts must not be empty")
        if pathfind_cooldown_seconds < 0:
            raise ValueError("pathfind_cooldown_seconds must not be negative")
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        self.session = session
        self._ocr_words = ocr_words
        self.keymap = dict(keymap)
        self.click_texts = tuple(click_texts)
        self._sleeper = sleeper
        self._clock = clock
        self.stop_event = stop_event or threading.Event()
        self._on_status = on_status
        self.pathfind_cooldown_seconds = float(pathfind_cooldown_seconds)
        self.interval_seconds = float(interval_seconds)
        self._screen_dir = Path(runtime_dir) / "screens"
        self._cycles = 0
        self._last_pathfind: float | None = None
        self._last_emitted: tuple[str, str | None, str | None] | None = None

    def step(self) -> AfkStatus:
        slot = self._cycles % AFK_SCREEN_SLOTS
        self._cycles += 1
        path = self.session.capture_to(self._screen_dir / f"afk-{slot:06d}.png")
        phrases = merge_adjacent_words(list(self._ocr_words(Path(path))))
        text = "\n".join(phrase for phrase, _x, _y in phrases)

        for state, keywords in AFK_BLOCKED_KEYWORDS.items():
            if any(
                ScreenRecognizer._has_blocked_keyword(text, keyword)
                for keyword in keywords
            ):
                return self._status("paused", reason=state.value)

        for target in self.click_texts:
            for phrase, x, y in phrases:
                if phrase == target:
                    self.session.click(x, y)
                    return self._status("running", last_action=f"click:{target}")

        if any(
            hint in phrase for phrase, _x, _y in phrases for hint in AFK_STORY_HINTS
        ):
            self.session.send_key(self.keymap["story_skip"])
            return self._status("running", last_action="key:story_skip")

        now = self._clock()
        if (
            self._last_pathfind is None
            or now - self._last_pathfind >= self.pathfind_cooldown_seconds
        ):
            self._last_pathfind = now
            self.session.send_key(self.keymap["task_pathfind"])
            return self._status("running", last_action="key:task_pathfind")

        return self._status("running")

    def run(self) -> AfkStatus:
        consecutive_errors = 0
        last_error: str | None = None
        while not self.stop_event.is_set():
            try:
                status = self.step()
                consecutive_errors = 0
                last_error = None
            except Exception as error:
                message = f"{type(error).__name__}: {error}"
                consecutive_errors = (
                    consecutive_errors + 1 if message == last_error else 1
                )
                last_error = message
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    status = self._status("stopped", reason=message)
                    self._emit(status)
                    return status
                status = self._status("paused", reason=message)
            self._emit(status)
            if self.stop_event.is_set():
                break
            self._sleeper(self.interval_seconds)
        status = self._status("stopped", reason="stop requested")
        self._emit(status)
        return status

    def _status(
        self,
        state: str,
        reason: str | None = None,
        last_action: str | None = None,
    ) -> AfkStatus:
        return AfkStatus(
            state=state, reason=reason, last_action=last_action, cycles=self._cycles
        )

    def _emit(self, status: AfkStatus) -> None:
        key = (status.state, status.reason, status.last_action)
        if key == self._last_emitted:
            return
        self._last_emitted = key
        if self._on_status is not None:
            self._on_status(status)

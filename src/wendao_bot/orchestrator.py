"""Fail-closed orchestration for foreground game automation."""

from __future__ import annotations

from datetime import datetime
from dataclasses import dataclass
from enum import Enum
import hashlib
from pathlib import Path
import time
import threading
from typing import Callable

from .models import ActionKind, ScreenSnapshot, ScreenState
from .notifier import PauseNotice


class RunStatus(Enum):
    STOPPED = "stopped"
    OBSERVING = "observing"
    RUNNING = "running"
    PAUSED = "paused"


@dataclass(frozen=True)
class StepResult:
    snapshot: ScreenSnapshot
    observed: ScreenSnapshot
    action_kind: str | None
    action_target: str | None
    status: str

    @property
    def state(self):
        return self.snapshot.state

    def sanitized_event(self) -> dict:
        return {
            "event": "step_observation",
            "state": self.observed.state.value,
            "confidence": self.observed.confidence,
            "targets": sorted(self.observed.targets),
            "action_kind": self.action_kind,
            "action_target": self.action_target,
            "status": self.status,
            "screenshot": self.observed.image_path,
        }


_PREFLIGHT_BLOCKED_STATES = frozenset(
    {
        ScreenState.BLOCKED_CHOICE,
        ScreenState.DISCONNECTED,
        ScreenState.CAPTCHA,
        ScreenState.DEAD,
        ScreenState.BAG_FULL,
        ScreenState.PAYMENT,
        ScreenState.UNKNOWN,
    }
)


def snapshot_is_blocked(snapshot: ScreenSnapshot) -> bool:
    return snapshot.state in _PREFLIGHT_BLOCKED_STATES


class Orchestrator:
    """Execute one conservative observe/plan/authorize/input cycle at a time."""

    def __init__(
        self,
        session,
        recognizer,
        planner,
        safety,
        notifier,
        store,
        progress,
        *,
        progress_extractor=None,
        progress_min_confidence: float = 0.88,
        stop_event: threading.Event | None = None,
        observe_only: bool = False,
        control_dir: Path | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        stable_wait_seconds: float = 0.5,
        action_timeout_seconds: float = 12.0,
        battle_timeout_seconds: float = 120.0,
    ) -> None:
        self.session = session
        self.recognizer = recognizer
        self.planner = planner
        self.safety = safety
        self.notifier = notifier
        self.store = store
        self.progress = progress
        self.progress_extractor = progress_extractor
        self.progress_min_confidence = progress_min_confidence
        self.stop_event = stop_event or threading.Event()
        self.control_dir = Path(control_dir or Path(store.root) / "control")
        self.control_dir.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.sleeper = sleeper
        self.stable_wait_seconds = stable_wait_seconds
        self.action_timeout_seconds = action_timeout_seconds
        self.battle_timeout_seconds = battle_timeout_seconds
        self.status = RunStatus.OBSERVING if observe_only else RunStatus.RUNNING
        self._observe_only = observe_only
        self._capture_number = 0
        self._wait_state = None
        self._wait_started = None
        self._step_observed = None
        self._step_action = None

    def step(self) -> StepResult | None:
        self._step_observed = None
        self._step_action = None
        try:
            snapshot = self._step()
        except Exception as exc:
            snapshot = ScreenSnapshot(
                ScreenState.UNKNOWN, 0.0, "", "unavailable", {}
            )
            self._step_observed = snapshot
            self._pause(f"orchestration failed: {type(exc).__name__}", snapshot)
        if snapshot is None:
            return None
        observed = self._step_observed or snapshot
        result = StepResult(
            snapshot=snapshot,
            observed=observed,
            action_kind=self._step_action.kind.value if self._step_action else None,
            action_target=self._step_action.target if self._step_action else None,
            status=self.status.value,
        )
        self.store.append_event(result.sanitized_event())
        return result

    def _step(self) -> ScreenSnapshot | None:
        if self.stop_event.is_set() or self._flag("stop"):
            self.status = RunStatus.STOPPED
            return None
        if self.status is RunStatus.STOPPED:
            return None
        if self.status is RunStatus.PAUSED:
            if not self._flag("resume"):
                return None
            self._remove_flag("resume")
            self._remove_flag("pause")
            self.status = RunStatus.OBSERVING if self._observe_only else RunStatus.RUNNING

        before, before_bytes = self._capture_and_recognize()
        self._step_observed = before
        if self.stop_event.is_set() or self._flag("stop"):
            self.status = RunStatus.STOPPED
            return before
        if self._flag("pause"):
            self._pause("external pause requested", before)
            return before

        if self._observe_only:
            health = getattr(
                self.recognizer,
                "health",
                lambda: {"ocr": True, "templates": True},
            )()
            if not all(health.get(name, False) for name in ("ocr", "templates")):
                self._pause("preflight recognition health failed", before)
                return before
            try:
                probe = self.planner.next_action(before, self._progress_value())
                self._step_action = probe
                self.safety.authorize(probe, before)
            except Exception as exc:
                self._pause(f"preflight failed: {type(exc).__name__}", before)
                return before
            if snapshot_is_blocked(before):
                self._pause(f"preflight rejected {before.state.value}", before)
            return before

        action = self.planner.next_action(before, self._progress_value())
        self._step_action = action
        try:
            coordinate = self.safety.authorize(action, before)
        except Exception as exc:
            self._pause(f"authorization failed: {type(exc).__name__}", before)
            return before

        if action.kind is ActionKind.PAUSE:
            self._pause(f"planner paused on {before.state.value}", before)
            return before
        if action.kind is ActionKind.WAIT:
            return self._handle_wait(before, action.wait_seconds)
        if action.kind is not ActionKind.CLICK or coordinate is None:
            self._pause("planner returned invalid input action", before)
            return before

        self._wait_state = None
        self._wait_started = None
        if self.stop_event.is_set() or self._flag("stop"):
            self.status = RunStatus.STOPPED
            return before
        try:
            self.session.click(*coordinate)
            after = self._verify_click(action, before, before_bytes)
        except Exception as exc:
            self._pause(f"input verification failed: {type(exc).__name__}", before)
            return before

        return after

    def _verify_click(self, action, before, before_bytes: bytes) -> ScreenSnapshot:
        after = before
        interval = max(self.stable_wait_seconds, self.action_timeout_seconds / 3)
        for _ in range(3):
            if self.stop_event.is_set() or self._flag("stop"):
                self.status = RunStatus.STOPPED
                return after
            self.sleeper(interval)
            after, after_bytes = self._capture_and_recognize()
            self.safety.record_transition(
                hashlib.sha256(before_bytes).hexdigest(),
                hashlib.sha256(after_bytes).hexdigest(),
            )
            if snapshot_is_blocked(after):
                self._pause(f"post-click rejected {after.state.value}", after)
                return after
            changed = after_bytes != before_bytes
            allowed = action.allowed_expected
            if changed and (not allowed or after.state in allowed):
                return after
        expected = ",".join(sorted(state.value for state in action.allowed_expected)) or "changed safe state"
        self._pause(f"expected one of {expected}, observed {after.state.value}", after)
        return after

    def _handle_wait(
        self, snapshot: ScreenSnapshot, requested_wait: float
    ) -> ScreenSnapshot:
        now = self.clock()
        if self._wait_state is not snapshot.state or self._wait_started is None:
            self._wait_state = snapshot.state
            self._wait_started = now
        self.sleeper(max(0.0, requested_wait or self.stable_wait_seconds))
        after, _ = self._capture_and_recognize()
        finished = self.clock()
        if after.state is not snapshot.state:
            self._wait_state = None
            self._wait_started = None
            return after
        timeout = (
            self.battle_timeout_seconds
            if snapshot.state is ScreenState.BATTLE
            else self.action_timeout_seconds
        )
        if finished - self._wait_started >= timeout:
            self._pause(f"{snapshot.state.value} timeout", after)
        return after

    def _capture_and_recognize(self) -> tuple[ScreenSnapshot, bytes]:
        content = self.session.capture()
        self._capture_number += 1
        path = self.store.save_screenshot(
            content, f"capture-{self._capture_number:06d}.png"
        )
        snapshot = self.recognizer.classify(Path(path))
        if Path(snapshot.image_path) != Path(path):
            raise RuntimeError("recognizer snapshot path does not match saved screenshot")
        self._update_progress(snapshot)
        return snapshot, content

    def _update_progress(self, snapshot: ScreenSnapshot) -> None:
        if self.progress_extractor is None:
            return
        trusted_states = {
            ScreenState.MAP,
            ScreenState.NPC_OPTIONS,
            ScreenState.DIALOGUE,
            ScreenState.AUTO_PATH,
            ScreenState.BATTLE,
            ScreenState.REWARD,
            ScreenState.ACTIVITY_LIST,
        }
        if (
            snapshot.state not in trusted_states
            or snapshot.confidence < self.progress_min_confidence
            or "ocr" not in snapshot.evidence
        ):
            return
        self.progress = self.progress_extractor.update(snapshot, self._progress_value())
        self.store.save_state(
            {"status": self.status.value, "progress": self._serialized_progress()}
        )

    def _progress_value(self):
        value = self.progress
        if callable(value):
            return value()
        getter = getattr(value, "get", None)
        return getter() if callable(getter) else value

    def _pause(self, reason: str, snapshot: ScreenSnapshot) -> None:
        self.status = RunStatus.PAUSED
        state = {
            "status": self.status.value,
            "reason": reason,
            "screen_state": snapshot.state.value,
            "screenshot": snapshot.image_path,
            "progress": self._serialized_progress(),
        }
        self.store.save_state(state)
        progress = self._progress_value()
        notice = PauseNotice(
            reason=reason,
            level=getattr(progress, "level", None),
            task=None,
            screenshot=snapshot.image_path,
            timestamp=datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z"),
        )
        try:
            self.notifier.send(notice)
        except Exception as exc:
            self.store.append_event(
                {"event": "notification_failed", "error": type(exc).__name__}
            )

    def _serialized_progress(self) -> dict:
        progress = self._progress_value()
        return {
            "level": getattr(progress, "level", None),
            "main_blocked_by_level": getattr(
                progress, "main_blocked_by_level", False
            ),
            "shimen_completed": getattr(progress, "shimen_completed", 0),
            "completed_dailies": sorted(getattr(progress, "completed_dailies", ())),
        }

    def _flag(self, name: str) -> bool:
        return (self.control_dir / name).is_file()

    def _remove_flag(self, name: str) -> None:
        (self.control_dir / name).unlink(missing_ok=True)

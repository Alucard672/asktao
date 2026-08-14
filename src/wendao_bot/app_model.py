"""Privacy-safe, immutable state exposed to the desktop application."""

from __future__ import annotations

from dataclasses import InitVar, dataclass, replace
import math
from numbers import Real
from pathlib import Path
import re
from typing import Any

from .target_schema import is_safe_daily_name


DEFAULT_MIN_CONFIDENCE = 0.88

_ACTIONABLE_STATES = frozenset(
    {"reward", "dialogue", "activity_list", "npc_options", "map"}
)
_KNOWN_STATES = _ACTIONABLE_STATES | frozenset(
    {
        "disconnected",
        "payment",
        "captcha",
        "blocked_choice",
        "dead",
        "auto_path",
        "battle",
        "bag_full",
        "unknown",
    }
)
_KNOWN_STATUSES = frozenset({"stopped", "running", "paused", "observing"})
_DISPLAY_STATUSES = _KNOWN_STATUSES | {"idle", "error"}
_MODES = frozenset({"observe", "single_step", "continuous"})
_PAUSE_REASONS = frozenset({"automation paused", "internal error"})
_READINESS_FAILURES = frozenset(
    {
        "window_not_found",
        "unknown_failure",
        "multiple_windows",
        "geometry_mismatch",
        "recognition_unavailable",
        "capture_failed",
        "invalid_config",
        "unknown",
        "owner_mismatch",
    }
)
_STANDARD_TARGETS = frozenset(
    {
        "patrol",
        "auto_path",
        "battle_auto",
        "main_quest",
        "npc_main_option",
        "claim",
        "equip",
        "shimen",
        "skip_dialogue",
        "shimen_task",
    }
)
_SCREENSHOT_NAME = re.compile(r"^capture-\d{6}\.png$")
_MAX_PATH_LENGTH = 4096


@dataclass(frozen=True)
class AppViewState:
    """Sanitized values suitable for rendering in a GUI.

    This boundary deliberately excludes OCR text, action coordinates, raw
    recognition evidence, notification details, and exception messages.
    """

    mode: str
    status: str
    screen_state: str
    confidence: float
    target_names: tuple[str, ...]
    screenshot_path: str | None
    pause_reason: str | None
    recognition_ready: bool
    single_step_verified: bool
    can_single_step: bool
    can_run_continuous: bool
    min_confidence: float = DEFAULT_MIN_CONFIDENCE
    window_match: bool | None = None
    ocr_available: bool | None = None
    template_available: bool | None = None
    observe_ready: bool = False
    readiness_failure: str | None = None
    accessibility_trusted: bool | None = None
    window_title: str | None = None
    detected_width: int | None = None
    detected_height: int | None = None
    expected_window_title: InitVar[str | None] = None
    trusted_screenshot_root: InitVar[Path | None] = None
    allowed_target_names: InitVar[tuple[str, ...]] = ()

    def __post_init__(
        self,
        expected_window_title: str | None,
        trusted_screenshot_root: Path | None,
        allowed_target_names: tuple[str, ...],
    ) -> None:
        mode = _safe_mode(self.mode)
        status = (
            self.status
            if isinstance(self.status, str) and self.status in _DISPLAY_STATUSES
            else "error"
        )
        screen_state = (
            self.screen_state
            if isinstance(self.screen_state, str)
            and self.screen_state in _KNOWN_STATES
            else "unknown"
        )
        threshold = (
            float(self.min_confidence)
            if _valid_threshold(self.min_confidence)
            else DEFAULT_MIN_CONFIDENCE
        )
        confidence = (
            float(self.confidence) if _valid_confidence(self.confidence) else 0.0
        )
        targets = (
            tuple(sorted(set(self.target_names)))
            if isinstance(self.target_names, tuple)
            and all(
                _safe_target_name(name, allowed_target_names)
                for name in self.target_names
            )
            else ()
        )
        screenshot_path = _validated_screenshot_path(
            self.screenshot_path, trusted_screenshot_root
        )
        recognition_ready = self.recognition_ready is True
        window_match = (
            self.window_match if isinstance(self.window_match, bool) else None
        )
        ocr_available = (
            self.ocr_available if isinstance(self.ocr_available, bool) else None
        )
        template_available = (
            self.template_available
            if isinstance(self.template_available, bool)
            else None
        )
        observe_ready = bool(
            self.observe_ready is True
            and window_match is True
            and ocr_available is True
            and template_available is True
        )
        readiness_failure = _safe_readiness_failure(self.readiness_failure)
        accessibility_trusted = (
            self.accessibility_trusted
            if isinstance(self.accessibility_trusted, bool)
            else None
        )
        window_title = (
            self.window_title
            if isinstance(self.window_title, str)
            and self.window_title == expected_window_title
            else None
        )
        detected_width = _safe_dimension(self.detected_width)
        detected_height = _safe_dimension(self.detected_height)
        ready = bool(
            status in {"running", "observing"}
            and readiness_failure is None
            and screen_state in _ACTIONABLE_STATES
            and confidence >= threshold
            and targets
            and recognition_ready
            and screenshot_path is not None
            and observe_ready
        )
        can_single_step = (
            ready
            and accessibility_trusted is True
            and self.can_single_step is True
        )
        verified = can_single_step and self.single_step_verified is True
        can_run_continuous = verified and self.can_run_continuous is True
        pause_reason = (
            self.pause_reason
            if isinstance(self.pause_reason, str)
            and self.pause_reason in _PAUSE_REASONS
            else None
        )
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "screen_state", screen_state)
        object.__setattr__(self, "confidence", confidence)
        object.__setattr__(self, "target_names", targets)
        object.__setattr__(self, "screenshot_path", screenshot_path)
        object.__setattr__(self, "pause_reason", pause_reason)
        object.__setattr__(self, "recognition_ready", recognition_ready)
        object.__setattr__(self, "single_step_verified", verified)
        object.__setattr__(self, "can_single_step", can_single_step)
        object.__setattr__(self, "can_run_continuous", can_run_continuous)
        object.__setattr__(self, "min_confidence", threshold)
        object.__setattr__(self, "window_match", window_match)
        object.__setattr__(self, "ocr_available", ocr_available)
        object.__setattr__(self, "template_available", template_available)
        object.__setattr__(self, "observe_ready", observe_ready)
        object.__setattr__(self, "readiness_failure", readiness_failure)
        object.__setattr__(self, "accessibility_trusted", accessibility_trusted)
        object.__setattr__(self, "window_title", window_title)
        object.__setattr__(self, "detected_width", detected_width)
        object.__setattr__(self, "detected_height", detected_height)

    @classmethod
    def initial(cls, *, mode: str = "observe") -> AppViewState:
        return cls(
            mode=_safe_mode(mode),
            status="idle",
            screen_state="unknown",
            confidence=0.0,
            target_names=(),
            screenshot_path=None,
            pause_reason=None,
            recognition_ready=False,
            single_step_verified=False,
            can_single_step=False,
            can_run_continuous=False,
            min_confidence=DEFAULT_MIN_CONFIDENCE,
        )

    @classmethod
    def stopped(cls) -> AppViewState:
        return cls(
            mode="observe",
            status="stopped",
            screen_state="unknown",
            confidence=0.0,
            target_names=(),
            screenshot_path=None,
            pause_reason=None,
            recognition_ready=False,
            single_step_verified=False,
            can_single_step=False,
            can_run_continuous=False,
            min_confidence=DEFAULT_MIN_CONFIDENCE,
        )

    @classmethod
    def starting(cls, *, mode: str = "observe") -> AppViewState:
        safe_mode = _safe_mode(mode)
        return cls(
            mode=safe_mode,
            status="observing" if safe_mode == "observe" else "running",
            screen_state="unknown",
            confidence=0.0,
            target_names=(),
            screenshot_path=None,
            pause_reason=None,
            recognition_ready=False,
            single_step_verified=False,
            can_single_step=False,
            can_run_continuous=False,
            min_confidence=DEFAULT_MIN_CONFIDENCE,
            window_match=None,
            ocr_available=None,
            template_available=None,
            observe_ready=False,
            readiness_failure=None,
        )

    @classmethod
    def from_step(
        cls,
        step: Any,
        *,
        mode: str = "observe",
        single_step_verified: bool = False,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
        screenshot_root: Path | None = None,
        allowed_target_names: tuple[str, ...] = (),
        accessibility_trusted: bool | None = True,
    ) -> AppViewState:
        if not (
            _valid_threshold(min_confidence)
            and isinstance(single_step_verified, bool)
        ):
            return cls.error(mode=mode)

        try:
            event = step.sanitized_event()
        except Exception:
            return cls.error(mode=mode)

        if not isinstance(event, dict):
            return cls.error(mode=mode)

        state = event.get("state")
        confidence = event.get("confidence")
        targets = event.get("targets")
        status = event.get("status")
        screenshot = event.get("screenshot")

        if not (
            isinstance(state, str)
            and state in _KNOWN_STATES
            and _valid_confidence(confidence)
            and isinstance(targets, list)
            and all(
                _safe_target_name(name, allowed_target_names) for name in targets
            )
            and status in _KNOWN_STATUSES
            and isinstance(screenshot, str)
        ):
            return cls.error(mode=mode)

        safe_screenshot = None
        if screenshot_root is not None:
            safe_screenshot = _validated_screenshot_path(screenshot, screenshot_root)
            if safe_screenshot is None:
                return cls.error(mode=mode)

        target_names = tuple(sorted(set(targets)))
        evidence_ready = _has_required_evidence(step)
        ocr_available, template_available = _evidence_availability(step)
        observe_ready = bool(
            safe_screenshot is not None
            and ocr_available is True
            and template_available is True
        )
        ready = bool(
            state in _ACTIONABLE_STATES
            and confidence >= min_confidence
            and target_names
            and evidence_ready
            and safe_screenshot is not None
        )
        verified = single_step_verified and ready
        paused = status == "paused"
        return cls(
            mode=_safe_mode(mode),
            status=status,
            screen_state=state,
            confidence=float(confidence),
            target_names=target_names,
            screenshot_path=safe_screenshot,
            pause_reason="automation paused" if paused else None,
            recognition_ready=evidence_ready,
            single_step_verified=verified,
            can_single_step=ready,
            can_run_continuous=ready and verified,
            min_confidence=float(min_confidence),
            window_match=True,
            ocr_available=ocr_available,
            template_available=template_available,
            observe_ready=observe_ready,
            accessibility_trusted=accessibility_trusted,
            trusted_screenshot_root=screenshot_root,
            allowed_target_names=allowed_target_names,
        )

    @classmethod
    def error(cls, _error: object = None, *, mode: str = "observe") -> AppViewState:
        return cls(
            mode=_safe_mode(mode),
            status="error",
            screen_state="unknown",
            confidence=0.0,
            target_names=(),
            screenshot_path=None,
            pause_reason="internal error",
            recognition_ready=False,
            single_step_verified=False,
            can_single_step=False,
            can_run_continuous=False,
            min_confidence=DEFAULT_MIN_CONFIDENCE,
        )

    @classmethod
    def readiness_error(
        cls,
        reason: object,
        *,
        mode: str = "observe",
        window_title: str | None = None,
        detected_width: int | None = None,
        detected_height: int | None = None,
    ) -> AppViewState:
        bounded = (
            reason
            if isinstance(reason, str) and reason in _READINESS_FAILURES
            else "unknown"
        )
        return cls(
            mode=_safe_mode(mode),
            status="error",
            screen_state="unknown",
            confidence=0.0,
            target_names=(),
            screenshot_path=None,
            pause_reason="internal error",
            recognition_ready=False,
            single_step_verified=False,
            can_single_step=False,
            can_run_continuous=False,
            window_match=False,
            ocr_available=None,
            template_available=None,
            observe_ready=False,
            readiness_failure=bounded,
            window_title=window_title,
            detected_width=detected_width,
            detected_height=detected_height,
            expected_window_title=window_title,
        )

    @classmethod
    def configuration_error(cls) -> AppViewState:
        return cls(
            mode="observe",
            status="stopped",
            screen_state="unknown",
            confidence=0.0,
            target_names=(),
            screenshot_path=None,
            pause_reason=None,
            recognition_ready=False,
            single_step_verified=False,
            can_single_step=False,
            can_run_continuous=False,
            window_match=None,
            ocr_available=None,
            template_available=None,
            observe_ready=False,
            readiness_failure="invalid_config",
        )

    def with_single_step_success(self) -> AppViewState:
        if not self.can_single_step:
            return self
        return replace(
            self,
            single_step_verified=True,
            can_run_continuous=True,
            trusted_screenshot_root=(
                Path(self.screenshot_path).parent if self.screenshot_path else None
            ),
            allowed_target_names=_configured_names_from_targets(self.target_names),
        )

    @property
    def targets(self) -> tuple[str, ...]:
        return self.target_names

    @property
    def screenshot(self) -> str | None:
        return self.screenshot_path


def _safe_mode(mode: object) -> str:
    return mode if isinstance(mode, str) and mode in _MODES else "observe"


def _safe_readiness_failure(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str) and value in _READINESS_FAILURES:
        return value
    return "unknown_failure"


def _allowed_targets(configured_names: object) -> frozenset[str]:
    if not isinstance(configured_names, tuple):
        return _STANDARD_TARGETS
    safe_names = tuple(
        name for name in configured_names if is_safe_daily_name(name)
    )
    configured_targets = {
        target
        for name in safe_names
        for target in (f"daily_{name}", f"daily_task_{name}")
    }
    return _STANDARD_TARGETS | configured_targets


def _safe_target_name(name: object, configured_names: object = ()) -> bool:
    return isinstance(name, str) and name in _allowed_targets(configured_names)


def _configured_names_from_targets(targets: tuple[str, ...]) -> tuple[str, ...]:
    names = []
    for target in targets:
        if target.startswith("daily_task_"):
            names.append(target[len("daily_task_") :])
        elif target.startswith("daily_"):
            names.append(target[len("daily_") :])
    return tuple(sorted(set(names)))


def _validated_screenshot_root(value: object) -> Path | None:
    if not (isinstance(value, Path) and value.is_absolute()):
        return None
    return value.resolve(strict=False)


def _validated_screenshot_path(value: object, trusted_root: object) -> str | None:
    root = _validated_screenshot_root(trusted_root)
    if root is None or not isinstance(value, str):
        return None
    if not 0 < len(value) <= _MAX_PATH_LENGTH:
        return None
    if "@" in value:
        return None
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return None
    path = Path(value)
    if (
        not path.is_absolute()
        or not _SCREENSHOT_NAME.fullmatch(path.name)
        or ".." in path.parts
    ):
        return None
    resolved = path.resolve(strict=False)
    return value if resolved.parent == root else None


def _valid_confidence(value: object) -> bool:
    return bool(
        not isinstance(value, bool)
        and isinstance(value, Real)
        and math.isfinite(value)
        and 0.0 <= value <= 1.0
    )


def _valid_threshold(value: object) -> bool:
    return _valid_confidence(value)


def _safe_dimension(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 16384:
        return value
    return None


def _has_required_evidence(step: object) -> bool:
    try:
        evidence = step.observed.evidence
    except Exception:
        return False
    return bool(
        isinstance(evidence, (set, frozenset))
        and {"ocr", "template"}.issubset(evidence)
    )


def _evidence_availability(step: object) -> tuple[bool | None, bool | None]:
    try:
        evidence = step.observed.evidence
    except Exception:
        return (None, None)
    if not isinstance(evidence, (set, frozenset)):
        return (None, None)
    return ("ocr" in evidence, "template" in evidence)

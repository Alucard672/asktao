import math
from numbers import Real

from .models import Action, ActionKind, ScreenSnapshot, ScreenState


class UnsafeAction(RuntimeError):
    """Raised when an action cannot be safely authorized."""


class SafetyGuard:
    ALLOWED_ACTION_KINDS = frozenset(
        {ActionKind.CLICK, ActionKind.WAIT, ActionKind.PAUSE}
    )
    CLICKABLE_STATES = frozenset(
        {
            ScreenState.MAP,
            ScreenState.NPC_OPTIONS,
            ScreenState.DIALOGUE,
            ScreenState.REWARD,
            ScreenState.ACTIVITY_LIST,
        }
    )
    FORBIDDEN_CLICK_STATES = frozenset(
        {
            ScreenState.PAYMENT,
            ScreenState.CAPTCHA,
            ScreenState.BLOCKED_CHOICE,
            ScreenState.DISCONNECTED,
            ScreenState.DEAD,
            ScreenState.BAG_FULL,
            ScreenState.UNKNOWN,
        }
    )

    def __init__(self, min_confidence: float, max_unchanged_actions: int) -> None:

        if isinstance(min_confidence, bool) or not (
            isinstance(min_confidence, Real)
            and math.isfinite(min_confidence)
            and 0.0 <= min_confidence <= 1.0
        ):
            raise ValueError("min_confidence must be between 0 and 1")

        if isinstance(max_unchanged_actions, bool) or (
            not isinstance(max_unchanged_actions, int)
            or max_unchanged_actions < 1
        ):
            raise ValueError("max_unchanged_actions must be a positive integer")

        self._min_confidence = min_confidence
        self._max_unchanged_actions = max_unchanged_actions
        self._unchanged_actions = 0

    def authorize(
        self, action: Action, snapshot: ScreenSnapshot
    ) -> tuple[int, int] | None:

        if not isinstance(action.kind, ActionKind) or (
            action.kind not in self.ALLOWED_ACTION_KINDS
        ):
            raise UnsafeAction(f"unsupported action kind {action.kind!r}")
        if not isinstance(snapshot.state, ScreenState):
            raise UnsafeAction(f"invalid screen state {snapshot.state!r}")
        if snapshot.confidence < self._min_confidence:
            raise UnsafeAction(
                f"snapshot confidence {snapshot.confidence:.2f} is below minimum "
                f"{self._min_confidence:.2f}"
            )

        if action.kind is not ActionKind.CLICK:
            return None

        if snapshot.state not in self.CLICKABLE_STATES:
            raise UnsafeAction(f"click forbidden in state {snapshot.state.value}")
        if action.target is None:
            raise UnsafeAction("click target is required")
        if action.target not in snapshot.targets:
            raise UnsafeAction(f"target {action.target!r} is absent from snapshot")

        coordinate = snapshot.targets[action.target]

        if not isinstance(coordinate, tuple) or (
            len(coordinate) != 2
            or any(
                isinstance(value, bool) or not isinstance(value, int)
                for value in coordinate
            )
        ):
            raise UnsafeAction(f"target {action.target!r} has invalid coordinate")

        return coordinate

    def record_transition(self, before_signature: str, after_signature: str) -> None:
        if before_signature != after_signature:
            self._unchanged_actions = 0
            return

        self._unchanged_actions += 1
        if self._unchanged_actions >= self._max_unchanged_actions:
            raise UnsafeAction(
                f"screen remained unchanged for {self._unchanged_actions} actions"
            )

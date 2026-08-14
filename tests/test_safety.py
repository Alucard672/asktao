import math

import pytest

from wendao_bot.models import Action, ActionKind, ScreenSnapshot, ScreenState
from wendao_bot.safety import SafetyGuard, UnsafeAction


def snap(
    state: ScreenState,
    confidence: float = 0.99,
    targets: dict[str, tuple[int, int]] | None = None,
) -> ScreenSnapshot:
    return ScreenSnapshot(state, confidence, "", "x.png", targets or {})


@pytest.mark.parametrize(
    "state",
    [
        ScreenState.PAYMENT,
        ScreenState.CAPTCHA,
        ScreenState.BLOCKED_CHOICE,
        ScreenState.DISCONNECTED,
        ScreenState.DEAD,
        ScreenState.BAG_FULL,
        ScreenState.UNKNOWN,
    ],
)
def test_forbidden_state_never_allows_click(state: ScreenState) -> None:
    guard = SafetyGuard(0.88, 3)

    with pytest.raises(UnsafeAction, match="state"):
        guard.authorize(
            Action(ActionKind.CLICK, "claim"),
            snap(state, confidence=1.0, targets={"claim": (1, 1)}),
        )


def test_low_confidence_rejects_action() -> None:
    guard = SafetyGuard(0.88, 3)

    with pytest.raises(UnsafeAction, match="confidence"):
        guard.authorize(Action(ActionKind.WAIT), snap(ScreenState.MAP, 0.87))


def test_confidence_at_threshold_is_allowed() -> None:
    guard = SafetyGuard(0.88, 3)

    assert guard.authorize(Action(ActionKind.WAIT), snap(ScreenState.MAP, 0.88)) is None


def test_click_target_is_required() -> None:
    guard = SafetyGuard(0.88, 3)

    with pytest.raises(UnsafeAction, match="target"):
        guard.authorize(Action(ActionKind.CLICK), snap(ScreenState.MAP))


def test_click_target_must_exist_in_snapshot() -> None:
    guard = SafetyGuard(0.88, 3)

    with pytest.raises(UnsafeAction, match="target"):
        guard.authorize(
            Action(ActionKind.CLICK, "main_quest"), snap(ScreenState.MAP)
        )


def test_click_returns_snapshot_coordinate() -> None:
    guard = SafetyGuard(0.88, 3)

    coordinate = guard.authorize(
        Action(ActionKind.CLICK, "main_quest"),
        snap(ScreenState.MAP, targets={"main_quest": (766, 191)}),
    )

    assert coordinate == (766, 191)


@pytest.mark.parametrize("kind", [ActionKind.WAIT, ActionKind.PAUSE])
def test_non_click_action_does_not_need_target(kind: ActionKind) -> None:
    guard = SafetyGuard(0.88, 3)

    assert guard.authorize(Action(kind), snap(ScreenState.MAP)) is None


def test_expected_state_is_not_treated_as_a_precondition() -> None:
    guard = SafetyGuard(0.88, 3)

    coordinate = guard.authorize(
        Action(ActionKind.CLICK, "main_quest", expected=ScreenState.AUTO_PATH),
        snap(ScreenState.MAP, targets={"main_quest": (766, 191)}),
    )

    assert coordinate == (766, 191)


@pytest.mark.parametrize("kind", [object(), []])
def test_unknown_action_kind_is_rejected(kind: object) -> None:
    guard = SafetyGuard(0.88, 3)
    action = Action(kind)  # type: ignore[arg-type]

    with pytest.raises(UnsafeAction, match="action kind"):
        guard.authorize(action, snap(ScreenState.MAP))


def test_malformed_snapshot_state_is_rejected_for_non_click_action() -> None:
    guard = SafetyGuard(0.88, 3)
    snapshot = snap(ScreenState.MAP)
    object.__setattr__(snapshot, "state", "map")

    with pytest.raises(UnsafeAction, match="screen state"):
        guard.authorize(Action(ActionKind.WAIT), snapshot)


@pytest.mark.parametrize(
    "state",
    [ScreenState.AUTO_PATH, ScreenState.BATTLE],
)
def test_non_clickable_gameplay_state_rejects_click(state: ScreenState) -> None:
    guard = SafetyGuard(0.88, 3)

    with pytest.raises(UnsafeAction, match="state"):
        guard.authorize(
            Action(ActionKind.CLICK, "target"),
            snap(state, targets={"target": (1, 1)}),
        )


@pytest.mark.parametrize(
    "coordinate",
    [
        [1, 2],
        (1,),
        (1, 2, 3),
        (1.0, 2),
        ("1", 2),
        (True, 2),
    ],
)
def test_click_rejects_malformed_coordinate(coordinate: object) -> None:
    guard = SafetyGuard(0.88, 3)

    with pytest.raises(UnsafeAction, match="coordinate"):
        guard.authorize(
            Action(ActionKind.CLICK, "target"),
            snap(ScreenState.MAP, targets={"target": coordinate}),  # type: ignore[dict-item]
        )


def test_unchanged_transition_raises_at_configured_limit() -> None:
    guard = SafetyGuard(0.88, 3)
    for _ in range(2):
        guard.record_transition("same", "same")

    with pytest.raises(UnsafeAction, match="unchanged"):
        guard.record_transition("same", "same")


def test_changed_transition_resets_unchanged_count() -> None:
    guard = SafetyGuard(0.88, 2)
    guard.record_transition("same", "same")
    guard.record_transition("before", "after")
    guard.record_transition("same", "same")

    with pytest.raises(UnsafeAction, match="unchanged"):
        guard.record_transition("same", "same")


@pytest.mark.parametrize(
    ("min_confidence", "max_unchanged_actions"),
    [
        (-0.01, 3),
        (1.01, 3),
        (math.nan, 3),
        (False, 3),
        (0.88, 0),
        (0.88, -1),
        (0.88, 1.5),
        (0.88, False),
    ],
)
def test_invalid_configuration_is_rejected(
    min_confidence: float, max_unchanged_actions: int
) -> None:
    with pytest.raises(ValueError):
        SafetyGuard(min_confidence, max_unchanged_actions)

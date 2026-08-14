import pytest

from wendao_bot.models import Action, ActionKind, ScreenSnapshot, ScreenState


def test_screen_snapshot_rejects_confidence_above_one() -> None:
    with pytest.raises(ValueError, match="confidence"):
        ScreenSnapshot(
            state=ScreenState.UNKNOWN,
            confidence=1.2,
            text="",
            image_path="screen.png",
        )


def test_click_action_retains_target() -> None:
    action = Action(ActionKind.CLICK, "main_quest", ScreenState.AUTO_PATH)

    assert action.target == "main_quest"


def test_screen_snapshot_targets_reject_mutation() -> None:
    snapshot = ScreenSnapshot(
        state=ScreenState.MAP,
        confidence=0.9,
        text="",
        image_path="screen.png",
        targets={"main_quest": (100, 200)},
    )

    with pytest.raises(TypeError):
        snapshot.targets["main_quest"] = (300, 400)


def test_screen_snapshot_targets_are_isolated_from_caller_mutation() -> None:
    targets = {"main_quest": (100, 200)}
    snapshot = ScreenSnapshot(
        state=ScreenState.MAP,
        confidence=0.9,
        text="",
        image_path="screen.png",
        targets=targets,
    )

    targets["main_quest"] = (300, 400)

    assert snapshot.targets["main_quest"] == (100, 200)

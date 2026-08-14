import pytest

from wendao_bot.models import ActionKind, ScreenSnapshot, ScreenState
from wendao_bot.tasks import Progress, ProgressExtractor, TaskPlanner


def snapshot(
    text: str = "", targets: dict[str, tuple[int, int]] | None = None,
    state: ScreenState = ScreenState.MAP,
) -> ScreenSnapshot:
    return ScreenSnapshot(state, 0.99, text, "x.png", targets or {})


def test_main_quest_has_priority() -> None:
    planner = TaskPlanner(("师门", "除暴", "修行"))
    action = planner.next_action(
        snapshot("主线 浮生若梦", {"main_quest": (700, 190), "shimen": (700, 245)}),
        Progress(level=16),
    )
    assert action.target == "main_quest"


def test_progress_extractor_parses_visible_planning_facts() -> None:
    progress = ProgressExtractor(("除暴",)).update(
        snapshot("等级15 主线 20级开启 师门 3/10 除暴 已完成")
    )
    assert progress == Progress(15, True, 3, frozenset({"除暴"}))


def test_unlock_level_is_never_mistaken_for_player_level() -> None:
    progress = ProgressExtractor().update(snapshot("主线 20级开启"))
    assert progress.level is None


@pytest.mark.parametrize("text", ["等级：15", "人物等级15", "角色等级: 15"])
def test_explicit_player_level_labels_are_parsed(text) -> None:
    assert ProgressExtractor().update(snapshot(text)).level == 15


def test_unknown_level_allows_main_but_not_shimen_or_daily() -> None:
    planner = TaskPlanner(("师门", "除暴"))
    assert planner.next_action(
        snapshot(targets={"main_quest": (1, 1)}), Progress(None)
    ).target == "main_quest"
    action = planner.next_action(
        snapshot(targets={"shimen": (1, 1), "daily_除暴": (2, 2)}),
        Progress(None, main_blocked_by_level=True),
    )
    assert action.kind is ActionKind.PAUSE


def test_blocked_main_uses_shimen_before_daily() -> None:
    planner = TaskPlanner(("师门", "除暴", "修行"))
    progress = Progress(level=16, main_blocked_by_level=True)
    action = planner.next_action(
        snapshot(
            "主线 20级开启 师门 1/10 除暴",
            {"shimen": (700, 245), "daily_除暴": (600, 300)},
        ),
        progress,
    )
    assert action.target == "shimen"


def test_shimen_stops_at_ten() -> None:
    planner = TaskPlanner(("师门", "除暴"))
    progress = Progress(level=20, main_blocked_by_level=True, shimen_completed=10)
    action = planner.next_action(
        snapshot("除暴 可参加", {"shimen": (700, 245), "daily_除暴": (600, 300)}),
        progress,
    )
    assert action.target == "daily_除暴"


@pytest.mark.parametrize(
    ("state", "targets", "kind", "target"),
    [
        (ScreenState.DIALOGUE, {"skip_dialogue": (1, 1)}, ActionKind.CLICK, "skip_dialogue"),
        (ScreenState.NPC_OPTIONS, {"npc_main_option": (1, 1)}, ActionKind.CLICK, "npc_main_option"),
        (ScreenState.REWARD, {"equip": (1, 1)}, ActionKind.CLICK, "equip"),
        (ScreenState.REWARD, {"claim": (1, 1)}, ActionKind.CLICK, "claim"),
        (ScreenState.BATTLE, {}, ActionKind.WAIT, None),
        (ScreenState.AUTO_PATH, {}, ActionKind.WAIT, None),
        (ScreenState.PAYMENT, {"main_quest": (1, 1)}, ActionKind.PAUSE, None),
        (ScreenState.UNKNOWN, {"main_quest": (1, 1)}, ActionKind.PAUSE, None),
    ],
)
def test_state_specific_safe_actions(state, targets, kind, target) -> None:
    action = TaskPlanner(("师门",)).next_action(snapshot(targets=targets, state=state), Progress(20))
    assert (action.kind, action.target) == (kind, target)


def test_npc_main_option_allows_legitimate_successors() -> None:
    action = TaskPlanner(("师门",)).next_action(
        snapshot(state=ScreenState.NPC_OPTIONS, targets={"npc_main_option": (1, 1)}),
        Progress(20),
    )
    assert {ScreenState.AUTO_PATH, ScreenState.DIALOGUE, ScreenState.MAP} <= action.allowed_expected


def test_named_existing_daily_precedes_starting_new_shimen() -> None:
    action = TaskPlanner(("师门", "除暴")).next_action(
        snapshot(targets={"daily_task_除暴": (1, 1), "shimen": (2, 2)}),
        Progress(20, main_blocked_by_level=True),
    )
    assert action.target == "daily_task_除暴"


@pytest.mark.parametrize(
    ("target", "completed"),
    [("daily_task_押镖", frozenset()), ("daily_task_除暴", frozenset({"除暴"}))],
)
def test_unwhitelisted_or_completed_existing_daily_is_not_clicked(target, completed) -> None:
    action = TaskPlanner(("除暴",)).next_action(
        snapshot(targets={target: (1, 1)}),
        Progress(20, completed_dailies=completed),
    )
    assert action.kind is ActionKind.PAUSE


def test_ambiguous_generic_daily_task_is_not_clicked() -> None:
    action = TaskPlanner(("除暴",)).next_action(
        snapshot(targets={"daily_task": (1, 1)}), Progress(20)
    )
    assert action.kind is ActionKind.PAUSE


@pytest.mark.parametrize(
    "progress",
    [
        Progress(14, main_blocked_by_level=True),
        Progress(20, main_blocked_by_level=False),
        Progress(20, main_blocked_by_level=True, shimen_completed=10),
    ],
)
def test_ineligible_existing_shimen_is_not_clicked(progress) -> None:
    action = TaskPlanner(("师门",)).next_action(
        snapshot(targets={"shimen_task": (1, 1)}), progress
    )
    assert action.kind is ActionKind.PAUSE


def test_ineligible_existing_shimen_falls_back_to_whitelisted_daily() -> None:
    action = TaskPlanner(("除暴",)).next_action(
        snapshot(targets={"shimen_task": (1, 1), "daily_除暴": (2, 2)}),
        Progress(20, main_blocked_by_level=True, shimen_completed=10),
    )
    assert action.target == "daily_除暴"


def test_patrol_is_last_safe_click_fallback() -> None:
    action = TaskPlanner(("除暴",)).next_action(
        snapshot(targets={"patrol": (1, 1)}), Progress(20)
    )
    assert (action.kind, action.target) == (
        ActionKind.CLICK,
        "patrol",
    )
    assert ScreenState.BATTLE in action.allowed_expected


def test_completed_and_unrecognized_dailies_are_never_clicked() -> None:
    action = TaskPlanner(("除暴",)).next_action(
        snapshot(targets={"daily_除暴": (1, 1), "daily_押镖": (2, 2), "purchase": (3, 3)}),
        Progress(20, main_blocked_by_level=True, shimen_completed=10, completed_dailies=frozenset({"除暴"})),
    )
    assert action.kind is ActionKind.PAUSE


@pytest.mark.parametrize(
    "kwargs",
    [
        {"level": True},
        {"level": -1},
        {"level": 0},
        {"level": 1, "main_blocked_by_level": 1},
        {"level": 1, "shimen_completed": False},
        {"level": 1, "shimen_completed": -1},
        {"level": 1, "shimen_completed": 11},
        {"level": 1, "completed_dailies": {"除暴"}},
        {"level": 1, "completed_dailies": frozenset({""})},
        {"level": 1, "completed_dailies": frozenset({" 除暴"})},
    ],
)
def test_progress_rejects_malformed_values(kwargs) -> None:
    with pytest.raises((TypeError, ValueError)):
        Progress(**kwargs)


@pytest.mark.parametrize(
    "whitelist", [(), ("除暴", "除暴"), ("",), (" 除暴",), ["除暴"], (1,)]
)
def test_planner_rejects_malformed_whitelist(whitelist) -> None:
    with pytest.raises((TypeError, ValueError)):
        TaskPlanner(whitelist)

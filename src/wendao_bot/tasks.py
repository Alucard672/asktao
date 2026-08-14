from dataclasses import dataclass, field
import re

from .models import Action, ActionKind, ScreenSnapshot, ScreenState


@dataclass(frozen=True)
class Progress:
    level: int | None
    main_blocked_by_level: bool = False
    shimen_completed: int = 0
    completed_dailies: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if self.level is not None and (
            isinstance(self.level, bool) or not isinstance(self.level, int)
        ):
            raise TypeError("level must be an integer or None")
        if self.level is not None and self.level < 1:
            raise ValueError("level must be positive")
        if not isinstance(self.main_blocked_by_level, bool):
            raise TypeError("main_blocked_by_level must be a boolean")
        if isinstance(self.shimen_completed, bool) or not isinstance(
            self.shimen_completed, int
        ):
            raise TypeError("shimen_completed must be an integer")
        if not (0 <= self.shimen_completed <= 10):
            raise ValueError("shimen_completed must be between 0 and 10")
        if not isinstance(self.completed_dailies, frozenset):
            raise TypeError("completed_dailies must be a frozenset")
        if any(
            not isinstance(name, str) or not name.strip() or name != name.strip()
            for name in self.completed_dailies
        ):
            raise ValueError("completed_dailies must contain nonempty strings")


class TaskPlanner:
    QUEST_SUCCESSORS = frozenset(
        {ScreenState.AUTO_PATH, ScreenState.NPC_OPTIONS, ScreenState.DIALOGUE,
         ScreenState.BATTLE, ScreenState.REWARD, ScreenState.MAP}
    )
    SKIP_SUCCESSORS = frozenset({ScreenState.MAP, ScreenState.NPC_OPTIONS, ScreenState.REWARD})
    REWARD_SUCCESSORS = frozenset({ScreenState.MAP, ScreenState.AUTO_PATH, ScreenState.DIALOGUE})

    def __init__(self, daily_whitelist: tuple[str, ...]) -> None:
        if not isinstance(daily_whitelist, tuple):
            raise TypeError("daily_whitelist must be a tuple")
        if not daily_whitelist:
            raise ValueError("daily_whitelist must not be empty")
        if any(
            not isinstance(name, str) or not name.strip() or name != name.strip()
            for name in daily_whitelist
        ):
            raise ValueError("daily_whitelist must contain nonempty strings")
        if len(set(daily_whitelist)) != len(daily_whitelist):
            raise ValueError("daily_whitelist must contain unique strings")
        self._daily_whitelist = daily_whitelist

    def next_action(self, snapshot: ScreenSnapshot, progress: Progress) -> Action:
        if not isinstance(snapshot, ScreenSnapshot) or not isinstance(progress, Progress):
            return self._pause()
        if not isinstance(snapshot.state, ScreenState):
            return self._pause()

        if snapshot.state is ScreenState.DIALOGUE:
            return self._click(snapshot, "skip_dialogue", self.SKIP_SUCCESSORS)
        if snapshot.state is ScreenState.NPC_OPTIONS:
            return self._click(snapshot, "npc_main_option", self.QUEST_SUCCESSORS)
        if snapshot.state is ScreenState.REWARD:
            if "equip" in snapshot.targets:
                return self._click(snapshot, "equip", self.REWARD_SUCCESSORS)
            return self._click(snapshot, "claim", self.REWARD_SUCCESSORS)
        if snapshot.state in {ScreenState.BATTLE, ScreenState.AUTO_PATH}:
            return Action(ActionKind.WAIT)
        if snapshot.state not in {ScreenState.MAP, ScreenState.ACTIVITY_LIST}:
            return self._pause()

        if not progress.main_blocked_by_level and "main_quest" in snapshot.targets:
            return self._click(snapshot, "main_quest", self.QUEST_SUCCESSORS)

        if progress.level is None:
            return self._pause()

        shimen_eligible = (
            progress.level is not None
            and progress.level >= 15
            and progress.main_blocked_by_level
            and progress.shimen_completed < 10
        )
        if shimen_eligible and "shimen_task" in snapshot.targets:
            return self._click(snapshot, "shimen_task", self.QUEST_SUCCESSORS)

        for name in self._daily_whitelist:
            target = f"daily_task_{name}"
            if name not in progress.completed_dailies and target in snapshot.targets:
                return self._click(snapshot, target, self.QUEST_SUCCESSORS)

        if shimen_eligible and "shimen" in snapshot.targets:
            return self._click(snapshot, "shimen", self.QUEST_SUCCESSORS)

        for name in self._daily_whitelist:
            target = f"daily_{name}"
            if name not in progress.completed_dailies and target in snapshot.targets:
                return self._click(snapshot, target, self.QUEST_SUCCESSORS)

        if "patrol" in snapshot.targets:
            return self._click(snapshot, "patrol", self.QUEST_SUCCESSORS)
        return self._pause()

    @staticmethod
    def _click(
        snapshot: ScreenSnapshot,
        target: str,
        expected: frozenset[ScreenState] = frozenset(),
    ) -> Action:
        if target not in snapshot.targets:
            return TaskPlanner._pause()
        return Action(ActionKind.CLICK, target=target, allowed_expected=expected)

    @staticmethod
    def _pause() -> Action:
        return Action(ActionKind.PAUSE)


class ProgressExtractor:
    """Conservatively derive only planning facts explicitly visible in OCR."""

    _LEVEL = re.compile(r"(?:(?:人物|角色)?等级)\s*[:：]?\s*(\d+)")
    _SHIMEN = re.compile(r"师门[^\d]{0,8}(\d+)\s*/\s*10")

    def __init__(self, daily_whitelist: tuple[str, ...] = ()) -> None:
        self._daily_whitelist = daily_whitelist

    def update(
        self, snapshot: ScreenSnapshot, previous: Progress | None = None
    ) -> Progress:
        previous = previous or Progress(None)
        match = self._LEVEL.search(snapshot.text)
        level = int(match.group(1)) if match else previous.level
        shimen = self._SHIMEN.search(snapshot.text)
        completed = int(shimen.group(1)) if shimen else previous.shimen_completed
        blocked = previous.main_blocked_by_level
        if re.search(r"(?:\d+\s*级开启|等级不足)", snapshot.text):
            blocked = True
        elif (
            "主线" in snapshot.text
            and "等级不足" not in snapshot.text
            and "级开启" not in snapshot.text
        ):
            blocked = False
        completed_dailies = set(previous.completed_dailies)
        compact = "".join(snapshot.text.split())
        for name in self._daily_whitelist:
            if f"{name}已完成" in compact or f"{name}完成" in compact:
                completed_dailies.add(name)
        return Progress(level, blocked, min(completed, 10), frozenset(completed_dailies))

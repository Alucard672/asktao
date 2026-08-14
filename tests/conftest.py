from dataclasses import dataclass, field
from pathlib import Path

import pytest

from wendao_bot.models import ActionKind, ScreenSnapshot, ScreenState
from wendao_bot.tasks import Progress, ProgressExtractor, TaskPlanner


class FakeSession:
    def __init__(self) -> None:
        self.captures = 0
        self.clicks: list[tuple[int, int]] = []

    def capture(self) -> bytes:
        self.captures += 1
        return f"capture-{self.captures}".encode()

    def click(self, x: int, y: int) -> None:
        self.clicks.append((x, y))


class FakeRecognizer:
    def __init__(self) -> None:
        self.snapshots: list[ScreenSnapshot] = []
        self.paths: list[Path] = []

    def classify(self, path: Path) -> ScreenSnapshot:
        self.paths.append(path)
        snapshot = self.snapshots.pop(0)
        return ScreenSnapshot(
            snapshot.state, snapshot.confidence, snapshot.text, str(path),
            snapshot.targets, snapshot.evidence,
        )


class FakeNotifier:
    def __init__(self) -> None:
        self.messages = []
        self.error: Exception | None = None

    def send(self, notice):
        self.messages.append(notice)
        if self.error:
            raise self.error


class FakeStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.screens = root / "screens"
        self.screens.mkdir(parents=True)
        self.states = []
        self.events = []

    def save_screenshot(self, content: bytes, name: str) -> Path:
        path = self.screens / name
        path.write_bytes(content)
        return path

    def save_state(self, state):
        self.states.append(dict(state))
        return self.root / "state.json"

    def append_event(self, event):
        self.events.append(dict(event))
        return self.root / "events.jsonl"


@dataclass
class FakeDependencies:
    session: FakeSession
    recognizer: FakeRecognizer
    planner: object
    safety: object
    notifier: FakeNotifier
    store: FakeStore
    progress: Progress = field(default_factory=lambda: Progress(20))

    def as_kwargs(self):
        return vars(self).copy()


@dataclass(frozen=True)
class OfflineResult:
    targets: list[str]
    progress: Progress
    forbidden_clicks: list[str]


class OfflineGame:
    """Small trusted-snapshot harness for planner integration tests."""

    def __init__(self) -> None:
        self.planner = TaskPlanner(("师门", "除暴", "修行"))
        self.extractor = ProgressExtractor(("师门", "除暴", "修行"))

    @staticmethod
    def _snapshot(text: str, targets: dict[str, tuple[int, int]]) -> ScreenSnapshot:
        return ScreenSnapshot(
            ScreenState.MAP,
            0.99,
            text,
            "offline.png",
            targets,
            frozenset({"ocr", "template"}),
        )

    def play_blocked_main_sequence(self) -> OfflineResult:
        frames = [
            self._snapshot("等级15 主线 20级开启 师门 0/10", {"shimen": (600, 300)})
        ]
        frames.extend(
            self._snapshot(
                f"等级15 主线 20级开启 师门 {completed}/10",
                {"shimen_task": (700, 245)},
            )
            for completed in range(1, 10)
        )
        frames.append(
            self._snapshot(
                "等级15 主线 师门 10/10", {"main_quest": (700, 190)}
            )
        )

        progress = Progress(None)
        targets: list[str] = []
        forbidden: list[str] = []
        allowed = {"shimen", "shimen_task", "main_quest"}
        for frame in frames:
            if frame.confidence >= 0.88 and "ocr" in frame.evidence:
                progress = self.extractor.update(frame, progress)
            action = self.planner.next_action(frame, progress)
            if action.kind is ActionKind.CLICK and action.target is not None:
                targets.append(action.target)
                if action.target not in allowed:
                    forbidden.append(action.target)
        return OfflineResult(targets, progress, forbidden)


@pytest.fixture
def fake_dependencies(tmp_path):
    from wendao_bot.safety import SafetyGuard
    from wendao_bot.tasks import TaskPlanner

    return FakeDependencies(
        FakeSession(), FakeRecognizer(), TaskPlanner(("师门", "除暴", "修行")),
        SafetyGuard(0.88, 3), FakeNotifier(), FakeStore(tmp_path)
    )


@pytest.fixture
def offline_game():
    return OfflineGame()

from pathlib import Path

import pytest

from wendao_bot.config import ConfigError, load_config
from wendao_bot.models import Action, ActionKind, ScreenSnapshot, ScreenState
from wendao_bot.orchestrator import Orchestrator, RunStatus
from wendao_bot.safety import SafetyGuard, UnsafeAction
from wendao_bot.session import GameSession, QuartzBackend, WindowInfo
from wendao_bot.session_windows import Win32Backend
from wendao_bot.tasks import Progress


def snap(
    state: ScreenState,
    confidence: float = 0.99,
    targets: dict[str, tuple[int, int]] | None = None,
) -> ScreenSnapshot:
    return ScreenSnapshot(state, confidence, "", "x.png", targets or {})


def write_config(tmp_path: Path, document: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(document, encoding="utf-8")
    return path


def test_default_keymap_binds_story_skip_and_task_pathfind(tmp_path: Path) -> None:
    path = write_config(tmp_path, "window:\n  title: 问道\n")

    assert load_config(path).keymap == (
        ("story_skip", "`"),
        ("task_pathfind", "t"),
    )


def test_custom_keymap_merges_with_defaults_and_sorts(tmp_path: Path) -> None:
    path = write_config(tmp_path, "keymap:\n  task_pathfind: p\n  open_bag: b\n")

    assert load_config(path).keymap == (
        ("open_bag", "b"),
        ("story_skip", "`"),
        ("task_pathfind", "p"),
    )


@pytest.mark.parametrize(
    "document",
    [
        "keymap: []",
        "keymap: text",
        "keymap:\n  bad name: t",
        "keymap:\n  " + "a" * 33 + ": t",
        "keymap:\n  story_skip: ab",
        "keymap:\n  story_skip: ''",
        "keymap:\n  story_skip: 12",
        "keymap:\n  story_skip: true",
        "keymap:\n  story_skip: 键",
    ],
)
def test_load_config_rejects_unsafe_keymap(tmp_path: Path, document: str) -> None:
    path = write_config(tmp_path, document)

    with pytest.raises(ConfigError, match="keymap"):
        load_config(path)


@pytest.mark.parametrize(
    "state",
    [
        ScreenState.MAP,
        ScreenState.NPC_OPTIONS,
        ScreenState.DIALOGUE,
        ScreenState.REWARD,
        ScreenState.ACTIVITY_LIST,
    ],
)
def test_key_is_authorized_in_clickable_states(state: ScreenState) -> None:
    guard = SafetyGuard(0.88, 3)

    assert guard.authorize(Action(ActionKind.KEY, "story_skip"), snap(state)) is None


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
        ScreenState.AUTO_PATH,
        ScreenState.BATTLE,
    ],
)
def test_key_is_rejected_outside_clickable_states(state: ScreenState) -> None:
    guard = SafetyGuard(0.88, 3)

    with pytest.raises(UnsafeAction, match="state"):
        guard.authorize(
            Action(ActionKind.KEY, "story_skip"), snap(state, confidence=1.0)
        )


def test_key_is_rejected_below_confidence_threshold() -> None:
    guard = SafetyGuard(0.88, 3)

    with pytest.raises(UnsafeAction, match="confidence"):
        guard.authorize(
            Action(ActionKind.KEY, "story_skip"), snap(ScreenState.MAP, 0.87)
        )


def test_key_target_is_required() -> None:
    guard = SafetyGuard(0.88, 3)

    with pytest.raises(UnsafeAction, match="target"):
        guard.authorize(Action(ActionKind.KEY), snap(ScreenState.MAP))


class FakeBackend:
    def __init__(self, windows: list[WindowInfo]) -> None:
        self.windows = windows
        self.keys: list[tuple[WindowInfo, str]] = []

    def list_windows(self) -> list[WindowInfo]:
        return self.windows

    def capture(self, target: WindowInfo) -> bytes:
        return b""

    def focus_and_click(self, target: WindowInfo, x: int, y: int) -> None:
        raise AssertionError("unexpected click")

    def send_key(self, target: WindowInfo, key: str) -> None:
        self.keys.append((target, key))


def game_window() -> WindowInfo:
    return WindowInfo(
        window_id=7, title="Wendao", x=100, y=200, width=800, height=600,
        owner_pid=42, owner_name="wendao.exe",
    )


def game_session(backend: FakeBackend) -> GameSession:
    return GameSession(backend, "Wendao", 800, 600, expected_owner_name="wendao.exe")


@pytest.mark.parametrize("key", ["", "ab", 7, None])
def test_session_send_key_rejects_non_single_characters(key: object) -> None:
    backend = FakeBackend([game_window()])

    with pytest.raises(ValueError, match="single character"):
        game_session(backend).send_key(key)  # type: ignore[arg-type]

    assert backend.keys == []


def test_session_send_key_forwards_resolved_window_and_key() -> None:
    window = game_window()
    backend = FakeBackend([window])

    game_session(backend).send_key("t")

    assert backend.keys == [(window, "t")]


def test_session_send_key_requires_exactly_one_window() -> None:
    backend = FakeBackend([])

    with pytest.raises(RuntimeError, match="exactly one"):
        game_session(backend).send_key("t")

    assert backend.keys == []


def test_session_send_key_rejects_geometry_mismatch() -> None:
    backend = FakeBackend([game_window()])
    session = GameSession(backend, "Wendao", 886, 672, expected_owner_name="wendao.exe")

    with pytest.raises(RuntimeError, match="geometry"):
        session.send_key("t")

    assert backend.keys == []


def test_quartz_backend_send_key_is_fail_closed() -> None:
    with pytest.raises(RuntimeError, match="not implemented on macOS"):
        QuartzBackend().send_key(game_window(), "t")


def win32_spec(
    *,
    hwnd: int = 7,
    title: str = "Wendao",
    rect: tuple[int, int, int, int] = (100, 200, 800, 600),
    pid: int = 42,
    image: str = "wendao.exe",
) -> dict:
    return {"hwnd": hwnd, "title": title, "rect": rect, "pid": pid, "image": image}


class FakeWin32Api:
    def __init__(self, windows: list[dict] | None = None) -> None:
        self.windows = windows if windows is not None else [win32_spec()]
        self.images = {window["pid"]: window["image"] for window in self.windows}
        first = self.windows[0]["hwnd"] if self.windows else 0
        self.foreground = first
        self.root = first
        self.iconic: set[int] = set()
        self.covered: dict[tuple[int, int], int] = {}
        self.focusable = True
        self.keys: list[str] = []
        self.focus_requests: list[int] = []

    def _spec(self, handle: int) -> dict:
        return next(window for window in self.windows if window["hwnd"] == handle)

    def enum_windows(self) -> list[int]:
        return [window["hwnd"] for window in self.windows]

    def window_title(self, handle: int) -> str:
        return self._spec(handle)["title"]

    def window_pid(self, handle: int) -> int:
        return self._spec(handle)["pid"]

    def process_image_name(self, pid: int) -> str:
        return self.images.get(pid, "")

    def client_rect(self, handle: int) -> tuple[int, int, int, int]:
        return self._spec(handle)["rect"]

    def foreground_window(self) -> int:
        return self.foreground

    def is_iconic(self, handle: int) -> bool:
        return handle in self.iconic

    def root_window_at(self, x: int, y: int) -> int:
        return self.covered.get((x, y), self.root)

    def set_foreground(self, handle: int) -> bool:
        self.focus_requests.append(handle)
        if self.focusable:
            self.foreground = handle
        return self.focusable

    def send_key(self, key: str) -> None:
        self.keys.append(key)


def win32_session(api: FakeWin32Api) -> GameSession:
    return GameSession(
        Win32Backend(api=api), "Wendao", 800, 600, expected_owner_name="wendao.exe"
    )


def test_win32_send_key_does_not_inject_when_focus_fails() -> None:
    api = FakeWin32Api()
    api.foreground = 999
    api.focusable = False

    with pytest.raises(RuntimeError, match="focus"):
        win32_session(api).send_key("t")

    assert api.keys == []


def test_win32_send_key_rejects_covered_window() -> None:
    api = FakeWin32Api()
    api.covered[(100, 200)] = 999

    with pytest.raises(RuntimeError, match="covered"):
        win32_session(api).send_key("t")

    assert api.keys == []


def test_win32_send_key_rejects_minimized_window() -> None:
    api = FakeWin32Api()
    api.iconic.add(7)

    with pytest.raises(RuntimeError, match="minimized"):
        win32_session(api).send_key("t")

    assert api.keys == []


def test_win32_send_key_rejects_bounds_change_after_focus() -> None:
    class ShiftingApi(FakeWin32Api):
        def set_foreground(self, handle: int) -> bool:
            result = super().set_foreground(handle)
            self._spec(handle)["rect"] = (100, 200, 800, 601)
            return result

    api = ShiftingApi()

    with pytest.raises(RuntimeError, match="changed after focus"):
        win32_session(api).send_key("t")

    assert api.keys == []


def test_win32_send_key_focuses_then_injects() -> None:
    api = FakeWin32Api()

    win32_session(api).send_key("t")

    assert api.focus_requests == [7]
    assert api.keys == ["t"]


class KeySession:
    def __init__(self) -> None:
        self.captures = 0
        self.keys: list[str] = []
        self.clicks: list[tuple[int, int]] = []

    def capture(self) -> bytes:
        self.captures += 1
        return f"capture-{self.captures}".encode()

    def click(self, x: int, y: int) -> None:
        self.clicks.append((x, y))

    def send_key(self, key: str) -> None:
        self.keys.append(key)


class KeyRecognizer:
    def __init__(self, snapshots: list[ScreenSnapshot]) -> None:
        self.snapshots = list(snapshots)

    def classify(self, path: Path) -> ScreenSnapshot:
        snapshot = self.snapshots.pop(0)
        return ScreenSnapshot(
            snapshot.state, snapshot.confidence, snapshot.text, str(path),
            snapshot.targets, snapshot.evidence,
        )


class KeyPlanner:
    def __init__(self, action: Action) -> None:
        self.action = action

    def next_action(self, snapshot, progress) -> Action:
        return self.action


class KeyNotifier:
    def __init__(self) -> None:
        self.messages = []

    def send(self, notice) -> None:
        self.messages.append(notice)


class KeyStore:
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

    def save_state(self, state) -> Path:
        self.states.append(dict(state))
        return self.root / "state.json"

    def append_event(self, event) -> Path:
        self.events.append(dict(event))
        return self.root / "events.jsonl"


def key_orchestrator(
    tmp_path: Path,
    snapshots: list[ScreenSnapshot],
    action: Action,
    keymap: dict[str, str] | None = None,
) -> tuple[Orchestrator, KeySession, KeyStore, KeyNotifier]:
    session = KeySession()
    store = KeyStore(tmp_path)
    notifier = KeyNotifier()
    runner = Orchestrator(
        session=session,
        recognizer=KeyRecognizer(snapshots),
        planner=KeyPlanner(action),
        safety=SafetyGuard(0.88, 3),
        notifier=notifier,
        store=store,
        progress=Progress(20),
        keymap=keymap,
        sleeper=lambda _: None,
    )
    return runner, session, store, notifier


def test_orchestrator_sends_mapped_key_and_verifies_transition(tmp_path: Path) -> None:
    runner, session, store, _ = key_orchestrator(
        tmp_path,
        [snap(ScreenState.MAP), snap(ScreenState.AUTO_PATH)],
        Action(ActionKind.KEY, "task_pathfind"),
        keymap={"story_skip": "`", "task_pathfind": "t"},
    )

    result = runner.step()

    assert session.keys == ["t"]
    assert session.clicks == []
    assert runner.status is RunStatus.RUNNING
    assert result.action_kind == "key"
    assert result.action_target == "task_pathfind"
    assert store.events[-1]["action_kind"] == "key"


def test_orchestrator_pauses_when_keymap_entry_is_missing(tmp_path: Path) -> None:
    runner, session, store, notifier = key_orchestrator(
        tmp_path,
        [snap(ScreenState.MAP)],
        Action(ActionKind.KEY, "task_pathfind"),
        keymap=None,
    )

    runner.step()

    assert runner.status is RunStatus.PAUSED
    assert session.keys == []
    assert "keymap" in store.states[-1]["reason"]
    assert len(notifier.messages) == 1


def test_orchestrator_pauses_key_action_on_blocked_state(tmp_path: Path) -> None:
    runner, session, store, _ = key_orchestrator(
        tmp_path,
        [snap(ScreenState.PAYMENT)],
        Action(ActionKind.KEY, "story_skip"),
        keymap={"story_skip": "`"},
    )

    runner.step()

    assert runner.status is RunStatus.PAUSED
    assert session.keys == []
    assert "authorization failed" in store.states[-1]["reason"]


def test_orchestrator_verify_click_alias_is_preserved(tmp_path: Path) -> None:
    runner, _, _, _ = key_orchestrator(
        tmp_path, [], Action(ActionKind.KEY, "story_skip"), keymap={"story_skip": "`"}
    )

    assert runner._verify_action.__func__ is runner._verify_click.__func__

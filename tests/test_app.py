import builtins
import importlib
from pathlib import Path
import sys
import tomllib

import pytest

from wendao_bot.app_model import AppViewState
from wendao_bot.app_service import InvalidModeTransition

import os as _os

TMP = "C:/tmp" if _os.name == "nt" else "/tmp"


class FakeView:
    def __init__(self):
        self.states = []
        self.errors = []
        self.hooks = []
        self.termination_replies = 0

    def render(self, state):
        self.states.append(state)

    def show_error(self, message):
        self.errors.append(message)

    def choose_config(self):
        self.hooks.append("choose_config")
        return None

    def open_runtime_folder(self, path=None):
        self.hooks.append("runtime_folder")

    def preview_notification(self, preview=None):
        self.hooks.append("notification_preview")

    def finish_termination(self):
        self.termination_replies += 1


class FakeService:
    def __init__(self, events=()):
        self.events = list(events)
        self.calls = []
        self.joined = True

    def start(self):
        self.calls.append("start")

    def drain_events(self):
        events, self.events = self.events, []
        return events

    def start_single_step(self):
        self.calls.append("single_step")

    def start_continuous(self):
        self.calls.append("continuous")

    def pause(self):
        self.calls.append("pause")

    def resume(self):
        self.calls.append("resume")

    def stop(self):
        self.calls.append("stop")

    def request_stop(self):
        self.calls.append("request_stop")

    def request_close(self):
        self.calls.append("request_close")

    def join(self, timeout=None):
        self.calls.append(("join", timeout))
        return self.joined

    def close(self):
        self.calls.append("close")

    def set_config(self, path):
        self.calls.append(("set_config", path))

    def ensure_runtime_directory(self):
        self.calls.append("ensure_runtime_directory")
        return Path(TMP + "/tmp/runtime")

    def preview_notification(self):
        self.calls.append("preview_notification")
        return type(
            "Preview", (), {"recipient": "to@example.com", "body": "exact body"}
        )()


def ready_state(*, verified=False):
    return AppViewState(
        mode="single_step" if verified else "observe",
        status="running",
        screen_state="map",
        confidence=0.97,
        target_names=("claim", "main_quest"),
        screenshot_path=TMP + "/tmp/screens/capture-000001.png",
        pause_reason=None,
        recognition_ready=True,
        single_step_verified=verified,
        can_single_step=True,
        can_run_continuous=verified,
        window_match=True,
        ocr_available=True,
        template_available=True,
        observe_ready=True,
        accessibility_trusted=True,
        trusted_screenshot_root=Path(TMP + "/tmp/screens"),
    )


def test_launch_starts_observe_only_and_renders_initial_state():
    from wendao_bot.app import AppController

    service, view = FakeService(), FakeView()
    controller = AppController(service, view)

    controller.launch()

    assert service.calls == ["start"]
    assert view.states[-1].mode == "observe"
    assert view.states[-1].status == "idle"


def test_poll_renders_all_safe_fields_and_button_gates():
    from wendao_bot.app import AppController, display_model

    state = ready_state(verified=False)
    service, view = FakeService([state]), FakeView()

    AppController(service, view).poll()

    assert view.states == [state]
    displayed = display_model(state)
    assert displayed.fields == {
        "window_connection": "已连接",
        "window_geometry": "—",
        "ocr_availability": "可用",
        "template_availability": "可用",
        "observe_readiness": "就绪",
        "accessibility": "可用",
        "state": "map",
        "confidence": "97.0%",
        "target_names": "claim, main_quest",
        "run_status": "running",
        "pause_reason": "—",
    }
    assert displayed.buttons["single_step"] is True
    assert displayed.buttons["continuous"] is False
    assert display_model(ready_state(verified=True)).buttons["continuous"] is True


def test_accessibility_permission_locks_live_buttons_but_not_observation():
    from dataclasses import replace
    from wendao_bot.app import display_model

    state = replace(ready_state(verified=True), accessibility_trusted=False)

    assert state.observe_ready
    assert not state.can_single_step
    assert not state.can_run_continuous
    assert display_model(state).fields["accessibility"] == "不可用"


def test_poll_finishes_on_restarted_observing_state_after_stale_stop():
    from wendao_bot.app import AppController

    stopped = AppViewState.stopped()
    observing = AppViewState.starting(mode="observe")
    service, view = FakeService([stopped, observing]), FakeView()

    AppController(service, view).poll()

    assert [state.status for state in view.states] == ["stopped", "observing"]
    assert view.states[-1].mode == "observe"


def test_controller_actions_call_only_service_safe_apis_and_hooks():
    from wendao_bot.app import AppController

    service, view = FakeService(), FakeView()
    controller = AppController(service, view)

    controller.observe()
    controller.single_step()
    controller.continuous()
    controller.pause()
    controller.resume()
    controller.stop()
    controller.choose_config()
    controller.open_runtime_folder()
    controller.preview_notification()

    assert service.calls == [
        "start", "single_step", "continuous", "pause", "resume", "request_stop",
        "ensure_runtime_directory", "preview_notification"
    ]
    assert view.hooks == [
        "choose_config", "runtime_folder", "notification_preview"
    ]


def test_controller_applies_selected_config_and_shows_exact_preview():
    from wendao_bot.app import AppController

    service, view = FakeService(), FakeView()
    view.choose_config = lambda: Path(TMP + "/tmp/new.yaml")
    view.open_runtime_folder = lambda path=None: view.hooks.append(("runtime", path))
    view.preview_notification = lambda preview=None: view.hooks.append(
        ("preview", preview.recipient, preview.body)
    )
    controller = AppController(service, view)

    controller.choose_config()
    controller.open_runtime_folder()
    controller.preview_notification()

    assert ("set_config", Path(TMP + "/tmp/new.yaml")) in service.calls
    assert ("runtime", Path(TMP + "/tmp/runtime")) in view.hooks
    assert ("preview", "to@example.com", "exact body") in view.hooks


def test_invalid_transition_is_caught_and_details_are_redacted():
    from wendao_bot.app import AppController

    class RejectingService(FakeService):
        def start_continuous(self):
            raise InvalidModeTransition("secret@example.com token=private")

    view = FakeView()
    AppController(RejectingService(), view).continuous()

    assert view.errors == ["当前状态不允许此操作"]
    assert "secret" not in repr(view.errors)


def test_close_uses_service_close_once_without_starting_a_runner():
    from wendao_bot.app import AppController

    service, view = FakeService(), FakeView()
    controller = AppController(service, view)
    controller.close()
    controller.close()

    assert service.calls == ["close"]


def test_stop_button_uses_nonblocking_service_request():
    from wendao_bot.app import AppController

    service, view = FakeService(), FakeView()

    AppController(service, view).stop()

    assert service.calls == ["request_stop"]


def test_termination_replies_only_after_worker_join_completes():
    from wendao_bot.app import AppController

    service, view = FakeService(), FakeView()
    service.joined = False
    controller = AppController(service, view)

    assert controller.begin_termination() is False
    assert service.calls == ["request_close", ("join", 0)]
    controller.poll()
    assert view.termination_replies == 0

    service.joined = True
    controller.poll()
    controller.poll()

    assert view.termination_replies == 1
    assert service.calls[-2:] == [("join", 0), ("join", 0)]


def test_termination_has_bounded_fallback_and_replies_exactly_once():
    from wendao_bot.app import AppController

    now = [100.0]
    service, view = FakeService(), FakeView()
    service.joined = False
    controller = AppController(
        service,
        view,
        clock=lambda: now[0],
        termination_timeout=5.0,
    )

    assert controller.begin_termination() is False
    now[0] = 104.9
    controller.poll()
    assert view.termination_replies == 0

    now[0] = 105.0
    controller.poll()
    controller.poll()

    assert view.termination_replies == 1


def test_close_fallback_stops_then_joins_when_close_is_unavailable():
    from wendao_bot.app import AppController

    class LegacyService:
        def __init__(self):
            self.calls = []

        def stop(self):
            self.calls.append("stop")

        def join(self):
            self.calls.append("join")

    service = LegacyService()
    AppController(service, FakeView()).close()

    assert service.calls == ["stop", "join"]


def test_import_does_not_attempt_to_import_appkit(monkeypatch):
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name in {"AppKit", "Foundation", "objc"}:
            raise AssertionError(f"eager GUI import: {name}")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    sys.modules.pop("wendao_bot.app", None)

    importlib.import_module("wendao_bot.app")


def test_native_adapter_constructs_with_valid_one_argument_selectors():
    AppKit = pytest.importorskip("AppKit")
    from wendao_bot.app import _build_native_app

    service = FakeService()
    service.runtime = Path(TMP + "/tmp")
    app, retained = _build_native_app(service)
    view, _controller, target, _delegate, timer = retained
    try:
        assert app is AppKit.NSApplication.sharedApplication()
        for selector in (
            "observe:",
            "singleStep:",
            "continuous:",
            "pause:",
            "resume:",
            "stop:",
            "chooseConfig:",
            "runtimeFolder:",
            "notificationPreview:",
            "poll:",
        ):
            assert target.respondsToSelector_(selector), selector
    finally:
        timer.invalidate()
        view.window.close()


def test_gui_entry_point_is_declared_as_gui_script():
    metadata = tomllib.loads(Path("pyproject.toml").read_text())

    assert metadata["project"]["gui-scripts"] == {
        "wendao-app": "wendao_bot.app:main"
    }
    assert metadata["project"]["scripts"] == {
        "wendao-bot": "wendao_bot.cli:main"
    }
class FakeOpenPanel:
    def __init__(self):
        self.files = None
        self.directories = None
        self.other_file_types = None
        self.allowed_types_calls = []

    def setCanChooseFiles_(self, value):
        self.files = value

    def setCanChooseDirectories_(self, value):
        self.directories = value

    def setAllowsOtherFileTypes_(self, value):
        self.other_file_types = value

    def setAllowedFileTypes_(self, value):
        self.allowed_types_calls.append(value)


def test_config_panel_avoids_deprecated_file_type_filter_that_disables_yaml():
    from wendao_bot.app import configure_config_panel

    panel = FakeOpenPanel()
    configure_config_panel(panel)

    assert panel.files is True
    assert panel.directories is False
    assert panel.other_file_types is True
    assert panel.allowed_types_calls == []


def test_emulator_candidates_filters_and_ranks_known_executables():
    from wendao_bot.app import emulator_candidates
    from wendao_bot.session import WindowInfo

    windows = [
        WindowInfo(3, "某某模拟器", 0, 0, 1280, 720, 12, "unknown.exe"),
        WindowInfo(1, "MuMu安卓设备 -1", 0, 0, 1600, 900, 10, "MuMuPlayer.exe"),
        WindowInfo(2, "记事本", 0, 0, 800, 600, 11, "notepad.exe"),
        WindowInfo(4, "MuMu悬浮球", 0, 0, 48, 48, 10, "MuMuPlayer.exe"),
        WindowInfo(5, "", 0, 0, 1280, 720, 13, "dnplayer.exe"),
    ]

    result = emulator_candidates(windows)

    assert [window.window_id for window in result] == [1, 3]


def test_detected_config_is_valid_and_loadable(tmp_path):
    from wendao_bot.app import write_detected_config
    from wendao_bot.config import load_config
    from wendao_bot.session import WindowInfo

    window = WindowInfo(1, "MuMu安卓设备 -1", 0, 0, 1600, 900, 10, "MuMuPlayer.exe")

    path = write_detected_config(window, tmp_path / "detected.yaml")

    config = load_config(path)
    assert config.window_title == "MuMu安卓设备 -1"
    assert config.window_owner == "MuMuPlayer.exe"
    assert (config.width, config.height) == (1600, 900)


def test_detect_emulator_applies_generated_config_via_service():
    from wendao_bot.app import AppController

    service, view = FakeService(), FakeView()
    detected = Path(TMP + "/detected.yaml")
    view.detect_emulator = lambda: detected
    AppController(service, view).detect_emulator()

    assert ("set_config", detected) in service.calls


def test_detect_emulator_cancel_calls_no_service_api():
    from wendao_bot.app import AppController

    service, view = FakeService(), FakeView()
    view.detect_emulator = lambda: None
    AppController(service, view).detect_emulator()

    assert service.calls == []

import threading
import time
from types import SimpleNamespace

import pytest

from wendao_bot import app_service
from wendao_bot.app_model import AppViewState
from wendao_bot.models import ScreenSnapshot, ScreenState
from wendao_bot.orchestrator import StepResult
from wendao_bot.orchestrator import RunStatus


def _write_config(
    path, *, title="Wendao", names="custom, 师门", recipient="preview@example.com"
):
    path.write_text(
        f"window:\n  title: {title}\ndaily_whitelist: [{names}]\n"
        f"notification:\n  recipient: {recipient}\n",
        encoding="utf-8",
    )


def test_build_runner_creates_observer_with_exact_shared_stop_event(monkeypatch, tmp_path):
    stop = threading.Event()
    captured = {}
    config = SimpleNamespace(
        window_title="Wendao",
        width=800,
        height=600,
        min_confidence=0.9,
        daily_whitelist=("shimen",),
        max_unchanged_actions=3,
        imessage_recipient="test@example.com",
        action_timeout_seconds=2.0,
        battle_timeout_seconds=30.0,
    )

    class FakeOrchestrator:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.status = (
                RunStatus.OBSERVING if kwargs["observe_only"] else RunStatus.RUNNING
            )

    monkeypatch.setattr(app_service, "load_config", lambda path: config)
    monkeypatch.setattr(app_service, "QuartzBackend", lambda: object())
    monkeypatch.setattr(app_service, "GameSession", lambda *args: object())
    monkeypatch.setattr(app_service, "ScreenRecognizer", lambda **kwargs: object())
    monkeypatch.setattr(app_service, "TaskPlanner", lambda *args: object())
    monkeypatch.setattr(app_service, "SafetyGuard", lambda *args: object())
    monkeypatch.setattr(
        app_service, "IMessageNotifier", lambda *args, **kwargs: object()
    )
    monkeypatch.setattr(app_service, "ProgressExtractor", lambda *args: object())
    monkeypatch.setattr(app_service, "Orchestrator", FakeOrchestrator)

    runner = app_service.build_runner(None, tmp_path, observe_only=True, stop_event=stop)

    assert runner.status is RunStatus.OBSERVING
    assert captured["observe_only"] is True
    assert captured["stop_event"] is stop


def test_app_service_defaults_to_gui_runner_with_dry_run_notifications(
    monkeypatch, tmp_path
):
    captured = {}

    def fake_build_runner(*args, **kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(app_service, "build_runner", fake_build_runner)
    service = app_service.AppService(runtime=tmp_path)

    assert service.runner_factory is app_service.build_app_runner
    app_service.build_app_runner(
        None, tmp_path, observe_only=True, stop_event=threading.Event()
    )
    assert captured["notification_dry_run"] is True


def test_app_runner_pause_notification_never_invokes_osascript(monkeypatch, tmp_path):
    captured = {}
    config = SimpleNamespace(
        window_title="Wendao",
        width=800,
        height=600,
        min_confidence=0.9,
        daily_whitelist=("shimen",),
        max_unchanged_actions=3,
        imessage_recipient="private@example.com",
        action_timeout_seconds=2.0,
        battle_timeout_seconds=30.0,
    )

    def forbidden_runner(*_args, **_kwargs):
        raise AssertionError("GUI pause attempted to invoke osascript")

    real_notifier = app_service.IMessageNotifier
    monkeypatch.setattr(app_service, "load_config", lambda _path: config)
    monkeypatch.setattr(app_service, "QuartzBackend", lambda: object())
    monkeypatch.setattr(app_service, "GameSession", lambda *_args: object())
    monkeypatch.setattr(app_service, "ScreenRecognizer", lambda **_kwargs: object())
    monkeypatch.setattr(app_service, "TaskPlanner", lambda *_args: object())
    monkeypatch.setattr(app_service, "SafetyGuard", lambda *_args: object())
    monkeypatch.setattr(app_service, "ProgressExtractor", lambda *_args: object())
    monkeypatch.setattr(
        app_service,
        "IMessageNotifier",
        lambda recipient, *, dry_run=False: real_notifier(
            recipient, runner=forbidden_runner, dry_run=dry_run
        ),
    )

    class CapturingOrchestrator:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(app_service, "Orchestrator", CapturingOrchestrator)
    app_service.build_app_runner(None, tmp_path, observe_only=True)

    body = captured["notifier"].send(
        app_service.PauseNotice("safe pause", None, None, "none", "now")
    )
    assert body.startswith("[问道脚本暂停]")


def test_load_progress_restores_valid_state_and_rejects_malformed_state():
    class Store:
        def __init__(self, progress):
            self.progress = progress

        def load_state(self):
            return {"progress": self.progress}

    restored = app_service.load_progress(Store({
        "level": 16,
        "main_blocked_by_level": True,
        "shimen_completed": 4,
        "completed_dailies": ["除暴"],
    }))

    assert restored.level == 16
    assert restored.completed_dailies == {"除暴"}
    assert app_service.load_progress(Store({"level": "admin"})).level is None


def _step(
    tmp_path, *, status="observing", target="main_quest", action_kind=None
):
    screen = tmp_path / "screens" / "capture-000001.png"
    snapshot = ScreenSnapshot(
        ScreenState.MAP,
        0.99,
        "private OCR",
        str(screen),
        {target: (12, 34)},
        frozenset({"ocr", "template"}),
    )
    return StepResult(
        snapshot,
        snapshot,
        action_kind,
        target if action_kind == "click" else None,
        status,
    )


def _wait_until(predicate, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("condition not reached")


def test_service_defaults_to_persistent_observe_and_uses_exact_stop_event(tmp_path):
    calls = []

    def driver(args, **kwargs):
        calls.append((args, kwargs["stop"]))
        kwargs["on_step"](_step(tmp_path))
        kwargs["stop"].wait(1)
        return 0

    service = app_service.AppService(runtime=tmp_path, command_driver=driver)
    service.start()
    _wait_until(lambda: len(calls) == 1)

    assert calls[0][0].command == "observe"
    assert calls[0][0].single_step is False
    assert calls[0][1] is service.stop_event
    assert service.mode is app_service.AppMode.OBSERVE
    assert service.drain_events()[0].status == "observing"
    service.close()
    assert calls[0][1].is_set()


def test_repeated_observe_start_is_idempotent_while_observer_is_running(tmp_path):
    events = []

    def driver(_args, **kwargs):
        events.append(kwargs["stop"])
        kwargs["stop"].wait(1)
        return 0

    service = app_service.AppService(runtime=tmp_path, command_driver=driver)
    service.start()
    _wait_until(lambda: len(events) == 1)
    service.start()
    assert len(events) == 1
    assert not events[0].is_set()

    service.stop()
    service.start()
    _wait_until(lambda: len(events) == 2)
    service.close()

    assert events[0] is not events[1]
    assert events[0].is_set()


def test_continuous_rejected_until_genuinely_successful_single_step(tmp_path):
    service = app_service.AppService(runtime=tmp_path, command_driver=lambda *_a, **_k: 0)
    with pytest.raises(app_service.InvalidModeTransition):
        service.start_continuous()

    service.close()


def test_drain_events_returns_queued_states_in_order(tmp_path):
    service = app_service.AppService(runtime=tmp_path, command_driver=lambda *_a, **_k: 0)
    first = AppViewState.initial()
    second = AppViewState.error()
    service.state_queue.put(first)
    service.state_queue.put(second)

    assert service.drain_events() == [first, second]
    assert service.drain_events() == []


def test_request_stop_returns_before_slow_worker_exits_then_publishes_stopped(tmp_path):
    entered = threading.Event()
    release = threading.Event()

    def driver(_args, **kwargs):
        entered.set()
        kwargs["stop"].wait(1)
        release.wait(1)
        return 0

    service = app_service.AppService(runtime=tmp_path, command_driver=driver)
    service.start()
    assert entered.wait(1)

    service.request_stop()

    assert service.worker_alive
    assert service.stop_event.is_set()
    release.set()
    assert service.join(timeout=1)
    assert service.current_state.status == "stopped"
    assert service.drain_events()[-1].status == "stopped"


def test_observe_can_restart_after_nonblocking_stop_finishes(tmp_path):
    calls = []

    def driver(_args, **kwargs):
        calls.append(kwargs["stop"])
        kwargs["stop"].wait(1)
        return 0

    service = app_service.AppService(runtime=tmp_path, command_driver=driver)
    service.start()
    _wait_until(lambda: len(calls) == 1)
    service.request_stop()
    assert service.join(timeout=1)

    service.start()
    _wait_until(lambda: len(calls) == 2)
    service.close()

    assert calls[0] is not calls[1]


def test_request_close_signals_then_join_reports_completion(tmp_path):
    release = threading.Event()

    def driver(_args, **kwargs):
        kwargs["stop"].wait(1)
        release.wait(1)
        return 0

    service = app_service.AppService(runtime=tmp_path, command_driver=driver)
    service.start()
    _wait_until(lambda: service.worker_alive)

    service.request_close()
    assert service.stop_event.is_set()
    assert service.join(timeout=0) is False
    release.set()
    assert service.join(timeout=1) is True


def test_stop_without_worker_still_publishes_stopped_state(tmp_path):
    service = app_service.AppService(runtime=tmp_path, command_driver=lambda *_a, **_k: 0)

    service.stop()

    assert service.current_state.status == "stopped"
    assert service.drain_events()[-1].status == "stopped"


def test_unsuccessful_preflight_does_not_unlock_continuous(tmp_path):
    def driver(args, **kwargs):
        assert args.preflight_seconds == 30.0
        return 1

    service = app_service.AppService(runtime=tmp_path, command_driver=driver)
    service.start_single_step()
    _wait_until(lambda: not service.worker_alive)

    assert service.single_step_verified is False
    with pytest.raises(app_service.InvalidModeTransition):
        service.start_continuous()
    service.close()


def test_successful_single_step_unlocks_continuous_using_live_command_path(tmp_path):
    calls = []

    def driver(args, **kwargs):
        calls.append(args)
        kwargs["on_step"](
            _step(tmp_path, status="running", action_kind="click")
        )
        if args.single_step:
            return 0
        kwargs["stop"].wait(1)
        return 0

    service = app_service.AppService(runtime=tmp_path, command_driver=driver)
    service.start_single_step()
    _wait_until(lambda: not service.worker_alive)
    assert service.single_step_verified is True

    service.start_continuous()
    _wait_until(lambda: len(calls) == 2)
    assert [(call.command, call.single_step, call.preflight_seconds) for call in calls] == [
        ("run", True, 30.0),
        ("run", False, 30.0),
    ]
    service.close()


def test_pause_resume_stop_share_safe_control_markers(tmp_path):
    service = app_service.AppService(runtime=tmp_path, command_driver=lambda *_a, **_k: 0)
    control = tmp_path / "control"

    service.pause()
    assert (control / "pause").exists()
    service.resume()
    assert (control / "resume").exists()
    assert not (control / "pause").exists()
    service.stop()
    assert (control / "stop").exists()
    with pytest.raises(app_service.InvalidModeTransition):
        service.resume()
    assert (control / "stop").exists()
    service.close()


@pytest.mark.parametrize(
    "step",
    [
        lambda path: _step(path, status="running", action_kind=None),
        lambda path: _step(path, status="stopped", action_kind="click"),
    ],
)
def test_nonclick_or_stopped_single_step_never_unlocks_continuous(tmp_path, step):
    def driver(_args, **kwargs):
        kwargs["on_step"](step(tmp_path))
        return 0

    service = app_service.AppService(runtime=tmp_path, command_driver=driver)
    service.start_single_step()
    _wait_until(lambda: not service.worker_alive)

    assert not service.single_step_verified
    with pytest.raises(app_service.InvalidModeTransition):
        service.start_continuous()
    service.close()


def test_runner_stopped_state_cannot_be_resumed(tmp_path):
    def driver(_args, **kwargs):
        kwargs["on_step"](_step(tmp_path, status="stopped", action_kind="click"))
        return 0

    service = app_service.AppService(runtime=tmp_path, command_driver=driver)
    service.start_single_step()
    _wait_until(lambda: not service.worker_alive)

    with pytest.raises(app_service.InvalidModeTransition):
        service.resume()
    service.close()


def test_stop_cannot_interleave_resume_marker_cleanup(tmp_path, monkeypatch):
    control = tmp_path / "control"
    entered_resume_write = threading.Event()
    allow_resume_write = threading.Event()
    original = app_service.write_control_flag

    def blocked_write(control_dir, name):
        if name == "resume":
            entered_resume_write.set()
            allow_resume_write.wait(1)
        original(control_dir, name)

    monkeypatch.setattr(app_service, "write_control_flag", blocked_write)
    service = app_service.AppService(runtime=tmp_path, command_driver=lambda *_a, **_k: 0)
    resuming = threading.Thread(target=service.resume)
    resuming.start()
    assert entered_resume_write.wait(1)
    stopping = threading.Thread(target=service.stop)
    stopping.start()
    time.sleep(0.02)
    allow_resume_write.set()
    resuming.join(1)
    stopping.join(1)

    assert (control / "stop").exists()
    assert not (control / "resume").exists()
    service.close()


def test_stop_during_stale_cleanup_leaves_stop_marker_and_no_worker(tmp_path, monkeypatch):
    control = tmp_path / "control"
    control.mkdir()
    stale = control / "stop"
    stale.touch()
    cleanup_entered = threading.Event()
    allow_cleanup = threading.Event()
    original_unlink = type(stale).unlink

    def blocked_unlink(path, *args, **kwargs):
        if path == stale:
            cleanup_entered.set()
            allow_cleanup.wait(1)
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(type(stale), "unlink", blocked_unlink)
    calls = []
    service = app_service.AppService(
        runtime=tmp_path, command_driver=lambda *_a, **_k: calls.append(True) or 0
    )
    starter = threading.Thread(target=service.start)
    starter.start()
    assert cleanup_entered.wait(1)
    stopping = threading.Thread(target=service.stop)
    stopping.start()
    time.sleep(0.02)
    allow_cleanup.set()
    starter.join(1)
    stopping.join(1)

    assert stale.exists()
    assert calls == []
    service.close()


def test_external_stop_marker_before_single_step_return_prevents_unlock(tmp_path):
    control = tmp_path / "control"

    def driver(_args, **kwargs):
        kwargs["on_step"](
            _step(tmp_path, status="running", action_kind="click")
        )
        control.mkdir(exist_ok=True)
        (control / "stop").touch()
        return 0

    service = app_service.AppService(runtime=tmp_path, command_driver=driver)
    service.start_single_step()
    _wait_until(lambda: not service.worker_alive)

    assert not service.single_step_verified
    service.close()


def test_service_clears_stale_stop_before_worker_and_disables_driver_cleanup(tmp_path):
    control = tmp_path / "control"
    control.mkdir()
    (control / "stop").touch()
    observed = []

    def driver(_args, **kwargs):
        observed.append(((control / "stop").exists(), kwargs["clear_stale_stop"]))
        return 0

    service = app_service.AppService(runtime=tmp_path, command_driver=driver)
    service.start()
    _wait_until(lambda: not service.worker_alive)
    service.close()

    assert observed == [(False, False)]


def test_close_never_starts_an_extra_runner(tmp_path):
    calls = []
    service = app_service.AppService(
        runtime=tmp_path, command_driver=lambda *_a, **_k: calls.append(True) or 0
    )
    service.close()
    service.close()
    assert calls == []
    with pytest.raises(app_service.InvalidModeTransition):
        service.start()


def test_pending_stop_dominates_starter_waiting_for_operation_lock(tmp_path):
    calls = []

    def driver(_args, **kwargs):
        calls.append(kwargs["stop"])
        kwargs["stop"].wait(1)
        return 0

    service = app_service.AppService(runtime=tmp_path, command_driver=driver)
    service._operation_lock.acquire()
    try:
        stopping = threading.Thread(target=service.stop)
        stopping.start()
        _wait_until(lambda: service._stop_in_progress)
        starter = threading.Thread(target=service.start)
        starter.start()
    finally:
        service._operation_lock.release()
    stopping.join(1)
    starter.join(1)

    assert calls == []
    assert not service.worker_alive
    assert (tmp_path / "control" / "stop").exists()

    # Stop is not permanent: a start requested after stop completes is valid.
    service.start()
    _wait_until(lambda: len(calls) == 1)
    service.close()


@pytest.mark.parametrize("stop_count", [2, 8])
def test_all_overlapping_stops_must_finish_before_restart_can_start(
    tmp_path, stop_count
):
    calls = []

    def driver(_args, **kwargs):
        calls.append(kwargs["stop"])
        kwargs["stop"].wait(1)
        return 0

    service = app_service.AppService(runtime=tmp_path, command_driver=driver)
    service._operation_lock.acquire()
    try:
        stops = [threading.Thread(target=service.stop) for _ in range(stop_count)]
        for stopping in stops:
            stopping.start()
        _wait_until(lambda: service._active_stops == stop_count)
        starter = threading.Thread(target=service.start)
        starter.start()
        starter.join(1)
        assert not starter.is_alive()
        assert calls == []
    finally:
        service._operation_lock.release()
    for stopping in stops:
        stopping.join(1)

    assert service._active_stops == 0
    assert calls == []
    assert (tmp_path / "control" / "stop").exists()
    service.start()
    _wait_until(lambda: len(calls) == 1)
    service.close()


def test_close_remains_permanent_after_worker_has_stopped(tmp_path):
    service = app_service.AppService(runtime=tmp_path, command_driver=lambda *_a, **_k: 0)
    service.close()

    with pytest.raises(app_service.InvalidModeTransition):
        service.start()
    with pytest.raises(app_service.InvalidModeTransition):
        service.start_single_step()


def test_worker_exception_is_redacted_to_class_based_view_error(tmp_path):
    def driver(*_args, **_kwargs):
        raise RuntimeError("private password and coordinates")

    service = app_service.AppService(runtime=tmp_path, command_driver=driver)
    service.start()
    _wait_until(lambda: not service.worker_alive)
    state = service.drain_events()[-1]
    service.close()

    assert state == AppViewState.error(mode="observe")
    assert "private" not in repr(state)


def test_geometry_failure_publishes_only_bounded_readiness_reason(tmp_path):
    def driver(*_args, **_kwargs):
        raise RuntimeError("window geometry mismatch: private dimensions 1x2")

    service = app_service.AppService(runtime=tmp_path, command_driver=driver)
    service.start()
    _wait_until(lambda: not service.worker_alive)
    state = service.drain_events()[-1]
    service.close()

    assert state.readiness_failure == "geometry_mismatch"
    assert state.window_match is False
    assert not state.can_single_step
    assert not state.can_run_continuous
    assert "private" not in repr(state)


def test_detected_geometry_can_be_exported_as_minimal_atomic_config(tmp_path):
    service = app_service.AppService(runtime=tmp_path, command_driver=lambda *_a, **_k: 0)
    service.current_state = AppViewState.readiness_error(
        "geometry_mismatch", window_title="问道", detected_width=919,
        detected_height=674,
    )
    destination = tmp_path / "detected.yaml"

    assert service.write_geometry_config(destination) == destination
    assert destination.read_text() == (
        "window:\n  title: 问道\n  width: 919\n  height: 674\n"
    )
    loaded = app_service.load_config(destination)
    assert (loaded.width, loaded.height) == (919, 674)


def test_step_output_is_sanitized_with_config_targets_and_runtime_screens(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("daily_whitelist: [custom]\n", encoding="utf-8")

    def driver(_args, **kwargs):
        kwargs["on_step"](_step(tmp_path, target="daily_custom"))
        return 0

    service = app_service.AppService(
        config_path=config, runtime=tmp_path, command_driver=driver
    )
    service.start()
    _wait_until(lambda: not service.worker_alive)
    state = service.drain_events()[-1]
    service.close()

    assert state.target_names == ("daily_custom",)
    assert state.screenshot_path == str(tmp_path / "screens" / "capture-000001.png")
    assert "private OCR" not in repr(state)


def test_set_config_validates_then_restarts_observe_with_new_path_and_targets(tmp_path):
    first = tmp_path / "first.yaml"
    second = tmp_path / "second.yaml"
    _write_config(first, names="first")
    _write_config(second, names="latin, 师门")
    calls = []

    def driver(args, **kwargs):
        calls.append(args.config)
        kwargs["stop"].wait(1)
        return 0

    service = app_service.AppService(
        config_path=first, runtime=tmp_path / "runtime", command_driver=driver
    )
    service.start()
    _wait_until(lambda: len(calls) == 1)

    service.set_config(second)
    _wait_until(lambda: len(calls) == 2)

    assert calls == [first, second]
    assert service.config_path == second
    assert service.allowed_target_names == ("latin", "师门")
    assert service.mode is app_service.AppMode.OBSERVE
    assert not service.single_step_verified
    events = service.drain_events()
    assert events[-1].status == "observing"
    assert service.current_state.status == "observing"
    assert service.worker_alive
    service.close()


def test_invalid_config_is_bounded_and_never_applied(tmp_path):
    invalid = tmp_path / "secret.yaml"
    invalid.write_text("window: [private@example.com", encoding="utf-8")
    entered = threading.Event()

    def driver(_args, **kwargs):
        entered.set()
        kwargs["stop"].wait(1)
        return 0

    service = app_service.AppService(
        runtime=tmp_path / "runtime", command_driver=driver
    )
    service.start()
    assert entered.wait(1)

    service.set_config(invalid)
    _wait_until(lambda: not service.config_change_in_progress)

    assert service.stop_event.is_set()
    assert service.current_state.status == "stopped"
    assert service.current_state.readiness_failure == "invalid_config"
    assert "private" not in repr(service.current_state)
    assert service.config_path is None


def test_overlapping_config_selection_is_rejected_without_interleaving(tmp_path):
    first = tmp_path / "first.yaml"
    second = tmp_path / "second.yaml"
    _write_config(first, names="first")
    _write_config(second, names="second")
    release = threading.Event()
    calls = []

    def driver(args, **kwargs):
        calls.append(args.config)
        kwargs["stop"].wait(1)
        if len(calls) == 1:
            release.wait(1)
        return 0

    service = app_service.AppService(
        runtime=tmp_path / "runtime", command_driver=driver
    )
    service.start()
    _wait_until(lambda: len(calls) == 1)

    service.set_config(first)
    with pytest.raises(app_service.ConfigSelectionError) as error:
        service.set_config(second)
    release.set()
    _wait_until(lambda: len(calls) == 2)

    assert (
        error.value.reason
        is app_service.ConfigSelectionFailure.CHANGE_IN_PROGRESS
    )
    assert calls == [None, first]
    assert service.config_path == first
    assert service.allowed_target_names == ("first",)
    service.close()


def test_thread_start_failure_ends_in_bounded_error_not_false_observing(
    tmp_path, monkeypatch
):
    class BrokenThread:
        def __init__(self, **_kwargs):
            pass

        def start(self):
            raise RuntimeError("private thread failure")

    monkeypatch.setattr(app_service.threading, "Thread", BrokenThread)
    service = app_service.AppService(runtime=tmp_path / "runtime")

    with pytest.raises(app_service.InvalidModeTransition):
        service.start()

    assert not service.worker_alive
    assert service.current_state.status == "error"
    assert service.current_state.readiness_failure == "unknown_failure"
    assert service.drain_events()[-1].status == "error"
    assert "private" not in repr(service.current_state)


def test_config_thread_start_failure_releases_transition_and_stays_stopped(
    tmp_path, monkeypatch
):
    config = tmp_path / "valid.yaml"
    _write_config(config, names="valid")

    class BrokenThread:
        def __init__(self, **_kwargs):
            pass

        def start(self):
            raise RuntimeError("private thread failure")

    monkeypatch.setattr(app_service.threading, "Thread", BrokenThread)
    service = app_service.AppService(runtime=tmp_path / "runtime")

    with pytest.raises(app_service.ConfigSelectionError) as error:
        service.set_config(config)

    assert error.value.reason is app_service.ConfigSelectionFailure.TRANSITION_FAILED
    assert not service.config_change_in_progress
    assert service.current_state.status == "stopped"
    assert "private" not in repr(service.current_state)


def test_replacement_worker_start_failure_ends_config_transition_in_error(
    tmp_path, monkeypatch
):
    config = tmp_path / "valid.yaml"
    _write_config(config, names="valid")
    real_thread = threading.Thread
    created = 0

    def thread_factory(**kwargs):
        nonlocal created
        created += 1
        if created == 1:
            return real_thread(**kwargs)

        class BrokenThread:
            def start(self):
                raise RuntimeError("private replacement failure")

        return BrokenThread()

    monkeypatch.setattr(app_service.threading, "Thread", thread_factory)
    service = app_service.AppService(runtime=tmp_path / "runtime")

    service.set_config(config)
    _wait_until(lambda: not service.config_change_in_progress)

    assert not service.worker_alive
    assert service.current_state.status == "error"
    assert service.current_state.readiness_failure == "unknown_failure"
    assert service.drain_events()[-1].status == "error"
    assert "private" not in repr(service.current_state)


def test_notification_preview_is_dry_run_and_not_published(tmp_path, monkeypatch):
    config = tmp_path / "preview.yaml"
    _write_config(config)
    calls = []

    class FakeNotifier:
        def __init__(self, recipient, *, dry_run=False):
            calls.append((recipient, dry_run))
            self.recipient = recipient

        def send(self, notice):
            calls.append(notice)
            return "exact preview body"

    monkeypatch.setattr(app_service, "IMessageNotifier", FakeNotifier)
    service = app_service.AppService(config_path=config, runtime=tmp_path / "runtime")

    preview = service.preview_notification()

    assert preview == app_service.NotificationPreview(
        "preview@example.com", "exact preview body"
    )
    assert calls[0] == ("preview@example.com", True)
    assert service.drain_events() == []
    assert "preview@example.com" not in repr(service.current_state)


def test_ensure_runtime_directory_creates_only_configured_directory(tmp_path):
    runtime = tmp_path / "nested" / "runtime"
    service = app_service.AppService(runtime=runtime)

    assert service.ensure_runtime_directory() == runtime
    assert runtime.is_dir()

"""Headless integration coverage for the desktop service/CLI boundary."""

import threading
import time

import pytest

from wendao_bot.app_service import AppMode, AppService, InvalidModeTransition
from wendao_bot.models import ScreenSnapshot, ScreenState
from wendao_bot.orchestrator import RunStatus, StepResult


class ScriptClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += max(seconds, 0.1)


def _snapshot(tmp_path):
    return ScreenSnapshot(
        ScreenState.MAP,
        0.99,
        "private OCR must not reach the app",
        str(tmp_path / "screens" / "capture-000001.png"),
        {"main_quest": (12, 34)},
        frozenset({"ocr", "template"}),
    )


def _wait_until(predicate, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("condition not reached")


def test_service_drives_real_command_orchestration_across_safe_mode_sequence(tmp_path):
    """Exercise AppService -> cli.run_command, replacing only platform runners."""
    clock = ScriptClock()
    factory_calls = []
    continuous_entered = threading.Event()

    class Runner:
        def __init__(self, *, role, stop_event):
            self.role = role
            self.stop_event = stop_event
            self.status = (
                RunStatus.OBSERVING if role != "live" else RunStatus.RUNNING
            )

        def step(self):
            snapshot = _snapshot(tmp_path)
            if self.role == "persistent-observe":
                self.stop_event.wait(1)
                self.status = RunStatus.STOPPED
                return None
            if self.role == "preflight":
                return snapshot
            if self.role == "continuous":
                continuous_entered.set()
                self.stop_event.wait(1)
                self.status = RunStatus.STOPPED
                return None
            return StepResult(snapshot, snapshot, "click", "main_quest", "running")

    generation = {"number": 0}

    def runner_factory(_config, _runtime, *, observe_only, stop_event):
        call = (generation["number"], observe_only, stop_event)
        factory_calls.append(call)
        if generation["number"] == 0:
            role = "persistent-observe"
        elif observe_only:
            role = "preflight"
        elif generation["number"] == 2:
            role = "continuous"
        else:
            role = "live"
        return Runner(role=role, stop_event=stop_event)

    service = AppService(
        runtime=tmp_path,
        runner_factory=runner_factory,
        clock=clock,
        sleeper=clock.sleep,
    )

    service.start()
    _wait_until(lambda: len(factory_calls) == 1)
    observe_stop = factory_calls[0][2]
    assert service.mode is AppMode.OBSERVE
    assert not service.single_step_verified
    service.stop()
    assert observe_stop.is_set()

    generation["number"] = 1
    service.start_single_step()
    _wait_until(lambda: not service.worker_alive)
    single_calls = [call for call in factory_calls if call[0] == 1]
    assert [call[1] for call in single_calls] == [True, False]
    assert single_calls[0][2] is single_calls[1][2] is service.stop_event
    assert single_calls[0][2] is not observe_stop
    assert service.single_step_verified

    generation["number"] = 2
    service.start_continuous()
    assert continuous_entered.wait(1)
    continuous_calls = [call for call in factory_calls if call[0] == 2]
    assert [call[1] for call in continuous_calls] == [True, False]
    assert continuous_calls[0][2] is continuous_calls[1][2] is service.stop_event
    assert continuous_calls[0][2] is not single_calls[0][2]
    service.stop()
    assert continuous_calls[0][2].is_set()
    assert service.current_state.status == "stopped"


def test_live_observe_transitions_asynchronously_to_single_step(tmp_path):
    clock = ScriptClock()
    observe_entered = threading.Event()
    observe_stopped = threading.Event()
    calls = []

    class Runner:
        def __init__(self, observe_only, stop_event, role):
            self.status = RunStatus.OBSERVING if observe_only else RunStatus.RUNNING
            self.stop_event = stop_event
            self.role = role

        def step(self):
            snapshot = _snapshot(tmp_path)
            if self.role == "observe":
                observe_entered.set()
                self.stop_event.wait(1)
                observe_stopped.set()
                self.status = RunStatus.STOPPED
                return None
            if self.role == "preflight":
                return snapshot
            return StepResult(snapshot, snapshot, "click", "main_quest", "running")

    def factory(_config, _runtime, *, observe_only, stop_event):
        role = "observe" if not calls else ("preflight" if observe_only else "live")
        calls.append((role, stop_event))
        return Runner(observe_only, stop_event, role)

    service = AppService(runtime=tmp_path, runner_factory=factory, clock=clock, sleeper=clock.sleep)
    service.start()
    assert observe_entered.wait(1)

    started = time.monotonic()
    service.start_single_step()
    assert time.monotonic() - started < 0.1
    _wait_until(lambda: service.single_step_verified)

    assert observe_stopped.is_set()
    assert [role for role, _stop in calls] == ["observe", "preflight", "live"]
    assert calls[0][1] is not calls[1][1]
    assert calls[1][1] is calls[2][1]
    service.close()


@pytest.mark.parametrize(
    ("exit_status", "action_kind", "step_status"),
    [
        (1, "click", "running"),
        (0, None, "running"),
        (0, "click", "stopped"),
    ],
)
def test_real_command_boundary_never_unlocks_rejected_single_step(
    tmp_path, exit_status, action_kind, step_status
):
    clock = ScriptClock()
    factory_count = 0

    class Runner:
        def __init__(self, observe_only, stop_event):
            self.status = RunStatus.OBSERVING if observe_only else RunStatus.RUNNING
            self.stop_event = stop_event

        def step(self):
            snapshot = _snapshot(tmp_path)
            if self.status is RunStatus.OBSERVING:
                if exit_status:
                    self.status = RunStatus.PAUSED
                return snapshot
            if step_status == "stopped":
                self.status = RunStatus.STOPPED
            return StepResult(snapshot, snapshot, action_kind, "main_quest", step_status)

    def runner_factory(_config, _runtime, *, observe_only, stop_event):
        nonlocal factory_count
        factory_count += 1
        return Runner(observe_only, stop_event)

    service = AppService(
        runtime=tmp_path,
        runner_factory=runner_factory,
        clock=clock,
        sleeper=clock.sleep,
    )
    service.start_single_step()
    _wait_until(lambda: not service.worker_alive)

    assert not service.single_step_verified
    with pytest.raises(InvalidModeTransition):
        service.start_continuous()
    service.close()

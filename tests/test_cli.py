import threading
import pytest

import wendao_bot.cli as cli_module
from wendao_bot.app_service import build_runner
from wendao_bot.cli import (
    _build_runner,
    _drive,
    _write_flag,
    build_parser,
    run_command,
    template_path,
)
from wendao_bot.models import ScreenSnapshot, ScreenState
from wendao_bot.orchestrator import RunStatus


def test_cli_builder_compatibility_alias_uses_shared_service():
    assert _build_runner is build_runner


def test_run_command_resolves_shared_builder_at_call_time(monkeypatch, tmp_path):
    calls = []

    class Runner:
        status = RunStatus.RUNNING

        def step(self):
            self.status = RunStatus.STOPPED

    def replacement(*args, **kwargs):
        calls.append((args, kwargs))
        return Runner()

    monkeypatch.setattr(cli_module, "build_runner", replacement)
    args = build_parser().parse_args(
        ["observe", "--runtime", str(tmp_path), "--single-step"]
    )

    assert run_command(args, stop=threading.Event()) == 0
    assert calls[0][1]["observe_only"] is True


def test_run_command_defaults_to_observe_preflight():
    args = build_parser().parse_args(["run"])
    assert args.preflight_seconds == 30
    assert args.single_step is False


def test_run_supports_single_step():
    args = build_parser().parse_args(["run", "--single-step"])
    assert args.single_step is True


def test_control_and_notify_commands_parse():
    parser = build_parser()
    assert parser.parse_args(["pause"]).command == "pause"
    assert parser.parse_args(["resume"]).command == "resume"
    assert parser.parse_args(["stop"]).command == "stop"
    assert parser.parse_args(["notify-test", "--dry-run"]).dry_run is True


def test_single_step_waits_for_full_preflight_before_one_live_step(tmp_path):
    args = build_parser().parse_args(
        ["run", "--single-step", "--runtime", str(tmp_path)]
    )
    now = 0.0
    calls = []

    class Runner:
        def __init__(self, mode):
            self.mode = mode
            self.status = RunStatus.OBSERVING if mode == "observe" else RunStatus.RUNNING

        def step(self):
            calls.append((self.mode, now))
            return ScreenSnapshot(
                ScreenState.MAP, 0.99, "等级15", "x.png",
                {"main_quest": (1, 1)}, frozenset({"ocr", "template"}),
            )

    def factory(_config, _runtime, *, observe_only, stop_event):
        return Runner("observe" if observe_only else "live")

    def clock():
        return now

    def sleep(seconds):
        nonlocal now
        now += seconds

    result = run_command(
        args,
        runner_factory=factory,
        clock=clock,
        sleeper=sleep,
        stop=threading.Event(),
    )

    live_calls = [timestamp for mode, timestamp in calls if mode == "live"]
    assert result == 0
    assert live_calls == [30.0]
    assert all(mode == "observe" for mode, timestamp in calls if timestamp < 30.0)


def test_packaged_template_path_is_used_by_real_builder(tmp_path):
    assert template_path().is_dir()
    runner = _build_runner(None, tmp_path, observe_only=True)
    assert runner.recognizer._template_dir == template_path()


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "-inf"])
def test_preflight_seconds_must_be_positive_and_finite(value):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["run", "--preflight-seconds", value])


@pytest.mark.parametrize(
    ("evidence", "expected"),
    [({"ocr"}, 1), ({"template"}, 1), ({"ocr", "template"}, 0)],
)
def test_preflight_requires_actionable_ocr_and_template_evidence(
    tmp_path, evidence, expected
):
    args = build_parser().parse_args(
        ["run", "--preflight-seconds", "0.3", "--single-step", "--runtime", str(tmp_path)]
    )
    now = 0.0

    class Runner:
        def __init__(self, observing):
            self.observing = observing
            self.status = RunStatus.OBSERVING if observing else RunStatus.RUNNING

        def step(self):
            if not self.observing:
                self.status = RunStatus.STOPPED
            return ScreenSnapshot(
                ScreenState.MAP, 0.99, "等级15", "x.png",
                {"main_quest": (1, 1)}, frozenset(evidence),
            )

    def factory(*_args, observe_only, **_kwargs):
        return Runner(observe_only)

    def sleep(seconds):
        nonlocal now
        now += seconds

    assert run_command(
        args, runner_factory=factory, clock=lambda: now, sleeper=sleep,
        stop=threading.Event(),
    ) == expected


def test_preflight_resets_readiness_after_nonqualifying_snapshot(tmp_path):
    args = build_parser().parse_args(
        ["run", "--preflight-seconds", "0.4", "--single-step", "--runtime", str(tmp_path)]
    )
    now = 0.0
    evidence = iter((
        {"ocr", "template"}, {"ocr", "template"}, {"ocr"}, {"ocr", "template"},
    ))

    class Runner:
        status = RunStatus.OBSERVING
        def step(self):
            return ScreenSnapshot(
                ScreenState.MAP, 0.99, "private OCR", "x.png",
                {"main_quest": (1, 1)}, frozenset(next(evidence)),
            )

    def sleep(seconds):
        nonlocal now
        now += seconds

    result = run_command(
        args, runner_factory=lambda *_a, **_k: Runner(), clock=lambda: now,
        sleeper=sleep, stop=threading.Event(),
    )
    assert result == 1


def test_preflight_accepts_three_final_consecutive_ready_snapshots(tmp_path):
    args = build_parser().parse_args(
        ["run", "--preflight-seconds", "0.4", "--single-step", "--runtime", str(tmp_path)]
    )
    now = 0.0
    live_steps = []
    samples = iter(({"ocr"}, {"ocr", "template"}, {"ocr", "template"}, {"ocr", "template"}))

    class Runner:
        def __init__(self, observe_only):
            self.observe_only = observe_only
            self.status = RunStatus.OBSERVING if observe_only else RunStatus.RUNNING
        def step(self):
            if not self.observe_only:
                live_steps.append(True)
                self.status = RunStatus.STOPPED
            return ScreenSnapshot(
                ScreenState.MAP, 0.99, "private OCR", "x.png",
                {"main_quest": (1, 1)},
                frozenset(next(samples)) if self.observe_only else frozenset(),
            )

    def sleep(seconds):
        nonlocal now
        now += seconds

    assert run_command(
        args, runner_factory=lambda *_a, observe_only, **_k: Runner(observe_only),
        clock=lambda: now, sleeper=sleep, stop=threading.Event(),
    ) == 0
    assert live_steps == [True]


def test_builder_restores_valid_progress_and_rejects_malformed_progress(tmp_path):
    from wendao_bot.storage import RuntimeStore

    RuntimeStore(tmp_path).save_state({
        "progress": {
            "level": 16, "main_blocked_by_level": True,
            "shimen_completed": 4, "completed_dailies": ["除暴"],
        }
    })
    runner = _build_runner(None, tmp_path, observe_only=True)
    assert runner.progress.level == 16
    assert runner.progress.completed_dailies == {"除暴"}
    RuntimeStore(tmp_path).save_state({"progress": {"level": "admin"}})
    assert _build_runner(None, tmp_path, observe_only=True).progress.level is None


def test_drive_polls_paused_runner_until_resume_then_stop():
    calls = []

    class Runner:
        status = RunStatus.PAUSED

        def step(self):
            calls.append(self.status)
            self.status = RunStatus.RUNNING if len(calls) == 1 else RunStatus.STOPPED

    _drive(Runner(), single_step=False, stop=threading.Event(), sleeper=lambda n: calls.append(n))
    assert RunStatus.PAUSED in calls
    assert RunStatus.RUNNING in calls
    assert 0.1 in calls


def test_drive_prints_sanitized_step_json():
    lines = []

    class Result:
        def sanitized_event(self):
            return {
                "event": "step_observation", "state": "map", "confidence": 0.99,
                "targets": ["main_quest"], "action_kind": "click",
                "action_target": "main_quest", "status": "running",
                "screenshot": "/local/capture.png",
            }

    class Runner:
        status = RunStatus.RUNNING
        def step(self):
            self.status = RunStatus.STOPPED
            return Result()

    _drive(
        Runner(), single_step=False, stop=threading.Event(),
        sleeper=lambda _: None, output=lines.append,
    )
    assert '"confidence": 0.99' in lines[0]
    assert '"targets": ["main_quest"]' in lines[0]
    assert "private OCR" not in lines[0]
    assert "recipient" not in lines[0]


def test_new_run_clears_stale_stop_and_resume_clears_pause_and_stop(tmp_path):
    control = tmp_path / "control"
    control.mkdir()
    (control / "stop").touch()

    class Runner:
        status = RunStatus.RUNNING
        def step(self):
            self.status = RunStatus.STOPPED

    def factory(*args, **kwargs):
        assert not (control / "stop").exists()
        return Runner()

    args = build_parser().parse_args(["observe", "--runtime", str(tmp_path), "--single-step"])
    assert run_command(args, runner_factory=factory, stop=threading.Event()) == 0
    (control / "pause").touch()
    (control / "stop").touch()
    _write_flag(control, "resume")
    assert not (control / "pause").exists()
    assert not (control / "stop").exists()

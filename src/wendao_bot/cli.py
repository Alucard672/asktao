"""Command-line interface for the foreground helper."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import signal
import threading
import time

from .app_service import (
    build_runner,
    default_runtime_path,
    load_progress,
    template_path,
    write_control_flag,
)
from .config import load_config
from .models import ScreenSnapshot, ScreenState
from .notifier import IMessageNotifier, PauseNotice
from .orchestrator import Orchestrator, RunStatus


def _positive_finite(value: str) -> float:
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a number") from None
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be finite and greater than zero")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wendao-bot")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("observe", "run"):
        command = commands.add_parser(name)
        command.add_argument("--config", type=Path)
        command.add_argument("--runtime", type=Path)
        command.add_argument("--single-step", action="store_true")
        if name == "run":
            command.add_argument(
                "--preflight-seconds", type=_positive_finite, default=30.0
            )
    for name in ("pause", "resume", "stop"):
        command = commands.add_parser(name)
        command.add_argument("--runtime", type=Path)
    notify = commands.add_parser("notify-test")
    notify.add_argument("--config", type=Path)
    notify.add_argument("--dry-run", action="store_true")
    return parser


def _write_flag(control_dir: Path, name: str) -> None:
    write_control_flag(control_dir, name)


_build_runner = build_runner
_load_progress = load_progress


def _drive(
    runner: Orchestrator,
    *,
    single_step: bool,
    stop: threading.Event,
    sleeper=time.sleep,
    output=print,
    on_step=None,
) -> None:
    while not stop.is_set() and runner.status is not RunStatus.STOPPED:
        result = runner.step()
        if result is not None and on_step is not None:
            on_step(result)
        if result is not None and hasattr(result, "sanitized_event"):
            output(json.dumps(result.sanitized_event(), ensure_ascii=False, sort_keys=True))
        if single_step:
            return
        if runner.status is RunStatus.STOPPED:
            return
        sleeper(0.1)


def run_command(
    args: argparse.Namespace,
    *,
    runner_factory=None,
    clock=time.monotonic,
    sleeper=time.sleep,
    stop: threading.Event | None = None,
    output=print,
    on_step=None,
    clear_stale_stop: bool = True,
) -> int:
    if runner_factory is None:
        runner_factory = build_runner
    stop = stop or threading.Event()
    runtime = args.runtime or default_runtime_path()
    if clear_stale_stop:
        (runtime / "control" / "stop").unlink(missing_ok=True)
    if args.command == "run":
        observer = runner_factory(
            args.config, runtime, observe_only=True, stop_event=stop
        )
        deadline = clock() + args.preflight_seconds
        consecutive_ready = 0
        last_ready = False
        while clock() < deadline and not stop.is_set():
            snapshot = observer.step()
            last_ready = _preflight_ready(snapshot)
            consecutive_ready = consecutive_ready + 1 if last_ready else 0
            if observer.status in {RunStatus.PAUSED, RunStatus.STOPPED}:
                break
            sleeper(min(0.1, max(0.0, deadline - clock())))
        if (
            consecutive_ready < 3
            or not last_ready
            or observer.status in {RunStatus.PAUSED, RunStatus.STOPPED}
            or stop.is_set()
        ):
            return 1
    runner = runner_factory(
        args.config,
        runtime,
        observe_only=args.command == "observe",
        stop_event=stop,
    )
    _drive(
        runner,
        single_step=args.single_step,
        stop=stop,
        sleeper=sleeper,
        output=output,
        on_step=on_step,
    )
    return 1 if runner.status is RunStatus.PAUSED else 0


def _preflight_ready(snapshot) -> bool:
    if hasattr(snapshot, "snapshot"):
        snapshot = snapshot.snapshot
    actionable = {
        ScreenState.MAP,
        ScreenState.DIALOGUE,
        ScreenState.NPC_OPTIONS,
        ScreenState.REWARD,
        ScreenState.ACTIVITY_LIST,
    }
    return bool(
        isinstance(snapshot, ScreenSnapshot)
        and snapshot.state in actionable
        and snapshot.targets
        and {"ocr", "template"}.issubset(snapshot.evidence)
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    runtime = getattr(args, "runtime", None) or default_runtime_path()
    control = runtime / "control"
    if args.command in {"pause", "resume", "stop"}:
        _write_flag(control, args.command)
        return 0
    if args.command == "notify-test":
        config = load_config(args.config)
        body = IMessageNotifier(config.imessage_recipient, dry_run=args.dry_run).send(
            PauseNotice("notification test", None, None, "none", "manual")
        )
        if args.dry_run:
            print(f"To: {config.imessage_recipient}\n{body}")
        return 0
    stop = threading.Event()

    def request_stop(_signum, _frame):
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    return run_command(args, stop=stop)


if __name__ == "__main__":
    raise SystemExit(main())

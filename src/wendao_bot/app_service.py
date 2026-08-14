"""Reusable construction helpers for Wendao automation entry points."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from importlib.resources import files
import os
from pathlib import Path
from queue import Empty, Full, Queue
import sys
import tempfile
import threading
import time
from typing import Callable

from .app_model import AppViewState
from .config import ConfigError, load_config
from .notifier import IMessageNotifier, PauseNotice
from .orchestrator import Orchestrator, StepResult
from .recognizer import ScreenRecognizer
from .safety import SafetyGuard
from .session import GameSession, QuartzBackend
from .storage import RuntimeStore
from .tasks import Progress, ProgressExtractor, TaskPlanner


def accessibility_is_trusted() -> bool:
    if sys.platform == "win32":
        return True
    try:
        import ctypes

        framework = ctypes.CDLL(
            "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
        )
        framework.AXIsProcessTrusted.restype = ctypes.c_bool
        return bool(framework.AXIsProcessTrusted())
    except Exception:
        return False


class AppMode(str, Enum):
    OBSERVE = "observe"
    SINGLE_STEP = "single_step"
    CONTINUOUS = "continuous"


class InvalidModeTransition(RuntimeError):
    """Raised when an app mode request would weaken the safety gates."""


class ConfigSelectionFailure(str, Enum):
    INVALID_CONFIG = "invalid_config"
    SERVICE_CLOSED = "service_closed"
    CHANGE_IN_PROGRESS = "change_in_progress"
    TRANSITION_FAILED = "transition_failed"


class ConfigSelectionError(RuntimeError):
    """A bounded configuration-selection failure safe for the controller."""

    def __init__(self, reason: ConfigSelectionFailure) -> None:
        self.reason = reason
        super().__init__(reason.value)


@dataclass(frozen=True)
class NotificationPreview:
    recipient: str
    body: str


def default_runtime_path() -> Path:
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        return base / "WendaoBot"
    return Path.home() / "Library" / "Application Support" / "WendaoBot"


def write_control_flag(control_dir: Path, name: str) -> None:
    if name not in {"resume", "stop", "pause"}:
        raise ValueError("unknown control marker")
    control_dir.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{name}.", dir=control_dir)
    os.close(descriptor)
    os.replace(temporary, control_dir / name)
    if name == "resume":
        (control_dir / "pause").unlink(missing_ok=True)
        (control_dir / "stop").unlink(missing_ok=True)
    elif name == "stop":
        (control_dir / "resume").unlink(missing_ok=True)


def template_path() -> Path:
    return Path(files("wendao_bot").joinpath("templates"))


def resolve_template_dir(runtime: Path) -> Path:
    candidate = Path(runtime) / "templates"
    try:
        if (
            candidate.is_dir()
            and not candidate.is_symlink()
            and any(candidate.glob("*.png"))
        ):
            return candidate
    except OSError:
        pass
    return template_path()


def load_progress(store: RuntimeStore) -> Progress:
    raw = store.load_state().get("progress")
    if not isinstance(raw, dict):
        return Progress(None)
    try:
        completed = raw.get("completed_dailies", [])
        if not isinstance(completed, list):
            return Progress(None)
        return Progress(
            raw.get("level"),
            raw.get("main_blocked_by_level", False),
            raw.get("shimen_completed", 0),
            frozenset(completed),
        )
    except (TypeError, ValueError):
        return Progress(None)


def _platform_session(config) -> GameSession:
    owner = getattr(config, "window_owner", None)
    if sys.platform == "win32":
        from .session_windows import Win32Backend

        if not owner:
            raise ConfigError(
                "window.owner is required on Windows: set it to the emulator "
                "executable name shown in Task Manager (e.g. MuMuPlayer.exe, "
                "dnplayer.exe)"
            )
        return GameSession(
            Win32Backend(),
            config.window_title,
            config.width,
            config.height,
            None,
            owner,
        )
    if owner:
        return GameSession(
            QuartzBackend(), config.window_title, config.width, config.height, None, owner
        )
    return GameSession(
        QuartzBackend(), config.window_title, config.width, config.height
    )


def build_runner(
    config_path: Path | None,
    runtime: Path,
    observe_only: bool,
    *,
    stop_event: threading.Event | None = None,
    notification_dry_run: bool = False,
) -> Orchestrator:
    config = load_config(config_path)
    store = RuntimeStore(runtime)
    progress = load_progress(store)
    return Orchestrator(
        session=_platform_session(config),
        recognizer=ScreenRecognizer(
            template_dir=resolve_template_dir(runtime),
            match_threshold=config.min_confidence,
            daily_whitelist=config.daily_whitelist,
        ),
        planner=TaskPlanner(config.daily_whitelist),
        safety=SafetyGuard(config.min_confidence, config.max_unchanged_actions),
        notifier=IMessageNotifier(
            config.imessage_recipient, dry_run=notification_dry_run
        ),
        store=store,
        progress=progress,
        keymap=dict(getattr(config, "keymap", ()) or ()),
        progress_extractor=ProgressExtractor(config.daily_whitelist),
        progress_min_confidence=config.min_confidence,
        stop_event=stop_event,
        observe_only=observe_only,
        action_timeout_seconds=config.action_timeout_seconds,
        battle_timeout_seconds=config.battle_timeout_seconds,
    )


def build_app_runner(
    config_path: Path | None,
    runtime: Path,
    observe_only: bool,
    *,
    stop_event: threading.Event | None = None,
) -> Orchestrator:
    return build_runner(
        config_path,
        runtime,
        observe_only=observe_only,
        stop_event=stop_event,
        notification_dry_run=True,
    )


class AppService:
    """Own one fail-closed background runner and sanitized GUI updates."""

    def __init__(
        self,
        config_path: Path | None = None,
        runtime: Path | None = None,
        *,
        runner_factory=None,
        command_driver=None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        permission_checker: Callable[[], bool] | None = None,
    ) -> None:
        self.config_path = config_path
        self.runtime = Path(runtime) if runtime is not None else default_runtime_path()
        self.runner_factory = runner_factory or build_app_runner
        self.command_driver = command_driver
        self.clock = clock
        self.sleeper = sleeper
        self.permission_checker = permission_checker or (
            accessibility_is_trusted if command_driver is None else (lambda: True)
        )
        self.state_queue = Queue(maxsize=100)
        self.current_state = AppViewState.initial()
        self.mode = AppMode.OBSERVE
        self._single_step_verified = False
        self.stop_event = threading.Event()
        self._worker = None
        self._operation_lock = threading.RLock()
        self._state_lock = threading.Lock()
        self._closing = threading.Event()
        self._active_stops = 0
        self._stop_generation = 0
        self._stopped = False
        self._urgent_lock = threading.Lock()
        self._urgent_stops = 0
        self._config_thread = None
        self._transition_thread = None
        self._config_lock = threading.Lock()
        try:
            initial_config = load_config(self.config_path)
        except Exception:
            initial_config = None
        self.allowed_target_names = (
            initial_config.daily_whitelist if initial_config is not None else ()
        )

    @property
    def worker_alive(self) -> bool:
        worker = self._worker
        return bool(worker is not None and worker.is_alive())

    @property
    def single_step_verified(self) -> bool:
        return self._single_step_verified

    @property
    def _stop_in_progress(self) -> bool:
        with self._state_lock:
            return self._active_stops > 0

    def start(self) -> None:
        self._start_mode(AppMode.OBSERVE)

    def start_single_step(self) -> None:
        self._request_mode(AppMode.SINGLE_STEP)

    def start_continuous(self) -> None:
        with self._operation_lock:
            if not self.single_step_verified:
                raise InvalidModeTransition(
                    "continuous mode requires a successful single step"
                )
            self._request_mode(AppMode.CONTINUOUS)

    def _request_mode(self, mode: AppMode) -> None:
        with self._state_lock:
            if self._closing.is_set():
                raise InvalidModeTransition("closed service cannot change mode")
            generation = self._stop_generation
            old_stop = self.stop_event
            alive = self.worker_alive
        if not alive:
            self._start_mode(mode)
            return
        old_stop.set()

        def transition() -> None:
            self.join()
            with self._state_lock:
                cancelled = bool(
                    self._closing.is_set()
                    or self._stopped
                    or self._stop_generation != generation
                )
            if not cancelled:
                self._start_mode(mode)

        thread = threading.Thread(
            target=transition,
            name=f"wendao-transition-{mode.value}",
            daemon=True,
        )
        self._transition_thread = thread
        thread.start()

    def pause(self) -> None:
        write_control_flag(self.runtime / "control", "pause")

    def resume(self) -> None:
        with self._operation_lock:
            with self._state_lock:
                if self._closing.is_set() or self._stopped:
                    raise InvalidModeTransition("stopped service cannot be resumed")
                write_control_flag(self.runtime / "control", "resume")

    def stop(self) -> None:
        with self._urgent_lock:
            self._urgent_stops += 1
        with self._state_lock:
            self._active_stops += 1
            self._stop_generation += 1
            self._stopped = True
            stop = self.stop_event
            write_control_flag(self.runtime / "control", "stop")
            stop.set()
        try:
            with self._operation_lock:
                self._join_worker()
            if self.current_state.status != "stopped":
                self._publish(AppViewState.stopped())
        finally:
            with self._state_lock:
                self._active_stops -= 1
            with self._urgent_lock:
                self._urgent_stops -= 1

    def request_stop(self) -> None:
        with self._urgent_lock:
            self._urgent_stops += 1
        try:
            with self._state_lock:
                self._stop_generation += 1
                self._stopped = True
                stop = self.stop_event
                write_control_flag(self.runtime / "control", "stop")
                stop.set()
        finally:
            with self._urgent_lock:
                self._urgent_stops -= 1
        if not self.worker_alive and self.current_state.status != "stopped":
            self._publish(AppViewState.stopped())

    def request_close(self) -> None:
        with self._state_lock:
            self._closing.set()
            stop = self.stop_event
        stop.set()

    def join(self, timeout: float | None = None) -> bool:
        worker = self._worker
        if worker is None or worker is threading.current_thread():
            return True
        worker.join(timeout)
        return not worker.is_alive()

    def close(self) -> None:
        self.request_close()
        with self._operation_lock:
            self._join_worker()

    def drain_events(self) -> list[AppViewState]:
        states = []
        while True:
            try:
                states.append(self.state_queue.get_nowait())
            except Empty:
                return states

    @property
    def config_change_in_progress(self) -> bool:
        acquired = self._config_lock.acquire(blocking=False)
        if acquired:
            self._config_lock.release()
        return not acquired

    def set_config(self, path: Path) -> None:
        candidate = Path(path)
        if self._closing.is_set():
            raise ConfigSelectionError(ConfigSelectionFailure.SERVICE_CLOSED)
        if not self._config_lock.acquire(blocking=False):
            raise ConfigSelectionError(ConfigSelectionFailure.CHANGE_IN_PROGRESS)
        try:
            self.request_stop()
        except Exception:
            self._config_lock.release()
            raise ConfigSelectionError(
                ConfigSelectionFailure.TRANSITION_FAILED
            ) from None

        def transition() -> None:
            try:
                self.join()
                try:
                    config = load_config(candidate)
                except Exception:
                    self._single_step_verified = False
                    self._publish(AppViewState.configuration_error())
                    return
                with self._operation_lock:
                    if self._closing.is_set():
                        return
                    self.config_path = candidate
                    self.allowed_target_names = config.daily_whitelist
                    self._single_step_verified = False
                try:
                    self.start()
                except InvalidModeTransition:
                    return
            finally:
                self._config_lock.release()

        worker = threading.Thread(
            target=transition,
            name="wendao-config-apply",
            daemon=True,
        )
        self._config_thread = worker
        try:
            worker.start()
        except Exception:
            self._config_lock.release()
            raise ConfigSelectionError(
                ConfigSelectionFailure.TRANSITION_FAILED
            ) from None

    def ensure_runtime_directory(self) -> Path:
        runtime = self.runtime
        if runtime.exists():
            if not runtime.is_dir() or runtime.is_symlink():
                raise RuntimeError("runtime_unavailable")
            return runtime
        runtime.mkdir(parents=True, mode=0o700, exist_ok=False)
        return runtime

    def write_geometry_config(self, destination: Path) -> Path:
        state = self.current_state
        config = load_config(self.config_path)
        if not (
            state.readiness_failure == "geometry_mismatch"
            and state.window_title == config.window_title
            and state.detected_width
            and state.detected_height
        ):
            raise RuntimeError("detected_geometry_unavailable")
        destination = Path(destination)
        if destination.is_symlink() or destination.parent.is_symlink():
            raise RuntimeError("unsafe_destination")
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = (
            f"window:\n  title: {config.window_title}"
            f"\n  width: {state.detected_width}"
            f"\n  height: {state.detected_height}\n"
        ).encode()
        descriptor, temporary = tempfile.mkstemp(
            prefix=".wendao-config.", dir=destination.parent
        )
        try:
            os.write(descriptor, payload)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, destination)
        return destination

    def preview_notification(self) -> NotificationPreview:
        config = load_config(self.config_path)
        notice = PauseNotice(
            reason="通知预览（不会发送）",
            level=None,
            task=None,
            screenshot="无",
            timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        body = IMessageNotifier(config.imessage_recipient, dry_run=True).send(notice)
        return NotificationPreview(config.imessage_recipient, body)

    def _start_mode(self, mode: AppMode) -> None:
        with self._state_lock:
            if self._closing.is_set():
                raise InvalidModeTransition("service is closed")
            if self._active_stops:
                return
            stop_generation = self._stop_generation
        with self._operation_lock:
            if self.worker_alive:
                if mode is AppMode.OBSERVE and self.mode is AppMode.OBSERVE:
                    return
                raise InvalidModeTransition("a worker is already running")
            if self._start_is_blocked(stop_generation):
                if self._closing.is_set():
                    raise InvalidModeTransition("service is closed")
                return
            self.stop_event.set()
            self._join_worker()
            if self._start_is_blocked(stop_generation):
                if self._closing.is_set():
                    raise InvalidModeTransition("service is closed")
                return
            stop = threading.Event()
            with self._state_lock:
                if (
                    self._closing.is_set()
                    or self._active_stops
                    or self._urgent_stop_pending()
                    or self._stop_generation != stop_generation
                ):
                    if self._closing.is_set():
                        raise InvalidModeTransition("service is closed")
                    return
                (self.runtime / "control" / "stop").unlink(missing_ok=True)
                if (
                    self._closing.is_set()
                    or self._active_stops
                    or self._stop_generation != stop_generation
                ):
                    if self._closing.is_set():
                        raise InvalidModeTransition("service is closed")
                    return
                self.stop_event = stop
                self._stopped = False
            if self._urgent_stop_pending():
                stop.set()
                return
            self.mode = mode
            worker = threading.Thread(
                target=self._run_worker,
                args=(mode, stop),
                name=f"wendao-{mode.value}",
                daemon=True,
            )
            self._worker = worker
            self._publish(AppViewState.starting(mode=mode.value))
            try:
                worker.start()
            except Exception:
                stop.set()
                with self._state_lock:
                    if self._worker is worker:
                        self._worker = None
                    self._stopped = True
                self._publish(
                    AppViewState.readiness_error("unknown_failure", mode=mode.value)
                )
                raise InvalidModeTransition("worker start failed") from None

    def _start_is_blocked(self, stop_generation: int) -> bool:
        with self._state_lock:
            return bool(
                self._closing.is_set()
                or self._active_stops
                or self._urgent_stop_pending()
                or self._stop_generation != stop_generation
            )

    def _urgent_stop_pending(self) -> bool:
        with self._urgent_lock:
            return self._urgent_stops > 0

    def _join_worker(self) -> None:
        worker = self._worker
        if worker is not None and worker is not threading.current_thread():
            worker.join()

    def _run_worker(self, mode: AppMode, stop: threading.Event) -> None:
        completed_live_click = False

        def on_step(step) -> None:
            nonlocal completed_live_click
            config = load_config(self.config_path)
            state = AppViewState.from_step(
                step,
                mode=mode.value,
                single_step_verified=self.single_step_verified,
                min_confidence=config.min_confidence,
                screenshot_root=(self.runtime / "screens").resolve(strict=False),
                allowed_target_names=config.daily_whitelist,
                accessibility_trusted=self.permission_checker(),
            )
            try:
                event = step.sanitized_event()
            except Exception:
                event = None
            completed_live_click = completed_live_click or bool(
                isinstance(step, StepResult)
                and isinstance(event, dict)
                and event.get("action_kind") == "click"
                and event.get("status") == "running"
            )
            if isinstance(event, dict) and event.get("status") == "stopped":
                with self._state_lock:
                    self._stopped = True
            self._publish(state)

        try:
            if self._closing.is_set() or stop.is_set():
                return
            if self.runner_factory is build_app_runner and self.command_driver is None:
                config = load_config(self.config_path)
                matches = [
                    window
                    for window in QuartzBackend().list_windows()
                    if window.title == config.window_title
                    and window.owner_name == config.window_title
                ]
                if len(matches) == 1 and (
                    matches[0].width,
                    matches[0].height,
                ) != (config.width, config.height):
                    self._publish(
                        AppViewState.readiness_error(
                            "geometry_mismatch",
                            mode=mode.value,
                            window_title=config.window_title,
                            detected_width=matches[0].width,
                            detected_height=matches[0].height,
                        )
                    )
                    return
            args = argparse.Namespace(
                command="observe" if mode is AppMode.OBSERVE else "run",
                config=self.config_path,
                runtime=self.runtime,
                single_step=mode is AppMode.SINGLE_STEP,
                preflight_seconds=30.0,
            )
            driver = self.command_driver
            if driver is None:
                from .cli import run_command

                driver = run_command
            kwargs = {
                "clock": self.clock,
                "sleeper": self.sleeper,
                "stop": stop,
                "output": lambda _line: None,
                "on_step": on_step,
                "clear_stale_stop": False,
            }
            if self.runner_factory is not None:
                kwargs["runner_factory"] = self.runner_factory
            result = driver(args, **kwargs)
            if (
                mode is AppMode.SINGLE_STEP
                and result == 0
                and completed_live_click
                and not stop.is_set()
                and not self._closing.is_set()
            ):
                with self._state_lock:
                    stopped = bool(
                        self._stopped
                        or stop.is_set()
                        or (self.runtime / "control" / "stop").is_file()
                    )
                    if not stopped:
                        self._single_step_verified = True
                if not stopped:
                    self._publish(self.current_state.with_single_step_success())
        except Exception as exc:
            self._log_worker_error(exc)
            self._publish(_bounded_worker_error(exc, mode=mode.value))
        finally:
            with self._state_lock:
                explicitly_stopped = bool(
                    stop.is_set() or self._stopped or self._closing.is_set()
                )
            if explicitly_stopped:
                self._publish(AppViewState.stopped())

    def _log_worker_error(self, error: BaseException) -> None:
        import traceback

        try:
            self.runtime.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).isoformat()
            with (self.runtime / "worker-errors.txt").open(
                "a", encoding="utf-8"
            ) as stream:
                stream.write(f"\n=== {stamp} ===\n")
                stream.write(
                    "".join(
                        traceback.format_exception(
                            type(error), error, error.__traceback__
                        )
                    )
                )
        except OSError:
            pass

    def _publish(self, state: AppViewState) -> None:
        self.current_state = state
        try:
            self.state_queue.put_nowait(state)
        except Full:
            try:
                self.state_queue.get_nowait()
            except Empty:
                pass
            self.state_queue.put_nowait(state)


def _bounded_worker_error(error: BaseException, mode: str) -> AppViewState:
    message = str(error).lower()
    if "geometry" in message or "pixel dimensions" in message:
        reason = "geometry_mismatch"
    elif "expected exactly one window" in message and "found 0" in message:
        reason = "window_not_found"
    elif "expected exactly one window" in message:
        reason = "multiple_windows"
    elif "owner" in message or "identity" in message:
        reason = "owner_mismatch"
    elif "capture" in message or "encode" in message:
        reason = "capture_failed"
    else:
        return AppViewState.error(type(error), mode=mode)
    return AppViewState.readiness_error(reason, mode=mode)

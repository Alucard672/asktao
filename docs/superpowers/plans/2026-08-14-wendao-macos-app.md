# 问道前台助手 macOS App Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a double-clickable `问道前台助手.app` that starts in observe-only mode and exposes safe single-step, continuous, pause, resume, and stop controls without bypassing the existing automation core.

**Architecture:** Extract a reusable application service around the existing runner construction and orchestration loops, then place a small immutable GUI state model above it. A lazy AppKit adapter owns native controls on the main thread while a worker thread owns observation/automation; a queue carries sanitized status updates. PyInstaller packages the entry point, YAML, templates, PyObjC frameworks, and OpenCV into a local unsigned app bundle.

**Tech Stack:** Python 3.11+, PyObjC/AppKit, threading/queue, PyInstaller, setuptools, pytest.

---

## File map

- Create `src/wendao_bot/app_service.py`: reusable worker lifecycle and safe mode transitions.
- Create `src/wendao_bot/app_model.py`: immutable UI state and sanitized step-to-view conversion.
- Create `src/wendao_bot/app.py`: lazy AppKit application entry point and native controller.
- Create `scripts/wendao_app.py`: absolute-import launcher used by PyInstaller.
- Create `packaging/wendao_app.spec`: deterministic PyInstaller bundle definition.
- Create `scripts/build_app.sh`: repeatable local build and ZIP creation.
- Create `tests/test_app_service.py`: worker lifecycle and safety-state tests.
- Create `tests/test_app_model.py`: state/redaction tests.
- Create `tests/test_app.py`: AppKit-independent controller tests and entry-point smoke tests.
- Create `tests/test_packaging.py`: bundle-resource and build-definition tests.
- Modify `src/wendao_bot/cli.py`: reuse the shared runner factory without changing CLI behavior.
- Modify `pyproject.toml`: add app/build extras and the GUI entry point.
- Modify `README.md`: double-click build/install/use instructions and external validation limits.

### Task 1: Extract a reusable runner factory

**Files:**
- Create: `src/wendao_bot/app_service.py`
- Modify: `src/wendao_bot/cli.py`
- Test: `tests/test_app_service.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing factory compatibility tests**

```python
class FakeRunner:
    status = RunStatus.STOPPED
    def step(self):
        return None


def observe_args(tmp_path):
    return argparse.Namespace(
        command="observe", config=None, runtime=tmp_path,
        single_step=True,
    )


def test_build_runner_preserves_observe_only_and_shared_stop(monkeypatch, tmp_path):
    stop = threading.Event()
    built = build_runner(None, tmp_path, observe_only=True, stop_event=stop)
    assert built.status is RunStatus.OBSERVING
    assert built.stop_event is stop


def test_cli_uses_shared_build_runner(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(cli, "build_runner", lambda *a, **k: calls.append(k) or FakeRunner())
    assert cli.run_command(observe_args(tmp_path), stop=threading.Event()) == 0
    assert calls[0]["observe_only"] is True
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `.venv/bin/python -m pytest -q tests/test_app_service.py tests/test_cli.py`

Expected: FAIL because `wendao_bot.app_service` and `cli.build_runner` do not exist.

- [ ] **Step 3: Move construction behind the shared function**

```python
# app_service.py
def build_runner(config_path, runtime, *, observe_only, stop_event=None):
    config = load_config(config_path)
    store = RuntimeStore(runtime)
    return Orchestrator(
        session=GameSession(QuartzBackend(), config.window_title, config.width, config.height),
        recognizer=ScreenRecognizer(
            template_dir=template_path(),
            match_threshold=config.min_confidence,
            daily_whitelist=config.daily_whitelist,
        ),
        planner=TaskPlanner(config.daily_whitelist),
        safety=SafetyGuard(config.min_confidence, config.max_unchanged_actions),
        notifier=IMessageNotifier(config.imessage_recipient),
        store=store,
        progress=load_progress(store),
        progress_extractor=ProgressExtractor(config.daily_whitelist),
        progress_min_confidence=config.min_confidence,
        stop_event=stop_event,
        observe_only=observe_only,
        action_timeout_seconds=config.action_timeout_seconds,
        battle_timeout_seconds=config.battle_timeout_seconds,
    )
```

Keep `template_path()` and `load_progress()` in this module. Import `build_runner` into `cli.py` and retain `_build_runner = build_runner` as a compatibility alias for existing tests/callers.

- [ ] **Step 4: Run focused and full tests**

Run: `.venv/bin/python -m pytest -q tests/test_app_service.py tests/test_cli.py`

Expected: PASS.

Run: `.venv/bin/python -m pytest -q`

Expected: all existing tests PASS with unchanged CLI semantics.

- [ ] **Step 5: Commit**

```bash
git add src/wendao_bot/app_service.py src/wendao_bot/cli.py tests/test_app_service.py tests/test_cli.py
git commit -m "refactor: share automation runner construction"
```

### Task 2: Add immutable, privacy-safe application state

**Files:**
- Create: `src/wendao_bot/app_model.py`
- Test: `tests/test_app_model.py`

- [ ] **Step 1: Write failing state-model tests**

```python
def test_observation_view_contains_only_sanitized_fields():
    snapshot = ScreenSnapshot(
        ScreenState.MAP, 0.95, "private chat wendao-owner@example.com",
        "/tmp/capture.png", {"main_quest": (10, 20)}, {"ocr", "template"},
    )
    view = AppViewState.from_step(StepResult(snapshot, snapshot, "click", "main_quest", "observing"))
    rendered = repr(view)
    assert view.screen_state == "map"
    assert view.targets == ("main_quest",)
    assert "private chat" not in rendered
    assert "@icloud.com" not in rendered


def test_continuous_is_locked_until_successful_single_step():
    state = AppViewState.initial()
    assert state.can_run_continuous is False
    assert state.with_single_step_success().can_run_continuous is True
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/bin/python -m pytest -q tests/test_app_model.py`

Expected: FAIL because `AppViewState` does not exist.

- [ ] **Step 3: Implement the minimal immutable model**

```python
@dataclass(frozen=True)
class AppViewState:
    mode: str
    status: str
    screen_state: str
    confidence: float | None
    targets: tuple[str, ...]
    screenshot: str | None
    pause_reason: str | None
    can_single_step: bool
    can_run_continuous: bool

    @classmethod
    def initial(cls):
        return cls("observe", "starting", "unknown", None, (), None, None, False, False)

    @classmethod
    def from_step(cls, step, *, mode="observe", single_step_verified=False):
        event = step.sanitized_event()
        ready = bool(event["targets"] and event["confidence"] >= 0.88)
        return cls(
            mode, event["status"], event["state"], event["confidence"],
            tuple(event["targets"]), event["screenshot"], None,
            ready, bool(single_step_verified),
        )
```

Do not store `ScreenSnapshot.text`, notifier recipient, or raw subprocess errors in this model.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest -q tests/test_app_model.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wendao_bot/app_model.py tests/test_app_model.py
git commit -m "feat: add privacy-safe app state model"
```

### Task 3: Implement the safe background application service

**Files:**
- Modify: `src/wendao_bot/app_service.py`
- Test: `tests/test_app_service.py`

- [ ] **Step 1: Write failing lifecycle tests with fake runners**

```python
@dataclass(frozen=True)
class FactoryCall:
    observe_only: bool
    stop_event: threading.Event


class RecordingFactory:
    def __init__(self):
        self.calls = []

    def __call__(self, _config, _runtime, *, observe_only, stop_event):
        self.calls.append(FactoryCall(observe_only, stop_event))
        return FakeRunner()


class BlockingFactory(RecordingFactory):
    def __call__(self, *args, **kwargs):
        super().__call__(*args, **kwargs)
        return BlockingRunner(kwargs["stop_event"])


def test_start_defaults_to_observe_and_emits_sanitized_state(tmp_path):
    service = AppService(runner_factory=RecordingFactory(), runtime=tmp_path)
    service.start()
    event = service.events.get(timeout=1)
    assert event.mode == "observe"
    assert service.mode is AppMode.OBSERVE
    assert service.factory.calls[0].observe_only is True


def test_continuous_rejected_before_successful_single_step(tmp_path):
    service = AppService(runner_factory=RecordingFactory(), runtime=tmp_path)
    with pytest.raises(InvalidModeTransition):
        service.start_continuous()


def test_stop_event_is_checked_before_worker_exit(tmp_path):
    service = AppService(runner_factory=BlockingFactory(), runtime=tmp_path)
    service.start()
    service.stop()
    assert service.stop_event.is_set()
    assert service.join(timeout=1)
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/bin/python -m pytest -q tests/test_app_service.py`

Expected: FAIL because `AppService`, `AppMode`, and transition validation do not exist.

- [ ] **Step 3: Implement the worker state machine**

```python
class AppMode(Enum):
    OBSERVE = "observe"
    SINGLE_STEP = "single_step"
    CONTINUOUS = "continuous"


class AppService:
    def __init__(self, *, config_path=None, runtime=None, runner_factory=build_runner):
        self.events = Queue()
        self.stop_event = threading.Event()
        self._single_step_verified = False
        self._thread = None

    def start(self):
        self._start_worker(AppMode.OBSERVE)

    def start_single_step(self):
        self._replace_worker(AppMode.SINGLE_STEP)

    def start_continuous(self):
        if not self._single_step_verified:
            raise InvalidModeTransition("continuous run requires a verified single step")
        self._replace_worker(AppMode.CONTINUOUS)

    def stop(self):
        self.stop_event.set()
        _write_flag(self.runtime / "control", "stop")
```

The single-step worker must call the same `run_command` preflight path with `preflight_seconds=30.0`; it may set `_single_step_verified` only when that call returns success after exactly one live step. Pause/resume/stop use the same control markers as the CLI. Worker exceptions become bounded local error states containing only the exception class.

- [ ] **Step 4: Add adversarial transition tests**

```python
def test_second_worker_is_rejected(service):
    service.start()
    with pytest.raises(InvalidModeTransition):
        service.start_single_step()


def test_resume_when_stopped_is_rejected(service):
    service.stop()
    with pytest.raises(InvalidModeTransition):
        service.resume()


def test_close_never_starts_an_additional_runner(service):
    service.start()
    count = len(service.factory.calls)
    service.close()
    assert len(service.factory.calls) == count
```

- [ ] **Step 5: Run focused and full tests**

Run: `.venv/bin/python -m pytest -q tests/test_app_service.py`

Expected: PASS.

Run: `.venv/bin/python -m pytest -q`

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/wendao_bot/app_service.py tests/test_app_service.py
git commit -m "feat: add safe app worker lifecycle"
```

### Task 4: Add the AppKit controller and double-click entry point

**Files:**
- Create: `src/wendao_bot/app.py`
- Create: `scripts/wendao_app.py`
- Create: `tests/test_app.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Write AppKit-independent controller tests**

```python
def test_controller_launch_starts_observe_only():
    service = FakeService()
    controller = AppController(service, FakeView())
    controller.application_did_finish_launching()
    assert service.calls == ["start"]


def test_view_buttons_follow_state():
    view = FakeView()
    controller = AppController(FakeService(), view)
    controller.render(AppViewState.initial())
    assert view.enabled["continuous"] is False
    assert view.enabled["single_step"] is False


def test_close_sets_stop_before_terminating():
    service = FakeService()
    controller = AppController(service, FakeView())
    controller.application_should_terminate()
    assert service.calls[:2] == ["stop", "join"]
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/bin/python -m pytest -q tests/test_app.py`

Expected: FAIL because the application controller does not exist.

- [ ] **Step 3: Implement controller and lazy AppKit view**

```python
class AppController:
    def __init__(self, service, view):
        self.service = service
        self.view = view

    def application_did_finish_launching(self):
        self.service.start()

    def poll(self):
        for state in self.service.drain_events():
            self.render(state)

    def application_should_terminate(self):
        self.service.stop()
        self.service.join(timeout=5.0)


def main() -> int:
    from AppKit import NSApplication
    app = NSApplication.sharedApplication()
    service = AppService()
    view = AppKitView(controller=None)
    controller = AppController(service, view)
    view.controller = controller
    view.build_window(title="问道前台助手")
    controller.application_did_finish_launching()
    app.run()
    return 0
```

```python
# scripts/wendao_app.py
from wendao_bot.app import main

if __name__ == "__main__":
    raise SystemExit(main())
```

Create status labels and the seven approved controls programmatically. Button callbacks call only `AppController` methods. Use `NSTimer` on the main run loop to call `poll`; the worker must never mutate AppKit objects.

- [ ] **Step 4: Register a GUI script**

```toml
[project.gui-scripts]
wendao-app = "wendao_bot.app:main"

[project.optional-dependencies]
app = ["pyinstaller>=6.10,<7"]
```

Keep the existing `wendao-bot` console script unchanged.

- [ ] **Step 5: Test import behavior without AppKit loaded**

```python
def test_app_module_import_is_lazy(monkeypatch):
    monkeypatch.setitem(sys.modules, "AppKit", None)
    module = importlib.reload(wendao_bot.app)
    assert callable(module.main)
```

- [ ] **Step 6: Run tests and compile**

Run: `.venv/bin/python -m pytest -q tests/test_app.py && .venv/bin/python -m compileall -q src`

Expected: PASS and exit 0.

- [ ] **Step 7: Commit**

```bash
git add src/wendao_bot/app.py tests/test_app.py pyproject.toml
git commit -m "feat: add native macos control window"
```

### Task 5: Package a standalone `.app` and ZIP

**Files:**
- Create: `packaging/wendao_app.spec`
- Create: `scripts/build_app.sh`
- Create: `tests/test_packaging.py`
- Modify: `.gitignore`

- [ ] **Step 1: Write failing packaging-definition tests**

```python
def test_spec_collects_required_resources():
    text = Path("packaging/wendao_app.spec").read_text("utf-8")
    assert "wendao_bot/default.yaml" in text
    assert "wendao_bot/templates" in text
    assert "问道前台助手" in text


def test_build_script_creates_app_and_zip_contract():
    text = Path("scripts/build_app.sh").read_text("utf-8")
    assert "dist/问道前台助手.app" in text
    assert "ditto -c -k --sequesterRsrc --keepParent" in text
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/bin/python -m pytest -q tests/test_packaging.py`

Expected: FAIL because the spec and build script do not exist.

- [ ] **Step 3: Add deterministic PyInstaller configuration**

```python
# packaging/wendao_app.spec
from PyInstaller.utils.hooks import collect_submodules

datas = [
    ("src/wendao_bot/default.yaml", "wendao_bot"),
    ("src/wendao_bot/templates", "wendao_bot/templates"),
]
hiddenimports = (
    collect_submodules("Quartz")
    + collect_submodules("Vision")
    + collect_submodules("AppKit")
    + ["cv2", "PIL.Image", "yaml"]
)
a = Analysis(["scripts/wendao_app.py"], pathex=["src"], datas=datas, hiddenimports=hiddenimports)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="问道前台助手", console=False)
coll = COLLECT(exe, a.binaries, a.datas, name="问道前台助手")
app = BUNDLE(coll, name="问道前台助手.app", bundle_identifier="local.wendao.foreground-helper")
```

Pin PyInstaller to `>=6.10,<7`, use its documented `Analysis`, `EXE`, `COLLECT`, and `BUNDLE` arguments, and set `target_arch=None` so the artifact matches the build machine architecture. Assert the resulting `Info.plist` contains bundle identifier `local.wendao.foreground-helper`.

- [ ] **Step 4: Add repeatable build script**

```bash
#!/bin/zsh
set -euo pipefail
project_root="${0:A:h:h}"
cd "$project_root"
.venv/bin/python -m PyInstaller --noconfirm --clean packaging/wendao_app.spec
test -d "dist/问道前台助手.app"
rm -f "dist/问道前台助手.zip"
ditto -c -k --sequesterRsrc --keepParent \
  "dist/问道前台助手.app" "dist/问道前台助手.zip"
```

Add `/dist/`, `/build/`, and `/*.spec.bak` to `.gitignore`; keep the canonical spec tracked.

- [ ] **Step 5: Install build extra and run packaging tests**

Run: `.venv/bin/python -m pip install -e '.[app,test]'`

Expected: PyInstaller 6.x installs successfully.

Run: `.venv/bin/python -m pytest -q tests/test_packaging.py`

Expected: PASS.

- [ ] **Step 6: Build and inspect the app without launching automation**

Run: `zsh scripts/build_app.sh`

Expected: exit 0; both `dist/问道前台助手.app` and `dist/问道前台助手.zip` exist.

Run: `find 'dist/问道前台助手.app/Contents' -maxdepth 3 -type f | sort | rg 'default.yaml|Info.plist|问道前台助手'`

Expected: bundle executable, plist, and packaged default configuration are present.

- [ ] **Step 7: Commit**

```bash
git add packaging/wendao_app.spec scripts/build_app.sh tests/test_packaging.py .gitignore
git commit -m "build: package standalone macos app"
```

### Task 6: Add configuration selection, permission/readiness display, and notification preview

**Files:**
- Modify: `src/wendao_bot/app.py`
- Modify: `src/wendao_bot/app_service.py`
- Modify: `src/wendao_bot/app_model.py`
- Test: `tests/test_app.py`
- Test: `tests/test_app_service.py`

- [ ] **Step 1: Write failing behavior tests**

```python
def test_dimension_mismatch_disables_live_controls():
    state = AppViewState.error("window geometry mismatch")
    assert state.can_single_step is False
    assert state.can_run_continuous is False


def test_config_selection_restarts_observe_only(tmp_path):
    service = FakeService()
    controller = AppController(service, FakeView(config_path=tmp_path / "local.yaml"))
    controller.choose_config()
    assert service.calls == [("set_config", tmp_path / "local.yaml"), "start"]


def test_notification_preview_never_sends():
    notifier = RecordingNotifier()
    preview = build_notification_preview(
        config_path=None,
        notifier_factory=lambda _recipient, *, dry_run: notifier.record(dry_run),
    )
    assert notifier.requested_dry_run is True
    assert preview.recipient == "wendao-owner@example.com"
    assert "@icloud.com" not in preview.body
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/bin/python -m pytest -q tests/test_app.py tests/test_app_service.py`

Expected: FAIL for missing configuration and preview APIs.

- [ ] **Step 3: Implement fail-closed readiness presentation**

Map known exception classes to short local UI messages without embedding exception strings. Show separate booleans for window match, OCR availability, template availability, and observe readiness. Any false/unknown readiness value disables single-step and continuous controls.

Use `NSOpenPanel` restricted to YAML files for configuration selection. Validate with `load_config()` before replacing the active configuration. On success stop/join the old worker and restart in observe-only mode; on failure remain stopped and show `ConfigError` without its raw YAML contents.

- [ ] **Step 4: Implement notification preview only**

```python
def build_notification_preview(config_path):
    config = load_config(config_path)
    notice = PauseNotice("notification test", None, None, "none", "manual")
    body = IMessageNotifier(config.imessage_recipient, dry_run=True).send(notice)
    return NotificationPreview(recipient=config.imessage_recipient, body=body)
```

The GUI displays recipient and body in a confirmation sheet but has no real-send button in this version.

- [ ] **Step 5: Run focused and full tests**

Run: `.venv/bin/python -m pytest -q tests/test_app.py tests/test_app_service.py tests/test_notifier.py`

Expected: PASS.

Run: `.venv/bin/python -m pytest -q`

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/wendao_bot/app.py src/wendao_bot/app_service.py src/wendao_bot/app_model.py tests/test_app.py tests/test_app_service.py
git commit -m "feat: add app readiness and config controls"
```

### Task 7: Document, verify, and produce the handoff artifact

**Files:**
- Modify: `README.md`
- Modify: `tests/test_end_to_end.py`

- [ ] **Step 1: Add a headless app-flow integration test**

```python
class ScriptedRunnerFactory:
    def __init__(self, *, ready_observations, single_step_exit):
        self.ready_observations = ready_observations
        self.single_step_exit = single_step_exit
        self.calls = []

    def __call__(self, _config, _runtime, *, observe_only, stop_event):
        runner = ScriptedRunner(observe_only, stop_event, self.ready_observations)
        self.calls.append(runner)
        return runner


def test_app_observe_single_step_then_continuous_unlock(tmp_path):
    factory = ScriptedRunnerFactory(ready_observations=3, single_step_exit=0)
    service = AppService(runner_factory=factory, runtime=tmp_path)
    service.start()
    service.start_single_step()
    service.join(timeout=1)
    assert service.current_state.can_run_continuous is True
    service.start_continuous()
    service.stop()
    assert all(call.stop_event is service.stop_event for call in factory.calls)
```

- [ ] **Step 2: Run the integration test and verify RED if any wiring is absent**

Run: `.venv/bin/python -m pytest -q tests/test_end_to_end.py::test_app_observe_single_step_then_continuous_unlock`

Expected before final wiring: FAIL; after connecting the service/model/controller contracts: PASS.

- [ ] **Step 3: Update the README with exact user workflow**

Document:

```text
zsh scripts/build_app.sh
open "dist/问道前台助手.app"
```

Explain Gatekeeper handling for the unsigned local build, Screen Recording and Accessibility permissions, default observe-only startup, 919×674 configuration/template requirement, single-step unlock, emergency stop, runtime screenshot privacy, and the fact that real iMessage sending is not exposed by the app.

- [ ] **Step 4: Run fresh final verification**

Run: `.venv/bin/python -m pytest -q`

Expected: all tests PASS with zero failures.

Run: `.venv/bin/python -m compileall -q src scripts && git diff --check`

Expected: exit 0 with no output.

Run: `zsh scripts/build_app.sh && test -d 'dist/问道前台助手.app' && test -f 'dist/问道前台助手.zip'`

Expected: exit 0.

- [ ] **Step 5: Perform a launch-only smoke test**

Run: `open -n 'dist/问道前台助手.app'`

Expected: the control window opens in observe-only mode. Do not press single-step or continuous run. Verify the app displays a controlled permission/window/template error rather than clicking if live prerequisites are absent, then quit normally.

- [ ] **Step 6: Commit documentation and final test**

```bash
git add README.md tests/test_end_to_end.py
git commit -m "docs: add macos app build and launch guide"
```

## Final acceptance boundary

The implementation is code-complete only when tests, compilation, bundle construction, resource inspection, and launch-only smoke testing pass. It is not declared safe for continuous gameplay until the user separately completes macOS permissions, captures/reviews real positive templates for the configured 919×674 client, observes for 30 seconds, and supervises one successful single-step click.

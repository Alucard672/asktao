from dataclasses import FrozenInstanceError, asdict
from pathlib import Path

import pytest

from wendao_bot.app_model import AppViewState
from wendao_bot.models import ScreenSnapshot, ScreenState
from wendao_bot.orchestrator import StepResult


PRIVATE_TEXT = "private OCR alice@example.com recipient@example.com"
SCREEN_ROOT = Path("/tmp/screens")


def step_result(
    *,
    state: ScreenState = ScreenState.MAP,
    confidence: float = 0.95,
    targets=None,
    evidence=frozenset({"ocr", "template"}),
    status: str = "observing",
) -> StepResult:
    if targets is None:
        targets = {"main_quest": (123, 456)}
    snapshot = ScreenSnapshot(
        state=state,
        confidence=confidence,
        text=PRIVATE_TEXT,
        image_path="/tmp/screens/capture-000001.png",
        targets=targets,
        evidence=evidence,
    )
    return StepResult(snapshot, snapshot, "click", "main_quest", status)


def app_from_step(step, **kwargs) -> AppViewState:
    return AppViewState.from_step(step, screenshot_root=SCREEN_ROOT, **kwargs)


def test_initial_state_is_locked_and_contains_no_observation() -> None:
    state = AppViewState.initial()

    assert state.mode == "observe"
    assert state.status == "idle"
    assert state.screen_state == "unknown"
    assert state.confidence == 0.0
    assert state.target_names == ()
    assert state.screenshot_path is None
    assert state.pause_reason is None
    assert not state.can_single_step
    assert not state.can_run_continuous


def test_stopped_state_is_sanitized_locked_and_observable_again() -> None:
    state = AppViewState.stopped()

    assert state.mode == "observe"
    assert state.status == "stopped"
    assert state.screen_state == "unknown"
    assert state.pause_reason is None
    assert not state.can_single_step
    assert not state.can_run_continuous


def test_from_step_retains_only_sanitized_display_data() -> None:
    state = app_from_step(step_result())
    rendered = f"{state!r} {asdict(state)!r}"

    assert state.screen_state == "map"
    assert state.screenshot_path == "/tmp/screens/capture-000001.png"
    assert PRIVATE_TEXT not in rendered
    assert "alice@example.com" not in rendered
    assert "recipient@example.com" not in rendered
    assert "123" not in rendered
    assert "456" not in rendered
    assert "frozenset" not in rendered


@pytest.mark.parametrize(
    ("state", "confidence", "targets", "evidence"),
    [
        (ScreenState.UNKNOWN, 0.99, {"main_quest": (1, 2)}, {"ocr", "template"}),
        (ScreenState.MAP, 0.87, {"main_quest": (1, 2)}, {"ocr", "template"}),
        (ScreenState.MAP, 0.99, {}, {"ocr", "template"}),
        (ScreenState.MAP, 0.99, {"main_quest": (1, 2)}, {"ocr"}),
        (ScreenState.MAP, 0.99, {"main_quest": (1, 2)}, {"template"}),
    ],
)
def test_single_step_remains_locked_without_every_readiness_condition(
    state, confidence, targets, evidence
) -> None:
    model = app_from_step(
        step_result(
            state=state,
            confidence=confidence,
            targets=targets,
            evidence=frozenset(evidence),
        ),
        min_confidence=0.88,
    )

    assert not model.can_single_step
    assert not model.can_run_continuous


def test_actionable_step_at_threshold_with_both_evidence_types_is_ready() -> None:
    state = app_from_step(
        step_result(confidence=0.88), min_confidence=0.88
    )

    assert state.recognition_ready
    assert state.can_single_step


def test_accessibility_gate_locks_live_modes_without_locking_observe_readiness():
    state = app_from_step(step_result(), accessibility_trusted=False)

    assert state.observe_ready
    assert state.accessibility_trusted is False
    assert not state.can_single_step
    assert not state.can_run_continuous
    assert not state.can_run_continuous


def test_readiness_dimensions_are_separate_and_fail_closed() -> None:
    state = app_from_step(step_result(evidence=frozenset({"ocr"})))

    assert state.window_match is True
    assert state.ocr_available is True
    assert state.template_available is False
    assert state.observe_ready is False
    assert not state.can_single_step
    assert not state.can_run_continuous


def test_bounded_geometry_failure_never_retains_exception_text() -> None:
    state = AppViewState.readiness_error(
        "geometry_mismatch", mode="observe"
    )

    assert state.window_match is False
    assert state.readiness_failure == "geometry_mismatch"
    assert state.observe_ready is False
    assert "exception" not in repr(state).lower()


@pytest.mark.parametrize("status", ["idle", "error", "stopped", "paused"])
def test_hostile_direct_state_cannot_enable_actions_in_unsafe_status(status) -> None:
    state = AppViewState(
        mode="continuous",
        status=status,
        screen_state="map",
        confidence=1.0,
        target_names=("main_quest",),
        screenshot_path="/tmp/screens/capture-000001.png",
        pause_reason=None,
        recognition_ready=True,
        single_step_verified=True,
        can_single_step=True,
        can_run_continuous=True,
        window_match=True,
        ocr_available=True,
        template_available=True,
        observe_ready=True,
        trusted_screenshot_root=SCREEN_ROOT,
    )

    assert not state.can_single_step
    assert not state.can_run_continuous


def test_hostile_direct_state_with_failure_reason_cannot_enable_actions() -> None:
    state = AppViewState(
        mode="continuous",
        status="running",
        screen_state="map",
        confidence=1.0,
        target_names=("main_quest",),
        screenshot_path="/tmp/screens/capture-000001.png",
        pause_reason=None,
        recognition_ready=True,
        single_step_verified=True,
        can_single_step=True,
        can_run_continuous=True,
        window_match=True,
        ocr_available=True,
        template_available=True,
        observe_ready=True,
        readiness_failure="geometry_mismatch",
        trusted_screenshot_root=SCREEN_ROOT,
    )

    assert not state.can_single_step
    assert not state.can_run_continuous


@pytest.mark.parametrize(
    "failure", ["secret@example.com raw failure", object(), 42]
)
def test_unrecognized_failure_is_bounded_and_locks_actions(failure) -> None:
    state = AppViewState(
        mode="continuous",
        status="running",
        screen_state="map",
        confidence=1.0,
        target_names=("main_quest",),
        screenshot_path="/tmp/screens/capture-000001.png",
        pause_reason=None,
        recognition_ready=True,
        single_step_verified=True,
        can_single_step=True,
        can_run_continuous=True,
        window_match=True,
        ocr_available=True,
        template_available=True,
        observe_ready=True,
        readiness_failure=failure,
        trusted_screenshot_root=SCREEN_ROOT,
    )

    assert state.readiness_failure == "unknown_failure"
    assert not state.can_single_step
    assert not state.can_run_continuous
    assert "secret@example.com" not in repr(state)


def test_target_names_are_sorted_and_coordinates_are_discarded() -> None:
    state = app_from_step(
        step_result(targets={"main_quest": (9, 9), "claim": (3, 4)})
    )

    assert state.target_names == ("claim", "main_quest")
    assert state.targets == ("claim", "main_quest")
    assert state.screenshot == "/tmp/screens/capture-000001.png"


def test_model_is_immutable() -> None:
    state = AppViewState.initial()

    with pytest.raises(FrozenInstanceError):
        state.status = "running"


def test_verified_single_step_unlocks_continuous_without_mutating_original() -> None:
    state = app_from_step(step_result())
    unlocked = state.with_single_step_success()

    assert not state.can_run_continuous
    assert unlocked.can_single_step
    assert unlocked.can_run_continuous
    assert unlocked.single_step_verified


def test_verified_flag_does_not_unlock_continuous_when_observation_is_not_ready() -> None:
    state = app_from_step(
        step_result(confidence=0.1), single_step_verified=True
    )

    assert not state.can_single_step
    assert not state.can_run_continuous


def test_malformed_sanitized_event_fails_closed() -> None:
    class MalformedStep:
        observed = step_result().observed

        def sanitized_event(self):
            return {
                "state": "map",
                "confidence": float("nan"),
                "targets": ["main_quest"],
                "status": "running",
                "screenshot": "/tmp/screens/capture-000001.png",
            }

    state = app_from_step(MalformedStep())

    assert state.status == "error"
    assert state.confidence == 0.0
    assert not state.can_single_step
    assert not state.can_run_continuous


def test_unrecognized_status_is_not_retained() -> None:
    private_status = "subprocess failed for alice@example.com"

    class MalformedStep:
        observed = step_result().observed

        def sanitized_event(self):
            event = step_result().sanitized_event()
            event["status"] = private_status
            return event

    state = app_from_step(MalformedStep())

    assert state.status == "error"
    assert private_status not in repr(state)


def test_non_boolean_verification_flag_fails_closed() -> None:
    state = app_from_step(step_result(), single_step_verified="yes")

    assert state.status == "error"
    assert not state.can_run_continuous


def test_error_redacts_raw_exception_details() -> None:
    state = AppViewState.error(
        RuntimeError("subprocess failed for alice@example.com --token secret")
    )
    rendered = f"{state!r} {asdict(state)!r}"

    assert state.status == "error"
    assert state.pause_reason == "internal error"
    assert "alice@example.com" not in rendered
    assert "secret" not in rendered
    assert "subprocess" not in rendered


@pytest.mark.parametrize("threshold", [True, -0.1, 1.1, float("nan"), "0.88"])
def test_invalid_threshold_fails_closed(threshold) -> None:
    state = app_from_step(step_result(), min_confidence=threshold)

    assert state.status == "error"
    assert not state.can_single_step


@pytest.mark.parametrize(
    "factory",
    [
        lambda mode: AppViewState.initial(mode=mode),
        lambda mode: app_from_step(step_result(), mode=mode),
        lambda mode: AppViewState.error(mode=mode),
    ],
)
def test_malicious_mode_is_never_retained(factory) -> None:
    private_mode = "alice@example.com --token secret"

    state = factory(private_mode)

    assert state.mode == "observe"
    assert private_mode not in repr(state)


def test_direct_construction_cannot_unlock_unrecognized_state() -> None:
    state = AppViewState(
        mode="continuous",
        status="running",
        screen_state="unknown",
        confidence=0.99,
        target_names=("main_quest",),
        screenshot_path="/tmp/screens/capture-000001.png",
        pause_reason=None,
        recognition_ready=True,
        single_step_verified=True,
        can_single_step=True,
        can_run_continuous=True,
        trusted_screenshot_root=SCREEN_ROOT,
    )

    assert not state.can_single_step
    assert not state.can_run_continuous
    assert not state.single_step_verified
    assert state.with_single_step_success() is state


def test_direct_construction_cannot_unlock_low_confidence_state() -> None:
    state = AppViewState(
        mode="continuous",
        status="running",
        screen_state="map",
        confidence=0.2,
        target_names=("main_quest",),
        screenshot_path="/tmp/screens/capture-000001.png",
        pause_reason=None,
        recognition_ready=True,
        single_step_verified=True,
        can_single_step=True,
        can_run_continuous=True,
        trusted_screenshot_root=SCREEN_ROOT,
    )

    assert not state.can_single_step
    assert not state.can_run_continuous
    assert not state.single_step_verified


def test_direct_construction_cannot_unlock_without_readiness_inputs() -> None:
    state = AppViewState(
        mode="continuous",
        status="running",
        screen_state="map",
        confidence=0.99,
        target_names=(),
        screenshot_path="/tmp/screens/capture-000001.png",
        pause_reason=None,
        recognition_ready=False,
        single_step_verified=True,
        can_single_step=True,
        can_run_continuous=True,
        trusted_screenshot_root=SCREEN_ROOT,
    )

    assert not state.can_single_step
    assert not state.can_run_continuous
    assert not state.single_step_verified


def test_direct_construction_redacts_malicious_mode() -> None:
    private_mode = "chat from alice@example.com"
    state = AppViewState(
        mode=private_mode,
        status="idle",
        screen_state="unknown",
        confidence=0.0,
        target_names=(),
        screenshot_path=None,
        pause_reason=None,
        recognition_ready=False,
        single_step_verified=False,
        can_single_step=False,
        can_run_continuous=False,
    )

    assert state.mode == "observe"
    assert private_mode not in repr(state)


@pytest.mark.parametrize(
    "unsafe_target",
    [
        "alice@example.com",
        "subprocess failed --token secret",
        "../../private",
        "daily_除暴\nrecipient@example.com",
    ],
)
def test_forged_event_never_retains_unsafe_target_names(unsafe_target) -> None:
    class ForgedStep:
        observed = step_result().observed

        def sanitized_event(self):
            event = step_result().sanitized_event()
            event["targets"] = ["main_quest", unsafe_target]
            return event

    state = app_from_step(ForgedStep())

    assert state.status == "error"
    assert state.target_names == ()
    assert unsafe_target not in repr(state)
    assert not state.can_single_step


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "relative/screen.png",
        "/tmp/alice@example.com.png",
        "/tmp/subprocess failed --token secret.png",
        "/tmp/subprocess_failed_--token_secret.png",
        "/tmp/screens/private.txt",
        "/tmp/screens/../private.png",
        "/tmp/screens/bad\nrecipient.png",
    ],
)
def test_forged_event_never_retains_unsafe_screenshot_path(unsafe_path) -> None:
    class ForgedStep:
        observed = step_result().observed

        def sanitized_event(self):
            event = step_result().sanitized_event()
            event["screenshot"] = unsafe_path
            return event

    state = app_from_step(ForgedStep())

    assert state.status == "error"
    assert state.screenshot_path is None
    assert unsafe_path not in repr(state)
    assert not state.can_single_step


def test_direct_construction_discards_unsafe_target_and_path_and_locks() -> None:
    private_target = "alice@example.com"
    private_path = "/tmp/subprocess failed --token secret.png"
    state = AppViewState(
        mode="continuous",
        status="running",
        screen_state="map",
        confidence=0.99,
        target_names=(private_target,),
        screenshot_path=private_path,
        pause_reason=None,
        recognition_ready=True,
        single_step_verified=True,
        can_single_step=True,
        can_run_continuous=True,
    )

    assert state.target_names == ()
    assert state.screenshot_path is None
    assert private_target not in repr(state)
    assert private_path not in repr(state)
    assert not state.can_single_step
    assert not state.can_run_continuous


def test_daily_chinese_target_schema_is_preserved() -> None:
    state = app_from_step(
        step_result(targets={"daily_除暴": (1, 2), "daily_task_押镖": (3, 4)}),
        allowed_target_names=("除暴", "押镖"),
    )

    assert state.target_names == ("daily_task_押镖", "daily_除暴")
    assert state.can_single_step


def test_configured_ascii_daily_target_is_preserved_and_unlockable() -> None:
    state = app_from_step(
        step_result(targets={"daily_shimen": (1, 2)}),
        allowed_target_names=("shimen",),
    )

    assert state.targets == ("daily_shimen",)
    assert state.can_single_step
    assert state.with_single_step_success().can_run_continuous


def test_unconfigured_daily_target_fails_closed() -> None:
    state = app_from_step(step_result(targets={"daily_shimen": (1, 2)}))

    assert state.status == "error"
    assert state.targets == ()
    assert not state.can_single_step


@pytest.mark.parametrize(
    "unsafe_name",
    ["alice@example.com", "raw subprocess error", "../private", "bad\nname"],
)
def test_unsafe_configured_daily_name_is_never_authorized(unsafe_name) -> None:
    state = app_from_step(
        step_result(targets={f"daily_{unsafe_name}": (1, 2)}),
        allowed_target_names=(unsafe_name,),
    )

    assert state.status == "error"
    assert state.targets == ()
    assert unsafe_name not in repr(state)


def test_production_default_screenshot_path_with_parent_spaces_is_preserved() -> None:
    production_path = (
        "/Users/test/Library/Application Support/WendaoBot/screens/"
        "capture-000001.png"
    )
    step = step_result()

    class ProductionStep:
        observed = step.observed

        def sanitized_event(self):
            event = step.sanitized_event()
            event["screenshot"] = production_path
            return event

    state = AppViewState.from_step(
        ProductionStep(),
        screenshot_root=Path(
            "/Users/test/Library/Application Support/WendaoBot/screens"
        ),
    )

    assert state.screenshot_path == production_path
    assert state.can_single_step


def test_from_step_without_trusted_root_discards_screenshot_and_locks() -> None:
    state = AppViewState.from_step(step_result())

    assert state.screenshot_path is None
    assert not state.can_single_step
    assert not state.can_run_continuous


def test_capture_from_untrusted_parent_is_not_retained() -> None:
    private_path = "/tmp/raw subprocess output/capture-000001.png"
    step = step_result()

    class ForgedStep:
        observed = step.observed

        def sanitized_event(self):
            event = step.sanitized_event()
            event["screenshot"] = private_path
            return event

    state = app_from_step(ForgedStep())

    assert state.status == "error"
    assert state.screenshot_path is None
    assert private_path not in repr(state)
    assert not state.can_single_step

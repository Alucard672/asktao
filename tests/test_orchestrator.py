import threading
from datetime import datetime, timezone

import pytest

from wendao_bot.models import ScreenSnapshot, ScreenState
from wendao_bot.orchestrator import Orchestrator, RunStatus


def snap(state, *, confidence=0.99, targets=None, image="ignored.png", text=""):
    return ScreenSnapshot(state, confidence, text, image, targets or {})


def test_unknown_screen_pauses_without_clicking_and_notifies(fake_dependencies):
    fake_dependencies.recognizer.snapshots = [snap(ScreenState.UNKNOWN, confidence=0.2)]
    runner = Orchestrator(**fake_dependencies.as_kwargs())
    runner.step()
    assert runner.status is RunStatus.PAUSED
    assert fake_dependencies.session.clicks == []
    assert len(fake_dependencies.notifier.messages) == 1


def test_dialogue_skip_clicks_once_then_reobserves_saved_path(fake_dependencies):
    fake_dependencies.recognizer.snapshots = [
        snap(ScreenState.DIALOGUE, targets={"skip_dialogue": (790, 76)}),
        snap(ScreenState.MAP),
    ]
    runner = Orchestrator(**fake_dependencies.as_kwargs(), sleeper=lambda _: None)
    runner.step()
    assert fake_dependencies.session.clicks == [(790, 76)]
    assert len(fake_dependencies.recognizer.paths) == 2
    assert all(
        path.parent == fake_dependencies.store.screens
        for path in fake_dependencies.recognizer.paths
    )


def test_step_emits_and_persists_sanitized_observation(fake_dependencies):
    fake_dependencies.recognizer.snapshots = [
        ScreenSnapshot(
            ScreenState.DIALOGUE, 0.99,
            "private OCR chat recipient@example.test",
            "ignored.png", {"skip_dialogue": (790, 76)},
            frozenset({"ocr", "template"}),
        ),
        snap(ScreenState.MAP),
    ]
    result = Orchestrator(
        **fake_dependencies.as_kwargs(), sleeper=lambda _: None
    ).step()
    event = fake_dependencies.store.events[-1]
    assert result.action_kind == "click"
    assert result.action_target == "skip_dialogue"
    assert event["state"] == "dialogue"
    assert event["confidence"] == 0.99
    assert event["targets"] == ["skip_dialogue"]
    assert "private OCR" not in repr(event)
    assert "recipient@example.test" not in repr(event)


def test_alternate_map_successor_is_accepted_without_second_click(fake_dependencies):
    fake_dependencies.recognizer.snapshots = [
        snap(ScreenState.NPC_OPTIONS, targets={"npc_main_option": (2, 3)}),
        snap(ScreenState.MAP), snap(ScreenState.MAP), snap(ScreenState.MAP),
    ]
    runner = Orchestrator(**fake_dependencies.as_kwargs(), sleeper=lambda _: None)
    runner.step()
    assert runner.status is RunStatus.RUNNING
    assert fake_dependencies.session.clicks == [(2, 3)]


def test_blocked_state_pauses_before_any_input(fake_dependencies):
    fake_dependencies.recognizer.snapshots = [snap(ScreenState.PAYMENT)]
    runner = Orchestrator(**fake_dependencies.as_kwargs())
    runner.step()
    assert runner.status is RunStatus.PAUSED
    assert fake_dependencies.session.clicks == []


def test_observe_only_never_clicks(fake_dependencies):
    fake_dependencies.recognizer.snapshots = [
        snap(ScreenState.MAP, targets={"main_quest": (7, 8)})
    ]
    runner = Orchestrator(**fake_dependencies.as_kwargs(), observe_only=True)
    runner.step()
    assert runner.status is RunStatus.OBSERVING
    assert fake_dependencies.session.clicks == []


def test_observe_only_preflight_rejects_blocked_screen(fake_dependencies):
    fake_dependencies.recognizer.snapshots = [snap(ScreenState.CAPTCHA)]
    runner = Orchestrator(**fake_dependencies.as_kwargs(), observe_only=True)
    runner.step()
    assert runner.status is RunStatus.PAUSED
    assert fake_dependencies.session.clicks == []


def test_capture_failure_is_contained_as_pause(fake_dependencies):
    def fail_capture():
        raise RuntimeError("private backend details")

    fake_dependencies.session.capture = fail_capture
    runner = Orchestrator(**fake_dependencies.as_kwargs())
    runner.step()
    assert runner.status is RunStatus.PAUSED
    assert "RuntimeError" in fake_dependencies.store.states[-1]["reason"]
    assert "private backend details" not in fake_dependencies.store.states[-1]["reason"]


def test_notifier_failure_is_sanitized_and_runner_stays_paused(fake_dependencies):
    fake_dependencies.notifier.error = RuntimeError("secret contact detail")
    fake_dependencies.recognizer.snapshots = [snap(ScreenState.UNKNOWN, confidence=0.1)]
    runner = Orchestrator(**fake_dependencies.as_kwargs())
    runner.step()
    assert runner.status is RunStatus.PAUSED
    assert {
        "event": "notification_failed", "error": "RuntimeError"
    } in fake_dependencies.store.events


def test_pause_and_stop_control_files_are_honored(fake_dependencies):
    control = fake_dependencies.store.root / "control"
    control.mkdir()
    (control / "pause").touch()
    fake_dependencies.recognizer.snapshots = [snap(ScreenState.MAP)]
    runner = Orchestrator(**fake_dependencies.as_kwargs(), control_dir=control)
    runner.step()
    assert runner.status is RunStatus.PAUSED
    (control / "stop").touch()
    runner.step()
    assert runner.status is RunStatus.STOPPED


def test_wait_timeout_pauses_without_clicking(fake_dependencies):
    fake_dependencies.recognizer.snapshots = [snap(ScreenState.BATTLE)] * 4
    times = iter((0.0, 1.0, 121.0, 122.0))
    runner = Orchestrator(
        **fake_dependencies.as_kwargs(),
        clock=lambda: next(times),
        sleeper=lambda _: None,
        battle_timeout_seconds=120,
    )
    runner.step()
    runner.step()
    assert runner.status is RunStatus.PAUSED
    assert fake_dependencies.session.clicks == []


def test_wait_reobserves_after_stability_delay(fake_dependencies):
    fake_dependencies.recognizer.snapshots = [
        snap(ScreenState.AUTO_PATH), snap(ScreenState.MAP)
    ]
    sleeps = []
    runner = Orchestrator(
        **fake_dependencies.as_kwargs(), clock=lambda: 0.0, sleeper=sleeps.append,
        stable_wait_seconds=0.25,
    )
    result = runner.step()
    assert result.state is ScreenState.MAP
    assert sleeps == [0.25]
    assert fake_dependencies.session.clicks == []


def test_stop_event_set_by_planner_prevents_click(fake_dependencies):
    stop = threading.Event()
    original = fake_dependencies.planner.next_action

    def plan(snapshot, progress):
        action = original(snapshot, progress)
        stop.set()
        return action

    fake_dependencies.planner.next_action = plan
    fake_dependencies.recognizer.snapshots = [
        snap(ScreenState.MAP, targets={"main_quest": (7, 8)})
    ]
    runner = Orchestrator(**fake_dependencies.as_kwargs(), stop_event=stop)
    runner.step()
    assert runner.status is RunStatus.STOPPED
    assert fake_dependencies.session.clicks == []


def test_stop_event_set_by_recognizer_prevents_planning_or_click(fake_dependencies):
    stop = threading.Event()
    classify = fake_dependencies.recognizer.classify

    def recognize(path):
        result = classify(path)
        stop.set()
        return result

    fake_dependencies.recognizer.classify = recognize
    fake_dependencies.recognizer.snapshots = [
        snap(ScreenState.MAP, targets={"main_quest": (7, 8)})
    ]
    runner = Orchestrator(**fake_dependencies.as_kwargs(), stop_event=stop)
    runner.step()
    assert runner.status is RunStatus.STOPPED
    assert fake_dependencies.session.clicks == []


def test_visible_progress_is_updated_and_persisted(fake_dependencies):
    from wendao_bot.tasks import ProgressExtractor

    fake_dependencies.recognizer.snapshots = [
        ScreenSnapshot(
            ScreenState.MAP, 0.99, "等级15 主线 20级开启 师门 2/10",
            "ignored.png", {}, frozenset({"ocr"}),
        )
    ]
    runner = Orchestrator(
        **fake_dependencies.as_kwargs(),
        progress_extractor=ProgressExtractor(),
        observe_only=True,
    )
    runner.step()
    progress = fake_dependencies.store.states[0]["progress"]
    assert progress["level"] == 15
    assert progress["main_blocked_by_level"] is True
    assert progress["shimen_completed"] == 2


@pytest.mark.parametrize(
    "snapshot",
    [
        ScreenSnapshot(
            ScreenState.UNKNOWN, 0.99, "等级99 师门 10/10", "ignored.png", {},
            frozenset({"ocr"}),
        ),
        ScreenSnapshot(
            ScreenState.PAYMENT, 0.99, "等级99 师门 10/10", "ignored.png", {},
            frozenset({"ocr"}),
        ),
        ScreenSnapshot(
            ScreenState.MAP, 0.50, "等级99 师门 10/10", "ignored.png", {},
            frozenset({"ocr"}),
        ),
        ScreenSnapshot(
            ScreenState.MAP, 0.99, "等级99 师门 10/10", "ignored.png", {},
            frozenset({"template"}),
        ),
    ],
)
def test_untrusted_snapshot_cannot_mutate_progress(fake_dependencies, snapshot):
    from wendao_bot.tasks import ProgressExtractor

    fake_dependencies.recognizer.snapshots = [snapshot]
    runner = Orchestrator(
        **fake_dependencies.as_kwargs(), progress_extractor=ProgressExtractor(),
        progress_min_confidence=0.88,
    )
    runner.step()
    assert runner.progress.level == 20
    assert runner.progress.shimen_completed == 0


def test_post_click_allows_intermediate_state_before_expected(fake_dependencies):
    fake_dependencies.recognizer.snapshots = [
        snap(ScreenState.NPC_OPTIONS, targets={"npc_main_option": (2, 3)}),
        snap(ScreenState.DIALOGUE),
        snap(ScreenState.AUTO_PATH),
    ]
    runner = Orchestrator(**fake_dependencies.as_kwargs(), sleeper=lambda _: None)
    result = runner.step()
    assert result.state is ScreenState.DIALOGUE
    assert runner.status is RunStatus.RUNNING
    assert fake_dependencies.session.clicks == [(2, 3)]


def test_forbidden_post_click_state_pauses_immediately(fake_dependencies):
    fake_dependencies.recognizer.snapshots = [
        snap(ScreenState.MAP, targets={"main_quest": (2, 3)}),
        snap(ScreenState.PAYMENT),
    ]
    runner = Orchestrator(**fake_dependencies.as_kwargs(), sleeper=lambda _: None)
    runner.step()
    assert runner.status is RunStatus.PAUSED
    assert fake_dependencies.session.clicks == [(2, 3)]
    assert "post-click rejected payment" in fake_dependencies.store.states[-1]["reason"]


def test_pause_notice_uses_local_timezone_timestamp(fake_dependencies, monkeypatch):
    class FixedDateTime:
        @staticmethod
        def now():
            return datetime(2026, 8, 14, 12, 30, tzinfo=timezone.utc)

    monkeypatch.setattr("wendao_bot.orchestrator.datetime", FixedDateTime)
    fake_dependencies.recognizer.snapshots = [snap(ScreenState.UNKNOWN, confidence=0.1)]
    Orchestrator(**fake_dependencies.as_kwargs()).step()
    expected = FixedDateTime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    assert fake_dependencies.notifier.messages[0].timestamp == expected

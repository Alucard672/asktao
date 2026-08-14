from wendao_bot.cli import main
from wendao_bot.models import ScreenSnapshot, ScreenState
from wendao_bot.orchestrator import Orchestrator, RunStatus
from wendao_bot.tasks import Progress, ProgressExtractor


def test_blocked_main_completes_ten_shimen_then_returns_to_main(offline_game):
    result = offline_game.play_blocked_main_sequence()

    assert result.targets[0] == "shimen"
    assert result.targets[1:10] == ["shimen_task"] * 9
    assert result.progress.shimen_completed == 10
    assert result.targets[-1] == "main_quest"
    assert result.forbidden_clicks == []


def test_orchestrator_dialogue_to_map_sequence_uses_only_safe_click(
    fake_dependencies,
):
    fake_dependencies.recognizer.snapshots = [
        ScreenSnapshot(
            ScreenState.DIALOGUE,
            0.99,
            "等级15",
            "ignored.png",
            {"skip_dialogue": (790, 76)},
            frozenset({"ocr", "template"}),
        ),
        ScreenSnapshot(
            ScreenState.MAP,
            0.99,
            "等级15 主线",
            "ignored.png",
            {"main_quest": (700, 190)},
            frozenset({"ocr", "template"}),
        ),
    ]
    runner = Orchestrator(
        **fake_dependencies.as_kwargs(),
        sleeper=lambda _seconds: None,
    )

    observed = runner.step()

    assert observed.state is ScreenState.MAP
    assert runner.status is RunStatus.RUNNING
    assert fake_dependencies.session.clicks == [(790, 76)]


def test_real_orchestrator_blocked_main_runs_shimen_ten_then_main(
    fake_dependencies,
):
    def trusted(state, text, targets=None):
        return ScreenSnapshot(
            state, 0.99, text, "ignored.png", targets or {},
            frozenset({"ocr", "template"}),
        )

    planning = [
        trusted(
            ScreenState.MAP, "等级15 主线 20级开启 师门 0/10",
            {"shimen": (600, 300)},
        )
    ]
    planning.extend(
        trusted(
            ScreenState.MAP, f"等级15 主线 20级开启 师门 {count}/10",
            {"shimen_task": (700, 245)},
        )
        for count in range(1, 10)
    )
    planning.append(
        trusted(
            ScreenState.MAP, "等级15 主线 师门 10/10",
            {"main_quest": (700, 190)},
        )
    )

    stream = []
    for index, frame in enumerate(planning):
        stream.extend((frame, trusted(ScreenState.AUTO_PATH, frame.text)))
        if index + 1 < len(planning):
            stream.extend((
                trusted(ScreenState.AUTO_PATH, frame.text), planning[index + 1]
            ))
    fake_dependencies.recognizer.snapshots = stream
    fake_dependencies.progress = Progress(None)
    runner = Orchestrator(
        **fake_dependencies.as_kwargs(),
        progress_extractor=ProgressExtractor(("师门", "除暴", "修行")),
        sleeper=lambda _: None,
    )

    for index in range(len(planning)):
        runner.step()
        if index + 1 < len(planning):
            runner.step()

    assert fake_dependencies.session.clicks == (
        [(600, 300)] + [(700, 245)] * 9 + [(700, 190)]
    )
    assert runner.progress == Progress(15, False, 10)
    assert fake_dependencies.store.states[-1]["progress"]["shimen_completed"] == 10
    assert fake_dependencies.notifier.messages == []
    assert {
        event.get("action_target")
        for event in fake_dependencies.store.events
        if event.get("action_target") is not None
    } <= {"shimen", "shimen_task", "main_quest"}


def test_notify_test_dry_run_never_invokes_osascript(monkeypatch, capsys):
    def forbidden_send(*_args, **_kwargs):
        raise AssertionError("dry-run attempted external communication")

    monkeypatch.setattr("wendao_bot.notifier.subprocess.run", forbidden_send)

    assert main(["notify-test", "--dry-run"]) == 0
    assert capsys.readouterr().out == (
        "To: wendao-owner@example.com\n"
        "[问道脚本暂停] 原因=notification test 等级=未知 任务=未知 "
        "截图=none 时间=manual\n"
    )

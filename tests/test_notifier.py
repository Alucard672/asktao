from dataclasses import FrozenInstanceError
import subprocess
import sys

import pytest

from wendao_bot.notifier import IMessageNotifier, NotificationError, PauseNotice


def notice(**overrides: object) -> PauseNotice:
    values = {
        "reason": "验证码",
        "level": 16,
        "task": "主线",
        "screenshot": "/tmp/screen.png",
        "timestamp": "2026-08-13 12:00:00",
    }
    values.update(overrides)
    return PauseNotice(**values)  # type: ignore[arg-type]


def test_pause_notice_is_frozen_and_formats_unknown_values() -> None:
    item = notice(level=None, task=None)

    assert IMessageNotifier.format_notice(item) == (
        "[问道脚本暂停] 原因=验证码 等级=未知 任务=未知 "
        "截图=/tmp/screen.png 时间=2026-08-13 12:00:00"
    )
    with pytest.raises(FrozenInstanceError):
        item.reason = "changed"  # type: ignore[misc]


def test_dry_run_returns_body_without_executing_runner() -> None:
    calls: list[object] = []
    notifier = IMessageNotifier(
        "wendao-owner@example.com", runner=calls.append, dry_run=True
    )

    body = notifier.send(notice())

    assert "验证码" in body
    assert calls == []


def test_send_uses_argv_and_selects_an_imessage_service(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    calls: list[tuple[list[str], dict[str, object]]] = []

    def runner(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, "", "")

    notifier = IMessageNotifier("person@example.com", runner=runner)
    body = notifier.send(notice(reason='quote " and shell $(touch nope)'))

    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[:2] == ["osascript", "-e"]
    assert args[-2:] == ["person@example.com", body]
    assert "service type is iMessage" in args[2]
    assert "enabled is true" in args[2]
    assert "buddy recipientId of targetService" in args[2]
    assert kwargs == {"check": True, "capture_output": True, "text": True}


def test_send_on_windows_uses_toast_without_recipient_in_argv(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    calls: list[tuple[list[str], dict[str, object]]] = []

    def runner(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, "", "")

    notifier = IMessageNotifier("person@example.com", runner=runner)
    body = notifier.send(notice(reason='quote " and shell $(touch nope)'))

    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[0] == "powershell"
    assert "-Command" in args
    assert "person@example.com" not in args
    assert body not in args
    assert kwargs["check"] is True
    assert kwargs["env"]["WENDAO_NOTIFY_BODY"] == body


@pytest.mark.parametrize("recipient", ["", "   ", "bad\x00recipient"])
def test_recipient_must_be_safe_nonempty_text(recipient: str) -> None:
    with pytest.raises(NotificationError):
        IMessageNotifier(recipient)


def test_notice_fields_are_validated() -> None:
    notifier = IMessageNotifier("person@example.com", dry_run=True)

    with pytest.raises(NotificationError, match="reason"):
        notifier.send(notice(reason=""))


def test_runner_failure_is_wrapped() -> None:
    def runner(*args: object, **kwargs: object) -> None:
        raise subprocess.CalledProcessError(1, args)

    with pytest.raises(NotificationError, match="failed"):
        IMessageNotifier("person@example.com", runner=runner).send(notice())


def test_subprocess_failure_does_not_leak_recipient_or_body() -> None:
    recipient = "private-person@example.com"
    secret_reason = "private pause details"

    def runner(args: list[str], **kwargs: object) -> None:
        raise subprocess.CalledProcessError(7, args, output="private stdout", stderr="private stderr")

    with pytest.raises(NotificationError) as raised:
        IMessageNotifier(recipient, runner=runner).send(notice(reason=secret_reason))

    rendered = repr(raised.value) + str(raised.value)
    assert recipient not in rendered
    assert secret_reason not in rendered
    assert "private stdout" not in rendered
    assert "private stderr" not in rendered
    assert "status 7" in str(raised.value)
    assert raised.value.__cause__ is None


def test_unexpected_runner_failure_is_also_controlled() -> None:
    def runner(*args: object, **kwargs: object) -> None:
        raise RuntimeError("runner adapter failed")

    with pytest.raises(NotificationError, match="failed"):
        IMessageNotifier("person@example.com", runner=runner).send(notice())

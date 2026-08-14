from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import os
import subprocess
import sys
from typing import Any


class NotificationError(RuntimeError):
    """A pause notification could not be prepared or delivered."""


@dataclass(frozen=True)
class PauseNotice:

    reason: str
    level: int | None
    task: str | None
    screenshot: str
    timestamp: str


_APPLESCRIPT = """on run argv
set recipientId to item 1 of argv
set bodyText to item 2 of argv
tell application "Messages"
    set matchingServices to every service whose service type is iMessage and enabled is true
    if (count of matchingServices) is 0 then error "No iMessage service is available"
    set targetService to item 1 of matchingServices
    send bodyText to buddy recipientId of targetService
end tell
end run"""


_POWERSHELL = """Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$icon = New-Object System.Windows.Forms.NotifyIcon
$icon.Icon = [System.Drawing.SystemIcons]::Information
$icon.BalloonTipTitle = '问道前台助手'
$icon.BalloonTipText = $env:WENDAO_NOTIFY_BODY
$icon.Visible = $true
$icon.ShowBalloonTip(10000)
Start-Sleep -Seconds 1
$icon.Dispose()"""


class IMessageNotifier:
    def __init__(
        self,
        recipient: str,
        *,
        runner: Callable[..., Any] = subprocess.run,
        dry_run: bool = False,
    ) -> None:
        self._recipient = self._required_text(recipient, "recipient")
        self._runner = runner
        self._dry_run = dry_run

    @staticmethod
    def _required_text(value: object, field: str) -> str:
        if not isinstance(value, str) or not value.strip() or "\x00" in value:
            raise NotificationError(f"{field} must be nonempty text without NUL bytes")
        return value

    @classmethod
    def format_notice(cls, notice: PauseNotice) -> str:
        if not isinstance(notice, PauseNotice):
            raise NotificationError("notice must be a PauseNotice")
        reason = cls._required_text(notice.reason, "reason")
        screenshot = cls._required_text(notice.screenshot, "screenshot")
        timestamp = cls._required_text(notice.timestamp, "timestamp")
        if notice.level is not None and (
            isinstance(notice.level, bool) or not isinstance(notice.level, int)
        ):
            raise NotificationError("level must be an integer or None")
        if notice.task is not None:
            task = cls._required_text(notice.task, "task")
        else:
            task = "未知"
        level = "未知" if notice.level is None else str(notice.level)
        return (
            f"[问道脚本暂停] 原因={reason} 等级={level} 任务={task} 截图="
            f"{screenshot} 时间={timestamp}"
        )

    def send(self, notice: PauseNotice) -> str:
        body = self.format_notice(notice)
        if self._dry_run:
            return body
        run_kwargs: dict[str, Any] = {}
        if sys.platform == "win32":
            args = [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                _POWERSHELL,
            ]
            run_kwargs["env"] = {**os.environ, "WENDAO_NOTIFY_BODY": body}
        else:
            args = ["osascript", "-e", _APPLESCRIPT, self._recipient, body]
        try:
            self._runner(args, check=True, capture_output=True, text=True, **run_kwargs)
        except subprocess.CalledProcessError as exc:
            raise NotificationError(
                f"iMessage notification failed with status {exc.returncode}"
            ) from None
        except Exception:
            raise NotificationError("iMessage notification failed") from None
        return body

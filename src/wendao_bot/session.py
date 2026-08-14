"""Validated access to a single macOS game window."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class WindowInfo:
    window_id: int
    title: str
    x: int
    y: int
    width: int
    height: int
    owner_pid: int = 0
    owner_name: str = ""


class WindowBackend(Protocol):
    def list_windows(self) -> list[WindowInfo]: ...

    def capture(self, target: WindowInfo) -> bytes: ...

    def focus_and_click(self, target: WindowInfo, x: int, y: int) -> None: ...

    def send_key(self, target: WindowInfo, key: str) -> None: ...


class GameSession:
    def __init__(
        self,
        backend: WindowBackend,
        title: str,
        width: int,
        height: int,
        expected_owner_pid: int | None = None,
        expected_owner_name: str = "问道",
    ) -> None:
        self.backend = backend
        self.title = title
        self.width = width
        self.height = height
        if not expected_owner_name:
            raise ValueError("expected owner name must not be empty")
        self._expected_owner_name = expected_owner_name
        self._expected_owner_pid = expected_owner_pid
        self._window_identity = None

    def _resolve(self) -> WindowInfo:
        matches = [
            window
            for window in self.backend.list_windows()
            if window.title == self.title
        ]
        if len(matches) != 1:
            raise RuntimeError(
                f"expected exactly one window titled {self.title!r}, found {len(matches)}"
            )
        window = matches[0]
        if (window.width, window.height) != (self.width, self.height):
            raise RuntimeError(
                "window geometry mismatch: expected "
                f"{self.width}x{self.height}, found "
                f"{window.width}x{window.height}"
            )
        if window.owner_pid <= 0 or not window.owner_name:
            raise RuntimeError("window owner identity is missing or invalid")
        if window.owner_name != self._expected_owner_name:
            raise RuntimeError(
                "window owner name mismatch: expected "
                f"{self._expected_owner_name!r}, found {window.owner_name!r}"
            )

        if self._expected_owner_pid is not None and (
            window.owner_pid != self._expected_owner_pid
        ):
            raise RuntimeError(
                "window owner identity changed: expected PID "
                f"{self._expected_owner_pid}, found {window.owner_pid}"
            )
        owner = (window.owner_pid, window.owner_name)
        identity = (window.window_id, *owner)
        if self._window_identity is None:
            self._window_identity = identity
        elif identity != self._window_identity:
            raise RuntimeError(
                "window identity changed: expected "
                f"{self._window_identity!r}, found {identity!r}"
            )
        return window

    def capture(self) -> bytes:
        window = self._resolve()
        return self.backend.capture(window)

    def click(self, x: int, y: int) -> None:
        if not (0 <= x < self.width and 0 <= y < self.height):
            raise ValueError(
                f"click coordinates ({x}, {y}) are outside client bounds "
                f"{self.width}x{self.height}"
            )
        window = self._resolve()
        self.backend.focus_and_click(window, x, y)

    def send_key(self, key: str) -> None:
        if not isinstance(key, str) or len(key) != 1:
            raise ValueError(f"key must be a single character, got {key!r}")
        window = self._resolve()
        self.backend.send_key(window, key)

    def capture_to(self, path: Path) -> Path:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        data = self.capture()
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".tmp",
                delete=False,
            ) as output:
                temporary = output.name
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, destination)
            temporary = None
            if os.name != "nt":
                directory_fd = os.open(
                    destination.parent,
                    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
                )
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)
        return destination


class QuartzBackend:
    """Window backend whose platform dependencies are loaded only when used."""

    def list_windows(self) -> list[WindowInfo]:
        import Quartz

        options = (
            Quartz.kCGWindowListOptionOnScreenOnly
            | Quartz.kCGWindowListExcludeDesktopElements
        )
        records = Quartz.CGWindowListCopyWindowInfo(
            options, Quartz.kCGNullWindowID
        )
        windows = []
        for record in records or []:
            title = record.get(Quartz.kCGWindowName)
            bounds = record.get(Quartz.kCGWindowBounds)
            if not (isinstance(title, str) and bounds):
                continue
            windows.append(
                WindowInfo(
                    window_id=int(record[Quartz.kCGWindowNumber]),
                    title=title,
                    x=int(bounds["X"]),
                    y=int(bounds["Y"]),
                    width=int(bounds["Width"]),
                    height=int(bounds["Height"]),
                    owner_pid=int(record[Quartz.kCGWindowOwnerPID]),
                    owner_name=str(record.get(Quartz.kCGWindowOwnerName, "")),
                )
            )
        return windows

    def capture(self, target: WindowInfo) -> bytes:
        import AppKit
        import Quartz

        image = Quartz.CGWindowListCreateImage(
            Quartz.CGRectNull,
            Quartz.kCGWindowListOptionIncludingWindow,
            target.window_id,
            Quartz.kCGWindowImageBoundsIgnoreFraming
            | Quartz.kCGWindowImageNominalResolution,
        )
        if image is None:
            raise RuntimeError(f"could not capture window {target.window_id}")
        bitmap = AppKit.NSBitmapImageRep.alloc().initWithCGImage_(image)
        if bitmap is None:
            raise RuntimeError("could not decode captured CGImage")
        dimensions = (int(bitmap.pixelsWide()), int(bitmap.pixelsHigh()))
        expected = (target.width, target.height)
        if dimensions != expected:
            raise RuntimeError(
                f"captured pixel dimensions {dimensions} do not match window {expected}"
            )
        data = bitmap.representationUsingType_properties_(
            AppKit.NSBitmapImageFileTypePNG, {}
        )
        if data is None:
            raise RuntimeError(f"could not encode window {target.window_id} as PNG")
        return bytes(data)

    def focus_and_click(self, target: WindowInfo, x: int, y: int) -> None:
        import AppKit
        import Quartz

        application = AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(
            target.owner_pid
        )
        if application is None or not application.activateWithOptions_(
            AppKit.NSApplicationActivateIgnoringOtherApps
        ):
            raise RuntimeError("could not focus window owner application")

        current = next(
            (item for item in self.list_windows() if item.window_id == target.window_id),
            None,
        )
        if current != target:
            raise RuntimeError("window identity or bounds changed after focus")
        frontmost = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
        if frontmost is None or int(frontmost.processIdentifier()) != target.owner_pid:
            raise RuntimeError("window owner is not the foreground application")

        point = (target.x + x, target.y + y)
        if not self._target_is_topmost_at(target, point):
            raise RuntimeError("target window is covered at click coordinates")
        down = Quartz.CGEventCreateMouseEvent(
            None, Quartz.kCGEventLeftMouseDown, point, Quartz.kCGMouseButtonLeft
        )
        up = Quartz.CGEventCreateMouseEvent(
            None, Quartz.kCGEventLeftMouseUp, point, Quartz.kCGMouseButtonLeft
        )
        if down is None or up is None:
            raise RuntimeError("could not create mouse click events")
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)

    def send_key(self, target: WindowInfo, key: str) -> None:
        raise RuntimeError("keyboard input is not implemented on macOS")

    def _target_is_topmost_at(
        self, target: WindowInfo, point: tuple[int, int]
    ) -> bool:
        import Quartz

        options = (
            Quartz.kCGWindowListOptionOnScreenOnly
            | Quartz.kCGWindowListExcludeDesktopElements
        )
        records = Quartz.CGWindowListCopyWindowInfo(options, Quartz.kCGNullWindowID)
        px, py = point
        for record in records or []:
            if float(record.get(Quartz.kCGWindowAlpha, 1.0)) <= 0:
                continue
            bounds = record.get(Quartz.kCGWindowBounds)
            if not bounds:
                continue
            contains = (
                bounds["X"] <= px < bounds["X"] + bounds["Width"]
                and bounds["Y"] <= py < bounds["Y"] + bounds["Height"]
            )
            if not contains:
                continue
            if int(record[Quartz.kCGWindowNumber]) != target.window_id:
                return False
            return (
                record.get(Quartz.kCGWindowName) == target.title
                and int(record.get(Quartz.kCGWindowOwnerPID, -1))
                == target.owner_pid
                and str(record.get(Quartz.kCGWindowOwnerName, ""))
                == target.owner_name
                and int(bounds["X"]) == target.x
                and int(bounds["Y"]) == target.y
                and int(bounds["Width"]) == target.width
                and int(bounds["Height"]) == target.height
            )
        return False

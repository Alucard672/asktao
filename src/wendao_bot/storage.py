from __future__ import annotations

from collections.abc import Mapping
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
from typing import Any


class SensitiveLogDataError(ValueError):
    """Event data contains a key that must never be logged."""


_SENSITIVE_KEYS = ("password", "captcha", "token", "chat_text")


class RuntimeStore:
    def __init__(self, root: Path, screenshot_limit: int = 20) -> None:
        if isinstance(screenshot_limit, bool) or not isinstance(screenshot_limit, int):
            raise ValueError("screenshot_limit must be an integer")
        if screenshot_limit < 1:
            raise ValueError("screenshot_limit must be positive")
        self.root = Path(root)
        self.screens = self.root / "screens"
        self.screenshot_limit = screenshot_limit
        self._lock = threading.RLock()
        self._ensure_real_directory(self.root, parents=True)
        self._ensure_real_directory(self.screens)

    def append_event(self, event: Mapping[str, Any]) -> Path:
        if not isinstance(event, Mapping):
            raise TypeError("event must be a mapping")
        self._reject_sensitive_keys(event)
        encoded = json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
        path = self.root / "events.jsonl"
        flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
        flags |= getattr(os, "O_NOFOLLOW", 0)
        if path.is_symlink():
            raise ValueError("event log must be a regular non-symlink file")
        try:
            descriptor = os.open(path, flags, 0o600)
        except OSError as exc:
            raise ValueError("event log must be a regular non-symlink file") from exc
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            raise ValueError("event log must be a regular file")
        with os.fdopen(descriptor, "a", encoding="utf-8") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        return path

    def save_state(self, state: Mapping[str, Any]) -> Path:
        if not isinstance(state, Mapping):
            raise TypeError("state must be a mapping")
        path = self.root / "state.json"
        self._reject_existing_symlink(path, "state file")
        data = json.dumps(state, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
        with self._lock:
            self._atomic_write(path, data)
        return path

    def load_state(self) -> dict[str, Any]:
        path = self.root / "state.json"
        try:
            self._reject_existing_symlink(path, "state file")
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags)
            with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
                value = json.load(stream)
        except (
            FileNotFoundError,
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            ValueError,
        ):
            return {}
        return dict(value) if isinstance(value, Mapping) else {}

    def save_screenshot(self, content: bytes, name: str) -> Path:
        if not isinstance(content, bytes):
            raise TypeError("screenshot content must be bytes")
        if (
            not isinstance(name, str)
            or not name
            or name in {".", ".."}
            or Path(name).name != name
            or Path(name).suffix.casefold() not in {".png", ".jpg", ".jpeg"}
        ):
            raise ValueError("screenshot name must be a safe basename")
        path = self.screens / name
        with self._lock:
            self._reject_existing_symlink(path, "screenshot")
            self._atomic_write(path, content)
            self._rotate_screenshots()
        return path

    @staticmethod
    def _ensure_real_directory(path: Path, *, parents: bool = False) -> None:
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            path.mkdir(parents=parents)
            metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError(f"directory must not be a symlink: {path}")
        if not stat.S_ISDIR(metadata.st_mode):
            raise ValueError(f"path must be a directory: {path}")

    @staticmethod
    def _reject_existing_symlink(path: Path, label: str) -> None:
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            return
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError(f"{label} must not be a symlink")

    @classmethod
    def _reject_sensitive_keys(cls, value: object) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                normalized = str(key).casefold()
                if any(secret in normalized for secret in _SENSITIVE_KEYS):
                    raise SensitiveLogDataError(f"sensitive log key rejected: {key}")
                cls._reject_sensitive_keys(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                cls._reject_sensitive_keys(child)

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        if os.name == "nt":
            return
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _atomic_write(self, path: Path, content: bytes) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            self._fsync_directory(path.parent)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    def _rotate_screenshots(self) -> None:
        screenshots = []
        for path in self.screens.iterdir():
            if path.name.startswith(".") or path.suffix.casefold() not in {
                ".png",
                ".jpg",
                ".jpeg",
            }:
                continue
            try:
                metadata = path.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISREG(metadata.st_mode):
                screenshots.append((metadata.st_mtime_ns, path.name, path))
        screenshots.sort(reverse=True)
        for _, _, stale in screenshots[self.screenshot_limit :]:
            try:
                stale.unlink()
            except FileNotFoundError:
                continue
        if len(screenshots) > self.screenshot_limit:
            self._fsync_directory(self.screens)

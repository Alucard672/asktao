import json
import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest

from wendao_bot.storage import RuntimeStore, SensitiveLogDataError


def _symlinks_supported() -> bool:
    import tempfile

    with tempfile.TemporaryDirectory() as scratch:
        target = Path(scratch) / "target"
        target.write_text("", encoding="utf-8")
        try:
            (Path(scratch) / "link").symlink_to(target)
        except OSError:
            return False
    return True


requires_symlinks = pytest.mark.skipif(
    not _symlinks_supported(), reason="symlink creation is unavailable on this platform"
)



@requires_symlinks
def test_root_must_not_be_a_symlink(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "runtime"
    link.symlink_to(real, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        RuntimeStore(link)


@requires_symlinks
def test_screens_directory_must_not_be_a_symlink(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    elsewhere = tmp_path / "elsewhere"
    root.mkdir()
    elsewhere.mkdir()
    (root / "screens").symlink_to(elsewhere, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        RuntimeStore(root)


@requires_symlinks
def test_events_file_symlink_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "outside.jsonl"
    target.write_text("outside\n", encoding="utf-8")
    store = RuntimeStore(tmp_path / "runtime")
    (store.root / "events.jsonl").symlink_to(target)

    with pytest.raises(ValueError, match="event"):
        store.append_event({"status": "paused"})

    assert target.read_text(encoding="utf-8") == "outside\n"


@requires_symlinks
def test_state_file_symlink_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "outside.json"
    target.write_text("outside\n", encoding="utf-8")
    store = RuntimeStore(tmp_path / "runtime")
    (store.root / "state.json").symlink_to(target)

    with pytest.raises(ValueError, match="state"):
        store.save_state({"status": "paused"})

    assert target.read_text(encoding="utf-8") == "outside\n"


def test_screenshot_rotation_keeps_most_recent_files(tmp_path: Path) -> None:
    store = RuntimeStore(tmp_path, screenshot_limit=2)
    paths = []
    for index in range(3):
        path = store.save_screenshot(f"image-{index}".encode(), f"s{index}.png")
        os.utime(path, (index + 1, index + 1))
        paths.append(path)

    assert {path.name for path in (tmp_path / "screens").glob("*.png")} == {
        "s1.png",
        "s2.png",
    }


@pytest.mark.parametrize("name", ["../escape.png", "nested/file.png", "..", ""])
def test_screenshot_name_rejects_traversal(tmp_path: Path, name: str) -> None:
    store = RuntimeStore(tmp_path, screenshot_limit=2)

    with pytest.raises(ValueError, match="name"):
        store.save_screenshot(b"image", name)


@pytest.mark.parametrize("key", ["password", "CAPTCHA", "Api_Token", "chat_text"])
def test_event_rejects_sensitive_keys_recursively(tmp_path: Path, key: str) -> None:
    store = RuntimeStore(tmp_path)

    with pytest.raises(SensitiveLogDataError):
        store.append_event({"safe": [{"nested": {key: "secret"}}]})

    assert not (tmp_path / "events.jsonl").exists()


def test_event_does_not_scan_safe_values_for_secret_words(tmp_path: Path) -> None:
    store = RuntimeStore(tmp_path)
    event = {"reason": "token expired text shown by game"}

    store.append_event(event)

    assert json.loads((tmp_path / "events.jsonl").read_text(encoding="utf-8")) == event


def test_save_state_atomically_replaces_json_and_cleans_temp_file(tmp_path: Path) -> None:
    store = RuntimeStore(tmp_path)

    path = store.save_state({"status": "paused", "level": 16})

    assert path == tmp_path / "state.json"
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "status": "paused",
        "level": 16,
    }
    assert list(tmp_path.glob(".state.json.*.tmp")) == []


def test_load_state_returns_mapping_and_malformed_json_fails_closed(tmp_path: Path) -> None:
    store = RuntimeStore(tmp_path)
    store.save_state({"progress": {"level": 15}})
    assert store.load_state() == {"progress": {"level": 15}}
    (tmp_path / "state.json").write_text("not json", encoding="utf-8")
    assert store.load_state() == {}


def test_save_screenshot_writes_exact_bytes_without_temp_files(tmp_path: Path) -> None:
    store = RuntimeStore(tmp_path)

    path = store.save_screenshot(b"\x89PNG\r\n", "screen.png")

    assert path.read_bytes() == b"\x89PNG\r\n"
    assert list((tmp_path / "screens").glob(".*.tmp")) == []


def test_rotation_preserves_unrelated_and_temporary_files(tmp_path: Path) -> None:
    store = RuntimeStore(tmp_path, screenshot_limit=1)
    unrelated = store.screens / "notes.txt"
    temporary = store.screens / ".pending.png.tmp"
    unrelated.write_text("keep", encoding="utf-8")
    temporary.write_bytes(b"keep")

    store.save_screenshot(b"one", "one.png")
    store.save_screenshot(b"two", "two.jpg")

    assert unrelated.read_text(encoding="utf-8") == "keep"
    assert temporary.read_bytes() == b"keep"
    assert len(list(store.screens.glob("*.png")) + list(store.screens.glob("*.jpg"))) == 1


def test_concurrent_saves_are_serialized_and_keep_limit(tmp_path: Path) -> None:
    store = RuntimeStore(tmp_path, screenshot_limit=3)

    with ThreadPoolExecutor(max_workers=8) as pool:
        paths = list(
            pool.map(
                lambda index: store.save_screenshot(bytes([index]), f"s{index}.png"),
                range(20),
            )
        )

    assert len(paths) == 20
    assert len(list(store.screens.glob("*.png"))) == 3


def test_zero_screenshot_limit_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="positive"):
        RuntimeStore(tmp_path, screenshot_limit=0)

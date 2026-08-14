from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from scripts.capture_template import (
    PrivacyError,
    _parse_args,
    _ensure_safe_output,
    build_filename,
    capture_template,
    main,
    validate_box,
)


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


@pytest.mark.parametrize(
    "box",
    [
        (10, 20, 30, 40),
        (0, 0, 886, 672),
        (885, 671, 1, 1),
    ],
)
def test_validate_box_accepts_exact_integer_boxes_inside_window(box) -> None:
    assert validate_box(box, 886, 672) == box


@pytest.mark.parametrize(
    "box",
    [
        (True, 0, 1, 1),
        (0.0, 0, 1, 1),
        (0, 0, 0, 1),
        (0, 0, 1, -1),
        (-1, 0, 1, 1),
        (0, -1, 1, 1),
        (0, 0, 887, 1),
        (0, 0, 1, 673),
    ],
)
def test_validate_box_rejects_non_integer_or_out_of_bounds_boxes(box) -> None:
    with pytest.raises((TypeError, ValueError)):
        validate_box(box, 886, 672)


def test_filename_uses_standard_state_target_scale_format() -> None:
    assert build_filename("dialogue", "skip_dialogue", "1x") == (
        "dialogue__skip_dialogue__1x.png"
    )
    with pytest.raises(ValueError):
        build_filename("dialogue/../../private", "skip_dialogue", "1x")
    with pytest.raises(ValueError, match="scale"):
        build_filename("dialogue", "skip_dialogue", "retina")
    assert build_filename("map", "daily_除暴", "1x", ("除暴",)) == "map__daily_除暴__1x.png"
    with pytest.raises(ValueError, match="target"):
        build_filename("map", "daily_押镖", "1x", ("除暴",))


class FakeSession:
    def __init__(self, png: bytes) -> None:
        self.png = png
        self.destinations: list[Path] = []

    def capture(self) -> bytes:
        return self.png


def _png(path: Path, size: tuple[int, int] = (886, 672)) -> bytes:
    Image.new("RGB", size, "navy").save(path)
    return path.read_bytes()


def test_capture_dry_run_reports_crop_without_saving(tmp_path, capsys) -> None:
    source_seed = tmp_path / "seed.png"
    session = FakeSession(_png(source_seed))
    destination = tmp_path / "templates"

    result = capture_template(
        session=session,
        runtime_source=tmp_path / "runtime" / "source.png",
        destination_dir=destination,
        state="dialogue",
        target="skip_dialogue",
        scale="1x",
        box=(400, 300, 20, 10),
        recognizer=lambda _: "harmless text",
        dry_run=True,
        forbidden_regions=(),
    )

    assert result is None
    assert not destination.exists()
    assert not (tmp_path / "runtime").exists()
    assert "20x10" in capsys.readouterr().out


@pytest.mark.parametrize("private_text", ["user@example.test", "138 0013 8000"])
def test_capture_rejects_private_ocr_text(tmp_path, private_text) -> None:
    seed = tmp_path / "seed.png"
    session = FakeSession(_png(seed))

    with pytest.raises(PrivacyError, match="private"):
        capture_template(
            session=session,
            runtime_source=tmp_path / "runtime" / "source.png",
            destination_dir=tmp_path / "templates",
            state="dialogue",
            target="skip_dialogue",
            scale="1x",
            box=(400, 300, 20, 10),
            recognizer=lambda _: private_text,
            forbidden_regions=(),
        )


def test_capture_rejects_overlap_with_forbidden_region(tmp_path) -> None:
    seed = tmp_path / "seed.png"
    session = FakeSession(_png(seed))

    with pytest.raises(PrivacyError, match="forbidden"):
        capture_template(
            session=session,
            runtime_source=tmp_path / "runtime" / "source.png",
            destination_dir=tmp_path / "templates",
            state="dialogue",
            target="skip_dialogue",
            scale="1x",
            box=(10, 10, 20, 20),
            recognizer=lambda _: "",
            forbidden_regions=((0, 0, 50, 50),),
        )


@requires_symlinks
def test_capture_saves_crop_and_refuses_symlink_destination(tmp_path) -> None:
    seed = tmp_path / "seed.png"
    session = FakeSession(_png(seed))
    destination = tmp_path / "templates"

    saved = capture_template(
        session=session,
        runtime_source=tmp_path / "runtime" / "source.png",
        destination_dir=destination,
        state="dialogue",
        target="skip_dialogue",
        scale="1x",
        box=(400, 300, 20, 10),
        recognizer=lambda _: "",
        forbidden_regions=(),
    )
    assert saved == destination / "dialogue__skip_dialogue__1x.png"
    assert Image.open(saved).size == (20, 10)

    saved.unlink()
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"keep")
    saved.symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        capture_template(
            session=session,
            runtime_source=tmp_path / "runtime" / "source.png",
            destination_dir=destination,
            state="dialogue",
            target="skip_dialogue",
            scale="1x",
            box=(400, 300, 20, 10),
            recognizer=lambda _: "",
            forbidden_regions=(),
        )
    assert outside.read_bytes() == b"keep"


def test_safe_output_rejects_escape_from_trusted_root(tmp_path) -> None:
    trusted = tmp_path / "trusted"
    trusted.mkdir()

    with pytest.raises(ValueError, match="outside trusted root"):
        _ensure_safe_output(tmp_path / "outside.png", trusted)
    with pytest.raises(ValueError, match="outside trusted root"):
        _ensure_safe_output(trusted / ".." / "escaped.png", trusted)


@requires_symlinks
def test_safe_output_rejects_symlink_in_existing_ancestor(tmp_path) -> None:
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (trusted / "nested").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        _ensure_safe_output(trusted / "nested" / "template.png", trusted)


def test_dry_run_rejects_save_source_and_creates_no_files(tmp_path) -> None:
    seed = tmp_path / "seed.png"
    session = FakeSession(_png(seed))
    source = tmp_path / "runtime" / "source.png"

    with pytest.raises(ValueError, match="mutually exclusive"):
        capture_template(
            session=session,
            runtime_source=source,
            destination_dir=tmp_path / "templates",
            state="dialogue",
            target="skip_dialogue",
            scale="1x",
            box=(400, 300, 20, 10),
            recognizer=lambda _: "",
            dry_run=True,
            save_source=True,
            forbidden_regions=(),
        )

    assert not source.exists()
    assert not (tmp_path / "templates").exists()


def test_cli_rejects_dry_run_with_save_source() -> None:
    with pytest.raises(SystemExit) as error:
        _parse_args(
            [
                "--state", "dialogue",
                "--target", "skip_dialogue",
                "--scale", "1x",
                "--box", "400", "300", "20", "10",
                "--dry-run",
                "--save-source",
            ]
        )

    assert error.value.code == 2


def test_capture_accepts_configured_919x674_geometry(tmp_path) -> None:
    seed = tmp_path / "seed.png"
    result = capture_template(
        session=FakeSession(_png(seed, (919, 674))),
        runtime_source=tmp_path / "runtime" / "source.png",
        destination_dir=tmp_path / "templates",
        state="map", target="shimen", scale="1x",
        box=(890, 650, 29, 24), recognizer=lambda _: "",
        forbidden_regions=(), window_width=919, window_height=674,
    )
    assert Image.open(result).size == (29, 24)


def test_capture_cli_builds_session_from_config_geometry(tmp_path, monkeypatch) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("window:\n  title: 问道\n  width: 919\n  height: 674\n", encoding="utf-8")
    constructed = []
    captured = []

    class Session:
        def __init__(self, backend, title, width, height):
            constructed.append((title, width, height))

    monkeypatch.setattr("scripts.capture_template.GameSession", Session)
    monkeypatch.setattr("scripts.capture_template.capture_template", lambda **kw: captured.append(kw))
    assert main([
        "--config", str(config), "--state", "map", "--target", "daily",
        "--daily-name", "除暴", "--scale", "1x", "--box", "1", "1", "10", "10",
        "--dry-run",
    ]) == 0
    assert constructed == [("问道", 919, 674)]
    assert captured[0]["target"] == "daily_除暴"
    assert (captured[0]["window_width"], captured[0]["window_height"]) == (919, 674)

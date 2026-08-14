import io

import pytest
from PIL import Image

from wendao_bot import template_capture
from wendao_bot.app_service import resolve_template_dir, template_path
from wendao_bot.template_capture import (
    PrivacyError,
    save_template,
    user_template_dir,
)


def _png_bytes(width=886, height=672):
    stream = io.BytesIO()
    Image.new("RGB", (width, height), (10, 20, 30)).save(stream, format="PNG")
    return stream.getvalue()


def test_save_template_crops_and_saves(tmp_path):
    destination = save_template(
        _png_bytes(),
        (10, 20, 40, 30),
        "map",
        "npc",
        "1x",
        tmp_path / "templates",
        lambda image: "问道",
        (),
    )
    assert destination == tmp_path / "templates" / "map__npc__1x.png"
    with Image.open(destination) as saved:
        assert saved.size == (40, 30)


def test_save_template_accepts_whitelisted_daily_target(tmp_path):
    destination = save_template(
        _png_bytes(),
        (10, 20, 40, 30),
        "activity_list",
        "daily_shimen",
        "1x",
        tmp_path,
        lambda image: "",
        (),
        daily_whitelist=("shimen",),
    )
    assert destination.name == "activity_list__daily_shimen__1x.png"


def test_save_template_rejects_out_of_bounds_box(tmp_path):
    with pytest.raises(ValueError):
        save_template(
            _png_bytes(),
            (880, 20, 40, 30),
            "map",
            "npc",
            "1x",
            tmp_path,
            lambda image: "",
            (),
        )
    assert not list(tmp_path.glob("*.png"))


def test_save_template_rejects_forbidden_region_overlap(tmp_path):
    with pytest.raises(PrivacyError):
        save_template(
            _png_bytes(),
            (10, 500, 40, 30),
            "map",
            "npc",
            "1x",
            tmp_path,
            lambda image: "",
            template_capture.scaled_forbidden_regions(886, 672),
        )
    assert not list(tmp_path.glob("*.png"))


def test_save_template_rejects_private_ocr_text(tmp_path):
    with pytest.raises(PrivacyError):
        save_template(
            _png_bytes(),
            (10, 20, 40, 30),
            "map",
            "npc",
            "1x",
            tmp_path,
            lambda image: "联系 someone@example.com",
            (),
        )
    assert not list(tmp_path.glob("*.png"))


def test_save_template_rejects_failing_recognizer(tmp_path):
    def broken(image):
        raise RuntimeError("OCR failed: no OCR backend")

    with pytest.raises(RuntimeError):
        save_template(
            _png_bytes(),
            (10, 20, 40, 30),
            "map",
            "npc",
            "1x",
            tmp_path,
            broken,
            (),
        )
    assert not list(tmp_path.glob("*.png"))


def test_save_template_rejects_symlink_destination(tmp_path):
    destination_dir = tmp_path / "templates"
    destination_dir.mkdir()
    elsewhere = tmp_path / "outside.png"
    elsewhere.write_bytes(b"")
    (destination_dir / "map__npc__1x.png").symlink_to(elsewhere)
    with pytest.raises(ValueError):
        save_template(
            _png_bytes(),
            (10, 20, 40, 30),
            "map",
            "npc",
            "1x",
            destination_dir,
            lambda image: "",
            (),
        )
    assert elsewhere.read_bytes() == b""


def test_user_template_dir_is_runtime_templates(tmp_path):
    assert user_template_dir(tmp_path) == tmp_path / "templates"


def test_resolve_template_dir_prefers_user_templates_with_png(tmp_path):
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "map__npc__1x.png").write_bytes(_png_bytes(4, 4))
    assert resolve_template_dir(tmp_path) == templates


def test_resolve_template_dir_falls_back_without_user_templates(tmp_path):
    assert resolve_template_dir(tmp_path) == template_path()
    (tmp_path / "templates").mkdir()
    assert resolve_template_dir(tmp_path) == template_path()

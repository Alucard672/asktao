from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from wendao_bot.updater import (
    UpdateError,
    UpdateInfo,
    build_update_script,
    check_update,
    download_and_verify,
    install_directory,
    parse_manifest,
)


GOOD_DIGEST = "a" * 64


def manifest(**overrides) -> dict:
    base = {
        "versionCode": 9,
        "versionName": "0.9.0",
        "forceUpdate": False,
        "zipUrl": "https://ota.example.com/wendao/app.zip",
        "sha256": GOOD_DIGEST,
        "changelog": "notes",
    }
    base.update(overrides)
    return base


def info_for(payload: bytes, **overrides) -> UpdateInfo:
    fields = manifest(sha256=hashlib.sha256(payload).hexdigest(), **overrides)
    return parse_manifest(fields)


def test_parse_manifest_accepts_gofilm_style_aliases() -> None:
    parsed = parse_manifest(
        manifest(zipUrl=None, downloadUrl="https://ota.example.com/x.zip", force=True)
    )

    assert parsed.zip_url == "https://ota.example.com/x.zip"
    assert parsed.force_update is True


@pytest.mark.parametrize(
    "overrides",
    [
        {"versionCode": 0},
        {"versionCode": True},
        {"versionName": " "},
        {"zipUrl": "http://ota.example.com/x.zip"},
        {"zipUrl": None},
        {"sha256": None},
        {"sha256": "abc"},
        {"sha256": "z" * 64},
    ],
)
def test_parse_manifest_rejects_unsafe_fields(overrides) -> None:
    with pytest.raises(UpdateError):
        parse_manifest(manifest(**overrides))


def test_check_update_returns_none_when_not_newer() -> None:
    payload = json.dumps(manifest(versionCode=3)).encode()

    assert check_update(fetcher=lambda _url: payload, current_code=3) is None


def test_check_update_returns_info_when_newer() -> None:
    payload = json.dumps(manifest(versionCode=4)).encode()

    result = check_update(fetcher=lambda _url: payload, current_code=3)

    assert result is not None
    assert result.version_code == 4


def test_check_update_wraps_network_errors() -> None:
    def failing(_url: str) -> bytes:
        raise OSError("boom")

    with pytest.raises(UpdateError, match="manifest"):
        check_update(fetcher=failing)


def test_download_and_verify_writes_archive_when_hash_matches(tmp_path) -> None:
    payload = b"archive-bytes"

    saved = download_and_verify(
        info_for(payload), tmp_path, fetcher=lambda _url, _hook=None: payload
    )

    assert saved.read_bytes() == payload
    assert saved.parent == tmp_path


def test_download_and_verify_rejects_hash_mismatch(tmp_path) -> None:
    with pytest.raises(UpdateError, match="hash"):
        download_and_verify(
            info_for(b"expected"), tmp_path, fetcher=lambda _url, _hook=None: b"tampered"
        )
    assert list(tmp_path.iterdir()) == []


def test_install_directory_requires_packaged_app() -> None:
    with pytest.raises(UpdateError, match="packaged"):
        install_directory()


def test_update_script_waits_extracts_and_restarts() -> None:
    script = build_update_script(
        Path("C:/temp/update.zip"),
        Path("C:/apps/问道前台助手"),
        "问道前台助手.exe",
        4242,
    )

    assert "Wait-Process -Id 4242" in script
    assert "Expand-Archive -LiteralPath 'C:\\temp\\update.zip'" in script.replace(
        "C:/temp/update.zip", "C:\\temp\\update.zip"
    ) or "Expand-Archive -LiteralPath 'C:/temp/update.zip'" in script
    assert "Copy-Item" in script
    assert "Start-Process" in script
    assert "问道前台助手.exe" in script
    assert script.index("Wait-Process") < script.index("Expand-Archive")
    assert script.index("Expand-Archive") < script.index("Start-Process")

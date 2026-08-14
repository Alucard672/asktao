from __future__ import annotations

import os
import plistlib
import stat
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "packaging" / "wendao_app.spec"
BUILD_SCRIPT = ROOT / "scripts" / "build_app.sh"
WIN_SPEC = ROOT / "packaging" / "wendao_app_win.spec"
WIN_BUILD_SCRIPT = ROOT / "scripts" / "build_app.ps1"


def _pinned_venv_python_works() -> bool:
    python = ROOT / ".venv" / "bin" / "python"
    if not python.exists():
        return False
    try:
        result = subprocess.run(
            [python, "-c", "import PyInstaller"],
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def test_spec_defines_native_app_identity_and_launcher() -> None:
    contents = SPEC.read_text(encoding="utf-8")

    assert "scripts/wendao_app.py" in contents
    assert "name=APP_NAME" in contents
    assert 'APP_NAME = "问道前台助手"' in contents
    assert 'BUNDLE_IDENTIFIER = "local.wendao.foreground-helper"' in contents
    assert "bundle_identifier=BUNDLE_IDENTIFIER" in contents
    assert "console=False" in contents
    assert "PROJECT_ROOT = Path(SPECPATH).parent" in contents


def test_spec_collects_packaged_configuration_and_templates() -> None:
    contents = SPEC.read_text(encoding="utf-8")

    assert "src/wendao_bot/default.yaml" in contents
    assert "src/wendao_bot/templates" in contents
    assert '"wendao_bot/templates"' in contents
    for dependency in ("Quartz", "Vision", "Cocoa", "AppKit", "cv2", "PIL", "yaml"):
        assert f'"{dependency}"' in contents


def test_build_script_uses_pinned_project_venv_and_archives_app() -> None:
    contents = BUILD_SCRIPT.read_text(encoding="utf-8")
    mode = BUILD_SCRIPT.stat().st_mode

    assert mode & stat.S_IXUSR
    assert 'PROJECT_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"' in contents
    assert '"$PROJECT_ROOT/.venv/bin/python" -m PyInstaller --noconfirm --clean' in contents
    assert '"$PROJECT_ROOT/packaging/wendao_app.spec"' in contents
    assert '"$PROJECT_ROOT/dist/问道前台助手.app"' in contents
    assert '"$PROJECT_ROOT/dist/问道前台助手.zip"' in contents
    assert "ditto -c -k --sequesterRsrc --keepParent" in contents
    assert contents.index('rm -f "$PROJECT_ROOT/dist/问道前台助手.zip"') < contents.index(
        '"$PROJECT_ROOT/.venv/bin/python" -m PyInstaller'
    )


def inspect_built_bundle(app_path: Path) -> None:
    """Validate bundle metadata and launcher without starting the GUI."""
    plist_path = app_path / "Contents" / "Info.plist"
    with plist_path.open("rb") as handle:
        info = plistlib.load(handle)

    assert info["CFBundleIdentifier"] == "local.wendao.foreground-helper"
    assert info["CFBundleDisplayName"] == "问道前台助手"
    assert "明确启用" in info["NSAppleEventsUsageDescription"]
    executable = app_path / "Contents" / "MacOS" / info["CFBundleExecutable"]
    assert executable.is_file()
    assert os.access(executable, os.X_OK)
    assert (app_path / "Contents" / "Resources" / "wendao_bot" / "default.yaml").is_file()
    assert (
        app_path
        / "Contents"
        / "Resources"
        / "wendao_bot"
        / "templates"
        / "README.md"
    ).is_file()


def test_windows_spec_defines_windowed_exe_and_launcher() -> None:
    contents = WIN_SPEC.read_text(encoding="utf-8")

    assert "scripts/wendao_app.py" in contents
    assert "name=APP_NAME" in contents
    assert 'APP_NAME = "问道前台助手"' in contents
    assert "console=False" in contents
    assert "PROJECT_ROOT = Path(SPECPATH).parent" in contents
    for dependency in ("cv2", "PIL", "yaml", "mss", "tkinter", "winsdk"):
        assert f'"{dependency}"' in contents


def test_windows_spec_collects_packaged_configuration_and_templates() -> None:
    contents = WIN_SPEC.read_text(encoding="utf-8")

    assert "src/wendao_bot/default.yaml" in contents
    assert "src/wendao_bot/templates" in contents
    assert '"wendao_bot/templates"' in contents


def test_windows_build_script_uses_pinned_project_venv_and_archives_app() -> None:
    contents = WIN_BUILD_SCRIPT.read_text(encoding="utf-8")

    assert ".venv\\Scripts\\python.exe" in contents
    assert "wendao_app_win.spec" in contents
    assert "-m PyInstaller --noconfirm --clean" in contents
    assert "Compress-Archive" in contents
    assert contents.index("Remove-Item -Force -ErrorAction SilentlyContinue $ZipPath") < contents.index(
        "-m PyInstaller"
    )


@pytest.mark.skipif(sys.platform != "win32", reason="Windows packaging build")
def test_windows_built_bundle_contract() -> None:
    subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(WIN_BUILD_SCRIPT),
        ],
        cwd=ROOT,
        check=True,
        timeout=600,
    )

    app_dir = ROOT / "dist" / "问道前台助手"
    assert (app_dir / "问道前台助手.exe").is_file()
    assert (app_dir / "_internal" / "wendao_bot" / "default.yaml").is_file()
    assert (app_dir / "_internal" / "wendao_bot" / "templates" / "README.md").is_file()
    assert (ROOT / "dist" / "问道前台助手-win.zip").is_file()


@pytest.mark.skipif(
    sys.platform != "darwin" or not _pinned_venv_python_works(),
    reason="macOS packaging build requires a working pinned .venv",
)
def test_built_bundle_contract() -> None:
    subprocess.run(
        [BUILD_SCRIPT],
        cwd=ROOT,
        check=True,
        timeout=180,
    )

    app_path = ROOT / "dist" / "问道前台助手.app"
    archive_path = ROOT / "dist" / "问道前台助手.zip"
    inspect_built_bundle(app_path)
    assert archive_path.is_file()
    assert archive_path.stat().st_size > 0

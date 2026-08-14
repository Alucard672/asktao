from __future__ import annotations

from pathlib import Path

from wendao_bot.diagnostics import run_diagnostics
from wendao_bot.session import WindowInfo


class FakeBackend:
    def __init__(self, windows, capture_error=None):
        self.windows = windows
        self.capture_error = capture_error

    def list_windows(self):
        return list(self.windows)

    def capture(self, target):
        if self.capture_error is not None:
            raise self.capture_error
        return b"\x89PNG fake"

    def click(self, target, x, y):
        raise AssertionError("diagnostics must never click")


def config_file(tmp_path: Path, width: int = 853, height: int = 519) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(
        "window:\n"
        "  title: MuMu安卓设备\n"
        "  owner: MuMuPlayer.exe\n"
        f"  width: {width}\n"
        f"  height: {height}\n",
        encoding="utf-8",
    )
    return path


def window(width: int = 853, height: int = 519) -> WindowInfo:
    return WindowInfo(7, "MuMu安卓设备", 100, 100, width, height, 10, "MuMuPlayer.exe")


def test_diagnostics_reports_real_capture_error_and_writes_report(tmp_path):
    backend = FakeBackend(
        [window()],
        capture_error=RuntimeError(
            "captured pixel dimensions (1280, 778) do not match window (853, 519)"
        ),
    )

    report, lines = run_diagnostics(config_file(tmp_path), tmp_path / "rt", backend)

    joined = "\n".join(lines)
    assert "[OK] 配置" in joined
    assert "[FAIL] 截屏" in joined
    assert "captured pixel dimensions" in joined
    assert report.read_text(encoding="utf-8").count("captured pixel dimensions") >= 1


def test_diagnostics_flags_geometry_mismatch_without_capturing(tmp_path):
    backend = FakeBackend([window(width=1280, height=778)])

    _report, lines = run_diagnostics(config_file(tmp_path), tmp_path / "rt", backend)

    joined = "\n".join(lines)
    assert "[FAIL] 几何" in joined
    assert "1280x778" in joined


def test_diagnostics_reports_config_error_without_crashing(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("window: [not-a-mapping\n", encoding="utf-8")

    report, lines = run_diagnostics(bad, tmp_path / "rt", FakeBackend([window()]))

    assert "[FAIL] 配置" in "\n".join(lines)
    assert report.is_file()


def test_diagnostics_counts_exact_title_matches(tmp_path):
    other = WindowInfo(8, "MuMu安卓设备 -1", 0, 0, 853, 519, 11, "MuMuPlayer.exe")
    backend = FakeBackend([window(), other])

    _report, lines = run_diagnostics(config_file(tmp_path), tmp_path / "rt", backend)

    assert "精确匹配 1 个" in "\n".join(lines)

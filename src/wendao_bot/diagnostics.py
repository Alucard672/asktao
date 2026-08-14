"""Step-by-step local diagnosis of the capture pipeline.

Unlike the sanitized GUI state, the generated report contains the real
error messages for each stage. It is written only to the local runtime
directory; it may include window titles, so review it before sharing.
"""
from __future__ import annotations

import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path


def _record(lines: list[str], stage: str, ok: bool, detail: str) -> None:
    lines.append(f"[{'OK' if ok else 'FAIL'}] {stage}: {detail}")


def _dpi_report(lines: list[str]) -> None:
    if sys.platform != "win32":
        _record(lines, "DPI", True, "非 Windows 平台，跳过")
        return
    try:
        import ctypes

        user32 = ctypes.windll.user32
        aware = bool(user32.IsProcessDPIAware())
        dpi = None
        try:
            dpi = int(user32.GetDpiForSystem())
        except Exception:
            pass
        detail = f"IsProcessDPIAware={aware}"
        if dpi:
            detail += f" GetDpiForSystem={dpi} (缩放约 {round(dpi / 96 * 100)}%)"
        _record(lines, "DPI", aware, detail + ("" if aware else " —— 进程未感知 DPI，坐标会被缩放虚拟化"))
    except Exception as error:
        _record(lines, "DPI", False, f"{type(error).__name__}: {error}")


def _ocr_report(lines: list[str]) -> None:
    if sys.platform != "win32":
        _record(lines, "OCR", True, "非 Windows 平台，跳过")
        return
    try:
        from winsdk.windows.globalization import Language
        from winsdk.windows.media.ocr import OcrEngine

        engine = OcrEngine.try_create_from_language(Language("zh-Hans"))
        if engine is None:
            engine = OcrEngine.try_create_from_user_profile_languages()
        if engine is None:
            _record(
                lines, "OCR", False,
                "无可用 OCR 语言引擎：请在 Windows 设置安装中文（简体）语言包",
            )
        else:
            _record(lines, "OCR", True, "OCR 引擎可用")
    except Exception as error:
        _record(lines, "OCR", False, f"winsdk 不可用: {type(error).__name__}: {error}")


def run_diagnostics(
    config_path: Path | None,
    runtime_dir: Path,
    backend=None,
    ocr=None,
) -> tuple[Path, list[str]]:
    lines: list[str] = [
        f"问道前台助手诊断报告 {datetime.now(timezone.utc).isoformat()}"
    ]
    failures: list[str] = []

    config = None
    try:
        from .config import load_config

        config = load_config(config_path)
        _record(
            lines, "配置", True,
            f"title={config.window_title!r} owner={config.window_owner!r} "
            f"size={config.width}x{config.height} (来源: {config_path or '内置默认'})",
        )
    except Exception as error:
        _record(lines, "配置", False, f"{type(error).__name__}: {error}")
        failures.append(traceback.format_exc())

    _dpi_report(lines)

    windows = []
    try:
        if backend is None:
            if sys.platform == "win32":
                from .session_windows import Win32Backend

                backend = Win32Backend()
            else:
                from .session import QuartzBackend

                backend = QuartzBackend()
        windows = backend.list_windows()
        _record(lines, "窗口枚举", True, f"发现 {len(windows)} 个可见窗口")
    except Exception as error:
        _record(lines, "窗口枚举", False, f"{type(error).__name__}: {error}")
        failures.append(traceback.format_exc())

    if config is not None and windows:
        matches = [w for w in windows if w.title == config.window_title]
        _record(
            lines, "标题匹配", len(matches) == 1,
            f"精确匹配 {len(matches)} 个（要求恰好 1 个）",
        )
        for window in matches[:3]:
            geometry_ok = (window.width, window.height) == (config.width, config.height)
            _record(
                lines, "候选窗口", True,
                f"title={window.title!r} owner={window.owner_name!r} "
                f"client={window.width}x{window.height} at ({window.x},{window.y})",
            )
            if not geometry_ok:
                _record(
                    lines, "几何", False,
                    f"配置 {config.width}x{config.height} != 实际 "
                    f"{window.width}x{window.height} —— 请重新“检测模拟器”或修改配置",
                )

    captured = None
    if config is not None and backend is not None:
        try:
            from .session import GameSession

            owner = config.window_owner or ("asktao.exe" if sys.platform == "win32" else "问道")
            session = GameSession(
                backend, config.window_title, config.width, config.height, None, owner
            )
            captured = session.capture()
            _record(lines, "截屏", True, f"成功，PNG {len(captured)} 字节")
        except Exception as error:
            _record(lines, "截屏", False, f"{type(error).__name__}: {error}")
            failures.append(traceback.format_exc())

    _ocr_report(lines)

    if captured is not None and config is not None:
        import tempfile

        frame = Path(tempfile.mkdtemp(prefix="wendao-diag-")) / "frame.png"
        frame.write_bytes(captured)
        try:
            if ocr is None:
                from .recognizer import default_ocr as ocr

            text = ocr(frame)
            _record(lines, "OCR识别", True, f"成功，识别到 {len(text)} 个字符")
        except Exception as error:
            _record(lines, "OCR识别", False, f"{type(error).__name__}: {error}")
            failures.append(traceback.format_exc())
        else:
            try:
                from .app_service import template_path
                from .recognizer import ScreenRecognizer

                snapshot = ScreenRecognizer(
                    ocr=ocr,
                    template_dir=template_path(),
                    daily_whitelist=config.daily_whitelist,
                ).classify(frame)
                _record(
                    lines, "画面识别", True,
                    f"state={snapshot.state.value} confidence={snapshot.confidence:.2f} "
                    f"evidence={sorted(snapshot.evidence)} targets={len(snapshot.targets)}",
                )
            except Exception as error:
                _record(lines, "画面识别", False, f"{type(error).__name__}: {error}")
                failures.append(traceback.format_exc())

    try:
        from .app_service import template_path

        templates = sorted(template_path().glob("*.png"))
        _record(
            lines, "模板", bool(templates),
            f"{len(templates)} 个模板文件"
            + ("" if templates else " —— 尚未采集，观察可用但不会解锁操作"),
        )
    except Exception as error:
        _record(lines, "模板", False, f"{type(error).__name__}: {error}")

    if failures:
        lines.append("")
        lines.append("=== 失败详情(traceback) ===")
        lines.extend(failures)

    runtime_dir = Path(runtime_dir)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    report = runtime_dir / "diagnostics.txt"
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report, lines

# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller recipe for the Wendao foreground-only Windows helper."""

from pathlib import Path


APP_NAME = "问道前台助手"
PROJECT_ROOT = Path(SPECPATH).parent

datas = [
    (str(PROJECT_ROOT / "src/wendao_bot/default.yaml"), "wendao_bot"),
    (str(PROJECT_ROOT / "src/wendao_bot/templates"), "wendao_bot/templates"),
]

# PyInstaller hooks cover the compiled files for OpenCV and Pillow.
# These names document and retain imports loaded indirectly by the frameworks.
hiddenimports = [
    "cv2",
    "PIL",
    "yaml",
    "mss",
    "tkinter",
    "winsdk",
    "winsdk.windows.media.ocr",
    "winsdk.windows.graphics.imaging",
    "winsdk.windows.storage.streams",
    "winsdk.windows.globalization",
]

a = Analysis(
    [str(PROJECT_ROOT / "scripts/wendao_app.py")],
    pathex=[str(PROJECT_ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)

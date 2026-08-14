# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller recipe for the Wendao foreground-only macOS helper."""

from pathlib import Path


APP_NAME = "问道前台助手"
BUNDLE_IDENTIFIER = "local.wendao.foreground-helper"
PROJECT_ROOT = Path(SPECPATH).parent

datas = [
    (str(PROJECT_ROOT / "src/wendao_bot/default.yaml"), "wendao_bot"),
    (str(PROJECT_ROOT / "src/wendao_bot/templates"), "wendao_bot/templates"),
]

# PyInstaller hooks cover the compiled files for OpenCV, Pillow, and PyObjC.
# These names document and retain imports loaded indirectly by the frameworks.
hiddenimports = [
    "objc",
    "Foundation",
    "Quartz",
    "Quartz.CoreGraphics",
    "Vision",
    "Cocoa",
    "AppKit",
    "cv2",
    "PIL",
    "yaml",
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
    upx=True,
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
    upx=True,
    upx_exclude=[],
    name=APP_NAME,
)
app = BUNDLE(
    coll,
    name=f"{APP_NAME}.app",
    icon=None,
    bundle_identifier=BUNDLE_IDENTIFIER,
    info_plist={
        "CFBundleDisplayName": APP_NAME,
        "LSUIElement": False,
        "NSHighResolutionCapable": True,
        "NSAppleEventsUsageDescription": (
            "仅在用户明确启用未来的真实暂停通知功能后控制“信息”；"
            "当前版本只提供不会发送的通知预览。"
        ),
    },
)

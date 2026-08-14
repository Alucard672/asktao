"""List visible top-level windows so the exact title can be copied into config.

Read-only: enumerates window titles, owner process names, and client sizes.
Never captures pixels and never clicks.

Usage:
    python scripts/list_windows.py            # all visible titled windows
    python scripts/list_windows.py MuMu       # only titles containing "MuMu"
"""
from __future__ import annotations

import sys


def _backend():
    if sys.platform == "win32":
        from wendao_bot.session_windows import Win32Backend

        return Win32Backend()
    from wendao_bot.session import QuartzBackend

    return QuartzBackend()


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    needle = args[0] if args else ""
    try:
        listed = _backend().list_windows()
    except Exception as error:
        print(f"cannot enumerate windows on this platform: {error}", file=sys.stderr)
        return 1
    windows = [
        window for window in listed if window.title and needle in window.title
    ]
    if not windows:
        print("no matching visible windows" + (f" containing {needle!r}" if needle else ""))
        return 1
    for window in windows:
        print(
            f"title={window.title!r} owner={window.owner_name!r} "
            f"client={window.width}x{window.height}"
        )
    print()
    print("把 title 的引号内内容原样复制到配置的 window.title,")
    print("owner 复制到 window.owner,client 尺寸复制到 width/height。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

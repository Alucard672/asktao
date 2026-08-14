"""Screenshot-only template capture tool with privacy guards.

The tool never clicks. It captures the configured game window, crops one
rectangle, and refuses to save anything that overlaps privacy regions or
contains private-looking OCR text.
"""
from __future__ import annotations

import argparse
import io
import re
import sys
from pathlib import Path
from typing import Callable, Sequence

from PIL import Image

from wendao_bot.config import load_config
from wendao_bot.session import GameSession
from wendao_bot.target_schema import is_safe_daily_name

SCALE_PATTERN = re.compile(r"^[1-9]\d*x$")
PRIVATE_TEXT_PATTERNS = (
    re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+"),
    re.compile(r"\d(?:[\s-]?\d){7,}"),
)
BASE_WINDOW = (886, 672)
BASE_FORBIDDEN_REGIONS = (
    (0, 502, 320, 170),
    (566, 0, 320, 40),
)


class PrivacyError(RuntimeError):
    """Raised when a capture would include private information."""


def validate_box(box: Sequence[object], width: int, height: int) -> tuple[int, int, int, int]:
    if len(box) != 4:
        raise ValueError("box must contain exactly four integers")
    for value in box:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError("box values must be integers")
    x, y, w, h = box
    if w <= 0 or h <= 0:
        raise ValueError("box width and height must be positive")
    if x < 0 or y < 0:
        raise ValueError("box origin must not be negative")
    if x + w > width or y + h > height:
        raise ValueError("box must stay inside the window")
    return (x, y, w, h)


def _require_safe_component(value: str, name: str) -> str:
    if not is_safe_daily_name(value):
        raise ValueError(f"{name} must be 1-32 alphanumeric, underscore, or hyphen characters")
    return value


def build_filename(
    state: str,
    target: str,
    scale: str,
    daily_whitelist: Sequence[str] = (),
) -> str:
    _require_safe_component(state, "state")
    if not SCALE_PATTERN.fullmatch(scale):
        raise ValueError("scale must look like 1x or 2x")
    if target.startswith("daily_"):
        daily_name = target[len("daily_"):]
        _require_safe_component(daily_name, "target")
        if daily_name not in tuple(daily_whitelist):
            raise ValueError("target daily name is not in the configured whitelist")
    else:
        _require_safe_component(target, "target")
    return f"{state}__{target}__{scale}.png"


def _ensure_safe_output(path: Path, trusted_root: Path) -> Path:
    candidate = Path(path)
    if ".." in candidate.parts:
        raise ValueError("output path is outside trusted root")
    root = trusted_root.absolute()
    try:
        candidate.absolute().relative_to(root)
    except ValueError:
        raise ValueError("output path is outside trusted root") from None
    ancestor = candidate.parent.absolute()
    while True:
        if ancestor.exists() and ancestor.is_symlink():
            raise ValueError("output ancestor must not be a symlink")
        if ancestor == root or ancestor == ancestor.parent:
            break
        ancestor = ancestor.parent
    if candidate.parent.exists():
        resolved = candidate.parent.resolve()
        if resolved != root.resolve() and root.resolve() not in resolved.parents:
            raise ValueError("output path is outside trusted root")
    return candidate


def _boxes_overlap(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def scaled_forbidden_regions(width: int, height: int) -> tuple[tuple[int, int, int, int], ...]:
    base_width, base_height = BASE_WINDOW
    regions = []
    for x, y, w, h in BASE_FORBIDDEN_REGIONS:
        regions.append(
            (
                x * width // base_width,
                y * height // base_height,
                -(-w * width // base_width),
                -(-h * height // base_height),
            )
        )
    return tuple(regions)


def capture_template(
    *,
    session,
    runtime_source: Path,
    destination_dir: Path,
    state: str,
    target: str,
    scale: str,
    box: Sequence[object],
    recognizer: Callable[[Image.Image], str],
    forbidden_regions: Sequence[tuple[int, int, int, int]],
    daily_whitelist: Sequence[str] = (),
    dry_run: bool = False,
    save_source: bool = False,
    window_width: int = 886,
    window_height: int = 672,
) -> Path | None:
    if dry_run and save_source:
        raise ValueError("--dry-run and --save-source are mutually exclusive")

    filename = build_filename(state, target, scale, daily_whitelist)
    checked_box = validate_box(box, window_width, window_height)

    for region in forbidden_regions:
        if _boxes_overlap(checked_box, tuple(region)):
            raise PrivacyError("box overlaps a forbidden privacy region")

    image = Image.open(io.BytesIO(session.capture()))
    if image.size != (window_width, window_height):
        raise ValueError(
            f"captured image is {image.size[0]}x{image.size[1]}, "
            f"expected {window_width}x{window_height}"
        )

    x, y, w, h = checked_box
    crop = image.crop((x, y, x + w, y + h))

    text = recognizer(crop)
    for pattern in PRIVATE_TEXT_PATTERNS:
        if pattern.search(text):
            raise PrivacyError("crop contains private-looking text")

    if dry_run:
        print(f"dry-run: would save {filename} crop {w}x{h} at ({x}, {y})")
        return None

    if save_source:
        source_path = _ensure_safe_output(runtime_source, runtime_source.parent)
        source_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(source_path)

    destination = _ensure_safe_output(destination_dir / filename, destination_dir)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink():
        raise ValueError("destination must not be a symlink")
    crop.save(destination)
    return destination


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture one recognition template from the configured game window.",
    )
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--state", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--daily-name", default=None)
    parser.add_argument("--scale", required=True)
    parser.add_argument("--box", nargs=4, type=int, required=True, metavar=("X", "Y", "W", "H"))
    parser.add_argument("--runtime", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--save-source", action="store_true")
    args = parser.parse_args(argv)
    if args.dry_run and args.save_source:
        parser.error("--dry-run and --save-source are mutually exclusive")
    return args


def _default_backend():
    try:
        if sys.platform == "win32":
            from wendao_bot.session_windows import Win32Backend

            return Win32Backend()
        from wendao_bot.session import QuartzBackend

        return QuartzBackend()
    except Exception:
        return None


def _default_recognizer() -> Callable[[Image.Image], str]:
    def recognize(image: Image.Image) -> str:
        try:
            from wendao_bot.recognizer import ocr_image
        except Exception:
            return ""
        return ocr_image(image)

    return recognize


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    config = load_config(args.config)

    target = args.target
    if target in {"daily", "daily_task"}:
        if not args.daily_name:
            print("--daily-name is required for daily targets", file=sys.stderr)
            return 2
        target = f"daily_{args.daily_name}"

    runtime_root = args.runtime if args.runtime is not None else Path.cwd() / "runtime"
    session = GameSession(
        _default_backend(),
        config.window_title,
        config.width,
        config.height,
    )
    capture_template(
        session=session,
        runtime_source=runtime_root / "source.png",
        destination_dir=Path.cwd() / "templates",
        state=args.state,
        target=target,
        scale=args.scale,
        box=tuple(args.box),
        recognizer=_default_recognizer(),
        forbidden_regions=scaled_forbidden_regions(config.width, config.height),
        daily_whitelist=config.daily_whitelist,
        dry_run=args.dry_run,
        save_source=args.save_source,
        window_width=config.width,
        window_height=config.height,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Reusable template-capture core with fail-closed privacy guards."""
from __future__ import annotations

import io
import os
import re
import tempfile
from pathlib import Path
from typing import Callable, Sequence

from PIL import Image

from .target_schema import is_safe_daily_name

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


def user_template_dir(runtime: Path) -> Path:
    return Path(runtime) / "templates"


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


def save_template(
    image_bytes: bytes,
    box: Sequence[object],
    state: str,
    target: str,
    scale: str,
    destination_dir: Path,
    recognizer: Callable[[Image.Image], str],
    forbidden_regions: Sequence[tuple[int, int, int, int]],
    daily_whitelist: Sequence[str] = (),
    window_width: int = 886,
    window_height: int = 672,
) -> Path:
    filename = build_filename(state, target, scale, daily_whitelist)
    checked_box = validate_box(box, window_width, window_height)

    for region in forbidden_regions:
        if _boxes_overlap(checked_box, tuple(region)):
            raise PrivacyError("box overlaps a forbidden privacy region")

    image = Image.open(io.BytesIO(image_bytes))
    if image.size != (window_width, window_height):
        raise ValueError(
            f"captured image is {image.size[0]}x{image.size[1]}, "
            f"expected {window_width}x{window_height}"
        )

    x, y, w, h = checked_box
    crop = image.crop((x, y, x + w, y + h))

    text = recognizer(crop)
    if not isinstance(text, str):
        raise PrivacyError("recognizer did not return text for the crop")
    for pattern in PRIVATE_TEXT_PATTERNS:
        if pattern.search(text):
            raise PrivacyError("crop contains private-looking text")

    destination_dir = Path(destination_dir)
    destination = _ensure_safe_output(destination_dir / filename, destination_dir)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink():
        raise ValueError("destination must not be a symlink")
    descriptor, temporary = tempfile.mkstemp(
        prefix=".wendao-template.", suffix=".png", dir=destination.parent
    )
    os.close(descriptor)
    try:
        crop.save(temporary)
        os.replace(temporary, destination)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise
    return destination

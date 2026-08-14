"""Conservative screen classification using OCR and local image templates."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
import re
import sys

import cv2
import numpy as np

from .models import ScreenSnapshot, ScreenState
from .target_schema import is_safe_daily_name


def _read_image(path: Path) -> "np.ndarray | None":
    try:
        data = np.fromfile(str(path), dtype=np.uint8)
    except OSError:
        return None
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)

OCR = Callable[[Path], str]

BLOCKED_KEYWORDS: Mapping[ScreenState, tuple[str, ...]] = {
    ScreenState.PAYMENT: ("充值", "金元宝", "购买", "支付"),
    ScreenState.CAPTCHA: ("验证码", "安全验证", "滑动验证"),
    ScreenState.BLOCKED_CHOICE: ("加点", "门派选择", "确认消耗", "属性分配"),
    ScreenState.DISCONNECTED: ("重新连接", "登录", "网络断开"),
    ScreenState.DEAD: ("死亡", "原地复活", "回城复活"),
    ScreenState.BAG_FULL: ("背包已满", "空间不足"),
}

GAMEPLAY_KEYWORDS: tuple[tuple[ScreenState, tuple[str, ...]], ...] = (
    (ScreenState.DIALOGUE, ("跳过剧情", "跳过对话", "继续对话")),
    (ScreenState.BATTLE, ("自动战斗", "战斗中")),
    (ScreenState.AUTO_PATH, ("自动寻路", "寻路中")),
    (ScreenState.NPC_OPTIONS, ("请选择", "对话选项")),
    (ScreenState.REWARD, ("领取奖励", "获得奖励")),
    (ScreenState.ACTIVITY_LIST, ("活动列表", "日常活动")),
    (ScreenState.MAP, ("世界地图", "当前地图")),
)

STANDARD_TARGETS = frozenset(
    {
        "main_quest",
        "skip_dialogue",
        "npc_main_option",
        "auto_path",
        "battle_auto",
        "claim",
        "equip",
        "shimen",
        "shimen_task",
        "patrol",
    }
)

MIN_TEMPLATE_GRAYSCALE_STDDEV = 1.0
_TEMPLATE_NAME = re.compile(r"^(?P<state>[a-z_]+)__(?P<target>.+?)__(?P<scale>[^.]+)\.png$")


def vision_ocr(image_path: Path) -> str:
    if sys.platform != "darwin":
        raise RuntimeError(
            "Vision OCR failed: macOS Vision framework is unavailable on this platform"
        )

    import Foundation
    import Vision

    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setRecognitionLanguages_(["zh-Hans"])
    request.setUsesLanguageCorrection_(True)
    url = Foundation.NSURL.fileURLWithPath_(str(Path(image_path).resolve()))
    handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(url, {})
    succeeded, error = handler.performRequests_error_([request], None)
    if not succeeded:
        raise RuntimeError(f"Vision OCR failed: {error}")
    lines = []
    for observation in request.results() or []:
        candidates = observation.topCandidates_(1)
        if not candidates:
            continue
        lines.append(str(candidates[0].string()))
    return "\n".join(lines)


OCR_TIMEOUT_SECONDS = 20.0


def _call_with_timeout(function, seconds: float, description: str):
    import threading

    outcome: list = []

    def work() -> None:
        try:
            outcome.append(("ok", function()))
        except BaseException as error:
            outcome.append(("error", error))

    worker = threading.Thread(target=work, name="wendao-ocr", daemon=True)
    worker.start()
    worker.join(seconds)
    if not outcome:
        raise RuntimeError(f"{description} timed out after {seconds:.0f}s")
    kind, value = outcome[0]
    if kind == "error":
        raise value
    return value


def windows_ocr(image_path: Path) -> str:
    if sys.platform != "win32":
        raise RuntimeError(
            "Windows OCR failed: Windows.Media.Ocr is unavailable on this platform"
        )
    return _call_with_timeout(
        lambda: _windows_ocr_impl(image_path),
        OCR_TIMEOUT_SECONDS,
        "Windows OCR",
    )


def _windows_ocr_impl(image_path: Path) -> str:
    import asyncio

    try:
        from winsdk.windows.globalization import Language
        from winsdk.windows.graphics.imaging import BitmapDecoder
        from winsdk.windows.media.ocr import OcrEngine
        from winsdk.windows.storage import StorageFile
    except ImportError as error:
        raise RuntimeError(
            f"Windows OCR failed: winsdk is not installed ({error})"
        ) from error

    async def recognize() -> str:
        engine = OcrEngine.try_create_from_language(Language("zh-Hans"))
        if engine is None:
            engine = OcrEngine.try_create_from_user_profile_languages()
        if engine is None:
            raise RuntimeError(
                "Windows OCR failed: no OCR engine for zh-Hans or the user profile languages"
            )
        file = await StorageFile.get_file_from_path_async(
            str(Path(image_path).resolve())
        )
        stream = await file.open_read_async()
        decoder = await BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_async()
        result = await engine.recognize_async(bitmap)
        lines = []
        for line in result.lines:
            lines.append(str(line.text))
        return "\n".join(lines)

    return asyncio.run(recognize())


def windows_ocr_words(image_path: Path) -> list[tuple[str, int, int, int, int]]:
    """Return OCR words as (text, left, top, width, height) tuples.

    Coordinates are screenshot pixels, which equal window client-area
    coordinates for captures taken through GameSession.
    """
    if sys.platform != "win32":
        raise RuntimeError(
            "Windows OCR failed: Windows.Media.Ocr is unavailable on this platform"
        )
    return _call_with_timeout(
        lambda: _windows_ocr_words_impl(image_path),
        OCR_TIMEOUT_SECONDS,
        "Windows OCR",
    )


def _windows_ocr_words_impl(image_path: Path) -> list[tuple[str, int, int, int, int]]:
    import asyncio

    try:
        from winsdk.windows.globalization import Language
        from winsdk.windows.graphics.imaging import BitmapDecoder
        from winsdk.windows.media.ocr import OcrEngine
        from winsdk.windows.storage import StorageFile
    except ImportError as error:
        raise RuntimeError(
            f"Windows OCR failed: winsdk is not installed ({error})"
        ) from error

    async def recognize() -> list[tuple[str, int, int, int, int]]:
        engine = OcrEngine.try_create_from_language(Language("zh-Hans"))
        if engine is None:
            engine = OcrEngine.try_create_from_user_profile_languages()
        if engine is None:
            raise RuntimeError(
                "Windows OCR failed: no OCR engine for zh-Hans or the user profile languages"
            )
        file = await StorageFile.get_file_from_path_async(
            str(Path(image_path).resolve())
        )
        stream = await file.open_read_async()
        decoder = await BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_async()
        result = await engine.recognize_async(bitmap)
        words = []
        for line in result.lines:
            for word in line.words:
                rect = word.bounding_rect
                words.append(
                    (
                        str(word.text),
                        int(rect.x),
                        int(rect.y),
                        int(rect.width),
                        int(rect.height),
                    )
                )
        return words

    return asyncio.run(recognize())


def default_ocr_words(image_path: Path) -> list[tuple[str, int, int, int, int]]:
    if sys.platform == "win32":
        return windows_ocr_words(image_path)
    raise RuntimeError(
        f"OCR word extraction failed: no backend for platform {sys.platform!r}"
    )


WORD_MERGE_ROW_TOLERANCE = 10


def merge_adjacent_words(
    words: list[tuple[str, int, int, int, int]], max_gap: int = 8
) -> list[tuple[str, int, int]]:
    """Merge horizontally adjacent OCR words into phrases.

    Input items are (text, left, top, width, height) boxes in screenshot
    pixels, as returned by windows_ocr_words. Output items are
    (phrase, center_x, center_y) where the center is that of the merged
    phrase bounding box.
    """
    rows: list[list[tuple[str, int, int, int, int]]] = []
    anchors: list[int] = []
    for word in sorted(words, key=lambda item: (item[2] + item[4] // 2, item[1])):
        center_y = word[2] + word[4] // 2
        placed = False
        for index, anchor in enumerate(anchors):
            if abs(center_y - anchor) <= WORD_MERGE_ROW_TOLERANCE:
                rows[index].append(word)
                placed = True
                break
        if not placed:
            rows.append([word])
            anchors.append(center_y)

    phrases: list[tuple[str, int, int]] = []
    for row in rows:
        ordered = sorted(row, key=lambda item: item[1])
        current: list[tuple[str, int, int, int, int]] = []
        for word in ordered:
            if current and word[1] - max(item[1] + item[3] for item in current) > max_gap:
                phrases.append(_phrase_from_words(current))
                current = []
            current.append(word)
        if current:
            phrases.append(_phrase_from_words(current))
    return phrases


def _phrase_from_words(
    words: list[tuple[str, int, int, int, int]]
) -> tuple[str, int, int]:
    text = "".join(item[0] for item in words)
    left = min(item[1] for item in words)
    right = max(item[1] + item[3] for item in words)
    top = min(item[2] for item in words)
    bottom = max(item[2] + item[4] for item in words)
    return (text, (left + right) // 2, (top + bottom) // 2)


def default_ocr(image_path: Path) -> str:
    if sys.platform == "darwin":
        return vision_ocr(image_path)
    if sys.platform == "win32":
        return windows_ocr(image_path)
    raise RuntimeError(f"OCR failed: no OCR backend for platform {sys.platform!r}")


class ScreenRecognizer:
    def __init__(
        self,
        ocr: OCR | None = None,
        template_dir: Path | None = None,
        template_registry: Mapping[str, Path] | None = None,
        match_threshold: float = 0.85,
        daily_whitelist: tuple[str, ...] = (),
    ) -> None:
        if not 0.0 <= match_threshold <= 1.0:
            raise ValueError("match_threshold must be between 0 and 1")
        self._ocr = ocr or default_ocr
        self._template_dir = Path(template_dir) if template_dir is not None else None
        self._template_registry = {
            name: Path(path) for name, path in (template_registry or {}).items()
        }
        self._match_threshold = match_threshold
        self._daily_whitelist = frozenset(daily_whitelist)
        self._ocr_succeeded = False

    def classify(self, image_path: Path) -> ScreenSnapshot:
        path = Path(image_path)
        text = self._ocr(path) or ""
        self._ocr_succeeded = True

        for state, keywords in BLOCKED_KEYWORDS.items():
            if any(self._has_blocked_keyword(text, keyword) for keyword in keywords):
                return self._snapshot(path, text, state, 0.99)

        ocr_state = None
        for state, keywords in GAMEPLAY_KEYWORDS:
            if any(keyword in text for keyword in keywords):
                ocr_state = state
                break

        template_matches = self._template_matches(path)
        if ocr_state is not None and template_matches:
            matching = [item for item in template_matches if item[0] is ocr_state]
            if not matching:
                return self._snapshot(
                    path, text, ScreenState.UNKNOWN, 0.1, evidence={"ocr", "template"}
                )
            targets, score = self._resolve_targets(matching)
            if targets is None:
                return self._snapshot(
                    path, text, ScreenState.UNKNOWN, 0.1, evidence={"ocr", "template"}
                )
            return self._snapshot(
                path, text, ocr_state, max(0.92, score), targets, {"ocr", "template"}
            )

        if ocr_state is not None:
            return self._snapshot(path, text, ocr_state, 0.92, evidence={"ocr"})
        if template_matches:
            state = template_matches[0][0]
            matching = [item for item in template_matches if item[0] is state]
            targets, score = self._resolve_targets(matching)
            if targets is None:
                return self._snapshot(path, text, ScreenState.UNKNOWN, 0.1)
            return self._snapshot(
                path, text, state, score, targets, {"ocr", "template"}
            )

        return self._snapshot(path, text, ScreenState.UNKNOWN, 0.1)

    @staticmethod
    def _has_blocked_keyword(text: str, keyword: str) -> bool:
        if keyword != "登录":
            return keyword in text
        compact = "".join(text.split())
        return compact == "登录" or any(
            phrase in compact
            for phrase in ("账号登录", "用户登录", "请登录", "重新登录", "登录游戏")
        )

    @staticmethod
    def _snapshot(
        path: Path,
        text: str,
        state: ScreenState,
        confidence: float,
        targets: Mapping[str, tuple[int, int]] | None = None,
        evidence: set[str] | None = None,
    ) -> ScreenSnapshot:
        return ScreenSnapshot(
            state=state,
            confidence=float(max(0.0, min(confidence, 1.0))),
            text=text,
            image_path=str(path),
            targets=targets or {},
            evidence=frozenset(evidence or {"ocr"}),
        )

    def health(self) -> dict[str, bool]:
        return {
            "ocr": self._ocr_succeeded,
            "templates": any(
                self._valid_template(path) for _, _, path in self._templates()
            ),
        }

    @staticmethod
    def _valid_template(path: Path) -> bool:
        image = _read_image(path)
        if image is None or image.ndim != 3 or image.shape[2] != 3:
            return False
        grayscale = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        return bool(
            image.shape[0]
            and image.shape[1]
            and np.std(grayscale) >= MIN_TEMPLATE_GRAYSCALE_STDDEV
        )

    def _templates(self) -> list[tuple[ScreenState, str, Path]]:
        paths = [
            (target, path) for target, path in self._template_registry.items()
        ]
        if self._template_dir is not None and self._template_dir.is_dir():
            paths.extend((None, path) for path in sorted(self._template_dir.glob("*.png")))

        templates = []
        for supplied_target, path in paths:
            match = _TEMPLATE_NAME.match(path.name)
            if match is None:
                continue
            target = supplied_target or match.group("target")
            if not self._allowed_target(target):
                continue
            try:
                state = ScreenState(match.group("state"))
            except ValueError:
                continue
            templates.append((state, target, path))
        return sorted(templates, key=lambda item: (item[0].value, item[1], str(item[2])))

    def _allowed_target(self, target: str) -> bool:
        if target in STANDARD_TARGETS:
            return True
        prefix = "daily_task_" if target.startswith("daily_task_") else "daily_"
        name = target[len(prefix):]
        return bool(
            target.startswith(prefix)
            and is_safe_daily_name(name)
            and name in self._daily_whitelist
        )

    def _template_matches(
        self, path: Path
    ) -> list[tuple[ScreenState, str, tuple[int, int], float, str]]:
        screen = _read_image(path)
        if screen is None or screen.ndim != 3 or screen.shape[2] != 3:
            return []
        matches = []
        screen_height, screen_width = screen.shape[:2]
        for state, target, template_path in self._templates():
            template = _read_image(template_path)
            if template is None or template.ndim != 3 or template.shape[2] != 3:
                continue
            height, width = template.shape[:2]
            if not height or not width or height > screen_height or width > screen_width:
                continue
            grayscale = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
            if float(np.std(grayscale)) < MIN_TEMPLATE_GRAYSCALE_STDDEV:
                continue
            result = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
            _, score, _, location = cv2.minMaxLoc(result)
            if not np.isfinite(score) or score < self._match_threshold:
                continue
            center = (location[0] + width // 2, location[1] + height // 2)
            matches.append((state, target, center, float(score), str(template_path)))

            suppressed = result.copy()
            x0, y0 = max(0, location[0] - width), max(0, location[1] - height)
            x1 = min(suppressed.shape[1], location[0] + width + 1)
            y1 = min(suppressed.shape[0], location[1] + height + 1)
            suppressed[y0:y1, x0:x1] = -1.0
            _, second_score, _, second_location = cv2.minMaxLoc(suppressed)
            if second_score >= self._match_threshold and score - second_score < 0.02:
                second_center = (
                    second_location[0] + width // 2,
                    second_location[1] + height // 2,
                )
                matches.append(
                    (state, target, second_center, float(second_score), str(template_path) + "#2")
                )
        return sorted(matches, key=lambda item: (-item[3], item[4], item[0].value, item[1]))

    @staticmethod
    def _resolve_targets(matches):
        selected = {}
        scores = []
        for state, target, center, score, _path in matches:
            previous = selected.get(target)
            if previous is not None:
                old_center, old_score = previous
                if old_center != center and abs(old_score - score) < 0.02:
                    return None, 0.0
                if old_score >= score:
                    continue
            selected[target] = (center, score)
        for center, score in selected.values():
            scores.append(score)
        return {target: value[0] for target, value in selected.items()}, min(scores)

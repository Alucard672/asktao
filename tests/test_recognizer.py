from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

from wendao_bot.models import ScreenState
from wendao_bot.recognizer import ScreenRecognizer
from wendao_bot.tasks import Progress, TaskPlanner


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "screens"
BLOCKED_FIXTURES = {
    "synthetic_payment_1x.png": ("充值", ScreenState.PAYMENT),
    "synthetic_captcha_1x.png": ("安全验证", ScreenState.CAPTCHA),
    "synthetic_blocked_choice_1x.png": ("属性分配", ScreenState.BLOCKED_CHOICE),
    "synthetic_disconnected_1x.png": ("网络断开", ScreenState.DISCONNECTED),
    "synthetic_dead_1x.png": ("回城复活", ScreenState.DEAD),
    "synthetic_bag_full_1x.png": ("背包已满", ScreenState.BAG_FULL),
}


@pytest.mark.parametrize("filename, expected", [(name, state) for name, (_, state) in BLOCKED_FIXTURES.items()])
def test_synthetic_blocked_fixture_exists_with_client_dimensions(filename, expected) -> None:
    path = FIXTURE_DIR / filename
    with Image.open(path) as image:
        assert image.size == (886, 672)
        assert image.mode == "RGB"


@pytest.mark.parametrize("filename, item", BLOCKED_FIXTURES.items())
def test_synthetic_blocked_fixture_maps_injected_ocr_to_state(filename, item) -> None:
    text, expected = item
    snapshot = ScreenRecognizer(ocr=lambda _: text).classify(FIXTURE_DIR / filename)
    assert snapshot.state is expected


def _blank(path: Path, size: tuple[int, int] = (320, 240)) -> Path:
    Image.new("RGB", size, "black").save(path)
    return path


def test_payment_keyword_is_blocked(tmp_path: Path) -> None:
    path = _blank(tmp_path / "screen.png")

    snapshot = ScreenRecognizer(ocr=lambda _: "充值 金元宝 购买").classify(path)

    assert snapshot.state is ScreenState.PAYMENT


def test_unknown_when_no_signal(tmp_path: Path) -> None:
    path = _blank(tmp_path / "blank.png")

    snapshot = ScreenRecognizer(ocr=lambda _: "").classify(path)

    assert snapshot.state is ScreenState.UNKNOWN
    assert snapshot.confidence < 0.88


def test_forbidden_state_has_priority_over_gameplay_state(tmp_path: Path) -> None:
    path = _blank(tmp_path / "screen.png")

    snapshot = ScreenRecognizer(ocr=lambda _: "自动战斗 安全验证").classify(path)

    assert snapshot.state is ScreenState.CAPTCHA


def test_daily_login_reward_is_not_treated_as_disconnect(tmp_path: Path) -> None:
    path = _blank(tmp_path / "screen.png")

    snapshot = ScreenRecognizer(ocr=lambda _: "每日登录奖励 领取奖励").classify(path)

    assert snapshot.state is ScreenState.REWARD


def test_exact_login_is_treated_as_disconnect(tmp_path: Path) -> None:
    path = _blank(tmp_path / "screen.png")

    snapshot = ScreenRecognizer(ocr=lambda _: "登录").classify(path)

    assert snapshot.state is ScreenState.DISCONNECTED


def test_template_match_returns_standard_target_center(tmp_path: Path) -> None:
    screen = np.zeros((120, 160, 3), dtype=np.uint8)
    pattern = np.zeros((16, 20, 3), dtype=np.uint8)
    pattern[:, :] = (20, 80, 140)
    pattern[3:13, 5:15] = (230, 210, 30)
    screen[40:56, 70:90] = pattern
    screen_path = tmp_path / "screen.png"
    template_path = tmp_path / "map__main_quest__1x.png"
    cv2.imwrite(str(screen_path), screen)
    cv2.imwrite(str(template_path), pattern)

    snapshot = ScreenRecognizer(
        ocr=lambda _: "", template_registry={"main_quest": template_path}
    ).classify(screen_path)

    assert snapshot.state is ScreenState.MAP
    assert snapshot.targets == {"main_quest": (80, 48)}
    assert snapshot.evidence == {"ocr", "template"}


def test_health_requires_successful_ocr_and_loaded_template(tmp_path: Path) -> None:
    screen = _blank(tmp_path / "screen.png")
    template = tmp_path / "map__main_quest__1x.png"
    pattern = np.zeros((12, 12, 3), dtype=np.uint8)
    pattern[2:10, 2:10] = 255
    cv2.imwrite(str(template), pattern)
    recognizer = ScreenRecognizer(ocr=lambda _: "当前地图", template_dir=tmp_path)
    assert recognizer.health() == {"ocr": False, "templates": True}
    recognizer.classify(screen)
    assert recognizer.health() == {"ocr": True, "templates": True}


def test_map_ocr_keeps_matching_main_quest_target(tmp_path: Path) -> None:
    screen = np.zeros((120, 160, 3), dtype=np.uint8)
    pattern = np.zeros((16, 20, 3), dtype=np.uint8)
    pattern[:, :] = (20, 80, 140)
    pattern[3:13, 5:15] = (230, 210, 30)
    screen[40:56, 70:90] = pattern
    screen_path = tmp_path / "screen.png"
    template_path = tmp_path / "map__main_quest__1x.png"
    cv2.imwrite(str(screen_path), screen)
    cv2.imwrite(str(template_path), pattern)

    snapshot = ScreenRecognizer(
        ocr=lambda _: "当前地图", template_registry={"main_quest": template_path}
    ).classify(screen_path)

    assert snapshot.state is ScreenState.MAP
    assert snapshot.targets == {"main_quest": (80, 48)}


def test_conflicting_ocr_and_template_states_return_unknown(tmp_path: Path) -> None:
    screen = np.zeros((80, 100, 3), dtype=np.uint8)
    pattern = np.zeros((12, 14, 3), dtype=np.uint8)
    pattern[2:10, 3:11] = (240, 120, 20)
    screen[30:42, 40:54] = pattern
    screen_path = tmp_path / "screen.png"
    template_path = tmp_path / "reward__claim__1x.png"
    cv2.imwrite(str(screen_path), screen)
    cv2.imwrite(str(template_path), pattern)

    snapshot = ScreenRecognizer(
        ocr=lambda _: "当前地图", template_registry={"claim": template_path}
    ).classify(screen_path)

    assert snapshot.state is ScreenState.UNKNOWN
    assert snapshot.targets == {}


def test_template_directory_uses_deterministic_best_match(tmp_path: Path) -> None:
    templates = tmp_path / "templates"
    templates.mkdir()
    screen = np.zeros((100, 140, 3), dtype=np.uint8)
    weak = np.zeros((12, 12, 3), dtype=np.uint8)
    weak[3:9, 1:11] = (80, 80, 80)
    strong = np.zeros((12, 12, 3), dtype=np.uint8)
    strong[2:10, 2:10] = (255, 255, 255)
    screen[30:42, 50:62] = strong
    screen_path = tmp_path / "screen.png"
    cv2.imwrite(str(screen_path), screen)
    cv2.imwrite(str(templates / "map__main_quest__1x.png"), weak)
    cv2.imwrite(str(templates / "reward__claim__1x.png"), strong)

    snapshot = ScreenRecognizer(ocr=lambda _: "", template_dir=templates).classify(
        screen_path
    )

    assert snapshot.state is ScreenState.REWARD
    assert snapshot.targets["claim"] == (56, 36)


def test_invalid_template_dimensions_are_skipped(tmp_path: Path) -> None:
    screen_path = _blank(tmp_path / "screen.png", (20, 20))
    oversized = tmp_path / "map__main_quest__1x.png"
    _blank(oversized, (40, 40))

    snapshot = ScreenRecognizer(
        ocr=lambda _: "", template_registry={"main_quest": oversized}
    ).classify(screen_path)

    assert snapshot.state is ScreenState.UNKNOWN
    assert snapshot.targets == {}


def test_uniform_template_is_rejected(tmp_path: Path) -> None:
    random = np.random.default_rng(7).integers(
        0, 256, size=(80, 100, 3), dtype=np.uint8
    )
    screen_path = tmp_path / "screen.png"
    template_path = tmp_path / "map__main_quest__1x.png"
    cv2.imwrite(str(screen_path), random)
    cv2.imwrite(str(template_path), np.zeros((12, 12, 3), dtype=np.uint8))

    snapshot = ScreenRecognizer(
        ocr=lambda _: "", template_registry={"main_quest": template_path}
    ).classify(screen_path)

    assert snapshot.state is ScreenState.UNKNOWN
    assert snapshot.targets == {}


def test_recognizer_returns_all_whitelisted_planner_targets(tmp_path: Path) -> None:
    templates = tmp_path / "templates"
    templates.mkdir()
    screen = np.zeros((140, 220, 3), dtype=np.uint8)
    specs = {
        "shimen": ((20, 20), 1),
        "daily_除暴": ((80, 45), 2),
        "patrol": ((150, 80), 3),
    }
    for target, ((x, y), seed) in specs.items():
        pattern = np.random.default_rng(seed).integers(0, 256, (14, 16, 3), dtype=np.uint8)
        screen[y:y + 14, x:x + 16] = pattern
        cv2.imwrite(str(templates / f"map__{target}__1x.png"), pattern)
    screen_path = tmp_path / "screen.png"
    cv2.imwrite(str(screen_path), screen)

    snapshot = ScreenRecognizer(
        ocr=lambda _: "当前地图 等级15 主线 20级开启",
        template_dir=templates,
        daily_whitelist=("除暴",),
    ).classify(screen_path)

    assert set(snapshot.targets) == {"shimen", "daily_除暴", "patrol"}
    planner = TaskPlanner(("除暴",))
    assert planner.next_action(snapshot, Progress(15, True)).target == "shimen"
    assert planner.next_action(snapshot, Progress(15, True, 10)).target == "daily_除暴"
    assert planner.next_action(
        snapshot, Progress(15, True, 10, frozenset({"除暴"}))
    ).target == "patrol"


def test_unwhitelisted_daily_template_is_ignored(tmp_path: Path) -> None:
    screen = np.random.default_rng(9).integers(0, 256, (40, 40, 3), dtype=np.uint8)
    pattern = screen[10:24, 12:28].copy()
    screen_path = tmp_path / "screen.png"
    template = tmp_path / "map__daily_押镖__1x.png"
    cv2.imwrite(str(screen_path), screen)
    cv2.imwrite(str(template), pattern)
    snapshot = ScreenRecognizer(ocr=lambda _: "当前地图", template_dir=tmp_path).classify(screen_path)
    assert snapshot.targets == {}


def test_near_equal_spatial_target_matches_fail_closed(tmp_path: Path) -> None:
    pattern = np.random.default_rng(33).integers(0, 256, (14, 16, 3), dtype=np.uint8)
    screen = np.zeros((80, 140, 3), dtype=np.uint8)
    screen[10:24, 15:31] = pattern
    screen[45:59, 95:111] = pattern
    screen_path = tmp_path / "screen.png"
    template = tmp_path / "map__shimen__1x.png"
    cv2.imwrite(str(screen_path), screen)
    cv2.imwrite(str(template), pattern)
    snapshot = ScreenRecognizer(ocr=lambda _: "当前地图", template_dir=tmp_path).classify(screen_path)
    assert snapshot.state is ScreenState.UNKNOWN
    assert snapshot.targets == {}

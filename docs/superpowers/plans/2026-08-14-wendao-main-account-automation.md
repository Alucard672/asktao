# 问道主号前台辅助脚本 Implementation Plan

**Date:** 2026-08-14

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建一个仅控制 macOS 可见《问道》窗口的主号辅助脚本，自动推进主线、跳过剧情、等待自动战斗，并在卡等级时执行安全的师门、日常或巡逻练级；异常时暂停并发送 iMessage。

**Architecture:** 使用 Python 包将窗口会话、视觉识别、安全策略、任务状态机、通知和 CLI 分开。视觉层只产生带置信度的状态快照，状态机只产生动作意图，安全层验证前置条件后才允许窗口会话点击；所有核心逻辑先通过离线截图和伪会话测试。

**Tech Stack:** Python 3.11+、PyObjC（Quartz/AppKit/Vision）、OpenCV、Pillow、PyYAML、pytest、macOS Messages/AppleScript。

---

## 文件结构

- `pyproject.toml`：包元数据、运行依赖、测试配置和 CLI 入口。
- `src/wendao_bot/models.py`：屏幕状态、动作、任务优先级和暂停原因等共享类型。
- `src/wendao_bot/config.py`：YAML 配置读取、窗口约束和安全默认值。
- `src/wendao_bot/session.py`：定位唯一游戏窗口、截图、受控点击和窗口指纹。
- `src/wendao_bot/recognizer.py`：模板匹配、OCR 文本读取和高层屏幕分类。
- `src/wendao_bot/safety.py`：动作前置条件、禁止页面和连续失败控制。
- `src/wendao_bot/tasks.py`：主线、师门、日常和练级处理器。
- `src/wendao_bot/orchestrator.py`：状态机循环、优先级、超时和恢复。
- `src/wendao_bot/notifier.py`：iMessage 通知和 dry-run。
- `src/wendao_bot/storage.py`：事件日志、状态快照和截图轮转。
- `src/wendao_bot/cli.py`：observe、run、pause、resume、stop 和通知测试命令。
- `config/default.yaml`：窗口尺寸、识别阈值、超时、白名单和通知目标。
- `assets/templates/README.md`：模板图片命名、裁剪和采集规则。
- `tests/fixtures/screens/README.md`：离线截图样本要求，不提交含私人聊天的截图。
- `tests/`：各模块单元测试和端到端伪会话测试。

### Task 1: 建立 Python 包与共享领域类型

**Files:**
- Create: `pyproject.toml`
- Create: `src/wendao_bot/__init__.py`
- Create: `src/wendao_bot/models.py`
- Create: `tests/test_models.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_models.py
from wendao_bot.models import Action, ActionKind, ScreenSnapshot, ScreenState


def test_snapshot_rejects_invalid_confidence() -> None:
    try:
        ScreenSnapshot(state=ScreenState.MAP, confidence=1.2, text="", image_path="x.png")
    except ValueError as exc:
        assert "confidence" in str(exc)
    else:
        raise AssertionError("invalid confidence must fail")


def test_action_is_data_only() -> None:
    action = Action(kind=ActionKind.CLICK, target="main_quest", expected=ScreenState.AUTO_PATH)
    assert action.target == "main_quest"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_models.py -v`

Expected: FAIL，提示 `wendao_bot` 或 `ScreenSnapshot` 不存在。

- [ ] **Step 3: 添加包配置和最小领域模型**

```toml
# pyproject.toml
[build-system]
requires = ["setuptools>=75", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "wendao-foreground-helper"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
  "opencv-python>=4.10,<5",
  "Pillow>=10,<12",
  "pyobjc-framework-Quartz>=10,<12",
  "pyobjc-framework-Vision>=10,<12",
  "pyobjc-framework-Cocoa>=10,<12",
  "PyYAML>=6,<7",
]

[project.optional-dependencies]
test = ["pytest>=8,<9"]

[project.scripts]
wendao-bot = "wendao_bot.cli:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
```

```python
# src/wendao_bot/models.py
from dataclasses import dataclass, field
from enum import Enum, auto


class ScreenState(Enum):
    MAP = auto()
    NPC_OPTIONS = auto()
    DIALOGUE = auto()
    AUTO_PATH = auto()
    BATTLE = auto()
    REWARD = auto()
    ACTIVITY_LIST = auto()
    BLOCKED_CHOICE = auto()
    DISCONNECTED = auto()
    CAPTCHA = auto()
    DEAD = auto()
    BAG_FULL = auto()
    PAYMENT = auto()
    UNKNOWN = auto()


class ActionKind(Enum):
    CLICK = auto()
    WAIT = auto()
    PAUSE = auto()


@dataclass(frozen=True)
class ScreenSnapshot:
    state: ScreenState
    confidence: float
    text: str
    image_path: str
    targets: dict[str, tuple[int, int]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")


@dataclass(frozen=True)
class Action:
    kind: ActionKind
    target: str | None = None
    expected: ScreenState | None = None
    wait_seconds: float = 0.0
```

`src/wendao_bot/__init__.py` 只定义 `__version__ = "0.1.0"`。

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_models.py -v`

Expected: 2 passed。

- [ ] **Step 5: 提交**

```bash
git add pyproject.toml src/wendao_bot tests/test_models.py
git commit -m "feat: add automation domain models"
```

### Task 2: 配置加载与安全默认值

**Files:**
- Create: `config/default.yaml`
- Create: `src/wendao_bot/config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_config.py
from pathlib import Path
from wendao_bot.config import load_config


def test_load_config_has_safe_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("window:\n  title: 问道\n", encoding="utf-8")
    config = load_config(path)
    assert config.min_confidence >= 0.85
    assert config.max_unchanged_actions == 3
    assert config.imessage_recipient == "wendao-owner@example.com"
    assert "payment" in config.blocked_states
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_config.py -v`

Expected: FAIL，提示 `wendao_bot.config` 不存在。

- [ ] **Step 3: 实现不可变配置对象和合并规则**

```python
# src/wendao_bot/config.py
from dataclasses import dataclass
from pathlib import Path
import yaml


@dataclass(frozen=True)
class BotConfig:
    window_title: str
    width: int
    height: int
    min_confidence: float
    max_unchanged_actions: int
    action_timeout_seconds: float
    battle_timeout_seconds: float
    imessage_recipient: str
    blocked_states: frozenset[str]
    daily_whitelist: tuple[str, ...]


DEFAULTS = {
    "window": {"title": "问道", "width": 886, "height": 672},
    "safety": {"min_confidence": 0.88, "max_unchanged_actions": 3},
    "timeouts": {"action": 12.0, "battle": 120.0},
    "notification": {"recipient": "wendao-owner@example.com"},
    "blocked_states": ["payment", "captcha", "blocked_choice", "dead", "bag_full", "disconnected"],
    "daily_whitelist": ["师门", "除暴", "修行"],
}


def _merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, value in override.items():
        result[key] = _merge(result.get(key, {}), value) if isinstance(value, dict) else value
    return result


def load_config(path: Path) -> BotConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    data = _merge(DEFAULTS, raw)
    return BotConfig(
        window_title=data["window"]["title"],
        width=int(data["window"]["width"]),
        height=int(data["window"]["height"]),
        min_confidence=float(data["safety"]["min_confidence"]),
        max_unchanged_actions=int(data["safety"]["max_unchanged_actions"]),
        action_timeout_seconds=float(data["timeouts"]["action"]),
        battle_timeout_seconds=float(data["timeouts"]["battle"]),
        imessage_recipient=data["notification"]["recipient"],
        blocked_states=frozenset(data["blocked_states"]),
        daily_whitelist=tuple(data["daily_whitelist"]),
    )
```

`config/default.yaml` 写入与 `DEFAULTS` 相同的可编辑字段，并加注释说明第一版只支持 886×672 窗口。

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_config.py -v`

Expected: 1 passed。

- [ ] **Step 5: 提交**

```bash
git add config/default.yaml src/wendao_bot/config.py tests/test_config.py
git commit -m "feat: add safe automation configuration"
```

### Task 3: 实现窗口会话与只观察截图

**Files:**
- Create: `src/wendao_bot/session.py`
- Create: `tests/test_session.py`

- [ ] **Step 1: 用伪后端写失败测试**

```python
# tests/test_session.py
import pytest
from wendao_bot.session import GameSession, WindowInfo


class FakeBackend:
    def list_windows(self):
        return [WindowInfo(7, "问道", 0, 0, 886, 672)]

    def capture(self, window_id):
        return b"png"

    def click(self, x, y):
        self.clicked = (x, y)


def test_click_requires_matching_window_geometry() -> None:
    backend = FakeBackend()
    session = GameSession(backend, "问道", 886, 672)
    session.click(100, 200)
    assert backend.clicked == (100, 200)


def test_multiple_windows_are_rejected() -> None:
    backend = FakeBackend()
    backend.list_windows = lambda: [
        WindowInfo(7, "问道", 0, 0, 886, 672),
        WindowInfo(8, "问道", 0, 0, 886, 672),
    ]
    with pytest.raises(RuntimeError, match="exactly one"):
        GameSession(backend, "问道", 886, 672).capture()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_session.py -v`

Expected: FAIL，提示 `GameSession` 不存在。

- [ ] **Step 3: 实现后端协议、窗口校验和 Quartz 后端**

`session.py` 定义 `WindowInfo` 数据类、`WindowBackend` Protocol、`GameSession.capture()` 和 `GameSession.click()`。Quartz 后端使用 `CGWindowListCopyWindowInfo` 过滤标题，使用 `CGWindowListCreateImage` 截取窗口，使用 `CGEventCreateMouseEvent` 发送单次左键按下/抬起。`GameSession` 每次截图和点击前都重新解析唯一窗口，并严格校验宽高；坐标必须落在窗口边界内。

核心校验代码必须为：

```python
def _resolve(self) -> WindowInfo:
    matches = [w for w in self.backend.list_windows() if w.title == self.title]
    if len(matches) != 1:
        raise RuntimeError("expected exactly one matching game window")
    window = matches[0]
    if (window.width, window.height) != (self.width, self.height):
        raise RuntimeError("game window geometry changed")
    return window
```

- [ ] **Step 4: 运行单元测试**

Run: `python -m pytest tests/test_session.py -v`

Expected: 2 passed。

- [ ] **Step 5: 添加只观察冒烟命令并验证不点击**

在 `session.py` 添加 `capture_to(path: Path) -> Path`，只保存截图。运行：

`python -c "from pathlib import Path; from wendao_bot.session import QuartzBackend,GameSession; GameSession(QuartzBackend(),'问道',886,672).capture_to(Path('runtime/observe.png'))"`

Expected: 创建 `runtime/observe.png`，游戏无输入动作。

- [ ] **Step 6: 提交**

```bash
git add src/wendao_bot/session.py tests/test_session.py
git commit -m "feat: add validated macos game session"
```

### Task 4: 视觉识别与禁止页面分类

**Files:**
- Create: `src/wendao_bot/recognizer.py`
- Create: `assets/templates/README.md`
- Create: `tests/fixtures/screens/README.md`
- Create: `tests/test_recognizer.py`

- [ ] **Step 1: 写基于合成图的失败测试**

```python
# tests/test_recognizer.py
from pathlib import Path
from PIL import Image, ImageDraw
from wendao_bot.models import ScreenState
from wendao_bot.recognizer import ScreenRecognizer


def test_payment_keyword_is_blocked(tmp_path: Path) -> None:
    image = Image.new("RGB", (886, 672), "black")
    path = tmp_path / "screen.png"
    image.save(path)
    recognizer = ScreenRecognizer(ocr=lambda _: "充值 金元宝 购买")
    snapshot = recognizer.classify(path)
    assert snapshot.state is ScreenState.PAYMENT


def test_unknown_when_no_signal(tmp_path: Path) -> None:
    path = tmp_path / "blank.png"
    Image.new("RGB", (886, 672), "black").save(path)
    snapshot = ScreenRecognizer(ocr=lambda _: "").classify(path)
    assert snapshot.state is ScreenState.UNKNOWN
    assert snapshot.confidence < 0.88
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_recognizer.py -v`

Expected: FAIL，提示 `ScreenRecognizer` 不存在。

- [ ] **Step 3: 实现模板注册、Vision OCR 适配器和分类优先级**

分类必须优先检查禁止页面，再检查剧情、战斗、寻路、NPC、奖励和普通地图。关键词至少包括：

```python
BLOCKED_KEYWORDS = {
    ScreenState.PAYMENT: ("充值", "金元宝", "购买", "支付"),
    ScreenState.CAPTCHA: ("验证码", "安全验证", "滑动验证"),
    ScreenState.BLOCKED_CHOICE: ("加点", "门派选择", "确认消耗", "属性分配"),
    ScreenState.DISCONNECTED: ("重新连接", "登录", "网络断开"),
    ScreenState.DEAD: ("死亡", "原地复活", "回城复活"),
    ScreenState.BAG_FULL: ("背包已满", "空间不足"),
}
```

模板命中输出标准化目标名，如 `skip_dialogue`、`main_quest`、`npc_main_option`、`equip`、`claim`。OCR 适配器通过 Vision 的 `VNRecognizeTextRequest` 返回简体中文文本；测试可注入 lambda，避免依赖系统 OCR。

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_recognizer.py -v`

Expected: 2 passed。

- [ ] **Step 5: 记录模板采集规范**

`assets/templates/README.md` 明确：模板只能从用户自己的游戏截图裁剪；保存 PNG；命名为 `<state>__<target>__<scale>.png`；不得包含聊天内容、账号信息或其他玩家个人信息。`tests/fixtures/screens/README.md` 列出 MAP、DIALOGUE、BATTLE、AUTO_PATH、NPC_OPTIONS、REWARD 和所有禁止页面的最小样本集。

- [ ] **Step 6: 提交**

```bash
git add src/wendao_bot/recognizer.py assets/templates tests/fixtures tests/test_recognizer.py
git commit -m "feat: classify game screens safely"
```

### Task 5: 安全门与动作验证

**Files:**
- Create: `src/wendao_bot/safety.py`
- Create: `tests/test_safety.py`

- [ ] **Step 1: 写失败测试覆盖禁止点击和重复失败**

```python
# tests/test_safety.py
import pytest
from wendao_bot.models import Action, ActionKind, ScreenSnapshot, ScreenState
from wendao_bot.safety import SafetyGuard, UnsafeAction


def snap(state, confidence=0.99, targets=None):
    return ScreenSnapshot(state, confidence, "", "x.png", targets or {})


def test_payment_never_allows_click() -> None:
    guard = SafetyGuard(0.88, 3)
    with pytest.raises(UnsafeAction):
        guard.authorize(Action(ActionKind.CLICK, "claim"), snap(ScreenState.PAYMENT, targets={"claim": (1, 1)}))


def test_target_must_exist() -> None:
    guard = SafetyGuard(0.88, 3)
    with pytest.raises(UnsafeAction, match="target"):
        guard.authorize(Action(ActionKind.CLICK, "main_quest"), snap(ScreenState.MAP))


def test_three_unchanged_actions_pause() -> None:
    guard = SafetyGuard(0.88, 3)
    for _ in range(2):
        guard.record_transition("same", "same")
    with pytest.raises(UnsafeAction, match="unchanged"):
        guard.record_transition("same", "same")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_safety.py -v`

Expected: FAIL，提示 `SafetyGuard` 不存在。

- [ ] **Step 3: 实现禁止状态、置信度、目标和变化验证**

`SafetyGuard.authorize()` 只返回已验证的绝对坐标，不执行点击。禁止状态集合固定包含 PAYMENT、CAPTCHA、BLOCKED_CHOICE、DISCONNECTED、DEAD、BAG_FULL、UNKNOWN；低于阈值和目标缺失同样抛出 `UnsafeAction`。`record_transition()` 用截图哈希或状态签名检测连续无变化。

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_safety.py -v`

Expected: 3 passed。

- [ ] **Step 5: 提交**

```bash
git add src/wendao_bot/safety.py tests/test_safety.py
git commit -m "feat: gate all game input through safety checks"
```

### Task 6: 主线、师门、日常和巡逻任务决策

**Files:**
- Create: `src/wendao_bot/tasks.py`
- Create: `tests/test_tasks.py`

- [ ] **Step 1: 写任务优先级失败测试**

```python
# tests/test_tasks.py
from wendao_bot.models import ScreenSnapshot, ScreenState
from wendao_bot.tasks import Progress, TaskPlanner


def snapshot(text, targets):
    return ScreenSnapshot(ScreenState.MAP, 0.99, text, "x.png", targets)


def test_main_quest_has_priority() -> None:
    planner = TaskPlanner(("师门", "除暴", "修行"))
    action = planner.next_action(snapshot("主线 浮生若梦", {"main_quest": (700, 190)}), Progress(level=16))
    assert action.target == "main_quest"


def test_blocked_main_uses_shimen_before_daily() -> None:
    planner = TaskPlanner(("师门", "除暴", "修行"))
    progress = Progress(level=16, main_blocked_by_level=True, shimen_completed=0)
    action = planner.next_action(snapshot("主线 20级开启 师门 1/10", {"shimen": (700, 245)}), progress)
    assert action.target == "shimen"


def test_shimen_stops_at_ten() -> None:
    planner = TaskPlanner(("师门", "除暴"))
    progress = Progress(level=20, main_blocked_by_level=True, shimen_completed=10)
    action = planner.next_action(snapshot("除暴 可参加", {"daily_除暴": (600, 300)}), progress)
    assert action.target == "daily_除暴"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_tasks.py -v`

Expected: FAIL，提示 `TaskPlanner` 不存在。

- [ ] **Step 3: 实现纯函数式任务规划器**

`Progress` 保存等级、主线是否卡等级、师门完成数和当日白名单日常完成集合。`TaskPlanner.next_action()` 的确定顺序必须为：剧情跳过、NPC 主线选项、任务奖励、可继续主线、未完成师门、白名单日常、巡逻、暂停。购买、答题、组队和不在白名单内的活动不得返回点击动作。

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_tasks.py -v`

Expected: 3 passed。

- [ ] **Step 5: 提交**

```bash
git add src/wendao_bot/tasks.py tests/test_tasks.py
git commit -m "feat: plan safe quest progression"
```

### Task 7: iMessage 通知与运行状态存储

**Files:**
- Create: `src/wendao_bot/notifier.py`
- Create: `src/wendao_bot/storage.py`
- Create: `tests/test_notifier.py`
- Create: `tests/test_storage.py`

- [ ] **Step 1: 写 dry-run 通知失败测试**

```python
# tests/test_notifier.py
from wendao_bot.notifier import IMessageNotifier, PauseNotice


def test_dry_run_does_not_execute_osascript() -> None:
    calls = []
    notifier = IMessageNotifier("wendao-owner@example.com", runner=calls.append, dry_run=True)
    text = notifier.send(PauseNotice("验证码", 16, "主线", "/tmp/screen.png", "2026-08-13 12:00:00"))
    assert "验证码" in text
    assert calls == []
```

```python
# tests/test_storage.py
from pathlib import Path
from wendao_bot.storage import RuntimeStore


def test_screenshot_rotation_keeps_limit(tmp_path: Path) -> None:
    store = RuntimeStore(tmp_path, screenshot_limit=2)
    for index in range(3):
        store.save_screenshot(f"image-{index}".encode(), f"s{index}.png")
    assert len(list((tmp_path / "screens").glob("*.png"))) == 2
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_notifier.py tests/test_storage.py -v`

Expected: FAIL，提示通知或存储模块不存在。

- [ ] **Step 3: 实现安全 AppleScript 参数传递和截图轮转**

`IMessageNotifier` 不拼接 shell 字符串；它调用：

```python
args = [
    "osascript", "-e",
    'on run argv\nset recipientId to item 1 of argv\nset bodyText to item 2 of argv\ntell application "Messages" to send bodyText to buddy recipientId of service 1\nend run',
    recipient,
    body,
]
subprocess.run(args, check=True, capture_output=True, text=True)
```

`RuntimeStore` 使用 JSON Lines 保存事件，使用临时文件加原子替换保存当前状态，并按修改时间保留最近 N 张截图。它拒绝日志字段名 `password`、`captcha`、`token` 和 `chat_text`。

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_notifier.py tests/test_storage.py -v`

Expected: 2 passed。

- [ ] **Step 5: 提交**

```bash
git add src/wendao_bot/notifier.py src/wendao_bot/storage.py tests/test_notifier.py tests/test_storage.py
git commit -m "feat: add pause notifications and runtime storage"
```

### Task 8: 编排循环、暂停恢复和 CLI

**Files:**
- Create: `src/wendao_bot/orchestrator.py`
- Create: `src/wendao_bot/cli.py`
- Create: `tests/test_orchestrator.py`
- Create: `tests/test_cli.py`

- [ ] **Step 1: 写伪会话端到端失败测试**

```python
# tests/test_orchestrator.py
from wendao_bot.models import ScreenSnapshot, ScreenState
from wendao_bot.orchestrator import Orchestrator, RunStatus


def test_unknown_screen_pauses_without_clicking(fake_dependencies) -> None:
    fake_dependencies.recognizer.snapshots = [
        ScreenSnapshot(ScreenState.UNKNOWN, 0.2, "", "unknown.png")
    ]
    runner = Orchestrator(**fake_dependencies.as_kwargs())
    runner.step()
    assert runner.status is RunStatus.PAUSED
    assert fake_dependencies.session.clicks == []
    assert fake_dependencies.notifier.messages


def test_dialogue_skip_then_reobserve(fake_dependencies) -> None:
    fake_dependencies.recognizer.snapshots = [
        ScreenSnapshot(ScreenState.DIALOGUE, 0.99, "剧情", "a.png", {"skip_dialogue": (790, 76)}),
        ScreenSnapshot(ScreenState.MAP, 0.99, "主线", "b.png", {"main_quest": (766, 191)}),
    ]
    runner = Orchestrator(**fake_dependencies.as_kwargs())
    runner.step()
    assert fake_dependencies.session.clicks == [(790, 76)]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_orchestrator.py -v`

Expected: FAIL，提示 `Orchestrator` 不存在。

- [ ] **Step 3: 实现单步循环和暂停文件**

`Orchestrator.step()` 必须按以下顺序运行：截图并保存 → 识别 → 检查外部 stop/pause 文件 → 规划动作 → SafetyGuard 授权 → 执行至多一个输入动作 → 等待最小稳定时间 → 再截图验证变化。任何异常调用统一 `_pause(reason, snapshot)`，保存状态并通知。

`cli.py` 提供：

```text
wendao-bot observe --config config/default.yaml
wendao-bot run --config config/default.yaml
wendao-bot pause
wendao-bot resume
wendao-bot stop
wendao-bot notify-test --dry-run
```

`run` 默认先执行 30 秒 observe-only 预检；只有窗口、模板和 OCR 均通过才开始点击。SIGINT 和 SIGTERM 只设置 stop 标志，不在信号处理器内发送点击。

- [ ] **Step 4: 添加 CLI 失败测试并实现参数解析**

```python
# tests/test_cli.py
from wendao_bot.cli import build_parser


def test_run_command_defaults_to_observe_preflight() -> None:
    args = build_parser().parse_args(["run"])
    assert args.preflight_seconds == 30
```

Run: `python -m pytest tests/test_cli.py tests/test_orchestrator.py -v`

Expected: 全部通过。

- [ ] **Step 5: 提交**

```bash
git add src/wendao_bot/orchestrator.py src/wendao_bot/cli.py tests/test_orchestrator.py tests/test_cli.py
git commit -m "feat: orchestrate safe foreground automation"
```

### Task 9: 采集用户窗口模板并完成真实只观察验证

**Files:**
- Create: `scripts/capture_template.py`
- Create: `assets/templates/*.png`
- Create: `tests/fixtures/screens/*.png`
- Modify: `assets/templates/README.md`

- [ ] **Step 1: 实现交互式裁剪脚本的坐标验证测试**

```python
# tests/test_capture_template.py
import pytest
from scripts.capture_template import validate_box


def test_crop_box_must_be_inside_window() -> None:
    assert validate_box((10, 20, 30, 40), 886, 672) == (10, 20, 30, 40)
    with pytest.raises(ValueError):
        validate_box((0, 0, 900, 40), 886, 672)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_capture_template.py -v`

Expected: FAIL，提示采集脚本不存在。

- [ ] **Step 3: 实现模板采集脚本**

脚本仅从当前窗口截图中裁剪用户明确指定的矩形区域，保存前显示路径和尺寸；拒绝裁剪任务聊天区、角色编号区和任何 OCR 判定包含邮箱/手机号的区域。提供 `--dry-run` 只输出裁剪信息。

- [ ] **Step 4: 采集并标注最小模板集**

从当前游戏窗口采集：`main_quest`、`skip_dialogue`、`npc_main_option`、`auto_path`、`battle_auto`、`claim`、`equip`。为 PAYMENT、CAPTCHA、BLOCKED_CHOICE、DISCONNECTED、DEAD、BAG_FULL 使用合成或人工脱敏样本，不制造真实付费或验证码操作。

- [ ] **Step 5: 运行只观察验证**

Run: `wendao-bot observe --config config/default.yaml`

Expected: 连续 30 秒输出状态和置信度，点击次数为 0；所有识别置信度低于阈值的画面标记 UNKNOWN。

- [ ] **Step 6: 提交**

```bash
git add scripts/capture_template.py assets/templates tests/fixtures/screens tests/test_capture_template.py
git commit -m "test: add sanitized game recognition fixtures"
```

### Task 10: 单步真实验证、通知授权和完整回归

**Files:**
- Modify: `README.md`
- Create: `tests/test_end_to_end.py`

- [ ] **Step 1: 写离线完整流程测试**

```python
# tests/test_end_to_end.py
def test_main_to_shimen_to_main(fake_game):
    fake_game.play("main_blocked_level_15", "shimen_1", "shimen_10", "main_available")
    result = fake_game.run_until_idle()
    assert result.targets[:2] == ["shimen", "shimen_task"]
    assert result.progress.shimen_completed == 10
    assert result.targets[-1] == "main_quest"
    assert not result.forbidden_clicks
```

- [ ] **Step 2: 运行完整测试确认失败，再补齐夹具**

Run: `python -m pytest tests/test_end_to_end.py -v`

Expected: 首次 FAIL，提示 `fake_game` fixture 不存在；在 `tests/conftest.py` 添加按截图序列驱动的伪游戏夹具后 PASS。

- [ ] **Step 3: 运行全量质量检查**

Run: `python -m pytest -v`

Expected: 所有测试通过。

Run: `python -m compileall -q src scripts`

Expected: 退出码 0，无输出。

- [ ] **Step 4: 执行 iMessage dry-run 与用户授权测试**

先运行：`wendao-bot notify-test --dry-run`

Expected: 控制台显示将发送给 `wendao-owner@example.com` 的文本，但不打开 Messages。

正式测试发送属于外部通信；在执行前向用户展示完整消息并取得确认。确认后运行一次 `wendao-bot notify-test`，预期 Messages 成功发送测试提醒。

- [ ] **Step 5: 执行真实单步模式**

先运行 observe 30 秒，再运行 `wendao-bot run --single-step`。每一步向用户展示识别状态、置信度和目标；只有在用户检查通过后才进入连续模式。不得在付费、验证码、加点、死亡或未知页面上继续。

- [ ] **Step 6: 完成 README**

README 必须包含安装、macOS 辅助功能/屏幕录制权限、配置、observe、单步、连续运行、暂停恢复、紧急停止、日志位置、iMessage 权限、已知限制和双开未支持说明。

- [ ] **Step 7: 最终验证并提交**

Run: `python -m pytest -v && python -m compileall -q src scripts && git status --short`

Expected: 测试全通过、编译成功，仅 README 和端到端测试为预期修改。

```bash
git add README.md tests/test_end_to_end.py tests/conftest.py
git commit -m "docs: finish safe automation workflow"
```

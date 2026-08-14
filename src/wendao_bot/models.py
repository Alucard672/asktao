from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Mapping


class ScreenState(Enum):
    MAP = "map"
    NPC_OPTIONS = "npc_options"
    DIALOGUE = "dialogue"
    AUTO_PATH = "auto_path"
    BATTLE = "battle"
    REWARD = "reward"
    ACTIVITY_LIST = "activity_list"
    BLOCKED_CHOICE = "blocked_choice"
    DISCONNECTED = "disconnected"
    CAPTCHA = "captcha"
    DEAD = "dead"
    BAG_FULL = "bag_full"
    PAYMENT = "payment"
    UNKNOWN = "unknown"


class ActionKind(Enum):
    CLICK = "click"
    KEY = "key"
    WAIT = "wait"
    PAUSE = "pause"


@dataclass(frozen=True)
class ScreenSnapshot:
    state: ScreenState
    confidence: float
    text: str
    image_path: str
    targets: Mapping[str, tuple[int, int]] = field(default_factory=dict)
    evidence: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        object.__setattr__(self, "targets", MappingProxyType(dict(self.targets)))
        object.__setattr__(self, "evidence", frozenset(self.evidence))


@dataclass(frozen=True)
class Action:
    kind: ActionKind
    target: str | None = None
    expected: ScreenState | None = None
    wait_seconds: float = 0.0
    allowed_expected: frozenset[ScreenState] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        allowed = frozenset(self.allowed_expected)
        if self.expected is not None:
            allowed = allowed | {self.expected}
        object.__setattr__(self, "allowed_expected", allowed)

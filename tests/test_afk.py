"""Tests for the OCR-word AFK loop."""

from __future__ import annotations

from pathlib import Path
import threading

import pytest

from wendao_bot.afk import AFK_CLICK_TEXTS, AfkLoop, AfkStatus
from wendao_bot.recognizer import merge_adjacent_words

KEYMAP = {"story_skip": "`", "task_pathfind": "t"}


class FakeSession:
    def __init__(self, errors=()) -> None:
        self.errors = list(errors)
        self.always_error = None
        self.captured = []
        self.clicks = []
        self.keys = []

    def capture_to(self, path):
        if self.always_error is not None:
            raise self.always_error
        if self.errors:
            error = self.errors.pop(0)
            if error is not None:
                raise error
        destination = Path(path)
        self.captured.append(destination)
        return destination

    def click(self, x, y):
        self.clicks.append((x, y))

    def send_key(self, key):
        self.keys.append(key)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class CountingSleeper:
    def __init__(self, stop_event=None, stop_after=None) -> None:
        self.calls = 0
        self.stop_event = stop_event
        self.stop_after = stop_after

    def __call__(self, seconds) -> None:
        self.calls += 1
        if self.stop_after is not None and self.calls >= self.stop_after:
            self.stop_event.set()


def word(text, x, y, width=24, height=24):
    return (text, x, y, width, height)


def build_loop(session, words=(), **kwargs):
    kwargs.setdefault("ocr_words", lambda path: list(words))
    kwargs.setdefault("keymap", KEYMAP)
    kwargs.setdefault("sleeper", CountingSleeper())
    kwargs.setdefault("clock", FakeClock())
    ocr_words = kwargs.pop("ocr_words")
    return AfkLoop(session, ocr_words, **kwargs)


def test_merge_joins_words_split_on_one_row():
    merged = merge_adjacent_words([word("领", 100, 50, 20, 20), word("取", 126, 50, 20, 20)])
    assert merged == [("领取", 123, 60)]


def test_merge_keeps_different_rows_apart():
    merged = merge_adjacent_words([word("领", 100, 50, 20, 20), word("取", 100, 80, 20, 20)])
    assert [phrase for phrase, _x, _y in merged] == ["领", "取"]


def test_merge_respects_max_gap():
    merged = merge_adjacent_words([word("领", 100, 50, 20, 20), word("取", 140, 50, 20, 20)])
    assert [phrase for phrase, _x, _y in merged] == ["领", "取"]


def test_merge_chains_three_words():
    merged = merge_adjacent_words(
        [
            word("继", 10, 50, 20, 20),
            word("续", 34, 50, 20, 20),
            word("任务", 58, 50, 44, 20),
        ]
    )
    assert merged == [("继续任务", 56, 60)]


def test_step_blocked_keyword_pauses_without_clicking():
    session = FakeSession()
    loop = build_loop(session, words=[word("验证码", 300, 200, 72, 24), word("领取", 100, 100, 48, 24)])
    status = loop.step()
    assert status.state == "paused"
    assert status.reason == "captcha"
    assert status.last_action is None
    assert session.clicks == []
    assert session.keys == []


def test_step_blocked_keyword_detected_across_split_words():
    session = FakeSession()
    loop = build_loop(session, words=[word("验证", 300, 200, 48, 24), word("码", 350, 200, 24, 24)])
    status = loop.step()
    assert status.state == "paused"
    assert status.reason == "captcha"
    assert session.clicks == []


def test_step_payment_keyword_pauses():
    session = FakeSession()
    loop = build_loop(session, words=[word("充值", 500, 400, 48, 24)])
    status = loop.step()
    assert status.state == "paused"
    assert status.reason == "payment"


def test_step_login_substring_is_exempt():
    session = FakeSession()
    loop = build_loop(session, words=[word("英雄登录榜", 300, 200, 120, 24)])
    status = loop.step()
    assert status.state == "running"
    assert status.last_action == "key:task_pathfind"


def test_step_clicks_matched_text_center():
    session = FakeSession()
    loop = build_loop(session, words=[word("领取", 100, 200, 48, 24)])
    status = loop.step()
    assert status.state == "running"
    assert status.last_action == "click:领取"
    assert session.clicks == [(124, 212)]
    assert session.keys == []


def test_step_click_priority_prefers_earlier_text():
    session = FakeSession()
    loop = build_loop(
        session,
        words=[word("领取", 100, 100, 48, 24), word("领取奖励", 400, 300, 96, 24)],
    )
    status = loop.step()
    assert status.last_action == "click:领取奖励"
    assert session.clicks == [(448, 312)]


def test_step_requires_exact_phrase_match():
    session = FakeSession()
    loop = build_loop(session, words=[word("一键领取", 100, 200, 96, 24)])
    status = loop.step()
    assert status.last_action == "key:task_pathfind"
    assert session.clicks == []


def test_step_story_hint_sends_skip_key():
    session = FakeSession()
    loop = build_loop(session, words=[word("跳过剧情", 500, 20, 96, 24)])
    status = loop.step()
    assert status.last_action == "key:story_skip"
    assert session.keys == ["`"]
    assert session.clicks == []


def test_step_takes_at_most_one_action():
    session = FakeSession()
    loop = build_loop(
        session,
        words=[word("领取", 100, 200, 48, 24), word("跳过剧情", 500, 20, 96, 24)],
    )
    status = loop.step()
    assert status.last_action == "click:领取"
    assert session.clicks == [(124, 212)]
    assert session.keys == []


def test_step_pathfind_respects_cooldown():
    session = FakeSession()
    clock = FakeClock()
    loop = build_loop(session, words=[], clock=clock)
    first = loop.step()
    assert first.last_action == "key:task_pathfind"
    clock.now = 5.0
    second = loop.step()
    assert second.last_action is None
    assert session.keys == ["t"]
    clock.now = 25.0
    third = loop.step()
    assert third.last_action == "key:task_pathfind"
    assert session.keys == ["t", "t"]


def test_step_rotates_screenshot_slots():
    session = FakeSession()
    clock = FakeClock()
    loop = build_loop(session, words=[], clock=clock)
    for cycle in range(21):
        clock.now = cycle * 100.0
        loop.step()
    names = [path.name for path in session.captured]
    assert names[0] == "afk-000000.png"
    assert names[19] == "afk-000019.png"
    assert names[20] == "afk-000000.png"


def test_run_pauses_on_session_error_and_recovers():
    session = FakeSession(
        errors=[RuntimeError("window occluded"), RuntimeError("window occluded")]
    )
    stop_event = threading.Event()
    statuses: list[AfkStatus] = []
    loop = build_loop(
        session,
        words=[],
        stop_event=stop_event,
        on_status=statuses.append,
        sleeper=CountingSleeper(stop_event=stop_event, stop_after=4),
    )
    final = loop.run()
    assert final.state == "stopped"
    assert final.reason == "stop requested"
    assert any(
        status.state == "paused" and "window occluded" in status.reason
        for status in statuses
    )
    assert any(status.state == "running" for status in statuses)
    assert session.captured


def test_run_stops_after_ten_identical_errors():
    session = FakeSession()
    session.always_error = RuntimeError("window occluded")
    sleeper = CountingSleeper()
    statuses: list[AfkStatus] = []
    loop = build_loop(session, words=[], sleeper=sleeper, on_status=statuses.append)
    final = loop.run()
    assert final.state == "stopped"
    assert "window occluded" in final.reason
    assert sleeper.calls == 9
    assert not loop.stop_event.is_set()
    assert statuses[-1].state == "stopped"


def test_run_alternating_errors_do_not_trigger_stop():
    session = FakeSession()
    errors = [RuntimeError("error a"), RuntimeError("error b")]
    calls = {"count": 0}

    def capture_to(path):
        error = errors[calls["count"] % 2]
        calls["count"] += 1
        raise error

    session.capture_to = capture_to
    stop_event = threading.Event()
    loop = build_loop(
        session,
        words=[],
        stop_event=stop_event,
        sleeper=CountingSleeper(stop_event=stop_event, stop_after=25),
    )
    final = loop.run()
    assert final.state == "stopped"
    assert final.reason == "stop requested"
    assert calls["count"] >= 25


def test_run_stops_immediately_when_stop_event_preset():
    session = FakeSession()
    stop_event = threading.Event()
    stop_event.set()
    sleeper = CountingSleeper()
    loop = build_loop(session, words=[], stop_event=stop_event, sleeper=sleeper)
    final = loop.run()
    assert final.state == "stopped"
    assert session.captured == []
    assert sleeper.calls == 0


def test_run_emits_status_only_on_change():
    session = FakeSession()
    session.always_error = RuntimeError("window occluded")
    stop_event = threading.Event()
    statuses: list[AfkStatus] = []
    loop = build_loop(
        session,
        words=[],
        stop_event=stop_event,
        on_status=statuses.append,
        sleeper=CountingSleeper(stop_event=stop_event, stop_after=3),
    )
    loop.run()
    assert [status.state for status in statuses] == ["paused", "stopped"]


def test_constructor_requires_keymap_entries():
    with pytest.raises(ValueError):
        build_loop(FakeSession(), keymap={"story_skip": "`"})


def test_constructor_rejects_empty_click_texts():
    with pytest.raises(ValueError):
        build_loop(FakeSession(), click_texts=())


def test_default_click_texts_order():
    assert AFK_CLICK_TEXTS[0] == "领取奖励"
    assert "领取" in AFK_CLICK_TEXTS

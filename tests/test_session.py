from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

from wendao_bot.session import GameSession, QuartzBackend, WindowInfo


class FakeBackend:
    def __init__(self, windows: list[WindowInfo], image: bytes = b"png") -> None:
        self.windows = windows
        self.image = image
        self.captures: list[int] = []
        self.clicks: list[tuple[int, int]] = []
        self.focus_result = True

    def list_windows(self) -> list[WindowInfo]:
        return self.windows

    def capture(self, target: WindowInfo) -> bytes:
        self.captures.append(target.window_id)
        return self.image

    def focus_and_click(self, target: WindowInfo, x: int, y: int) -> None:
        if not self.focus_result:
            raise RuntimeError("focus failed")
        current = next(
            (item for item in self.windows if item.window_id == target.window_id), None
        )
        if current != target:
            raise RuntimeError("window identity or bounds changed")
        self.clicks.append((target.x + x, target.y + y))


def window(
    *,
    window_id: int = 7,
    x: int = 100,
    y: int = 200,
    owner_name: str = "问道",
) -> WindowInfo:
    return WindowInfo(window_id, "Wendao", x, y, 800, 600, 42, owner_name)


def test_click_validates_window_and_applies_its_screen_offset() -> None:
    backend = FakeBackend([window(owner_name="Game Owner")])
    session = GameSession(
        backend, "Wendao", 800, 600, expected_owner_name="Game Owner"
    )

    session.click(25, 40)

    assert backend.clicks == [(125, 240)]


def test_default_expected_owner_rejects_wrong_owner_before_capture() -> None:
    backend = FakeBackend([window(owner_name="Game Owner")])
    session = GameSession(backend, "Wendao", 800, 600)

    with pytest.raises(RuntimeError, match="owner name"):
        session.capture()

    assert backend.captures == []


def test_explicit_expected_owner_rejects_wrong_owner_before_capture() -> None:
    backend = FakeBackend([window(owner_name="Game Owner")])
    session = GameSession(backend, "Wendao", 800, 600, expected_owner_name="问道")

    with pytest.raises(RuntimeError, match="owner name"):
        session.capture()

    assert backend.captures == []


def test_capture_rejects_multiple_exact_title_matches() -> None:
    backend = FakeBackend([window(window_id=1), window(window_id=2)])
    session = GameSession(backend, "Wendao", 800, 600)

    with pytest.raises(RuntimeError, match="exactly one"):
        session.capture()

    assert backend.captures == []


def test_capture_rejects_unexpected_window_geometry() -> None:
    bad = WindowInfo(7, "Wendao", 100, 200, 801, 600, 42, "Game Owner")
    backend = FakeBackend([bad])
    session = GameSession(backend, "Wendao", 800, 600)

    with pytest.raises(RuntimeError, match="geometry"):
        session.capture()

    assert backend.captures == []


def test_capture_rejects_window_without_owner_identity() -> None:
    backend = FakeBackend([WindowInfo(7, "Wendao", 100, 200, 800, 600)])
    session = GameSession(backend, "Wendao", 800, 600)

    with pytest.raises(RuntimeError, match="owner identity"):
        session.capture()

    assert backend.captures == []


@pytest.mark.parametrize("point", [(-1, 0), (0, -1), (800, 0), (0, 600)])
def test_click_rejects_coordinates_outside_the_client_area(
    point: tuple[int, int],
) -> None:
    backend = FakeBackend([window()])
    session = GameSession(backend, "Wendao", 800, 600)

    with pytest.raises(ValueError, match="bounds"):
        session.click(*point)

    assert backend.clicks == []


def test_click_rejects_owner_identity_change_after_initial_resolution() -> None:
    backend = FakeBackend([window()])
    session = GameSession(backend, "Wendao", 800, 600)
    session.capture()
    backend.windows = [
        WindowInfo(7, "Wendao", 100, 200, 800, 600, 99, "Impostor")
    ]

    with pytest.raises(RuntimeError, match="owner"):
        session.click(10, 10)

    assert backend.clicks == []


def test_click_rejects_pid_change_after_initial_resolution() -> None:
    backend = FakeBackend([window()])
    session = GameSession(backend, "Wendao", 800, 600)
    session.capture()
    backend.windows = [
        WindowInfo(7, "Wendao", 100, 200, 800, 600, 99, "问道")
    ]

    with pytest.raises(RuntimeError, match="identity"):
        session.click(10, 10)

    assert backend.clicks == []


def test_click_rejects_window_id_change_after_initial_resolution() -> None:
    backend = FakeBackend([window()])
    session = GameSession(backend, "Wendao", 800, 600)
    session.capture()
    backend.windows = [window(window_id=8)]

    with pytest.raises(RuntimeError, match="identity"):
        session.click(10, 10)

    assert backend.clicks == []


def test_click_does_not_post_when_backend_cannot_focus() -> None:
    backend = FakeBackend([window()])
    backend.focus_result = False
    session = GameSession(backend, "Wendao", 800, 600)

    with pytest.raises(RuntimeError, match="focus"):
        session.click(10, 10)

    assert backend.clicks == []


def test_capture_to_creates_parent_and_writes_captured_png(tmp_path: Path) -> None:
    backend = FakeBackend([window()], image=b"PNG DATA")
    session = GameSession(backend, "Wendao", 800, 600)
    destination = tmp_path / "new" / "capture.png"

    result = session.capture_to(destination)

    assert result == destination
    assert destination.read_bytes() == b"PNG DATA"
    assert backend.captures == [7]


def _install_capture_frameworks(
    monkeypatch: pytest.MonkeyPatch, *, pixels: tuple[int, int] = (800, 600), image=object()
) -> SimpleNamespace:
    calls = SimpleNamespace(options=None)

    class Bitmap:
        @classmethod
        def alloc(cls):
            return cls()

        def initWithCGImage_(self, received):
            if received is None:
                return None
            return self

        def pixelsWide(self):
            return pixels[0]

        def pixelsHigh(self):
            return pixels[1]

        def representationUsingType_properties_(self, kind, properties):
            return b"PNG"

    quartz = SimpleNamespace(
        CGRectNull=object(),
        kCGWindowListOptionIncludingWindow=2,
        kCGWindowImageBoundsIgnoreFraming=4,
        kCGWindowImageNominalResolution=8,
        CGWindowListCreateImage=lambda rect, option, window_id, flags: (
            setattr(calls, "options", flags) or image
        ),
    )
    appkit = SimpleNamespace(
        NSBitmapImageRep=Bitmap,
        NSBitmapImageFileTypePNG=1,
    )
    monkeypatch.setitem(sys.modules, "Quartz", quartz)
    monkeypatch.setitem(sys.modules, "AppKit", appkit)
    return calls


def test_quartz_capture_requests_nominal_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_capture_frameworks(monkeypatch)

    result = QuartzBackend().capture(window())

    assert result == b"PNG"
    assert calls.options == 12


def test_quartz_capture_rejects_retina_pixel_dimension_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_capture_frameworks(monkeypatch, pixels=(1600, 1200))

    with pytest.raises(RuntimeError, match="pixel dimensions"):
        QuartzBackend().capture(window())


def test_quartz_capture_rejects_missing_cgimage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_capture_frameworks(monkeypatch, image=None)

    with pytest.raises(RuntimeError, match="could not capture"):
        QuartzBackend().capture(window())


def test_quartz_focus_failure_posts_no_mouse_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    posted: list[object] = []

    class Application:
        def activateWithOptions_(self, options):
            return False

    appkit = SimpleNamespace(
        NSApplicationActivateIgnoringOtherApps=1,
        NSRunningApplication=SimpleNamespace(
            runningApplicationWithProcessIdentifier_=lambda pid: Application()
        ),
    )
    quartz = SimpleNamespace(
        CGEventPost=lambda tap, event: posted.append(event),
    )
    monkeypatch.setitem(sys.modules, "AppKit", appkit)
    monkeypatch.setitem(sys.modules, "Quartz", quartz)

    with pytest.raises(RuntimeError, match="focus"):
        QuartzBackend().focus_and_click(window(), 10, 10)

    assert posted == []


def test_higher_layer_overlay_covering_point_is_not_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = window()
    bounds = {"X": 100, "Y": 200, "Width": 800, "Height": 600}
    records = [
        {"number": 99, "layer": 10, "alpha": 1.0, "bounds": bounds},
        {
            "number": target.window_id,
            "layer": 0,
            "alpha": 1.0,
            "bounds": bounds,
            "title": target.title,
            "pid": target.owner_pid,
            "owner": target.owner_name,
        },
    ]
    quartz = SimpleNamespace(
        kCGWindowListOptionOnScreenOnly=1,
        kCGWindowListExcludeDesktopElements=2,
        kCGNullWindowID=0,
        kCGWindowLayer="layer",
        kCGWindowAlpha="alpha",
        kCGWindowBounds="bounds",
        kCGWindowNumber="number",
        kCGWindowName="title",
        kCGWindowOwnerPID="pid",
        kCGWindowOwnerName="owner",
        CGWindowListCopyWindowInfo=lambda options, window_id: records,
    )
    monkeypatch.setitem(sys.modules, "Quartz", quartz)

    assert not QuartzBackend()._target_is_topmost_at(target, (110, 210))

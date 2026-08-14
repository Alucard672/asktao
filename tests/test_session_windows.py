import pytest

from wendao_bot.session import GameSession
from wendao_bot.session_windows import Win32Backend


def spec(
    *,
    hwnd: int = 7,
    title: str = "Wendao",
    rect: tuple[int, int, int, int] = (100, 200, 800, 600),
    pid: int = 42,
    image: str = "wendao.exe",
) -> dict:
    return {"hwnd": hwnd, "title": title, "rect": rect, "pid": pid, "image": image}


class FakeWin32Api:
    def __init__(self, windows: list[dict] | None = None) -> None:
        self.windows = windows if windows is not None else [spec()]
        self.images = {window["pid"]: window["image"] for window in self.windows}
        first = self.windows[0]["hwnd"] if self.windows else 0
        self.foreground = first
        self.root = first
        self.iconic: set[int] = set()
        self.covered: dict[tuple[int, int], int] = {}
        self.focusable = True
        self.grab_size: tuple[int, int] | None = None
        self.grab_data = b"PNG DATA"
        self.grabs: list[tuple[int, int, int, int]] = []
        self.cursor_moves: list[tuple[int, int]] = []
        self.clicks: list[tuple[int, int]] = []
        self.focus_requests: list[int] = []

    def _spec(self, handle: int) -> dict:
        return next(window for window in self.windows if window["hwnd"] == handle)

    def enum_windows(self) -> list[int]:
        return [window["hwnd"] for window in self.windows]

    def window_title(self, handle: int) -> str:
        return self._spec(handle)["title"]

    def window_pid(self, handle: int) -> int:
        return self._spec(handle)["pid"]

    def process_image_name(self, pid: int) -> str:
        return self.images.get(pid, "")

    def client_rect(self, handle: int) -> tuple[int, int, int, int]:
        return self._spec(handle)["rect"]

    def foreground_window(self) -> int:
        return self.foreground

    def is_iconic(self, handle: int) -> bool:
        return handle in self.iconic

    def root_window_at(self, x: int, y: int) -> int:
        return self.covered.get((x, y), self.root)

    def set_foreground(self, handle: int) -> bool:
        self.focus_requests.append(handle)
        if self.focusable:
            self.foreground = handle
        return self.focusable

    def move_cursor(self, x: int, y: int) -> None:
        self.cursor_moves.append((x, y))

    def send_click(self, x: int, y: int) -> None:
        self.clicks.append((x, y))

    def grab_png(
        self, x: int, y: int, width: int, height: int
    ) -> tuple[tuple[int, int], bytes]:
        self.grabs.append((x, y, width, height))
        return self.grab_size or (width, height), self.grab_data


def session_for(api: FakeWin32Api) -> GameSession:
    return GameSession(
        Win32Backend(api=api), "Wendao", 800, 600, expected_owner_name="wendao.exe"
    )


def test_capture_rejects_multiple_exact_title_matches() -> None:
    api = FakeWin32Api([spec(), spec(hwnd=8)])

    with pytest.raises(RuntimeError, match="exactly one"):
        session_for(api).capture()

    assert api.grabs == []


def test_capture_rejects_unexpected_client_geometry() -> None:
    api = FakeWin32Api([spec(rect=(100, 200, 801, 600))])

    with pytest.raises(RuntimeError, match="geometry"):
        session_for(api).capture()

    assert api.grabs == []


def test_capture_rejects_wrong_executable_name() -> None:
    api = FakeWin32Api([spec(image="impostor.exe")])

    with pytest.raises(RuntimeError, match="owner name"):
        session_for(api).capture()

    assert api.grabs == []


def test_capture_allows_unoccluded_background_window() -> None:
    api = FakeWin32Api()
    api.foreground = 999

    session_for(api).capture()

    assert len(api.grabs) == 1


def test_capture_still_rejects_covered_background_window() -> None:
    api = FakeWin32Api()
    api.foreground = 999
    api.covered[(100, 200)] = 999

    with pytest.raises(RuntimeError, match="covered at sample point"):
        session_for(api).capture()

    assert api.grabs == []


def test_capture_rejects_minimized_window() -> None:
    api = FakeWin32Api()
    api.iconic.add(7)

    with pytest.raises(RuntimeError, match="minimized"):
        session_for(api).capture()

    assert api.grabs == []


@pytest.mark.parametrize(
    "point", [(100, 200), (899, 200), (100, 799), (899, 799), (500, 500)]
)
def test_capture_rejects_sample_point_covered_by_other_window(
    point: tuple[int, int],
) -> None:
    api = FakeWin32Api()
    api.covered[point] = 999

    with pytest.raises(RuntimeError, match="covered"):
        session_for(api).capture()

    assert api.grabs == []


def test_capture_rejects_pixel_dimension_mismatch() -> None:
    api = FakeWin32Api()
    api.grab_size = (1600, 1200)

    with pytest.raises(RuntimeError, match="pixel dimensions"):
        session_for(api).capture()


def test_capture_grabs_exact_client_rect() -> None:
    api = FakeWin32Api()

    result = session_for(api).capture()

    assert result == b"PNG DATA"
    assert api.grabs == [(100, 200, 800, 600)]


@pytest.mark.parametrize("point", [(-1, 0), (0, -1), (800, 0), (0, 600)])
def test_click_rejects_coordinates_outside_the_client_area(
    point: tuple[int, int],
) -> None:
    api = FakeWin32Api()

    with pytest.raises(ValueError, match="bounds"):
        session_for(api).click(*point)

    assert api.clicks == []
    assert api.cursor_moves == []


def test_click_does_not_send_input_when_focus_fails() -> None:
    api = FakeWin32Api()
    api.foreground = 999
    api.focusable = False

    with pytest.raises(RuntimeError, match="focus"):
        session_for(api).click(10, 10)

    assert api.clicks == []
    assert api.cursor_moves == []


def test_click_rejects_covered_click_point() -> None:
    api = FakeWin32Api()
    api.covered[(125, 240)] = 999

    with pytest.raises(RuntimeError, match="covered at click"):
        session_for(api).click(25, 40)

    assert api.clicks == []
    assert api.cursor_moves == []


def test_click_rejects_bounds_change_after_focus() -> None:
    class ShiftingApi(FakeWin32Api):
        def set_foreground(self, handle: int) -> bool:
            result = super().set_foreground(handle)
            self._spec(handle)["rect"] = (100, 200, 800, 601)
            return result

    api = ShiftingApi()

    with pytest.raises(RuntimeError, match="changed after focus"):
        session_for(api).click(10, 10)

    assert api.clicks == []


def test_click_moves_cursor_then_sends_input_at_screen_point() -> None:
    api = FakeWin32Api()

    session_for(api).click(25, 40)

    assert api.focus_requests == [7]
    assert api.cursor_moves == [(125, 240)]
    assert api.clicks == [(125, 240)]

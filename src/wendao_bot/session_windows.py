"""Validated access to a single Windows game window."""

from __future__ import annotations

import sys

from .session import WindowInfo

_PER_MONITOR_AWARE_V2 = -4
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_GA_ROOT = 2
_INPUT_MOUSE = 0
_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004
_IMAGE_NAME_CAPACITY = 32767
_TITLE_CAPACITY = 512

_dpi_awareness_applied = False


def _ensure_dpi_awareness() -> None:
    global _dpi_awareness_applied
    if _dpi_awareness_applied or sys.platform != "win32":
        return
    import ctypes

    user32 = ctypes.windll.user32
    try:
        applied = bool(
            user32.SetProcessDpiAwarenessContext(
                ctypes.c_void_p(_PER_MONITOR_AWARE_V2)
            )
        )
    except AttributeError:
        applied = False
    if not applied:
        user32.SetProcessDPIAware()
    _dpi_awareness_applied = True


class _Win32Api:
    """Thin seam over the ctypes Win32 calls used by Win32Backend."""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError("Win32Backend requires Windows")
        import ctypes
        import ctypes.wintypes as wintypes

        _ensure_dpi_awareness()
        self._ctypes = ctypes
        self._wintypes = wintypes
        self._user32 = ctypes.windll.user32
        self._kernel32 = ctypes.windll.kernel32
        self._enum_proc = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
        )

        class MouseInput(ctypes.Structure):
            _fields_ = [
                ("dx", wintypes.LONG),
                ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t),
            ]

        class Input(ctypes.Structure):
            _fields_ = [
                ("type", wintypes.DWORD),
                ("mi", MouseInput),
            ]

        self._input_type = Input
        user32 = self._user32
        kernel32 = self._kernel32
        user32.EnumWindows.argtypes = (self._enum_proc, wintypes.LPARAM)
        user32.EnumWindows.restype = wintypes.BOOL
        user32.IsWindowVisible.argtypes = (wintypes.HWND,)
        user32.IsWindowVisible.restype = wintypes.BOOL
        user32.GetWindowTextW.argtypes = (
            wintypes.HWND,
            wintypes.LPWSTR,
            ctypes.c_int,
        )
        user32.GetWindowTextW.restype = ctypes.c_int
        user32.GetWindowThreadProcessId.argtypes = (
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        )
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.GetClientRect.argtypes = (
            wintypes.HWND,
            ctypes.POINTER(wintypes.RECT),
        )
        user32.GetClientRect.restype = wintypes.BOOL
        user32.ClientToScreen.argtypes = (
            wintypes.HWND,
            ctypes.POINTER(wintypes.POINT),
        )
        user32.ClientToScreen.restype = wintypes.BOOL
        user32.GetForegroundWindow.argtypes = ()
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.IsIconic.argtypes = (wintypes.HWND,)
        user32.IsIconic.restype = wintypes.BOOL
        user32.WindowFromPoint.argtypes = (wintypes.POINT,)
        user32.WindowFromPoint.restype = wintypes.HWND
        user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
        user32.GetAncestor.restype = wintypes.HWND
        user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
        user32.SetForegroundWindow.restype = wintypes.BOOL
        user32.SetCursorPos.argtypes = (ctypes.c_int, ctypes.c_int)
        user32.SetCursorPos.restype = wintypes.BOOL
        user32.SendInput.argtypes = (
            wintypes.UINT,
            ctypes.POINTER(Input),
            ctypes.c_int,
        )
        user32.SendInput.restype = wintypes.UINT
        kernel32.OpenProcess.argtypes = (
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        )
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.QueryFullProcessImageNameW.argtypes = (
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        )
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL

    def enum_windows(self) -> list[int]:
        handles: list[int] = []

        def collect(handle, _lparam):
            if self._user32.IsWindowVisible(handle):
                handles.append(int(handle))
            return True

        self._user32.EnumWindows(self._enum_proc(collect), 0)
        return handles

    def window_title(self, handle: int) -> str:
        buffer = self._ctypes.create_unicode_buffer(_TITLE_CAPACITY)
        self._user32.GetWindowTextW(handle, buffer, _TITLE_CAPACITY)
        return buffer.value

    def window_pid(self, handle: int) -> int:
        owner_pid = self._wintypes.DWORD(0)
        self._user32.GetWindowThreadProcessId(
            handle, self._ctypes.byref(owner_pid)
        )
        return int(owner_pid.value)

    def process_image_name(self, pid: int) -> str:
        handle = self._kernel32.OpenProcess(
            _PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if not handle:
            return ""
        try:
            size = self._wintypes.DWORD(_IMAGE_NAME_CAPACITY)
            buffer = self._ctypes.create_unicode_buffer(_IMAGE_NAME_CAPACITY)
            if not self._kernel32.QueryFullProcessImageNameW(
                handle, 0, buffer, self._ctypes.byref(size)
            ):
                return ""
            return buffer.value.rsplit("\\", 1)[-1]
        finally:
            self._kernel32.CloseHandle(handle)

    def client_rect(self, handle: int) -> tuple[int, int, int, int] | None:
        rect = self._wintypes.RECT()
        if not self._user32.GetClientRect(handle, self._ctypes.byref(rect)):
            return None
        origin = self._wintypes.POINT(0, 0)
        if not self._user32.ClientToScreen(handle, self._ctypes.byref(origin)):
            return None
        return (
            int(origin.x),
            int(origin.y),
            int(rect.right - rect.left),
            int(rect.bottom - rect.top),
        )

    def foreground_window(self) -> int:
        return int(self._user32.GetForegroundWindow() or 0)

    def is_iconic(self, handle: int) -> bool:
        return bool(self._user32.IsIconic(handle))

    def root_window_at(self, x: int, y: int) -> int:
        below = self._user32.WindowFromPoint(
            self._wintypes.POINT(int(x), int(y))
        )
        if not below:
            return 0
        return int(self._user32.GetAncestor(below, _GA_ROOT) or 0)

    def set_foreground(self, handle: int) -> bool:
        return bool(self._user32.SetForegroundWindow(handle))

    def move_cursor(self, x: int, y: int) -> None:
        if not self._user32.SetCursorPos(int(x), int(y)):
            raise RuntimeError(f"could not move cursor to ({x}, {y})")

    def send_click(self, x: int, y: int) -> None:
        events = (self._input_type * 2)()
        for event, flags in zip(
            events, (_MOUSEEVENTF_LEFTDOWN, _MOUSEEVENTF_LEFTUP)
        ):
            event.type = _INPUT_MOUSE
            event.mi.dx = int(x)
            event.mi.dy = int(y)
            event.mi.dwFlags = flags
        sent = self._user32.SendInput(
            2, events, self._ctypes.sizeof(self._input_type)
        )
        if int(sent) != 2:
            raise RuntimeError("could not post mouse click events")

    def grab_png(
        self, x: int, y: int, width: int, height: int
    ) -> tuple[tuple[int, int], bytes]:
        import mss
        import mss.tools

        with mss.mss() as screen:
            shot = screen.grab(
                {
                    "left": int(x),
                    "top": int(y),
                    "width": int(width),
                    "height": int(height),
                }
            )
        return (
            (int(shot.width), int(shot.height)),
            bytes(mss.tools.to_png(shot.rgb, shot.size)),
        )


class Win32Backend:
    """Window backend whose Win32 dependencies are loaded only when used."""

    def __init__(self, api: object | None = None) -> None:
        self._api = api

    def _win32(self):
        if self._api is None:
            self._api = _Win32Api()
        return self._api

    def list_windows(self) -> list[WindowInfo]:
        api = self._win32()
        windows = []
        for handle in api.enum_windows():
            title = api.window_title(handle)
            geometry = api.client_rect(handle)
            if not (title and geometry):
                continue
            x, y, width, height = geometry
            owner_pid = int(api.window_pid(handle))
            windows.append(
                WindowInfo(
                    window_id=int(handle),
                    title=title,
                    x=int(x),
                    y=int(y),
                    width=int(width),
                    height=int(height),
                    owner_pid=owner_pid,
                    owner_name=str(api.process_image_name(owner_pid)),
                )
            )
        return windows

    def capture(self, target: WindowInfo) -> bytes:
        api = self._win32()
        self._assert_target_visible(api, target)
        dimensions, data = api.grab_png(
            target.x, target.y, target.width, target.height
        )
        expected = (target.width, target.height)
        if tuple(dimensions) != expected:
            raise RuntimeError(
                f"captured pixel dimensions {tuple(dimensions)} do not match window {expected}"
            )
        return bytes(data)

    def focus_and_click(self, target: WindowInfo, x: int, y: int) -> None:
        api = self._win32()
        handle = target.window_id
        api.set_foreground(handle)
        if int(api.foreground_window() or 0) != handle:
            raise RuntimeError("could not focus window owner application")
        current = next(
            (item for item in self.list_windows() if item.window_id == handle),
            None,
        )
        if current != target:
            raise RuntimeError("window identity or bounds changed after focus")
        self._assert_target_visible(api, target)
        point = (target.x + x, target.y + y)
        if int(api.root_window_at(*point) or 0) != handle:
            raise RuntimeError("target window is covered at click coordinates")
        api.move_cursor(*point)
        api.send_click(*point)

    def _assert_target_visible(self, api, target: WindowInfo) -> None:
        handle = target.window_id
        if api.is_iconic(handle):
            raise RuntimeError("target window is minimized")
        if int(api.foreground_window() or 0) != handle:
            raise RuntimeError("window owner is not the foreground application")
        for point in self._sample_points(target):
            if int(api.root_window_at(*point) or 0) != handle:
                raise RuntimeError(
                    f"target window is covered at sample point {point}"
                )

    @staticmethod
    def _sample_points(target: WindowInfo) -> tuple[tuple[int, int], ...]:
        left = target.x
        top = target.y
        right = target.x + target.width - 1
        bottom = target.y + target.height - 1
        return (
            (left, top),
            (right, top),
            (left, bottom),
            (right, bottom),
            (target.x + target.width // 2, target.y + target.height // 2),
        )

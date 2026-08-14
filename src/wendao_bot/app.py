"""Native macOS control window with an AppKit-independent controller."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys
import time
from typing import Mapping

from .app_model import AppViewState
from .app_service import AppService, ConfigSelectionError, InvalidModeTransition


@dataclass(frozen=True)
class DisplayModel:
    """Text and control state safe to hand to a native view."""

    fields: Mapping[str, str]
    buttons: Mapping[str, bool]


def display_model(state: AppViewState) -> DisplayModel:
    active = state.status in {"observing", "running", "paused"}
    return DisplayModel(
        fields={
            "window_connection": _window_availability(state.window_match),
            "window_geometry": (
                f"{state.window_title} {state.detected_width}×{state.detected_height}"
                if state.window_title and state.detected_width and state.detected_height
                else "—"
            ),
            "ocr_availability": _availability(state.ocr_available),
            "template_availability": _availability(state.template_available),
            "observe_readiness": "就绪" if state.observe_ready else "未就绪",
            "accessibility": _availability(state.accessibility_trusted),
            "state": state.screen_state,
            "confidence": f"{state.confidence:.1%}",
            "target_names": ", ".join(state.target_names) or "—",
            "run_status": state.status,
            "pause_reason": state.pause_reason or "—",
        },
        buttons={
            "observe": not active,
            "single_step": state.can_single_step,
            "continuous": state.can_run_continuous,
            "pause": state.status in {"observing", "running"},
            "resume": state.status == "paused",
            "stop": active,
            "choose_config": True,
            "runtime_folder": True,
            "notification_preview": True,
            "save_geometry_config": bool(
                state.readiness_failure == "geometry_mismatch"
                and state.detected_width
                and state.detected_height
            ),
        },
    )


KNOWN_EMULATOR_EXECUTABLES = frozenset(
    {
        "mumuplayer.exe",
        "mumunxdevice.exe",
        "nemuplayer.exe",
        "dnplayer.exe",
        "nox.exe",
        "hd-player.exe",
        "ldplayer.exe",
        "memu.exe",
    }
)

_EMULATOR_TITLE_HINTS = ("mumu", "模拟器", "雷电", "夜神", "bluestacks", "安卓")

_MIN_EMULATOR_CLIENT_SIZE = 200


def emulator_candidates(windows) -> list:
    ranked = []
    for window in windows:
        if not window.title:
            continue
        if window.width < _MIN_EMULATOR_CLIENT_SIZE:
            continue
        if window.height < _MIN_EMULATOR_CLIENT_SIZE:
            continue
        known_owner = window.owner_name.casefold() in KNOWN_EMULATOR_EXECUTABLES
        title = window.title.casefold()
        hinted = any(hint in title for hint in _EMULATOR_TITLE_HINTS)
        if known_owner or hinted:
            ranked.append((0 if known_owner else 1, window))
    ranked.sort(key=lambda item: (item[0], item[1].title))
    return [window for _, window in ranked]


def write_detected_config(window, destination: Path) -> Path:
    import json

    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        f"window:\n"
        f"  title: {json.dumps(window.title, ensure_ascii=False)}\n"
        f"  owner: {json.dumps(window.owner_name, ensure_ascii=False)}\n"
        f"  width: {int(window.width)}\n"
        f"  height: {int(window.height)}\n"
    )
    destination.write_text(payload, encoding="utf-8")
    return destination


class AppController:
    """Coordinate GUI actions without importing or touching AppKit."""

    _TRANSITION_ERROR = "当前状态不允许此操作"

    def __init__(
        self,
        service: AppService,
        view,
        *,
        clock=time.monotonic,
        termination_timeout: float = 5.0,
    ) -> None:
        self.service = service
        self.view = view
        self.clock = clock
        self.termination_timeout = termination_timeout
        self._closed = False
        self._terminating = False
        self._termination_deadline = None

    def launch(self) -> None:
        self.view.render(AppViewState.initial())
        self._call(self.service.start)

    def poll(self) -> None:
        for state in self.service.drain_events():
            self.view.render(state)
        if self._terminating:
            worker_finished = self.service.join(timeout=0)
            timed_out = bool(
                self._termination_deadline is not None
                and self.clock() >= self._termination_deadline
            )
            if worker_finished or timed_out:
                self._terminating = False
                self._termination_deadline = None
                self.view.finish_termination()

    def observe(self) -> None:
        self._call(self.service.start)

    def single_step(self) -> None:
        self._call(self.service.start_single_step)

    def continuous(self) -> None:
        self._call(self.service.start_continuous)

    def pause(self) -> None:
        self._call(self.service.pause)

    def resume(self) -> None:
        self._call(self.service.resume)

    def stop(self) -> None:
        request_stop = getattr(self.service, "request_stop", None)
        self._call(request_stop if callable(request_stop) else self.service.stop)

    def choose_config(self) -> None:
        path = self.view.choose_config()
        if path is not None:
            self._call(lambda: self.service.set_config(path))

    def detect_emulator(self) -> None:
        path = self.view.detect_emulator()
        if path is not None:
            self._call(lambda: self.service.set_config(path))

    def open_runtime_folder(self) -> None:
        self._call(
            lambda: self.view.open_runtime_folder(self.service.ensure_runtime_directory())
        )

    def preview_notification(self) -> None:
        self._call(
            lambda: self.view.preview_notification(self.service.preview_notification())
        )

    def save_geometry_config(self) -> None:
        path = self.view.save_geometry_config()
        if path is not None:
            self._call(lambda: self.service.write_geometry_config(path))

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        close = getattr(self.service, "close", None)
        if callable(close):
            close()
            return
        self.service.stop()
        join = getattr(self.service, "join", None)
        if callable(join):
            join()

    def begin_termination(self) -> bool:
        if self._closed:
            return not self._terminating
        self._closed = True
        request_close = getattr(self.service, "request_close", None)
        if not callable(request_close):
            self.service.close()
            return True
        request_close()
        if self.service.join(timeout=0):
            return True
        self._terminating = True
        self._termination_deadline = self.clock() + self.termination_timeout
        return False

    def _call(self, action) -> None:
        try:
            action()
        except (InvalidModeTransition, ConfigSelectionError, RuntimeError):
            self.view.show_error(self._TRANSITION_ERROR)


def _availability(value: bool | None) -> str:
    if value is True:
        return "可用"
    if value is False:
        return "不可用"
    return "未知"


def _window_availability(value: bool | None) -> str:
    if value is True:
        return "已连接"
    if value is False:
        return "不匹配"
    return "未知"


def configure_config_panel(panel) -> None:
    panel.setCanChooseFiles_(True)
    panel.setCanChooseDirectories_(False)
    panel.setAllowsOtherFileTypes_(True)


def _build_native_app(service: AppService):
    import AppKit
    import Foundation
    import objc

    button_titles = {
        "observe": "仅观察",
        "single_step": "单步执行",
        "continuous": "连续运行",
        "pause": "暂停",
        "resume": "恢复",
        "stop": "停止",
        "choose_config": "选择配置",
        "runtime_folder": "运行目录",
        "notification_preview": "通知预览",
        "save_geometry_config": "保存尺寸配置",
    }
    button_selectors = {
        "observe": "observe:",
        "single_step": "singleStep:",
        "continuous": "continuous:",
        "pause": "pause:",
        "resume": "resume:",
        "stop": "stop:",
        "choose_config": "chooseConfig:",
        "runtime_folder": "runtimeFolder:",
        "notification_preview": "notificationPreview:",
        "save_geometry_config": "saveGeometryConfig:",
    }
    field_titles = {
        "window_connection": "窗口 / 连接",
        "window_geometry": "检测窗口",
        "ocr_availability": "OCR",
        "template_availability": "模板",
        "observe_readiness": "观察就绪",
        "accessibility": "辅助功能",
        "state": "识别状态",
        "confidence": "置信度",
        "target_names": "目标名称",
        "run_status": "运行状态",
        "pause_reason": "暂停原因",
    }

    class NativeView:
        def __init__(self) -> None:
            style = (
                AppKit.NSWindowStyleMaskTitled
                | AppKit.NSWindowStyleMaskClosable
                | AppKit.NSWindowStyleMaskMiniaturizable
            )
            self.window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                Foundation.NSMakeRect(0, 0, 540, 700),
                style,
                AppKit.NSBackingStoreBuffered,
                False,
            )
            self.window.setTitle_("问道前台助手")
            self.window.center()
            content = self.window.contentView()
            self.labels = {}
            y = 650
            for key, title in field_titles.items():
                caption = AppKit.NSTextField.labelWithString_(f"{title}：")
                caption.setFrame_(Foundation.NSMakeRect(24, y, 120, 24))
                value = AppKit.NSTextField.labelWithString_("—")
                value.setFrame_(Foundation.NSMakeRect(145, y, 365, 24))
                content.addSubview_(caption)
                content.addSubview_(value)
                self.labels[key] = value
                y -= 38
            self.buttons = {}

        def install_buttons(self, target) -> None:
            content = self.window.contentView()
            for index, (key, title) in enumerate(button_titles.items()):
                row, column = divmod(index, 3)
                button = AppKit.NSButton.buttonWithTitle_target_action_(
                    title, target, button_selectors[key]
                )
                button.setFrame_(
                    Foundation.NSMakeRect(24 + column * 170, 130 - row * 42, 155, 32)
                )
                content.addSubview_(button)
                self.buttons[key] = button

        def render(self, state: AppViewState) -> None:
            model = display_model(state)
            for key, value in model.fields.items():
                self.labels[key].setStringValue_(value)
            for key, enabled in model.buttons.items():
                self.buttons[key].setEnabled_(enabled)

        def show_error(self, message: str) -> None:
            alert = AppKit.NSAlert.alloc().init()
            alert.setMessageText_(message)
            alert.runModal()

        def choose_config(self) -> Path | None:
            panel = AppKit.NSOpenPanel.openPanel()
            configure_config_panel(panel)
            if panel.runModal() != AppKit.NSModalResponseOK:
                return None
            url = panel.URL()
            return Path(str(url.path())) if url is not None else None

        def open_runtime_folder(self, runtime: Path) -> None:
            AppKit.NSWorkspace.sharedWorkspace().openURL_(
                Foundation.NSURL.fileURLWithPath_(str(runtime))
            )

        def preview_notification(self, preview) -> None:
            alert = AppKit.NSAlert.alloc().init()
            alert.setMessageText_("iMessage 通知预览（不会发送）")
            alert.setInformativeText_(
                f"收件人：{preview.recipient}\n\n正文：\n{preview.body}"
            )
            alert.addButtonWithTitle_("关闭")
            alert.beginSheetModalForWindow_completionHandler_(self.window, None)

        def save_geometry_config(self) -> Path | None:
            panel = AppKit.NSSavePanel.savePanel()
            panel.setNameFieldStringValue_("wendao-geometry.yaml")
            if panel.runModal() != AppKit.NSModalResponseOK:
                return None
            url = panel.URL()
            return Path(str(url.path())) if url is not None else None

        def finish_termination(self) -> None:
            AppKit.NSApplication.sharedApplication().replyToApplicationShouldTerminate_(
                True
            )

    class ActionTarget(Foundation.NSObject):
        def initWithController_(self, controller):
            self = objc.super(ActionTarget, self).init()
            if self is not None:
                self.controller = controller
            return self

        def observe_(self, sender): self.controller.observe()
        def singleStep_(self, sender): self.controller.single_step()
        def continuous_(self, sender): self.controller.continuous()
        def pause_(self, sender): self.controller.pause()
        def resume_(self, sender): self.controller.resume()
        def stop_(self, sender): self.controller.stop()
        def chooseConfig_(self, sender): self.controller.choose_config()
        def runtimeFolder_(self, sender): self.controller.open_runtime_folder()
        def notificationPreview_(self, sender): self.controller.preview_notification()
        def saveGeometryConfig_(self, sender): self.controller.save_geometry_config()

        def poll_(self, timer):
            self.controller.poll()

    class AppDelegate(Foundation.NSObject):
        def initWithController_(self, controller):
            self = objc.super(AppDelegate, self).init()
            if self is not None:
                self.controller = controller
            return self

        def applicationShouldTerminateAfterLastWindowClosed_(self, app):
            return True

        def applicationShouldTerminate_(self, app):
            if self.controller.begin_termination():
                return AppKit.NSTerminateNow
            return AppKit.NSTerminateLater

        def applicationWillTerminate_(self, notification):
            self.controller.close()

    app = AppKit.NSApplication.sharedApplication()
    view = NativeView()
    controller = AppController(service, view)
    target = ActionTarget.alloc().initWithController_(controller)
    delegate = AppDelegate.alloc().initWithController_(controller)
    view.install_buttons(target)
    app.setDelegate_(delegate)
    timer = Foundation.NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
        0.1, target, "poll:", None, True
    )
    Foundation.NSRunLoop.mainRunLoop().addTimer_forMode_(
        timer, Foundation.NSRunLoopCommonModes
    )
    retained = (view, controller, target, delegate, timer)
    return app, retained


class TkView:
    _FIELD_TITLES = {
        "window_connection": "窗口 / 连接",
        "window_geometry": "检测窗口",
        "ocr_availability": "OCR",
        "template_availability": "模板",
        "observe_readiness": "观察就绪",
        "accessibility": "辅助功能",
        "state": "识别状态",
        "confidence": "置信度",
        "target_names": "目标名称",
        "run_status": "运行状态",
        "pause_reason": "暂停原因",
    }
    _BUTTON_TITLES = {
        "observe": "仅观察",
        "single_step": "单步执行",
        "continuous": "连续运行",
        "pause": "暂停",
        "resume": "恢复",
        "stop": "停止",
        "detect_emulator": "检测模拟器",
        "choose_config": "选择配置",
        "runtime_folder": "运行目录",
        "notification_preview": "通知预览",
        "check_update": "检查更新",
        "diagnose": "诊断",
        "capture_template": "采集模板",
    }
    _BUTTON_ACTIONS = {
        "observe": "observe",
        "single_step": "single_step",
        "continuous": "continuous",
        "pause": "pause",
        "resume": "resume",
        "stop": "stop",
        "detect_emulator": "detect_emulator",
        "choose_config": "choose_config",
        "runtime_folder": "open_runtime_folder",
        "notification_preview": "preview_notification",
    }
    _GATED_BUTTONS = ("single_step", "continuous")

    _SUMMARY_TITLES = {
        "connection": "连接",
        "screen": "画面",
        "afk": "挂机",
    }

    def __init__(self) -> None:
        import tkinter as tk

        self._destroyed = False
        self.root = tk.Tk()
        self.root.title("问道前台助手")

        self.summary_labels = {}
        summary = tk.Frame(self.root, padx=24, pady=16)
        summary.pack(fill="x")
        for row, (key, title) in enumerate(self._SUMMARY_TITLES.items()):
            caption = tk.Label(summary, text=f"{title}：", width=6, anchor="w")
            caption.grid(row=row, column=0, sticky="w")
            value = tk.Label(summary, text="—", anchor="w")
            value.grid(row=row, column=1, sticky="w")
            self.summary_labels[key] = value

        self.buttons = {}
        primary = tk.Frame(self.root, padx=24, pady=8)
        primary.pack(fill="x")
        for column, (key, title) in enumerate(
            [
                ("afk_start", "开始挂机"),
                ("afk_stop", "停止挂机"),
                ("detect_emulator", "检测模拟器"),
                ("toggle_advanced", "高级 ▾"),
            ]
        ):
            button = tk.Button(primary, text=title, width=10)
            button.grid(row=0, column=column, padx=4, pady=4)
            self.buttons[key] = button
        self.buttons["afk_stop"].configure(state="disabled")

        self._advanced = tk.Frame(self.root)
        self.labels = {}
        fields = tk.Frame(self._advanced, padx=24, pady=8)
        fields.pack(fill="x")
        for row, (key, title) in enumerate(self._FIELD_TITLES.items()):
            caption = tk.Label(fields, text=f"{title}：", width=12, anchor="w")
            caption.grid(row=row, column=0, sticky="w")
            value = tk.Label(fields, text="—", anchor="w")
            value.grid(row=row, column=1, sticky="w")
            self.labels[key] = value
        actions = tk.Frame(self._advanced, padx=24, pady=8)
        actions.pack(fill="x")
        for index, (key, title) in enumerate(self._BUTTON_TITLES.items()):
            if key == "detect_emulator":
                continue
            row, column = divmod(index, 3)
            button = tk.Button(actions, text=title, width=12)
            button.grid(row=row, column=column, padx=4, pady=4)
            self.buttons[key] = button
        self._advanced_visible = False
        self.buttons["toggle_advanced"].configure(command=self._toggle_advanced)

    def _toggle_advanced(self) -> None:
        self._advanced_visible = not self._advanced_visible
        if self._advanced_visible:
            self._advanced.pack(fill="x")
            self.buttons["toggle_advanced"].configure(text="高级 ▴")
        else:
            self._advanced.pack_forget()
            self.buttons["toggle_advanced"].configure(text="高级 ▾")

    def install_buttons(self, controller) -> None:
        for key, action in self._BUTTON_ACTIONS.items():
            self.buttons[key].configure(command=getattr(controller, action))

    def run_diagnostics_flow(self, service) -> None:
        import queue
        import threading
        import tkinter as tk
        from tkinter import messagebox

        from .diagnostics import run_diagnostics

        if getattr(self, "_diagnosing", False):
            return
        self._diagnosing = True
        self.buttons["diagnose"].configure(state="disabled", text="诊断中…")
        results: queue.Queue = queue.Queue(maxsize=1)
        config_path = service.config_path
        runtime = service.ensure_runtime_directory()

        def work() -> None:
            try:
                results.put(("ok", run_diagnostics(config_path, runtime)))
            except Exception as error:
                results.put(("error", error))

        threading.Thread(target=work, name="wendao-diagnostics", daemon=True).start()

        def finish() -> None:
            if self._destroyed:
                return
            try:
                kind, payload = results.get_nowait()
            except queue.Empty:
                self.root.after(200, finish)
                return
            self._diagnosing = False
            self.buttons["diagnose"].configure(state="normal", text="诊断")
            if kind == "error":
                messagebox.showerror(
                    "诊断", f"诊断执行失败：{payload}", parent=self.root
                )
                return
            report, lines = payload
            self._show_diagnostics_result(report, lines)

        self.root.after(200, finish)

    def _show_diagnostics_result(self, report, lines) -> None:
        import tkinter as tk

        window = tk.Toplevel(self.root)
        window.title("诊断结果")
        window.transient(self.root)
        text = tk.Text(window, width=88, height=24, wrap="word")
        text.insert("1.0", "\n".join(lines) + f"\n\n报告已保存：{report}")
        text.configure(state="disabled")
        text.pack(fill="both", expand=True, padx=8, pady=8)
        tk.Button(window, text="关闭", width=10, command=window.destroy).pack(
            pady=(0, 8)
        )

    def run_capture_flow(self, service) -> None:
        import queue
        import threading
        from tkinter import messagebox

        if getattr(self, "_capturing", False):
            return
        self._capturing = True
        self.buttons["capture_template"].configure(state="disabled", text="截屏中…")
        results: queue.Queue = queue.Queue(maxsize=1)
        config_path = service.config_path

        def work() -> None:
            try:
                from .config import load_config
                from .session import GameSession

                config = load_config(config_path)
                if sys.platform == "win32":
                    from .session_windows import Win32Backend

                    backend = Win32Backend()
                else:
                    from .session import QuartzBackend

                    backend = QuartzBackend()
                owner = config.window_owner or (
                    "asktao.exe" if sys.platform == "win32" else "问道"
                )
                session = GameSession(
                    backend,
                    config.window_title,
                    config.width,
                    config.height,
                    None,
                    owner,
                )
                results.put(("ok", (config, session.capture())))
            except Exception as error:
                results.put(("error", error))

        threading.Thread(target=work, name="wendao-capture", daemon=True).start()

        def finish() -> None:
            if self._destroyed:
                return
            try:
                kind, payload = results.get_nowait()
            except queue.Empty:
                self.root.after(200, finish)
                return
            self._capturing = False
            self.buttons["capture_template"].configure(
                state="normal", text="采集模板"
            )
            if kind == "error":
                messagebox.showerror(
                    "采集模板", f"截屏失败：{payload}", parent=self.root
                )
                return
            config, image_bytes = payload
            self._show_capture_editor(service, config, image_bytes)

        self.root.after(200, finish)

    def _show_capture_editor(self, service, config, image_bytes) -> None:
        import io
        import queue
        import threading
        import tkinter as tk
        from tkinter import messagebox, ttk

        from PIL import Image, ImageTk

        from . import template_capture

        image = Image.open(io.BytesIO(image_bytes))
        scale = 1.0
        display = image
        if image.width > 900:
            scale = 900 / image.width
            display = image.resize((900, max(1, round(image.height * scale))))
        window = tk.Toplevel(self.root)
        window.title("采集模板")
        window.transient(self.root)
        photo = ImageTk.PhotoImage(display, master=window)
        window._capture_photo = photo
        tk.Label(
            window,
            text="在截图上拖拽画出红框选区，然后填写信息并保存。",
            anchor="w",
        ).pack(fill="x", padx=8, pady=(8, 0))
        canvas = tk.Canvas(
            window, width=display.width, height=display.height, cursor="cross"
        )
        canvas.pack(padx=8, pady=8)
        canvas.create_image(0, 0, image=photo, anchor="nw")
        selection = {"start": None, "box": None, "rect": None}

        def clamp(value, limit):
            return min(max(int(value), 0), limit)

        def press(event) -> None:
            selection["start"] = (
                clamp(event.x, display.width),
                clamp(event.y, display.height),
            )

        def drag(event) -> None:
            if selection["start"] is None:
                return
            x0, y0 = selection["start"]
            x1 = clamp(event.x, display.width)
            y1 = clamp(event.y, display.height)
            if selection["rect"] is not None:
                canvas.delete(selection["rect"])
            selection["rect"] = canvas.create_rectangle(
                x0, y0, x1, y1, outline="red", width=2
            )
            selection["box"] = (
                min(x0, x1),
                min(y0, y1),
                abs(x1 - x0),
                abs(y1 - y0),
            )

        canvas.bind("<ButtonPress-1>", press)
        canvas.bind("<B1-Motion>", drag)
        canvas.bind("<ButtonRelease-1>", drag)

        def original_box():
            if not selection["box"]:
                return None
            x, y, w, h = selection["box"]
            left = clamp(round(x / scale), image.width)
            top = clamp(round(y / scale), image.height)
            right = clamp(round((x + w) / scale), image.width)
            bottom = clamp(round((y + h) / scale), image.height)
            if right <= left or bottom <= top:
                return None
            return (left, top, right - left, bottom - top)

        form = tk.Frame(window, padx=8, pady=4)
        form.pack(fill="x")
        tk.Label(form, text="状态：", width=10, anchor="w").grid(
            row=0, column=0, sticky="w"
        )
        state_var = tk.StringVar(master=window, value="map")
        ttk.Combobox(
            form,
            textvariable=state_var,
            values=(
                "map",
                "npc_options",
                "dialogue",
                "auto_path",
                "battle",
                "reward",
                "activity_list",
            ),
            state="readonly",
            width=16,
        ).grid(row=0, column=1, sticky="w")
        tk.Label(form, text="目标名：", width=10, anchor="w").grid(
            row=1, column=0, sticky="w"
        )
        target_var = tk.StringVar(master=window)
        tk.Entry(form, textvariable=target_var, width=18).grid(
            row=1, column=1, sticky="w"
        )
        tk.Label(form, text="日常目标：", width=10, anchor="w").grid(
            row=2, column=0, sticky="w"
        )
        daily_var = tk.StringVar(master=window, value="")
        ttk.Combobox(
            form,
            textvariable=daily_var,
            values=("",) + tuple(config.daily_whitelist),
            state="readonly",
            width=16,
        ).grid(row=2, column=1, sticky="w")
        save_button = tk.Button(window, text="保存", width=10)
        save_button.pack(pady=(4, 8))
        results: queue.Queue = queue.Queue(maxsize=1)

        def finish() -> None:
            if self._destroyed or not window.winfo_exists():
                return
            try:
                kind, payload = results.get_nowait()
            except queue.Empty:
                self.root.after(200, finish)
                return
            if kind == "error":
                save_button.configure(state="normal", text="保存")
                if isinstance(payload, template_capture.PrivacyError):
                    message = f"隐私检查未通过，已拒绝保存：{payload}"
                elif "OCR" in str(payload):
                    message = f"OCR 不可用，已拒绝保存：{payload}"
                else:
                    message = f"保存失败：{payload}"
                messagebox.showerror("采集模板", message, parent=window)
                return
            total = len(list(payload.parent.glob("*.png")))
            window.destroy()
            messagebox.showinfo(
                "采集模板",
                f"已保存：\n{payload}\n\n当前用户模板共 {total} 个。\n"
                "重新开始观察后生效。",
                parent=self.root,
            )

        def save() -> None:
            box = original_box()
            if box is None:
                messagebox.showerror(
                    "采集模板", "请先在截图上拖拽框选区域", parent=window
                )
                return
            daily = daily_var.get()
            target = f"daily_{daily}" if daily else target_var.get().strip()
            if not target:
                messagebox.showerror(
                    "采集模板", "请填写目标名或选择日常目标", parent=window
                )
                return
            try:
                destination_dir = template_capture.user_template_dir(
                    service.ensure_runtime_directory()
                )
            except Exception as error:
                messagebox.showerror(
                    "采集模板", f"运行目录不可用：{error}", parent=window
                )
                return
            state = state_var.get()
            save_button.configure(state="disabled", text="保存中…")

            def work() -> None:
                try:
                    def recognize(crop_image):
                        import tempfile

                        from .recognizer import default_ocr

                        with tempfile.TemporaryDirectory(
                            prefix="wendao-capture-"
                        ) as folder:
                            crop_path = Path(folder) / "crop.png"
                            crop_image.save(crop_path)
                            return default_ocr(crop_path)

                    saved = template_capture.save_template(
                        image_bytes,
                        box,
                        state,
                        target,
                        "1x",
                        destination_dir,
                        recognize,
                        template_capture.scaled_forbidden_regions(
                            config.width, config.height
                        ),
                        daily_whitelist=config.daily_whitelist,
                        window_width=config.width,
                        window_height=config.height,
                    )
                    results.put(("ok", saved))
                except Exception as error:
                    results.put(("error", error))

            threading.Thread(
                target=work, name="wendao-capture-save", daemon=True
            ).start()
            self.root.after(200, finish)

        save_button.configure(command=save)

    def run_update_flow(self, shutdown) -> None:
        import tempfile
        from tkinter import messagebox

        from . import updater

        try:
            info = updater.check_update()
        except updater.UpdateError as error:
            messagebox.showerror("检查更新", f"检查更新失败：\n{error}", parent=self.root)
            return
        if info is None:
            messagebox.showinfo("检查更新", "当前已是最新版本。", parent=self.root)
            return
        wanted = messagebox.askyesno(
            "发现新版本",
            f"新版本：{info.version_name}\n\n更新内容：\n{info.changelog or '—'}\n\n"
            "是否下载并安装？下载完成后程序会自动重启。",
            parent=self.root,
        )
        if not wanted:
            return
        try:
            install_dir = updater.install_directory()
        except updater.UpdateError as error:
            messagebox.showerror("检查更新", str(error), parent=self.root)
            return

        import tkinter as tk

        progress = tk.Toplevel(self.root)
        progress.title("正在下载更新")
        progress.transient(self.root)
        label = tk.Label(progress, text="正在下载…", width=40, anchor="w")
        label.pack(padx=12, pady=12)

        def report(received: int, total: int) -> None:
            if total:
                label.configure(
                    text=f"正在下载… {received // 1048576}MB / {total // 1048576}MB"
                )
            else:
                label.configure(text=f"正在下载… {received // 1048576}MB")
            progress.update_idletasks()
            self.root.update()

        try:
            archive = updater.download_and_verify(
                info,
                Path(tempfile.gettempdir()) / "wendao-updates",
                reporthook=report,
            )
            updater.stage_windows_update(
                archive, install_dir, Path(sys.executable).name
            )
        except updater.UpdateError as error:
            progress.destroy()
            messagebox.showerror("检查更新", f"更新失败：\n{error}", parent=self.root)
            return
        progress.destroy()
        messagebox.showinfo(
            "检查更新",
            "下载完成并已校验。程序即将退出，更新会自动完成并重新启动。",
            parent=self.root,
        )
        shutdown()

    def render(self, state: AppViewState) -> None:
        model = display_model(state)
        for key, value in model.fields.items():
            self.labels[key].configure(text=value)
        for key in self._GATED_BUTTONS:
            self.buttons[key].configure(
                state="normal" if model.buttons[key] else "disabled"
            )
        fields = model.fields
        connection = fields.get("window_connection", "—")
        geometry = fields.get("window_geometry", "—")
        if geometry not in ("", "—"):
            connection = f"{connection}（{geometry}）"
        self.summary_labels["connection"].configure(text=connection)
        self.summary_labels["screen"].configure(
            text=f"{fields.get('state', '—')}（置信度 {fields.get('confidence', '—')}）"
        )

    def set_afk_summary(self, text: str) -> None:
        if not self._destroyed:
            self.summary_labels["afk"].configure(text=text)

    def show_error(self, message: str) -> None:
        from tkinter import messagebox

        messagebox.showerror("问道前台助手", message, parent=self.root)

    def choose_config(self) -> Path | None:
        from tkinter import filedialog

        selected = filedialog.askopenfilename(parent=self.root)
        return Path(selected) if selected else None

    def _list_windows(self) -> list:
        if sys.platform == "win32":
            from .session_windows import Win32Backend

            return Win32Backend().list_windows()
        from .session import QuartzBackend

        return QuartzBackend().list_windows()

    def detect_emulator(self) -> Path | None:
        import tkinter as tk
        from tkinter import messagebox

        from .app_service import default_runtime_path

        try:
            candidates = emulator_candidates(self._list_windows())
        except Exception:
            messagebox.showerror(
                "问道前台助手", "无法枚举窗口，请确认系统权限", parent=self.root
            )
            return None
        if not candidates:
            messagebox.showinfo(
                "问道前台助手",
                "未找到模拟器窗口。\n请确认模拟器已启动且未最小化，然后重试。",
                parent=self.root,
            )
            return None

        dialog = tk.Toplevel(self.root)
        dialog.title("选择模拟器窗口")
        dialog.transient(self.root)
        dialog.grab_set()
        tk.Label(
            dialog,
            text="找到以下候选窗口，请选择要连接的模拟器实例：",
            anchor="w",
        ).pack(fill="x", padx=8, pady=(8, 4))
        listbox = tk.Listbox(dialog, width=64, height=min(10, len(candidates)))
        for window in candidates:
            listbox.insert(
                "end",
                f"{window.title}  —  {window.owner_name}"
                f"  ({window.width}x{window.height})",
            )
        listbox.selection_set(0)
        listbox.pack(fill="both", expand=True, padx=8, pady=4)
        chosen: list = []

        def confirm() -> None:
            selection = listbox.curselection()
            if selection:
                chosen.append(candidates[selection[0]])
            dialog.destroy()

        buttons = tk.Frame(dialog)
        buttons.pack(pady=(4, 8))
        tk.Button(buttons, text="确定", width=10, command=confirm).grid(
            row=0, column=0, padx=4
        )
        tk.Button(buttons, text="取消", width=10, command=dialog.destroy).grid(
            row=0, column=1, padx=4
        )
        dialog.wait_window()
        if not chosen:
            return None
        destination = default_runtime_path() / "detected-config.yaml"
        path = write_detected_config(chosen[0], destination)
        messagebox.showinfo(
            "问道前台助手",
            f"已生成配置：\n{path}\n\n"
            f"窗口：{chosen[0].title}（{chosen[0].width}x{chosen[0].height}）\n"
            "点击确定后自动载入并开始只读观察。\n"
            "请保持模拟器窗口完整可见、不被其他窗口遮挡。",
            parent=self.root,
        )
        return path

    def open_runtime_folder(self, runtime: Path) -> None:
        import os
        import subprocess

        if sys.platform == "win32":
            os.startfile(str(runtime))
        elif sys.platform == "darwin":
            subprocess.run(["open", str(runtime)], check=False)
        else:
            subprocess.run(["xdg-open", str(runtime)], check=False)

    def preview_notification(self, preview) -> None:
        from tkinter import messagebox

        messagebox.showinfo(
            "iMessage 通知预览（不会发送）",
            f"收件人：{preview.recipient}\n\n正文：\n{preview.body}",
            parent=self.root,
        )

    def finish_termination(self) -> None:
        if self._destroyed:
            return
        self._destroyed = True
        self.root.destroy()


def _build_tk_app(service: AppService):
    view = TkView()
    controller = AppController(service, view)
    view.install_buttons(controller)

    def poll() -> None:
        controller.poll()
        if not view._destroyed:
            view.root.after(200, poll)

    def on_close() -> None:
        if afk_state["stop"] is not None:
            afk_state["stop"].set()
        if controller.begin_termination():
            view.finish_termination()

    view.buttons["check_update"].configure(
        command=lambda: view.run_update_flow(on_close)
    )
    view.buttons["diagnose"].configure(
        command=lambda: view.run_diagnostics_flow(service)
    )

    import threading

    afk_state: dict = {"stop": None, "thread": None}

    def start_afk() -> None:
        if afk_state["thread"] is not None and afk_state["thread"].is_alive():
            return
        from .afk import AfkLoop
        from .app_service import _platform_session
        from .config import load_config
        from .recognizer import default_ocr_words

        try:
            config = load_config(service.config_path)
            session = _platform_session(config)
        except Exception as error:
            view.show_error(f"无法开始挂机：{error}")
            return
        try:
            service.request_stop()
        except Exception:
            pass
        stop = threading.Event()

        def on_status(status) -> None:
            names = {"running": "运行中", "paused": "已暂停", "stopped": "已停止"}
            text = names.get(status.state, status.state)
            if status.reason:
                text += f"（{status.reason}）"
            if status.last_action:
                text += f"｜{status.last_action}"
            if not view._destroyed:
                view.root.after(0, lambda value=text: view.set_afk_summary(value))

        loop = AfkLoop(
            session=session,
            ocr_words=default_ocr_words,
            keymap=dict(config.keymap),
            stop_event=stop,
            on_status=on_status,
            runtime_dir=service.ensure_runtime_directory(),
        )

        def work() -> None:
            try:
                loop.run()
            except Exception as error:
                if not view._destroyed:
                    view.root.after(
                        0,
                        lambda value=f"已停止（{error}）": view.set_afk_summary(value),
                    )
            finally:
                if not view._destroyed:
                    view.root.after(0, reset_afk_buttons)

        afk_state["stop"] = stop
        afk_state["thread"] = threading.Thread(
            target=work, name="wendao-afk", daemon=True
        )
        view.buttons["afk_start"].configure(state="disabled")
        view.buttons["afk_stop"].configure(state="normal")
        view.set_afk_summary("启动中…")
        afk_state["thread"].start()

    def reset_afk_buttons() -> None:
        view.buttons["afk_start"].configure(state="normal")
        view.buttons["afk_stop"].configure(state="disabled")

    def stop_afk() -> None:
        if afk_state["stop"] is not None:
            afk_state["stop"].set()
        view.set_afk_summary("停止中…")

    view.buttons["afk_start"].configure(command=start_afk)
    view.buttons["afk_stop"].configure(command=stop_afk)
    view.buttons["capture_template"].configure(
        command=lambda: view.run_capture_flow(service)
    )

    view.root.protocol("WM_DELETE_WINDOW", on_close)
    view.root.after(200, poll)
    retained = (view, controller)
    return view.root, retained


def _startup_config_path() -> Path | None:
    from .app_service import default_runtime_path

    detected = default_runtime_path() / "detected-config.yaml"
    return detected if detected.is_file() else None


def main() -> None:
    if sys.platform == "win32":
        from .session_windows import _ensure_dpi_awareness

        _ensure_dpi_awareness()
    if sys.platform == "darwin":
        service = AppService()
        app, retained = _build_native_app(service)
        view, controller = retained[0], retained[1]
        controller.launch()
        view.window.makeKeyAndOrderFront_(None)
        app.activateIgnoringOtherApps_(True)
        app.run()
        return
    service = AppService(config_path=_startup_config_path())
    root, retained = _build_tk_app(service)
    view, controller = retained[0], retained[1]
    controller.launch()
    root.mainloop()

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
        "choose_config": "选择配置",
        "runtime_folder": "运行目录",
        "notification_preview": "通知预览",
    }
    _BUTTON_ACTIONS = {
        "observe": "observe",
        "single_step": "single_step",
        "continuous": "continuous",
        "pause": "pause",
        "resume": "resume",
        "stop": "stop",
        "choose_config": "choose_config",
        "runtime_folder": "open_runtime_folder",
        "notification_preview": "preview_notification",
    }
    _GATED_BUTTONS = ("single_step", "continuous")

    def __init__(self) -> None:
        import tkinter as tk

        self._destroyed = False
        self.root = tk.Tk()
        self.root.title("问道前台助手")
        self.labels = {}
        fields = tk.Frame(self.root, padx=24, pady=16)
        fields.pack(fill="x")
        for row, (key, title) in enumerate(self._FIELD_TITLES.items()):
            caption = tk.Label(fields, text=f"{title}：", width=12, anchor="w")
            caption.grid(row=row, column=0, sticky="w")
            value = tk.Label(fields, text="—", anchor="w")
            value.grid(row=row, column=1, sticky="w")
            self.labels[key] = value
        self.buttons = {}
        actions = tk.Frame(self.root, padx=24, pady=16)
        actions.pack(fill="x")
        for index, (key, title) in enumerate(self._BUTTON_TITLES.items()):
            row, column = divmod(index, 3)
            button = tk.Button(actions, text=title, width=12)
            button.grid(row=row, column=column, padx=4, pady=4)
            self.buttons[key] = button

    def install_buttons(self, controller) -> None:
        for key, action in self._BUTTON_ACTIONS.items():
            self.buttons[key].configure(command=getattr(controller, action))

    def render(self, state: AppViewState) -> None:
        model = display_model(state)
        for key, value in model.fields.items():
            self.labels[key].configure(text=value)
        for key in self._GATED_BUTTONS:
            self.buttons[key].configure(
                state="normal" if model.buttons[key] else "disabled"
            )

    def show_error(self, message: str) -> None:
        from tkinter import messagebox

        messagebox.showerror("问道前台助手", message, parent=self.root)

    def choose_config(self) -> Path | None:
        from tkinter import filedialog

        selected = filedialog.askopenfilename(parent=self.root)
        return Path(selected) if selected else None

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
        if controller.begin_termination():
            view.finish_termination()

    view.root.protocol("WM_DELETE_WINDOW", on_close)
    view.root.after(200, poll)
    retained = (view, controller)
    return view.root, retained


def main() -> None:
    service = AppService()
    if sys.platform == "darwin":
        app, retained = _build_native_app(service)
        view, controller = retained[0], retained[1]
        controller.launch()
        view.window.makeKeyAndOrderFront_(None)
        app.activateIgnoringOtherApps_(True)
        app.run()
        return
    root, retained = _build_tk_app(service)
    view, controller = retained[0], retained[1]
    controller.launch()
    root.mainloop()

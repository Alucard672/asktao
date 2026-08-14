# 开发与构建说明

> 2026-08 起本项目为双平台:核心模块跨平台共用,平台层新增 `src/wendao_bot/session_windows.py`(Win32 会话,ctypes)、`recognizer.py` 的 `windows_ocr` 后端(winsdk)、`app.py` 的 tkinter 视图与 `notifier.py` 的 Windows 气泡通知分支。Windows 打包为 `packaging/wendao_app_win.spec` + `scripts/build_app.ps1`,合同测试见 `tests/test_packaging.py` 与 `tests/test_session_windows.py`。以下 macOS 说明仍然有效。

## 架构边界

原生 AppKit 窗口只是一个保守控制面。`AppController` 将按钮动作交给 `AppService`；服务拥有唯一后台线程、停止事件、模式代际和经过脱敏的 `AppViewState` 队列。服务不复制 CLI 行为，而是调用 `cli.run_command`。该共享入口负责只观察循环、实时运行前 30 秒预检以及单步循环；它通过 `build_runner` 构造真正的 `Orchestrator`。编排器之下才是窗口会话、识别、任务规划、安全授权、持久化和通知边界。

```text
AppKit view -> AppController -> AppService -> cli.run_command -> Orchestrator
                                      |                              |
                              sanitized state                capture / recognize /
                                  queue                      authorize / click / log
```

这条边界保证 GUI 与 CLI 使用相同的预检和点击规则。观察、预检观察器和同一代实时 runner 共用同一个 `threading.Event`；模式切换先设置旧事件，再由后台转换线程等待旧 worker 结束，确认没有用户停止、关闭或更新代际后才启动新模式，因此 AppKit 主线程不 join，也不会重叠 runner。连续模式只能由本会话中返回成功点击 `StepResult` 的单步运行解锁。GUI 通过 `build_app_runner` 构造编排器，强制通知器为 dry-run，因此任何自动暂停都不会调用 `osascript`。CLI 则通过默认 `build_runner` 使用真实通知器：`observe`/`run` 的自动暂停和不带 `--dry-run` 的 `notify-test` 都可能调用 `osascript` 发送配置收件人的 iMessage，必须在运行前取得明确许可并处理终端的 macOS Automation 权限。

GUI 启动 runner 前以 Quartz 只读窗口列表探测精确配置标题和预期 owner；仅在唯一可信匹配存在时公开有界整数宽高。几何配置导出拒绝符号链接并原子替换目标文件。`AXIsProcessTrusted()` 只读检查通过依赖注入进入状态模型：观察保持可用，单步和连续按钮则在 Accessibility 未授权时失败关闭，且不会主动请求权限。

## 关键文件

- `src/wendao_bot/app.py`：AppKit 界面、显示字段、按钮启用规则和控制器。
- `src/wendao_bot/app_service.py`：线程生命周期、模式门禁、配置切换、停止代际、通知预览和界面状态脱敏。
- `src/wendao_bot/cli.py`：CLI 参数、共享 `run_command`、预检和运行循环。
- `src/wendao_bot/orchestrator.py`：一次观察/计划/授权/输入/验证周期。
- `src/wendao_bot/app_model.py`：GUI 可见状态的不可变、失败关闭校验。
- `packaging/wendao_app.spec`：PyInstaller 应用包定义和运行依赖。
- `scripts/build_app.sh`：清理构建、生成 `.app` 和 zip 的唯一构建入口。
- `tests/test_app_command_integration.py`：从 `AppService` 穿过真实 `run_command` 的无界面集成测试；仅 runner/驱动后端是脚本化的。

## 开发环境和测试

要求 Python 3.11+。在仓库根目录执行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[app,test]'
python -m pytest -v
python -m compileall -q src scripts tests
```

测试分层包括纯模型与安全规则单元测试、CLI/编排器测试、AppKit 无关的控制器测试、真实 `AppService -> run_command` 边界测试，以及会实际调用构建脚本的打包合同测试。测试用合成画面和脚本化后端不会替代真实客户端模板、权限、窗口几何与用户监督单步验证。

构建脚本的 shell 语法可单独检查：

```bash
zsh -n scripts/build_app.sh
```

## 构建、检查和发布限制

```bash
zsh scripts/build_app.sh
```

输出为 `dist/问道前台助手.app` 和 `dist/问道前台助手.zip`。开发验收至少应检查：

```bash
codesign --verify --deep --strict --verbose=2 'dist/问道前台助手.app'
ditto -x -k 'dist/问道前台助手.zip' /path/to/empty-temp-directory
```

还应核对 `Contents/Info.plist`、可执行文件权限、打包配置和模板目录，并记录 artifact 的大小及 SHA-256。当前 PyInstaller 配置使用 `target_arch=None`，所以生成当前主机架构构建；项目当前验证的是 `arm64`。PyInstaller 可能施加 ad-hoc 签名以满足本机加载要求，但这不等于 Apple Developer ID 发布签名。对外分发仍缺少证书签名、加固运行时、公证和 stapling。

本地首次打开若被 Gatekeeper 拦截，使用 Finder 的右键/Control 点按“打开”流程，不要建议关闭全局安全机制。屏幕录制和辅助功能必须授予实际运行应用。安全烟雾测试仅启动应用、确认进程/窗口短暂存在并退出；应用会自动进入只读观察，因此测试期间不得点击“单步执行”“连续运行”或触发真实通知。

## 外部验证项

当前真实窗口报告为 919×674，而打包默认配置为 886×672；真实模板也尚未提供。必须针对最终固定尺寸采集并人工复核模板，再在真实客户端上完成观察和单步授权验证。几何不匹配、权限缺失或模板不可用导致的失败关闭属于预期安全结果，不能通过降低阈值或绕过检查解决。

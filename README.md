# 问道前台辅助工具(Windows / macOS)

这是一个保守型前台自动化原型,现已移植到 Windows。它只操作标题、进程身份和固定尺寸均匹配的单个《问道》窗口;识别置信度不足、页面未知、窗口被遮挡、失去前台或出现付费、验证码、死亡、背包满等状态时会失败关闭并暂停。使用前请确认游戏规则允许自动化。

核心编排、安全规则、任务规划与测试套件跨平台共用;平台层按系统自动选择:

| 能力 | Windows | macOS |
| --- | --- | --- |
| 窗口定位/校验 | Win32(EnumWindows + 客户区几何 + 前台/遮挡采样) | Quartz |
| 截屏 | mss(物理像素,进程为 Per-Monitor DPI Aware) | Quartz |
| OCR | Windows.Media.Ocr(winsdk,需系统中文语言包) | Vision |
| 点击 | SendInput | CGEvent |
| 图形界面 | tkinter | AppKit |
| 暂停通知 | 本地气泡通知(不发送消息) | iMessage(仅 CLI) |

## Windows 安装

需要 Python 3.11–3.13(推荐 3.12;winsdk 尚无 3.14 轮子)。在项目目录:

```bash
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -e ".[app,test]"
.venv\Scripts\python -m pytest -v
```

Windows OCR 需要系统安装中文(简体)语言包:设置 → 时间和语言 → 语言和区域 → 添加"中文(简体,中国)"。OCR 引擎不可用时工具会失败关闭,不会静默降级。

Windows 不需要 macOS 式的屏幕录制/辅助功能授权;不要以管理员身份运行模拟器,否则普通权限的本工具无法向其注入点击(这是预期的失败关闭)。

## Windows 配置(安卓模拟器)

Windows 场景下手游《问道》运行在安卓模拟器中,本工具定位、校验和操作的都是**模拟器窗口**。

**推荐方式——一键检测**:启动模拟器(不要最小化),在图形界面点击"检测模拟器",从候选列表中选择实例,工具会自动检测标题、进程名和客户区尺寸,生成配置并立即载入,无需手写任何文件。生成的配置保存在运行目录的 `detected-config.yaml`,之后调整模拟器窗口大小需要重新检测。

**手动方式**:除了标题和固定宽高,Windows 上必须配置 `window.owner`:模拟器进程的可执行文件名,用于进程身份校验,缺失时工具会以配置错误拒绝启动:

```yaml
window:
  title: MuMu模拟器12
  owner: MuMuPlayer.exe
  width: 1600
  height: 900
```

常见模拟器进程名:MuMu 模拟器 12 → `MuMuPlayer.exe`,雷电 → `dnplayer.exe`,夜神 → `Nox.exe`,BlueStacks → `HD-Player.exe`;以任务管理器 → 详细信息中看到的为准。完整示例见 `config/windows-example.yaml`。

模拟器侧准备:

1. 在模拟器设置中固定分辨率(手动指定,关闭"自适应/跟随窗口"),记下窗口大小并不再改动;
2. 窗口标题必须与配置完全一致(逐字符,含空格)且在系统中唯一。多开实例的标题(如 `MuMu安卓设备`、`MuMu安卓设备 -1`、`MuMu安卓设备 -2`)彼此不同,精确匹配互不冲突;用下面的命令列出真实标题直接复制,避免手抄空格出错:

   ```bash
   python scripts/list_windows.py MuMu
   ```

3. 多开时为每个实例建一份配置(如 `config/mumu-1.yaml`、`config/mumu-2.yaml`,只有 `window.title` 不同),CLI 再为每个实例指定独立的 `--runtime` 目录。**同一时刻只能自动化一个实例**:本工具是前台工具,要求目标窗口保持前台且完全无遮挡,这是不可绕过的安全设计;其他实例可以开着,但不得覆盖目标窗口,切换目标实例就换配置重新走单步验证;
4. 关闭模拟器的悬浮球、侧边栏广告弹窗等会遮挡画面的组件;工具检测到窗口被遮挡或失去前台会按设计暂停;
5. 模拟器不要以管理员身份运行,否则普通权限的本工具无法向其注入点击(预期失败关闭)。

宽高校验的是模拟器客户区物理像素;首次运行时几何不匹配的报错会显示实际检测到的宽高,照抄进配置即可。修改系统 DPI 缩放或模拟器窗口尺寸后必须重新核对,并为该尺寸重新采集全部识别模板。**真实客户端上的模板采集与用户监督验证仍是外部阻塞项**;在完成前,模板就绪指标会失败关闭,不能把离线测试结果当作实时运行许可。点击通过 SendInput 注入鼠标事件,由模拟器自行映射为触屏操作。

## 构建 Windows 应用

```bash
powershell -ExecutionPolicy Bypass -File scripts\build_app.ps1
```

产物:

```text
dist\问道前台助手\问道前台助手.exe
dist\问道前台助手-win.zip
```

本地构建没有代码签名:首次运行 SmartScreen 可能拦截("更多信息"→"仍要运行"),部分杀毒软件对 PyInstaller 打包的未签名程序有误报,请自行加白名单,不要关闭系统防护。正式分发需要代码签名证书。

## 图形应用使用流程(两平台一致)

应用启动后默认自动进入"仅观察",不会点击。窗口中的"窗口 / 连接""OCR""模板""观察就绪"等是前置就绪指标;识别状态、置信度和目标名称用于人工核对,"运行状态"和"暂停原因"用于判断是否已失败关闭。

1. 用"选择配置"选择自定义 YAML;切换配置会先停止旧运行,再验证配置,并重新进入只观察。
2. 保持游戏窗口完整可见并处于前台,观察状态、置信度和目标至少 30 秒。
3. 点击"单步执行":执行完整的 30 秒只观察预检,最多执行一次点击。只有一个成功且状态仍为 `running` 的点击结果才会在本次会话中解锁"连续运行"。
4. 人工复核单步结果后,才可点击"连续运行"。切换配置或重启应用后必须重新通过单步验证。

"暂停"保留当前进程供检查,"停止"是紧急停止入口并会锁住后续恢复。"通知预览"只显示收件人与正文,图形应用在任何模式下都不会真实发送通知。

## CLI 安全运行流程

```bash
wendao-bot observe --config C:\absolute\path\config.yaml
wendao-bot run --single-step --config C:\absolute\path\config.yaml
wendao-bot run --config C:\absolute\path\config.yaml
wendao-bot pause
wendao-bot resume
wendao-bot stop
```

观察模式绝不点击;`run` 自带 30 秒只观察预检,任一不合格样本重置就绪状态,只有截止前最后至少 3 个连续可操作样本都有可信 OCR 与模板证据才会进入单次点击。逐步复核通过后才可连续运行。控制命令通过运行目录 `control/` 下的标志文件生效;紧急停止用 `Ctrl-C` 加另一终端的 `wendao-bot stop`,不要依赖关闭游戏窗口。

通知行为的平台差异:macOS CLI 在自动暂停时会尝试向 `notification.recipient` 发送真实 iMessage(运行前必须核对收件人并取得发送许可);**Windows 上没有 iMessage,自动暂停改为显示本地气泡通知,不向任何人发送消息**,`notification.recipient` 仅出现在通知正文预览中。`wendao-bot notify-test --dry-run` 在两个平台都只打印收件人与正文。

## 模板采集

采集工具只截图、不点击;先查看参数并使用 dry-run:

```bash
python scripts/capture_template.py --help
python scripts/capture_template.py --config C:\absolute\path\config.yaml --state map --target main_quest --box 0 0 100 40 --scale 1x --dry-run
```

采集工具使用配置中的标题和宽高,并按配置尺寸等比扩大隐私禁区;OCR 检出邮箱、电话等私密文本或裁剪区与禁区重叠时拒绝保存。日常目标用 `--target daily` 加 `--daily-name` 指定白名单中的名称。坐标必须从当前固定尺寸画面重新测量,不要跨尺寸复用模板。

## 日志和运行数据

默认运行目录:

- Windows:`%APPDATA%\WendaoBot\`
- macOS:`~/Library/Application Support/WendaoBot/`

`state.json` 保存暂停原因和可信进度,`events.jsonl` 保存经过敏感字段过滤的事件,`screens/` 最多保留 20 张最近截图,`control/` 保存暂停、恢复和停止标志。事件和界面不显示 OCR 原文、坐标或通知隐私字段,但截图仍可能包含角色名、聊天等个人信息;分享前必须人工检查脱敏,不要把运行截图提交到仓库。

## 已知限制

- 只支持一个完全可见、尺寸固定且标题唯一的游戏窗口;不支持双开或多开。
- 不支持任意缩放、任意窗口尺寸或跨尺寸复用模板;Windows 上修改 DPI 缩放后必须重新采集模板。
- 不处理付费、验证码、加点、死亡、背包满、断线和未知页面;这些状态必须暂停并由人接管。
- 不会规避前台、遮挡、窗口身份、置信度、预检或点击后状态校验;模拟器以管理员运行时点击注入会失败。
- 仍需在真实客户端上完成用户监督的模板采集和单步确认,离线测试不能替代这些检查。

## 开发文档

架构、关键文件、测试层次见 [`docs/development.md`](docs/development.md)。macOS 构建入口为 `zsh scripts/build_app.sh`(需要本机 `.venv` 含 pyobjc 与 PyInstaller)。设计文档保留在 `docs/superpowers/` 下供追溯设计边界。

# AI 驱动的微信自动回复系统

<br />


## 1. 声明
本项目通过模拟鼠标点击实现控制，通过截图进行文本识别，复制粘贴AI回复到微信窗口进行回复。

仅供个人学习与自动化测试使用，请遵守微信使用条款与当地法律法规。

温馨提示：该项目有微信强制登出与封号风险，请谨慎使用。



## 2. 安装

### 2.1 选择运行环境

本项目推荐使用Python 3.12.13版本。

命令行界面启动：
```bat
:: 1 装依赖（首次）
python daemon.py --install
:: 2 运行命令行界面
python daemon.py 
```

可视化界面启动：

```bat
:: 1 装依赖（首次）
python daemon.py --install
:: 2 运行可视化界面
python gui.py
```

### 2.2 `daemon.py --install` 内部行为

会打印当前 Python 的完整路径 + 目标 site-packages，避免装错环境：

如果失败会给出 3 条常见原因（环境选错 / 文件被锁 / 权限）。

### 2.3 依赖清单

主要依赖（[requirements.txt](file:///e:/project/Afiles/requirements.txt)）：

- `pygetwindow`、`pyautogui`、`pyperclip`、`Pillow`、`numpy`
- `cnocr==2.3.3`、`cnstd==1.2.8`、`opencv-python-headless`
- `openai>=2.0`（默认走 OpenAI 兼容协议；Ollama / vLLM / OneAPI 通用）
- `ollama`（可选：旧 SDK 路径）
- 标准库 `tkinter`

## 3. 配置 `wechat_config.json`

```jsonc
{
  "window_title": "微信",
  "window_position": [-10, 0],
  "window_size": [800, 600],
  "contacts_region": [110, 50, 130, 550],
  "username_region": [300, 0, 200, 50],
  "chat_region": [300, 70, 470, 330],
  "message_input_position": [650, 500],
  "black_text_threshold": 210,        // 亮度 < 210 视为黑色 (联系人名)
  "gray_text_threshold": 210,         // 亮度 >= 210 视为灰色 (最近消息, 忽略)
  "check_interval": 5,
  "max_contacts_to_check": 10,
  "debug_mode": true,
  "cache_timeout": 300,
  "data_dir": "data",
  "ai": {
    "provider": "openai_compat",     // openai_compat | ollama_sdk
    "base_url": "http://127.0.0.1:11434/v1",
    "api_key": "ollama",              // Ollama 兼容模式下填什么都行
    "model": "deepseek-r1:7b",
    "timeout": 60,
    "system_prompt": "你的名字叫做小镜...每句话的末尾加一个\"喵~\".",
    "temperature": 0.7,
    "max_tokens": 512
  },
  "guardian": {
    "max_consecutive_errors": 5,
    "cooldown_on_burst": 30,
    "wechat_recheck_interval": 60,
    "max_idle_rounds": 0              // 0 = 无限轮询
  }
}
```

> 想用 vLLM/OneAPI/OpenAI：改 `ai.base_url` / `ai.api_key` / `ai.model` 即可。
> 想用回老 ollama SDK：`ai.provider = "ollama_sdk"`。

## 4. 命令行

> **入口拆分**：
> - GUI 控制器：`python gui.py` （或 `python -m gui`）
> - CLI / 守护：`python daemon.py --daemon` / `--once` / `--test` 等
> 
> 不再通过 `main.py` 启动任何东西（已重命名为 `daemon.py`）。

```bash
# 查看所有选项
.venv\Scripts\python.exe daemon.py --help

# ====== GUI 控制器 (独立入口) ======
.venv\Scripts\python.exe gui.py          # 或 python -m gui

# ====== 命令行 / 守护 (daemon.py) ======

# 长驻守护 (Ctrl+C 退出)
.venv\Scripts\python.exe daemon.py --daemon

# 单轮处理（调试 / 计划任务）
.venv\Scripts\python.exe daemon.py --once

# 5 秒后取鼠标位置校准输入框
.venv\Scripts\python.exe daemon.py --calibrate

# 打开 Tkinter 可视化校准窗口（旧版单窗口）
.venv\Scripts\python.exe daemon.py --calibrate-gui

# 单独跑组件自检
.venv\Scripts\python.exe daemon.py --test window
.venv\Scripts\python.exe daemon.py --test ocr
.venv\Scripts\python.exe daemon.py --test message
.venv\Scripts\python.exe daemon.py --test ai
.venv\Scripts\python.exe daemon.py --test all

# 重置配置
.venv\Scripts\python.exe daemon.py --reset-config

# 安装依赖
.venv\Scripts\python.exe daemon.py --install

# 打印当前配置
.venv\Scripts\python.exe daemon.py --show-config
```

## 5. 可视化校准（`--calibrate-gui` 旧版 / GUI 校准 tab 新版）

#### 5.0.1 新版校准 tab（推荐）

GUI 控制器中的「🎯 校准」tab 提供：

- **🖱 拖选 / 取点** 工具栏
  - `📦 联系人区域 / 用户名区域 / 聊天记录区域`：拉起一个全屏透明遮罩，**鼠标拖动**框选区域，松手自动写回 (x, y, w, h)
  - `🖱 点击取输入框`：全屏遮罩下**单击**取输入框位置
  - `Esc` / `右键` 取消
- **🖼 全屏预览**：抓取整个微信窗口，**叠加 3 个区域彩色框**（联系人=绿 / 用户名=蓝 / 聊天=橙）
- **📐 3 个小预览**：3 区域独立截图轮询（2s）
  - 按 `ⓞ OCR` 跑一次 OCR + 分类，叠加在对应小图上
  - 调阈值 Spinbox 立即重新着色（无需重跑 OCR）
- **↶ 撤销**：保存前可回退到上次状态
- 💾 / 🎯 激活微信 / 拖选 / 取点 一应俱全

#### 5.0.2 旧版 `--calibrate-gui`

简单的单窗口校准工具，保留兼容：
- 显示 `contacts_region / username_region / chat_region` 实时截图
- Spinbox 调整 (x, y, w, h) 任意区域
- 一键"校准输入框(5s)"抓取鼠标位置写入 `message_input_position`
- "测试 OCR" 按钮立刻对当前区域跑 CnOCR，结果打印在下方日志框
- 任何修改即时落盘 `wechat_config.json`

### 5.1 GUI 控制器（`gui.py`）

`python gui.py` 启动一个 Tkinter Notebook 控制器，包含 **8 个**标签页：

| 标签页    | 作用                                                |
| ------ | ------------------------------------------------- |
| 🏠 仪表盘 | 守护状态 / 启停 / 立即回复 / 聊天总结 (今天/昨天/近 7 天) / 黑名单独立栏     |
| ⚙ 配置   | 可视化编辑 `wechat_config.json`（窗口/区域/AI/守护/IPC/白名单/知识库 7 子 tab） |
| 🎯 校准  | 拖选 + 全屏预览 + 3 区叠加 + 3 小图 + 阈值联动 + 撤销栈                 |
| 🛠 调试  | OCR 实时测试 + 临时阈值滑块 + 标注图                           |
| 📜 日志  | 实时日志尾随（支持级别/关键词过滤）                                |
| 📚 历史  | 聊天记录 / AI 回复历史浏览                                  |
| 🚫 名单  | 白/黑名单管理器                                          |
| 📖 知识库 | 编辑知识库文件 / 检索测试                                     |

**工作模式**：

- **GUI 自己可启动/停止守护进程**：仪表盘的「▶ 启动守护 / ⏹ 停止守护」按钮直接 `subprocess.Popen` 启 `python daemon.py --daemon`（隐藏黑窗，输出重定向到 `data/logs/daemon_stdout.log`）
- 也可手动在另一个终端 `python daemon.py --daemon` 启动
- 两者之间通过 `data/runtime/` 下的 JSON 文件通信（详见 `wechat_bot/ipc.py`）
- GUI 可发送 `trigger_reply` / `run_once` / `pause` / `resume` / `reload_kb` 等指令
- 配置页 / 知识库页保存后守护进程下一轮生效（部分字段需重启）

## 6. 模块 API 速查

```python
from wechat_bot import (
    BotConfig,           # 配置 dataclass + load/save/reset
    get_logger,          # 统一日志
    WindowManager,       # 微信窗口 find/activate/move/screenshot
    OcrEngine,           # recognize(image) -> [{text, score, position}]
    AIClient,            # chat(user_text, system_prompt, use_cache)
    Storage,             # data/ 分层
    ReplyEngine,         # 主流程 run_once()
    Guardian,            # 守护 run() / stop()
)

cfg = BotConfig.load()
storage = Storage(cfg.data_dir)
ocr = OcrEngine(cfg.cache_timeout)
ai = AIClient(cfg.ai, cfg.cache_timeout)
window = WindowManager(cfg.window_title)
engine = ReplyEngine(cfg, ocr, ai, window, storage)

# 单轮
engine.run_once()

# 长驻
guardian = Guardian(cfg, window, engine.run_once)
guardian.run()
```

## 7. 目录结构

```
.
├── daemon.py                      # CLI / 守护 入口 
├── gui.py                          # GUI 控制器 独立入口
├── requirements.txt
├── wechat_config.json              # 运行时自动生成
├── wechat_bot/
│   ├── __init__.py
│   ├── config.py                   # BotConfig + AIConfig + GuardianConfig
│   ├── logger.py                   # 统一日志 (data/logs/)
│   ├── color_utils.py              # 亮度分类 / 重叠去重
│   ├── window.py                   # WindowManager (find/activate/move/screenshot)
│   ├── ocr_engine.py               # CnOCR 封装 + 图像指纹缓存
│   ├── ai_client.py                # OpenAI 兼容 + Ollama SDK 双后端
│   ├── storage.py                  # data/ 分层 + 旧版迁移
│   ├── reply_engine.py             # 识别→AI→发送 主流程
│   ├── knowledge.py                # RAG 知识库 (top-k 检索)
│   ├── whitelist.py                # 白/黑名单
│   ├── ipc.py                      # GUI 与守护进程的文件 IPC
│   └── guardian.py                 # 长驻守护 + 异常自愈
├── gui/
│   ├── __init__.py
│   ├── app.py                      # GUI 主框架 (Notebook + 8 tab)
│   ├── widgets.py                  # 共享组件 (LogTail / StatusBar / ScrolledFrame / ...)
│   ├── overlay.py                  # 拖选 / 取点 全屏遮罩
│   ├── dashboard_tab.py            # 仪表盘 (状态/启停/聊天总结/黑名单)
│   ├── config_tab.py               # 配置 (7 子 tab)
│   ├── calibrate_tab.py            # 校准 (拖选 + 全屏预览 + 3 小图)
│   ├── debug_tab.py                # 调试 (OCR 实时 + 临时阈值)
│   ├── log_tab.py                  # 日志
│   ├── history_tab.py              # 历史
│   ├── blacklist_tab.py            # 白/黑名单
│   ├── knowledge_tab.py            # 知识库编辑
│   └── calibrator.py               # 旧版单窗口校准
└── data/                           # 首次启动自动创建
    ├── history/<user>_<ts>.txt
    ├── replies/<user>_ai_reply_<ts>.txt
    ├── debug/<name>.png
    ├── knowledge.txt               # 知识库文件
    ├── replied_cache.json          # 每用户最近 8 条回复 (防回复自己)
    ├── runtime/                    # GUI <-> 守护 文件 IPC
    └── logs/wechat_auto_reply.log
```
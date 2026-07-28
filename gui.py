"""GUI controller entry point.

用法:
    python gui.py
    python -m gui

启动后是 8 个 tab 的 GUI 控制器 (仪表盘 / 配置 / 校准 / 调试 / 日志 / 历史 / 名单 / 知识库).
守护进程由 GUI 内的"启动守护"按钮调起 (subprocess.Popen 启 python daemon.py --daemon),
或单独在终端跑 `python daemon.py --daemon`.

注意: 不要通过 daemon.py 启动 GUI. CLI 入口见 daemon.py.
"""
from __future__ import annotations

import sys

from wechat_bot.logger import get_logger

log = get_logger("gui.entry")


def main() -> int:
    try:
        from gui.app import App
    except Exception as e:
        log.error(f"导入 gui.app 失败: {e}")
        return 1
    import tkinter as tk

    from wechat_bot.config import BotConfig

    cfg = BotConfig.load()
    root = tk.Tk()
    app = App(root, cfg)
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())

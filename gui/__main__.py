"""`python -m gui` 入口.

与根目录 ``gui.py`` 行为一致; 提供 ``python -m gui`` 调用方式.
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


main()  # noqa: E402
sys.exit(0)

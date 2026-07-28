"""Log tab: real-time log tail (delegates to widgets.LogTail)."""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk

from gui.widgets import LogTail
from wechat_bot.config import BotConfig


class LogTab(ttk.Frame):
    def __init__(self, master: tk.Misc, cfg: BotConfig):
        super().__init__(master, padding=4)
        log_path = os.path.join(cfg.data_dir, "logs", "wechat_auto_reply.log")
        # 兼容旧版根目录的 wechat_auto_reply.log
        legacy = "wechat_auto_reply.log"
        path = log_path if os.path.exists(log_path) else (legacy if os.path.exists(legacy) else log_path)
        self.tail = LogTail(self, path, max_lines=5000, poll_ms=1000)
        self.tail.pack(fill=tk.BOTH, expand=True)

    def stop(self) -> None:
        self.tail.stop()

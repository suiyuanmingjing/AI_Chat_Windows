"""Unified logging facade.

用法:
    from wechat_bot.logger import get_logger
    log = get_logger("WeChatBot")
    log.info("hello")
"""
from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from typing import Optional

_DEFAULT_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
_CONFIGURED = False
_DATA_DIR = "data"


def configure_logging(
    data_dir: str = _DATA_DIR,
    level: int = logging.INFO,
    log_filename: str = "wechat_auto_reply.log",
    max_bytes: int = 2 * 1024 * 1024,
    backup_count: int = 3,
) -> None:
    """Initialize root logging once for the whole process."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    log_dir = os.path.join(data_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, log_filename)

    formatter = logging.Formatter(_DEFAULT_FORMAT)

    # 兼容旧版本：旧日志写在项目根，也允许读取
    legacy_path = log_filename  # 原 main.py / daemon.py 把日志写在 cwd
    handlers = []

    fh = RotatingFileHandler(
        log_path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
    )
    fh.setFormatter(formatter)
    handlers.append(fh)

    # 旧版日志（追加模式，向后兼容）
    if os.path.abspath(legacy_path) != os.path.abspath(log_path):
        try:
            legacy_fh = logging.FileHandler(legacy_path, mode="a", encoding="utf-8")
            legacy_fh.setFormatter(formatter)
            handlers.append(legacy_fh)
        except Exception:
            # 文件被占用等场景下容错
            pass

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(formatter)
    handlers.append(sh)

    root = logging.getLogger()
    root.setLevel(level)
    # 清理已有 handler，避免重复输出
    for h in list(root.handlers):
        root.removeHandler(h)
    for h in handlers:
        root.addHandler(h)

    _CONFIGURED = True


def get_logger(name: str, data_dir: str = _DATA_DIR) -> logging.Logger:
    """Return a logger, configuring root logging on first call."""
    configure_logging(data_dir=data_dir)
    return logging.getLogger(name)

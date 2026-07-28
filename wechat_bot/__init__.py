"""WeChat auto-reply bot (modular rebuild).

Public entry points:
    BotConfig          - configuration model
    BotLogger          - logging facade
    WindowManager      - WeChat window control
    OcrEngine          - CnOCR wrapper
    AIClient           - OpenAI-compatible chat client
    Storage            - data/ directory persistence
    ReplyEngine        - identify -> AI -> send pipeline
    Guardian           - long-running supervisor with auto-recovery
    IPCPaths           - shared file paths for GUI/daemon communication
    DaemonReporter     - daemon-side status/control writer
    GUIController      - GUI-side status reader / command writer
    ContactFilter      - whitelist/blacklist management
    KnowledgeBase      - RAG-style knowledge base (新增 v2.4)
"""

from wechat_bot.config import BotConfig
from wechat_bot.logger import get_logger
from wechat_bot.window import WindowManager
from wechat_bot.ocr_engine import OcrEngine
from wechat_bot.ai_client import AIClient
from wechat_bot.storage import Storage
from wechat_bot.reply_engine import ReplyEngine
from wechat_bot.guardian import Guardian
from wechat_bot.ipc import (
    IPCPaths,
    DaemonReporter,
    GUIController,
)
from wechat_bot.whitelist import ContactFilter
from wechat_bot.knowledge import KnowledgeBase

__all__ = [
    "BotConfig",
    "get_logger",
    "WindowManager",
    "OcrEngine",
    "AIClient",
    "Storage",
    "ReplyEngine",
    "Guardian",
    "IPCPaths",
    "DaemonReporter",
    "GUIController",
    "ContactFilter",
    "KnowledgeBase",
]

__version__ = "2.6.3"

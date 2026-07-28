"""Configuration model & persistence.

Backward compatible: 旧 main.py / daemon.py 中的 wechat_config.json 字段全部保留；
新增字段都有默认值；缺文件时落回默认配置并立刻写盘。
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Tuple

from wechat_bot.logger import get_logger

log = get_logger("wechat_bot.config")

CONFIG_FILE = "wechat_config.json"
DATA_DIR = "data"


# ----------------------------- default config ------------------------------

_DEFAULT_CONFIG: Dict[str, Any] = {
    # 微信窗口
    "window_title": "微信",
    "window_position": (-10, 0),
    "window_size": (800, 600),
    # 截图区域 (x, y, w, h)，相对屏幕
    "contacts_region": (110, 50, 130, 550),
    "username_region": (300, 0, 200, 50),
    "chat_region": (300, 70, 470, 330),
    "message_input_position": (650, 500),
    # 颜色阈值：亮度 < black 为黑色文字（联系人），>= gray 为灰色文字（最近消息）
    "black_text_threshold": 210,
    "gray_text_threshold": 210,
    # 行为
    "check_interval": 5,
    "max_contacts_to_check": 10,
    "debug_mode": True,
    "cache_timeout": 300,
    # 多行消息拆分发送: AI 返回的 reply 按 \n 拆成多段, 段间 sleep 这个秒数
    "line_send_interval": 0.6,
    # AI (OpenAI 兼容)
    "ai": {
        "provider": "openai_compat",   # openai_compat | ollama_sdk
        "base_url": "http://127.0.0.1:11434/v1",
        "api_key": "ollama",            # Ollama 兼容模式下随便填
        "model": "deepseek-r1:7b",
        "timeout": 60,
        "system_prompt": (
            "你的名字叫做小镜，正在与微信用户聊天。"
            "请生成友好、自然、有帮助的中文回复。"
            "回复要简洁明了，每句话的末尾加一个\"喵~\"。"
        ),
        "temperature": 0.7,
        "max_tokens": 512,
    },
    # 守护
    "guardian": {
        "max_consecutive_errors": 5,
        "cooldown_on_burst": 30,
        "wechat_recheck_interval": 60,
        "max_idle_rounds": 0,  # 0 = 无限轮询
    },
    # GUI / 守护间 IPC（文件方式）
    "ipc": {
        "runtime_dir": "runtime",           # 相对 data_dir
        "status_file": "daemon_status.json",
        "control_file": "control.json",
        "whitelist_file": "whitelist.txt",
        "blacklist_file": "blacklist.txt",
        "pause_flag": "pause.flag",
        "status_ttl": 30,  # 秒；GUI 看到这个时间没更新就认为守护挂了
    },
    # 白黑名单行为
    "whitelist": {
        "enabled": False,           # 是否启用白名单（启用后只回复名单内联系人）
        "case_sensitive": False,
    },
    # 知识库 (RAG 风格: 关键词检索 top-k 拼到 system_prompt 末尾)
    "kb": {
        "enabled": False,
        "file": "knowledge.txt",   # 相对 data_dir
        "top_k": 3,
        "min_score": 0.05,
        "max_chars_per_chunk": 400,
    },
    # 存储
    "data_dir": DATA_DIR,
}


@dataclass
class AIConfig:
    provider: str = "openai_compat"
    base_url: str = "http://127.0.0.1:11434/v1"
    api_key: str = "ollama"
    model: str = "deepseek-r1:7b"
    timeout: int = 60
    system_prompt: str = _DEFAULT_CONFIG["ai"]["system_prompt"]  # type: ignore[index]
    temperature: float = 0.7
    max_tokens: int = 512

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "AIConfig":
        merged = {**_DEFAULT_CONFIG["ai"], **(raw or {})}  # type: ignore[index]
        return cls(**merged)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class GuardianConfig:
    max_consecutive_errors: int = 5
    cooldown_on_burst: int = 30
    wechat_recheck_interval: int = 60
    max_idle_rounds: int = 0

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "GuardianConfig":
        merged = {**_DEFAULT_CONFIG["guardian"], **(raw or {})}  # type: ignore[index]
        return cls(**merged)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class WhitelistConfig:
    enabled: bool = False
    case_sensitive: bool = False

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "WhitelistConfig":
        merged = {**_DEFAULT_CONFIG["whitelist"], **(raw or {})}  # type: ignore[index]
        return cls(**merged)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class KBConfig:
    enabled: bool = False
    file: str = "knowledge.txt"
    top_k: int = 3
    min_score: float = 0.05
    max_chars_per_chunk: int = 400

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "KBConfig":
        merged = {**_DEFAULT_CONFIG["kb"], **(raw or {})}  # type: ignore[index]
        return cls(**merged)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class IPCConfig:
    runtime_dir: str = "runtime"
    status_file: str = "daemon_status.json"
    control_file: str = "control.json"
    whitelist_file: str = "whitelist.txt"
    blacklist_file: str = "blacklist.txt"
    pause_flag: str = "pause.flag"
    status_ttl: int = 30

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "IPCConfig":
        merged = {**_DEFAULT_CONFIG["ipc"], **(raw or {})}  # type: ignore[index]
        return cls(**merged)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class BotConfig:
    window_title: str = "微信"
    window_position: Tuple[int, int] = (-10, 0)
    window_size: Tuple[int, int] = (800, 600)
    contacts_region: Tuple[int, int, int, int] = (110, 50, 130, 550)
    username_region: Tuple[int, int, int, int] = (300, 0, 200, 50)
    chat_region: Tuple[int, int, int, int] = (300, 70, 470, 330)
    message_input_position: Tuple[int, int] = (650, 500)
    black_text_threshold: int = 210
    gray_text_threshold: int = 210
    check_interval: int = 5
    max_contacts_to_check: int = 10
    debug_mode: bool = True
    cache_timeout: int = 300
    line_send_interval: float = 0.6
    data_dir: str = DATA_DIR
    ai: AIConfig = field(default_factory=AIConfig)
    guardian: GuardianConfig = field(default_factory=GuardianConfig)
    ipc: IPCConfig = field(default_factory=IPCConfig)
    whitelist: WhitelistConfig = field(default_factory=WhitelistConfig)
    kb: KBConfig = field(default_factory=KBConfig)

    # ---- (de)serialization ----
    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "BotConfig":
        merged = {**_DEFAULT_CONFIG, **(raw or {})}
        ai = AIConfig.from_dict(merged.pop("ai", {}))
        guardian = GuardianConfig.from_dict(merged.pop("guardian", {}))
        ipc = IPCConfig.from_dict(merged.pop("ipc", {}))
        whitelist = WhitelistConfig.from_dict(merged.pop("whitelist", {}))
        kb = KBConfig.from_dict(merged.pop("kb", {}))
        return cls(
            ai=ai, guardian=guardian, ipc=ipc, whitelist=whitelist, kb=kb, **merged
        )

    # ---- file I/O ----
    def save(self, path: str = CONFIG_FILE) -> None:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
            log.info(f"配置已保存: {path}")
        except Exception as e:
            log.error(f"保存配置失败: {e}")

    @classmethod
    def load(cls, path: str = CONFIG_FILE) -> "BotConfig":
        if not os.path.exists(path):
            log.info(f"配置文件不存在，使用默认: {path}")
            cfg = cls()
            cfg.save(path)
            return cfg
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            cfg = cls.from_dict(raw)
            log.info(f"已加载配置: {path}")
            return cfg
        except Exception as e:
            log.error(f"配置加载失败，使用默认: {e}")
            return cls()

    def reset(self) -> None:
        default = BotConfig()
        self.__dict__.update(default.__dict__)
        self.save()
        log.info("配置已重置为默认值")

    # ---- mutators ----
    def update_input_position(self, x: int, y: int) -> None:
        self.message_input_position = (int(x), int(y))
        self.save()
        log.info(f"输入框位置已更新: {self.message_input_position}")

    def update_region(self, name: str, region: Tuple[int, int, int, int]) -> None:
        if not hasattr(self, name):
            raise AttributeError(f"未知的区域字段: {name}")
        setattr(self, name, tuple(int(v) for v in region))
        self.save()
        log.info(f"区域 {name} 已更新: {getattr(self, name)}")

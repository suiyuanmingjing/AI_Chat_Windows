"""Whitelist / blacklist management."""
from __future__ import annotations

import os
from typing import Iterable, List, Set

from wechat_bot.config import BotConfig
from wechat_bot.ipc import IPCPaths
from wechat_bot.logger import get_logger

log = get_logger("wechat_bot.whitelist")


class ContactFilter:
    """Reads whitelist.txt / blacklist.txt and answers membership questions."""

    def __init__(self, paths: IPCPaths, cfg: BotConfig):
        self.paths = paths
        self.cfg = cfg
        self._whitelist: Set[str] = set()
        self._blacklist: Set[str] = set()
        self.reload()

    def reload(self) -> None:
        self._whitelist = self._read_set(self.paths.whitelist_path)
        self._blacklist = self._read_set(self.paths.blacklist_path)
        log.info(
            f"名单刷新: 白名单 {len(self._whitelist)} 人, "
            f"黑名单 {len(self._blacklist)} 人"
        )

    @staticmethod
    def _read_set(path: str) -> Set[str]:
        if not os.path.exists(path):
            return set()
        out: Set[str] = set()
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if s and not s.startswith("#"):
                    out.add(s)
        return out

    def _norm(self, name: str) -> str:
        if not name:
            return ""
        return name if self.cfg.whitelist.case_sensitive else name.lower()

    def is_whitelisted(self, name: str) -> bool:
        if not self._whitelist:
            return True
        return self._norm(name) in {self._norm(x) for x in self._whitelist}

    def is_blacklisted(self, name: str) -> bool:
        return self._norm(name) in {self._norm(x) for x in self._blacklist}

    def allowed(self, name: str) -> bool:
        if self.is_blacklisted(name):
            return False
        if self.cfg.whitelist.enabled and not self.is_whitelisted(name):
            return False
        return True

    # ---- GUI mutators ----
    def add_whitelist(self, name: str) -> None:
        if not name:
            return
        self._append(self.paths.whitelist_path, name)
        self.reload()

    def add_blacklist(self, name: str) -> None:
        if not name:
            return
        self._append(self.paths.blacklist_path, name)
        self.reload()

    def remove_whitelist(self, name: str) -> None:
        self._remove(self.paths.whitelist_path, name)
        self.reload()

    def remove_blacklist(self, name: str) -> None:
        self._remove(self.paths.blacklist_path, name)
        self.reload()

    @staticmethod
    def _append(path: str, name: str) -> None:
        # 避免重复
        existing = set()
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                existing = {l.strip() for l in f if l.strip() and not l.startswith("#")}
        if name in existing:
            return
        with open(path, "a", encoding="utf-8") as f:
            f.write(name + "\n")

    @staticmethod
    def _remove(path: str, name: str) -> None:
        if not os.path.exists(path):
            return
        kept: List[str] = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                s = line.rstrip("\n")
                if s.strip() == name:
                    continue
                kept.append(s)
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(kept))

    # ---- inspection ----
    def whitelist(self) -> List[str]:
        return sorted(self._whitelist)

    def blacklist(self) -> List[str]:
        return sorted(self._blacklist)

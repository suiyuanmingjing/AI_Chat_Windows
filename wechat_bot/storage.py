"""Persistence: chat history, AI replies, debug artifacts.

新增 (v2.5): 每天一个文件 + 追加模式
    data/history/<user>_<YYYYMMDD>.txt
    data/replies/<user>_ai_reply_<YYYYMMDD>.txt
    data/debug/<name>.png
    data/logs/wechat_auto_reply.log

文件保留 15 天, 启动时自动清理 15 天前的历史/回复文件.

设计目的: reply_engine 读 get_sent_texts(username) 获取该用户 15 天内
所有 history+replies 内容, 用于 OCR 文本去重 (子串包含判断):
  - OCR 行 ⊆ 某条 sent  ->  已回复, 跳过
  - OCR 行 ⊇ 某条 sent  ->  已回复, 跳过
  - 都不匹配           ->  incoming, 触发 AI 回复
"""
from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

from wechat_bot.logger import get_logger

log = get_logger("wechat_bot.storage")


# 历史 / 回复 文件保留天数
HISTORY_RETENTION_DAYS = 15


class Storage:
    def __init__(self, data_dir: str = "data"):
        self.data_dir = data_dir
        self.history_dir = os.path.join(data_dir, "history")
        self.replies_dir = os.path.join(data_dir, "replies")
        self.debug_dir = os.path.join(data_dir, "debug")
        self.logs_dir = os.path.join(data_dir, "logs")
        self.index_file = os.path.join(data_dir, "chat_index.json")
        self._ensure_dirs()
        # 旧版兼容
        self._migrate_legacy()
        # 启动清理一次 (15 天前)
        try:
            self.cleanup_old_files(HISTORY_RETENTION_DAYS)
        except Exception as e:
            log.warning(f"启动清理旧文件失败: {e}")

    # --------------------------------------------------------------- folders
    def _ensure_dirs(self) -> None:
        for d in (
            self.data_dir,
            self.history_dir,
            self.replies_dir,
            self.debug_dir,
            self.logs_dir,
        ):
            os.makedirs(d, exist_ok=True)

    def _migrate_legacy(self) -> None:
        """一次性把老项目根目录下的 chat_histories/ai_replies/debug_screenshots
        以及 chat_index.json 迁移到 data/ 下。迁移完改名加 .migrated 后缀。"""
        legacy_pairs = [
            ("chat_histories", self.history_dir),
            ("ai_replies", self.replies_dir),
            ("debug_screenshots", self.debug_dir),
        ]
        for old, new in legacy_pairs:
            if os.path.isdir(old) and not os.listdir(new):
                try:
                    for name in os.listdir(old):
                        src = os.path.join(old, name)
                        dst = os.path.join(new, name)
                        if os.path.isfile(src):
                            shutil.move(src, dst)
                    os.rename(old, old + ".migrated")
                    log.info(f"已迁移旧目录 {old} -> {new}")
                except Exception as e:
                    log.warning(f"迁移 {old} 失败: {e}")
        if os.path.exists("chat_index.json") and not os.path.exists(self.index_file):
            try:
                shutil.move("chat_index.json", self.index_file)
                os.rename("chat_index.json", "chat_index.json.migrated")
                log.info("已迁移 chat_index.json -> data/chat_index.json")
            except Exception as e:
                log.warning(f"迁移 chat_index.json 失败: {e}")

    # --------------------------------------------------------------- index
    def load_index(self) -> Dict[str, Any]:
        if not os.path.exists(self.index_file):
            return {}
        try:
            with open(self.index_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log.error(f"聊天索引加载失败: {e}")
            return {}

    def save_index(self, index: Dict[str, Any]) -> None:
        try:
            with open(self.index_file, "w", encoding="utf-8") as f:
                json.dump(index, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log.error(f"聊天索引保存失败: {e}")

    @staticmethod
    def update_index_entry(
        index: Dict[str, Any], username: str, chat_hash: str
    ) -> Dict[str, Any]:
        entry = index.get(username)
        if entry is None:
            entry = {
                "last_hash": chat_hash,
                "last_check": datetime.now().isoformat(),
                "reply_count": 0,
            }
            index[username] = entry
        entry["last_hash"] = chat_hash
        entry["last_check"] = datetime.now().isoformat()
        entry["reply_count"] = int(entry.get("reply_count", 0)) + 1
        return entry

    # --------------------------------------------------------------- filename
    @staticmethod
    def _safe_username(username: str) -> str:
        s = re.sub(r"[^\w\s\-_]", "", username or "unknown")
        return s[:50] or "unknown"

    @staticmethod
    def _today_str() -> str:
        """YYYYMMDD 形式."""
        return datetime.now().strftime("%Y%m%d")

    def debug_path(self, filename: str) -> str:
        return os.path.join(self.debug_dir, filename)

    # ========================================================== daily files
    def _append_daily(
        self,
        base_dir: str,
        base_fn: str,
        content: str,
    ) -> Optional[str]:
        """把 content 追加到当天的 base_fn_YYYYMMDD.txt.

        每天一个文件, 同一天多次调用会追加 (用 === timestamp === 分隔).
        返回写入的文件路径, 失败返回 None.
        """
        if not content or not content.strip():
            return None
        day = self._today_str()
        fn = f"{base_fn}_{day}.txt"
        path = os.path.join(base_dir, fn)
        sep = f"\n=== {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===\n"
        try:
            is_new = not os.path.exists(path) or os.path.getsize(path) == 0
            with open(path, "a", encoding="utf-8") as f:
                if not is_new:
                    f.write(sep)
                f.write(content.strip() + "\n")
            return path
        except Exception as e:
            log.error(f"写入 {path} 失败: {e}")
            return None

    def save_chat(self, username: str, chat_text: str) -> Optional[str]:
        """把整段 chat 追加到 history/<safe>_<YYYYMMDD>.txt."""
        if not chat_text or not chat_text.strip():
            return None
        path = self._append_daily(
            self.history_dir, self._safe_username(username), chat_text
        )
        if path:
            log.info(f"聊天记录已追加: {path}")
        return path

    def save_reply(self, username: str, reply: str) -> Optional[str]:
        """把单次 AI 回复追加到 replies/<safe>_ai_reply_<YYYYMMDD>.txt."""
        if not reply or not reply.strip():
            return None
        path = self._append_daily(
            self.replies_dir,
            f"{self._safe_username(username)}_ai_reply",
            reply,
        )
        if path:
            log.info(f"AI 回复已追加: {path}")
        return path

    # ======================================================== sent lookup
    # 文件名里 YYYYMMDD 的正则
    _DAY_RE = re.compile(r"_(\d{8})\.txt$")
    # 文件名里 YYYYMMDD_HHMMSS 的正则 (旧格式)
    _TS_RE_LOOSE = re.compile(r"_(\d{8})(?:_\d{6})?\.txt$")
    # reply 文件中分隔多条 reply 的 "=== YYYY-MM-DD HH:MM:SS ===" 行
    _REPLY_SPLIT_RE = re.compile(r"\n===\s*\d{4}-\d{2}-\d{2}[^=\n]*===\n")
    # "已发"指纹: 每条 reply 末尾 N 个中文字符 (跳过标点/emoji/空白/ASCII)
    # 用户配置可调 (但默认 6, 与需求对齐)
    _REPLY_TAIL_CHARS = 6
    # 中文字符正则 (CJK 基本区 + 扩展 A 区)
    _CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")

    def _split_replies(self, content: str) -> List[str]:
        """把 reply 文件内容按 '=== timestamp ===' 分隔符拆成多条 reply 文本.

        reply 文件结构:
            用户: xxx
            时间: YYYY-MM-DD HH:MM:SS
            ==================================================
            <reply 1 文本>
            ==================================================

            === YYYY-MM-DD HH:MM:SS ===

            <reply 2 文本>
            ==================================================
        """
        parts = self._REPLY_SPLIT_RE.split(content)
        return [p.strip() for p in parts if p.strip()]

    def _last_chinese_chars(self, text: str, n: int) -> str:
        """提取 text 末尾 n 个中文字符 (跳过标点/emoji/空白/ASCII/数字).

        例: '哈哈，你太会了！😋 确实，这种挖掘本地风味的感觉最棒了！'
            -> '感觉最棒了' (末尾 6 个中文字)
        例: 'Hello world' (无中文) -> ''
        """
        if not text or n <= 0:
            return ""
        chars = self._CJK_RE.findall(text)
        if not chars:
            return ""
        if len(chars) <= n:
            return "".join(chars)
        return "".join(chars[-n:])

    def _split_segments(self, text: str) -> List[str]:
        """按行 + 中英文标点拆句, 返回 strip 后的非空短句列表 (>= 4 字)."""
        out: List[str] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            # 跳过分隔符和元数据
            if line.startswith("===") and line.endswith("==="):
                continue
            if line.startswith("用户:") or line.startswith("时间:"):
                continue
            for seg in self._SPLIT_RE.split(line):
                seg = seg.strip()
                if len(seg) < self._MIN_SEG_LEN:
                    continue
                # 过滤纯标点/无中英文/数字的废片段
                if not re.search(r"[\u4e00-\u9fa5a-zA-Z0-9]", seg):
                    continue
                out.append(seg)
        return out

    def get_sent_texts(
        self,
        username: str,
        days: int = HISTORY_RETENTION_DAYS,
    ) -> Set[str]:
        """加载该用户最近 N 天内所有 **replies** 文件, 提取每条 reply 的
        "末尾 N 个中文字符"作为"已发"指纹集合.

        设计 (v2.6):
        - 只读 replies/ (AI 实际发过的内容), 不读 history/ (污染)
        - 每条 reply 只贡献 1 个指纹 = "末尾 N 个中文字符"
          (跳过标点/emoji/空白/ASCII, 只算汉字)
        - 颗粒度小, 双向子串匹配安全, 不会因为 reply 内容长而误判
        - 指纹短 (6 字) 但稳定: AI 每条 reply 末尾内容相对独特,
          不容易和对方新消息撞指纹

        Returns:
            set[str] (尾标, 已 strip, 只含中文字符)
        """
        result: Set[str] = set()
        safe = self._safe_username(username)
        cutoff_date = datetime.now() - timedelta(days=days)
        n = self._REPLY_TAIL_CHARS
        base_dir = self.replies_dir
        prefix = f"{safe}_ai_reply_"
        if not os.path.isdir(base_dir):
            return result
        try:
            names = os.listdir(base_dir)
        except Exception as e:
            log.warning(f"listdir {base_dir} 失败: {e}")
            return result
        for fn in names:
            if not fn.startswith(prefix) or not fn.endswith(".txt"):
                continue
            # 提取 YYYYMMDD (兼容 _YYYYMMDD.txt 和 _YYYYMMDD_HHMMSS.txt)
            m = self._TS_RE_LOOSE.search(fn)
            if not m:
                continue
            try:
                file_date = datetime.strptime(m.group(1), "%Y%m%d")
            except ValueError:
                continue
            if file_date.date() < cutoff_date.date():
                continue
            # 读文件, 拆 reply, 提尾标
            try:
                with open(
                    os.path.join(base_dir, fn), "r", encoding="utf-8"
                ) as f:
                    content = f.read()
            except Exception as e:
                log.warning(f"读 {fn} 失败: {e}")
                continue
            for reply in self._split_replies(content):
                tail = self._last_chinese_chars(reply, n)
                if tail:
                    result.add(tail)
        log.debug(
            f"get_sent_texts({username}, days={days}, tail={n}) -> "
            f"{len(result)} 个指纹"
        )
        return result

    # ========================================================== cleanup
    def cleanup_old_files(self, days: int = HISTORY_RETENTION_DAYS) -> int:
        """清理 N 天前的 history + reply 文件. 返回删除数.

        兼容两种文件名: <safe>_YYYYMMDD_HHMMSS.txt (旧) 和 <safe>_YYYYMMDD.txt (新).
        """
        cutoff = datetime.now() - timedelta(days=days)
        deleted = 0
        # 旧文件名: 末尾 _YYYYMMDD_HHMMSS.txt
        old_re = re.compile(r"_(\d{8})_(\d{6})\.txt$")
        for base_dir in (self.history_dir, self.replies_dir):
            if not os.path.isdir(base_dir):
                continue
            try:
                names = os.listdir(base_dir)
            except Exception:
                continue
            for fn in names:
                if not fn.endswith(".txt"):
                    continue
                # 先试新格式 _YYYYMMDD.txt
                m_new = self._DAY_RE.search(fn)
                m_old = old_re.search(fn) if not m_new else None
                if m_new:
                    try:
                        file_date = datetime.strptime(m_new.group(1), "%Y%m%d")
                    except ValueError:
                        continue
                elif m_old:
                    try:
                        file_date = datetime.strptime(
                            m_old.group(1) + m_old.group(2), "%Y%m%d%H%M%S"
                        )
                    except ValueError:
                        continue
                else:
                    continue
                if file_date < cutoff:
                    try:
                        os.remove(os.path.join(base_dir, fn))
                        deleted += 1
                    except Exception as e:
                        log.warning(f"删除 {fn} 失败: {e}")
        if deleted:
            log.info(f"已清理 {deleted} 个超过 {days} 天的历史/回复文件")
        return deleted

    # ========================================================== summary
    _TS_RE = re.compile(r"_(\d{8})_(\d{6})\.txt$")

    @classmethod
    def _parse_filename_ts(cls, fname: str) -> Optional[datetime]:
        """解析文件名末尾的 YYYYMMDD_HHMMSS 时间戳 (旧格式)."""
        m = cls._TS_RE.search(fname)
        if not m:
            return None
        try:
            return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
        except Exception:
            return None

    @staticmethod
    def _display_name(safe_name: str) -> str:
        """从 safe_name 还原显示名（去掉 _YYYYMMDD_HHMMSS / _YYYYMMDD / _ai_reply 段）."""
        # 文件名形如 "<safe>_YYYYMMDD_HHMMSS.txt" 或 "<safe>_YYYYMMDD.txt"
        # 或 "<safe>_ai_reply_YYYYMMDD_HHMMSS.txt" 或 "<safe>_ai_reply_YYYYMMDD.txt"
        m = re.match(
            r"^(.*?)(?:_ai_reply)?(?:_\d{8}(?:_\d{6})?)?\.txt$", safe_name
        )
        if m:
            return m.group(1)
        return safe_name

    def scan_summary(
        self,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """扫描 history/ + replies/, 按时间范围聚合.

        兼容新旧两种文件名格式.
        """
        result: Dict[str, Any] = {
            "total_messages": 0,
            "total_replies": 0,
            "by_contact": {},
            "hourly": {h: 0 for h in range(24)},
            "files_chat": 0,
            "files_reply": 0,
        }

        def in_range(ts: Optional[datetime]) -> bool:
            if ts is None:
                return False
            if start and ts < start:
                return False
            if end and ts > end:
                return False
            return True

        def extract_date(fn: str) -> Optional[datetime]:
            m_old = self._TS_RE.search(fn)
            if m_old:
                try:
                    return datetime.strptime(
                        m_old.group(1) + m_old.group(2), "%Y%m%d%H%M%S"
                    )
                except ValueError:
                    pass
            m_new = self._DAY_RE.search(fn)
            if m_new:
                try:
                    d = datetime.strptime(m_new.group(1), "%Y%m%d")
                    return d.replace(hour=12)  # 整天内随便给个中午
                except ValueError:
                    pass
            return None

        # 历史
        if os.path.isdir(self.history_dir):
            for fn in os.listdir(self.history_dir):
                if not fn.endswith(".txt"):
                    continue
                ts = extract_date(fn)
                if not in_range(ts):
                    continue
                name = self._display_name(fn)
                result["files_chat"] += 1
                result["total_messages"] += 1
                bucket = result["by_contact"].setdefault(
                    name, {"messages": 0, "replies": 0, "last_time": None}
                )
                bucket["messages"] += 1
                if ts and (bucket["last_time"] is None or ts > bucket["last_time"]):
                    bucket["last_time"] = ts
                if ts:
                    result["hourly"][ts.hour] += 1

        # 回复
        if os.path.isdir(self.replies_dir):
            for fn in os.listdir(self.replies_dir):
                if not fn.endswith(".txt"):
                    continue
                ts = extract_date(fn)
                if not in_range(ts):
                    continue
                name = self._display_name(fn)
                result["files_reply"] += 1
                result["total_replies"] += 1
                bucket = result["by_contact"].setdefault(
                    name, {"messages": 0, "replies": 0, "last_time": None}
                )
                bucket["replies"] += 1
                if ts and (bucket["last_time"] is None or ts > bucket["last_time"]):
                    bucket["last_time"] = ts
        return result

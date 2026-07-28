"""Lightweight knowledge base for RAG-style prompt augmentation.

特点:
- 零依赖 (纯 stdlib + re)
- 知识库文件 = UTF-8 纯文本, 用空行分段, 每段作为一个 chunk
- 检索 = 关键词 jaccard 相似度 (支持中英文, 词级别切分)
- 热加载: mtime 变化时自动重读
- 检索结果以 "[知识库参考]\n..." 形式追加到 system_prompt 末尾

新增 (v2.4).
"""
from __future__ import annotations

import os
import re
import time
from typing import List, Optional, Tuple

from wechat_bot.logger import get_logger

log = get_logger("wechat_bot.knowledge")

# 中文 + 英文 + 数字 token
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[\u4e00-\u9fff]")


def _tokenize(text: str) -> List[str]:
    return _TOKEN_RE.findall((text or "").lower())


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


class KnowledgeBase:
    """简单知识库: 文本分段 + jaccard 关键词检索.

    用法:
        kb = KnowledgeBase(path="data/knowledge.txt", top_k=3, min_score=0.05)
        chunks = kb.search("微信回复太慢")
        prompt = kb.build_context("微信回复太慢", original_system_prompt)
    """

    def __init__(
        self,
        path: str,
        top_k: int = 3,
        min_score: float = 0.05,
        max_chars_per_chunk: int = 400,
    ):
        self.path = path
        self.top_k = max(1, int(top_k))
        self.min_score = float(min_score)
        self.max_chars_per_chunk = max(50, int(max_chars_per_chunk))
        self._chunks: List[str] = []
        self._tokens: List[set] = []
        self._mtime: float = 0.0
        self._loaded = False
        # 立即触发一次 ensure_loaded (会自动创建文件)
        self._ensure_loaded()

    # ------------------------------------------------------------------ IO
    def _ensure_loaded(self) -> None:
        if not self.path:
            return
        # 文件不存在: 自动创建空文件
        if not os.path.exists(self.path):
            try:
                os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
                with open(self.path, "w", encoding="utf-8") as f:
                    f.write(
                        "# 知识库文件 (UTF-8)\n"
                        "# 用空行分段, 每段作为一个知识条目.\n"
                        "# 检索时按关键词 jaccard 相似度匹配 top_k 条,\n"
                        "# 自动拼到 system_prompt 末尾给 LLM 参考.\n\n"
                    )
                log.info(f"知识库文件已创建: {self.path}")
            except Exception as e:
                log.warning(f"创建知识库文件失败: {e}")
                return
        try:
            mtime = os.path.getmtime(self.path)
        except Exception:
            return
        if self._loaded and mtime == self._mtime:
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            log.warning(f"读知识库失败: {e}")
            return
        # 段落切分: 用空行 (>= 2 个换行) 分
        raw_chunks = re.split(r"\n\s*\n", content)
        chunks: List[str] = []
        for raw in raw_chunks:
            # 去掉 # 注释行
            lines = [
                ln for ln in raw.splitlines()
                if ln.strip() and not ln.strip().startswith("#")
            ]
            text = "\n".join(lines).strip()
            if not text:
                continue
            # 太长就硬切
            if len(text) > self.max_chars_per_chunk:
                for i in range(0, len(text), self.max_chars_per_chunk):
                    sub = text[i:i + self.max_chars_per_chunk].strip()
                    if sub:
                        chunks.append(sub)
            else:
                chunks.append(text)
        self._chunks = chunks
        self._tokens = [set(_tokenize(c)) for c in chunks]
        self._mtime = mtime
        self._loaded = True
        log.info(f"知识库已加载: {len(self._chunks)} 条, path={self.path}")

    def reload(self) -> None:
        """强制重读 (绕开 mtime 缓存)."""
        self._loaded = False
        self._mtime = 0.0
        self._ensure_loaded()

    # ------------------------------------------------------------------ API
    @property
    def size(self) -> int:
        self._ensure_loaded()
        return len(self._chunks)

    def search(self, query: str, top_k: Optional[int] = None) -> List[Tuple[str, float]]:
        """返回 top-k 检索结果 [(chunk, score), ...], 倒序."""
        self._ensure_loaded()
        if not self._chunks or not query or not query.strip():
            return []
        k = top_k or self.top_k
        q_tokens = set(_tokenize(query))
        if not q_tokens:
            return []
        scored: List[Tuple[int, float]] = []
        for i, toks in enumerate(self._tokens):
            s = _jaccard(q_tokens, toks)
            if s >= self.min_score:
                scored.append((i, s))
        scored.sort(key=lambda x: -x[1])
        scored = scored[:k]
        return [(self._chunks[i], s) for i, s in scored]

    def build_context(self, query: str, system_prompt: str = "") -> str:
        """拼出最终 system_prompt: 原 prompt + 知识库 top-k."""
        results = self.search(query)
        if not results:
            return system_prompt
        # 拼装
        kb_lines = ["", "【知识库参考】"]
        for i, (chunk, score) in enumerate(results, 1):
            preview = chunk if len(chunk) <= 200 else chunk[:200] + "…"
            kb_lines.append(f"[{i}] (相关度 {score:.2f}) {preview}")
        return (system_prompt.rstrip() + "\n" + "\n".join(kb_lines)).strip()


# 默认空实例 (config 没启用时不实例化, 调用方自己 new)
def make_default(data_dir: str) -> KnowledgeBase:
    return KnowledgeBase(
        path=os.path.join(data_dir, "knowledge.txt"),
        top_k=3,
        min_score=0.05,
    )

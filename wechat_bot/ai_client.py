"""AI chat client.

- 默认走 OpenAI 兼容协议 (openai>=1.30 SDK) —— Ollama、vLLM、LM Studio、
  OneAPI、OpenAI 官方接口都能直接用。
- 老项目里 ollama SDK 路径保留为 provider='ollama_sdk' 的兜底选项。
- 新增 (v2.4): 支持外挂知识库 (KnowledgeBase), 在 system_prompt 末尾
  追加 top-k 检索结果, 由调用方传入 kb 实例.
"""
from __future__ import annotations

import hashlib
import time
from typing import Any, Dict, List, Optional, Tuple

from wechat_bot.config import AIConfig
from wechat_bot.logger import get_logger

log = get_logger("wechat_bot.ai_client")


class AIClient:
    """OpenAI-compatible chat completion with simple TTL cache."""

    def __init__(
        self,
        cfg: AIConfig,
        cache_timeout: int = 300,
        kb: Optional[Any] = None,
    ):
        """
        Args:
            cfg: AI 配置
            cache_timeout: 缓存 TTL (秒), 0=不缓存
            kb: KnowledgeBase 实例 (可选), 启用时会在每次 chat 时
                自动 search+build_context 拼到 system_prompt
        """
        self.cfg = cfg
        self.cache_timeout = cache_timeout
        self.kb = kb
        self._cache: Dict[str, Tuple[str, float]] = {}

    # --------------------------------------------------------------- public
    def chat(
        self,
        user_text: str,
        system_prompt: Optional[str] = None,
        use_cache: bool = True,
    ) -> str:
        if not user_text or not user_text.strip():
            return "你好！有什么我可以帮助你的吗？"

        sys_prompt = (system_prompt or self.cfg.system_prompt).strip()

        # 知识库增强
        if self.kb is not None:
            try:
                ctx = self.kb.build_context(user_text, sys_prompt)
                if ctx and ctx != sys_prompt:
                    log.debug(
                        f"知识库命中: {self.kb.size} 条库, "
                        f"query='{user_text[:30]}'"
                    )
                sys_prompt = ctx
            except Exception as e:
                log.warning(f"知识库检索失败: {e}")

        key = self._cache_key(user_text, sys_prompt)
        if use_cache:
            cached = self._cache_get(key)
            if cached is not None:
                log.info("AI 回复命中缓存")
                return cached

        try:
            text = self._dispatch(user_text, sys_prompt)
        except Exception as e:
            log.error(f"AI 调用失败: {e}")
            text = "我已经看到你的消息了，正在思考如何回复..."

        if use_cache and text:
            self._cache_set(key, text)
        return text

    def clear_cache(self) -> None:
        """清空缓存 (用于知识库变更后强制重生成)."""
        self._cache.clear()

    # --------------------------------------------------------------- dispatch
    def _dispatch(self, user_text: str, system_prompt: str) -> str:
        if self.cfg.provider == "ollama_sdk":
            return self._via_ollama_sdk(user_text, system_prompt)
        # 默认 openai_compat
        return self._via_openai_compat(user_text, system_prompt)

    def _via_openai_compat(self, user_text: str, system_prompt: str) -> str:
        from openai import OpenAI  # type: ignore

        client = OpenAI(
            base_url=self.cfg.base_url,
            api_key=self.cfg.api_key or "ollama",
            timeout=self.cfg.timeout,
        )
        messages: List[Dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_text})

        log.info(
            f"AI 调用: model={self.cfg.model} base={self.cfg.base_url} "
            f"chars={len(user_text)}"
        )
        resp = client.chat.completions.create(
            model=self.cfg.model,
            messages=messages,
            temperature=self.cfg.temperature,
            max_tokens=self.cfg.max_tokens,
        )
        return self._extract_text(resp)

    def _via_ollama_sdk(self, user_text: str, system_prompt: str) -> str:
        import ollama  # type: ignore

        messages: List[Dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_text})

        log.info(f"Ollama SDK 调用: model={self.cfg.model}")
        resp = ollama.chat(model=self.cfg.model, messages=messages)
        if isinstance(resp, dict):
            return resp.get("message", {}).get("content", "") or ""
        # 1.x 版本的 Pydantic 模型
        return getattr(resp.message, "content", "") or ""

    @staticmethod
    def _extract_text(resp: Any) -> str:
        # 兼容 openai SDK 1.x 和可能的 dict 形态
        try:
            return resp.choices[0].message.content or ""
        except Exception:
            pass
        try:
            return resp["choices"][0]["message"]["content"] or ""
        except Exception:
            return ""

    # ---------------------------------------------------------------- cache
    def _cache_key(self, user_text: str, system_prompt: Optional[str]) -> str:
        h = hashlib.md5()
        h.update((system_prompt or "").encode("utf-8"))
        h.update(b"\x00")
        h.update(user_text.encode("utf-8"))
        h.update(b"\x00")
        h.update(self.cfg.model.encode("utf-8"))
        return h.hexdigest()

    def _cache_get(self, key: str) -> Optional[str]:
        v = self._cache.get(key)
        if not v:
            return None
        text, ts = v
        if time.time() - ts < self.cache_timeout:
            return text
        self._cache.pop(key, None)
        return None

    def _cache_set(self, key: str, text: str) -> None:
        self._cache[key] = (text, time.time())

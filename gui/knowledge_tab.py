"""Knowledge Base editor tab.

直接读写 data/knowledge.txt (UTF-8 纯文本), 用空行分段.
保存时自动 reload, AI 客户端下次调用会重新检索.
"""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Optional

from gui.widgets import ScrolledFrame
from wechat_bot.config import BotConfig
from wechat_bot.knowledge import KnowledgeBase
from wechat_bot.logger import get_logger

log = get_logger("gui.knowledge_tab")


class KnowledgeTab(ttk.Frame):
    def __init__(self, master: tk.Misc, cfg: BotConfig, on_save: Optional[callable] = None):
        super().__init__(master, padding=8)
        self.cfg = cfg
        self.on_save = on_save
        self.kb = self._make_kb()
        # 滚动容器
        self._sf = ScrolledFrame(self)
        self._sf.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.body = self._sf.interior
        self._build()
        self._load()

    def _make_kb(self) -> KnowledgeBase:
        path = self.cfg.kb.file
        if not os.path.isabs(path):
            path = os.path.join(self.cfg.data_dir, path)
        return KnowledgeBase(
            path=path,
            top_k=self.cfg.kb.top_k,
            min_score=self.cfg.kb.min_score,
            max_chars_per_chunk=self.cfg.kb.max_chars_per_chunk,
        )

    def _build(self) -> None:
        body = self.body
        # 顶部说明
        info = ttk.Label(
            body,
            text=(
                "知识库 = UTF-8 纯文本, 用空行分段, 每段一个条目.\n"
                "AI 收到消息时, 会按关键词 jaccard 相似度检索 top_k 条,"
                "拼到 system_prompt 末尾给 LLM 参考。\n"
                "修改后请点「💾 保存」, 守护进程会热加载 (无需重启)。"
            ),
            foreground="#666",
            justify=tk.LEFT,
        )
        info.pack(side=tk.TOP, anchor=tk.W, pady=(0, 8))

        # 工具条
        bar = ttk.Frame(body)
        bar.pack(side=tk.TOP, fill=tk.X, pady=(0, 4))
        ttk.Button(bar, text="💾 保存", command=self._save).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="↺ 重新加载", command=self._reload).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="🧪 检索测试", command=self._test_search).pack(
            side=tk.LEFT, padx=2
        )
        self.var_enabled = tk.BooleanVar(value=self.cfg.kb.enabled)
        ttk.Checkbutton(
            bar, text="启用知识库",
            variable=self.var_enabled,
            command=self._toggle_enabled,
        ).pack(side=tk.LEFT, padx=(16, 2))
        self.var_status = tk.StringVar(value=f"已加载 {self.kb.size} 条")
        ttk.Label(
            bar, textvariable=self.var_status, foreground="#666"
        ).pack(side=tk.RIGHT, padx=4)

        # 检索测试区
        search_frame = ttk.LabelFrame(body, text="🔍 检索测试", padding=4)
        search_frame.pack(side=tk.TOP, fill=tk.X, pady=(0, 8))
        self.var_query = tk.StringVar()
        ent = ttk.Entry(search_frame, textvariable=self.var_query, width=50)
        ent.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        ttk.Button(
            search_frame, text="检索", command=self._test_search
        ).pack(side=tk.LEFT, padx=2)
        self.search_result = tk.Text(
            search_frame, height=4, font=("Consolas", 9), state=tk.DISABLED,
            wrap=tk.WORD, background="#fafafa",
        )
        self.search_result.pack(side=tk.TOP, fill=tk.X, pady=(4, 0))

        # 编辑器
        editor_frame = ttk.LabelFrame(body, text="📝 知识库内容 (每段用空行分隔)", padding=4)
        editor_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        wrap = ttk.Frame(editor_frame)
        wrap.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.text = tk.Text(
            wrap, wrap=tk.WORD, font=("Consolas", 10), undo=True,
        )
        ys = ttk.Scrollbar(wrap, orient=tk.VERTICAL, command=self.text.yview)
        self.text.configure(yscrollcommand=ys.set)
        self.text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ys.pack(side=tk.RIGHT, fill=tk.Y)

    def _load(self) -> None:
        try:
            if not os.path.exists(self.kb.path):
                self.kb._ensure_loaded()  # 自动创建模板
            with open(self.kb.path, "r", encoding="utf-8") as f:
                content = f.read()
            self.text.delete("1.0", tk.END)
            self.text.insert("1.0", content)
        except Exception as e:
            messagebox.showerror("加载失败", str(e))
        self._refresh_status()

    def _save(self) -> None:
        try:
            content = self.text.get("1.0", tk.END)
            with open(self.kb.path, "w", encoding="utf-8") as f:
                f.write(content)
            # 强制 reload
            self.kb.reload()
            self._refresh_status()
            if self.on_save:
                try:
                    self.on_save()
                except Exception:
                    pass
            messagebox.showinfo("已保存", f"知识库已写入:\n{self.kb.path}")
        except Exception as e:
            log.error(f"保存知识库失败: {e}")
            messagebox.showerror("保存失败", str(e))

    def _reload(self) -> None:
        self.kb.reload()
        self._refresh_status()
        log.info("知识库已重读")

    def _refresh_status(self) -> None:
        self.var_status.set(f"已加载 {self.kb.size} 条  |  {self.kb.path}")

    def _toggle_enabled(self) -> None:
        self.cfg.kb.enabled = bool(self.var_enabled.get())
        self.cfg.save()
        log.info(f"知识库启用: {self.cfg.kb.enabled}")

    def _test_search(self) -> None:
        q = self.var_query.get().strip()
        self.search_result.configure(state=tk.NORMAL)
        self.search_result.delete("1.0", tk.END)
        if not q:
            self.search_result.insert("1.0", "(请输入查询关键词)")
            self.search_result.configure(state=tk.DISABLED)
            return
        results = self.kb.search(q)
        if not results:
            self.search_result.insert("1.0", "(无匹配结果)")
        else:
            lines = []
            for i, (chunk, score) in enumerate(results, 1):
                lines.append(f"[{i}] 相关度 {score:.3f}")
                lines.append(chunk)
                lines.append("-" * 40)
            self.search_result.insert("1.0", "\n".join(lines))
        self.search_result.configure(state=tk.DISABLED)

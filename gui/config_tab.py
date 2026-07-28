"""Config tab: visualize all BotConfig fields and save with one click.

新增 (v2.4): 外层 ScrolledFrame 包装 (内容多时可滚)
新增 (v2.4): 「知识库」子页 (KBConfig)
"""
from __future__ import annotations

import os
import subprocess
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Dict, Tuple

from gui.widgets import ScrolledFrame
from wechat_bot.config import AIConfig, BotConfig, GuardianConfig, IPCConfig, WhitelistConfig
from wechat_bot.logger import get_logger

log = get_logger("gui.config_tab")


class ConfigTab(ttk.Frame):
    def __init__(self, master: tk.Misc, cfg: BotConfig, on_save: callable):
        super().__init__(master, padding=8)
        self.cfg = cfg
        self.on_save = on_save
        self._widgets: Dict[str, tk.Variable] = {}
        # 滚动容器
        self._sf = ScrolledFrame(self)
        self._sf.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.body = self._sf.interior
        self._build()
        self._load()

    def _build(self) -> None:
        body = self.body
        nb = ttk.Notebook(body)
        nb.pack(fill=tk.BOTH, expand=True)
        self._build_window_tab(nb)
        self._build_regions_tab(nb)
        self._build_ai_tab(nb)
        self._build_guardian_tab(nb)
        self._build_ipc_tab(nb)
        self._build_whitelist_tab(nb)
        self._build_kb_tab(nb)

        # 底部按钮
        bar = ttk.Frame(body)
        bar.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(bar, text="💾 保存到 wechat_config.json", command=self._save).pack(
            side=tk.RIGHT, padx=2
        )
        ttk.Button(bar, text="↺ 重新加载", command=self._load).pack(side=tk.RIGHT, padx=2)
        ttk.Button(bar, text="⚠ 重置为默认", command=self._reset).pack(side=tk.LEFT, padx=2)

    # ----- tabs -----
    def _build_window_tab(self, nb: ttk.Notebook) -> None:
        f = ttk.Frame(nb, padding=8)
        nb.add(f, text="窗口")
        self._add_entry(f, "window_title", "微信窗口标题", row=0)
        self._add_entry2(
            f, "window_position", "窗口位置 (x, y)", row=1, cast=int
        )
        self._add_entry2(
            f, "window_size", "窗口大小 (w, h)", row=2, cast=int
        )
        self._add_entry(
            f, "message_input_position",
            "消息输入框位置 (x, y)，逗号分隔",
            row=3, cast=None, is_pair=True
        )

    def _build_regions_tab(self, nb: ttk.Notebook) -> None:
        f = ttk.Frame(nb, padding=8)
        nb.add(f, text="截图区域")
        self._add_entry(f, "contacts_region", "联系人区域 (x, y, w, h)", row=0, is_quad=True)
        self._add_entry(f, "username_region", "用户名区域 (x, y, w, h)", row=1, is_quad=True)
        self._add_entry(f, "chat_region", "聊天记录区域 (x, y, w, h)", row=2, is_quad=True)
        self._add_entry(f, "black_text_threshold", "黑色文字亮度阈值 (<)", row=3, cast=int)
        self._add_entry(f, "gray_text_threshold", "灰色文字亮度阈值 (>=)", row=4, cast=int)

    def _build_ai_tab(self, nb: ttk.Notebook) -> None:
        f = ttk.Frame(nb, padding=8)
        nb.add(f, text="AI")
        self._add_combo(
            f, "ai.provider", "Provider", row=0, values=["openai_compat", "ollama_sdk"]
        )
        self._add_entry(f, "ai.base_url", "Base URL", row=1)
        self._add_entry(f, "ai.api_key", "API Key", row=2)
        self._add_entry(f, "ai.model", "Model", row=3)
        self._add_entry(f, "ai.timeout", "Timeout (s)", row=4, cast=int)
        self._add_entry(f, "ai.temperature", "Temperature", row=5, cast=float)
        self._add_entry(f, "ai.max_tokens", "Max Tokens", row=6, cast=int)
        ttk.Label(f, text="System Prompt:").grid(row=7, column=0, sticky=tk.NW, padx=2, pady=4)
        self.var_system_prompt = tk.Text(f, height=8, width=60, wrap=tk.WORD)
        self.var_system_prompt.grid(row=7, column=1, sticky=tk.EW, padx=2, pady=4)

        # 不保存即可临时测试
        bar = ttk.Frame(f)
        bar.grid(row=8, column=0, columnspan=2, sticky=tk.EW, pady=(8, 0))
        ttk.Button(bar, text="🔌 测试当前 AI 配置", command=self._test_ai).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Label(
            bar,
            text="（不写入文件，仅基于当前输入测试连通性，5-10s 出结果）",
            foreground="#666",
        ).pack(side=tk.LEFT, padx=4)

    def _build_guardian_tab(self, nb: ttk.Notebook) -> None:
        f = ttk.Frame(nb, padding=8)
        nb.add(f, text="守护")
        self._add_entry(f, "check_interval", "检查间隔 (s)", row=0, cast=int)
        self._add_entry(f, "max_contacts_to_check", "每轮最多联系人数", row=1, cast=int)
        self._add_entry(f, "cache_timeout", "缓存超时 (s)", row=2, cast=int)
        self._add_entry(
            f, "guardian.max_consecutive_errors", "最大连续错误", row=3, cast=int
        )
        self._add_entry(
            f, "guardian.cooldown_on_burst", "错误冷却 (s)", row=4, cast=int
        )
        self._add_entry(
            f, "guardian.wechat_recheck_interval", "微信重检间隔 (s)", row=5, cast=int
        )
        self._add_entry(
            f, "guardian.max_idle_rounds", "最大空轮数 (0=无限)", row=6, cast=int
        )
        self._add_check(
            f, "debug_mode", "调试模式（保存截图与 stats）", row=7
        )
        self._add_entry(f, "data_dir", "数据目录", row=8)
        self._add_entry(
            f, "line_send_interval",
            "多行消息段间 delay (s, AI 回复按 \\n 拆段时每段间 sleep)",
            row=9, cast=float
        )

    def _build_ipc_tab(self, nb: ttk.Notebook) -> None:
        f = ttk.Frame(nb, padding=8)
        nb.add(f, text="IPC")
        ttk.Label(f, text="(GUI 与守护进程之间的文件 IPC 路径)").grid(
            row=0, column=0, columnspan=2, sticky=tk.W, pady=(0, 8)
        )
        self._add_entry(f, "ipc.runtime_dir", "Runtime 子目录", row=1)
        self._add_entry(f, "ipc.status_file", "Status 文件名", row=2)
        self._add_entry(f, "ipc.control_file", "Control 文件名", row=3)
        self._add_entry(f, "ipc.whitelist_file", "白名单文件名", row=4)
        self._add_entry(f, "ipc.blacklist_file", "黑名单文件名", row=5)
        self._add_entry(f, "ipc.pause_flag", "暂停标记文件名", row=6)
        self._add_entry(f, "ipc.status_ttl", "状态 TTL (s)", row=7, cast=int)

    def _build_whitelist_tab(self, nb: ttk.Notebook) -> None:
        f = ttk.Frame(nb, padding=8)
        nb.add(f, text="白名单策略")
        self._add_check(
            f, "whitelist.enabled", "启用白名单（启用后只回复名单内联系人）", row=0
        )
        self._add_check(
            f, "whitelist.case_sensitive", "区分大小写", row=1
        )

    def _build_kb_tab(self, nb: ttk.Notebook) -> None:
        f = ttk.Frame(nb, padding=8)
        nb.add(f, text="知识库")
        ttk.Label(
            f,
            text="RAG 风格: 每次 AI 调用时, 自动按关键词 jaccard 相似度检索 top_k 条,"
            "拼到 system_prompt 末尾。\n知识库文件为 UTF-8 纯文本, 用空行分段, "
            "每段一个条目 (# 开头为注释)。",
            foreground="#666",
            justify=tk.LEFT,
        ).grid(row=0, column=0, columnspan=2, sticky=tk.W, pady=(0, 8))
        self._add_check(
            f, "kb.enabled", "启用知识库 (RAG 检索增强)", row=1
        )
        self._add_entry(f, "kb.file", "知识库文件 (相对 data_dir)", row=2)
        self._add_entry(f, "kb.top_k", "检索 top_k 条", row=3, cast=int)
        self._add_entry(f, "kb.min_score", "最小相关度阈值 (0~1)", row=4, cast=float)
        self._add_entry(
            f, "kb.max_chars_per_chunk", "每段最大字符数", row=5, cast=int
        )
        ttk.Button(
            f, text="📂 打开知识库文件", command=self._open_kb_file
        ).grid(row=6, column=0, columnspan=2, sticky=tk.W, pady=(8, 0))

    def _open_kb_file(self) -> None:
        """用系统默认编辑器打开知识库文件, 不存在则创建空模板."""
        from wechat_bot.knowledge import KnowledgeBase
        path = os.path.join(self.cfg.data_dir, self.cfg.kb.file) \
            if not os.path.isabs(self.cfg.kb.file) else self.cfg.kb.file
        # 触发一次 ensure_loaded (会自动创建模板)
        KnowledgeBase(path=path, top_k=self.cfg.kb.top_k)._ensure_loaded()
        try:
            if hasattr(os, "startfile"):
                os.startfile(path)  # type: ignore[attr-defined]
            else:
                subprocess.call(["xdg-open", path])
        except Exception as e:
            messagebox.showerror("打开失败", str(e))

    # ----- helpers -----
    def _resolve(self, dotted: str) -> Any:
        obj: Any = self.cfg
        for part in dotted.split("."):
            obj = getattr(obj, part)
        return obj

    def _set_resolve(self, dotted: str, value: Any) -> None:
        parts = dotted.split(".")
        obj: Any = self.cfg
        for p in parts[:-1]:
            obj = getattr(obj, p)
        setattr(obj, parts[-1], value)

    def _add_entry(
        self,
        parent: tk.Misc,
        key: str,
        label: str,
        row: int,
        cast: Any = None,
        is_pair: bool = False,
        is_quad: bool = False,
    ) -> None:
        ttk.Label(parent, text=label + ":").grid(row=row, column=0, sticky=tk.W, padx=2, pady=2)
        if is_pair or is_quad:
            sep = "," if is_quad else ","
            var = tk.StringVar()
            ent = ttk.Entry(parent, textvariable=var, width=40)
            ent.grid(row=row, column=1, sticky=tk.EW, padx=2, pady=2)
            self._widgets[key] = (var, "tuple", int if is_pair else int)
        else:
            var = tk.StringVar()
            ent = ttk.Entry(parent, textvariable=var, width=40)
            ent.grid(row=row, column=1, sticky=tk.EW, padx=2, pady=2)
            self._widgets[key] = (var, cast, None)
        parent.columnconfigure(1, weight=1)

    def _add_entry2(
        self, parent, key, label, row, cast
    ) -> None:
        """两个值的元组."""
        self._add_entry(parent, key, label, row, cast=cast, is_pair=True)

    def _add_combo(self, parent, key, label, row, values) -> None:
        ttk.Label(parent, text=label + ":").grid(row=row, column=0, sticky=tk.W, padx=2, pady=2)
        var = tk.StringVar()
        cb = ttk.Combobox(parent, textvariable=var, values=values, state="readonly", width=38)
        cb.grid(row=row, column=1, sticky=tk.EW, padx=2, pady=2)
        self._widgets[key] = (var, "combo", values)

    def _add_check(self, parent, key, label, row) -> None:
        var = tk.BooleanVar()
        ttk.Checkbutton(parent, text=label, variable=var).grid(
            row=row, column=0, columnspan=2, sticky=tk.W, padx=2, pady=2
        )
        self._widgets[key] = (var, bool, None)

    # ----- load/save -----
    def _load(self) -> None:
        for key, (var, cast, _extra) in self._widgets.items():
            if key == "ai.system_prompt":
                self.var_system_prompt.delete("1.0", tk.END)
                self.var_system_prompt.insert("1.0", self.cfg.ai.system_prompt)
                continue
            if cast == bool:
                var.set(bool(self._resolve(key)))
            elif cast in (int, float):
                var.set(str(self._resolve(key)))
            elif cast == "tuple":
                val = self._resolve(key)
                var.set(", ".join(str(x) for x in val))
            elif cast == "combo":
                var.set(str(self._resolve(key)))
            else:
                var.set(str(self._resolve(key)))

    def _save(self) -> None:
        try:
            for key, (var, cast, extra) in self._widgets.items():
                if key == "ai.system_prompt":
                    self.cfg.ai.system_prompt = self.var_system_prompt.get("1.0", tk.END).strip()
                    continue
                if cast == bool:
                    self._set_resolve(key, bool(var.get()))
                elif cast in (int, float):
                    self._set_resolve(key, cast(var.get()))
                elif cast == "tuple":
                    parts = [p.strip() for p in var.get().split(",") if p.strip()]
                    self._set_resolve(key, tuple(int(x) for x in parts))
                elif cast == "combo":
                    self._set_resolve(key, var.get())
                else:
                    self._set_resolve(key, var.get())
            self.cfg.save()
            if self.on_save:
                self.on_save()
            messagebox.showinfo("已保存", "配置已写入 wechat_config.json")
        except Exception as e:
            log.error(f"保存失败: {e}")
            messagebox.showerror("保存失败", str(e))

    def _reset(self) -> None:
        if messagebox.askyesno("重置", "重置为默认值？"):
            self.cfg.reset()
            self._load()

    # ----- transient test (no save) -----
    def _test_ai(self) -> None:
        """基于当前页面输入测试 AI 连通性，不写入配置文件。"""
        import threading
        from tkinter import simpledialog

        from wechat_bot.ai_client import AIClient

        # 临时构造一个 AIConfig 副本（不修改 self.cfg.ai）
        try:
            tmp = AIConfig(
                provider=self._widgets["ai.provider"][0].get(),
                base_url=self._widgets["ai.base_url"][0].get().strip(),
                api_key=self._widgets["ai.api_key"][0].get().strip(),
                model=self._widgets["ai.model"][0].get().strip(),
                timeout=int(self._widgets["ai.timeout"][0].get() or 30),
                temperature=float(self._widgets["ai.temperature"][0].get() or 0.7),
                max_tokens=int(self._widgets["ai.max_tokens"][0].get() or 512),
                system_prompt=self.var_system_prompt.get("1.0", tk.END).strip(),
            )
        except Exception as e:
            messagebox.showerror("参数错误", f"请检查 timeout/temperature/max_tokens 是否合法: {e}")
            return

        prompt = simpledialog.askstring(
            "测试 AI", "输入要发送的提示（留空 = 默认）:", initialvalue="你好, 请用一句话介绍你自己。"
        )
        if prompt is None:
            return
        if not prompt.strip():
            prompt = "你好"

        result_box = {"text": None, "err": None}

        def worker():
            try:
                client = AIClient(tmp, cache_timeout=0)
                result_box["text"] = client.chat(prompt, use_cache=False)
            except Exception as e:
                result_box["err"] = str(e)

        th = threading.Thread(target=worker, daemon=True)
        th.start()
        # 简易等待
        self._wait_with_busy(th, result_box, "正在调用 AI…")

    def _wait_with_busy(self, th, result_box, msg):
        import time as _t
        from tkinter import Toplevel

        busy = Toplevel(self)
        busy.title("请稍候")
        ttk.Label(busy, text=msg, padding=20).pack()
        busy.update()

        while th.is_alive():
            self.update()
            _t.sleep(0.1)
        busy.destroy()

        if result_box["err"]:
            messagebox.showerror("AI 调用失败", result_box["err"])
        else:
            text = result_box["text"] or ""
            # 用 Toplevel 显示长文本
            view = Toplevel(self)
            view.title("AI 回复")
            view.geometry("640x360")
            t = tk.Text(view, wrap=tk.WORD, font=("Consolas", 10))
            t.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
            t.insert("1.0", text)

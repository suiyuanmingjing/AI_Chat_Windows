"""Blacklist / Whitelist management tab.

新增 (v2.4): 外层 ScrolledFrame 包装
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import List

from gui.widgets import ScrolledFrame
from wechat_bot.config import BotConfig
from wechat_bot.logger import get_logger
from wechat_bot.whitelist import ContactFilter

log = get_logger("gui.blacklist_tab")


class BlacklistTab(ttk.Frame):
    def __init__(self, master: tk.Misc, cfg: BotConfig, cf: ContactFilter):
        super().__init__(master, padding=8)
        self.cfg = cfg
        self.cf = cf
        # 滚动容器
        self._sf = ScrolledFrame(self)
        self._sf.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.body = self._sf.interior
        self._build()
        self._refresh()

    def _build(self) -> None:
        body = self.body
        # 顶部说明
        info = ttk.Label(
            body,
            text=(
                "白名单启用后只回复名单内联系人；黑名单永远不回复。"
                "每行一个名字（# 开头为注释），保存后守护进程会立即生效。"
            ),
            foreground="#666",
        )
        info.pack(side=tk.TOP, anchor=tk.W, pady=(0, 8))

        # 启用开关
        ctrl = ttk.Frame(body)
        ctrl.pack(side=tk.TOP, fill=tk.X, pady=4)
        self.var_enabled = tk.BooleanVar(value=self.cfg.whitelist.enabled)
        ttk.Checkbutton(
            ctrl,
            text="启用白名单（启用后只回复名单内联系人）",
            variable=self.var_enabled,
            command=self._toggle_enabled,
        ).pack(side=tk.LEFT)
        ttk.Button(ctrl, text="💾 保存策略", command=self._save_policy).pack(
            side=tk.RIGHT, padx=2
        )
        ttk.Button(ctrl, text="🔄 重新读取名单", command=self._refresh).pack(
            side=tk.RIGHT, padx=2
        )

        # 双栏
        body2 = ttk.Frame(body)
        body2.pack(side=tk.TOP, fill=tk.BOTH, expand=True, pady=8)
        left = ttk.LabelFrame(body2, text="白名单 (允许)", padding=4)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 4))
        right = ttk.LabelFrame(body2, text="黑名单 (禁止)", padding=4)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(4, 0))

        self._build_list(left, "whitelist", "白")
        self._build_list(right, "blacklist", "黑")

    def _build_list(self, parent, kind: str, color: str) -> None:
        # 列表
        list_frame = ttk.Frame(parent)
        list_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        tree = ttk.Treeview(list_frame, columns=("name",), show="headings", height=14)
        tree.heading("name", text=f"{color}名单")
        tree.column("name", width=180)
        ys = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=ys.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ys.pack(side=tk.RIGHT, fill=tk.Y)
        if kind == "whitelist":
            self.tree_wl = tree
        else:
            self.tree_bl = tree

        # 操作
        op = ttk.Frame(parent)
        op.pack(side=tk.TOP, fill=tk.X, pady=4)
        var = tk.StringVar()
        ttk.Entry(op, textvariable=var, width=20).pack(side=tk.LEFT, padx=2)
        ttk.Button(
            op, text="➕ 添加", command=lambda k=kind, v=var: self._add(k, v)
        ).pack(side=tk.LEFT, padx=2)
        ttk.Button(
            op, text="➖ 删除选中", command=lambda k=kind: self._remove(k)
        ).pack(side=tk.LEFT, padx=2)

    # ------------------------------------------------------------ actions
    def _refresh(self) -> None:
        for t in (self.tree_wl, self.tree_bl):
            for iid in t.get_children():
                t.delete(iid)
        for name in self.cf.whitelist():
            self.tree_wl.insert("", tk.END, values=(name,))
        for name in self.cf.blacklist():
            self.tree_bl.insert("", tk.END, values=(name,))

    def _add(self, kind: str, var: tk.StringVar) -> None:
        name = var.get().strip()
        if not name:
            return
        if kind == "whitelist":
            self.cf.add_whitelist(name)
        else:
            self.cf.add_blacklist(name)
        var.set("")
        self._refresh()

    def _remove(self, kind: str) -> None:
        tree = self.tree_wl if kind == "whitelist" else self.tree_bl
        sel = tree.selection()
        for s in sel:
            name = tree.item(s, "values")[0]
            if kind == "whitelist":
                self.cf.remove_whitelist(name)
            else:
                self.cf.remove_blacklist(name)
        self._refresh()

    def _toggle_enabled(self) -> None:
        self.cfg.whitelist.enabled = bool(self.var_enabled.get())
        self.cfg.save()
        log.info(f"白名单启用: {self.cfg.whitelist.enabled}")

    def _save_policy(self) -> None:
        self.cfg.whitelist.enabled = bool(self.var_enabled.get())
        self.cfg.save()
        messagebox.showinfo("已保存", "策略已写入 wechat_config.json")

"""History tab: browse chat history & AI replies by user / time.

新增 (v2.4): 外层 ScrolledFrame 包装
"""
from __future__ import annotations

import os
import subprocess
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk
from typing import Dict, List, Optional

from gui.widgets import ScrolledFrame
from wechat_bot.config import BotConfig
from wechat_bot.logger import get_logger
from wechat_bot.storage import Storage

log = get_logger("gui.history_tab")


class HistoryTab(ttk.Frame):
    def __init__(self, master: tk.Misc, cfg: BotConfig, storage: Storage):
        super().__init__(master, padding=8)
        self.cfg = cfg
        self.storage = storage
        # 滚动容器
        self._sf = ScrolledFrame(self)
        self._sf.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.body = self._sf.interior
        self._build()
        self._refresh()

    def _build(self) -> None:
        body = self.body
        # 顶部：搜索 + 切换
        top = ttk.Frame(body)
        top.pack(side=tk.TOP, fill=tk.X)
        ttk.Label(top, text="搜索用户:").pack(side=tk.LEFT, padx=2)
        self.var_search = tk.StringVar()
        ent = ttk.Entry(top, textvariable=self.var_search, width=20)
        ent.pack(side=tk.LEFT, padx=2)
        ent.bind("<KeyRelease>", lambda _e: self._refresh())
        ttk.Label(top, text="类型:").pack(side=tk.LEFT, padx=(8, 2))
        self.var_type = tk.StringVar(value="all")
        cb = ttk.Combobox(
            top,
            textvariable=self.var_type,
            values=["all", "chat", "reply"],
            width=8,
            state="readonly",
        )
        cb.pack(side=tk.LEFT, padx=2)
        cb.bind("<<ComboboxSelected>>", lambda _e: self._refresh())
        ttk.Button(top, text="🔄 刷新", command=self._refresh).pack(side=tk.LEFT, padx=8)
        ttk.Button(top, text="📂 打开文件", command=self._open_selected).pack(
            side=tk.LEFT, padx=2
        )

        # 中：列表 + 详情
        body2 = ttk.Frame(body)
        body2.pack(side=tk.TOP, fill=tk.BOTH, expand=True, pady=(8, 0))
        left = ttk.LabelFrame(body2, text="记录", padding=4)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 4))
        right = ttk.LabelFrame(body2, text="内容预览", padding=4)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(4, 0))

        cols = ("type", "user", "time", "size")
        self.tree = ttk.Treeview(left, columns=cols, show="headings", height=20)
        for c, t, w in [
            ("type", "类型", 50),
            ("user", "用户", 140),
            ("time", "时间", 130),
            ("size", "大小", 60),
        ]:
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor=tk.W)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._on_select())
        ys = ttk.Scrollbar(left, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=ys.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ys.pack(side=tk.RIGHT, fill=tk.Y)

        self.text = tk.Text(
            right, wrap=tk.WORD, font=("Consolas", 10), state=tk.DISABLED
        )
        ys2 = ttk.Scrollbar(right, orient=tk.VERTICAL, command=self.text.yview)
        self.text.configure(yscrollcommand=ys2.set)
        self.text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ys2.pack(side=tk.RIGHT, fill=tk.Y)

        self._current_path: Optional[str] = None

    # ------------------------------------------------------------ data
    def _scan(self) -> List[Dict]:
        rows: List[Dict] = []
        for sub, type_ in [
            (self.storage.history_dir, "chat"),
            (self.storage.replies_dir, "reply"),
        ]:
            if not os.path.isdir(sub):
                continue
            for f in os.listdir(sub):
                if not f.endswith(".txt"):
                    continue
                p = os.path.join(sub, f)
                try:
                    st = os.stat(p)
                except Exception:
                    continue
                # 解析文件名: 用户_时间戳.txt / 用户_ai_reply_时间戳.txt
                base = f[:-4]
                parts = base.split("_")
                if type_ == "reply" and "ai" in parts and "reply" in parts:
                    ai_idx = parts.index("ai")
                    user = "_".join(parts[:ai_idx])
                    ts = "_".join(parts[ai_idx + 2 :])  # skip 'ai','reply'
                else:
                    user = "_".join(parts[:-2]) if len(parts) >= 2 else parts[0]
                    ts = "_".join(parts[-2:])
                try:
                    dt = datetime.strptime(ts, "%Y%m%d_%H%M%S")
                except Exception:
                    dt = None
                rows.append(
                    {
                        "type": type_,
                        "user": user,
                        "time": dt.strftime("%Y-%m-%d %H:%M:%S") if dt else ts,
                        "sort_key": ts,
                        "size": st.st_size,
                        "path": p,
                    }
                )
        rows.sort(key=lambda r: r["sort_key"], reverse=True)
        return rows

    def _refresh(self) -> None:
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        rows = self._scan()
        needle = self.var_search.get().strip().lower()
        type_filter = self.var_type.get()
        for r in rows:
            if type_filter != "all" and r["type"] != type_filter:
                continue
            if needle and needle not in r["user"].lower():
                continue
            self.tree.insert(
                "", tk.END, values=(r["type"], r["user"], r["time"], r["size"])
            )
            self.tree.set(self.tree.get_children()[-1], "user", r["user"])
            # store path
            self.tree_iid_to_path = getattr(self, "tree_iid_to_path", {})
            last = self.tree.get_children()[-1]
            self.tree_iid_to_path[last] = r["path"]

    def _on_select(self) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        path = self.tree_iid_to_path.get(sel[0])
        if not path:
            return
        self._current_path = path
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            content = f"(读取失败: {e})"
        self.text.configure(state=tk.NORMAL)
        self.text.delete("1.0", tk.END)
        self.text.insert(tk.END, content)
        self.text.configure(state=tk.DISABLED)

    def _open_selected(self) -> None:
        if not self._current_path:
            messagebox.showinfo("提示", "请先选一条记录")
            return
        try:
            if hasattr(os, "startfile"):
                os.startfile(self._current_path)  # type: ignore[attr-defined]
            else:
                subprocess.call(["xdg-open", self._current_path])
        except Exception as e:
            messagebox.showerror("失败", str(e))

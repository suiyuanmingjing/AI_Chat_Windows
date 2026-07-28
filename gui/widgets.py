"""Shared tkinter widgets used across tabs.

包含:
    ScrolledFrame - 垂直滚动容器（tab 内容超过窗口时可滚）
    LogTail     - 实时日志尾随（从文件 tail + 内存 ring buffer）
    StatusBar   - 底部状态条（daemon 状态 + 活动）
    ChartCanvas - matplotlib 嵌入 tkinter 的画布
    ImagePreview - 截图预览（带自动刷新）
"""
from __future__ import annotations

import os
import time
import tkinter as tk
from collections import deque
from tkinter import ttk
from typing import Any, Callable, Deque, Dict, List, Optional


# ============================================================ ScrolledFrame
class ScrolledFrame(ttk.Frame):
    """可垂直滚动的容器 (Canvas + Scrollbar + 内部 Frame).

    用法:
        sf = ScrolledFrame(parent)
        sf.pack(fill=tk.BOTH, expand=True)
        body = sf.interior  # ← 把所有 widget 挂到这里
        ttk.Label(body, ...).pack(...)
    """

    def __init__(self, master: tk.Misc, **kwargs: Any) -> None:
        super().__init__(master, **kwargs)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        self.vscroll = ttk.Scrollbar(self, orient=tk.VERTICAL, command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vscroll.set)

        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        # vscroll 默认不显示 (需要时再 pack)
        self._scrollbar_packed = False

        self.interior = ttk.Frame(self.canvas)
        self._win_id = self.canvas.create_window(
            (0, 0), window=self.interior, anchor=tk.NW
        )

        # interior 尺寸变化 → 刷新 scrollregion
        self.interior.bind("<Configure>", self._on_interior_configure)
        # canvas 尺寸变化 → 让 interior 宽度跟上 + 重算滚动条显隐
        self.canvas.bind("<Configure>", self._on_canvas_configure)

        # 鼠标滚轮：进入 canvas 时绑定，移出时解绑（避免污染其他 tab）
        self.canvas.bind("<Enter>", self._bind_wheel)
        self.canvas.bind("<Leave>", self._unbind_wheel)
        self._wheel_bound = False

    # ----- 内部事件 -----
    def _on_interior_configure(self, _e: tk.Event) -> None:
        try:
            self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        except Exception:
            pass
        # 滚动条显隐联动
        self._update_scrollbar_visibility()

    def _on_canvas_configure(self, e: tk.Event) -> None:
        try:
            self.canvas.itemconfigure(self._win_id, width=e.width)
        except Exception:
            pass
        self._update_scrollbar_visibility()

    def _update_scrollbar_visibility(self) -> None:
        """内容未超出 canvas 时隐藏滚动条, 超出时显示."""
        try:
            canvas_h = self.canvas.winfo_height()
            interior_h = self.interior.winfo_reqheight()
            # 留 2px 阈值避免抖动
            need = interior_h > canvas_h + 2
        except Exception:
            return
        if need and not self._scrollbar_packed:
            self.vscroll.pack(side=tk.RIGHT, fill=tk.Y)
            self._scrollbar_packed = True
        elif not need and self._scrollbar_packed:
            self.vscroll.pack_forget()
            self._scrollbar_packed = False

    def _bind_wheel(self, _e: tk.Event) -> None:
        if self._wheel_bound:
            return
        self._wheel_bound = True
        # Windows / macOS
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        # Linux (X11)
        self.canvas.bind_all("<Button-4>", lambda _e: self.canvas.yview_scroll(-1, "units"))
        self.canvas.bind_all("<Button-5>", lambda _e: self.canvas.yview_scroll(1, "units"))

    def _unbind_wheel(self, _e: tk.Event) -> None:
        if not self._wheel_bound:
            return
        self._wheel_bound = False
        self.canvas.unbind_all("<MouseWheel>")
        self.canvas.unbind_all("<Button-4>")
        self.canvas.unbind_all("<Button-5>")

    def _on_mousewheel(self, e: tk.Event) -> None:
        # 只在鼠标真正在自己 canvas 上时才滚
        x, y = e.x_root, e.y_root
        try:
            w = self.canvas.winfo_containing(x, y)
        except Exception:
            w = None
        if w is None or self.canvas.winfo_id() != w.winfo_id():
            return
        # Windows: delta=±120; Mac: delta=±1
        try:
            if e.delta > 0:
                self.canvas.yview_scroll(-1, "units")
            elif e.delta < 0:
                self.canvas.yview_scroll(1, "units")
        except Exception:
            pass


# ============================================================ LogTail
class LogTail(ttk.Frame):
    """A scrollable text widget that tails a log file and supports filtering."""

    def __init__(
        self,
        master: tk.Misc,
        log_path: str,
        max_lines: int = 5000,
        poll_ms: int = 1000,
    ):
        super().__init__(master)
        self.log_path = log_path
        self.max_lines = max_lines
        self.poll_ms = poll_ms
        self._buffer: Deque[str] = deque(maxlen=max_lines)
        self._filter_text: str = ""
        self._level_filter: str = "ALL"
        self._pos = 0  # file read pointer
        self._alive = True

        # toolbar
        bar = ttk.Frame(self)
        bar.pack(side=tk.TOP, fill=tk.X)
        ttk.Label(bar, text="过滤:").pack(side=tk.LEFT, padx=(4, 2))
        self.var_filter = tk.StringVar()
        ent = ttk.Entry(bar, textvariable=self.var_filter, width=24)
        ent.pack(side=tk.LEFT, padx=2)
        ent.bind("<KeyRelease>", lambda _e: self._on_filter())
        ttk.Label(bar, text="级别:").pack(side=tk.LEFT, padx=(8, 2))
        self.var_level = tk.StringVar(value="ALL")
        cb = ttk.Combobox(
            bar,
            textvariable=self.var_level,
            values=["ALL", "INFO", "WARNING", "ERROR", "DEBUG"],
            width=10,
            state="readonly",
        )
        cb.pack(side=tk.LEFT, padx=2)
        cb.bind("<<ComboboxSelected>>", lambda _e: self._on_filter())
        ttk.Button(bar, text="清屏", command=self._clear).pack(side=tk.RIGHT, padx=2)
        ttk.Button(bar, text="打开文件", command=self._open_in_explorer).pack(
            side=tk.RIGHT, padx=2
        )

        # text
        wrap = ttk.Frame(self)
        wrap.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.text = tk.Text(
            wrap, wrap=tk.NONE, font=("Consolas", 9), bg="#1e1e1e", fg="#d4d4d4"
        )
        ys = ttk.Scrollbar(wrap, orient=tk.VERTICAL, command=self.text.yview)
        xs = ttk.Scrollbar(wrap, orient=tk.HORIZONTAL, command=self.text.xview)
        self.text.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        self.text.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="we")
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)
        self.text.tag_configure("INFO", foreground="#9cdcfe")
        self.text.tag_configure("WARNING", foreground="#dcdcaa")
        self.text.tag_configure("ERROR", foreground="#f48771")
        self.text.tag_configure("DEBUG", foreground="#808080")

        self._schedule()

    # ---- public ----
    def stop(self) -> None:
        self._alive = False

    # ---- internal ----
    def _schedule(self) -> None:
        if not self._alive:
            return
        try:
            self._poll()
        except Exception:
            pass
        self.after(self.poll_ms, self._schedule)

    def _poll(self) -> None:
        if not os.path.exists(self.log_path):
            return
        try:
            with open(self.log_path, "r", encoding="utf-8", errors="ignore") as f:
                f.seek(self._pos)
                chunk = f.read()
                self._pos = f.tell()
        except Exception:
            return
        if not chunk:
            return
        for line in chunk.splitlines():
            self._buffer.append(line)
        self._refresh_view()

    def _refresh_view(self) -> None:
        self.text.delete("1.0", tk.END)
        for line in self._buffer:
            if self._filter_text and self._filter_text.lower() not in line.lower():
                continue
            if self._level_filter != "ALL" and f" - {self._level_filter} - " not in line:
                continue
            tag = "INFO"
            for lvl in ("ERROR", "WARNING", "DEBUG", "INFO"):
                if f" - {lvl} - " in line:
                    tag = lvl
                    break
            self.text.insert(tk.END, line + "\n", tag)
        self.text.see(tk.END)

    def _on_filter(self) -> None:
        self._filter_text = self.var_filter.get().strip()
        self._level_filter = self.var_level.get()
        self._refresh_view()

    def _clear(self) -> None:
        self._buffer.clear()
        self.text.delete("1.0", tk.END)

    def _open_in_explorer(self) -> None:
        if not os.path.exists(self.log_path):
            return
        try:
            os.startfile(os.path.dirname(self.log_path))  # type: ignore[attr-defined]
        except Exception:
            try:
                os.system(f'explorer "{os.path.dirname(self.log_path)}"')
            except Exception:
                pass


# ============================================================ StatusBar
class StatusBar(ttk.Frame):
    """Bottom status bar: shows daemon state, last update, current activity."""

    def __init__(self, master: tk.Misc):
        super().__init__(master, relief=tk.SUNKEN, padding=4)
        self.var_state = tk.StringVar(value="● 未连接")
        self.var_activity = tk.StringVar(value="")
        self.var_last = tk.StringVar(value="")
        ttk.Label(self, textvariable=self.var_state, width=18).pack(
            side=tk.LEFT, padx=(4, 8)
        )
        ttk.Label(self, textvariable=self.var_activity, width=60).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Label(self, textvariable=self.var_last).pack(side=tk.RIGHT, padx=4)

    def update_status(self, status: Optional[Dict[str, Any]]) -> None:
        if not status:
            self.var_state.set("● 守护未运行")
            self.var_activity.set("")
            self.var_last.set("")
            return
        st = status.get("state", "?")
        icon = {
            "running": "🟢",
            "paused": "🟡",
            "waiting_wechat": "🟠",
            "cooldown": "🔴",
        }.get(st, "⚪")
        self.var_state.set(f"{icon} {st}")
        self.var_activity.set(status.get("current_activity", "")[:60])
        self.var_last.set("last update: " + str(status.get("last_update", ""))[:19])


# ============================================================ ChartCanvas
class ChartCanvas(ttk.Frame):
    """Matplotlib embedded chart. Falls back to text when matplotlib missing."""

    def __init__(self, master: tk.Misc, height_inches: float = 3.0):
        super().__init__(master)
        self._mpl = None
        try:
            import matplotlib  # type: ignore
            matplotlib.use("TkAgg")
            from matplotlib.figure import Figure  # type: ignore
            from matplotlib.backends.backend_tkagg import (  # type: ignore
                FigureCanvasTkAgg,
            )

            self._mpl = (Figure, FigureCanvasTkAgg)
            self.fig = Figure(figsize=(6, height_inches), dpi=100)
            self.canvas = FigureCanvasTkAgg(self.fig, master=self)
            self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        except Exception as e:
            ttk.Label(self, text=f"matplotlib 不可用: {e}").pack()

    def draw_bar(self, data: Dict[str, int], title: str = "") -> None:
        if not self._mpl:
            return
        Figure = self._mpl[0]
        self.fig.clear()
        ax = self.fig.add_subplot(111)
        items = sorted(data.items(), key=lambda x: -x[1])[:10]
        if not items:
            ax.text(0.5, 0.5, "暂无数据", ha="center", va="center", transform=ax.transAxes)
        else:
            names = [k or "(空)" for k, _ in items]
            vals = [v for _, v in items]
            ax.barh(range(len(names)), vals, color="#4a9eff")
            ax.set_yticks(range(len(names)))
            ax.set_yticklabels(names)
            ax.invert_yaxis()
        if title:
            ax.set_title(title)
        self.fig.tight_layout()
        self.canvas.draw()

    def draw_line(self, data: List[tuple], title: str = "", xlabel: str = "", ylabel: str = "") -> None:
        if not self._mpl or not data:
            return
        Figure = self._mpl[0]
        self.fig.clear()
        ax = self.fig.add_subplot(111)
        xs = [d[0] for d in data]
        ys = [d[1] for d in data]
        ax.plot(xs, ys, marker="o", color="#4a9eff")
        if title:
            ax.set_title(title)
        if xlabel:
            ax.set_xlabel(xlabel)
        if ylabel:
            ax.set_ylabel(ylabel)
        self.fig.tight_layout()
        self.canvas.draw()


# ============================================================ ImagePreview
class ImagePreview(ttk.Label):
    """Auto-refreshing screenshot preview."""

    def __init__(self, master: tk.Misc, poll_ms: int = 2000):
        super().__init__(master, text="(无图像)", background="#222")
        self.poll_ms = poll_ms
        self._alive = True
        self._source: Optional[Callable[[], Optional["Image.Image"]]] = None
        self._last_id: Optional[int] = None
        self._max_size = (640, 480)
        self._photo: Optional[Any] = None
        self._schedule()

    def set_source(self, source: Callable[[], Optional["Image.Image"]]) -> None:
        self._source = source

    def stop(self) -> None:
        self._alive = False

    def _schedule(self) -> None:
        if not self._alive:
            return
        try:
            self._refresh()
        except Exception:
            pass
        self.after(self.poll_ms, self._schedule)

    def _refresh(self) -> None:
        if not self._source:
            return
        img = self._source()
        if img is None:
            return
        # hash to skip identical
        try:
            import hashlib

            h = hashlib.md5(img.tobytes()).hexdigest()
            if h == self._last_id:
                return
            self._last_id = h
        except Exception:
            pass
        img2 = img.copy()
        img2.thumbnail(self._max_size)
        try:
            from PIL import ImageTk  # local import

            self._photo = ImageTk.PhotoImage(img2)
            self.configure(image=self._photo, text="")
        except Exception:
            pass

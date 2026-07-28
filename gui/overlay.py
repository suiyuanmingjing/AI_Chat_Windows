"""Fullscreen overlay tools for visual region picking.

- DragSelector  : 鼠标拖动画矩形，松手返回 (x, y, w, h)
- ClickPicker   : 任意位置单击，返回 (x, y)

使用方式: 在调用方临时 new 一个，工具关闭时 Toplevel 自动销毁。
        均为非模态（不阻塞主窗口），按 Esc 或右键取消。
"""
from __future__ import annotations

import tkinter as tk
from typing import Callable, Optional, Tuple


def _hint_font() -> tuple:
    # 中文不指定 font 时 Win10 上是方块
    return ("Microsoft YaHei", 11)


class _BaseOverlay:
    """A semi-transparent fullscreen Toplevel that captures input."""

    MIN_W = 4
    MIN_H = 4

    def __init__(
        self,
        root: tk.Misc,
        on_done: Callable,
        on_cancel: Optional[Callable[[], None]] = None,
        hint: str = "",
        tint: str = "#202020",
    ):
        self.root = root
        self.on_done = on_done
        self.on_cancel = on_cancel
        self.hint = hint
        self.tint = tint
        self._alive = True
        self._build()

    # --------------------------------------------------------------- build
    def _build(self) -> None:
        ov = tk.Toplevel(self.root)
        ov.attributes("-fullscreen", True)
        ov.attributes("-alpha", 0.30)
        ov.attributes("-topmost", True)
        ov.configure(bg=self.tint)
        try:
            ov.config(cursor="crosshair")
        except Exception:
            pass
        self.ov = ov

        canvas = tk.Canvas(ov, bg=self.tint, highlightthickness=0)
        canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas = canvas

        if self.hint:
            badge = tk.Label(
                canvas,
                text=self.hint,
                bg="#000000",
                fg="#ffffff",
                font=_hint_font(),
                padx=14,
                pady=8,
            )
            badge.place(relx=0.5, y=24, anchor="n")

        # 绑定
        canvas.bind("<Button-3>", lambda _e: self._cancel())  # 右键取消
        ov.bind("<Escape>", lambda _e: self._cancel())
        ov.bind("<Button-1>", self._on_click)
        ov.bind("<B1-Motion>", self._on_drag)
        ov.bind("<ButtonRelease-1>", self._on_release)
        ov.focus_force()
        ov.grab_set_global()

    # --------------------------------------------------------------- helpers
    def _cleanup(self) -> None:
        if not self._alive:
            return
        self._alive = False
        try:
            self.ov.grab_release()
        except Exception:
            pass
        try:
            self.ov.destroy()
        except Exception:
            pass

    def _cancel(self) -> None:
        self._cleanup()
        if self.on_cancel:
            try:
                self.on_cancel()
            except Exception:
                pass


class DragSelector(_BaseOverlay):
    """Drag to draw a rectangle. Returns (x, y, w, h) on release."""

    def __init__(
        self,
        root: tk.Misc,
        on_done: Callable[[int, int, int, int], None],
        on_cancel: Optional[Callable[[], None]] = None,
        hint: str = "🖱 拖动鼠标框选区域  |  Esc/右键 取消",
        tint: str = "#202020",
    ):
        self._start: Optional[Tuple[int, int]] = None
        self._rect_id: Optional[int] = None
        super().__init__(root, on_done, on_cancel, hint, tint)

    def _on_click(self, e: tk.Event) -> None:
        self._start = (e.x, e.y)
        if self._rect_id is not None:
            self.canvas.delete(self._rect_id)
        self._rect_id = self.canvas.create_rectangle(
            e.x, e.y, e.x, e.y,
            outline="#ff5252", width=2, dash=(4, 2),
        )

    def _on_drag(self, e: tk.Event) -> None:
        if self._start is None or self._rect_id is None:
            return
        self.canvas.coords(
            self._rect_id,
            self._start[0], self._start[1], e.x, e.y,
        )

    def _on_release(self, e: tk.Event) -> None:
        if self._start is None:
            return
        x0, y0 = self._start
        x1, y1 = e.x, e.y
        x = min(x0, x1)
        y = min(y0, y1)
        w = abs(x1 - x0)
        h = abs(y1 - y0)
        if w < self.MIN_W or h < self.MIN_H:
            # 框太小当作取消
            self._cancel()
            return
        self._cleanup()
        try:
            self.on_done(x, y, w, h)
        except Exception:
            pass


class ClickPicker(_BaseOverlay):
    """Single click captures position. Returns (x, y)."""

    def __init__(
        self,
        root: tk.Misc,
        on_pick: Callable[[int, int], None],
        on_cancel: Optional[Callable[[], None]] = None,
        hint: str = "🖱 点击鼠标取点  |  Esc/右键 取消",
        tint: str = "#0a3d62",
    ):
        super().__init__(root, on_pick, on_cancel, hint, tint)

    def _on_click(self, e: tk.Event) -> None:
        self._cleanup()
        try:
            self.on_done(e.x, e.y)
        except Exception:
            pass

    def _on_drag(self, _e: tk.Event) -> None:
        return

    def _on_release(self, _e: tk.Event) -> None:
        return

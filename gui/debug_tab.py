"""Debug tab: live OCR + contact extraction + threshold preview.

新增 (v2.4): 外层 ScrolledFrame 包装
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any, Dict, List, Optional

import cv2
import numpy as np
from PIL import Image

from gui.widgets import ImagePreview, ScrolledFrame
from wechat_bot.color_utils import classify, filter_overlapping, split_by_color
from wechat_bot.config import BotConfig
from wechat_bot.logger import get_logger
from wechat_bot.ocr_engine import OcrEngine
from wechat_bot.window import WindowManager

log = get_logger("gui.debug_tab")


class DebugTab(ttk.Frame):
    def __init__(self, master: tk.Misc, cfg: BotConfig, ocr: OcrEngine, window: WindowManager):
        super().__init__(master, padding=8)
        self.cfg = cfg
        self.ocr = ocr
        self.window = window
        self._last_results: List[Dict[str, Any]] = []
        self._last_img: Optional[np.ndarray] = None
        # 滚动容器
        self._sf = ScrolledFrame(self)
        self._sf.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.body = self._sf.interior
        self._build()

    def _build(self) -> None:
        body = self.body
        # 上：控制条
        bar = ttk.Frame(body)
        bar.pack(side=tk.TOP, fill=tk.X)
        ttk.Button(bar, text="📷 截图并 OCR (contacts)", command=self._capture_ocr).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(bar, text="📷 截图 (chat)", command=self._capture_chat).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(bar, text="🖼 显示标注图", command=self._show_overlay).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Label(bar, text="  区域:").pack(side=tk.LEFT, padx=(16, 2))
        self.var_region = tk.StringVar(value="contacts")
        ttk.Combobox(
            bar,
            textvariable=self.var_region,
            values=["contacts", "username", "chat"],
            width=10,
            state="readonly",
        ).pack(side=tk.LEFT)

        ttk.Button(bar, text="✖ 清空", command=self._clear).pack(side=tk.RIGHT, padx=2)

        # 阈值滑块（仅调试页生效，不写回 cfg）
        slider_bar = ttk.Frame(body)
        slider_bar.pack(side=tk.TOP, fill=tk.X, pady=(0, 4))
        ttk.Label(slider_bar, text="临时黑/灰阈值 (调试用):").pack(side=tk.LEFT, padx=2)
        self.var_bt = tk.IntVar(value=self.cfg.black_text_threshold)
        self.var_gt = tk.IntVar(value=self.cfg.gray_text_threshold)
        ttk.Label(slider_bar, text="黑 <").pack(side=tk.LEFT, padx=(8, 2))
        ttk.Spinbox(
            slider_bar, from_=0, to=255, width=6, textvariable=self.var_bt,
            command=self._on_threshold_change,
        ).pack(side=tk.LEFT)
        ttk.Label(slider_bar, text="灰 >=").pack(side=tk.LEFT, padx=(8, 2))
        ttk.Spinbox(
            slider_bar, from_=0, to=255, width=6, textvariable=self.var_gt,
            command=self._on_threshold_change,
        ).pack(side=tk.LEFT)
        ttk.Label(
            slider_bar,
            text="（调这里只影响当前显示，调好后回配置页保存）",
            foreground="#666",
        ).pack(side=tk.LEFT, padx=8)

        # 中：左侧预览，右侧结果
        body2 = ttk.Frame(body)
        body2.pack(side=tk.TOP, fill=tk.BOTH, expand=True, pady=8)
        left = ttk.LabelFrame(body2, text="截图预览", padding=4)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 4))
        right = ttk.LabelFrame(body2, text="OCR 结果", padding=4)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(4, 0))

        self.preview = ImagePreview(left, poll_ms=3000)
        self.preview.pack(fill=tk.BOTH, expand=True)

        # 结果区
        self.tree = ttk.Treeview(
            right, columns=("text", "score", "brightness", "type"), show="headings"
        )
        for c, t, w in [
            ("text", "文本", 220),
            ("score", "置信度", 60),
            ("brightness", "亮度", 60),
            ("type", "类型", 60),
        ]:
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor=tk.W)
        ys = ttk.Scrollbar(right, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=ys.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ys.pack(side=tk.RIGHT, fill=tk.Y)

    # ------------------------------------------------------- actions
    def _current_region(self):
        name = self.var_region.get()
        if name == "username":
            return self.cfg.username_region
        if name == "chat":
            return self.cfg.chat_region
        return self.cfg.contacts_region

    def _capture_ocr(self) -> None:
        region = self._current_region()
        img = self.window.screenshot(region)
        if img is None:
            log.warning("截图失败")
            return
        self._last_img = np.asarray(img)
        try:
            self._last_results = self.ocr.recognize(self._last_img)
        except Exception as e:
            log.error(f"OCR 失败: {e}")
            return
        self._refresh_tree()
        self._save_overlay()

    def _capture_chat(self) -> None:
        self.var_region.set("chat")
        self._capture_ocr()

    def _show_overlay(self) -> None:
        if self._last_img is None:
            self._capture_ocr()
        # The ImagePreview will pick up the saved file on next poll

    def _save_overlay(self) -> None:
        if self._last_img is None or not self._last_results:
            return
        try:
            import os
            from wechat_bot.storage import Storage
            from wechat_bot.color_utils import to_aabb

            storage = Storage(self.cfg.data_dir)
            overlay = self._last_img.copy()
            bt = int(self.var_bt.get())
            gt = int(self.var_gt.get())
            for item in self._last_results:
                pos = item.get("position", [0, 0, 0, 0])
                x1, y1, x2, y2 = to_aabb(pos)
                info = classify(self._last_img, pos, bt, gt)
                color = (
                    (0, 255, 0)
                    if info["is_black"]
                    else (255, 0, 0)
                    if info["is_gray"]
                    else (0, 0, 255)
                )
                cv2.rectangle(overlay, (x1, y1), (x2, y2), color, 2)
            path = storage.debug_path("debug_overlay.png")
            Image.fromarray(overlay).save(path)
            # 用闭包换 source，指向新文件（每次 _refresh 会重新读盘）
            def _src():
                try:
                    return Image.open(path)
                except Exception:
                    return None
            self.preview.set_source(_src)
        except Exception as e:
            log.error(f"保存标注图失败: {e}")

    def _refresh_tree(self) -> None:
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        if self._last_img is None:
            return
        # split into black/gray（使用调试页临时阈值）
        try:
            black, gray = split_by_color(
                self._last_results,
                self._last_img,
                int(self.var_bt.get()),
                int(self.var_gt.get()),
            )
        except Exception:
            black, gray = [], []
        for c in black:
            self.tree.insert(
                "",
                tk.END,
                values=(
                    c["text"],
                    f"{c['confidence']:.2f}",
                    f"{c['brightness']:.0f}",
                    "黑色",
                ),
            )
        for c in gray:
            self.tree.insert(
                "",
                tk.END,
                values=(
                    c["text"],
                    f"{c['confidence']:.2f}",
                    f"{c['brightness']:.0f}",
                    "灰色",
                ),
            )
        log.info(f"OCR: 黑 {len(black)} / 灰 {len(gray)}")

    def _on_threshold_change(self) -> None:
        """调阈值时，若已有截图则重算分类并刷新覆盖图。"""
        if self._last_img is not None and self._last_results:
            self._refresh_tree()
            self._save_overlay()

    def _clear(self) -> None:
        self._last_results = []
        self._last_img = None
        for iid in self.tree.get_children():
            self.tree.delete(iid)

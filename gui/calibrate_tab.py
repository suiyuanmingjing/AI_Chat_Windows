"""Calibrate tab: 鼠标拖选 + 全屏预览 + 3 区叠加 + 3 小图 + 阈值联动.

布局:
    [拖选/取点] [操作]
    ┌─区域 & 阈值─┐ ┌─全屏预览 (微信窗口 + 3 区叠加) ─┐
    │ 3 个区域    │ │                                  │
    │ 输入框位置  │ │  阈值 Spinbox (与下方小图联动)     │
    │ 阈值        │ ├──────────────────────────────────┤
    └────────────┘ │  联系人  用户名  聊天              │
                   │  (3 小预览，可跑 OCR+分类)         │
                   └──────────────────────────────────┘

新增 (v2.4): 外层 ScrolledFrame 包装
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Callable, Dict, List, Optional

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageTk

from gui.overlay import ClickPicker, DragSelector
from gui.widgets import ImagePreview, ScrolledFrame
from wechat_bot.color_utils import classify
from wechat_bot.config import BotConfig
from wechat_bot.logger import get_logger
from wechat_bot.window import WindowManager

log = get_logger("gui.calibrate_tab")


# 区域 → 叠加框颜色 (BGR)
REGION_COLORS = {
    "contacts_region": (0, 200, 0),    # 绿
    "username_region": (200, 0, 0),    # 蓝 (cv2 BGR)
    "chat_region": (0, 165, 255),      # 橙
}
REGION_LABELS = {
    "contacts_region": "联系人区域",
    "username_region": "用户名区域",
    "chat_region": "聊天记录区域",
}
POLL_MS = 2000


# ============================================================ 全屏预览
class FullWindowPreview(tk.Label):
    """Auto-refreshing full window screenshot with 3 region overlays."""

    def __init__(
        self,
        master: tk.Misc,
        cfg: BotConfig,
        window: WindowManager,
        region_vars: Dict[str, List[tk.IntVar]],
    ):
        super().__init__(master, text="(等待窗口…)", background="#222", foreground="#aaa")
        self.cfg = cfg
        self.window = window
        self.region_vars = region_vars
        self._alive = True
        self._max_size = (960, 540)
        self._photo: Optional[Any] = None
        self._schedule()

    def stop(self) -> None:
        self._alive = False

    def _schedule(self) -> None:
        if not self._alive:
            return
        try:
            self._refresh()
        except Exception:
            pass
        self.after(POLL_MS, self._schedule)

    def _refresh(self) -> None:
        bbox = self.window.window_bbox()
        if not bbox:
            self.configure(text="(未找到微信窗口，请点 '🎯 激活微信')", image="", background="#3a1f1f")
            return
        img = self.window.screenshot(bbox)
        if img is None:
            return
        arr = np.asarray(img).copy()
        # 画 3 个区域的叠加框 (相对窗口内坐标)
        wx, wy, ww, wh = bbox
        for key, color in REGION_COLORS.items():
            vars_ = self.region_vars.get(key)
            if not vars_ or len(vars_) != 4:
                continue
            rx, ry, rw, rh = [int(v.get()) for v in vars_]
            # 转为窗口内坐标
            lx, ly = rx - wx, ry - wy
            if lx + rw <= 0 or ly + rh <= 0 or lx >= ww or ly >= wh:
                continue
            lx = max(0, lx)
            ly = max(0, ly)
            rw = min(rw, ww - lx)
            rh = min(rh, wh - ly)
            cv2.rectangle(arr, (lx, ly), (lx + rw, ly + rh), color, 2)
            # 写标签
            label = REGION_LABELS.get(key, key)
            cv2.putText(
                arr, label, (lx + 4, ly + 18),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA,
            )
        # 转 PhotoImage
        pil = Image.fromarray(arr)
        pil.thumbnail(self._max_size)
        try:
            self._photo = ImageTk.PhotoImage(pil)
            self.configure(image=self._photo, text="", background="#222")
        except Exception:
            pass


# ============================================================ 区域小预览
class RegionPreview(tk.Label):
    """Single region preview, with optional OCR + classification overlay."""

    def __init__(
        self,
        master: tk.Misc,
        cfg: BotConfig,
        window: WindowManager,
        region_key: str,
        region_vars: Dict[str, List[tk.IntVar]],
        black_th: tk.IntVar,
        gray_th: tk.IntVar,
        ocr_cache: Dict[str, List[Dict]],
        ocr_engine=None,
    ):
        super().__init__(master, text="(等待截图…)", background="#222", foreground="#aaa")
        self.cfg = cfg
        self.window = window
        self.region_key = region_key
        self.region_vars = region_vars
        self.black_th = black_th
        self.gray_th = gray_th
        self.ocr_cache = ocr_cache
        self.ocr_engine = ocr_engine
        self._alive = True
        self._max_size = (320, 200)
        self._photo: Optional[Any] = None
        self._last_screenshot_id: Optional[str] = None
        self._last_pil: Optional[Image.Image] = None
        # 监听阈值变化 → 重绘
        black_th.trace_add("write", lambda *_: self._rerender())
        gray_th.trace_add("write", lambda *_: self._rerender())
        self._schedule()

    def stop(self) -> None:
        self._alive = False

    def _schedule(self) -> None:
        if not self._alive:
            return
        try:
            self._refresh()
        except Exception:
            pass
        self.after(POLL_MS, self._schedule)

    def _current_region(self) -> Optional[tuple]:
        vars_ = self.region_vars.get(self.region_key)
        if not vars_ or len(vars_) != 4:
            return None
        r = tuple(int(v.get()) for v in vars_)
        if r[2] <= 0 or r[3] <= 0:
            return None
        return r

    def _refresh(self) -> None:
        region = self._current_region()
        if not region:
            self.configure(text="(区域无效)", image="", background="#3a1f1f")
            return
        img = self.window.screenshot(region)
        if img is None:
            return
        # 缓存
        try:
            sid = hash(img.tobytes())
        except Exception:
            sid = None
        if sid != self._last_screenshot_id:
            self._last_screenshot_id = sid
            self._last_pil = img.copy()
        self._rerender()

    def _rerender(self) -> None:
        if self._last_pil is None:
            return
        arr = np.asarray(self._last_pil).copy()
        # 叠加 OCR 分类框
        cache = self.ocr_cache.get(self.region_key) or []
        if cache:
            try:
                bt = int(self.black_th.get())
                gt = int(self.gray_th.get())
            except Exception:
                bt, gt = 0, 255
            for item in cache:
                pos = item.get("position") or [0, 0, 0, 0]
                x1, y1, x2, y2 = map(int, pos)
                info = classify(arr, pos, bt, gt)
                color = (
                    (0, 200, 0) if info["is_black"]
                    else (0, 0, 200) if info["is_gray"]
                    else (200, 200, 200)
                )
                cv2.rectangle(arr, (x1, y1), (x2, y2), color, 2)
        pil = Image.fromarray(arr)
        pil.thumbnail(self._max_size)
        try:
            self._photo = ImageTk.PhotoImage(pil)
            self.configure(image=self._photo, text="", background="#222")
        except Exception:
            pass


# ============================================================ 主类
class CalibrateTab(ttk.Frame):
    def __init__(self, master: tk.Misc, cfg: BotConfig, on_save: Optional[Callable[[], None]] = None):
        super().__init__(master, padding=8)
        self.cfg = cfg
        self.on_save = on_save
        self.window = WindowManager(cfg.window_title)
        # 区域变量
        self._region_vars: Dict[str, List[tk.IntVar]] = {}
        for key in ["contacts_region", "username_region", "chat_region"]:
            self._region_vars[key] = [tk.IntVar(value=v) for v in getattr(cfg, key)]
        # 输入框
        self._input_x = tk.IntVar(value=cfg.message_input_position[0])
        self._input_y = tk.IntVar(value=cfg.message_input_position[1])
        # 阈值
        self._black_th = tk.IntVar(value=cfg.black_text_threshold)
        self._gray_th = tk.IntVar(value=cfg.gray_text_threshold)
        # OCR 缓存 (跨 region_key 共享)
        self._ocr_cache: Dict[str, List[Dict]] = {}
        # 撤销栈: 每次 _save 之前 push 一份
        self._undo_stack: List[Dict[str, Any]] = []
        # 引入 OCR 引擎（懒加载失败也不影响主功能）
        self.ocr = None
        try:
            from wechat_bot.ocr_engine import OcrEngine

            self.ocr = OcrEngine(cache_timeout=cfg.cache_timeout)
        except Exception as e:
            log.warning(f"OCR 引擎未就绪: {e}")
        # 小预览 widgets
        self._small_previews: Dict[str, RegionPreview] = {}
        # 滚动容器
        self._sf = ScrolledFrame(self)
        self._sf.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.body = self._sf.interior
        self._build()

    # --------------------------------------------------------------- build
    def _build(self) -> None:
        body = self.body
        # ===== 顶部工具栏 =====
        toolbar = ttk.Frame(body)
        toolbar.pack(side=tk.TOP, fill=tk.X, pady=(0, 4))

        drag_frame = ttk.LabelFrame(toolbar, text="🖱 拖选/取点", padding=4)
        drag_frame.pack(side=tk.LEFT, padx=(0, 8))
        for key in ["contacts_region", "username_region", "chat_region"]:
            ttk.Button(
                drag_frame, text=f"📦 {REGION_LABELS[key]}",
                command=lambda k=key: self._drag_select(k),
            ).pack(side=tk.LEFT, padx=2)
        ttk.Separator(drag_frame, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=6)
        ttk.Button(
            drag_frame, text="🖱 点击取输入框",
            command=self._pick_input,
        ).pack(side=tk.LEFT, padx=2)

        op_frame = ttk.Frame(toolbar)
        op_frame.pack(side=tk.LEFT)
        ttk.Button(op_frame, text="🎯 激活微信", command=self._activate).pack(side=tk.LEFT, padx=2)
        ttk.Button(op_frame, text="💾 保存", command=self._save).pack(side=tk.LEFT, padx=2)
        ttk.Button(op_frame, text="↶ 撤销", command=self._undo).pack(side=tk.LEFT, padx=2)

        # ===== 主体: 左编辑 + 右预览 =====
        body2 = ttk.Frame(body)
        body2.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        # 左: 区域 & 阈值
        left = ttk.LabelFrame(body2, text="区域 & 阈值", padding=8)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 8))

        for i, key in enumerate(["contacts_region", "username_region", "chat_region"]):
            self._region_row(left, key, REGION_LABELS[key], i * 3)
        self._add_hsep(left, 9)
        self._xy_input_row(left, start_row=10)
        self._add_hsep(left, 12)
        self._threshold_row(left, start_row=13)

        # 右: 全屏预览 + 小预览
        right = ttk.Frame(body2)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        full_frame = ttk.LabelFrame(right, text="🖼 全屏预览（微信窗口 + 3 区叠加）", padding=4)
        full_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, pady=(0, 4))
        self.full_preview = FullWindowPreview(
            full_frame, self.cfg, self.window, self._region_vars,
        )
        self.full_preview.pack(fill=tk.BOTH, expand=True)

        small_frame = ttk.LabelFrame(
            right, text="📐 区域小预览（按 ⓞ 跑 OCR + 分类，调阈值实时联动）", padding=4,
        )
        small_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        cols = ttk.Frame(small_frame)
        cols.pack(fill=tk.BOTH, expand=True)
        for i, key in enumerate(["contacts_region", "username_region", "chat_region"]):
            col = ttk.Frame(cols)
            col.grid(row=0, column=i, sticky="nsew", padx=2)
            cols.columnconfigure(i, weight=1)
            header = ttk.Frame(col)
            header.pack(fill=tk.X)
            ttk.Label(
                header, text=REGION_LABELS[key], font=("Microsoft YaHei", 9, "bold"),
            ).pack(side=tk.LEFT)
            ttk.Button(
                header, text="ⓞ OCR", width=8,
                command=lambda k=key: self._run_ocr(k),
            ).pack(side=tk.RIGHT)
            pv = RegionPreview(
                col, self.cfg, self.window, key,
                self._region_vars, self._black_th, self._gray_th, self._ocr_cache,
                ocr_engine=self.ocr,
            )
            pv.pack(fill=tk.BOTH, expand=True)
            self._small_previews[key] = pv

        # 监听区域变化 → 联动全屏预览的子图 (image preview 是按 timer 拉取，不用强制刷新)

    def stop(self) -> None:
        try:
            self.full_preview.stop()
        except Exception:
            pass
        for p in self._small_previews.values():
            try:
                p.stop()
            except Exception:
                pass

    # --------------------------------------------------------------- widgets
    def _region_row(self, parent, key, label, row):
        ttk.Label(parent, text=label, font=("Microsoft YaHei", 9, "bold")).grid(
            row=row, column=0, columnspan=4, sticky=tk.W, pady=(6, 0),
        )
        for i, axis in enumerate(["x", "y", "w", "h"]):
            ttk.Label(parent, text=axis).grid(row=row + 1, column=i, padx=2, pady=2)
            ttk.Spinbox(
                parent, from_=0, to=4000, width=6, textvariable=self._region_vars[key][i],
            ).grid(row=row + 1, column=i, padx=2, pady=2)

    def _add_hsep(self, parent, row):
        ttk.Separator(parent, orient=tk.HORIZONTAL).grid(
            row=row, column=0, columnspan=4, sticky="we", pady=8,
        )

    def _xy_input_row(self, parent, start_row):
        ttk.Label(parent, text="消息输入框位置", font=("Microsoft YaHei", 9, "bold")).grid(
            row=start_row, column=0, columnspan=4, sticky=tk.W, pady=(6, 0),
        )
        ttk.Label(parent, text="x").grid(row=start_row + 1, column=0, padx=2, pady=2)
        ttk.Spinbox(parent, from_=0, to=4000, width=8, textvariable=self._input_x).grid(
            row=start_row + 1, column=1, padx=2, pady=2,
        )
        ttk.Label(parent, text="y").grid(row=start_row + 1, column=2, padx=2, pady=2)
        ttk.Spinbox(parent, from_=0, to=4000, width=8, textvariable=self._input_y).grid(
            row=start_row + 1, column=3, padx=2, pady=2,
        )

    def _threshold_row(self, parent, start_row):
        ttk.Label(
            parent, text="亮度阈值（与小预览 OCR 分类联动）",
            font=("Microsoft YaHei", 9, "bold"),
        ).grid(row=start_row, column=0, columnspan=4, sticky=tk.W, pady=(6, 0))
        ttk.Label(parent, text="黑 <").grid(row=start_row + 1, column=0, padx=2, pady=2)
        ttk.Spinbox(
            parent, from_=0, to=255, width=6, textvariable=self._black_th,
            command=self._on_threshold_change,
        ).grid(row=start_row + 1, column=1, padx=2, pady=2)
        ttk.Label(parent, text="灰 >=").grid(row=start_row + 1, column=2, padx=2, pady=2)
        ttk.Spinbox(
            parent, from_=0, to=255, width=6, textvariable=self._gray_th,
            command=self._on_threshold_change,
        ).grid(row=start_row + 1, column=3, padx=2, pady=2)

    # --------------------------------------------------------------- actions
    def _drag_select(self, key: str) -> None:
        """弹全屏透明遮罩，让用户拖选一个矩形，写回对应区域。"""
        def on_done(x: int, y: int, w: int, h: int) -> None:
            for var, val in zip(self._region_vars[key], (x, y, w, h)):
                var.set(int(val))
            log.info(f"拖选 {REGION_LABELS[key]}: ({x},{y},{w},{h})")

        DragSelector(
            self.winfo_toplevel(),
            on_done=on_done,
            on_cancel=lambda: log.info("拖选已取消"),
            hint=f"🖱 拖动框选 {REGION_LABELS[key]}  |  Esc/右键 取消",
            tint="#1a3d1a",
        )

    def _pick_input(self) -> None:
        """弹全屏遮罩，让用户单击一点，写入消息输入框位置。"""
        def on_pick(x: int, y: int) -> None:
            self._input_x.set(int(x))
            self._input_y.set(int(y))
            log.info(f"点击取输入框: ({x},{y})")

        ClickPicker(
            self.winfo_toplevel(),
            on_pick=on_pick,
            on_cancel=lambda: log.info("取点已取消"),
            hint="🖱 单击鼠标取输入框位置  |  Esc/右键 取消",
        )

    def _activate(self) -> None:
        try:
            if self.window.activate():
                messagebox.showinfo("已激活", "微信窗口已置前", parent=self)
            else:
                messagebox.showwarning("失败", "未找到微信窗口", parent=self)
        except Exception as e:
            messagebox.showerror("异常", str(e), parent=self)

    def _run_ocr(self, key: str) -> None:
        """对当前 key 区域跑一次 OCR，缓存结果到 self._ocr_cache。"""
        if not self.ocr:
            messagebox.showwarning("OCR 未就绪", "OCR 引擎未初始化", parent=self)
            return
        vars_ = self._region_vars.get(key)
        if not vars_ or len(vars_) != 4:
            return
        region = tuple(int(v.get()) for v in vars_)
        if region[2] <= 0 or region[3] <= 0:
            messagebox.showwarning("区域无效", f"{REGION_LABELS[key]} 宽/高必须 > 0", parent=self)
            return
        try:
            img = self.window.screenshot(region)
            if img is None:
                messagebox.showwarning("截图失败", "请确认微信窗口可见", parent=self)
                return
            arr = np.asarray(img)
            results = self.ocr.recognize(arr)
            self._ocr_cache[key] = results
            log.info(f"OCR {REGION_LABELS[key]}: {len(results)} 个文本框")
            # 触发该小预览重绘
            pv = self._small_previews.get(key)
            if pv:
                pv._rerender()
        except Exception as e:
            log.error(f"OCR 失败: {e}")
            messagebox.showerror("OCR 失败", str(e), parent=self)

    def _on_threshold_change(self) -> None:
        """阈值变化时把所有小预览重绘。"""
        # 同步到 cfg（不持久化）
        try:
            self.cfg.black_text_threshold = int(self._black_th.get())
            self.cfg.gray_text_threshold = int(self._gray_th.get())
        except Exception:
            pass
        for pv in self._small_previews.values():
            try:
                pv._rerender()
            except Exception:
                pass

    # --------------------------------------------------------------- save / undo
    def _snapshot(self) -> Dict[str, Any]:
        return {
            "contacts_region": tuple(int(v.get()) for v in self._region_vars["contacts_region"]),
            "username_region": tuple(int(v.get()) for v in self._region_vars["username_region"]),
            "chat_region": tuple(int(v.get()) for v in self._region_vars["chat_region"]),
            "message_input_position": (int(self._input_x.get()), int(self._input_y.get())),
            "black_text_threshold": int(self._black_th.get()),
            "gray_text_threshold": int(self._gray_th.get()),
        }

    def _restore_snapshot(self, snap: Dict[str, Any]) -> None:
        for key in ["contacts_region", "username_region", "chat_region"]:
            for var, val in zip(self._region_vars[key], snap[key]):
                var.set(int(val))
        self._input_x.set(snap["message_input_position"][0])
        self._input_y.set(snap["message_input_position"][1])
        self._black_th.set(snap["black_text_threshold"])
        self._gray_th.set(snap["gray_text_threshold"])

    def _save(self) -> None:
        try:
            self._undo_stack.append(self._snapshot())
            if len(self._undo_stack) > 20:
                self._undo_stack.pop(0)
            for key in ["contacts_region", "username_region", "chat_region"]:
                self.cfg.update_region(
                    key, tuple(int(v.get()) for v in self._region_vars[key])
                )
            self.cfg.update_input_position(int(self._input_x.get()), int(self._input_y.get()))
            self.cfg.black_text_threshold = int(self._black_th.get())
            self.cfg.gray_text_threshold = int(self._gray_th.get())
            self.cfg.save()
            if self.on_save:
                self.on_save()
            log.info("已保存")
        except Exception as e:
            log.error(f"保存失败: {e}")
            messagebox.showerror("保存失败", str(e), parent=self)

    def _undo(self) -> None:
        if not self._undo_stack:
            messagebox.showinfo("无撤销", "没有可撤销的修改", parent=self)
            return
        snap = self._undo_stack.pop()
        self._restore_snapshot(snap)
        log.info("已撤销一次保存")

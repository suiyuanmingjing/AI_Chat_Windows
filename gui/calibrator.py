"""Tkinter based visual calibrator.

Run via:
    python -m gui.calibrator
or:
    python daemon.py --calibrate-gui

功能:
    - 实时显示当前 contacts_region / username_region / chat_region 截图
    - 拖动 4 个 Spinbox 直接改 (x, y, w, h)
    - 点"校准输入框": 5 秒后取鼠标坐标写入 message_input_position
    - 点"测试 OCR": 把当前 contacts 区域丢给 CnOCR, 把识别结果打印到下方文本框
    - 任何修改立刻保存到 wechat_config.json
"""
from __future__ import annotations

import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Tuple

import numpy as np
from PIL import Image, ImageTk

from wechat_bot.config import BotConfig
from wechat_bot.logger import get_logger
from wechat_bot.ocr_engine import OcrEngine
from wechat_bot.storage import Storage
from wechat_bot.window import WindowManager

log = get_logger("gui.calibrator")

Region = Tuple[int, int, int, int]


class CalibratorApp:
    def __init__(self, root: tk.Tk, cfg: BotConfig):
        self.root = root
        self.cfg = cfg
        self.window = WindowManager(cfg.window_title)
        self.storage = Storage(cfg.data_dir)
        try:
            self.ocr = OcrEngine(cache_timeout=cfg.cache_timeout)
        except Exception as e:
            log.error(f"CnOCR 初始化失败: {e}")
            self.ocr = None  # type: ignore[assignment]

        root.title("自动回复系统 - 校准器")
        root.geometry("980x720")
        self._build_ui()
        self._refresh_preview()

    # ----------------------------------------------------------------- UI
    def _build_ui(self):
        pad = {"padx": 4, "pady": 2}
        frm_left = ttk.Frame(self.root)
        frm_left.pack(side=tk.LEFT, fill=tk.Y, padx=8, pady=8)
        frm_right = ttk.Frame(self.root)
        frm_right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=8, pady=8)

        # 区域编辑
        self.region_vars = {}
        row = 0
        ttk.Label(frm_left, text="区域 (x, y, w, h)", font=("", 10, "bold")).grid(
            row=row, column=0, columnspan=5, sticky="w", **pad
        )
        row += 1
        for name in ("contacts_region", "username_region", "chat_region"):
            ttk.Label(frm_left, text=name).grid(row=row, column=0, sticky="w", **pad)
            self.region_vars[name] = [tk.IntVar(value=v) for v in getattr(self.cfg, name)]
            for col in range(4):
                ttk.Spinbox(
                    frm_left,
                    from_=0,
                    to=4000,
                    width=6,
                    textvariable=self.region_vars[name][col],
                ).grid(row=row, column=1 + col, **pad)
            ttk.Button(
                frm_left, text="预览", command=lambda n=name: self._refresh_one(n)
            ).grid(row=row, column=5, **pad)
            row += 1

        ttk.Separator(frm_left).grid(row=row, column=0, columnspan=6, sticky="we", pady=6)
        row += 1

        # 输入框
        ttk.Label(frm_left, text="message_input_position (x, y)").grid(
            row=row, column=0, columnspan=5, sticky="w", **pad
        )
        row += 1
        self.input_x = tk.IntVar(value=self.cfg.message_input_position[0])
        self.input_y = tk.IntVar(value=self.cfg.message_input_position[1])
        ttk.Spinbox(frm_left, from_=0, to=4000, width=8, textvariable=self.input_x).grid(
            row=row, column=0, **pad
        )
        ttk.Spinbox(frm_left, from_=0, to=4000, width=8, textvariable=self.input_y).grid(
            row=row, column=1, **pad
        )
        ttk.Button(frm_left, text="校准输入框(5s)", command=self._calibrate_input).grid(
            row=row, column=2, columnspan=3, **pad
        )
        row += 1

        # 阈值
        ttk.Label(frm_left, text="亮度阈值").grid(row=row, column=0, sticky="w", **pad)
        row += 1
        ttk.Label(frm_left, text="black <").grid(row=row, column=0, **pad)
        self.black_th = tk.IntVar(value=self.cfg.black_text_threshold)
        ttk.Spinbox(frm_left, from_=0, to=255, width=6, textvariable=self.black_th).grid(
            row=row, column=1, **pad
        )
        ttk.Label(frm_left, text="gray >=").grid(row=row, column=2, **pad)
        self.gray_th = tk.IntVar(value=self.cfg.gray_text_threshold)
        ttk.Spinbox(frm_left, from_=0, to=255, width=6, textvariable=self.gray_th).grid(
            row=row, column=3, **pad
        )
        row += 1

        ttk.Separator(frm_left).grid(row=row, column=0, columnspan=6, sticky="we", pady=6)
        row += 1

        # 按钮
        ttk.Button(frm_left, text="保存所有修改", command=self._save_all).grid(
            row=row, column=0, columnspan=6, sticky="we", **pad
        )
        row += 1
        ttk.Button(frm_left, text="重置默认", command=self._reset_default).grid(
            row=row, column=0, columnspan=6, sticky="we", **pad
        )
        row += 1
        ttk.Button(frm_left, text="测试 OCR (contacts)", command=self._test_ocr).grid(
            row=row, column=0, columnspan=6, sticky="we", **pad
        )
        row += 1
        ttk.Button(frm_left, text="激活微信窗口", command=self._activate_wechat).grid(
            row=row, column=0, columnspan=6, sticky="we", **pad
        )
        row += 1
        ttk.Button(frm_left, text="刷新预览", command=self._refresh_preview).grid(
            row=row, column=0, columnspan=6, sticky="we", **pad
        )

        # 右侧: 预览 + 日志
        self.preview_label = ttk.Label(frm_right, text="(预览区域)")
        self.preview_label.pack(fill=tk.BOTH, expand=True)
        self.log_text = tk.Text(frm_right, height=12, font=("Consolas", 9))
        self.log_text.pack(fill=tk.X, pady=(8, 0))

    # ---------------------------------------------------------------- actions
    def _activate_wechat(self):
        try:
            ok = self.window.activate()
            if ok:
                self._log("已激活微信窗口")
            else:
                self._log("激活失败, 请检查微信是否打开")
        except Exception as e:
            self._log(f"激活异常: {e}")

    def _calibrate_input(self):
        self._log("请在 5 秒内把鼠标移到微信输入框...")
        self.root.after(0, self._do_calibrate_input)

    def _do_calibrate_input(self):
        time.sleep(5)
        try:
            import pyautogui

            x, y = pyautogui.position()
        except Exception as e:
            self._log(f"读取鼠标失败: {e}")
            return
        self.input_x.set(x)
        self.input_y.set(y)
        self.cfg.update_input_position(x, y)
        self._log(f"输入框坐标: ({x}, {y})")

    def _save_all(self):
        try:
            for name, vars_ in self.region_vars.items():
                self.cfg.update_region(name, tuple(v.get() for v in vars_))
            self.cfg.update_input_position(self.input_x.get(), self.input_y.get())
            self.cfg.black_text_threshold = int(self.black_th.get())
            self.cfg.gray_text_threshold = int(self.gray_th.get())
            self.cfg.save()
            self._log("已保存")
        except Exception as e:
            messagebox.showerror("保存失败", str(e))

    def _reset_default(self):
        if messagebox.askyesno("确认", "重置所有配置为默认值?"):
            self.cfg.reset()
            for name in self.region_vars:
                vals = getattr(self.cfg, name)
                for i, v in enumerate(vals):
                    self.region_vars[name][i].set(v)
            self.input_x.set(self.cfg.message_input_position[0])
            self.input_y.set(self.cfg.message_input_position[1])
            self.black_th.set(self.cfg.black_text_threshold)
            self.gray_th.set(self.cfg.gray_text_threshold)
            self._log("已重置")

    def _test_ocr(self):
        if self.ocr is None:
            messagebox.showerror("OCR", "CnOCR 未初始化成功")
            return
        region = tuple(v.get() for v in self.region_vars["contacts_region"])
        img = self.window.screenshot(region=region)
        if img is None:
            self._log("截图失败")
            return
        try:
            results = self.ocr.recognize(np.asarray(img))
            self._log(f"识别到 {len(results)} 个文本区域:")
            for i, it in enumerate(results, 1):
                self._log(
                    f"  {i}. '{(it.get('text') or '').strip()}' "
                    f"score={it.get('score', 0):.2f}"
                )
        except Exception as e:
            self._log(f"OCR 失败: {e}")

    def _refresh_one(self, name: str):
        region = tuple(v.get() for v in self.region_vars[name])
        img = self.window.screenshot(region=region)
        if img is None:
            return
        self._show_image(img, f"{name} {region}")

    def _refresh_preview(self):
        region = tuple(v.get() for v in self.region_vars["contacts_region"])
        img = self.window.screenshot(region=region)
        if img is None:
            self.preview_label.configure(text="(无法截图: 微信可能未打开)")
            return
        self._show_image(img, f"contacts_region {region}")

    def _show_image(self, img: Image.Image, title: str):
        # 缩放到 label 大小
        max_w, max_h = 720, 560
        img2 = img.copy()
        img2.thumbnail((max_w, max_h))
        tk_img = ImageTk.PhotoImage(img2)
        self.preview_label.configure(image=tk_img, text=title, compound=tk.TOP)
        self.preview_label.image = tk_img  # 防止被回收

    def _log(self, msg: str):
        log.info(msg)
        self.log_text.insert(tk.END, msg + "\n")
        self.log_text.see(tk.END)


def main():
    cfg = BotConfig.load()
    root = tk.Tk()
    CalibratorApp(root, cfg)
    root.mainloop()


if __name__ == "__main__":
    main()

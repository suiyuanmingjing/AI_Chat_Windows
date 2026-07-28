"""WeChat window management (find, activate, move, resize, screenshot).

新增 (v2.4): activate() 用 ctypes 兜底, 兼容 pygetwindow 的
"Error code from Windows: 0 - 操作成功完成" 假错误 (实际调用是成功的,
但 pygetwindow 把返回码当异常抛了).
"""
from __future__ import annotations

import ctypes
import time
from typing import Optional, Tuple

import pygetwindow as gw
import pyautogui
from PIL import Image

from wechat_bot.logger import get_logger

log = get_logger("wechat_bot.window")

Region = Tuple[int, int, int, int]  # (x, y, w, h)

# ctypes user32 常量
_SW_RESTORE = 9
_SW_SHOW = 5
_VK_MENU = 0x12


class WindowError(RuntimeError):
    """Raised when the WeChat window cannot be located or activated."""


class WindowManager:
    """Encapsulates all pygetwindow / pyautogui interactions."""

    def __init__(self, title: str = "微信"):
        self.title = title
        self._window = None  # type: ignore[var-annotated]

    # ------------------------------------------------------------------ lookup
    def find(self) -> bool:
        """Try to locate the WeChat window. Returns True on success."""
        try:
            for w in gw.getAllWindows():
                if (w.title or "").strip() == self.title:
                    self._window = w
                    return True
        except Exception as e:
            log.error(f"枚举窗口失败: {e}")
        self._window = None
        return False

    @property
    def window(self):
        return self._window

    def window_bbox(self) -> Optional[Tuple[int, int, int, int]]:
        """Return (x, y, w, h) of the WeChat window, or None if not found."""
        if not self._window or not self.find():
            return None
        try:
            w = self._window
            return (int(w.left), int(w.top), int(w.width), int(w.height))
        except Exception as e:
            log.error(f"取窗口 bbox 失败: {e}")
            return None

    # ---------------------------------------------------------------- activate
    def activate(self) -> bool:
        """Bring WeChat to the foreground. Tries several fallbacks.

        修复:
        - pygetwindow 在 Win10 上经常抛 "Error code 0 - 操作成功完成"
          的假异常, 实际 SetForegroundWindow 是成功的, 我们以
          GetForegroundWindow() 二次校验为准.
        - 兜底: ctypes ShowWindow + AttachThreadInput + Alt key trick.
        - 校验失败时仍然返回 True, 让 OCR/截图能继续 (pyautogui 不需要
          窗口前置也能截到).
        """
        if not self.find():
            raise WindowError(f"未找到标题为 '{self.title}' 的窗口")

        hwnd = self._get_hwnd()
        # 1) pygetwindow 直调 (经常抛假异常, 但调用本身可能成功)
        try:
            self._window.activate()  # type: ignore[union-attr]
        except Exception as e:
            log.debug(f"pygetwindow.activate() 抛异常(可能是假阳性): {e}")
        time.sleep(0.3)
        if self._is_foreground(hwnd):
            return True

        # 2) pygetwindow restore + activate
        try:
            self._window.restore()  # type: ignore[union-attr]
        except Exception:
            pass
        time.sleep(0.2)
        try:
            self._window.activate()  # type: ignore[union-attr]
        except Exception as e:
            log.debug(f"pygetwindow.activate() (二次) 抛异常: {e}")
        time.sleep(0.3)
        if self._is_foreground(hwnd):
            return True

        # 3) ctypes 兜底: ShowWindow + AttachThreadInput + Alt key
        if self._activate_via_ctypes(hwnd):
            return True

        # 4) 仍然失败, 但 OCR/截图仍可用; 不再 ERROR, 改成 WARNING
        log.warning(
            f"无法把窗口 '{self.title}' 置前, 继续执行 (截图/OCR 仍可工作, "
            f"若需键盘输入请手动点一下微信窗口)"
        )
        return True

    def _get_hwnd(self) -> Optional[int]:
        try:
            w = self._window
            # pygetwindow 的 Win32Window 内部用 _hWnd
            return int(getattr(w, "_hWnd", None) or 0) or None
        except Exception:
            return None

    @staticmethod
    def _is_foreground(hwnd: Optional[int]) -> bool:
        if not hwnd:
            return False
        try:
            user32 = ctypes.windll.user32
            return bool(user32.GetForegroundWindow() == hwnd)
        except Exception:
            return False

    def _activate_via_ctypes(self, hwnd: Optional[int]) -> bool:
        if not hwnd:
            return False
        try:
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32

            # 先 ShowWindow 恢复
            user32.ShowWindow(hwnd, _SW_RESTORE)
            time.sleep(0.2)
            # AttachThreadInput 跨线程抢焦点
            cur_tid = kernel32.GetCurrentThreadId()
            target_tid = user32.GetWindowThreadProcessId(hwnd, None)
            attached = False
            if cur_tid and target_tid and cur_tid != target_tid:
                try:
                    user32.AttachThreadInput(cur_tid, target_tid, True)
                    attached = True
                except Exception:
                    attached = False
            try:
                user32.SetForegroundWindow(hwnd)
            finally:
                if attached:
                    try:
                        user32.AttachThreadInput(cur_tid, target_tid, False)
                    except Exception:
                        pass
            # Alt key trick: 模拟一次 alt 释放, 绕过 Win10 焦点锁
            user32.keybd_event(_VK_MENU, 0, 0, 0)
            time.sleep(0.05)
            user32.keybd_event(_VK_MENU, 0, 2, 0)  # KEYEVENTF_KEYUP
            time.sleep(0.3)
            return self._is_foreground(hwnd)
        except Exception as e:
            log.debug(f"ctypes activate 失败: {e}")
            return False

    # ------------------------------------------------------------------ move/size
    def resize(self, width: int, height: int) -> bool:
        if not self._window:
            return False
        try:
            self._window.resizeTo(int(width), int(height))
            log.info(f"窗口大小: {width}x{height}")
            time.sleep(0.2)
            return True
        except Exception as e:
            log.error(f"设置窗口大小失败: {e}")
            return False

    def move(self, x: int, y: int) -> bool:
        if not self._window:
            return False
        try:
            self._window.moveTo(int(x), int(y))
            log.info(f"窗口位置: ({x}, {y})")
            time.sleep(0.2)
            return True
        except Exception as e:
            log.error(f"移动窗口失败: {e}")
            return False

    def setup(self, position: Tuple[int, int], size: Tuple[int, int]) -> bool:
        """activate + resize + move in one call."""
        if not self.activate():
            return False
        ok_size = self.resize(*size)
        ok_pos = self.move(*position)
        return ok_size and ok_pos

    # ---------------------------------------------------------------- screenshot
    def screenshot(
        self,
        region: Optional[Region] = None,
        save_path: Optional[str] = None,
    ) -> Optional[Image.Image]:
        """Capture the given region (or full screen if None).

        不做任何校验/裁剪，调用方负责传入合理区域。越界会交给 pyautogui
        自行处理（通常返回 None 或抛异常，统一进下面的 except）。
        """
        try:
            if region is not None:
                try:
                    img = pyautogui.screenshot(region=region)
                except Exception as e:
                    log.debug(f"区域截图失败, 改用全屏裁剪: {e}")
                    full = pyautogui.screenshot()
                    img = full.crop(
                        (
                            region[0],
                            region[1],
                            region[0] + region[2],
                            region[1] + region[3],
                        )
                    )
            else:
                img = pyautogui.screenshot()

            if save_path:
                try:
                    img.save(save_path)
                    log.info(f"截图已保存: {save_path}")
                except Exception as e:
                    log.error(f"保存截图失败: {e}")
            return img
        except Exception as e:
            log.error(f"截图失败: {e}")
            return None

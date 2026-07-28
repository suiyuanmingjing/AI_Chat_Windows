"""Main GUI application: 7-tab controller for the WeChat bot daemon.

新增 (v2.4):
- GUI 自身可启动/停止守护进程 (subprocess.Popen, sys.executable, 隐藏窗口)
- 启动/停止通过回调注入到 DashboardTab
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Dict, Optional

from gui.blacklist_tab import BlacklistTab
from gui.calibrate_tab import CalibrateTab
from gui.config_tab import ConfigTab
from gui.dashboard_tab import DashboardTab
from gui.debug_tab import DebugTab
from gui.history_tab import HistoryTab
from gui.knowledge_tab import KnowledgeTab
from gui.log_tab import LogTab
from gui.widgets import StatusBar
from wechat_bot.config import BotConfig
from wechat_bot.ipc import GUIController, IPCPaths
from wechat_bot.logger import get_logger
from wechat_bot.ocr_engine import OcrEngine
from wechat_bot.storage import Storage
from wechat_bot.whitelist import ContactFilter

log = get_logger("gui.app")


class App:
    def __init__(self, root: tk.Tk, cfg: BotConfig):
        self.root = root
        self.cfg = cfg
        # 后台组件（仅 OCR / Window / Storage，daemon 进程在外部或子进程）
        self.storage = Storage(cfg.data_dir)
        self.ocr = OcrEngine(cache_timeout=cfg.cache_timeout)
        self.window_manager = None  # debug_tab 自己创建
        self.paths = IPCPaths(cfg)
        self.controller = GUIController(self.paths, cfg)
        self.contact_filter = ContactFilter(self.paths, cfg)
        # GUI 自己启动的守护进程
        self.daemon_proc: Optional[subprocess.Popen] = None

        self._build()

    def _build(self) -> None:
        self.root.title("自动回复系统 - 控制器")
        self.root.geometry("1080x720")
        try:
            ttk.Style().theme_use("clam")
        except Exception:
            pass

        nb = ttk.Notebook(self.root)
        nb.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        # 各 Tab (Dashboard 需要 start/stop 守护进程的回调)
        self.tab_dashboard = DashboardTab(
            nb, self.cfg, self.storage, self.controller,
            on_start_daemon=self.start_daemon,
            on_stop_daemon=self.stop_daemon,
        )
        nb.add(self.tab_dashboard, text="🏠 仪表盘")

        self.tab_config = ConfigTab(nb, self.cfg, on_save=self._on_config_saved)
        nb.add(self.tab_config, text="⚙ 配置")

        self.tab_calibrate = CalibrateTab(nb, self.cfg, on_save=self._on_config_saved)
        nb.add(self.tab_calibrate, text="🎯 校准")

        # Debug tab 需要 window_manager
        from wechat_bot.window import WindowManager

        self.tab_debug = DebugTab(nb, self.cfg, self.ocr, WindowManager(self.cfg.window_title))
        nb.add(self.tab_debug, text="🔍 调试")

        self.tab_log = LogTab(nb, self.cfg)
        nb.add(self.tab_log, text="📜 日志")

        self.tab_history = HistoryTab(nb, self.cfg, self.storage)
        nb.add(self.tab_history, text="📚 历史")

        self.tab_blacklist = BlacklistTab(nb, self.cfg, self.contact_filter)
        nb.add(self.tab_blacklist, text="🚫 白黑名单")

        self.tab_kb = KnowledgeTab(nb, self.cfg, on_save=self._on_kb_saved)
        nb.add(self.tab_kb, text="📖 知识库")

        # 状态条
        self.status = StatusBar(self.root)
        self.status.pack(side=tk.BOTTOM, fill=tk.X)
        self._schedule_status()

    # ============================================================ 守护启停
    def start_daemon(self) -> bool:
        """后台启动守护进程 (隐藏窗口).

        Returns: True=已启动 / 已经在跑, False=启动失败
        """
        if self.daemon_proc and self.daemon_proc.poll() is None:
            log.info("守护进程已在运行 (PID=%d)", self.daemon_proc.pid)
            return True
        log_file = os.path.join(self.cfg.data_dir, "logs", "daemon_stdout.log")
        try:
            os.makedirs(os.path.dirname(log_file), exist_ok=True)
        except Exception:
            pass
        # 拿 daemon 入口路径 (与 gui.py 平级)
        daemon_py = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "daemon.py",
        )
        if not os.path.exists(daemon_py):
            log.error(f"找不到 daemon.py: {daemon_py}")
            return False
        try:
            flags = 0
            if sys.platform == "win32":
                # 隐藏黑窗
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            self.daemon_proc = subprocess.Popen(
                [sys.executable, daemon_py, "--daemon"],
                stdout=open(log_file, "ab", buffering=0),
                stderr=subprocess.STDOUT,
                cwd=os.path.dirname(daemon_py),
                creationflags=flags,
            )
            log.info(
                f"已启动守护进程 PID={self.daemon_proc.pid}, "
                f"日志: {log_file}"
            )
            return True
        except Exception as e:
            log.error(f"启动守护失败: {e}")
            return False

    def stop_daemon(self) -> bool:
        """停止守护进程 (三段式: 优雅指令 -> 等待 -> 强杀 兜底).

        Returns: True=已停止, False=找不到进程或停止失败
        """
        # 1) 解析目标 PID (优先 GUI 启动的 Popen, 兜底读 status.json)
        target_pid: Optional[int] = None
        if self.daemon_proc:
            if self.daemon_proc.poll() is None:
                target_pid = self.daemon_proc.pid
            self.daemon_proc = None
        if target_pid is None:
            st = self.controller.read_status()
            if st and st.get("pid"):
                try:
                    target_pid = int(st["pid"])
                except Exception:
                    target_pid = None
        if target_pid is None:
            log.info("stop_daemon: 没找到守护 pid (可能未启动)")
            return False
        log.info(f"stop_daemon: 目标 PID={target_pid}")

        # 2) 尝试优雅退出: 写 stop 指令到 control.json, 守护会在下个 tick
        #    收到后 self._stop=True, 走完 finally 清理 status
        try:
            self.controller.stop_daemon()
            log.info("stop_daemon: 已发送 stop 指令, 等待守护收尾...")
        except Exception as e:
            log.warning(f"发送 stop 指令失败, 将直接强杀: {e}")

        # 3) 等待守护优雅退出 (最多 3s)
        if self._wait_pid_gone(target_pid, timeout=3.0):
            log.info(f"stop_daemon: 守护已优雅退出 (PID={target_pid})")
            return True

        # 4) 兜底: taskkill /F /T (隐藏黑窗)
        log.info(f"stop_daemon: 守护未在 3s 内退出, 启动 taskkill 强杀")
        flags = 0
        if sys.platform == "win32":
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        try:
            rc = subprocess.call(
                ["taskkill", "/F", "/T", "/PID", str(target_pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=flags,
            )
            log.info(f"taskkill /F /T /PID {target_pid} rc={rc}")
        except Exception as e:
            log.error(f"taskkill 调用失败: {e}")
            return False

        # 5) 验证 PID 真的死了
        if self._wait_pid_gone(target_pid, timeout=2.0):
            log.info(f"stop_daemon: 已强杀 (PID={target_pid})")
            return True
        log.error(f"stop_daemon: PID={target_pid} 仍然存活, 停止失败")
        return False

    @staticmethod
    def _wait_pid_gone(pid: int, timeout: float = 2.0) -> bool:
        """轮询直到 PID 消失或超时. 优先用 psutil, 兜底 os.kill(pid, 0)."""
        import time
        try:
            import psutil  # type: ignore
            deadline = time.time() + timeout
            while time.time() < deadline:
                if not psutil.pid_exists(pid):
                    return True
                time.sleep(0.15)
            return False
        except ImportError:
            pass
        # 兜底: os.kill(pid, 0) 探测 (不实际发信号)
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                os.kill(pid, 0)
            except (OSError, ProcessLookupError, PermissionError):
                # ProcessLookupError = 已死; PermissionError = 活着但无权限
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    return True
                # PermissionError 视作仍存活
                time.sleep(0.15)
                continue
            time.sleep(0.15)
        return False

    def _schedule_status(self) -> None:
        try:
            st = self.controller.read_status()
            self.status.update_status(st if self.controller.is_daemon_alive() else None)
        except Exception:
            pass
        self.root.after(1500, self._schedule_status)

    def _on_config_saved(self) -> None:
        # 让 blacklist_tab 等能刷新
        try:
            self.contact_filter.reload()
        except Exception:
            pass

    def _on_kb_saved(self) -> None:
        # 知识库变更后通知守护进程清掉 AI 缓存 + 重载
        try:
            self.controller.send_command("reload_kb")
        except Exception as e:
            log.debug(f"通知守护 reload_kb 失败: {e}")

    def run(self) -> None:
        try:
            self.root.mainloop()
        finally:
            for t in (self.tab_dashboard, self.tab_log):
                if hasattr(t, "stop"):
                    try:
                        t.stop()
                    except Exception:
                        pass


def main() -> int:
    cfg = BotConfig.load()
    root = tk.Tk()
    app = App(root, cfg)
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())

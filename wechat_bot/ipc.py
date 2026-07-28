"""File-based IPC between the GUI controller and the daemon process.

约定:
- data/runtime/ 目录
- daemon_status.json: 守护进程写，GUI 读
- control.json: GUI 写，守护读后清空（防重复消费）
- pause.flag: 文件存在 = 暂停；不存在 = 运行（最轻量的暂停信号）
- whitelist.txt / blacklist.txt: 每行一个联系人名
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from typing import Any, Dict, Optional

from wechat_bot.config import BotConfig
from wechat_bot.logger import get_logger

log = get_logger("wechat_bot.ipc")


class IPCPaths:
    """Resolve all IPC file paths under data_dir/runtime/."""

    def __init__(self, cfg: BotConfig):
        self.runtime_dir = os.path.join(cfg.data_dir, cfg.ipc.runtime_dir)
        os.makedirs(self.runtime_dir, exist_ok=True)
        self.status_path = os.path.join(self.runtime_dir, cfg.ipc.status_file)
        self.control_path = os.path.join(self.runtime_dir, cfg.ipc.control_file)
        self.pause_flag = os.path.join(self.runtime_dir, cfg.ipc.pause_flag)
        self.whitelist_path = os.path.join(self.runtime_dir, cfg.ipc.whitelist_file)
        self.blacklist_path = os.path.join(self.runtime_dir, cfg.ipc.blacklist_file)


# ---------------------------------------------------------------------------
# Daemon side
# ---------------------------------------------------------------------------
class DaemonReporter:
    """Used by the daemon: write status, read & consume control commands,
    honor pause flag."""

    def __init__(self, paths: IPCPaths, cfg: BotConfig):
        self.paths = paths
        self.cfg = cfg
        self._tick_count = 0
        self._messages_handled = 0
        self._errors_count = 0
        self._started_at = datetime.now().isoformat()
        self._current_activity = "starting"

    # ---- status ----
    def report(
        self,
        state: str,
        activity: str = "",
        handled_delta: int = 0,
        error_delta: int = 0,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._tick_count += 1
        if handled_delta:
            self._messages_handled += handled_delta
        if error_delta:
            self._errors_count += error_delta
        if activity:
            self._current_activity = activity
        payload: Dict[str, Any] = {
            "pid": os.getpid(),
            "started_at": self._started_at,
            "last_update": datetime.now().isoformat(),
            "state": state,
            "tick_count": self._tick_count,
            "messages_handled": self._messages_handled,
            "errors_count": self._errors_count,
            "current_activity": self._current_activity,
        }
        if extra:
            payload.update(extra)
        try:
            tmp = self.paths.status_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.paths.status_path)
        except Exception as e:
            log.debug(f"写 status 失败: {e}")

    # ---- control ----
    def consume_command(self) -> Optional[Dict[str, Any]]:
        """Read & delete control.json. Returns the command dict or None."""
        if not os.path.exists(self.paths.control_path):
            return None
        try:
            with open(self.paths.control_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            os.remove(self.paths.control_path)
            return data
        except Exception as e:
            log.warning(f"读 control 失败: {e}")
            return None

    def is_paused(self) -> bool:
        return os.path.exists(self.paths.pause_flag)

    def cleanup(self) -> None:
        """Daemon 退出时清理 status 文件."""
        try:
            if os.path.exists(self.paths.status_path):
                os.remove(self.paths.status_path)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# GUI side
# ---------------------------------------------------------------------------
class GUIController:
    """Used by the GUI: read status, send commands, toggle pause."""

    def __init__(self, paths: IPCPaths, cfg: BotConfig):
        self.paths = paths
        self.cfg = cfg

    def read_status(self) -> Optional[Dict[str, Any]]:
        if not os.path.exists(self.paths.status_path):
            return None
        try:
            with open(self.paths.status_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log.debug(f"读 status 失败: {e}")
            return None

    def is_daemon_alive(self) -> bool:
        st = self.read_status()
        if not st:
            return False
        try:
            ts = datetime.fromisoformat(st.get("last_update", ""))
        except Exception:
            return False
        age = (datetime.now() - ts).total_seconds()
        return age < self.cfg.ipc.status_ttl

    def _write_control(self, payload: Dict[str, Any]) -> None:
        tmp = self.paths.control_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.paths.control_path)

    def send_command(self, command: str, **kwargs: Any) -> None:
        self._write_control(
            {"command": command, "ts": datetime.now().isoformat(), **kwargs}
        )

    def pause(self) -> None:
        open(self.paths.pause_flag, "w").close()

    def resume(self) -> None:
        try:
            os.remove(self.paths.pause_flag)
        except FileNotFoundError:
            pass

    def is_paused(self) -> bool:
        return os.path.exists(self.paths.pause_flag)

    def trigger_reply(self, username: str) -> None:
        self.send_command("trigger_reply", target_user=username)

    def skip_contact(self, username: str) -> None:
        self.send_command("skip_contact", target_user=username)

    def force_reply_now(self) -> None:
        """Force a one-off full pass right now."""
        self.send_command("run_once")

    def stop_daemon(self) -> None:
        """通过 control.json 通知守护进程走优雅退出."""
        self.send_command("stop")

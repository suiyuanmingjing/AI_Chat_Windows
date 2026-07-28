"""Long-running supervisor with auto-recovery + IPC support.

新增（相对旧版）:
- 通过 DaemonReporter 把状态写到 data/runtime/daemon_status.json
- 每个 tick 检测 pause.flag；存在则跳过本轮处理
- 读 control.json 消费指令：
    * trigger_reply /target=张三 -> 立即处理指定用户
    * run_once -> 本轮强制多跑一次（不等 check_interval）
    * skip -> 忽略，保留接口
- 集成白黑名单
"""
from __future__ import annotations

import signal
import time
from typing import Any, Callable, Dict, List, Optional

from wechat_bot.config import BotConfig, GuardianConfig
from wechat_bot.ipc import DaemonReporter, IPCPaths
from wechat_bot.logger import get_logger
from wechat_bot.whitelist import ContactFilter
from wechat_bot.window import WindowError, WindowManager

log = get_logger("wechat_bot.guardian")


class Guardian:
    def __init__(
        self,
        cfg: BotConfig,
        window: WindowManager,
        tick: Callable[..., bool],
        contact_filter: Optional[ContactFilter] = None,
        paths: Optional[IPCPaths] = None,
    ):
        self.cfg = cfg
        self.guard_cfg: GuardianConfig = cfg.guardian
        self.window = window
        self.tick = tick
        self._stop = False
        self._consecutive_errors = 0
        self._wechat_missing_since: Optional[float] = None
        self._idle_rounds = 0
        self._paused = False
        self._stats: Dict[str, Any] = {
            "total_replies": 0,
            "total_errors": 0,
            "by_contact": {},  # username -> count
            "recent": [],      # 最近 N 条 [{user, ts, ok, text}]
        }

        self.paths = paths or IPCPaths(cfg)
        self.reporter = DaemonReporter(self.paths, cfg)
        self.contact_filter = contact_filter or ContactFilter(self.paths, cfg)

        # 跨平台信号
        try:
            signal.signal(signal.SIGINT, self._on_signal)
        except Exception:
            pass
        try:
            signal.signal(signal.SIGTERM, self._on_signal)
        except Exception:
            pass

    # ------------------------------------------------------------ lifecycle
    def _on_signal(self, signum, frame):  # noqa: ARG002
        log.info(f"收到信号 {signum}, 准备停止...")
        self._stop = True

    def stop(self) -> None:
        self._stop = True

    # --------------------------------------------------------------- main
    def run(self) -> None:
        log.info(
            f"守护启动: 间隔 {self.cfg.check_interval}s, "
            f"最大连续错误 {self.guard_cfg.max_consecutive_errors}"
        )
        self.reporter.report("running", "started")
        try:
            while not self._stop:
                # 0) 暂停检查
                if self.reporter.is_paused():
                    if not self._paused:
                        log.info("收到 pause 信号, 进入暂停态")
                        self._paused = True
                    self.reporter.report("paused", "waiting for resume")
                    self._sleep(1)
                    continue
                if self._paused:
                    log.info("pause 信号解除, 恢复运行")
                    self._paused = False

                # 1) 消费 GUI 控制指令
                self._drain_commands()

                # 2) 跑一轮
                ok = self._safe_tick()
                if ok is None:
                    # wechat window not present yet
                    self.reporter.report("waiting_wechat", "window not found")
                    if self._maybe_recover_wechat():
                        continue
                    self._sleep(self.guard_cfg.wechat_recheck_interval)
                    continue

                if ok:
                    self._consecutive_errors = 0
                    self._idle_rounds = 0
                else:
                    self._idle_rounds += 1
                    if (
                        self.guard_cfg.max_idle_rounds
                        and self._idle_rounds >= self.guard_cfg.max_idle_rounds
                    ):
                        log.info("达到最大空轮数, 退出")
                        break

                # 3) 正常轮询间隔
                self._sleep(self.cfg.check_interval)
        finally:
            log.info("守护已停止")
            self.reporter.cleanup()

    # --------------------------------------------------------------- commands
    def _drain_commands(self) -> None:
        """消费 control.json 中所有指令（最多 5 个/轮，避免卡死）。"""
        for _ in range(5):
            cmd = self.reporter.consume_command()
            if not cmd:
                return
            self._handle_command(cmd)

    def _handle_command(self, cmd: Dict[str, Any]) -> None:
        name = cmd.get("command")
        log.info(f"收到 GUI 指令: {name} {cmd}")
        if name == "trigger_reply":
            user = cmd.get("target_user") or ""
            self.reporter.report("running", f"trigger_reply -> {user}")
            try:
                ok = self.tick(target_user=user, force=True)
                if ok:
                    self._record_reply(user, True, "(forced)")
                else:
                    self._record_reply(user, False, "(forced)")
            except Exception as e:
                log.error(f"trigger_reply 失败: {e}")
                self._record_reply(user, False, str(e))
        elif name == "run_once":
            self.reporter.report("running", "forced run_once")
            try:
                self.tick(force=True)
            except Exception as e:
                log.error(f"forced run_once 失败: {e}")
        elif name == "skip_contact":
            user = cmd.get("target_user") or ""
            # 简单实现：加到黑名单内存，下次 reload 后生效
            log.info(f"GUI 请求跳过 {user}")
        elif name == "reload_kb":
            # 知识库 mtime 自动热加载, 这里只是 ack, 不需要特殊处理
            log.info("GUI 通知 reload_kb (下次 AI.chat 自动重读)")
        elif name == "stop":
            # GUI 通知停止: 走优雅退出 (清 status, 关 watchdog)
            log.info("GUI 请求停止守护, 进入收尾...")
            self.reporter.report("stopping", "stop requested by GUI")
            self._stop = True
        else:
            log.warning(f"未知指令: {name}")

    def _record_reply(self, user: str, ok: bool, text: str) -> None:
        self._stats["total_replies"] += 1 if ok else 0
        if not ok:
            self._stats["total_errors"] += 1
        d = self._stats["by_contact"]
        d[user] = d.get(user, 0) + 1
        self._stats["recent"].insert(
            0,
            {"user": user, "ts": time.strftime("%H:%M:%S"), "ok": ok, "text": text[:60]},
        )
        self._stats["recent"] = self._stats["recent"][:20]
        self.reporter.report(
            "running",
            activity=f"reply to {user}",
            handled_delta=1 if ok else 0,
            error_delta=0 if ok else 1,
            extra={"stats": self._stats},
        )

    # --------------------------------------------------------------- helpers
    def _safe_tick(self, **kwargs) -> Optional[bool]:
        """Run one tick. Return True/False on success, None if WeChat missing."""
        try:
            return bool(self.tick(**kwargs))
        except WindowError as e:
            log.warning(f"微信窗口异常: {e}")
            return None
        except Exception as e:
            self._consecutive_errors += 1
            log.error(f"本轮异常({self._consecutive_errors}): {e}")
            if self._consecutive_errors >= self.guard_cfg.max_consecutive_errors:
                cooldown = self.guard_cfg.cooldown_on_burst
                log.warning(f"连续错误达到上限, 冷却 {cooldown}s 后继续")
                self.reporter.report("cooldown", f"cooldown {cooldown}s")
                self._sleep(cooldown)
                self._consecutive_errors = 0
            return False

    def _maybe_recover_wechat(self) -> bool:
        now = time.time()
        if self._wechat_missing_since is None:
            self._wechat_missing_since = now
            log.warning("未找到微信窗口, 等待用户打开或重新启动微信")
            return False
        if now - self._wechat_missing_since >= self.guard_cfg.wechat_recheck_interval:
            log.info("重新尝试激活微信窗口...")
            if self.window.find() and self.window.activate():
                log.info("微信窗口已重新激活, 恢复轮询")
                self._wechat_missing_since = None
                return True
            self._wechat_missing_since = now
        return False

    def _sleep(self, seconds: int) -> None:
        for _ in range(int(seconds)):
            if self._stop:
                break
            time.sleep(1)

    def _interruptible_sleep(self, seconds: int) -> None:
        """Sleep 但每 200ms 轮询 control.json, 让 stop 指令能立即生效.

        同时检查 self._stop (信号 / 外部 stop() 调用).
        """
        deadline = time.time() + max(0.0, float(seconds))
        while time.time() < deadline and not self._stop:
            self._drain_commands()
            if self._stop:
                break
            remaining = min(0.2, deadline - time.time())
            if remaining > 0:
                time.sleep(remaining)

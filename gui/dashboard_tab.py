"""Dashboard tab: 守护状态 / 启停 / 聊天总结 / 黑名单.

新增 (v2.3):
- 聊天总结区: 今天 / 昨天 / 近 7 天 三个时间窗
    * 联系人级别表 (msg/reply/last/blacklist 标记)
    * 今日汇总指标卡
    * 小时级趋势图
- 黑名单独立一栏
- 9 PM (21:00) 整点自动重扫 (after-based)

新增 (v2.4):
- 「启动/停止守护」直接调 App 注入的回调 (subprocess.Popen, 隐藏黑窗)
- 顶部 ScrolledFrame 包装
"""
from __future__ import annotations

import os
import tkinter as tk
from datetime import datetime, timedelta
from tkinter import messagebox, ttk
from typing import Any, Callable, Dict, List, Optional

from gui.widgets import ScrolledFrame, StatusBar
from wechat_bot.config import BotConfig
from wechat_bot.ipc import GUIController
from wechat_bot.logger import get_logger
from wechat_bot.storage import Storage
from wechat_bot.whitelist import ContactFilter

log = get_logger("gui.dashboard")


# ============================================================ helpers
def _range_bounds(name: str) -> tuple:
    """返回 (start, end) 时间范围."""
    now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if name == "today":
        return today_start, now
    if name == "yesterday":
        return today_start - timedelta(days=1), today_start - timedelta(seconds=1)
    if name == "week":
        return now - timedelta(days=7), now
    return None, None


# ============================================================ 主类
class DashboardTab(ttk.Frame):
    POLL_MS = 1500
    NIGHTLY_HOUR = 21  # 21:00 自动重扫

    def __init__(
        self,
        master: tk.Misc,
        cfg: BotConfig,
        storage: Storage,
        controller: GUIController,
        on_start_daemon: Optional[Callable[[], bool]] = None,
        on_stop_daemon: Optional[Callable[[], bool]] = None,
    ):
        super().__init__(master, padding=8)
        self.cfg = cfg
        self.storage = storage
        self.controller = controller
        self.on_start_daemon = on_start_daemon
        self.on_stop_daemon = on_stop_daemon
        self.contact_filter: Optional[ContactFilter] = None
        # 滚动容器
        self._sf = ScrolledFrame(self)
        self._sf.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.body = self._sf.interior
        self._build()
        self._schedule()
        self._schedule_nightly_refresh()

    # --------------------------------------------------------------- build
    def _build(self) -> None:
        body = self.body
        # 上：状态 + 控制
        top = ttk.Frame(body)
        top.pack(side=tk.TOP, fill=tk.X)
        left = ttk.LabelFrame(top, text="守护状态", padding=8)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 4))
        right = ttk.LabelFrame(top, text="快捷操作", padding=8)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(4, 0))

        self.var_pid = tk.StringVar(value="-")
        self.var_state = tk.StringVar(value="-")
        self.var_started = tk.StringVar(value="-")
        self.var_tick = tk.StringVar(value="0")
        self.var_replies = tk.StringVar(value="0")
        self.var_errors = tk.StringVar(value="0")
        self.var_activity = tk.StringVar(value="-")
        for i, (label, var) in enumerate(
            [
                ("PID", self.var_pid),
                ("状态", self.var_state),
                ("启动时间", self.var_started),
                ("累计 tick", self.var_tick),
                ("已回复", self.var_replies),
                ("错误数", self.var_errors),
                ("当前活动", self.var_activity),
            ]
        ):
            ttk.Label(left, text=label + ":", width=10, anchor=tk.W).grid(
                row=i, column=0, sticky=tk.W, padx=2, pady=2
            )
            ttk.Label(left, textvariable=var, anchor=tk.W).grid(
                row=i, column=1, sticky=tk.W, padx=2, pady=2
            )

        ttk.Button(right, text="▶ 启动守护", command=self._start_daemon).pack(
            fill=tk.X, pady=2
        )
        ttk.Button(right, text="⏸ 暂停", command=self._pause).pack(fill=tk.X, pady=2)
        ttk.Button(right, text="⏵ 恢复", command=self._resume).pack(fill=tk.X, pady=2)
        ttk.Button(right, text="⏹ 停止守护 (关窗口)", command=self._stop_daemon).pack(
            fill=tk.X, pady=2
        )
        ttk.Separator(right, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=4)
        ttk.Button(right, text="⚡ 立即处理一轮", command=self._run_once).pack(
            fill=tk.X, pady=2
        )
        ttk.Label(right, text="指定联系人:").pack(anchor=tk.W, pady=(8, 0))
        row = ttk.Frame(right)
        row.pack(fill=tk.X)
        self.var_target = tk.StringVar()
        ttk.Entry(row, textvariable=self.var_target).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(row, text="触发回复", command=self._trigger_reply).pack(side=tk.LEFT, padx=2)

        # ===== 聊天总结 =====
        summary = ttk.LabelFrame(body, text="💬 聊天总结", padding=4)
        summary.pack(side=tk.TOP, fill=tk.BOTH, expand=True, pady=(8, 0))

        # 工具条
        bar = ttk.Frame(summary)
        bar.pack(side=tk.TOP, fill=tk.X)
        ttk.Button(bar, text="🔄 刷新", command=self._refresh_summary_now).pack(
            side=tk.LEFT, padx=2
        )
        self.var_last_scan = tk.StringVar(value="未扫描")
        ttk.Label(bar, textvariable=self.var_last_scan, foreground="#666").pack(
            side=tk.LEFT, padx=8
        )
        ttk.Label(
            bar,
            text=f"每日 {self.NIGHTLY_HOUR:02d}:00 自动重扫",
            foreground="#666",
        ).pack(side=tk.RIGHT, padx=4)

        # 时间窗 notebook
        self.nb_summary = ttk.Notebook(summary)
        self.nb_summary.pack(side=tk.TOP, fill=tk.BOTH, expand=True, pady=(4, 0))
        self._summary_pages: Dict[str, Dict[str, Any]] = {}
        for key, label in [
            ("today", "今天"),
            ("yesterday", "昨天"),
            ("week", "近 7 天"),
        ]:
            self._build_summary_page(self.nb_summary, key, label)

        # ===== 黑名单独立一栏 =====
        bl_frame = ttk.LabelFrame(body, text="🚫 黑名单联系人", padding=4)
        bl_frame.pack(side=tk.TOP, fill=tk.X, pady=(8, 0))
        self.tree_bl = ttk.Treeview(
            bl_frame,
            columns=("name", "msgs", "last"),
            show="headings",
            height=4,
        )
        for c, t, w in [
            ("name", "联系人", 200),
            ("msgs", "消息数 (本时段)", 140),
            ("last", "最近活跃", 160),
        ]:
            self.tree_bl.heading(c, text=t)
            self.tree_bl.column(c, width=w, anchor=tk.W)
        ys = ttk.Scrollbar(bl_frame, orient=tk.VERTICAL, command=self.tree_bl.yview)
        self.tree_bl.configure(yscrollcommand=ys.set)
        self.tree_bl.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ys.pack(side=tk.RIGHT, fill=tk.Y)

    def _build_summary_page(self, nb: ttk.Notebook, key: str, label: str) -> None:
        page = ttk.Frame(nb, padding=4)
        nb.add(page, text=label)
        # 左: 联系人表
        left = ttk.LabelFrame(page, text="联系人级别", padding=4)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 4))
        tree = ttk.Treeview(
            left,
            columns=("name", "msgs", "rep", "last", "flag"),
            show="headings",
            height=8,
        )
        for c, t, w in [
            ("name", "联系人", 180),
            ("msgs", "消息", 60),
            ("rep", "已回复", 60),
            ("last", "最近活跃", 140),
            ("flag", "标记", 90),
        ]:
            tree.heading(c, text=t)
            tree.column(c, width=w, anchor=tk.W)
        ys = ttk.Scrollbar(left, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=ys.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ys.pack(side=tk.RIGHT, fill=tk.Y)
        # 右: 汇总 + 趋势
        right = ttk.Frame(page)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(4, 0))
        # 汇总卡
        cards = ttk.Frame(right)
        cards.pack(side=tk.TOP, fill=tk.X)
        var_total_msg = tk.StringVar(value="0")
        var_total_rep = tk.StringVar(value="0")
        var_skip = tk.StringVar(value="0")
        var_rate = tk.StringVar(value="-")
        for i, (lab, var) in enumerate([
            ("总消息", var_total_msg),
            ("已回复", var_total_rep),
            ("跳过/黑名单", var_skip),
            ("回复率", var_rate),
        ]):
            cell = ttk.LabelFrame(cards, text=lab, padding=4)
            cell.grid(row=0, column=i, sticky="ew", padx=2, pady=2)
            ttk.Label(cell, textvariable=var, font=("", 14, "bold")).pack()
            cards.columnconfigure(i, weight=1)
        self._summary_pages[key] = dict(
            tree=tree,
            var_total_msg=var_total_msg,
            var_total_rep=var_total_rep,
            var_skip=var_skip,
            var_rate=var_rate,
        )

    # ------------------------------------------------------------ actions
    def _start_daemon(self) -> None:
        if not self.on_start_daemon:
            messagebox.showwarning(
                "未配置",
                "本 GUI 控制器未启用「启动守护」回调。\n"
                "请改用命令行: python daemon.py --daemon",
            )
            return
        ok = self.on_start_daemon()
        if ok:
            messagebox.showinfo(
                "已启动",
                "守护进程已在后台启动。\n"
                "stdout/stderr 写入 data/logs/daemon_stdout.log",
            )
        else:
            messagebox.showerror("启动失败", "详见日志 (data/logs/wechat_auto_reply.log)")

    def _stop_daemon(self) -> None:
        if not self.on_stop_daemon:
            messagebox.showwarning(
                "未配置",
                "本 GUI 控制器未启用「停止守护」回调。\n"
                "请到终端按 Ctrl+C。",
            )
            return
        if not messagebox.askyesno("停止守护", "确认停止守护进程？"):
            return
        ok = self.on_stop_daemon()
        if ok:
            messagebox.showinfo("已停止", "守护进程已停止")
        else:
            messagebox.showerror("停止失败", "未找到守护进程或停止失败")

    def _pause(self) -> None:
        self.controller.pause()
        log.info("GUI: 已发送暂停信号")

    def _resume(self) -> None:
        self.controller.resume()
        log.info("GUI: 已发送恢复信号")

    def _run_once(self) -> None:
        self.controller.force_reply_now()
        log.info("GUI: 已发送 run_once 指令")

    def _trigger_reply(self) -> None:
        target = self.var_target.get().strip()
        if not target:
            messagebox.showwarning("未指定", "请先填联系人名（含匹配片段即可）")
            return
        self.controller.trigger_reply(target)
        log.info(f"GUI: 已发送 trigger_reply -> {target}")

    def _refresh_summary_now(self) -> None:
        self._refresh_summary(force=True)

    # ------------------------------------------------------------ polling
    def _schedule(self) -> None:
        try:
            self._refresh()
        except Exception:
            pass
        self.after(self.POLL_MS, self._schedule)

    def _refresh(self) -> None:
        st = self.controller.read_status()
        if not st:
            self.var_state.set("守护未运行")
            self.var_activity.set("-")
            return
        self.var_pid.set(str(st.get("pid", "-")))
        self.var_state.set(st.get("state", "?"))
        self.var_started.set(str(st.get("started_at", "-"))[:19])
        self.var_tick.set(str(st.get("tick_count", 0)))
        self.var_replies.set(str(st.get("messages_handled", 0)))
        self.var_errors.set(str(st.get("errors_count", 0)))
        self.var_activity.set(st.get("current_activity", "-"))

    # ------------------------------------------------------------ summary
    def _schedule_nightly_refresh(self) -> None:
        """排到下一个 21:00 自动重扫 summary."""
        now = datetime.now()
        target = now.replace(hour=self.NIGHTLY_HOUR, minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        delta_ms = int((target - now).total_seconds() * 1000)
        log.info(f"下一次 summary 自动重扫: {target.isoformat(timespec='seconds')}")
        self.after(delta_ms, self._nightly_fire)

    def _nightly_fire(self) -> None:
        try:
            self._refresh_summary(force=True)
        finally:
            # 再排下一次
            self.after(24 * 3600 * 1000, self._nightly_fire)

    def _refresh_summary(self, force: bool = False) -> None:
        # 确保 contact_filter 已加载
        if self.contact_filter is None:
            try:
                from wechat_bot.ipc import IPCPaths

                self.contact_filter = ContactFilter(IPCPaths(self.cfg), self.cfg)
            except Exception as e:
                log.warning(f"加载名单失败: {e}")
                self.contact_filter = None

        for key in ("today", "yesterday", "week"):
            start, end = _range_bounds(key)
            try:
                data = self.storage.scan_summary(start=start, end=end)
            except Exception as e:
                log.error(f"scan_summary 失败: {e}")
                data = {
                    "total_messages": 0, "total_replies": 0,
                    "by_contact": {}, "hourly": {h: 0 for h in range(24)},
                    "files_chat": 0, "files_reply": 0,
                }
            self._render_summary_page(key, data)

        # 黑名单独立栏：取"今天"的数据来展示（最直观）
        try:
            today_start, today_end = _range_bounds("today")
            data = self.storage.scan_summary(start=today_start, end=today_end)
            self._render_blacklist(data)
        except Exception as e:
            log.error(f"黑名单刷新失败: {e}")

        self.var_last_scan.set(f"已扫描 @ {datetime.now().strftime('%H:%M:%S')}")

    def _render_summary_page(self, key: str, data: Dict[str, Any]) -> None:
        page = self._summary_pages[key]
        # 树
        tree = page["tree"]
        for iid in tree.get_children():
            tree.delete(iid)
        # 按消息数倒序
        rows = []
        for name, info in data["by_contact"].items():
            is_black = self.contact_filter.is_blacklisted(name) if self.contact_filter else False
            is_white = self.contact_filter.is_whitelisted(name) if self.contact_filter else False
            flag = []
            if is_black:
                flag.append("🚫黑名单")
            if is_white:
                flag.append("✅白名单")
            rows.append({
                "name": name,
                "msgs": info["messages"],
                "rep": info["replies"],
                "last": info["last_time"],
                "flag": " ".join(flag) or "-",
            })
        rows.sort(key=lambda r: -r["msgs"])
        for r in rows:
            lt = r["last"].strftime("%m-%d %H:%M") if r["last"] else "-"
            tree.insert("", tk.END, values=(r["name"], r["msgs"], r["rep"], lt, r["flag"]))
        # 汇总
        msg = data["total_messages"]
        rep = data["total_replies"]
        skip = max(0, msg - rep)
        rate = f"{(rep / msg * 100):.0f}%" if msg > 0 else "-"
        page["var_total_msg"].set(str(msg))
        page["var_total_rep"].set(str(rep))
        page["var_skip"].set(str(skip))
        page["var_rate"].set(rate)
        # 趋势图已移除 (v2.4)

    def _render_blacklist(self, data: Dict[str, Any]) -> None:
        for iid in self.tree_bl.get_children():
            self.tree_bl.delete(iid)
        if not self.contact_filter:
            return
        # 找名字在 by_contact 里, 且在黑名单中
        for name, info in data["by_contact"].items():
            if not self.contact_filter.is_blacklisted(name):
                continue
            lt = info["last_time"].strftime("%m-%d %H:%M") if info["last_time"] else "-"
            self.tree_bl.insert(
                "", tk.END,
                values=(name, info["messages"], lt),
            )
        if not self.tree_bl.get_children():
            self.tree_bl.insert("", tk.END, values=("（今日无黑名单联系人消息）", "-", "-"))

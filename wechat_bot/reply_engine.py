"""Core pipeline: identify contacts -> click -> read chat -> AI reply -> send.

Public API:
    ReplyEngine(cfg, ocr, ai, window, storage).run_once()  -> bool

新增 (v2.5):
- 不再验证消息是否发送成功 (删除 _verify_sent OCR 验证)
- 用 storage.get_sent_texts(username) 加载该用户 15 天内所有 history+reply
  内容, 与 OCR 文本做子串包含判断:
    * OCR 行 ⊆ 某条 sent  ->  已回复, 跳过
    * OCR 行 ⊇ 某条 sent  ->  已回复, 跳过
    * 都不匹配           ->  incoming, 触发 AI 回复
- send_message 改为只发一次不重试不验证, 异常时返回 False
- 文件以"每天一个文件 + 追加"模式存, 15 天后自动清理

新增 (v2.5.1):
- AI 返回的 reply 如果含 \\n, 按换行拆分成多段, 逐段独立发送
  (微信 PC 端输入框是单行, 整段粘贴只发出第一行)
- send_message 返回 int: 实际成功发送的段数 (不是 bool)
- 段间 delay 用 cfg.line_send_interval (默认 0.6s, 可在 GUI 调)

新增 (v2.6):
- 消息去重指纹改为"每条 reply 末尾 N 个中文字符" (N=6, 只算汉字)
  (sent_texts set 元素从"按标点+行拆出的短句"简化为"末尾 6 字")
- 双向子串匹配更安全: 6 字指纹短而独特, 不会与新消息误撞
- OCR 文本 < 4 字仍视为新消息 (避免通用词误杀)
"""
from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple

import cv2
import numpy as np
import pyautogui
import pyperclip
from PIL import Image

from wechat_bot.ai_client import AIClient
from wechat_bot.color_utils import (
    classify,
    filter_overlapping,
    split_by_color,
    to_aabb,
)
from wechat_bot.config import BotConfig
from wechat_bot.logger import get_logger
from wechat_bot.ocr_engine import OcrEngine
from wechat_bot.storage import Storage
from wechat_bot.whitelist import ContactFilter
from wechat_bot.window import WindowManager

log = get_logger("wechat_bot.reply_engine")

# 合并 N 条 incoming 一起回
INCOMING_MERGE_N = 10


def _is_already_sent(ocr_text: str, sent_texts: Set[str]) -> bool:
    """判断 OCR 文本是否已经存在于 sent_texts (双向子串匹配).

    sent_texts 是 storage.get_sent_texts 返回的"已发指纹"集合,
    元素是每条 AI reply 的"末尾 N 个中文字符" (N=6, 只算汉字).

    - ocr_text ∈ sent       -> 命中 (OCR 文本被某个指纹包含)
    - sent     ∈ ocr_text   -> 命中 (某个指纹出现在 OCR 文本中, 如 AI 旧消息)
    - 前 N 字相同           -> 命中 (容忍 OCR 切分误差)

    门槛: OCR 文本 < 4 字视为新消息 (短通用词不去重).
    sent_texts 元素本身就是 6 字指纹, 不需要额外门槛.
    """
    s = (ocr_text or "").strip()
    if not s:
        return True
    if len(s) < 4:
        return False
    for sent in sent_texts:
        t = (sent or "").strip()
        if not t:
            continue
        if s in t or t in s:
            return True
        # 前缀匹配: 头 6 字相同 (指纹本身就是 6 字, 等价于相等)
        if s[:6] == t[:6]:
            return True
    return False


class ReplyEngine:
    def __init__(
        self,
        cfg: BotConfig,
        ocr: OcrEngine,
        ai: AIClient,
        window: WindowManager,
        storage: Storage,
        contact_filter: Optional[ContactFilter] = None,
    ):
        self.cfg = cfg
        self.ocr = ocr
        self.ai = ai
        self.window = window
        self.storage = storage
        self.contact_filter = contact_filter
        self.chat_index = storage.load_index()

    # ============================================================== high level
    def run_once(
        self,
        target_user: Optional[str] = None,
        force: bool = False,
    ) -> bool:
        """Process visible contacts once.

        Args:
            target_user: if set, only process this contact (matched by name).
            force: if True, 即使所有 OCR 文本都"已发"也强制重新生成 (GUI 手动触发).
        """
        log.info("=" * 60)
        log.info(f"开始新一轮处理 (target={target_user}, force={force})")
        log.info("=" * 60)

        if not self.window.setup(self.cfg.window_position, self.cfg.window_size):
            log.error("无法激活微信窗口")
            return False

        contacts = self.extract_contacts()
        if not contacts:
            log.info("未找到任何联系人")
            return False

        # 目标用户过滤
        if target_user:
            contacts = [c for c in contacts if target_user in c["text"]]
            if not contacts:
                log.info(f"目标 {target_user} 不在当前联系人列表")
                return False

        handled = 0
        for c in contacts:
            # 白黑名单检查
            if self.contact_filter and not self.contact_filter.allowed(c["text"]):
                log.info(f"联系人 {c['text']} 在白/黑名单中被屏蔽, 跳过")
                continue
            if self._process_contact(c, force=force):
                handled += 1
            time.sleep(1)
        log.info(f"本轮处理 {handled}/{len(contacts)} 个联系人")
        return handled > 0

    def process_contact(self, contact: Dict[str, Any]) -> bool:
        return self._process_contact(contact, force=False)

    def _process_contact(
        self, contact: Dict[str, Any], force: bool = False
    ) -> bool:
        """处理单个联系人: 抓 chat -> 区分 self/user -> y 截断 -> AI 回复 -> 发送.

        关键设计 (v1.0.0):  
        1) OCR 抓所有 chat_lines, 每行带 is_self (颜色识别)
        2) 自己的消息 (绿色气泡) 直接丢弃, 不参与去重
        3) **y 截断**: 找最下面 (y 最大) 的 self_line 作为"最新 AI 回复"边界,
           只对 y > last_self_y 的 user_lines 视为候选新消息
           (y <= last_self_y 的用户消息已经被下方 AI 回复覆盖, 跳过)
        4) 对候选消息再走 6 字指纹去重 (兜底: 同一用户长会话内偶发的指纹重合)
        5) 候选为空 -> 跳过; 否则触发 AI 回复 + 保存 + 发送
        """
        name = contact["text"]
        log.info(f"处理联系人: '{name}' (亮度 {contact['brightness']:.1f}, force={force})")
        if not self.click_contact(contact["x_position"], contact["y_position"]):
            return False

        username = self.extract_username() or name

        # 1) OCR chat (含 is_self 字段)
        chat_lines = self.extract_chat_lines()
        if not chat_lines:
            log.info(f"聊天记录为空, 跳过 {username}")
            return False

        # 2) 分离自己 vs 用户 (自己 = 绿色气泡, 已是我们发的不用回)
        self_lines = [l for l in chat_lines if l.get("is_self")]
        user_lines = [l for l in chat_lines if not l.get("is_self")]
        log.info(
            f"{username}: chat 总 {len(chat_lines)} 行 "
            f"(自己={len(self_lines)}, 用户={len(user_lines)})"
        )
        if not user_lines:
            log.info(f"无用户消息 (只有自己的 {len(self_lines)} 条), 跳过 {username}")
            return False

        # 3) y 截断: 找最下面 (y 最大) 的 self_line
        #    没有 self_line 时 (首次接触), last_self_y = -1, 所有 user_lines 都视为候选
        last_self_y = -1
        if self_lines:
            last_self_y = max(l["y"] for l in self_lines)
        log.info(
            f"{username}: last_self_y={last_self_y} "
            f"(self_lines 共 {len(self_lines)} 条)"
        )

        # 4) 候选: y > last_self_y 的 user_lines
        candidate_lines = [l for l in user_lines if l["y"] > last_self_y]
        if not candidate_lines:
            log.info(
                f"无新消息 (所有 {len(user_lines)} 条 user_lines "
                f"y <= {last_self_y} 即被下方 self_line 回复), 跳过 {username}"
            )
            return False

        # 5) 候选上做 6 字指纹去重 (兜底, 通常不会命中)
        sent_texts = self.storage.get_sent_texts(username)
        if force:
            incoming = candidate_lines
            log.info(f"force 模式: 把全部 {len(candidate_lines)} 候选视为 incoming")
        else:
            incoming = [
                l for l in candidate_lines
                if not _is_already_sent(l["text"], sent_texts)
            ]
            if not incoming:
                log.info(
                    f"无新消息 (候选 {len(candidate_lines)} 条, 全部命中 15 天已发指纹 "
                    f"size={len(sent_texts)}), 跳过 {username}"
                )
                return False
        log.info(
            f"{username}: 候选 {len(candidate_lines)} -> 入 incoming {len(incoming)} 条"
        )

        # 6) 截取最近 N 条 incoming
        incoming = incoming[-INCOMING_MERGE_N:]
        incoming_text = "\n".join(l["text"] for l in incoming)
        # 保存完整 chat (含自己 + 用户), 方便日后排查
        full_chat_text = "\n".join(l["text"] for l in chat_lines)
        log.info(
            f"{username}: 合并最近 {len(incoming)} 条 incoming: "
            f"{incoming_text[:80]!r}{'...' if len(incoming_text) > 80 else ''}"
        )

        # 7) 保存 chat
        self.storage.save_chat(username, full_chat_text)

        # 8) AI 回复
        reply = self.ai.chat(incoming_text, self.cfg.ai.system_prompt)
        if not reply or not reply.strip():
            log.warning(f"AI 返回空回复, 跳过 {username}")
            return False
        self.storage.save_reply(username, reply)

        # 9) 发送 (按 \n 拆段, 不重试不验证, 返回 int 成功段数)
        total_lines = len([s for s in re.split(r"[\r\n]+", reply) if s.strip()])
        sent_n = self.send_message(reply)
        if sent_n == 0:
            log.warning(
                f"send_message 全部失败 ({total_lines} 段): {username} "
                f"(reply 已写入 replies/, 下轮 OCR 仍能看到, 会重发)"
            )
            return False
        if sent_n < total_lines:
            log.warning(
                f"send_message 部分失败: {username} "
                f"({sent_n}/{total_lines} 段, 未发段下轮重试)"
            )
        else:
            log.info(
                f"已回复: {username} (incoming={len(incoming)}, "
                f"分段={sent_n}/{total_lines})"
            )
        return True

    # ============================================================== contacts
    def extract_contacts(self) -> List[Dict[str, Any]]:
        """OCR the contacts list, classify by color, return black-text contacts."""
        try:
            img = self.window.screenshot(
                self.cfg.contacts_region,
                save_path=(
                    self.storage.debug_path("contacts.png")
                    if self.cfg.debug_mode
                    else None
                ),
            )
            if img is None:
                log.error("联系人区域截图失败")
                return []
            arr = np.asarray(img)

            results = self.ocr.recognize(arr)
            log.info(f"识别到 {len(results)} 个文本区域")

            x_off, y_off, w_off, _ = self.cfg.contacts_region
            black, gray = split_by_color(
                results,
                arr,
                self.cfg.black_text_threshold,
                self.cfg.gray_text_threshold,
                region_offset_y=y_off,
                click_x_in_region=x_off,
                region_width=w_off,
                min_text_len=2,
            )

            log.info(
                f"统计: 黑色(联系人) {len(black)} 个, "
                f"灰色(最近消息) {len(gray)} 个"
            )
            if self.cfg.debug_mode:
                for c in black:
                    log.debug(
                        f"  联系: '{c['text']}' 亮度 {c['brightness']:.1f}"
                    )

            contacts = filter_overlapping(black)
            contacts = contacts[: self.cfg.max_contacts_to_check]

            if self.cfg.debug_mode:
                self._save_debug_overlay(arr, results)
                self._save_contacts_stats(black, gray, contacts)
            return contacts
        except Exception as e:
            log.error(f"提取联系人失败: {e}")
            return []

    def _save_debug_overlay(self, img: np.ndarray, results: List[Dict[str, Any]]):
        try:
            overlay = img.copy()
            for item in results:
                pos = item.get("position", [0, 0, 0, 0])
                x1, y1, x2, y2 = to_aabb(pos)
                info = classify(
                    img,
                    pos,
                    self.cfg.black_text_threshold,
                    self.cfg.gray_text_threshold,
                )
                color = (
                    (0, 255, 0)
                    if info["is_black"]
                    else (255, 0, 0)
                    if info["is_gray"]
                    else (0, 0, 255)
                )
                cv2.rectangle(overlay, (x1, y1), (x2, y2), color, 2)
            Image.fromarray(overlay).save(
                self.storage.debug_path("contacts_filtered.png")
            )
        except Exception as e:
            log.debug(f"保存调试覆盖图失败: {e}")

    def _save_contacts_stats(
        self,
        black: List[Dict[str, Any]],
        gray: List[Dict[str, Any]],
        final_list: List[Dict[str, Any]],
    ):
        try:
            path = self.storage.debug_path("contacts_stats.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write("=== 颜色阈值 ===\n")
                f.write(
                    f"黑色阈值: <{self.cfg.black_text_threshold}, "
                    f"灰色阈值: >={self.cfg.gray_text_threshold}\n\n"
                )
                f.write("=== 黑色 (联系人) ===\n")
                for i, c in enumerate(black, 1):
                    f.write(
                        f"{i}. {c['text']}  Y={c['y_position']:.1f}  "
                        f"亮度={c['brightness']:.1f}\n"
                    )
                f.write("\n=== 灰色 (最近消息, 已忽略) ===\n")
                for i, m in enumerate(gray, 1):
                    f.write(
                        f"{i}. {m['text']}  Y={m['y_position']:.1f}  "
                        f"亮度={m['brightness']:.1f}\n"
                    )
                f.write(f"\n最终参与处理: {len(final_list)} 个\n")
        except Exception as e:
            log.debug(f"写 stats 失败: {e}")

    # ============================================================== click
    def click_contact(self, x: float, y: float) -> bool:
        try:
            sw, sh = pyautogui.size()
            if not (0 <= x < sw and 0 <= y < sh):
                log.error(f"点击位置超界 ({x:.0f},{y:.0f})")
                return False
            pyautogui.moveTo(x, y, duration=0.4)
            time.sleep(0.2)
            pyautogui.click()
            time.sleep(1.2)
            return True
        except Exception as e:
            log.error(f"点击联系人失败: {e}")
            try:
                pyautogui.doubleClick()
                time.sleep(1.2)
                return True
            except Exception:
                return False

    # ============================================================== username
    def extract_username(self) -> Optional[str]:
        try:
            img = self.window.screenshot(
                self.cfg.username_region,
                save_path=(
                    self.storage.debug_path("username.png")
                    if self.cfg.debug_mode
                    else None
                ),
            )
            if img is None:
                return None
            arr = np.asarray(img)
            results = self.ocr.recognize(arr)
            parts = [
                (item.get("text") or "").strip()
                for item in results
                if (item.get("text") or "").strip()
            ]
            if not parts:
                return None
            import re

            name = " ".join(parts).strip()
            name = re.sub(r"[^\u4e00-\u9fa5a-zA-Z0-9 _\-\.]", "", name)
            return name or None
        except Exception as e:
            log.error(f"提取用户名失败: {e}")
            return None

    # ============================================================== chat text
    def extract_chat_history(self) -> str:
        """保留原 API: 返回拼接后的纯文本 (最后 10 行)."""
        lines = self.extract_chat_lines()
        if not lines:
            return ""
        text = "\n".join(l["text"] for l in lines)
        if len(text.splitlines()) > 10:
            text = "\n".join(text.splitlines()[-10:])
        return text

    def extract_chat_lines(self) -> List[Dict[str, Any]]:
        """结构化 OCR 聊天记录, 返回 [{text, y, x, is_self}, ...] 按 y 从上到下排序.

        新增 (v2.6.3): 灰色文字过滤.
        微信聊天区里的时间戳 ("18:30" / "昨天") 和系统提示
        ("你已添加对方为好友" / "消息已发送") 颜色是浅灰,
        判为 is_gray (亮度 >= gray_text_threshold) 时直接丢弃,
        避免被当作用户消息触发 AI 回复.

        - 自己消息 (绿色气泡): 不参与灰色过滤, 仍由 _is_self_bubble 识别
        - 用户/系统消息: 走 classify 判颜色, 灰色丢弃
        """
        try:
            img = self.window.screenshot(
                self.cfg.chat_region,
                save_path=(
                    self.storage.debug_path("chat.png")
                    if self.cfg.debug_mode
                    else None
                ),
            )
            if img is None:
                return []
            arr = np.asarray(img)
            results = self.ocr.recognize(arr)
            items: List[Dict[str, Any]] = []
            for r in results:
                txt = (r.get("text") or "").strip()
                if not txt:
                    continue
                pos = r.get("position", [0, 0, 0, 0])
                x1, y1, x2, y2 = to_aabb(pos)
                # 1) 自己消息识别 (绿色气泡)
                is_self = self._is_self_bubble(arr, pos)
                # 2) 非自己消息做灰色过滤 (时间戳/系统提示)
                if not is_self:
                    info = classify(
                        arr, pos,
                        self.cfg.black_text_threshold,
                        self.cfg.gray_text_threshold,
                    )
                    if info["is_gray"] and not info["is_black"]:
                        log.debug(
                            f"丢弃灰色 (时间戳/系统提示): {txt[:30]!r} "
                            f"brightness={info['avg_brightness']:.1f}"
                        )
                        continue
                items.append({
                    "text": txt,
                    "y": (y1 + y2) // 2,
                    "x": (x1 + x2) // 2,
                    "is_self": is_self,
                })
            items.sort(key=lambda x: x["y"])
            return items
        except Exception as e:
            log.error(f"提取聊天记录失败: {e}")
            return []

    # 微信自己的气泡颜色判别阈值 (RGB 空间, 宽松: G 比 R/B 都大 30+)
    _SELF_G_DIFF = 30  # G - R >= diff AND G - B >= diff
    _SELF_G_MIN = 100  # G 通道至少要这么多 (排除纯灰/白)

    def _is_self_bubble(self, img: np.ndarray, position, padding: int = 8) -> bool:
        """用气泡颜色判断是否自己的消息 (微信 PC 端).

        微信自己的气泡: 绿色 (#07C160 老版 / #95EC69 新版)
        用户的气泡: 白色 / 浅灰

        在 position 向外扩 padding 像素范围内采样均值, RGB 三通道:
        - G > R + 30 AND G > B + 30 AND G > 100 -> 自己 (绿色)
        - 其他                          -> 用户

        img 来自 np.asarray(PIL.Image), 是 RGB 顺序.
        """
        try:
            x1, y1, x2, y2 = to_aabb(position)
        except Exception:
            return False
        h, w = img.shape[:2]
        if x1 >= w or y1 >= h or x2 <= 0 or y2 <= 0:
            return False
        # 采样范围: 文本框向外扩 padding (气泡通常延伸到文字外)
        sx1 = max(0, min(w - 1, x1 - padding))
        sy1 = max(0, min(h - 1, y1 - padding))
        sx2 = max(0, min(w, x2 + padding + 1))
        sy2 = max(0, min(h, y2 + padding + 1))
        if sx2 <= sx1 or sy2 <= sy1:
            return False
        region = img[sy1:sy2, sx1:sx2]
        if region.size == 0:
            return False
        mean_rgb = region.reshape(-1, 3).mean(axis=0)
        r, g, b = float(mean_rgb[0]), float(mean_rgb[1]), float(mean_rgb[2])
        is_green = (
            g >= self._SELF_G_MIN
            and (g - r) >= self._SELF_G_DIFF
            and (g - b) >= self._SELF_G_DIFF
        )
        return bool(is_green)

    # ============================================================== send
    def send_message(self, message: str) -> int:
        """按换行拆分多行消息, 逐段独立发送 (不重试不验证).

        微信 PC 端的输入框是单行, 整段粘贴碰到 \\n 时只会发出第一行.
        因此 AI 返回的 reply 必须按 \\n 拆分成多段, 每段独立走
        _send_with_fallback (clipboard -> typing -> slow_typing).

        Returns:
            实际成功发送的段数 (int), 0 = 一段都没发出去.
            全部异常 (粘贴/输入都失败) 时, 异常段返回 0, 其它已发的不影响计数.
        """
        if not message or not message.strip():
            return 0
        # 按 \r\n / \n / \r 拆, 过滤空段, strip 每段
        lines = [seg.strip() for seg in re.split(r"[\r\n]+", message) if seg.strip()]
        if not lines:
            return 0
        interval = max(0.0, float(getattr(self.cfg, "line_send_interval", 0.6) or 0.0))
        total = len(lines)
        log.info(f"send_message: 拆 {total} 段, 段间 {interval}s")
        sent_count = 0
        for i, line in enumerate(lines, 1):
            log.debug(
                f"send_message: 第 {i}/{total} 段: {line[:40]!r}"
                f"{'...' if len(line) > 40 else ''}"
            )
            try:
                ok = self._send_with_fallback(line)
            except Exception as e:
                log.error(f"send_message 第 {i} 段异常: {e}")
                ok = False
            if ok:
                sent_count += 1
            else:
                log.warning(
                    f"send_message 第 {i}/{total} 段失败: {line[:40]!r}"
                )
            if i < total and interval > 0:
                time.sleep(interval)
        log.info(f"send_message: 成功 {sent_count}/{total} 段")
        return sent_count

    def _send_with_fallback(self, message: str) -> bool:
        """三层降级: clipboard -> 分段 -> 极慢."""
        try:
            if self._send_via_clipboard(message):
                return True
        except Exception as e:
            log.warning(f"剪贴板粘贴失败, 降级到逐字输入: {e}")
        try:
            if self._send_via_typing(message, chunk_size=30, interval=0.05):
                return True
        except Exception as e:
            log.warning(f"分段输入失败, 降级到极慢速输入: {e}")
        return self._send_via_slow_typing(message)

    def _click_input_box(self) -> None:
        x, y = self.cfg.message_input_position
        pyautogui.moveTo(x, y, duration=0.4)
        time.sleep(0.2)
        pyautogui.click()
        time.sleep(0.4)

    def _clear_input(self) -> None:
        for _ in range(3):
            pyautogui.hotkey("ctrl", "a")
            time.sleep(0.15)
            pyautogui.press("delete")
            time.sleep(0.15)
        time.sleep(0.3)

    def _send_via_clipboard(self, message: str) -> bool:
        pyperclip.copy("")
        time.sleep(0.1)
        pyperclip.copy(message)
        time.sleep(0.4)
        self._click_input_box()
        self._clear_input()
        for _ in range(3):
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.6)
            if pyperclip.paste() == message:
                break
        time.sleep(1.5)
        for _ in range(3):
            pyautogui.press("enter")
            time.sleep(0.4)
        return True

    def _send_via_typing(self, message: str, chunk_size: int = 30, interval: float = 0.05) -> bool:
        self._click_input_box()
        self._clear_input()
        for i in range(0, len(message), chunk_size):
            pyautogui.write(message[i : i + chunk_size], interval=interval)
            time.sleep(0.25)
        time.sleep(1.5)
        for _ in range(3):
            pyautogui.press("enter")
            time.sleep(0.4)
        return True

    def _send_via_slow_typing(self, message: str) -> bool:
        try:
            self._click_input_box()
            for _ in range(5):
                pyautogui.hotkey("ctrl", "a")
                time.sleep(0.1)
                pyautogui.press("delete")
                time.sleep(0.1)
            time.sleep(0.5)
            for i, ch in enumerate(message):
                try:
                    pyautogui.write(ch, interval=0.15)
                except Exception:
                    time.sleep(0.2)
                    pyautogui.write(ch, interval=0.2)
                time.sleep(0.08)
                if i % 30 == 0:
                    time.sleep(0.4)
            time.sleep(1.5)
            for _ in range(3):
                pyautogui.press("enter")
                time.sleep(0.4)
            return True
        except Exception as e:
            log.error(f"极慢速输入也失败: {e}")
            return False

    # ============================================================== helpers
    @staticmethod
    def _hash(username: str, text: str) -> str:
        """保留 hash 工具, 但当前主流程不再用 (供未来扩展)."""
        if not text:
            return ""
        import hashlib
        return hashlib.md5(f"{username}:{text}".encode("utf-8")).hexdigest()

    def _is_new(self, username: str, h: str) -> bool:
        """保留, 但当前主流程不再用."""
        if not h:
            return False
        last = self.chat_index.get(username, {}).get("last_hash", "")
        return last != h

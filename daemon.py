"""CLI entry point for the WeChat auto-reply bot (守护 + 测试 + 安装).

只负责命令行/守护模式, 不再启动 GUI.
GUI 入口请走独立的 gui.py (`python gui.py` 或 `python -m gui`).

Examples
--------
# 长驻守护 (Ctrl+C 退出) — 正常用法
python daemon.py --daemon

# 单轮运行 (调试 / 计划任务)
python daemon.py --once

# 校准输入框 (5 秒后取鼠标位置)
python daemon.py --calibrate

# 启动可视化校准窗口 (旧版单窗口)
python daemon.py --calibrate-gui

# 单独测试某个组件
python daemon.py --test ocr
python daemon.py --test window
python daemon.py --test message
python daemon.py --test ai

# 重置配置
python daemon.py --reset-config

# 安装依赖
python daemon.py --install

# 打印配置
python daemon.py --show-config
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from typing import List

from wechat_bot import (
    AIClient,
    BotConfig,
    Guardian,
    OcrEngine,
    ReplyEngine,
    Storage,
    WindowManager,
    get_logger,
)

log = get_logger("daemon")


# ---------------------------------------------------------------------- helpers
def _install_requirements() -> int:
    import subprocess

    req_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "requirements.txt")
    if not os.path.exists(req_path):
        print(f"未找到 {req_path}")
        return 1
    print(f"使用 Python: {sys.executable} ({sys.version.split()[0]})")
    print(f"目标 site-packages: {os.path.join(sys.prefix, 'Lib', 'site-packages')}")
    print(f"安装依赖: {req_path}")
    rc = subprocess.call(
        [sys.executable, "-m", "pip", "install", "-r", req_path]
    )
    if rc != 0:
        print(
            "\n[!] 安装失败。常见原因：\n"
            "    1) 当前 python 不是你想用的环境。推荐用你日常的 conda 环境：\n"
            "       C:\\Users\\<你>\\.conda\\envs\\<env>\\python.exe daemon.py --install\n"
            "    2) 有别的 python 进程锁住了 site-packages。关掉所有 python 窗口后重试。\n"
            "    3) 权限不足：conda 环境通常不需要管理员权限；如用系统 python 加 --user。\n"
        )
    return rc


def _build_components(cfg: BotConfig):
    storage = Storage(cfg.data_dir)
    ocr = OcrEngine(cache_timeout=cfg.cache_timeout)
    # 知识库 (可选)
    kb = None
    if cfg.kb.enabled:
        from wechat_bot.knowledge import KnowledgeBase

        kb_path = cfg.kb.file
        if not os.path.isabs(kb_path):
            kb_path = os.path.join(cfg.data_dir, kb_path)
        kb = KnowledgeBase(
            path=kb_path,
            top_k=cfg.kb.top_k,
            min_score=cfg.kb.min_score,
            max_chars_per_chunk=cfg.kb.max_chars_per_chunk,
        )
    ai = AIClient(cfg.ai, cache_timeout=cfg.cache_timeout, kb=kb)
    window = WindowManager(cfg.window_title)
    engine = ReplyEngine(cfg, ocr, ai, window, storage)
    return storage, ocr, ai, window, engine


# ---------------------------------------------------------------------- tests
def _test_window(window: WindowManager) -> bool:
    log.info("=== 测试: 窗口 ===")
    try:
        if not window.find():
            log.error("未找到微信窗口")
            return False
        log.info(f"找到窗口: '{window.window.title}'")
        return window.activate()
    except Exception as e:
        log.error(f"窗口测试失败: {e}")
        return False


def _test_ocr(cfg: BotConfig, ocr: OcrEngine, window: WindowManager) -> bool:
    log.info("=== 测试: OCR ===")
    try:
        img = window.screenshot(cfg.contacts_region)
        if img is None:
            log.error("截图失败")
            return False
        results = ocr.recognize(img)
        log.info(f"识别到 {len(results)} 个文本区域")
        for i, it in enumerate(results, 1):
            log.info(
                f"  {i}. '{(it.get('text') or '').strip()}' "
                f"score={it.get('score', 0):.2f}"
            )
        return True
    except Exception as e:
        log.error(f"OCR 测试失败: {e}")
        return False


def _test_message(cfg: BotConfig, engine: ReplyEngine) -> bool:
    log.info("=== 测试: 消息发送 ===")
    return engine.send_message("【自动测试】这是一条校准消息，请忽略喵~")


def _test_ai(cfg: BotConfig, ai: AIClient) -> bool:
    log.info("=== 测试: AI 回复 ===")
    text = ai.chat("你好, 请用一句话介绍你自己。", use_cache=False)
    log.info(f"AI 回复: {text}")
    return bool(text)


TESTS = {
    "ocr": lambda cfg, parts: _test_ocr(cfg, parts["ocr"], parts["window"]),
    "window": lambda cfg, parts: _test_window(parts["window"]),
    "message": lambda cfg, parts: _test_message(cfg, parts["engine"]),
    "ai": lambda cfg, parts: _test_ai(cfg, parts["ai"]),
}


# ---------------------------------------------------------------------- main
def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="自动回复",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--once", action="store_true", help="只运行一轮")
    parser.add_argument(
        "--daemon", action="store_true",
        help="长驻守护模式 (默认不指定时为 GUI)",
    )
    parser.add_argument(
        "--interval", type=int, default=None, help="检查间隔 (秒), 默认取配置"
    )
    parser.add_argument(
        "--max-contacts", type=int, default=None, help="每轮最多处理几个联系人"
    )
    parser.add_argument(
        "--threshold", type=int, default=None, help="亮度阈值, 同时设黑/灰"
    )
    parser.add_argument("--input-x", type=int, default=None, help="临时覆盖输入框 X")
    parser.add_argument("--input-y", type=int, default=None, help="临时覆盖输入框 Y")
    parser.add_argument("--calibrate", action="store_true", help="5 秒后取鼠标设输入框")
    parser.add_argument(
        "--calibrate-gui", action="store_true", help="打开 Tkinter 可视化校准器"
    )
    parser.add_argument(
        "--no-gui", action="store_true",
        help="不启动 GUI (一般不需要, 加 --daemon / --once 即可)",
    )
    parser.add_argument(
        "--test", choices=list(TESTS.keys()) + ["all"], help="单独运行某个测试"
    )
    parser.add_argument("--reset-config", action="store_true", help="重置配置")
    parser.add_argument("--install", action="store_true", help="安装 requirements.txt")
    parser.add_argument(
        "--show-config", action="store_true", help="打印当前配置并退出"
    )

    args = parser.parse_args(argv)

    if args.install:
        return _install_requirements()

    cfg = BotConfig.load()

    if args.interval is not None:
        cfg.check_interval = max(1, args.interval)
    if args.max_contacts is not None:
        cfg.max_contacts_to_check = max(1, args.max_contacts)
    if args.threshold is not None:
        cfg.black_text_threshold = max(0, min(255, args.threshold))
        cfg.gray_text_threshold = cfg.black_text_threshold
    if args.input_x is not None or args.input_y is not None:
        x = args.input_x if args.input_x is not None else cfg.message_input_position[0]
        y = args.input_y if args.input_y is not None else cfg.message_input_position[1]
        cfg.update_input_position(x, y)

    log.info("=" * 60)
    log.info("  自动回复系统")
    log.info(f"  检查间隔: {cfg.check_interval}s")
    log.info(f"  最大联系人数: {cfg.max_contacts_to_check}")
    log.info(f"  亮度阈值: 黑<{cfg.black_text_threshold} 灰>={cfg.gray_text_threshold}")
    log.info(f"  AI: provider={cfg.ai.provider} model={cfg.ai.model} base={cfg.ai.base_url}")
    log.info(f"  数据目录: {os.path.abspath(cfg.data_dir)}")
    log.info("=" * 60)

    if args.show_config:
        import json

        print(json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2))
        return 0

    if args.reset_config:
        cfg.reset()
        return 0

    if args.calibrate_gui:
        try:
            from gui.calibrator import main as gui_main
        except Exception as e:
            log.error(f"无法启动 GUI: {e}")
            return 1
        gui_main()
        return 0

    if args.calibrate:
        log.info("请在 5 秒内点击微信消息输入框...")
        time.sleep(5)
        try:
            import pyautogui

            x, y = pyautogui.position()
        except Exception as e:
            log.error(f"读取鼠标失败: {e}")
            return 1
        cfg.update_input_position(x, y)
        log.info(f"输入框已校准: ({x}, {y})")
        return 0

    storage, ocr, ai, window, engine = _build_components(cfg)
    parts = dict(storage=storage, ocr=ocr, ai=ai, window=window, engine=engine)

    if args.test:
        targets = list(TESTS.keys()) if args.test == "all" else [args.test]
        ok = True
        for name in targets:
            log.info(f"\n>>> 测试: {name}")
            ok = TESTS[name](cfg, parts) and ok
        log.info(f"\n测试结果: {'全部通过' if ok else '有失败'}")
        return 0 if ok else 1

    if args.once:
        log.info("执行单轮处理...")
        return 0 if engine.run_once() else 1

    if args.daemon:
        log.info("启动长驻守护 (Ctrl+C 退出)")
        guardian = Guardian(cfg, window, engine.run_once)
        try:
            guardian.run()
        except KeyboardInterrupt:
            log.info("收到 Ctrl+C, 退出")
        return 0

    # ====== 无参数: 报错并提示走 gui.py ======
    parser.print_usage()
    log.error(
        "\ndaemon.py 不再启动 GUI. 请明确指定模式, 例如:\n"
        "    python daemon.py --daemon    # 长驻守护\n"
        "    python daemon.py --once      # 单轮\n"
        "    python daemon.py --test ocr  # 组件测试\n"
        "\n要启动 GUI 控制器, 请运行:\n"
        "    python gui.py                # 或 python -m gui\n"
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())

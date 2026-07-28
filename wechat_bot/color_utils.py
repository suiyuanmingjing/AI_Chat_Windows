"""Color/brightness analysis helpers (used to distinguish contact names from
recent messages in the contacts panel)."""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

import cv2
import numpy as np

from wechat_bot.logger import get_logger

log = get_logger("wechat_bot.color_utils")

Position = Tuple[int, int, int, int]


def to_aabb(pos) -> Position:
    """把 cnocr 返回的 position 标准化为轴对齐矩形 (x1, y1, x2, y2).

    cnocr 2.x: position 是 (4, 2) ndarray, 4 个角点
    cnocr 1.x / 旧版: position 是 [x1, y1, x2, y2] 平铺
    任何 numpy 标量都会被转成 int
    """
    arr = np.asarray(pos)
    if arr.ndim == 2 and arr.shape == (4, 2):
        # 4 个角点 -> AABB
        xs = arr[:, 0]
        ys = arr[:, 1]
        return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())
    if arr.ndim == 1 and arr.size >= 4:
        return int(arr[0]), int(arr[1]), int(arr[2]), int(arr[3])
    # 兜底: 当成 4-tuple
    flat = arr.flatten().tolist()[:4]
    while len(flat) < 4:
        flat.append(0)
    return int(flat[0]), int(flat[1]), int(flat[2]), int(flat[3])


def clamp_region(
    pos: Position, img_shape: Tuple[int, int]
) -> Tuple[int, int, int, int]:
    """Clamp a bounding box to image bounds; returns safe (x1,y1,x2,y2).

    自动适配 cnocr 1.x (flat) 与 2.x ((4,2) corners) 两种 position 格式.
    """
    x1, y1, x2, y2 = to_aabb(pos)
    h, w = img_shape[:2]
    x1, x2 = max(0, x1), min(w, x2)
    y1, y2 = max(0, y1), min(h, y2)
    return x1, y1, x2, y2


def average_brightness(img: np.ndarray, pos: Position) -> float:
    """Return mean grayscale brightness (0-255) for the given box."""
    x1, y1, x2, y2 = clamp_region(pos, img.shape)
    if x2 <= x1 or y2 <= y1:
        return 255.0
    patch = img[y1:y2, x1:x2]
    if patch.ndim == 3:
        patch = cv2.cvtColor(patch, cv2.COLOR_RGB2GRAY)
    return float(np.mean(patch))


def classify(
    img: np.ndarray,
    pos: Position,
    black_threshold: int,
    gray_threshold: int,
) -> Dict[str, Any]:
    """Classify a text region by brightness.

    Returns a dict with keys: is_black, is_gray, avg_brightness.
    """
    brightness = average_brightness(img, pos)
    return {
        "is_black": brightness < black_threshold,
        "is_gray": brightness >= gray_threshold,
        "avg_brightness": brightness,
    }


def split_by_color(
    results: List[Dict[str, Any]],
    img: np.ndarray,
    black_threshold: int,
    gray_threshold: int,
    region_offset_y: int = 0,
    click_x_in_region: float | None = None,
    region_width: int | None = None,
    min_text_len: int = 2,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Split OCR results into black-text items (contact names) and gray-text
    items (recent messages, ignored)."""
    black: List[Dict[str, Any]] = []
    gray: List[Dict[str, Any]] = []

    for item in results:
        text = (item.get("text") or "").strip()
        if len(text) < min_text_len:
            continue
        pos = item.get("position", [0, 0, 0, 0])
        info = classify(img, pos, black_threshold, gray_threshold)
        x1, y1, x2, y2 = to_aabb(pos)
        center_y = (y1 + y2) / 2 + region_offset_y
        if click_x_in_region is not None and region_width is not None:
            click_x = click_x_in_region + region_width / 2
        else:
            click_x = (x1 + x2) / 2
        record = {
            "text": text,
            "x_position": float(click_x),
            "y_position": float(center_y),
            "confidence": float(item.get("score", 0.5)),
            "brightness": info["avg_brightness"],
        }
        if info["is_black"]:
            black.append(record)
        elif info["is_gray"]:
            gray.append(record)

    return black, gray


def filter_overlapping(
    items: List[Dict[str, Any]], min_vertical_gap: int = 20
) -> List[Dict[str, Any]]:
    """Remove near-duplicate items whose Y is within min_vertical_gap of the
    previously accepted item (top-to-bottom dedup)."""
    items = sorted(items, key=lambda r: r["y_position"])
    out: List[Dict[str, Any]] = []
    last_y = -10_000
    for it in items:
        if it["y_position"] - last_y > min_vertical_gap:
            out.append(it)
            last_y = it["y_position"]
    return out

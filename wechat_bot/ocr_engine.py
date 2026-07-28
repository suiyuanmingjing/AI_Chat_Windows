"""CnOCR wrapper with simple in-memory cache."""
from __future__ import annotations

import hashlib
import time
from typing import Any, Dict, List, Optional

import numpy as np
from PIL import Image

from wechat_bot.logger import get_logger

log = get_logger("wechat_bot.ocr_engine")


class OcrEngine:
    """Thin wrapper around cnocr.CnOcr adding a TTL cache."""

    # 默认固定到 v2.3 时代的模型组合（已下载到 AppData/Roaming）：
    #   rec: densenet_lite_136-gru
    #   det: ch_PP-OCRv5_det
    # cnocr 2.3.3 默认走 multi_PP-OCRv6_det_small 触发下载，机器无网/SSL 异常时
    # 容易卡住；显式指定 v5 即可直接用本地缓存。
    DEFAULT_REC_MODEL = "densenet_lite_136-gru"
    DEFAULT_DET_MODEL = "ch_PP-OCRv5_det"

    def __init__(
        self,
        cache_timeout: int = 300,
        rec_model_name: str | None = None,
        det_model_name: str | None = None,
    ):
        log.info("正在初始化 CnOCR，首次使用会下载模型...")
        # 延迟导入，避免无 cnocr 环境下模块加载即失败
        from cnocr import CnOcr  # type: ignore

        kwargs = {}
        if rec_model_name:
            kwargs["rec_model_name"] = rec_model_name
        else:
            kwargs["rec_model_name"] = self.DEFAULT_REC_MODEL
        if det_model_name:
            kwargs["det_model_name"] = det_model_name
        else:
            kwargs["det_model_name"] = self.DEFAULT_DET_MODEL
        log.info(
            f"CnOcr 模型: rec={kwargs['rec_model_name']} "
            f"det={kwargs['det_model_name']}"
        )
        self.ocr = CnOcr(**kwargs)
        self.cache_timeout = cache_timeout
        self._cache: Dict[str, Any] = {}
        log.info("CnOCR 初始化完成")

    # ------------------------------------------------------------------ cache
    def _cache_get(self, key: str) -> Optional[Any]:
        v = self._cache.get(key)
        if not v:
            return None
        value, ts = v
        if time.time() - ts < self.cache_timeout:
            return value
        self._cache.pop(key, None)
        return None

    def _cache_set(self, key: str, value: Any) -> None:
        self._cache[key] = (value, time.time())

    @staticmethod
    def _fingerprint(img: Image.Image | np.ndarray) -> str:
        if isinstance(img, Image.Image):
            arr = np.asarray(img)
        else:
            arr = img
        # 缩略到 32x32 灰度再算 hash，足以区分两次截图
        small = Image.fromarray(arr).convert("L").resize((32, 32))
        return hashlib.md5(small.tobytes()).hexdigest()

    # ------------------------------------------------------------------ ocr
    def recognize(self, image: Image.Image | np.ndarray) -> List[Dict[str, Any]]:
        """Run OCR and return the raw results list (each item: text, score, position)."""
        arr = np.asarray(image) if isinstance(image, Image.Image) else image
        key = self._fingerprint(arr)
        cached = self._cache_get(key)
        if cached is not None:
            log.debug("OCR 命中缓存")
            return cached
        results = self.ocr.ocr(arr)  # type: ignore[union-attr]
        self._cache_set(key, results)
        return results

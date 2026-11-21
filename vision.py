from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field

import numpy as np
from PIL import Image
from rapidocr import RapidOCR


# 所有区域都以窗口客户区为基准归一化，适配 16:9 的不同分辨率。
BOX_TITLE = (0.435, 0.000, 0.565, 0.062)
BOX_CONTINUE_CALAMITY = (0.632, 0.590, 0.723, 0.654)
BOX_RETREAT_CALAMITY = (0.298, 0.590, 0.372, 0.654)
BOX_CONTINUE_NORMAL = (0.630, 0.645, 0.725, 0.725)
BOX_CURRENT = (0.212, 0.278, 0.270, 0.336)
BOX_NEXT_1 = (0.548, 0.280, 0.596, 0.338)
BOX_NEXT_2 = (0.548, 0.366, 0.596, 0.438)
BOX_NEXT_3 = (0.548, 0.462, 0.596, 0.535)
LAYOUT_SHIFT = 59.0 / 900.0
BOX_CURRENT_LOW = (0.212, 0.278 + LAYOUT_SHIFT, 0.270, 0.336 + LAYOUT_SHIFT)
BOX_NEXT_1_LOW = (0.548, 0.280 + LAYOUT_SHIFT, 0.596, 0.338 + LAYOUT_SHIFT)
BOX_NEXT_2_LOW = (0.548, 0.366 + LAYOUT_SHIFT, 0.596, 0.438 + LAYOUT_SHIFT)
BOX_NEXT_3_LOW = (0.548, 0.462 + LAYOUT_SHIFT, 0.596, 0.535 + LAYOUT_SHIFT)
BOX_DIALOG_ALL = (0.170, 0.180, 0.830, 0.750)

TITLES = ("行动抉择", "撤退决策")


def _norm(text: str) -> str:
    return re.sub(r"[\s·.。:：,，]+", "", text or "")


def _number(text: str) -> int | None:
    m = re.search(r"(\d{2,5})", text or "")
    return int(m.group(1)) if m else None


def _is_continue(text: str) -> bool:
    t = _norm(text)
    return "继续挑战" in t or "继续" in t or "挑战" in t


def _is_retreat(text: str) -> bool:
    t = _norm(text)
    return "撤离" in t or "撤退" in t


def _pixel_box(size: tuple[int, int], box: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
    w, h = size
    x0, y0, x1, y1 = box
    return max(0, int(x0 * w)), max(0, int(y0 * h)), min(w, int(x1 * w)), min(h, int(y1 * h))


@dataclass
class Reading:
    found: bool = False
    calamity: bool = False
    title: str = ""
    current: int | None = None
    next_level: int | None = None
    continue_text: str = ""
    retreat_text: str = ""
    score: float = 0.0
    note: str = ""
    layout: str = "high"
    button_y: float = 0.622
    raw: dict = field(default_factory=dict)


class Vision:
    """只读识别：先走固定区域的轻量文字识别，失败时再做整块检测。"""

    def __init__(self) -> None:
        self._engine = RapidOCR()
        self._lock = threading.Lock()
        self._last_full_scan = 0.0

    def _run_rec(self, crop: Image.Image, scale: float = 1.0) -> tuple[str, float]:
        if scale != 1.0:
            crop = crop.resize((max(1, int(crop.width * scale)), max(1, int(crop.height * scale))),
                               Image.Resampling.LANCZOS)
        arr = np.asarray(crop.convert("RGB"))[:, :, ::-1]
        with self._lock:
            out = self._engine(arr, use_det=False, use_cls=False, use_rec=True)
        texts = getattr(out, "txts", None)
        scores = getattr(out, "scores", None)
        if texts is None:
            texts = ()
        if scores is None:
            scores = ()
        if not texts:
            return "", 0.0
        return str(texts[0]), float(scores[0]) if scores else 0.0

    def _rec(self, frame: Image.Image, box: tuple[float, float, float, float]) -> tuple[str, float]:
        x0, y0, x1, y1 = _pixel_box(frame.size, box)
        if x1 <= x0 or y1 <= y0:
            return "", 0.0
        return self._run_rec(frame.crop((x0, y0, x1, y1)))

    def read_text(self, frame: Image.Image, box: tuple[float, float, float, float],
                  scale: float = 1.0) -> tuple[str, float]:
        """给主控角色的蓄力百分比等小框用；scale>1 时先放大再识别。"""
        x0, y0, x1, y1 = _pixel_box(frame.size, box)
        if x1 <= x0 or y1 <= y0:
            return "", 0.0
        return self._run_rec(frame.crop((x0, y0, x1, y1)), scale=scale)

    def _pick_next(self, values: tuple[int | None, int | None, int | None], current: int | None) -> tuple[int | None, bool]:
        n1, n2, n3 = values
        # 第一行上方紧挨着“下一轮次”，文字识别偶尔会把标签末尾并进数字（例如 1180）。
        # 只要数字和左侧当前等级差得很远，就先按误读处理，交给下面两行做序列校验。
        if n1 is not None and current and current >= 50 and not (0 < n1 - current <= 30):
            n1 = None
        if n1 and n2 and n2 - n1 == 5:
            return n1, True
        if n1 and n3 and n3 - n1 == 10:
            return n1, True
        if n2 and n3 and n3 - n2 == 5:
            return n2 - 5, True
        if n1 and current and n1 - current == 5:
            return n1, True
        if n1:
            return n1, False
        if n2:
            return n2 - 5, False
        if n3:
            return n3 - 10, False
        return None, False

    def read(self, frame: Image.Image, allow_full_scan: bool = True) -> Reading:
        title_text, title_score = self._rec(frame, BOX_TITLE)
        continue_text, continue_score = self._rec(frame, BOX_CONTINUE_CALAMITY)
        retreat_text, _ = self._rec(frame, BOX_RETREAT_CALAMITY)

        title = _norm(title_text)
        reading = Reading(title=title, continue_text=_norm(continue_text), retreat_text=_norm(retreat_text))

        layout = "high"
        button_y = 0.622
        if not _is_continue(continue_text):
            normal_text, normal_score = self._rec(frame, BOX_CONTINUE_NORMAL)
            if _is_continue(normal_text) and normal_score > 0.35:
                layout = "low"
                button_y = 0.622 + LAYOUT_SHIFT
                continue_text = normal_text
                continue_score = normal_score
            else:
                reading.note = "未检测到行动抉择" if not title else f"当前标题：{title}"
                reading.raw = {"title": title}
                return reading

        reading.found = True
        reading.layout = layout
        reading.button_y = button_y
        reading.score = continue_score
        reading.continue_text = _norm(continue_text)

        if layout == "high":
            boxes = (BOX_CURRENT, BOX_NEXT_1, BOX_NEXT_2, BOX_NEXT_3)
        else:
            boxes = (BOX_CURRENT_LOW, BOX_NEXT_1_LOW, BOX_NEXT_2_LOW, BOX_NEXT_3_LOW)
        current_text, current_score = self._rec(frame, boxes[0])
        n1_text, _ = self._rec(frame, boxes[1])
        n2_text, _ = self._rec(frame, boxes[2])
        n3_text, _ = self._rec(frame, boxes[3])
        current = _number(current_text)
        values = (_number(n1_text), _number(n2_text), _number(n3_text))
        reading.raw = {
            "current_text": _norm(current_text),
            "next_text": [_norm(n1_text), _norm(n2_text), _norm(n3_text)],
            "continue_score": continue_score,
            "layout": layout,
        }

        next_level, sequence_valid = self._pick_next(values, current)
        if next_level is None and allow_full_scan and time.monotonic() - self._last_full_scan > 4.0:
            self._last_full_scan = time.monotonic()
            full = self.read_full(frame)
            if full is not None:
                full_current, full_next = full
                if full_next is not None:
                    current, next_level = full_current, full_next
                    sequence_valid = bool(full_current is not None and full_next - full_current == 5)

        if next_level is None:
            texts = (current_text, n1_text, n2_text, n3_text)
            if any("轮次" in _norm(text) for text in texts):
                reading.note = "常规/扼守行动抉择，已忽略"
            else:
                reading.note = "弹窗已出现，等级文字未读清"
            return reading

        if next_level % 5 != 0:
            reading.note = "常规/扼守行动抉择，已忽略"
            return reading

        if current is None or (next_level - current) != 5:
            current = next_level - 5

        reading.current = current
        reading.next_level = next_level
        reading.calamity = bool(
            sequence_valid
            or "lv" in _norm(current_text).lower()
            or (current >= 100 and next_level - current == 5)
        )
        reading.note = "灾厄等级序列" if reading.calamity else f"等级差不是 5：{current}->{next_level}"
        return reading

    def read_full(self, frame: Image.Image) -> tuple[int | None, int | None] | None:
        """整块检测兜底：按文字框位置找当前等级与右侧下一轮等级。"""
        x0, y0, x1, y1 = _pixel_box(frame.size, BOX_DIALOG_ALL)
        crop = frame.crop((x0, y0, x1, y1))
        arr = np.asarray(crop.convert("RGB"))[:, :, ::-1]
        with self._lock:
            out = self._engine(arr, use_det=True, use_cls=True, use_rec=True)
        texts = getattr(out, "txts", None)
        boxes = getattr(out, "boxes", None)
        if texts is None:
            texts = ()
        if boxes is None:
            boxes = ()
        if len(texts) == 0 or len(boxes) == 0:
            return None

        gw, gh = frame.size
        nums: list[tuple[float, float, float, int]] = []
        current: int | None = None
        for text, box in zip(texts, boxes):
            value = _number(str(text))
            if value is None:
                continue
            b = np.asarray(box)
            bx0 = float(b[:, 0].min()) + x0
            by0 = float(b[:, 1].min()) + y0
            bx1 = float(b[:, 0].max()) + x0
            by1 = float(b[:, 1].max()) + y0
            cx = (bx0 + bx1) / 2.0 / gw
            cy = (by0 + by1) / 2.0 / gh
            bh = (by1 - by0) / gh
            if 0.45 <= cx <= 0.68 and bh >= 0.025:
                nums.append((cy, cx, bh, value))
            elif 0.15 <= cx <= 0.35 and bh >= 0.03 and current is None:
                current = value
        if not nums:
            return None
        nums.sort(key=lambda item: item[0])
        return current, nums[0][3]

    def full_texts(self, frame: Image.Image) -> list[tuple[str, float, tuple[int, int, int, int]]]:
        """识别测试用：返回整块弹窗的文字和坐标。"""
        x0, y0, x1, y1 = _pixel_box(frame.size, BOX_DIALOG_ALL)
        crop = frame.crop((x0, y0, x1, y1))
        arr = np.asarray(crop.convert("RGB"))[:, :, ::-1]
        with self._lock:
            out = self._engine(arr, use_det=True, use_cls=True, use_rec=True)
        texts = getattr(out, "txts", None)
        boxes = getattr(out, "boxes", None)
        scores = getattr(out, "scores", None)
        if texts is None:
            texts = ()
        if boxes is None:
            boxes = ()
        if scores is None:
            scores = ()
        result = []
        for i, (text, box) in enumerate(zip(texts, boxes)):
            b = np.asarray(box)
            result.append((
                str(text),
                float(scores[i]) if i < len(scores) else 0.0,
                (
                    int(b[:, 0].min() + x0),
                    int(b[:, 1].min() + y0),
                    int(b[:, 0].max() + x0),
                    int(b[:, 1].max() + y0),
                ),
            ))
        return result

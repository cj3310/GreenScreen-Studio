"""
绿幕抠图核心引擎 — 纯算法模块，不依赖 Qt。

负责 HSV 色度抠除、蒙版形态学处理、高斯羽化、边缘平滑、
裁切、RGBA 合成与透明 PNG 导出。
"""

import os
import json
import numpy as np
import cv2
from dataclasses import dataclass, asdict, field
from typing import Optional, Tuple, Dict


@dataclass
class KeyingParams:
    """抠图全部可调参数。HSV 范围遵循 OpenCV 8 位尺度：
    H: 0-179, S: 0-255, V: 0-255。"""
    # HSV 阈值上下限
    h_low: int = 35
    h_high: int = 85
    s_low: int = 50
    s_high: int = 255
    v_low: int = 50
    v_high: int = 255
    # 高斯羽化半径（蒙版边缘柔和）
    feather_radius: float = 2.0
    # 蒙版收缩/扩张：正值=收缩前景蒙版(去除绿边)，负值=扩张
    mask_shrink: int = 0
    # 边缘平滑抗锯齿强度
    edge_smooth: float = 1.0
    # 裁切矩形 (x, y, w, h)，None 表示不裁切
    crop_rect: Optional[Tuple[int, int, int, int]] = None
    # 是否仅预览黑白蒙版
    show_mask_only: bool = False

    def to_dict(self) -> Dict:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: Dict) -> "KeyingParams":
        known = {f.name for f in cls.__dataclass_fields__.values()}
        clean = {k: v for k, v in d.items() if k in known}
        return cls(**clean)


# 默认绿幕预设
DEFAULT_PRESET = KeyingParams()


def _odd_kernel(radius: float) -> int:
    """根据半径生成奇数卷积核大小。"""
    k = int(round(radius * 2))
    if k < 1:
        k = 1
    if k % 2 == 0:
        k += 1
    return k


def crop_frame(frame: np.ndarray,
               rect: Optional[Tuple[int, int, int, int]]) -> np.ndarray:
    """按裁切矩形裁剪画面，rect 为 None 时原样返回。"""
    if rect is None:
        return frame
    x, y, w, h = rect
    x = max(0, int(x))
    y = max(0, int(y))
    w = max(1, int(w))
    h = max(1, int(h))
    fh, fw = frame.shape[:2]
    x2 = min(fw, x + w)
    y2 = min(fh, y + h)
    return frame[y:y2, x:x2].copy()


def compute_mask(hsv: np.ndarray, p: KeyingParams) -> np.ndarray:
    """根据 HSV 与参数计算前景 Alpha 蒙版（前景=255, 绿幕=0）。

    流程：inRange 找绿幕 → 反转得前景 → 形态学收缩/扩张 → 羽化 → 边缘平滑。
    """
    lower = np.array([p.h_low, p.s_low, p.v_low], dtype=np.uint8)
    upper = np.array([p.h_high, p.s_high, p.v_high], dtype=np.uint8)
    green_mask = cv2.inRange(hsv, lower, upper)          # 绿幕区域=255
    mask = cv2.bitwise_not(green_mask)                    # 前景=255, 绿幕=0

    # 形态学：收缩(正值)去除边缘绿溢；扩张(负值)恢复细节
    if p.mask_shrink != 0:
        ksize = 3
        kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (ksize, ksize))
        if p.mask_shrink > 0:
            mask = cv2.erode(mask, kernel, iterations=int(p.mask_shrink))
        else:
            mask = cv2.dilate(mask, kernel, iterations=int(-p.mask_shrink))

    # 高斯羽化：让小狗毛发边缘柔和过渡
    if p.feather_radius > 0:
        k = _odd_kernel(p.feather_radius)
        mask = cv2.GaussianBlur(mask, (k, k), p.feather_radius)

    # 边缘平滑抗锯齿：再一次轻量模糊
    if p.edge_smooth > 0:
        k = _odd_kernel(p.edge_smooth)
        mask = cv2.GaussianBlur(mask, (k, k), p.edge_smooth)

    return mask


def apply_keying(frame_bgr: np.ndarray,
                 p: KeyingParams) -> np.ndarray:
    """完整抠图流程：先裁切，再做 HSV 抠图与蒙版处理，返回 RGBA 或蒙版预览。"""
    cropped = crop_frame(frame_bgr, p.crop_rect)
    if cropped.size == 0:
        # 裁切区域无效，返回 1x1 透明占位
        return np.zeros((1, 1, 4), dtype=np.uint8)

    hsv = cv2.cvtColor(cropped, cv2.COLOR_BGR2HSV)
    mask = compute_mask(hsv, p)

    if p.show_mask_only:
        # 单独查看黑白 Alpha 蒙版
        return cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)

    rgba = cv2.cvtColor(cropped, cv2.COLOR_BGR2BGRA)
    rgba[:, :, 3] = mask
    return rgba


def eyedropper_pick(bgr_pixel: np.ndarray,
                    cur: KeyingParams) -> KeyingParams:
    """吸管取色：根据点击的绿色像素自动生成 HSV 参数范围。"""
    pix = np.uint8([[bgr_pixel]])  # (1,1,3)
    hsv = cv2.cvtColor(pix, cv2.COLOR_BGR2HSV)[0, 0]
    h, s, v = int(hsv[0]), int(hsv[1]), int(hsv[2])

    # H 范围 ±18（约 ±36 度），覆盖不同绿幕深浅
    h_low = max(0, h - 18)
    h_high = min(179, h + 18)
    # S/V 下限放宽，上限拉满
    s_low = max(0, int(s * 0.45))
    s_high = 255
    v_low = max(0, int(v * 0.45))
    v_high = 255

    new = KeyingParams.from_dict(cur.to_dict())
    new.h_low, new.h_high = h_low, h_high
    new.s_low, new.s_high = s_low, s_high
    new.v_low, new.v_high = v_low, v_high
    return new


def save_png(rgba: np.ndarray, path: str) -> bool:
    """将 RGBA 图像保存为透明 PNG。兼容含中文/特殊字符的路径。"""
    try:
        ok = cv2.imwrite(path, rgba,
                         [cv2.IMWRITE_PNG_COMPRESSION, 6])
        if ok:
            return True
    except Exception:
        pass

    # Fallback：OpenCV imwrite 在 Windows 非 ASCII 路径上可能失败，
    # 用 imencode 生成 PNG 字节流后通过 Python IO 写入，可正确处理 Unicode。
    try:
        ok, buf = cv2.imencode(
            ".png", rgba, [cv2.IMWRITE_PNG_COMPRESSION, 6])
        if not ok:
            return False
        with open(path, "wb") as f:
            f.write(buf.tobytes())
        return True
    except Exception:
        return False


def process_for_export(frame_bgr: np.ndarray,
                       p: KeyingParams) -> np.ndarray:
    """导出用处理：裁切 + 抠图，返回 RGBA。"""
    return apply_keying(frame_bgr, p)


# ---------- 预设持久化 ----------

def save_preset(path: str, p: KeyingParams) -> bool:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(p.to_dict(), f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def load_preset(path: str) -> Optional[KeyingParams]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        return KeyingParams.from_dict(d)
    except Exception:
        return None

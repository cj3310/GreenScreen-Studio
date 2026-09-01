"""
画布组件 — 棋盘格预览画布 + 交互式裁切选框 + 吸管取色。

提供左右双画布所需的显示与交互能力。
"""

import numpy as np
import cv2
from PySide6.QtCore import Qt, QPoint, QRect, Signal
from PySide6.QtGui import (QPixmap, QImage, QPainter, QPen, QColor,
                           QBrush, QCursor)
from PySide6.QtWidgets import QWidget


# ------------------------------------------------------------------
#  工具函数
# ------------------------------------------------------------------

def make_checkerboard(w: int, h: int, tile: int = 12) -> QPixmap:
    """生成棋盘格背景，用于直观查看透明通道。"""
    pm = QPixmap(w, h)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    c1 = QColor(235, 235, 235)
    c2 = QColor(200, 200, 200)
    painter.setPen(Qt.NoPen)
    for y in range(0, h, tile):
        for x in range(0, w, tile):
            painter.setBrush(QBrush(c1 if ((x // tile + y // tile) % 2 == 0) else c2))
            painter.drawRect(x, y, tile, tile)
    painter.end()
    return pm


def bgr_to_qpixmap(bgr: np.ndarray) -> QPixmap:
    """BGR numpy 数组 → QPixmap。"""
    if bgr is None or bgr.size == 0:
        return QPixmap()
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    rgb = np.ascontiguousarray(rgb)
    h, w = rgb.shape[:2]
    qimg = QImage(rgb.data, w, h, w * 3, QImage.Format_RGB888)
    return QPixmap.fromImage(qimg)


def rgba_to_qpixmap(rgba: np.ndarray) -> QPixmap:
    """RGBA numpy 数组 → QPixmap（保留 alpha）。"""
    if rgba is None or rgba.size == 0:
        return QPixmap()
    # OpenCV BGRA → Qt RGBA8888
    rgba_q = cv2.cvtColor(rgba, cv2.COLOR_BGRA2RGBA)
    rgba_q = np.ascontiguousarray(rgba_q)
    h, w = rgba_q.shape[:2]
    qimg = QImage(rgba_q.data, w, h, w * 4, QImage.Format_RGBA8888)
    return QPixmap.fromImage(qimg)


def mask_to_qpixmap(mask_gray: np.ndarray) -> QPixmap:
    """灰度蒙版 → QPixmap。"""
    if mask_gray is None or mask_gray.size == 0:
        return QPixmap()
    gray = np.ascontiguousarray(mask_gray)
    h, w = gray.shape[:2]
    qimg = QImage(gray.data, w, h, w, QImage.Format_Grayscale8)
    return QPixmap.fromImage(qimg)


# ------------------------------------------------------------------
#  预览画布
# ------------------------------------------------------------------

class PreviewCanvas(QWidget):
    """可显示帧画面、支持裁切框选与吸管取色的画布。

    左侧原图画布启用裁切与吸管；右侧抠图画布仅显示。
    """

    # 裁切矩形变更（图像坐标系 QRect 或 None）
    crop_changed = Signal(object)
    # 吸管取色，发射 BGR 像素 numpy 数组
    pixel_picked = Signal(object)

    HANDLE_SIZE = 10  # 控制点尺寸（像素）

    def __init__(self, title: str = "",
                 enable_crop: bool = True,
                 enable_eyedropper: bool = True,
                 parent=None):
        super().__init__(parent)
        self._title = title
        self._enable_crop = enable_crop
        self._enable_eyedropper = enable_eyedropper

        self._pixmap = QPixmap()        # 当前显示画面
        self._frame_bgr = None          # 原始 BGR 帧（供吸管取色）
        self._image_size = (0, 0)       # (w, h) 原始尺寸

        # 交互模式: "view" / "crop" / "eyedropper"
        self._mode = "view"

        # 裁切矩形（图像坐标系）
        self._crop_rect = None           # QRect 或 None
        self._drag_action = None        # "create"/"move"/"resize-xx"
        self._drag_start = None         # QPoint (widget 坐标)
        self._drag_orig_rect = None      # 拖拽前的 rect（图像坐标）

        self.setMinimumSize(320, 240)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)

    # ---------- 公开接口 ----------

    def set_frame_bgr(self, bgr: np.ndarray):
        """设置原图帧（左侧画布用）。"""
        self._frame_bgr = bgr
        self._pixmap = bgr_to_qpixmap(bgr)
        if not self._pixmap.isNull():
            self._image_size = (self._pixmap.width(), self._pixmap.height())
        self.update()

    def set_display_pixmap(self, pm: QPixmap, bgr_for_pick=None):
        """直接设置显示画面（右侧画布用）。"""
        self._pixmap = pm
        if bgr_for_pick is not None:
            self._frame_bgr = bgr_for_pick
        if not pm.isNull():
            self._image_size = (pm.width(), pm.height())
        self.update()

    def set_mode(self, mode: str):
        self._mode = mode
        if mode == "eyedropper":
            self.setCursor(Qt.CrossCursor)
        elif mode == "crop":
            self.setCursor(Qt.CrossCursor)
        else:
            self.setCursor(Qt.ArrowCursor)
        self.update()

    def get_crop_rect(self):
        return self._crop_rect

    def set_crop_rect(self, rect):
        self._crop_rect = rect
        self.update()

    def clear_crop(self):
        self._crop_rect = None
        self.crop_changed.emit(None)
        self.update()

    # ---------- 坐标映射 ----------

    def _display_rect(self) -> QRect:
        """计算画面在 widget 中的显示矩形（保持比例居中）。"""
        if self._pixmap.isNull():
            return QRect()
        iw, ih = self._image_size
        ww, wh = self.width(), self.height()
        if iw == 0 or ih == 0:
            return QRect()
        scale = min(ww / iw, wh / ih)
        dw = max(1, int(iw * scale))
        dh = max(1, int(ih * scale))
        dx = (ww - dw) // 2
        dy = (wh - dh) // 2
        return QRect(dx, dy, dw, dh)

    def _scale(self) -> float:
        r = self._display_rect()
        if r.width() == 0 or self._image_size[0] == 0:
            return 1.0
        return r.width() / self._image_size[0]

    def _widget_to_image(self, p: QPoint):
        r = self._display_rect()
        s = self._scale()
        if s == 0:
            return None
        ix = (p.x() - r.x()) / s
        iy = (p.y() - r.y()) / s
        iw, ih = self._image_size
        if ix < 0 or iy < 0 or ix >= iw or iy >= ih:
            return None
        return (int(ix), int(iy))

    def _image_to_widget(self, rect_img: QRect) -> QRect:
        r = self._display_rect()
        s = self._scale()
        return QRect(int(r.x() + rect_img.x() * s),
                     int(r.y() + rect_img.y() * s),
                     int(rect_img.width() * s),
                     int(rect_img.height() * s))

    # ---------- 控制点命中测试 ----------

    def _handle_rects(self, wrect: QRect):
        """返回 8 个控制点的 widget 坐标矩形。"""
        hs = self.HANDLE_SIZE
        x, y, w, h = wrect.x(), wrect.y(), wrect.width(), wrect.height()
        names = ["tl", "tm", "tr", "ml", "mr", "bl", "bm", "br"]
        pts = {
            "tl": QPoint(x, y), "tm": QPoint(x + w // 2, y),
            "tr": QPoint(x + w, y), "ml": QPoint(x, y + h // 2),
            "mr": QPoint(x + w, y + h // 2), "bl": QPoint(x, y + h),
            "bm": QPoint(x + w // 2, y + h), "br": QPoint(x + w, y + h),
        }
        return {n: QRect(p.x() - hs, p.y() - hs, hs * 2, hs * 2)
                for n, p in pts.items()}

    def _hit_test(self, pos: QPoint):
        """返回 "resize-<name>" / "move" / None。"""
        if self._crop_rect is None:
            return None
        wrect = self._image_to_widget(self._crop_rect)
        for name, hr in self._handle_rects(wrect).items():
            if hr.contains(pos):
                return "resize-" + name
        if wrect.contains(pos):
            return "move"
        return None

    # ---------- 鼠标事件 ----------

    def mousePressEvent(self, ev):
        if ev.button() != Qt.LeftButton:
            return
        pos = ev.position().toPoint()

        if self._mode == "eyedropper":
            self._do_eyedropper(pos)
            return

        if self._mode != "crop":
            return

        hit = self._hit_test(pos)
        if hit == "move":
            self._drag_action = "move"
            self._drag_start = pos
            self._drag_orig_rect = QRect(self._crop_rect)
        elif hit and hit.startswith("resize"):
            self._drag_action = hit
            self._drag_start = pos
            self._drag_orig_rect = QRect(self._crop_rect)
        else:
            # 新建选区
            img = self._widget_to_image(pos)
            if img is None:
                return
            self._drag_action = "create"
            self._drag_start = pos
            self._crop_rect = QRect(img[0], img[1], 1, 1)
            self._drag_orig_rect = QRect(self._crop_rect)

    def mouseMoveEvent(self, ev):
        pos = ev.position().toPoint()

        # 鼠标悬停时光标提示
        if self._drag_action is None and self._mode == "crop":
            hit = self._hit_test(pos)
            if hit == "move":
                self.setCursor(Qt.SizeAllCursor)
            elif hit:
                self.setCursor(self._handle_cursor(hit))
            else:
                self.setCursor(Qt.CrossCursor)

        if self._drag_action is None:
            return

        if self._drag_action == "create":
            img = self._widget_to_image(pos)
            if img is None:
                return
            s = self._widget_to_image(self._drag_start)
            if s is None:
                return
            x0, y0 = s
            x1, y1 = img
            r = QRect(min(x0, x1), min(y0, y1),
                      abs(x1 - x0) + 1, abs(y1 - y0) + 1)
            self._crop_rect = r
            self.update()
        elif self._drag_action == "move":
            s = self._scale()
            dx = int((pos.x() - self._drag_start.x()) / s)
            dy = int((pos.y() - self._drag_start.y()) / s)
            r = QRect(self._drag_orig_rect)
            r.translate(dx, dy)
            iw, ih = self._image_size
            # 约束在画面内
            r.setX(max(0, min(r.x(), iw - r.width())))
            r.setY(max(0, min(r.y(), ih - r.height())))
            self._crop_rect = r
            self.update()
        elif self._drag_action.startswith("resize"):
            self._do_resize(pos)

    def mouseReleaseEvent(self, ev):
        if self._drag_action is None:
            return
        # 规范化选区（宽高 >= 1）
        if self._crop_rect is not None:
            r = self._crop_rect.normalized()
            if r.width() < 2 or r.height() < 2:
                self._crop_rect = None
            else:
                self._crop_rect = r
        self._drag_action = None
        self.crop_changed.emit(self._crop_rect)
        self.update()

    def _do_resize(self, pos: QPoint):
        s = self._scale()
        dx = int((pos.x() - self._drag_start.x()) / s)
        dy = int((pos.y() - self._drag_start.y()) / s)
        r = QRect(self._drag_orig_rect)
        name = self._drag_action.split("-")[1]
        if "l" in name:
            r.setLeft(r.left() + dx)
        if "r" in name:
            r.setRight(r.right() + dx)
        if "t" in name:
            r.setTop(r.top() + dy)
        if "b" in name:
            r.setBottom(r.bottom() + dy)
        r = r.normalized()
        iw, ih = self._image_size
        r = r.intersected(QRect(0, 0, iw, ih))
        if r.width() >= 1 and r.height() >= 1:
            self._crop_rect = r
            self.update()

    def _do_eyedropper(self, pos: QPoint):
        img = self._widget_to_image(pos)
        if img is None or self._frame_bgr is None:
            return
        ix, iy = img
        px = self._frame_bgr[iy, ix]
        self.pixel_picked.emit(np.array(px))

    def _handle_cursor(self, hit: str):
        name = hit.split("-")[1]
        cursors = {
            "tl": Qt.SizeFDiagCursor, "br": Qt.SizeFDiagCursor,
            "tr": Qt.SizeBDiagCursor, "bl": Qt.SizeBDiagCursor,
            "tm": Qt.SizeVerCursor, "bm": Qt.SizeVerCursor,
            "ml": Qt.SizeHorCursor, "mr": Qt.SizeHorCursor,
        }
        return cursors.get(name, Qt.ArrowCursor)

    # ---------- 绘制 ----------

    def paintEvent(self, ev):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(30, 30, 30))

        dr = self._display_rect()
        if dr.isNull() or self._pixmap.isNull():
            painter.setPen(QColor(160, 160, 160))
            painter.drawText(self.rect(), Qt.AlignCenter,
                             "暂无画面" if not self._title else self._title)
            painter.end()
            return

        # 棋盘格底板
        cb = make_checkerboard(dr.width(), dr.height())
        painter.drawPixmap(dr, cb)
        # 画面
        painter.drawPixmap(dr, self._pixmap)

        # 标题
        if self._title:
            painter.setPen(QColor(255, 255, 255))
            painter.fillRect(0, 0, self.width(), 22, QColor(0, 0, 0, 140))
            painter.drawText(QRect(8, 0, self.width() - 16, 22),
                             Qt.AlignVCenter | Qt.AlignLeft, self._title)

        # 裁切框
        if self._mode == "crop" and self._crop_rect is not None:
            wrect = self._image_to_widget(self._crop_rect)
            # 遮罩半透明
            painter.setBrush(QColor(0, 0, 0, 100))
            painter.setPen(Qt.NoPen)
            # 上下左右四块
            painter.drawRect(QRect(dr.x(), dr.y(), dr.width(),
                                  wrect.y() - dr.y()))
            painter.drawRect(QRect(dr.x(), wrect.bottom() + 1,
                                  dr.width(), dr.bottom() - wrect.bottom()))
            painter.drawRect(QRect(dr.x(), wrect.y(),
                                  wrect.x() - dr.x(), wrect.height()))
            painter.drawRect(QRect(wrect.right() + 1, wrect.y(),
                                  dr.right() - wrect.right(),
                                  wrect.height()))
            # 选框边线
            pen = QPen(QColor(0, 200, 255), 2)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(wrect)
            # 控制点
            painter.setBrush(QColor(0, 200, 255))
            for hr in self._handle_rects(wrect).values():
                painter.drawRect(hr)

        painter.end()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self.update()

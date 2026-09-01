"""
绿幕抠图工作台 — 主窗口。

整合拖拽导入、双画布预览、抠图参数、时间轴、批量导出。
"""

import os
import cv2
import numpy as np

from PySide6.QtCore import (Qt, QObject, QThread, Signal, QTimer, QUrl,
                            QMimeData)
from PySide6.QtGui import (QDragEnterEvent, QDropEvent, QShortcut,
                            QKeySequence, QPixmap, QIcon)
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                                QSplitter, QStackedWidget, QLabel, QPushButton,
                                QMessageBox, QScrollArea, QFileDialog,
                                QSizePolicy, QApplication)

from .keying_engine import (KeyingParams, DEFAULT_PRESET, apply_keying,
                              eyedropper_pick, save_png, process_for_export,
                              crop_frame)
from .canvas_widgets import (PreviewCanvas, bgr_to_qpixmap,
                              rgba_to_qpixmap, mask_to_qpixmap)
from .control_panels import KeyingPanel, TimelineBar, ExportPanel


VIDEO_EXTS = (".mp4", ".mov", ".avi", ".mkv", ".m4v", ".webm")


# ------------------------------------------------------------------
#  拖拽导入区
# ------------------------------------------------------------------

class DropZone(QWidget):
    """拖拽导入区域：提示文字 + 文件选择按钮。"""

    file_dropped = Signal(str)
    choose_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setMinimumSize(480, 300)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def paintEvent(self, ev):
        from PySide6.QtGui import QPainter, QColor, QPen, QFont
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(45, 47, 54))
        # 虚线边框
        pen = QPen(QColor(120, 140, 200), 2, Qt.DashLine)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(self.rect().adjusted(20, 20, -20, -20), 16, 16)

        f = QFont()
        f.setPointSize(20)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(220, 225, 240))
        p.drawText(self.rect(), Qt.AlignCenter,
                   "🎬  将绿幕视频拖拽至此\n\n支持 mp4 / mov 等格式\n或点击下方按钮选择文件")
        p.end()

    def dragEnterEvent(self, ev: QDragEnterEvent):
        if ev.mimeData().hasUrls():
            urls = ev.mimeData().urls()
            for u in urls:
                path = u.toLocalFile()
                if path.lower().endswith(VIDEO_EXTS):
                    ev.acceptProposedAction()
                    return
        ev.ignore()

    def dragMoveEvent(self, ev):
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()
        else:
            ev.ignore()

    def dropEvent(self, ev: QDropEvent):
        for u in ev.mimeData().urls():
            path = u.toLocalFile()
            if path.lower().endswith(VIDEO_EXTS):
                self.file_dropped.emit(path)
                ev.acceptProposedAction()
                return
        # 非视频文件
        QMessageBox.warning(self, "文件格式不支持",
                            "请拖入 mp4 / mov 等视频文件。")
        ev.ignore()

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self.choose_clicked.emit()


# ------------------------------------------------------------------
#  导出工作线程
# ------------------------------------------------------------------

class ExportWorker(QObject):
    """后台批量导出线程：裁切 + 抠图 + 透明 PNG。"""

    progress = Signal(int, int, int)   # done, total, current_frame
    finished_ok = Signal(int)           # 已导出帧数
    failed = Signal(str)               # 错误信息

    def __init__(self, video_path, params: KeyingParams,
                 output_dir, frame_interval):
        super().__init__()
        self._video_path = video_path
        self._params = params
        self._output_dir = output_dir
        self._interval = max(1, int(frame_interval))
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            cap = cv2.VideoCapture(self._video_path)
            if not cap.isOpened():
                self.failed.emit("无法打开视频文件，可能已损坏。")
                return
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            if total <= 0:
                self.failed.emit("无法读取视频帧数信息。")
                cap.release()
                return

            os.makedirs(self._output_dir, exist_ok=True)

            done = 0
            out_idx = 0
            frame_no = 0
            while True:
                if self._cancel:
                    break
                ok, frame = cap.read()
                if not ok:
                    break
                if frame_no % self._interval == 0:
                    rgba = process_for_export(frame, self._params)
                    name = f"frame_{out_idx + 1:06d}.png"
                    path = os.path.join(self._output_dir, name)
                    if not save_png(rgba, path):
                        self.failed.emit(f"保存失败：{path}")
                        cap.release()
                        return
                    out_idx += 1
                    done += 1
                    self.progress.emit(done, total, frame_no)
                frame_no += 1
            cap.release()
            self.finished_ok.emit(out_idx)
        except Exception as e:
            self.failed.emit(f"导出过程出错：{e}")


# ------------------------------------------------------------------
#  主窗口
# ------------------------------------------------------------------

class GreenScreenStudio(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("绿幕抠图工作台 · GreenScreen Studio")
        self.resize(1280, 820)
        self.setAcceptDrops(True)

        # 视频状态
        self._cap = None
        self._video_path = None
        self._total_frames = 0
        self._fps = 30.0
        self._frame_w = 0
        self._frame_h = 0
        self._current_frame_no = 0
        self._current_bgr = None     # 当前帧 numpy

        # 抠图参数（权威）
        self._params = KeyingParams()
        self._playing = False

        # 播放定时器
        self._play_timer = QTimer(self)
        self._play_timer.setTimerType(Qt.PreciseTimer)
        self._play_timer.timeout.connect(self._play_tick)

        # 导出线程
        self._export_thread = None
        self._export_worker = None

        self._build_ui()
        self._connect_signals()
        self._setup_shortcuts()

    # ---------------- 界面构建 ----------------

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        outer = QHBoxLayout(central)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(6)

        splitter = QSplitter(Qt.Horizontal)
        outer.addWidget(splitter, 1)

        # ---- 左侧：画布 + 时间轴 ----
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(6)

        # 工具栏
        toolbar = QHBoxLayout()
        self.btn_open = QPushButton("📁 打开视频")
        self.btn_open.setMinimumHeight(32)
        self.lbl_file = QLabel("未加载视频")
        self.lbl_file.setStyleSheet("color:#aab;padding:0 8px;")
        toolbar.addWidget(self.btn_open)
        toolbar.addWidget(self.lbl_file, 1)
        ll.addLayout(toolbar)

        # 画布堆叠区
        self.stack = QStackedWidget()
        self.drop_zone = DropZone()
        self.stack.addWidget(self.drop_zone)   # index 0

        canvas_wrap = QWidget()
        cl = QHBoxLayout(canvas_wrap)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(6)
        self.canvas_orig = PreviewCanvas("原图预览",
                                         enable_crop=True,
                                         enable_eyedropper=True)
        self.canvas_keyed = PreviewCanvas("抠图预览",
                                          enable_crop=False,
                                          enable_eyedropper=False)
        cl.addWidget(self.canvas_orig, 1)
        cl.addWidget(self.canvas_keyed, 1)
        self.stack.addWidget(canvas_wrap)       # index 1
        self.stack.setCurrentIndex(0)
        ll.addWidget(self.stack, 1)

        # 时间轴
        self.timeline = TimelineBar()
        ll.addWidget(self.timeline)

        splitter.addWidget(left)

        # ---- 右侧：参数 + 导出 ----
        right = QWidget()
        right.setMinimumWidth(340)
        right.setMaximumWidth(460)
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(6)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_inner = QWidget()
        sil = QVBoxLayout(scroll_inner)
        sil.setContentsMargins(0, 0, 0, 0)
        self.keying_panel = KeyingPanel()
        sil.addWidget(self.keying_panel)
        scroll.setWidget(scroll_inner)
        rl.addWidget(scroll, 1)

        self.export_panel = ExportPanel()
        rl.addWidget(self.export_panel)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)

        # 状态栏
        self.statusBar().showMessage("就绪 — 请拖入或选择绿幕视频")

    # ---------------- 信号连接 ----------------

    def _connect_signals(self):
        self.btn_open.clicked.connect(self._choose_file)
        self.drop_zone.choose_clicked.connect(self._choose_file)
        self.drop_zone.file_dropped.connect(self.load_video)

        # 抠图参数
        self.keying_panel.params_changed.connect(self._on_params_changed)
        self.keying_panel.eyedropper_toggled.connect(self._on_eyedropper)
        self.keying_panel.crop_mode_toggled.connect(self._on_crop_mode)
        self.keying_panel.reset_crop.connect(self._on_reset_crop)

        # 画布交互
        self.canvas_orig.crop_changed.connect(self._on_crop_changed)
        self.canvas_orig.pixel_picked.connect(self._on_pixel_picked)

        # 时间轴
        self.timeline.position_changed.connect(self._seek)
        self.timeline.play_toggled.connect(self._on_play_toggled)
        self.timeline.step_frame.connect(self._step_frame)

        # 导出
        self.export_panel.export_requested.connect(self._start_export)
        self.export_panel.cancel_requested.connect(self._cancel_export)

    def _setup_shortcuts(self):
        QShortcut(QKeySequence(Qt.Key_Space), self,
                  activated=self._toggle_play_shortcut)
        QShortcut(QKeySequence(Qt.Key_Left), self,
                  activated=lambda: self._step_frame(-1))
        QShortcut(QKeySequence(Qt.Key_Right), self,
                  activated=lambda: self._step_frame(1))

    # ---------------- 视频载入 ----------------

    def _choose_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择绿幕视频", "",
            "视频文件 (*.mp4 *.mov *.avi *.mkv *.m4v *.webm)")
        if path:
            self.load_video(path)

    def load_video(self, path: str):
        if not os.path.exists(path):
            QMessageBox.critical(self, "文件不存在", f"找不到文件：\n{path}")
            return
        # 释放旧 capture
        if self._cap is not None:
            self._cap.release()
            self._cap = None
        try:
            cap = cv2.VideoCapture(path)
        except Exception as e:
            QMessageBox.critical(self, "视频打开失败", f"读取视频出错：\n{e}")
            return
        if not cap.isOpened():
            QMessageBox.critical(self, "视频打开失败",
                                 "无法打开视频，文件可能已损坏或格式不支持。")
            return

        self._cap = cap
        self._video_path = path
        self._total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self._fps = float(cap.get(cv2.CAP_PROP_FPS)) or 30.0
        self._frame_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))

        self._frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        self.lbl_file.setText(os.path.basename(path))
        self.statusBar().showMessage(
            f"已加载：{os.path.basename(path)}  |  "
            f"{self._frame_w}×{self._frame_h}  |  "
            f"{self._total_frames} 帧  |  {self._fps:.1f} fps")

        self.timeline.set_total_frames(self._total_frames)
        self._current_frame_no = 0
        self._read_frame_at(0)
        self.stack.setCurrentIndex(1)

        # 重置裁切
        self.canvas_orig.clear_crop()
        self._params.crop_rect = None

    # ---------------- 帧读取 ----------------

    def _read_frame_at(self, frame_no: int):
        if self._cap is None:
            return
        frame_no = max(0, min(frame_no, self._total_frames - 1))
        self._cap.set(cv2.CAP_PROP_POS_FRAMES, frame_no)
        ok, frame = self._cap.read()
        if not ok:
            QMessageBox.warning(self, "读取失败",
                                f"无法读取第 {frame_no} 帧，视频可能损坏。")
            return
        self._current_frame_no = frame_no
        self._current_bgr = frame
        self.timeline.update_position(frame_no)
        self._refresh_canvases()

    def _seek(self, frame_no: int):
        if self._playing:
            self.timeline.set_playing(False)
            self._stop_playback()
        self._read_frame_at(frame_no)

    def _step_frame(self, delta: int):
        if self._cap is None:
            return
        if self._playing:
            self.timeline.set_playing(False)
            self._stop_playback()
        self._read_frame_at(self._current_frame_no + delta)

    # ---------------- 画布刷新 ----------------

    def _refresh_canvases(self):
        if self._current_bgr is None:
            return
        # 原图画布：显示完整帧（裁切框作为覆盖层）
        self.canvas_orig.set_frame_bgr(self._current_bgr)
        # 抠图画布：裁切 + 抠图结果
        result = apply_keying(self._current_bgr, self._params)
        if result.ndim == 3 and result.shape[2] == 4:
            pm = rgba_to_qpixmap(result)
        elif result.ndim == 3 and result.shape[2] == 3:
            pm = bgr_to_qpixmap(result)
        else:
            pm = QPixmap()
        self.canvas_keyed.set_display_pixmap(pm)

    # ---------------- 参数变更 ----------------

    def _on_params_changed(self, p: KeyingParams):
        # 保留裁切矩形（面板不管理 crop_rect）
        p.crop_rect = self._params.crop_rect
        self._params = p
        self._refresh_canvases()

    # ---------------- 裁切 ----------------

    def _on_crop_mode(self, active: bool):
        if active:
            # 退出吸管模式
            self.keying_panel.set_eyedropper_active(False)
            self.canvas_orig.set_mode("crop")
        else:
            if self.canvas_orig._mode == "crop":
                self.canvas_orig.set_mode("view")

    def _on_crop_changed(self, rect):
        if rect is None:
            self._params.crop_rect = None
        else:
            self._params.crop_rect = (rect.x(), rect.y(),
                                      rect.width(), rect.height())
        self.canvas_orig.update()
        self._refresh_canvases()

    def _on_reset_crop(self):
        self.canvas_orig.clear_crop()
        self._params.crop_rect = None
        self._refresh_canvases()

    # ---------------- 吸管 ----------------

    def _on_eyedropper(self, active: bool):
        if active:
            self.keying_panel.set_crop_mode_active(False)
            self.canvas_orig.set_mode("eyedropper")
        else:
            if self.canvas_orig._mode == "eyedropper":
                self.canvas_orig.set_mode("view")

    def _on_pixel_picked(self, bgr_pixel: np.ndarray):
        new_params = eyedropper_pick(bgr_pixel, self._params)
        self._params = new_params
        self.keying_panel.set_eyedropper_active(False)
        self.canvas_orig.set_mode("view")
        # 更新滑块并刷新预览
        self.keying_panel.set_params(new_params, emit=True)

    # ---------------- 播放控制 ----------------

    def _toggle_play_shortcut(self):
        if self._cap is None:
            return
        self.timeline.btn_play.toggle()

    def _on_play_toggled(self, playing: bool):
        if playing:
            self._start_playback()
        else:
            self._stop_playback()

    def _start_playback(self):
        if self._cap is None:
            self.timeline.set_playing(False)
            return
        self._playing = True
        interval = int(1000 / self._fps) if self._fps > 0 else 33
        self._play_timer.start(interval)

    def _stop_playback(self):
        self._playing = False
        self._play_timer.stop()

    def _play_tick(self):
        if self._cap is None:
            self._stop_playback()
            self.timeline.set_playing(False)
            return
        ok, frame = self._cap.read()
        if not ok:
            # 到结尾
            self._stop_playback()
            self.timeline.set_playing(False)
            self._read_frame_at(0)
            return
        self._current_frame_no += 1
        self._current_bgr = frame
        self.timeline.update_position(self._current_frame_no)
        self._refresh_canvases()

    # ---------------- 导出 ----------------

    def _start_export(self, output_dir: str, interval: int):
        if self._cap is None:
            QMessageBox.warning(self, "未加载视频", "请先载入绿幕视频。")
            return
        if self._export_thread is not None and self._export_thread.isRunning():
            QMessageBox.information(self, "导出进行中", "已有导出任务正在运行。")
            return
        try:
            os.makedirs(output_dir, exist_ok=True)
        except Exception as e:
            QMessageBox.critical(self, "文件夹创建失败",
                                 f"无法创建输出文件夹：\n{e}")
            return

        self.export_panel.set_exporting(True)
        self.export_panel.set_status("准备导出…")

        self._export_thread = QThread()
        self._export_worker = ExportWorker(
            self._video_path, self._params, output_dir, interval)
        self._export_worker.moveToThread(self._export_thread)
        self._export_thread.started.connect(self._export_worker.run)
        self._export_worker.progress.connect(self._on_export_progress)
        self._export_worker.finished_ok.connect(self._on_export_done)
        self._export_worker.failed.connect(self._on_export_failed)
        # 清理
        self._export_worker.finished_ok.connect(self._export_thread.quit)
        self._export_worker.failed.connect(self._export_thread.quit)
        self._export_thread.start()

    def _on_export_progress(self, done, total, current_frame):
        self.export_panel.set_progress(done, total, current_frame)

    def _on_export_done(self, count):
        self.export_panel.set_exporting(False)
        self.export_panel.set_status(f"导出完成！共 {count} 张透明 PNG")
        QMessageBox.information(self, "导出完成",
                                f"批量导出完成！\n共导出 {count} 张透明 PNG。")

    def _on_export_failed(self, msg):
        self.export_panel.set_exporting(False)
        self.export_panel.set_status("导出失败", error=True)
        QMessageBox.critical(self, "导出失败", msg)

    def _cancel_export(self):
        if self._export_worker is not None:
            self._export_worker.cancel()

    # ---------------- 拖拽（窗口级） ----------------

    def dragEnterEvent(self, ev: QDragEnterEvent):
        if ev.mimeData().hasUrls():
            for u in ev.mimeData().urls():
                if u.toLocalFile().lower().endswith(VIDEO_EXTS):
                    ev.acceptProposedAction()
                    return
        ev.ignore()

    def dropEvent(self, ev: QDropEvent):
        for u in ev.mimeData().urls():
            path = u.toLocalFile()
            if path.lower().endswith(VIDEO_EXTS):
                self.load_video(path)
                ev.acceptProposedAction()
                return
        QMessageBox.warning(self, "文件格式不支持",
                            "请拖入 mp4 / mov 等视频文件。")
        ev.ignore()

    # ---------------- 关闭清理 ----------------

    def closeEvent(self, ev):
        self._stop_playback()
        if self._export_thread is not None and self._export_thread.isRunning():
            self._export_worker.cancel()
            self._export_thread.quit()
            self._export_thread.wait(3000)
        if self._cap is not None:
            self._cap.release()
        ev.accept()

"""
控制面板 — 抠图参数面板、时间轴控制、批量导出配置。

均为纯 UI 组件，通过信号与主窗口通信。
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
                                QGroupBox, QSlider, QLabel, QPushButton,
                                QCheckBox, QSpinBox, QLineEdit, QFileDialog,
                                QProgressBar, QSizePolicy, QFrame)
from PySide6.QtGui import QFont

from .keying_engine import KeyingParams, DEFAULT_PRESET


# ------------------------------------------------------------------
#  带标签滑块行
# ------------------------------------------------------------------

class LabeledSlider(QWidget):
    """标签 + 滑块 + 数值显示的复合控件。"""

    valueChanged = Signal(int)

    def __init__(self, label, vmin, vmax, vinit, divisor=1, suffix="",
                 parent=None):
        super().__init__(parent)
        self._divisor = divisor
        self._suffix = suffix
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._label = QLabel(label)
        self._label.setFixedWidth(96)
        self._slider = QSlider(Qt.Horizontal)
        self._slider.setRange(vmin, vmax)
        self._slider.setValue(vinit)
        self._value_lbl = QLabel()
        self._value_lbl.setFixedWidth(56)
        self._value_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        layout.addWidget(self._label)
        layout.addWidget(self._slider, 1)
        layout.addWidget(self._value_lbl)

        self._slider.valueChanged.connect(self._on_changed)
        self._update_text(vinit)

    def _on_changed(self, v):
        self._update_text(v)
        self.valueChanged.emit(v)

    def _update_text(self, v):
        real = v / self._divisor if self._divisor != 1 else v
        if self._divisor != 1:
            text = f"{real:.1f}{self._suffix}"
        else:
            text = f"{int(real)}{self._suffix}"
        self._value_lbl.setText(text)

    def value(self):
        return self._slider.value()

    def set_value(self, v):
        self._slider.setValue(int(v))

    def set_block(self, blocked):
        self._slider.blockSignals(blocked)


# ------------------------------------------------------------------
#  抠图参数面板
# ------------------------------------------------------------------

class KeyingPanel(QWidget):
    """HSV 阈值、羽化、蒙版收缩、边缘平滑、吸管、蒙版预览、重置、预设。"""

    params_changed = Signal(object)        # KeyingParams
    eyedropper_toggled = Signal(bool)      # 吸管开关
    mask_preview_toggled = Signal(bool)   # 蒙版预览开关
    crop_mode_toggled = Signal(bool)      # 裁切模式开关
    reset_crop = Signal()                  # 重置裁切

    def __init__(self, parent=None):
        super().__init__(parent)
        self._updating = False
        self._build_ui()
        self.set_params(DEFAULT_PRESET, emit=False)

    # ---------- 构建界面 ----------

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # 工具按钮行
        tool_row = QHBoxLayout()
        self.btn_crop = QPushButton("✂ 裁切模式")
        self.btn_crop.setCheckable(True)
        self.btn_pick = QPushButton("💧 吸管取色")
        self.btn_pick.setCheckable(True)
        self.btn_mask = QPushButton("▦ 蒙版预览")
        self.btn_mask.setCheckable(True)
        for b in (self.btn_crop, self.btn_pick, self.btn_mask):
            b.setMinimumHeight(34)
        tool_row.addWidget(self.btn_crop)
        tool_row.addWidget(self.btn_pick)
        tool_row.addWidget(self.btn_mask)
        root.addLayout(tool_row)

        self.btn_crop.toggled.connect(self.crop_mode_toggled.emit)
        self.btn_pick.toggled.connect(self.eyedropper_toggled.emit)
        self.btn_mask.toggled.connect(self._on_mask_toggled)

        # HSV 色度阈值组
        hsv_box = QGroupBox("HSV 色度阈值")
        hsv_form = QFormLayout(hsv_box)
        hsv_form.setSpacing(6)

        self.h_low = LabeledSlider("H 下限", 0, 179, 35, suffix="")
        self.h_high = LabeledSlider("H 上限", 0, 179, 85, suffix="")
        self.s_low = LabeledSlider("S 下限", 0, 255, 50, suffix="")
        self.s_high = LabeledSlider("S 上限", 0, 255, 255, suffix="")
        self.v_low = LabeledSlider("V 下限", 0, 255, 50, suffix="")
        self.v_high = LabeledSlider("V 上限", 0, 255, 255, suffix="")

        for w in [self.h_low, self.h_high, self.s_low, self.s_high,
                  self.v_low, self.v_high]:
            w.valueChanged.connect(self._rebuild_and_emit)
            hsv_form.addRow(w)
        root.addWidget(hsv_box)

        # 蒙版处理组
        mask_box = QGroupBox("蒙版与边缘处理")
        mask_form = QFormLayout(mask_box)
        mask_form.setSpacing(6)

        self.feather = LabeledSlider("高斯羽化半径", 0, 200, 20,
                                      divisor=10, suffix="px")
        self.shrink = LabeledSlider("蒙版收缩/扩张", -15, 15, 0, suffix="")
        self.smooth = LabeledSlider("边缘平滑抗锯齿", 0, 100, 10,
                                     divisor=10, suffix="px")

        for w in [self.feather, self.shrink, self.smooth]:
            w.valueChanged.connect(self._rebuild_and_emit)
            mask_form.addRow(w)
        root.addWidget(mask_box)

        # 重置 & 预设
        preset_row = QHBoxLayout()
        self.btn_reset = QPushButton("↺ 参数重置")
        self.btn_reset.setMinimumHeight(34)
        self.btn_reset_crop = QPushButton("裁切重置")
        self.btn_reset_crop.setMinimumHeight(34)
        self.btn_save_preset = QPushButton("保存预设")
        self.btn_load_preset = QPushButton("加载预设")
        preset_row.addWidget(self.btn_reset)
        preset_row.addWidget(self.btn_reset_crop)
        root.addLayout(preset_row)

        preset_row2 = QHBoxLayout()
        preset_row2.addWidget(self.btn_save_preset)
        preset_row2.addWidget(self.btn_load_preset)
        root.addLayout(preset_row2)

        self.btn_reset.clicked.connect(self._on_reset)
        self.btn_reset_crop.clicked.connect(self.reset_crop.emit)
        self.btn_save_preset.clicked.connect(self._on_save_preset)
        self.btn_load_preset.clicked.connect(self._on_load_preset)

        root.addStretch(1)

    # ---------- 参数同步 ----------

    def _rebuild_and_emit(self):
        if self._updating:
            return
        p = self.get_params()
        self.params_changed.emit(p)

    def get_params(self) -> KeyingParams:
        p = KeyingParams()
        p.h_low = self.h_low.value()
        p.h_high = self.h_high.value()
        p.s_low = self.s_low.value()
        p.s_high = self.s_high.value()
        p.v_low = self.v_low.value()
        p.v_high = self.v_high.value()
        p.feather_radius = self.feather.value() / 10.0
        p.mask_shrink = self.shrink.value()
        p.edge_smooth = self.smooth.value() / 10.0
        p.show_mask_only = self.btn_mask.isChecked()
        return p

    def set_params(self, p: KeyingParams, emit: bool = True):
        """从 KeyingParams 更新所有滑块（用于吸管、预设加载）。"""
        self._updating = True
        self.h_low.set_value(p.h_low)
        self.h_high.set_value(p.h_high)
        self.s_low.set_value(p.s_low)
        self.s_high.set_value(p.s_high)
        self.v_low.set_value(p.v_low)
        self.v_high.set_value(p.v_high)
        self.feather.set_value(int(p.feather_radius * 10))
        self.shrink.set_value(int(p.mask_shrink))
        self.smooth.set_value(int(p.edge_smooth * 10))
        self.btn_mask.setChecked(p.show_mask_only)
        self._updating = False
        if emit:
            self.params_changed.emit(self.get_params())

    # ---------- 按钮回调 ----------

    def _on_reset(self):
        self.set_params(DEFAULT_PRESET, emit=True)

    def _on_mask_toggled(self, checked):
        self.mask_preview_toggled.emit(checked)
        self._rebuild_and_emit()

    def _on_save_preset(self):
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(
            self, "保存抠图预设", "keying_preset.json", "JSON (*.json)")
        if path:
            from .keying_engine import save_preset
            save_preset(path, self.get_params())

    def _on_load_preset(self):
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self, "加载抠图预设", "", "JSON (*.json)")
        if path:
            from .keying_engine import load_preset
            p = load_preset(path)
            if p is not None:
                self.set_params(p, emit=True)

    # 外部同步开关
    def set_eyedropper_active(self, active: bool):
        self.btn_pick.blockSignals(True)
        self.btn_pick.setChecked(active)
        self.btn_pick.blockSignals(False)

    def set_crop_mode_active(self, active: bool):
        self.btn_crop.blockSignals(True)
        self.btn_crop.setChecked(active)
        self.btn_crop.blockSignals(False)

    def set_mask_preview_active(self, active: bool):
        self.btn_mask.blockSignals(True)
        self.btn_mask.setChecked(active)
        self.btn_mask.blockSignals(False)


# ------------------------------------------------------------------
#  时间轴控制
# ------------------------------------------------------------------

class TimelineBar(QWidget):
    """帧时间轴：滑块跳转、逐帧进退、播放暂停。"""

    position_changed = Signal(int)   # 请求跳转到某帧
    play_toggled = Signal(bool)      # 播放/暂停
    step_frame = Signal(int)         # +1 / -1 帧

    def __init__(self, parent=None):
        super().__init__(parent)
        self._total = 0
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(4)

        ctrl = QHBoxLayout()
        self.btn_prev = QPushButton("◀")
        self.btn_play = QPushButton("▶ 播放")
        self.btn_play.setCheckable(True)
        self.btn_next = QPushButton("▶▶")
        for b in (self.btn_prev, self.btn_play, self.btn_next):
            b.setFixedWidth(80)
            b.setMinimumHeight(30)
        self.lbl_frame = QLabel("0 / 0")
        self.lbl_frame.setMinimumWidth(120)
        self.lbl_frame.setAlignment(Qt.AlignCenter)
        ctrl.addWidget(self.btn_prev)
        ctrl.addWidget(self.btn_play)
        ctrl.addWidget(self.btn_next)
        ctrl.addWidget(self.lbl_frame)
        layout.addLayout(ctrl)

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setEnabled(False)
        layout.addWidget(self.slider)

        self.slider.valueChanged.connect(self.position_changed.emit)
        self.btn_prev.clicked.connect(lambda: self.step_frame.emit(-1))
        self.btn_next.clicked.connect(lambda: self.step_frame.emit(1))
        self.btn_play.toggled.connect(self._on_play)

    def _on_play(self, checked):
        self.btn_play.setText("⏸ 暂停" if checked else "▶ 播放")
        self.play_toggled.emit(checked)

    def set_total_frames(self, total: int):
        self._total = max(0, total)
        self.slider.setEnabled(total > 0)
        self.slider.setRange(0, max(0, total - 1))

    def update_position(self, frame_no: int):
        self.slider.blockSignals(True)
        self.slider.setValue(frame_no)
        self.slider.blockSignals(False)
        self.lbl_frame.setText(f"{frame_no} / {self._total}")

    def set_playing(self, playing: bool):
        self.btn_play.blockSignals(True)
        self.btn_play.setChecked(playing)
        self.btn_play.blockSignals(False)
        self.btn_play.setText("⏸ 暂停" if playing else "▶ 播放")


# ------------------------------------------------------------------
#  批量导出面板
# ------------------------------------------------------------------

class ExportPanel(QWidget):
    """输出文件夹、抽帧间隔、导出按钮、进度条。"""

    export_requested = Signal(str, int)   # (output_dir, frame_interval)
    cancel_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        box = QGroupBox("批量导出透明 PNG")
        layout = QVBoxLayout(box)
        layout.setSpacing(6)

        # 输出目录
        dir_row = QHBoxLayout()
        self.edit_dir = QLineEdit()
        self.edit_dir.setPlaceholderText("选择输出文件夹…")
        self.btn_browse = QPushButton("浏览…")
        dir_row.addWidget(self.edit_dir, 1)
        dir_row.addWidget(self.btn_browse)
        layout.addLayout(dir_row)

        # 抽帧间隔
        int_row = QHBoxLayout()
        int_row.addWidget(QLabel("每隔 N 帧保存一张："))
        self.spin_interval = QSpinBox()
        self.spin_interval.setRange(1, 1000)
        self.spin_interval.setValue(1)
        int_row.addWidget(self.spin_interval)
        int_row.addStretch(1)
        layout.addLayout(int_row)

        # 导出按钮
        self.btn_export = QPushButton("⬇ 开始批量导出")
        self.btn_export.setMinimumHeight(38)
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.setMinimumHeight(38)
        self.btn_cancel.setEnabled(False)
        btn_row = QHBoxLayout()
        btn_row.addWidget(self.btn_export)
        btn_row.addWidget(self.btn_cancel)
        layout.addLayout(btn_row)

        # 进度
        self.progress = QProgressBar()
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        layout.addWidget(self.progress)

        self.lbl_status = QLabel("就绪")
        self.lbl_status.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.lbl_status)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(box)

        self.btn_browse.clicked.connect(self._browse)
        self.btn_export.clicked.connect(self._on_export)
        self.btn_cancel.clicked.connect(self.cancel_requested.emit)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "选择输出文件夹")
        if d:
            self.edit_dir.setText(d)

    def _on_export(self):
        out_dir = self.edit_dir.text().strip()
        if not out_dir:
            self.set_status("请先选择输出文件夹", error=True)
            return
        self.export_requested.emit(out_dir, self.spin_interval.value())

    def set_exporting(self, exporting: bool):
        self.btn_export.setEnabled(not exporting)
        self.btn_cancel.setEnabled(exporting)
        if not exporting:
            self.progress.setValue(0)

    def set_progress(self, done: int, total: int, current_frame: int):
        pct = int(done / total * 100) if total else 0
        self.progress.setValue(pct)
        self.lbl_status.setText(
            f"导出中… {done}/{total} 帧（当前 {current_frame}）")

    def set_status(self, text: str, error: bool = False):
        self.lbl_status.setText(text)
        c = "#c0392b" if error else "#27ae60"
        self.lbl_status.setStyleSheet(f"color:{c};")

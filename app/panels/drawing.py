"""Drawing tab: open pictures and PDF drawings, and choose where on the paper they go."""
from __future__ import annotations

from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QFormLayout, QGridLayout, QGroupBox,
                               QLabel, QPushButton, QVBoxLayout, QWidget)

from plotter.pipeline import AREA_PRESETS, drawing_area, preset_drawing_area


def spin() -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setDecimals(1)
    s.setSingleStep(1)
    s.setSuffix(" mm")
    return s


class DrawingPanel(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        lay = QVBoxLayout(self)

        job = QGroupBox("Drawing job")
        jl = QGridLayout(job)
        self.open_picture_btn = QPushButton("🖼 Open picture…")
        self.open_picture_btn.setToolTip("A photo, scan, sketch or logo (PNG, JPEG, BMP, TIFF, WebP). "
                                         "It is cleaned up and traced into pen lines.")
        self.open_picture_btn.clicked.connect(win.open_picture)
        self.adjust_btn = QPushButton("Adjust picture…")
        self.adjust_btn.clicked.connect(win.adjust_picture)
        self.open_pdf_btn = QPushButton("Open PDF drawing…")
        self.open_pdf_btn.clicked.connect(win.open_pdf)
        jl.addWidget(self.open_picture_btn, 0, 0)
        jl.addWidget(self.adjust_btn, 0, 1)
        jl.addWidget(self.open_pdf_btn, 1, 0, 1, 2)
        self.job_label = QLabel()
        self.job_label.setWordWrap(True)
        jl.addWidget(self.job_label, 2, 0, 1, 2)
        lay.addWidget(job)

        box = QGroupBox("Drawing area on the paper")
        form = QFormLayout(box)
        self.preset = QComboBox()
        for key, title in AREA_PRESETS.items():
            self.preset.addItem(title, key)
        self.preset.addItem("Custom", "custom")
        form.addRow("Area", self.preset)
        self.area_left, self.area_top, self.area_width, self.area_height = spin(), spin(), spin(), spin()
        form.addRow("From the left edge", self.area_left)
        form.addRow("From the top edge", self.area_top)
        form.addRow("Width", self.area_width)
        form.addRow("Height", self.area_height)
        hint = QLabel("The drawing is scaled to fit inside this area, keeping its proportions, and "
                      "centred in it. Edges are as seen from the front of the machine.\n\n"
                      "While this tab is open you can also set it on the preview: drag an edge or "
                      "corner of the orange area to resize it, drag inside it to move it, or drag "
                      "outside it to draw a new one.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #9aa3b2;")
        form.addRow(hint)
        lay.addWidget(box)
        lay.addStretch(1)

        self.load_from_profile()
        self.preset.currentIndexChanged.connect(self._preset_changed)
        for w in (self.area_left, self.area_top, self.area_width, self.area_height):
            w.valueChanged.connect(self._numbers_changed)

    def _widgets(self):
        return self.preset, self.area_left, self.area_top, self.area_width, self.area_height

    def load_from_profile(self):
        p = self.win.profile
        for w in self._widgets():
            w.blockSignals(True)
        l, t, r, b = drawing_area(p)
        self.area_left.setRange(p.safe_min_x, p.safe_max_x)
        self.area_top.setRange(p.safe_min_y, p.safe_max_y)
        self.area_width.setRange(1, p.safe_max_x - p.safe_min_x)
        self.area_height.setRange(1, p.safe_max_y - p.safe_min_y)
        self.area_left.setValue(l)
        self.area_top.setValue(t)
        self.area_width.setValue(r - l)
        self.area_height.setValue(b - t)
        key = "custom"
        for k in AREA_PRESETS:
            preset = preset_drawing_area(p, k)
            if preset == p.drawing_area or (preset and p.drawing_area
                                            and all(abs(a - c) < 0.05 for a, c in zip(preset, p.drawing_area))):
                key = k
                break
        self.preset.setCurrentIndex(self.preset.findData(key))
        for w in self._widgets():
            w.blockSignals(False)

    def _preset_changed(self, *_):
        key = self.preset.currentData()
        if key == "custom":
            return
        self.win.set_drawing_area(preset_drawing_area(self.win.profile, key))

    def _numbers_changed(self, *_):
        l, t = self.area_left.value(), self.area_top.value()
        self.win.set_drawing_area([l, t, l + self.area_width.value(), t + self.area_height.value()])

    def set_job(self, text: str, picture: bool):
        self.job_label.setText(text)
        self.adjust_btn.setEnabled(picture)

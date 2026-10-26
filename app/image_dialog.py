"""Picture dialog: turn a photo, scan or sketch into pen strokes, with a live preview.

Left: the original picture (click a colour to remove it, drag to crop). Right: what the pen
will draw, with a slider that replays the drawing order. Processing runs on a worker thread so
the controls stay responsive; only the newest result is shown.
"""
from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import numpy as np
from PySide6.QtCore import QObject, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
                               QPushButton, QScrollArea, QSlider, QSpinBox, QSplitter, QToolButton,
                               QVBoxLayout, QWidget)

from plotter.config import MachineProfile

from . import theme
from plotter.drawing.image_import import (LINE_STYLES, ImageProcessor, ImageResult, ImageSettings,
                                          nearest_palette_index)
from plotter.drawing.pdf_import import DrawingError
from plotter.gcode.estimate import format_duration

STROKE = QColor("#1f3b73")
LATER = QColor("#c9d3df")
TRAVEL = QColor("#6fb3e0")
CROP = QColor("#ff7f0e")


def to_qimage(arr: np.ndarray) -> QImage:
    if arr.ndim == 2:
        a = np.ascontiguousarray(arr)
        return QImage(a.data, a.shape[1], a.shape[0], a.strides[0], QImage.Format_Grayscale8).copy()
    a = np.ascontiguousarray(arr[:, :, ::-1])            # BGR -> RGB
    return QImage(a.data, a.shape[1], a.shape[0], a.strides[0], QImage.Format_RGB888).copy()


class ImageView(QWidget):
    """A picture scaled to fit, with an overlay painted in picture coordinates."""
    clicked = Signal(QPointF)          # picture coordinates
    dragged = Signal(QRectF)           # picture coordinates, clipped to the picture

    def __init__(self, drag: bool = False):
        super().__init__()
        self.setMinimumSize(300, 240)
        self.image: QImage | None = None
        self.size_px = (1.0, 1.0)
        self.overlay = None            # callable(QPainter, scale)
        self.drag_enabled = drag
        self._press: QPointF | None = None
        self._rubber: QRectF | None = None

    def set_image(self, image: QImage | None, size: tuple[float, float] | None = None):
        self.image = image
        if size:
            self.size_px = size
        elif image is not None:
            self.size_px = (image.width(), image.height())
        self.update()

    def _xform(self):
        w, h = self.size_px
        s = max(1e-6, min((self.width() - 12) / w, (self.height() - 12) / h))
        return s, (self.width() - w * s) / 2, (self.height() - h * s) / 2

    def to_image(self, pos: QPointF) -> QPointF:
        s, ox, oy = self._xform()
        return QPointF((pos.x() - ox) / s, (pos.y() - oy) / s)

    def paintEvent(self, _event):
        qp = QPainter(self)
        qp.setRenderHint(QPainter.Antialiasing)
        qp.setRenderHint(QPainter.SmoothPixmapTransform)
        theme.paint_backdrop(qp, self.rect(), alpha=22)
        s, ox, oy = self._xform()
        qp.translate(ox, oy)
        qp.scale(s, s)
        frame = QRectF(0, 0, *self.size_px)
        if self.image is not None:
            qp.drawImage(frame, self.image)
        else:
            qp.fillRect(frame, QColor("white"))
        if self.overlay:
            self.overlay(qp, s)
        if self._rubber is not None:
            qp.setPen(QPen(CROP, 2 / s, Qt.DashLine))
            qp.setBrush(Qt.NoBrush)
            qp.drawRect(self._rubber)
        qp.end()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press = e.position()

    def mouseMoveEvent(self, e):
        if self._press is None or not self.drag_enabled:
            return
        if self._rubber is not None or (e.position() - self._press).manhattanLength() > 6:
            self._rubber = QRectF(self.to_image(self._press), self.to_image(e.position())).normalized() \
                .intersected(QRectF(0, 0, *self.size_px))
            self.update()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.LeftButton or self._press is None:
            return
        rubber, self._rubber, self._press = self._rubber, None, None
        if rubber is not None:
            self.update()
            if rubber.width() > 4 and rubber.height() > 4:
                self.dragged.emit(rubber)
        else:
            p = self.to_image(e.position())
            if 0 <= p.x() < self.size_px[0] and 0 <= p.y() < self.size_px[1]:
                self.clicked.emit(p)


class _Relay(QObject):
    done = Signal(int, object)         # generation, ImageResult or Exception


def slider(lo: int, hi: int, value: int) -> QSlider:
    s = QSlider(Qt.Horizontal)
    s.setRange(lo, hi)
    s.setValue(value)
    return s


class PictureDialog(QDialog):
    def __init__(self, parent, processor: ImageProcessor, settings: ImageSettings | None,
                 area_mm: tuple[float, float], profile: MachineProfile):
        super().__init__(parent)
        self.setWindowTitle(f"Picture → drawing: {processor.name} — {theme.APP_NAME} by {theme.OWNER}")
        self.resize(1400, 860)
        self.processor = processor
        self.settings = replace(settings) if settings else ImageSettings()
        self.settings.removed_colors = list(self.settings.removed_colors)
        self.area_mm = area_mm
        self.profile = profile
        self.result: ImageResult | None = None
        self._gen = 0
        self._busy = False
        self._pool = ThreadPoolExecutor(max_workers=1)
        self._relay = _Relay()
        self._relay.done.connect(self._on_done)
        self._timer = QTimer(self, singleShot=True, interval=250)
        self._timer.timeout.connect(self._run)
        self._play = QTimer(self, interval=30)
        self._play.timeout.connect(self._play_step)
        self._swatches: list[QToolButton] = []

        self._polys: list[QPolygonF] = []

        self._build()
        self._load(self.settings)
        for w in self._widgets():
            for sig in ("valueChanged", "toggled", "currentIndexChanged"):
                if hasattr(w, sig):
                    getattr(w, sig).connect(self._changed)
                    break
        self._schedule(0)

    # ------------------------------------------------------------------ UI
    def _build(self):
        img = self.processor.image
        self.original = ImageView(drag=True)
        self.original.set_image(to_qimage(self._display_copy(img)), (img.shape[1], img.shape[0]))
        self.original.overlay = self._paint_crop
        self.original.clicked.connect(self._pick_colour)
        self.original.dragged.connect(self._crop_to)
        self.original.setToolTip("Click a colour to remove it (click again to bring it back).\n"
                                 "Drag a rectangle to crop the picture.")
        self.output = ImageView()
        self.output.overlay = self._paint_strokes

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(QLabel("<b>Original</b> — click a colour to remove it, drag to crop"))
        ll.addWidget(self.original, 1)
        crop_row = QHBoxLayout()
        self.crop_label = QLabel()
        reset_crop = QPushButton("Use the whole picture")
        reset_crop.clicked.connect(lambda: self._crop_to(None))
        crop_row.addWidget(self.crop_label, 1)
        crop_row.addWidget(reset_crop)
        ll.addLayout(crop_row)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        head.addWidget(QLabel("<b>What the pen will draw</b>"))
        head.addStretch(1)
        self.view_mode = QComboBox()
        self.view_mode.addItem("Pen lines", "strokes")
        self.view_mode.addItem("Pen lines over the picture", "over")
        self.view_mode.addItem("Cleaned-up picture", "work")
        self.view_mode.addItem("Tones the lines are traced from", "tone")
        self.view_mode.currentIndexChanged.connect(self._refresh_output)
        self.show_travel = QCheckBox("Pen-up moves")
        self.show_travel.toggled.connect(self.output.update)
        head.addWidget(self.view_mode)
        head.addWidget(self.show_travel)
        rl.addLayout(head)
        rl.addWidget(self.output, 1)
        play_row = QHBoxLayout()
        self.play_btn = QPushButton("▶ Replay drawing order")
        self.play_btn.setCheckable(True)
        self.play_btn.toggled.connect(self._toggle_play)
        self.order = slider(0, 0, 0)
        self.order.valueChanged.connect(self._order_changed)
        self.order_label = QLabel()
        self.order_label.setMinimumWidth(110)
        play_row.addWidget(self.play_btn)
        play_row.addWidget(self.order, 1)
        play_row.addWidget(self.order_label)
        rl.addLayout(play_row)
        self.stats = QLabel("Processing…")
        self.stats.setWordWrap(True)
        rl.addWidget(self.stats)

        views = QSplitter(Qt.Horizontal)
        views.addWidget(left)
        views.addWidget(right)
        views.setSizes([480, 560])

        controls = QWidget()
        cl = QVBoxLayout(controls)
        cl.addWidget(self._colours_box())
        cl.addWidget(self._tone_box())
        cl.addWidget(self._lines_box())
        cl.addWidget(self._shading_box())
        cl.addWidget(self._detail_box())
        cl.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidget(controls)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(controls.sizeHint().width() + 24)
        scroll.setMaximumWidth(460)

        top = QHBoxLayout()
        top.addWidget(views, 1)
        top.addWidget(scroll)

        buttons = QDialogButtonBox()
        reset = buttons.addButton("Reset settings", QDialogButtonBox.ResetRole)
        reset.clicked.connect(self._reset)
        buttons.addButton(QDialogButtonBox.Cancel)
        self.ok_btn = buttons.addButton("Use this drawing", QDialogButtonBox.AcceptRole)
        self.ok_btn.setStyleSheet("font-weight: bold; padding: 6px 14px;")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addLayout(top, 1)
        lay.addWidget(buttons)

    @staticmethod
    def _display_copy(img: np.ndarray) -> np.ndarray:
        h, w = img.shape[:2]
        f = 1600 / max(h, w)
        if f >= 1:
            return img
        import cv2
        return cv2.resize(img, (round(w * f), round(h * f)), interpolation=cv2.INTER_AREA)

    def _colours_box(self) -> QGroupBox:
        box = QGroupBox("Colours")
        lay = QVBoxLayout(box)
        hint = QLabel("Colours found in the picture. Click one to remove it from the drawing "
                      "(crossed out = removed), or click it on the picture. Its lighter shades "
                      "are removed with it. If removing a colour also breaks lines you want to "
                      "keep, raise the number of colours.")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self.swatch_grid = QGridLayout()
        lay.addLayout(self.swatch_grid)
        row = QHBoxLayout()
        row.addWidget(QLabel("Number of colours"))
        self.palette_size = QSpinBox()
        self.palette_size.setRange(2, 12)
        self.palette_size.valueChanged.connect(self._changed)
        row.addWidget(self.palette_size)
        restore = QPushButton("Keep all colours")
        restore.clicked.connect(self._keep_all_colours)
        row.addStretch(1)
        row.addWidget(restore)
        lay.addLayout(row)
        return box

    def _tone_box(self) -> QGroupBox:
        box = QGroupBox("Smoothing and tone")
        f = QFormLayout(box)
        self.denoise = slider(0, 10, 3)
        self.denoise.setToolTip("Edge-preserving smoothing: removes noise, paper grain and JPEG "
                                "blocks, keeps edges sharp")
        f.addRow("Smooth the picture", self.denoise)
        self.auto_levels = QCheckBox("Auto levels (darkest → black, lightest → white)")
        self.local_contrast = QCheckBox("Local contrast (uneven light, scans)")
        f.addRow(self.auto_levels)
        f.addRow(self.local_contrast)
        self.contrast = slider(30, 300, 100)
        f.addRow("Contrast", self.contrast)
        self.brightness = slider(-100, 100, 0)
        f.addRow("Brightness", self.brightness)
        self.invert = QCheckBox("Invert (light lines on a dark background)")
        f.addRow(self.invert)
        return box

    def _lines_box(self) -> QGroupBox:
        box = QGroupBox("Lines")
        f = QFormLayout(box)
        self.line_style = QComboBox()
        for key, title in LINE_STYLES.items():
            self.line_style.addItem(title, key)
        f.addRow("Style", self.line_style)
        thr = QHBoxLayout()
        self.threshold = slider(1, 254, 128)
        self.auto_threshold = QCheckBox("Auto")
        thr.addWidget(self.threshold, 1)
        thr.addWidget(self.auto_threshold)
        self.threshold_row = QWidget()
        self.threshold_row.setLayout(thr)
        thr.setContentsMargins(0, 0, 0, 0)
        self.threshold.setToolTip("Tones darker than this count as ink")
        f.addRow("Ink threshold", self.threshold_row)
        self.edge_detail = slider(0, 100, 50)
        self.edge_detail.setToolTip("More = more (and weaker) edges")
        f.addRow("Edge detail", self.edge_detail)
        return box

    def _shading_box(self) -> QGroupBox:
        box = QGroupBox("Shading (hatching)")
        f = QFormLayout(box)
        self.shading = QCheckBox("Shade dark areas with hatch lines")
        f.addRow(self.shading)
        self.hatch_levels = QSpinBox()
        self.hatch_levels.setRange(1, 4)
        self.hatch_levels.setToolTip("Darker tones get more crossing hatch directions")
        f.addRow("Tone levels", self.hatch_levels)
        self.hatch_spacing = QDoubleSpinBox()
        self.hatch_spacing.setRange(0.4, 10)
        self.hatch_spacing.setSingleStep(0.1)
        self.hatch_spacing.setSuffix(" mm")
        f.addRow("Line spacing", self.hatch_spacing)
        return box

    def _detail_box(self) -> QGroupBox:
        box = QGroupBox("Pen lines")
        f = QFormLayout(box)
        self.smoothing = QDoubleSpinBox()
        self.smoothing.setRange(0, 8)
        self.smoothing.setSingleStep(0.5)
        self.smoothing.setSuffix(" px")
        self.smoothing.setToolTip("Smooths each line (removes pixel stair-steps)")
        f.addRow("Line smoothing", self.smoothing)
        self.simplify = QDoubleSpinBox()
        self.simplify.setRange(0, 3)
        self.simplify.setSingleStep(0.1)
        self.simplify.setSuffix(" px")
        self.simplify.setToolTip("Merges nearly straight runs into one move: fewer G-code lines, "
                                 "smoother motion")
        f.addRow("Simplify", self.simplify)
        self.min_length = QDoubleSpinBox()
        self.min_length.setRange(0, 20)
        self.min_length.setSingleStep(0.5)
        self.min_length.setSuffix(" mm")
        self.min_length.setToolTip("Lines shorter than this (specks, noise) are not drawn")
        f.addRow("Shortest line", self.min_length)
        self.resolution = QComboBox()
        for px in (500, 700, 900, 1200, 1600):
            self.resolution.addItem(f"{px} px", px)
        self.resolution.setToolTip("Working size of the picture: more = finer detail, slower")
        f.addRow("Detail", self.resolution)
        return box

    # ------------------------------------------------------------- settings
    def _widgets(self):
        return (self.palette_size, self.denoise, self.auto_levels, self.local_contrast, self.contrast,
                self.brightness, self.invert, self.line_style, self.threshold, self.auto_threshold,
                self.edge_detail, self.shading, self.hatch_levels, self.hatch_spacing, self.smoothing,
                self.simplify, self.min_length, self.resolution)

    def _load(self, s: ImageSettings):
        for w in self._widgets():
            w.blockSignals(True)
        self.palette_size.setValue(s.palette_size)
        self.denoise.setValue(s.denoise)
        self.auto_levels.setChecked(s.auto_levels)
        self.local_contrast.setChecked(s.local_contrast)
        self.contrast.setValue(round(s.contrast * 100))
        self.brightness.setValue(round(s.brightness))
        self.invert.setChecked(s.invert)
        self.line_style.setCurrentIndex(max(0, self.line_style.findData(s.line_style)))
        self.auto_threshold.setChecked(s.threshold < 0)
        if s.threshold >= 0:
            self.threshold.setValue(s.threshold)
        self.edge_detail.setValue(s.edge_detail)
        self.shading.setChecked(s.shading)
        self.hatch_levels.setValue(s.hatch_levels)
        self.hatch_spacing.setValue(s.hatch_spacing_mm)
        self.smoothing.setValue(s.smoothing)
        self.simplify.setValue(s.simplify)
        self.min_length.setValue(s.min_length_mm)
        i = self.resolution.findData(s.resolution)
        if i < 0:
            self.resolution.addItem(f"{s.resolution} px", s.resolution)
            i = self.resolution.count() - 1
        self.resolution.setCurrentIndex(i)
        for w in self._widgets():
            w.blockSignals(False)
        self._update_enables()
        self._update_crop_label()

    def _read(self) -> ImageSettings:
        s = self.settings
        return replace(
            s, palette_size=self.palette_size.value(), denoise=self.denoise.value(),
            auto_levels=self.auto_levels.isChecked(), local_contrast=self.local_contrast.isChecked(),
            contrast=self.contrast.value() / 100, brightness=float(self.brightness.value()),
            invert=self.invert.isChecked(), line_style=self.line_style.currentData(),
            threshold=-1 if self.auto_threshold.isChecked() else self.threshold.value(),
            edge_detail=self.edge_detail.value(), shading=self.shading.isChecked(),
            hatch_levels=self.hatch_levels.value(), hatch_spacing_mm=self.hatch_spacing.value(),
            smoothing=self.smoothing.value(), simplify=self.simplify.value(),
            min_length_mm=self.min_length.value(), resolution=int(self.resolution.currentData()),
            removed_colors=list(s.removed_colors))

    def _update_enables(self):
        style = self.line_style.currentData()
        self.threshold_row.setEnabled(style in ("centerline", "outline"))
        self.threshold.setEnabled(not self.auto_threshold.isChecked())
        self.edge_detail.setEnabled(style == "edges")
        on = self.shading.isChecked()
        self.hatch_levels.setEnabled(on)
        self.hatch_spacing.setEnabled(on)

    def _changed(self, *_):
        if self.sender() is self.palette_size:
            self.settings.removed_colors = []        # a new palette: the old picks don't apply
        self.settings = self._read()
        self._update_enables()
        self._schedule()

    def _reset(self):
        self.settings = ImageSettings()
        self._load(self.settings)
        self._schedule(0)

    def _keep_all_colours(self):
        self.settings.removed_colors = []
        self._schedule(0)

    # ---------------------------------------------------------------- colours / crop
    def _toggle_colour(self, index: int):
        if not self.result:
            return
        pal = self.result.palette
        s = self.settings
        if pal[index].removed:
            target = pal[index].shade_of if pal[index].shade_of is not None else index
            s.removed_colors = [c for c in s.removed_colors if nearest_palette_index(pal, c) != target]
        else:
            s.removed_colors = s.removed_colors + [pal[index].rgb]
        self._schedule(0)

    def _pick_colour(self, p: QPointF):
        if not self.result:
            return
        b, g, r = self.processor.image[int(p.y()), int(p.x())]
        self._toggle_colour(nearest_palette_index(self.result.palette, (int(r), int(g), int(b))))

    def _crop_to(self, rect: QRectF | None):
        w, h = self.original.size_px
        self.settings.crop = None if rect is None else (
            rect.left() / w, rect.top() / h, rect.right() / w, rect.bottom() / h)
        self._update_crop_label()
        self.original.update()
        self._schedule(0)

    def _update_crop_label(self):
        h, w = self.processor.image.shape[:2]
        c = self.settings.crop
        if c is None:
            self.crop_label.setText(f"Whole picture, {w} × {h} px")
        else:
            self.crop_label.setText(f"Cropped to {round((c[2] - c[0]) * w)} × {round((c[3] - c[1]) * h)} px")

    def _rebuild_swatches(self):
        pal = self.result.palette if self.result else []
        while self.swatch_grid.count():
            item = self.swatch_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._swatches = []
        for i, c in enumerate(pal):
            pm = QPixmap(34, 22)
            pm.fill(QColor(*c.rgb))
            if c.removed:
                qp = QPainter(pm)
                qp.setRenderHint(QPainter.Antialiasing)
                light = sum(c.rgb) > 380
                qp.setPen(QPen(QColor("#c62828") if light else QColor("white"), 3))
                qp.drawLine(3, 3, 31, 19)
                qp.drawLine(31, 3, 3, 19)
                qp.end()
            b = QToolButton()
            b.setIcon(QIcon(pm))
            b.setIconSize(QSize(34, 22))
            b.setText(f"{c.share:.0%}" + (" removed" if c.removed else ""))
            b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            b.setToolTip(f"RGB {c.rgb}, {c.share:.1%} of the picture. Click to "
                         + ("draw it again." if c.removed else "remove it from the drawing.")
                         + ("\nA lighter shade of a removed colour, removed with it." if c.shade_of is not None else ""))
            b.clicked.connect(lambda _=False, i=i: self._toggle_colour(i))
            self.swatch_grid.addWidget(b, i // 4, i % 4)
            self._swatches.append(b)

    # ---------------------------------------------------------------- processing
    def _schedule(self, delay: int = 250):
        self._timer.start(delay)

    def _run(self):
        self._gen += 1
        gen, settings = self._gen, replace(self.settings, removed_colors=list(self.settings.removed_colors))
        self._busy = True
        self.ok_btn.setEnabled(False)
        self.stats.setText("Processing…")

        def work():
            try:
                res = self.processor.process(settings, self.area_mm)
            except (DrawingError, ValueError) as exc:
                res = exc
            self._relay.done.emit(gen, res)

        self._pool.submit(work)

    def _on_done(self, gen: int, res):
        if gen != self._gen:
            return                                      # a newer request is on its way
        self._busy = False
        if isinstance(res, Exception):
            self.result = None
            self._polys = []
            self.stats.setText(f"<span style='color: #ff6b6b'>{res}</span>")
            self.ok_btn.setEnabled(False)
            self.output.update()
            return
        self.result = res
        n = len(res.drawing.strokes)
        self._polys = [QPolygonF([QPointF(x, y) for x, y in st]) for st in res.drawing.strokes]
        self.order.blockSignals(True)
        self.order.setRange(0, n)
        self.order.setValue(n)
        self.order.blockSignals(False)
        self._order_changed()
        self._rebuild_swatches()
        self._refresh_output()
        self._show_stats()
        self.ok_btn.setEnabled(n > 0)

    def _show_stats(self):
        res = self.result
        st = res.stats
        p = self.profile
        lifts = st["strokes"]
        travel_feed = p.travel_feed if p.travel_feed > 0 else 3000.0
        seconds = 60 * (st["draw_mm"] / p.draw_feed + st["travel_mm"] / travel_feed
                        + 2 * lifts * abs(p.pen_up_z - p.pen_down_z) / p.z_feed)
        w = res.drawing.width * res.mm_per_px
        h = res.drawing.height * res.mm_per_px
        layers = res.drawing.layer_sizes
        parts = f"{layers[0]} outline" + (f" + {sum(layers[1:])} shading" if len(layers) > 1 else "")
        text = (f"<b>{lifts} lines</b> ({parts}) · pen down {st['draw_mm'] / 1000:.1f} m · "
                f"pen-up travel {st['travel_mm'] / 1000:.1f} m · about {format_duration(seconds)} · "
                f"{w:.0f} × {h:.0f} mm on paper")
        if self.settings.line_style in ("centerline", "outline"):
            text += f" · ink threshold {res.threshold}"
        for warn in res.drawing.warnings:
            text += f"<br><span style='color: #ffb74d'>⚠ {warn}</span>"
        self.stats.setText(text)

    # ---------------------------------------------------------------- output view
    def _refresh_output(self):
        res = self.result
        mode = self.view_mode.currentData()
        size = (res.drawing.width, res.drawing.height) if res else self.output.size_px
        if res is None or mode == "strokes":
            self.output.set_image(None, size)
        elif mode == "tone":
            self.output.set_image(to_qimage(res.tone), size)
        else:
            self.output.set_image(to_qimage(res.work), size)

    def _order_changed(self, *_):
        self.order_label.setText(f"{self.order.value()} / {self.order.maximum()} lines")
        self.output.update()

    def _paint_strokes(self, qp: QPainter, scale: float):
        res = self.result
        if not res:
            return
        strokes, polys = res.drawing.strokes, self._polys
        n = self.order.value()
        width = 1.2 / scale                               # about 1.2 screen pixels
        if self.show_travel.isChecked():
            qp.setPen(QPen(TRAVEL, 0, Qt.DashLine))
            for a, b in zip(strokes[:max(0, n - 1)], strokes[1:n]):
                qp.drawLine(QPointF(*a[-1]), QPointF(*b[0]))
        if n < len(strokes):
            qp.setPen(QPen(LATER, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            for poly in polys[n:]:
                qp.drawPolyline(poly)
        qp.setPen(QPen(STROKE, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        for poly in polys[:n]:
            qp.drawPolyline(poly)
        if 0 < n < len(strokes):
            x, y = strokes[n - 1][-1]
            r = 5 / scale
            qp.setPen(QPen(QColor("#000"), 1 / scale))
            qp.setBrush(QColor("#e4572e"))
            qp.drawEllipse(QPointF(x, y), r, r)

    def _paint_crop(self, qp: QPainter, scale: float):
        c = self.settings.crop
        if c is None:
            return
        w, h = self.original.size_px
        r = QRectF(c[0] * w, c[1] * h, (c[2] - c[0]) * w, (c[3] - c[1]) * h)
        shade = QColor(0, 0, 0, 110)
        qp.fillRect(QRectF(0, 0, w, r.top()), shade)
        qp.fillRect(QRectF(0, r.bottom(), w, h - r.bottom()), shade)
        qp.fillRect(QRectF(0, r.top(), r.left(), r.height()), shade)
        qp.fillRect(QRectF(r.right(), r.top(), w - r.right(), r.height()), shade)
        qp.setPen(QPen(CROP, 2 / scale))
        qp.setBrush(Qt.NoBrush)
        qp.drawRect(r)

    # ---------------------------------------------------------------- playback
    def _toggle_play(self, on: bool):
        if on:
            if self.order.value() >= self.order.maximum():
                self.order.setValue(0)
            self.play_btn.setText("⏸ Pause")
            self._play.start()
        else:
            self.play_btn.setText("▶ Replay drawing order")
            self._play.stop()

    def _play_step(self):
        n = self.order.maximum()
        step = max(1, math.ceil(n / 240))               # about 7 s for the whole picture
        v = min(n, self.order.value() + step)
        self.order.setValue(v)
        if v >= n:
            self.play_btn.setChecked(False)

    def done(self, r):
        self._play.stop()
        self._timer.stop()
        self._gen += 1                                   # ignore anything still running
        # Wait for a job already running (at most a second or two): it must not report to a
        # dialog that is being destroyed, nor use the processor's cache at the same time as
        # the main window, which re-traces the picture with the same processor.
        self._pool.shutdown(wait=True, cancel_futures=True)
        super().done(r)

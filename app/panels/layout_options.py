from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox

from plotter.text.hershey import FONT_TITLES, available_fonts


def spin(minimum, maximum, step, decimals=1, suffix=" mm"):
    s = QDoubleSpinBox()
    s.setRange(minimum, maximum)
    s.setSingleStep(step)
    s.setDecimals(decimals)
    s.setSuffix(suffix)
    return s


class LayoutPanel(QGroupBox):
    FIELDS = ("char_height", "line_spacing", "letter_spacing",
              "margin_left", "margin_right", "margin_top", "margin_bottom")

    def __init__(self, win):
        super().__init__("Layout")
        self.win = win
        form = QFormLayout(self)
        self.font = QComboBox()
        for name in available_fonts():
            self.font.addItem(FONT_TITLES.get(name, name), name)
        form.addRow("Font", self.font)
        self.char_height = spin(1, 60, 0.5)
        self.char_height.setToolTip("Height of capital letters")
        form.addRow("Character height", self.char_height)
        self.line_spacing = spin(1.0, 5.0, 0.1, 2, " ×")
        self.line_spacing.setToolTip("Distance between baselines as a multiple of the character height")
        form.addRow("Line spacing", self.line_spacing)
        self.letter_spacing = spin(-2, 10, 0.1)
        form.addRow("Extra letter spacing", self.letter_spacing)
        self.margin_left = spin(0, 100, 1)
        self.margin_right = spin(0, 100, 1)
        self.margin_top = spin(0, 140, 1)
        self.margin_bottom = spin(0, 140, 1)
        form.addRow("Left margin", self.margin_left)
        form.addRow("Right margin", self.margin_right)
        form.addRow("Top margin", self.margin_top)
        form.addRow("Bottom margin", self.margin_bottom)
        self.align = QComboBox()
        self.align.addItem("Centered on the paper", "center")
        self.align.addItem("Left (from the left margin)", "left")
        form.addRow("Horizontal", self.align)
        self.valign = QComboBox()
        self.valign.addItem("Middle of the paper", "middle")
        self.valign.addItem("Top (from the top margin)", "top")
        form.addRow("Vertical", self.valign)
        self.hyphenate = QCheckBox("Hyphenate words longer than a line")
        form.addRow(self.hyphenate)
        self.load_from_profile()
        self.font.currentIndexChanged.connect(self._changed)
        self.align.currentIndexChanged.connect(self._changed)
        self.valign.currentIndexChanged.connect(self._changed)
        self.hyphenate.toggled.connect(self._changed)
        for name in self.FIELDS:
            getattr(self, name).valueChanged.connect(self._changed)

    def load_from_profile(self):
        p = self.win.profile
        widgets = [self.font, self.hyphenate, self.align, self.valign] + [getattr(self, n) for n in self.FIELDS]
        for w in widgets:
            w.blockSignals(True)
        idx = self.font.findData(p.font)
        self.font.setCurrentIndex(max(0, idx))
        self.hyphenate.setChecked(p.hyphenate_long_words)
        self.align.setCurrentIndex(max(0, self.align.findData(p.text_align)))
        self.valign.setCurrentIndex(max(0, self.valign.findData(p.vertical_align)))
        for name in self.FIELDS:
            w = getattr(self, name)
            if name.startswith("margin"):
                w.setMinimum(p.safety_margin)     # text margins can't go inside the safety margin
            w.setValue(getattr(p, name))
        for w in widgets:
            w.blockSignals(False)

    def _changed(self, *_):
        p = self.win.profile
        p.font = self.font.currentData()
        p.hyphenate_long_words = self.hyphenate.isChecked()
        p.text_align = self.align.currentData()
        p.vertical_align = self.valign.currentData()
        for name in self.FIELDS:
            setattr(p, name, getattr(self, name).value())
        self.win.profile_edited(replan=True)

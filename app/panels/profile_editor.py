from __future__ import annotations

from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from plotter.config import CONTROL_ACTIONS, VoiceCommand, default_profile_path


def spin(minimum, maximum, step=1.0, decimals=2, suffix=""):
    s = QDoubleSpinBox()
    s.setRange(minimum, maximum)
    s.setSingleStep(step)
    s.setDecimals(decimals)
    s.setSuffix(suffix)
    return s


class ProfilePanel(QWidget):
    NUMERIC = {
        "paper_width": (10, 1000, 1, 1, " mm"), "paper_height": (10, 1000, 1, 1, " mm"),
        "safety_margin": (0, 100, 1, 1, " mm"),
        "usable_travel_x": (10, 2000, 1, 1, " mm"), "usable_travel_y": (10, 2000, 1, 1, " mm"),
        "pen_up_z": (-50, 50, 0.5, 2, " mm"), "pen_down_z": (-50, 50, 0.5, 2, " mm"),
        "z_feed": (1, 5000, 50, 0, " mm/min"),
        "draw_feed": (1, 20000, 100, 0, " mm/min"), "travel_feed": (0, 20000, 100, 0, " mm/min"),
        "park_x": (-1000, 1000, 1, 1, " mm"), "park_y": (-1000, 1000, 1, 1, " mm"),
        "jog_feed": (1, 20000, 100, 0, " mm/min"), "jog_z_feed": (1, 5000, 50, 0, " mm/min"),
    }
    LABELS = {
        "paper_width": "Paper width (X)", "paper_height": "Paper height (Y)",
        "safety_margin": "Safety margin", "usable_travel_x": "Usable travel X (measured)",
        "usable_travel_y": "Usable travel Y (measured)", "pen_up_z": "Pen up Z (work)", "pen_down_z": "Pen down Z (work)",
        "z_feed": "Z feed", "draw_feed": "Draw feed (XY)", "travel_feed": "Travel feed (0 = G0 rapid)",
        "park_x": "Park X (work)", "park_y": "Park Y (work)", "jog_feed": "Jog feed XY",
        "jog_z_feed": "Jog feed Z",
    }

    def __init__(self, win):
        super().__init__()
        self.win = win
        lay = QVBoxLayout(self)

        orient = QGroupBox("Axis orientation (confirm on first run: jog +X and +Y and watch the pen)")
        of = QFormLayout(orient)
        self.x_positive = QComboBox()
        self.x_positive.addItem("+X moves the pen to the RIGHT", "right")
        self.x_positive.addItem("+X moves the pen to the LEFT", "left")
        self.y_positive = QComboBox()
        self.y_positive.addItem("+Y moves the pen AWAY from me", "away")
        self.y_positive.addItem("+Y moves the pen TOWARD me", "toward")
        of.addRow(self.x_positive)
        of.addRow(self.y_positive)
        lay.addWidget(orient)

        box = QGroupBox("Paper, pen and feeds")
        form = QFormLayout(box)
        self.fields: dict[str, QDoubleSpinBox] = {}
        for name, (lo, hi, step, dec, suffix) in self.NUMERIC.items():
            s = spin(lo, hi, step, dec, suffix)
            self.fields[name] = s
            form.addRow(self.LABELS[name], s)
        self.jog_steps = QLineEdit()
        self.jog_steps.setToolTip("Comma-separated jog step sizes in mm")
        form.addRow("Jog steps (mm)", self.jog_steps)
        self.origin = QLabel()
        form.addRow("Saved paper origin", self.origin)
        lay.addWidget(box)

        vbox = QGroupBox("Voice commands (a control command must be the whole utterance)")
        vl = QVBoxLayout(vbox)
        self.commands = QTableWidget(0, 2)
        self.commands.setHorizontalHeaderLabels(["Phrase", "Action"])
        self.commands.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.commands.setMinimumHeight(140)
        vl.addWidget(self.commands)
        row = QHBoxLayout()
        add = QPushButton("Add")
        add.clicked.connect(lambda: self._add_command(VoiceCommand("", "newline")))
        rem = QPushButton("Remove selected")
        rem.clicked.connect(self._remove_command)
        row.addWidget(add)
        row.addWidget(rem)
        row.addStretch(1)
        vl.addLayout(row)
        lay.addWidget(vbox)

        self.problems = QLabel()
        self.problems.setWordWrap(True)
        lay.addWidget(self.problems)
        row = QHBoxLayout()
        self.apply_btn = QPushButton("Apply && save profile")
        self.apply_btn.setStyleSheet("font-weight: bold;")
        self.apply_btn.clicked.connect(self.apply)
        self.revert_btn = QPushButton("Revert")
        self.revert_btn.clicked.connect(self.load_from_profile)
        row.addWidget(self.apply_btn)
        row.addWidget(self.revert_btn)
        row.addStretch(1)
        lay.addLayout(row)
        path = QLabel(f"Profile file: {default_profile_path()}")
        path.setStyleSheet("color: #9aa3b2;")
        path.setWordWrap(True)
        lay.addWidget(path)
        lay.addStretch(1)
        self.load_from_profile()

    def _add_command(self, vc: VoiceCommand):
        r = self.commands.rowCount()
        self.commands.insertRow(r)
        self.commands.setItem(r, 0, QTableWidgetItem(vc.phrase))
        combo = QComboBox()
        combo.addItems(list(CONTROL_ACTIONS))
        combo.setCurrentText(vc.action)
        self.commands.setCellWidget(r, 1, combo)

    def _remove_command(self):
        for r in sorted({i.row() for i in self.commands.selectedIndexes()}, reverse=True):
            self.commands.removeRow(r)

    def load_from_profile(self):
        p = self.win.profile
        self.x_positive.setCurrentIndex(max(0, self.x_positive.findData(p.x_positive)))
        self.y_positive.setCurrentIndex(max(0, self.y_positive.findData(p.y_positive)))
        for name, w in self.fields.items():
            w.setValue(getattr(p, name))
        self.jog_steps.setText(", ".join(f"{s:g}" for s in p.jog_steps))
        self.origin.setText("not set" if not p.paper_origin_mpos else
                            f"machine X{p.paper_origin_mpos[0]:.3f} Y{p.paper_origin_mpos[1]:.3f}")
        self.commands.setRowCount(0)
        for vc in p.voice_commands:
            self._add_command(vc)
        self.show_problems(p.problems())

    def show_problems(self, problems: list[str]):
        if problems:
            self.problems.setText("⚠ " + "\n⚠ ".join(problems))
            self.problems.setStyleSheet("color: #ff6b6b;")
        else:
            self.problems.setText("Profile OK.")
            self.problems.setStyleSheet("color: #4cd07d;")

    def apply(self):
        p = self.win.profile
        candidate = type(p).from_dict(p.to_dict())
        candidate.x_positive = self.x_positive.currentData()
        candidate.y_positive = self.y_positive.currentData()
        for name, w in self.fields.items():
            setattr(candidate, name, w.value())
        try:
            steps = [float(s) for s in self.jog_steps.text().replace(";", ",").split(",") if s.strip()]
            if not steps or any(s <= 0 for s in steps):
                raise ValueError
            candidate.jog_steps = steps
        except ValueError:
            self.show_problems(["Jog steps must be positive numbers separated by commas"])
            return
        cmds = []
        for r in range(self.commands.rowCount()):
            item = self.commands.item(r, 0)
            phrase = item.text().strip() if item else ""
            if phrase:
                cmds.append(VoiceCommand(phrase, self.commands.cellWidget(r, 1).currentText()))
        candidate.voice_commands = cmds
        problems = candidate.problems()
        self.show_problems(problems)
        if problems:
            return
        self.win.replace_profile(candidate)

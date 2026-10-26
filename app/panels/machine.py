from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QComboBox, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
                               QPushButton, QVBoxLayout)


class MachinePanel(QGroupBox):
    def __init__(self, win):
        super().__init__("Machine control")
        self.win = win
        lay = QVBoxLayout(self)

        row = QHBoxLayout()
        self.home_btn = QPushButton("⌂ Home ($H)")
        self.home_btn.setStyleSheet("font-weight: bold;")
        self.home_btn.clicked.connect(win.home)
        self.homed = QLabel()
        row.addWidget(self.home_btn)
        row.addWidget(self.homed, 1)
        lay.addLayout(row)
        self.interlock = QCheckBox("Require homing before motion")
        self.interlock.setChecked(True)
        self.interlock.setToolTip("Safety interlock. Turning it off lasts only for this session.")
        self.interlock.toggled.connect(win.set_interlock)
        lay.addWidget(self.interlock)

        # Jog pad (directions are as seen from the front; mapped to ±X/±Y by the profile)
        step_row = QHBoxLayout()
        step_row.addWidget(QLabel("Jog step"))
        self.step = QComboBox()
        step_row.addWidget(self.step, 1)
        lay.addLayout(step_row)

        grid = QGridLayout()
        self.jog_btns = {}
        for text, direction, r, c in (("▲\naway", "away", 0, 1), ("◀\nleft", "left", 1, 0),
                                      ("▶\nright", "right", 1, 2), ("▼\ntoward", "toward", 2, 1)):
            b = QPushButton(text)
            b.setMinimumHeight(44)
            b.clicked.connect(lambda _=False, d=direction: win.jog(d))
            grid.addWidget(b, r, c)
            self.jog_btns[direction] = b
        self.cancel_btn = QPushButton("■")
        self.cancel_btn.setToolTip("Cancel jog (GRBL 1.1 only)")
        self.cancel_btn.setMinimumHeight(44)
        self.cancel_btn.clicked.connect(win.jog_cancel)
        grid.addWidget(self.cancel_btn, 1, 1)
        for text, sign, r in (("Z ▲ up", 1, 0), ("Z ▼ down", -1, 2)):
            b = QPushButton(text)
            b.setMinimumHeight(44)
            b.clicked.connect(lambda _=False, s=sign: win.jog_z(s))
            grid.addWidget(b, r, 3)
            self.jog_btns[f"z{sign}"] = b
        lay.addLayout(grid)

        row = QHBoxLayout()
        self.pen_up_btn = QPushButton("Pen up")
        self.pen_up_btn.clicked.connect(win.pen_up)
        self.pen_down_btn = QPushButton("Pen down")
        self.pen_down_btn.clicked.connect(win.pen_down)
        self.park_btn = QPushButton("Go to park")
        self.park_btn.clicked.connect(win.go_park)
        for b in (self.pen_up_btn, self.pen_down_btn, self.park_btn):
            row.addWidget(b)
        lay.addLayout(row)

        self.origin_hint = QLabel()
        self.origin_hint.setWordWrap(True)
        self.origin_hint.setStyleSheet("color: #9aa3b2;")
        lay.addWidget(self.origin_hint)
        row = QHBoxLayout()
        row.addWidget(QLabel("Pen is at the"))
        self.origin_corner = QComboBox()
        for corner in ("back-left", "back-right", "front-left", "front-right"):
            self.origin_corner.addItem(f"{corner} corner", corner)
        self.origin_corner.setToolTip("Which corner of the sheet the pen is at (as seen from the front).")
        row.addWidget(self.origin_corner, 1)
        lay.addLayout(row)
        row = QHBoxLayout()
        self.origin_mode = QComboBox()
        self.origin_mode.addItem("exactly on the corner", "corner")
        self.origin_mode.addItem("10 mm inside the corner", "safe")
        self.origin_mode.setToolTip("'Inside' = one safety margin in from both edges of the sheet.")
        self.origin_btn = QPushButton("Set paper origin")
        self.origin_btn.clicked.connect(lambda: win.set_origin(self.origin_corner.currentData(),
                                                               self.origin_mode.currentData() == "safe"))
        row.addWidget(self.origin_mode, 1)
        row.addWidget(self.origin_btn)
        lay.addLayout(row)
        row = QHBoxLayout()
        self.zzero_btn = QPushButton("Set Z zero here")
        self.zzero_btn.setToolTip("Make the current Z height work Z0 (usually right after homing). "
                                   "Pen up/down heights in the profile are relative to it.")
        self.zzero_btn.clicked.connect(win.set_z_zero)
        self.restore_btn = QPushButton("Restore saved origin")
        self.restore_btn.setToolTip("Re-apply the paper origin stored in the profile (G10 L2).")
        self.restore_btn.clicked.connect(win.restore_origin)
        row.addWidget(self.zzero_btn)
        row.addWidget(self.restore_btn)
        lay.addLayout(row)
        self.center_btn = QPushButton("Center page in machine travel")
        self.center_btn.setToolTip(
            "Place the page automatically inside the reachable area (uses $130/$131). "
            "Use this when the paper covers the whole plotting area.")
        self.center_btn.clicked.connect(win.center_page)
        lay.addWidget(self.center_btn)
        self.origin_corner.activated.connect(lambda _i: setattr(self, "_corner_touched", True))
        self.refresh_profile()
        self.set_homed(False)
        self.set_enabled(False, False)

    def refresh_profile(self):
        p = self.win.profile
        current = self.step.currentData()
        self.step.clear()
        for s in p.jog_steps:
            self.step.addItem(f"{s:g} mm", s)
        idx = self.step.findData(current) if current is not None else 1
        self.step.setCurrentIndex(idx if idx >= 0 else min(1, self.step.count() - 1))
        home = p.home_corner_name(self.win.homing_dir_mask())
        self.origin_hint.setText(
            f"Home is the <b>{home}</b> corner. Easiest: put the sheet's {home} corner under the "
            "pen after homing, then press Set paper origin. Any corner works if you pick it below.")
        self.origin_mode.setItemText(1, f"{p.safety_margin:g} mm inside the corner")
        idx = self.origin_corner.findData(home)
        if idx >= 0 and not getattr(self, "_corner_touched", False):
            self.origin_corner.setCurrentIndex(idx)

    @property
    def step_mm(self) -> float:
        return float(self.step.currentData() or 1.0)

    def set_homed(self, homed: bool):
        if homed:
            self.homed.setText("✔ Homed")
            self.homed.setStyleSheet("color: #4cd07d; font-weight: bold;")
        else:
            self.homed.setText("✖ Not homed")
            self.homed.setStyleSheet("color: #ff6b6b; font-weight: bold;")

    def set_enabled(self, connected: bool, motion_ok: bool, jog_cancel: bool = False):
        self.home_btn.setEnabled(connected)
        for b in list(self.jog_btns.values()) + [self.pen_up_btn, self.pen_down_btn, self.park_btn]:
            b.setEnabled(connected and motion_ok)
        self.cancel_btn.setEnabled(connected and jog_cancel)
        for b in (self.origin_btn, self.zzero_btn, self.restore_btn, self.center_btn):
            b.setEnabled(connected)

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from plotter.grbl.protocol import (DIRECTION_SETTINGS, SETTINGS_INFO, settings_warnings,
                                   validate_setting)


def fmt(v: float) -> str:
    return f"{v:g}" if v == int(v) else f"{v:.3f}".rstrip("0")


class SettingsPanel(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.values: dict[int, float] = {}
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        self.read_btn = QPushButton("Read settings ($$)")
        self.read_btn.clicked.connect(win.read_settings)
        self.write_btn = QPushButton("Write changed settings…")
        self.write_btn.clicked.connect(self.write_changed)
        row.addWidget(self.read_btn)
        row.addWidget(self.write_btn)
        row.addStretch(1)
        lay.addLayout(row)
        self.warnings = QLabel("")
        self.warnings.setWordWrap(True)
        lay.addWidget(self.warnings)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["$", "Setting", "Value", "Unit", "Description"])
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(4, QHeaderView.Stretch)
        h.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.itemChanged.connect(self._item_changed)
        lay.addWidget(self.table, 1)
        note = QLabel("Values are read from the board. Edited cells turn yellow; nothing is "
                      "written until you confirm. $3 and $23 (motor/homing directions) are already "
                      "correct for this machine and should not be changed.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #9aa3b2;")
        lay.addWidget(note)
        self.set_enabled(False)

    def set_enabled(self, on: bool):
        self.read_btn.setEnabled(on)
        self.write_btn.setEnabled(on)

    def show_settings(self, settings: dict[int, float]):
        self.values = dict(settings)
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for n in sorted(settings):
            name, unit, desc = SETTINGS_INFO.get(n, (f"${n}", "", ""))
            r = self.table.rowCount()
            self.table.insertRow(r)
            for c, text in enumerate([f"${n}", name, fmt(settings[n]), unit, desc]):
                item = QTableWidgetItem(text)
                if c != 2:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                else:
                    item.setData(Qt.UserRole, n)
                if n in DIRECTION_SETTINGS:
                    item.setForeground(QColor("#e0b062"))
                self.table.setItem(r, c, item)
        self.table.blockSignals(False)
        w = settings_warnings(settings, self.win.profile.pen_down_z)
        if w:
            self.warnings.setText("⚠ " + "\n⚠ ".join(w))
            self.warnings.setStyleSheet("color: #ffb74d;")
        else:
            self.warnings.setText("Settings are consistent with this machine (280 × 280 mm travel, Z-stepper pen).")
            self.warnings.setStyleSheet("color: #4cd07d;")

    def _item_changed(self, item):
        n = item.data(Qt.UserRole)
        if n is None:
            return
        changed = item.text().strip() != fmt(self.values.get(n, 0))
        item.setBackground(QColor("#5a4a12") if changed else QColor(Qt.transparent))

    def changed(self) -> dict[int, str]:
        out = {}
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 2)
            n = item.data(Qt.UserRole)
            text = item.text().strip()
            if text != fmt(self.values.get(n, 0)):
                out[n] = text
        return out

    def write_changed(self):
        changes = self.changed()
        if not changes:
            QMessageBox.information(self, "GRBL settings", "No settings were changed.")
            return
        errors = [msg for n, v in changes.items() for ok, msg in [validate_setting(n, v)] if not ok]
        if errors:
            QMessageBox.warning(self, "Invalid values", "\n".join(errors))
            return
        lines = [f"${n}: {fmt(self.values.get(n, 0))} → {v}   ({SETTINGS_INFO.get(n, ('', '', ''))[0]})"
                 for n, v in sorted(changes.items())]
        warn = ""
        if set(changes) & DIRECTION_SETTINGS:
            warn = ("\n\n⚠ You are changing $3/$23 (motor or homing direction). These are already "
                    "correct for this machine. Changing them can drive axes into the frame.")
        box = QMessageBox(QMessageBox.Warning if warn else QMessageBox.Question, "Write GRBL settings",
                          "Write these settings to the board's EEPROM?\n\n" + "\n".join(lines) + warn,
                          QMessageBox.Yes | QMessageBox.Cancel, self)
        box.setDefaultButton(QMessageBox.Cancel)
        if box.exec() != QMessageBox.Yes:
            return
        self.win.write_settings(changes)

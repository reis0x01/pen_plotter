from __future__ import annotations

import time

from plotter.config import default_profile_path

from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QLineEdit, QPlainTextEdit, QPushButton,
                               QVBoxLayout, QWidget)


class LogPanel(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self._last_status = ""
        self.file = None
        try:
            log_dir = default_profile_path().parent / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            self.path = log_dir / time.strftime("session-%Y%m%d-%H%M%S.log")
            self.file = open(self.path, "a", buffering=1, encoding="utf-8")
        except OSError:
            self.path = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(5000)
        font = QFont("monospace")
        font.setStyleHint(QFont.Monospace)
        self.view.setFont(font)
        lay.addWidget(self.view, 1)
        row = QHBoxLayout()
        self.show_status = QCheckBox("Show status reports")
        self.show_status.setToolTip("Include the periodic '?' status replies")
        self.autoscroll = QCheckBox("Auto-scroll")
        self.autoscroll.setChecked(True)
        clear = QPushButton("Clear")
        clear.clicked.connect(self.view.clear)
        self.cmd = QLineEdit()
        self.cmd.setPlaceholderText("Send a command to GRBL (e.g. $G, $#, $I). Motion obeys the homing interlock.")
        self.cmd.returnPressed.connect(self._send)
        send = QPushButton("Send")
        send.clicked.connect(self._send)
        row.addWidget(self.cmd, 1)
        row.addWidget(send)
        row.addWidget(self.show_status)
        row.addWidget(self.autoscroll)
        row.addWidget(clear)
        lay.addLayout(row)

    def _send(self):
        text = self.cmd.text().strip()
        if text and self.win.send(text):
            self.cmd.clear()

    def add(self, direction: str, text: str):
        stamp = time.strftime("%H:%M:%S")
        is_status = text.startswith("<") and text.endswith(">")
        # Session file: everything except repeated identical status reports.
        if self.file and (not is_status or text != self._last_status):
            try:
                self.file.write(f"{stamp} {direction} {text}\n")
            except OSError:
                pass
        if is_status:
            self._last_status = text
        if not self.show_status.isChecked() and is_status:
            return
        self.view.appendPlainText(f"{stamp} {direction} {text}")
        if self.autoscroll.isChecked():
            sb = self.view.verticalScrollBar()
            sb.setValue(sb.maximum())

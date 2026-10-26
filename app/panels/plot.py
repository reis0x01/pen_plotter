from __future__ import annotations

from PySide6.QtWidgets import (QGridLayout, QGroupBox, QHBoxLayout, QLabel, QProgressBar,
                               QPushButton, QSpinBox, QVBoxLayout)

from plotter.gcode.estimate import format_duration


class PlotPanel(QGroupBox):
    def __init__(self, win):
        super().__init__("Plot")
        self.win = win
        lay = QVBoxLayout(self)

        self.summary = QLabel("Nothing to plot yet.")
        self.summary.setWordWrap(True)
        lay.addWidget(self.summary)
        row = QHBoxLayout()
        row.addWidget(QLabel("Page"))
        self.page = QSpinBox()
        self.page.setMinimum(1)
        self.page.setMaximum(1)
        self.page.valueChanged.connect(lambda v: win.show_page(v - 1))
        self.page_count = QLabel("of 1")
        row.addWidget(self.page)
        row.addWidget(self.page_count)
        row.addStretch(1)
        lay.addLayout(row)

        grid = QGridLayout()
        self.start_btn = QPushButton("▶ Plot")
        self.start_btn.setStyleSheet("font-weight: bold; padding: 8px;")
        self.pause_btn = QPushButton("⏸ Pause")
        self.resume_btn = QPushButton("⏵ Resume")
        self.stop_btn = QPushButton("■ STOP")
        self.stop_btn.setStyleSheet("background: #c62828; color: white; font-weight: bold; padding: 8px;")
        self.stop_btn.setToolTip("Feed hold, then soft reset. The machine must be re-homed afterwards.")
        self.start_btn.clicked.connect(win.start_plot)
        self.pause_btn.clicked.connect(win.pause)
        self.resume_btn.clicked.connect(win.resume)
        self.stop_btn.clicked.connect(win.stop)
        grid.addWidget(self.start_btn, 0, 0, 1, 2)
        grid.addWidget(self.pause_btn, 1, 0)
        grid.addWidget(self.resume_btn, 1, 1)
        grid.addWidget(self.stop_btn, 2, 0, 1, 2)
        lay.addLayout(grid)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        lay.addWidget(self.progress)
        self.eta = QLabel("")
        lay.addWidget(self.eta)
        self.message = QLabel("")
        self.message.setWordWrap(True)
        lay.addWidget(self.message)
        self.set_state(connected=False, can_start=False, running=False, paused=False)

    def set_pages(self, count: int, current: int):
        self.page.blockSignals(True)
        self.page.setMaximum(max(1, count))
        self.page.setValue(current + 1)
        self.page.blockSignals(False)
        self.page_count.setText(f"of {max(1, count)}")

    def set_summary(self, text: str, ok: bool):
        self.summary.setText(text)
        self.summary.setStyleSheet("" if ok else "color: #ff6b6b; font-weight: bold;")

    def set_state(self, connected: bool, can_start: bool, running: bool, paused: bool):
        self.start_btn.setEnabled(connected and can_start and not running and not paused)
        self.pause_btn.setEnabled(connected and running)
        self.resume_btn.setEnabled(connected and paused)
        self.stop_btn.setEnabled(connected)
        self.page.setEnabled(not running and not paused)

    def set_progress(self, fraction: float, elapsed: float, total_estimate: float):
        self.progress.setValue(int(fraction * 1000))
        remaining = max(0.0, total_estimate * (1 - fraction))
        self.eta.setText(f"{fraction * 100:.0f}%   elapsed {format_duration(elapsed)}   "
                         f"remaining ≈ {format_duration(remaining)}")

    def set_message(self, text: str, error: bool = False):
        self.message.setText(text)
        self.message.setStyleSheet("color: #ff6b6b;" if error else "color: #d7dbe3;")

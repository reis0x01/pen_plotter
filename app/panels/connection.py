from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                               QPushButton, QVBoxLayout, QWidget)

from plotter.grbl.connection import list_serial_ports

STATE_COLORS = {
    "Idle": "#4cd07d", "Run": "#6ea8ff", "Jog": "#6ea8ff", "Home": "#c792ea",
    "Hold": "#ffa040", "Alarm": "#ff6b6b", "Door": "#ff6b6b", "Check": "#9aa3b2", "Sleep": "#9aa3b2",
}


class ConnectionPanel(QGroupBox):
    def __init__(self, win):
        super().__init__("Connection")
        self.win = win
        lay = QVBoxLayout(self)

        form = QFormLayout()
        port_row = QHBoxLayout()
        self.port = QComboBox()
        self.port.setEditable(True)
        self.port.setMinimumWidth(150)
        self.refresh_btn = QPushButton("↻")
        self.refresh_btn.setToolTip("Refresh the serial port list")
        self.refresh_btn.setFixedWidth(32)
        self.refresh_btn.clicked.connect(self.refresh_ports)
        port_row.addWidget(self.port, 1)
        port_row.addWidget(self.refresh_btn)
        form.addRow("Port", port_row)
        self.baud = QComboBox()
        self.baud.setEditable(True)
        self.baud.addItems(["115200", "250000", "57600", "38400", "19200", "9600"])
        form.addRow("Baud", self.baud)
        lay.addLayout(form)

        sim_row = QHBoxLayout()
        self.sim = QCheckBox("Simulation (no hardware)")
        self.sim.setToolTip("Run the whole pipeline against a simulated GRBL. Nothing is sent to a port.")
        self.sim_version = QComboBox()
        self.sim_version.addItems(["1.1h", "0.9j"])
        self.sim_version.setToolTip("GRBL version to simulate")
        self.sim_speed = QComboBox()
        self.sim_speed.addItems(["1×", "5×", "20×", "100×"])
        self.sim_speed.setToolTip("Simulation speed")
        sim_row.addWidget(self.sim)
        sim_row.addWidget(self.sim_version)
        sim_row.addWidget(self.sim_speed)
        lay.addLayout(sim_row)
        self.sim.toggled.connect(self._sim_toggled)
        self._sim_toggled(False)

        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self._toggle)
        lay.addWidget(self.connect_btn)

        self.state = QLabel("Disconnected")
        self.state.setStyleSheet("font-size: 16px; font-weight: bold; color: #9aa3b2;")
        lay.addWidget(self.state)
        self.version = QLabel("")
        lay.addWidget(self.version)
        pos = QWidget()
        pf = QFormLayout(pos)
        pf.setContentsMargins(0, 0, 0, 0)
        self.wpos = QLabel("-")
        self.mpos = QLabel("-")
        for l in (self.wpos, self.mpos):
            l.setStyleSheet("font-family: monospace;")
        pf.addRow("Work (WPos)", self.wpos)
        pf.addRow("Machine (MPos)", self.mpos)
        lay.addWidget(pos)

        self.alarm = QLabel("")
        self.alarm.setWordWrap(True)
        self.alarm.setStyleSheet("background: #3a1518; color: #ffb3b3; border: 1px solid #7a2a2f;"
                                 "border-radius: 4px; padding: 6px;")
        self.alarm.hide()
        lay.addWidget(self.alarm)
        row = QHBoxLayout()
        self.unlock_btn = QPushButton("Unlock ($X)")
        self.unlock_btn.setToolTip("Clear the alarm lock. Motion stays blocked until you home.")
        self.unlock_btn.clicked.connect(win.unlock)
        self.reset_btn = QPushButton("Soft reset")
        self.reset_btn.setToolTip("Send Ctrl-X. Needed after hard/soft-limit alarms. Requires re-homing.")
        self.reset_btn.clicked.connect(win.soft_reset)
        row.addWidget(self.unlock_btn)
        row.addWidget(self.reset_btn)
        lay.addLayout(row)
        self.refresh_ports()
        self.set_connected(False)

    def _sim_toggled(self, on: bool):
        self.sim_version.setEnabled(on)
        self.sim_speed.setEnabled(on)
        self.port.setEnabled(not on)
        self.baud.setEnabled(not on)
        self.refresh_btn.setEnabled(not on)

    def refresh_ports(self):
        current = self.port.currentText()
        self.port.clear()
        for dev, desc in list_serial_ports():
            self.port.addItem(f"{dev}", dev)
            self.port.setItemData(self.port.count() - 1, desc, 3)  # tooltip role
        if current:
            self.port.setCurrentText(current)
        elif self.win.profile.port:
            self.port.setCurrentText(self.win.profile.port)

    def _toggle(self):
        if self.win.ctl is not None:
            self.win.disconnect_machine()
        else:
            self.win.connect_machine()

    @property
    def sim_speed_value(self) -> float:
        return float(self.sim_speed.currentText().rstrip("×"))

    def set_connecting(self):
        self.connect_btn.setEnabled(False)
        self.connect_btn.setText("Connecting…")
        self.state.setText("Connecting…")

    def set_connected(self, on: bool):
        self.connect_btn.setEnabled(True)
        self.connect_btn.setText("Disconnect" if on else "Connect")
        for w in (self.sim, self.port, self.baud, self.refresh_btn, self.sim_version, self.sim_speed):
            w.setEnabled(not on)
        if not on:
            self._sim_toggled(self.sim.isChecked())
            self.state.setText("Disconnected")
            self.state.setStyleSheet("font-size: 16px; font-weight: bold; color: #9aa3b2;")
            self.version.setText("")
            self.wpos.setText("-")
            self.mpos.setText("-")
            self.alarm.hide()
        self.unlock_btn.setEnabled(on)
        self.reset_btn.setEnabled(on)

    def set_version(self, text: str):
        self.version.setText(text)

    def update_status(self, st):
        color = STATE_COLORS.get(st.state, "#d7dbe3")
        label = st.state + (f":{st.substate}" if st.substate else "")
        self.state.setText(label)
        self.state.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {color};")
        f = lambda v: "  ".join(f"{a}{c:8.3f}" for a, c in zip("XYZ", v)) if v else "-"
        self.wpos.setText(f(st.wpos))
        self.mpos.setText(f(st.mpos))
        if st.state != "Alarm" and self.alarm.isVisible() and not self.alarm.property("sticky"):
            self.alarm.hide()

    def show_alarm(self, title: str, explanation: str, sticky: bool = False):
        self.alarm.setText(f"<b>{title}</b><br>{explanation}")
        self.alarm.setProperty("sticky", sticky)
        self.alarm.show()

    def append_alarm(self, text: str):
        if self.alarm.isVisible():
            self.alarm.setText(self.alarm.text() + f"<br><i>{text}</i>")
        else:
            self.show_alarm("Reset required", text, sticky=True)

    def clear_alarm(self):
        self.alarm.setProperty("sticky", False)
        self.alarm.hide()

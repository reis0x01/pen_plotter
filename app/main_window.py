"""Main window: wires the panels to the core. All machine logic lives in plotter/."""
from __future__ import annotations

if __name__ == "__main__" and not __package__:
    # Started as a script (e.g. VS Code "Run Python File"): relaunch as `python -m app`
    # so the project root is importable and relative imports work.
    import os as _os
    import runpy as _runpy
    import sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    _runpy.run_module("app", run_name="__main__", alter_sys=True)
    raise SystemExit(0)

import threading
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QCheckBox, QDockWidget, QFileDialog, QHBoxLayout, QInputDialog, QLabel,
                               QMainWindow, QMessageBox, QScrollArea, QSplitter, QTabWidget, QVBoxLayout,
                               QWidget)

from plotter.calibration import calibration_gcode
from plotter.config import MachineProfile
from plotter.gcode.estimate import format_duration
from plotter.gcode.generator import fmt
from plotter.grbl.connection import RT_RESET, SerialTransport, SimulatedGrbl, TransportError
from plotter.grbl.protocol import Alarm, Status
from plotter.grbl.streamer import BusyError, Event, GrblController, InterlockError, JobState
from plotter.drawing.image_import import IMAGE_FILTER, ImageProcessor, load_image
from plotter.drawing.pdf_import import DrawingError, load_pdf, pdf_page_count
from plotter.pipeline import (Plan, centered_work_offset, clamp_drawing_area, drawing_area,
                              machine_bounds, page_fit_problems, plan_drawing, plan_gcode, plan_text)
from plotter.stt.commands import parse_transcript
from plotter.text.layout import LayoutError

from . import theme
from .bridge import ControllerBridge, SpeechController
from .image_dialog import PictureDialog
from .panels.connection import ConnectionPanel
from .panels.drawing import DrawingPanel
from .panels.layout_options import LayoutPanel
from .panels.log import LogPanel
from .panels.machine import MachinePanel
from .panels.plot import PlotPanel
from .panels.profile_editor import ProfilePanel
from .panels.settings import SettingsPanel
from .panels.text_input import TextInputPanel
from .preview import PreviewWidget


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{theme.APP_NAME} — {theme.OWNER}")
        self.resize(1400, 900)
        self.profile = MachineProfile.load()
        self.ctl: GrblController | None = None
        self.plan: Plan | None = None
        self.page_index = 0
        self.source = "text"                  # "text", "file" (G-code) or "drawing" (PDF)
        self.file_name = ""
        self.drawing = None                   # PdfDrawing or ImageDrawing when source == "drawing"
        self.picture = None                   # (ImageProcessor, ImageSettings, area size mm) of a picture job
        self.drawing_mode = "fit"
        self._plan_wco = None
        self.job_page = 0
        self.job_plan: Plan | None = None     # the plan the running job belongs to
        self.job_started = 0.0
        self.job_estimate = 0.0
        self._capture_origin_after = None      # time the origin G10 was sent
        self._last_summary = ""

        self.bridge = ControllerBridge()
        self.bridge.event.connect(self.on_event)
        self.speech = SpeechController(self)

        self._replan_timer = QTimer(self, singleShot=True, interval=250)
        self._replan_timer.timeout.connect(self.replan)
        self._save_timer = QTimer(self, singleShot=True, interval=1000)
        self._save_timer.timeout.connect(self.save_profile)
        self._rec_timer = QTimer(self, singleShot=True)
        self._rec_timer.timeout.connect(self.stop_recording)

        self._build_ui()
        self._wire_speech()
        self.update_enables()
        self.replan()

    # ------------------------------------------------------------------ UI build
    def _build_ui(self):
        self.connection = ConnectionPanel(self)
        self.machine = MachinePanel(self)
        self.plot = PlotPanel(self)
        left = QWidget()
        ll = QVBoxLayout(left)
        for w in (self.connection, self.machine, self.plot):
            ll.addWidget(w)
        ll.addStretch(1)
        left_scroll = QScrollArea()
        left_scroll.setWidget(left)
        left_scroll.setWidgetResizable(True)
        left_scroll.setMinimumWidth(360)

        center = QWidget()
        cl = QVBoxLayout(center)
        cl.setContentsMargins(0, 0, 0, 0)
        self.preview = PreviewWidget()
        self.preview.area_edited.connect(self.set_drawing_area)
        cl.addWidget(self.preview, 1)
        opts = QHBoxLayout()
        self.show_travel = QCheckBox("Show travel moves")
        self.show_travel.setChecked(True)
        self.show_travel.toggled.connect(self._toggle_travel)
        opts.addWidget(self.show_travel)
        opts.addStretch(1)
        cl.addLayout(opts)

        self.text_input = TextInputPanel(self)
        self.layout_panel = LayoutPanel(self)
        text_tab = QSplitter(Qt.Vertical)
        text_tab.addWidget(self.text_input)
        text_tab.addWidget(self.layout_panel)
        text_tab.setStretchFactor(0, 3)
        self.profile_panel = ProfilePanel(self)
        profile_scroll = QScrollArea()
        profile_scroll.setWidget(self.profile_panel)
        profile_scroll.setWidgetResizable(True)
        self.settings_panel = SettingsPanel(self)
        self.drawing_panel = DrawingPanel(self)
        self.tabs = QTabWidget()
        self.tabs.addTab(text_tab, "Text && voice")
        self.tabs.addTab(self.drawing_panel, "Picture && drawing")
        self.tabs.addTab(profile_scroll, "Machine profile")
        self.tabs.addTab(self.settings_panel, "GRBL settings")
        self.tabs.setMinimumWidth(420)
        self.tabs.currentChanged.connect(lambda _: self._update_area_overlay())

        split = QSplitter(Qt.Horizontal)
        split.addWidget(left_scroll)
        split.addWidget(center)
        split.addWidget(self.tabs)
        split.setStretchFactor(1, 1)
        split.setSizes([380, 560, 460])
        banner = QLabel(theme.banner_html())
        banner.setObjectName("banner")
        banner.setStyleSheet(f"#banner {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, "
                             f"stop:0 {theme.BASE}, stop:1 #16223a); border-bottom: 2px solid "
                             f"{theme.KALI_BLUE}; padding: 7px 14px; }}")
        root = QWidget()
        rl = QVBoxLayout(root)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(4)
        rl.addWidget(banner)
        rl.addWidget(split, 1)
        self.setCentralWidget(root)

        self.log = LogPanel(self)
        dock = QDockWidget("Serial log", self)
        dock.setWidget(self.log)
        dock.setObjectName("log")
        self.addDockWidget(Qt.BottomDockWidgetArea, dock)
        self.resizeDocks([dock], [180], Qt.Vertical)

        menu = self.menuBar().addMenu("&File")
        for text, slot, key in (("Open picture (photo, scan, sketch)…", self.open_picture, "Ctrl+I"),
                                ("Adjust picture…", self.adjust_picture, None),
                                ("Open PDF drawing…", self.open_pdf, None),
                                ("Open G-code file…", self.open_gcode, QKeySequence.Open),
                                ("Export G-code…", self.export_gcode, QKeySequence.Save),
                                ("Load calibration pattern (100 mm lines)", self.load_calibration, None),
                                ("Back to text job", self.back_to_text, None),
                                ("Save profile", self.save_profile, None),
                                ("Quit", self.close, QKeySequence.Quit)):
            act = QAction(text, self)
            if key is not None:
                act.setShortcut(key)
            act.triggered.connect(slot)
            menu.addAction(act)
        view = self.menuBar().addMenu("&View")
        view.addAction(dock.toggleViewAction())
        help_menu = self.menuBar().addMenu("&Help")
        about = QAction(f"About {theme.APP_NAME}", self)
        about.triggered.connect(self.show_about)
        help_menu.addAction(about)
        owner = QLabel(f"© {theme.OWNER}")
        owner.setStyleSheet(f"color: {theme.KALI_BLUE}; font-weight: bold; padding-right: 8px;")
        self.statusBar().addPermanentWidget(owner)
        self.statusBar().showMessage("Ready. Connect to the machine or enable simulation.")

    def show_about(self):
        QMessageBox.about(
            self, f"About {theme.APP_NAME}",
            f"<h2 style='color:{theme.KALI_BLUE}'>{theme.APP_NAME}</h2>"
            f"<p>Version {theme.VERSION}</p>"
            f"<p>Owner and author: <b>{theme.OWNER}</b></p>"
            "<p>Turns speech, typed text, pictures and PDF drawings into pen plots on a GRBL "
            "pen plotter.</p>")

    def _wire_speech(self):
        s = self.speech
        s.level.connect(self.text_input.set_level)
        s.recording_changed.connect(self.text_input.set_recording)
        s.busy.connect(self.text_input.set_busy)
        s.error.connect(self._speech_error)
        s.transcript.connect(self._on_transcript)

    def _toggle_travel(self, on):
        self.preview.show_travel = on
        self.preview.update()

    # ---------------------------------------------------------------- helpers
    def info(self, msg: str, log: bool = True):
        self.statusBar().showMessage(msg, 10000)
        if log:
            self.log.add("··", msg)

    def warn(self, title: str, msg: str):
        self.info(msg)
        QMessageBox.warning(self, title, msg)

    @property
    def connected(self) -> bool:
        return self.ctl is not None and self.ctl.connected

    def _pen_up_line(self) -> str:
        return f"G90 G1 Z{fmt(self.profile.pen_up_z)} F{fmt(self.profile.z_feed)}"

    def send(self, line: str) -> bool:
        if not self.connected:
            self.info("Not connected.")
            return False
        try:
            self.ctl.send_command(line)
            return True
        except (InterlockError, BusyError, ValueError) as exc:
            self.warn("Command refused", str(exc))
            return False

    def _call(self, fn, *args) -> bool:
        if not self.connected:
            self.info("Not connected.")
            return False
        try:
            fn(*args)
            return True
        except (InterlockError, BusyError, ValueError) as exc:
            self.warn("Command refused", str(exc))
            return False

    # ------------------------------------------------------------- connection
    def connect_machine(self):
        cp = self.connection
        if cp.sim.isChecked():
            transport = SimulatedGrbl(version=cp.sim_version.currentText(), speed=cp.sim_speed_value)
            label = f"simulated GRBL {transport.version}"
        else:
            port = cp.port.currentData() or cp.port.currentText().strip()
            if not port:
                self.warn("Connect", "Choose a serial port (or enable simulation).")
                return
            try:
                baud = int(cp.baud.currentText())
            except ValueError:
                self.warn("Connect", "Baud rate must be a number.")
                return
            try:
                transport = SerialTransport(port, baud)
            except TransportError as exc:
                self.warn("Connect", str(exc))
                return
            self.profile.port, self.profile.baud = port, baud
            self._save_timer.start()
            label = f"{port} @ {baud}"
        ctl = GrblController(transport, self.bridge.callback,
                             homing_required=self.machine.interlock.isChecked())
        ctl.pen_up_command = self._pen_up_line()
        self.ctl = ctl
        cp.set_connecting()
        self.info(f"Connecting to {label}…")
        threading.Thread(target=ctl.start, kwargs={"banner_timeout": 3.0}, daemon=True).start()

    def disconnect_machine(self):
        if self.ctl is None:
            return
        if self.ctl.job_active:
            if QMessageBox.question(self, "Disconnect", "A job is running. Stop it and disconnect?") \
                    != QMessageBox.Yes:
                return
            self.ctl.stop()
            time.sleep(0.8)
        ctl, self.ctl = self.ctl, None
        ctl.close()
        self.connection.set_connected(False)
        self.machine.set_homed(False)
        self.preview.set_pen(None)
        self.info("Disconnected.")
        self.update_enables()

    # ---------------------------------------------------------------- events
    def on_event(self, ev):
        if self.ctl is None:
            return
        k, d = ev.kind, ev.data
        if k == "rx":
            self.log.add("<<", d)
        elif k == "tx":
            self.log.add(">>", d)
        elif k == "status":
            self._on_status(d)
        elif k == "alarm":
            self._on_alarm(d)
        elif k == "error":
            err, line = d
            self.info(f"GRBL error for '{line}': {err.text}. {err.explanation}", log=False)
        elif k == "banner":
            self.connection.set_version(f"GRBL {d.version}")
            self.machine.set_homed(False)
        elif k == "version":
            self.connection.set_version(f"GRBL {d}")
            self.update_enables()
        elif k == "settings":
            self.settings_panel.show_settings(d)
            self.machine.refresh_profile()
            self.preview.homing_dir_mask = self.homing_dir_mask()
            self.preview.update()
            self._schedule_replan()
        elif k == "message":
            self.info(f"GRBL: {d.text}", log=False)
            if "Reset to continue" in d.text:
                self.connection.append_alarm("GRBL needs a reset: press Soft reset, then home the machine.")
        elif k == "info":
            self.info(d)
        elif k == "homed":
            self.machine.set_homed(d)
            if d:
                self.connection.clear_alarm()
            self.update_enables()
        elif k == "job":
            self._on_job(d)
        elif k == "connection":
            self._on_connection(d)

    def _on_connection(self, d):
        if d.get("connected"):
            self.connection.set_connected(True)
            if not d.get("banner"):
                self.warn("Connect", "No GRBL banner received. Check the port and baud rate "
                                     "(GRBL uses 115200 by default).")
            else:
                self.info("Connected. Home the machine ($H) before any motion.")
        else:
            err = d.get("error")
            if err:
                self.warn("Connection lost", f"Connection lost: {err}")
                ctl, self.ctl = self.ctl, None
                if ctl:
                    ctl.close()
                self.connection.set_connected(False)
                self.machine.set_homed(False)
        self.update_enables()

    def _on_status(self, st: Status):
        self.connection.update_status(st)
        if self._capture_origin_after and st.wco and \
                time.monotonic() - self._capture_origin_after > 0.3 and not self.ctl._inflight:
            self._capture_origin_after = None
            p = self.profile
            p.paper_origin_mpos = [round(st.wco[0], 3), round(st.wco[1], 3)]
            self.profile_panel.load_from_profile()
            self.save_profile()
            self.info(f"Paper origin set: work X0 Y0 = machine X{p.paper_origin_mpos[0]:.3f} "
                      f"Y{p.paper_origin_mpos[1]:.3f}. Saved in the profile.")
            self._schedule_replan()
        self.preview.set_pen(st.wpos)
        if st.state == "Alarm" and not self.connection.alarm.isVisible():
            self.connection.show_alarm("Alarm lock", "GRBL is locked. Home the machine ($H). "
                                       "Unlock ($X) only if you know the position is safe.")
        if st.wco and self._plan_wco is not None and st.wco != self._plan_wco and not self.ctl.job_active:
            self._schedule_replan(restart=False)
        self.update_enables()

    def _on_alarm(self, alarm: Alarm):
        self.connection.show_alarm(f"ALARM{'' if alarm.code is None else f' {alarm.code}'}: {alarm.name}",
                                   alarm.explanation, sticky=True)
        self.machine.set_homed(False)
        self.info(f"ALARM: {alarm.name}. {alarm.explanation}", log=False)
        self.update_enables()

    def _on_job(self, j):
        self.preview.set_done_line(j.last_line)
        elapsed = time.monotonic() - self.job_started if self.job_started else 0.0
        self.plot.set_progress(j.fraction, elapsed, self.job_estimate)
        failed = j.state == JobState.FAILED
        self.plot.set_message(j.message, error=failed)
        self.update_enables()
        if j.state == JobState.FAILED:
            self.info(j.message, log=True)
            QMessageBox.critical(self, "Plot failed", j.message)
        elif j.state == JobState.STOPPED:
            self.info(j.message)
        elif j.state == JobState.DONE:
            self.plot.set_progress(1.0, elapsed, self.job_estimate)
            self.info(f"Page {self.job_page + 1} finished in {format_duration(elapsed)}.")
            plan = self.job_plan
            if plan is not None and plan is self.plan and self.source == "text" \
                    and self.job_page + 1 < len(plan.pages):
                nxt = self.job_page + 1
                if QMessageBox.question(
                        self, "Next page",
                        f"Page {self.job_page + 1} of {len(plan.pages)} is finished.\n\n"
                        "Put a new sheet in exactly the same position, then press Yes to plot "
                        f"page {nxt + 1}.") == QMessageBox.Yes:
                    self.show_page(nxt)
                    self.start_plot(confirm=False)

    # --------------------------------------------------------- machine control
    def home(self):
        self._call(self.ctl.home) if self.ctl else self.info("Not connected.")

    def unlock(self):
        if self.ctl:
            self.ctl.unlock()
            self.connection.clear_alarm()

    def soft_reset(self):
        if self.ctl:
            self.ctl.realtime(RT_RESET, "<soft reset 0x18>")
            self.connection.clear_alarm()
            self.info("Soft reset sent. Home the machine before any motion.")

    def set_interlock(self, on: bool):
        if not on:
            ans = QMessageBox.warning(
                self, "Disable homing interlock",
                "Allow motion without homing?\n\nThe machine position is unknown until it is "
                "homed. Only do this if you know exactly where the pen is. This setting is not "
                "saved and resets when the app restarts.",
                QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
            if ans != QMessageBox.Yes:
                self.machine.interlock.blockSignals(True)
                self.machine.interlock.setChecked(True)
                self.machine.interlock.blockSignals(False)
                return
            self.info("Homing interlock DISABLED for this session.")
        else:
            self.info("Homing interlock enabled.")
        if self.ctl:
            self.ctl.homing_required = on
        self.update_enables()

    def jog(self, direction: str):
        axis, sign = self.profile.jog_vector(direction)
        if self.ctl:
            self._call(self.ctl.jog, axis, sign * self.machine.step_mm, self.profile.jog_feed)

    def jog_z(self, sign: int):
        if self.ctl:
            self._call(self.ctl.jog, "Z", sign * self.machine.step_mm, self.profile.jog_z_feed)

    def jog_cancel(self):
        if self.ctl and not self.ctl.jog_cancel():
            self.info("Jog cancel needs GRBL 1.1. Keep jog steps small on 0.9.")

    def pen_up(self):
        self.send(self._pen_up_line())

    def pen_down(self):
        self.send(f"G90 G1 Z{fmt(self.profile.pen_down_z)} F{fmt(self.profile.z_feed)}")

    def go_park(self):
        if self.send(self._pen_up_line()):
            self.send(f"G90 G0 X{fmt(self.profile.park_x)} Y{fmt(self.profile.park_y)}")

    def homing_dir_mask(self) -> int:
        if self.ctl and 23 in self.ctl.settings:
            return int(self.ctl.settings[23])
        return 0

    def set_origin(self, corner: str, inset: bool):
        if not self.ctl or not self.ctl.status or not self.ctl.status.mpos:
            self.info("No machine position yet.")
            return
        if not self.ctl.motion_allowed():
            self.warn("Set paper origin", "Home the machine first so the origin is repeatable.")
            return
        if self.ctl.status.state != "Idle":
            self.warn("Set paper origin", "Wait until the machine is Idle (not moving).")
            return
        p = self.profile
        wx, wy = p.corner_work_coords(corner, inset)
        mx, my, _ = self.ctl.status.mpos
        where = (f"{p.safety_margin:g} mm inside the {corner} corner" if inset
                 else f"exactly on the {corner} corner")
        problems = page_fit_problems(p, self.ctl.settings, (mx - wx, my - wy))
        if problems:
            home = p.home_corner_name(self.homing_dir_mask())
            self.log.add("##", f"Origin refused: pen at MPos X{mx:g} Y{my:g} as {where} -> "
                               + "; ".join(problems))
            QMessageBox.warning(
                self, "The page would not fit",
                f"With the pen {where}, the page does not fit in the machine:\n\n• "
                + "\n• ".join(problems)
                + f"\n\nCheck that you picked the right corner (home is the {home} corner) and "
                  "that the jog arrows move the pen the way they point (Machine profile → "
                  "Axis orientation). Nothing was sent.")
            return
        if QMessageBox.question(
                self, "Set paper origin",
                f"The pen is {where} of the sheet?\n\nThis sends G10 L20 P1 X{fmt(wx)} Y{fmt(wy)} "
                "(GRBL stores it in EEPROM).") != QMessageBox.Yes:
            return
        if self.send(f"G10 L20 P1 X{fmt(wx)} Y{fmt(wy)}"):
            # The exact origin is the work offset GRBL reports after executing the G10.
            self._capture_origin_after = time.monotonic()

    def center_page(self):
        if not self.ctl or not self.ctl.motion_allowed():
            self.warn("Center page", "Home the machine first.")
            return
        if self.ctl.status and self.ctl.status.state != "Idle":
            self.warn("Center page", "Wait until the machine is Idle.")
            return
        wco = centered_work_offset(self.profile, self.ctl.settings)
        if wco is None:
            self.warn("Center page", "Can't center the page: $130/$131 are unknown (read the GRBL "
                      "settings) or the safe area is larger than the machine travel.")
            return
        p = self.profile
        if QMessageBox.question(
                self, "Center page in machine travel",
                f"Place the page ({p.paper_width:g} × {p.paper_height:g} mm) in the middle of the "
                f"machine travel?\n\nWork origin becomes machine X{fmt(wco[0])} Y{fmt(wco[1])} "
                f"(the {p.origin_corner_name()} corner of the page, as the preview shows).\n\n"
                "Make sure the paper covers the whole plotting area.") != QMessageBox.Yes:
            return
        if self.send(f"G10 L2 P1 X{fmt(wco[0])} Y{fmt(wco[1])}"):
            self._capture_origin_after = time.monotonic()

    def set_z_zero(self):
        if QMessageBox.question(
                self, "Set Z zero",
                "Make the current Z height work Z0?\n\nPen up is then "
                f"Z{fmt(self.profile.pen_up_z)} and pen down Z{fmt(self.profile.pen_down_z)}, relative to "
                "this point. Normally done right after homing.") == QMessageBox.Yes:
            self.send("G10 L20 P1 Z0")

    def restore_origin(self):
        o = self.profile.paper_origin_mpos
        if not o:
            self.info("No paper origin saved in the profile yet.")
            return
        self.send(f"G10 L2 P1 X{fmt(o[0])} Y{fmt(o[1])}")

    # --------------------------------------------------------------- settings
    def read_settings(self):
        if self.ctl:
            self._call(self.ctl.read_settings)

    def write_settings(self, changes: dict[int, str]):
        if not self.ctl:
            return
        def done(n, v):
            # Runs on the serial reader thread: report through the thread-safe bridge.
            return lambda ok, err: self.bridge.callback(Event("info", f"${n}={v} " + (
                "written." if ok else f"FAILED: {err.text if err else 'no reply'}")))
        for n, v in sorted(changes.items()):
            if not self._call(self.ctl.write_setting, n, v, done(n, v)):
                return
        self._call(self.ctl.read_settings)

    # ------------------------------------------------------------------ profile
    def profile_edited(self, replan: bool = True):
        self._save_timer.start()
        self.profile_panel.show_problems(self.profile.problems())
        self._update_area_overlay()
        if replan:
            self._schedule_replan()

    def replace_profile(self, profile: MachineProfile):
        if profile.drawing_area:
            fitted = clamp_drawing_area(profile, profile.drawing_area)
            if fitted != profile.drawing_area:
                profile.drawing_area = fitted
                self.info("The drawing area was moved inside the new paper / safety margin.")
        self.profile = profile
        self.save_profile()
        self.layout_panel.load_from_profile()
        self.drawing_panel.load_from_profile()
        self.machine.refresh_profile()
        self.profile_panel.load_from_profile()
        if self.ctl:
            self.ctl.pen_up_command = self._pen_up_line()
        self.info("Machine profile applied and saved.")
        self.replan()

    def save_profile(self):
        try:
            self.profile.save()
        except OSError as exc:
            self.info(f"Could not save profile: {exc}")

    # ------------------------------------------------------------------ planning
    def text_changed(self):
        if self.source != "text":
            self.source = "text"
            self.info("Text edited: switched back to the text job.")
        self._schedule_replan()

    def _schedule_replan(self, restart: bool = True):
        # Status-driven requests arrive 5x per second; they must not keep pushing the
        # debounce timer back, or the re-plan would never run.
        if restart or not self._replan_timer.isActive():
            self._replan_timer.start()

    def _live_wco(self):
        return self.ctl.wco if self.connected else None

    def replan(self):
        if self.ctl and self.ctl.job_active:
            return
        settings = self.ctl.settings if self.connected else None
        wco = self._live_wco()
        self._plan_wco = wco
        if self.source == "file":
            self.plan = plan_gcode(self.plan.pages[0].gcode, self.profile, settings, wco) if self.plan else None
        elif self.source == "drawing" and self.drawing is not None:
            self._refresh_picture()
            self.plan = plan_drawing(self.drawing, self.profile, settings, wco, self.drawing_mode)
        else:
            text = self.text_input.text.toPlainText()
            if not text.strip():
                self.plan = None
                self.text_input.set_warnings([])
                self.preview.set_plan(self.profile, None, "")
                self.plot.set_summary("Nothing to plot yet.", True)
                self.plot.set_pages(1, 0)
                self.update_enables()
                return
            try:
                self.plan = plan_text(text, self.profile, settings, wco)
            except (LayoutError, ValueError) as exc:
                self.plan = None
                self.preview.set_plan(self.profile, None, "")
                self.plot.set_summary(f"Cannot lay out the text: {exc}", False)
                self.update_enables()
                return
            self.text_input.set_warnings(self.plan.warnings)
        self.page_index = min(self.page_index, len(self.plan.pages) - 1) if self.plan else 0
        self.show_page(self.page_index)

    def show_page(self, index: int):
        if not self.plan:
            return
        self.page_index = max(0, min(index, len(self.plan.pages) - 1))
        page = self.plan.pages[self.page_index]
        n = len(self.plan.pages)
        what = Path(self.file_name).name if self.source in ("file", "drawing") else "text"
        title = f"{what} — page {self.page_index + 1}/{n}"
        self.preview.set_plan(self.profile, page.validation, title)
        self._update_area_overlay()
        self.plot.set_pages(n, self.page_index)
        draws = sum(1 for m in page.validation.moves if m.kind == "draw")
        env = " (incl. machine travel check)" if self._plan_wco and self.connected and \
            all(k in self.ctl.settings for k in (130, 131)) else ""
        if page.ok:
            summary = (f"{n} page(s). This page: {len(page.gcode)} lines, {draws} pen-down moves, "
                       f"estimated {format_duration(page.seconds)}. Safety checks passed{env}.")
            if self.source == "drawing" and self.plan.warnings:
                summary += "\n" + "\n".join(self.plan.warnings)
            if self.profile.problems():
                summary += "\nFix the machine profile before plotting."
        else:
            summary = ("Refused by the safety check:\n" + page.validation.summary(limit=4))
            if any("machine travel" in i.message for i in page.validation.issues):
                summary += ("\n\nOut of reach: at the current work origin the page extends past "
                            "the machine travel. First check Machine profile → Axis orientation "
                            "(the jog arrows must move the pen the way they point). Then press "
                            "'Center page in machine travel', or set the paper origin at the "
                            f"{self.profile.origin_corner_name()} corner, which is the corner "
                            "farthest from home.")
        self.plot.set_summary(summary, page.ok and not self.profile.problems())
        if summary != self._last_summary:
            self._last_summary = summary
            ctx = ""
            if self.connected:
                st = self.ctl.settings
                ctx = (f"  [WCO={self.ctl.wco} $130={st.get(130)} $131={st.get(131)} "
                       f"$23={st.get(23)} +X {self.profile.x_positive}, +Y {self.profile.y_positive}]")
            self.log.add("##", summary.replace("\n", " | ") + ctx)
        self.update_enables()

    # ------------------------------------------------------------------ plotting
    def start_plot(self, confirm: bool = True):
        if not self.connected:
            self.info("Not connected.")
            return
        problems = self.profile.problems()
        if problems:
            self.warn("Machine profile", "Fix the machine profile first:\n" + "\n".join(problems))
            return
        self.replan()                  # validate against the live work offset right now
        if not self.plan:
            self.info("Nothing to plot.")
            return
        page = self.plan.pages[self.page_index]
        if not page.ok:
            QMessageBox.critical(self, "Job refused",
                                 "This job breaks the safety rules and will not be sent.\n\n"
                                 + page.validation.summary(limit=15))
            return
        if confirm:
            extra = ""
            if not self.profile.paper_origin_mpos:
                extra += "\n\n⚠ No paper origin has been set with 'Set paper origin' yet."
            if len(self.plan.pages) > 1 and self.source == "text":
                extra += (f"\n\nThe text needs {len(self.plan.pages)} pages; you will be asked to "
                          "change the sheet after each page.")
            if QMessageBox.question(
                    self, "Start plotting",
                    f"Plot page {self.page_index + 1} of {len(self.plan.pages)}?\n"
                    f"Estimated time: {format_duration(page.seconds)}{extra}") != QMessageBox.Yes:
                return
        bounds = machine_bounds(self.profile, self.ctl.settings, self.ctl.wco)
        self.job_page = self.page_index
        self.job_plan = self.plan
        self.job_estimate = page.seconds
        self.job_started = time.monotonic()
        self.preview.set_done_line(0)
        if self._call(self.ctl.start_job, page.gcode, bounds):
            self.info(f"Plotting page {self.page_index + 1}…")

    def pause(self):
        if self.ctl:
            self.ctl.pause()

    def resume(self):
        if self.ctl:
            self._call(self.ctl.resume)

    def stop(self):
        if self.ctl:
            self.ctl.pen_up_command = self._pen_up_line()
            self.ctl.stop()

    # ------------------------------------------------------------ import/export
    def open_gcode(self):
        if self.ctl and self.ctl.job_active:
            self.info("Cannot load a file while plotting.")
            return
        path, _ = QFileDialog.getOpenFileName(self, "Open G-code", "",
                                              "G-code (*.gcode *.nc *.ngc *.gc *.txt);;All files (*)")
        if not path:
            return
        try:
            lines = Path(path).read_text(errors="replace").splitlines()
        except OSError as exc:
            self.warn("Open G-code", str(exc))
            return
        self.source, self.file_name, self.page_index = "file", path, 0
        self.plan = plan_gcode(lines, self.profile, self.ctl.settings if self.connected else None,
                               self._live_wco())
        self._plan_wco = self._live_wco()
        self.show_page(0)
        page = self.plan.pages[0]
        if not page.ok:
            QMessageBox.warning(self, "G-code refused",
                                f"{Path(path).name} fails the safety check and cannot be plotted:\n\n"
                                + page.validation.summary(limit=15))
        else:
            self.info(f"Loaded {Path(path).name}: passed the safety check.")

    def load_calibration(self):
        if self.ctl and self.ctl.job_active:
            self.info("Cannot load while plotting.")
            return
        self.source, self.file_name, self.page_index = "file", "calibration pattern", 0
        self.plan = plan_gcode(calibration_gcode(self.profile), self.profile,
                               self.ctl.settings if self.connected else None, self._live_wco())
        self._plan_wco = self._live_wco()
        self.show_page(0)
        self.info("Calibration pattern loaded: two 100 mm lines. After plotting, measure them: "
                  "new $100/$101 = current × 100 / measured length.")

    def open_pdf(self):
        if self.ctl and self.ctl.job_active:
            self.info("Cannot load a drawing while plotting.")
            return
        path, _ = QFileDialog.getOpenFileName(self, "Open PDF drawing", "", "PDF (*.pdf);;All files (*)")
        if not path:
            return
        try:
            pages = pdf_page_count(path)
        except DrawingError as exc:
            self.warn("Open PDF", str(exc))
            return
        page = 0
        if pages > 1:
            page, ok = QInputDialog.getInt(self, "Open PDF", f"The PDF has {pages} pages. Plot page:",
                                           1, 1, pages)
            if not ok:
                return
            page -= 1
        sizes = ["Fit to the page (keep proportions)", "Actual size from the PDF"]
        choice, ok = QInputDialog.getItem(self, "Drawing size", "Size on the paper:", sizes, 0, False)
        if not ok:
            return
        try:
            drawing = load_pdf(path, page)
        except DrawingError as exc:
            self.warn("Open PDF", str(exc))
            return
        self.drawing, self.drawing_mode = drawing, ("fit" if choice == sizes[0] else "actual")
        self.source, self.file_name, self.page_index = "drawing", path, 0
        self.picture = None
        self.drawing_panel.set_job(f"PDF drawing: {Path(path).name}, page {page + 1}", picture=False)
        self.replan()
        n = sum(len(s) for s in drawing.strokes)
        self.info(f"Loaded {Path(path).name} page {page + 1}: {len(drawing.strokes)} strokes, "
                  f"{n} points. Check the preview, then press Plot.")
        for w in drawing.warnings:
            self.info(w)

    def open_picture(self):
        if self.ctl and self.ctl.job_active:
            self.info("Cannot load a picture while plotting.")
            return
        path, _ = QFileDialog.getOpenFileName(self, "Open picture", "", IMAGE_FILTER)
        if not path:
            return
        try:
            processor = ImageProcessor(load_image(path), Path(path).name)
        except DrawingError as exc:
            self.warn("Open picture", str(exc))
            return
        self._edit_picture(processor, None, path)

    def adjust_picture(self):
        if self.picture is None:
            self.info("Open a picture first.")
            return
        if self.ctl and self.ctl.job_active:
            self.info("Cannot change the picture while plotting.")
            return
        processor, settings, _ = self.picture
        self._edit_picture(processor, settings, self.file_name)

    def _picture_area_mm(self) -> tuple[float, float]:
        l, t, r, b = drawing_area(self.profile)
        return r - l, b - t

    def _edit_picture(self, processor, settings, path):
        """Run the picture dialog; 'Use this drawing' makes the traced picture the job."""
        area = self._picture_area_mm()
        dlg = PictureDialog(self, processor, settings, area, self.profile)
        accepted = dlg.exec() == PictureDialog.Accepted
        res, new_settings = dlg.result, dlg.settings
        dlg.deleteLater()                    # it holds copies of the picture
        if not accepted or res is None:
            return
        self.picture = (processor, new_settings, area)
        self.drawing, self.drawing_mode = res.drawing, "fit"
        self.source, self.file_name, self.page_index = "drawing", path, 0
        self.tabs.setCurrentWidget(self.drawing_panel)
        st = res.stats
        self.drawing_panel.set_job(
            f"Picture: {processor.name}\n{st['strokes']} lines, drawn in order: outlines first, then "
            "shading from light to dark, each area finished before the pen moves on.", picture=True)
        self.replan()
        self.info(f"Picture {processor.name} traced: {st['strokes']} lines. Check the preview, then press Plot.")
        for w in res.drawing.warnings:
            self.info(w)

    def _refresh_picture(self):
        """Re-trace the picture when the drawing area's size changed: hatch spacing and the
        shortest line are in mm on paper, so they depend on the final scale."""
        if self.picture is None:
            return
        processor, settings, area = self.picture
        now = self._picture_area_mm()
        if max(abs(a - b) for a, b in zip(area, now)) < 0.05:
            return
        try:
            res = processor.process(settings, now)
        except DrawingError as exc:
            self.info(f"Could not re-trace the picture: {exc}")
            return
        self.picture = (processor, settings, now)
        self.drawing = res.drawing

    def set_drawing_area(self, area):
        """New drawing area from the Drawing tab or a drag on the preview (None = text margins)."""
        self.profile.drawing_area = clamp_drawing_area(self.profile, area) if area else None
        self.drawing_panel.load_from_profile()
        self.profile_edited(replan=self.source == "drawing")

    def _update_area_overlay(self):
        on_tab = self.tabs.currentWidget() is self.drawing_panel
        show = on_tab or self.source == "drawing"
        self.preview.set_area(drawing_area(self.profile) if show else None, editable=on_tab)

    def back_to_text(self):
        self.source, self.file_name = "text", ""
        self.replan()

    def export_gcode(self):
        if not self.plan:
            self.info("Nothing to export.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export G-code", "plot.gcode", "G-code (*.gcode)")
        if not path:
            return
        base = Path(path)
        pages = self.plan.pages
        written = []
        for page in pages:
            target = base if len(pages) == 1 else base.with_name(f"{base.stem}_p{page.index + 1}{base.suffix or '.gcode'}")
            target.write_text("\n".join(page.gcode) + "\n")
            written.append(target.name)
        self.info(f"Exported {', '.join(written)}")

    # ------------------------------------------------------------------ voice
    def start_recording(self):
        self.speech.start(self.profile.stt_language, self.profile.stt_sample_rate,
                          self.profile.stt_max_seconds)
        if self.speech.recording:
            self._rec_timer.start(int(self.profile.stt_max_seconds * 1000))
        else:
            self.text_input.set_recording(False)

    def stop_recording(self):
        self._rec_timer.stop()
        self.speech.stop()

    def _speech_error(self, msg: str):
        self.text_input.show_error(msg)
        self.info(f"Voice: {msg}")

    def _on_transcript(self, text: str):
        parsed = parse_transcript(text, self.profile.voice_commands)
        self.log.add("🎤", f"heard: {text!r}")
        if parsed.is_command:
            for action in parsed.actions:
                self.execute_voice_action(action)
            return
        self.text_input.insert_transcript(parsed.text)
        self.info("Transcript added. Review and edit it, then press Plot.")
        if self.profile.ask_to_plot_after_speech:
            self.offer_plot()

    def plot_blocker(self) -> str | None:
        """Why the current text can't be plotted right now (None if it can)."""
        if not self.connected:
            return "The machine is not connected."
        if self.ctl.job_active:
            return "A plot is already running."
        if self.ctl.homing or not self.ctl.motion_allowed():
            return "The machine is not homed. Press Home first."
        if self.profile.problems():
            return "The machine profile has problems: " + "; ".join(self.profile.problems())
        if not self.plan:
            return "There is no text to plot."
        page = self.plan.pages[self.page_index]
        if not page.ok:
            return "The safety check refused it:\n" + page.validation.summary(limit=3)
        return None

    def offer_plot(self):
        """After dictation: ask Plot now / Continue speaking / Edit text first."""
        self._replan_timer.stop()
        self.replan()                              # plan the text exactly as it is now
        blocker = self.plot_blocker()
        choice = self._ask_plot_choice(self.text_input.text.toPlainText(), blocker)
        if choice == "plot" and blocker is None:
            self.start_plot(confirm=False)
        elif choice == "speak":
            self.text_input.record_btn.setChecked(True)   # starts a new recording

    def _ask_plot_choice(self, text: str, blocker: str | None) -> str:
        box = QMessageBox(self)
        box.setWindowTitle("Plot this text?")
        shown = text if len(text) <= 400 else text[:400] + "…"
        details = ""
        if blocker is None and self.plan:
            page = self.plan.pages[self.page_index]
            details = f"\n\nEstimated time: {format_duration(page.seconds)}"
            if len(self.plan.pages) > 1:
                details += (f"\nThe text needs {len(self.plan.pages)} pages; you'll be asked to "
                            "change the sheet after each page.")
        else:
            details = f"\n\nIt can't be plotted yet: {blocker}"
        box.setText(f"“{shown}”{details}")
        plot_btn = box.addButton("▶ Plot now", QMessageBox.AcceptRole)
        plot_btn.setEnabled(blocker is None)
        speak_btn = box.addButton("🎤 Continue speaking", QMessageBox.ActionRole)
        edit_btn = box.addButton("Edit text first", QMessageBox.RejectRole)
        box.setDefaultButton(plot_btn if blocker is None else edit_btn)
        box.exec()
        clicked = box.clickedButton()
        return {plot_btn: "plot", speak_btn: "speak"}.get(clicked, "edit")

    def execute_voice_action(self, action: str):
        ctl = self.ctl
        moving = ctl is not None and (ctl.job_active or (ctl.status and ctl.status.state in ("Run", "Jog", "Hold")))
        if action == "pause" and ctl and ctl.job_active:
            self.pause()
        elif action == "resume" and ctl and ctl.job_active:
            self.resume()
        elif action == "stop" and moving:
            self.stop()
        else:
            self.info(f"Voice command '{action}': nothing to {action}.")
            return
        self.info(f"Voice command: {action}.")

    # ---------------------------------------------------------------- enables
    def update_enables(self):
        ctl = self.ctl
        connected = self.connected
        job = ctl.job if ctl else None
        running = bool(job and job.state == JobState.RUNNING)
        paused = bool(job and job.state == JobState.PAUSED)
        busy = running or paused
        motion_ok = connected and ctl.motion_allowed() and not busy and not ctl.homing
        self.machine.set_enabled(connected and not busy, motion_ok, jog_cancel=connected and ctl.is_v11)
        page_ok = bool(self.plan and self.plan.pages[self.page_index].ok and not self.profile.problems())
        self.plot.set_state(connected, page_ok and motion_ok, running, paused)
        self.settings_panel.set_enabled(connected and not busy)

    def closeEvent(self, event):
        if self.ctl and self.ctl.job_active:
            if QMessageBox.question(self, "Quit", "A job is running. Stop it and quit?") != QMessageBox.Yes:
                event.ignore()
                return
            self.ctl.stop()
            time.sleep(0.8)
        self.stop_recording()
        if self.ctl:
            self.ctl.close()
        self.save_profile()
        event.accept()

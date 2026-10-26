"""Headless GUI smoke test: the whole pipeline through the real main window, in simulation."""
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from plotter.grbl.streamer import JobState  # noqa: E402


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICE_PLOTTER_PROFILE", str(tmp_path / "profile.json"))
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    box = QtWidgets.QMessageBox
    monkeypatch.setattr(box, "question", staticmethod(lambda *a, **k: box.Yes))
    dialogs = []
    monkeypatch.setattr(box, "warning", staticmethod(lambda *a, **k: dialogs.append(a[2]) or box.Ok))
    monkeypatch.setattr(box, "critical", staticmethod(lambda *a, **k: dialogs.append(a[2]) or box.Ok))
    from app.main_window import MainWindow
    w = MainWindow()
    w.show()

    def pump(seconds, cond):
        end = time.time() + seconds
        while time.time() < end:
            app.processEvents()
            if cond():
                return True
            time.sleep(0.01)
        return cond()

    w.pump, w.dialogs = pump, dialogs
    yield w
    w.close()
    app.processEvents()


@pytest.mark.parametrize("version", ["1.1h", "0.9j"])
def test_text_to_plot_in_simulation(window, version):
    w = window
    w.connection.sim.setChecked(True)
    w.connection.sim_version.setCurrentText(version)
    w.connection.sim_speed.setCurrentText("100×")
    w.connect_machine()
    assert w.pump(5, lambda: w.connected and w.ctl.status is not None and 130 in w.ctl.settings)
    assert not w.plot.start_btn.isEnabled()                 # interlock: not homed yet

    w.home()
    assert w.pump(5, lambda: w.ctl.homed)
    w.send("G90 G0 X-250 Y-278")
    assert w.pump(5, lambda: w.ctl.status.mpos[:2] == (-250.0, -278.0) and w.ctl.status.state == "Idle")
    w.set_origin("front-left", True)
    assert w.pump(3, lambda: w.profile.paper_origin_mpos == [-260.0, -288.0])

    w.text_input.text.setPlainText("Voice plotter smoke test")
    assert w.pump(3, lambda: w.plan is not None and w.plan.ok and w.plot.start_btn.isEnabled())
    w.start_plot()
    assert w.pump(30, lambda: w.ctl.job.state == JobState.DONE), w.ctl.job.message
    assert not w.ctl.t.overflowed
    assert w.pump(2, lambda: w.ctl.status.wpos[:2] == (w.profile.park_x, w.profile.park_y))
    assert w.dialogs == []


def _connect_and_home(w):
    w.connection.sim.setChecked(True)
    w.connection.sim_speed.setCurrentText("100×")
    w.connect_machine()
    assert w.pump(5, lambda: w.connected and w.ctl.status is not None and 130 in w.ctl.settings)
    w.home()
    assert w.pump(5, lambda: w.ctl.homed and w.ctl.status.state == "Idle")


def test_center_page_fixes_origin_next_to_home(window):
    """Reported on hardware: an origin left next to home -> 'out of reach' for everything."""
    w = window
    _connect_and_home(w)
    w.send("G10 L20 P1 X10 Y10")                          # stale origin at the home corner
    assert w.pump(3, lambda: w.ctl.wco is not None and w.ctl.wco[0] > -20)
    w.text_input.text.setPlainText("Hello")
    assert w.pump(3, lambda: w.plan is not None and not w.plan.ok)
    assert "Out of reach" in w.plot.summary.text()
    assert not w.plot.start_btn.isEnabled()

    w.center_page()
    assert w.pump(3, lambda: w.profile.paper_origin_mpos == [-245.0, -288.5])
    assert w.pump(3, lambda: w.plan is not None and w.plan.ok and w.plot.start_btn.isEnabled())
    w.start_plot()
    assert w.pump(30, lambda: w.ctl.job.state == JobState.DONE), w.ctl.job.message


def test_owner_machine_origin_at_home_corner(window):
    """Owner's setup: +X moves left, home = back-left. Sheet's back-left corner under the pen
    after homing, 'Set paper origin' with the default corner -> plottable."""
    w = window
    w.profile.x_positive = "left"
    w.machine.refresh_profile()
    _connect_and_home(w)
    assert w.machine.origin_corner.currentData() == "back-left"     # defaults to home corner
    w.machine.origin_mode.setCurrentIndex(1)                        # 10 mm inside
    w.set_origin(w.machine.origin_corner.currentData(), True)
    assert w.pump(3, lambda: w.profile.paper_origin_mpos is not None
                  and abs(w.profile.paper_origin_mpos[0] - (-1 - 200)) < 1e-6)
    w.text_input.text.setPlainText("welcome to Turkey")
    assert w.pump(3, lambda: w.plan is not None and w.plan.ok), w.plot.summary.text()
    w.start_plot()
    assert w.pump(30, lambda: w.ctl.job.state == JobState.DONE), w.ctl.job.message
    assert w.dialogs == []


def test_wrong_corner_is_refused_before_sending(window):
    w = window
    w.profile.x_positive = "left"
    _connect_and_home(w)
    sent_before = len(w.ctl.t.received_lines)
    w.set_origin("front-right", True)                               # pen is at home (back-left)
    assert w.dialogs and "does not fit" in w.dialogs[0]
    w.pump(0.5, lambda: False)
    assert not any(l.startswith("G10") for l in w.ctl.t.received_lines[sent_before:])


def test_after_speech_ask_then_plot_now(window, monkeypatch):
    """Speak -> dialog -> 'Plot now' starts the job without pressing the Plot button."""
    w = window
    w.connection.sim.setChecked(True)
    w.connection.sim_speed.setCurrentText("100×")
    w.connect_machine()
    assert w.pump(5, lambda: w.connected and w.ctl.status is not None and 130 in w.ctl.settings)

    asked = []
    monkeypatch.setattr(type(w), "_ask_plot_choice", lambda self, text, blocker: asked.append(blocker) or "plot")
    w.speech.transcript.emit("hello before homing")
    assert w.pump(2, lambda: asked)
    assert "not homed" in asked[0]                     # refused: interlock still applies
    assert w.ctl.job.state == JobState.IDLE

    w.home()
    assert w.pump(5, lambda: w.ctl.homed and w.ctl.status.state == "Idle")
    w.center_page()
    assert w.pump(3, lambda: w.profile.paper_origin_mpos == [-245.0, -288.5])
    w.text_input.text.clear()
    asked.clear()
    w.speech.transcript.emit("welcome to Turkey")
    assert w.pump(2, lambda: asked == [None])
    assert w.pump(30, lambda: w.ctl.job.state == JobState.DONE), w.ctl.job.message


def test_after_speech_continue_speaking_and_opt_out(window, monkeypatch):
    w = window
    choices = []
    monkeypatch.setattr(type(w), "_ask_plot_choice", lambda self, text, blocker: choices.append(text) or "speak")
    started = []
    monkeypatch.setattr(type(w), "start_recording", lambda self: started.append(True))
    w.speech.transcript.emit("first part")
    assert w.pump(2, lambda: started == [True])
    assert choices == ["first part"]
    w.text_input.ask_plot.setChecked(False)
    assert w.profile.ask_to_plot_after_speech is False
    w.speech.transcript.emit("second part")
    w.pump(0.5, lambda: False)
    assert choices == ["first part"]                  # no dialog when turned off
    assert w.text_input.text.toPlainText() == "first part second part"


def test_open_pdf_drawing_and_plot(window, monkeypatch, tmp_path):
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    pg = doc.new_page(width=300, height=200)
    pg.draw_rect(pymupdf.Rect(20, 20, 280, 180))
    pg.draw_circle((150, 100), 60)
    doc.save(str(tmp_path / "art.pdf"))
    QtWidgets.QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (str(tmp_path / "art.pdf"), ""))
    monkeypatch.setattr(QtWidgets.QInputDialog, "getItem",
                        staticmethod(lambda *a, **k: ("Fit to the page (keep proportions)", True)))
    w = window
    _connect_and_home(w)
    w.center_page()
    assert w.pump(3, lambda: w.profile.paper_origin_mpos == [-245.0, -288.5])
    w.open_pdf()
    assert w.pump(3, lambda: w.source == "drawing" and w.plan is not None and w.plan.ok), w.plot.summary.text()
    assert "Drawing size on paper" in w.plot.summary.text()
    w.start_plot()
    assert w.pump(60, lambda: w.ctl.job.state == JobState.DONE), w.ctl.job.message
    w.text_input.text.setPlainText("back to text")
    assert w.pump(2, lambda: w.source == "text")


def test_open_picture_remove_colour_choose_area_and_plot(window, monkeypatch, tmp_path):
    """Picture -> dialog (remove a colour) -> drawing area dragged on the preview -> plot."""
    cv2 = pytest.importorskip("cv2")
    import numpy as np
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    from app.image_dialog import PictureDialog

    img = np.full((300, 400, 3), 255, np.uint8)
    cv2.rectangle(img, (0, 0), (399, 80), (40, 40, 220), -1)          # red band to remove
    cv2.circle(img, (200, 190), 90, (0, 0, 0), 6)
    cv2.line(img, (60, 280), (340, 100), (0, 0, 0), 5)
    path = tmp_path / "sketch.png"
    cv2.imwrite(str(path), img)
    QtWidgets.QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (str(path), ""))

    w = window
    seen = {}

    def run_dialog(dlg):
        assert w.pump(20, lambda: dlg.result is not None), dlg.stats.text()
        in_band = lambda res: sum(1 for st in res.drawing.strokes for x, y in st if y < 80 * res.drawing.height / 300)
        before = in_band(dlg.result)
        red = next(i for i, c in enumerate(dlg.result.palette) if c.rgb[0] > 150 and c.rgb[2] < 120)
        dlg._swatches[red].click()                                      # remove the red band
        assert w.pump(20, lambda: dlg.result is not None and dlg.result.palette[red].removed)
        seen["band"] = (before, in_band(dlg.result))
        dlg.play_btn.setChecked(True)                                   # replay runs and stops
        assert w.pump(15, lambda: not dlg.play_btn.isChecked())
        assert dlg.order.value() == dlg.order.maximum()
        dlg.accept()
        return PictureDialog.Accepted

    monkeypatch.setattr(PictureDialog, "exec", run_dialog)
    _connect_and_home(w)
    w.center_page()
    assert w.pump(3, lambda: w.profile.paper_origin_mpos == [-245.0, -288.5])
    w.open_picture()
    assert w.pump(3, lambda: w.source == "drawing" and w.plan is not None and w.plan.ok), w.plot.summary.text()
    before, after = seen["band"]
    assert before > 0 and after == 0                                    # the band's line is gone
    assert w.tabs.currentWidget() is w.drawing_panel and w.drawing_panel.adjust_btn.isEnabled()

    # Drag a new drawing area on the preview: bottom half of the page, roughly.
    pv = w.preview
    scale, ox, oy = pv._transform()

    def mouse(kind, u, v, button=Qt.LeftButton):
        pos = QPointF(ox + u * scale, oy + v * scale)
        ev = QMouseEvent(kind, pos, pos, button, button if kind != QMouseEvent.MouseButtonRelease else Qt.NoButton,
                         Qt.NoModifier)
        QtWidgets.QApplication.sendEvent(pv, ev)

    def drag(u0, v0, u1, v1):
        mouse(QMouseEvent.MouseButtonPress, u0, v0)
        mouse(QMouseEvent.MouseMove, (u0 + u1) / 2, (v0 + v1) / 2)
        mouse(QMouseEvent.MouseMove, u1, v1)
        mouse(QMouseEvent.MouseButtonRelease, u1, v1)

    l, t, r, b = w.profile.drawing_area or (15, 15, 195, 282)
    drag(l, t, 30, 160)                                  # top-left corner handle: resize
    assert w.profile.drawing_area == pytest.approx([30, 160, r, b], abs=1.0)
    drag(100, 200, 90, 180)                              # inside: move by (-10, -20)
    assert w.profile.drawing_area == pytest.approx([20, 140, r - 10, b - 20], abs=1.0)
    drag(30, 30, 120, 100)                               # outside: draw a new area
    assert w.profile.drawing_area == pytest.approx([30, 30, 120, 100], abs=1.0)
    drag(120, 60, 180, 60)                               # right edge handle: wider
    area = w.profile.drawing_area
    assert area == pytest.approx([30, 30, 180, 100], abs=1.0)
    assert w.drawing_panel.preset.currentData() == "custom"
    assert w.pump(3, lambda: w.plan is not None and "fitted to the drawing area" in w.plot.summary.text())
    draws = [m for m in w.plan.pages[0].validation.moves if m.kind == "draw"]
    uv = [w.profile.work_to_page(*m.end[:2]) for m in draws]
    assert all(area[0] - 1e-6 <= u <= area[2] + 1e-6 and area[1] - 1e-6 <= v <= area[3] + 1e-6 for u, v in uv)

    w.drawing_panel.preset.setCurrentIndex(w.drawing_panel.preset.findData("tl"))
    assert w.profile.drawing_area == [10, 10, 105, 148.5]
    assert w.pump(3, lambda: w.plan is not None and w.plan.ok)
    w.start_plot()
    assert w.pump(90, lambda: w.ctl.job.state == JobState.DONE), w.ctl.job.message

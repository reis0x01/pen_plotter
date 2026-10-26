"""Thread-safe glue between the pure-Python core (threads + callbacks) and Qt.

Signals emitted from worker threads are delivered on the GUI thread through Qt's queued
connections, so widgets are only ever touched from the GUI thread.
"""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Signal

from plotter.grbl.streamer import Event
from plotter.stt.google_stt import Recorder, SttConfig, SttError, transcribe


class ControllerBridge(QObject):
    """Receives GrblController events on its threads and re-emits them as a Qt signal."""
    event = Signal(object)

    def callback(self, ev: Event) -> None:
        self.event.emit(ev)


class SpeechController(QObject):
    """Record from the microphone and transcribe with Google STT, off the GUI thread."""
    level = Signal(float)
    recording_changed = Signal(bool)
    transcript = Signal(str)
    error = Signal(str)
    busy = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._recorder: Recorder | None = None
        self._config = SttConfig()

    @property
    def recording(self) -> bool:
        return self._recorder is not None and self._recorder.recording

    def start(self, language: str, sample_rate: int, max_seconds: float) -> None:
        if self.recording:
            return
        self._config = SttConfig(language=language, sample_rate=sample_rate)
        self._recorder = Recorder(sample_rate, max_seconds, level_callback=self.level.emit)
        try:
            self._recorder.start()
        except SttError as exc:
            self._recorder = None
            self.error.emit(str(exc))
            return
        self.recording_changed.emit(True)

    def stop(self) -> None:
        if not self.recording:
            return
        rec = self._recorder
        audio = rec.stop()
        self._recorder = None
        self.recording_changed.emit(False)
        self.level.emit(0.0)
        if rec.truncated:
            self.error.emit(f"Recording was limited to {rec.max_seconds:.0f} s.")
        if len(audio) < rec.sample_rate * 2 * 0.3:   # < 0.3 s of 16-bit audio
            self.error.emit("Recording too short: nothing to transcribe.")
            return
        self.busy.emit(True)
        config = self._config

        def work():
            try:
                self.transcript.emit(transcribe(audio, config))
            except SttError as exc:
                self.error.emit(str(exc))
            except Exception as exc:
                self.error.emit(f"Speech-to-text failed: {exc}")
            finally:
                self.busy.emit(False)
        threading.Thread(target=work, daemon=True).start()

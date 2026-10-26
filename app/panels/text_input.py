from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel, QPlainTextEdit,
                               QProgressBar, QPushButton, QVBoxLayout, QWidget)

from plotter.stt.google_stt import stt_problem

LANGUAGES = ["en-US", "tr-TR", "en-GB", "de-DE", "fr-FR", "es-ES", "ar-EG"]


class TextInputPanel(QWidget):
    """Voice recording + the editable text box that is the single source for plotting."""

    def __init__(self, win):
        super().__init__()
        self.win = win
        lay = QVBoxLayout(self)

        row = QHBoxLayout()
        self.record_btn = QPushButton("🎤 Record")
        self.record_btn.setCheckable(True)
        self.record_btn.setMinimumHeight(40)
        self.record_btn.setStyleSheet("QPushButton:checked { background: #c62828; color: white; }")
        self.record_btn.toggled.connect(self._record_toggled)
        row.addWidget(self.record_btn)
        self.level = QProgressBar()
        self.level.setRange(0, 100)
        self.level.setTextVisible(False)
        self.level.setMaximumHeight(14)
        self.level.setToolTip("Microphone level")
        row.addWidget(self.level, 1)
        lay.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("Language"))
        self.language = QComboBox()
        self.language.setEditable(True)
        self.language.addItems(LANGUAGES)
        self.language.setCurrentText(win.profile.stt_language)
        self.language.currentTextChanged.connect(self._language_changed)
        row.addWidget(self.language)
        self.append = QCheckBox("Append to text")
        self.append.setChecked(True)
        self.append.setToolTip("Unchecked: a new transcript replaces the text")
        row.addWidget(self.append)
        self.ask_plot = QCheckBox("Ask to plot after speaking")
        self.ask_plot.setChecked(win.profile.ask_to_plot_after_speech)
        self.ask_plot.setToolTip("After each transcript, ask: Plot now / Continue speaking / Edit text first")
        self.ask_plot.toggled.connect(self._ask_plot_toggled)
        row.addWidget(self.ask_plot)
        row.addStretch(1)
        lay.addLayout(row)

        self.stt_status = QLabel()
        self.stt_status.setWordWrap(True)
        lay.addWidget(self.stt_status)

        lay.addWidget(QLabel("Text to plot (transcripts appear here; you can type or edit freely):"))
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText("Speak with the Record button, or type here. "
                                     "Nothing is plotted until you press Plot.")
        self.text.textChanged.connect(win.text_changed)
        lay.addWidget(self.text, 1)

        row = QHBoxLayout()
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.clicked.connect(self.text.clear)
        row.addStretch(1)
        row.addWidget(self.clear_btn)
        lay.addLayout(row)
        self.warnings = QLabel("")
        self.warnings.setWordWrap(True)
        self.warnings.setStyleSheet("color: #ffb74d;")
        lay.addWidget(self.warnings)
        self.refresh_status()

    def refresh_status(self):
        problem = stt_problem()
        if problem:
            self.stt_status.setText(f"⚠ Voice input unavailable: {problem}")
            self.stt_status.setStyleSheet("color: #ffb74d;")
        else:
            self.stt_status.setText("Voice: Google speech recognition (no key needed, internet required).")
            self.stt_status.setStyleSheet("color: #4cd07d;")

    def _language_changed(self, text: str):
        self.win.profile.stt_language = text.strip() or "en-US"
        self.win.profile_edited(replan=False)

    def _ask_plot_toggled(self, on: bool):
        self.win.profile.ask_to_plot_after_speech = on
        self.win.profile_edited(replan=False)

    def _record_toggled(self, on: bool):
        if on:
            self.win.start_recording()
        else:
            self.win.stop_recording()

    def set_recording(self, on: bool):
        self.record_btn.blockSignals(True)
        self.record_btn.setChecked(on)
        self.record_btn.setText("■ Stop recording" if on else "🎤 Record")
        self.record_btn.blockSignals(False)

    def set_busy(self, busy: bool):
        self.record_btn.setEnabled(not busy)
        if busy:
            self.stt_status.setText("Transcribing…")
            self.stt_status.setStyleSheet("color: #6ea8ff;")
        else:
            self.refresh_status()

    def set_level(self, level: float):
        self.level.setValue(int(level * 100))

    def show_error(self, msg: str):
        self.stt_status.setText(f"⚠ {msg}")
        self.stt_status.setStyleSheet("color: #ff6b6b;")

    def insert_transcript(self, text: str):
        if not text:
            return
        if not self.append.isChecked() or not self.text.toPlainText().strip():
            self.text.setPlainText(text)
        else:
            current = self.text.toPlainText()
            sep = "" if current.endswith(("\n", " ")) or text.startswith("\n") else " "
            self.text.setPlainText(current + sep + text)
        self.text.moveCursor(self.text.textCursor().MoveOperation.End)

    def set_warnings(self, warnings: list[str]):
        self.warnings.setText("\n".join(warnings))

"""Microphone recording and Google speech recognition.

Transcription uses Google's keyless Web Speech endpoint (the one Chrome uses) through the
SpeechRecognition library: no Google account, API key or credentials file is needed, only
an internet connection. Audio is sent over HTTPS.

Audio and speech libraries are imported lazily so the rest of the app (manual text,
plotting, simulation, tests) works on machines without them.
"""
from __future__ import annotations

import importlib.util
import threading
from dataclasses import dataclass
from typing import Callable

ENDPOINT = "https://www.google.com/speech-api/v2/recognize"


class SttError(RuntimeError):
    """A user-presentable speech-to-text failure."""


def stt_problem() -> str | None:
    """None if speech recognition can be used, otherwise what is missing."""
    if importlib.util.find_spec("speech_recognition") is None:
        return "SpeechRecognition is not installed (pip install -r requirements.txt)."
    return None


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------

class Recorder:
    """Record 16-bit mono PCM from the default microphone.

    ``level_callback(level)`` receives the RMS level scaled to 0..1 about 30 times a second
    (called from the audio thread).
    """

    def __init__(self, sample_rate: int = 16000, max_seconds: float = 55.0,
                 level_callback: Callable[[float], None] | None = None,
                 device: int | str | None = None):
        self.sample_rate = sample_rate
        self.max_seconds = max_seconds
        self.level_callback = level_callback
        self.device = device
        self._chunks: list[bytes] = []
        self._frames = 0
        self._stream = None
        self._lock = threading.Lock()
        self.truncated = False

    def start(self) -> None:
        try:
            import numpy as np
            import sounddevice as sd
        except (ImportError, OSError) as exc:
            raise SttError(f"Audio recording is unavailable ({exc}). Install 'sounddevice' "
                           "and PortAudio, or type the text manually.") from exc
        self._np = np
        self._chunks.clear()
        self._frames = 0
        self.truncated = False
        max_frames = int(self.max_seconds * self.sample_rate)

        def callback(indata, frames, _time, _status):
            with self._lock:
                if self._frames >= max_frames:
                    self.truncated = True
                    return
                self._chunks.append(bytes(indata))
                self._frames += frames
            if self.level_callback:
                samples = np.frombuffer(indata, dtype=np.int16).astype(np.float32)
                rms = float(np.sqrt(np.mean(samples ** 2))) if samples.size else 0.0
                self.level_callback(min(1.0, rms / 8000.0))

        try:
            self._stream = sd.RawInputStream(samplerate=self.sample_rate, channels=1,
                                             dtype="int16", blocksize=self.sample_rate // 30,
                                             callback=callback, device=self.device)
            self._stream.start()
        except Exception as exc:  # PortAudioError and friends
            self._stream = None
            raise SttError(f"Could not open the microphone: {exc}") from exc

    def stop(self) -> bytes:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            finally:
                self._stream = None
        with self._lock:
            return b"".join(self._chunks)

    @property
    def seconds(self) -> float:
        return self._frames / self.sample_rate

    @property
    def recording(self) -> bool:
        return self._stream is not None


# ---------------------------------------------------------------------------
# Transcription
# ---------------------------------------------------------------------------

@dataclass
class SttConfig:
    language: str = "en-US"
    sample_rate: int = 16000
    timeout: float = 30.0


def transcribe(audio: bytes, config: SttConfig) -> str:
    """Send 16-bit mono PCM audio to Google speech recognition and return the text.

    Raises SttError with a clear message when offline, when Google refuses the request,
    or when no speech was recognised.
    """
    if not audio:
        raise SttError("No audio was recorded.")
    problem = stt_problem()
    if problem:
        raise SttError(problem)
    import speech_recognition as sr

    recognizer = sr.Recognizer()
    recognizer.operation_timeout = config.timeout
    data = sr.AudioData(audio, config.sample_rate, 2)
    try:
        text = recognizer.recognize_google(data, language=config.language, endpoint=ENDPOINT)
    except sr.UnknownValueError as exc:
        raise SttError("No speech was recognised. Try again closer to the microphone.") from exc
    except sr.RequestError as exc:
        raise SttError("Cannot reach Google speech recognition (offline or blocked). "
                       f"Check the connection, or type the text manually. ({exc})") from exc
    except Exception as exc:
        raise SttError(f"Speech recognition failed: {exc}") from exc
    text = (text or "").strip()
    if not text:
        raise SttError("No speech was recognised. Try again closer to the microphone.")
    return text

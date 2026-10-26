import json

import pytest

from plotter.config import MachineProfile, VoiceCommand
from plotter.stt.commands import parse_transcript
from plotter.stt.google_stt import SttConfig, SttError, stt_problem, transcribe


# ---- profile ------------------------------------------------------------------------------

def test_profile_roundtrip(tmp_path):
    p = MachineProfile(pen_down_z=-3.5, x_positive="left", paper_origin_mpos=[-250.0, -278.0],
                       voice_commands=[VoiceCommand("next line", "newline")])
    path = p.save(tmp_path / "profile.json")
    q = MachineProfile.load(path)
    assert q == p
    assert json.loads(path.read_text())["pen_down_z"] == -3.5


def test_profile_defaults_when_missing(tmp_path):
    assert MachineProfile.load(tmp_path / "nope.json") == MachineProfile()


def test_profile_ignores_unknown_keys(tmp_path):
    path = tmp_path / "p.json"
    path.write_text(json.dumps({"pen_up_z": 1.0, "obsolete": 5}))
    assert MachineProfile.load(path).pen_up_z == 1.0


def test_profile_env_override(tmp_path, monkeypatch):
    target = tmp_path / "custom.json"
    monkeypatch.setenv("VOICE_PLOTTER_PROFILE", str(target))
    MachineProfile(char_height=9).save()
    assert MachineProfile.load().char_height == 9


def test_defaults_are_consistent():
    p = MachineProfile()
    assert p.problems() == []
    assert (p.paper_width, p.paper_height, p.safety_margin) == (210, 297, 10)
    assert (p.pen_up_z, p.pen_down_z) == (0, -4)


@pytest.mark.parametrize("change,fragment", [
    ({"pen_down_z": 1}, "Pen-down"), ({"park_x": 5}, "Park"), ({"margin_left": 5}, "margin_left"),
    ({"x_positive": "up"}, "x_positive"), ({"draw_feed": 0}, "draw_feed"),
])
def test_profile_problems(change, fragment):
    p = MachineProfile(**change)
    assert any(fragment in msg for msg in p.problems())


def test_jog_vector_follows_orientation():
    p = MachineProfile()
    assert p.jog_vector("right") == ("X", 1) and p.jog_vector("away") == ("Y", 1)
    p.x_positive, p.y_positive = "left", "toward"
    assert p.jog_vector("right") == ("X", -1) and p.jog_vector("away") == ("Y", -1)


# ---- voice commands -------------------------------------------------------------------------

@pytest.mark.parametrize("utterance,action", [("Stop.", "stop"), ("pause", "pause"),
                                               ("Resume!", "resume"), ("  STOP  ", "stop")])
def test_control_commands_whole_utterance(utterance, action):
    r = parse_transcript(utterance)
    assert r.actions == [action] and r.text == "" and r.is_command


def test_control_word_inside_sentence_is_dictation():
    r = parse_transcript("Please don't stop the music.")
    assert r.actions == [] and r.text == "Please don't stop the music."


def test_new_line_inserted_anywhere():
    r = parse_transcript("Dear Sam, new line, thank you for the book. New line. Bye")
    assert r.text == "Dear Sam,\nThank you for the book.\nBye"
    assert r.actions == []


def test_custom_commands():
    cmds = [VoiceCommand("next line", "newline"), VoiceCommand("halt", "stop")]
    assert parse_transcript("halt", cmds).actions == ["stop"]
    assert parse_transcript("stop", cmds).actions == []          # default removed
    assert parse_transcript("a next line b", cmds).text == "a\nB"


def test_empty_transcript():
    assert parse_transcript("  ").text == ""


# ---- STT error handling (no network needed) ------------------------------------------------------

def test_empty_audio_is_rejected():
    with pytest.raises(SttError, match="No audio"):
        transcribe(b"", SttConfig())


def test_stt_available_without_credentials(monkeypatch):
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    assert stt_problem() is None


def test_offline_is_a_clear_error(monkeypatch):
    import speech_recognition as sr

    def offline(*a, **k):
        raise sr.RequestError("recognition connection failed: [Errno -3] Temporary failure")
    monkeypatch.setattr(sr.Recognizer, "recognize_google", offline)
    with pytest.raises(SttError, match="Cannot reach Google"):
        transcribe(b"\x00\x00" * 16000, SttConfig())


def test_no_speech_is_a_clear_error(monkeypatch):
    import speech_recognition as sr

    def nothing(*a, **k):
        raise sr.UnknownValueError()
    monkeypatch.setattr(sr.Recognizer, "recognize_google", nothing)
    with pytest.raises(SttError, match="No speech"):
        transcribe(b"\x00\x00" * 16000, SttConfig())


def test_transcribe_uses_language_and_https(monkeypatch):
    import speech_recognition as sr
    seen = {}

    def fake(self, data, language, endpoint):
        seen.update(language=language, endpoint=endpoint, rate=data.sample_rate)
        return " hello world "
    monkeypatch.setattr(sr.Recognizer, "recognize_google", fake)
    assert transcribe(b"\x00\x00" * 16000, SttConfig(language="ar-EG")) == "hello world"
    assert seen == {"language": "ar-EG", "endpoint": "https://www.google.com/speech-api/v2/recognize",
                    "rate": 16000}


@pytest.mark.parametrize("utterance,action", [("Dur.", "stop"), ("duraklat", "pause"),
                                               ("Devam et", "resume")])
def test_turkish_control_commands(utterance, action):
    assert parse_transcript(utterance).actions == [action]


def test_turkish_new_line_and_characters_pass_through():
    r = parse_transcript("Merhaba dünya yeni satır nasılsın, İstanbul")
    assert r.text == "Merhaba dünya\nNasılsın, İstanbul" and r.actions == []

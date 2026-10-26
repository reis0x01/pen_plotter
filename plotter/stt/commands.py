"""Separate spoken commands from dictated text.

* Insert commands (action ``newline``) may appear anywhere in the dictation and are
  replaced by a line break: "Dear Sam, new line, thank you" -> "Dear Sam,\\nThank you".
* Control commands (``stop``, ``pause``, ``resume``) are recognised only when they are
  the *whole* utterance ("Stop." / "pause"), so a sentence such as "don't stop now" is
  dictated normally instead of stopping the machine.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..config import VoiceCommand, default_voice_commands

INSERT_ACTIONS = {"newline": "\n"}
CONTROL_ACTIONS = {"stop", "pause", "resume"}


@dataclass
class ParsedTranscript:
    text: str                                   # dictated text with commands applied
    actions: list[str] = field(default_factory=list)   # control actions to execute

    @property
    def is_command(self) -> bool:
        return bool(self.actions) and not self.text


def _words(s: str) -> list[str]:
    return re.sub(r"[^\w\s']", " ", s).lower().split()


def _phrase_regex(phrase: str) -> str:
    words = _words(phrase)
    return r"\b" + r"\s+".join(re.escape(w) for w in words) + r"\b"


def parse_transcript(transcript: str,
                     commands: list[VoiceCommand] | None = None) -> ParsedTranscript:
    commands = commands if commands is not None else default_voice_commands()
    utterance = _words(transcript)
    if not utterance:
        return ParsedTranscript("")

    for cmd in commands:
        if cmd.action in CONTROL_ACTIONS and utterance == _words(cmd.phrase):
            return ParsedTranscript("", [cmd.action])

    text = transcript.strip()
    for cmd in commands:
        repl = INSERT_ACTIONS.get(cmd.action)
        if repl is None or not _words(cmd.phrase):
            continue
        pattern = r"[ \t]*" + _phrase_regex(cmd.phrase) + r"[ \t,.;:!?]*"
        text = re.sub(pattern, lambda _m: repl, text, flags=re.IGNORECASE)
    # Tidy: no spaces around inserted breaks, capitalise the start of new lines.
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
    text = re.sub(r"\n([a-z])", lambda m: "\n" + m.group(1).upper(), text)
    return ParsedTranscript(text)

"""Machine profile: every machine- and job-specific setting, persisted as JSON.

Coordinate frames used throughout the project
---------------------------------------------
* Page frame (u, v), in mm: the paper as the user sees it standing in front of the
  machine. u grows to the right, v grows *downward* (toward the user) from the far
  edge. Layout and the preview work in this frame.
* Work frame (x, y), in mm: GRBL work coordinates (G54). The paper origin set with
  "Set paper origin" is work (0, 0) and the sheet occupies [0, W] x [0, H].

Which physical corner is work (0, 0), and how u/v map onto x/y, depends on which way
+X and +Y move the pen. That is captured by ``x_positive`` and ``y_positive`` and is
applied only here, so the firmware direction settings ($3/$23) never need changing.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

X_DIRECTIONS = ("right", "left")      # direction +X moves the pen, as seen by the user
Y_DIRECTIONS = ("away", "toward")     # direction +Y moves the pen, relative to the user
CONTROL_ACTIONS = ("newline", "stop", "pause", "resume")


@dataclass
class VoiceCommand:
    phrase: str
    action: str  # one of CONTROL_ACTIONS


def default_voice_commands() -> list[VoiceCommand]:
    return [
        VoiceCommand("new line", "newline"),
        VoiceCommand("stop", "stop"),
        VoiceCommand("pause", "pause"),
        VoiceCommand("resume", "resume"),
        # Turkish
        VoiceCommand("yeni satır", "newline"),
        VoiceCommand("dur", "stop"),
        VoiceCommand("duraklat", "pause"),
        VoiceCommand("devam et", "resume"),
    ]


@dataclass
class MachineProfile:
    # Paper and safe area
    paper_width: float = 210.0
    paper_height: float = 297.0
    safety_margin: float = 10.0

    # Usable travel measured on the machine. The envelope check uses the smaller of this and
    # GRBL's $130/$131, so a too-large firmware value can never send the pen past the frame.
    usable_travel_x: float = 280.0
    usable_travel_y: float = 280.0

    # Axis orientation (see module docstring)
    x_positive: str = "right"
    y_positive: str = "away"

    # Paper origin in machine coordinates (MPos X, Y) captured by "Set paper origin".
    # None until the user sets it.
    paper_origin_mpos: list[float] | None = None

    # Pen (Z is a stepper; pen up/down are ordinary Z moves in work coordinates)
    pen_up_z: float = 0.0
    pen_down_z: float = -4.0
    z_feed: float = 300.0

    # XY feeds, mm/min. travel_feed <= 0 means travel with G0 (firmware max rate).
    draw_feed: float = 1000.0
    travel_feed: float = 2000.0

    # Park position, work coordinates. Must lie inside the safe area.
    park_x: float = 10.0
    park_y: float = 10.0

    # Jogging
    jog_steps: list[float] = field(default_factory=lambda: [0.1, 1.0, 10.0])
    jog_feed: float = 1000.0
    jog_z_feed: float = 200.0

    # Layout
    font: str = "futural"
    char_height: float = 7.0          # capital-letter height, mm
    line_spacing: float = 1.8         # baseline pitch as a multiple of char_height
    letter_spacing: float = 0.0       # extra mm between glyphs
    margin_left: float = 15.0
    margin_right: float = 15.0
    margin_top: float = 15.0
    margin_bottom: float = 15.0
    hyphenate_long_words: bool = True
    text_align: str = "left"          # 'left' or 'center'
    vertical_align: str = "top"       # 'top' or 'middle'

    # Where PDF drawings and pictures go: page-frame [left, top, right, bottom] in mm.
    # None = inside the text margins, centred on the paper.
    drawing_area: list[float] | None = None

    # Connection
    port: str = ""
    baud: int = 115200

    # Speech-to-text
    stt_language: str = "en-US"
    stt_sample_rate: int = 16000
    stt_max_seconds: float = 55.0
    ask_to_plot_after_speech: bool = True   # offer 'Plot now / Continue speaking' after a transcript
    voice_commands: list[VoiceCommand] = field(default_factory=default_voice_commands)

    # ---- derived geometry -------------------------------------------------
    @property
    def safe_min_x(self) -> float:
        return self.safety_margin

    @property
    def safe_max_x(self) -> float:
        return self.paper_width - self.safety_margin

    @property
    def safe_min_y(self) -> float:
        return self.safety_margin

    @property
    def safe_max_y(self) -> float:
        return self.paper_height - self.safety_margin

    def in_safe_area(self, x: float, y: float, tol: float = 1e-6) -> bool:
        return (self.safe_min_x - tol <= x <= self.safe_max_x + tol
                and self.safe_min_y - tol <= y <= self.safe_max_y + tol)

    def page_to_work(self, u: float, v: float) -> tuple[float, float]:
        """Page frame (u right, v down as viewed) -> work X, Y."""
        x = u if self.x_positive == "right" else self.paper_width - u
        y = self.paper_height - v if self.y_positive == "away" else v
        return x, y

    def work_to_page(self, x: float, y: float) -> tuple[float, float]:
        u = x if self.x_positive == "right" else self.paper_width - x
        v = self.paper_height - y if self.y_positive == "away" else y
        return u, v

    def origin_corner_name(self) -> str:
        """Physical paper corner that must be work (0, 0)."""
        near_far = "front" if self.y_positive == "away" else "back"
        left_right = "left" if self.x_positive == "right" else "right"
        return f"{near_far}-{left_right}"

    def home_corner_name(self, homing_dir_mask: int = 0) -> str:
        """Physical corner the pen goes to when homing. GRBL homes toward +X/+Y unless the
        axis bit is set in $23, in which case it homes toward -X/-Y."""
        x_to_max = not (homing_dir_mask & 1)
        y_to_max = not (homing_dir_mask & 2)
        x_side = self.x_positive if x_to_max else ("left" if self.x_positive == "right" else "right")
        y_side = self.y_positive if y_to_max else ("toward" if self.y_positive == "away" else "away")
        return f"{'back' if y_side == 'away' else 'front'}-{x_side}"

    def corner_work_coords(self, corner: str, inset: bool) -> tuple[float, float]:
        """Work X, Y of a physical paper corner ('back-left', 'front-right', ...), or of the
        point one safety margin inside it when ``inset``. Used by 'Set paper origin' so the pen
        can be at whichever corner is convenient."""
        ns, ew = corner.split("-")
        if ns not in ("back", "front") or ew not in ("left", "right"):
            raise ValueError(f"unknown corner {corner!r}")
        m = self.safety_margin if inset else 0.0
        u = m if ew == "left" else self.paper_width - m
        v = m if ns == "back" else self.paper_height - m
        return self.page_to_work(u, v)

    def jog_vector(self, direction: str) -> tuple[str, int]:
        """Screen jog direction ('right','left','away','toward') -> (axis, sign)."""
        if direction in ("right", "left"):
            return "X", 1 if direction == self.x_positive else -1
        if direction in ("away", "toward"):
            return "Y", 1 if direction == self.y_positive else -1
        raise ValueError(f"unknown jog direction {direction!r}")

    # ---- validation -------------------------------------------------------
    def problems(self) -> list[str]:
        """Human-readable list of inconsistent settings (empty when OK)."""
        p = []
        if self.x_positive not in X_DIRECTIONS:
            p.append(f"x_positive must be one of {X_DIRECTIONS}")
        if self.y_positive not in Y_DIRECTIONS:
            p.append(f"y_positive must be one of {Y_DIRECTIONS}")
        if self.paper_width <= 0 or self.paper_height <= 0:
            p.append("Paper size must be positive")
        if not 0 <= self.safety_margin < min(self.paper_width, self.paper_height) / 2:
            p.append("Safety margin must be >= 0 and leave a drawable area")
        if self.usable_travel_x <= 0 or self.usable_travel_y <= 0:
            p.append("Usable travel must be positive")
        if self.pen_down_z >= self.pen_up_z:
            p.append("Pen-down Z must be below pen-up Z")
        for name in ("z_feed", "draw_feed", "jog_feed", "jog_z_feed", "char_height", "line_spacing"):
            if getattr(self, name) <= 0:
                p.append(f"{name} must be positive")
        if not self.in_safe_area(self.park_x, self.park_y):
            p.append("Park position must be inside the safe area (paper minus safety margin)")
        for name in ("margin_left", "margin_right", "margin_top", "margin_bottom"):
            if getattr(self, name) < self.safety_margin:
                p.append(f"{name} must be at least the safety margin ({self.safety_margin} mm)")
        if self.margin_left + self.margin_right >= self.paper_width:
            p.append("Left + right margins leave no room for text")
        if self.margin_top + self.margin_bottom >= self.paper_height:
            p.append("Top + bottom margins leave no room for text")
        if self.text_align not in ("left", "center"):
            p.append("text_align must be 'left' or 'center'")
        if self.vertical_align not in ("top", "middle"):
            p.append("vertical_align must be 'top' or 'middle'")
        if self.drawing_area is not None:
            a = self.drawing_area
            if len(a) != 4 or a[2] - a[0] < 1 or a[3] - a[1] < 1:
                p.append("Drawing area must be at least 1 × 1 mm")
            elif not (self.in_safe_area(a[0], a[1], 1e-3) and self.in_safe_area(a[2], a[3], 1e-3)):
                p.append("Drawing area must lie inside the safe area (paper minus safety margin)")
        for vc in self.voice_commands:
            if vc.action not in CONTROL_ACTIONS:
                p.append(f"Voice command {vc.phrase!r}: unknown action {vc.action!r}")
        return p

    # ---- persistence ------------------------------------------------------
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "MachineProfile":
        known = {f.name for f in fields(cls)}
        kwargs = {k: v for k, v in data.items() if k in known}
        if "voice_commands" in kwargs:
            kwargs["voice_commands"] = [VoiceCommand(**vc) for vc in kwargs["voice_commands"]]
        return cls(**kwargs)

    def save(self, path: Path | str | None = None) -> Path:
        path = Path(path) if path else default_profile_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2))
        tmp.replace(path)
        return path

    @classmethod
    def load(cls, path: Path | str | None = None) -> "MachineProfile":
        """Load the profile; returns defaults when the file does not exist."""
        path = Path(path) if path else default_profile_path()
        if not path.exists():
            return cls()
        return cls.from_dict(json.loads(path.read_text()))


def default_profile_path() -> Path:
    env = os.environ.get("VOICE_PLOTTER_PROFILE")
    if env:
        return Path(env)
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "voice_plotter" / "profile.json"

"""Independent safety check of a G-code program before (and while) it is streamed.

The checker is stateful and fed one line at a time, so the streamer can run the exact
same rules on every line it is about to send. It never modifies coordinates: a line
that breaks a rule is reported, and the job is refused.

Rules
-----
* Only G0/G1 moves in mm, absolute or relative; no arcs, homing, probing, coordinate
  system changes or GRBL system commands inside a job.
* The pen height (Z) must be known before any XY motion.
* The pen is "up" only at or above pen_up_z; anything lower counts as down.
* No rapid (G0) move with the pen down, and no combined XY+Z move that involves the
  pen being down (the pen goes down and up with Z-only moves).
* Z never goes below pen_down_z (protects the pen and paper).
* Every XY target lies inside the safe area (paper minus safety margin) and, when the
  work offset and machine travel are known, inside the machine envelope.
* The program ends with the pen up.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..config import MachineProfile

TOL = 1e-4
WORD_RE = re.compile(r"([A-Z])([-+]?(?:\d+\.?\d*|\.\d+))")
COMMENT_RE = re.compile(r"\([^)]*\)|;.*$")

ALLOWED_G = {0, 1, 4, 17, 21, 40, 49, 54, 80, 90, 91, 94}
ALLOWED_M = {2, 3, 4, 5, 8, 9, 30}
FORBIDDEN_G_REASONS = {
    2: "arcs (G2) are not supported; flatten curves to G1 segments",
    3: "arcs (G3) are not supported; flatten curves to G1 segments",
    20: "inch units (G20) are not allowed; use G21 (mm)",
    28: "G28 moves to a stored position outside the bounds check",
    30: "G30 moves to a stored position outside the bounds check",
    38: "probing (G38.x) is not allowed in a job",
    53: "machine-coordinate moves (G53) bypass the paper bounds check",
    92: "G92 changes the coordinate system during the job",
    10: "G10 changes the coordinate system during the job",
    43: "tool length offsets (G43) are not allowed",
    55: "only the G54 work coordinate system is supported",
    56: "only the G54 work coordinate system is supported",
    57: "only the G54 work coordinate system is supported",
    58: "only the G54 work coordinate system is supported",
    59: "only the G54 work coordinate system is supported",
    93: "inverse-time feed (G93) is not supported",
}


@dataclass
class Bounds:
    """Safe area and pen heights the checker enforces."""
    min_x: float
    max_x: float
    min_y: float
    max_y: float
    pen_up_z: float
    pen_down_z: float
    # Optional machine envelope: work offset (WCO) and max travel per axis.
    wco: tuple[float, float, float] | None = None
    max_travel: tuple[float, float, float] | None = None

    @classmethod
    def from_profile(cls, profile: MachineProfile, wco=None, max_travel=None) -> "Bounds":
        return cls(profile.safe_min_x, profile.safe_max_x, profile.safe_min_y,
                   profile.safe_max_y, profile.pen_up_z, profile.pen_down_z,
                   tuple(wco) if wco else None, tuple(max_travel) if max_travel else None)


@dataclass
class Issue:
    line_no: int         # 1-based line number in the program
    text: str            # the original line
    message: str

    def __str__(self) -> str:
        return f"line {self.line_no}: {self.message}   [{self.text.strip()}]"


@dataclass
class Move:
    line_no: int
    kind: str                                        # 'draw', 'travel', 'z', 'dwell'
    start: tuple[float | None, float | None, float | None]
    end: tuple[float | None, float | None, float | None]
    feed: float | None                               # mm/min, None for G0
    rapid: bool
    duration: float = 0.0                            # dwell seconds
    bad: bool = False


@dataclass
class ValidationResult:
    issues: list[Issue]
    moves: list[Move]
    line_count: int
    draw_extent: tuple[float, float, float, float] | None  # min_x, min_y, max_x, max_y

    @property
    def ok(self) -> bool:
        return not self.issues

    def summary(self, limit: int = 10) -> str:
        if self.ok:
            return "G-code passed all safety checks."
        head = "\n".join(str(i) for i in self.issues[:limit])
        more = len(self.issues) - limit
        return head + (f"\n... and {more} more" if more > 0 else "")


def strip_line(raw: str) -> str:
    """Remove comments, whitespace and line numbers; upper-case. '' if nothing left."""
    s = COMMENT_RE.sub("", raw).strip().upper()
    s = s.replace(" ", "").replace("\t", "")
    if s.startswith("N"):
        s = re.sub(r"^N\d+", "", s)
    return s


def parse_words(line: str) -> list[tuple[str, float]] | None:
    """Parse a stripped line into (letter, value) words; None if it is not pure G-code."""
    words = WORD_RE.findall(line)
    if "".join(l + v for l, v in words) != line:
        return None
    return [(l, float(v)) for l, v in words]


class GcodeChecker:
    def __init__(self, bounds: Bounds):
        self.b = bounds
        self.absolute = True
        self.motion: int | None = None  # 0 or 1
        self.feed: float | None = None
        self.pos: list[float | None] = [None, None, None]
        self.moves: list[Move] = []
        self.issues: list[Issue] = []
        self.line_no = 0
        self._extent: list[float] | None = None

    # ---- helpers ----------------------------------------------------------
    def pen_down(self, z: float | None) -> bool | None:
        if z is None:
            return None
        return z < self.b.pen_up_z - TOL

    def _issue(self, raw: str, msg: str, out: list[Issue]) -> None:
        issue = Issue(self.line_no, raw, msg)
        out.append(issue)
        self.issues.append(issue)

    def _check_xy(self, x: float, y: float, raw: str, out: list[Issue]) -> bool:
        b = self.b
        ok = True
        if not (b.min_x - TOL <= x <= b.max_x + TOL and b.min_y - TOL <= y <= b.max_y + TOL):
            self._issue(raw, f"X{x:g} Y{y:g} is outside the safe area "
                             f"(X {b.min_x:g}..{b.max_x:g}, Y {b.min_y:g}..{b.max_y:g})", out)
            ok = False
        if b.wco and b.max_travel:
            for axis, val, off, trav in (("X", x, b.wco[0], b.max_travel[0]),
                                         ("Y", y, b.wco[1], b.max_travel[1])):
                m = val + off
                if not (-trav - TOL <= m <= TOL):
                    self._issue(raw, f"{axis}{val:g} is machine {axis}{m:.3f}, outside the "
                                     f"machine travel (-{trav:g}..0)", out)
                    ok = False
        return ok

    # ---- main entry points ------------------------------------------------
    def check_line(self, raw: str, line_no: int | None = None) -> list[Issue]:
        """Check one line and advance the state. Returns the issues for this line."""
        self.line_no = line_no if line_no is not None else self.line_no + 1
        out: list[Issue] = []
        line = strip_line(raw)
        if not line or line == "%":
            return out
        if line[0] in "$?!~" or any(c in raw for c in "\x18\x85"):
            self._issue(raw, "GRBL system/real-time commands are not allowed inside a job", out)
            return out
        words = parse_words(line)
        if words is None:
            self._issue(raw, "cannot parse this line", out)
            return out

        values: dict[str, float] = {}
        gcodes: list[float] = []
        for letter, val in words:
            if letter == "G":
                gcodes.append(val)
            elif letter == "M":
                m = int(val)
                if m not in ALLOWED_M:
                    self._issue(raw, f"M{m} is not allowed in a job", out)
            elif letter in "XYZFP":
                if letter in values:
                    self._issue(raw, f"{letter} appears twice", out)
                values[letter] = val
            elif letter in "ST":
                pass  # spindle speed / tool: harmless on a plotter
            else:
                self._issue(raw, f"unsupported word {letter}", out)

        dwell = False
        for g in gcodes:
            gi = int(g)
            if g != gi and gi != 38:
                self._issue(raw, f"G{g:g} is not supported", out)
            elif gi in FORBIDDEN_G_REASONS:
                self._issue(raw, FORBIDDEN_G_REASONS[gi], out)
            elif gi not in ALLOWED_G:
                self._issue(raw, f"G{gi} is not supported", out)
            elif gi in (0, 1):
                self.motion = gi
            elif gi == 90:
                self.absolute = True
            elif gi == 91:
                self.absolute = False
            elif gi == 4:
                dwell = True
        if out:
            return out

        if "F" in values:
            if values["F"] <= 0:
                self._issue(raw, "feed rate must be positive", out)
                return out
            self.feed = values["F"]

        if dwell:
            if "P" not in values or values["P"] < 0:
                self._issue(raw, "G4 dwell needs a non-negative P value", out)
                return out
            self.moves.append(Move(self.line_no, "dwell", tuple(self.pos), tuple(self.pos),
                                   None, False, duration=values["P"]))
            return out

        axes = [a for a in "XYZ" if a in values]
        if not axes:
            return out
        if self.motion is None:
            self._issue(raw, "axis words without a G0/G1 motion mode", out)
            return out
        rapid = self.motion == 0
        if not rapid and self.feed is None:
            self._issue(raw, "G1 move without a feed rate (F)", out)
            return out

        start = list(self.pos)
        target = list(self.pos)
        for i, a in enumerate("XYZ"):
            if a not in values:
                continue
            if self.absolute:
                target[i] = values[a]
            elif start[i] is None:
                self._issue(raw, f"relative {a} move from an unknown position", out)
                return out
            else:
                target[i] = start[i] + values[a]

        z0, z1 = start[2], target[2]
        if z1 is not None and z1 < self.b.pen_down_z - TOL:
            self._issue(raw, f"Z{z1:g} is below the pen-down height Z{self.b.pen_down_z:g}", out)

        moves_xy = ("X" in values or "Y" in values) and (target[0], target[1]) != (start[0], start[1])
        moves_z = "Z" in values and z1 != z0
        bad = False

        if moves_xy:
            if z0 is None or z1 is None:
                self._issue(raw, "XY motion before the pen height (Z) is set", out)
                bad = True
            elif target[0] is None or target[1] is None:
                self._issue(raw, "XY motion with only one axis known; give both X and Y first", out)
                bad = True
            else:
                down0, down1 = self.pen_down(z0), self.pen_down(z1)
                if moves_z and (down0 or down1):
                    self._issue(raw, "combined XY and Z move with the pen down; "
                                     "lower and raise the pen with Z-only moves", out)
                    bad = True
                if rapid and (down0 or down1):
                    self._issue(raw, "rapid G0 move with the pen down", out)
                    bad = True
                if not self._check_xy(target[0], target[1], raw, out):
                    bad = True
            kind = "draw" if self.pen_down(z1) and self.pen_down(z0) else "travel"
        else:
            kind = "z"
            if moves_z and self.pen_down(z1):
                if start[0] is None or start[1] is None:
                    self._issue(raw, "pen lowered at an unknown XY position", out)
                    bad = True

        if moves_xy or moves_z:
            move = Move(self.line_no, kind, tuple(start), tuple(target),
                        None if rapid else self.feed, rapid, bad=bad)
            self.moves.append(move)
            if kind == "draw" and not bad:
                self._grow_extent(start[0], start[1])
                self._grow_extent(target[0], target[1])
        self.pos = target
        return out

    def _grow_extent(self, x, y):
        if x is None or y is None:
            return
        if self._extent is None:
            self._extent = [x, y, x, y]
        else:
            e = self._extent
            e[0], e[1], e[2], e[3] = min(e[0], x), min(e[1], y), max(e[2], x), max(e[3], y)

    def finish(self) -> list[Issue]:
        out: list[Issue] = []
        if self.pen_down(self.pos[2]):
            self._issue("", "program ends with the pen down", out)
        return out

    def result(self) -> ValidationResult:
        return ValidationResult(list(self.issues), list(self.moves), self.line_no,
                                tuple(self._extent) if self._extent else None)


def validate(lines: list[str], bounds: Bounds) -> ValidationResult:
    checker = GcodeChecker(bounds)
    for i, line in enumerate(lines, start=1):
        checker.check_line(line, i)
    checker.line_no = len(lines)
    checker.finish()
    return checker.result()


def validate_for_profile(lines: list[str], profile: MachineProfile,
                         wco=None, max_travel=None) -> ValidationResult:
    return validate(lines, Bounds.from_profile(profile, wco, max_travel))

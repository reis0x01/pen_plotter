"""GRBL response parsing for both the 0.9 text format and the 1.1 numeric format.

0.9:  <Idle,MPos:0.000,0.000,0.000,WPos:0.000,0.000,0.000>
      error: Bad number format        ALARM: Hard limit        ['$H'|'$X' to unlock]
      $100=250.000 (x, step/mm)       [0.9j.20160726:]
1.1:  <Idle|MPos:0.000,0.000,0.000|FS:0,0|WCO:0.000,0.000,0.000>
      error:2                         ALARM:1                  [MSG:'$H'|'$X' to unlock]
      $100=250.000                    [VER:1.1h.20190825:]
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Plain-language tables
# ---------------------------------------------------------------------------

# 1.1 numeric alarm codes -> (short name, explanation)
ALARMS_11 = {
    1: ("Hard limit", "A limit switch was triggered during motion. Position is lost. "
        "Move the carriage off the switch if needed, then reset and home ($H)."),
    2: ("Soft limit", "A move would have left the machine travel ($130-$132). Nothing moved. "
        "Reset and home, then check the job and the paper origin."),
    3: ("Abort during cycle", "The machine was reset while moving, so its position is "
        "no longer trusted. Home ($H) before continuing."),
    4: ("Probe fail", "Probe was not in the expected state before probing."),
    5: ("Probe fail", "Probe did not contact within the search distance."),
    6: ("Homing fail: reset", "Homing was interrupted by a reset. Home again."),
    7: ("Homing fail: door", "Safety door opened during homing."),
    8: ("Homing fail: pull-off", "The switch stayed pressed after pull-off. Check the switch "
        "wiring, or increase the pull-off distance ($27)."),
    9: ("Homing fail: switch not found", "An axis moved its full search distance without "
        "hitting its limit switch. Check the switch wiring, the homing direction and $13x."),
    10: ("Homing fail: dual axis", "Dual-axis homing failed."),
}

# 0.9 text alarms -> 1.1-equivalent code
ALARM_TEXT_09 = {
    "hard limit": 1,
    "soft limit": 2,
    "abort during cycle": 3,
    "probe fail": 5,
    "homing fail": 9,
}

# 1.1 numeric error codes -> explanation
ERRORS_11 = {
    1: "G-code words consist of a letter and a value. Letter was not found.",
    2: "Numeric value format is not valid or missing an expected value.",
    3: "Grbl '$' system command was not recognized or supported.",
    4: "Negative value received for an expected positive value.",
    5: "Homing cycle is not enabled via settings ($22).",
    6: "Minimum step pulse time must be greater than 3 microseconds.",
    7: "EEPROM read failed. Reset and restored to default values.",
    8: "Grbl '$' command cannot be used unless Grbl is IDLE.",
    9: "G-code locked out during alarm or jog state. Home ($H) or unlock ($X) first.",
    10: "Soft limits cannot be enabled without homing also enabled.",
    11: "Max characters per line exceeded. Line was not processed.",
    12: "Setting value exceeds the maximum step rate supported.",
    13: "Safety door detected as opened.",
    14: "Build info or startup line exceeded EEPROM line length limit.",
    15: "Jog target exceeds machine travel. Command ignored.",
    16: "Jog command with no '=' or contains prohibited g-code.",
    17: "Laser mode requires PWM output.",
    20: "Unsupported or invalid g-code command found in block.",
    21: "More than one g-code command from same modal group found in block.",
    22: "Feed rate has not yet been set or is undefined.",
    23: "G-code command in block requires an integer value.",
    24: "Two G-code commands that both require the use of the XYZ axis words were detected.",
    25: "A G-code word was repeated in the block.",
    26: "A G-code command implicitly or explicitly requires XYZ axis words in the block, "
        "but none were detected.",
    27: "N line number value is not within the valid range of 1 - 9,999,999.",
    28: "A G-code command was sent, but is missing some required P or L value words.",
    29: "Grbl supports six work coordinate systems G54-G59. G59.1-G59.3 are not supported.",
    30: "The G53 G-code command requires either a G0 seek or G1 feed motion mode to be active.",
    31: "There are unused axis words in the block and G80 motion mode cancel is active.",
    32: "A G2 or G3 arc was commanded but there are no XYZ axis words in the selected plane.",
    33: "The motion command has an invalid target.",
    34: "A G2 or G3 arc, traced with the radius definition, had a mathematical error.",
    35: "A G2 or G3 arc, traced with the offset definition, is missing the IJK offset word.",
    36: "There are unused, leftover G-code words that aren't used by any command in the block.",
    37: "The G43.1 dynamic tool length offset command cannot apply an offset to an axis "
        "other than its configured axis.",
    38: "Tool number greater than max supported value.",
}

# 0.9 error texts -> friendlier explanation
ERRORS_09 = {
    "alarm lock": ERRORS_11[9],
    "not idle": ERRORS_11[8],
    "homing not enabled": ERRORS_11[5],
    "line overflow": ERRORS_11[11],
    "step rate > 30khz": ERRORS_11[12],
    "modal group violation": ERRORS_11[21],
    "unsupported command": ERRORS_11[20],
    "undefined feed rate": ERRORS_11[22],
    "bad number format": ERRORS_11[2],
    "expected command letter": ERRORS_11[1],
    "invalid statement": ERRORS_11[3],
    "value < 0": ERRORS_11[4],
    "setting disabled": "That setting is disabled in this firmware build.",
    "value < 3 usec": ERRORS_11[6],
    "eeprom read fail. using defaults": ERRORS_11[7],
}

# GRBL settings: number -> (name, unit/type, description)
SETTINGS_INFO = {
    0: ("Step pulse", "µs", "Length of the step pulse sent to the drivers (min 3)."),
    1: ("Step idle delay", "ms", "Motor hold time after motion; 255 keeps motors always on."),
    2: ("Step port invert", "mask", "Inverts step signals (bit0=X, bit1=Y, bit2=Z)."),
    3: ("Direction port invert", "mask", "Inverts axis directions (bit0=X, bit1=Y, bit2=Z). "
        "Already set for this machine; do not change."),
    4: ("Step enable invert", "bool", "Inverts the stepper enable pin."),
    5: ("Limit pins invert", "bool", "Inverts limit inputs (1 for normally-closed switches)."),
    6: ("Probe pin invert", "bool", "Inverts the probe input."),
    10: ("Status report", "mask", "What '?' reports (MPos/WPos, buffer state)."),
    11: ("Junction deviation", "mm", "Cornering speed; smaller is slower through corners."),
    12: ("Arc tolerance", "mm", "Arc segment accuracy (arcs are not used by this app)."),
    13: ("Report inches", "bool", "Must be 0: this app works in mm."),
    20: ("Soft limits", "bool", "Refuse moves beyond $130-$132 (needs homing)."),
    21: ("Hard limits", "bool", "Stop immediately when a limit switch triggers."),
    22: ("Homing cycle", "bool", "Enable $H homing. Must be 1 on this machine."),
    23: ("Homing direction invert", "mask", "Homing direction per axis. Already set for this "
         "machine; do not change."),
    24: ("Homing feed", "mm/min", "Slow locate speed during homing."),
    25: ("Homing seek", "mm/min", "Fast search speed during homing."),
    26: ("Homing debounce", "ms", "Switch debounce delay."),
    27: ("Homing pull-off", "mm", "Distance to back off the switches after homing."),
    30: ("Max spindle speed", "RPM", "Spindle PWM maximum (unused on this plotter)."),
    31: ("Min spindle speed", "RPM", "Spindle PWM minimum (unused on this plotter)."),
    32: ("Laser mode", "bool", "Must be 0 on this plotter."),
    100: ("X steps/mm", "steps/mm", "Calibrate with the 100 mm line test."),
    101: ("Y steps/mm", "steps/mm", "Calibrate with the 100 mm line test."),
    102: ("Z steps/mm", "steps/mm", "Z lead: sets how far the pen actually moves."),
    110: ("X max rate", "mm/min", "Fastest X speed (also G0 speed)."),
    111: ("Y max rate", "mm/min", "Fastest Y speed (also G0 speed)."),
    112: ("Z max rate", "mm/min", "Fastest Z speed."),
    120: ("X acceleration", "mm/s²", "X acceleration."),
    121: ("Y acceleration", "mm/s²", "Y acceleration."),
    122: ("Z acceleration", "mm/s²", "Z acceleration."),
    130: ("X max travel", "mm", "Usable X travel; should be about 280 on this machine."),
    131: ("Y max travel", "mm", "Usable Y travel; should be about 280 on this machine."),
    132: ("Z max travel", "mm", "Usable Z travel; must exceed the pen stroke + pull-off."),
}

BOOL_SETTINGS = {4, 5, 6, 13, 20, 21, 22, 32}
MASK_SETTINGS = {2, 3, 23}
DIRECTION_SETTINGS = {3, 23}  # owner: never change these


def validate_setting(num: int, value: str) -> tuple[bool, str]:
    """Check a proposed setting value. Returns (ok, message)."""
    try:
        v = float(value)
    except ValueError:
        return False, f"${num}: '{value}' is not a number"
    if v < 0:
        return False, f"${num}: value must not be negative"
    if num in BOOL_SETTINGS and v not in (0, 1):
        return False, f"${num}: must be 0 or 1"
    if num in MASK_SETTINGS and (v != int(v) or v > 7):
        return False, f"${num}: must be an integer mask 0-7"
    if num == 10 and (v != int(v) or v > 255):
        return False, "$10: must be an integer 0-255"
    if num == 0 and v < 3:
        return False, "$0: step pulse must be at least 3 µs"
    if num == 1 and (v != int(v) or v > 255):
        return False, "$1: must be an integer 0-255"
    if num in (100, 101, 102, 110, 111, 112, 120, 121, 122) and v <= 0:
        return False, f"${num}: must be greater than 0"
    if num == 13 and v != 0:
        return False, "$13: this app requires mm reporting (0)"
    return True, ""


def settings_warnings(settings: dict[int, float], pen_down_z: float = -4.0,
                      expected_travel: float = 280.0) -> list[str]:
    """Consistency warnings for a machine with 280 x 280 mm travel and a Z-stepper pen."""
    w = []
    for n, axis in ((130, "X"), (131, "Y")):
        if n in settings and abs(settings[n] - expected_travel) > 5:
            w.append(f"${n} (max travel {axis}) is {settings[n]:g} mm but the usable travel "
                     f"is {expected_travel:g} mm.")
    if 132 in settings:
        need = abs(pen_down_z) + settings.get(27, 1.0)
        if settings[132] < need:
            w.append(f"$132 (Z max travel) is {settings[132]:g} mm, less than the pen stroke "
                     f"+ pull-off ({need:g} mm).")
    if settings.get(22, 1) == 0:
        w.append("$22 homing is disabled but this machine homes on power-up.")
    if settings.get(20, 0) == 1 and settings.get(22, 1) == 0:
        w.append("$20 soft limits need $22 homing enabled.")
    if settings.get(13, 0) == 1:
        w.append("$13 reports inches; this app expects mm.")
    if settings.get(32, 0) == 1:
        w.append("$32 laser mode is on; it should be off on a plotter.")
    return w


# ---------------------------------------------------------------------------
# Parsed messages
# ---------------------------------------------------------------------------

@dataclass
class Status:
    state: str                    # Idle, Run, Hold, Jog, Alarm, Door, Check, Home, Sleep
    substate: str = ""            # e.g. "0" in Hold:0
    mpos: tuple[float, float, float] | None = None
    wpos: tuple[float, float, float] | None = None
    wco: tuple[float, float, float] | None = None
    feed: float | None = None
    buffer: tuple[int, int] | None = None   # (planner blocks free / used, rx bytes)
    pins: str = ""
    raw: str = ""

    def resolve(self, last_wco: tuple[float, float, float] | None) -> "Status":
        """Fill in whichever of MPos/WPos is missing using the (last known) WCO."""
        wco = self.wco or last_wco
        if self.mpos and self.wpos and not self.wco:
            self.wco = tuple(m - w for m, w in zip(self.mpos, self.wpos))
        elif self.mpos and not self.wpos and wco:
            self.wpos = tuple(m - o for m, o in zip(self.mpos, wco))
            self.wco = wco
        elif self.wpos and not self.mpos and wco:
            self.mpos = tuple(w + o for w, o in zip(self.wpos, wco))
            self.wco = wco
        return self


@dataclass
class Alarm:
    code: int | None
    name: str
    explanation: str
    raw: str


@dataclass
class Error:
    code: int | None
    text: str
    explanation: str
    raw: str


@dataclass
class Banner:
    version: str      # e.g. "0.9j", "1.1h"
    major_minor: tuple[int, int]
    raw: str


@dataclass
class Setting:
    number: int
    value: float
    raw_value: str


@dataclass
class Message:
    text: str
    raw: str


@dataclass
class VersionInfo:
    version: str
    raw: str


@dataclass
class Ok:
    raw: str = "ok"


@dataclass
class Unknown:
    raw: str


_NUM3 = r"(-?\d+\.?\d*),(-?\d+\.?\d*),(-?\d+\.?\d*)"


def _triple(s: str) -> tuple[float, float, float]:
    parts = s.split(",")[:3]
    return tuple(float(p) for p in parts)  # type: ignore[return-value]


def parse_status(line: str) -> Status | None:
    if not (line.startswith("<") and line.endswith(">")):
        return None
    body = line[1:-1]
    if "|" in body or ("," not in body):
        return _parse_status_11(body, line)
    return _parse_status_09(body, line)


def _parse_status_11(body: str, raw: str) -> Status:
    fields = body.split("|")
    state, _, sub = fields[0].partition(":")
    st = Status(state=state, substate=sub, raw=raw)
    for f in fields[1:]:
        key, _, val = f.partition(":")
        try:
            if key == "MPos":
                st.mpos = _triple(val)
            elif key == "WPos":
                st.wpos = _triple(val)
            elif key == "WCO":
                st.wco = _triple(val)
            elif key in ("FS", "F"):
                st.feed = float(val.split(",")[0])
            elif key == "Bf":
                a, b = val.split(",")
                st.buffer = (int(a), int(b))
            elif key == "Pn":
                st.pins = val
        except ValueError:
            continue
    return st


def _parse_status_09(body: str, raw: str) -> Status:
    state = body.split(",", 1)[0]
    st = Status(state=state, raw=raw)
    m = re.search(r"MPos:" + _NUM3, body)
    if m:
        st.mpos = tuple(float(g) for g in m.groups())
    m = re.search(r"WPos:" + _NUM3, body)
    if m:
        st.wpos = tuple(float(g) for g in m.groups())
    m = re.search(r"Buf:(\d+),RX:(\d+)", body)
    if m:
        st.buffer = (int(m.group(1)), int(m.group(2)))
    m = re.search(r"Lim:([01]{3})", body)
    if m:
        st.pins = "".join(a for a, bit in zip("XYZ", m.group(1)[::-1]) if bit == "1")
    return st


def parse_alarm(line: str) -> Alarm | None:
    if not line.upper().startswith("ALARM"):
        return None
    rest = line.split(":", 1)[1].strip() if ":" in line else ""
    if rest.isdigit():
        code = int(rest)
        name, expl = ALARMS_11.get(code, (f"Alarm {code}", "Unknown alarm code."))
        return Alarm(code, name, expl, line)
    code = ALARM_TEXT_09.get(rest.lower())
    if code is not None:
        name, expl = ALARMS_11[code]
        if code == 9:  # 0.9 has a single homing-fail alarm
            name, expl = "Homing fail", ("Homing did not complete. Check the limit switches "
                                         "and wiring, then home again.")
        return Alarm(code, rest or name, expl, line)
    return Alarm(None, rest or "Alarm", "Unknown alarm; reset and home the machine.", line)


def parse_error(line: str) -> Error | None:
    if not line.lower().startswith("error"):
        return None
    rest = line.split(":", 1)[1].strip() if ":" in line else ""
    if rest.isdigit():
        code = int(rest)
        return Error(code, f"error:{code}", ERRORS_11.get(code, "Unknown error code."), line)
    low = rest.lower()
    expl = ERRORS_09.get(low)
    if expl is None and low.startswith("invalid gcode id"):
        expl = "G-code command not recognized or its arguments are invalid."
    return Error(None, rest, expl or rest, line)


_BANNER_RE = re.compile(r"^Grbl\s+(\d+)\.(\d+)(\w*)", re.I)
_SETTING_RE = re.compile(r"^\$(\d+)=(-?\d+\.?\d*)")
_VER11_RE = re.compile(r"^\[VER:(\d+\.\d+\w*)")
_VER09_RE = re.compile(r"^\[(\d+\.\d+\w*)\.\d+:")


def parse_line(line: str):
    """Classify one line received from GRBL."""
    line = line.strip()
    if line == "ok":
        return Ok()
    if line.startswith("<"):
        return parse_status(line) or Unknown(line)
    if line.lower().startswith("error"):
        return parse_error(line)
    if line.upper().startswith("ALARM"):
        return parse_alarm(line)
    m = _BANNER_RE.match(line)
    if m:
        return Banner(f"{m.group(1)}.{m.group(2)}{m.group(3)}",
                      (int(m.group(1)), int(m.group(2))), line)
    m = _SETTING_RE.match(line)
    if m:
        return Setting(int(m.group(1)), float(m.group(2)), m.group(2))
    m = _VER11_RE.match(line) or _VER09_RE.match(line)
    if m:
        return VersionInfo(m.group(1), line)
    if line.startswith("[") and line.endswith("]"):
        text = line[1:-1]
        if text.startswith("MSG:"):
            text = text[4:]
        return Message(text, line)
    return Unknown(line)


def is_realtime_status_line(line: str) -> bool:
    return line.startswith("<") and line.endswith(">")

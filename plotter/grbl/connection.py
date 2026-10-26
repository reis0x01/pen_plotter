"""Transports to GRBL: a real serial port, and a simulated GRBL for no-hardware mode.

Both expose the same small interface used by :class:`plotter.grbl.streamer.GrblController`::

    write(data: bytes) -> None
    read_line(timeout: float) -> str | None     # one line without EOL, or None on timeout
    close() -> None
    is_open: bool
"""
from __future__ import annotations

import math
import queue
import re
import threading
import time
from dataclasses import dataclass, field

RT_STATUS = b"?"
RT_HOLD = b"!"
RT_RESUME = b"~"
RT_RESET = b"\x18"
RT_JOG_CANCEL = b"\x85"
REALTIME = {0x3F, 0x21, 0x7E, 0x18, 0x85}


class TransportError(IOError):
    pass


def list_serial_ports() -> list[tuple[str, str]]:
    """[(device, description)] for all serial ports; Arduinos first."""
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    ports = [(p.device, p.description or "") for p in list_ports.comports()]
    ports.sort(key=lambda p: (0 if re.search(r"arduino|ch340|usb", p[1], re.I) else 1, p[0]))
    return ports


class SerialTransport:
    def __init__(self, port: str, baud: int = 115200):
        import serial  # imported lazily so tests and simulation need no pyserial
        try:
            self._ser = serial.Serial(port, baud, timeout=0.05, write_timeout=2)
        except (serial.SerialException, OSError) as exc:
            raise TransportError(f"Cannot open {port}: {exc}") from exc
        self._buf = bytearray()
        self._serial_mod = serial

    @property
    def is_open(self) -> bool:
        return self._ser.is_open

    def write(self, data: bytes) -> None:
        try:
            self._ser.write(data)
        except (self._serial_mod.SerialException, OSError) as exc:
            raise TransportError(f"Serial write failed: {exc}") from exc

    def read_line(self, timeout: float = 0.1) -> str | None:
        deadline = time.monotonic() + timeout
        while True:
            nl = self._buf.find(b"\n")
            if nl >= 0:
                raw = bytes(self._buf[:nl])
                del self._buf[:nl + 1]
                return raw.decode("ascii", "replace").strip("\r")
            try:
                chunk = self._ser.read(max(1, self._ser.in_waiting))
            except (self._serial_mod.SerialException, OSError) as exc:
                raise TransportError(f"Serial read failed: {exc}") from exc
            if chunk:
                self._buf.extend(chunk)
            elif time.monotonic() >= deadline:
                return None

    def close(self) -> None:
        try:
            self._ser.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Simulated GRBL
# ---------------------------------------------------------------------------

DEFAULT_SIM_SETTINGS: dict[int, float] = {
    0: 10, 1: 25, 2: 0, 3: 0, 4: 0, 5: 0, 6: 0, 10: 3, 11: 0.010, 12: 0.002, 13: 0,
    20: 0, 21: 0, 22: 1, 23: 0, 24: 25.0, 25: 500.0, 26: 250, 27: 1.0,
    100: 80.0, 101: 80.0, 102: 400.0,
    110: 3000.0, 111: 3000.0, 112: 600.0,
    120: 100.0, 121: 100.0, 122: 50.0,
    130: 280.0, 131: 280.0, 132: 40.0,
}
DESCRIPTIONS_09 = {
    0: "step pulse, usec", 1: "step idle delay, msec", 2: "step port invert mask:00000000",
    3: "dir port invert mask:00000000", 4: "step enable invert, bool", 5: "limit pins invert, bool",
    6: "probe pin invert, bool", 10: "status report mask:00000011", 11: "junction deviation, mm",
    12: "arc tolerance, mm", 13: "report inches, bool", 20: "soft limits, bool",
    21: "hard limits, bool", 22: "homing cycle, bool", 23: "homing dir invert mask:00000000",
    24: "homing feed, mm/min", 25: "homing seek, mm/min", 26: "homing debounce, msec",
    27: "homing pull-off, mm", 100: "x, step/mm", 101: "y, step/mm", 102: "z, step/mm",
    110: "x max rate, mm/min", 111: "y max rate, mm/min", 112: "z max rate, mm/min",
    120: "x accel, mm/sec^2", 121: "y accel, mm/sec^2", 122: "z accel, mm/sec^2",
    130: "x max travel, mm", 131: "y max travel, mm", 132: "z max travel, mm",
}
ERR_TEXT_09 = {1: "Expected command letter", 2: "Bad number format", 3: "Invalid statement",
               8: "Not idle", 9: "Alarm lock", 11: "Line overflow", 20: "Unsupported command",
               22: "Undefined feed rate", 15: "Invalid statement", 26: "Invalid statement"}
ALARM_TEXT_09 = {1: "Hard limit", 2: "Soft limit", 3: "Abort during cycle", 9: "Homing fail"}


@dataclass
class _Block:
    start: list[float]
    end: list[float]
    duration: float           # seconds (0 for instantaneous blocks)
    dwell: bool = False
    elapsed: float = 0.0


@dataclass
class SimulatedGrbl:
    """A GRBL good enough to exercise the whole app without hardware.

    speed: time multiplier (1 = real time, 100 = a hundred times faster).
    """
    version: str = "1.1h"
    speed: float = 1.0
    settings: dict[int, float] = field(default_factory=lambda: dict(DEFAULT_SIM_SETTINGS))
    rx_size: int = 128
    planner_size: int = 15
    homing_time: float = 2.0      # simulated seconds for a homing cycle
    tick: float = 0.002

    def __post_init__(self):
        self.is_open = True
        self._out: queue.Queue[str] = queue.Queue()
        self._rx = bytearray()
        self._lock = threading.RLock()
        self.overflowed = False           # set if a client ever overran the RX buffer
        self.max_rx_used = 0
        self.received_lines: list[str] = []
        self.fail_lines: dict[str, int] = {}   # substring -> error code to inject
        self._reset_state(power_on=True)
        self._thread = threading.Thread(target=self._run, name="sim-grbl", daemon=True)
        self._thread.start()

    # ---- transport interface ---------------------------------------------
    def write(self, data: bytes) -> None:
        if not self.is_open:
            raise ConnectionError("simulator closed")
        with self._lock:
            for b in data:
                if b in REALTIME:
                    self._realtime(b)
                    continue
                if len(self._rx) >= self.rx_size:
                    self.overflowed = True     # real GRBL would silently drop this byte
                    continue
                self._rx.append(b)
                self.max_rx_used = max(self.max_rx_used, len(self._rx))

    def read_line(self, timeout: float = 0.1) -> str | None:
        try:
            return self._out.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        self.is_open = False

    # ---- test helpers -----------------------------------------------------
    def trigger_alarm(self, code: int) -> None:
        """Simulate e.g. a hard-limit hit (code 1)."""
        with self._lock:
            self._alarm(code, critical=code in (1, 2))

    @property
    def is_v11(self) -> bool:
        return self.version.startswith("1.")

    # ---- state -------------------------------------------------------------
    def _reset_state(self, power_on: bool = False, abort_alarm: bool = False) -> None:
        with self._lock:
            if power_on:
                self.mpos = [0.0, 0.0, 0.0]
                self.wco = [0.0, 0.0, 0.0]
                self.homed = False
            self._rx.clear()
            self.planner: list[_Block] = []
            self.absolute = True
            self.motion = 0
            self.feed = 0.0
            self.hold = False
            self.check_mode = False
            self.homing_left = 0.0
            self.jogging = False
            self.critical = False
            self.reports = 0
            self.wco_dirty = True
            self._emit("")
            self._emit(f"Grbl {self.version} ['$' for help]")
            if abort_alarm:
                self.state = "Alarm"
                self.homed = False
                self._emit(self._msg("'$H'|'$X' to unlock"))
            elif self.settings.get(22, 0) and not self.homed:
                self.state = "Alarm"
                self._emit(self._msg("'$H'|'$X' to unlock"))
            else:
                self.state = "Idle"

    def _emit(self, line: str) -> None:
        self._out.put(line)

    def _msg(self, text: str) -> str:
        return f"[MSG:{text}]" if self.is_v11 else f"[{text}]"

    def _error(self, code: int) -> None:
        self._emit(f"error:{code}" if self.is_v11 else f"error: {ERR_TEXT_09.get(code, 'Invalid statement')}")

    def _alarm(self, code: int, critical: bool = False) -> None:
        self.planner.clear()
        self._rx.clear()
        self.homing_left = 0.0
        self.jogging = False
        self.hold = False
        self.state = "Alarm"
        self.homed = False
        self.critical = critical
        self._emit(f"ALARM:{code}" if self.is_v11 else f"ALARM: {ALARM_TEXT_09.get(code, 'Hard limit')}")
        if critical:
            self._emit(self._msg("Reset to continue"))

    def _moving(self) -> bool:
        return bool(self.planner) or self.homing_left > 0

    # ---- real-time commands --------------------------------------------------
    def _realtime(self, b: int) -> None:
        if b == 0x3F:  # '?'
            self._emit(self._status())
        elif b == 0x21:  # '!'
            if self.state in ("Run", "Jog"):
                self.hold = True
                self.state = "Hold"
        elif b == 0x7E:  # '~'
            if self.hold:
                self.hold = False
                self.state = "Run" if self.planner else "Idle"
        elif b == 0x85:  # jog cancel (1.1)
            if self.jogging:
                self.planner.clear()
                self._rx.clear()
                self.jogging = False
                self.state = "Idle"
        elif b == 0x18:  # soft reset
            moving = (self._moving() and not self.hold) or self.state == "Home"
            if moving:
                self._emit("ALARM:3" if self.is_v11 else "ALARM: Abort during cycle")
            self._reset_state(abort_alarm=moving)

    def _status(self) -> str:
        m = self.mpos
        w = [m[i] - self.wco[i] for i in range(3)]
        f3 = lambda v: ",".join(f"{x:.3f}" for x in v)
        state = self.state
        if self.is_v11:
            if state == "Hold":
                state = "Hold:0"
            parts = [state, f"MPos:{f3(m)}", f"FS:{self.feed:g},0"]
            self.reports += 1
            if self.reports % 10 == 1 or self.wco_dirty:
                parts.append(f"WCO:{f3(self.wco)}")
                self.wco_dirty = False
            return "<" + "|".join(parts) + ">"
        return f"<{state},MPos:{f3(m)},WPos:{f3(w)}>"

    # ---- main loop -------------------------------------------------------------
    def _run(self) -> None:
        last = time.monotonic()
        while self.is_open:
            time.sleep(self.tick)
            now = time.monotonic()
            dt = (now - last) * self.speed
            last = now
            with self._lock:
                self._advance(dt)
                self._consume_rx()

    def _advance(self, dt: float) -> None:
        if self.hold or self.state == "Alarm":
            return
        if self.homing_left > 0:
            self.homing_left -= dt
            if self.homing_left <= 0:
                self._finish_homing()
            return
        while dt > 0 and self.planner:
            blk = self.planner[0]
            left = blk.duration - blk.elapsed
            step = min(dt, left)
            blk.elapsed += step
            dt -= step
            frac = 1.0 if blk.duration <= 0 else min(1.0, blk.elapsed / blk.duration)
            if not blk.dwell:
                self.mpos = [s + (e - s) * frac for s, e in zip(blk.start, blk.end)]
            if blk.elapsed >= blk.duration - 1e-12:
                self.planner.pop(0)
        if not self.planner and self.state in ("Run", "Jog"):
            self.state = "Idle"
            self.jogging = False

    def _finish_homing(self) -> None:
        pull = self.settings.get(27, 1.0)
        mask = int(self.settings.get(23, 0))
        for i in range(3):
            travel = self.settings.get(130 + i, 200.0)
            # GRBL puts machine space in negative coordinates after homing
            self.mpos[i] = -travel + pull if mask & (1 << i) else -pull
        self.homing_left = 0.0
        self.homed = True
        self.state = "Idle"
        self._emit("ok")

    def _consume_rx(self) -> None:
        # GRBL only pulls a line out of RX once the planner has room for it.
        while True:
            if self.homing_left > 0:
                return
            nl = self._rx.find(b"\n")
            if nl < 0:
                return
            if len(self.planner) >= self.planner_size:
                return
            raw = bytes(self._rx[:nl]).decode("ascii", "replace").strip("\r ")
            del self._rx[:nl + 1]
            if not raw:
                continue
            self.received_lines.append(raw)
            self._execute(raw)

    # ---- command execution --------------------------------------------------------
    def _execute(self, line: str) -> None:
        for sub, code in self.fail_lines.items():
            if sub in line:
                self._error(code)
                return
        if len(line) > 79:
            self._error(11)
            return
        if self.critical:
            self._error(9)   # needs a reset first
            return
        if line.startswith("$"):
            self._system(line)
            return
        if self.state == "Alarm":
            self._error(9)
            return
        self._gcode(line)

    def _system(self, line: str) -> None:
        cmd = line.upper()
        if cmd == "$$":
            for n in sorted(self.settings):
                v = self.settings[n]
                val = f"{v:.3f}" if n in (11, 12, 24, 25, 27) or n >= 100 else f"{int(v)}"
                if self.is_v11:
                    self._emit(f"${n}={val}")
                else:
                    self._emit(f"${n}={val} ({DESCRIPTIONS_09.get(n, '')})")
            self._emit("ok")
        elif cmd == "$I":
            if self.is_v11:
                self._emit(f"[VER:{self.version}.20190825:]")
                self._emit(f"[OPT:V,{self.planner_size},{self.rx_size}]")
            else:
                self._emit(f"[{self.version}.20160726:]")
            self._emit("ok")
        elif cmd == "$X":
            if self.state == "Alarm":
                self.state = "Idle"
                self._emit(self._msg("Caution: Unlocked"))
            self._emit("ok")
        elif cmd == "$H":
            if not self.settings.get(22, 0):
                self._error(5)
                return
            if self._moving():
                self._error(8)
                return
            self.state = "Home"
            self.homing_left = self.homing_time
            # 'ok' is sent by _finish_homing when the cycle completes
        elif cmd == "$G":
            mode = "G90" if self.absolute else "G91"
            self._emit(f"[GC:G{self.motion} G54 G17 G21 {mode} G94 M5 M9 T0 F{self.feed:g} S0]"
                       if self.is_v11 else f"[G{self.motion} G54 G17 G21 {mode} G94 M0 M5 M9 T0 F{self.feed:g} S0.]")
            self._emit("ok")
        elif cmd == "$#":
            w = ",".join(f"{v:.3f}" for v in self.wco)
            self._emit(f"[G54:{w}]")
            self._emit("ok")
        elif cmd.startswith("$J="):
            if not self.is_v11:
                self._error(3)
                return
            if self.state not in ("Idle", "Jog"):
                self._error(8)
                return
            self._jog(cmd[3:])
        elif cmd == "$C":
            self.check_mode = not self.check_mode
            self.state = "Check" if self.check_mode else "Idle"
            self._emit(self._msg("Enabled" if self.check_mode else "Disabled"))
            self._emit("ok")
        else:
            m = re.match(r"^\$(\d+)=(-?\d*\.?\d+)$", cmd)
            if not m:
                self._error(3)
                return
            if self._moving():
                self._error(8)
                return
            n = int(m.group(1))
            if n not in self.settings:
                self._error(3)
                return
            self.settings[n] = float(m.group(2))
            self._emit("ok")

    def _words(self, line: str):
        s = re.sub(r"\([^)]*\)|;.*$", "", line).upper().replace(" ", "")
        words = re.findall(r"([A-Z])(-?\d*\.?\d+)", s)
        if "".join(a + b for a, b in words) != s:
            return None
        return words

    def _jog(self, body: str) -> None:
        words = self._words(body)
        if words is None:
            self._error(2)
            return
        absolute = self.absolute
        vals = {}
        for letter, val in words:
            if letter == "G":
                g = int(float(val))
                if g == 91:
                    absolute = False
                elif g == 90:
                    absolute = True
                elif g not in (20, 21, 53):
                    self._error(16)
                    return
            else:
                vals[letter] = float(val)
        if "F" not in vals:
            self._error(16)
            return
        target = self._target(vals, absolute)
        if self.settings.get(20, 0) and not self._within_travel(target):
            self._error(15)
            return
        self.jogging = True
        self._queue_move(target, vals["F"])
        self.state = "Jog"
        self._emit("ok")

    def _target(self, vals: dict, absolute: bool) -> list[float]:
        end = self.planner[-1].end if self.planner else self.mpos
        target = list(end)
        for i, a in enumerate("XYZ"):
            if a in vals:
                target[i] = vals[a] + self.wco[i] if absolute else end[i] + vals[a]
        return target

    def _within_travel(self, target: list[float]) -> bool:
        return all(-self.settings.get(130 + i, 200) - 1e-6 <= target[i] <= 1e-6 for i in range(3))

    def _queue_move(self, target: list[float], feed: float) -> None:
        start = list(self.planner[-1].end if self.planner else self.mpos)
        dist = math.dist(start, target)
        # Limit by per-axis max rate
        rate = feed
        if dist > 0:
            for i in range(3):
                d = abs(target[i] - start[i])
                if d > 0:
                    rate = min(rate, self.settings.get(110 + i, 1000) * dist / d)
        duration = dist / rate * 60.0 if dist > 0 and rate > 0 else 0.0
        if self.check_mode:
            return
        self.planner.append(_Block(start, target, duration))
        if self.state == "Idle":
            self.state = "Run"
        self.feed = feed

    def _gcode(self, line: str) -> None:
        words = self._words(line)
        if words is None:
            self._error(1)
            return
        vals: dict[str, float] = {}
        gs: list[float] = []
        for letter, val in words:
            if letter == "G":
                gs.append(float(val))
            elif letter == "M":
                if int(float(val)) not in (0, 1, 2, 3, 4, 5, 8, 9, 30):
                    self._error(20)
                    return
            elif letter == "N":
                continue
            else:
                vals[letter] = float(val)
        g10 = False
        dwell = False
        g92 = False
        absolute = self.absolute
        motion = self.motion
        for g in gs:
            gi = int(g)
            if gi in (0, 1):
                motion = gi
            elif gi == 90:
                absolute = True
            elif gi == 91:
                absolute = False
            elif gi == 4:
                dwell = True
            elif gi == 10:
                g10 = True
            elif gi == 92:
                g92 = True
            elif gi in (17, 21, 40, 49, 54, 80, 94):
                pass
            else:
                self._error(20)
                return
        self.absolute = absolute
        if g10:
            L = int(vals.get("L", -1))
            if L not in (2, 20) or int(vals.get("P", 1)) != 1:
                self._error(20)
                return
            end = self.planner[-1].end if self.planner else self.mpos
            for i, a in enumerate("XYZ"):
                if a in vals:
                    self.wco[i] = vals[a] if L == 2 else end[i] - vals[a]
            self.wco_dirty = True
            self._emit("ok")
            return
        if g92:
            end = self.planner[-1].end if self.planner else self.mpos
            for i, a in enumerate("XYZ"):
                if a in vals:
                    self.wco[i] = end[i] - vals[a]
            self.wco_dirty = True
            self._emit("ok")
            return
        if dwell:
            if "P" not in vals:
                self._error(28)
                return
            end = list(self.planner[-1].end if self.planner else self.mpos)
            self.planner.append(_Block(end, end, vals["P"], dwell=True))
            if self.state == "Idle":
                self.state = "Run"
            self._emit("ok")
            return
        self.motion = motion
        if "F" in vals:
            if vals["F"] <= 0:
                self._error(4)
                return
            self.feed = vals["F"]
        if any(a in vals for a in "XYZ"):
            if motion == 1 and self.feed <= 0:
                self._error(22)
                return
            target = self._target(vals, absolute)
            if self.settings.get(20, 0) and self.homed and not self._within_travel(target):
                self._alarm(2, critical=True)
                return
            rate = self.feed if motion == 1 else max(self.settings.get(110 + i, 1000) for i in range(3))
            self._queue_move(target, rate)
        self._emit("ok")

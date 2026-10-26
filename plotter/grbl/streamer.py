"""GRBL controller: character-counting streamer, status polling, real-time commands,
homing interlock and in-stream safety checks. Pure Python (threads + callbacks);
the GUI wraps the callbacks in Qt signals.

Character counting: GRBL has a 128-byte serial RX buffer. We track the bytes of every
line sent but not yet answered with ``ok``/``error`` and only send the next line when
it fits in ``RX_LIMIT`` (127) bytes. That keeps GRBL's planner full without ever
overflowing its buffer.
"""
from __future__ import annotations

import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from ..gcode.validator import Bounds, GcodeChecker, strip_line
from .connection import RT_HOLD, RT_JOG_CANCEL, RT_RESET, RT_RESUME, RT_STATUS, TransportError
from .protocol import (Alarm, Banner, Error, Message, Ok, Setting, Status, VersionInfo,
                       parse_line)

RX_LIMIT = 127
MAX_LINE = 79

MOTION_G = {0, 1, 2, 3, 28, 30, 38}
NON_MOTION_AXIS_G = {4, 10, 92}   # these take axis words without moving


@dataclass
class Event:
    kind: str
    data: Any = None


class JobState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    DONE = "done"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass
class JobProgress:
    state: JobState
    total: int = 0
    sent: int = 0
    acked: int = 0
    message: str = ""
    failed_line: int | None = None      # 1-based line number in the original program
    last_line: int = 0                  # original line number of the last acknowledged line

    @property
    def fraction(self) -> float:
        return self.acked / self.total if self.total else 0.0


class InterlockError(RuntimeError):
    """Motion refused: the machine has not been homed this session."""


class BusyError(RuntimeError):
    """Command refused because a job is running or the controller is not ready."""


@dataclass
class _Cmd:
    line: str
    raw: str = ""
    job_line: int | None = None        # original 1-based line number for job lines
    callback: Callable[[bool, list[str], Error | None], None] | None = None
    collected: list[str] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.line) + 1


_AXIS_RE = re.compile(r"[XYZ][-+]?[\d.]")
_G_RE = re.compile(r"G(\d+(?:\.\d+)?)")


def is_motion_line(line: str) -> bool:
    """True if sending this line could move an axis."""
    s = strip_line(line)
    if not s:
        return False
    if s.startswith("$J="):
        return True
    if s.startswith("$"):
        return False
    gs = {int(float(g)) for g in _G_RE.findall(s)}
    if gs & MOTION_G:
        return True
    if gs & NON_MOTION_AXIS_G:
        return False
    return bool(_AXIS_RE.search(s))


class GrblController:
    def __init__(self, transport, listener: Callable[[Event], None] | None = None,
                 poll_interval: float = 0.2, homing_required: bool = True):
        self.t = transport
        self.listener = listener or (lambda e: None)
        self.poll_interval = poll_interval
        self.homing_required = homing_required
        self.pen_up_command: str | None = None   # sent after re-homing following a Stop

        self.version: str | None = None
        self.settings: dict[int, float] = {}
        self.status: Status | None = None
        self.wco: tuple[float, float, float] | None = None
        self.homed = False
        self.homing = False
        self.connected = False

        self._lock = threading.RLock()
        self._wlock = threading.Lock()
        self._queue: deque[_Cmd] = deque()
        self._inflight: deque[_Cmd] = deque()
        self._inflight_bytes = 0
        self._job = JobProgress(JobState.IDLE)
        self._guard: GcodeChecker | None = None
        self._idle_after_done = 0
        self._lift_after_home = False
        self._banner_seen = threading.Event()
        self._running = False
        self._threads: list[threading.Thread] = []

    # ------------------------------------------------------------------ util
    def _emit(self, kind: str, data: Any = None) -> None:
        try:
            self.listener(Event(kind, data))
        except Exception:  # a broken listener must never kill the serial threads
            pass

    def _write(self, data: bytes, log: str | None = None) -> None:
        with self._wlock:
            try:
                self.t.write(data)
            except (TransportError, OSError, ConnectionError) as exc:
                self._connection_lost(str(exc))
                return
        if log is not None:
            self._emit("tx", log)

    @property
    def is_v11(self) -> bool:
        return bool(self.version) and self.version.startswith("1.")

    @property
    def job(self) -> JobProgress:
        return self._job

    @property
    def job_active(self) -> bool:
        return self._job.state in (JobState.RUNNING, JobState.PAUSED)

    @property
    def inflight_bytes(self) -> int:
        return self._inflight_bytes

    def motion_allowed(self) -> bool:
        return self.homed or not self.homing_required

    # ------------------------------------------------------------ lifecycle
    def start(self, banner_timeout: float = 3.0, query: bool = True) -> bool:
        """Start the I/O threads and wait for GRBL's banner. Returns True if seen."""
        self._running = True
        self.connected = True
        for target, name in ((self._reader, "grbl-reader"), (self._poller, "grbl-poller")):
            th = threading.Thread(target=target, name=name, daemon=True)
            th.start()
            self._threads.append(th)
        seen = self._banner_seen.wait(banner_timeout)
        if not seen:
            # Board did not reset on open (or banner missed): ask for a soft reset.
            self._write(RT_RESET, "<soft reset>")
            seen = self._banner_seen.wait(banner_timeout)
        self._emit("connection", {"connected": True, "banner": seen})
        if query:
            self.query_info()
        return seen

    def close(self) -> None:
        self._running = False
        self.connected = False
        for th in self._threads:
            if th is not threading.current_thread():
                th.join(timeout=1.0)
        self._threads.clear()
        try:
            self.t.close()
        except Exception:
            pass
        self._emit("connection", {"connected": False})

    def _connection_lost(self, msg: str) -> None:
        if not self.connected:
            return
        self.connected = False
        self._running = False
        with self._lock:
            self._flush_pending()
            if self.job_active:
                self._set_job(JobState.FAILED, f"Connection lost: {msg}")
        self.homed = False
        self._emit("connection", {"connected": False, "error": msg})

    # --------------------------------------------------------------- threads
    def _poller(self) -> None:
        while self._running:
            time.sleep(self.poll_interval)
            # Don't talk to the board until GRBL has booted (the Uno bootloader
            # listens on the serial port for a moment after the port opens).
            if self._running and self._banner_seen.is_set():
                self._write(RT_STATUS)

    def _reader(self) -> None:
        while self._running:
            try:
                line = self.t.read_line(0.1)
            except (TransportError, OSError, ConnectionError) as exc:
                self._connection_lost(str(exc))
                return
            if line is None:
                continue
            line = line.strip()
            if not line:
                continue
            try:
                self._handle_line(line)
            except Exception as exc:  # keep the reader alive whatever happens
                self._emit("info", f"Internal error handling {line!r}: {exc}")

    # ------------------------------------------------------------- receiving
    def _handle_line(self, line: str) -> None:
        msg = parse_line(line)
        self._emit("rx", line)
        if isinstance(msg, Status):
            self._on_status(msg)
        elif isinstance(msg, Ok):
            self._on_ack(None)
        elif isinstance(msg, Error):
            self._on_ack(msg)
        elif isinstance(msg, Alarm):
            self._on_alarm(msg)
        elif isinstance(msg, Banner):
            self._on_banner(msg)
        else:
            with self._lock:
                if self._inflight:
                    self._inflight[0].collected.append(line)
            if isinstance(msg, Setting):
                self.settings[msg.number] = msg.value
            elif isinstance(msg, VersionInfo):
                self.version = msg.version
                self._emit("version", msg.version)
            elif isinstance(msg, Message):
                self._emit("message", msg)

    def _on_status(self, st: Status) -> None:
        st.resolve(self.wco)
        if st.wco:
            self.wco = st.wco
        self.status = st
        self._emit("status", st)
        with self._lock:
            if (self._job.state == JobState.RUNNING and self._job.acked >= self._job.total
                    and not self._queue and not self._inflight):
                if st.state == "Idle":
                    self._idle_after_done += 1
                    if self._idle_after_done >= 2:
                        self._guard = None
                        self._set_job(JobState.DONE, "Job finished.")
                else:
                    self._idle_after_done = 0

    def _on_ack(self, err: Error | None) -> None:
        with self._lock:
            if not self._inflight:
                return  # stray ok (e.g. after a reset); nothing to match
            cmd = self._inflight.popleft()
            self._inflight_bytes -= cmd.size
        if err is not None:
            self._emit("error", (err, cmd.line))
        if cmd.callback:
            try:
                cmd.callback(err is None, cmd.collected, err)
            except Exception as exc:
                self._emit("info", f"Callback error for {cmd.line!r}: {exc}")
        if cmd.job_line is not None:
            with self._lock:
                if err is not None and self.job_active:
                    self._fail_job(f"GRBL rejected line {cmd.job_line}: {cmd.raw.strip()} "
                                   f"-> {err.text}: {err.explanation}", cmd.job_line)
                elif self.job_active:
                    self._job.acked += 1
                    self._job.last_line = cmd.job_line
                    self._emit("job", self._snapshot())
        self._pump()

    def _on_alarm(self, alarm: Alarm) -> None:
        with self._lock:
            self._flush_pending()
            if self.job_active:
                self._set_job(JobState.FAILED, f"ALARM: {alarm.name}. {alarm.explanation}")
        self.homing = False
        self._set_homed(False)
        self._emit("alarm", alarm)

    def _on_banner(self, banner: Banner) -> None:
        first = not self._banner_seen.is_set()
        self.version = banner.version
        with self._lock:
            self._flush_pending()
            if self.job_active:
                self._set_job(JobState.FAILED, "GRBL was reset during the job.")
        self.homing = False
        if not first:
            self._set_homed(False)
        self._banner_seen.set()
        self._emit("banner", banner)

    def _flush_pending(self) -> None:
        """GRBL discards its RX buffer on reset/alarm: forget everything in flight."""
        for cmd in list(self._inflight) + list(self._queue):
            if cmd.callback and cmd.job_line is None:
                try:
                    cmd.callback(False, cmd.collected, None)
                except Exception:
                    pass
        self._inflight.clear()
        self._queue.clear()
        self._inflight_bytes = 0

    # --------------------------------------------------------------- sending
    def _pump(self) -> None:
        with self._lock:
            while self._queue and self.connected:
                cmd = self._queue[0]
                if self._inflight_bytes + cmd.size > RX_LIMIT:
                    break
                if cmd.job_line is not None:
                    if not self.job_active:
                        self._queue.popleft()
                        continue
                    if self._guard is not None:
                        issues = self._guard.check_line(cmd.raw, cmd.job_line)
                        if issues:
                            self._fail_job("Safety check refused line "
                                           f"{cmd.job_line}: {issues[0].message} "
                                           f"[{cmd.raw.strip()}]", cmd.job_line)
                            return
                self._queue.popleft()
                self._inflight.append(cmd)
                self._inflight_bytes += cmd.size
                if cmd.job_line is not None:
                    self._job.sent += 1
                self._write((cmd.line + "\n").encode("ascii"), cmd.line)

    def _enqueue(self, cmd: _Cmd) -> None:
        if len(cmd.line) > MAX_LINE:
            raise ValueError(f"Line longer than {MAX_LINE} characters: {cmd.line[:40]}...")
        with self._lock:
            self._queue.append(cmd)
        self._pump()

    def send_command(self, line: str, callback=None, allow_during_job: bool = False) -> None:
        """Queue one user/system command (character-counted like everything else)."""
        if not self.connected:
            raise BusyError("Not connected")
        s = line.strip()
        if not s:
            return
        if self.job_active and not allow_during_job:
            raise BusyError("A job is running; pause/stop it first")
        if is_motion_line(s) and not self.motion_allowed():
            raise InterlockError("Home the machine ($H) first. Motion is blocked until a "
                                 "successful homing cycle in this session.")
        if self.homing and is_motion_line(s):
            raise BusyError("Homing in progress")
        self._enqueue(_Cmd(s, s, None, callback))

    def realtime(self, byte: bytes, label: str) -> None:
        self._write(byte, label)

    # ------------------------------------------------------------ operations
    def query_info(self) -> None:
        def on_info(ok, lines, err):
            for l in lines:
                m = parse_line(l)
                if isinstance(m, VersionInfo):
                    self.version = m.version
        self._enqueue(_Cmd("$I", "$I", None, on_info))
        self.read_settings()

    def read_settings(self, callback: Callable[[dict[int, float]], None] | None = None) -> None:
        def on_settings(ok, lines, err):
            parsed = {}
            for l in lines:
                m = parse_line(l)
                if isinstance(m, Setting):
                    parsed[m.number] = m.value
            if ok:
                self.settings.update(parsed)
                self._emit("settings", dict(self.settings))
                if callback:
                    callback(dict(self.settings))
        self._enqueue(_Cmd("$$", "$$", None, on_settings))

    def write_setting(self, number: int, value: float | str, callback=None) -> None:
        if self.job_active:
            raise BusyError("Cannot change settings while a job is running")
        if self.status and self.status.state not in ("Idle", "Alarm"):
            raise BusyError("GRBL must be idle to change settings")
        line = f"${number}={value}"
        def on_done(ok, lines, err):
            if ok:
                try:
                    self.settings[number] = float(value)
                except ValueError:
                    pass
            if callback:
                callback(ok, err)
        self._enqueue(_Cmd(line, line, None, on_done))

    def home(self) -> None:
        if self.job_active:
            raise BusyError("A job is running")
        if self.homing:
            raise BusyError("Homing already in progress")
        self.homing = True
        self._emit("info", "Homing started (Z first, then X and Y).")

        def on_home(ok, lines, err):
            self.homing = False
            self._set_homed(ok)
            if ok:
                self._emit("info", "Homing complete.")
                if self._lift_after_home and self.pen_up_command:
                    self._lift_after_home = False
                    try:
                        for cmd in self.pen_up_command.split("\n"):
                            self.send_command(cmd)
                        self._emit("info", "Pen lifted after re-homing.")
                    except (BusyError, InterlockError) as exc:
                        self._emit("info", f"Could not lift pen: {exc}")
            else:
                self._emit("info", "Homing failed.")
        self._enqueue(_Cmd("$H", "$H", None, on_home))

    def unlock(self) -> None:
        self._enqueue(_Cmd("$X", "$X", None, None))
        if self.homing_required:
            self._emit("info", "Alarm unlocked with $X. Motion stays blocked until you home ($H).")

    def _set_homed(self, value: bool) -> None:
        if self.homed != value:
            self.homed = value
            self._emit("homed", value)

    def jog(self, axis: str, distance: float, feed: float) -> None:
        axis = axis.upper()
        if axis not in "XYZ" or len(axis) != 1:
            raise ValueError("axis must be X, Y or Z")
        d = f"{distance:.3f}".rstrip("0").rstrip(".")
        if self.is_v11:
            self.send_command(f"$J=G91 G21 {axis}{d} F{feed:g}")
        else:
            # GRBL 0.9 has no $J: small incremental move, then back to absolute mode.
            self.send_command(f"G91 G21 G1 {axis}{d} F{feed:g}")
            self.send_command("G90")

    def jog_cancel(self) -> bool:
        """Cancel a running jog (GRBL 1.1 only). Returns False on 0.9."""
        if not self.is_v11:
            return False
        self.realtime(RT_JOG_CANCEL, "<jog cancel>")
        return True

    # ------------------------------------------------------------------- job
    def start_job(self, lines: list[str], bounds: Bounds) -> JobProgress:
        """Stream a validated program. Every line is re-checked against ``bounds`` right
        before it is sent; the job fails on the first violation, error or alarm."""
        if not self.connected:
            raise BusyError("Not connected")
        if self.job_active:
            raise BusyError("A job is already running")
        if self.homing:
            raise BusyError("Homing in progress")
        if not self.motion_allowed():
            raise InterlockError("Home the machine ($H) before plotting.")
        if self.status and self.status.state not in ("Idle",):
            raise BusyError(f"GRBL is {self.status.state}; it must be Idle to start a job")
        cmds = []
        for i, raw in enumerate(lines, start=1):
            s = strip_line(raw)
            if not s:
                continue
            if len(s) > MAX_LINE:
                raise ValueError(f"Line {i} is longer than {MAX_LINE} characters")
            cmds.append(_Cmd(s, raw, i))
        with self._lock:
            self._guard = GcodeChecker(bounds)
            self._job = JobProgress(JobState.RUNNING, total=len(cmds), message="Plotting...")
            self._idle_after_done = 0
            self._queue.extend(cmds)
        self._emit("job", self._snapshot())
        self._pump()
        return self._snapshot()

    def pause(self) -> None:
        self.realtime(RT_HOLD, "! (feed hold)")
        with self._lock:
            if self._job.state == JobState.RUNNING:
                self._set_job(JobState.PAUSED, "Paused (feed hold).")

    def resume(self) -> None:
        with self._lock:
            if self._job.state == JobState.FAILED:
                raise BusyError("The job failed; it cannot be resumed. Stop and re-home.")
        self.realtime(RT_RESUME, "~ (cycle start/resume)")
        with self._lock:
            if self._job.state == JobState.PAUSED:
                self._set_job(JobState.RUNNING, "Plotting...")

    def stop(self, hold_timeout: float = 0.6) -> None:
        """Feed hold, soft reset, then require re-homing. Runs the wait off-thread."""
        def sequence():
            self._write(RT_HOLD, "! (feed hold)")
            deadline = time.monotonic() + hold_timeout
            while time.monotonic() < deadline:
                st = self.status.state if self.status else ""
                if st.startswith("Hold") or st in ("Idle", "Alarm"):
                    break
                time.sleep(0.02)
            with self._lock:
                self._flush_pending()
                if self.job_active or self._job.state == JobState.FAILED:
                    self._set_job(JobState.STOPPED, "Stopped. Home ($H) before plotting again.")
                self._lift_after_home = True
            self._write(RT_RESET, "<soft reset 0x18>")
            self._set_homed(False)
            self._emit("info", "Machine stopped with a soft reset. Position is no longer "
                               "trusted: home ($H) before any motion. The pen is lifted after homing.")
        th = threading.Thread(target=sequence, name="grbl-stop", daemon=True)
        th.start()

    def _fail_job(self, message: str, line_no: int | None) -> None:
        """Stop feeding a job after an error/refusal and hold the machine where it is."""
        for cmd in list(self._queue):
            if cmd.job_line is not None:
                self._queue.remove(cmd)
        self._guard = None
        self._write(RT_HOLD, "! (feed hold after failure)")
        self._set_job(JobState.FAILED, message + "  Machine is in feed hold; press Stop.",
                      line_no)

    def _set_job(self, state: JobState, message: str, failed_line: int | None = None) -> None:
        self._job.state = state
        self._job.message = message
        if failed_line is not None:
            self._job.failed_line = failed_line
        self._emit("job", self._snapshot())

    def _snapshot(self) -> JobProgress:
        j = self._job
        return JobProgress(j.state, j.total, j.sent, j.acked, j.message, j.failed_line, j.last_line)

    # ------------------------------------------------------------- testing aid
    def wait_for(self, predicate: Callable[[], bool], timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.01)
        return predicate()

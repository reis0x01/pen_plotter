import queue
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plotter.config import MachineProfile  # noqa: E402
from plotter.grbl.connection import SimulatedGrbl  # noqa: E402
from plotter.grbl.streamer import GrblController  # noqa: E402


class FakeTransport:
    """Scriptable transport: records writes; the test feeds GRBL responses."""

    def __init__(self, banner: str | None = "Grbl 1.1h ['$' for help]"):
        self.writes: list[bytes] = []
        self.incoming: queue.Queue[str] = queue.Queue()
        self.is_open = True
        if banner:
            self.incoming.put(banner)

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    def read_line(self, timeout: float = 0.1):
        try:
            return self.incoming.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        self.is_open = False

    def feed(self, *lines: str) -> None:
        for l in lines:
            self.incoming.put(l)

    def sent_lines(self) -> list[str]:
        return [w.decode().rstrip("\n") for w in self.writes if w.endswith(b"\n")]


@pytest.fixture
def profile():
    return MachineProfile()


@pytest.fixture
def events():
    return []


def make_sim_controller(version="1.1h", speed=500.0, events=None, **sim_kwargs):
    sim = SimulatedGrbl(version=version, speed=speed, **sim_kwargs)
    ctl = GrblController(sim, (events.append if events is not None else None), poll_interval=0.02)
    assert ctl.start(banner_timeout=1.0)
    assert ctl.wait_for(lambda: 130 in ctl.settings, 3)
    return sim, ctl


@pytest.fixture(params=["1.1h", "0.9j"])
def sim_ctl(request, events):
    sim, ctl = make_sim_controller(request.param, events=events)
    yield sim, ctl
    ctl.close()
    sim.close()


def home_and_set_origin(ctl, x=-250.0, y=-278.0):
    """Home, jog the pen to the safe-area corner at machine (x, y) and make it work (10, 10).

    A4 is 297 mm long but Y travel is 280 mm, so the reachable reference is the corner
    of the safe area (10 mm inside the paper corner), not the paper corner itself.
    """
    ctl.home()
    assert ctl.wait_for(lambda: ctl.homed, 5)
    ctl.send_command(f"G90 G0 X{x} Y{y} Z-1")   # machine == work before offsets are set
    ctl.send_command("G10 L20 P1 X10 Y10 Z0")
    assert ctl.wait_for(lambda: ctl.status is not None and ctl.status.state == "Idle"
                        and not ctl._inflight and not ctl._queue, 5)
    assert ctl.wait_for(lambda: ctl.wco is not None and abs(ctl.wco[1] - (y - 10)) < 1e-6, 3)
    # The newest status report decides whether a job may start: wait until it says Idle too.
    assert ctl.wait_for(lambda: ctl.status.state == "Idle" and not ctl._inflight and not ctl._queue, 5)

import time

import pytest

from conftest import FakeTransport, home_and_set_origin, make_sim_controller
from plotter.config import MachineProfile
from plotter.gcode.validator import Bounds
from plotter.grbl.streamer import (RX_LIMIT, BusyError, GrblController, InterlockError, JobState,
                                   is_motion_line)
from plotter.pipeline import machine_bounds, plan_text


def job_lines(profile, text="Hello world"):
    plan = plan_text(text, profile)
    assert plan.ok
    return plan.pages[0].gcode


def wait_job_end(ctl, timeout=20):
    return ctl.wait_for(lambda: ctl.job.state not in (JobState.RUNNING, JobState.PAUSED), timeout)


# ---- pure character counting (scripted transport) ----------------------------------------------------

def test_character_counting_never_exceeds_rx_buffer():
    t = FakeTransport()
    ctl = GrblController(t, poll_interval=10, homing_required=False)
    assert ctl.start(banner_timeout=1, query=False)
    lines = [f"G1 X{i}.123456 Y{i}.654321 F1000" for i in range(20)]   # ~31 bytes each
    bounds = Bounds(-1e9, 1e9, -1e9, 1e9, pen_up_z=0, pen_down_z=-10)
    ctl.start_job(["G1 Z0 F100"] + lines, bounds)
    time.sleep(0.1)
    sent = t.sent_lines()
    # 9 bytes + 4 x 26 bytes = 113; a fifth 26-byte line would make 139 > 127
    assert len(sent) == 5
    assert sum(len(l) + 1 for l in sent) <= RX_LIMIT
    assert ctl.inflight_bytes == sum(len(l) + 1 for l in sent)
    n_before = len(sent)
    t.feed("ok")          # frees only the 9-byte first line: not enough room for 26 bytes
    time.sleep(0.1)
    assert len(t.sent_lines()) == n_before
    t.feed("ok")          # frees a 26-byte line: exactly one more may go out
    assert ctl.wait_for(lambda: len(t.sent_lines()) == n_before + 1, 1)
    assert ctl.inflight_bytes <= RX_LIMIT
    # acknowledge everything; the job must be fully sent in order
    for _ in range(len(lines) - 1):
        t.feed("ok")
    assert ctl.wait_for(lambda: ctl.job.acked == len(lines) + 1, 2)
    assert t.sent_lines() == ["G1Z0F100"] + [l.replace(" ", "") for l in lines]
    ctl.close()


def test_is_motion_line():
    assert is_motion_line("G0 X1")
    assert is_motion_line("X5 Y5")           # modal motion
    assert is_motion_line("$J=G91 X1 F100")
    assert is_motion_line("G28")
    assert not is_motion_line("$H")
    assert not is_motion_line("$$")
    assert not is_motion_line("G10 L20 P1 X0 Y0")
    assert not is_motion_line("G4 P1")
    assert not is_motion_line("G90 G21")


# ---- against the simulated GRBL (both 0.9j and 1.1h via the fixture) ------------------------------------

def test_version_and_settings_detected(sim_ctl):
    sim, ctl = sim_ctl
    assert ctl.version == sim.version
    assert ctl.settings[130] == 280.0
    assert ctl.settings[100] == 80.0


def test_homing_interlock_blocks_motion_until_homed(sim_ctl):
    sim, ctl = sim_ctl
    with pytest.raises(InterlockError):
        ctl.send_command("G0 X-10")
    with pytest.raises(InterlockError):
        ctl.jog("X", -1, 500)
    with pytest.raises(InterlockError):
        ctl.start_job(["G1 Z0 F100"], Bounds.from_profile(MachineProfile()))
    ctl.send_command("$$")                      # non-motion is fine
    ctl.home()
    assert ctl.wait_for(lambda: ctl.homed, 5)
    ctl.send_command("G0 X-10")                  # now allowed


def test_interlock_override(sim_ctl):
    sim, ctl = sim_ctl
    ctl.homing_required = False
    ctl.unlock()
    assert ctl.wait_for(lambda: ctl.status and ctl.status.state == "Idle", 3)
    ctl.send_command("G0 X-5")


def test_full_job_streams_without_overflow(sim_ctl, profile):
    sim, ctl = sim_ctl
    home_and_set_origin(ctl)
    lines = job_lines(profile, "The quick brown fox jumps")
    bounds = machine_bounds(profile, ctl.settings, ctl.wco)
    assert bounds.wco is not None and bounds.max_travel == (280, 280, 40)
    ctl.start_job(lines, bounds)
    assert wait_job_end(ctl)
    assert ctl.job.state == JobState.DONE, ctl.job.message
    assert ctl.job.acked == ctl.job.total == len([l for l in lines if not l.startswith(";")])
    assert not sim.overflowed
    assert sim.max_rx_used <= RX_LIMIT
    # ended parked with the pen up
    assert ctl.wait_for(lambda: ctl.status.wpos is not None, 1)
    assert ctl.status.wpos == pytest.approx((profile.park_x, profile.park_y, profile.pen_up_z), abs=1e-3)


def test_job_error_halts_and_holds(sim_ctl, profile):
    sim, ctl = sim_ctl
    home_and_set_origin(ctl)
    lines = job_lines(profile)
    target = lines[20]
    sim.fail_lines[target.replace(" ", "")] = 20
    ctl.start_job(lines, Bounds.from_profile(profile))
    assert wait_job_end(ctl)
    assert ctl.job.state == JobState.FAILED
    assert ctl.job.failed_line == 21
    assert "rejected" in ctl.job.message
    assert ctl.wait_for(lambda: ctl.status.state == "Hold", 2)
    sent_after = len(sim.received_lines)
    time.sleep(0.2)
    assert len(sim.received_lines) == sent_after        # nothing more was streamed


def test_alarm_during_job_fails_job_and_clears_homed(sim_ctl, profile, events):
    sim, ctl = sim_ctl
    home_and_set_origin(ctl)
    sim.speed = 20
    ctl.start_job(job_lines(profile, "alarm test text " * 3), Bounds.from_profile(profile))
    assert ctl.wait_for(lambda: ctl.job.acked > 5, 5)
    sim.trigger_alarm(1)
    assert wait_job_end(ctl, 5)
    assert ctl.job.state == JobState.FAILED and "ALARM" in ctl.job.message
    assert not ctl.homed
    assert any(e.kind == "alarm" and e.data.code == 1 for e in events)
    with pytest.raises(InterlockError):
        ctl.send_command("G0 X1")


def test_streamer_guard_refuses_out_of_bounds_line(sim_ctl, profile):
    sim, ctl = sim_ctl
    home_and_set_origin(ctl)
    lines = ["G21 G90", "G1 Z0 F300", "G0 X50 Y50", "G1 Z-4 F300", "G1 X60 Y60 F1000",
             "G1 X250 Y60", "G1 Z0 F300"]
    ctl.start_job(lines, Bounds.from_profile(profile))
    assert wait_job_end(ctl)
    assert ctl.job.state == JobState.FAILED
    assert ctl.job.failed_line == 6
    assert "Safety check" in ctl.job.message
    assert not any("X250" in l for l in sim.received_lines)


def test_pause_resume(sim_ctl, profile):
    sim, ctl = sim_ctl
    home_and_set_origin(ctl)
    sim.speed = 20
    ctl.start_job(job_lines(profile, "pause and resume"), Bounds.from_profile(profile))
    assert ctl.wait_for(lambda: ctl.status.state == "Run", 3)
    ctl.pause()
    assert ctl.job.state == JobState.PAUSED
    assert ctl.wait_for(lambda: ctl.status.state == "Hold", 2)
    pos = ctl.status.mpos
    time.sleep(0.2)
    assert ctl.status.mpos == pos                         # not moving while held
    ctl.resume()
    assert ctl.job.state == JobState.RUNNING
    sim.speed = 500
    assert wait_job_end(ctl)
    assert ctl.job.state == JobState.DONE


def test_stop_resets_requires_rehome_and_lifts_pen(sim_ctl, profile, events):
    sim, ctl = sim_ctl
    home_and_set_origin(ctl)
    ctl.pen_up_command = f"G90 G1 Z{profile.pen_up_z} F{profile.z_feed}"
    sim.speed = 20
    ctl.start_job(job_lines(profile, "stop me please"), Bounds.from_profile(profile))
    assert ctl.wait_for(lambda: ctl.job.acked > 5, 5)
    ctl.stop()
    assert ctl.wait_for(lambda: ctl.job.state == JobState.STOPPED, 3)
    assert ctl.wait_for(lambda: not ctl.homed, 2)
    assert ctl.wait_for(lambda: any(e.kind == "banner" for e in events[-20:]), 3)
    with pytest.raises(InterlockError):
        ctl.send_command("G0 X1")
    sim.speed = 500
    ctl.home()
    assert ctl.wait_for(lambda: ctl.homed, 5)
    assert ctl.wait_for(lambda: any("Z0" in l and "G1" in l for l in sim.received_lines[-3:]), 3)


def test_jog_uses_version_specific_command(sim_ctl):
    sim, ctl = sim_ctl
    ctl.home()
    assert ctl.wait_for(lambda: ctl.homed, 5)
    ctl.jog("X", -2.5, 800)
    assert ctl.wait_for(lambda: len(sim.received_lines) and "X-2.5" in sim.received_lines[-1]
                        or (len(sim.received_lines) > 1 and "X-2.5" in sim.received_lines[-2]), 2)
    if sim.is_v11:
        assert any(l.startswith("$J=") for l in sim.received_lines)
    else:
        assert "G91 G21 G1 X-2.5 F800" in sim.received_lines
        assert ctl.wait_for(lambda: sim.received_lines[-1] == "G90", 2)
    assert ctl.wait_for(lambda: ctl.status.mpos[0] == pytest.approx(-3.5, abs=1e-3), 3)


def test_busy_while_job_running(sim_ctl, profile):
    sim, ctl = sim_ctl
    home_and_set_origin(ctl)
    sim.speed = 5
    ctl.start_job(job_lines(profile), Bounds.from_profile(profile))
    with pytest.raises(BusyError):
        ctl.send_command("G0 X0")
    with pytest.raises(BusyError):
        ctl.start_job(["G1 Z0 F100"], Bounds.from_profile(profile))
    ctl.stop()
    assert ctl.wait_for(lambda: ctl.job.state == JobState.STOPPED, 3)


def test_write_setting_roundtrip(sim_ctl):
    sim, ctl = sim_ctl
    results = []
    ctl.write_setting(110, 2500, lambda ok, err: results.append(ok))
    assert ctl.wait_for(lambda: results == [True], 2)
    assert sim.settings[110] == 2500
    assert ctl.settings[110] == 2500


def test_connection_lost_fails_job(profile, events):
    sim, ctl = make_sim_controller("1.1h", events=events)
    home_and_set_origin(ctl)
    sim.speed = 5
    ctl.start_job(job_lines(profile), Bounds.from_profile(profile))
    sim.close()       # unplugged
    assert wait_job_end(ctl, 3)
    assert ctl.job.state == JobState.FAILED
    assert any(e.kind == "connection" and e.data.get("connected") is False for e in events)
    ctl.close()


def test_machine_envelope_guard_in_streamer(sim_ctl, profile):
    """Origin placed so that the top of the page is beyond the Y travel: refused, not clamped."""
    sim, ctl = sim_ctl
    home_and_set_origin(ctl, -250, -200)        # safe area Y 10..287 -> machine -200..+77
    bounds = machine_bounds(profile, ctl.settings, ctl.wco)
    ctl.start_job(job_lines(profile), bounds)
    assert wait_job_end(ctl)
    assert ctl.job.state == JobState.FAILED
    assert "machine travel" in ctl.job.message

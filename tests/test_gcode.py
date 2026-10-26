import re

import pytest

from plotter.config import MachineProfile
from plotter.gcode.estimate import MotionLimits, estimate_seconds, format_duration
from plotter.gcode.generator import GcodeGenerator, GcodeOptions, fmt
from plotter.gcode.validator import Bounds, GcodeChecker, validate, validate_for_profile
from plotter.pipeline import plan_gcode, plan_text

BOUNDS = Bounds(10, 200, 10, 287, pen_up_z=0.0, pen_down_z=-4.0)


def gen(strokes, **kw):
    opts = GcodeOptions(**{"park": (10, 10), **kw})
    return GcodeGenerator(opts).generate(strokes)


def z_of(line):
    return float(line.split("Z")[1].split()[0]) if "Z" in line else None


# ---- generator --------------------------------------------------------------------------

def test_fmt():
    assert fmt(1.0) == "1" and fmt(-0.0) == "0" and fmt(1.23456) == "1.235" and fmt(-4) == "-4"


def test_program_starts_with_pen_up_and_ends_up_and_parked():
    g = gen([[(20, 20), (30, 30)]])
    motion = [l for l in g if not l.startswith(";")]
    assert motion[0] == "G21 G90 G94"
    assert motion[1].startswith("G1 Z0 ")                 # pen up before anything moves
    assert motion[-1] == "M2"
    assert motion[-2] == "G1 X10 Y10 F2000"             # park
    assert z_of(motion[-3]) == 0.0                        # pen up before park


def test_pen_discipline_per_stroke():
    g = gen([[(20, 20), (30, 20), (30, 30)], [(50, 50), (60, 60)]])
    pen = "up"
    for line in g:
        if line.startswith("G1 Z") or line.startswith("G0 Z"):
            pen = "down" if z_of(line) < 0 else "up"
            assert "X" not in line and "Y" not in line      # Z-only pen moves
        elif ("X" in line or "Y" in line):
            if line.startswith("G0") or "F2000" in line:
                assert pen == "up", f"travel with pen down: {line}"
    assert pen == "up"
    # each stroke: travel, pen down, draws..., pen up
    i = g.index("G1 X20 Y20 F2000")
    assert g[i + 1] == "G1 Z-4 F300"
    assert g[i + 2] == "G1 X30 Y20 F1000"
    assert g[i + 3] == "G1 X30 Y30"
    assert g[i + 4] == "G1 Z0 F300"


def test_g0_travel_when_travel_feed_disabled():
    g = gen([[(20, 20), (30, 30)]], travel_feed=0)
    assert "G0 X20 Y20" in g
    assert "G0 X10 Y10" in g


def test_only_straight_moves():
    g = gen([[(20, 20), (25, 21), (30, 30)]])
    assert not any(re.search(r"\bG0?[23]\b", l) for l in g)


def test_single_point_stroke_makes_a_dot():
    g = gen([[(40, 40)]])
    i = g.index("G1 X40 Y40 F2000")
    assert g[i + 1] == "G1 Z-4 F300" and g[i + 2] == "G1 Z0 F300"


def test_duplicate_points_removed():
    g = gen([[(20, 20), (20, 20), (30, 30), (30.001, 30.001)]])
    assert sum(1 for l in g if l.startswith("G1 X") and "F2000" not in l) == 1


def test_pen_down_must_be_below_pen_up():
    with pytest.raises(ValueError):
        GcodeGenerator(GcodeOptions(pen_up_z=0, pen_down_z=1))


def test_orientation_mapping_page_to_work():
    p = MachineProfile()
    # default: +X right, +Y away -> page top-left (u=0, v=0) is work (0, 297)
    assert p.page_to_work(0, 0) == (0, 297)
    assert p.origin_corner_name() == "front-left"
    p.x_positive, p.y_positive = "left", "toward"
    assert p.page_to_work(0, 0) == (210, 0)
    assert p.origin_corner_name() == "back-right"
    for u, v in [(12.5, 40), (200, 280)]:
        assert p.work_to_page(*p.page_to_work(u, v)) == pytest.approx((u, v))


def test_generated_text_passes_validator_for_every_orientation():
    for xp in ("right", "left"):
        for yp in ("away", "toward"):
            p = MachineProfile(x_positive=xp, y_positive=yp)
            plan = plan_text("Hello world, testing orientation.", p)
            assert plan.ok, plan.pages[0].validation.summary()


# ---- validator ------------------------------------------------------------------------

def issues(lines):
    return [i.message for i in validate(lines, BOUNDS).issues]


def test_valid_program_passes():
    g = gen([[(20, 20), (30, 30)]])
    r = validate(g, BOUNDS)
    assert r.ok, r.summary()
    kinds = [m.kind for m in r.moves]
    assert "draw" in kinds and "travel" in kinds and "z" in kinds
    assert r.draw_extent == (20, 20, 30, 30)


def test_xy_before_pen_height_is_refused():
    assert any("before the pen height" in m for m in issues(["G90", "G0 X20 Y20"]))


def test_rapid_with_pen_down_is_refused():
    msgs = issues(["G90", "G1 Z0 F300", "G0 X20 Y20", "G1 Z-4", "G0 X30 Y30", "G1 Z0"])
    assert any("rapid G0 move with the pen down" in m for m in msgs)


def test_out_of_bounds_reports_line_and_is_not_clamped():
    lines = ["G90", "G1 Z0 F300", "G0 X20 Y20", "G1 Z-4", "G1 X205 Y20 F1000", "G1 Z0"]
    r = validate(lines, BOUNDS)
    assert not r.ok
    assert r.issues[0].line_no == 5
    assert "outside the safe area" in r.issues[0].message
    assert r.issues[0].text == "G1 X205 Y20 F1000"


def test_travel_outside_safe_area_is_refused_too():
    assert any("outside the safe area" in m for m in issues(["G1 Z0 F300", "G0 X5 Y20"]))


def test_z_below_pen_down_is_refused():
    assert any("below the pen-down height" in m for m in issues(["G1 Z-5 F100"]))


def test_combined_xy_and_z_plunge_is_refused():
    msgs = issues(["G1 Z0 F300", "G0 X20 Y20", "G1 X30 Y30 Z-4 F300", "G1 Z0"])
    assert any("combined XY and Z" in m for m in msgs)


def test_program_ending_pen_down_is_refused():
    msgs = issues(["G1 Z0 F300", "G0 X20 Y20", "G1 Z-4"])
    assert any("ends with the pen down" in m for m in msgs)


@pytest.mark.parametrize("line,fragment", [
    ("G2 X30 Y30 I5 J0", "arcs"), ("G20", "inch"), ("G28", "stored position"),
    ("G92 X0", "coordinate system"), ("G10 L20 P1 X0", "coordinate system"),
    ("G55", "G54"), ("$H", "system"), ("M7", "M7"), ("G53 G0 X0", "machine-coordinate"),
    ("G1 X1 Y1 Q5", "unsupported word"), ("hello", "cannot parse"),
])
def test_forbidden_commands(line, fragment):
    msgs = issues(["G1 Z0 F300", line])
    assert any(fragment in m for m in msgs), msgs


def test_relative_moves_are_tracked():
    ok = ["G90", "G1 Z0 F300", "G0 X20 Y20", "G91", "G1 Z-4 F300", "G1 X10 F500", "G1 Z4", "G90"]
    assert validate(ok, BOUNDS).ok
    bad = ["G90", "G1 Z0 F300", "G0 X190 Y20", "G91", "G1 Z-4 F300", "G1 X20 F500", "G1 Z4"]
    assert any("outside the safe area" in m for m in issues(bad))


def test_relative_move_from_unknown_position_is_refused():
    assert any("unknown position" in m for m in issues(["G91", "G1 Z-1 F100"]))


def test_comments_and_line_numbers_are_ignored():
    lines = ["(header)", "; note", "N10 G90 G21", "N20 G1 Z0 F300 (up)", "G0 X20 Y20 ; go"]
    assert validate(lines, BOUNDS).ok


def test_machine_envelope_check():
    # machine = work + WCO and must stay within [-travel, 0]
    b = Bounds(10, 200, 10, 287, 0.0, -4.0, wco=(-150.0, -290.0, -1.0), max_travel=(280, 280, 40))
    r = validate(["G1 Z0 F300", "G0 X20 Y20", "G0 X200 Y20"], b)   # X200 -> machine +50
    assert len(r.issues) == 1
    assert r.issues[0].line_no == 3 and "outside the machine travel" in r.issues[0].message


def test_machine_envelope_allows_reachable_job():
    # X20..200 -> -250..-70, Y20..287 -> -270..-3: all reachable with 280 mm travel
    b = Bounds(10, 200, 10, 287, 0.0, -4.0, wco=(-270.0, -290.0, -1.0), max_travel=(280, 280, 40))
    assert validate(["G1 Z0 F300", "G0 X20 Y20", "G0 X200 Y287"], b).ok


def test_checker_line_by_line_matches_batch():
    g = gen([[(20, 20), (30, 30)], [(40, 40), (41, 45)]])
    c = GcodeChecker(BOUNDS)
    for i, l in enumerate(g, 1):
        assert c.check_line(l, i) == []
    assert c.finish() == []


def test_validate_for_profile_uses_margin(profile):
    lines = ["G1 Z0 F300", "G0 X9.9 Y50"]
    assert not validate_for_profile(lines, profile).ok
    profile.safety_margin = 5
    assert validate_for_profile(lines, profile).ok


def test_plan_gcode_file_reports_issues(profile):
    plan = plan_gcode(["G21 G90\n", "G0 X50 Y50\n"], profile)
    assert not plan.ok
    assert plan.pages[0].validation.issues[0].line_no == 2


# ---- estimate --------------------------------------------------------------------------

def test_estimate_straight_line_matches_physics():
    r = validate(["G1 Z0 F6000", "G0 X10 Y10", "G1 X110 Y10 F600"], Bounds(0, 500, 0, 500, 0, -4))
    lim = MotionLimits((6000, 6000, 6000), (1e6, 1e6, 1e6), 0.01)
    moves = [m for m in r.moves if m.kind != "z" and m.start[0] is not None]
    # 100 mm at 600 mm/min with (practically) infinite acceleration = 10 s
    assert estimate_seconds(moves, lim) == pytest.approx(10, rel=1e-3)


def test_estimate_accounts_for_acceleration():
    r = validate(["G1 Z0 F6000", "G0 X10 Y10", "G1 X110 Y10 F6000"], Bounds(0, 500, 0, 500, 0, -4))
    moves = [m for m in r.moves if m.start[0] is not None]
    fast = estimate_seconds(moves, MotionLimits((6000,) * 3, (1e6,) * 3, 0.01))
    slow = estimate_seconds(moves, MotionLimits((6000,) * 3, (10,) * 3, 0.01))
    assert slow > fast * 2


def test_estimate_includes_dwell_and_format():
    r = validate(["G4 P2.5"], BOUNDS)
    assert estimate_seconds(r.moves, MotionLimits()) == pytest.approx(2.5)
    assert format_duration(65) == "1:05" and format_duration(3725) == "1:02:05"


def test_calibration_pattern_is_valid_and_100mm(profile):
    from plotter.calibration import calibration_gcode, calibration_strokes
    r = validate_for_profile(calibration_gcode(profile), profile)
    assert r.ok, r.summary()
    (a, b) = calibration_strokes(profile)[0]
    (c, d) = calibration_strokes(profile)[3]
    assert b[0] - a[0] == pytest.approx(100) and d[1] - c[1] == pytest.approx(100)


def test_centered_work_offset_fits_travel(profile):
    from plotter.pipeline import centered_work_offset, machine_bounds
    wco = centered_work_offset(profile, {130: 280, 131: 280})
    # X: safe 10..200 -> WCO in [-290, -200]; Y: safe 10..287 -> WCO in [-290, -287]
    assert wco == (-245.0, -288.5)
    settings = {130: 280, 131: 280, 132: 40}
    plan = plan_text("Hello, reachable world", profile, settings, (wco[0], wco[1], 0.0))
    assert plan.ok, plan.pages[0].validation.summary()
    assert machine_bounds(profile, settings, (wco[0], wco[1], 0.0)).wco is not None


def test_centered_work_offset_unknown_or_too_small(profile):
    from plotter.pipeline import centered_work_offset
    assert centered_work_offset(profile, None) is None
    assert centered_work_offset(profile, {130: 280, 131: 200}) is None     # 277 mm won't fit


def test_origin_next_to_home_is_out_of_reach(profile):
    """The reported bug: origin set at the home corner -> every move beyond the switches."""
    settings = {130: 280, 131: 280, 132: 40}
    plan = plan_text("Hello", profile, settings, (-10.0, -10.0, 0.0))
    assert not plan.ok
    assert all("machine travel" in i.message for i in plan.pages[0].validation.issues)


def test_travel_uses_smaller_of_firmware_and_measured(profile):
    """Board reports $130=377/$131=305 but the owner measured 280 x 280: trust 280."""
    from plotter.pipeline import centered_work_offset, effective_travel, machine_bounds
    settings = {130: 377.0, 131: 305.0, 132: 100.0}
    assert effective_travel(profile, settings) == (280.0, 280.0)
    assert centered_work_offset(profile, settings) == (-245.0, -288.5)
    assert machine_bounds(profile, settings, (-245.0, -288.5, 0.0)).max_travel == (280.0, 280.0, 100.0)
    profile.usable_travel_x = 400
    assert effective_travel(profile, settings)[0] == 377.0


def test_home_corner_and_corner_coords_for_this_machine():
    """Owner's machine: +X moves left, +Y away, $23=0, home at the back-left."""
    from plotter.config import MachineProfile
    p = MachineProfile(x_positive="left", y_positive="away")
    assert p.home_corner_name(0) == "back-left"
    assert p.home_corner_name(3) == "front-right"
    assert p.corner_work_coords(p.origin_corner_name(), inset=False) == (0, 0)
    assert p.corner_work_coords("back-left", inset=True) == (200, 287)
    assert p.corner_work_coords("back-left", inset=False) == (210, 297)


def test_origin_at_home_corner_now_fits(profile):
    """Reproduces the hardware log: pen at home (MPos 0,0), $130=377/$131=305, usable 280.
    Setting the origin as 'back-left corner, 10 mm inside' must give a plottable page."""
    profile.x_positive, profile.y_positive = "left", "away"
    wx, wy = profile.corner_work_coords("back-left", inset=True)
    wco = (0.0 - wx, 0.0 - wy, 0.0)                     # G10 L20: current MPos becomes (wx, wy)
    settings = {130: 377.0, 131: 305.0, 132: 100.0}
    plan = plan_text("welcome to Turkey", profile, settings, wco)
    assert plan.ok, plan.pages[0].validation.summary()


def test_page_fit_problems(profile):
    from plotter.pipeline import page_fit_problems
    settings = {130: 377.0, 131: 305.0}
    profile.x_positive = "left"
    assert page_fit_problems(profile, settings, (-200.0, -287.0)) == []
    bad = page_fit_problems(profile, settings, (-10.0, -10.0))     # the logged mistake
    assert any("X: the page would reach 190.0 mm past the home end" in m for m in bad)
    assert any(m.startswith("Y:") for m in bad)
    assert page_fit_problems(profile, None, (-10.0, -10.0)) == []


def test_owner_confirmed_orientation_reads_correctly():
    """Hardware-confirmed on paper: +X moves the pen LEFT, +Y moves it TOWARD the user, so home
    ($23=0, max end) is the front-left corner. ('right'+'toward' plotted mirrored left-right.)"""
    from plotter.config import MachineProfile
    p = MachineProfile(x_positive="left", y_positive="toward")
    assert p.home_corner_name(0) == "front-left"
    assert p.corner_work_coords("front-left", inset=True) == (200, 287)
    x0, y0 = p.page_to_work(15, 15)             # top-left of the text area
    x1, _ = p.page_to_work(195, 15)             # top-right
    assert x1 < x0                              # reading left->right = -X (pen moves right)
    assert y0 == 15                             # first line far from home (the back of the sheet)

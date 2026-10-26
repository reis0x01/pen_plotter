import pytest

from plotter.grbl.protocol import (Alarm, Banner, Error, Ok, Setting, Status, Unknown, parse_line,
                                   settings_warnings, validate_setting)


# ---- status ---------------------------------------------------------------------------

def test_status_09_with_mpos_and_wpos():
    st = parse_line("<Idle,MPos:-10.000,-20.500,-1.000,WPos:5.000,6.000,0.000>")
    assert isinstance(st, Status)
    assert st.state == "Idle"
    assert st.mpos == (-10.0, -20.5, -1.0)
    assert st.wpos == (5.0, 6.0, 0.0)
    st.resolve(None)
    assert st.wco == pytest.approx((-15.0, -26.5, -1.0))


def test_status_09_with_buffer_and_limits():
    st = parse_line("<Run,MPos:0.000,0.000,0.000,WPos:0.000,0.000,0.000,Buf:3,RX:12,Lim:101>")
    assert st.state == "Run" and st.buffer == (3, 12) and st.pins == "XZ"


def test_status_11_with_wco():
    st = parse_line("<Hold:0|MPos:-1.000,-2.000,-3.000|FS:500,0|WCO:-0.500,-1.000,-1.000>")
    assert st.state == "Hold" and st.substate == "0"
    assert st.feed == 500
    st.resolve(None)
    assert st.wpos == pytest.approx((-0.5, -1.0, -2.0))


def test_status_11_without_wco_uses_last_known():
    st = parse_line("<Run|MPos:-10.000,-10.000,0.000|FS:0,0|Bf:15,128|Pn:XZ>")
    st.resolve((-5.0, -5.0, 0.0))
    assert st.wpos == pytest.approx((-5.0, -5.0, 0.0))
    assert st.buffer == (15, 128) and st.pins == "XZ"


def test_status_11_wpos_only():
    st = parse_line("<Idle|WPos:1.000,2.000,3.000|FS:0,0>")
    st.resolve((10.0, 10.0, 10.0))
    assert st.mpos == pytest.approx((11.0, 12.0, 13.0))


# ---- errors ---------------------------------------------------------------------------------

def test_error_11_numeric_with_explanation():
    e = parse_line("error:9")
    assert isinstance(e, Error) and e.code == 9
    assert "alarm" in e.explanation.lower()


def test_error_09_text_with_explanation():
    e = parse_line("error: Alarm lock")
    assert isinstance(e, Error) and e.code is None
    assert "Home" in e.explanation


def test_error_09_invalid_gcode_id():
    e = parse_line("error: Invalid gcode ID:24")
    assert isinstance(e, Error) and "not recognized" in e.explanation


# ---- alarms -----------------------------------------------------------------------------------

@pytest.mark.parametrize("line,code", [("ALARM:1", 1), ("ALARM: Hard limit", 1),
                                        ("ALARM:2", 2), ("ALARM: Soft limit", 2),
                                        ("ALARM:3", 3), ("ALARM: Abort during cycle", 3),
                                        ("ALARM:9", 9), ("ALARM: Homing fail", 9)])
def test_alarms_both_formats(line, code):
    a = parse_line(line)
    assert isinstance(a, Alarm) and a.code == code
    assert len(a.explanation) > 20


def test_unknown_alarm():
    a = parse_line("ALARM: Something new")
    assert isinstance(a, Alarm) and a.code is None


# ---- banners, versions, settings, messages ---------------------------------------------------------

def test_banners():
    b = parse_line("Grbl 0.9j ['$' for help]")
    assert isinstance(b, Banner) and b.version == "0.9j" and b.major_minor == (0, 9)
    b = parse_line("Grbl 1.1h ['$' for help]")
    assert b.version == "1.1h" and b.major_minor == (1, 1)


def test_version_info_both_formats():
    assert parse_line("[VER:1.1h.20190825:]").version == "1.1h"
    assert parse_line("[0.9j.20160726:]").version == "0.9j"


def test_settings_09_with_description_and_11_plain():
    s = parse_line("$100=250.000 (x, step/mm)")
    assert isinstance(s, Setting) and s.number == 100 and s.value == 250.0
    s = parse_line("$23=3")
    assert s.number == 23 and s.value == 3


def test_messages_both_formats():
    assert parse_line("[MSG:'$H'|'$X' to unlock]").text == "'$H'|'$X' to unlock"
    assert parse_line("['$H'|'$X' to unlock]").text == "'$H'|'$X' to unlock"
    assert isinstance(parse_line("ok"), Ok)
    assert isinstance(parse_line("garbage"), Unknown)


# ---- setting validation -------------------------------------------------------------------------

@pytest.mark.parametrize("num,value,ok", [
    (100, "80", True), (100, "0", False), (100, "abc", False), (22, "1", True), (22, "2", False),
    (3, "5", True), (3, "8", False), (0, "2", False), (13, "1", False), (130, "280", True),
    (110, "-5", False),
])
def test_validate_setting(num, value, ok):
    assert validate_setting(num, value)[0] is ok


def test_settings_warnings_for_this_machine():
    w = settings_warnings({130: 200, 131: 280, 132: 3, 27: 1, 22: 1})
    assert any("$130" in x for x in w)
    assert not any("$131" in x for x in w)
    assert any("$132" in x for x in w)
    assert settings_warnings({130: 280, 131: 280, 132: 40, 22: 1}) == []

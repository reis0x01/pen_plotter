"""Calibration pattern: a 100 mm line along X and one along Y, with end ticks.

Measure each line with a ruler. If a line measures L mm, the corrected steps/mm is
``new = current * 100 / L`` ($100 for X, $101 for Y).
"""
from __future__ import annotations

from .config import MachineProfile
from .gcode.generator import GcodeGenerator, GcodeOptions

LENGTH = 100.0
TICK = 4.0


def calibration_strokes(profile: MachineProfile) -> list[list[tuple[float, float]]]:
    """Strokes in work coordinates, starting 20 mm inside the safe-area corner."""
    x0 = profile.safe_min_x + 20
    y0 = profile.safe_min_y + 20
    strokes = [
        [(x0, y0), (x0 + LENGTH, y0)],                       # X line
        [(x0, y0 - TICK / 2), (x0, y0 + TICK / 2)],           # ticks at both ends
        [(x0 + LENGTH, y0 - TICK / 2), (x0 + LENGTH, y0 + TICK / 2)],
        [(x0, y0 + 10), (x0, y0 + 10 + LENGTH)],             # Y line
        [(x0 - TICK / 2, y0 + 10), (x0 + TICK / 2, y0 + 10)],
        [(x0 - TICK / 2, y0 + 10 + LENGTH), (x0 + TICK / 2, y0 + 10 + LENGTH)],
    ]
    return strokes


def calibration_gcode(profile: MachineProfile) -> list[str]:
    gen = GcodeGenerator(GcodeOptions.from_profile(profile))
    return gen.generate(calibration_strokes(profile),
                        "calibration: 100 mm X and Y lines; new steps/mm = old * 100 / measured")

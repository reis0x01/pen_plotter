"""Plot-time estimate from the machine's real rates and accelerations.

Mirrors GRBL's planner closely enough for a useful ETA: per-axis max rate ($110-$112)
and acceleration ($120-$122) limit each block, junction speeds come from the junction
deviation ($11), and a forward/backward pass yields trapezoidal velocity profiles.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .validator import Move


@dataclass
class MotionLimits:
    max_rate: tuple[float, float, float] = (1000.0, 1000.0, 500.0)      # mm/min
    accel: tuple[float, float, float] = (50.0, 50.0, 50.0)              # mm/s^2
    junction_deviation: float = 0.01                                    # mm

    @classmethod
    def from_settings(cls, settings: dict[int, float], fallback: "MotionLimits | None" = None):
        fb = fallback or cls()
        def get(n, default):
            v = settings.get(n)
            return float(v) if v is not None and float(v) > 0 else default
        return cls(
            (get(110, fb.max_rate[0]), get(111, fb.max_rate[1]), get(112, fb.max_rate[2])),
            (get(120, fb.accel[0]), get(121, fb.accel[1]), get(122, fb.accel[2])),
            get(11, fb.junction_deviation),
        )


@dataclass
class _Block:
    length: float
    unit: tuple[float, float, float]
    v_max: float      # mm/s
    accel: float      # mm/s^2
    v_entry_max: float = 0.0
    v_entry: float = 0.0


def _limit_by_axes(unit, per_axis):
    """Largest scalar value along `unit` such that no axis exceeds its limit."""
    limit = math.inf
    for u, lim in zip(unit, per_axis):
        if abs(u) > 1e-12:
            limit = min(limit, lim / abs(u))
    return limit


def _block_time(length, v0, v1, v_max, a):
    """Duration of a trapezoidal (or triangular) profile."""
    if length <= 0:
        return 0.0
    d_acc = max(0.0, (v_max ** 2 - v0 ** 2) / (2 * a))
    d_dec = max(0.0, (v_max ** 2 - v1 ** 2) / (2 * a))
    if d_acc + d_dec <= length:
        cruise = length - d_acc - d_dec
        return (v_max - v0) / a + (v_max - v1) / a + cruise / v_max
    # triangular: peak speed reached mid-block
    v_peak = math.sqrt(max(0.0, (2 * a * length + v0 ** 2 + v1 ** 2) / 2))
    return max(0.0, (v_peak - v0) / a) + max(0.0, (v_peak - v1) / a)


def estimate_seconds(moves: list[Move], limits: MotionLimits) -> float:
    blocks: list[_Block] = []
    dwell_total = 0.0
    max_rate_s = [r / 60.0 for r in limits.max_rate]
    for m in moves:
        if m.kind == "dwell":
            dwell_total += m.duration
            continue
        if any(v is None for v in m.start + m.end):
            continue  # first move from an unknown position; negligible
        d = [e - s for s, e in zip(m.start, m.end)]
        length = math.sqrt(sum(c * c for c in d))
        if length < 1e-9:
            continue
        unit = tuple(c / length for c in d)
        axis_rate = _limit_by_axes(unit, max_rate_s)
        v = axis_rate if m.rapid or m.feed is None else min(m.feed / 60.0, axis_rate)
        a = _limit_by_axes(unit, limits.accel)
        blocks.append(_Block(length, unit, v, a))

    if not blocks:
        return dwell_total

    # Junction speed limits (GRBL junction deviation model).
    prev = None
    for b in blocks:
        if prev is None:
            b.v_entry_max = 0.0
        else:
            cos_theta = -sum(p * q for p, q in zip(prev.unit, b.unit))
            if cos_theta > 0.999999:        # full reversal
                b.v_entry_max = 0.0
            elif cos_theta < -0.999999:     # straight through
                b.v_entry_max = min(prev.v_max, b.v_max)
            else:
                sin_half = math.sqrt(0.5 * (1.0 - cos_theta))
                vj = math.sqrt(b.accel * limits.junction_deviation * sin_half / (1.0 - sin_half))
                b.v_entry_max = min(vj, prev.v_max, b.v_max)
        prev = b

    # Backward pass: must be able to stop at the end.
    n = len(blocks)
    next_entry = 0.0
    for i in range(n - 1, -1, -1):
        b = blocks[i]
        b.v_entry = min(b.v_entry_max, math.sqrt(next_entry ** 2 + 2 * b.accel * b.length))
        next_entry = b.v_entry
    # Forward pass: acceleration limit from the previous block.
    for i in range(1, n):
        p, b = blocks[i - 1], blocks[i]
        b.v_entry = min(b.v_entry, math.sqrt(p.v_entry ** 2 + 2 * p.accel * p.length))

    total = dwell_total
    for i, b in enumerate(blocks):
        v_exit = blocks[i + 1].v_entry if i + 1 < n else 0.0
        total += _block_time(b.length, b.v_entry, v_exit, b.v_max, b.accel)
    return total


def format_duration(seconds: float) -> str:
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

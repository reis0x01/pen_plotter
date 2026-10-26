"""Page-frame strokes -> G-code with strict pen discipline.

Emitted program shape::

    G21 G90 G94          metric, absolute, feed/min
    G1 Z<up> F<z>        pen up before anything else
    for each stroke:
        G0 X Y           travel with the pen up   (G1 F<travel> if travel_feed > 0)
        G1 Z<down> F<z>  pen down, Z only
        G1 X Y F<draw>   drawing segments
        G1 Z<up> F<z>    pen up, Z only
    G0 X<park> Y<park>   park with the pen up
    M2

Only straight G0/G1 moves are produced; GRBL never sees an arc.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..config import MachineProfile

Point = tuple[float, float]


def fmt(value: float) -> str:
    s = f"{value:.3f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


@dataclass
class GcodeOptions:
    pen_up_z: float = 0.0
    pen_down_z: float = -4.0
    z_feed: float = 300.0
    draw_feed: float = 1000.0
    travel_feed: float = 2000.0   # <= 0: use G0
    park: Point | None = (10.0, 10.0)
    min_segment: float = 0.01     # drop drawing segments shorter than this (mm)

    @classmethod
    def from_profile(cls, profile: MachineProfile) -> "GcodeOptions":
        return cls(profile.pen_up_z, profile.pen_down_z, profile.z_feed, profile.draw_feed,
                   profile.travel_feed, (profile.park_x, profile.park_y))


class GcodeGenerator:
    def __init__(self, options: GcodeOptions):
        if options.pen_down_z >= options.pen_up_z:
            raise ValueError("pen_down_z must be below pen_up_z")
        self.o = options

    def _travel(self, x: float, y: float) -> str:
        if self.o.travel_feed > 0:
            return f"G1 X{fmt(x)} Y{fmt(y)} F{fmt(self.o.travel_feed)}"
        return f"G0 X{fmt(x)} Y{fmt(y)}"

    def _pen_up(self) -> str:
        return f"G1 Z{fmt(self.o.pen_up_z)} F{fmt(self.o.z_feed)}"

    def _pen_down(self) -> str:
        return f"G1 Z{fmt(self.o.pen_down_z)} F{fmt(self.o.z_feed)}"

    def generate(self, strokes: list[list[Point]], title: str = "voice plotter job") -> list[str]:
        """strokes are already in *work* coordinates (x, y), mm."""
        out = [f"; {title}", "G21 G90 G94", self._pen_up()]
        for stroke in strokes:
            pts = self._clean(stroke)
            if not pts:
                continue
            x0, y0 = pts[0]
            out.append(self._travel(x0, y0))
            out.append(self._pen_down())
            first = True
            for x, y in pts[1:]:
                feed = f" F{fmt(self.o.draw_feed)}" if first else ""
                out.append(f"G1 X{fmt(x)} Y{fmt(y)}{feed}")
                first = False
            out.append(self._pen_up())
        if self.o.park is not None:
            out.append(self._travel(*self.o.park))
        out.append("M2")
        return out

    def _clean(self, stroke: list[Point]) -> list[Point]:
        """Remove consecutive duplicate/near-duplicate points. A single point is kept
        (it plots a dot: travel, pen down, pen up)."""
        pts: list[Point] = []
        for p in stroke:
            if pts and abs(p[0] - pts[-1][0]) < self.o.min_segment and abs(p[1] - pts[-1][1]) < self.o.min_segment:
                continue
            pts.append((round(p[0], 3), round(p[1], 3)))
        return pts


def page_strokes_to_work(strokes: list[list[Point]], profile: MachineProfile) -> list[list[Point]]:
    return [[profile.page_to_work(u, v) for u, v in s] for s in strokes]


def generate_page(strokes_page_frame: list[list[Point]], profile: MachineProfile,
                  title: str = "voice plotter job") -> list[str]:
    """Convenience: page-frame strokes -> final G-code lines for this profile."""
    work = page_strokes_to_work(strokes_page_frame, profile)
    return GcodeGenerator(GcodeOptions.from_profile(profile)).generate(work, title)

"""Text or G-code file -> validated, time-estimated page jobs. The GUI calls only this."""
from __future__ import annotations

from dataclasses import dataclass, field

from .config import MachineProfile
from .gcode.estimate import MotionLimits, estimate_seconds
from .gcode.generator import generate_page
from .gcode.validator import Bounds, ValidationResult, validate
from .text.layout import LayoutParams, LayoutResult, TextLayout


@dataclass
class PageJob:
    index: int                      # 0-based page number
    gcode: list[str]
    validation: ValidationResult
    seconds: float

    @property
    def ok(self) -> bool:
        return self.validation.ok


@dataclass
class Plan:
    pages: list[PageJob]
    warnings: list[str] = field(default_factory=list)
    layout: LayoutResult | None = None

    @property
    def ok(self) -> bool:
        return all(p.ok for p in self.pages)


def machine_bounds(profile: MachineProfile, settings: dict[int, float] | None,
                   wco: tuple[float, float, float] | None) -> Bounds:
    """Safe-area bounds plus the machine envelope when the work offset and $130/$131 are known."""
    travel = None
    if settings and all(n in settings for n in (130, 131, 132)):
        travel = effective_travel(profile, settings) + (settings[132],)
    return Bounds.from_profile(profile, wco if travel else None, travel if wco else None)


def effective_travel(profile: MachineProfile, settings: dict[int, float]) -> tuple[float, float]:
    """X/Y travel to trust: the smaller of GRBL's $130/$131 and the measured usable travel."""
    return (min(settings[130], profile.usable_travel_x), min(settings[131], profile.usable_travel_y))


def plan_text(text: str, profile: MachineProfile, settings: dict[int, float] | None = None,
              wco: tuple[float, float, float] | None = None) -> Plan:
    layout = TextLayout(LayoutParams.from_profile(profile)).layout(text)
    limits = MotionLimits.from_settings(settings or {})
    bounds = machine_bounds(profile, settings, wco)
    pages = []
    for i, page in enumerate(layout.pages):
        title = f"voice plotter job, page {i + 1}/{layout.page_count}"
        gcode = generate_page(page.strokes, profile, title)
        result = validate(gcode, bounds)
        pages.append(PageJob(i, gcode, result, estimate_seconds(result.moves, limits)))
    return Plan(pages, list(layout.warnings), layout)


def plan_gcode(lines: list[str], profile: MachineProfile, settings: dict[int, float] | None = None,
               wco: tuple[float, float, float] | None = None) -> Plan:
    lines = [l.rstrip("\r\n") for l in lines]
    result = validate(lines, machine_bounds(profile, settings, wco))
    seconds = estimate_seconds(result.moves, MotionLimits.from_settings(settings or {}))
    return Plan([PageJob(0, lines, result, seconds)])


def centered_work_offset(profile: MachineProfile,
                         settings: dict[int, float] | None) -> tuple[float, float] | None:
    """Work offset (G54 X, Y in machine coordinates) that puts the page's safe area in the
    middle of the machine travel. GRBL's machine space after homing is [-$13x, 0], so the
    safe area [m, W-m] fits when -T <= WCO + m and WCO + (W - m) <= 0, where T is the
    effective travel (smaller of $13x and the profile's usable travel).
    Returns None if $130/$131 are unknown or the safe area is larger than the travel."""
    if not settings or 130 not in settings or 131 not in settings:
        return None
    tx, ty = effective_travel(profile, settings)
    out = []
    for travel, lo_safe, hi_safe in ((tx, profile.safe_min_x, profile.safe_max_x),
                                     (ty, profile.safe_min_y, profile.safe_max_y)):
        lo = -travel - lo_safe
        hi = -hi_safe
        if lo > hi:
            return None
        out.append(round((lo + hi) / 2, 3))
    return out[0], out[1]


def page_fit_problems(profile: MachineProfile, settings: dict[int, float] | None,
                      wco: tuple[float, float]) -> list[str]:
    """Why the safe area would not fit inside the machine travel with this work offset
    (empty list if it fits, or if the travel is unknown)."""
    if not settings or 130 not in settings or 131 not in settings:
        return []
    tx, ty = effective_travel(profile, settings)
    out = []
    for axis, travel, off, lo, hi in (("X", tx, wco[0], profile.safe_min_x, profile.safe_max_x),
                                      ("Y", ty, wco[1], profile.safe_min_y, profile.safe_max_y)):
        low, high = off + lo, off + hi
        if high > 1e-6:
            out.append(f"{axis}: the page would reach {high:.1f} mm past the home end of the axis")
        if low < -travel - 1e-6:
            out.append(f"{axis}: the page would reach {-travel - low:.1f} mm past the far end of "
                       f"the {travel:g} mm travel")
    return out


def drawing_area(profile: MachineProfile) -> tuple[float, float, float, float]:
    """Page-frame rectangle (left, top, right, bottom) in mm that drawings and pictures are
    placed in: the user's chosen drawing area, or the text margins when none is set."""
    if profile.drawing_area:
        return tuple(profile.drawing_area)
    return (profile.margin_left, profile.margin_top,
            profile.paper_width - profile.margin_right, profile.paper_height - profile.margin_bottom)


AREA_PRESETS = {
    "margins": "Inside the text margins (default)",
    "safe": "Whole printable area",
    "top": "Top half", "bottom": "Bottom half", "left": "Left half", "right": "Right half",
    "tl": "Top-left quarter", "tr": "Top-right quarter",
    "bl": "Bottom-left quarter", "br": "Bottom-right quarter",
    "center": "Centre, half size",
}
MIN_AREA_MM = 5.0


def preset_drawing_area(profile: MachineProfile, key: str) -> list[float] | None:
    """Drawing area for a preset name ('top', 'tl', ...); None for 'margins'. Halves and
    quarters split the printable area (paper minus safety margin) as seen from the front."""
    if key == "margins":
        return None
    l, t, r, b = profile.safe_min_x, profile.safe_min_y, profile.safe_max_x, profile.safe_max_y
    cx, cy, qw, qh = (l + r) / 2, (t + b) / 2, (r - l) / 4, (b - t) / 4
    areas = {"safe": (l, t, r, b), "top": (l, t, r, cy), "bottom": (l, cy, r, b),
             "left": (l, t, cx, b), "right": (cx, t, r, b), "tl": (l, t, cx, cy), "tr": (cx, t, r, cy),
             "bl": (l, cy, cx, b), "br": (cx, cy, r, b), "center": (cx - qw, cy - qh, cx + qw, cy + qh)}
    if key not in areas:
        raise ValueError(f"unknown drawing area preset {key!r}")
    return [round(v, 3) for v in areas[key]]


def clamp_drawing_area(profile: MachineProfile, area) -> list[float]:
    """Keep a user-drawn area inside the printable area and at least MIN_AREA_MM on each side."""
    lo_x, lo_y, hi_x, hi_y = profile.safe_min_x, profile.safe_min_y, profile.safe_max_x, profile.safe_max_y
    l, t, r, b = area
    l, r = sorted((l, r))
    t, b = sorted((t, b))
    l, t = max(lo_x, min(l, hi_x - MIN_AREA_MM)), max(lo_y, min(t, hi_y - MIN_AREA_MM))
    r, b = min(hi_x, max(r, l + MIN_AREA_MM)), min(hi_y, max(b, t + MIN_AREA_MM))
    return [round(v, 3) for v in (l, t, r, b)]


def plan_drawing(drawing, profile: MachineProfile, settings: dict[int, float] | None = None,
                 wco: tuple[float, float, float] | None = None, mode: str = "fit") -> Plan:
    """A PDF drawing (PdfDrawing) or traced picture (ImageDrawing) -> one validated page job.
    The drawing is placed inside the drawing area (centred in it; without a chosen area,
    inside the text margins and centred on the paper). PDF strokes are joined where they touch
    and reordered to reduce pen-up travel; pictures arrive already ordered."""
    from .drawing.ordering import join_touching
    from .drawing.pdf_import import fit_to_page, optimize_order
    area = drawing_area(profile)
    if profile.drawing_area:
        center = ((area[0] + area[2]) / 2, (area[1] + area[3]) / 2)
    else:
        center = (profile.paper_width / 2, profile.paper_height / 2)
    strokes, scale = fit_to_page(drawing, area, center, mode)
    if not drawing.ordered:
        strokes = optimize_order(join_touching(strokes, 0.05))
    gcode = generate_page(strokes, profile, drawing.title)
    result = validate(gcode, machine_bounds(profile, settings, wco))
    seconds = estimate_seconds(result.moves, MotionLimits.from_settings(settings or {}))
    x0, y0, x1, y1 = drawing.bbox()
    fitted = "fitted to the drawing area" if profile.drawing_area else "fitted to the page"
    size = f"Drawing size on paper: {(x1 - x0) * scale:.0f} × {(y1 - y0) * scale:.0f} mm " \
           f"({fitted if mode == 'fit' else 'actual size'})."
    return Plan([PageJob(0, gcode, result, seconds)], [size] + list(drawing.warnings))

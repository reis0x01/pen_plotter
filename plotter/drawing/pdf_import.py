"""Vector drawings from PDF files -> pen strokes on the page.

Every vector path on the chosen PDF page becomes one or more polylines: straight lines as
they are, Bezier curves flattened into short segments, rectangles/quads closed. Filled
shapes are drawn as their outline (a pen can't fill). White fill-only shapes (usually the
page background) are skipped. Raster images and live text can't be plotted and are
reported as warnings.

Coordinates stay in PDF points (1/72 inch, origin top-left, y down) until ``fit_to_page``
scales them into the page frame used by layout and the preview (mm, u right, v down).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

Point = tuple[float, float]
Polyline = list[Point]

PT_TO_MM = 25.4 / 72.0
CURVE_STEP_PT = 1.0          # flatten Beziers into ~1 pt (0.35 mm) chords before scaling


class DrawingError(ValueError):
    pass


@dataclass
class PdfDrawing:
    strokes: list[Polyline]                 # PDF points, y down
    page_width: float                       # PDF points
    page_height: float
    page_index: int
    page_count: int
    warnings: list[str] = field(default_factory=list)
    ordered = False                         # plan_drawing optimises the order

    @property
    def title(self) -> str:
        return f"PDF drawing, page {self.page_index + 1}"

    def bbox(self) -> tuple[float, float, float, float]:
        pts = [p for s in self.strokes for p in s]
        return (min(p[0] for p in pts), min(p[1] for p in pts),
                max(p[0] for p in pts), max(p[1] for p in pts))


def _cubic(p0, p1, p2, p3) -> list[Point]:
    ctrl_len = math.dist(p0, p1) + math.dist(p1, p2) + math.dist(p2, p3)
    n = max(2, min(200, math.ceil(ctrl_len / CURVE_STEP_PT)))
    out = []
    for i in range(1, n + 1):
        t = i / n
        a, b, c, d = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t ** 2, t ** 3
        out.append((a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0],
                    a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1]))
    return out


def _is_white(color) -> bool:
    return color is not None and all(c > 0.97 for c in color)


def _path_polylines(drawing: dict) -> list[Polyline]:
    """Split one PyMuPDF drawing dict into connected polylines."""
    lines: list[Polyline] = []
    cur: Polyline = []

    def start(p):
        nonlocal cur
        p = (p.x, p.y)
        if cur and math.dist(cur[-1], p) < 1e-6:
            return
        if len(cur) > 1:
            lines.append(cur)
        cur = [p]

    for item in drawing["items"]:
        kind = item[0]
        if kind == "l":
            start(item[1])
            cur.append((item[2].x, item[2].y))
        elif kind == "c":
            start(item[1])
            cur.extend(_cubic((item[1].x, item[1].y), (item[2].x, item[2].y),
                              (item[3].x, item[3].y), (item[4].x, item[4].y)))
        elif kind == "re":
            r = item[1]
            if len(cur) > 1:
                lines.append(cur)
            cur = []
            lines.append([(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1), (r.x0, r.y0)])
        elif kind == "qu":
            q = item[1]
            if len(cur) > 1:
                lines.append(cur)
            cur = []
            pts = [(q.ul.x, q.ul.y), (q.ur.x, q.ur.y), (q.lr.x, q.lr.y), (q.ll.x, q.ll.y)]
            lines.append(pts + [pts[0]])
    if len(cur) > 1:
        if drawing.get("closePath") and math.dist(cur[0], cur[-1]) > 1e-6:
            cur.append(cur[0])
        lines.append(cur)
    return lines


def pdf_page_count(path: str) -> int:
    try:
        import pymupdf
    except ImportError as exc:
        raise DrawingError("PyMuPDF is not installed (pip install -r requirements.txt).") from exc
    try:
        with pymupdf.open(path) as doc:
            return doc.page_count
    except Exception as exc:
        raise DrawingError(f"Cannot open PDF: {exc}") from exc


def load_pdf(path: str, page_index: int = 0) -> PdfDrawing:
    try:
        import pymupdf
    except ImportError as exc:
        raise DrawingError("PyMuPDF is not installed (pip install -r requirements.txt).") from exc
    try:
        doc = pymupdf.open(path)
    except Exception as exc:
        raise DrawingError(f"Cannot open PDF: {exc}") from exc
    with doc:
        if doc.page_count == 0:
            raise DrawingError("The PDF has no pages.")
        if not 0 <= page_index < doc.page_count:
            raise DrawingError(f"Page {page_index + 1} does not exist (the PDF has {doc.page_count}).")
        page = doc[page_index]
        strokes: list[Polyline] = []
        skipped_white = 0
        for d in page.get_drawings():
            stroked = d.get("color") is not None and d["type"] in ("s", "fs")
            if not stroked and _is_white(d.get("fill")):
                skipped_white += 1          # background / white-out shapes: nothing to draw
                continue
            strokes.extend(_path_polylines(d))
        warnings = []
        if page.get_images():
            warnings.append(f"{len(page.get_images())} image(s) on this page were ignored: a pen "
                            "plotter can only draw vector lines, not pictures.")
        if page.get_text().strip():
            warnings.append("Text on this page was ignored. To plot it, convert the text to "
                            "outlines/paths in your drawing program before exporting the PDF.")
        result = PdfDrawing(strokes, page.rect.width, page.rect.height, page_index,
                            doc.page_count, warnings)
    if not strokes:
        raise DrawingError("No vector drawing found on this page. "
                           + (" ".join(warnings) if warnings else "The page is empty."))
    return result


# ---------------------------------------------------------------------------
# Placement and ordering
# ---------------------------------------------------------------------------

def fit_to_page(drawing: PdfDrawing, area: tuple[float, float, float, float],
                paper_center: tuple[float, float], mode: str = "fit") -> tuple[list[Polyline], float]:
    """Scale drawing strokes into the page frame (mm) and centre them on the paper.

    area: (left, top, right, bottom) in mm that the drawing must stay inside.
    mode: 'fit' scales the drawing (keeping proportions) to fill the area; 'actual' keeps
    the PDF's real size. Returns (strokes, scale in mm per PDF point).
    """
    x0, y0, x1, y1 = drawing.bbox()
    w, h = max(x1 - x0, 1e-9), max(y1 - y0, 1e-9)
    left, top, right, bottom = area
    aw, ah = right - left, bottom - top
    if mode == "fit":
        scale = min(aw / w, ah / h)
    elif mode == "actual":
        scale = PT_TO_MM
    else:
        raise ValueError(f"unknown mode {mode!r}")
    sw, sh = w * scale, h * scale
    # centre on the paper, then keep inside the area (never clamp individual points)
    u0 = min(max(paper_center[0] - sw / 2, left), right - sw) if sw <= aw else paper_center[0] - sw / 2
    v0 = min(max(paper_center[1] - sh / 2, top), bottom - sh) if sh <= ah else paper_center[1] - sh / 2
    out = [[(u0 + (x - x0) * scale, v0 + (y - y0) * scale) for x, y in s] for s in drawing.strokes]
    return out, scale


def optimize_order(strokes: list[Polyline]) -> list[Polyline]:
    """Drawing order with short pen-up travel: nearest neighbour, then 2-opt (strokes may be
    reversed). See ordering.py."""
    if len(strokes) < 3:
        return list(strokes)
    from .ordering import order_strokes
    return order_strokes(strokes)


def travel_length(strokes: list[Polyline]) -> float:
    return sum(math.dist(a[-1], b[0]) for a, b in zip(strokes, strokes[1:]))

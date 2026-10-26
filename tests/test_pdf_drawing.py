import math
import re

import pytest

pymupdf = pytest.importorskip("pymupdf")

from plotter.config import MachineProfile  # noqa: E402
from plotter.drawing.pdf_import import (DrawingError, fit_to_page, load_pdf,  # noqa: E402
                                        optimize_order, travel_length)
from plotter.pipeline import plan_drawing  # noqa: E402


def make_pdf(path, text=False, image=False, background=True, pages=1):
    doc = pymupdf.open()
    for n in range(pages):
        pg = doc.new_page(width=200, height=100)
        if background:
            pg.draw_rect(pymupdf.Rect(0, 0, 200, 100), color=None, fill=(1, 1, 1), overlay=False)
        if n == 0:
            pg.draw_line((10, 10), (190, 10))
            pg.draw_rect(pymupdf.Rect(20, 20, 80, 60))
            pg.draw_circle((140, 50), 30)
        if text:
            pg.insert_text((20, 90), "Hi")
        if image:
            pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 4, 4), False)
            pg.insert_image(pymupdf.Rect(150, 80, 160, 90), pixmap=pix)
    doc.save(str(path))
    return str(path)


def test_load_pdf_extracts_lines_rects_and_curves(tmp_path):
    d = load_pdf(make_pdf(tmp_path / "a.pdf"))
    assert d.page_count == 1 and d.warnings == []
    assert len(d.strokes) == 3                               # line, rectangle, circle
    line, rect, circle = d.strokes
    assert line == [(10, 10), (190, 10)]
    assert rect[0] == rect[-1] and len(rect) == 5            # closed rectangle
    assert circle[0] == pytest.approx(circle[-1])            # closed circle
    for x, y in circle:                                      # flattened points lie on the circle
        assert math.hypot(x - 140, y - 50) == pytest.approx(30, abs=0.3)
    assert d.bbox() == pytest.approx((10, 10, 190, 80))      # white background skipped


def test_text_and_images_are_reported(tmp_path):
    d = load_pdf(make_pdf(tmp_path / "b.pdf", text=True, image=True))
    assert any("Text" in w for w in d.warnings)
    assert any("image" in w for w in d.warnings)


def test_empty_or_raster_only_pdf_is_a_clear_error(tmp_path):
    doc = pymupdf.open()
    pg = doc.new_page()
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 4, 4), False)
    pg.insert_image(pymupdf.Rect(10, 10, 100, 100), pixmap=pix)
    doc.save(str(tmp_path / "scan.pdf"))
    with pytest.raises(DrawingError, match="No vector drawing"):
        load_pdf(str(tmp_path / "scan.pdf"))
    with pytest.raises(DrawingError, match="Cannot open"):
        load_pdf(str(tmp_path / "missing.pdf"))


def test_page_selection(tmp_path):
    path = make_pdf(tmp_path / "c.pdf", pages=2)
    assert load_pdf(path, 0).page_count == 2
    with pytest.raises(DrawingError, match="No vector drawing"):
        load_pdf(path, 1)                                    # second page is blank
    with pytest.raises(DrawingError, match="does not exist"):
        load_pdf(path, 5)


def test_fit_keeps_aspect_and_centres_on_paper(tmp_path):
    d = load_pdf(make_pdf(tmp_path / "d.pdf"))
    strokes, scale = fit_to_page(d, (15, 15, 200, 282), (105, 148.5), "fit")
    pts = [p for s in strokes for p in s]
    u0, v0 = min(p[0] for p in pts), min(p[1] for p in pts)
    u1, v1 = max(p[0] for p in pts), max(p[1] for p in pts)
    assert (u1 - u0) / (v1 - v0) == pytest.approx(180 / 70, rel=1e-6)   # aspect kept
    assert u0 >= 15 - 1e-9 and u1 <= 200 + 1e-9                        # inside the area
    assert (v0 + v1) / 2 == pytest.approx(148.5)                        # centred vertically
    assert (u0 + u1) / 2 == pytest.approx(107.5)                        # centred, shifted into area
    actual, s2 = fit_to_page(d, (15, 15, 200, 282), (105, 148.5), "actual")
    assert s2 == pytest.approx(25.4 / 72)


def test_optimize_order_reduces_travel():
    strokes = [[(i * 10.0, 0.0), (i * 10.0, 5.0)] for i in (5, 1, 9, 3, 7, 0, 8, 2, 6, 4)]
    better = optimize_order(strokes)
    assert travel_length(better) < travel_length(strokes)
    assert sorted(map(tuple, map(sorted, better))) == sorted(map(tuple, map(sorted, strokes)))


def test_plan_drawing_is_valid_gcode_on_owner_machine(tmp_path):
    p = MachineProfile(x_positive="left", y_positive="toward", pen_up_z=3, pen_down_z=-1)
    d = load_pdf(make_pdf(tmp_path / "e.pdf", text=True))
    plan = plan_drawing(d, p, {130: 377.0, 131: 305.0, 132: 100.0}, (-200.0, -287.0, 0.0))
    assert plan.ok, plan.pages[0].validation.summary()
    assert "Drawing size on paper" in plan.warnings[0] and any("Text" in w for w in plan.warnings)
    g = plan.pages[0].gcode
    assert g[2] == "G1 Z3 F300" and g[-1] == "M2"
    assert not any(re.search(r"\bG0?[23]\b", l) for l in g)


def test_actual_size_too_big_is_refused_not_clamped(tmp_path):
    doc = pymupdf.open()
    pg = doc.new_page(width=1200, height=1200)
    pg.draw_line((0, 0), (1190, 1190))                         # ~420 mm long at actual size
    doc.save(str(tmp_path / "big.pdf"))
    plan = plan_drawing(load_pdf(str(tmp_path / "big.pdf")), MachineProfile(), mode="actual")
    assert not plan.ok
    assert any("outside the safe area" in i.message for i in plan.pages[0].validation.issues)
    assert plan_drawing(load_pdf(str(tmp_path / "big.pdf")), MachineProfile(), mode="fit").ok

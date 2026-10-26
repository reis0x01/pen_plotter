import math
import random

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from plotter.config import MachineProfile  # noqa: E402
from plotter.drawing.image_import import (ImageProcessor, ImageSettings, hatch, load_image,  # noqa: E402
                                          nearest_palette_index, smooth_polyline)
from plotter.drawing.ordering import is_closed, join_touching, order_strokes, travel_length  # noqa: E402
from plotter.drawing.pdf_import import DrawingError  # noqa: E402
from plotter.pipeline import (clamp_drawing_area, drawing_area, plan_drawing,  # noqa: E402
                              preset_drawing_area)

AREA = (180.0, 180.0)        # mm, square: 1 px = 0.2 mm for a 900 px picture


def white(h=600, w=600):
    return np.full((h, w, 3), 255, np.uint8)


def process(img, **kw):
    return ImageProcessor(img).process(ImageSettings(**kw), AREA)


def points(strokes):
    return np.array([p for s in strokes for p in s])


# ---------------------------------------------------------------- loading

def test_load_image_transparent_png_with_turkish_name(tmp_path):
    rgba = np.zeros((40, 60, 4), np.uint8)              # fully transparent ...
    rgba[10:20, 10:50] = (0, 0, 0, 255)                 # ... except an opaque black bar
    path = tmp_path / "çizim_ğüşı.png"
    ok, buf = cv2.imencode(".png", rgba)
    path.write_bytes(buf.tobytes())
    img = load_image(str(path))
    assert img.shape == (40, 60, 3)
    assert (img[0, 0] == 255).all() and (img[15, 30] == 0).all()


def test_load_image_errors(tmp_path):
    (tmp_path / "notes.png").write_text("not a picture")
    with pytest.raises(DrawingError, match="Cannot read"):
        load_image(str(tmp_path / "notes.png"))
    with pytest.raises(DrawingError, match="Cannot open"):
        load_image(str(tmp_path / "missing.png"))


# ---------------------------------------------------------------- tracing

def test_centerline_draws_a_thick_line_once_along_its_middle():
    img = white()
    cv2.line(img, (60, 300), (540, 300), (0, 0, 0), 15)
    res = process(img, line_style="centerline")
    assert len(res.drawing.strokes) == 1
    pts = points(res.drawing.strokes) / 1.5             # 600 px picture worked on at 900 px
    assert abs(pts[:, 1].mean() - 300) < 1.5                                 # on the centre line
    assert res.stats["draw_mm"] == pytest.approx(480 * 1.5 * res.mm_per_px, rel=0.05)


def test_centerline_keeps_crossing_lines_long():
    img = white()
    cv2.line(img, (100, 300), (500, 300), (0, 0, 0), 9)
    cv2.line(img, (300, 100), (300, 500), (0, 0, 0), 9)
    res = process(img, line_style="centerline")
    assert len(res.drawing.strokes) <= 3                 # not four stubs meeting at the centre
    longest = max(res.drawing.strokes, key=lambda s: math.dist(s[0], s[-1]))
    assert math.dist(longest[0], longest[-1]) > 500      # one line runs straight through (px of 900)


def test_outline_gives_smooth_closed_loops():
    img = white()
    cv2.circle(img, (300, 300), 200, (0, 0, 0), -1)
    res = process(img, line_style="outline")
    assert len(res.drawing.strokes) == 1
    loop = res.drawing.strokes[0]
    assert is_closed(loop)
    r = np.hypot(*(np.array(loop) - 450).T)               # centre and radius at 900 px
    assert r.mean() == pytest.approx(300, rel=0.02) and r.std() < 1.5
    assert len(loop) < 200                                # simplified, not one point per pixel


def test_removed_colour_is_not_drawn():
    img = white()
    cv2.rectangle(img, (50, 50), (250, 250), (30, 30, 220), -1)     # red square
    cv2.circle(img, (430, 400), 100, (0, 0, 0), -1)                 # black disc
    proc = ImageProcessor(img)
    s = ImageSettings(line_style="outline")
    first = proc.process(s, AREA)
    assert len(first.drawing.strokes) == 2
    red = nearest_palette_index(first.palette, (220, 30, 30))
    s.removed_colors = [first.palette[red].rgb]
    res = proc.process(s, AREA)
    assert res.palette[red].removed
    assert len(res.drawing.strokes) == 1
    assert points(res.drawing.strokes)[:, 0].min() > 400           # only the disc (right side) is left


def test_crop_keeps_only_the_chosen_part():
    img = white()
    cv2.circle(img, (150, 300), 80, (0, 0, 0), -1)
    cv2.circle(img, (450, 300), 80, (0, 0, 0), -1)
    res = process(img, line_style="outline", crop=(0.5, 0.0, 1.0, 1.0))
    assert len(res.drawing.strokes) == 1
    assert res.drawing.width / res.drawing.height == pytest.approx(0.5, rel=0.01)


def test_edges_work_on_a_dull_low_contrast_photo():
    img = np.full((400, 600, 3), 120, np.uint8)
    cv2.circle(img, (300, 200), 100, (105, 105, 105), -1)          # barely darker disc
    img = cv2.add(img, np.random.default_rng(1).integers(0, 6, img.shape, dtype=np.uint8))
    res = process(img, line_style="edges", denoise=5)
    pts = points(res.drawing.strokes) / (900 / 600)
    r = np.hypot(pts[:, 0] - 300, pts[:, 1] - 200)
    assert len(pts) > 20 and np.median(np.abs(r - 100)) < 4         # the disc's rim is found


def test_hatching_is_denser_for_darker_tones_and_stays_inside():
    img = white(400, 800)
    img[100:300, 100:300] = 170                         # light grey square
    img[100:300, 500:700] = 20                          # near-black square
    res = process(img, line_style="none", shading=True, hatch_levels=3, auto_levels=False)
    sizes = res.drawing.layer_sizes
    assert sizes[0] == 0 and len(sizes) == 4
    k = 900 / 800
    pts = points(res.drawing.strokes) / k
    inside = ((100 - 3 <= pts[:, 1]) & (pts[:, 1] <= 300 + 3)
              & (((100 - 3 <= pts[:, 0]) & (pts[:, 0] <= 300 + 3)) | ((500 - 3 <= pts[:, 0]) & (pts[:, 0] <= 700 + 3))))
    assert inside.all()
    ink = [0.0, 0.0]
    for s in res.drawing.strokes:
        a = np.array(s) / k
        ink[int(a[:, 0].mean() > 400)] += np.hypot(*np.diff(a, axis=0).T).sum()
    assert ink[1] > 2.5 * ink[0]                        # dark square: three hatch directions


def test_hatch_rows_are_linked_into_zigzags():
    mask = np.zeros((200, 200), bool)
    mask[20:180, 20:180] = True
    lines = hatch(mask, 0.0, 5.0)
    assert len(lines) == 1                              # one continuous zig-zag fills the square
    assert len(lines[0]) >= 2 * 30


def test_lines_are_drawn_before_shading_and_ordered():
    img = white()
    for i in range(6):
        cv2.circle(img, (100 + 80 * i, 150 + 50 * (i % 3)), 30, (0, 0, 0), 3)
    cv2.rectangle(img, (80, 400), (520, 550), (60, 60, 60), -1)
    res = process(img, line_style="outline", shading=True)
    sizes = res.drawing.layer_sizes
    assert sizes[0] >= 6 and sum(sizes) == len(res.drawing.strokes)
    first = res.drawing.strokes[:sizes[0]]
    assert all(is_closed(s) for s in first)              # the outlines come first
    shuffled = list(res.drawing.strokes)
    random.Random(0).shuffle(shuffled)
    assert travel_length(res.drawing.strokes) < 0.5 * travel_length(shuffled)


def test_nothing_to_draw_is_a_warning_not_a_crash():
    res = process(white(), line_style="centerline")
    assert res.drawing.strokes == [] and "Nothing to draw" in res.drawing.warnings[0]
    with pytest.raises(DrawingError, match="nothing would be drawn"):
        process(white(), line_style="none", shading=False)


def test_smoothing_keeps_open_ends_and_closed_loops_closed():
    t = np.linspace(0, 2 * np.pi, 80)
    loop = np.c_[np.cos(t), np.sin(t)] * 50
    loop[-1] = loop[0]
    sm = smooth_polyline(loop, 3, closed=True)
    assert np.allclose(sm[0], sm[-1])
    zig = np.array([(i, i % 2) for i in range(30)], float)
    sm = smooth_polyline(zig, 2, closed=False)
    assert np.allclose(sm[0], zig[0]) and np.allclose(sm[-1], zig[-1])
    assert np.abs(sm[3:-3, 1] - 0.5).max() < 0.1          # the stair-steps are gone


# ---------------------------------------------------------------- joining and ordering

def test_join_touching_prefers_straight_continuation():
    c = (50.0, 50.0)
    arms = [[(0.0, 50.0), c], [c, (100.0, 50.0)], [(50.0, 0.0), c], [c, (50.0, 100.0)]]
    joined = join_touching(arms, 0.5)
    assert len(joined) == 2
    for s in joined:
        assert math.dist(s[0], s[-1]) == pytest.approx(100)   # two straight lines through the centre


def test_order_strokes_beats_nearest_neighbour_and_rotates_loops():
    rng = random.Random(3)
    strokes = [[(rng.uniform(0, 200), rng.uniform(0, 200)), (rng.uniform(0, 200), rng.uniform(0, 200))]
               for _ in range(150)]
    from plotter.drawing.ordering import _nearest_neighbour
    nn = _nearest_neighbour(strokes, (0, 0))
    best = order_strokes(strokes, (0, 0))
    assert travel_length(best, (0, 0)) < travel_length(nn, (0, 0))
    assert sorted(map(sorted, best)) == sorted(map(sorted, strokes))
    square = [(10.0, 10.0), (20.0, 10.0), (20.0, 20.0), (10.0, 20.0), (10.0, 10.0)]
    (out,) = order_strokes([square], start=(25.0, 25.0))
    assert out[0] == (20.0, 20.0) and out[-1] == out[0]        # starts at the corner nearest the pen


# ---------------------------------------------------------------- placement on the paper

def test_drawing_area_presets_and_clamping():
    p = MachineProfile()
    assert preset_drawing_area(p, "margins") is None
    assert drawing_area(p) == (15, 15, 195, 282)                # text margins by default
    assert preset_drawing_area(p, "safe") == [10, 10, 200, 287]
    assert preset_drawing_area(p, "top") == [10, 10, 200, 148.5]
    assert preset_drawing_area(p, "br") == [105, 148.5, 200, 287]
    assert clamp_drawing_area(p, [250, 300, -5, 100]) == [10, 100, 200, 287]
    assert clamp_drawing_area(p, [50, 50, 51, 51]) == [50, 50, 55, 55]
    p.drawing_area = [0, 0, 100, 100]
    assert any("Drawing area" in m for m in p.problems())
    p.drawing_area = preset_drawing_area(p, "tl")
    assert p.problems() == []


def test_picture_is_placed_inside_the_chosen_area():
    p = MachineProfile(x_positive="left", y_positive="toward", pen_up_z=3, pen_down_z=-1)
    p.drawing_area = preset_drawing_area(p, "tr")               # top-right quarter
    img = white(400, 600)
    cv2.rectangle(img, (0, 0), (599, 399), (0, 0, 0), 8)
    cv2.circle(img, (300, 200), 120, (0, 0, 0), 6)
    l, t, r, b = p.drawing_area
    res = ImageProcessor(img, "frame.png").process(ImageSettings(), (r - l, b - t))
    plan = plan_drawing(res.drawing, p, {130: 377.0, 131: 305.0, 132: 100.0}, (-200.0, -287.0, 0.0))
    assert plan.ok, plan.pages[0].validation.summary()
    assert "fitted to the drawing area" in plan.warnings[0]
    draws = [m for m in plan.pages[0].validation.moves if m.kind == "draw"]
    uv = np.array([p.work_to_page(*m.end[:2]) for m in draws])
    assert (uv[:, 0] >= l - 1e-6).all() and (uv[:, 0] <= r + 1e-6).all()
    assert (uv[:, 1] >= t - 1e-6).all() and (uv[:, 1] <= b + 1e-6).all()
    assert uv[:, 0].max() - uv[:, 0].min() == pytest.approx(r - l, abs=1.5)   # fills the width
    assert plan.pages[0].gcode[0] == "; picture frame.png"


def test_thin_pale_lines_stay_whole_and_grid_paper_is_ignored():
    """A technical drawing: thin anti-aliased blue lines on light grid paper."""
    img = white(400, 600)
    for x in range(0, 600, 20):                                     # light blue grid
        cv2.line(img, (x, 0), (x, 399), (245, 225, 205), 1)
    for y in range(0, 400, 20):
        cv2.line(img, (0, y), (599, y), (245, 225, 205), 1)
    cv2.line(img, (50, 100), (550, 130), (220, 130, 40), 1, cv2.LINE_AA)   # thin blue dimension line
    cv2.circle(img, (300, 270), 90, (40, 20, 10), 5, cv2.LINE_AA)        # thick dark outline
    res = process(img, line_style="centerline")
    long_lines = [s for s in res.drawing.strokes if math.dist(s[0], s[-1]) > 600]
    assert len(long_lines) == 1                       # the dimension line in one piece, not dashes
    pts = points(res.drawing.strokes) / 1.5
    assert ((np.abs(pts[:, 1] - (100 + (pts[:, 0] - 50) * 30 / 500)) < 3)
            | (np.abs(np.hypot(pts[:, 0] - 300, pts[:, 1] - 270) - 90) < 4)).all()   # no grid lines


def test_removed_colour_crossing_a_line_leaves_no_gap():
    img = white()
    cv2.circle(img, (300, 300), 200, (0, 0, 0), 6)
    cv2.line(img, (0, 300), (599, 300), (220, 120, 30), 9)          # blue line across the circle
    proc = ImageProcessor(img)
    s = ImageSettings(line_style="centerline")
    first = proc.process(s, AREA)
    s.removed_colors = [first.palette[nearest_palette_index(first.palette, (30, 120, 220))].rgb]
    res = proc.process(s, AREA)
    assert len(res.drawing.strokes) == 1 and is_closed(res.drawing.strokes[0])   # one unbroken circle


def test_long_lines_come_out_straight_but_small_text_keeps_its_shape():
    img = white()
    cv2.line(img, (40, 100), (560, 160), (0, 0, 0), 4, cv2.LINE_AA)
    cv2.putText(img, "5", (280, 400), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 0), 2, cv2.LINE_AA)
    res = process(img, line_style="centerline")
    line = max(res.drawing.strokes, key=lambda s: math.dist(s[0], s[-1]))
    assert len(line) <= 3                                           # a straight line, not a wobble
    digit = [s for s in res.drawing.strokes if s is not line]
    assert sum(len(s) for s in digit) >= 6                          # the '5' is not flattened away


def test_removing_a_background_colour_does_not_merge_letters():
    img = np.full((300, 600, 3), (60, 200, 250), np.uint8)          # yellow background
    for i in range(5):
        cv2.line(img, (100 + 60 * i, 80), (100 + 60 * i, 220), (0, 0, 0), 6)   # five separate strokes
    proc = ImageProcessor(img)
    s = ImageSettings(line_style="centerline")
    first = proc.process(s, AREA)
    s.removed_colors = [first.palette[nearest_palette_index(first.palette, (250, 200, 60))].rgb]
    res = proc.process(s, AREA)
    assert len(res.drawing.strokes) == 5

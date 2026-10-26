"""Raster pictures (photos, scans, sketches, logos) -> pen strokes, using OpenCV.

Every step is a setting in ``ImageSettings`` that the picture dialog shows to the user:

1. Crop and resize to a working resolution (small pictures are enlarged so lines come out smooth).
2. Colour palette: k-means in Lab colour space. Colours the user removes are painted white,
   so the pen never draws them (backgrounds, coloured fills, watermarks, ...).
3. Edge-preserving smoothing (bilateral filter): removes noise, paper grain and JPEG blocks
   but keeps edges sharp.
4. Tone: auto levels (stretch the darkest..lightest tones towards black..white, at most 4x), local contrast
   (CLAHE, for unevenly lit photos and scans), contrast, brightness, invert.
5. Vectorise:
   * lines - ``centerline``: dark lines are thinned to 1 px and traced, so each line of a
     sketch is drawn once along its middle; ``outline``: outlines of dark shapes;
     ``edges``: Canny edges with thresholds taken from the picture's own gradient
     strength, so dull and high-contrast photos both work; ``none``.
   * shading - hatching of dark areas: one hatch direction per tone level, darker tones get
     more crossing directions. Neighbouring hatch rows are linked into zig-zags, so the pen
     stays down across a whole area instead of lifting for every row.
6. Clean-up of every line: Gaussian smoothing along the line (removes pixel stair-steps),
   Douglas-Peucker simplification (fewer, longer moves: smoother motion on GRBL), and
   specks shorter than a minimum length are dropped.
7. Drawing order: outlines first, then shading from light to dark. Inside each layer,
   touching lines are joined and the sequence is optimised (``ordering.py``), so the pen
   works through the picture area by area instead of jumping around.

Coordinates are pixels of the working image (x right, y down), the same orientation as the
page frame, so ``pipeline.plan_drawing`` only has to scale and shift them.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .ordering import is_closed, join_touching, order_strokes
from .pdf_import import DrawingError

Point = tuple[float, float]
Polyline = list[Point]

LINE_STYLES = {
    "centerline": "Centre lines (sketches, line art, handwriting)",
    "outline": "Outlines of dark shapes (logos, silhouettes)",
    "edges": "Edges (photos)",
    "none": "No lines (shading only)",
}
HATCH_ANGLES = (45.0, 135.0, 0.0, 90.0)   # level 1 (lightest) .. 4 (darkest)
IMAGE_FILTER = "Pictures (*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp);;All files (*)"


def _cv2():
    try:
        import cv2
    except ImportError as exc:
        raise DrawingError("OpenCV is not installed (pip install -r requirements.txt).") from exc
    return cv2


@dataclass
class ImageSettings:
    crop: tuple[float, float, float, float] | None = None   # (x0, y0, x1, y1), fractions 0..1
    resolution: int = 900              # working size of the longest side, px
    palette_size: int = 10             # colours found in the picture
    removed_colors: list[tuple[int, int, int]] = field(default_factory=list)   # RGB
    denoise: int = 3                   # 0..10 edge-preserving smoothing strength
    auto_levels: bool = True
    local_contrast: bool = False
    contrast: float = 1.0              # 0.3..3
    brightness: float = 0.0            # -100..100
    invert: bool = False
    line_style: str = "centerline"     # key of LINE_STYLES
    threshold: int = -1                # 0..255 ink threshold; -1 = automatic (Otsu)
    edge_detail: int = 50              # 0..100, more = more edges (edges style)
    shading: bool = False
    hatch_levels: int = 3              # 1..4
    hatch_spacing_mm: float = 1.5
    smoothing: float = 4.0             # px, Gaussian sigma along each line (less on short lines)
    simplify: float = 0.8              # px, Douglas-Peucker tolerance (less on short lines)
    min_length_mm: float = 1.0

    def problems(self) -> list[str]:
        p = []
        if self.line_style not in LINE_STYLES:
            p.append(f"unknown line style {self.line_style!r}")
        if self.line_style == "none" and not self.shading:
            p.append("Choose a line style or turn shading on: nothing would be drawn.")
        if self.crop is not None:
            x0, y0, x1, y1 = self.crop
            if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
                p.append("crop must satisfy 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1")
        return p


@dataclass
class PaletteColor:
    rgb: tuple[int, int, int]
    share: float                       # fraction of the picture's pixels
    removed: bool
    shade_of: int | None = None        # removed as a lighter shade of this palette entry


@dataclass
class ImageDrawing:
    """Strokes traced from a picture, already in drawing order. Same interface as PdfDrawing."""
    strokes: list[Polyline]            # working-image px, y down
    width: float                       # working image size, px
    height: float
    name: str = "picture"
    layer_sizes: list[int] = field(default_factory=list)   # strokes per layer, in order
    warnings: list[str] = field(default_factory=list)
    page_index: int = 0
    page_count: int = 1
    ordered = True                     # plan_drawing keeps this order

    @property
    def title(self) -> str:
        return f"picture {self.name}"

    def bbox(self) -> tuple[float, float, float, float]:
        # The whole (cropped) picture frame, so the composition is kept when it is fitted.
        return 0.0, 0.0, self.width, self.height


@dataclass
class ImageResult:
    drawing: ImageDrawing
    work: np.ndarray                   # BGR working image after colour removal and smoothing
    tone: np.ndarray                   # grey image the strokes are traced from
    palette: list[PaletteColor]
    mm_per_px: float
    threshold: int                     # ink threshold actually used

    @property
    def stats(self) -> dict[str, float]:
        s = self.drawing.strokes
        draw = sum(_length(p) for p in s) * self.mm_per_px
        travel = sum(math.dist(a[-1], b[0]) for a, b in zip(s, s[1:])) * self.mm_per_px
        return {"strokes": len(s), "draw_mm": draw, "travel_mm": travel,
                "points": sum(len(p) for p in s)}


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_image(path: str) -> np.ndarray:
    """Read a picture as 8-bit BGR. Transparent areas become white paper."""
    cv2 = _cv2()
    try:
        data = np.fromfile(path, dtype=np.uint8)     # works with non-ASCII (Turkish) paths
    except OSError as exc:
        raise DrawingError(f"Cannot open picture: {exc}") from exc
    img = cv2.imdecode(data, cv2.IMREAD_UNCHANGED) if data.size else None
    if img is None:
        raise DrawingError("Cannot read this file as a picture (PNG, JPEG, BMP, TIFF and WebP "
                           "are supported).")
    return to_bgr(img)


def to_bgr(img: np.ndarray) -> np.ndarray:
    cv2 = _cv2()
    if img.dtype == np.uint16:
        img = (img // 257).astype(np.uint8)
    elif img.dtype != np.uint8:
        img = np.clip(img.astype(np.float64) * (255.0 if img.max() <= 1.0 else 1.0), 0, 255).astype(np.uint8)
    if img.ndim == 2:
        return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    if img.shape[2] == 4:
        alpha = img[:, :, 3:4].astype(np.float32) / 255.0
        return (img[:, :, :3].astype(np.float32) * alpha + 255.0 * (1 - alpha)).round().astype(np.uint8)
    return np.ascontiguousarray(img[:, :, :3])


# ---------------------------------------------------------------------------
# Raster helpers
# ---------------------------------------------------------------------------

def _crop_box(shape, crop) -> tuple[int, int, int, int]:
    h, w = shape[:2]
    if crop is None:
        return 0, 0, w, h
    x0, y0, x1, y1 = crop
    a, b = int(round(x0 * w)), int(round(y0 * h))
    c, d = max(a + 8, int(round(x1 * w))), max(b + 8, int(round(y1 * h)))
    return max(0, min(a, w - 8)), max(0, min(b, h - 8)), min(w, c), min(h, d)


def _prepare(img: np.ndarray, crop, resolution: int) -> np.ndarray:
    cv2 = _cv2()
    x0, y0, x1, y1 = _crop_box(img.shape, crop)
    img = img[y0:y1, x0:x1]
    h, w = img.shape[:2]
    scale = min(resolution / max(h, w), 4.0)
    if abs(scale - 1) < 1e-3:
        return img.copy()
    size = (max(8, round(w * scale)), max(8, round(h * scale)))
    return cv2.resize(img, size, interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC)


def _palette(img: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """k-means colours in Lab, sorted dark to light. Returns (centres BGR uint8 (k,3),
    centres Lab float (k,3), label per pixel (h,w))."""
    cv2 = _cv2()
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB).reshape(-1, 3).astype(np.float32)
    rng = np.random.default_rng(0)
    sample = lab[rng.choice(len(lab), min(len(lab), 20000), replace=False)]
    k = max(1, min(k, len(sample)))
    cv2.setRNGSeed(0)
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.5)
    _, _, centres = cv2.kmeans(sample, k, None, criteria, 3, cv2.KMEANS_PP_CENTERS)
    centres = centres[np.argsort(centres[:, 0])]
    labels = np.empty(len(lab), dtype=np.int32)
    for i in range(0, len(lab), 200000):
        chunk = lab[i:i + 200000]
        labels[i:i + len(chunk)] = ((chunk[:, None, :] - centres[None]) ** 2).sum(-1).argmin(1)
    bgr = cv2.cvtColor(centres.clip(0, 255).astype(np.uint8)[:, None, :], cv2.COLOR_LAB2BGR)[:, 0]
    return bgr, centres, labels.reshape(img.shape[:2])


def _rgb_to_lab(rgb) -> np.ndarray:
    cv2 = _cv2()
    px = np.array([[[rgb[2], rgb[1], rgb[0]]]], dtype=np.uint8)
    return cv2.cvtColor(px, cv2.COLOR_BGR2LAB)[0, 0].astype(np.float32)


def nearest_palette_index(palette: list[PaletteColor], rgb) -> int:
    """The palette entry a picked pixel colour belongs to (what the user means by 'this colour')."""
    lab = np.array([_rgb_to_lab(c.rgb) for c in palette])
    return int(((lab - _rgb_to_lab(rgb)) ** 2).sum(1).argmin())


def ink_mask(gray: np.ndarray, thr: int) -> np.ndarray:
    """Pixels that are ink, with hysteresis: darker than ``thr`` is ink; lighter pixels up to
    halfway to the paper tone are ink too when they touch such dark ink. Thin anti-aliased lines
    (whose edge pixels are pale) then stay whole instead of breaking into dashes, while faint
    marks that never get dark, such as grid paper, stay out."""
    cv2 = _cv2()
    strong = gray < thr
    paper = float(np.median(gray))
    if paper <= thr or not strong.any():
        return strong
    weak = gray < thr + 0.5 * (paper - thr)
    n, labels = cv2.connectedComponents(weak.astype(np.uint8), connectivity=8)
    keep = np.zeros(n, dtype=bool)
    keep[np.unique(labels[strong])] = True
    keep[0] = False
    return keep[labels]


def _shades(centres_lab: np.ndarray, picked: set[int]) -> dict[int, int]:
    """Palette entries that are lighter shades of a picked colour, i.e. blends of it with the
    paper (anti-aliased edges, thin strokes of the same ink): {shade: picked colour}. Removing
    a colour removes these too, so one click removes 'the blue' and not just one shade of it."""
    paper = int(centres_lab[:, 0].argmax())              # the lightest colour is the paper
    out: dict[int, int] = {}
    for j in range(len(centres_lab)):
        if j in picked or j == paper:
            continue
        best = None
        for i in picked:
            a, d = centres_lab[i], centres_lab[paper] - centres_lab[i]
            t = float((centres_lab[j] - a) @ d) / max(float(d @ d), 1e-6)
            off = float(np.linalg.norm(centres_lab[j] - (a + np.clip(t, 0, 1) * d)))
            if -0.05 <= t <= 0.9 and off < 12 and (best is None or off < best[0]):
                best = (off, i)
        if best:
            out[j] = best[1]
    return out


def _blend_fringe(img: np.ndarray, removed: np.ndarray, centres_lab: np.ndarray,
                  removed_idx: set[int]) -> np.ndarray:
    """Pixels next to a removed colour that are a blend of it and the paper (anti-aliased or
    resized edges). They belong to other palette entries, so without this a removed line would
    leave a pale outline of itself behind."""
    cv2 = _cv2()
    ring = cv2.dilate(removed.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    ring &= ~removed
    if not ring.any():
        return np.zeros_like(removed)
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)[ring].astype(np.float32)
    p = int(centres_lab[:, 0].argmax())                  # the lightest colour is the paper
    paper = centres_lab[p]

    def blend_distance(i):
        """Distance of each ring pixel from the blends of colour i with the paper."""
        a, d = centres_lab[i], paper - centres_lab[i]
        t = np.clip(((lab - a) @ d) / max(float(d @ d), 1e-6), 0.0, 0.9)   # not the paper itself
        return np.linalg.norm(lab - (a + t[:, None] * d), axis=1)

    gone = np.min([blend_distance(i) for i in removed_idx], axis=0)
    kept = [i for i in range(len(centres_lab)) if i not in removed_idx and i != p]
    # Only clear blends of a removed colour, never the soft edge of a kept line.
    out = gone < 10
    if kept:
        out &= gone < np.min([blend_distance(i) for i in kept], axis=0)
    fringe = np.zeros_like(removed)
    fringe[ring] = out
    return fringe


def _remove_small(mask: np.ndarray, min_area: float) -> np.ndarray:
    cv2 = _cv2()
    if min_area <= 1 or not mask.any():
        return mask
    n, lab, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    keep = stats[:, cv2.CC_STAT_AREA] >= min_area
    keep[0] = False
    return keep[lab]


# ---- thinning (Zhang-Suen, vectorised with a lookup table) -----------------------

def _bits(code: int) -> list[int]:
    return [(code >> i) & 1 for i in range(8)]      # P2(N) P3(NE) P4(E) P5(SE) P6(S) P7(SW) P8(W) P9(NW)


def _transitions(b: list[int]) -> int:
    return sum(1 for i in range(8) if b[i] == 0 and b[(i + 1) % 8] == 1)


def _make_luts():
    lut1 = np.zeros(256, dtype=bool)
    lut2 = np.zeros(256, dtype=bool)
    cross = np.zeros(256, dtype=np.uint8)
    for c in range(256):
        b = _bits(c)
        p2, p3, p4, p5, p6, p7, p8, p9 = b
        n, a = sum(b), _transitions(b)
        cross[c] = a
        ok = 2 <= n <= 6 and a == 1
        lut1[c] = ok and p2 * p4 * p6 == 0 and p4 * p6 * p8 == 0
        lut2[c] = ok and p2 * p4 * p8 == 0 and p2 * p6 * p8 == 0
    return lut1, lut2, cross


_LUT1, _LUT2, _CROSS = _make_luts()


def _codes(img: np.ndarray) -> np.ndarray:
    """8-neighbour code of every pixel of a padded bool image (border pixels get 0)."""
    p = img.astype(np.uint8)
    c = np.zeros(img.shape, dtype=np.uint8)
    c[1:-1, 1:-1] = (p[:-2, 1:-1] | (p[:-2, 2:] << 1) | (p[1:-1, 2:] << 2) | (p[2:, 2:] << 3)
                     | (p[2:, 1:-1] << 4) | (p[2:, :-2] << 5) | (p[1:-1, :-2] << 6) | (p[:-2, :-2] << 7))
    return c


def thin(mask: np.ndarray) -> np.ndarray:
    """1-pixel-wide skeleton of a bool mask (Zhang-Suen)."""
    img = np.pad(mask.astype(bool), 1)
    while True:
        changed = False
        for lut in (_LUT1, _LUT2):
            rm = img & lut[_codes(img)]
            if rm.any():
                img[rm] = False
                changed = True
        if not changed:
            return img[1:-1, 1:-1]


# 4-neighbours first, so straight runs are followed before diagonals
_STEPS = ((-1, 0), (0, 1), (1, 0), (0, -1), (-1, 1), (1, 1), (1, -1), (-1, -1))   # (dy, dx)


def trace_skeleton(skel: np.ndarray) -> list[Polyline]:
    """Split a 1-px skeleton into polylines that run between end points and junctions.
    Closed rings come out as closed polylines."""
    sk = np.pad(skel.astype(bool), 1)
    H, W = sk.shape
    cross = _CROSS[_codes(sk)]
    flat = sk.ravel()
    node = (flat & (cross.ravel() != 2))            # end points (1), junctions (>=3), dots (0)
    offs = [dy * W + dx for dy, dx in _STEPS]
    visited = np.zeros(flat.size, dtype=bool)
    used: set[tuple[int, int]] = set()
    paths: list[list[int]] = []

    def step_from(prev: int, cur: int) -> int:
        """Next pixel: a neighbouring junction/end point if there is one (so every line ends
        exactly on its junction), else the unvisited neighbour that continues straightest."""
        vy, vx = divmod(cur - prev + W + 1, W)
        vy, vx = vy - 1, vx - 1
        best, best_score = -1, -math.inf
        for (dy, dx), o in zip(_STEPS, offs):
            q = cur + o
            if q == prev or not flat[q] or (visited[q] and not node[q]):
                continue
            if node[q] and (q, cur) in used:
                continue
            score = (vy * dy + vx * dx) / math.hypot(dy, dx) + (10.0 if node[q] else 0.0)
            if score > best_score:
                best, best_score = q, score
        return best

    def absorb_corners(cur: int, nxt: int):
        """A diagonal step skips the corner pixel of a stair-step: mark it visited, or it would
        come back later as a stray copy of this line."""
        ny, nx = divmod(nxt, W)
        for o in offs:
            q = cur + o
            if q != nxt and flat[q] and not node[q] and not visited[q]:
                qy, qx = divmod(q, W)
                if abs(qy - ny) <= 1 and abs(qx - nx) <= 1:
                    visited[q] = True

    def walk(a: int, b: int) -> list[int]:
        path, prev, cur = [a, b], a, b
        while not node[cur]:
            visited[cur] = True
            nxt = step_from(prev, cur)
            if nxt < 0:
                break
            absorb_corners(cur, nxt)
            path.append(nxt)
            prev, cur = cur, nxt
        if node[cur]:
            used.add((cur, prev))
        return path

    for a in np.flatnonzero(node):
        for o in offs:
            b = a + o
            if not flat[b] or (a, b) in used:
                continue
            if node[b]:
                if (b, a) not in used:
                    used.add((a, b))
                    paths.append([a, b])
                continue
            if visited[b]:
                continue
            used.add((a, b))
            paths.append(walk(a, b))

    for s in np.flatnonzero(flat & ~node & ~visited):  # rings without end points or junctions
        if visited[s]:
            continue
        visited[s] = True
        path, prev, cur = [s], s, s
        while True:
            nxt = step_from(prev, cur)
            if nxt < 0 or node[nxt]:
                break
            absorb_corners(cur, nxt)
            visited[nxt] = True
            path.append(nxt)
            prev, cur = cur, nxt
        if len(path) > 2 and abs(path[-1] // W - s // W) <= 1 and abs(path[-1] % W - s % W) <= 1:
            path.append(s)
        paths.append(path)

    return [[(float(i % W - 1), float(i // W - 1)) for i in p] for p in paths]


def merge_junctions(paths: list[Polyline], max_bridge: float) -> list[Polyline]:
    """Where two thick lines cross at an angle, thinning leaves two junctions joined by a
    short 'bridge'. Drop bridges up to ``max_bridge`` px long and move the ends of every line
    at those junctions to the cluster's centre, so the crossing becomes one point that
    ``join_touching`` can carry each line straight through."""
    count: dict[Point, int] = {}
    for p in paths:
        if len(p) >= 2 and p[0] != p[-1]:
            for e in (p[0], p[-1]):
                count[e] = count.get(e, 0) + 1
    parent: dict[Point, Point] = {}

    def find(a):
        while parent.get(a, a) != a:
            a = parent[a]
        return a

    keep = []
    for p in paths:
        a, b = (p[0], p[-1]) if len(p) >= 2 else (None, None)
        if a is not None and a != b and count[a] >= 2 and count[b] >= 2 and _length(p) <= max_bridge:
            parent[find(a)] = find(b)                     # a bridge: merge its two junctions
        else:
            keep.append(p)
    if not parent:
        return paths
    groups: dict[Point, list[Point]] = {}
    for e in count:
        groups.setdefault(find(e), []).append(e)
    centre = {e: (sum(q[0] for q in g) / len(g), sum(q[1] for q in g) / len(g))
              for g in groups.values() if len(g) > 1 for e in g}
    out = []
    for p in keep:
        p = list(p)
        if p[0] in centre:
            p[0] = centre[p[0]]
        if p[-1] in centre:
            p[-1] = centre[p[-1]]
        out.append(p)
    return out


# ---- hatching ------------------------------------------------------------------

def hatch(mask: np.ndarray, angle: float, spacing: float, phase: float = 0.0) -> list[Polyline]:
    """Parallel hatch lines over a bool mask. Rows alternate direction and are linked into
    zig-zags wherever the link stays inside the mask."""
    cv2 = _cv2()
    h, w = mask.shape
    D = int(math.ceil(math.hypot(h, w))) + 4
    M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    M[0, 2] += D / 2 - w / 2
    M[1, 2] += D / 2 - h / 2
    rot = cv2.warpAffine(mask.astype(np.uint8), M, (D, D), flags=cv2.INTER_NEAREST) > 0
    inv = cv2.invertAffineTransform(M)

    def inside(p, q) -> bool:
        n = max(2, int(math.dist(p, q)) + 1)
        xs = np.linspace(p[0], q[0], n).round().astype(int).clip(0, D - 1)
        ys = np.linspace(p[1], q[1], n).round().astype(int).clip(0, D - 1)
        return bool(rot[ys, xs].all())

    finished: list[list[Point]] = []
    open_lines: list[list[Point]] = []
    for r, yf in enumerate(np.arange(phase % spacing, D, spacing)):
        y = int(round(yf))
        if y >= D:
            break
        row = rot[y].astype(np.int8)
        d = np.diff(np.concatenate(([0], row, [0])))
        segs = [(float(s), float(e - 1)) for s, e in zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1))
                if e - 1 - s >= 1]
        if r % 2:
            segs = [(e, s) for s, e in reversed(segs)]
        taken = [False] * len(open_lines)
        nxt_open = []
        for a, b in segs:
            best, best_d = -1, 2.5 * spacing
            for k, line in enumerate(open_lines):
                dist = abs(line[-1][0] - a)
                if not taken[k] and dist <= best_d and inside(line[-1], (a, y)):
                    best, best_d = k, dist
            if best >= 0:
                taken[best] = True
                open_lines[best].extend([(a, y), (b, y)])
                nxt_open.append(open_lines[best])
            else:
                nxt_open.append([(a, y), (b, y)])
        finished.extend(l for l, t in zip(open_lines, taken) if not t)
        open_lines = nxt_open
    finished.extend(open_lines)
    out = []
    for line in finished:
        pts = np.asarray(line, dtype=np.float64)
        back = pts @ inv[:, :2].T + inv[:, 2]
        out.append([(float(x), float(y)) for x, y in back])
    return out


# ---- line clean-up -------------------------------------------------------------

def _length(p) -> float:
    a = np.asarray(p, dtype=np.float64)
    return float(np.hypot(*np.diff(a, axis=0).T).sum()) if len(a) > 1 else 0.0


def smooth_polyline(pts: np.ndarray, sigma: float, closed: bool) -> np.ndarray:
    """Gaussian smoothing along the line. Open lines keep their end points."""
    n = len(pts)
    if sigma <= 0 or n < 4:
        return pts
    if closed:
        body = pts[:-1]
        r = min(int(math.ceil(3 * sigma)), len(body) // 2)
        if r < 1:
            return pts
        padded = np.concatenate([body[-r:], body, body[:r]])
    else:
        r = min(int(math.ceil(3 * sigma)), n - 1)
        padded = np.concatenate([2 * pts[0] - pts[r:0:-1], pts, 2 * pts[-1] - pts[-2:-r - 2:-1]])
    x = np.arange(-r, r + 1)
    k = np.exp(-0.5 * (x / sigma) ** 2)
    k /= k.sum()
    out = np.column_stack([np.convolve(padded[:, i], k, mode="valid") for i in range(2)])
    if closed:
        return np.vstack([out, out[:1]])
    out[0], out[-1] = pts[0], pts[-1]
    return out


def clean_lines(lines: list[Polyline], smoothing: float, simplify: float, min_len_px: float) -> list[Polyline]:
    cv2 = _cv2()
    out = []
    for line in lines:
        pts = np.asarray(line, dtype=np.float64)
        length = _length(pts)
        if len(pts) < 2 or length < min_len_px:
            continue
        closed = is_closed(line, 1e-6)
        # Scaled with the line's length: long lines are straightened fully, while short strokes
        # (small text, details) keep their shape instead of being rounded away.
        pts = smooth_polyline(pts, min(smoothing, max(0.8, length / 15)), closed)
        eps = min(simplify, max(0.3, length / 100))
        if simplify > 0 and len(pts) > 3:
            # A closed loop goes in as a polygon without its repeated end point: given an open
            # curve whose ends coincide, approxPolyDP drops the closing point.
            src = pts[:-1] if closed else pts
            pts = cv2.approxPolyDP(src.astype(np.float32).reshape(-1, 1, 2), eps, closed) \
                .reshape(-1, 2).astype(np.float64)
            if closed:
                pts = np.vstack([pts, pts[:1]])
        out.append([(float(x), float(y)) for x, y in pts])
    return out


# ---------------------------------------------------------------------------
# The whole chain
# ---------------------------------------------------------------------------

class ImageProcessor:
    """Holds one picture and turns it into strokes for given settings. Keeps the expensive
    crop/resize and colour-palette steps cached while only later settings change."""

    def __init__(self, image: np.ndarray, name: str = "picture"):
        self.image = to_bgr(image)
        self.name = name
        self._prep_key = None
        self._prep = None
        self._pal_key = None
        self._pal = None

    def prepared(self, s: ImageSettings) -> np.ndarray:
        key = (s.crop, s.resolution)
        if key != self._prep_key:
            self._prep, self._prep_key, self._pal_key = _prepare(self.image, s.crop, s.resolution), key, None
        return self._prep

    def palette(self, s: ImageSettings):
        img = self.prepared(s)
        key = (self._prep_key, s.palette_size)
        if key != self._pal_key:
            self._pal, self._pal_key = _palette(img, s.palette_size), key
        return self._pal

    def process(self, s: ImageSettings, area_mm: tuple[float, float]) -> ImageResult:
        cv2 = _cv2()
        problems = s.problems()
        if problems:
            raise DrawingError("; ".join(problems))
        img = self.prepared(s).copy()
        h, w = img.shape[:2]
        mm_per_px = min(area_mm[0] / w, area_mm[1] / h)
        px = 1.0 / mm_per_px                                  # px per mm
        min_len_px = s.min_length_mm * px

        # 2. colours
        bgr, lab, labels = self.palette(s)
        picked = {int(((lab - _rgb_to_lab(c)) ** 2).sum(1).argmin()) for c in s.removed_colors}
        shades = _shades(lab, picked)
        removed_idx = picked | set(shades)
        removed = np.isin(labels, list(removed_idx)) if removed_idx else np.zeros((h, w), dtype=bool)
        if removed.any():
            removed |= _blend_fringe(img, removed, lab, removed_idx)
        img[removed] = 255
        shares = np.bincount(labels.ravel(), minlength=len(bgr)) / labels.size
        palette = [PaletteColor((int(c[2]), int(c[1]), int(c[0])), float(shares[i]), i in removed_idx,
                                shades.get(i)) for i, c in enumerate(bgr)]

        # 3. edge-preserving smoothing
        for _ in range((s.denoise + 3) // 4 if s.denoise > 0 else 0):
            img = cv2.bilateralFilter(img, 0, 12 + 7 * s.denoise, 1 + 0.6 * s.denoise)

        # 4. tone
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        if s.auto_levels:
            kept = gray[~removed]
            if kept.size:
                lo, hi = np.percentile(kept, (0.5, 99.5))
                # At most 4x: a nearly flat picture would otherwise have its noise blown up
                # into fake edges and shading.
                gain = min(4.0, 255.0 / max(hi - lo, 1.0))
                g = (gray.astype(np.float32) - (lo + hi) / 2) * gain + 127.5
                gray = np.clip(g, 0, 255).astype(np.uint8)
        if s.local_contrast:
            gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
        g = (gray.astype(np.float32) - 128.0) * s.contrast + 128.0 + s.brightness * 1.28
        gray = np.clip(g, 0, 255).astype(np.uint8)
        if s.invert:
            gray = 255 - gray
        gray[removed] = 255                                   # removed colours are never drawn

        # 5. lines
        thr = s.threshold
        if thr < 0:
            thr = int(cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[0])
        lines: list[Polyline] = []
        join_tol = 2.5                  # px; ends this close are one continuous line
        join_gap = 0.0                  # px; aligned ends this far apart are mended (see below)
        removed_width = 0.0
        warnings = []
        if s.line_style in ("centerline", "outline"):
            ink = ink_mask(gray, thr)
            if removed.any():
                # A removed line (a blue dimension line, say) that crossed a kept line cut a gap
                # in it as wide as the removed line: let join_touching mend gaps that wide.
                narrow = removed & ~(cv2.morphologyEx(removed.astype(np.uint8), cv2.MORPH_OPEN,
                                                      cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (25, 25))) > 0)
                if narrow.any():
                    dist = cv2.distanceTransform(narrow.astype(np.uint8), cv2.DIST_L2, 3)
                    removed_width = 2 * float(np.percentile(dist[narrow], 95))
            ink = _remove_small(ink, (min_len_px / 2) ** 2)
            if s.line_style == "centerline" and ink.mean() > 0.3:
                warnings.append(f"{ink.mean():.0%} of the picture is dark: centre lines are meant for line "
                                "drawings and give a web of lines on photos. Try 'Edges' (with shading) or "
                                "'Outlines', or raise the brightness / lower the threshold.")
            if s.line_style == "centerline":
                skel = thin(ink)
                width = 2.0 * float(np.median(cv2.distanceTransform(ink.astype(np.uint8), cv2.DIST_L2, 3)[skel])) \
                    if skel.any() else 1.0
                lines = merge_junctions(trace_skeleton(skel), max(3.0, 2.0 * width))
                join_tol = max(join_tol, 0.6 * width)     # a junction of thick lines spans ~ their width
                if removed_width:
                    # The gap is the removed line plus half the kept line's width on each side,
                    # where its traced centre line stops short.
                    join_gap = min(30.0, removed_width + width + 4)
            else:
                contours, _ = cv2.findContours(ink.astype(np.uint8), cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
                lines = [[(float(x), float(y)) for x, y in c[:, 0]] + [(float(c[0, 0, 0]), float(c[0, 0, 1]))]
                         for c in contours if len(c) >= 3]
        elif s.line_style == "edges":
            blur = cv2.GaussianBlur(gray, (0, 0), 1.2)
            mag = cv2.magnitude(cv2.Sobel(blur, cv2.CV_32F, 1, 0), cv2.Sobel(blur, cv2.CV_32F, 0, 1))
            # Edge pixels are the strongest few percent of gradients (more detail = a larger
            # share), but always well above the typical gradient, which in a flat or noisy
            # picture is just noise.
            noise = float(np.median(mag))
            hi = max(20.0, (5.0 - 0.03 * s.edge_detail) * noise,
                     float(np.percentile(mag, 97.5 - 0.17 * s.edge_detail)))
            edges = cv2.Canny(blur, 0.4 * hi, hi, L2gradient=True) > 0
            edges = _remove_small(edges, min_len_px * 0.75)
            lines = merge_junctions(trace_skeleton(thin(edges)), 3.0)
        if s.line_style != "outline":
            lines = join_touching(lines, join_tol, join_gap)
        lines = clean_lines(lines, s.smoothing, s.simplify, min_len_px)

        # 5b. shading
        layers = [lines]
        if s.shading:
            spacing = max(1.5, s.hatch_spacing_mm * px)
            n = max(1, min(4, s.hatch_levels))
            for i in range(1, n + 1):
                level = gray < 255 * (n + 1 - i) / (n + 1)
                level = _remove_small(cv2.morphologyEx(level.astype(np.uint8), cv2.MORPH_OPEN,
                                                       np.ones((3, 3), np.uint8)) > 0, spacing ** 2)
                strokes = hatch(level, HATCH_ANGLES[i - 1], spacing, phase=spacing * i / (n + 1))
                layers.append([p for p in strokes if _length(p) >= min_len_px])

        # 7. order: layer by layer, each continuing from where the previous one ended
        ordered: list[Polyline] = []
        sizes = []
        pos: Point = (0.0, 0.0)
        for layer in layers:
            seq = order_strokes(layer, pos, time_budget=1.0 if layer is lines else 0.5)
            ordered.extend(seq)
            sizes.append(len(seq))
            if seq:
                pos = seq[-1][-1]

        if not ordered:
            warnings.append("Nothing to draw with these settings: try another line style, a different "
                            "threshold, or fewer removed colours.")
        elif len(ordered) > 4000:
            warnings.append(f"{len(ordered)} separate lines: the plot will take long. Raise the minimum "
                            "line length or the smoothing to simplify the picture.")
        drawing = ImageDrawing(ordered, float(w), float(h), self.name, sizes, warnings)
        return ImageResult(drawing, img, gray, palette, mm_per_px, thr)

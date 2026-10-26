"""Stroke joining and ordering: draw in a sensible sequence with as little pen-up travel as possible.

Works on any polylines (PDF points, image pixels or page mm): every step only uses distances,
so the result is the same after a uniform scale and shift.

* ``join_touching`` chains strokes whose ends meet into one continuous stroke, preferring the
  continuation that keeps the line's direction (a line through a junction stays one line).
* ``order_strokes`` picks the drawing sequence: greedy nearest neighbour from the pen's start
  position (strokes may be drawn backwards), then 2-opt, which reverses whole runs of strokes
  whenever that shortens the pen-up travel. Closed loops start at the vertex nearest the pen.
"""
from __future__ import annotations

import math
import time

import numpy as np

Point = tuple[float, float]
Polyline = list[Point]


def travel_length(strokes: list[Polyline], start: Point | None = None) -> float:
    """Total pen-up distance between consecutive strokes (plus from ``start`` if given)."""
    total = sum(math.dist(a[-1], b[0]) for a, b in zip(strokes, strokes[1:]))
    if start is not None and strokes:
        total += math.dist(start, strokes[0][0])
    return total


def is_closed(stroke: Polyline, tol: float = 1e-9) -> bool:
    return len(stroke) > 2 and math.dist(stroke[0], stroke[-1]) <= tol


# ---------------------------------------------------------------------------
# Joining
# ---------------------------------------------------------------------------

def _heading(stroke: Polyline, at_end: bool, span: int = 4) -> tuple[float, float] | None:
    """Unit direction of pen motion over the last ``span`` segments (at_end) or the first ones."""
    (x0, y0), (x1, y1) = (stroke[-span - 1], stroke[-1]) if at_end and len(stroke) > span else \
        (stroke[0], stroke[-1]) if at_end else (stroke[0], stroke[min(span, len(stroke) - 1)])
    n = math.hypot(x1 - x0, y1 - y0)
    return None if n < 1e-12 else ((x1 - x0) / n, (y1 - y0) / n)


def _aim(stroke: Polyline, at_end: bool, skip: int = 4, span: int = 12) -> tuple[float, float] | None:
    """Direction of a line near one end, ignoring the last ``skip`` points: traced line ends
    often hook sideways for a pixel or two. For the start, it points into the stroke."""
    pts = stroke if at_end else stroke[::-1]
    if len(pts) < 3:
        return _heading(stroke, at_end)
    b = len(pts) - 1 - min(skip, (len(pts) - 1) // 3)
    a = max(0, b - span)
    (x0, y0), (x1, y1) = pts[a], pts[b]
    n = math.hypot(x1 - x0, y1 - y0)
    if n < 1e-12:
        return None
    return ((x1 - x0) / n, (y1 - y0) / n) if at_end else ((x0 - x1) / n, (y0 - y1) / n)


def _aims_across(a: Polyline, b: Polyline) -> bool:
    """True when line ``a`` (at its end) and line ``b`` (at its start) both point straight
    across the gap between them, like the two halves of a line with a piece cut out."""
    p, q = a[-1], b[0]
    d = math.dist(p, q)
    out, into = _aim(a, at_end=True), _aim(b, at_end=False)
    if d < 1e-12 or not out or not into:
        return False
    across = ((q[0] - p[0]) / d, (q[1] - p[1]) / d)
    return (out[0] * across[0] + out[1] * across[1] > 0.8          # within ~37°: arcs too
            and into[0] * across[0] + into[1] * across[1] > 0.8)


def join_touching(strokes: list[Polyline], tol: float, gap: float = 0.0) -> list[Polyline]:
    """Chain strokes whose end points are within ``tol`` of each other.

    At a junction where several strokes meet, the one that continues straightest is chosen,
    so crossing lines are drawn as two long lines rather than four short ones. Closed loops
    are left alone. Every input point is kept; the joined strokes cover exactly the same lines.

    With ``gap`` > ``tol``, ends up to ``gap`` apart are bridged too, but only where both lines
    point straight across the gap at each other: a line interrupted by something (a removed
    colour crossing it) is mended, while ends that merely lie close together stay apart.
    """
    if len(strokes) < 2 or tol <= 0:
        return [list(s) for s in strokes]
    reach = max(tol, gap)
    cell = reach
    grid: dict[tuple[int, int], list[tuple[int, int]]] = {}   # cell -> [(stroke, 0=start / 1=end)]

    def key(p):
        return int(math.floor(p[0] / cell)), int(math.floor(p[1] / cell))

    open_ids = [i for i, s in enumerate(strokes) if len(s) >= 2 and not is_closed(s)]
    for i in open_ids:
        for side, p in ((0, strokes[i][0]), (1, strokes[i][-1])):
            grid.setdefault(key(p), []).append((i, side))
    used = [False] * len(strokes)

    def best_next(chain: Polyline, me: int) -> tuple[int, int] | None:
        p = chain[-1]
        heading = _heading(chain, at_end=True)
        kx, ky = key(p)
        best, best_score = None, math.inf
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j, side in grid.get((kx + dx, ky + dy), ()):
                    if used[j] or j == me:
                        continue
                    q = strokes[j][0] if side == 0 else strokes[j][-1]
                    d = math.dist(p, q)
                    if d > reach:
                        continue
                    nxt = strokes[j] if side == 0 else strokes[j][::-1]
                    into = _heading(nxt, at_end=False)
                    if d > tol and not _aims_across(chain, nxt):   # a gap: only straight across
                        continue
                    score = d / reach
                    if heading and into:
                        score += 1.0 - (heading[0] * into[0] + heading[1] * into[1])   # 0 straight .. 2 U-turn
                    if score < best_score:
                        best, best_score = (j, side), score
        return best

    out: list[Polyline] = []
    for i in range(len(strokes)):
        if used[i]:
            continue
        used[i] = True
        chain = list(strokes[i])
        if len(chain) < 2 or is_closed(chain):
            out.append(chain)
            continue
        for _ in range(2):                       # grow forwards, then backwards from the start
            while True:
                nxt = best_next(chain, i)
                if nxt is None:
                    break
                j, side = nxt
                used[j] = True
                seg = strokes[j] if side == 0 else list(reversed(strokes[j]))
                chain.extend(seg[1:] if math.dist(chain[-1], seg[0]) < 1e-12 else seg)
            chain.reverse()
        d = math.dist(chain[0], chain[-1]) if len(chain) > 3 else 0.0
        if 0 < d <= tol or (tol < d <= gap and _aims_across(chain, chain)):
            chain.append(chain[0])               # the chain came back to its start: close the loop
        out.append(chain)
    return out


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------

def _nearest_neighbour(strokes: list[Polyline], start: Point) -> list[Polyline]:
    starts = np.array([s[0] for s in strokes], dtype=float)
    ends = np.array([s[-1] for s in strokes], dtype=float)
    remaining = np.ones(len(strokes), dtype=bool)
    pos = np.asarray(start, dtype=float)
    out: list[Polyline] = []
    for _ in range(len(strokes)):
        ds = np.where(remaining, np.hypot(*(starts - pos).T), np.inf)
        de = np.where(remaining, np.hypot(*(ends - pos).T), np.inf)
        i_s, i_e = int(ds.argmin()), int(de.argmin())
        if ds[i_s] <= de[i_e]:
            idx, stroke = i_s, list(strokes[i_s])
        else:
            idx, stroke = i_e, list(reversed(strokes[i_e]))
        remaining[idx] = False
        out.append(stroke)
        pos = np.asarray(stroke[-1], dtype=float)
    return out


def _two_opt(strokes: list[Polyline], start: Point, time_budget: float, window: int) -> list[Polyline]:
    """Reverse runs of strokes (and each stroke in them) while that shortens pen-up travel.

    For the run i..j, the travel E[i-1]->S[i] and E[j]->S[j+1] is replaced by
    E[i-1]->E[j] and S[i]->S[j+1]. The path is open: the last stroke has no successor.
    """
    n = len(strokes)
    order = list(range(n))
    rev = np.zeros(n, dtype=bool)
    S = np.array([s[0] for s in strokes], dtype=float)
    E = np.array([s[-1] for s in strokes], dtype=float)
    start = np.asarray(start, dtype=float)
    deadline = time.monotonic() + time_budget
    improved = True
    while improved and time.monotonic() < deadline:
        improved = False
        for i in range(n):
            if time.monotonic() >= deadline:
                break
            prev = E[i - 1] if i > 0 else start
            hi = min(n, i + window)
            js = np.arange(i, hi)
            nxt = np.arange(i + 1, hi + 1)
            has_next = nxt < n
            nxt_c = np.minimum(nxt, n - 1)
            old = np.hypot(*(prev - S[i])) + np.where(has_next, np.hypot(*(E[js] - S[nxt_c]).T), 0.0)
            new = np.hypot(*(prev - E[js]).T) + np.where(has_next, np.hypot(*(S[i] - S[nxt_c]).T), 0.0)
            gain = old - new
            k = int(gain.argmax())
            if gain[k] > 1e-9:
                j = i + k
                S[i:j + 1], E[i:j + 1] = E[i:j + 1][::-1].copy(), S[i:j + 1][::-1].copy()
                order[i:j + 1] = order[i:j + 1][::-1]
                rev[i:j + 1] = ~rev[i:j + 1][::-1]
                improved = True
    return [list(reversed(strokes[o])) if r else strokes[o] for o, r in zip(order, rev)]


def _rotate_loops(strokes: list[Polyline], start: Point) -> list[Polyline]:
    """Start each closed loop at the vertex nearest to where the pen is coming from."""
    pos = start
    out = []
    for s in strokes:
        if is_closed(s):
            body = s[:-1]
            pts = np.asarray(body, dtype=float)
            k = int(np.hypot(*(pts - np.asarray(pos)).T).argmin())
            s = body[k:] + body[:k] + [body[k]]
        out.append(s)
        pos = s[-1]
    return out


def order_strokes(strokes: list[Polyline], start: Point = (0.0, 0.0),
                  time_budget: float = 1.5, window: int = 400) -> list[Polyline]:
    """Drawing sequence with short pen-up travel. Strokes may be reversed, loops rotated;
    the set of lines drawn is unchanged."""
    strokes = [list(s) for s in strokes if len(s) > 0]
    if len(strokes) < 2:
        return _rotate_loops(strokes, start)
    seq = _nearest_neighbour(strokes, start)
    if len(seq) >= 3:
        seq = _two_opt(seq, start, time_budget, window)
    return _rotate_loops(seq, start)

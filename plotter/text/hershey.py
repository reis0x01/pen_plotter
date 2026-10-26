"""Single-stroke Hershey fonts in JHF format.

JHF glyph record: columns 0-4 glyph id, 5-7 vertex count (including the left/right
bearing pair), then coordinate pairs encoded as ``chr(ord('R') + value)``. The first
pair is the left/right bearing; " R" is a pen-up. Font y grows downward.

The bundled files hold printable ASCII (32..126) in order, so line N (after the
optional ``#{...}`` metrics header) is ``chr(32 + N)``. Turkish letters (ç ğ ı İ ö ş ü,
plus â î û) are composed at load time from the base glyph and a drawn accent.
"""
from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

FONT_DIR = Path(__file__).parent / "fonts"

FONT_TITLES = {
    "futural": "Roman Simplex",
    "scripts": "Script Simplex",
    "rowmand": "Roman Duplex",
}

# Characters STT commonly produces that the ASCII fonts lack.
SUBSTITUTIONS = {
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "…": "...", " ": " ",
    "\t": " ",
}

Point = tuple[float, float]


@dataclass(frozen=True)
class Glyph:
    char: str
    left: int
    right: int
    strokes: tuple[tuple[Point, ...], ...]  # font units, y down, x relative to glyph centre

    @property
    def advance(self) -> int:
        return self.right - self.left


@dataclass(frozen=True)
class HersheyFont:
    name: str
    glyphs: dict[str, Glyph]
    cap_line: float
    base_line: float
    bottom_line: float

    @property
    def cap_height(self) -> float:
        return self.base_line - self.cap_line

    @property
    def ascent(self) -> float:
        """Largest extent of any glyph above the baseline, font units."""
        top = min((y for g in self.glyphs.values() for s in g.strokes for _, y in s),
                  default=self.cap_line)
        return self.base_line - min(top, self.cap_line)

    @property
    def descent(self) -> float:
        """Largest extent of any glyph below the baseline, font units."""
        bottom = max((y for g in self.glyphs.values() for s in g.strokes for _, y in s),
                     default=self.base_line)
        return max(bottom, self.base_line) - self.base_line

    def glyph(self, ch: str) -> Glyph | None:
        return self.glyphs.get(ch)


def _decode(record: str, ch: str) -> Glyph:
    body = record[8:]
    if len(body) < 2:
        raise ValueError(f"glyph {ch!r}: truncated record")
    left = ord(body[0]) - ord("R")
    right = ord(body[1]) - ord("R")
    strokes: list[tuple[Point, ...]] = []
    cur: list[Point] = []
    for i in range(2, len(body) - 1, 2):
        pair = body[i:i + 2]
        if pair == " R":
            if cur:
                strokes.append(tuple(cur))
            cur = []
            continue
        cur.append((ord(pair[0]) - ord("R"), ord(pair[1]) - ord("R")))
    if cur:
        strokes.append(tuple(cur))
    return Glyph(ch, left, right, tuple(strokes))


def parse_jhf(text: str, name: str = "font") -> HersheyFont:
    lines = text.splitlines()
    metrics = {"define_cap_line": -12, "define_base_line": 9, "define_bottom_line": 16}
    if lines and lines[0].startswith("#"):
        metrics.update(json.loads(lines[0][1:]))
        lines = lines[1:]
    glyphs = {}
    for idx, record in enumerate(lines):
        code = 32 + idx
        if code > 126:
            break
        if not record.strip():
            continue
        ch = chr(code)
        glyphs[ch] = _decode(record.rstrip("\n"), ch)
    return HersheyFont(name, glyphs, float(metrics["define_cap_line"]),
                       float(metrics["define_base_line"]), float(metrics["define_bottom_line"]))


# ---------------------------------------------------------------------------
# Turkish (and circumflex) letters, composed from the base glyph + a drawn accent
# ---------------------------------------------------------------------------

COMPOSITES = {
    "ç": ("c", "cedilla"), "Ç": ("C", "cedilla"),
    "ş": ("s", "cedilla"), "Ş": ("S", "cedilla"),
    "ğ": ("g", "breve"), "Ğ": ("G", "breve"),
    "ö": ("o", "diaeresis"), "Ö": ("O", "diaeresis"),
    "ü": ("u", "diaeresis"), "Ü": ("U", "diaeresis"),
    "ı": ("i", "dotless"), "İ": ("I", "dot"),
    "â": ("a", "circumflex"), "Â": ("A", "circumflex"),
    "î": ("ı", "circumflex"), "Î": ("I", "circumflex"),
    "û": ("u", "circumflex"), "Û": ("U", "circumflex"),
}


def _dot(x: float, y: float) -> tuple[Point, ...]:
    """A small closed diamond, like the dots in the Hershey fonts."""
    return ((x, y - 1), (x + 1, y), (x, y + 1), (x - 1, y), (x, y - 1))


def _compose(base: Glyph, ch: str, accent: str, base_line: float) -> Glyph:
    pts = [p for stroke in base.strokes for p in stroke]
    xs = [x for x, _ in pts]
    top = min(y for _, y in pts)
    cx = (min(xs) + max(xs)) / 2
    strokes = list(base.strokes)
    if accent == "dotless":
        stem_top = min(y for _, y in max(base.strokes, key=lambda st: max(y for _, y in st) - min(y for _, y in st)))
        strokes = [st for st in base.strokes if max(y for _, y in st) >= stem_top]
    elif accent == "dot":
        strokes.append(_dot(cx, top - 3))
    elif accent == "diaeresis":
        strokes += [_dot(cx - 3, top - 3), _dot(cx + 3, top - 3)]
    elif accent == "breve":
        strokes.append(((cx - 4, top - 5), (cx - 3, top - 3), (cx - 1, top - 2),
                        (cx + 1, top - 2), (cx + 3, top - 3), (cx + 4, top - 5)))
    elif accent == "circumflex":
        strokes.append(((cx - 4, top - 2), (cx, top - 6), (cx + 4, top - 2)))
    elif accent == "cedilla":
        b = base_line
        strokes.append(((cx, b), (cx, b + 2), (cx + 2, b + 3), (cx + 2, b + 5),
                        (cx, b + 6), (cx - 2, b + 6)))
    return Glyph(ch, base.left, base.right, tuple(tuple(st) for st in strokes))


def add_composites(font: HersheyFont) -> HersheyFont:
    for ch, (base_ch, accent) in COMPOSITES.items():     # dict order: 'ı' before 'î'
        base = font.glyphs.get(base_ch)
        if base is not None and ch not in font.glyphs:
            font.glyphs[ch] = _compose(base, ch, accent, font.base_line)
    return font


def available_fonts() -> list[str]:
    return sorted(p.stem for p in FONT_DIR.glob("*.jhf"))


@lru_cache(maxsize=None)
def load_font(name: str = "futural") -> HersheyFont:
    path = FONT_DIR / f"{name}.jhf"
    if not path.exists():
        raise ValueError(f"Unknown font {name!r}; available: {', '.join(available_fonts())}")
    return add_composites(parse_jhf(path.read_text(encoding="ascii"), name))


def normalize_text(text: str, font: HersheyFont) -> tuple[str, list[str]]:
    """Replace characters the font cannot draw. Returns (text, warnings)."""
    out = []
    missing: set[str] = set()
    text = unicodedata.normalize("NFC", text)       # e.g. 'i' + combining dot -> 'İ'
    for ch in text.replace("\r\n", "\n").replace("\r", "\n"):
        ch = SUBSTITUTIONS.get(ch, ch)
        for c in ch:
            if c == "\n" or c in font.glyphs:
                out.append(c)
            else:
                missing.add(c)
                out.append("?")
    warnings = [f"Character {c!r} is not in font '{font.name}' and was replaced by '?'"
                for c in sorted(missing)]
    return "".join(out), warnings

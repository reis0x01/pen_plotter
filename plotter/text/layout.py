"""Word-wrapping text layout on the page using Hershey advance widths.

Output coordinates are in the page frame (u right, v down, mm) described in
``plotter.config``. Conversion to machine work coordinates happens in the G-code
generator, so layout never needs to know the axis orientation.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .hershey import HersheyFont, load_font, normalize_text

Point = tuple[float, float]
Polyline = list[Point]


@dataclass
class LayoutParams:
    page_width: float = 210.0
    page_height: float = 297.0
    margin_left: float = 15.0
    margin_right: float = 15.0
    margin_top: float = 15.0
    margin_bottom: float = 15.0
    font: str = "futural"
    char_height: float = 7.0       # capital height, mm
    line_spacing: float = 1.8      # baseline pitch = line_spacing * char_height
    letter_spacing: float = 0.0    # extra mm after every glyph
    hyphenate: bool = True         # add '-' when hard-breaking a word
    align: str = "left"            # 'left' or 'center' (each line on the paper centre)
    valign: str = "top"            # 'top' or 'middle' (the block on the paper centre)

    @classmethod
    def from_profile(cls, profile) -> "LayoutParams":
        return cls(
            page_width=profile.paper_width, page_height=profile.paper_height,
            margin_left=profile.margin_left, margin_right=profile.margin_right,
            margin_top=profile.margin_top, margin_bottom=profile.margin_bottom,
            font=profile.font, char_height=profile.char_height,
            line_spacing=profile.line_spacing, letter_spacing=profile.letter_spacing,
            hyphenate=profile.hyphenate_long_words,
            align=profile.text_align, valign=profile.vertical_align,
        )

    @property
    def text_width(self) -> float:
        return self.page_width - self.margin_left - self.margin_right


@dataclass
class Line:
    text: str
    baseline: float          # v coordinate of the baseline, mm
    width: float             # advance width, mm


@dataclass
class Page:
    lines: list[Line] = field(default_factory=list)
    strokes: list[Polyline] = field(default_factory=list)


@dataclass
class LayoutResult:
    pages: list[Page]
    warnings: list[str]
    lines_per_page: int

    @property
    def page_count(self) -> int:
        return len(self.pages)


class LayoutError(ValueError):
    pass


class TextLayout:
    def __init__(self, params: LayoutParams, font: HersheyFont | None = None):
        self.p = params
        self.font = font or load_font(params.font)
        self.scale = params.char_height / self.font.cap_height

    # ---- measuring --------------------------------------------------------
    def char_advance(self, ch: str) -> float:
        g = self.font.glyph(ch)
        return (g.advance if g else 0) * self.scale + self.p.letter_spacing

    def text_width(self, text: str) -> float:
        return sum(self.char_advance(c) for c in text)

    @property
    def line_pitch(self) -> float:
        return self.p.char_height * self.p.line_spacing

    def lines_per_page(self) -> int:
        """How many baselines fit between the top and bottom margins, keeping every
        glyph (including ascenders and descenders) inside them."""
        ascent = self.font.ascent * self.scale
        descent = self.font.descent * self.scale
        avail = self.p.page_height - self.p.margin_top - self.p.margin_bottom - ascent - descent
        if avail < 0:
            return 0
        return int(avail // self.line_pitch) + 1

    # ---- wrapping ---------------------------------------------------------
    def wrap(self, text: str) -> list[str]:
        """Split text into lines that fit the text width. Explicit newlines are kept;
        words wider than a line are hard-broken (with a hyphen when enabled)."""
        width = self.p.text_width
        if width <= 0:
            raise LayoutError("Margins leave no horizontal room for text")
        if self.text_width("W") > width:
            raise LayoutError("Character height is too large for the line width")
        out: list[str] = []
        for paragraph in text.split("\n"):
            words = paragraph.split()
            if not words:
                out.append("")
                continue
            line = ""
            for word in words:
                candidate = f"{line} {word}" if line else word
                if self.text_width(candidate) <= width:
                    line = candidate
                    continue
                if line:
                    out.append(line)
                    line = ""
                # word alone on a fresh line
                while self.text_width(word) > width:
                    head, word = self._break_word(word, width)
                    out.append(head)
                line = word
            out.append(line)
        return out

    def _break_word(self, word: str, width: float) -> tuple[str, str]:
        hyphen = "-" if self.p.hyphenate else ""
        hyphen_w = self.text_width(hyphen)
        n = 0
        acc = 0.0
        for ch in word:
            w = self.char_advance(ch)
            if acc + w + hyphen_w > width:
                break
            acc += w
            n += 1
        n = max(n, 1)  # always make progress
        if n >= len(word):
            return word, ""
        return word[:n] + hyphen, word[n:]

    # ---- geometry ---------------------------------------------------------
    def glyph_strokes(self, text: str, u0: float, baseline: float) -> list[Polyline]:
        strokes: list[Polyline] = []
        u = u0
        s = self.scale
        for ch in text:
            g = self.font.glyph(ch)
            if g is None:
                continue
            ox = u - g.left * s  # glyph x is relative to its centre; left is negative
            for stroke in g.strokes:
                strokes.append([(ox + x * s, baseline + (y - self.font.base_line) * s)
                                for x, y in stroke])
            u += g.advance * s + self.p.letter_spacing
        return strokes

    def layout(self, text: str) -> LayoutResult:
        text, warnings = normalize_text(text, self.font)
        lines = self.wrap(text)
        # Drop trailing blank lines; they would only produce empty pages.
        while lines and not lines[-1].strip():
            lines.pop()
        per_page = self.lines_per_page()
        if per_page < 1:
            raise LayoutError("Top/bottom margins leave no room for a single line")
        ascent = self.font.ascent * self.scale
        descent = self.font.descent * self.scale
        avail_h = self.p.page_height - self.p.margin_top - self.p.margin_bottom
        pages: list[Page] = []
        for start in range(0, len(lines), per_page):
            page = Page()
            chunk = lines[start:start + per_page]
            top = self.p.margin_top
            if self.p.valign == "middle":
                # Centre the block on the paper, then keep it inside the margins.
                block = ascent + (len(chunk) - 1) * self.line_pitch + descent
                top = min(max((self.p.page_height - block) / 2, self.p.margin_top),
                          self.p.margin_top + max(0.0, avail_h - block))
            for i, text_line in enumerate(chunk):
                baseline = top + ascent + i * self.line_pitch
                width = self.text_width(text_line)
                u0 = self.p.margin_left
                if self.p.align == "center":
                    # Centre on the paper (not between unequal margins), inside the margins.
                    u0 = min(max((self.p.page_width - width) / 2, self.p.margin_left),
                             self.p.margin_left + max(0.0, self.p.text_width - width))
                page.lines.append(Line(text_line, baseline, width))
                page.strokes.extend(self.glyph_strokes(text_line, u0, baseline))
            pages.append(page)
        if not pages:
            pages.append(Page())
        return LayoutResult(pages, warnings, per_page)


def layout_text(text: str, params: LayoutParams) -> LayoutResult:
    return TextLayout(params).layout(text)

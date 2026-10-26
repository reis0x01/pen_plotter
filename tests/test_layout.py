import pytest

from plotter.text.hershey import available_fonts, load_font, normalize_text, parse_jhf
from plotter.text.layout import LayoutError, LayoutParams, TextLayout, layout_text


def params(**kw):
    base = dict(page_width=210, page_height=297, margin_left=15, margin_right=15,
                margin_top=15, margin_bottom=15, char_height=7, line_spacing=1.8)
    base.update(kw)
    return LayoutParams(**base)


def all_points(page):
    return [p for s in page.strokes for p in s]


# ---- fonts ------------------------------------------------------------------

def test_bundled_fonts_cover_printable_ascii():
    assert {"futural", "scripts"} <= set(available_fonts())
    for name in available_fonts():
        f = load_font(name)
        assert all(chr(c) in f.glyphs for c in range(32, 127))      # printable ASCII
        assert len(f.glyphs) == 95 + 18                               # + Turkish letters
        assert f.cap_height == 21
        assert f.glyph(" ").strokes == ()


def test_glyph_decoding_simple_record():
    font = parse_jhf("#{\"define_cap_line\": -12, \"define_base_line\": 9, \"define_bottom_line\": 16}\n"
                     "12345  1JZ\n"          # space
                     "12345  4MWRFRT RRYQZ")  # '!' fragment: two strokes
    g = font.glyph("!")
    assert (g.left, g.right) == (-5, 5)
    assert g.strokes == (((0, -12), (0, 2)), ((0, 7), (-1, 8)))


def test_single_stroke_font_is_not_outline():
    # 'l' in a single-stroke font is one open line, not a closed outline
    g = load_font("futural").glyph("l")
    assert len(g.strokes) == 1
    assert g.strokes[0][0] != g.strokes[0][-1]


def test_normalize_replaces_unknown_characters():
    text, warnings = normalize_text("café “ok”—yes", load_font("futural"))
    assert text == 'caf? "ok"-yes'
    assert len(warnings) == 1 and "é" in warnings[0]


# ---- wrapping -----------------------------------------------------------------

def test_wrap_uses_advance_widths_and_fits_width():
    lay = TextLayout(params())
    text = "the quick brown fox jumps over the lazy dog " * 6
    lines = lay.wrap(text)
    assert len(lines) > 1
    for line in lines:
        assert lay.text_width(line) <= lay.p.text_width + 1e-9
    # greedy: the next word would not have fit on the previous line
    for a, b in zip(lines, lines[1:]):
        assert lay.text_width(a + " " + b.split()[0]) > lay.p.text_width


def test_wrap_keeps_words_intact():
    lay = TextLayout(params())
    text = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu " * 3
    assert " ".join(lay.wrap(text)).split() == text.split()


def test_explicit_newlines_are_honoured_including_blank_lines():
    lay = TextLayout(params())
    assert lay.wrap("one\ntwo\n\nthree") == ["one", "two", "", "three"]


def test_long_word_is_hard_broken_with_hyphen():
    lay = TextLayout(params(margin_left=60, margin_right=60))
    word = "supercalifragilisticexpialidocious"
    lines = lay.wrap(word)
    assert len(lines) > 1
    assert all(l.endswith("-") for l in lines[:-1])
    assert "".join(l.rstrip("-") for l in lines) == word
    assert all(lay.text_width(l) <= lay.p.text_width + 1e-9 for l in lines)


def test_long_word_without_hyphen():
    lay = TextLayout(params(margin_left=60, margin_right=60, hyphenate=False))
    lines = lay.wrap("abcdefghijklmnopqrstuvwxyzabcdefghij")
    assert "".join(lines) == "abcdefghijklmnopqrstuvwxyzabcdefghij"
    assert not any(l.endswith("-") for l in lines)


def test_character_too_large_is_rejected():
    with pytest.raises(LayoutError):
        TextLayout(params(char_height=200)).wrap("W")


# ---- geometry / pages ---------------------------------------------------------------

def test_strokes_stay_inside_margins():
    p = params()
    result = layout_text("Hello, World! gjpqy {[()]} " * 20, p)
    for page in result.pages:
        for u, v in all_points(page):
            assert p.margin_left - 1e-6 <= u <= p.page_width - p.margin_right + 1e-6
            assert p.margin_top - 1e-6 <= v <= p.page_height - p.margin_bottom + 1e-6


def test_first_line_is_near_top_and_lines_go_down():
    result = layout_text("first\nsecond", params())
    lines = result.pages[0].lines
    assert lines[0].baseline < lines[1].baseline
    assert lines[1].baseline - lines[0].baseline == pytest.approx(7 * 1.8)
    assert min(v for _, v in all_points(result.pages[0])) >= 15


def test_overflow_creates_additional_pages():
    lay = TextLayout(params())
    per_page = lay.lines_per_page()
    text = "\n".join(f"line {i}" for i in range(per_page + 3))
    result = lay.layout(text)
    assert result.page_count == 2
    assert len(result.pages[0].lines) == per_page
    assert [l.text for l in result.pages[1].lines] == [f"line {i}" for i in range(per_page, per_page + 3)]


def test_empty_text_gives_one_empty_page():
    result = layout_text("   \n\n", params())
    assert result.page_count == 1
    assert result.pages[0].strokes == []


def test_character_height_scales_glyphs():
    small = layout_text("H", params(char_height=5)).pages[0]
    big = layout_text("H", params(char_height=10)).pages[0]
    h = lambda page: max(v for _, v in all_points(page)) - min(v for _, v in all_points(page))
    assert h(big) == pytest.approx(2 * h(small))
    assert h(small) == pytest.approx(5)


def _bbox(page):
    pts = all_points(page)
    return min(u for u, _ in pts), min(v for _, v in pts), max(u for u, _ in pts), max(v for _, v in pts)


def test_center_alignment_centres_on_the_paper():
    # unequal margins (owner's profile: left 15, right 10) must not shift the centre
    p = params(align="center", valign="middle", margin_left=15, margin_right=10)
    page = layout_text("I love Egypt\nwelcome to Turkey", p).pages[0]
    lay = TextLayout(p)
    for line in page.lines:
        strokes = lay.glyph_strokes(line.text, (p.page_width - line.width) / 2, line.baseline)
        assert strokes == [s for s in page.strokes if s in strokes]      # placed at paper centre
    u0, v0, u1, v1 = _bbox(page)
    assert (u0 + u1) / 2 == pytest.approx(105, abs=1.5)   # ink centred on the A4 width
    assert (v0 + v1) / 2 == pytest.approx(148.5, abs=3)   # and on the height
    assert v0 > 120 and v1 < 180


def test_left_top_default_is_unchanged():
    a = layout_text("Hello", params()).pages[0]
    b = layout_text("Hello", params(align="left", valign="top")).pages[0]
    assert a.strokes == b.strokes
    assert _bbox(a)[0] == pytest.approx(15, abs=1.5)


def test_center_long_text_still_within_margins():
    p = params(align="center", valign="middle")
    result = layout_text("the quick brown fox jumps over the lazy dog " * 40, p)
    for page in result.pages:
        u0, v0, u1, v1 = _bbox(page)
        assert u0 >= p.margin_left - 1e-6 and u1 <= p.page_width - p.margin_right + 1e-6
        assert v0 >= p.margin_top - 1e-6 and v1 <= p.page_height - p.margin_bottom + 1e-6


# ---- Turkish ------------------------------------------------------------------------

TURKISH = "çÇğĞıİöÖşŞüÜâîûÂÎÛ"


@pytest.mark.parametrize("font", ["futural", "scripts", "rowmand"])
def test_turkish_letters_exist_in_every_font(font):
    f = load_font(font)
    for ch in TURKISH:
        assert f.glyph(ch) is not None, ch
    text, warnings = normalize_text("Günaydın İstanbul, çok güzel şehir", f)
    assert warnings == [] and "?" not in text


def test_turkish_accents_are_added_to_the_base_letter():
    f = load_font("futural")
    top = lambda g: min(y for s in g.strokes for _, y in s)
    bottom = lambda g: max(y for s in g.strokes for _, y in s)
    for ch, base, where in [("ö", "o", "above"), ("Ü", "U", "above"), ("ğ", "g", "above"),
                            ("İ", "I", "above"), ("ç", "c", "below"), ("Ş", "S", "below")]:
        g, b = f.glyph(ch), f.glyph(base)
        assert set(b.strokes) <= set(g.strokes) and len(g.strokes) > len(b.strokes)
        assert g.advance == b.advance
        assert (top(g) < top(b)) if where == "above" else (bottom(g) > f.base_line)
    # dotless i: the i without its dot
    assert len(f.glyph("ı").strokes) == len(f.glyph("i").strokes) - 1
    assert top(f.glyph("ı")) > top(f.glyph("i"))


def test_decomposed_unicode_is_normalised():
    text, warnings = normalize_text("İstanbul ş", load_font("futural"))   # I+dot, s+cedilla
    assert text == "İstanbul ş" and warnings == []


def test_turkish_text_layout_stays_inside_margins():
    p = params(align="center", valign="middle")
    result = layout_text("ÇĞİÖŞÜ Âİ ağaç şeker " * 30, p)
    for page in result.pages:
        u0, v0, u1, v1 = _bbox(page)
        assert v0 >= p.margin_top - 1e-6 and v1 <= p.page_height - p.margin_bottom + 1e-6

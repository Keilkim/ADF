"""Symbols from symbol fonts (Wingdings, Symbol) that a PDF stores without text.

Office exports a Wingdings character such as ❷ in a font subset with no
character map: the text layer reads a control code. The subset keeps the
glyph's outline, so the installed font's character with the same outline
names it. Common symbols get their Unicode character, which any Korean
font draws; others get the symbol font's private-use code, which the
reader places as a picture of the symbol.
"""
import io
from functools import lru_cache

from .font_metrics import _installed, _key

# Wingdings codes with an exact Unicode counterpart.
WINGDINGS = {
    **{0x80 + index: chr(0x24EA if index == 0 else 0x245F + index) for index in range(11)},  # ⓪①…⑩
    **{0x8B + index: chr(0x24FF if index == 0 else 0x2775 + index) for index in range(11)},  # ⓿❶…❿
    0x6C: '●', 0x6E: '■', 0x6F: '□', 0x71: '❑', 0x72: '❒', 0x76: '❖', 0xA1: '○', 0xA7: '▪',
    0xA8: '◻', 0xD8: '➢', 0xFC: '✔', 0xFB: '✘', 0x4A: '☺', 0x4C: '☹', 0xF0: '⇨', 0xE8: '➔',
}
UNICODE = {'wingdings': WINGDINGS}


def _outline(font, glyph_name):
    glyph = font['glyf'][glyph_name]
    if glyph.isComposite() or not glyph.numberOfContours:
        return None
    return tuple(glyph.getCoordinates(font['glyf'])[0])


@lru_cache(maxsize=8)
def _codes_by_outline(path):
    """Installed symbol font: outline -> its single code (ambiguous ones dropped)."""
    from fontTools.ttLib import TTFont
    font = TTFont(path)
    found = {}
    for table in font['cmap'].tables:
        for code, glyph_name in table.cmap.items():
            outline = _outline(font, glyph_name)
            if outline:
                found.setdefault(outline, set()).add(code & 0xFF)
    return {outline: codes.pop() for outline, codes in found.items() if len(codes) == 1}


def _subset_codes(pdf, xref, path):
    """Glyph id -> symbol font code for an embedded subset."""
    from fontTools.ttLib import TTFont
    try:
        buffer = pdf.extract_font(xref)[3]
        subset = TTFont(io.BytesIO(buffer)) if buffer else None
    except Exception:
        return {}
    if subset is None or 'glyf' not in subset:
        return {}
    known = _codes_by_outline(path)
    codes = {}
    for glyph_id, glyph_name in enumerate(subset.getGlyphOrder()):
        outline = _outline(subset, glyph_name)
        if outline in known:
            codes[glyph_id] = known[outline]
    return codes


def recover(page):
    """(x, y) of a glyph origin -> text, for symbols the text layer lost."""
    fonts = {}
    for xref, _, _, base, _, _ in page.get_fonts():
        family = _key(base)
        if family in ('wingdings', 'wingdings2', 'wingdings3', 'webdings', 'symbol'):
            path = _installed().get(family)
            if path:
                fonts[_key(base)] = (xref, path, family)
    if not fonts:
        return {}
    found, subsets = {}, {}
    try:
        traces = page.get_texttrace()
    except Exception:
        return {}
    for trace in traces:
        entry = fonts.get(_key(trace.get('font', '')))
        if entry is None:
            continue
        xref, path, family = entry
        if xref not in subsets:
            subsets[xref] = _subset_codes(page.parent, xref, path)
        codes = subsets[xref]
        for unicode, glyph_id, origin, _ in trace['chars']:
            if (unicode < 32 or unicode == 0xFFFD) and glyph_id in codes:
                code = codes[glyph_id]
                text = UNICODE.get(family, {}).get(code) or chr(0xF000 + code)
                found[(round(origin[0], 1), round(origin[1], 1))] = text
    return found


__all__ = ['recover', 'WINGDINGS']

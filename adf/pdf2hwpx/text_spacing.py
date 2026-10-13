"""Whitespace a PDF's text layer hides, recovered for conversion and editing.

Some producers draw a word gap as a blank glyph mapped to '$'; MuPDF
omits gaps between Korean words that were positioned apart without a
space character. Both are restored in a rawdict text tree in place.
Only pymupdf is needed, so the converter stays a self-contained package.
"""
from __future__ import annotations

from statistics import median

import pymupdf


def restore_blank_spacers(page, data):
    """Recover a dollar-encoded spacer only when its actual glyph has no ink.

    PDF producers may map a blank glyph to '$', or omit its Unicode mapping
    and expose CID 36. A replacement font then paints a real dollar sign.
    Accurate glyph bounds distinguish these spacers from genuine currency.
    Keep the normal boxes for selection and redaction; change only the text.
    """
    def chars(tree):
        for block in tree['blocks']:
            for line in block.get('lines', []):
                for span in line['spans']:
                    for char in span['chars']:
                        if char['c'] == '$':
                            yield span, char

    candidates = list(chars(data))
    if not candidates:
        return set()
    flags = (pymupdf.TEXTFLAGS_RAWDICT & ~pymupdf.TEXT_PRESERVE_IMAGES) | pymupdf.TEXT_ACCURATE_BBOXES
    accurate = page.get_text('rawdict', flags=flags)

    def key(span, char):
        return span['font'], span['size'], tuple(char['origin'])

    blank = {}
    for span, char in chars(accurate):
        rect = pymupdf.Rect(char['bbox'])
        empty = rect.width > 0 and abs(rect.height) < 1e-6
        identity = key(span, char)
        blank[identity] = blank.get(identity, True) and empty
    recovered = set()
    for span, char in candidates:
        if blank.get(key(span, char), False):
            recovered.add(key(span, char))
            char['c'] = ' '
    return recovered


def restore_text_spacing(page, data):
    """Keep encoded whitespace and recover gaps omitted between Korean words.

    MuPDF deliberately omits inferred spaces between CJK glyphs. Some Korean
    PDFs position words separately without drawing a space. Compare gaps with
    the line's normal tracking so evenly spaced lettering remains untouched.
    Chinese and Japanese writing is left alone: gaps need not delimit words.
    """
    restore_blank_spacers(page, data)

    def hangul(text):
        return any('\uac00' <= char <= '\ud7a3' or '\u1100' <= char <= '\u11ff'
                   or '\u3130' <= char <= '\u318f' for char in text)

    for block in data['blocks']:
        for line in block.get('lines', []):
            if tuple(line['dir']) != (1., 0.) or line.get('wmode', 0):
                continue
            chars = [(span, char) for span in line['spans'] for char in span['chars']]
            pairs = [(a, b) for a, b in zip(chars, chars[1:])
                     if a[1]['c'] and b[1]['c'] and not a[1]['c'].isspace() and not b[1]['c'].isspace()]
            if not pairs or not any(hangul(char['c']) for _, char in chars):
                continue
            gaps = [b[1]['bbox'][0] - a[1]['bbox'][2] for a, b in pairs]
            tracking = max(0., median(gaps))
            previous = None
            for span in line['spans']:
                normalized = []
                for char in span['chars']:
                    if previous is not None:
                        previous_span, last = previous
                        gap = char['bbox'][0] - last['bbox'][2]
                        size = min(span['size'], previous_span['size'])
                        if (last['c'] and char['c'] and not last['c'].isspace() and not char['c'].isspace()
                                and (hangul(last['c']) or hangul(char['c']))
                                and gap >= max(.5, tracking + .12 * size)):
                            normalized.append({'c': ' ', 'synthetic': True,
                                'origin': (last['bbox'][2], char['origin'][1]),
                                'bbox': (last['bbox'][2], char['bbox'][1], char['bbox'][0], char['bbox'][3])})
                    normalized.append(char)
                    previous = span, char
                span['chars'] = normalized
    return data

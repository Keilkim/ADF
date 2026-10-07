"""Recover visually coherent editable paragraphs from positioned PDF text.

PDFs do not reliably store sentences or paragraphs. These groups therefore use
line spacing, alignment, font size and table rules, not language interpretation.
All coordinates are unrotated page coordinates, as returned by get_text().
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from statistics import median

import pymupdf


@dataclass
class _Line:
    spans: list[dict]
    bbox: pymupdf.Rect
    text: str
    baseline: float
    direction: tuple
    wmode: int

    @property
    def style(self):
        return max(self.spans, key=lambda span: len(span["text"].strip()))

    @property
    def size(self):
        return max(float(self.style["size"]), 1)


def _rules(page):
    horizontal, vertical = [], []
    for drawing in page.get_drawings():
        for item in drawing["items"]:
            segments = []
            if item[0] == "l":
                segments = [(item[1], item[2])]
            elif item[0] == "re":
                rect = pymupdf.Rect(item[1])
                segments = [(rect.tl, rect.tr), (rect.bl, rect.br),
                            (rect.tl, rect.bl), (rect.tr, rect.br)]
            for start, end in segments:
                if abs(start.y - end.y) < .5 and abs(start.x - end.x) > 4:
                    horizontal.append((start.y, min(start.x, end.x), max(start.x, end.x)))
                if abs(start.x - end.x) < .5 and abs(start.y - end.y) > 4:
                    vertical.append((start.x, min(start.y, end.y), max(start.y, end.y)))
    return horizontal, vertical


def _make_line(spans, direction, wmode):
    box = pymupdf.Rect(spans[0]["bbox"])
    text = spans[0]["text"]
    for previous, span in zip(spans, spans[1:]):
        gap = span["bbox"][0] - previous["bbox"][2]
        if (gap > .16 * min(span["size"], previous["size"])
                and text and not text[-1].isspace() and not span["text"].startswith(" ")):
            text += " "
        text += span["text"]
        box |= pymupdf.Rect(span["bbox"])
    main = max(spans, key=lambda span: len(span["text"].strip()))
    return _Line(spans, box, text.strip(), float(main["origin"][1]), tuple(direction), wmode)


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


def _extract_lines(page, vertical):
    # RAWDICT lets us split even a single wide span at a column-sized space.
    data = page.get_text("rawdict", flags=pymupdf.TEXTFLAGS_RAWDICT & ~pymupdf.TEXT_PRESERVE_IMAGES)
    restore_text_spacing(page, data)
    fragments = []
    for block in data["blocks"]:
        if block["type"] != 0:
            continue
        for line in block["lines"]:
            spans = []
            for span in line["spans"]:
                chars = span["chars"]
                start = 0
                for index in range(1, len(chars) + 1):
                    split = index == len(chars)
                    if not split:
                        gap = chars[index]["bbox"][0] - chars[index - 1]["bbox"][2]
                        split = gap > max(8, span["size"] * 1.6)
                        if chars[index - 1]["c"].isspace():
                            space_start = index - 1
                            while space_start > start and chars[space_start - 1]["c"].isspace():
                                space_start -= 1
                            split |= chars[index - 1]["bbox"][2] - chars[space_start]["bbox"][0] > span["size"] * 1.6
                    if split:
                        subset = chars[start:index]
                        while subset and subset[-1]["c"].isspace():
                            subset = subset[:-1]
                        while subset and subset[0]["c"].isspace():
                            subset = subset[1:]
                        if subset:
                            box = pymupdf.Rect(subset[0]["bbox"])
                            for char in subset[1:]:
                                box |= pymupdf.Rect(char["bbox"])
                            spans.append({**span, "text": "".join(char["c"] for char in subset),
                                          "chars": subset, "bbox": tuple(box), "origin": subset[0]["origin"]})
                        start = index
            for span in spans:
                fragments.append(_make_line([span], line["dir"], line["wmode"]))
    fragments.sort(key=lambda item: (round(item.baseline, 1), item.bbox.x0))
    lines = []
    for fragment in fragments:
        # Mix of fonts or drawing operations may split one visual line into
        # several extraction blocks. Join only nearby, same-baseline pieces.
        candidates = [item for item in lines if item.direction == fragment.direction
                      and item.wmode == fragment.wmode
                      and abs(item.baseline - fragment.baseline) <= .2 * min(item.size, fragment.size)
                      and -.5 <= fragment.bbox.x0 - item.bbox.x1 <= max(8, 1.6 * min(item.size, fragment.size))]
        previous = max(candidates, key=lambda item: item.bbox.x1, default=None)
        if previous and previous.direction == (1.0, 0.0) and not any(
                previous.bbox.x1 <= x <= fragment.bbox.x0 and y0 <= fragment.baseline <= y1
                for x, y0, y1 in vertical):
            lines.remove(previous)
            lines.append(_make_line(previous.spans + fragment.spans, fragment.direction, fragment.wmode))
        else:
            lines.append(fragment)
    return sorted(lines, key=lambda item: (item.baseline, item.bbox.x0))


_LIST_START = re.compile(r"^(?:[•●▪◦‣\-–]\s|\(?\d+[.)]\s|[A-Za-z][.)]\s)")


def _continues(first, second, horizontal, lines):
    if first.direction != (1.0, 0.0) or second.direction != first.direction or first.wmode or second.wmode:
        return False
    size = min(first.size, second.size)
    if max(first.size, second.size) / size > 1.22:
        return False
    gap = second.baseline - first.baseline
    if not .75 * size <= gap <= 1.75 * size:
        return False
    shift = second.bbox.x0 - first.bbox.x0
    allowed_indent = 2 * size if _LIST_START.match(first.text) else .85 * size
    if shift > allowed_indent or shift < -2 * size:
        return False
    left, right = max(first.bbox.x0, second.bbox.x0), min(first.bbox.x1, second.bbox.x1)
    if right <= left:
        return False
    if _LIST_START.match(second.text):
        return False
    # A rule between the baselines separates table rows even when the text
    # bounding boxes touch, as often happens with tightly spaced spreadsheets.
    if any(first.baseline < y < second.baseline and x0 < right and x1 > left
           for y, x0, x1 in horizontal):
        return False
    # Repeated short aligned cells on the same rows are likely a borderless
    # table. Longer prose in adjacent columns is allowed to remain paragraphs.
    if first.bbox.width < size * 9 and second.bbox.width < size * 9:
        peers_a = [item for item in lines if item is not first
                   and abs(item.baseline - first.baseline) < size * .2
                   and (item.bbox.x0 > first.bbox.x1 + size or item.bbox.x1 < first.bbox.x0 - size)]
        peers_b = [item for item in lines if item is not second
                   and abs(item.baseline - second.baseline) < size * .2]
        if any(abs(a.bbox.x0 - b.bbox.x0) < size * .5 for a in peers_a for b in peers_b):
            return False
    return True


def text_group_at(page: pymupdf.Page, point) -> dict | None:
    """Return the paragraph under a click, or None for empty/image-only areas.

    The result extends the usual span dictionary with source_rects (small
    glyph-run redaction targets), newline-preserving text and lineheight.
    Styling in a mixed-style paragraph initially follows its largest text run.
    Non-horizontal writing is returned separately with its dir/wmode metadata;
    callers should reject editing unsupported orientations.
    """
    point = pymupdf.Point(point)
    horizontal, vertical = _rules(page)
    lines = _extract_lines(page, vertical)
    hits = [index for index, line in enumerate(lines) if line.bbox.contains(point)]
    if not hits:
        return None
    selected = min(hits, key=lambda index: lines[index].bbox.get_area())
    group = [selected]
    # Follow nearest neighbours in the same column. This cannot leap over a
    # different paragraph or a heading to find another matching line below it.
    for step in (-1, 1):
        cursor = selected
        while True:
            current = lines[cursor]
            nearby = [index for index, item in enumerate(lines)
                      if (item.baseline - current.baseline) * step > .5 * current.size
                      and abs(item.bbox.x0 - current.bbox.x0) <= 2 * current.size]
            if not nearby:
                break
            next_index = min(nearby, key=lambda index: abs(lines[index].baseline - current.baseline))
            first, second = (lines[next_index], current) if step < 0 else (current, lines[next_index])
            if not _continues(first, second, horizontal, lines):
                break
            group.append(next_index)
            cursor = next_index
    chosen = [lines[index] for index in sorted(group, key=lambda index: lines[index].baseline)]
    spans = [span for line in chosen for span in line.spans]
    main = max(spans, key=lambda span: len(span["text"].strip()))
    box = pymupdf.Rect(chosen[0].bbox)
    for line in chosen[1:]:
        box |= line.bbox
    source_rects = []
    for span in spans:
        for char in span["chars"]:
            rect = pymupdf.Rect(char["bbox"])
            if rect.is_empty or rect.is_infinite:
                continue
            # Redaction removes characters whose boxes intersect its target.
            # A central strip avoids deleting a neighbouring line's descenders.
            center = (rect.y0 + rect.y1) / 2
            inset = min(rect.width * .1, .2)
            strip = (rect.x0 + inset, center - rect.height * .1,
                     rect.x1 - inset, center + rect.height * .1)
            previous = source_rects[-1] if source_rects else None
            # Adjacent glyphs can share one redaction annotation. Keep separate
            # strips at gaps or different baselines/styles, preserving all the
            # isolation of glyph targets without hundreds of PDF annotations
            # on every live-preview keystroke.
            if (previous and abs(previous[1] - strip[1]) < .01
                    and abs(previous[3] - strip[3]) < .01
                    and -.01 <= strip[0] - previous[2] <= .5):
                source_rects[-1] = (previous[0], previous[1], strip[2], previous[3])
            else:
                source_rects.append(strip)
    spacings = [(b.baseline - a.baseline) / main["size"] for a, b in zip(chosen, chosen[1:])]
    return {key: value for key, value in {
        **main, "text": "\n".join(line.text for line in chosen), "bbox": tuple(box),
        "source_rects": source_rects, "lineheight": median(spacings) if spacings else None,
        "dir": chosen[0].direction, "wmode": chosen[0].wmode,
    }.items() if key != "chars"}


def new_text_box(page: pymupdf.Page, point, size: float = 11, width: float = 240) -> dict:
    """Return an empty paragraph at a click, for writing new text on a page.

    Empty source_rects mean nothing is removed from the page when it is applied.
    The box starts one line tall around the click and stays inside the page.
    """
    point = pymupdf.Point(point)
    bounds = page.rect * page.derotation_matrix
    lineheight = 1.3
    height = min(size * lineheight + 2, bounds.height)
    width = min(width, bounds.width)
    x0 = min(max(bounds.x0, point.x), bounds.x1 - width)
    y0 = min(max(bounds.y0, point.y - height / 2), bounds.y1 - height)
    return {"text": "", "font": "", "size": size, "color": 0x222222, "flags": 0,
            "bbox": (x0, y0, x0 + width, y0 + height), "source_rects": [], "lineheight": lineheight,
            "dir": (1.0, 0.0), "wmode": 0, "new": True}

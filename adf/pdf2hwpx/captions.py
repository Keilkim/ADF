"""Captions of tables and figures: "표 1. 제목", "<그림 2>", "Figure 3:".

A PDF holds a caption as an ordinary line of text. People see at once that
it belongs to the table or picture next to it; here the same is decided
from the words that open it ("표 1", "그림 2", "Table 3", "Figure 4") and
from its place: a short block right above or below one table or picture,
inside its width. Such a paragraph is attached to the object, and the
writer makes it a real Hancom caption (it moves and is deleted with its
table or picture).
"""
import re

# Opens a caption: an optional bracket, the kind, a number such as 1, 1-2,
# II-1 or 3.2, then a closing bracket or a separator.
KINDS = r'표|그림|도|사진|도표|Table|Tab\.?|Figure|Fig\.?|Chart|Photo|Image'
OPENING = re.compile(
    rf'^\s*[<〈\[\(「《]?\s*(?P<kind>(?i:{KINDS}))\s*\.?\s*'
    r'(?P<number>(?:[\dIVXⅠ-Ⅻ]+(?:[-.‐–][\dIVXⅠ-Ⅻ]+)*|[A-Z]\d+|[A-Z](?=[.:)])))'
    r'\s*(?:[>〉\]\)」》]|[.:：)]|\s|$)')
TABLE_WORDS = ('표', '도표', 'table', 'tab')
FIGURE_WORDS = ('그림', '도', '사진', 'figure', 'fig', 'chart', 'photo', 'image')


def names_several(text):
    """True when one line holds two captions side by side ('[표 1] ...   [그림 2] ...'):
    each belongs to its own object, which a single paragraph cannot serve."""
    parts = [part for part in re.split(r'\s{3,}|	', (text or '').strip()) if part]
    return sum(1 for part in parts if OPENING.match(part)) > 1


def caption_kind(text):
    """'table' or 'figure' when text opens like a caption, else None."""
    match = OPENING.match(text or '')
    if match is None:
        return None
    word = match.group('kind').lower().rstrip('.')
    if word in TABLE_WORDS:
        return 'table'
    return 'figure' if word in FIGURE_WORDS else None


def _area(block, width, height):
    x0, y0, x1, y1 = block['bbox']
    return x0 * width / 1000, y0 * height / 1000, x1 * width / 1000, y1 * height / 1000


def attach_captions(blocks, width, height):
    """Move caption paragraphs into the table or picture they belong to.

    The object block takes `caption`: side TOP or BOTTOM, the paragraph,
    the gap between them in points and the object's own bbox; its `bbox`
    grows to cover both, as the Hancom object does. Returns the number
    attached. Blocks are changed in place.
    """
    objects = [block for block in blocks
               if block['kind'] == 'table' or (block['kind'] == 'image' and not block.get('placement'))]
    taken, attached = set(), 0
    for paragraph in list(blocks):
        if (paragraph['kind'] != 'paragraph' or paragraph.get('placement') or paragraph.get('line_count', 1) > 3
                or len(paragraph.get('text', '').strip()) > 300):
            continue
        kind = caption_kind(paragraph.get('text', ''))
        if kind is None or names_several(paragraph['text']):
            continue
        p0, p1, p2, p3 = _area(paragraph, width, height)
        size = paragraph.get('line_size_pt') or paragraph.get('font_size_pt', 10)
        reach = max(2.5 * size, 28)
        best = None
        for target in objects:
            if id(target) in taken or target.get('column', 0) != paragraph.get('column', 0):
                continue
            t0, t1, t2, t3 = _area(target, width, height)
            # Inside the object's width (a caption is never wider than what
            # it names) and close above or below it.
            if p0 < t0 - .1 * (t2 - t0) or p2 > t2 + .1 * (t2 - t0) or p2 - p0 < 1:
                continue
            if 0 <= t1 - p3 <= reach:
                side, gap = 'TOP', t1 - p3
            elif 0 <= p1 - t3 <= reach:
                side, gap = 'BOTTOM', p1 - t3
            else:
                continue
            # A caption naming a table prefers tables, one naming a figure
            # prefers pictures.
            rank = (0 if (target['kind'] == 'table') == (kind == 'table') else 1, gap)
            if best is None or rank < best[0]:
                best = (rank, target, side, gap)
        if best is None:
            continue
        _, target, side, gap = best
        taken.add(id(target))
        own = list(target['bbox'])
        target['caption'] = dict(side=side, gap_pt=round(gap, 2), paragraph=paragraph, object=dict(bbox=own))
        target['bbox'] = [min(own[0], paragraph['bbox'][0]), min(own[1], paragraph['bbox'][1]),
                          max(own[2], paragraph['bbox'][2]), max(own[3], paragraph['bbox'][3])]
        blocks.remove(paragraph)
        attached += 1
    return attached


def detach_captions(blocks):
    """The blocks as they were before attach_captions (for free layouts,
    where every caption is a text box at its own place)."""
    result = []
    for block in blocks:
        caption = block.get('caption')
        if caption is None:
            result.append(block)
            continue
        rest = {key: value for key, value in block.items() if key != 'caption'}
        rest['bbox'] = list(caption['object']['bbox'])
        result.extend([rest, caption['paragraph']])
    return result

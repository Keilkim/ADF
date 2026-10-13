"""Measure a PDF page: text rows, paragraphs, character style, ruled edges and fills."""
from __future__ import annotations

import re
from statistics import median

import pymupdf

from ..text_groups import restore_text_spacing
from .font_metrics import glyph_width, installed, registered_family


def clean_text(value):
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]', '', value)


def color(value):
    if isinstance(value, int):
        return f'#{value & 0xffffff:06X}'
    channels = tuple(value or (0, 0, 0))
    if len(channels) == 1:
        channels *= 3
    return '#' + ''.join(f'{max(0, min(255, round(channel * 255))):02X}' for channel in channels[:3])


# Hancom finds these families only by their Korean names; under the PDF
# (PostScript) name it substitutes another font with different widths.
KOREAN_FONTS = {
    'HCRBatang': '함초롬바탕', 'HCRDotum': '함초롬돋움', 'Haansoft Batang': '한컴바탕', 'Haansoft Dotum': '한컴돋움',
    'Batang': '바탕', 'BatangChe': '바탕체', 'Gungsuh': '궁서', 'GungsuhChe': '궁서체',
    'Dotum': '돋움', 'DotumChe': '돋움체', 'Gulim': '굴림', 'GulimChe': '굴림체',
    'MalgunGothic': '맑은 고딕', 'Malgun Gothic': '맑은 고딕',
    'NanumGothic': '나눔고딕', 'NanumMyeongjo': '나눔명조', 'NanumBarunGothic': '나눔바른고딕',
    'HYGothic-Medium': 'HY중고딕', 'HYGoThic-Medium': 'HY중고딕', 'HYHeadLine-Medium': 'HY헤드라인M',
    'HYSinMyeongJo-Medium': 'HY신명조', 'HYGraphic-Medium': 'HY그래픽M', 'HYMyeongJo-Extra': 'HY견명조',
    'HYGothic-Extra': 'HY견고딕', 'HYGungSo-Bold': 'HY궁서B',
}


# PostScript names of Hanyang (HY) fonts that Hancom ships.
HANYANG_FONTS = {
    'H2GTRM': 'HY중고딕', 'H2GTRE': 'HY견고딕', 'H2HDRM': 'HY헤드라인M', 'H2MJRE': 'HY견명조', 'H2MJSM': 'HY신명조',
    'H2GPRM': 'HY그래픽M', 'H2GSRB': 'HY궁서B', 'H2PORL': 'HY엽서L', 'H2PORM': 'HY엽서M',
}
STYLE_SUFFIX = re.compile(r'[-,]?(Regular|BoldItalic|BoldOblique|Bold|Italic|Oblique|Roman|Medium|Light)$')


def _known(name):
    for table in (KOREAN_FONTS, HANYANG_FONTS):
        found = next((value for key, value in table.items() if key.lower() == name.lower()), None)
        if found:
            return found
    if name.startswith('Helvetica') or name in {'ArialMT', 'Arial-BoldMT', 'Arial-ItalicMT'}:
        return 'Arial'
    if name.startswith(('Times-', 'TimesNewRoman')):
        return 'Times New Roman'
    if name.startswith('Courier'):
        return 'Courier New'
    if name in {'Gothic', 'Droid Sans Fallback', 'Heiti', 'Mincho'}:
        return '맑은 고딕'
    return None


def font_name(value, serif=False):
    """The font family name Hancom knows for a PDF font name."""
    name = re.sub(r'^[A-Z]{6}\+', '', value or '')
    try:
        # Korean names written as CP949 bytes read like 'ÈÞ¸Õ¸íÁ¶' (휴먼명조).
        decoded = name.encode('latin-1').decode('cp949')
        if decoded != name and re.search('[가-힣]', decoded):
            name = decoded
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    # CID font names carry their encoding: YDIYGO120-KSCPC-EUC-H.
    name = re.sub(r'-(KSC|UniKS|KSCms|KSCpc|UniCNS|UniJIS|Identity)[-\w]*$', '', name, flags=re.IGNORECASE)
    weight = re.search(r'[-,](Medium|Light|Thin|SemiBold|ExtraBold|ExtraLight|Black|Heavy)$', name)
    if weight and registered_family(name):
        return registered_family(name)
    for candidate in (name, STYLE_SUFFIX.sub('', name)):
        found = _known(candidate)
        if found:
            return found if installed(found) is not False or found in HANYANG_FONTS.values() else (
                '바탕' if serif else '맑은 고딕')
    name = STYLE_SUFFIX.sub('', name)
    # Unnamed embedded fonts (T5, F1, Type3) and fonts this PC lacks would
    # be replaced by Hancom's default; a common installed family of the
    # same kind keeps the page looking similar and measurable.
    if (not name or re.fullmatch(r'(T|F|C|TT)\d+|Type3|unnamed', name, flags=re.IGNORECASE)
            or installed(name) is False):
        return '바탕' if serif else '맑은 고딕'
    return name


def span_style(span):
    # PDF text matrices leave sizes like 14.03pt; Hancom sizes are set in
    # tenths, and the extra width can move a word to the next line.
    flags, font = span.get('flags', 0), span.get('font') or ''
    return dict(font=font_name(font, serif=bool(flags & 4)), font_size_pt=max(1., min(144., round(span.get('size', 10), 2))),
                bold=bool(flags & 16) or bool(re.search(r'Bold|Black|Heavy', font)), italic=bool(flags & 2),
                color=color(span.get('color', 0)))


LIST_MARKER = re.compile(r'\s*(?:[-–—*※○●◦■□▪▫▴▸►◆◇•➊-➓❶-❿①-⑳]|\d{1,2}[.)]\s|\(\d{1,2}\)|[a-zA-Z][.)]\s)')


def letter_spacing(spans, size):
    """Hancom letter spacing (자간): advance change relative to glyph width.

    The glyph width is the installed font's, which Hancom draws with, and
    otherwise the width the PDF declares for its embedded font.
    """
    ratios = []
    for span in spans:
        chars = span['chars']
        names = (span['font'], font_name(span['font'])) if span.get('font') else ()
        for char, following in zip(chars, chars[1:]):
            width = next((value for value in (glyph_width(name, char['c'], size) for name in names) if value),
                         char['bbox'][2] - char['bbox'][0])
            advance = following['origin'][0] - char['origin'][0]
            if not char['c'].isspace() and not following['c'].isspace() and width > .2 * size:
                ratios.append(advance / width - 1)
    if len(ratios) < 3:
        return None
    # Hancom justifies by widening spaces only, so a changed advance
    # between letters is letter spacing set in the source document.
    value = round(median(ratios) * 100)
    return max(-50, min(50, value)) if value else None


def _percent(pitch, size):
    return max(50, min(500, round(pitch / size * 100)))


def _row_text(row):
    return ''.join(run['text'] for run in row['runs'])


def _continues(group, row, region):
    last = group[-1]
    size = last['size']
    pitch = row['baseline'] - last['baseline']
    limit = 1.25 * median(b['baseline'] - a['baseline'] for a, b in zip(group, group[1:])) if len(group) > 1 else 2 * size
    left, right = last['box'].x0 - region.x0, region.x1 - last['box'].x1
    if len(group) == 1 and left > .3 * size and abs(left - right) <= max(.6 * size, .08 * (left + right)):
        return False  # a centred line, such as a heading or a centred cell
    return (last['box'].x1 >= region.x1 - max(1.2 * size, 3)
            and abs(row['size'] - size) <= .6 and .8 * size <= pitch <= limit
            and row['box'].x0 - region.x0 <= 4 * size
            and (len(group) == 1 or abs(row['box'].x0 - group[1]['box'].x0) <= .6 * size)
            and _same_left_edge(group[0], row, size)
            and not LIST_MARKER.match(_row_text(row)))


def _same_left_edge(first, row, size):
    """Wrapped lines share a left edge, apart from a first-line indent or a
    hanging indent after a list marker; centred lines of different widths
    do not, and must stay separate paragraphs."""
    shift = row['box'].x0 - first['box'].x0
    text = _row_text(first)
    return (abs(shift) <= .6 * size or (shift > 0 and bool(LIST_MARKER.match(text)))
            or (shift < 0 and text[:1].isspace()))


def _paragraph(group, region):
    runs, inside_word = [], False
    for row in group:
        row_runs = [dict(run) for run in row['runs']]
        if runs:
            inside_word |= bool(re.search(r'[가-힣]$', runs[-1]['text'])
                                and re.match(r'[가-힣]', row_runs[0]['text']))
            # Korean lines wrap inside words or keep the trailing space;
            # only Latin words lose the space that the line break replaced.
            previous, following = runs[-1]['text'], row_runs[0]['text']
            if re.search(r'[A-Za-z0-9,.;:]$', previous) and re.match(r'[A-Za-z0-9(]', following):
                runs[-1]['text'] = previous + ' '
        runs.extend(row_runs)
    merged = []
    for run in runs:
        if merged and {k: v for k, v in merged[-1].items() if k != 'text'} == {k: v for k, v in run.items() if k != 'text'}:
            merged[-1]['text'] += run['text']
        else:
            merged.append(run)
    weights = {}
    for row in group:
        for run in row['runs']:
            weights[run['font_size_pt']] = weights.get(run['font_size_pt'], 0) + len(run['text'].strip())
    size = max(weights, key=weights.get)
    main = max((run for run in merged if run['font_size_pt'] == size), key=lambda run: len(run['text'].strip()))
    box = pymupdf.Rect(group[0]['box'])
    for row in group[1:]:
        box |= row['box']
    result = {**main, 'text': ''.join(run['text'] for run in merged), 'runs': merged, 'line_count': len(group),
              'top_pt': box.y0, 'box_pt': [box.x0, box.y0, box.x1, box.y1]}
    # Hancom sizes a line by its largest characters; line spacing and
    # heights are measured against that size, not the most common one.
    largest = max(run['font_size_pt'] for run in merged if run['text'].strip())
    if largest > size + .1:
        result['line_size_pt'] = largest
    if inside_word:
        # The source wrapped Korean text between syllables of one word.
        result['wraps_inside_words'] = True
    first = group[0]['box'].x0
    left, right = first - region.x0, region.x1 - max(row['box'].x1 for row in group)
    if len(group) > 1:
        result['line_spacing_percent'] = _percent(median(b['baseline'] - a['baseline'] for a, b in zip(group, group[1:])),
                                                  result.get('line_size_pt', size))
    if len(group) == 1 and left > .5 * size and abs(left - right) <= max(.6 * size, .08 * (left + right)):
        result['alignment'] = 'CENTER'
    elif len(group) == 1 and left > 3 * size and right < min(left / 3, .6 * size):
        result['alignment'] = 'RIGHT'
    else:
        result['alignment'] = 'JUSTIFY' if len(group) > 1 else 'LEFT'
        # Hancom measures a hanging indent (negative) from the first line
        # and a first-line indent (positive) from the following lines.
        continuation = group[1]['box'].x0 if len(group) > 1 else first
        result['indent_left_pt'] = max(0., min(first, continuation) - region.x0)
        result['left_pt'] = min(first, continuation)  # on the page, for writers with other margins
        result['first_line_indent_pt'] = first - continuation
    return result


def _stacks(rows, rect):
    """Columns of single characters stacked top to bottom (vertical writing)."""
    if len(rows) < 3 or rect.height < 2 * rect.width:
        return None
    if sum(len(_row_text(row).strip()) <= 2 for row in rows) < .8 * len(rows):
        return None
    stacks = []
    for row in sorted(rows, key=lambda row: row['baseline']):
        middle = (row['box'].x0 + row['box'].x1) / 2
        stack = next((item for item in stacks if abs(item['middle'] - middle) <= .6 * row['size']), None)
        if stack is None:
            stacks.append(dict(middle=middle, rows=[row]))
        else:
            stack['rows'].append(row)
    # Vertical lines are read from right to left.
    return sorted(stacks, key=lambda item: -item['middle'])


def _vertical_cell(stacks, rect):
    """A cell written vertically: each stack of characters is one line."""
    paragraphs = []
    for stack in stacks:
        runs = []
        for row in stack['rows']:
            for run in row['runs']:
                text = run['text'].strip()
                if not text:
                    continue
                if runs and {k: v for k, v in runs[-1].items() if k != 'text'} == {k: v for k, v in run.items() if k != 'text'}:
                    runs[-1]['text'] += text
                else:
                    runs.append(dict(run, text=text))
        if runs:
            main = max(runs, key=lambda run: len(run['text']))
            paragraphs.append(dict(main, text=''.join(run['text'] for run in runs), runs=runs, alignment='CENTER',
                                   line_count=1, line_spacing_percent=100))
    # The distance between stacks is the vertical line pitch.
    middles = [stack['middle'] for stack in stacks]
    if len(middles) > 1:
        pitch = median(a - b for a, b in zip(middles, middles[1:]))
        for paragraph in paragraphs:
            paragraph['line_spacing_percent'] = _percent(pitch, paragraph.get('line_size_pt', paragraph['font_size_pt']))
    runs = []
    for paragraph in paragraphs:
        runs.extend(dict(run, text=('\n' if runs and position == 0 else '') + run['text'])
                    for position, run in enumerate(paragraph['runs']))
    main = max(runs, key=lambda run: len(run['text'].strip()))
    return dict(main, text=''.join(run['text'] for run in runs), runs=runs, paragraphs=paragraphs, alignment='CENTER',
                text_direction='VERTICAL', vertical_alignment='CENTER', width_pt=rect.width, height_pt=rect.height,
                margins_pt=dict(left=0., right=0., top=0., bottom=0.))


def fit_cell(cell, paragraphs, height):
    """Keep a cell's text within the PDF row height.

    Hancom adds line spacing below the last line too, and grows the row
    when text needs more room; a few points per row pushed whole tables
    onto the next page. Padding goes first, then extra line spacing.
    """
    margins = cell['margins_pt']
    need = lambda: sum(item.get('line_count', 1) * item.get('line_size_pt', item['font_size_pt'])
                       * item['line_spacing_percent'] / 100
                       for item in paragraphs)
    if need() + margins['top'] + margins['bottom'] <= height:
        return
    room = max(0., height - need())
    margins['top'], margins['bottom'] = min(margins['top'], room), 0.
    if need() > height:
        scale = height / need()
        for item in paragraphs:
            item['line_spacing_percent'] = max(100, int(item['line_spacing_percent'] * scale))


def _without_shadows(pieces):
    """Drop a drop shadow: the same text drawn again slightly offset.

    The darker copy is the text; the lighter one is its shadow.
    """
    def darkness(piece):
        colour = piece['runs'][0].get('color', '#000000')
        return -sum(int(colour[i:i + 2], 16) for i in (1, 3, 5))

    kept = []
    for piece in sorted(pieces, key=darkness, reverse=True):
        text = ''.join(run['text'] for run in piece['runs']).strip()
        if any(text == ''.join(run['text'] for run in other['runs']).strip()
               and abs(piece['box'].x0 - other['box'].x0) < .3 * piece['size']
               and abs(piece['baseline'] - other['baseline']) < .5 * piece['size'] for other in kept):
            continue
        kept.append(piece)
    return kept


def join_rows(row, piece):
    """Append a piece of the same visual line, keeping the gap as spaces.

    Columns of one visual line (date fields, a title and a name in one
    cell) are separate PDF lines; line breaks would stack them.
    """
    size = max(piece['size'], row['size'])
    gap = piece['box'].x0 - row['box'].x1
    previous = row['runs'][-1]['text']
    if not previous[-1:].isspace() and not piece['runs'][0]['text'][:1].isspace():
        previous += ' '
    count = max(0, round(gap / (.5 * size)) - (len(previous) - len(previous.rstrip())))
    row['runs'][-1]['text'] = previous + ' ' * count
    row['runs'].extend(dict(run) for run in piece['runs'])
    row['box'] = pymupdf.Rect(row['box']) | piece['box']
    row['chars'] = row.get('chars', 0) + sum(len(run['text']) for run in piece['runs'])
    row['size'] = max(row['size'], piece['size'])
    row.setdefault('blocks', set()).update(piece.get('blocks', {piece.get('block')}))


def paragraphs_from_rows(rows, region=None):
    """Rebuild wrapped PDF lines into paragraphs that Hancom can rewrap.

    A row continues the previous paragraph only when that row reached the
    right edge, the font size and line pitch agree and the row does not
    start a list item. Measured line pitch and indents are kept so the
    rewrapped paragraph occupies the same lines as the PDF.
    """
    if not rows:
        return []
    if region is None:
        region = pymupdf.Rect(min(row['box'].x0 for row in rows), 0, max(row['box'].x1 for row in rows), 0)
    groups = []
    for row in rows:
        group = groups[-1] if groups else None
        if group and _continues(group, row, region):
            group.append(row)
        else:
            groups.append([row])
    result = [_paragraph(group, region) for group in groups]
    # A single-line paragraph has no internal pitch. Consecutive list or
    # note lines are set as tightly as in the PDF; separated paragraphs
    # receive the page's prevailing ratio later.
    for index, (group, paragraph) in enumerate(zip(groups, result)):
        if 'line_spacing_percent' in paragraph or index + 1 == len(groups):
            continue
        pitch = groups[index + 1][0]['baseline'] - group[-1]['baseline']
        size = paragraph.get('line_size_pt', paragraph['font_size_pt'])
        if .9 * size <= pitch <= 1.9 * size and abs(groups[index + 1][0]['size'] - group[-1]['size']) <= .6:
            paragraph['line_spacing_percent'] = _percent(pitch, size)
    return result


class PdfFormatting:
    def __init__(self, page):
        self.page = page
        flags = pymupdf.TEXTFLAGS_RAWDICT & ~pymupdf.TEXT_PRESERVE_IMAGES
        self.data = restore_text_spacing(page, page.get_text('rawdict', flags=flags, sort=True))
        self.lines = [line for block in self.data['blocks'] for line in block.get('lines', [])]
        self.block_of = {id(line): index for index, block in enumerate(self.data['blocks'])
                         for line in block.get('lines', [])}
        # Bold without a bold font is drawn as filled and stroked glyphs
        # (Hancom exports bold this way); the span flags do not show it.
        self.stroked = set()
        try:
            for trace in page.get_texttrace():
                if trace.get('type') == 1 and (trace.get('linewidth') or 0) > 0:
                    self.stroked.update((round(char[2][0], 1), round(char[2][1], 1)) for char in trace['chars'])
        except Exception:
            pass
        self.edges, self.fills, self.marks = [], [], []
        for drawing in page.get_drawings():
            for item in drawing['items']:
                segments = []
                if item[0] not in ('l', 're'):
                    self.marks.append(pymupdf.Rect(drawing['rect']))
                    continue
                if item[0] == 'l':
                    segments = [(item[1], item[2])]
                elif item[0] == 're':
                    rect = pymupdf.Rect(item[1])
                    segments = [(rect.tl, rect.tr), (rect.tr, rect.br), (rect.br, rect.bl), (rect.bl, rect.tl)]
                    if drawing.get('fill') is not None:
                        self.fills.append((rect, color(drawing['fill'])))
                if 's' not in drawing.get('type', ''):
                    continue
                dashes = [float(v) for v in re.findall(r'[\d.]+', drawing.get('dashes', '').split(']')[0])]
                width = float(drawing.get('width', 1))
                line_type = ('SOLID' if not dashes else 'DASH_DOT_DOT' if len(dashes) >= 6 else
                             'DASH_DOT' if len(dashes) >= 4 else 'DOT' if dashes[0] <= max(width * 1.5, 1) else 'DASH')
                style = dict(type=line_type, width_pt=max(0., width), color=color(drawing.get('color')))
                for a, b in segments:
                    if abs(a.y - b.y) <= .1:
                        self.edges.append(('h', (a.y + b.y) / 2, min(a.x, b.x), max(a.x, b.x), style))
                    elif abs(a.x - b.x) <= .1:
                        self.edges.append(('v', (a.x + b.x) / 2, min(a.y, b.y), max(a.y, b.y), style))

    def has_edge(self, direction, position, lo, hi):
        """Whether a drawn line covers most of a cell side."""
        return any(axis == direction and abs(where - position) <= .8 and min(hi, end) - max(lo, start) >= .75 * (hi - lo)
                   for axis, where, start, end, style in self.edges)

    def is_frame(self, rect):
        """True when the drawings in rect only outline it, like a text box."""
        rect, slack = pymupdf.Rect(rect), 1.5
        area = pymupdf.Rect(rect.x0 - slack, rect.y0 - slack, rect.x1 + slack, rect.y1 + slack)
        edges = [edge for edge in self.edges if area.contains(
            pymupdf.Point(edge[2], edge[1]) if edge[0] == 'h' else pymupdf.Point(edge[1], edge[2]))]
        sides = {('top' if abs(position - rect.y0) <= slack else 'bottom' if abs(position - rect.y1) <= slack else None)
                 if direction == 'h' else
                 ('left' if abs(position - rect.x0) <= slack else 'right' if abs(position - rect.x1) <= slack else None)
                 for direction, position, start, end, style in edges}
        inner = pymupdf.Rect(rect.x0 + slack, rect.y0 + slack, rect.x1 - slack, rect.y1 - slack)
        return (sides == {'top', 'bottom', 'left', 'right'}
                and not any(mark.intersects(inner) for mark in self.marks)
                and not any(fill.intersects(inner) and (fill & rect).get_area() < .9 * rect.get_area()
                            for fill, _ in self.fills))

    def _pieces(self, lines, rect):
        """One piece per PDF line: styled runs, glyph box and baseline."""
        pieces = []
        for line in lines:
            runs, chars = [], []
            for span in line['spans']:
                kept = [char for char in span['chars'] if rect is None or
                        rect.contains(pymupdf.Point((char['bbox'][0] + char['bbox'][2]) / 2,
                                                    (char['bbox'][1] + char['bbox'][3]) / 2))]
                if not kept:
                    continue
                style = span_style(span)
                size = style['font_size_pt']
                spacing = letter_spacing([dict(chars=kept, font=span.get('font', ''))], size)
                if spacing is None and len(kept) < 4:
                    spacing = letter_spacing(line['spans'], size)
                if spacing:
                    style['letter_spacing'] = spacing
                # Hancom draws a space half an em wide unless the style uses
                # the font's own space; Latin fonts have far narrower ones.
                spaces = [following['origin'][0] - char['origin'][0] for char, following in zip(kept, kept[1:])
                          if char['c'] == ' ' and not following['c'].isspace()]
                if spaces and min(spaces) < .42 * size:
                    style['font_space'] = True
                # Bold drawn by stroking can start or end inside a span.
                for char in kept:
                    stroked = (round(char['origin'][0], 1), round(char['origin'][1], 1)) in self.stroked
                    bold = style['bold'] or (stroked if not char['c'].isspace() else None)
                    if runs and runs[-1]['span'] is span and (bold is None or runs[-1]['bold'] == bold):
                        runs[-1]['text'] += char['c']
                    else:
                        runs.append(dict(style, text=char['c'], span=span, bold=bool(bold)))
                chars.append((span, kept))
            for run in runs:
                del run['span']
            if not any(not char['c'].isspace() for _, kept in chars for char in kept):
                continue
            # Leading spaces are kept as text, so they belong to the box;
            # a trailing space may hang past the right margin and does not.
            ordered = [char for _, kept in chars for char in kept]
            while ordered[-1]['c'].isspace():
                ordered.pop()
            box = pymupdf.Rect()
            for char in ordered:
                box |= pymupdf.Rect(char['bbox'])
            span = max(chars, key=lambda item: len(item[1]))[0]
            pieces.append(dict(runs=runs, box=box, size=span_style(span)['font_size_pt'], baseline=span['origin'][1],
                               block=self.block_of.get(id(line))))
        return pieces

    def rows(self, lines, rect=None, keep_columns=False):
        """Visual text rows: pieces sharing a baseline form one row.

        With keep_columns, pieces from different PDF text blocks that are
        far apart stay separate rows, so page columns can be found later.
        """
        clusters = []
        for piece in sorted(_without_shadows(self._pieces(lines, rect)), key=lambda piece: piece['baseline']):
            if clusters and piece['baseline'] - clusters[-1][0]['baseline'] <= .35 * min(piece['size'], clusters[-1][0]['size']):
                clusters[-1].append(piece)
            else:
                clusters.append([piece])
        rows = []
        for cluster in clusters:
            row = None
            for piece in sorted(cluster, key=lambda piece: piece['box'].x0):
                gap = piece['box'].x0 - row['box'].x1 if row else 0
                size = max(piece['size'], row['size']) if row else piece['size']
                if row and gap < 8 * size and (not keep_columns or piece['block'] in row['blocks'] or gap < 1.5 * size):
                    join_rows(row, piece)
                    continue
                row = dict(runs=[dict(run) for run in piece['runs']], box=pymupdf.Rect(piece['box']), size=piece['size'],
                           baseline=piece['baseline'], blocks={piece['block']},
                           chars=sum(len(run['text']) for run in piece['runs']))
                rows.append(row)
        return rows

    def paragraphs(self, lines, rect=None, region=None):
        return paragraphs_from_rows(self.rows(lines, rect), region)

    def text(self, lines, rect=None):
        paragraphs = self.paragraphs(lines, rect, rect)
        if not paragraphs:
            return dict(text='', runs=[], font_size_pt=10, font='맑은 고딕', alignment='LEFT')
        runs = []
        for paragraph in paragraphs:
            paragraph_runs = [dict(run) for run in paragraph['runs']]
            if runs:
                paragraph_runs[0]['text'] = '\n' + paragraph_runs[0]['text']
            runs.extend(paragraph_runs)
        main = max(runs, key=lambda run: len(run['text'].strip()))
        result = {**main, 'text': ''.join(run['text'] for run in runs), 'runs': runs, 'alignment': paragraphs[0]['alignment']}
        if len(paragraphs) > 1:
            result['paragraphs'] = paragraphs
        for key in ('line_spacing_percent', 'indent_left_pt', 'first_line_indent_pt'):
            if key in paragraphs[0] and len(paragraphs) == 1:
                result[key] = paragraphs[0][key]
        return result

    def cell(self, box, fallback):
        rect = pymupdf.Rect(box)
        rows = self.rows(self.lines, rect)
        stacks = _stacks(rows, rect)
        if stacks:
            return _vertical_cell(stacks, rect)
        result = self.text(self.lines, rect)
        if rows:
            # Cell padding is the smallest measured gap. Larger gaps are kept
            # as paragraph indents; a narrow right padding avoids rewrapping.
            size = max(row['size'] for row in rows)
            left = max(0., min(row['box'].x0 for row in rows) - rect.x0)
            right = max(0., rect.x1 - max(row['box'].x1 for row in rows))
            aligned = [result] if 'paragraphs' not in result else result['paragraphs']
            if all(paragraph['alignment'] == 'CENTER' for paragraph in aligned):
                left = right = min(left, right, size * .5)
            else:
                right = min(right, 1.5)
            # Hancom centres cell text vertically by default. Keep the side
            # the PDF text was nearer to, so taller rewrapped text grows
            # into the empty side instead of over the cell border.
            top, bottom = max(0., rows[0]['box'].y0 - rect.y0), max(0., rect.y1 - rows[-1]['box'].y1)
            if abs(top - bottom) <= max(2., .3 * size):
                result['vertical_alignment'] = 'CENTER'
                top = bottom = min(top, bottom, 1.5)
            elif top < bottom:
                result['vertical_alignment'], bottom = 'TOP', 0.
            else:
                result['vertical_alignment'], top = 'BOTTOM', 0.
            result['margins_pt'] = dict(left=left, right=right, top=top, bottom=bottom)
            measured = [paragraph['line_spacing_percent'] for paragraph in aligned if 'line_spacing_percent' in paragraph]
            for paragraph in aligned:
                paragraph.pop('box_pt', None)
                paragraph.setdefault('line_spacing_percent', round(median(measured)) if measured else 100)
                if paragraph.get('indent_left_pt'):
                    paragraph['indent_left_pt'] = max(0., paragraph['indent_left_pt'] - left)
            fit_cell(result, aligned, rect.height)
        if not result['text'] and fallback:
            result['text'] = fallback
        result['width_pt'], result['height_pt'] = rect.width, rect.height
        result['borders'] = []
        for side, axis, coordinate, lo, hi in (
            ('left', 'v', rect.x0, rect.y0, rect.y1), ('right', 'v', rect.x1, rect.y0, rect.y1),
            ('top', 'h', rect.y0, rect.x0, rect.x1), ('bottom', 'h', rect.y1, rect.x0, rect.x1)):
            matches = [(abs(position - coordinate), style) for direction, position, start, end, style in self.edges
                       if direction == axis and abs(position - coordinate) <= .8
                       and min(hi, end) - max(lo, start) >= .75 * (hi - lo)]
            style = min(matches, key=lambda item: item[0])[1] if matches else dict(type='NONE', width_pt=0, color='#000000')
            result['borders'].append(dict(side=side, **style))
        fills = [(area, fill) for area, fill in self.fills
                 if (area & rect).get_area() >= .9 * rect.get_area()]
        result['fill_color'] = fills[-1][1] if fills else None
        return result

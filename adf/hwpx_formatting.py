"""PDF character and cell formatting, and its editable HWPX representation."""
from copy import deepcopy
import json
import re
from statistics import median

import pymupdf

from .text_groups import restore_text_spacing

HP = '{http://www.hancom.co.kr/hwpml/2011/paragraph}'
HH = '{http://www.hancom.co.kr/hwpml/2011/head}'
LINE_WIDTHS = (.1, .12, .15, .2, .25, .3, .4, .5, .6, .7, 1., 1.5, 2., 3., 4., 5.)


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


def font_name(value):
    name = re.sub(r'^[A-Z]{6}\+', '', value or '')
    if name in KOREAN_FONTS:
        return KOREAN_FONTS[name]
    if name.startswith('Helvetica'):
        return 'Arial'
    if name.startswith(('Times-', 'TimesNewRoman')):
        return 'Times New Roman'
    if name.startswith('Courier'):
        return 'Courier New'
    if name in {'Gothic', 'Droid Sans Fallback', 'Heiti', 'Mincho'}:
        return '맑은 고딕'
    name = re.sub(r'[-,](Regular|BoldItalic|Bold|Italic|Roman|Oblique)$', '', name) or '맑은 고딕'
    return KOREAN_FONTS.get(name, name)


def span_style(span):
    # PDF text matrices leave sizes like 14.03pt; Hancom sizes are set in
    # tenths, and the extra width can move a word to the next line.
    return dict(font=font_name(span.get('font')), font_size_pt=max(1., min(144., round(span.get('size', 10), 2))),
                bold=bool(span.get('flags', 0) & 16), italic=bool(span.get('flags', 0) & 2),
                color=color(span.get('color', 0)))


LIST_MARKER = re.compile(r'\s*(?:[-–—*※○●◦■□▪▫▴▸►◆◇•➊-➓❶-❿①-⑳]|\d{1,2}[.)]\s|\(\d{1,2}\)|[a-zA-Z][.)]\s)')


def letter_spacing(spans, size):
    """Hancom letter spacing (자간): advance change relative to glyph width."""
    ratios = []
    for span in spans:
        chars = span['chars']
        for char, following in zip(chars, chars[1:]):
            width = char['bbox'][2] - char['bbox'][0]
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
    return (last['box'].x1 >= region.x1 - max(1.2 * size, 3)
            and abs(row['size'] - size) <= .6 and .8 * size <= pitch <= limit
            and row['box'].x0 - region.x0 <= 4 * size
            and (len(group) == 1 or abs(row['box'].x0 - group[1]['box'].x0) <= .6 * size)
            and not LIST_MARKER.match(_row_text(row)))


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
    if inside_word:
        # The source wrapped Korean text between syllables of one word.
        result['wraps_inside_words'] = True
    first = group[0]['box'].x0
    left, right = first - region.x0, region.x1 - max(row['box'].x1 for row in group)
    if len(group) > 1:
        result['line_spacing_percent'] = _percent(median(b['baseline'] - a['baseline'] for a, b in zip(group, group[1:])), size)
    if len(group) == 1 and left > .5 * size and abs(left - right) <= max(.6 * size, .08 * (left + right)):
        result['alignment'] = 'CENTER'
    elif len(group) == 1 and right < min(left / 3, .6 * size):
        result['alignment'] = 'RIGHT'
    else:
        result['alignment'] = 'JUSTIFY' if len(group) > 1 else 'LEFT'
        # Hancom measures a hanging indent (negative) from the first line
        # and a first-line indent (positive) from the following lines.
        continuation = group[1]['box'].x0 if len(group) > 1 else first
        result['indent_left_pt'] = max(0., min(first, continuation) - region.x0)
        result['first_line_indent_pt'] = first - continuation
    return result


class PdfFormatting:
    def __init__(self, page):
        self.page = page
        flags = pymupdf.TEXTFLAGS_RAWDICT & ~pymupdf.TEXT_PRESERVE_IMAGES
        self.data = restore_text_spacing(page, page.get_text('rawdict', flags=flags, sort=True))
        self.lines = [line for block in self.data['blocks'] for line in block.get('lines', [])]
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

    def rows(self, lines, rect=None):
        """Visual text rows: pieces sharing a baseline form one row."""
        pieces = []
        for line in lines:
            runs, chars = [], []
            for span in line['spans']:
                kept = [char for char in span['chars'] if rect is None or
                        rect.contains(pymupdf.Point((char['bbox'][0] + char['bbox'][2]) / 2,
                                                    (char['bbox'][1] + char['bbox'][3]) / 2))]
                if kept:
                    runs.append(dict(text=''.join(char['c'] for char in kept), **span_style(span)))
                    visible = [char for char in kept if not char['c'].isspace()]
                    if visible and sum((round(char['origin'][0], 1), round(char['origin'][1], 1)) in self.stroked
                                       for char in visible) * 2 > len(visible):
                        runs[-1]['bold'] = True
                    chars.append((span, kept))
                    spacing = letter_spacing([dict(chars=kept)], runs[-1]['font_size_pt'])
                    if spacing is None and len(kept) < 4:
                        spacing = letter_spacing(line['spans'], runs[-1]['font_size_pt'])
                    if spacing:
                        runs[-1]['letter_spacing'] = spacing
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
            pieces.append(dict(runs=runs, box=box, size=span_style(span)['font_size_pt'], baseline=span['origin'][1]))
        clusters = []
        for piece in sorted(pieces, key=lambda piece: piece['baseline']):
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
                if row and gap < 8 * size:
                    # Columns of one visual line (date fields, a title and a
                    # name in one cell) are separate PDF lines. Joining them
                    # with line breaks would stack them on top of each other.
                    previous = row['runs'][-1]['text']
                    if not previous[-1:].isspace() and not piece['runs'][0]['text'][:1].isspace():
                        previous += ' '
                    count = max(0, round(gap / (.5 * size)) - (len(previous) - len(previous.rstrip())))
                    row['runs'][-1]['text'] = previous + ' ' * count
                    row['runs'].extend(piece['runs'])
                    row['box'] |= piece['box']
                    row['chars'] += sum(len(run['text']) for run in piece['runs'])
                    if piece['size'] > row['size']:
                        row['size'] = piece['size']
                    continue
                row = dict(runs=[dict(run) for run in piece['runs']], box=pymupdf.Rect(piece['box']), size=piece['size'],
                           baseline=piece['baseline'], chars=sum(len(run['text']) for run in piece['runs']))
                rows.append(row)
        return rows

    def paragraphs(self, lines, rect=None, region=None):
        """Rebuild wrapped PDF lines into paragraphs that Hancom can rewrap.

        A row continues the previous paragraph only when that row reached the
        right edge, the font size and line pitch agree and the row does not
        start a list item. Measured line pitch and indents are kept so the
        rewrapped paragraph occupies the same lines as the PDF.
        """
        rows = self.rows(lines, rect)
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
            size = paragraph['font_size_pt']
            if .9 * size <= pitch <= 1.9 * size and abs(groups[index + 1][0]['size'] - group[-1]['size']) <= .6:
                paragraph['line_spacing_percent'] = _percent(pitch, size)
        return result

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
                top = bottom = min(top, bottom)
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


def add_runs(doc, paragraph, block, spacing_before_pt=0):
    paragraph.clear_text()
    runs = block.get('runs') or [block]
    for run in runs:
        style = doc.styles.ensure_run(font=run.get('font', block.get('font', '맑은 고딕')),
            size=run.get('font_size_pt', block['font_size_pt']), bold=run.get('bold', block.get('bold', False)),
            italic=run.get('italic', block.get('italic', False)), color=run.get('color', block.get('color', '#000000')),
            # Spacing belongs to each run; None would let python-hwpx reuse
            # a style with any spacing.
            letter_spacing=run.get('letter_spacing') or 0)
        paragraph.add_run(clean_text(run['text']), char_pr_id_ref=style, expand_special_characters=True)
    paragraph_format(doc, paragraph, block, spacing_before_pt)


def _structure(element, skip_id=False):
    attributes = sorted((k, v) for k, v in element.attrib.items() if not (skip_id and k == 'id'))
    return element.tag, attributes, (element.text or '').strip(), [_structure(child) for child in element]


def line_breaking_shape(doc, base_id, inside_words, condense=10):
    """A copy of a paragraph shape that breaks lines the way the source did.

    The PDF shows whether a paragraph's Korean text wrapped between the
    syllables of a word (per-character breaking) or only between words.
    OWPML names per-character breaking KEEP_WORD and word breaking
    BREAK_WORD. A small space condensing lets a justified line keep its
    last word, as the PDF lines do. Formatting derived from this shape
    keeps both settings.
    """
    header = doc.oxml.headers[0]
    container = header.element.find('.//' + HH + 'paraProperties')
    base = next((shape for shape in container if shape.get('id') == str(base_id)), None)
    if base is None:
        return base_id
    shape = deepcopy(base)
    shape.set('condense', str(condense))
    breaking = shape.find(HH + 'breakSetting')
    if breaking is not None:
        breaking.set('breakNonLatinWord', 'KEEP_WORD' if inside_words else 'BREAK_WORD')
    shape.attrib.pop('id')
    wanted = _structure(shape)
    for existing in container:
        if _structure(existing, skip_id=True) == wanted:
            return existing.get('id')
    identity = str(max(int(item.get('id', '0')) for item in container) + 1)
    shape.set('id', identity)
    container.append(shape)
    container.set('itemCnt', str(len(container)))
    header.mark_dirty()
    return identity


def paragraph_format(doc, paragraph, block, spacing_before_pt=0):
    paragraph.para_pr_id_ref = line_breaking_shape(doc, paragraph.para_pr_id_ref, block.get('wraps_inside_words', False))
    # Rounded values keep the number of distinct paragraph shapes small.
    millimeters = lambda points: round(points * 25.4 / 72, 1)
    doc.styles.apply_paragraph_format(paragraphs=[paragraph], alignment=block.get('alignment', 'LEFT'),
        line_spacing_percent=block.get('line_spacing_percent', 160),
        indent_left_mm=millimeters(block.get('indent_left_pt', 0)),
        first_line_indent_mm=millimeters(block.get('first_line_indent_pt', 0)),
        spacing_before_pt=round(spacing_before_pt * 2) / 2, spacing_after_pt=0)


def vertical_spacing(items, start):
    """Space before each item so it starts where the PDF placed it.

    Hancom adds a line's spacing below every line, so a paragraph ends
    line_count pitches below its top. Items are (top_pt, height_pt).
    """
    cursor, result = start, []
    for top, height in items:
        result.append(max(0., top - cursor))
        cursor = max(cursor, top) + height
    return result


def paragraph_height(block):
    size = block['font_size_pt']
    pitch = size * block.get('line_spacing_percent', 160) / 100
    return block.get('line_count', 1) * pitch


def cell_border_fill(doc, cell, cache):
    """Create one independent borderFill for a cell's four distinct edges."""
    key = json.dumps([cell.get('borders'), cell.get('fill_color')], sort_keys=True)
    if key in cache:
        return cache[key]
    template_id = doc.styles.ensure_border_fill(active_borders=[], fill_color=cell.get('fill_color'))
    header = doc.oxml.headers[0]
    container = header.element.find('.//' + HH + 'borderFills')
    template = next(element for element in container if element.get('id') == template_id)
    element = deepcopy(template)
    identity = str(max(int(item.get('id', '0')) for item in container) + 1)
    element.set('id', identity)
    borders = {border['side']: border for border in cell.get('borders', [])}
    for side in ('left', 'right', 'top', 'bottom'):
        border = borders.get(side, dict(type='NONE', width_pt=0, color='#000000'))
        edge = element.find(HH + side + 'Border')
        mm = float(border['width_pt']) * 25.4 / 72
        nearest = min(LINE_WIDTHS, key=lambda value: abs(value - mm))
        width = f'{nearest:.1f}' if nearest in (1., 2., 3., 4., 5.) else str(nearest)
        edge.set('type', border['type'])
        edge.set('width', width + ' mm')
        edge.set('color', border['color'])
    container.append(element)
    container.set('itemCnt', str(len(container)))
    header.mark_dirty()
    cache[key] = identity
    return identity


def apply_cell_format(doc, table, cell, block, cache):
    r, c = cell['row'], cell['col']
    target = table.cell(r, c)
    style = {**block, **cell}
    target.set_text('')
    paragraphs = cell.get('paragraphs') or [style]
    spacing = vertical_spacing([(paragraph.get('top_pt', 0), paragraph_height({**style, **paragraph}))
                                for paragraph in paragraphs], paragraphs[0].get('top_pt', 0))
    for index, (paragraph, before) in enumerate(zip(paragraphs, spacing)):
        add_runs(doc, target.paragraphs[0] if index == 0 else target.add_paragraph(), {**style, **paragraph}, before)
    if 'width_pt' in cell and 'height_pt' in cell:
        target.set_size(round(cell['width_pt'] * 100), round(cell['height_pt'] * 100))
    if cell.get('margins_pt'):
        target.set_margins(**{key: round(value * 100) for key, value in cell['margins_pt'].items()})
    if 'borders' in cell or 'fill_color' in cell:
        table.set_cell_border_fill(r, c, cell_border_fill(doc, cell, cache))
    if cell.get('vertical_alignment'):
        sublist = target.element.find(HP + 'subList')
        if sublist is not None:
            sublist.set('vertAlign', cell['vertical_alignment'])


def reuse_native_formatting(native, extracted):
    """AI transcription keeps measured PDF styles when text/grid matches."""
    for block in extracted['blocks']:
        candidates = [source for source in native['blocks'] if source['kind'] == block['kind']
                      and (pymupdf.Rect(source['bbox']) & pymupdf.Rect(block['bbox'])).get_area()
                      > .5 * min(pymupdf.Rect(source['bbox']).get_area(), pymupdf.Rect(block['bbox']).get_area())]
        if len(candidates) != 1:
            continue
        source = candidates[0]
        if block['kind'] == 'paragraph' and source['text'].strip() == block['text'].strip():
            block.update({key: value for key, value in source.items() if key not in ('kind', 'bbox')})
        elif block['kind'] == 'table' and (source['rows'], source['cols']) == (block['rows'], block['cols']):
            measured = {(cell['row'], cell['col']): cell for cell in source['cells']}
            for cell in block['cells']:
                original = measured.get((cell['row'], cell['col']))
                if original and (original['row_span'], original['col_span']) == (cell['row_span'], cell['col_span']):
                    cell.update({key: value for key, value in original.items() if key not in
                                 ('row', 'col', 'row_span', 'col_span', 'text', 'runs', 'paragraphs')})
                    if original['text'].strip() == cell['text'].strip():
                        cell['runs'] = original['runs']
                        cell['text'] = original['text']
                        if 'paragraphs' in original:
                            cell['paragraphs'] = original['paragraphs']
            for key in ('column_widths_pt', 'row_heights_pt'):
                if key in source:
                    block[key] = source[key]
    return extracted

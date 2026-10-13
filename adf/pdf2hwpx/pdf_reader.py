"""Measure a PDF page: text rows, paragraphs, character style, ruled edges and fills."""
from __future__ import annotations

import re
from statistics import median

import pymupdf

from ..text_groups import restore_text_spacing
from .appearance import PageLook, far, hex_colour
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
    # Hanyang fonts embedded under their maker's name (HaanYGodic24).
    for prefix, family in (('HaanYGodic', 'HY견고딕'), ('HaanYHead', 'HY헤드라인M'), ('HaanYMyungjo', 'HY견명조')):
        if name.startswith(prefix):
            return family
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


def _kind(char):
    return 'wide' if 'ᄀ' <= char <= 'ᇿ' or '　' <= char <= '鿿' or '가' <= char <= '힣'         or '＀' <= char <= '￯' else 'narrow'


def _spacing_by_kind(chars, font, size):
    """Letter spacing measured separately for wide (Korean) and other letters."""
    names = (font, font_name(font)) if font else ()
    ratios = {}
    for char, following in zip(chars, chars[1:]):
        if char['c'].isspace() or following['c'].isspace():
            continue
        width = next((value for value in (glyph_width(name, char['c'], size) for name in names) if value),
                     char['bbox'][2] - char['bbox'][0])
        if width > .2 * size:
            ratios.setdefault(_kind(char['c']), []).append((following['origin'][0] - char['origin'][0]) / width - 1)
    return {kind: max(-50, min(50, round(median(values) * 100))) for kind, values in ratios.items() if len(values) >= 3}


def _percent(pitch, size):
    return max(50, min(500, round(pitch / size * 100)))


def _row_text(row):
    return ''.join(run['text'] for run in row['runs'])


def _body_size(row):
    """The size most of a row's characters have (a bullet may be larger)."""
    weights = {}
    for run in row['runs']:
        weights[run['font_size_pt']] = weights.get(run['font_size_pt'], 0) + len(run['text'].strip())
    return max(weights, key=weights.get) if weights else row['size']


def _continues(group, row, region):
    last = group[-1]
    size = _body_size(last)
    pitch = row['baseline'] - last['baseline']
    limit = 1.25 * median(b['baseline'] - a['baseline'] for a, b in zip(group, group[1:])) if len(group) > 1 else 2 * size
    left, right = last['box'].x0 - region.x0, region.x1 - last['box'].x1
    if len(group) == 1 and left > .3 * size and abs(left - right) <= max(.6 * size, .08 * (left + right)):
        return False  # a centred line, such as a heading or a centred cell
    # A line ends early only when the next line's first word did not fit
    # after it: the next line continues the paragraph (ragged right text).
    text = _row_text(row).strip()
    word = text.split(' ')[0] if text else ''
    advance = row['box'].width / max(1, len(text))
    wrapped = last['box'].x1 + .5 * size + len(word) * advance > region.x1
    return ((last['box'].x1 >= region.x1 - max(1.2 * size, 3) or wrapped)
            and abs(_body_size(row) - size) <= .6 and .8 * size <= pitch <= limit
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
    # Producers that drop the space at a line end drop it on every line;
    # Hancom keeps it, and its lines without one wrapped inside a word.
    stripped = len(group) > 1 and not any(_row_text(row)[-1:].isspace() for row in group[:-1])
    for row in group:
        row_runs = [dict(run) for run in row['runs']]
        if runs:
            previous, following = runs[-1]['text'], row_runs[0]['text']
            if stripped and not previous.endswith('-'):
                runs[-1]['text'] = previous + ' '
            else:
                inside_word |= bool(re.search(r'[가-힣]$', previous) and re.match(r'[가-힣]', following))
                # Latin words always lose the space a line break replaced.
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
    # Hancom puts a line's baseline 0.85 of its largest size below the line
    # top, whatever the font's ascender (Malgun Gothic's glyph boxes reach
    # far above). The paragraph starts where that puts the PDF's baseline.
    if 'baseline' in group[0]:
        box.y0 = min(box.y1 - 1, group[0]['baseline'] - HANCOM_ASCENT * group[0]['size'])
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
        # Justified lines end together; ragged ones are left aligned.
        ends = [row['box'].x1 for row in group[:-1]]
        result['alignment'] = 'JUSTIFY' if ends and max(ends) - min(ends) <= .5 * size and (
            len(ends) > 1 or ends[0] >= region.x1 - max(1.2 * size, 3)) else 'LEFT'
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
    if sum(len(_row_text(row).replace(' ', '')) <= 2 for row in rows) < .8 * len(rows):
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
            # In vertical writing letters advance downwards; their PDF pitch
            # becomes the letter spacing.
            baselines = [row['baseline'] for row in stack['rows']]
            if len(baselines) > 1:
                pitch = median(b - a for a, b in zip(baselines, baselines[1:]))
                for run in runs:
                    run['letter_spacing'] = max(-50, min(50, round((pitch / run['font_size_pt'] - 1) * 100)))
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


def _mostly_centred(rows, rect):
    """Whether most text rows in a cell sit centred between its sides."""
    if len(rows) < 2:
        return False
    centred = sum(abs((row['box'].x0 - rect.x0) - (rect.x1 - row['box'].x1)) <= max(1.2 * row['size'], 3)
                  for row in rows)
    # Centred lines of different lengths start at different places; lines
    # that share a left edge are left aligned however full they are.
    starts = [row['box'].x0 for row in rows]
    return centred >= .6 * len(rows) and max(starts) - min(starts) > max(row['size'] for row in rows)


def line_width(runs):
    """Width Hancom gives a line of runs, or None if a font is unknown."""
    total = 0.
    for run in runs:
        size, scale = run['font_size_pt'], 1 + (run.get('letter_spacing') or 0) / 100
        for char in run['text']:
            if char == ' ':
                own = glyph_width(run['font'], ' ', size)
                width = own if run.get('font_space') and own else size / 2
            else:
                width = glyph_width(run['font'], char, size)
                if width is None:
                    return None
            total += width * scale
    return total


def fit_line(paragraph, room):
    """Condense a one-line paragraph that Hancom would wrap in its room.

    The PDF line fitted; small width differences (a space, rounding) must
    not wrap it in a narrow table cell, which would make the row taller.
    """
    runs = paragraph['runs']
    width = line_width(runs)
    room *= .97  # Hancom rounds glyph positions; a line filling its cell exactly still wraps
    if width is None or width <= room or room <= 0:
        return
    excess = (width - room) / width
    for run in runs:
        run['letter_spacing'] = max(-50, round((run.get('letter_spacing') or 0) - excess * 100 - .5))


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
            item['line_spacing_percent'] = max(70, int(item['line_spacing_percent'] * scale))


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


def _join_dots(edges):
    """Join runs of very short collinear segments into dotted lines.

    Some producers draw a dotted border as hundreds of tiny strokes; a
    table finder and cell borders need it as one line.
    """
    short = [edge for edge in edges if edge[3] - edge[2] < 3]
    if len(short) < 10:
        return edges
    joined, groups = [edge for edge in edges if edge[3] - edge[2] >= 3], {}
    for edge in short:
        groups.setdefault((edge[0], round(edge[1], 1)), []).append(edge)
    for (axis, position), group in groups.items():
        group.sort(key=lambda edge: edge[2])
        start, end, count = group[0][2], group[0][3], 1
        for edge in group[1:] + [None]:
            if edge is not None and edge[2] - end <= 4:
                end, count = max(end, edge[3]), count + 1
                continue
            if count >= 5 and end - start >= 20:
                joined.append((axis, position, start, end, dict(group[0][4], type='DOT')))
            else:
                joined.extend(item for item in group if start <= item[2] and item[3] <= end)
            if edge is not None:
                start, end, count = edge[2], edge[3], 1
    return joined


HANCOM_ASCENT = .85


def space_width(run):
    """Width Hancom gives one space in run: half an em or the font's own
    space, changed by the run's letter spacing."""
    own = glyph_width(run['font'], ' ', run['font_size_pt']) if run.get('font_space') else None
    return max(.5, (own or .5 * run['font_size_pt']) * (1 + (run.get('letter_spacing') or 0) / 100))


def join_rows(row, piece):
    """Append a piece of the same visual line, keeping the gap as spaces.

    Columns of one visual line (date fields, a title and a name in one
    cell) are separate PDF lines; line breaks would stack them.
    """
    gap = piece['box'].x0 - row['box'].x1
    carrier = row['runs'][-1]
    previous = carrier['text']
    if not previous[-1:].isspace() and not piece['runs'][0]['text'][:1].isspace():
        previous += ' '
    # The spaces take the width Hancom gives them in the run that holds them.
    count = max(0, round(gap / space_width(carrier)) - (len(previous) - len(previous.rstrip())))
    row['runs'][-1]['text'] = previous + ' ' * count
    # A piece on another baseline (a small label beside a title) keeps its
    # height through Hancom's character offset, a share of its size.
    shift = piece['baseline'] - row['baseline']
    for run in piece['runs']:
        run = dict(run)
        if abs(shift) > .15 * run['font_size_pt']:
            run['baseline_shift'] = max(-100, min(100, round(shift / run['font_size_pt'] * 100)))
        row['runs'].append(run)
    row['box'] = pymupdf.Rect(row['box']) | piece['box']
    row['chars'] = row.get('chars', 0) + sum(len(run['text']) for run in piece['runs'])
    row['size'] = max(row['size'], piece['size'])
    row.setdefault('blocks', set()).update(piece.get('blocks', {piece.get('block')}))


def paragraphs_from_rows(rows, region=None, merge=True):
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
        if merge and group and _continues(group, row, region):
            group.append(row)
        else:
            groups.append([row])
    result = [_paragraph(group, region) for group in groups]
    # Consecutive lines that end together but start apart are right aligned.
    start = 0
    while start < len(groups):
        end = start
        while (end + 1 < len(groups) and len(groups[end + 1]) == 1 and len(groups[start]) == 1
               and abs(groups[end + 1][0]['box'].x1 - groups[start][0]['box'].x1) <= 1):
            end += 1
        lefts = [groups[index][0]['box'].x0 for index in range(start, end + 1)]
        if end > start and max(lefts) - min(lefts) > groups[start][0]['size']:
            for index in range(start, end + 1):
                result[index]['alignment'] = 'RIGHT'
                for key in ('indent_left_pt', 'first_line_indent_pt'):
                    result[index].pop(key, None)
        start = end + 1
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
        elif pitch < .9 * size:
            # Smaller text tucked under a large line (a subtitle): the line
            # may take only the room down to it.
            room = groups[index + 1][0]['box'].y0 - group[-1]['box'].y0
            paragraph['line_spacing_percent'] = max(70, min(160, round(room / size * 100)))
    return result


def _serif(span):
    """Whether a font has serifs by its name; None when the name does not say."""
    raw = re.sub(r'^[A-Z]{6}\+', '', span.get('font', ''))
    try:
        raw += ' ' + raw.encode('latin-1').decode('cp949')  # 'ÈÞ¸Õ¸íÁ¶' is 휴먼명조
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    if re.search(r'Gothic|Godic|Dotum|Gulim|Sans|Malgun|GO\d|고딕|돋움|굴림', raw, re.IGNORECASE):
        return False
    if re.search(r'Myeong|Myungjo|Batang|Mincho|Serif|Times|명조|바탕|궁서', raw, re.IGNORECASE):
        return True
    return None


SERIF_CANDIDATES = ('바탕', '함초롬바탕', 'HY신명조', 'HY견명조')
SANS_CANDIDATES = ('맑은 고딕', '굴림', '함초롬돋움', 'HY중고딕', 'HY견고딕', 'HY헤드라인M')


class PdfFormatting:
    def __init__(self, page):
        self.page = page
        flags = pymupdf.TEXTFLAGS_RAWDICT & ~pymupdf.TEXT_PRESERVE_IMAGES
        self.data = restore_text_spacing(page, page.get_text('rawdict', flags=flags, sort=True))
        self.lines = [line for block in self.data['blocks'] for line in block.get('lines', [])]
        # Symbols in the Private Use Area only exist in the PDF's own font.
        # The text keeps their room with an ideographic space; the symbol
        # itself is placed as a picture where it was (see extract).
        self.symbols = []
        for line in self.lines:
            for span in line['spans']:
                for char in span['chars']:
                    if '' <= char['c'] <= '':
                        self.symbols.append(pymupdf.Rect(char['bbox']))
                        char['c'] = '　'
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
        self.look = PageLook(page)
        self.substitutes = self._substitutes()
        self.font_space = self._font_spaces()
        self.edges, self.fills, self.marks = [], [], []
        for drawing in page.get_drawings():
            # A filled rectangle drawn as a closed path of straight sides.
            if (drawing.get('fill') is not None and len(drawing['items']) >= 3
                    and all(item[0] == 'l' and (abs(item[1].x - item[2].x) <= .1 or abs(item[1].y - item[2].y) <= .1)
                            for item in drawing['items'])):
                self.fills.append((pymupdf.Rect(drawing['rect']), color(drawing['fill'])))
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
        self.edges = _join_dots(self.edges)

    def _substitutes(self):
        """For fonts this PC lacks, the installed font of closest widths.

        The PDF declares each glyph's width; the candidate whose widths
        match the page's characters best keeps lines the same length.
        """
        samples, serif = {}, {}
        for line in self.lines:
            for span in line['spans']:
                serif[span['font']] = _serif(span)
                for char in span['chars']:
                    if not char['c'].isspace():
                        samples.setdefault(span['font'], []).append(
                            (char['c'], (char['bbox'][2] - char['bbox'][0]) / max(1., span['size'])))
        result = {}
        for font, chars in samples.items():
            mapped = font_name(font, bool(serif[font]))
            if installed(font) is not False or len(chars) < 5 or (
                    mapped not in ('바탕', '맑은 고딕') and installed(mapped) is not False):
                continue
            # PDF serif flags are often wrong (design tools); without a hint
            # in the name both kinds compete on widths.
            candidates = {True: SERIF_CANDIDATES, False: SANS_CANDIDATES}.get(
                serif[font], SANS_CANDIDATES + SERIF_CANDIDATES)
            scores = []
            for candidate in candidates:
                widths = [(glyph_width(candidate, char, 1), width) for char, width in chars[:400]]
                if all(found is not None for found, _ in widths):
                    scores.append((sum(abs(found - width) for found, width in widths), candidate))
            if scores:
                result[font] = min(scores)[1]
        return result

    def _font_spaces(self):
        """Fonts whose text uses the font's own space width, per page.

        Hancom draws a space half an em wide unless a style uses the font's
        own space. Justified lines stretch spaces; unjustified lines (last
        lines, headings) show the font's own width exactly. Without the
        font installed, spaces clearly narrower than half an em tell.
        The producer named in the PDF does not tell: Hancom documents are
        re-saved by other programs, and Hancom styles may use either.
        """
        spaces, spacing = {}, {}
        measured = [(span, letter_spacing([span], max(1., span['size']))) for line in self.lines for span in line['spans']]
        for span, value in measured:
            if value is not None:
                spacing.setdefault(span['font'], []).append(value)
        # A style condensed throughout gives every span the same spacing;
        # spans too short to measure take it. Spacing set on a few lines
        # only is not spread to others.
        self.usual_spacing = {}
        for font, values in spacing.items():
            middle = median(values)
            if len(values) >= 3 and middle and sum(abs(value - middle) <= 2 for value in values) >= .7 * len(values):
                self.usual_spacing[font] = round(middle)
        for span, value in measured:
            size = max(1., span['size'])
            pairs = [(char, following) for char, following in zip(span['chars'], span['chars'][1:])
                     if char['c'] == ' ' and not following['c'].isspace()]
            if not pairs:
                continue
            # Hancom applies letter spacing to spaces too; remove it to
            # compare the space itself. A short span takes its font's usual.
            if value is None:
                value = median(spacing[span['font']]) if span['font'] in spacing else 0
            scale = 1 + value / 100
            spaces.setdefault(span['font'], []).extend(
                (following['origin'][0] - char['origin'][0]) / size / scale for char, following in pairs)
        result = {}
        for font, ratios in spaces.items():
            own = next((width for width in (glyph_width(name, ' ', 1) for name in (font, font_name(font))) if width), None)
            if own is not None and own < .45:
                # Unjustified spaces sit exactly at the font's width;
                # Hancom's condensed spaces spread between it and half an em.
                result[font] = sum(abs(ratio - own) <= .02 for ratio in ratios) >= 3
            else:
                result[font] = min(ratios) < .42
        return result

    def has_edge(self, direction, position, lo, hi):
        """Whether a drawn line covers most of a cell side."""
        return any(axis == direction and abs(where - position) <= .8 and min(hi, end) - max(lo, start) >= .75 * (hi - lo)
                   for axis, where, start, end, style in self.edges)

    def frame_style(self, rect):
        """Line style of the top side of a frame, or None."""
        rect = pymupdf.Rect(rect)
        found = [edge for edge in self.edges if edge[0] == 'h' and abs(edge[1] - rect.y0) <= 1.5]
        return dict(max(found, key=lambda edge: edge[3] - edge[2])[4]) if found else None

    def is_frame(self, rect, contents=False):
        """True when the drawings in rect only outline it, like a text box.

        With contents, other drawings inside (a framed page's tables) are
        allowed: only the four sides are required.
        """
        rect, slack = pymupdf.Rect(rect), 1.5
        area = pymupdf.Rect(rect.x0 - slack, rect.y0 - slack, rect.x1 + slack, rect.y1 + slack)
        edges = [edge for edge in self.edges if area.contains(
            pymupdf.Point(edge[2], edge[1]) if edge[0] == 'h' else pymupdf.Point(edge[1], edge[2]))]
        sides = {('top' if abs(position - rect.y0) <= slack else 'bottom' if abs(position - rect.y1) <= slack else None)
                 if direction == 'h' else
                 ('left' if abs(position - rect.x0) <= slack else 'right' if abs(position - rect.x1) <= slack else None)
                 for direction, position, start, end, style in edges}
        inner = pymupdf.Rect(rect.x0 + slack, rect.y0 + slack, rect.x1 - slack, rect.y1 - slack)
        if contents:
            return {'top', 'bottom', 'left', 'right'} <= sides
        return (sides == {'top', 'bottom', 'left', 'right'}
                and not any(mark.intersects(inner) for mark in self.marks)
                and not any(fill.intersects(inner) and (fill & rect).get_area() < .9 * rect.get_area()
                            for fill, _ in self.fills))

    def _highlights(self, line):
        """Fills behind some words of a line (a highlighter), with colours.

        A fill holding the whole line is a cell or box background instead.
        """
        box = pymupdf.Rect(line['bbox'])
        marks = [(fill, colour) for fill, colour in getattr(self, 'fills', [])
                 if fill.intersects(box) and not fill.contains(box) and fill.height <= 2 * box.height
                 and (fill & box).height > .6 * box.height and colour.upper() != '#FFFFFF']
        if not marks:
            return []
        # Only shading that differs from what is around the line shows.
        around = self.look.surround(box)
        return [(fill, colour) for fill, colour in marks
                if around is None or max(abs(int(colour[1 + 2 * i:3 + 2 * i], 16) - around[i]) for i in range(3)) >= 20]

    def _pieces(self, lines, rect):
        """One piece per PDF line: styled runs, glyph box and baseline."""
        pieces = []
        for line in lines:
            runs, chars = [], []
            marks = self._highlights(line)
            for span in line['spans']:
                # Characters the writer drops (control codes from broken
                # encodings) must not widen the line's box either.
                kept = [char for char in span['chars'] if clean_text(char['c']) and (rect is None or
                        rect.contains(pymupdf.Point((char['bbox'][0] + char['bbox'][2]) / 2,
                                                    (char['bbox'][1] + char['bbox'][3]) / 2)))]
                if not kept:
                    continue
                style = span_style(span)
                style['font'] = self.substitutes.get(span.get('font'), style['font'])
                size = style['font_size_pt']
                # The rendering decides the colour when the text data
                # reports a colour other than the one drawn.
                boxes = [pymupdf.Rect(char['bbox']) for char in kept if not char['c'].isspace()]
                if boxes:
                    area = pymupdf.Rect(boxes[0])
                    for box in boxes[1:]:
                        area |= box
                    seen = self.look.ink_colour(area)
                    reported = tuple(int(style['color'][i:i + 2], 16) for i in (1, 3, 5))
                    if seen is not None and far(seen, reported):
                        style['color'] = hex_colour(seen)
                spacing = letter_spacing([dict(chars=kept, font=span.get('font', ''))], size)
                if spacing is None and len(kept) < 4:
                    spacing = letter_spacing(line['spans'], size)
                if spacing is None:
                    spacing = self.usual_spacing.get(span.get('font'))
                if spacing:
                    style['letter_spacing'] = spacing
                if self.font_space.get(span.get('font')):
                    style['font_space'] = True
                # Korean and other letters are measured apart: a substitute
                # font matches the source's Hangul widths better than its
                # Latin, digit and punctuation widths.
                by_kind = _spacing_by_kind(kept, span.get('font', ''), size)
                # Bold drawn by stroking can start or end inside a span.
                if runs and kept and chars:
                    # Between spans too, a gap made by positioning alone is
                    # kept as spaces.
                    last = chars[-1][1][-1]
                    gap = kept[0]['origin'][0] - last['bbox'][2]
                    if not last['c'].isspace() and not kept[0]['c'].isspace() and gap > .4 * size:
                        runs[-1]['text'] += ' ' * max(1, round(gap / space_width(runs[-1])))
                for index, char in enumerate(kept):
                    stroked = (round(char['origin'][0], 1), round(char['origin'][1], 1)) in self.stroked
                    bold = style['bold'] or (stroked if not char['c'].isspace() else None)
                    text = char['c']
                    following = kept[index + 1] if index + 1 < len(kept) else None
                    if following is not None and not text.isspace() and not following['c'].isspace():
                        # A gap made by positioning alone (no space character)
                        # is kept as spaces, or the words would touch.
                        gap = following['origin'][0] - char['bbox'][2]
                        if gap > .4 * size:
                            text += ' ' * max(1, round(gap / space_width(style)))
                    elif following is not None and text == ' ':
                        # One space drawn as wide as several (a label and its
                        # value) keeps its width; justified spaces stay one.
                        width = following['origin'][0] - char['origin'][0]
                        if width >= max(.7 * size, 2 * space_width(style)):
                            text = ' ' * round(width / space_width(style))
                    spacing = by_kind.get(_kind(char['c']), style.get('letter_spacing')) if not text.isspace() else None
                    centre = pymupdf.Point((char['bbox'][0] + char['bbox'][2]) / 2, (char['bbox'][1] + char['bbox'][3]) / 2)
                    shade = next((colour for mark, colour in marks if mark.contains(centre)), None)
                    if (runs and runs[-1]['span'] is span and (bold is None or runs[-1]['bold'] == bold)
                            and (spacing is None or runs[-1].get('letter_spacing') == spacing)
                            and runs[-1].get('highlight') == shade):
                        runs[-1]['text'] += text
                    else:
                        run = dict(style, text=text, span=span, bold=bool(bold))
                        if shade:
                            run['highlight'] = shade
                        if spacing is not None:
                            run['letter_spacing'] = spacing
                        if not run.get('letter_spacing'):
                            run.pop('letter_spacing', None)
                        runs.append(run)
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
        rows = self.rows(lines, rect)
        centred = rect is not None and _mostly_centred(rows, rect)
        paragraphs = paragraphs_from_rows(rows, rect, merge=not centred)
        if centred:
            # A cell of centred lines keeps its lines: rewrapping them as one
            # justified paragraph spreads short lines across the cell.
            for paragraph in paragraphs:
                paragraph['alignment'] = 'CENTER'
                paragraph.pop('indent_left_pt', None)
                paragraph.pop('first_line_indent_pt', None)
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
        # Vertical writing is judged on the PDF's own pieces: letters of two
        # vertical lines at one height would join into one row.
        stacks = _stacks(self._pieces(self.lines, rect), rect)
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
            inner = rect.width - result['margins_pt']['left'] - result['margins_pt']['right'] - .5
            for paragraph in aligned:
                if paragraph.get('line_count', 1) == 1:
                    fit_line(paragraph, inner - paragraph.get('indent_left_pt', 0))
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
        if result['fill_color'] is None:
            # Gradients drawn as many thin lines have no single fill.
            inner = pymupdf.Rect(rect.x0 + 1, rect.y0 + 1, rect.x1 - 1, rect.y1 - 1)
            seen = self.look.background(inner) if not inner.is_empty else None
            if seen is not None and sum(255 - value for value in seen) > 30:
                result['fill_color'] = hex_colour(seen)
        return result

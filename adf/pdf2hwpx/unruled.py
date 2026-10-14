"""Tables without cell borders: text lined up in columns and rows.

A person sees a table in words that stand in columns even when no line is
drawn (a paper's table has only a top, a header and a bottom rule; a form
has none). The same is read here from geometry alone:

1. each text line is cut where a gap is wider than a word space, giving
   segments (the cells' texts);
2. segments on one baseline make a row; a region is a run of at least
   three rows with two or more segments each;
3. columns are the empty vertical channels that stay empty down the
   whole region;
4. the region is a table only with evidence: rules above and below it, a
   table caption beside it, or enough rows and columns lined up. Running
   text in columns, tables of contents and bullet lists are rejected.

The result is a grid (column and row boundaries); the caller reads each
cell with the ordinary cell reader.
"""
import re
from statistics import median

import pymupdf

from .captions import caption_kind

LEADER = re.compile(r'[·.․‥…ㆍ]{4,}')
MARKER = re.compile(r'^\s*(?:[-–—*※○●◦■□▪▫▴▸►◆◇•❍❏❑➢➤▶▷◎◉◈☞✓✔ㆍ∙]|\d{1,2}[.)]|\(\d{1,2}\)|[가-힣A-Za-z][.)])\s*$')


def _dominant_baseline(line):
    weights = {}
    for span in line['spans']:
        key = round(span['origin'][1], 1)
        weights[key] = weights.get(key, 0) + len(span['chars']) * span['size']
    return max(weights, key=weights.get) if weights else None


def segments(lines):
    """Text lines cut at gaps wider than a word space: (x0, y0, x1, y1, baseline, size, text)."""
    result = []
    for line in lines:
        if tuple(line['dir']) != (1., 0.) or line.get('wmode', 0):
            continue
        baseline = _dominant_baseline(line)
        chars = [(char, span['size']) for span in line['spans'] for char in span['chars']
                 if char['c'].strip() and abs(char['origin'][1] - (baseline or 0)) <= .4 * span['size']]
        group = []
        for char, size in chars + [(None, 0)]:
            if group and (char is None or char['bbox'][0] - group[-1][0]['bbox'][2] > max(5., .9 * group[-1][1])):
                boxes = [item[0]['bbox'] for item in group]
                result.append((min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes),
                               max(b[3] for b in boxes), baseline, max(item[1] for item in group),
                               ''.join(item[0]['c'] for item in group)))
                group = []
            if char is not None:
                group.append((char, size))
    return result


def _rows(items):
    """Segments on one baseline form a row (sorted down the page)."""
    rows = []
    for item in sorted(items, key=lambda item: item[4]):
        if rows and item[4] - rows[-1]['baseline'] <= .35 * min(item[5], rows[-1]['size']):
            rows[-1]['items'].append(item)
        else:
            rows.append(dict(baseline=item[4], size=item[5], items=[item]))
    for row in rows:
        row['items'].sort(key=lambda item: item[0])
        row['top'] = min(item[1] for item in row['items'])
        row['bottom'] = max(item[3] for item in row['items'])
    return rows


def _tabular(row):
    return len(row['items']) >= 2 and not any(LEADER.search(item[6]) for item in row['items'])


def _runs(rows):
    """Runs of consecutive rows that look like table rows. One row with a
    single segment may sit inside (a cell's second line)."""
    runs, run, pitches = [], [], []
    for row in rows:
        if run:
            pitch = row['baseline'] - run[-1]['baseline']
            limit = max(2.6 * row['size'], 1.8 * median(pitches)) if pitches else 3.2 * row['size']
            if pitch > limit:
                runs.append(run)
                run, pitches = [], []
            else:
                pitches.append(pitch)
        if _tabular(row):
            run.append(row)
        elif run and not any(LEADER.search(item[6]) for item in row['items']) and len(row['items']) == 1 and (
                not run[-1].get('weak')):
            row['weak'] = True
            run.append(row)
        else:
            if run:
                runs.append(run)
            run, pitches = [], []
    if run:
        runs.append(run)
    # A run starts and ends on a real row.
    cleaned = []
    for run in runs:
        while run and run[0].get('weak'):
            run = run[1:]
        while run and run[-1].get('weak'):
            run = run[:-1]
        if sum(1 for row in run if not row.get('weak')) >= 3:
            cleaned.append(run)
    return cleaned


def _channels(run, minimum=1.2):
    """Column boundaries: x positions where no segment of the run reaches.

    A gap counts only when most rows have text on both sides of it, so a
    chance gap inside one column (centred or short entries) is no boundary,
    while a narrow real one (two columns set almost touching) is.
    """
    real = [row for row in run if not row.get('weak')]
    spans = sorted((item[0], item[2]) for row in real for item in row['items'])
    candidates, reach = [], spans[0][1]
    for start, end in spans[1:]:
        if start - reach >= minimum:
            candidates.append((reach + start) / 2)
        reach = max(reach, end)
    edges = []
    for x in candidates:
        both = sum(1 for row in real if any(item[2] <= x for item in row['items'])
                   and any(item[0] >= x for item in row['items']))
        if both >= max(3, .5 * len(real)):
            edges.append(x)
    return edges


def _rules(formatting, y0, y1, x0, x1, taken=()):
    """Horizontal rules between y0 and y1 that cover most of x0..x1 (their y positions),
    leaving out rules that are the borders of tables already found."""
    found = []
    for direction, position, start, end, style in formatting.edges:
        if (direction == 'h' and y0 <= position <= y1 and min(end, x1) - max(start, x0) >= .7 * (x1 - x0)
                and style.get('type') in ('SOLID', 'DOUBLE_SLIM')
                and not any(rect.y0 - 1.5 <= position <= rect.y1 + 1.5 and min(end, rect.x1) - max(start, rect.x0) > 0
                            for rect in taken)):
            found.append(position)
    return sorted(set(round(y, 2) for y in found))


def _evidence(formatting, run, lines_text, columns, taken):
    """Why this region is a table: 'rules', 'caption', 'aligned' or None."""
    real = [row for row in run if not row.get('weak')]
    size = median(row['size'] for row in real)
    x0 = min(item[0] for row in run for item in row['items'])
    x1 = max(item[2] for row in run for item in row['items'])
    top, bottom = run[0]['top'], run[-1]['bottom']
    captioned = any(((0 <= top - y1 <= 3 * size) or (0 <= y0 - bottom <= 3 * size)) and caption_kind(text) == 'table'
                    for text, y0, y1 in lines_text)
    # Rules above and below: a table of three or more columns, or a captioned one
    # (a label and its text between two rules is a card or a list).
    if (columns >= 3 or captioned) and \
            len(_rules(formatting, top - 3 * size, top + .5 * size, x0, x1, taken)) >= 1 and \
            len(_rules(formatting, bottom - .5 * size, bottom + 3 * size, x0, x1, taken)) >= 1:
        return 'rules'
    if captioned:
        return 'caption'
    regular = sum(1 for row in real if len(row['items']) >= columns - 1)
    if columns >= 3 and len(real) >= 4 and regular >= .7 * len(real) and not _cards(formatting, real, x1 - x0):
        return 'aligned'
    return None


def _cards(formatting, real, width):
    """True when the text sits in separate drawn boxes (a designed slide of
    cards, each its own shape), not in the cells of one table: such a page
    is better kept as text boxes where they were drawn."""
    boxes = [rect for rect in list(formatting.marks) + [rect for rect, _ in formatting.fills]
             if 0 < rect.width < .7 * width and rect.height > 4]
    items = [item for row in real for item in row['items']]
    boxed = sum(1 for item in items
                if any(rect.contains(pymupdf.Point((item[0] + item[2]) / 2, (item[1] + item[3]) / 2)) for rect in boxes))
    return boxed >= .4 * len(items)


ENABLED = True  # switched off by checks that compare with and without this reader


def find_unruled_tables(formatting, lines, taken=()):
    """Grids of tables drawn without cell borders, among the given text lines.

    Each grid is dict(rect, xs, ys, evidence): column and row boundaries
    (row boundaries sit on a drawn rule where there is one). `taken` holds
    the rectangles of tables already found.
    """
    items = segments(lines) if ENABLED else []
    if len(items) < 6:
        return []
    lines_text = [(text, y0, y1) for _, y0, _, y1, _, _, text in items]
    grids = []
    for run in _runs(_rows(items)):
        real = [row for row in run if not row.get('weak')]
        edges = _channels(run)
        columns = len(edges) + 1
        if columns < 2 or len(real) < 3:
            continue
        texts = [len(item[6]) for row in real for item in row['items']]
        # Running text set in columns is not a table.
        if median(texts) > 32:
            continue
        # A list or a table of contents: a bullet or number, then its text, on every row.
        firsts = [row['items'][0][6] for row in real]
        if sum(1 for text in firsts if MARKER.match(text)) >= .8 * len(firsts):
            continue
        evidence = _evidence(formatting, run, lines_text, columns, taken)
        if evidence is None or (columns == 2 and evidence == 'aligned'):
            continue
        x0 = min(item[0] for row in run for item in row['items'])
        x1 = max(item[2] for row in run for item in row['items'])
        size = median(row['size'] for row in real)
        rules = _rules(formatting, run[0]['top'] - 3 * size, run[-1]['bottom'] + 3 * size, x0, x1, taken)
        # Rows: a line with no text in the first column continues the row above
        # (a cell wrapped onto a second line); a line whose only text is in
        # the first column joins the row above when that cell ends with a
        # hyphen (a wrapped word).
        merged = []
        for row in run:
            first = [item for item in row['items'] if item[2] <= edges[0]] if edges else row['items']
            continues = False
            if merged:
                above = merged[-1]
                if not first:
                    continues = True
                elif len(row['items']) == 1:
                    cell = next((item for item in above['items'] if item[0] - 1 <= row['items'][0][0] <= item[2] + 1), None)
                    continues = cell is not None and cell[6].rstrip().endswith('-')
            if continues:
                above['items'] += row['items']
                above['bottom'] = max(above['bottom'], row['bottom'])
                above['last'] = row['baseline']
                above['last_size'] = row['size']
                above['joined'] = True
                continue
            merged.append(dict(row, items=list(row['items']), last=row['baseline'], last_size=row['size']))
        if any(row.get('weak') and not row.get('joined') for row in merged):
            # A lone line that continues nothing: a note under the table, or
            # the table ends there.
            while merged and merged[-1].get('weak') and not merged[-1].get('joined'):
                merged.pop()
        if len(merged) < 3:
            continue
        # Row boundaries: on a rule when one lies between the rows.
        ys = []
        for upper, lower in zip(merged, merged[1:]):
            lo = upper['last'] + .25 * upper['last_size']
            hi = lower['baseline'] - .85 * lower['size']
            between = [y for y in rules if min(lo, hi) - 1 <= y <= max(lo, hi) + 1]
            ys.append(between[0] if between else (lo + hi) / 2)
        first, last = merged[0], merged[-1]
        above = [y for y in rules if first['baseline'] - .85 * first['size'] - 3 * size <= y <= first['baseline'] - .85 * first['size'] + 1]
        end = last['last'] + .25 * last['last_size']
        below = [y for y in rules if end - 1 <= y <= end + 3 * size]
        ys = [above[-1] if above else first['baseline'] - .85 * first['size'] - 1.5] + ys + \
             [below[0] if below else end + 1.5]
        long_rules = [(start, end) for direction, position, start, end, style in formatting.edges
                      if direction == 'h' and min(ys) - 1 <= position <= max(ys) + 1 and end - start >= .7 * (x1 - x0)]
        left = min([x0] + [start for start, _ in long_rules]) if long_rules else x0 - 2
        right = max([x1] + [end for _, end in long_rules]) if long_rules else x1 + 2
        if right - left < 20 or any(b - a < 2 for a, b in zip(ys, ys[1:])):
            continue
        grids.append(dict(rect=pymupdf.Rect(left, ys[0], right, ys[-1]), xs=[left] + edges + [right], ys=ys,
                          evidence=evidence))
    return grids

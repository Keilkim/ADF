"""Find page columns (newspaper-style text, side-by-side text blocks).

Rows from PDF text blocks that sit side by side on one baseline are
either columns or one visual line. When several rows in a row share a
vertical gap (a gutter) and no row crosses it, that stretch of the page is
a column band; the writer turns it into a borderless table whose cells
rewrap like the page columns. A few side-by-side pieces outside a band
are one line (a heading with a note to its right) and are joined.
"""
import pymupdf

from .pdf_reader import join_rows

MIN_SHARED_ROWS = 3


def _same_line(row, other):
    # Text of different sizes on one line has different baselines; boxes
    # that mostly overlap vertically still share the line.
    overlap = min(row['box'].y1, other['box'].y1) - max(row['box'].y0, other['box'].y0)
    return (abs(row['baseline'] - other['baseline']) <= .35 * min(row['size'], other['size'])
            or overlap > .6 * min(row['box'].height, other['box'].height))


def _lines(rows):
    lines = []
    for row in sorted(rows, key=lambda row: (row['baseline'], row['box'].x0)):
        if lines and _same_line(lines[-1][0], row):
            lines[-1].append(row)
        else:
            lines.append([row])
    return [sorted(line, key=lambda row: row['box'].x0) for line in lines]


def _gutters(lines):
    """Gap positions shared by at least MIN_SHARED_ROWS side-by-side lines."""
    gaps = [(left['box'].x1, right['box'].x0) for line in lines for left, right in zip(line, line[1:])]
    chosen = []
    for x0, x1 in sorted(gaps, key=lambda gap: -sum(other[0] <= (gap[0] + gap[1]) / 2 <= other[1] for other in gaps)):
        middle = (x0 + x1) / 2
        support = sum(other[0] <= middle <= other[1] for other in gaps)
        if support >= MIN_SHARED_ROWS and all(abs(middle - known) > 20 for known in chosen):
            chosen.append(middle)
    return sorted(chosen)


def split_columns(rows):
    """Return page items in reading order: rows and column bands.

    A band is dict(box=Rect, columns=[rows of column 1, rows of column 2...]).
    """
    lines = _lines(rows)
    gutters = _gutters(lines)
    crosses = lambda row: any(row['box'].x0 < x < row['box'].x1 for x in gutters)
    items, stretch = [], []

    def flush():
        side_by_side = sum(len(line) > 1 for line in stretch)
        if gutters and side_by_side >= MIN_SHARED_ROWS:
            columns = [[] for _ in range(len(gutters) + 1)]
            for line in stretch:
                for row in line:
                    columns[sum(row['box'].x0 >= x for x in gutters)].append(row)
            columns = [column for column in columns if column]
            if len(columns) > 1:
                box = pymupdf.Rect(stretch[0][0]['box'])
                for line in stretch:
                    for row in line:
                        box |= row['box']
                items.append(dict(box=box, columns=columns))
                stretch.clear()
                return
        for line in stretch:
            row = line[0]
            for piece in line[1:]:
                join_rows(row, piece)
            items.append(row)
        stretch.clear()

    for line in lines:
        if gutters and not any(crosses(row) for row in line):
            stretch.append(line)
            continue
        flush()
        stretch.append(line)
        flush()
    flush()
    return items

"""Turn one PDF page into validated blocks (paragraphs, tables, images) without AI."""
from statistics import median

import pymupdf

from .columns import split_columns
from .pdf_reader import PdfFormatting, clean_text as _text, paragraphs_from_rows
from .schema import ConversionError, validate_page


def _bbox(rect, page):
    rect = pymupdf.Rect(rect) * page.rotation_matrix
    rect &= page.rect
    return [max(0., min(1000., v)) for v in (
        (rect.x0 - page.rect.x0) * 1000 / page.rect.width,
        (rect.y0 - page.rect.y0) * 1000 / page.rect.height,
        (rect.x1 - page.rect.x0) * 1000 / page.rect.width,
        (rect.y1 - page.rect.y0) * 1000 / page.rect.height)]


def _column_band(item):
    """A borderless one-row table whose cells hold the page columns."""
    columns = item['columns']
    extents = [pymupdf.Rect(min(row['box'].x0 for row in rows), 0, max(row['box'].x1 for row in rows), 0)
               for rows in columns]
    box = item['box']
    edges = [box.x0] + [(left.x1 + right.x0) / 2 for left, right in zip(extents, extents[1:])] + [box.x1]
    cells = []
    for index, (rows, extent) in enumerate(zip(columns, extents)):
        paragraphs = paragraphs_from_rows(rows, extent)
        runs = []
        for paragraph in paragraphs:
            runs.extend(dict(run, text=('\n' if runs and position == 0 else '') + run['text'])
                        for position, run in enumerate(paragraph['runs']))
        main = max(runs, key=lambda run: len(run['text'].strip()))
        top = min(row['box'].y0 for row in rows)
        cells.append(dict(main, row=0, col=index, row_span=1, col_span=1, text=''.join(run['text'] for run in runs),
                          runs=runs, paragraphs=paragraphs, alignment='LEFT', vertical_alignment='TOP',
                          width_pt=edges[index + 1] - edges[index], height_pt=box.height,
                          margins_pt=dict(left=extent.x0 - edges[index], right=max(0., edges[index + 1] - extent.x1),
                                          top=max(0., top - box.y0), bottom=0),
                          borders=[dict(side=side, type='NONE', width_pt=0, color='#000000')
                                   for side in ('left', 'right', 'top', 'bottom')], fill_color=None))
    return dict(kind='table', box=box, text='', font_size_pt=10, rows=1, cols=len(cells), cells=cells,
                bold=False, italic=False, alignment='LEFT', column_widths_pt=[cell['width_pt'] for cell in cells],
                row_heights_pt=[box.height])


def _merge_unruled(cells, xs, ys, formatting):
    """Merge neighbouring cells that the PDF draws as one cell.

    A column boundary that exists only in other rows (a narrow vertical
    heading below a wide one) splits a merged cell in the detected grid.
    Neighbours merge when no line is drawn between them and a text line
    crosses that boundary or one of them is empty.
    """
    def crossing(direction, position, lo, hi):
        for line in formatting.lines:
            box = pymupdf.Rect(line['bbox'])
            if direction == 'v' and box.x0 < position - 1 < position + 1 < box.x1 and lo <= (box.y0 + box.y1) / 2 <= hi:
                return True
            if direction == 'h' and box.y0 < position - 1 < position + 1 < box.y1 and lo <= (box.x0 + box.x1) / 2 <= hi:
                return True
        return False

    changed = True
    while changed:
        changed = False
        at = {(cell['row'], cell['col']): cell for cell in cells}
        for cell in cells:
            r, c, rs, cs = cell['row'], cell['col'], cell['row_span'], cell['col_span']
            for other, direction, position, lo, hi in (
                    (at.get((r, c + cs)), 'v', xs[min(c + cs, len(xs) - 1)], ys[r], ys[r + rs]),
                    (at.get((r + rs, c)), 'h', ys[min(r + rs, len(ys) - 1)], xs[c], xs[c + cs])):
                if other is None or (direction == 'v' and other['row_span'] != rs) or (
                        direction == 'h' and other['col_span'] != cs):
                    continue
                if formatting.has_edge(direction, position, lo, hi) or cell.get('fill_color') != other.get('fill_color'):
                    continue  # a drawn line or a change of colour separates them
                if not (crossing(direction, position, lo, hi) or not cell['text'].strip() or not other['text'].strip()):
                    continue
                rs2 = rs if direction == 'v' else rs + other['row_span']
                cs2 = cs + other['col_span'] if direction == 'v' else cs
                rect = pymupdf.Rect(xs[c], ys[r], xs[c + cs2], ys[r + rs2])
                fallback = ' '.join(text for text in (cell['text'], other['text']) if text.strip())
                merged = dict(row=r, col=c, row_span=rs2, col_span=cs2, **formatting.cell(rect, fallback))
                cells = [item for item in cells if item is not cell and item is not other] + [merged]
                cells.sort(key=lambda item: (item['row'], item['col']))
                changed = True
                break
            if changed:
                break
    return cells


def _open_sides(formatting):
    """Invisible side edges for tables drawn without left and right borders.

    Korean reports often rule only the rows of a table. Three or more
    horizontal rules of the same span get vertical edges at both ends so
    the table finder sees the grid; the cells keep no visible side border.
    """
    rules = {}
    for axis, position, start, end, style in formatting.edges:
        if axis == 'h' and end - start > 50:
            key = (round(start / 2), round(end / 2))
            rules.setdefault(key, []).append((position, start, end))
    lines = []
    for found in rules.values():
        if len(found) < 3:
            continue
        top, bottom = min(item[0] for item in found), max(item[0] for item in found)
        start, end = min(item[1] for item in found), max(item[2] for item in found)
        lines += [((start, top), (start, bottom)), ((end, top), (end, bottom))]
    return lines


def _inside_frame(page, frame, slack=1.5):
    """Clusters of the drawings inside frame, without the frame's own sides."""
    def on_side(rect):
        if all(abs(a - b) <= slack for a, b in zip(rect, frame)):
            return True  # the frame drawn as one rectangle
        thin = min(rect.width, rect.height) <= 2 * slack
        return thin and (abs(rect.x0 - frame.x0) <= slack or abs(rect.x1 - frame.x1) <= slack
                         or abs(rect.y0 - frame.y0) <= slack or abs(rect.y1 - frame.y1) <= slack)
    drawings = [drawing for drawing in page.get_drawings()
                if frame.contains(pymupdf.Rect(drawing['rect'])) and not on_side(pymupdf.Rect(drawing['rect']))]
    # Boxes a little apart in one bar (a label and its title) stay together.
    clusters = page.cluster_drawings(drawings=drawings, x_tolerance=12) if drawings else []
    # Never the frame again, which would repeat forever.
    return [rect for rect in clusters if not all(abs(a - b) <= slack for a, b in zip(rect, frame))]


def _filled_row(rect, formatting, inside):
    """A one-row table for shaded boxes in a row that hold text, or None.

    The gaps between the boxes become empty cells without borders.
    """
    def light(colour):
        # Light shading behind dark text; a coloured band of a designed page
        # (white text on a strong colour) stays artwork.
        try:
            return min(int(colour[index:index + 2], 16) for index in (1, 3, 5)) >= 200
        except (TypeError, ValueError):
            return False
    shaded = [(fill, colour) for fill, colour in formatting.fills
              if (fill & rect).get_area() > .9 * fill.get_area() and fill.width > 8 and fill.height > 8]
    if not shaded or not all(light(colour) for _, colour in shaded):
        return None
    fills = sorted({tuple(fill) for fill, _ in shaded})
    fills = [pymupdf.Rect(fill) for fill in fills]
    if not fills or sum(fill.get_area() for fill in fills) < .7 * rect.get_area():
        return None
    top, bottom = fills[0].y0, fills[0].y1
    if any(abs(fill.y0 - top) > 1.5 or abs(fill.y1 - bottom) > 1.5 for fill in fills):
        return None
    if any(right.x0 < left.x1 - 1 for left, right in zip(fills, fills[1:])):
        return None
    # Every box holds text; one PDF line may run across several boxes.
    if not inside(rect) or not all(any(pymupdf.Rect(line['bbox']).intersects(fill) for line in formatting.lines)
                                   for fill in fills):
        return None
    # A highlight behind words of a longer line is not a box.
    for line in formatting.lines:
        box = pymupdf.Rect(line['bbox'])
        if box.intersects(rect) and (box & rect).get_area() < .9 * box.get_area():
            return None
    boxes = []
    for fill in fills:
        if boxes and fill.x0 - boxes[-1].x1 > 1:
            boxes.append(pymupdf.Rect(boxes[-1].x1, top, fill.x0, bottom))
        boxes.append(pymupdf.Rect(fill.x0, top, fill.x1, bottom))
    whole = pymupdf.Rect(boxes[0].x0, top, boxes[-1].x1, bottom)
    cells = [dict(row=0, col=index, row_span=1, col_span=1, _rect=box, **formatting.cell(box, ''))
             for index, box in enumerate(boxes)]
    for cell in cells:
        # A narrow gap cell must not be widened by Hancom's default padding.
        cell.setdefault('margins_pt', dict(left=0, right=0, top=0, bottom=0))
    return dict(kind='table', bbox=_bbox(whole, formatting.page), text='', font_size_pt=10, rows=1,
                cols=len(boxes), cells=cells, bold=False, italic=False, alignment='LEFT',
                column_widths_pt=[box.width for box in boxes], row_heights_pt=[whole.height])


def _inner_boxes(formatting, blocks, slack=1.5):
    """Dotted rectangles inside a table cell that are not the cell itself."""
    dotted = [edge for edge in formatting.edges if edge[4].get('type') == 'DOT']
    across = [edge for edge in dotted if edge[0] == 'h']
    down = [edge for edge in dotted if edge[0] == 'v']
    cells = [cell['_rect'] for block in blocks if block['kind'] == 'table'
             for cell in block['cells'] if cell.get('_rect') is not None]
    found = []
    for top in across:
        for bottom in across:
            if bottom[1] - top[1] < 6 or abs(top[2] - bottom[2]) > slack or abs(top[3] - bottom[3]) > slack:
                continue
            box = pymupdf.Rect(top[2], top[1], top[3], bottom[1])
            sides = [edge for edge in down if top[1] - slack <= edge[2] and edge[3] <= bottom[1] + slack
                     and edge[3] - edge[2] >= .8 * box.height]
            if not (any(abs(edge[1] - box.x0) <= slack for edge in sides)
                    and any(abs(edge[1] - box.x1) <= slack for edge in sides)):
                continue
            inside = [cell for cell in cells if cell.contains(box + (-slack, -slack, slack, slack))]
            # Inside a cell, not one of the cells (a dotted grid line).
            if inside and not any(all(abs(a - b) <= 3 for a, b in zip(cell, box)) for cell in cells):
                if not any(all(abs(a - b) <= slack for a, b in zip(other, box)) for other, _ in found):
                    found.append((box, dict(top[4])))
    return found


def _place_in_cell(info, image, tables, page):
    """Put an image that lies inside one table cell into that cell."""
    for table in tables:
        for cell in table['cells']:
            rect = cell.get('_rect')
            if rect is not None and (image & rect).get_area() > .9 * image.get_area():
                entry = dict(box_pt=[image.x0, image.y0, image.x1, image.y1])
                matrix = info.get('transform') or (0, 0, 0, 0, 0, 0)
                if info.get('xref') and matrix[1] == matrix[2] == 0 and matrix[0] > 0 and matrix[3] > 0 and not page.rotation:
                    entry['xref'] = info['xref']
                cell.setdefault('images', []).append(entry)
                cell['margins_pt'] = dict(left=0., right=0., top=0., bottom=0.)
                return True
    return False


def _spread(page):
    """The split of a two-up page (two portrait pages printed on one
    landscape sheet): an empty band down the middle that no text line
    crosses, with text on both sides. Returns (left, right) rects or None.
    """
    width, height = page.rect.width, page.rect.height
    if width < 1.2 * height:
        return None
    lines = [pymupdf.Rect(line['bbox']) for block in page.get_text('dict')['blocks']
             for line in block.get('lines', []) if ''.join(span['text'] for span in line['spans']).strip()]
    low, high = .4 * width, .6 * width
    if any(line.x0 < high and line.x1 > low and line.width > .5 * width for line in lines):
        return None
    taken = sorted((max(low, line.x0), min(high, line.x1)) for line in lines if line.x0 < high and line.x1 > low)
    gaps, cursor = [], low
    for start, end in taken:
        if start > cursor:
            gaps.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < high:
        gaps.append((cursor, high))
    gap = max(gaps, key=lambda item: item[1] - item[0], default=None)
    if gap is None or gap[1] - gap[0] < 12:
        return None
    middle = (gap[0] + gap[1]) / 2
    if sum(line.x1 <= middle for line in lines) < 5 or sum(line.x0 >= middle for line in lines) < 5:
        return None
    return pymupdf.Rect(0, 0, middle, height), pymupdf.Rect(middle, 0, width, height)


def _moved(value, dx, page_width, half_width):
    """A half page's block with its positions on the whole sheet."""
    if isinstance(value, list):
        return [_moved(item, dx, page_width, half_width) for item in value]
    if not isinstance(value, dict):
        return value
    moved = {}
    for key, item in value.items():
        if key == 'bbox':
            moved[key] = [(item[0] * half_width / 1000 + dx) * 1000 / page_width, item[1],
                          (item[2] * half_width / 1000 + dx) * 1000 / page_width, item[3]]
        elif key in ('left_pt', 'right_pt'):
            moved[key] = item + dx
        elif key == 'box_pt':
            moved[key] = [item[0] + dx, item[1], item[2] + dx, item[3]]
        elif key == 'xref':
            continue  # an image of the half page's copy, not of this document
        else:
            moved[key] = _moved(item, dx, page_width, half_width)
    return moved


def _spread_page(page, halves):
    """Each half of a two-up sheet read as its own page, then put back."""
    blocks, warnings, columns = [], [], []
    for index, half in enumerate(halves):
        with pymupdf.open() as copy:
            target = copy.new_page(width=half.width, height=half.height)
            target.show_pdf_page(target.rect, page.parent, page.number, clip=half)
            result = native_page(target, split=False)
        if result.get('free_layout'):
            # A designed spread keeps its artwork as one sheet.
            return native_page(page, split=False)
        warnings.extend(warning for warning in result.get('warnings', []) if warning not in warnings)
        for block in result['blocks']:
            block = _moved(block, half.x0, page.rect.width, half.width)
            block['column'] = index
            blocks.append(block)
        columns.append([half.x0, half.y0, half.x1, half.y1])
    result = validate_page(dict(blocks=blocks, warnings=warnings))
    result['columns'] = columns
    return result


def native_page(page, split=True):
    """Reuse exact PDF text and ruled table grids, without an API request."""
    halves = _spread(page) if split and not page.rotation else None
    if halves:
        return _spread_page(page, halves)
    blocks, table_boxes, warnings = [], [], []
    formatting = PdfFormatting(page)
    try:
        rotation = page.rotation
        try:
            # find_tables analyzes rotated pages in displayed coordinates;
            # text spans use unrotated coordinates. Normalize before finding
            # tables and copy all data before restoring the displayed page.
            page.set_rotation(0)
            extra = _open_sides(formatting)
            read = lambda finder: [(tuple(table.bbox), table.extract(), table.col_count,
                                    [list(row.cells) for row in table.rows], list(table.cells))
                                   for table in finder.tables]
            tables = read(page.find_tables(add_lines=extra or None))
            # Lines added for open tables also cut through boxes drawn inside
            # cells (a dotted note); the plain grid is the fallback for a
            # table whose cells then cannot be rebuilt.
            plain = read(page.find_tables()) if extra else []
        finally:
            page.set_rotation(rotation)
        queue = [(table, True) for table in tables]
        while queue:
            (table_bbox, grid, cols, table_rows, table_cells), first_try = queue.pop(0)
            if not grid:
                continue
            rows = len(grid)
            # Merged PDF cells have a larger rectangle; derive spans from grid
            # boundaries instead of treating every None slot as an empty cell.
            xs = sorted({round(v, 2) for box in table_cells if box for v in (box[0], box[2])})
            ys = sorted({round(v, 2) for box in table_cells if box for v in (box[1], box[3])})
            cells = []
            for r in range(rows):
                for c in range(cols):
                    box = table_rows[r][c]
                    if box is None:
                        continue
                    rs = max(1, sum(box[1] - .1 <= y < box[3] - .1 for y in ys))
                    cs = max(1, sum(box[0] - .1 <= x < box[2] - .1 for x in xs))
                    cells.append(dict(row=r, col=c, row_span=rs, col_span=cs,
                                      **formatting.cell(box, _text(grid[r][c] or ''))))
            # Some PDFs draw cell fills but omit a border segment. PyMuPDF
            # leaves those slots as None even though their text still exists.
            # Recover only uncovered slots with an unambiguous complete grid;
            # a slot already occupied by a merged cell must remain covered.
            recovered = False
            if len(xs) == cols + 1 and len(ys) == rows + 1:
                occupied = {(r, c) for cell in cells
                            for r in range(cell['row'], cell['row'] + cell['row_span'])
                            for c in range(cell['col'], cell['col'] + cell['col_span'])}
                for r in range(rows):
                    for c in range(cols):
                        if table_rows[r][c] is None and (r, c) not in occupied:
                            rect = pymupdf.Rect(xs[c], ys[r], xs[c + 1], ys[r + 1])
                            cells.append(dict(row=r, col=c, row_span=1, col_span=1,
                                              **formatting.cell(rect, _text(page.get_textbox(rect)).strip())))
                            recovered = True
                cells.sort(key=lambda cell: (cell['row'], cell['col']))
            if len(xs) == cols + 1 and len(ys) == rows + 1:
                cells = _merge_unruled(cells, xs, ys, formatting)
                for cell in cells:
                    cell['_rect'] = pymupdf.Rect(xs[cell['col']], ys[cell['row']], xs[cell['col'] + cell['col_span']],
                                                 ys[cell['row'] + cell['row_span']])
            block = dict(kind='table', bbox=_bbox(table_bbox, page), text='', font_size_pt=10,
                         rows=rows, cols=cols, cells=cells, bold=False, italic=False, alignment='LEFT')
            if len(xs) == cols + 1 and len(ys) == rows + 1:
                block['column_widths_pt'] = [b - a for a, b in zip(xs, xs[1:])]
                block['row_heights_pt'] = [b - a for a, b in zip(ys, ys[1:])]
            try:
                validate_page(dict(blocks=[block], warnings=[]))
            except ConversionError:
                same = [table for table in plain if first_try and pymupdf.Rect(table[0]).intersects(pymupdf.Rect(table_bbox))
                        and (pymupdf.Rect(table[0]) & pymupdf.Rect(table_bbox)).get_area()
                        > .8 * pymupdf.Rect(table_bbox).get_area()]
                if same:
                    queue.insert(0, (same[0], False))
                    continue
                warnings.append('병합 셀을 정확히 복원하지 못한 표는 문단으로 추출했습니다.')
                continue
            blocks.append(block)
            table_boxes.append(pymupdf.Rect(table_bbox))
            if recovered:
                warnings.append('표의 일부 경계를 주변 격자로 복원했습니다. 해당 셀을 원본과 대조하세요.')
    except Exception:
        warnings.append('로컬 표 분석에 실패해 해당 내용은 문단으로 추출했습니다.')
    # A dotted box drawn inside a table cell (a note) joins the table's
    # drawings, so it is found from its four sides and drawn on the paper.
    for box, style in _inner_boxes(formatting, blocks):
        bbox = _bbox(box, page)
        if bbox[0] < bbox[2] and bbox[1] < bbox[3]:
            blocks.append(dict(kind='image', bbox=bbox, text='', font_size_pt=10, bold=False, italic=False,
                               alignment='LEFT', rows=0, cols=0, cells=[], placement='BEHIND_TEXT',
                               text_image=True, frame=style))
    figure_boxes, designed = [], False
    # The page's body text size: the size most characters have.
    weights = {}
    for line in formatting.lines:
        for span in line['spans']:
            weights[round(span['size'], 1)] = weights.get(round(span['size'], 1), 0) + len(span['chars'])
    body_size = max(weights, key=weights.get) if weights else 10
    image_infos = page.get_image_info(xrefs=True)
    images = [pymupdf.Rect(image['bbox']) for image in image_infos]
    inside = lambda rect: [line for line in formatting.lines if pymupdf.Rect(line['bbox']).intersects(rect) and
                           (pymupdf.Rect(line['bbox']) & rect).get_area() > .5 * max(1, pymupdf.Rect(line['bbox']).get_area())]
    try:
        clusters = page.cluster_drawings()
        while clusters:
            rect = pymupdf.Rect(clusters.pop(0))
            if any((rect & table).get_area() > rect.get_area() * .5 for table in table_boxes):
                # A table's own rules are skipped; a frame well outside the
                # table (a page border) is still drawn below.
                if not any(sum(gap > 3 for gap in (table.x0 - rect.x0, table.y0 - rect.y0, rect.x1 - table.x1,
                                                   rect.y1 - table.y1)) >= 3
                           and (rect & table).get_area() > .9 * table.get_area() for table in table_boxes):
                    continue
            if rect.get_area() < 400:
                # A small mark beside a text line (a square bullet drawn as
                # a shape) is placed where it was; other specks are dropped.
                beside = any(min(rect.y1, line['bbox'][3]) - max(rect.y0, line['bbox'][1]) > .5 * rect.height
                             and rect.height <= 2.5 * (line['bbox'][3] - line['bbox'][1])
                             and not pymupdf.Rect(line['bbox']).contains(rect) for line in formatting.lines)
                bbox = _bbox(rect, page)
                if beside and rect.width > 2 and rect.height > 2 and bbox[0] < bbox[2] and bbox[1] < bbox[3]:
                    blocks.append(dict(kind='image', bbox=bbox, text='', font_size_pt=10, bold=False, italic=False,
                                       alignment='LEFT', rows=0, cols=0, cells=[], placement='BEHIND_TEXT',
                                       text_image=True))
                continue
            # A border around the whole page is decoration, not one giant
            # figure that should swallow all editable document content.
            if rect.get_area() > (page.rect.width * page.rect.height) * .8:
                # Unless it is the page's artwork itself: many shapes that are
                # not table rules (a chart drawn over the page). Such a page
                # keeps its artwork and places its text on top.
                shapes = [pymupdf.Rect(drawing['rect']) for drawing in page.get_drawings()]
                artwork = sum(1 for shape in shapes if shape.intersects(rect)
                              and not any(shape.intersects(table) for table in table_boxes))
                if artwork >= 30:
                    designed = True
                continue
            bbox = _bbox(rect, page)
            if bbox[0] >= bbox[2] or bbox[1] >= bbox[3]:
                continue
            if (formatting.is_frame(rect) and inside(rect)
                    and not any(image.intersects(rect) for image in images)):
                # A ruled box around text (a title box) stays editable as a
                # one-cell table instead of a picture of its text.
                cell = dict(row=0, col=0, row_span=1, col_span=1, _rect=rect, **formatting.cell(rect, ''))
                blocks.append(dict(kind='table', bbox=bbox, text='', font_size_pt=10, rows=1, cols=1, cells=[cell],
                                   bold=False, italic=False, alignment='LEFT',
                                   column_widths_pt=[rect.width], row_heights_pt=[rect.height]))
                table_boxes.append(rect)
                continue
            crossing = [pymupdf.Rect(line['bbox']) for line in formatting.lines
                        if pymupdf.Rect(line['bbox']).intersects(rect)]
            if crossing and all(rect.height <= 1.5 * line.height and not rect.contains(line) for line in crossing):
                # A highlight behind words of a text line, not a figure.
                continue
            row = _filled_row(rect, formatting, inside)
            if row and not any(image.intersects(rect) for image in images):
                # Shaded boxes side by side around text (a title bar) stay
                # editable as a one-row table.
                blocks.append(row)
                table_boxes.append(rect)
                continue
            # A border around a section of text or tables is a container,
            # not a figure; a picture of it would lose the editable text.
            # Charts keep their few short labels and stay pictures.
            lines = inside(rect)
            text_area = sum(pymupdf.Rect(line['bbox']).get_area() for line in lines)
            # A diagram's labels are smaller than the body text and short;
            # with them it stays one picture, as a figure in a paper.
            sizes = [max(span['size'] for span in line['spans']) for line in lines if line['spans']]
            labelled = (len(lines) >= 6 and median(sizes) <= .85 * body_size
                        and median(pymupdf.Rect(line['bbox']).width for line in lines) <= .35 * rect.width)
            if (any((rect & table).get_area() > .5 * table.get_area() for table in table_boxes)
                    or (len(lines) >= 6 and text_area > .1 * rect.get_area() and not labelled)):
                # Its outline (a dotted page frame) is drawn behind the text.
                style = formatting.frame_style(rect) if formatting.is_frame(rect, contents=True) else None
                if style:
                    blocks.append(dict(kind='image', bbox=bbox, text='', font_size_pt=10, bold=False, italic=False,
                                       alignment='LEFT', rows=0, cols=0, cells=[], placement='BEHIND_TEXT',
                                       text_image=True, frame=style))
                    # Shapes inside the frame (a title box) are judged on
                    # their own, without the frame that joined them.
                    clusters.extend(_inside_frame(page, rect))
                continue
            figure_boxes.append(rect)
            block = dict(kind='image', bbox=bbox, text='', font_size_pt=10,
                         bold=False, italic=False, alignment='LEFT', rows=0, cols=0, cells=[])
            if rect.height <= 6 and rect.width > 8 * rect.height:
                # A thin band (a coloured rule under a heading) is drawn where
                # it was; as a line of its own it would push the text down.
                block['placement'] = 'BEHIND_TEXT'
            blocks.append(block)
    except Exception:
        warnings.append('일부 벡터 도형을 분석하지 못했습니다. 원본과 대조하세요.')
    tables = [block for block in blocks if block['kind'] == 'table']
    for info, image in zip(image_infos, images):
        if _place_in_cell(info, image, tables, page):
            continue
        # Mostly inside a figure, the image is part of its picture.
        if not any((image & box).get_area() > .9 * image.get_area() for box in figure_boxes):
            bbox = _bbox(image, page)
            if bbox[0] < bbox[2] and bbox[1] < bbox[3]:
                block = dict(kind='image', bbox=bbox, text='', font_size_pt=10,
                             bold=False, italic=False, alignment='LEFT', rows=0, cols=0, cells=[])
                # A page template or a photo with text over it is a background:
                # it is placed behind the text instead of taking a line.
                text_on = any(image.contains(pymupdf.Rect(line['bbox']).tl + (pymupdf.Rect(line['bbox']).br
                                                                              - pymupdf.Rect(line['bbox']).tl) * .5)
                              for line in formatting.lines)
                # An icon beside a text line (a bullet picture) would take a
                # line of its own in the flow; it is placed where it was.
                beside = any(min(image.y1, line['bbox'][3]) - max(image.y0, line['bbox'][1]) > .5 * image.height
                             and image.height <= 2.5 * (line['bbox'][3] - line['bbox'][1])
                             for line in formatting.lines)
                thin = image.height <= 6 and image.width > 8 * image.height
                if text_on or beside or thin or image.get_area() > .5 * page.rect.get_area():
                    block['placement'] = 'BEHIND_TEXT'
                matrix = info.get('transform') or (0, 0, 0, 0, 0, 0)
                if info.get('xref') and matrix[1] == matrix[2] == 0 and matrix[0] > 0 and matrix[3] > 0 and not page.rotation:
                    block['xref'] = info['xref']  # upright: the embedded bitmap itself
                blocks.append(block)
    # Text drawn by a font without a Unicode map reads as control codes.
    # It cannot become editable text; its image keeps it visible in place.
    for line in formatting.lines:
        text = ''.join(char['c'] for span in line['spans'] for char in span['chars'])
        unreadable = sum(1 for char in text if char in '\x00\ufffd' or (ord(char) < 32 and char != '\t'))
        box = pymupdf.Rect(line['bbox'])
        if text.strip() and unreadable > .5 * len(text.strip()) and not any(box in other for other in figure_boxes):
            bbox = _bbox(box, page)
            if bbox[0] < bbox[2] and bbox[1] < bbox[3]:
                blocks.append(dict(kind='image', bbox=bbox, text='', font_size_pt=10, bold=False, italic=False,
                                   alignment='LEFT', rows=0, cols=0, cells=[], placement='BEHIND_TEXT',
                                   text_image=True))
    for symbol in formatting.symbols:
        bbox = _bbox(symbol, page)
        if bbox[0] < bbox[2] and bbox[1] < bbox[3] and not any(symbol in figure for figure in figure_boxes):
            blocks.append(dict(kind='image', bbox=bbox, text='', font_size_pt=10, bold=False, italic=False,
                               alignment='LEFT', rows=0, cols=0, cells=[], placement='BEHIND_TEXT', text_image=True))
    covered = {id(line) for box in table_boxes + figure_boxes for line in inside(box)}
    rows = formatting.rows([line for line in formatting.lines if id(line) not in covered], keep_columns=True)
    items = split_columns(rows)
    paragraphs = paragraphs_from_rows([item for item in items if 'columns' not in item])
    bands = [_column_band(item) for item in items if 'columns' in item]
    # Single-line paragraphs take the pitch that the page's wrapped
    # paragraphs use, so headings and body lines keep the PDF rhythm.
    every = paragraphs + [paragraph for band in bands for cell in band['cells'] for paragraph in cell['paragraphs']]
    measured = [(paragraph['line_spacing_percent'], paragraph['line_count']) for paragraph in every
                if paragraph['line_count'] > 1]
    spacing = median([percent for percent, count in measured for _ in range(count)]) if measured else 160
    for paragraph in every:
        paragraph.setdefault('line_spacing_percent', round(spacing))
    # Text drawn on a figure is part of its picture in a flowing page. A
    # designed page shows the figure without text, so the writer then
    # needs this text as well; IN_FIGURE keeps it out of the flow.
    in_figures = [line for box in figure_boxes for line in inside(box) if id(line) not in
                  {id(other) for table in table_boxes for other in inside(table)}]
    for paragraph in paragraphs_from_rows(formatting.rows(in_figures)) if in_figures else []:
        paragraph['placement'] = 'IN_FIGURE'
        paragraph.setdefault('line_spacing_percent', round(spacing))
        paragraphs.append(paragraph)
    for paragraph in paragraphs:
        x0, y0, x1, y1 = paragraph.pop('box_pt')
        bbox = _bbox((x0, y0, x1, y1), page)
        if bbox[0] < bbox[2] and bbox[1] < bbox[3] and paragraph['text'].strip():
            blocks.append(dict(kind='paragraph', bbox=bbox, rows=0, cols=0, cells=[], **paragraph))
    for band in bands:
        for cell in band['cells']:
            for paragraph in cell['paragraphs']:
                paragraph.pop('box_pt', None)
        band['bbox'] = _bbox(band.pop('box'), page)
        if band['bbox'][0] < band['bbox'][2] and band['bbox'][1] < band['bbox'][3]:
            blocks.append(band)
    # Filled shapes outside table cells and figures (bars of a chart drawn
    # over a grid) are lost in a flowing page: such a page is designed.
    cells = [cell['_rect'] for block in blocks for cell in block.get('cells', []) if '_rect' in cell]
    loose = 0
    for drawing in page.get_drawings():
        shape = pymupdf.Rect(drawing['rect'])
        if (drawing.get('fill') is None or shape.get_area() < 4 or shape.get_area() > .5 * page.rect.get_area()
                or any(shape in figure for figure in figure_boxes)
                or any(pymupdf.Rect(cell.x0 - 2, cell.y0 - 2, cell.x1 + 2, cell.y1 + 2).contains(shape) for cell in cells)):
            continue
        loose += 1
    designed = designed or loose >= 15
    for block in blocks:
        for cell in block.get('cells', []):
            cell.pop('_rect', None)
    blocks.sort(key=lambda b: (b['bbox'][1], b['bbox'][0]))
    if page.rotation:
        warnings.append('회전한 페이지의 글자는 가로 문단으로 복원했습니다. 원본의 글자 방향과 대조하세요.')
    if not any(b['kind'] in {'paragraph', 'table'} for b in blocks):
        warnings.append('선택한 페이지에 추출 가능한 글자가 없습니다. 스캔 문서는 Gemini AI 변환을 사용하세요.')
    result = validate_page(dict(blocks=blocks, warnings=warnings))
    if designed:
        result['free_layout'] = True
    return result

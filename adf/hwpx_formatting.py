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


def font_name(value):
    name = re.sub(r'^[A-Z]{6}\+', '', value or '')
    if name.startswith('Helvetica'):
        return 'Arial'
    if name.startswith(('Times-', 'TimesNewRoman')):
        return 'Times New Roman'
    if name.startswith('Courier'):
        return 'Courier New'
    if name in {'Gothic', 'Droid Sans Fallback', 'Heiti', 'Mincho'}:
        return '맑은 고딕'
    return re.sub(r'[-,](Regular|BoldItalic|Bold|Italic|Roman|Oblique)$', '', name) or '맑은 고딕'


def span_style(span):
    return dict(font=font_name(span.get('font')), font_size_pt=max(1., min(144., span.get('size', 10))),
                bold=bool(span.get('flags', 0) & 16), italic=bool(span.get('flags', 0) & 2),
                color=color(span.get('color', 0)))


class PdfFormatting:
    def __init__(self, page):
        self.page = page
        flags = pymupdf.TEXTFLAGS_RAWDICT & ~pymupdf.TEXT_PRESERVE_IMAGES
        self.data = restore_text_spacing(page, page.get_text('rawdict', flags=flags, sort=True))
        self.lines = [line for block in self.data['blocks'] for line in block.get('lines', [])]
        self.edges, self.fills = [], []
        for drawing in page.get_drawings():
            for item in drawing['items']:
                segments = []
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

    def text(self, lines, rect=None):
        runs, boxes, baselines = [], [], []
        for line in lines:
            line_runs = []
            for span in line['spans']:
                chars = [char for char in span['chars'] if rect is None or
                         rect.contains(pymupdf.Point((char['bbox'][0] + char['bbox'][2]) / 2,
                                                     (char['bbox'][1] + char['bbox'][3]) / 2))]
                text = ''.join(char['c'] for char in chars)
                if not text:
                    continue
                line_runs.append(dict(text=text, **span_style(span)))
                boxes.extend(pymupdf.Rect(char['bbox']) for char in chars if not char['c'].isspace())
            if line_runs:
                if runs:
                    line_runs[0]['text'] = '\n' + line_runs[0]['text']
                runs.extend(line_runs)
                baselines.append(line['spans'][0]['origin'][1])
        if not runs:
            return dict(text='', runs=[], font_size_pt=10, font='맑은 고딕', alignment='LEFT')
        main = max(runs, key=lambda run: len(run['text'].strip()))
        result = {**main, 'text': ''.join(run['text'] for run in runs), 'runs': runs, 'alignment': 'LEFT'}
        if len(baselines) > 1:
            spacing = median(b - a for a, b in zip(baselines, baselines[1:]))
            result['line_spacing_percent'] = max(50, min(500, round(spacing / main['font_size_pt'] * 100)))
        if rect is not None and boxes:
            box = boxes[0]
            for other in boxes[1:]:
                box |= other
            left, right = max(0., box.x0 - rect.x0), max(0., rect.x1 - box.x1)
            if abs(left - right) < main['font_size_pt'] * .3:
                result['alignment'] = 'CENTER'
            elif right < min(left / 3, main['font_size_pt']):
                result['alignment'] = 'RIGHT'
            result['margins_pt'] = dict(left=left if result['alignment'] == 'LEFT' else min(left, right),
                                      right=right if result['alignment'] == 'RIGHT' else min(left, right),
                                      top=max(0., box.y0 - rect.y0), bottom=0)
        return result

    def cell(self, box, fallback):
        rect = pymupdf.Rect(box)
        result = self.text(self.lines, rect)
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


def add_runs(doc, paragraph, block):
    paragraph.clear_text()
    runs = block.get('runs') or [block]
    for run in runs:
        style = doc.styles.ensure_run(font=run.get('font', block.get('font', '맑은 고딕')),
            size=run.get('font_size_pt', block['font_size_pt']), bold=run.get('bold', block.get('bold', False)),
            italic=run.get('italic', block.get('italic', False)), color=run.get('color', block.get('color', '#000000')))
        paragraph.add_run(clean_text(run['text']), char_pr_id_ref=style, expand_special_characters=True)
    doc.styles.apply_paragraph_format(paragraphs=[paragraph], alignment=block.get('alignment', 'LEFT'),
        line_spacing_percent=block.get('line_spacing_percent', 100), spacing_before_pt=0, spacing_after_pt=0)


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
    add_runs(doc, target.paragraphs[0], style)
    if 'width_pt' in cell and 'height_pt' in cell:
        target.set_size(round(cell['width_pt'] * 100), round(cell['height_pt'] * 100))
    if cell.get('margins_pt'):
        target.set_margins(**{key: round(value * 100) for key, value in cell['margins_pt'].items()})
    if 'borders' in cell or 'fill_color' in cell:
        table.set_cell_border_fill(r, c, cell_border_fill(doc, cell, cache))


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
                                 ('row', 'col', 'row_span', 'col_span', 'text', 'runs')})
                    if original['text'].strip() == cell['text'].strip():
                        cell['runs'] = original['runs']
                        cell['text'] = original['text']
            for key in ('column_widths_pt', 'row_heights_pt'):
                if key in source:
                    block[key] = source[key]
    return extracted

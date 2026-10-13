"""Page structure shared by every extractor and the HWPX writer, and its validation."""
import math
import re


class ConversionError(ValueError):
    """A message safe to show without provider bodies or credentials."""


def validate_format(value):
    def number(number, low, high):
        return type(number) in (int, float) and math.isfinite(number) and low <= number <= high

    if any(key in value and not number(value[key], 1, 144) for key in ('font_size_pt', 'line_size_pt')):
        raise ConversionError('글자 크기 서식을 확인해 주세요.')
    if any(key in value and not isinstance(value[key], bool) for key in ('bold', 'italic', 'font_space')):
        raise ConversionError('글자 서식을 확인해 주세요.')
    if 'font' in value and (not isinstance(value['font'], str) or not 1 <= len(value['font']) <= 200):
        raise ConversionError('글꼴 이름을 확인해 주세요.')
    if value.get('alignment', 'LEFT') not in {'LEFT', 'CENTER', 'RIGHT', 'JUSTIFY'}:
        raise ConversionError('정렬 서식을 확인해 주세요.')
    for key in ('color', 'fill_color', 'highlight'):
        if key in value and not (key == 'fill_color' and value[key] is None) and (not isinstance(value[key], str) or
                                                       not re.fullmatch(r'#[0-9a-fA-F]{6}', value[key])):
            raise ConversionError('문서 색상 서식을 확인해 주세요.')
    for key in ('width_pt', 'height_pt'):
        if key in value and not number(value[key], .01, 20000):
            raise ConversionError('셀 크기를 확인해 주세요.')
    if 'line_spacing_percent' in value and not number(value['line_spacing_percent'], 50, 500):
        raise ConversionError('줄 간격을 확인해 주세요.')
    if value.get('placement', 'INLINE') not in {'INLINE', 'BEHIND_TEXT', 'IN_FIGURE'}:
        raise ConversionError('그림 배치를 확인해 주세요.')
    if value.get('text_direction', 'HORIZONTAL') not in {'HORIZONTAL', 'VERTICAL'}:
        raise ConversionError('셀 글자 방향을 확인해 주세요.')
    if value.get('vertical_alignment', 'CENTER') not in {'TOP', 'CENTER', 'BOTTOM'}:
        raise ConversionError('셀 세로 정렬을 확인해 주세요.')
    if 'baseline_shift' in value and (type(value['baseline_shift']) is not int or not -100 <= value['baseline_shift'] <= 100):
        raise ConversionError('글자 위치를 확인해 주세요.')
    if 'letter_spacing' in value and (type(value['letter_spacing']) is not int or not -50 <= value['letter_spacing'] <= 50):
        raise ConversionError('자간을 확인해 주세요.')
    if any(key in value and not number(value[key], low, 20000) for key, low in (
            ('indent_left_pt', 0), ('first_line_indent_pt', -20000), ('top_pt', -20000), ('left_pt', -20000))):
        raise ConversionError('문단 들여쓰기를 확인해 주세요.')
    if 'line_count' in value and (type(value['line_count']) is not int or not 1 <= value['line_count'] <= 10000):
        raise ConversionError('문단 줄 수를 확인해 주세요.')
    if 'paragraphs' in value:
        paragraphs = value['paragraphs']
        if not isinstance(paragraphs, list) or len(paragraphs) > 1000:
            raise ConversionError('셀 문단 구조를 확인해 주세요.')
        for paragraph in paragraphs:
            if not isinstance(paragraph, dict) or not isinstance(paragraph.get('text'), str) or 'paragraphs' in paragraph:
                raise ConversionError('셀 문단 구조를 확인해 주세요.')
            validate_format(paragraph)
    if 'margins_pt' in value and (not isinstance(value['margins_pt'], dict) or
            any(key not in {'left', 'right', 'top', 'bottom'} or not number(n, 0, 20000)
                for key, n in value['margins_pt'].items())):
        raise ConversionError('셀 안쪽 여백을 확인해 주세요.')
    if 'runs' in value:
        runs = value['runs']
        if not isinstance(runs, list) or len(runs) > 10000:
            raise ConversionError('글자별 서식 구조를 확인해 주세요.')
        for run in runs:
            if not isinstance(run, dict) or not isinstance(run.get('text'), str) or 'runs' in run:
                raise ConversionError('글자별 서식 구조를 확인해 주세요.')
            validate_format(run)
        if runs and ''.join(run['text'] for run in runs) != value['text']:
            raise ConversionError('글자별 서식과 본문이 일치하지 않습니다.')
    if 'borders' in value:
        borders = value['borders']
        if not isinstance(borders, list) or len(borders) > 4:
            raise ConversionError('표 테두리 서식을 확인해 주세요.')
        sides = set()
        for border in borders:
            if (not isinstance(border, dict) or border.get('side') not in {'left', 'right', 'top', 'bottom'}
                    or border['side'] in sides or border.get('type') not in {'NONE', 'SOLID', 'DASH', 'DOT',
                        'DASH_DOT', 'DASH_DOT_DOT', 'DOUBLE_SLIM', 'SLIM_THICK', 'THICK_SLIM', 'SLIM_THICK_SLIM'}
                    or not number(border.get('width_pt'), 0, 20) or not isinstance(border.get('color'), str)
                    or not re.fullmatch(r'#[0-9a-fA-F]{6}', border['color'])):
                raise ConversionError('표 테두리 서식을 확인해 주세요.')
            sides.add(border['side'])


def validate_page(value):
    """Reject malformed coordinates, oversized grids and overlapping merges."""
    if not isinstance(value, dict) or not isinstance(value.get('blocks'), list) or len(value['blocks']) > 1000:
        raise ConversionError('페이지의 문단·표 구조가 올바르지 않습니다.')
    warnings = value.get('warnings', [])
    if not isinstance(warnings, list) or len(warnings) > 100 or not all(isinstance(w, str) and len(w) <= 4000 for w in warnings):
        raise ConversionError('페이지 검토 항목의 형식이 올바르지 않습니다.')
    for block in value['blocks']:
        if not isinstance(block, dict) or block.get('kind') not in {'paragraph', 'table', 'image'}:
            raise ConversionError('지원하지 않는 문서 개체가 있습니다.')
        bbox = block.get('bbox')
        if (not isinstance(bbox, list) or len(bbox) != 4 or
                any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 1000 for v in bbox)
                or bbox[0] >= bbox[2] or bbox[1] >= bbox[3]):
            raise ConversionError('문서 개체의 위치를 인식하지 못했습니다.')
        if not isinstance(block.get('text', ''), str) or len(block.get('text', '')) > 200000:
            raise ConversionError('문단 텍스트를 확인해 주세요.')
        size = block.get('font_size_pt', 10)
        if isinstance(size, bool) or not isinstance(size, (float, int)) or not math.isfinite(size) or not 1 <= size <= 144:
            raise ConversionError('문단의 글자 크기가 올바르지 않습니다.')
        if any(not isinstance(block.get(flag, False), bool) for flag in ('bold', 'italic')):
            raise ConversionError('문단의 글자 서식이 올바르지 않습니다.')
        if block.get('alignment', 'LEFT') not in {'LEFT', 'CENTER', 'RIGHT', 'JUSTIFY'}:
            raise ConversionError('문단의 정렬 방식이 올바르지 않습니다.')
        validate_format(block)
        if block['kind'] != 'table':
            continue
        rows, cols = block.get('rows'), block.get('cols')
        if any(type(v) is not int for v in (rows, cols)) or not 1 <= rows <= 200 or not 1 <= cols <= 60:
            raise ConversionError('표의 행·열 수가 올바르지 않습니다.')
        for key, count in (('column_widths_pt', cols), ('row_heights_pt', rows)):
            if key in block and (not isinstance(block[key], list) or len(block[key]) != count or any(
                    type(n) not in (int, float) or not math.isfinite(n) or not .01 <= n <= 20000 for n in block[key])):
                raise ConversionError('표의 행·열 크기를 확인해 주세요.')
        cells = block.get('cells')
        if not isinstance(cells, list) or len(cells) > 12000:
            raise ConversionError('표의 셀 구조가 올바르지 않습니다.')
        occupied = set()
        for cell in cells:
            if not isinstance(cell, dict) or any(type(cell.get(k)) is not int for k in ('row', 'col', 'row_span', 'col_span')):
                raise ConversionError('표 셀의 위치가 올바르지 않습니다.')
            r, c, rs, cs = (cell[k] for k in ('row', 'col', 'row_span', 'col_span'))
            if r < 0 or c < 0 or rs < 1 or cs < 1 or r + rs > rows or c + cs > cols:
                raise ConversionError('병합 셀이 표의 범위를 벗어났습니다.')
            if not isinstance(cell.get('text'), str) or len(cell['text']) > 200000:
                raise ConversionError('표 셀의 텍스트가 올바르지 않습니다.')
            validate_format(cell)
            addresses = {(i, j) for i in range(r, r + rs) for j in range(c, c + cs)}
            if occupied & addresses:
                raise ConversionError('병합 셀이 서로 겹칩니다.')
            occupied.update(addresses)
        if len(occupied) != rows * cols:
            raise ConversionError('표에 인식되지 않은 셀이 있습니다. 다른 모델로 다시 시도해 주세요.')
    return value

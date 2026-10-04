"""PDF -> editable HWPX. Rendering, credentials and publishing stay separate."""
from __future__ import annotations

from collections import Counter
import json
import os
from pathlib import Path
import re
import zipfile

import pymupdf

from .hwpx_api import ConversionError, DEFAULT_MODEL, GeminiClient, RelayClient, validate_page


def _text(value):
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]', '', value)


def _bbox(rect, page):
    rect = pymupdf.Rect(rect) * page.rotation_matrix
    rect &= page.rect
    return [max(0., min(1000., v)) for v in (
        (rect.x0 - page.rect.x0) * 1000 / page.rect.width,
        (rect.y0 - page.rect.y0) * 1000 / page.rect.height,
        (rect.x1 - page.rect.x0) * 1000 / page.rect.width,
        (rect.y1 - page.rect.y0) * 1000 / page.rect.height)]


def native_page(page):
    """Reuse exact PDF text and ruled table grids, without an API request."""
    blocks, table_boxes, warnings = [], [], []
    try:
        rotation = page.rotation
        try:
            # find_tables analyzes rotated pages in displayed coordinates;
            # text spans use unrotated coordinates. Normalize before finding
            # tables and copy all data before restoring the displayed page.
            page.set_rotation(0)
            finder = page.find_tables()
            tables = [(tuple(table.bbox), table.extract(), table.col_count,
                       [list(row.cells) for row in table.rows], list(table.cells)) for table in finder.tables]
        finally:
            page.set_rotation(rotation)
        for table_bbox, grid, cols, table_rows, table_cells in tables:
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
                    cells.append(dict(row=r, col=c, row_span=rs, col_span=cs, text=_text(grid[r][c] or '')))
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
                                              text=_text(page.get_textbox(rect)).strip()))
                            recovered = True
                cells.sort(key=lambda cell: (cell['row'], cell['col']))
            block = dict(kind='table', bbox=_bbox(table_bbox, page), text='', font_size_pt=10,
                         rows=rows, cols=cols, cells=cells, bold=False, italic=False, alignment='LEFT')
            try:
                validate_page(dict(blocks=[block], warnings=[]))
            except ConversionError:
                warnings.append('병합 셀을 정확히 복원하지 못한 표는 문단으로 추출했습니다.')
                continue
            blocks.append(block)
            table_boxes.append(pymupdf.Rect(table_bbox))
            if recovered:
                warnings.append('표의 일부 경계를 주변 격자로 복원했습니다. 해당 셀을 원본과 대조하세요.')
    except Exception:
        warnings.append('로컬 표 분석에 실패해 해당 내용은 문단으로 추출했습니다.')
    figure_boxes = []
    try:
        for rectangle in page.cluster_drawings():
            rect = pymupdf.Rect(rectangle)
            if rect.get_area() < 400 or any((rect & table).get_area() > rect.get_area() * .5 for table in table_boxes):
                continue
            # A border around the whole page is decoration, not one giant
            # figure that should swallow all editable document content.
            if rect.get_area() > (page.rect.width * page.rect.height) * .8:
                continue
            bbox = _bbox(rect, page)
            if bbox[0] >= bbox[2] or bbox[1] >= bbox[3]:
                continue
            figure_boxes.append(rect)
            blocks.append(dict(kind='image', bbox=bbox, text='', font_size_pt=10,
                               bold=False, italic=False, alignment='LEFT', rows=0, cols=0, cells=[]))
    except Exception:
        warnings.append('일부 벡터 도형을 분석하지 못했습니다. 원본과 대조하세요.')
    for block in page.get_text('dict', sort=True)['blocks']:
        bbox = _bbox(block['bbox'], page)
        if bbox[0] >= bbox[2] or bbox[1] >= bbox[3]:
            continue
        if block['type'] == 1:
            if any(pymupdf.Rect(block['bbox']) in box for box in figure_boxes):
                continue
            blocks.append(dict(kind='image', bbox=bbox, text='', font_size_pt=10,
                               bold=False, italic=False, alignment='LEFT', rows=0, cols=0, cells=[]))
            continue
        lines = []
        spans = []
        for line in block.get('lines', []):
            if any(pymupdf.Rect(line['bbox']).intersects(box) and
                   (pymupdf.Rect(line['bbox']) & box).get_area() / max(1, pymupdf.Rect(line['bbox']).get_area()) > .5 for box in table_boxes + figure_boxes):
                continue
            lines.append(''.join(s['text'] for s in line['spans']))
            spans.extend(line['spans'])
        text = _text('\n'.join(lines)).strip()
        if not text:
            continue
        main = max(spans, key=lambda s: len(s['text']))
        blocks.append(dict(kind='paragraph', bbox=bbox, text=text,
            font_size_pt=max(4., min(96., float(main.get('size', 10)))),
            bold=bool(main.get('flags', 0) & 16), italic=bool(main.get('flags', 0) & 2),
            alignment='LEFT', rows=0, cols=0, cells=[]))
    blocks.sort(key=lambda b: (b['bbox'][1], b['bbox'][0]))
    if page.rotation:
        warnings.append('회전한 페이지의 글자는 가로 문단으로 복원했습니다. 원본의 글자 방향과 대조하세요.')
    if not any(b['kind'] in {'paragraph', 'table'} for b in blocks):
        warnings.append('선택한 페이지에 추출 가능한 글자가 없습니다. 스캔 문서는 Gemini AI 변환을 사용하세요.')
    return validate_page(dict(blocks=blocks, warnings=warnings))


def _coverage(native, extracted):
    def content(page):
        return ''.join(b['text'] if b['kind'] == 'paragraph' else
                       ''.join(c['text'] for c in b.get('cells', [])) for b in page['blocks'])
    original = Counter(c for c in content(native) if c.isalnum())
    actual = Counter(c for c in content(extracted) if c.isalnum())
    count = sum(original.values())
    if count and sum((original - actual).values()) / count > .02:
        extracted['warnings'].append('PDF 텍스트와 비교해 일부 글자의 누락 또는 변경 가능성이 있습니다. 원본과 대조하세요.')


def _add_paragraph(doc, section, block, first=False):
    paragraph = section.paragraphs[0] if first else doc.add_paragraph(section=section, include_run=False, inherit_style=False)
    paragraph.clear_text()
    style = doc.styles.ensure_run(font='맑은 고딕', size=block['font_size_pt'], bold=block.get('bold', False), italic=block.get('italic', False))
    paragraph.add_run(_text(block['text']), char_pr_id_ref=style, expand_special_characters=True)
    doc.styles.apply_paragraph_format(paragraphs=[paragraph], alignment=block.get('alignment', 'LEFT'), line_spacing_percent=100)
    return paragraph


def write_page(doc, page, structured, index):
    section = doc.sections[0] if index == 0 else doc.add_section()
    # The first paragraph contains secPr. Keep it instead of removing section
    # settings or introducing an empty paragraph before every source page.
    if not section.paragraphs:
        doc.add_paragraph(section=section)
    doc.page.set_size(width=round(page.rect.width * 100), height=round(page.rect.height * 100), section=section)
    doc.page.set_margins(left=1800, right=1800, top=1800, bottom=1800, header=0, footer=0, gutter=0, section=section)
    first = True
    counts = dict(paragraphs=0, tables=0, figures=0)
    available_width = max(100, round(page.rect.width * 100) - 3600)
    available_height = max(100, round(page.rect.height * 100) - 3600)
    for block in structured['blocks']:
        if block['kind'] == 'paragraph':
            _add_paragraph(doc, section, block, first)
            first = False
            counts['paragraphs'] += 1
        elif block['kind'] == 'table':
            width = min(available_width, max(100, round((block['bbox'][2] - block['bbox'][0]) * page.rect.width / 10)))
            height = max(block['rows'] * 1200, round((block['bbox'][3] - block['bbox'][1]) * page.rect.height / 10))
            if first:
                table = section.paragraphs[0].add_table(block['rows'], block['cols'], width=width, height=height)
            else:
                table = doc.add_table(block['rows'], block['cols'], width=width, height=height, section=section)
            first = False
            for cell in block['cells']:
                r, c = cell['row'], cell['col']
                if cell['row_span'] > 1 or cell['col_span'] > 1:
                    table.merge_cells(r, c, r + cell['row_span'] - 1, c + cell['col_span'] - 1)
                table.set_cell_text(r, c, _text(cell['text']))
                for paragraph in table.cell(r, c).paragraphs:
                    for run in paragraph.runs:
                        run.char_pr_id_ref = doc.styles.ensure_run(font='맑은 고딕', size=block['font_size_pt'])
            counts['tables'] += 1
        else:
            box = block['bbox']
            clip = pymupdf.Rect(box[0] * page.rect.width / 1000, box[1] * page.rect.height / 1000,
                                box[2] * page.rect.width / 1000, box[3] * page.rect.height / 1000)
            image = page.get_pixmap(clip=clip, dpi=180, alpha=False).tobytes('png')
            width = min(available_width, max(100, round(clip.width * 100)))
            height = max(100, round(width * clip.height / clip.width))
            if height > available_height:
                width = max(100, round(width * available_height / height))
                height = available_height
            image_id = str(doc.media.add_image(image, 'png'))
            paragraph = section.paragraphs[0] if first else doc.add_paragraph(section=section, inherit_style=False)
            paragraph.add_picture(image_id, width=width, height=height)
            first = False
            counts['figures'] += 1
    section.remove_stale_layout_caches()
    return counts


def verify_package(path):
    from hwpx import HwpxDocument
    from hwpx.tools.package_validator import validate_package
    report = validate_package(path)
    if not report.ok:
        raise ConversionError('생성한 HWPX의 패키지 구조 검증에 실패했습니다.')
    with zipfile.ZipFile(path) as archive:
        if archive.read('mimetype') != b'application/hwp+zip':
            raise ConversionError('생성한 파일의 HWPX 형식이 올바르지 않습니다.')
    with HwpxDocument.open(path) as reopened:
        if not reopened.validate().ok:
            raise ConversionError('생성한 HWPX 문서의 XML 검증에 실패했습니다.')


def convert_document(source, output, *, provider='local', model=DEFAULT_MODEL, page_numbers=None,
                     client=None, progress=None):
    """Write only to a staging path; the desktop parent commits after success."""
    from hwpx import HwpxDocument
    from .document import _require_permission
    if provider not in {'local', 'gemini', 'relay'}:
        raise ConversionError('변환 방식을 확인해 주세요.')
    if provider != 'local' and client is None:
        raise ConversionError('Gemini 연결을 먼저 설정해 주세요.')
    progress = progress or (lambda data: None)
    result = dict(pages=0, paragraphs=0, tables=0, figures=0, model=model if provider != 'local' else None,
                  provider=provider, warnings=[], usage=dict(promptTokenCount=0, candidatesTokenCount=0, thoughtsTokenCount=0))
    result['warnings'].append(dict(page=None, message='글꼴은 맑은 고딕으로 대체했습니다. 쪽 여백과 줄바꿈·배치는 원본과 대조해 주세요.'))
    with pymupdf.open(source) as pdf, HwpxDocument.new() as document:
        _require_permission(pdf, pymupdf.PDF_PERM_COPY)
        numbers = list(range(1, len(pdf) + 1)) if page_numbers is None else page_numbers
        if not len(pdf) or len(numbers) != len(pdf) or any(type(n) is not int or n < 1 for n in numbers):
            raise ConversionError('내보낼 PDF와 페이지 번호를 확인해 주세요.')
        if client is not None:
            progress(dict(stage='Gemini 연결 확인', detail='문서 전송 전에 모델 접근 권한을 확인합니다.'))
            client.check()
        for index, page in enumerate(pdf):
            label = numbers[index]
            progress(dict(stage='페이지 분석', completed=index, total=len(pdf), detail=f'원본 {label}쪽 · {model if client else "로컬 추출"}'))
            native = native_page(page)
            if client is None:
                structured = native
                if any(page.annots() or []) or any(page.widgets() or []):
                    structured['warnings'].append('로컬 추출에서는 주석·필기·입력 양식의 표시가 달라질 수 있습니다. 원본과 대조하세요.')
            else:
                scale = min(180 / 72, 2800 / max(page.rect.width, page.rect.height))
                png = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False).tobytes('png')
                structured, usage = client.extract(png, dict(width_pt=page.rect.width, height_pt=page.rect.height,
                                                              blocks=native['blocks']))
                validate_page(structured)
                _coverage(native, structured)
                for key in result['usage']:
                    result['usage'][key] += usage.get(key, 0)
            for message in structured.get('warnings', []):
                result['warnings'].append(dict(page=label, message=message))
            counts = write_page(document, page, structured, index)
            for key, count in counts.items():
                result[key] += count
            result['pages'] += 1
            progress(dict(stage='HWPX 작성', completed=index + 1, total=len(pdf), detail=f'원본 {label}쪽 완료'))
        progress(dict(stage='HWPX 검증', detail='ZIP·XML 구조와 저장 후 다시 열기를 확인합니다.'))
        if not document.validate().ok:
            raise ConversionError('생성할 HWPX의 XML 구조를 검증하지 못했습니다.')
        document.save_to_path(output)
    verify_package(output)
    return result


def make_client(options):
    provider = options['provider']
    if provider == 'local':
        return None
    model = options.get('model', DEFAULT_MODEL)
    if provider == 'gemini':
        from .hwpx_credentials import load_developer_key
        return GeminiClient(load_developer_key(), model)
    return RelayClient(options.get('service_url', ''), os.environ.get('XDF_HWPX_SERVICE_TOKEN', ''), model)


def hwpx_worker(request_path):
    path = Path(request_path)
    result = dict(ok=False, error='HWPX 변환을 완료하지 못했습니다.')
    try:
        task = json.loads(path.read_text(encoding='utf-8'))
        options = task['options']
        client = make_client(options)
        if task.get('operation') == 'check':
            result = dict(ok=True, result=client.check() if client else dict(ok=True, model='local'))
        else:
            def progress(data):
                temporary = path.parent / 'progress.tmp'
                temporary.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
                temporary.replace(path.parent / 'progress.json')
            converted = convert_document(path.parent / 'document.pdf', path.parent / 'output.hwpx',
                provider=options['provider'], model=options.get('model', DEFAULT_MODEL), client=client,
                page_numbers=task.get('page_numbers'), progress=progress)
            (path.parent / 'conversion.json').write_text(json.dumps(converted, ensure_ascii=False, indent=2), encoding='utf-8')
            result = dict(ok=True, result=converted)
    except (ConversionError, PermissionError) as error:
        result = dict(ok=False, error=str(error))
    except ImportError:
        result = dict(ok=False, error='HWPX 모듈을 불러오지 못했습니다. python-hwpx가 포함된 XDF 빌드를 사용해 주세요.')
    except Exception:
        # Never print exception bodies, page content, requests or credentials.
        pass
    (path.parent / 'result.json').write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
    return 0 if result['ok'] else 1

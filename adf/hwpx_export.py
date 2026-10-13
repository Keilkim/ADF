"""PDF -> editable HWPX. Rendering, credentials and publishing stay separate."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import json
import os
from pathlib import Path
import re
from statistics import median
import zipfile

import pymupdf

from .hwpx_api import ConversionError, DEFAULT_MODEL, GeminiClient, RelayClient, validate_page
from .hwpx_formatting import (PdfFormatting, add_runs, apply_cell_format, clean_text as _text, paragraph_format,
                              paragraph_height, reuse_native_formatting, vertical_spacing)


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
    formatting = PdfFormatting(page)
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
            block = dict(kind='table', bbox=_bbox(table_bbox, page), text='', font_size_pt=10,
                         rows=rows, cols=cols, cells=cells, bold=False, italic=False, alignment='LEFT')
            if len(xs) == cols + 1 and len(ys) == rows + 1:
                block['column_widths_pt'] = [b - a for a, b in zip(xs, xs[1:])]
                block['row_heights_pt'] = [b - a for a, b in zip(ys, ys[1:])]
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
    images = [pymupdf.Rect(image['bbox']) for image in page.get_image_info()]
    inside = lambda rect: [line for line in formatting.lines if pymupdf.Rect(line['bbox']).intersects(rect) and
                           (pymupdf.Rect(line['bbox']) & rect).get_area() > .5 * max(1, pymupdf.Rect(line['bbox']).get_area())]
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
            if (formatting.is_frame(rect) and inside(rect)
                    and not any(image.intersects(rect) for image in images)):
                # A ruled box around text (a title box) stays editable as a
                # one-cell table instead of a picture of its text.
                cell = dict(row=0, col=0, row_span=1, col_span=1, **formatting.cell(rect, ''))
                blocks.append(dict(kind='table', bbox=bbox, text='', font_size_pt=10, rows=1, cols=1, cells=[cell],
                                   bold=False, italic=False, alignment='LEFT',
                                   column_widths_pt=[rect.width], row_heights_pt=[rect.height]))
                table_boxes.append(rect)
                continue
            figure_boxes.append(rect)
            blocks.append(dict(kind='image', bbox=bbox, text='', font_size_pt=10,
                               bold=False, italic=False, alignment='LEFT', rows=0, cols=0, cells=[]))
    except Exception:
        warnings.append('일부 벡터 도형을 분석하지 못했습니다. 원본과 대조하세요.')
    for image in images:
        if not any(image in box for box in figure_boxes):
            bbox = _bbox(image, page)
            if bbox[0] < bbox[2] and bbox[1] < bbox[3]:
                blocks.append(dict(kind='image', bbox=bbox, text='', font_size_pt=10,
                                   bold=False, italic=False, alignment='LEFT', rows=0, cols=0, cells=[]))
    covered = {id(line) for box in table_boxes + figure_boxes for line in inside(box)}
    paragraphs = formatting.paragraphs([line for line in formatting.lines if id(line) not in covered])
    # Single-line paragraphs take the pitch that the page's wrapped
    # paragraphs use, so headings and body lines keep the PDF rhythm.
    measured = [(paragraph['line_spacing_percent'], paragraph['line_count']) for paragraph in paragraphs
                if paragraph['line_count'] > 1]
    spacing = median([percent for percent, count in measured for _ in range(count)]) if measured else 160
    for paragraph in paragraphs:
        paragraph.setdefault('line_spacing_percent', round(spacing))
        x0, y0, x1, y1 = paragraph.pop('box_pt')
        bbox = _bbox((x0, y0, x1, y1), page)
        if bbox[0] < bbox[2] and bbox[1] < bbox[3] and paragraph['text'].strip():
            blocks.append(dict(kind='paragraph', bbox=bbox, rows=0, cols=0, cells=[], **paragraph))
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


def _sync_manifest(document):
    """Keep every embedded image listed after sections were added.

    python-hwpx records images in the package manifest but writes its own
    copy when a section was added, which loses the images added since
    then. Hancom shows a missing-picture placeholder for such an image.
    """
    namespace = '{http://www.idpf.org/2007/opf/}manifest'
    target = document.oxml.manifest.find(namespace)
    source = document.package.manifest_tree().find(namespace)
    if target is None or source is None or target is source:
        return
    known = {item.get('id') for item in target}
    for item in source:
        if item.get('id') not in known:
            target.append(deepcopy(item))


def _picture_source_size(element, width_px, height_px):
    """Describe the embedded bitmap by its pixels, as Hancom does.

    python-hwpx records the display size as the image's own size. Hancom
    reads that as a crop of a bitmap measured at 96 dpi (75 HWPUNIT per
    pixel), so a higher-resolution picture showed only its top-left part.
    """
    width, height = width_px * 75, height_px * 75
    for child in element.iter():
        name = child.tag.rsplit('}', 1)[-1]
        if name == 'imgClip':
            child.set('left', '0'); child.set('top', '0')
            child.set('right', str(width)); child.set('bottom', str(height))
        elif name == 'imgDim':
            child.set('dimwidth', str(width)); child.set('dimheight', str(height))


def write_page(doc, page, structured, index):
    section = doc.sections[0] if index == 0 else doc.add_section()
    # The first paragraph contains secPr. Keep it instead of removing section
    # settings or introducing an empty paragraph before every source page.
    if not section.paragraphs:
        doc.add_paragraph(section=section)
    width, height = page.rect.width, page.rect.height
    boxes = [pymupdf.Rect(b['bbox'][0] * width / 1000, b['bbox'][1] * height / 1000,
                          b['bbox'][2] * width / 1000, b['bbox'][3] * height / 1000) for b in structured['blocks']]
    # The page margins enclose the PDF content so paragraphs wrap at the
    # same width and start at the same place as in the source page.
    area = pymupdf.Rect(boxes[0]) if boxes else pymupdf.Rect()
    for box in boxes[1:]:
        area |= box
    if area.width < 72 or area.height < 36:
        area = pymupdf.Rect(18, 18, width - 18, height - 18)
    margins = dict(left=max(0, area.x0), right=max(0, width - area.x1), top=max(0, area.y0),
                   bottom=min(max(0, height - area.y1), 10))
    doc.page.set_size(width=round(width * 100), height=round(height * 100), section=section)
    doc.page.set_margins(**{key: round(value * 100) for key, value in margins.items()},
                         header=0, footer=0, gutter=0, section=section)
    first = True
    border_cache = {}
    counts = dict(paragraphs=0, tables=0, figures=0)
    available_width = max(100, round(area.width * 100))
    available_height = max(100, round((height - margins['top'] - margins['bottom']) * 100))
    # Pictures side by side in the PDF (a row of logos) share one line.
    items = []
    for block, box in zip(structured['blocks'], boxes):
        band = pymupdf.Rect(items[-1][0][1]) if items else None
        for _, other in items[-1] if items else []:
            band |= other
        if (block['kind'] == 'image' and items and all(member['kind'] == 'image' for member, _ in items[-1])
                and min(box.y1, band.y1) - max(box.y0, band.y0) > .5 * min(box.height, band.height)):
            items[-1].append((block, box))
        else:
            items.append([(block, box)])
    bands = []
    for item in items:
        band = pymupdf.Rect(item[0][1])
        for _, box in item[1:]:
            band |= box
        bands.append(band)
    heights = [paragraph_height(item[0][0]) if item[0][0]['kind'] == 'paragraph' else band.height
               for item, band in zip(items, bands)]
    spacing = vertical_spacing([(band.y0, h) for band, h in zip(bands, heights)], margins['top'])
    for item, band, before in zip(items, bands, spacing):
        block, box = item[0]
        if block['kind'] == 'paragraph':
            paragraph = section.paragraphs[0] if first else doc.add_paragraph(section=section, include_run=False, inherit_style=False)
            layout = dict(block)
            if 'indent_left_pt' not in layout and layout.get('alignment', 'LEFT') in ('LEFT', 'JUSTIFY'):
                layout['indent_left_pt'] = max(0, box.x0 - area.x0)
            add_runs(doc, paragraph, layout, before)
            first = False
            counts['paragraphs'] += 1
            continue
        # Tables and pictures sit in their own paragraph; place it with the
        # source offset instead of the default left edge.
        placement = dict(alignment='LEFT', line_spacing_percent=100, indent_left_pt=max(0, band.x0 - area.x0))
        if len(item) == 1 and abs((box.x0 - area.x0) - (area.x1 - box.x1)) < 3:
            placement = dict(alignment='CENTER', line_spacing_percent=100)
        if block['kind'] == 'table':
            table_width = min(available_width, max(100, round(box.width * 100)))
            table_height = max(100, round(box.height * 100))
            if block.get('row_heights_pt'):
                table_height = max(100, round(sum(block['row_heights_pt']) * 100))
            host = section.paragraphs[0] if first else doc.add_paragraph(section=section, include_run=False, inherit_style=False)
            table = host.add_table(block['rows'], block['cols'], width=table_width, height=table_height)
            first = False
            for cell in block['cells']:
                r, c = cell['row'], cell['col']
                if cell['row_span'] > 1 or cell['col_span'] > 1:
                    table.merge_cells(r, c, r + cell['row_span'] - 1, c + cell['col_span'] - 1)
                apply_cell_format(doc, table, cell, block, border_cache)
                if block.get('row_heights_pt'):
                    table.cell(r, c).set_size(height=round(sum(
                        block['row_heights_pt'][r:r + cell['row_span']]) * 100))
            if block.get('column_widths_pt'):
                table.set_column_widths(block['column_widths_pt'])
            counts['tables'] += 1
        else:
            host = section.paragraphs[0] if first else doc.add_paragraph(section=section, inherit_style=False)
            first = False
            right = band.x0
            for _, box in sorted(item, key=lambda member: member[1].x0):
                clip = box & page.rect
                pixmap = page.get_pixmap(clip=clip, dpi=180, alpha=False)
                image_width = min(available_width, max(100, round(clip.width * 100)))
                image_height = max(100, round(image_width * clip.height / clip.width))
                if image_height > available_height:
                    image_width = max(100, round(image_width * available_height / image_height))
                    image_height = available_height
                image_id = str(doc.media.add_image(pixmap.tobytes('png'), 'png'))
                picture = host.add_picture(image_id, width=image_width, height=image_height)
                _picture_source_size(picture.element, pixmap.width, pixmap.height)
                # The gap to the previous picture of the row is its margin.
                margin = next((child for child in picture.element if child.tag.endswith('}outMargin')), None)
                if margin is not None and clip.x0 > right:
                    margin.set('left', str(round((clip.x0 - right) * 100)))
                right = clip.x1
                counts['figures'] += 1
        paragraph_format(doc, host, placement, before)
    # Cached line layouts from the template describe other content; Hancom
    # then draws later lines over pictures. Let Hancom lay out every line.
    section.remove_layout_caches()
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
    result['warnings'].append(dict(page=None, message='PDF의 글자·표 서식을 보존합니다. 없는 글꼴은 한글에서 대체될 수 있으며, 쪽 여백·줄바꿈·배치와 스캔 인식 결과는 원본과 대조해 주세요.'))
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
                reuse_native_formatting(native, structured)
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
        _sync_manifest(document)
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
                try:
                    temporary.replace(path.parent / 'progress.json')
                except PermissionError:
                    # Windows may deny replacement while the UI reads the old
                    # progress file. The next update will publish fresh data.
                    # A display update must not discard a completed conversion.
                    pass
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

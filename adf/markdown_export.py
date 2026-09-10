"""Local layout/OCR export. Workers write only a private staging directory."""
from dataclasses import dataclass
from html import escape
from html.parser import HTMLParser
import base64
import io
import json
import math
import os
from pathlib import Path
import re
from urllib.parse import quote

import pymupdf

from .document import _open_pdf, _require_permission
from .ocr_models import MODELS
from .task_progress import ProgressWriter, write_result

PICTURE_LABELS = {'image', 'chart', 'seal', 'header_image', 'footer_image',
                  'display_formula', 'inline_formula'}


@dataclass
class Word:
    box: tuple
    text: str
    score: float = 1.


def markdown_text(text):
    """PDF text is content, never executable HTML or a Markdown image/link."""
    text = escape(text, quote=False)
    return re.sub(r'([\\`*_{}\[\]()#+.!|>\-])', r'\\\1', text)


def overlap(inner, outer):
    x0, y0, x1, y1 = inner
    a, b, c, d = outer
    area = max(0, min(x1, c)-max(x0, a))*max(0, min(y1, d)-max(y0, b))
    return area/max(1, (x1-x0)*(y1-y0))


def words_in(words, box):
    return [word for word in words if overlap(word.box, box) > .45]


def lines_of(words):
    lines = []
    for word in sorted(words, key=lambda w: ((w.box[1]+w.box[3])/2, w.box[0])):
        center = (word.box[1]+word.box[3])/2
        height = max(1, word.box[3]-word.box[1])
        if not lines or abs(center-lines[-1][0]) > height*.5:
            lines.append((center, [word]))
        else:
            lines[-1][1].append(word)
    return [' '.join(word.text for word in sorted(line, key=lambda w: w.box[0])) for _, line in lines]


class TableMarkup(HTMLParser):
    """Keep table structure while escaping extracted text and other markup."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.rows = []
        self.cell = None
        self.merged = False

    def handle_starttag(self, tag, attrs):
        if tag not in {'table', 'thead', 'tbody', 'tr', 'td', 'th', 'br'}:
            return
        safe = ''
        if tag in {'td', 'th'}:
            self.cell = []
            for key, value in attrs:
                if key in {'rowspan', 'colspan'} and value and value.isdecimal() and 1 <= int(value) <= 10000:
                    safe += f' {key}="{int(value)}"'
                    self.merged |= int(value) > 1
        elif tag == 'tr':
            self.rows.append([])
        elif tag == 'br' and self.cell is not None:
            self.cell.append(' ')
        self.parts.append('<'+tag+safe+'>')

    def handle_endtag(self, tag):
        if tag not in {'table', 'thead', 'tbody', 'tr', 'td', 'th'}:
            return
        if tag in {'td', 'th'} and self.cell is not None:
            if self.rows:
                self.rows[-1].append(''.join(self.cell).strip())
            self.cell = None
        self.parts.append('</'+tag+'>')

    def handle_data(self, data):
        self.parts.append(escape(data))
        if self.cell is not None:
            self.cell.append(data)

    def markdown(self):
        rows = [row for row in self.rows if row]
        if not rows:
            return ''
        width = max(map(len, rows))
        if self.merged or any(len(row) != width for row in rows):
            return ''.join(self.parts)
        line = lambda row: '| '+' | '.join(markdown_text(cell).replace('\n', '<br>') for cell in row)+' |'
        return '\n'.join([line(rows[0]), '| '+' | '.join(['---']*width)+' |', *(line(row) for row in rows[1:])])


class LayoutOcrEngine:
    def __init__(self, models, progress=lambda *a, **kw: None):
        models = Path(models)
        missing = [entry['name'] for entry in MODELS if not (models/entry['name']).is_file()]
        if missing:
            raise FileNotFoundError('OCR 모델이 없습니다. 모델이 포함된 ADF 설치 파일로 다시 설치해 주세요.\n'+', '.join(missing))
        progress('인식 엔진 준비', 0, 3, '한글·영어 OCR을 준비합니다.')
        from rapidocr import RapidOCR, OCRVersion, ModelType, LangRec
        from rapid_layout import RapidLayout, ModelType as LayoutType
        from rapid_table import RapidTable, RapidTableInput
        threads = max(1, min(4, (os.cpu_count() or 2)-1))
        params = {'Det.ocr_version': OCRVersion.PPOCRV5, 'Det.model_type': ModelType.SERVER,
                  'Rec.ocr_version': OCRVersion.PPOCRV5, 'Rec.model_type': ModelType.MOBILE,
                  'Rec.lang_type': LangRec.KOREAN, 'Global.use_cls': False,
                  'Det.mean': [.485, .456, .406], 'Det.std': [.229, .224, .225],
                  'Global.log_level': 'warning', 'Global.max_side_len': 2000,
                  'Global.model_root_dir': str(models),
                  'Det.model_path': str(models/'ch_PP-OCRv5_det_server.onnx'),
                  'Cls.model_path': str(models/'ch_ppocr_mobile_v2.0_cls_mobile.onnx'),
                  'Rec.model_path': str(models/'korean_PP-OCRv5_rec_mobile.onnx'),
                  'EngineConfig.onnxruntime.intra_op_num_threads': threads,
                  'EngineConfig.onnxruntime.inter_op_num_threads': 1}
        self.ocr = RapidOCR(params=params)
        progress('인식 엔진 준비', 1, 3, '문단·표·그림과 읽는 순서를 분석하는 모델을 준비합니다.')
        self.layout = RapidLayout(model_type=LayoutType.PP_DOC_LAYOUTV3,
            model_dir_or_path=str(models/'pp_doc_layoutv3.onnx'), conf_thresh=.4,
            engine_cfg={'intra_op_num_threads': threads, 'inter_op_num_threads': 1})
        progress('인식 엔진 준비', 2, 3, '표 구조 복원을 준비합니다.')
        self.table = RapidTable(RapidTableInput(use_ocr=False,
            model_dir_or_path=str(models/'slanet-plus.onnx'),
            engine_cfg={'intra_op_num_threads': threads, 'inter_op_num_threads': 1}))
        progress('인식 엔진 준비', 3, 3)

    def regions(self, image):
        result = self.layout(image)
        if result.boxes is None:
            return []
        return [dict(box=list(map(float, box)), label=label, score=float(score))
                for box, label, score in zip(result.boxes, result.class_names, result.scores)]

    def recognize(self, image):
        result = self.ocr(image)
        if result.boxes is None:
            return []
        return [Word((float(box[:, 0].min()), float(box[:, 1].min()),
                      float(box[:, 0].max()), float(box[:, 1].max())), text, float(score))
                for box, text, score in zip(result.boxes, result.txts, result.scores)]

    def table_markdown(self, image, words):
        import numpy as np
        structures, cells = self.table.table_structure([image])
        boxes = np.array([word.box for word in words], dtype=np.float32)
        if not words:
            return ''
        html = self.table.table_matcher(structures, cells, [boxes],
                                      [[(escape(word.text), word.score) for word in words]])[0]
        parsed = TableMarkup()
        parsed.feed(html)
        return parsed.markdown()


def native_words(page, width, height):
    result = []
    sx, sy = width/page.rect.width, height/page.rect.height
    for word in page.get_text('words'):
        text = word[4].strip()
        if not text or '\ufffd' in text or text.startswith('(cid:'):
            continue
        rect = pymupdf.Rect(word[:4])*page.rotation_matrix
        result.append(Word((rect.x0*sx, rect.y0*sy, rect.x1*sx, rect.y1*sy), text))
    return result


def validate_markdown_name(name):
    if not isinstance(name, str) or not name.lower().endswith('.md') or not name[:-3].strip() or \
            re.search(r'[<>:"/\\|?*\x00-\x1f]', name) or \
            len(name.encode('utf-8')) > 210 or \
            re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', name, re.I):
        raise ValueError('Markdown 파일 이름을 확인해 주세요.')
    return name


def write_image(markdown, image, output, name, caption, storage):
    """Stream one PNG into the Markdown or save a relative local asset."""
    if storage == 'files':
        path = output/name
        path.parent.mkdir(exist_ok=True)
        image.save(path)
        markdown.write(f'![{caption}]({quote(name, safe="/")})\n\n')
        return {'image': name}
    markdown.write(f'<!-- image: {quote(name, safe="/")} -->\n![{caption}](data:image/png;base64,')
    with io.BytesIO() as data:
        image.save(data, format='PNG')
        data.seek(0)
        # Multiples of three preserve Base64 boundaries without a huge string.
        while chunk := data.read(57*1024):
            markdown.write(base64.b64encode(chunk).decode('ascii'))
    markdown.write(')\n\n')
    return {'image_in_markdown': name}


def convert_document(source, output, models, *, page_numbers=None, password=None,
                     force_ocr=False, include_pages=True, image_storage='embedded',
                     markdown_name='document.md', dpi=216, progress=None, engine=None):
    """Create a new bundle; caller publishes it only after this succeeds."""
    from PIL import Image
    import numpy as np
    progress = progress or (lambda *args, **kwargs: None)
    validate_markdown_name(markdown_name)
    asset_prefix = markdown_name[:-3]
    if image_storage not in {'embedded', 'files'}:
        raise ValueError('이미지 저장 방식을 확인해 주세요.')
    output = Path(output)
    if output.exists():
        raise FileExistsError('내보낼 임시 폴더가 이미 있습니다.')
    with _open_pdf(source, password) as document:
        _require_permission(document, pymupdf.PDF_PERM_COPY)
        total = document.page_count
        page_numbers = list(page_numbers) if page_numbers is not None else list(range(1, total+1))
        if len(page_numbers) != total:
            raise ValueError('선택한 페이지 정보가 PDF와 일치하지 않습니다.')
        engine = engine or LayoutOcrEngine(models, progress)
        output.mkdir()
        records, warnings = [], []
        figures = tables = ocr_pages = 0
        with (output/markdown_name).open('w', encoding='utf-8', newline='\n') as markdown:
            for index in range(total):
                number = page_numbers[index]
                detail = f'{index+1} / {total}페이지 · 원본 {number}페이지'
                progress('페이지 이미지 준비', index, total, detail, unit='ocr_pages')
                page = document[index]
                scale = min(dpi/72, math.sqrt(12_000_000/max(1, page.rect.get_area())))
                pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csRGB, alpha=False)
                pil = Image.frombytes('RGB', (pixmap.width, pixmap.height), pixmap.samples)
                # RapidAI's ndarray interface uses BGR, while Pillow/PyMuPDF use RGB.
                pixels = np.array(pil)[:, :, ::-1].copy()
                progress('레이아웃과 읽는 순서 분석', index, total, detail, unit='ocr_pages')
                regions = engine.regions(pixels)
                embedded = [] if force_ocr else native_words(page, pixmap.width, pixmap.height)
                needs_ocr = force_ocr or not embedded or any(
                    region['label'] not in PICTURE_LABELS and not words_in(embedded, region['box'])
                    for region in regions)
                recognized = []
                if needs_ocr:
                    progress('한글·영어 글자 인식', index, total, detail, unit='ocr_pages')
                    recognized = engine.recognize(pixels)
                    ocr_pages += 1
                if not regions:
                    regions = [dict(box=[0, 0, pixmap.width, pixmap.height], label='text', score=0)]
                    warnings.append(dict(page=number, reason='레이아웃을 구분하지 못해 인식한 글을 순서대로 저장했습니다.'))
                markdown.write(f'<!-- 원본 {number}페이지 -->\n\n')
                page_record = dict(page=number, width=pixmap.width, height=pixmap.height, blocks=[])
                used_native = set()
                for order, region in enumerate(regions):
                    progress('표·그림·본문 정리', index, total, detail+f' · 영역 {order+1} / {len(regions)}', unit='ocr_pages')
                    x0, y0, x1, y1 = region['box']
                    box = (max(0, int(x0)-3), max(0, int(y0)-3),
                           min(pixmap.width, math.ceil(x1)+3), min(pixmap.height, math.ceil(y1)+3))
                    if box[2] <= box[0] or box[3] <= box[1]:
                        continue
                    label = region['label']
                    native = words_in(embedded, box)
                    used_native.update(id(word) for word in native)
                    words = native or words_in(recognized, box)
                    text = '\n'.join(lines_of(words))
                    item = dict(order=order, label=label, box=list(box), layout_confidence=region['score'],
                                text_source='pdf' if native else 'ocr', text=text)
                    if words and not native:
                        item['ocr_confidence'] = min(word.score for word in words)
                        if item['ocr_confidence'] < .85 and label not in PICTURE_LABELS:
                            warnings.append(dict(page=number, block=order, reason='인식 확신도가 낮은 글자가 있습니다.'))
                    if label in PICTURE_LABELS or label == 'table':
                        name = f'images/{asset_prefix}_p{number:05d}_{order+1:03d}.png'
                        if label == 'table':
                            shifted = [Word((w.box[0]-box[0], w.box[1]-box[1], w.box[2]-box[0], w.box[3]-box[1]), w.text, w.score) for w in words]
                            try:
                                table_text = engine.table_markdown(pixels[box[1]:box[3], box[0]:box[2]], shifted)
                            except Exception as error:
                                table_text = ''
                                warnings.append(dict(page=number, block=order, reason='표 구조 복원 실패: '+str(error)))
                            if table_text:
                                markdown.write(table_text+'\n\n')
                                tables += 1
                            else:
                                warnings.append(dict(page=number, block=order, reason='표를 원본 이미지로 보존했습니다.'))
                            caption = '표 원본'
                        else:
                            figures += 1
                            caption = '그래프' if label == 'chart' else '그림'
                        progress('이미지를 MD에 포함' if image_storage == 'embedded' else '이미지 파일 저장',
                                 index, total, detail, unit='ocr_pages')
                        item.update(write_image(markdown, pil.crop(box), output, name,
                                                f'{caption} · {number}페이지', image_storage))
                    elif text:
                        prefix = '# ' if label == 'doc_title' else '## ' if label == 'paragraph_title' else ''
                        markdown.write(prefix+markdown_text(text)+'\n\n')
                    else:
                        name = f'images/{asset_prefix}_p{number:05d}_{order+1:03d}.png'
                        item.update(write_image(markdown, pil.crop(box), output, name,
                                                f'인식되지 않은 영역 · {number}페이지', image_storage))
                        warnings.append(dict(page=number, block=order, reason='글자를 읽지 못한 영역을 이미지로 보존했습니다.'))
                    page_record['blocks'].append(item)
                remaining = [word for word in embedded if id(word) not in used_native]
                if remaining:
                    markdown.write(markdown_text('\n'.join(lines_of(remaining)))+'\n\n')
                    warnings.append(dict(page=number, reason='레이아웃 밖의 PDF 텍스트를 페이지 끝에 추가했습니다.'))
                if include_pages:
                    name = f'pages/{asset_prefix}_p{number:05d}.png'
                    page_record['original'] = write_image(markdown, pil, output, name,
                        f'원본 {number}페이지 전체 이미지', image_storage)
                markdown.write('---\n\n')
                records.append(page_record)
                progress('페이지 변환', index+1, total, detail, unit='ocr_pages')
        result = dict(pages=total, figures=figures, tables=tables, ocr_pages=ocr_pages,
                      warnings=warnings, page_numbers=page_numbers,
                      image_storage=image_storage, markdown_file=markdown_name)
        progress('Markdown 묶음 확인', detail='본문과 이미지 파일의 연결을 확인합니다.')
        (output/'layout.json').write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
        (output/'conversion.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        storage_note = (f'{markdown_name} 안에 모든 이미지가 Base64로 들어 있습니다. MD 파일만 옮겨도 이미지 데이터가 유지됩니다.\n'
                        '일부 Markdown 뷰어는 내장 이미지를 표시하지 않습니다. 이 경우 이미지 폴더 분리로 다시 내보내세요.\n'
                        if image_storage == 'embedded' else f'{markdown_name}와 images 폴더를 함께 보관하세요. pages 폴더가 있으면 함께 옮기세요.\n')
        (output/'README.txt').write_text(storage_note+
            '표의 병합 셀은 Markdown 안의 HTML 표로 보존합니다. 그래프와 도형은 이미지입니다.\n'
            'layout.json은 읽는 순서와 위치·인식 확신도이며, image_in_markdown은 MD 안의 이미지 식별자입니다.\n'
            'OCR 결과에는 오인식이 있을 수 있습니다. conversion.json의 확인 항목과 원본을 비교하세요.\n', encoding='utf-8')
        return result


def markdown_worker(request):
    task = json.loads(Path(request).read_text(encoding='utf-8'))
    progress = ProgressWriter(task['progress'])
    try:
        result = convert_document(task['source'], task['staging'], task['models'],
            page_numbers=task.get('page_numbers'), password=task.get('password'),
            force_ocr=task.get('force_ocr', False), include_pages=task.get('include_pages', True),
            image_storage=task.get('image_storage', 'embedded'), markdown_name=task.get('markdown_name', 'document.md'),
            progress=progress)
        result = dict(ok=True, result=result)
    except Exception as error:
        result = dict(ok=False, error=str(error))
    write_result(task['result'], result)
    return 0 if result['ok'] else 1

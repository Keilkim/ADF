"""PDF -> editable HWPX pipeline. Writes only to the given staging path."""
import zipfile

import pymupdf

from .ai import DEFAULT_MODEL
from .extract import native_page
from .hwpx_writer import _sync_manifest, write_page
from .reconcile import _coverage, reuse_native_formatting
from .schema import ConversionError, validate_page


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


def _require_copy_permission(pdf):
    """The owner may forbid copying text out of the PDF. A host app that
    opened it with the owner password marks it _adf_owner_authenticated."""
    if not (getattr(pdf, '_adf_owner_authenticated', False) or pdf.permissions & pymupdf.PDF_PERM_COPY):
        raise PermissionError('이 PDF는 소유자가 편집 또는 추출을 제한했습니다. 소유자 암호로 다시 열어 주세요.')


def convert_document(source, output, *, provider='local', model=DEFAULT_MODEL, page_numbers=None,
                     client=None, progress=None):
    """Write only to a staging path; the desktop parent commits after success."""
    from hwpx import HwpxDocument
    if provider not in {'local', 'gemini', 'relay'}:
        raise ConversionError('변환 방식을 확인해 주세요.')
    if provider != 'local' and client is None:
        raise ConversionError('Gemini 연결을 먼저 설정해 주세요.')
    progress = progress or (lambda data: None)
    result = dict(pages=0, paragraphs=0, tables=0, figures=0, model=model if provider != 'local' else None,
                  provider=provider, warnings=[], usage=dict(promptTokenCount=0, candidatesTokenCount=0, thoughtsTokenCount=0))
    result['warnings'].append(dict(page=None, message='PDF의 글자·표 서식을 보존합니다. 없는 글꼴은 한글에서 대체될 수 있으며, 쪽 여백·줄바꿈·배치와 스캔 인식 결과는 원본과 대조해 주세요.'))
    with pymupdf.open(source) as pdf, HwpxDocument.new() as document:
        _require_copy_permission(pdf)
        numbers = list(range(1, len(pdf) + 1)) if page_numbers is None else page_numbers
        if not len(pdf) or len(numbers) != len(pdf) or any(type(n) is not int or n < 1 for n in numbers):
            raise ConversionError('내보낼 PDF와 페이지 번호를 확인해 주세요.')
        if client is not None:
            progress(dict(stage='Gemini 연결 확인', detail='문서 전송 전에 모델 접근 권한을 확인합니다.'))
            client.check()
        for index, page in enumerate(pdf):
            label = numbers[index]
            progress(dict(stage='페이지 분석', completed=index, total=len(pdf), detail=f'원본 {label}쪽 · {model if client else "로컬 추출"}'))
            try:
                native = native_page(page)
            except Exception:
                # One unreadable page must not stop the document: it goes in
                # as a picture of the page, with a warning to check it.
                native = dict(blocks=[dict(kind='image', bbox=[0, 0, 1000, 1000], text='', font_size_pt=10, bold=False,
                                           italic=False, alignment='LEFT', rows=0, cols=0, cells=[])],
                              warnings=['이 쪽은 구조를 읽지 못해 쪽 그림으로 넣었습니다(편집 불가). 원본과 대조하세요.'])
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

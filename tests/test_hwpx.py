"""HWPX structure, transcription integrity, credential and publication checks."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import urllib.error
from unittest.mock import patch
from types import SimpleNamespace
import zipfile

import pymupdf
import pytest
from PIL import Image
from hwpx import HwpxDocument

from adf.hwpx_api import ConversionError, GeminiClient, DEFAULT_MODEL, validate_page, request_json, service_url
from adf.hwpx_credentials import create_bundle, load_bundle
from adf.hwpx_export import convert_document, native_page, hwpx_worker


def sample_pdf(path, rotated=False):
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=595, height=842)
        page.insert_text((50, 60), 'Editable heading', fontsize=18)
        page.insert_text((50, 85), 'Exact amount 1,234.56 and punctuation.', fontsize=11)
        for y in (110, 150, 190):
            page.draw_line((50, y), (350, y))
        for x in (50, 350):
            page.draw_line((x, 110), (x, 190))
        page.draw_line((200, 150), (200, 190))
        for point, text in [((65, 135), 'Merged heading'), ((65, 175), 'Left cell'), ((215, 175), 'Right cell')]:
            page.insert_text(point, text)
        image = io.BytesIO()
        Image.new('RGB', (120, 80), (240, 75, 45)).save(image, format='PNG')
        page.insert_image(pymupdf.Rect(50, 220, 170, 300), stream=image.getvalue())
        if rotated:
            page.set_rotation(90)
        page = pdf.new_page(width=842, height=595)
        font = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / 'malgun.ttf'
        if font.is_file():
            page.insert_text((50, 70), '한글 원문과 숫자 2026을 유지합니다.', fontname='korean', fontfile=str(font))
        else:
            page.insert_text((50, 70), 'Second page, 2026.')
        pdf.save(path)


def test_local_output_contains_editable_text_merged_table_images_and_page_geometry(tmp_path):
    source, output = tmp_path / 'source.pdf', tmp_path / 'result.hwpx'
    sample_pdf(source)
    original = source.read_bytes()
    result = convert_document(source, output)
    assert result['pages'] == 2
    assert result['tables'] == 1
    assert result['figures'] == 1
    assert result['paragraphs'] == 3
    assert source.read_bytes() == original
    with HwpxDocument.open(output) as document:
        assert document.validate().ok
        text = document.text.plain()
        assert 'Exact amount 1,234.56 and punctuation.' in text
        assert text.count('Merged heading') == 1
        table = list(document.tables)[0]
        assert table.cell(0, 0).span == (1, 2)
        assert table.cell(1, 1).text == 'Right cell'
        assert len(document.sections) == 2
        assert document.sections[1].properties.page_size.width == 84200
        assert document.sections[1].properties.page_size.height == 59500
        if '한글' in ''.join(p.get_text() for p in pymupdf.open(source)):
            assert '한글 원문과 숫자 2026을 유지합니다.' in text
    with zipfile.ZipFile(output) as archive:
        assert archive.read('mimetype') == b'application/hwp+zip'
        assert any(name.startswith('BinData/') for name in archive.namelist())


def test_rotated_table_coordinates_normalized_without_changing_source(tmp_path):
    source = tmp_path / 'source.pdf'
    sample_pdf(source, rotated=True)
    with pymupdf.open(source) as pdf:
        result = native_page(pdf[0])
        assert pdf[0].rotation == 90
    assert len([b for b in result['blocks'] if b['kind'] == 'table']) == 1
    assert any('회전' in message for message in result['warnings'])


def test_missing_border_cells_keep_their_editable_text(tmp_path):
    source = tmp_path / 'missing-borders.pdf'
    boxes = [[(50, 100, 150, 140), (150, 100, 350, 140)],
             [(50, 140, 150, 180), None],
             [(50, 180, 150, 220), (150, 180, 350, 220)],
             [(50, 220, 150, 260), None]]
    grid = [['Name', 'Role'], ['Company', None], ['City', 'Install lights'], ['Foundation', None]]
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        for point, text in [((60, 125), 'Name'), ((160, 125), 'Role'),
                            ((60, 165), 'Company'), ((160, 165), 'Fund the project'),
                            ((60, 205), 'City'), ((160, 205), 'Install lights'),
                            ((60, 245), 'Foundation'), ((160, 245), 'Coordinate partners')]:
            page.insert_text(point, text)
        pdf.save(source)
    table = SimpleNamespace(bbox=(50, 100, 350, 260), col_count=2,
        extract=lambda: grid, rows=[SimpleNamespace(cells=row) for row in boxes],
        cells=[box for row in boxes for box in row if box])
    finder = SimpleNamespace(tables=[table])
    with patch.object(pymupdf.Page, 'find_tables', return_value=finder):
        result = convert_document(source, tmp_path / 'result.hwpx')
    assert result['tables'] == 1
    assert result['paragraphs'] == 0
    with HwpxDocument.open(tmp_path / 'result.hwpx') as document:
        restored = list(document.tables)[0]
        assert restored.cell(1, 1).text == 'Fund the project'
        assert restored.cell(3, 1).text == 'Coordinate partners'
        assert document.text.plain().count('Fund the project') == 1


def test_scanned_page_is_preserved_and_reports_missing_editable_text(tmp_path):
    source = tmp_path / 'scan.pdf'
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        image = io.BytesIO()
        Image.new('RGB', (100, 100), 'white').save(image, format='PNG')
        page.insert_image(page.rect, stream=image.getvalue())
        pdf.save(source)
    result = convert_document(source, tmp_path / 'scan.hwpx')
    assert result['figures'] == 1
    assert result['paragraphs'] == 0
    assert any('추출 가능한 글자' in w['message'] for w in result['warnings'])


def test_gemini_pipeline_checks_connection_preserves_text_and_accounts_for_usage(tmp_path):
    source = tmp_path / 'source.pdf'
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((50, 60), 'Original 1,234.56')
        pdf.save(source)
    class Client:
        checked = False
        def check(self):
            self.checked = True
        def extract(self, png, hints):
            assert self.checked
            assert png.startswith(b'\x89PNG')
            assert hints['blocks'][0]['text'] == 'Original 1,234.56'
            page = dict(blocks=[dict(hints['blocks'][0], text='Original 1,234.56')], warnings=[])
            return page, dict(promptTokenCount=500, candidatesTokenCount=100, thoughtsTokenCount=20)
    result = convert_document(source, tmp_path / 'result.hwpx', provider='gemini', client=Client(), page_numbers=[7])
    assert result['usage']['thoughtsTokenCount'] == 20
    assert result['model'] == DEFAULT_MODEL
    assert result['paragraphs'] == 1


@pytest.mark.parametrize('mutation', ['overlap', 'hole', 'nan', 'bad_span'])
def test_rejects_invalid_table_responses(mutation):
    block = dict(kind='table', bbox=[50, 50, 900, 900], text='', font_size_pt=10, rows=1, cols=2,
                 cells=[dict(row=0, col=0, row_span=1, col_span=2, text='Merged')])
    if mutation == 'overlap':
        block['cells'].append(dict(row=0, col=1, row_span=1, col_span=1, text='Overlap'))
    elif mutation == 'hole':
        block['cells'][0]['col_span'] = 1
    elif mutation == 'nan':
        block['bbox'][0] = float('nan')
    else:
        block['cells'][0]['col_span'] = 3
    with pytest.raises(ConversionError):
        validate_page(dict(blocks=[block], warnings=[]))


def test_api_header_hides_key_from_url_and_bounded_structured_generation():
    key = 'FAKE_PRIVATE_TEST_KEY'
    client = GeminiClient(key, 'gemini-3.8-flash')
    payload = dict(blocks=[], warnings=[])
    response = dict(candidates=[dict(finishReason='STOP', content=dict(parts=[dict(text=json.dumps(payload))]))],
                    usageMetadata=dict(promptTokenCount=10, candidatesTokenCount=2, thoughtsTokenCount=1))
    with patch('adf.hwpx_api.request_json', return_value=response) as request:
        page, usage = client.extract(b'not-real-image', dict(blocks=[]))
    args, kwargs = request.call_args
    assert key not in args[0]
    assert kwargs['headers']['x-goog-api-key'] == key
    assert key not in json.dumps(kwargs['payload'])
    assert kwargs['payload']['generationConfig']['thinkingConfig']['thinkingLevel'] == 'MEDIUM'
    assert kwargs['payload']['generationConfig']['maxOutputTokens'] == 24576
    assert page == payload
    assert usage['thoughtsTokenCount'] == 1


def test_api_errors_never_echo_provider_body_or_key():
    key = 'FAKE_SECRET_DO_NOT_LOG'
    opener = type('Opener', (), {'open': lambda *args, **kwargs: (_ for _ in ()).throw(
        urllib.error.HTTPError('https://example.test', 403, key, {}, io.BytesIO(key.encode())))})()
    with patch('adf.hwpx_api.urllib.request.build_opener', return_value=opener):
        with pytest.raises(ConversionError) as raised:
            request_json('https://example.test', headers={'x-goog-api-key': key})
    assert key not in str(raised.value)


def test_truncated_response_fails_before_writing_a_document():
    with patch('adf.hwpx_api.request_json', return_value=dict(candidates=[dict(finishReason='MAX_TOKENS')])):
        with pytest.raises(ConversionError):
            GeminiClient('FAKE_KEY').extract(b'image', {})


def test_credential_bundle_encrypts_plaintext_and_authenticates_changes(tmp_path):
    directory = tmp_path / 'private'
    key = 'FAKE_TEST_ONLY_GEMINI_CREDENTIAL'
    create_bundle(directory, key)
    assert load_bundle(directory) == key
    assert key.encode() not in (directory / 'credential.enc').read_bytes()
    ciphertext = bytearray((directory / 'credential.enc').read_bytes())
    ciphertext[-1] ^= 1
    (directory / 'credential.enc').write_bytes(ciphertext)
    with pytest.raises(ConversionError) as raised:
        load_bundle(directory)
    assert key not in str(raised.value)


def test_worker_sanitizes_unexpected_errors(tmp_path):
    task = tmp_path / 'task.json'
    task.write_text(json.dumps(dict(options=dict(provider='gemini', model=DEFAULT_MODEL))), encoding='utf-8')
    with patch('adf.hwpx_export.make_client', side_effect=RuntimeError('FAKE_SECRET_PROVIDER_BODY')):
        assert hwpx_worker(task) == 1
    assert 'FAKE_SECRET_PROVIDER_BODY' not in (tmp_path / 'result.json').read_text(encoding='utf-8')


@pytest.mark.parametrize('url', ['http://example.test', 'https://name:secret@example.test', 'https://example.test?key=private'])
def test_relay_rejects_insecure_or_secret_bearing_urls(url):
    with pytest.raises(ConversionError):
        service_url(url)


def test_publication_refuses_existing_file_and_rolls_back_batch(tmp_path):
    from adf.hwpx_widgets import publish_hwpx
    folder = tmp_path / 'job'
    folder.mkdir()
    (folder / 'output.hwpx').write_bytes(b'hwpx')
    (folder / 'conversion.json').write_bytes(b'{}')
    destination = tmp_path / 'document.hwpx'
    destination.write_bytes(b'original')
    with pytest.raises(FileExistsError):
        publish_hwpx(folder, destination)
    assert destination.read_bytes() == b'original'
    destination.unlink()
    link = os.link
    def fail_report(source, target):
        if str(target).endswith('.json'):
            raise OSError('Test interrupted publication')
        link(source, target)
    with patch('adf.hwpx_widgets.os.link', side_effect=fail_report):
        with pytest.raises(OSError):
            publish_hwpx(folder, destination)
    assert not destination.exists()
    assert not destination.with_suffix('.conversion.json').exists()


def test_worker_finishes_when_windows_temporarily_locks_progress_file(tmp_path):
    sample_pdf(tmp_path / 'document.pdf')
    request = tmp_path / 'request.json'
    request.write_text(json.dumps({'operation': 'convert', 'options': {'provider': 'local'}}), encoding='utf-8')
    original_replace = Path.replace
    blocked = []

    def sharing_violation(path, target):
        if path.name == 'progress.tmp':
            blocked.append(True)
            raise PermissionError('simulated Windows sharing violation')
        return original_replace(path, target)

    with patch.object(Path, 'replace', sharing_violation):
        assert hwpx_worker(request) == 0
    assert blocked
    assert json.loads((tmp_path / 'result.json').read_text(encoding='utf-8'))['ok']
    with HwpxDocument.open(tmp_path / 'output.hwpx') as document:
        assert 'Editable heading' in document.text.plain()

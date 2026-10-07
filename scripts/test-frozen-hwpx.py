"""Check editable cell styles through a packaged app using a stdlib oracle."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from xml.etree import ElementTree as ET
import zipfile

HP = '{http://www.hancom.co.kr/hwpml/2011/paragraph}'
HH = '{http://www.hancom.co.kr/hwpml/2011/head}'


def fixture_pdf(path):
    # Deliberately unequal rows/columns, distinct cell fonts and border styles.
    # Construct the PDF directly so a verification runner needs no dependencies.
    stream = b'''q
.95 .9 .85 rg 50 660 80 40 re f
1 0 0 RG 2 w 50 590 300 110 re S
0 1 0 RG .5 w 130 590 m 130 700 l S
0 0 1 RG 1 w [4 2] 0 d 50 660 m 350 660 l S
Q
BT /F2 18 Tf 1 0 0 rg 56 672 Td (Big) Tj ET
BT /F3 8 Tf 0 0 1 rg 140 675 Td (Small) Tj ET
BT /F1 14 Tf 0 0 0 rg 56 627 Td (Body) Tj ET
BT /F1 10 Tf 0 0 0 rg 140 625 Td (Other) Tj ET
'''
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Count 1 /Kids [3 0 R] >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] '
        b'/Resources << /Font << /F1 5 0 R /F2 6 0 R /F3 7 0 R >> >> /Contents 4 0 R >>',
        b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'endstream',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Oblique >>']
    data, offsets = b'%PDF-1.7\n', [0]
    for index, body in enumerate(objects, 1):
        offsets.append(len(data))
        data += str(index).encode() + b' 0 obj\n' + body + b'\nendobj\n'
    xref = len(data)
    data += b'xref\n0 8\n0000000000 65535 f \n'
    data += b''.join(f'{offset:010d} 00000 n \n'.encode() for offset in offsets[1:])
    data += b'trailer\n<< /Size 8 /Root 1 0 R >>\nstartxref\n' + str(xref).encode() + b'\n%%EOF\n'
    path.write_bytes(data)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('executable', type=Path)
    parser.add_argument('--report', required=True, type=Path)
    args = parser.parse_args()
    executable = args.executable.resolve()
    internal = executable.parent / '_internal' if os.name == 'nt' else executable.parent.parent / 'Frameworks'
    assert not (internal / 'HWPX_CREDENTIALS').exists(), 'Use a public build without a bundled API key'
    env = os.environ.copy()
    for key in ('PYTHONHOME', 'PYTHONPATH', 'GEMINI_API_KEY', 'XDF_HWPX_API_KEY'):
        env.pop(key, None)
    report = {'ok': False}
    with tempfile.TemporaryDirectory(prefix='xdf-frozen-hwpx-') as temporary:
        folder = Path(temporary)

        def invoke(directory, operation, provider, success):
            request = directory / 'task.json'
            request.write_text(json.dumps(dict(operation=operation, options=dict(provider=provider))), encoding='utf-8')
            run = subprocess.run([str(executable), '--hwpx-worker', str(request)], env=env, timeout=120,
                capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            result = json.loads((directory / 'result.json').read_text(encoding='utf-8'))
            assert result['ok'] == success and (run.returncode == 0) == success, result
            return result

        source = folder / 'document.pdf'
        fixture_pdf(source)
        original = hashlib.sha256(source.read_bytes()).hexdigest()
        result = invoke(folder, 'convert', 'local', True)
        assert result['result']['tables'] == 1, result
        with zipfile.ZipFile(folder / 'output.hwpx') as archive:
            assert archive.testzip() is None
            assert archive.read('mimetype') == b'application/hwp+zip'
            header = ET.fromstring(archive.read('Contents/header.xml'))
            section = ET.fromstring(archive.read('Contents/section0.xml'))
        styles = {element.get('id'): element for element in header.iter(HH + 'charPr')}
        borders = {element.get('id'): element for element in header.iter(HH + 'borderFill')}
        table = next(section.iter(HP + 'tbl'))
        cells = list(table.iter(HP + 'tc'))
        assert len(cells) == 4
        assert [''.join(cell.itertext()) for cell in cells] == ['Big', 'Small', 'Body', 'Other']
        sizes = [styles[next(cell.iter(HP + 'run')).get('charPrIDRef')] for cell in cells]
        assert [style.get('height') for style in sizes] == ['1800', '800', '1400', '1000']
        assert sizes[0].find(HH + 'bold') is not None
        assert sizes[1].find(HH + 'italic') is not None
        assert [style.get('textColor') for style in sizes[:2]] == ['#FF0000', '#0000FF']
        assert [cell.find(HP + 'cellSz').get('width') for cell in cells] == ['8000','22000','8000','22000']
        assert [cell.find(HP + 'cellSz').get('height') for cell in cells] == ['4000','4000','7000','7000']
        fill = borders[cells[0].get('borderFillIDRef')]
        top, right, bottom = [fill.find(HH + side + 'Border') for side in ('top', 'right', 'bottom')]
        assert (top.get('width'), top.get('color'), top.get('type')) == ('0.7 mm', '#FF0000', 'SOLID')
        assert (right.get('width'), right.get('color')) == ('0.2 mm', '#00FF00')
        assert (bottom.get('type'), bottom.get('color')) == ('DASH', '#0000FF')
        report.update(editable_cell_text=True, distinct_font_sizes=True, bold_italic_colors=True,
                      unequal_column_widths=True, unequal_row_heights=True, distinct_cell_borders=True,
                      package_xml=True, source_unchanged=hashlib.sha256(source.read_bytes()).hexdigest() == original)
        assert report['source_unchanged']
        key_check = folder / 'no-key'
        key_check.mkdir()
        rejected = invoke(key_check, 'check', 'gemini', False)
        assert '개발자가 배포용 API 키를 설정해야 합니다' in rejected['error'], rejected
        report['public_build_without_gemini_key'] = True
        report['ok'] = True
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()

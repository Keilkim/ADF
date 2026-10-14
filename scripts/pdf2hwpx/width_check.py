"""Measure how wide Hancom draws text and compare it with pdf2hwpx's width model.

    .venv\\Scripts\\python.exe scripts/pdf2hwpx/width_check.py --out widths
        [--fonts "맑은 고딕,휴먼명조"] [--render-python "py -3"]

Line breaks in Hancom follow its own glyph widths; when the converter's
estimate (pdf_reader.line_width) is off, lines wrap elsewhere than in the
PDF. Each sample is written between two bars, '|sample|', at 20 pt; the
distance between the bars' left edges in Hancom's page image is the
advance width of '|' plus the sample, whatever the glyphs' side bearings.
The report lists measured and predicted widths per font and kind of text.
"""
import argparse
from pathlib import Path
import shlex
import subprocess
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from hwpx import HwpxDocument  # noqa: E402

from adf.pdf2hwpx.hwpx_writer import _sync_manifest, add_runs  # noqa: E402
from adf.pdf2hwpx.pdf_reader import line_width  # noqa: E402

RENDERER = Path(__file__).with_name('hancom_render.py')
SIZE, DPI = 20, 192
SAMPLES = {
    'hangul': '대학교육의환경을빠르게변화시키고있다',
    'latin': 'Thequickbrownfoxjumpsoverthelazydog',
    'digits': '0123456789202606',
    'spaces': '가 나 다 라 마 바 사 아',
    'middle dot': '·' * 12,
    'ellipsis': '…' * 8,
    'brackets': '「」『』()[]',
    'punctuation': ',.:;!?~-',
}
FONTS = ['맑은 고딕', '휴먼명조', '함초롬돋움', '함초롬바탕', '바탕', 'Calibri', 'Times New Roman', 'Arial',
         'HY헤드라인M', '굴림', '돋움체', 'Pretendard Medium']


def build(fonts, target):
    doc = HwpxDocument.new()
    section = doc.sections[0]
    doc.page.set_margins(left=2000, right=2000, top=2000, bottom=2000, header=0, footer=0, gutter=0,
                         section=section)
    rows = []
    for index, (font, kind) in enumerate((font, kind) for font in fonts for kind in SAMPLES):
        paragraph = section.paragraphs[0] if index == 0 else doc.add_paragraph(section=section, include_run=False,
                                                                                 inherit_style=False)
        text = '|' + SAMPLES[kind] + '|'
        block = dict(runs=[dict(text=text, font=font, font_size_pt=SIZE)], font_size_pt=SIZE, alignment='LEFT',
                     line_spacing_percent=180, text=text)
        add_runs(doc, paragraph, block)
        rows.append((font, kind, block['runs']))
    section.remove_layout_caches()
    _sync_manifest(doc)
    doc.save_to_path(str(target))
    return rows


def bars(image):
    """Left edges of the first and last ink run of each text line, in points."""
    ink = np.asarray(image.convert('L')) < 128
    rows = np.flatnonzero(ink.any(1))
    # Bands closer than a quarter line belong to one line (a broken bar '¦').
    groups = np.split(rows, np.flatnonzero(np.diff(rows) > SIZE * DPI / 72 / 2) + 1) if len(rows) else []
    lines = [ink[group[0]:group[-1] + 1] for group in groups]
    result = []
    for band in lines:
        columns = np.flatnonzero(band.any(0))
        runs = np.split(columns, np.flatnonzero(np.diff(columns) > 1) + 1)
        result.append((runs[-1][0] - runs[0][0]) * 72 / DPI)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--out', type=Path, default=Path('pdf2hwpx-widths'))
    parser.add_argument('--fonts', help='comma-separated font names')
    parser.add_argument('--render-python', default='py -3')
    args = parser.parse_args()
    fonts = args.fonts.split(',') if args.fonts else FONTS
    args.out.mkdir(parents=True, exist_ok=True)
    target = args.out / 'widths.hwpx'
    rows = build(fonts, target)
    for old in args.out.glob('page-*.bmp'):
        old.unlink()
    subprocess.run([*shlex.split(args.render_python), str(RENDERER), str(target), str(args.out / 'page-'),
                    '--dpi', str(DPI)], check=True, capture_output=True, text=True, timeout=300)
    measured = []
    for page in sorted(args.out.glob('page-*.bmp'), key=lambda path: int(path.stem.split('-')[1])):
        measured.extend(bars(Image.open(page)))
    if len(measured) != len(rows):
        raise SystemExit(f'found {len(measured)} lines for {len(rows)} samples; see {args.out}')
    print(f'{"font":18} {"kind":12} {"Hancom pt":>9} {"model pt":>9} {"error":>7}')
    errors = {}
    for (font, kind, runs), width in zip(rows, measured):
        bar = line_width([dict(runs[0], text='|')])
        model = line_width(runs)
        predicted = None if model is None or bar is None else model - bar
        error = None if predicted is None else (predicted - width) / width * 100
        errors.setdefault(font, []).append(abs(error) if error is not None else None)
        print(f'{font:18} {kind:12} {width:9.1f} {predicted if predicted is not None else float("nan"):9.1f} '
              f'{error if error is not None else float("nan"):6.1f}%')
    print()
    for font, values in errors.items():
        known = [value for value in values if value is not None]
        print(f'{font:18} worst {max(known):5.1f}%' if known else f'{font:18} no width model')


if __name__ == '__main__':
    main()

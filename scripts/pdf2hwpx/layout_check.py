"""Convert PDFs to HWPX, render them in Hancom Office and compare with the PDF.

    .venv\\Scripts\\python.exe scripts/pdf2hwpx/layout_check.py a.pdf b.pdf --out check
        [--render-python "py -3"] [--pages 1-5] [--per-page]

For each PDF the report lists the page count of both versions and, per page,
the text lines whose position or right edge differ from the PDF (a word
moved to another line shifts the right edge). check/<name>/compare-N.png
shows the PDF on the left and Hancom's rendering on the right. With
--per-page every page is converted alone, which names the pages that
overflow onto an extra Hancom page.
"""
import argparse
from pathlib import Path
import shlex
import subprocess
import sys

import numpy as np
import pymupdf
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from adf.pdf2hwpx import convert_document  # noqa: E402

RENDERER = Path(__file__).with_name('hancom_render.py')
DPI = 96


def ink_lines(image):
    """Text lines as (top, bottom, left, right) pixel bands."""
    ink = np.asarray(image.convert('L')) < 140
    rows = np.where(ink.sum(1) > 2)[0]
    bands = []
    for row in rows:
        if bands and row - bands[-1][1] <= 2:
            bands[-1][1] = row
        else:
            bands.append([row, row])
    result = []
    for top, bottom in bands:
        columns = np.where(ink[top:bottom + 1].any(0))[0]
        result.append((top, bottom, columns[0], columns[-1]))
    return result


def compare(pdf, rendered, folder):
    report, mismatched = [], 0
    for index, page in enumerate(pdf):
        pixmap = page.get_pixmap(dpi=DPI)
        source = Image.frombytes('RGB', (pixmap.width, pixmap.height), pixmap.samples)
        path = rendered.get(index)
        target = Image.open(path).convert('RGB') if path else Image.new('RGB', source.size, 'gray')
        canvas = Image.new('RGB', (source.width + target.width + 10, max(source.height, target.height)), (120,) * 3)
        canvas.paste(source, (0, 0))
        canvas.paste(target, (source.width + 10, 0))
        canvas.save(folder / f'compare-{index + 1}.png')
        a, b = ink_lines(source), ink_lines(target) if path else []
        moved = sum(abs(x[0] - y[0]) > 4 or abs(x[3] - y[3]) > 8 for x, y in zip(a, b)) + abs(len(a) - len(b))
        mismatched += moved
        report.append(f'  page {index + 1}: {len(a)} lines in PDF, {len(b)} in Hancom, {moved} differ')
    return mismatched, report


def check(source, out, render_python, pages=None):
    folder = out / source.stem
    folder.mkdir(parents=True, exist_ok=True)
    with pymupdf.open(source) as original:
        if pages:
            subset = pymupdf.open()
            subset.insert_pdf(original, from_page=pages[0] - 1, to_page=pages[1] - 1)
            source = folder / 'source.pdf'
            subset.save(source)
    target = folder / 'result.hwpx'
    target.unlink(missing_ok=True)
    result = convert_document(source, target)
    for old in folder.glob('hancom-*.bmp'):
        old.unlink()
    subprocess.run([*shlex.split(render_python), str(RENDERER), str(target), str(folder / 'hancom-')],
                   check=True, capture_output=True, timeout=300)
    rendered = {int(path.stem.split('-')[1]) - 1: path for path in folder.glob('hancom-*.bmp')}
    with pymupdf.open(source) as pdf:
        mismatched, report = compare(pdf, rendered, folder)
        pages_ok = len(rendered) == len(pdf)
        print(f'{source.name}: {len(pdf)} pages -> {len(rendered)} in Hancom'
              f'{"" if pages_ok else " (PAGE COUNT DIFFERS)"}, {mismatched} line differences, '
              f'{result["paragraphs"]} paragraphs, {result["tables"]} tables, {result["figures"]} figures')
    print('\n'.join(report))
    return mismatched + (0 if pages_ok else 100)


def overflowing_pages(source, out, render_python):
    with pymupdf.open(source) as pdf:
        count = len(pdf)
    overflow = []
    for number in range(1, count + 1):
        folder = out / f'{source.stem}-page{number}'
        folder.mkdir(parents=True, exist_ok=True)
        with pymupdf.open(source) as pdf, pymupdf.open() as single:
            single.insert_pdf(pdf, from_page=number - 1, to_page=number - 1)
            single.save(folder / 'source.pdf')
        target = folder / 'result.hwpx'
        for old in [target, *folder.glob('hancom-*.bmp')]:
            old.unlink(missing_ok=True)
        convert_document(folder / 'source.pdf', target)
        rendered = subprocess.run([*shlex.split(render_python), str(RENDERER), str(target), str(folder / 'hancom-')],
                                  check=True, capture_output=True, text=True, timeout=300).stdout.split()
        pages = int(rendered[-1])
        if pages != 1:
            overflow.append(number)
        print(f'  page {number}: {pages} Hancom page(s)')
    print(f'{source.name}: overflowing pages {overflow or "none"}')
    return len(overflow) * 100


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('pdfs', nargs='+', type=Path)
    parser.add_argument('--out', type=Path, default=Path('pdf2hwpx-check'))
    parser.add_argument('--render-python', default='py -3')
    parser.add_argument('--pages', help='first-last page range, e.g. 1-5')
    parser.add_argument('--per-page', action='store_true', help='convert each page alone to find overflow')
    args = parser.parse_args()
    if args.per_page:
        total = sum(overflowing_pages(pdf, args.out, args.render_python) for pdf in args.pdfs)
        raise SystemExit(print('total score', total))
    pages = tuple(int(n) for n in args.pages.split('-')) if args.pages else None
    total = sum(check(pdf, args.out, args.render_python, pages) for pdf in args.pdfs)
    print('total score', total, '(0 = every line where the PDF has it)')

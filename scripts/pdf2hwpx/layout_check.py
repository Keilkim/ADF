"""Convert PDFs to HWPX, render them in Hancom Office and compare with the PDF.

    .venv\\Scripts\\python.exe scripts/pdf2hwpx/layout_check.py a.pdf b.pdf --out check
        [--render-python "py -3"] [--pages 1-5] [--per-page]

For each PDF the report lists the page count of both versions and, per page,
how many of the PDF's text lines Hancom draws at the same place (a word that
wraps differently or a shifted line does not match). check/<name>/compare-N.png
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


def render(render_python, target, prefix):
    """Run the Hancom renderer; Hancom automation fails now and then, so retry once."""
    command = [*shlex.split(render_python), str(RENDERER), str(target), str(prefix)]
    for attempt in (1, 2):
        try:
            return subprocess.run(command, check=True, capture_output=True, text=True, timeout=300).stdout
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            if attempt == 2:
                raise
DPI = 96


def text_lines(page):
    """Pixel boxes of the PDF's own text lines."""
    scale = DPI / 72
    boxes = []
    for block in page.get_text('dict')['blocks']:
        for line in block.get('lines', []):
            if ''.join(span['text'] for span in line['spans']).strip():
                x0, y0, x1, y1 = (pymupdf.Rect(line['bbox']) * page.rotation_matrix)
                boxes.append((int(x0 * scale), int(y0 * scale), int(x1 * scale) + 1, int(y1 * scale) + 1))
    return boxes


def glyph_columns(band):
    """Columns holding glyph ink: a column inked from top to bottom of the
    line is a cell border, not a letter."""
    filled = band.mean(0)
    return np.where(band.any(0) & (filled < .9))[0]


def locate(source, target, box, reach=24):
    """Where a PDF text line is in the Hancom image.

    The vertical ink profile of the line is matched within reach pixels;
    at the best offset the line's first and last ink columns are compared.
    The last column shows whether the same words ended the line, without
    depending on how justification spread the spaces. Returns
    (similarity, vertical offset, left shift, right shift) or None.
    """
    x0, y0, x1, y1 = box
    piece = source[y0:y1, x0:x1]
    if piece.sum() < 4:
        return None
    profile = piece.sum(1).astype(float)
    # Both images are read through the same window, so a neighbouring mark
    # (a bullet picture) counts the same on both sides.
    lo, hi = max(0, x0 - 20), min(target.shape[1], x1 + 20)
    columns = glyph_columns(source[y0:y1, lo:hi])
    if not len(columns):
        return None
    left, right = lo + columns[0], lo + columns[-1]
    candidates = []
    for dy in range(-reach, reach + 1):
        if y0 + dy < 0 or y1 + dy > target.shape[0]:
            continue
        band = target[y0 + dy:y1 + dy, lo:hi]
        other = band.sum(1).astype(float)
        norm = np.linalg.norm(profile) * np.linalg.norm(other)
        candidates.append((float(profile @ other / norm) if norm else 0., dy, band))
    if not candidates:
        return None
    # Lines of one paragraph look alike; of the near-best matches the
    # nearest is the line itself, not its neighbour.
    top = max(candidate[0] for candidate in candidates)
    score, dy, band = min((c for c in candidates if c[0] >= top - .02), key=lambda c: abs(c[1]))
    found = glyph_columns(band)
    if not len(found):
        return score, dy, 999, 999
    return score, dy, lo + found[0] - left, lo + found[-1] - right
def without_rules(ink, length=18):
    """Ink with long straight horizontal and vertical runs (table rules,
    underlines, frames) removed, so only glyphs are compared."""
    from numpy.lib.stride_tricks import sliding_window_view
    result = ink.copy()
    for axis in (0, 1):
        if ink.shape[axis] < length:
            continue
        full = sliding_window_view(ink, length, axis=axis).all(-1)
        padded = np.pad(full, [(length - 1, length - 1) if a == axis else (0, 0) for a in (0, 1)])
        covered = sliding_window_view(padded, length, axis=axis).any(-1)
        covered = covered[:ink.shape[0], :ink.shape[1]]
        result &= ~covered
    return result


def compare(pdf, rendered, folder):
    """Per PDF text line: is the same ink at the same place in Hancom?

    A line is in place when Hancom draws a line of the same shape within
    6 px (about 4.5 pt) of the PDF position that starts and ends where the
    PDF line does. A shifted line has the same words elsewhere; a differing
    line wrapped differently, changed width or is missing.
    """
    report, misplaced, in_place = [], 0, 0
    for index, page in enumerate(pdf):
        pixmap = page.get_pixmap(dpi=DPI)
        source = Image.frombytes('RGB', (pixmap.width, pixmap.height), pixmap.samples)
        path = rendered.get(index)
        target = Image.open(path).convert('RGB') if path else Image.new('RGB', source.size, 'white')
        canvas = Image.new('RGB', (source.width + target.width + 10, max(source.height, target.height)), (120,) * 3)
        canvas.paste(source, (0, 0))
        canvas.paste(target, (source.width + 10, 0))
        canvas.save(folder / f'compare-{index + 1}.png')
        ink_source = without_rules(np.asarray(source.convert('L')) < 160)
        ink_target = without_rules(np.asarray(target.convert('L').resize(source.size)) < 160)
        placed = moved = lost = 0
        for box in text_lines(page):
            found = locate(ink_source, ink_target, box)
            if found is None:
                continue
            score, offset, left, right = found
            if score >= .8 and abs(offset) <= 6 and abs(left) <= 6 and abs(right) <= 10:
                placed += 1
            elif score >= .8 and abs(left) <= 6 and abs(right) <= 10:
                moved += 1
            else:
                lost += 1
        misplaced += moved + lost
        in_place += placed
        total = placed + moved + lost
        report.append(f'  page {index + 1}: {placed}/{total} text lines in place, {moved} shifted, {lost} differ')
    return misplaced, report, in_place


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
    render(render_python, target, folder / 'hancom-')
    rendered = {int(path.stem.split('-')[1]) - 1: path for path in folder.glob('hancom-*.bmp')}
    with pymupdf.open(source) as pdf:
        mismatched, report, in_place = compare(pdf, rendered, folder)
        share = round(100 * in_place / max(1, in_place + mismatched))
        pages_ok = len(rendered) == len(pdf)
        print(f'{source.name}: {len(pdf)} pages -> {len(rendered)} in Hancom'
              f'{"" if pages_ok else " (PAGE COUNT DIFFERS)"}, {share}% of text lines in place '
              f'({mismatched} not), '
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
        rendered = render(render_python, target, folder / 'hancom-').split()
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
    print('total score', total, '(0 = every PDF text line drawn in place)')

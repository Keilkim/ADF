"""Measure Hancom's character widths for fonts whose files cannot be read.

    .venv\\Scripts\\python.exe scripts/pdf2hwpx/hancom_widths.py --out widths
        [--fonts "휴먼명조,한양신명조"] [--render-python "py -3"]

Hancom Office draws its own HFT fonts (휴먼명조, 한양신명조 ...), whose
widths no font library reads, and draws characters a font lacks (Korean
in Arial) with a fallback. Both decide where Hancom wraps a line. Each
character is written ten times between two bars, '|cccccccccc|', at 20 pt;
the bars' distance in Hancom's page image, less the bars' own '||', is ten
advances. The result, in em, goes to adf/pdf2hwpx/hancom_widths.json,
which font_metrics.glyph_width reads. HFT fonts ship with Hancom Office,
so the table holds on every PC with it.
"""
import argparse
import json
from pathlib import Path
import shlex
import subprocess
import sys

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from hwpx import HwpxDocument  # noqa: E402
from width_check import DPI, RENDERER, SIZE, bars  # noqa: E402

from adf.pdf2hwpx.font_metrics import _hancom_font_folders  # noqa: E402
from adf.pdf2hwpx.hwpx_writer import _sync_manifest, add_runs  # noqa: E402

REPEAT = 10
CHARACTERS = (''.join(chr(code) for code in range(0x20, 0x7F))
              + '·ㆍ…‥「」『』《》〈〉【】“”‘’※○●◎□■◇◆△▲▽▼▶◀→←↑↓⇒⇔∼～―—–•∙'
              + 'ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ①②③④⑤⑥⑦⑧⑨⑩㈜℃％＆（）［］：；，．　'
              + '가한뷁힣漢字')
# Hangul and Hanja are one width in these fonts; the table keeps one value.
CLASSES = {'hangul': '가한뷁힣', 'hanja': '漢字'}
LATIN_FONTS = ['Arial', 'Calibri', 'Times New Roman', 'Segoe UI', 'Tahoma', 'Verdana', 'Georgia', 'Cambria']
PICTURES = ('산돌 ClipArt', '산돌 사인', '산돌 패턴', '산돌 픽토그램', '수려아트', '수식', '한글 풀어쓰기')


def hft_fonts():
    names = []
    for folder in _hancom_font_folders():
        info = folder / 'HFTInfo.dat'
        if not info.is_file():
            continue
        for line in info.read_bytes().decode('utf-16', 'ignore').splitlines():
            name, _, value = line.partition('=')
            if ('.HFT' in value.upper() and name and name not in names
                    and not name.startswith(PICTURES) and not name.endswith(('세로', 'V'))):
                names.append(name)
    return names


def build(fonts, target):
    doc = HwpxDocument.new()
    section = doc.sections[0]
    doc.page.set_margins(left=2000, right=2000, top=2000, bottom=2000, header=0, footer=0, gutter=0,
                         section=section)
    samples = []
    for font in fonts:
        for char in [''] + list(CHARACTERS):
            paragraph = section.paragraphs[0] if not samples else doc.add_paragraph(
                section=section, include_run=False, inherit_style=False)
            text = '|' + char * REPEAT + '|'
            add_runs(doc, paragraph, dict(runs=[dict(text=text, font=font, font_size_pt=SIZE)], font_size_pt=SIZE,
                                          alignment='LEFT', line_spacing_percent=400, text=text))
            samples.append((font, char))
    section.remove_layout_caches()
    _sync_manifest(doc)
    doc.save_to_path(str(target))
    return samples


def measure(fonts, out, render_python):
    target = out / 'hancom_widths.hwpx'
    samples = build(fonts, target)
    for old in out.glob('page-*.bmp'):
        old.unlink()
    subprocess.run([*shlex.split(render_python), str(Path(RENDERER)), str(target), str(out / 'page-'),
                    '--dpi', str(DPI)], check=True, capture_output=True, text=True, timeout=3600)
    widths = []
    for page in sorted(out.glob('page-*.bmp'), key=lambda path: int(path.stem.split('-')[1])):
        widths.extend(bars(Image.open(page)))
    if len(widths) != len(samples):
        raise ValueError(f'found {len(widths)} lines for {len(samples)} samples; see {out}')
    table = {}
    for (font, char), width in zip(samples, widths):
        table.setdefault(font, {})[char] = width
    result = {}
    for font, measured in table.items():
        bar = measured.pop('')
        ems = {char: round((width - bar) / REPEAT / SIZE, 4) for char, width in measured.items()}
        for name, members in CLASSES.items():
            values = [ems.pop(char) for char in members]
            ems[name] = round(sum(values) / len(values), 4)
        result[font] = ems
    return result


def dump(table):
    """One line per font, widths rounded to a thousandth of an em."""
    return '{\n' + ',\n'.join(json.dumps(font, ensure_ascii=False) + ':' + json.dumps(
        {char: round(width, 3) for char, width in sorted(widths.items())}, ensure_ascii=False, separators=(',', ':'))
        for font, widths in sorted(table.items())) + '\n}\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--out', type=Path, default=Path('pdf2hwpx-hancom-widths'))
    parser.add_argument('--fonts', help='comma-separated font names (default: every HFT font and common Latin fonts)')
    parser.add_argument('--batch', type=int, default=12, help='fonts per Hancom document')
    parser.add_argument('--render-python', default='py -3')
    args = parser.parse_args()
    fonts = args.fonts.split(',') if args.fonts else hft_fonts() + LATIN_FONTS
    args.out.mkdir(parents=True, exist_ok=True)
    table_path = Path(__file__).resolve().parents[2] / 'adf' / 'pdf2hwpx' / 'hancom_widths.json'
    table = json.loads(table_path.read_text(encoding='utf-8')) if table_path.is_file() else {}
    for start in range(0, len(fonts), args.batch):
        batch = fonts[start:start + args.batch]
        try:
            table.update(measure(batch, args.out, args.render_python))
        except ValueError:
            # One font's glyphs ran lines together; measure the batch one by one.
            for font in batch:
                try:
                    table.update(measure([font], args.out, args.render_python))
                except ValueError as error:
                    print(f'skipped {font}: {error}', flush=True)
        table_path.write_text(dump(table), encoding='utf-8')
        print(f'{start + len(batch)}/{len(fonts)} fonts measured', flush=True)
    # A Korean character a Latin font lacks: Hancom's fallback, as drawn for Arial.
    if 'Arial' in table:
        table['fallback'] = {name: table['Arial'][name] for name in CLASSES}
        table_path.write_text(dump(table), encoding='utf-8')


if __name__ == '__main__':
    main()

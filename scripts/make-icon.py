"""Export XDF's primary wordmark, compact X and website vectors."""
import io
import copy
import os
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PIL import Image
from PySide6.QtCore import QByteArray, QBuffer, QIODevice, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer


ROOT = Path(__file__).resolve().parents[1]
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def render_asset(width, height, source):
    renderer = QSvgRenderer(str(source))
    if not renderer.isValid():
        raise ValueError(f'Invalid icon master: {source}')
    image = QImage(width*4, height*4, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter)
    painter.end()
    storage = QByteArray()
    buffer = QBuffer(storage)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, 'PNG')
    buffer.close()
    with Image.open(io.BytesIO(bytes(storage))) as rendered:
        # Resample coverage, then apply the flat brand fill. Resampling RGBA
        # directly can overshoot the premultiplied channels at narrow counters.
        alpha = rendered.getchannel('A').resize((width, height), Image.Resampling.LANCZOS)
    icon = Image.new('RGBA', (width, height), (240, 75, 45, 0))
    icon.putalpha(alpha)
    # Keep fully transparent pixels zeroed for native decoders and previews.
    icon.paste((0, 0, 0, 0), mask=alpha.point(lambda value: 255 if value == 0 else 0))
    return icon


def render_icon(size, source):
    return render_asset(size, size, source)


def export_compact_mark(wordmark, target):
    # The X in the primary wordmark is the sole source of the compact icon.
    # Derive its square clear space from the actual vector bounds.
    renderer = QSvgRenderer(str(wordmark))
    bounds = renderer.boundsOnElement('xdf-x')
    if not renderer.isValid() or bounds.isEmpty():
        raise ValueError(f'Missing X glyph in primary wordmark: {wordmark}')
    side = max(bounds.width(), bounds.height()) / 0.75
    box = (bounds.center().x() - side / 2, bounds.center().y() - side / 2, side, side)
    namespace = 'http://www.w3.org/2000/svg'
    ET.register_namespace('', namespace)
    root = ET.Element(f'{{{namespace}}}svg', width='1024', height='1024',
                      viewBox=' '.join(f'{value:.4f}' for value in box))
    ET.SubElement(root, f'{{{namespace}}}title').text = 'XDF compact X'
    ET.SubElement(root, f'{{{namespace}}}desc').text = 'The X glyph from the primary XDF wordmark, with square clear space for small icons.'
    glyph = copy.deepcopy(ET.parse(wordmark).find(".//*[@id='xdf-x']"))
    glyph.set('fill', '#f04b2d')
    root.append(glyph)
    ET.indent(root, space='  ')
    ET.ElementTree(root).write(target, encoding='utf-8', xml_declaration=False)


def main():
    app = QGuiApplication.instance() or QGuiApplication([])
    assets = ROOT / 'assets'
    wordmark = assets / 'xdf-wordmark.svg'
    source = assets / 'xdf-mark.svg'
    export_compact_mark(wordmark, source)
    large = render_icon(1024, source)
    large.save(assets / 'xdf.png')
    # Each frame comes from the vector. 32-bit DIB frames retain alpha / AND
    # masks when Windows loads the icon as a native resource.
    frames = [render_icon(size, source) for size in SIZES]
    frames[-1].save(assets / 'xdf.ico', sizes=[(s, s) for s in SIZES],
                    append_images=frames[:-1], bitmap_format='bmp')
    large.save(assets / 'xdf.icns')
    # Ship precisely the same vector as the browser favicon and site logo.
    shutil.copyfile(source, ROOT / 'site' / 'assets' / source.name)
    bounds = QSvgRenderer(str(wordmark)).viewBoxF()
    width = 1536
    render_asset(width, round(width * bounds.height() / bounds.width()), wordmark).save(assets / 'xdf-wordmark.png')
    shutil.copyfile(wordmark, ROOT / 'site' / 'assets' / wordmark.name)
    print('XDF primary wordmark and transparent vermilion X icons exported')


if __name__ == '__main__':
    main()

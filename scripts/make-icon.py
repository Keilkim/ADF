"""Export XDF's independent wordmark and symbol masters for apps and the web."""
import io
import os
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PIL import Image
from PySide6.QtCore import QByteArray, QBuffer, QIODevice, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter
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
        # Preserve the SVG's fill independently of the interface accent.
        # Resampling only coverage avoids color overshoot at narrow edges.
        fills = {element.get('fill') for element in ET.parse(source).iter()
                 if element.get('fill') not in (None, 'none')}
        if len(fills) == 1 and QColor(next(iter(fills))).isValid():
            color = QColor(next(iter(fills)))
            alpha = rendered.getchannel('A').resize((width, height), Image.Resampling.LANCZOS)
            icon = Image.new('RGBA', (width, height), color.getRgb())
            icon.putalpha(alpha)
        else:
            icon = rendered.resize((width, height), Image.Resampling.LANCZOS)
    # Keep fully transparent pixels zeroed for native decoders and previews.
    alpha = icon.getchannel('A')
    icon.paste((0, 0, 0, 0), mask=alpha.point(lambda value: 255 if value == 0 else 0))
    return icon


def render_icon(size, source):
    return render_asset(size, size, source)


def main():
    app = QGuiApplication.instance() or QGuiApplication([])
    assets = ROOT / 'assets'
    wordmark = assets / 'xdf-wordmark.svg'
    source = assets / 'xdf-mark.svg'
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
    shutil.copyfile(source, assets / 'fonts' / source.name)
    bounds = QSvgRenderer(str(wordmark)).viewBoxF()
    width = 1536
    render_asset(width, round(width * bounds.height() / bounds.width()), wordmark).save(assets / 'xdf-wordmark.png')
    shutil.copyfile(wordmark, ROOT / 'site' / 'assets' / wordmark.name)
    shutil.copyfile(wordmark, assets / 'fonts' / wordmark.name)
    print('XDF wordmark and symbol exported for app, tray, installer and website assets')


if __name__ == '__main__':
    main()

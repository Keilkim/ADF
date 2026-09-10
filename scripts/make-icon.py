"""Export the approved blue A vector as transparent PNG, ICO and ICNS assets."""
import io
import os
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PIL import Image
from PySide6.QtCore import QByteArray, QBuffer, QIODevice, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer


ROOT = Path(__file__).resolve().parents[1]
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def render_icon(size, source):
    renderer = QSvgRenderer(str(source))
    if not renderer.isValid():
        raise ValueError(f'Invalid icon master: {source}')
    image = QImage(size*4, size*4, QImage.Format.Format_ARGB32_Premultiplied)
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
        return rendered.convert('RGBA').resize((size, size), Image.Resampling.LANCZOS)


def main():
    app = QGuiApplication.instance() or QGuiApplication([])
    assets = ROOT / 'assets'
    source = assets / 'adf-mark.svg'
    large = render_icon(1024, source)
    large.save(assets / 'adf.png')
    # Each frame comes from the vector. 32-bit DIB frames retain alpha / AND
    # masks when Windows loads the icon as a native resource.
    frames = [render_icon(size, source) for size in SIZES]
    frames[-1].save(assets / 'adf.ico', sizes=[(s, s) for s in SIZES],
                    append_images=frames[:-1], bitmap_format='bmp')
    large.save(assets / 'adf.icns')
    print('ADF transparent blue A icons exported from assets/adf-mark.svg')


if __name__ == '__main__':
    main()

"""What a page looks like when drawn, for styles the PDF hides.

Some producers paint text through clipping and fills, or build a fill from
hundreds of thin lines. The text data then reports black text and no cell
colour. A rendering of the page shows what the reader sees; these helpers
read colours from it.
"""
import numpy as np
import pymupdf

ZOOM = 2


class PageLook:
    def __init__(self, page):
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(ZOOM, ZOOM), alpha=False)
        self.image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.width, pixmap.n)[:, :, :3]

    def _area(self, rect):
        x0, y0 = max(0, int(rect.x0 * ZOOM)), max(0, int(rect.y0 * ZOOM))
        x1, y1 = min(self.image.shape[1], int(rect.x1 * ZOOM) + 1), min(self.image.shape[0], int(rect.y1 * ZOOM) + 1)
        return self.image[y0:y1, x0:x1].reshape(-1, 3).astype(int) if x1 > x0 and y1 > y0 else None

    def background(self, rect):
        """The most common colour in rect, as (r, g, b)."""
        pixels = self._area(rect)
        if pixels is None or not len(pixels):
            return None
        quantized = pixels // 8 * 8
        values, counts = np.unique(quantized, axis=0, return_counts=True)
        return tuple(int(v) + 4 for v in values[counts.argmax()])

    def surround(self, rect, margin=2):
        """The most common colour in a thin ring just outside rect."""
        outer = pymupdf.Rect(rect.x0 - margin, rect.y0 - margin, rect.x1 + margin, rect.y1 + margin)
        pieces = [self._area(part) for part in (
            pymupdf.Rect(outer.x0, outer.y0, outer.x1, rect.y0), pymupdf.Rect(outer.x0, rect.y1, outer.x1, outer.y1),
            pymupdf.Rect(outer.x0, rect.y0, rect.x0, rect.y1), pymupdf.Rect(rect.x1, rect.y0, outer.x1, rect.y1))]
        pixels = [piece for piece in pieces if piece is not None and len(piece)]
        if not pixels:
            return None
        values, counts = np.unique(np.concatenate(pixels) // 8 * 8, axis=0, return_counts=True)
        return tuple(int(v) + 4 for v in values[counts.argmax()])

    def ink_colour(self, rect):
        """Median colour of the pixels in rect that differ from the colour
        around it (a glyph box can hold more ink than background)."""
        pixels = self._area(rect)
        back = self.surround(rect)
        if pixels is None or back is None:
            return None
        distance = np.abs(pixels - np.array(back)).sum(1)
        ink = pixels[distance > 150]
        if len(ink) < 6:
            return None
        # Glyph edges blend with the background; the core pixels, those
        # farthest from it, carry the text colour.
        core = ink[distance[distance > 150] >= np.percentile(distance[distance > 150], 70)]
        return tuple(int(v) for v in np.median(core, axis=0))


def hex_colour(rgb):
    return '#' + ''.join(f'{max(0, min(255, value)):02X}' for value in rgb)


def far(a, b, limit=120):
    """Whether two colours differ in kind, not only in rendering.

    Thin glyphs render lighter than their colour, so two greys must differ
    a lot in lightness (black text drawn white) to count.
    """
    grey = lambda colour: max(colour) - min(colour) < 40
    if grey(a) and grey(b):
        return abs(sum(a) - sum(b)) > 450
    return sum(abs(x - y) for x, y in zip(a, b)) > limit

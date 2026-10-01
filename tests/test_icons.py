"""Check shipped icon data with Qt and Windows' native icon decoder.

The Windows check reads only the ICO data file; it does not load or execute
the ADF shell DLL or the packaged application.
"""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import struct
import sys
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PIL import Image
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication


ROOT = Path(__file__).resolve().parents[1]
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def windows_icon_pixels(size):
    """Decode ICO data and draw it onto a transparent 32-bit menu bitmap."""
    user = ctypes.WinDLL('user32', use_last_error=True)
    gdi = ctypes.WinDLL('gdi32', use_last_error=True)
    handle = wintypes.HANDLE
    user.LoadImageW.argtypes = [handle, wintypes.LPCWSTR, wintypes.UINT,
                               ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user.LoadImageW.restype = handle
    user.DrawIconEx.argtypes = [handle, ctypes.c_int, ctypes.c_int, handle,
                               ctypes.c_int, ctypes.c_int, wintypes.UINT,
                               handle, wintypes.UINT]
    user.DrawIconEx.restype = wintypes.BOOL
    user.DestroyIcon.argtypes = [handle]
    gdi.CreateDIBSection.argtypes = [handle, ctypes.c_void_p, wintypes.UINT,
                                   ctypes.POINTER(ctypes.c_void_p), handle, wintypes.DWORD]
    gdi.CreateDIBSection.restype = handle
    gdi.CreateCompatibleDC.argtypes = [handle]
    gdi.CreateCompatibleDC.restype = handle
    gdi.SelectObject.argtypes = [handle, handle]
    gdi.SelectObject.restype = handle
    gdi.DeleteObject.argtypes = [handle]
    gdi.DeleteDC.argtypes = [handle]
    icon = user.LoadImageW(None, str(ROOT / 'assets' / 'xdf.ico'), 1, size, size, 0x10)
    if not icon:
        raise ctypes.WinError(ctypes.get_last_error())
    bitmap = dc = previous = None
    try:
        info = ctypes.create_string_buffer(struct.pack('<IiiHHIIiiII',
            40, size, -size, 1, 32, 0, 0, 0, 0, 0, 0) + bytes(4))
        pixels = ctypes.c_void_p()
        bitmap = gdi.CreateDIBSection(None, info, 0, ctypes.byref(pixels), None, 0)
        dc = gdi.CreateCompatibleDC(None)
        if not bitmap or not pixels.value or not dc:
            raise ctypes.WinError(ctypes.get_last_error())
        ctypes.memset(pixels, 0, size * size * 4)
        previous = gdi.SelectObject(dc, bitmap)
        if not previous or previous == ctypes.c_void_p(-1).value:
            previous = None
            raise ctypes.WinError(ctypes.get_last_error())
        # DI_NORMAL | DI_NOMIRROR: matches the documented Windows menu path.
        if not user.DrawIconEx(dc, 0, 0, icon, size, size, 0, None, 0x13):
            raise ctypes.WinError(ctypes.get_last_error())
        gdi.GdiFlush()
        return ctypes.string_at(pixels, size * size * 4)
    finally:
        if previous:
            gdi.SelectObject(dc, previous)
        if dc:
            gdi.DeleteDC(dc)
        if bitmap:
            gdi.DeleteObject(bitmap)
        user.DestroyIcon(icon)


class IconTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_shipped_frames_have_transparent_background_and_vermilion_wordmark(self):
        with Image.open(ROOT / 'assets' / 'xdf.ico') as ico:
            self.assertEqual(ico.ico.sizes(), {(s, s) for s in SIZES})
            images = [ico.ico.getimage((s, s)).convert('RGBA') for s in SIZES]
        with Image.open(ROOT / 'assets' / 'xdf.png') as png:
            self.assertEqual(png.mode, 'RGBA')
            images.append(png.copy())
        for image in images:
            with self.subTest(size=image.width):
                self.assertEqual(image.getpixel((0, 0))[3], 0)
                # Counterforms may pass through the center; the background is clear.
                self.assertEqual(image.getpixel((image.width - 1, image.height - 1))[3], 0)
                pixels = image.get_flattened_data()
                self.assertTrue(any(0 < p[3] < 255 for p in pixels))
                opaque = [p for p in pixels if p[3] == 255]
                self.assertTrue(opaque)
                self.assertGreater(sum(p[3] == 0 for p in pixels), image.width * image.height // 3)
                self.assertTrue(all(r > g > b for r, g, b, a in opaque))
                # Flat brand fill stays consistent even at narrow counter edges.
                self.assertTrue(all(abs(actual - expected) <= 3
                                    for pixel in opaque
                                    for actual, expected in zip(pixel[:3], (240, 75, 45))))

    def test_qt_loads_all_menu_sizes_with_alpha(self):
        for name in ('xdf.png', 'xdf.ico'):
            icon = QIcon(str(ROOT / 'assets' / name))
            for size in SIZES[:7]:
                with self.subTest(asset=name, size=size):
                    pixmap = icon.pixmap(size, size)
                    self.assertFalse(pixmap.isNull())
                    self.assertEqual((pixmap.width(), pixmap.height()), (size, size))
                    self.assertTrue(pixmap.hasAlphaChannel())
                    self.assertEqual(pixmap.toImage().pixelColor(0, 0).alpha(), 0)

    @unittest.skipUnless(sys.platform == 'win32', 'Windows ICO decoding')
    def test_windows_menu_pixels_keep_premultiplied_alpha_at_all_scales(self):
        for size in SIZES[:7]:
            with self.subTest(size=size):
                raw = windows_icon_pixels(size)
                pixels = list(struct.iter_unpack('BBBB', raw))  # BGRA
                self.assertEqual(pixels[0], (0, 0, 0, 0))
                self.assertTrue(any(p[3] == 255 for p in pixels))
                self.assertTrue(any(0 < p[3] < 255 for p in pixels))
                self.assertTrue(all(max(b, g, r) <= a for b, g, r, a in pixels))
                self.assertTrue(all(r > g > b for b, g, r, a in pixels if a == 255))


if __name__ == '__main__':
    unittest.main()

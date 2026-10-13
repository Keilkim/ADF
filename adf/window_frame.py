"""Windows title bar drawn by XDF, so document tabs can sit where the title was.

The window keeps the styles of a normal captioned window, so Windows still
snaps, animates, shadows and resizes it; only the caption area becomes client
area. XDF answers hit tests so its empty title bar still drags the window.
"""

import ctypes
from ctypes import wintypes

WM_NCCALCSIZE = 0x0083
WM_NCHITTEST = 0x0084
WM_MOVING = 0x0216
WM_ENTERSIZEMOVE = 0x0231
WM_EXITSIZEMOVE = 0x0232

HTCAPTION = 2
HTLEFT, HTRIGHT, HTTOP, HTTOPLEFT, HTTOPRIGHT = 10, 11, 12, 13, 14
HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT = 15, 16, 17

GWL_STYLE = -16
WS_CAPTION = 0x00C00000
WS_SYSMENU = 0x00080000
WS_THICKFRAME = 0x00040000
WS_MINIMIZEBOX = 0x00020000
WS_MAXIMIZEBOX = 0x00010000
SWP_FRAMECHANGED = 0x0020
SWP_NOACTIVATE = 0x0010
SWP_NOZORDER = 0x0004
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001


class MARGINS(ctypes.Structure):
    _fields_ = [('left', ctypes.c_int), ('right', ctypes.c_int),
                ('top', ctypes.c_int), ('bottom', ctypes.c_int)]


def _user32():
    user32 = ctypes.windll.user32
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    return user32


def install(hwnd):
    """Give a frameless Qt window the styles of a captioned one, and its shadow."""
    user32 = _user32()
    style = user32.GetWindowLongPtrW(hwnd, GWL_STYLE)
    user32.SetWindowLongPtrW(hwnd, GWL_STYLE, style | WS_CAPTION | WS_SYSMENU | WS_THICKFRAME
                             | WS_MINIMIZEBOX | WS_MAXIMIZEBOX)
    try:
        # A one-pixel frame lets DWM draw the window shadow on Windows 10 too.
        ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(wintypes.HWND(hwnd), ctypes.byref(MARGINS(1, 1, 1, 1)))
    except OSError:
        pass
    refresh(hwnd)


def refresh(hwnd):
    """Ask Windows to measure the frame again, after a style or screen change."""
    _user32().SetWindowPos(hwnd, None, 0, 0, 0, 0, SWP_FRAMECHANGED | SWP_NOMOVE | SWP_NOSIZE
                           | SWP_NOZORDER | SWP_NOACTIVATE)


def message(address):
    return wintypes.MSG.from_address(int(address))


def edge_hit(x, y, width, height, border):
    """The resize edge under a point inside the window, or None."""
    left, right = x < border, x >= width - border
    top, bottom = y < border, y >= height - border
    if top and left:
        return HTTOPLEFT
    if top and right:
        return HTTOPRIGHT
    if bottom and left:
        return HTBOTTOMLEFT
    if bottom and right:
        return HTBOTTOMRIGHT
    if left:
        return HTLEFT
    if right:
        return HTRIGHT
    if top:
        return HTTOP
    if bottom:
        return HTBOTTOM
    return None

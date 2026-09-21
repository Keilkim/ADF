"""Touchpad pinch as zoom.

Windows turns a precision touchpad pinch into Ctrl+wheel messages. The Ctrl is
only a flag in the message (MK_CONTROL); the keyboard state that Qt reads for
wheel modifiers stays up, so a pinch arrived as plain scrolling. A native event
filter remembers the time of such messages, which Qt gives to the wheel event.
macOS sends a zoom gesture instead.
"""
from collections import deque
import sys

from PySide6.QtCore import QAbstractNativeEventFilter, QEvent, Qt

WM_MOUSEWHEEL = 0x020A
MK_CONTROL = 0x0008
# A mouse wheel notch. A pinch sends many smaller deltas.
NOTCH = 120
NOTCH_ZOOM = 1.12


class PinchFilter(QAbstractNativeEventFilter):
    def __init__(self):
        super().__init__()
        self.times = deque(maxlen=64)

    def nativeEventFilter(self, event_type, message):
        if bytes(event_type) == b'windows_generic_MSG':
            from ctypes import wintypes
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_MOUSEWHEEL and msg.wParam & MK_CONTROL:
                self.times.append(msg.time)
        return False, 0


_filter = None


def install(app):
    global _filter
    if sys.platform == 'win32' and _filter is None:
        _filter = PinchFilter()
        app.installNativeEventFilter(_filter)
    return _filter


def zooms(event):
    """Whether a wheel event asks to zoom: Ctrl+wheel or a touchpad pinch."""
    return bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier
                or (_filter is not None and event.timestamp() in _filter.times))


def wheel_zoom(event):
    """Zoom factor of a wheel event: 12% a notch, proportionally less for a pinch."""
    return NOTCH_ZOOM ** max(-3, min(3, event.angleDelta().y() / NOTCH))


def gesture_zoom(event):
    """Zoom factor of a macOS trackpad pinch, or None for any other event."""
    if event.type() == QEvent.Type.NativeGesture and event.gestureType() == Qt.NativeGestureType.ZoomNativeGesture:
        return max(.5, 1 + event.value())
    return None

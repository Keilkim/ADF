"""Touchpad pinch as zoom.

Windows turns a precision touchpad pinch into Ctrl+wheel messages. The Ctrl is
only a flag in the message (MK_CONTROL); the keyboard state that Qt reads for
wheel modifiers stays up, so a pinch arrived as plain scrolling. A native event
filter remembers the time and delta of such messages, which Qt gives to the
wheel event. macOS sends a zoom gesture instead.
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
        # (time, delta) of each flagged message. The time alone is only a
        # millisecond tick, which a plain wheel message can share.
        self.wheels = deque(maxlen=64)

    def nativeEventFilter(self, event_type, message):
        if bytes(event_type) == b'windows_generic_MSG':
            from ctypes import wintypes
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_MOUSEWHEEL and msg.wParam & MK_CONTROL:
                delta = (msg.wParam >> 16) & 0xFFFF  # HIWORD, a signed short
                self.wheels.append((msg.time, delta - 0x10000 if delta & 0x8000 else delta))
        return False, 0

    def take(self, event):
        """Whether a wheel event came from a flagged message; each message zooms once."""
        key = (event.timestamp(), event.angleDelta().y())
        if key in self.wheels:
            self.wheels.remove(key)
            return True
        return False


_filter = None


def install(app):
    global _filter
    if sys.platform == 'win32' and _filter is None:
        _filter = PinchFilter()
        app.installNativeEventFilter(_filter)
    return _filter


def zooms(event):
    """Whether a wheel event asks to zoom: Ctrl+wheel or a touchpad pinch."""
    pinched = _filter is not None and _filter.take(event)
    return bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier or pinched)


def wheel_zoom(event):
    """Zoom factor of a wheel event: 12% a notch, proportionally less for a pinch."""
    return NOTCH_ZOOM ** max(-3, min(3, event.angleDelta().y() / NOTCH))


def gesture_zoom(event):
    """Zoom factor of a macOS trackpad pinch, or None for any other event."""
    if event.type() == QEvent.Type.NativeGesture and event.gestureType() == Qt.NativeGestureType.ZoomNativeGesture:
        return max(.5, 1 + event.value())
    return None

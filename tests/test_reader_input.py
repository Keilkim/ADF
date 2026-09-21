"""Wheel event sequences from notched mice and macOS trackpads."""
import os
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pymupdf
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt, QTimer
from PySide6.QtGui import QNativeGestureEvent, QWheelEvent, QInputDevice, QPointingDevice
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from adf import pinch
from adf.document import PdfDocument
from adf.viewer import PdfView

MOUSE = QPointingDevice('test mouse', 1, QInputDevice.DeviceType.Mouse,
                       QPointingDevice.PointerType.Generic, QInputDevice.Capability.Position, 1, 3)
TRACKPAD = QPointingDevice('test trackpad', 2, QInputDevice.DeviceType.TouchPad,
                          QPointingDevice.PointerType.Finger,
                          QInputDevice.Capability.Position | QInputDevice.Capability.PixelScroll, 2, 1)


@pytest.fixture(scope='module')
def app():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


@pytest.fixture(params=['single', 'spread', 'spread_right'])
def view(app, tmp_path, request):
    source = tmp_path / 'pages.pdf'
    with pymupdf.open() as pdf:
        for index in range(12):
            page = pdf.new_page(width=360, height=480)
            page.insert_text((40, 60), f'Page {index + 1}')
        pdf.save(source)
    document = PdfDocument()
    document.open(source)
    view = PdfView()
    view.resize(900, 700)
    view.start_right = request.param == 'spread_right'
    view.show()
    view.load(document)
    view.set_mode('spread' if request.param.startswith('spread') else 'single')
    view.fit('page')
    app.processEvents()
    yield view
    for timer in view.findChildren(QTimer):
        timer.stop()
    view.close()
    view.deleteLater()
    app.processEvents()
    document.close()


def wheel(view, y=0, *, pixels=None, phase=Qt.ScrollPhase.NoScrollPhase,
          modifiers=Qt.KeyboardModifier.NoModifier, inverted=False, precise=True):
    pos = QPoint(50, 50)
    event = QWheelEvent(QPointF(pos), QPointF(view.viewport().mapToGlobal(pos)),
                        QPoint(*pixels) if pixels is not None else QPoint(), QPoint(0, y),
                        Qt.MouseButton.NoButton, modifiers, phase, inverted,
                        Qt.MouseEventSource.MouseEventNotSynthesized,
                        TRACKPAD if pixels is not None and precise else MOUSE)
    QApplication.sendEvent(view.viewport(), event)


def test_trackpad_gesture_turns_only_one_page_group(view):
    for direction in (-1, 1):
        view.goto(5)
        target = view.navigation_target(-direction)
        wheel(view, phase=Qt.ScrollPhase.ScrollBegin)
        wheel(view, direction, pixels=(0, direction), phase=Qt.ScrollPhase.ScrollUpdate)
        assert view.current == 5  # A tiny touch must not turn the page.
        for _ in range(40):
            wheel(view, direction * 8, pixels=(0, direction * 8), phase=Qt.ScrollPhase.ScrollUpdate)
        for _ in range(30):
            wheel(view, direction * 4, pixels=(0, direction * 4), phase=Qt.ScrollPhase.ScrollMomentum)
        wheel(view, phase=Qt.ScrollPhase.ScrollEnd)
        assert view.current == target
        target = view.navigation_target(-direction)
        wheel(view, phase=Qt.ScrollPhase.ScrollBegin)
        wheel(view, direction * 120, pixels=(0, direction * 80), phase=Qt.ScrollPhase.ScrollUpdate)
        wheel(view, phase=Qt.ScrollPhase.ScrollEnd)
        assert view.current == target


def test_fine_wheel_accumulates_but_notched_wheel_still_steps(view):
    for _ in range(3):
        wheel(view, -30)
        assert view.current == 0
    target = view.navigation_target(1)
    wheel(view, -30)
    assert view.current == target
    for _ in range(3):
        target = view.navigation_target(1)
        wheel(view, -120)
        assert view.current == target
    target = view.navigation_target(-1)
    wheel(view, 120)
    assert view.current == target


def test_unphased_pixel_burst_cannot_run_to_document_end(view):
    target = view.navigation_target(1)
    for _ in range(40):
        wheel(view, -8, pixels=(0, -8))
    assert view.current == target
    QTest.qWait(300)
    target = view.navigation_target(1)
    wheel(view, -120, pixels=(0, -80))
    assert view.current == target


def test_zoomed_page_scrolls_pixels_before_turning(view):
    view.goto(5)
    view.set_zoom(2)
    QApplication.processEvents()
    scroll = view.verticalScrollBar()
    assert scroll.maximum() > scroll.minimum() + 100
    scroll.setValue(scroll.minimum() + 30)
    initial = scroll.value()
    wheel(view, phase=Qt.ScrollPhase.ScrollBegin)
    wheel(view, -2, pixels=(0, -18), phase=Qt.ScrollPhase.ScrollUpdate)
    assert view.current == 5
    assert scroll.value() == initial + 18
    # Momentum may reach the edge, but must not start a page turn there.
    scroll.setValue(scroll.maximum())
    for _ in range(20):
        wheel(view, -30, pixels=(0, -30), phase=Qt.ScrollPhase.ScrollMomentum)
    wheel(view, phase=Qt.ScrollPhase.ScrollEnd)
    assert view.current == 5
    target = view.navigation_target(1)
    wheel(view, phase=Qt.ScrollPhase.ScrollBegin)
    wheel(view, -120, pixels=(0, -80), phase=Qt.ScrollPhase.ScrollUpdate)
    wheel(view, phase=Qt.ScrollPhase.ScrollEnd)
    assert view.current == target
    assert scroll.value() == scroll.minimum()


def test_horizontal_and_zero_delta_events_do_not_turn_or_zoom(view):
    view.goto(5)
    view.set_zoom(3)
    QApplication.processEvents()
    horizontal = view.horizontalScrollBar()
    horizontal.setValue(horizontal.minimum() + 50)
    initial = horizontal.value()
    scale = view.transform().m11()
    wheel(view, pixels=(18, 0), phase=Qt.ScrollPhase.ScrollUpdate)
    assert horizontal.value() == initial - 18
    for phase in (Qt.ScrollPhase.ScrollBegin, Qt.ScrollPhase.ScrollEnd):
        wheel(view, phase=phase, modifiers=Qt.KeyboardModifier.ControlModifier)
    assert view.current == 5
    assert view.transform().m11() == scale


def test_pixel_direction_takes_precedence_and_respects_natural_scrolling(view):
    view.goto(5)
    target = view.navigation_target(1)
    wheel(view, phase=Qt.ScrollPhase.ScrollBegin)
    wheel(view, 120, pixels=(0, -80), phase=Qt.ScrollPhase.ScrollUpdate, inverted=True)
    assert view.current == target


def test_macos_notched_mouse_with_estimated_pixels_keeps_each_notch(view):
    for _ in range(3):
        target = view.navigation_target(1)
        wheel(view, -120, pixels=(0, -2), precise=False)
        assert view.current == target
    target = view.navigation_target(-1)
    wheel(view, 120, pixels=(0, 2), precise=False)
    assert view.current == target


def test_boundaries_and_previous_zoomed_page(view):
    for index, direction in [(0, 1), (len(view.pages) - 1, -1)]:
        view.goto(index)
        wheel(view, phase=Qt.ScrollPhase.ScrollBegin)
        for _ in range(10):
            wheel(view, direction * 120, pixels=(0, direction * 80), phase=Qt.ScrollPhase.ScrollUpdate)
        wheel(view, phase=Qt.ScrollPhase.ScrollEnd)
        assert view.current == index
    view.goto(5)
    view.set_zoom(2)
    QApplication.processEvents()
    scroll = view.verticalScrollBar()
    scroll.setValue(scroll.minimum())
    target = view.navigation_target(-1)
    wheel(view, phase=Qt.ScrollPhase.ScrollBegin)
    wheel(view, 120, pixels=(0, 80), phase=Qt.ScrollPhase.ScrollUpdate)
    wheel(view, phase=Qt.ScrollPhase.ScrollEnd)
    assert view.current == target
    assert scroll.value() == scroll.maximum()


def pinch_wheel(view, y, timestamp, *, modifiers=Qt.KeyboardModifier.NoModifier):
    pos = QPoint(50, 50)
    event = QWheelEvent(QPointF(pos), QPointF(view.viewport().mapToGlobal(pos)), QPoint(), QPoint(0, y),
                        Qt.MouseButton.NoButton, modifiers, Qt.ScrollPhase.NoScrollPhase, False,
                        Qt.MouseEventSource.MouseEventNotSynthesized, TRACKPAD)
    event.setTimestamp(timestamp)
    QApplication.sendEvent(view.viewport(), event)


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows wheel messages')
def test_windows_touchpad_pinch_zooms_in_proportion(view, monkeypatch):
    from ctypes import addressof, wintypes
    monkeypatch.setattr(pinch, '_filter', pinch.PinchFilter())

    def message(time, wparam):
        msg = wintypes.MSG()
        msg.message, msg.wParam, msg.time = pinch.WM_MOUSEWHEEL, wparam, time
        pinch._filter.nativeEventFilter(b'windows_generic_MSG', addressof(msg))

    view.goto(5)
    scale = view.transform().m11()
    # Windows marks the pinch's wheel messages with MK_CONTROL; the Ctrl key is up.
    message(1000, (30 << 16) | pinch.MK_CONTROL)
    pinch_wheel(view, 30, 1000)
    assert view.transform().m11() == pytest.approx(scale * 1.12 ** .25)
    assert view.current == 5
    message(1001, (-30 & 0xFFFF) << 16 | pinch.MK_CONTROL)
    pinch_wheel(view, -30, 1001)
    assert view.transform().m11() == pytest.approx(scale)
    # A wheel message without the flag still scrolls.
    message(1002, (-120 & 0xFFFF) << 16)
    target = view.navigation_target(1)
    pinch_wheel(view, -120, 1002)
    assert view.transform().m11() == pytest.approx(scale)
    assert view.current == target


def test_ctrl_wheel_notch_still_zooms_twelve_percent(view):
    scale = view.transform().m11()
    wheel(view, 120, modifiers=Qt.KeyboardModifier.ControlModifier)
    assert view.transform().m11() == pytest.approx(scale * 1.12)
    wheel(view, -40, modifiers=Qt.KeyboardModifier.ControlModifier)
    assert view.transform().m11() == pytest.approx(scale * 1.12 * 1.12 ** (-1 / 3))


def test_macos_trackpad_pinch_gesture_zooms(view):
    scale = view.transform().m11()
    pos = QPointF(50, 50)
    for value in (.1, -.05):
        event = QNativeGestureEvent(Qt.NativeGestureType.ZoomNativeGesture, TRACKPAD, 2, pos, pos,
                                    QPointF(view.viewport().mapToGlobal(pos.toPoint())), value, QPointF())
        QApplication.sendEvent(view.viewport(), event)
    assert view.transform().m11() == pytest.approx(scale * 1.1 * .95)


def test_compare_view_turns_pinch_fractions_into_zoom_steps(app):
    from adf.compare_widgets import CompareView
    view = CompareView()
    steps = []
    view.zoomRequested.connect(steps.append)
    for _ in range(3):
        wheel(view, 40, modifiers=Qt.KeyboardModifier.ControlModifier)
    assert steps == [1]
    pos = QPointF(50, 50)
    event = QNativeGestureEvent(Qt.NativeGestureType.ZoomNativeGesture, TRACKPAD, 2, pos, pos, pos,
                                1 / 1.12 - 1, QPointF())
    QApplication.sendEvent(view.viewport(), event)
    assert steps == [1, -1]
    view.deleteLater()

"""Alignment math plus real pointer, modifier, rotation and PDF workflows."""
import io
import os
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pymupdf
import pytest
from PIL import Image
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from adf.document import PdfDocument
from adf.snapping import SnapIndex
from adf.text_groups import text_group_at
from adf.viewer import PdfView


def test_move_edges_centers_nearest_target_and_threshold():
    index = SnapIndex(QRectF(0, 0, 400, 500), [QRectF(80, 100, 60, 40)])
    rect, guides = index.snap(QRectF(83, 163, 40, 40), 7)
    assert rect.left() == 80
    assert rect.top() == 163
    assert (0, 80) in guides
    rect, _ = index.snap(QRectF(184, 233, 40, 40), 7)
    assert rect.center() == QPointF(200, 250)
    rect, _ = index.snap(QRectF(53, 183, 20, 20), 6)
    assert rect == QRectF(53, 183, 20, 20)


def test_resize_fixed_origin_minimum_and_page_bounds():
    index = SnapIndex(QRectF(0, 0, 400, 500), [QRectF(82, 102, 50, 40)])
    rect, guides = index.snap(QRectF(20, 30, 59, 70), 7, resize=True)
    assert rect == QRectF(20, 30, 62, 72)
    assert (0, 82) in guides and (1, 102) in guides
    rect, _ = index.snap(QRectF(68, 83, 18, 10), 7, resize=True)
    assert rect.width() >= 18 and rect.height() >= 10
    rect, _ = index.snap(QRectF(0, 0, 397, 497), 7, resize=True)
    assert rect == QRectF(0, 0, 400, 500)


@pytest.mark.parametrize('ratio', [.25, .5, 1, 2, 4])
def test_image_resize_preserves_ratio_and_reports_only_real_alignments(ratio):
    index = SnapIndex(QRectF(0, 0, 600, 600), [QRectF(121, 150, 31, 40)])
    rect, guides = index.snap(QRectF(20, 20, 100, 100 * ratio), 7,
                             resize=True, ratio=ratio, minimum=(15, 0))
    assert rect.height() / rect.width() == pytest.approx(ratio)
    assert rect.right() == 121
    assert (0, 121) in guides
    assert all(abs((rect.right(), rect.bottom())[axis] - value) < 1e-5 for axis, value in guides)
    assert rect.right() <= 600 and rect.bottom() <= 600


@pytest.fixture
def editor(tmp_path):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    data = io.BytesIO()
    Image.new('RGB', (80, 40), (210, 60, 80)).save(data, format='PNG')
    source = tmp_path / 'source.pdf'
    with pymupdf.open() as pdf:
        for rotation in (0, 90, 180, 270):
            page = pdf.new_page(width=360, height=480)
            page.insert_text((40, 60), 'Guide text', fontsize=12)
            page.insert_image((220, 100, 280, 130), stream=data.getvalue())
            page.draw_rect((80, 200, 160, 260))
            page.draw_line((40, 300), (300, 300))
            page.set_rotation(rotation)
        pdf.save(source)
    document = PdfDocument()
    document.open(source)
    view = PdfView()
    view.resize(800, 800)
    view.load(document)
    view.set_mode('single')
    view.set_zoom(1)
    view.show()
    app.processEvents()
    errors = []
    previous_hook = sys.excepthook
    sys.excepthook = lambda kind, error, tb: errors.append(error)
    pm = QPixmap()
    pm.loadFromData(data.getvalue())
    QTest.keyRelease(view, Qt.Key.Key_Alt)
    yield view, document, pm, data.getvalue()
    QTest.keyRelease(view, Qt.Key.Key_Alt)
    view.clear_text_selection()
    view.stop_stamp()
    for timer in view.findChildren(QTimer):
        timer.stop()
    view.close()
    view.deleteLater()
    app.processEvents()
    document.close()
    sys.excepthook = previous_hook
    assert not errors


def pointer(view, page, point):
    return view.mapFromScene(view.pages[page].mapToScene(QPointF(*point)))


def place(view, pm):
    view.place_image(pm, width_mm=80 * 25.4 / 72, point=QPointF(100, 330))
    view.placement.setPos(100, 330)
    return view.placement


def test_image_drag_unsticks_alt_updates_without_motion_and_click_does_not_move(editor):
    view, _, pm, _ = editor
    item = place(view, pm)
    start = pointer(view, 0, (140, 350))
    near = pointer(view, 0, (123, 350))
    viewport = view.viewport()
    QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(viewport, near)
    assert item.x() == 80
    assert (0, 80) in view.snap_guides
    QTest.keyPress(view, Qt.Key.Key_Alt)
    assert item.x() == pytest.approx(83, abs=1)
    assert not view.snap_guides
    QTest.keyRelease(view, Qt.Key.Key_Alt)
    assert item.x() == 80
    away = pointer(view, 0, (152, 350))
    QTest.mouseMove(viewport, away)
    assert item.x() == pytest.approx(112, abs=1)
    QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=away)
    assert not view.snap_guides
    item.setPos(83, 330)
    QTest.mouseClick(viewport, Qt.MouseButton.LeftButton, pos=near)
    assert item.x() == 83


def test_real_image_resize_snaps_to_existing_image_and_saves_with_undo(editor, tmp_path):
    view, document, pm, data = editor
    item = place(view, pm)
    viewport = view.viewport()
    start = pointer(view, 0, (180, 370))
    finish = pointer(view, 0, (223, 392))
    QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(viewport, finish)
    assert item.x() + item.size.width() == 220
    assert item.size.width() / item.size.height() == 2
    assert (0, 220) in view.snap_guides
    QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=finish)
    page, rect = view.image_rect()
    document.add_image(page, rect, data)
    document.save(tmp_path / 'snapped.pdf')
    with pymupdf.open(tmp_path / 'snapped.pdf') as saved:
        assert saved[0].get_image_info()[-1]['bbox'] == pytest.approx(tuple(rect))
        assert len(saved[0].get_drawings()) == 2  # Guides never enter the PDF.
    assert document.undo()
    assert len(document.doc[0].get_image_info()) == 1
    assert document.redo()
    assert len(document.doc[0].get_image_info()) == 2


@pytest.mark.parametrize('zoom', [.25, 1, 4])
def test_snap_distance_is_constant_in_screen_pixels(editor, zoom):
    view, _, _, _ = editor
    view.set_zoom(zoom)
    # An empty page isolates the threshold from other object anchors.
    index = SnapIndex(QRectF(0, 0, 360, 480))
    view.snap_cache[(0, view.document.revision, None)] = index
    near = QRectF(6 / zoom, 330, 13, 17)
    assert view.snap_geometry(view.pages[0], near).left() == 0
    outside = QRectF(8 / zoom, 330, 13, 17)
    assert view.snap_geometry(view.pages[0], outside).left() == outside.left()


@pytest.mark.parametrize('page_index', range(4))
def test_rotated_text_move_resize_and_self_exclusion(editor, page_index):
    view, document, _, _ = editor
    view.goto(page_index)
    page = document.doc[page_index]
    group = text_group_at(page, (50, 55))
    view.select_text(page_index, group)
    view.text_mode = True
    item = view.text_placement
    original = QRectF(item.pos(), item.size)
    index = view.snap_index(view.pages[page_index])
    # None of the selected paragraph's unique text boundaries attract itself.
    selected_y = original.top() if page.rotation in (0, 180) else original.left()
    axis = 1 if page.rotation in (0, 180) else 0
    assert selected_y not in index.anchors[axis]
    target = pymupdf.Rect(80, 200, 160, 260) * page.rotation_matrix
    desired = QPointF(target.x0 - 3, target.y0 - 3)
    start = view.mapFromScene(item.mapToScene(QPointF(15, -2)))
    finish = view.mapFromScene(view.pages[page_index].mapToScene(desired + QPointF(15, -2)))
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(view.viewport(), finish)
    assert item.x() == pytest.approx(target.x0)
    assert item.y() == pytest.approx(target.y0)
    assert view.snap_guides
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=finish)
    assert not view.snap_guides
    shown = view.text_rect() * page.rotation_matrix
    assert shown.x0 == pytest.approx(item.x())
    assert shown.y0 == pytest.approx(item.y())
    start = view.mapFromScene(item.mapToScene(QPointF(item.size.width(), item.size.height())))
    finish = pointer(view, page_index, (target.x1 + 3, target.y1 + 3))
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(view.viewport(), finish)
    assert item.x() + item.size.width() == pytest.approx(target.x1)
    assert item.y() + item.size.height() == pytest.approx(target.y1)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=finish)


@pytest.mark.parametrize('page_index', range(4))
def test_stamp_preview_matches_click_on_rotated_pages_and_alt_bypasses(editor, page_index):
    view, document, pm, _ = editor
    view.goto(page_index)
    view.start_stamp(pm, 80 * 25.4 / 72)
    page = document.doc[page_index]
    target = pymupdf.Rect(80, 200, 160, 260) * page.rotation_matrix
    position = (target.x0 + 43, target.y0 + 23)
    point = pointer(view, page_index, position)
    QTest.mouseMove(view.viewport(), point)
    expected = QRectF(target.x0, target.y0, 80, 40)
    assert view.stamp_ghost.pos() == expected.topLeft()
    emitted = []
    view.stampRequested.connect(lambda index, rect: emitted.append((index, rect)))
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=point)
    shown = emitted[-1][1] * page.rotation_matrix
    assert tuple(shown) == pytest.approx((expected.left(), expected.top(), expected.right(), expected.bottom()))
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.AltModifier, pos=point)
    shown = emitted[-1][1] * page.rotation_matrix
    assert shown.x0 == pytest.approx(target.x0 + 3, abs=1)
    assert shown.y0 == pytest.approx(target.y0 + 3, abs=1)
    view.stop_stamp()
    assert not view.snap_guides


def test_snap_targets_refresh_after_edit_undo_and_selection_cancel(editor):
    view, document, _, data = editor
    first = view.snap_index(view.pages[0])
    document.add_image(0, (177, 350, 237, 380), data)
    view.invalidate_page(0)
    assert 177 in view.snap_index(view.pages[0]).anchors[0]
    document.undo()
    assert 177 not in view.snap_index(view.pages[0]).anchors[0]
    assert view.snap_index(view.pages[0]) is not first
    view.snap_geometry(view.pages[0], QRectF(83, 330, 40, 20))
    assert view.snap_guides
    view.set_snap_enabled(False)
    assert not view.snap_guides
    raw = QRectF(83, 330, 40, 20)
    assert view.snap_geometry(view.pages[0], raw) == raw
    view.set_snap_enabled(True)
    view.snap_geometry(view.pages[0], raw)
    view.cancel_image()
    assert not view.snap_guides

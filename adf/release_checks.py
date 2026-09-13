"""Extra --smoke-test checks, also executed inside the installed application.

Only generated fixtures and MainWindow(smoke=True)'s temporary stamp library
are used. This module is not called during normal application startup.
"""
import io
import time
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw
from PySide6.QtCore import QPoint, QPointF, QSettings, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialogButtonBox

from .compare_widgets import CompareDialog
from .stamp_widgets import StampDialog


def check_pen_and_save(window, folder, output):
    source = folder/'pen-save.pdf'
    with pymupdf.open() as doc:
        doc.new_page(width=380, height=480)
        doc.save(source)
    before = source.read_bytes()
    assert window.open_path(source)
    window.set_view_mode('single')
    window.show_pen_options('pen')
    assert window.pen_menu.kind.count() == 3
    window.pen_menu.kind.setCurrentIndex(2)
    QTest.qWait(30)
    window.pen_menu.grab().save(str(output.with_name(output.stem+'-pen-options.png')))
    window.pen_menu.hide()
    QTest.qWait(30)
    view = window.view
    item = view.pages[0]
    points = [view.mapFromScene(item.mapToScene(QPointF(x, y))) for x, y in [(60, 80), (100, 110), (145, 85)]]
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=points[0])
    QTest.mouseMove(view.viewport(), points[1])
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=points[2])
    assert window.document.dirty and source.read_bytes() == before
    assert len(list(window.document.doc[0].annots())) == 1
    window.change_pointer('select_tool')
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=points[1])
    selection = view.ink_selection
    assert selection is not None and len(selection.handles()) == 9
    start = view.mapFromScene(selection.mapToScene(QPointF()))
    end = start+QPoint(30, 20)
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(view.viewport(), end)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
    assert view.ink_selection is not None
    QTest.qWait(40)
    window.grab().save(str(output.with_name(output.stem+'-pen.png')))
    window.actions['save'].trigger()
    assert not window.document.dirty and source.read_bytes() != before
    with pymupdf.open(source) as saved:
        page = saved[0]
        annotation = page.first_annot
        assert annotation.type[0] == pymupdf.PDF_ANNOT_INK
        assert annotation.flags & pymupdf.PDF_ANNOT_IS_PRINT
        from .ink import read_ink
        assert read_ink(annotation)['kind'] == 'brush'
        assert page.get_pixmap(annots=True).samples != page.get_pixmap(annots=False).samples
    window.undo()
    assert window.document.dirty and len(list(window.document.doc[0].annots())) == 1
    window.undo()
    assert window.document.dirty and not list(window.document.doc[0].annots())
    window.redo()
    window.redo()
    assert not window.document.dirty and len(list(window.document.doc[0].annots())) == 1
    window.change_pointer('select_tool')
    result = dict(pen_mouse_input=True, pen_saved_as_pdf_ink=True, pen_printable=True,
                pen_undo_redo=True, pen_object_transform=True, pen_eight_resize_handles=True,
                pen_rotation_handle=True, pen_kinds=True, save_current_file=True)
    result.update(check_eraser_and_region_copy(window, folder, output))
    return result


def check_eraser_and_region_copy(window, folder, output):
    from .ink import read_ink
    from .viewer import page_raster
    source = folder/'eraser-copy.pdf'
    with pymupdf.open() as doc:
        page = doc.new_page(width=380, height=480)
        page.insert_text((40, 55), 'Copy the whole table', fontsize=14)
        page.draw_rect((40, 70, 240, 170), color=(0, 0, 0))
        page.draw_line((40, 120), (240, 120), color=(0, 0, 0))
        page.draw_polyline([(150, 155), (180, 130), (220, 145)], color=(0, 0, 1), width=3)
        image = io.BytesIO()
        Image.new('RGB', (30, 30), 'red').save(image, format='PNG')
        page.insert_image((50, 80, 90, 110), stream=image.getvalue())
        doc.save(source)
    assert window.open_path(source)
    window.set_view_mode('single')
    view = window.view
    window.show_pen_options('pen')
    menu = window.pen_menu
    menu.change_tool('pen')
    menu.kind.setCurrentIndex(0)
    QApplication.processEvents()
    def position(x, y):
        return view.mapFromScene(view.pages[0].mapToScene(QPointF(x, y)))
    def drag(start, end):
        QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=position(*start))
        QTest.mouseMove(view.viewport(), position(*end))
        QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=position(*end))
    revision = window.document.revision
    drag((50, 220), (240, 220))
    assert not menu.isVisible() and window.document.revision == revision
    assert 240 <= menu.field.width() <= 280 and menu.field.height() == 100
    assert menu.width() <= 304 and menu.height() <= 470
    drag((50, 220), (240, 220))
    window.show_pen_options('eraser')
    QTest.qWait(20)
    menu.grab().save(str(output.with_name(output.stem+'-eraser-options.png')))
    menu.hide()
    drag((145, 190), (145, 250))
    page = window.document.doc[0]
    assert len(read_ink(page.first_annot)['paths']) == 2
    assert page.get_pixmap().pixel(145, 220) == (255, 255, 255)
    window.undo()
    page = window.document.doc[0]
    assert len(read_ink(page.first_annot)['paths']) == 1
    window.redo()
    window.change_pointer('region_tool')
    try:
        drag((30, 30), (260, 250))
        region = view.selected_region()
        expected, _ = page_raster(window.document.doc[0], 3, region[1])
        copied = QApplication.clipboard().image()
        assert copied == expected.toImage()
        assert window.copy_notice.isVisible() and '캡처' in window.copy_notice.title.text()
        assert view.selection_item.finished and view.selection_item.text_path.isEmpty()
        copied.save(str(output.with_name(output.stem+'-region-copy.png')))
    finally:
        QApplication.clipboard().clear()
    window.document.save(source)
    with pymupdf.open(source) as saved:
        page = saved[0]
        assert len(read_ink(page.first_annot)['paths']) == 2
        assert 'Copy the whole table' in page.get_text()
        assert len(page.get_image_info()) == 1
    window.change_pointer('select_tool')
    # Exercise the separate main target and corner settings inside the executable.
    for tool in ('pen', 'eraser'):
        button = getattr(window, tool+'_button')
        QTest.mouseClick(button, Qt.MouseButton.LeftButton, pos=QPoint(12, 12))
        assert window.pointer_mode == tool and view.pen.enabled
        assert not menu.isVisible()
        QTest.mouseClick(button, Qt.MouseButton.LeftButton, pos=QPoint(12, 12))
        assert window.pointer_mode == 'select_tool' and not view.pen.enabled
        QTest.mouseClick(button.options, Qt.MouseButton.LeftButton)
        assert menu.isVisible() and window.pointer_mode == tool
        menu.slider.setValue(4)
        small = menu.preview.grab().toImage()
        menu.slider.setValue(55)
        assert small != menu.preview.grab().toImage()
        menu.hide()
        window.change_pointer('select_tool')
    window.change_pointer('pen')
    pen = view.pen
    pen.begin(QPointF(position(50, 290)))
    pen.append(QPointF(position(110, 299)))
    pen.append(QPointF(position(230, 300)))
    QTest.qWait(pen.HOLD_MS+100)
    assert pen.line_origin is not None and len(pen.paths[0]) == 2
    pen.finish(QPointF(position(230, 300)))
    page = window.document.doc[0]
    assert len(read_ink(list(page.annots())[-1])['paths'][0]) == 2
    window.undo()
    window.change_pointer('select_tool')
    drag((30, 30), (260, 65))
    assert 'Copy the whole table' in window.clip_text
    assert view.selection_item.finished and not view.selection_item.text_path.isEmpty()
    window.copy_text()
    assert window.copy_notice.isVisible() and '텍스트' in window.copy_notice.title.text()
    QTest.qWait(100)
    window.grab().save(str(output.with_name(output.stem+'-text-selection.png')))
    selection = view.selection_item
    window.escape()
    assert view.selection_item is None and not selection.timer.isActive()
    return dict(pen_popover_dismissal=True, pen_compact_color_picker=True,
                separate_pen_eraser_toggles=True, corner_options=True, tool_size_preview=True,
                hold_to_straighten=True, selected_text_highlight=True, selection_cleanup=True,
                capture_dashed_boundary=True, clipboard_copy_notice=True, eraser_partial_ink=True,
                eraser_undo_redo_save=True, region_copy_mixed_content=True)


def check_ocr_and_loading(window, folder, output):
    from .markdown_widgets import MarkdownOptionsDialog, export_markdown
    import base64
    import re
    from urllib.parse import unquote

    assert window.smoke
    native, scanned = make_layout_fixture(folder)
    original = scanned.read_bytes()
    loading_stages, ocr_stages = set(), set()
    timer = QTimer(window)
    timer.setInterval(20)

    def capture():
        loading = window.loading_dialog
        if loading is not None and loading.isVisible():
            loading_stages.add(loading.stage.text())
            loading.grab().save(str(output.with_name(output.stem+'-loading.png')))
        ocr = window.markdown_progress
        if ocr is not None and ocr.isVisible():
            stage = ocr.stage.text()
            if stage not in ocr_stages:
                ocr_stages.add(stage)
                ocr.grab().save(str(output.with_name(output.stem+'-ocr-progress.png')))
                if '글자 인식' in stage:
                    ocr.grab().save(str(output.with_name(output.stem+'-ocr-recognizing.png')))

    timer.timeout.connect(capture)
    timer.start()
    try:
        assert window.open_path(scanned)
        dialog = MarkdownOptionsDialog(window.document, 0, [0], window)
        dialog.show()
        QTest.qWait(40)
        assert dialog.image_storage.currentData() == 'embedded'
        dialog.grab().save(str(output.with_name(output.stem+'-ocr-options.png')))
        dialog.image_storage.setCurrentIndex(1)
        dialog.grab().save(str(output.with_name(output.stem+'-ocr-options-files.png')))
        markdown_name = dialog.bundle_name()+'.md'
        dialog.reject()
        dialog.deleteLater()
        destination = folder/'markdown-result'
        result = export_markdown(window, dict(pages=[0], output=str(destination),
            force_ocr=False, include_pages=True, markdown_name=markdown_name))
        content = (destination/markdown_name).read_text(encoding='utf-8')
        assert result['markdown_file'] == markdown_name
        encoded = re.findall(r'data:image/png;base64,([A-Za-z0-9+/=]+)', content)
        assert len(encoded) >= 4 and not (destination/'images').exists()
        for value in encoded:
            with Image.open(io.BytesIO(base64.b64decode(value, validate=True))) as picture:
                picture.verify()
        assert result['pages'] == 1 and result['ocr_pages'] == 1
        assert result['figures'] >= 2 and result['tables'] >= 1
        assert '문서 인식 검증 보고서' in content and '99%' in content
        assert content.index('왼쪽 단락') < content.index('오른쪽 단락')
        assert scanned.read_bytes() == original
        assert not list(folder.glob('.adf-md-*'))
        assert loading_stages and any('글자 인식' in stage for stage in ocr_stages)
        window.close_document()
        # Exercise file naming, escaping and relocation without repeating inference.
        from .markdown_export import convert_document
        from .markdown_widgets import publish_bundle
        class ImageRegion:
            def regions(self, pixels):
                height, width = pixels.shape[:2]
                return [dict(label='chart', box=[0, 0, width, height], score=1)]
            def recognize(self, pixels):
                return []
        name = '20260909_보고서 (검토)_a1b2c3d4e5f6.md'
        converted = folder/'separate-images'
        convert_document(native, converted, '', engine=ImageRegion(), image_storage='files', markdown_name=name)
        moved = folder/'다른 폴더'
        publish_bundle(converted, moved, name, 'files')
        separate = (moved/name).read_text(encoding='utf-8')
        links = re.findall(r'\]\(((?:images|pages)/[^)]+)\)', separate)
        assert links and all((moved/unquote(link)).is_file() for link in links)
        assert all(Path(unquote(link)).name.startswith(name[:-3]) for link in links)
        return dict(pdf_loading_stages=sorted(loading_stages), ocr_stages=sorted(ocr_stages),
            local_korean_ocr=True, markdown_reading_order=True, markdown_tables=True,
            markdown_graphics=True, markdown_worker_cleanup=True, ocr_result=result,
            markdown_embedded_images=True, markdown_relative_images=True, markdown_dated_names=True)
    finally:
        timer.stop()
        timer.deleteLater()


def check_help(window, folder, output):
    import sys
    import json
    import zipfile
    from . import __version__

    assert window.smoke
    window.actions['help'].trigger()
    guide = window.help_dialogs['guide']
    dialog = guide
    assert dialog is not None and dialog.isVisible()
    dialog.page.search.setText('전체화면')
    assert 'F11' in dialog.page.browser.toPlainText()
    QTest.qWait(60)
    dialog.grab().save(str(output.with_name(output.stem+'-help.png')))
    window.actions['licenses'].trigger()
    dialog = window.help_dialogs['licenses']
    assert dialog is not guide and guide.isVisible()
    assert 'AGPL' in dialog.page.browser.toPlainText()
    dialog.page.topics.setCurrentRow(1)
    assert 'GNU AFFERO GENERAL PUBLIC LICENSE' in dialog.page.browser.toPlainText()
    QTest.qWait(60)
    dialog.grab().save(str(output.with_name(output.stem+'-licenses.png')))
    window.actions['sources'].trigger()
    dialog = window.help_dialogs['sources']
    if getattr(sys, 'frozen', False):
        assert dialog.sources_available and dialog.source_browser is not None
        from unittest.mock import patch
        browser = dialog.source_browser
        item = browser.tree.findItems('main.py', Qt.MatchFlag.MatchExactly | Qt.MatchFlag.MatchRecursive)[0]
        browser.tree.setCurrentItem(item)
        assert 'from adf.app import main' in browser.preview.toPlainText()
        saved = folder/'source-copy.py'
        with patch('adf.source_widgets.QFileDialog.getSaveFileName', return_value=(str(saved), '')):
            browser.save_button.click()
        assert saved.read_text(encoding='utf-8') == browser.preview.toPlainText()
        with zipfile.ZipFile(dialog.sources_path/dialog.source_files[0]) as archive:
            assert __version__ in archive.read('ADF/adf/__init__.py').decode('utf-8')
        with zipfile.ZipFile(dialog.sources_path/dialog.source_files[1]) as archive:
            assert json.loads(archive.read('build-manifest.json'))['version'] == __version__
    QTest.qWait(60)
    dialog.grab().save(str(output.with_name(output.stem+'-sources.png')))
    QTest.keyClick(dialog, Qt.Key.Key_Escape)
    assert 'sources' not in window.help_dialogs
    assert len(window.help_dialogs) == 2 and guide.page.search.text() == '전체화면'
    for dialog in list(window.help_dialogs.values()):
        dialog.close()
    return dict(offline_help=True, original_license_reader=True, help_independent_windows=True,
                in_app_source_reader=bool(getattr(sys, 'frozen', False)),
                installed_sources_verified=bool(getattr(sys, 'frozen', False)))


def check_fullscreen(window, folder, output):
    assert window.smoke
    source = folder/'fullscreen-reader.pdf'
    with pymupdf.open() as doc:
        for index in range(4):
            page = doc.new_page()
            page.insert_text((50, 80), f'Fullscreen reader - page {index+1}', fontsize=24)
            page.insert_text((50, 130), 'Move to the top, left or bottom edge to reveal controls.', fontsize=13)
        doc.save(source)
    original = source.read_bytes()
    assert window.open_path(source)
    window.set_view_mode('single')
    window.view.goto(1)
    window.actions['fullscreen'].trigger()
    QTest.qWait(80)
    view, full = window.view, window.fullscreen
    center = view.viewport().rect().center()
    QTest.mouseMove(view.viewport(), center)
    assert window.isFullScreen() and full.active
    assert view.viewport().size() == window.size()
    assert not any(widget.isVisible() for widget in
                   (window.toolbar, window.sidebar, window.page_nav, window.menuBar(), window.statusBar()))
    window.grab().save(str(output.with_name(output.stem+'-fullscreen.png')))
    size, scale = view.size(), view.transform().m11()
    edges = {'top': QPoint(view.width()//2, 1), 'left': QPoint(1, view.height()//2),
             'bottom': QPoint(view.width()//2, view.height()-2)}
    for edge, position in edges.items():
        QTest.mouseMove(view.viewport(), position)
        QTest.qWait(250)
        assert full.panels[edge].isVisible()
        assert view.size() == size and view.transform().m11() == scale
        window.grab().save(str(output.with_name(output.stem+f'-fullscreen-{edge}.png')))
        QTest.mouseMove(view.viewport(), center)
        QTest.qWait(550)
        assert full.panels[edge].isHidden()
    QTest.keyClick(view.viewport(), Qt.Key.Key_PageDown)
    assert window.current == 2
    QTest.keyClick(view.viewport(), Qt.Key.Key_Escape)
    assert not full.active and not window.isFullScreen()
    assert window.current == 2 and window.toolbar.isVisible() and window.sidebar.isVisible()
    assert source.read_bytes() == original
    window.close_document()
    return dict(fullscreen_document_only=True, fullscreen_edge_slides=True,
                fullscreen_escape_restore=True, fullscreen_preserves_page=True)


def check_printing_and_extraction(window, folder, output):
    from unittest.mock import patch
    from PySide6.QtGui import QPageSize
    from PySide6.QtPrintSupport import QPrintDialog, QPrinter
    from PySide6.QtWidgets import QDialog
    from .dialogs import PrintOptionsDialog, SplitDialog

    assert window.smoke
    source = folder/'feedback-source.pdf'
    with pymupdf.open() as doc:
        for i in range(6):
            page = doc.new_page(width=842, height=1191)
            page.insert_text((60, 90), f'Feedback page {i + 1}', fontsize=24)
        doc.save(source)
    original = source.read_bytes()
    assert window.open_path(source)
    assert window.actions['select_tool'].isChecked()
    assert window.view.dragMode() == window.view.DragMode.NoDrag
    window.set_view_mode('single')
    QTest.qWait(30)
    view = window.view
    start = view.mapFromScene(view.pages[0].mapToScene(QPointF(50, 60)))
    finish = view.mapFromScene(view.pages[0].mapToScene(QPointF(350, 100)))
    text_point = view.mapFromScene(view.pages[0].mapToScene(QPointF(70, 80)))
    QTest.mouseMove(view.viewport(), text_point)
    assert view.viewport().cursor().shape() == Qt.CursorShape.IBeamCursor
    QTest.mouseMove(view.viewport(), start)
    assert view.viewport().cursor().shape() == Qt.CursorShape.ArrowCursor
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(view.viewport(), finish)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=finish)
    assert 'Feedback page 1' in window.clip_text
    window.escape()
    for mode, expected in (('range', 4), ('current', 1)):
        printed = folder/f'feedback-{mode}.pdf'
        window.view.goto(2)

        def select(dialog):
            dialog.target.setCurrentIndex(dialog.target.findData(mode))
            dialog.ranges.setText('1,3,5-6')
            dialog.accept()
            return dialog.result()

        def printer(dialog):
            device = dialog.printer()
            device.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
            device.setOutputFileName(str(printed))
            device.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
            return QDialog.DialogCode.Accepted

        with patch.object(PrintOptionsDialog, 'exec', select), patch.object(QPrintDialog, 'exec', printer):
            window.print_document()
        with pymupdf.open(printed) as doc:
            assert doc.page_count == expected
            assert all(abs(page.rect.width-595) < 1 and abs(page.rect.height-842) < 1 for page in doc)

    window.resize(2000, 1000)
    for columns in (1, 2, 5, 1):
        width = 248 if columns == 1 else columns*window.thumbnails.CELL_WIDTH + 12
        splitter = window.reader_splitter
        splitter.setSizes([width, splitter.width()-width-splitter.handleWidth()])
        QTest.qWait(60)
        thumbs = window.thumbnails
        assert thumbs.columns == columns
        y = thumbs.visualItemRect(thumbs.item(0)).y()
        assert sum(thumbs.visualItemRect(thumbs.item(i)).y() == y for i in range(6)) == columns
        assert abs(thumbs.thumbnail_size(0).width()-204) < .01
        if columns == 5:
            window.grab().save(str(output.with_name(output.stem+'-grid.png')))
    window.page_spin.blockSignals(True)
    window.page_spin.setMaximum(10000)
    window.page_spin.setValue(10000)
    QApplication.processEvents()
    edit = window.page_spin.lineEdit()
    assert edit.width()-4 >= edit.fontMetrics().horizontalAdvance('10000')
    window.page_spin.blockSignals(False)
    window.refresh_actions()
    window.edit(lambda: window.document.rotate([2], 90))
    window.thumbnails.clearSelection()
    for index in (0, 2, 4):
        window.thumbnails.item(index).setSelected(True)
    extracted = folder/'feedback-extracted.pdf'

    def select_extract(dialog):
        assert dialog._get_groups() == [[0, 2, 4]]
        dialog.accept()
        return dialog.result()

    with patch.object(SplitDialog, 'exec', select_extract), patch.object(window, 'output_path', return_value=str(extracted)):
        window.extract_pages()
        deadline = time.monotonic()+15
        while window.worker and time.monotonic() < deadline:
            QTest.qWait(20)
    assert window.worker is None and extracted.exists()
    with pymupdf.open(extracted) as doc:
        assert [page.get_text().strip() for page in doc] == ['Feedback page 1', 'Feedback page 3', 'Feedback page 5']
        assert doc[1].rotation == 90
    assert source.read_bytes() == original
    window.document.save(folder/'feedback-edited.pdf')
    window.close_document()
    return dict(default_content_selection=True, text_hover_cursor=True, print_disjoint=True, print_current=True, print_a4_fit=True,
                sidebar_fixed_grid=True, page_number_width=True, selected_page_extract=True)


def check_intro(window, folder, output):
    assert window.smoke, 'Release checks must use an isolated test window'
    original_settings = window.settings
    path = str(folder/'intro-settings.ini')
    window.settings = QSettings(path, QSettings.Format.IniFormat)
    try:
        window.show_first_run_intro()
        QApplication.processEvents()
        dialog = window.intro_dialog
        assert dialog is not None and dialog.isVisible()
        assert dialog.start_button.isVisible()
        window.show_intro()
        assert window.intro_dialog is dialog, 'Only one overview window should open'
        dialog.grab().save(str(output.with_name(output.stem+'-intro.png')))
        assert dialog.highlights
        dialog.skip_button.click()
        QApplication.processEvents()
        assert window.intro_dialog is None
        window.settings.sync()
        assert QSettings(path, QSettings.Format.IniFormat).value('intro_seen', False, type=bool)
        window.show_first_run_intro()
        assert window.intro_dialog is None, 'First-run overview must not repeat'
        window.actions['intro'].trigger()
        QApplication.processEvents()
        assert window.intro_dialog is not None and window.intro_dialog.isVisible()
        from .intro_widgets import STEPS
        for index in range(len(STEPS)):
            dialog = window.intro_dialog
            assert dialog.index == index and dialog.highlights
            assert dialog.rect().contains(dialog.panel.geometry())
            dialog.start_button.click()
        assert window.intro_dialog is None
        window.actions['intro'].trigger()
        QTest.keyClick(window.intro_dialog, Qt.Key.Key_Escape)
        assert window.intro_dialog is None
    finally:
        if window.intro_dialog is not None:
            window.intro_dialog.reject()
        window.settings = original_settings
    return dict(first_run_intro=True, intro_seen_persisted=True, intro_help_reopen=True,
                tour_skip_persisted=True, tour_six_steps=True, tour_targets_real_controls=True)


def check_alignment_and_sidebar(window, folder, output):
    assert window.smoke, 'Release checks must use an isolated test window'
    source, saved_path = folder/'alignment-source.pdf', folder/'alignment-saved.pdf'
    image = io.BytesIO()
    Image.new('RGB', (80, 40), (80, 140, 110)).save(image, format='PNG')
    with pymupdf.open() as doc:
        for _ in range(2):
            page = doc.new_page(width=360, height=480)
            page.insert_text((40, 60), 'Editable text', fontsize=12)
            page.insert_image((220, 100, 280, 130), stream=image.getvalue())
            page.draw_rect((80, 200, 160, 260))
        doc.save(source)
    original = source.read_bytes()
    assert window.open_path(source)
    view = window.view
    window.set_view_mode('single')
    view.goto(1)
    view.set_zoom(1)
    view.set_snap_enabled(True)
    window.begin_image(image.getvalue(), width_mm=80*25.4/72, point=QPointF(100, 330))
    placement = view.placement
    placement.setPos(100, 330)
    QApplication.processEvents()

    def point(x, y):
        return view.mapFromScene(view.pages[1].mapToScene(QPointF(x, y)))

    viewport = view.viewport()
    QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=point(140, 350))
    QTest.mouseMove(viewport, point(123, 350))
    assert abs(placement.x()-80) < .01 and (0, 80) in view.snap_guides
    QTest.keyPress(view, Qt.Key.Key_Alt)
    assert abs(placement.x()-83) < 1 and not view.snap_guides
    QTest.keyRelease(view, Qt.Key.Key_Alt)
    assert abs(placement.x()-80) < .01
    QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=point(123, 350))
    assert not view.snap_guides
    QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=point(160, 370))
    QTest.mouseMove(viewport, point(223, 402))
    assert abs(placement.x()+placement.size.width()-220) < .01
    assert abs(placement.size.width()/placement.size.height()-2) < .001
    QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=point(223, 402))
    rect = view.image_rect()[1]

    panel = window.page_sidebar
    panel.set_expanded(True)
    window.reader_splitter.setSizes([320, max(1, window.reader_splitter.width()-320)])
    QApplication.processEvents()
    width = panel.rail.width()
    preview_pos = QPointF(placement.pos())
    QTest.mouseClick(panel.toggle, Qt.MouseButton.LeftButton)
    QApplication.processEvents()
    assert not panel.expanded and panel.rail.width() == 12 and window.sidebar.isHidden()
    assert panel.childAt(panel.toggle.geometry().center()) is panel.toggle
    assert abs(panel.toggle.geometry().center().y()-panel.rect().center().y()) <= 1
    assert window.current == 1 and view.placement is placement and placement.pos() == preview_pos
    window.grab().save(str(output.with_name(output.stem+'-sidebar.png')))
    panel.toggle.setFocus()
    QTest.keyClick(panel.toggle, Qt.Key.Key_Space)
    QApplication.processEvents()
    assert panel.expanded and panel.rail.width() == width
    assert window.current == 1 and window.thumbnails.currentRow() == 1
    assert view.placement is placement and placement.pos() == preview_pos
    assert window.commit_image()
    window.document.save(saved_path)
    with pymupdf.open(saved_path) as saved:
        assert all(abs(a-b) < .01 for a, b in zip(saved[1].get_image_info()[-1]['bbox'], rect))
        assert len(saved[1].get_drawings()) == 1, 'Alignment guides must not be saved in the PDF'
    window.undo()
    assert len(window.document.doc[1].get_image_info()) == 1
    window.redo()
    assert len(window.document.doc[1].get_image_info()) == 2
    assert source.read_bytes() == original
    window.document.save(saved_path)
    window.close_document()
    return dict(object_snap_move=True, object_snap_resize=True, object_snap_alt_bypass=True,
                object_snap_save_undo=True, sidebar_rail_toggle=True, sidebar_keyboard_toggle=True,
                sidebar_width_restore=True, sidebar_preserves_edit=True)


def _dialog_click(button, configure):
    errors = []

    def fill():
        dialog = QApplication.activeModalWidget()
        try:
            assert isinstance(dialog, StampDialog), 'Stamp management popup missing'
            configure(dialog)
            dialog.buttons.button(QDialogButtonBox.StandardButton.Save).click()
        except Exception as error:
            errors.append(error)
            if dialog:
                dialog.reject()

    QTimer.singleShot(0, fill)
    button.click()
    if errors:
        raise errors[0]


def check_stamp_and_compare(window, folder, output):
    assert window.smoke, 'Release checks must use an isolated test window'
    source, saved_path = folder/'stamp-source.pdf', folder/'stamp-saved.pdf'
    with pymupdf.open() as doc:
        for index in range(3):
            doc.new_page(width=300, height=400).insert_text((30, 50), f'Keep page {index + 1}')
        unused = doc.get_new_xref()
        doc.update_object(unused, '<< /Unused 123 >>')
        original = doc.tobytes().replace(b'/Unused', b'(BADXX)')
    source.write_bytes(original)
    png = folder/'registered-stamp.png'
    picture = Image.new('RGBA', (100, 60), (0, 0, 0, 0))
    ImageDraw.Draw(picture).ellipse((10, 10, 90, 50), outline=(195, 35, 45, 255), width=5)
    picture.save(png)
    assert window.open_path(source)
    window.set_view_mode('single')
    window.show_stamps()
    dock = window.stamp_dock

    def register(dialog):
        assert dialog.width.value() == 20
        assert not dialog.buttons.button(QDialogButtonBox.StandardButton.Save).isEnabled()
        assert dialog.load_file(png)
        dialog.name.setText('검증용 도장')

    _dialog_click(dock.register, register)
    assert window.active_stamp_id is None
    stamp = window.stamp_library.list()[0]
    card = dock.cards[stamp.id]
    QTest.mouseClick(card.preview, Qt.MouseButton.LeftButton)
    assert window.active_stamp_id == stamp.id and card.property('selected')
    assert window.view.placement is None and not window.imagebar.isVisible()
    QApplication.processEvents()

    def click_page(x, y):
        view = window.view
        position = view.mapFromScene(view.pages[0].mapToScene(QPointF(x, y)))
        QTest.mouseMove(view.viewport(), position)
        QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=position)
        QApplication.processEvents()

    for x, y in ((100, 120), (150, 200), (180, 280)):
        click_page(x, y)
    assert len(window.document.doc[0].get_image_info()) == 3
    assert window.active_stamp_id == stamp.id
    for entry in window.document.doc[0].get_image_info():
        assert abs(pymupdf.Rect(entry['bbox']).width*25.4/72 - 20) < .01
    window.document.save(saved_path)
    window.undo()
    assert len(window.document.doc[0].get_image_info()) == 2
    window.redo()
    assert len(window.document.doc[0].get_image_info()) == 3
    QTest.mouseMove(card, QPoint(15, 15))
    QTest.qWait(80)
    assert card.edit.isVisible() and card.delete.isVisible()
    window.grab().save(str(output.with_name(output.stem+'-stamps.png')))

    def edit(dialog):
        assert dialog.name.text() == '검증용 도장' and dialog.width.value() == 20
        dialog.name.setText('수정한 도장')
        dialog.width.setValue(30)

    _dialog_click(card.edit, edit)
    updated = window.stamp_library.get(stamp.id)
    assert updated.name == '수정한 도장' and updated.width_mm == 30
    card = dock.cards[stamp.id]
    card.preview.click()
    click_page(100, 340)
    assert len(window.document.doc[0].get_image_info()) == 4
    assert abs(pymupdf.Rect(window.document.doc[0].get_image_info()[-1]['bbox']).width*25.4/72-30) < .01
    card.delete.click()
    assert window.active_stamp_id is None and not window.stamp_library.list()
    assert len(window.document.doc[0].get_image_info()) == 4
    dock.undo_remove()
    dock.cards[stamp.id].preview.click()
    window.activateWindow()
    window.view.setFocus()
    QTest.qWait(50)
    QTest.keyClick(window.view.viewport(), Qt.Key.Key_Escape)
    assert window.active_stamp_id is None
    window.document.save(saved_path)
    with pymupdf.open(saved_path) as saved:
        assert len(saved[0].get_image_info()) == 4
        assert saved.page_count == 3 and 'Keep page 2' in saved[1].get_text()
    assert source.read_bytes() == original
    window.close_document()
    assert not dock.cards[stamp.id].preview.isEnabled() and dock.register.isEnabled()
    dock.hide()

    dialog = CompareDialog(window, str(source))
    try:
        dialog.paths[1].setText(str(saved_path))
        dialog.show()
        dialog.start_comparison()
        deadline = time.monotonic()+25
        while dialog.worker is not None and time.monotonic() < deadline:
            QTest.qWait(20)
        assert dialog.worker is None, 'Packaged PDF comparison timed out'
        assert dialog.result is not None, dialog.status.text()
        assert dialog.result['changed_pages'] == 1
        assert dialog.changes.count() > 0
        dialog.navigate(1)
        dialog.zoom.setCurrentIndex(dialog.zoom.findData(2.))
        QApplication.processEvents()
        assert dialog.views[0].transform().m11() == dialog.views[1].transform().m11()
        dialog.grab().save(str(output.with_name(output.stem+'-compare.png')))
    finally:
        dialog.reject()
        if dialog.worker is not None:
            dialog.cancel_comparison()
            deadline = time.monotonic()+5
            while dialog.worker is not None and time.monotonic() < deadline:
                QTest.qWait(20)
        dialog.deleteLater()
    return dict(stamp_registration_popup=True, stamp_default_20mm=True,
                stamp_continuous_clicks=True, stamp_hover_edit_delete=True,
                stamp_undo_redo_save=True, stamp_malformed_pdf=True,
                stamp_registered_only=True, comparison_worker=True)


def check_semantic_search(window, folder, output):
    """Meaning-based search with the bundled model: a paraphrase finds its page."""
    assert window.smoke, 'Release checks must use an isolated test window'
    source = folder/'semantic-search.pdf'
    paragraphs = ['회의실 예약은 사내 포털에서 신청하고 사용한 뒤에는 정리합니다.',
                  '계약을 해지하려면 30일 전에 서면으로 알려야 하며 위약금이 발생할 수 있습니다.',
                  '분기별 매출 보고서는 매월 마지막 영업일까지 제출합니다.']
    with pymupdf.open() as doc:
        for text in paragraphs:
            doc.new_page(width=420, height=300).insert_textbox(pymupdf.Rect(40, 60, 380, 200), text, fontname='korea', fontsize=11)
        doc.save(source)
    assert window.open_path(source)
    window.show_search()
    assert window.semantic_toggle.isEnabled(), 'Search model missing from the application'
    window.semantic_toggle.setChecked(True)
    window.search_input.setText('계약 해지 조건')
    window.run_semantic_search()
    deadline = time.monotonic()+60
    while (window.semantic_job is not None or window.semantic_timer.isActive()) and time.monotonic() < deadline:
        QTest.qWait(50)
    assert window.search_matches and window.search_matches[0][0] == 1, window.search_count.text()
    assert window.current == 1 and window.search_count.text() == f'1 / {len(window.search_matches)}'
    window.grab().save(str(output.with_name(output.stem+'-semantic-search.png')))
    window.close_search()
    window.semantic_toggle.setChecked(False)
    window.close_document()
    return dict(semantic_search=True, semantic_search_bundled_model=True)


def make_layout_fixture(folder):
    native, scanned = folder/'layout.pdf', folder/'scan.pdf'
    with pymupdf.open() as doc:
        page = doc.new_page(width=595, height=842)
        # OCR expectations assume an installed Korean system font, not MuPDF's fallback.
        font = next((path for path in (Path('C:/Windows/Fonts/malgun.ttf'), Path('/System/Library/Fonts/AppleSDGothicNeo.ttc'))
                     if path.exists()), None)
        if font:
            page.insert_font(fontname='kr', fontfile=str(font))
            korean = 'kr'
        else:
            korean = 'korea'
        def text(x, y, value, size=12):
            page.insert_text((x, y), value, fontname=korean, fontsize=size)
        text(48, 65, '문서 인식 검증 보고서', 24)
        for x, title, lines in [(48, '1. 운영 현황', ['한글 문장을 정확하게 읽습니다.', '왼쪽 단락을 먼저 읽습니다.']),
                                (318, '2. 다음 계획', ['오른쪽 단락이 이어집니다.', '이미지와 그래프를 저장합니다.'])]:
            text(x, 145, title, 17)
            for i, line in enumerate(lines):
                text(x, 185+i*30, line, 11)
        text(48, 310, '3. 분기별 실적', 17)
        for x in [48, 220, 380, 545]:
            page.draw_line((x, 340), (x, 484), color=(.3, .3, .3))
        for y in [340, 376, 412, 448, 484]:
            page.draw_line((48, y), (545, y), color=(.3, .3, .3))
        for row, values in enumerate([['구분', '문서 수', '완료율'], ['1분기', '120', '95%'],
                                      ['2분기', '180', '97%'], ['3분기', '240', '99%']]):
            for x, value in zip([60, 237, 398], values):
                text(x, 364+row*36, value)
        text(48, 555, '그림 1. 처리량 그래프', 13)
        page.draw_line((65, 710), (285, 710), color=(.2, .2, .2))
        for x, h in [(95, 50), (160, 80), (225, 110)]:
            page.draw_rect((x, 710-h, x+33, 710), color=None, fill=(.18, .4, .8))
        text(325, 555, '그림 2. 작업 흐름', 13)
        for y, label in [(605, '문서 열기'), (665, '결과 저장')]:
            page.draw_rect((344, y-25, 522, y+9), color=(.2, .4, .7))
            text(390, y, label)
        page.draw_line((431, 615), (431, 640), color=(.3, .3, .3))
        doc.save(native)
        pixels = page.get_pixmap(matrix=pymupdf.Matrix(3, 3))
        with pymupdf.open() as scan:
            target = scan.new_page(width=595, height=842)
            target.insert_image(target.rect, stream=pixels.tobytes('png'))
            scan.save(scanned)
    return native, scanned

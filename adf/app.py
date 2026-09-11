from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import traceback

import pymupdf
from PySide6.QtCore import Qt, QTimer, QSettings, QStandardPaths, QUrl, QProcess, QByteArray, QBuffer, QIODevice, QEvent, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QDesktopServices, QFont, QFontDatabase, QIcon, QKeySequence, QPixmap, QPainter, QPageSize, QPageLayout
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QToolButton, QButtonGroup, QStackedWidget, QFrame,
    QComboBox, QLineEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QFontComboBox, QFileDialog, QMessageBox, QInputDialog, QMenu,
    QProgressDialog, QDialog, QDialogButtonBox, QFormLayout, QCheckBox)
from PySide6.QtPrintSupport import QPrinter, QPrintDialog

from .document import PdfDocument, PasswordRequired, merge_pdfs, split_pdf, compress_pdf, extract_pdf
from . import __version__
from .theme import apply_theme, icon
from .viewer import PdfView, ThumbnailList, page_pixmap
from .sidebar_widgets import PageSidebar
from .fonts import original_font, installed_font, normal_name


def resource_path(name):
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent.parent)) / name


def sample_pdf(path):
    doc = pymupdf.open()
    blue = (0.14, 0.36, 0.79)
    from .dialogs import resolve_font_file
    # Embed TrueType Korean fonts so the sample stays editable with its own font.
    # Apple SD Gothic Neo is CID-keyed CFF, which fonts.py does not reuse.
    fontfile = resolve_font_file('Malgun Gothic' if sys.platform=='win32' else 'AppleGothic')
    fontname = 'adfkorean' if fontfile else 'korea'
    content = [
        ('문서 작업, 가볍게.', 'ADF 시작 안내', [
            '익숙한 PDF 그대로. 필요한 편집을 한곳에서.',
            '문서를 열고, 페이지를 정리하고, 새 파일로 저장하세요.',
            '모든 작업은 이 컴퓨터 안에서 처리됩니다.']),
        ('페이지를 정리하세요.', '01  페이지 편집', [
            '왼쪽 썸네일을 드래그하면 순서가 바뀝니다.',
            'Ctrl 또는 Shift를 누르면 여러 페이지를 선택할 수 있습니다.',
            '회전, 교체, 삭제, 빈 페이지 추가를 사용해 보세요.',
            'Ctrl+Z로 되돌릴 수 있습니다.']),
        ('한 번에 마무리하세요.', '02  문서 도구', [
            '여러 PDF를 합치거나 원하는 페이지를 따로 저장하세요.',
            '페이지 번호와 도장 이미지를 넣을 수 있습니다.',
            '저용량 저장으로 이미지 품질과 파일 크기를 조절하세요.',
            '다른 이름으로 저장하면 원본은 그대로 남습니다.']),
    ]
    for i, (title, eyebrow, paragraphs) in enumerate(content):
        p = doc.new_page(width=595, height=842)
        if fontfile:
            p.insert_font(fontname=fontname,fontfile=fontfile)
        p.draw_rect(pymupdf.Rect(0,0,595,12), color=None, fill=blue)
        p.insert_text((52,72), 'ADF / WORK WITH DOCUMENTS', fontsize=10, color=blue)
        p.insert_text((52,158), eyebrow, fontname=fontname, fontsize=12, color=blue)
        p.insert_text((52,218), title, fontname=fontname, fontsize=31, color=(.13,.17,.23))
        p.draw_line((52,255),(543,255), color=(.85,.88,.92))
        for j, text in enumerate(paragraphs):
            p.insert_text((52,305+j*36), text, fontname=fontname, fontsize=12, color=(.32,.37,.44))
        p.draw_rect(pymupdf.Rect(52,525,543,670), color=None, fill=(.95,.97,.99))
        p.insert_text((76,563), 'LOCAL. SIMPLE. PDF.', fontsize=17, color=blue)
        p.insert_text((76,603), '로그인 없이, 업로드 없이, 표준 PDF로.', fontname=fontname, fontsize=12, color=(.32,.37,.44))
        p.insert_text((52,790), 'ADF  /  시작 안내', fontname=fontname, fontsize=9, color=(.5,.55,.62))
        p.insert_text((524,790), f'{i+1:02}', fontsize=10, color=blue)
    doc.subset_fonts()
    doc.save(path, garbage=4, deflate=True)
    doc.close()


class DocumentFontCombo(QComboBox):
    """Show the real PDF font name while Qt uses an isolated preview family."""
    currentFontChanged = Signal(QFont)

    def __init__(self):
        super().__init__()
        for family in QFontDatabase.families():
            if not family.startswith('ADFDocument'):
                self.addItem(family, family)
        self.currentIndexChanged.connect(lambda _: self.currentFontChanged.emit(self.currentFont()))

    def currentFont(self):
        font = QFont(self.currentData() or self.currentText())
        font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
        font.setStyleStrategy(QFont.StyleStrategy.NoFontMerging)
        font.setKerning(False)
        return font

    def setCurrentFont(self, font):
        index = self.findData(font.family())
        if index < 0:
            self.addItem(font.family(), font.family())
            index = self.count()-1
        self.setCurrentIndex(index)

    def setDocumentFont(self, source, family):
        self.clearDocumentFonts()
        self.insertItem(0, f'{source.label} ({source.source})', family)
        self.setCurrentIndex(0)

    def clearDocumentFonts(self):
        for index in range(self.count()-1, -1, -1):
            if str(self.itemData(index)).startswith('ADFDocument'):
                self.removeItem(index)


class ParagraphEditor(QTextEdit):
    applyRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptRichText(False)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setTabChangesFocus(True)
        self.setMinimumSize(1, 1)
        self.setStyleSheet('QTextEdit { background: transparent; border: none; padding: 0; }')
        self.document().setDocumentMargin(0)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.applyRequested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class AdaptiveToolbar(QFrame):
    """One toolbar that reflows its tool groups into two rows when needed."""

    def __init__(self, parent=None):
        from PySide6.QtWidgets import QSizePolicy

        super().__init__(parent)
        self.setObjectName('unifiedToolbar')
        self.rows = QVBoxLayout(self)
        self.rows.setContentsMargins(12, 5, 12, 5)
        self.rows.setSpacing(3)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.file_items = []
        self.edit_items = []
        self.tail_items = []
        self.edit_groups = [[]]
        self.dividers = []
        self.separator = QFrame()
        self.separator.setObjectName('toolbarSeparator')
        self.separator.setFrameShape(QFrame.Shape.VLine)
        self._two_rows = None
        self._arrangement = None

    def button(self, action, label, group='edit', icon_only=False, options=None):
        if options is not None:
            from .pen_widgets import ToolOptionsButton
            button = ToolOptionsButton(self)
            button.optionsRequested.connect(options)
            button.options.setAccessibleName(label+' 옵션')
            button.options.setToolTip(label+' 옵션')
        else:
            button = QToolButton(self)
        button.setDefaultAction(action)
        button.setAccessibleName(action.text())
        button.setText(label)
        # QAction changes otherwise restore its longer menu label on the button.
        action.changed.connect(lambda: button.setText(label))
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly if icon_only else Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.setAutoRaise(True)
        self._add(button, group)
        return button

    def widget(self, widget, group='tail'):
        self._add(widget, group)
        return widget

    def _add(self, widget, group):
        getattr(self, group + '_items').append(widget)
        if group == 'edit':
            self.edit_groups[-1].append(widget)

    def section(self):
        """Start the next group of editing tools, set apart by a faint divider."""
        if self.edit_groups[-1]:
            self.edit_groups.append([])

    def reflow(self, width):
        tool_groups = [group for group in self.edit_groups if group]
        while len(self.dividers) < len(tool_groups):
            divider = QFrame(self)
            divider.setObjectName('toolbarDivider')
            divider.setFrameShape(QFrame.Shape.VLine)
            divider.hide()
            self.dividers.append(divider)
        dividers = iter(self.dividers)

        def joined(row):
            widgets = []
            for group in row:
                widgets += ([next(dividers)] if widgets else []) + group
            return widgets

        gap = 12  # a divider with its margins
        items = self.file_items + self.edit_items + self.tail_items
        required = sum(widget.sizeHint().width() for widget in items) + len(items)*3 + len(tool_groups)*gap + 50
        two_rows = width < max(1180, required)
        if two_rows:
            groups = [self.file_items + [None] + self.tail_items]
            row, used = [], 0
            # Wrap only between groups so that related tools stay together.
            for group in tool_groups:
                size = sum(widget.sizeHint().width()+3 for widget in group)
                if row and used+gap+size > width-24:
                    groups.append(joined(row)+[None])
                    row, used = [], 0
                used += size + (gap if row else 0)
                row.append(group)
            groups.append(joined(row)+[None])
        else:
            groups = [self.file_items + [self.separator] + joined(tool_groups) + [None] + self.tail_items]
        arrangement = tuple(tuple(id(w) if w else 0 for w in row) for row in groups)
        if arrangement == self._arrangement:
            return
        self._arrangement = arrangement
        self._two_rows = two_rows
        while self.rows.count():
            row = self.rows.takeAt(0).layout()
            while row.count():
                item = row.takeAt(0)
                if item.widget():
                    item.widget().hide()
            row.deleteLater()
        for widgets in groups:
            row = QHBoxLayout()
            row.setSpacing(3)
            for widget in widgets:
                if widget is None:
                    row.addStretch()
                else:
                    row.addWidget(widget)
                    widget.show()
            self.rows.addLayout(row)
        self.setFixedHeight(46+38*(len(groups)-1))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.reflow(event.size().width())


class MainWindow(QMainWindow):
    def __init__(self, smoke=False):
        super().__init__()
        self.smoke = smoke
        self.document = PdfDocument()
        self.settings = QSettings('ADF', 'ADF') if not smoke else QSettings(str(Path(tempfile.gettempdir())/'adf-smoke-settings.ini'), QSettings.Format.IniFormat)
        self.intro_dialog = None
        self.help_dialogs = {}
        self.intro_timer = QTimer(self)
        self.intro_timer.setSingleShot(True)
        self.intro_timer.timeout.connect(self.show_first_run_intro)
        self.current = 0
        self.clip_text = ''
        self.image_data = None
        self.stamp_dock = None
        self.stamp_library = None
        self.stamp_temp = None
        self.active_stamp_id = None
        self.selected_image = None
        self.text_selection = None
        self.text_selection_dirty = False
        self._updating_text_controls = False
        self.worker = None
        self.worker_temp = None
        self.loading_dialog = None
        self.markdown_progress = None
        self.search_matches = []
        self.search_index = -1
        self.search_page = 0
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.timeout.connect(self.search_step)
        self.setWindowTitle('ADF — 문서 작업, 가볍게')
        self.setWindowIcon(QIcon(str(resource_path('assets/adf.ico'))))
        self.resize(1320, 900)
        self.setMinimumSize(1020, 700)
        self.setAcceptDrops(True)
        self.actions = {}
        self.create_actions()
        self.create_menus()
        self.create_toolbar()
        self.stack = QStackedWidget()
        shell = QWidget()
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)
        shell_layout.addWidget(self.toolbar)
        shell_layout.addWidget(self.stack, 1)
        self.setCentralWidget(shell)
        self.build_home()
        self.build_editor()
        from .fullscreen import FullscreenReader
        self.fullscreen = FullscreenReader(self)
        self.status_label = QLabel('모든 문서는 이 컴퓨터에서 처리됩니다')
        self.status_label.setObjectName('muted')
        self.statusBar().addPermanentWidget(self.status_label)
        self.statusBar().showMessage('준비됨')
        self.refresh_actions()
        if not smoke:
            geometry = self.settings.value('geometry')
            if geometry:
                self.restoreGeometry(geometry)
            self.intro_timer.start(300)

    def action(self, key, label, callback, shortcut=None, glyph=None, checkable=False):
        a = QAction(icon(glyph) if glyph else QIcon(), label, self)
        a.setCheckable(checkable)
        if shortcut:
            a.setShortcut(shortcut)
        a.triggered.connect(callback)
        self.actions[key] = a
        self.addAction(a)
        return a

    def create_actions(self):
        a = self.action
        a('open', '열기', self.open_dialog, QKeySequence.StandardKey.Open, 'open')
        a('save', '저장', self.save, QKeySequence.StandardKey.Save, 'save')
        a('save_as', '다른 이름으로 저장…', self.save_as, 'Ctrl+Shift+S', 'save_as')
        a('close', '문서 닫기', self.close_document, 'Ctrl+W')
        a('undo', '실행 취소', self.undo, QKeySequence.StandardKey.Undo, 'undo')
        a('redo', '다시 실행', self.redo, QKeySequence.StandardKey.Redo, 'redo')
        self.actions['redo'].setShortcuts(QKeySequence.keyBindings(QKeySequence.StandardKey.Redo)+[QKeySequence('Ctrl+Shift+Z')])
        a('merge', '파일을 페이지로 추가…', self.add_pdf, None, 'plus')
        a('combine', 'PDF 병합…', self.standalone_merge, None, 'merge')
        a('split', '분할', self.split, None, 'split')
        a('extract', '선택 페이지 추출…', self.extract_pages, None, 'split')
        a('markdown', 'OCR · Markdown 내보내기…', self.export_markdown, None, 'ocr')
        a('number', '페이지 번호', self.number, None, 'number')
        a('number_remove', '번호 삭제', self.remove_numbers, None, 'number_remove')
        a('compress', '저용량 저장', self.compress, None, 'compress')
        a('image', '이미지 삽입', self.insert_image, None, 'image')
        a('stamps', '도장 보관함', self.show_stamps, None, 'stamp')
        a('compare', 'PDF 두 버전 비교…', self.compare_versions, None, 'compare')
        a('paste', '이미지 붙여넣기', self.paste_image, QKeySequence.StandardKey.Paste)
        a('text', '텍스트 수정', self.toggle_text, None, 'text', True)
        a('select_image', '기존 이미지 선택', self.toggle_image_select, None, 'image', True)
        snap = a('snap', '오브젝트 스냅', self.toggle_snap, None, 'magnet', True)
        snap.setChecked(self.settings.value('object_snap', True, type=bool))
        snap.setToolTip('페이지와 오브젝트의 가장자리·중앙에 정렬합니다. Alt를 누르면 잠시 해제합니다.')
        a('copy', '선택 내용 복사', self.copy_text, QKeySequence.StandardKey.Copy)
        a('copy_region', '선택 영역을 이미지로 복사', self.copy_region, 'Ctrl+Shift+C', 'capture')
        a('rotate', '오른쪽 회전', lambda: self.edit(lambda: self.document.rotate(self.selected_pages(),90)), 'Ctrl+R', 'rotate')
        a('rotate_left', '왼쪽 회전', lambda: self.edit(lambda: self.document.rotate(self.selected_pages(),-90)), 'Ctrl+Shift+R', 'rotate_left')
        a('delete', '삭제', self.delete_selection, 'Delete', 'trash')
        a('blank', '페이지 추가', lambda: self.show_page_add_menu(), None, 'plus')
        a('replace', '페이지 교체', self.replace_pages, None, 'replace')
        a('up', '페이지 앞으로 이동', lambda: self.move_pages(-1), 'Alt+Up')
        a('down', '페이지 뒤로 이동', lambda: self.move_pages(1), 'Alt+Down')
        a('find', '문서 검색', self.show_search, QKeySequence.StandardKey.Find, 'search')
        a('print', '인쇄', self.print_document, QKeySequence.StandardKey.Print, 'print')
        a('fullscreen', '전체 화면', self.toggle_fullscreen, 'F11', 'fullscreen', True)
        a('escape', '선택 취소', self.escape, 'Escape')
        a('next', '다음 페이지', lambda: self.view.navigate(1), 'PgDown')
        a('previous', '이전 페이지', lambda: self.view.navigate(-1), 'PgUp')
        a('settings', '보기 설정…', self.view_settings)
        a('default', '기본 PDF 앱 설정', self.default_app)
        a('about', 'ADF 정보', self.about)
        a('intro', '기능 둘러보기', self.show_intro)
        a('help', '사용 안내', lambda: self.show_help('guide'), 'F1')
        a('licenses', '오픈소스 라이선스', lambda: self.show_help('licenses'))
        a('sources', '소스코드', lambda: self.show_help('sources'))
        self.pointer_actions = QActionGroup(self)
        self.pointer_mode = 'select_tool'
        for key, label in [('select_tool', '선택 도구'), ('hand_tool', '손 도구')]:
            action = a(key, label, lambda checked=False, mode=key: self.change_pointer(mode), checkable=True)
            self.pointer_actions.addAction(action)
        self.actions['select_tool'].setChecked(True)
        self.actions['select_tool'].setToolTip('텍스트를 드래그하거나 이미지를 클릭해서 선택합니다.')
        self.actions['hand_tool'].setToolTip('문서를 끌어서 이동합니다.')
        self.pointer_actions.addAction(a('pen', '펜', self.activate_pen, None, 'pen', checkable=True))
        self.actions['pen'].setToolTip('펜 켜기 / 끄기 · 작은 화살표로 옵션 열기')
        self.pointer_actions.addAction(a('eraser', '지우개', lambda: self.toggle_drawing_tool('eraser'),
                                        None, 'eraser', checkable=True))
        self.actions['eraser'].setToolTip('지우개 켜기 / 끄기 · 작은 화살표로 크기 설정')
        self.pointer_actions.addAction(a('region_tool', '영역 복사', lambda: self.toggle_drawing_tool('region_tool'),
                                        None, 'capture', checkable=True))
        self.actions['region_tool'].setToolTip('드래그한 영역을 그림으로 복사합니다 · 표·그래프·글 포함')

    def create_menus(self):
        groups = [('파일', ['open','save','save_as','close',None,'compare','merge','combine','split','extract','compress','markdown',None,'print','default']),
                  ('편집',['undo','redo',None,'copy','copy_region','region_tool','paste','image','stamps','text','select_image']),
                  ('페이지',['rotate','rotate_left','blank','replace','delete',None,'up','down','number','number_remove']),
                  ('보기',['find','fullscreen',None,'select_tool','hand_tool','pen','eraser',None,'snap','settings']),
                  ('도움말',['help','licenses','sources',None,'intro','about'])]
        for title, keys in groups:
            menu = self.menuBar().addMenu(title)
            for key in keys:
                menu.addAction(self.actions[key]) if key else menu.addSeparator()

    def create_toolbar(self):
        self.toolbar = AdaptiveToolbar(self)
        for key, label, icon_only in [
            ('open','열기',True), ('save','저장',True), ('save_as','다른 이름 저장',True), ('compress','저용량 저장',True),
            ('print','인쇄',True), ('undo','실행 취소',True), ('redo','다시 실행',True), ('find','찾기',True),
            ('compare','비교',True), ('markdown','OCR · MD',True),
        ]:
            self.toolbar.button(self.actions[key], label, 'file', icon_only)
        # Related tools stay together; faint dividers separate the groups.
        for group in [
            [('split','분할'), ('extract','페이지 추출')],
            [('rotate','오른쪽 회전'), ('rotate_left','왼쪽 회전')],
            [('number','페이지 번호'), ('number_remove','번호 삭제')],
            [('image','이미지'), ('text','텍스트 수정'), ('pen','펜'), ('eraser','지우개')],
            [('snap','스냅'), ('region_tool','캡처'), ('stamps','도장 보관함')],
        ]:
            self.toolbar.section()
            for key, label in group:
                options = (lambda tool=key: self.show_pen_options(tool)) if key in ('pen', 'eraser') else None
                button = self.toolbar.button(self.actions[key], label, options=options)
                if options:
                    setattr(self, key+'_button', button)
        self.toolbar.reflow(self.width())

    def build_home(self):
        self.empty_workspace = QWidget()
        self.empty_workspace.setObjectName('emptyWorkspace')
        layout = QVBoxLayout(self.empty_workspace)
        layout.setContentsMargins(32, 32, 32, 32)
        layout.setSpacing(12)
        layout.addStretch()
        mark = QLabel()
        mark.setPixmap(icon('doc', '#a0a9b6').pixmap(42, 42))
        layout.addWidget(mark, 0, Qt.AlignmentFlag.AlignHCenter)
        title = QLabel('열린 문서가 없습니다')
        title.setObjectName('emptyTitle')
        layout.addWidget(title, 0, Qt.AlignmentFlag.AlignHCenter)
        hint = QLabel('PDF 파일을 여기에 놓거나 열어주세요')
        hint.setObjectName('muted')
        layout.addWidget(hint, 0, Qt.AlignmentFlag.AlignHCenter)
        button = QPushButton('PDF 열기')
        button.setIcon(icon('open'))
        button.setMinimumWidth(130)
        button.clicked.connect(self.open_dialog)
        layout.addSpacing(6)
        layout.addWidget(button, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch()
        self.stack.addWidget(self.empty_workspace)

    def build_editor(self):
        editor = QWidget()
        outer = QVBoxLayout(editor)
        outer.setContentsMargins(0,0,0,0)
        outer.setSpacing(0)
        self.docbar = QFrame()
        self.docbar.setObjectName('pagebar')
        row = QHBoxLayout(self.docbar)
        row.setContentsMargins(20,9,18,9)
        self.filename = QLabel()
        self.filename.setStyleSheet('font-weight: 600;')
        row.addWidget(self.filename)
        self.dirty_badge = QLabel('저장된 문서')
        self.dirty_badge.setObjectName('badge')
        row.addWidget(self.dirty_badge)
        row.addStretch()
        previous = QToolButton()
        previous.setIcon(icon('left'))
        previous.setToolTip('이전 페이지 (PgUp)')
        previous.clicked.connect(lambda: self.view.navigate(-1))
        self.page_spin = QSpinBox()
        self.page_spin.setMinimum(1)
        self.page_spin.setMinimumWidth(76)
        self.page_spin.setAccessibleName('현재 페이지')
        self.page_spin.setKeyboardTracking(False)
        self.page_spin.valueChanged.connect(lambda i: self.view.goto(i-1))
        self.nav_total = QLabel()
        following = QToolButton()
        following.setIcon(icon('right'))
        following.setToolTip('다음 페이지 (PgDn)')
        following.clicked.connect(lambda: self.view.navigate(1))
        self.view_buttons = {}
        self.view_button_group = QButtonGroup(self)
        def view_separator():
            separator = QFrame()
            separator.setObjectName('viewGroupSeparator')
            separator.setFixedSize(1, 20)
            row.addWidget(separator)

        for label, mode in [('한 쪽씩 보기','single'),('한 쪽 연속 보기','continuous'),
                            ('두 쪽씩 보기','spread'),('두 쪽 연속 보기','spread_continuous'),('전체 보기','grid')]:
            if mode in ('spread', 'grid'):
                view_separator()
            button = QToolButton()
            button.setIcon(icon(mode))
            button.setToolTip(label)
            button.setAccessibleName(label)
            button.setCheckable(True)
            self.view_button_group.addButton(button)
            button.setChecked(mode == 'continuous')
            button.clicked.connect(lambda checked=False, value=mode: self.set_view_mode(value))
            row.addWidget(button)
            self.view_buttons[mode] = button
        view_separator()
        self.fullscreen_button = QToolButton()
        self.fullscreen_button.setDefaultAction(self.actions['fullscreen'])
        self.fullscreen_button.setAccessibleName('전체 화면')
        self.fullscreen_button.setToolTip('전체 화면 (F11)')
        row.addWidget(self.fullscreen_button)
        view_separator()
        self.direction_buttons = {}
        for label, start_right in [('첫 페이지를 왼쪽에 놓기', False), ('첫 페이지를 오른쪽에 놓기', True)]:
            button = QToolButton()
            name = 'start_right' if start_right else 'start_left'
            button_icon = icon(name)
            muted = icon(name, '#c4cbd5').pixmap(24, 24)
            for state in (QIcon.State.Off, QIcon.State.On):
                button_icon.addPixmap(muted, QIcon.Mode.Disabled, state)
            button.setIcon(button_icon)
            button.setToolTip(label)
            button.setAccessibleName(label)
            button.setCheckable(True)
            button.clicked.connect(lambda checked=False, value=start_right: self.set_start_side(value))
            row.addWidget(button)
            self.direction_buttons[start_right] = button
        self.zoom = QComboBox()
        self.zoom.setEditable(True)
        self.zoom.addItems(['폭 맞춤','페이지 맞춤','50%','75%','100%','125%','150%','200%'])
        self.zoom.setFixedWidth(118)
        self.zoom.setAccessibleName('확대 배율')
        self.zoom.activated.connect(self.zoom_selected)
        self.zoom.lineEdit().returnPressed.connect(self.zoom_selected)
        row.addWidget(self.zoom)
        outer.addWidget(self.docbar)
        self.textbar = QFrame()
        self.textbar.setObjectName('contextToolbar')
        text_layout = QVBoxLayout(self.textbar)
        text_layout.setContentsMargins(16, 7, 16, 9)
        text_layout.setSpacing(6)
        tr = QHBoxLayout()
        tr.setSpacing(7)
        self.text_value = ParagraphEditor(self)
        self.text_value.setAccessibleName('선택한 문단 내용')
        self.text_value.hide()
        self.text_value.textChanged.connect(self.update_text_preview)
        self.text_value.applyRequested.connect(self.apply_text)
        self.edit_font = None
        self.font_choices = {}
        self.edit_font_id = -1
        self.edit_font_family = None
        self.font_check_timer = QTimer(self)
        self.font_check_timer.setSingleShot(True)
        self.font_check_timer.timeout.connect(self.check_text_font)
        self.text_font = DocumentFontCombo()
        self.text_font.setAccessibleName('글꼴')
        self.text_font.setMaximumWidth(205)
        self.text_font.currentFontChanged.connect(self.update_text_preview)
        tr.addWidget(self.text_font)
        self.text_size = QDoubleSpinBox()
        self.text_size.setRange(4, 144)
        self.text_size.setDecimals(1)
        self.text_size.setSuffix(' pt')
        self.text_size.setValue(11)
        self.text_size.setFixedWidth(105)
        self.text_size.setAccessibleName('글자 크기')
        self.text_size.valueChanged.connect(self.update_text_preview)
        tr.addWidget(self.text_size)
        from .dialogs import ColorButton
        self.text_color = ColorButton(QColor('#222222'))
        self.text_color.setToolTip('글자 색')
        self.text_color.changed.connect(self.update_text_preview)
        tr.addWidget(self.text_color)
        tr.addStretch()
        self.text_apply = QPushButton('완료')
        self.text_apply.clicked.connect(self.finish_text_selection)
        tr.addWidget(self.text_apply)
        text_layout.addLayout(tr)
        outer.addWidget(self.textbar)
        self.textbar.hide()
        self.set_text_controls_enabled(False)
        self.searchbar = QWidget()
        sr = QHBoxLayout(self.searchbar)
        sr.setContentsMargins(20,7,20,7)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText('문서에서 찾을 텍스트')
        self.search_input.setMaximumWidth(340)
        self.search_input.textChanged.connect(self.start_search)
        self.search_input.returnPressed.connect(self.next_match)
        sr.addWidget(self.search_input)
        self.search_count = QLabel()
        sr.addWidget(self.search_count)
        for text, callback in [('이전',lambda: self.next_match(-1)),('다음',self.next_match),('닫기',lambda: self.searchbar.hide())]:
            b = QPushButton(text)
            b.clicked.connect(callback)
            sr.addWidget(b)
        sr.addStretch()
        outer.addWidget(self.searchbar)
        self.searchbar.hide()
        self.imagebar = QWidget()
        ir = QHBoxLayout(self.imagebar)
        ir.setContentsMargins(20,7,20,7)
        ir.addWidget(QLabel('끌어서 이동 · 오른쪽 아래 모서리로 크기 조절 · Alt로 스냅 잠시 해제'))
        ir.addStretch()
        for text, callback in [('취소',self.cancel_image),('이미지 적용',self.commit_image)]:
            b = QPushButton(text)
            b.clicked.connect(callback)
            ir.addWidget(b)
        outer.addWidget(self.imagebar)
        self.imagebar.hide()
        sidebar = QFrame()
        self.sidebar = sidebar
        sidebar.setObjectName('sidebar')
        sidebar.setMinimumWidth(248)
        sl = QVBoxLayout(sidebar)
        sl.setContentsMargins(0,14,0,8)
        sh = QHBoxLayout()
        sh.setContentsMargins(17,0,17,0)
        sh.addWidget(QLabel('페이지'))
        sh.addStretch()
        self.page_total = QLabel()
        self.page_total.setObjectName('muted')
        sh.addWidget(self.page_total)
        sl.addLayout(sh)
        self.thumbnails = ThumbnailList()
        self.thumbnails.itemClicked.connect(lambda item: self.view.goto(item.data(Qt.ItemDataRole.UserRole)))
        self.thumbnails.itemClicked.connect(lambda item: self.clear_content_selection())
        self.thumbnails.itemSelectionChanged.connect(self.refresh_selection_status)
        self.thumbnails.reordered.connect(lambda order: self.edit(lambda: self.document.reorder(order)))
        self.thumbnails.filesInserted.connect(lambda paths, index: self.add_pdf(paths=paths, insert_index=index))
        self.thumbnails.insertionRequested.connect(self.show_page_add_menu)
        self.thumbnails.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.thumbnails.customContextMenuRequested.connect(self.page_context_menu)
        self.thumbnails.renderError.connect(lambda e: self.statusBar().showMessage('미리보기를 만들 수 없습니다: '+e,10000))
        sl.addWidget(self.thumbnails)
        side_tools = QHBoxLayout()
        side_tools.setContentsMargins(12,0,12,0)
        side_tools.setSpacing(6)
        side_tools.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.side_buttons = {}
        for key in ['rotate_left','rotate','blank','replace','delete']:
            b = QToolButton()
            b.setDefaultAction(self.actions[key])
            b.setFixedSize(36, 34)
            b.setStyleSheet('QToolButton { padding: 5px; }')
            side_tools.addWidget(b)
            self.side_buttons[key] = b
        sl.addLayout(side_tools)
        self.view = PdfView()
        self.view.set_snap_enabled(self.actions['snap'].isChecked())
        # Old rtl/cover preferences described a different layout; do not carry
        # their reversed order or centered cover into the new starting-side UI.
        self.view.start_right = self.settings.value('spread_start_right',False,type=bool)
        self.sync_view_buttons()
        self.view.pageChanged.connect(self.page_changed)
        self.view.zoomChanged.connect(self.zoom_changed)
        self.view.textRequested.connect(self.edit_text)
        self.view.textFinished.connect(self.finish_text_selection)
        self.view.textGeometryChanged.connect(self.text_geometry_changed)
        self.view.textPreviewError.connect(lambda e: self.statusBar().showMessage('텍스트 미리보기: ' + e, 6000))
        self.view.imageSelected.connect(self.select_image)
        self.view.filesDropped.connect(self.drop_files)
        self.view.selectedText.connect(self.set_selected_text)
        self.view.selectionCleared.connect(self.clear_content_selection)
        self.view.renderError.connect(lambda e: self.statusBar().showMessage('페이지를 표시할 수 없습니다: '+e,10000))
        self.view.pen.strokeReady.connect(self.apply_ink)
        self.view.pen.eraseReady.connect(self.apply_eraser)
        self.view.pen.failed.connect(self.error)
        self.view.regionCopied.connect(self.copy_region)
        self.view.pen.canceled.connect(lambda: self.change_pointer('select_tool'))
        self.view.inkTransformed.connect(self.transform_ink)
        reader_pane = QWidget()
        reader_pane.setMinimumWidth(320)
        reader_layout = QVBoxLayout(reader_pane)
        reader_layout.setContentsMargins(0,0,0,0)
        reader_layout.setSpacing(0)
        reader_layout.addWidget(self.view,1)
        self.page_sidebar = PageSidebar(sidebar, reader_pane)
        self.reader_splitter = self.page_sidebar.splitter
        self.page_sidebar.expandedChanged.connect(lambda expanded: self.thumbnails.timer.start(0) if expanded else None)
        outer.addWidget(self.page_sidebar,1)
        nav = QWidget()
        self.page_nav = nav
        nr = QHBoxLayout(nav)
        nr.setContentsMargins(16,6,16,6)
        nr.addStretch()
        for widget in (previous, self.page_spin, self.nav_total, following):
            nr.addWidget(widget)
        nr.addStretch()
        reader_layout.addWidget(nav)
        self.stack.addWidget(editor)

    def error(self, error):
        # Keep the failing operation's traceback, without PDF contents, so a
        # platform-specific MuPDF failure can be diagnosed after the dialog.
        try:
            log = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)) / 'error.log'
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open('a', encoding='utf-8') as stream:
                from datetime import datetime
                stream.write(f'\n{datetime.now().isoformat()} ADF {__version__}\n')
                stream.write(''.join(traceback.format_exception(error)) if isinstance(error, BaseException) else str(error) + '\n')
        except OSError:
            pass
        QMessageBox.warning(self,'작업을 완료하지 못했습니다',str(error))

    def selected_pages(self):
        return sorted(i.data(Qt.ItemDataRole.UserRole) for i in self.thumbnails.selectedItems()) or [self.current]

    def toggle_snap(self, enabled):
        self.view.set_snap_enabled(enabled)
        self.settings.setValue('object_snap', enabled)
        self.statusBar().showMessage('오브젝트 스냅 켜짐 · Alt를 누르면 잠시 해제됩니다' if enabled else '오브젝트 스냅 꺼짐', 3500)

    def refresh_selection_status(self):
        count = len(self.thumbnails.selectedItems())
        self.statusBar().showMessage(f'{count}개 페이지 선택 · 끌어서 순서를 바꿀 수 있습니다' if count else '페이지를 선택하세요')

    def refresh_actions(self):
        loaded = bool(self.document.page_count)
        self.actions['select_tool'].setEnabled(loaded)
        self.actions['hand_tool'].setEnabled(loaded)
        can_copy = loaded and (getattr(self.document.doc, '_adf_owner_authenticated', False) or
                               self.document.permissions & pymupdf.PDF_PERM_COPY)
        self.actions['region_tool'].setEnabled(bool(can_copy))
        self.actions['copy_region'].setEnabled(bool(can_copy))
        editable = loaded and getattr(self.document,'editable',True)
        self.actions['pen'].setEnabled(editable)
        self.actions['eraser'].setEnabled(editable)
        if not editable:
            self.stop_pen()
        if not editable and self.active_stamp_id:
            self.stop_stamp()
        if self.stamp_dock:
            self.stamp_dock.set_editable(editable)
        for key in ['save','save_as','close','split','extract','compress','markdown','find','print','copy','next','previous','settings','fullscreen']:
            self.actions[key].setEnabled(loaded)
        for key in ['merge','number','number_remove','image','paste','text','select_image','rotate','rotate_left','delete','blank','replace','up','down']:
            self.actions[key].setEnabled(editable)
        self.actions['undo'].setEnabled(loaded and self.document.can_undo)
        self.actions['redo'].setEnabled(loaded and self.document.can_redo)
        if loaded:
            self.filename.setText(Path(self.document.path).name if self.document.path else '새 문서.pdf')
            self.filename.setToolTip(str(self.document.path or ''))
            self.dirty_badge.setText('저장하지 않은 변경' if self.document.dirty else ('저장된 문서' if editable else '읽기 전용'))
            self.setWindowTitle(f'{"● " if self.document.dirty else ""}{self.filename.text()} — ADF')
            self.page_total.setText(f'{self.document.page_count}쪽')
            self.nav_total.setText(f'/ {self.document.page_count}')
            self.page_spin.setMaximum(self.document.page_count)
        else:
            self.setWindowTitle('ADF — 문서 작업, 가볍게')

    def maybe_save(self):
        if not self.resolve_placement():
            return False
        if not self.document.dirty:
            return True
        box = QMessageBox(self)
        box.setWindowTitle('변경한 문서를 저장할까요?')
        box.setText('저장하지 않은 변경 사항이 있습니다.')
        save = box.addButton('저장',QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton('저장하지 않음',QMessageBox.ButtonRole.DestructiveRole)
        box.addButton('취소',QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(save)
        box.exec()
        return self.save() if box.clickedButton()==save else box.clickedButton()==discard

    def resolve_placement(self):
        if getattr(self.view, 'text_placement', None):
            if self.text_selection_dirty and not self.apply_text():
                return False
            self.clear_text_selection()
        if not self.view.placement:
            return True
        answer = QMessageBox.question(self,'삽입 중인 이미지','배치 중인 이미지를 문서에 적용할까요?',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No|QMessageBox.StandardButton.Cancel)
        if answer == QMessageBox.StandardButton.Cancel:
            return False
        if answer == QMessageBox.StandardButton.Yes:
            return self.commit_image()
        self.cancel_image()
        return True

    def open_dialog(self):
        path,_ = QFileDialog.getOpenFileName(self,'PDF 열기',str(Path.home()),'PDF 문서 (*.pdf)')
        return self.open_path(path) if path else False

    def open_path(self,path):
        if self.worker or self.loading_dialog is not None or not self.maybe_save():
            return False
        from .loading import load_pdf
        candidate = None
        password = None
        try:
            while True:
                try:
                    candidate = load_pdf(path, password, self)
                    if candidate is None:
                        self.statusBar().showMessage('문서 열기를 취소했습니다', 5000)
                        return False
                    break
                except PasswordRequired:
                    password,ok = QInputDialog.getText(self,'암호로 보호된 PDF','문서 열람 암호',QLineEdit.EchoMode.Password)
                    if not ok:
                        return False
            self.search_timer.stop()
            self.stop_stamp()
            self.document.close()
            self.document = candidate
            self.font_choices.clear()
            self.current = 0
            self.search_input.clear()
            self.clip_text = ''
            self.selected_image = None
            self.text_selection = None
            self.text_selection_dirty = False
            self.refresh_document()
            self.stack.setCurrentIndex(1)
            self.view.apply_fit()
            self.view.setFocus(Qt.FocusReason.OtherFocusReason)
            if not self.smoke:
                recent = self.settings.value('recent',[],type=list)
                absolute = str(Path(path).resolve())
                self.settings.setValue('recent',([absolute]+[p for p in recent if p!=absolute])[:8])
            self.statusBar().showMessage('문서를 열었습니다 · Ctrl+S 저장 · Ctrl+Shift+S 다른 이름 저장',6000)
            return True
        except Exception as e:
            if candidate is not None:
                candidate.close()
            self.error(e)
            return False

    def refresh_document(self):
        self.search_timer.stop()
        self.current = min(self.current,max(0,self.document.page_count-1))
        self.view.load(self.document,self.current)
        self.release_edit_font()
        self.thumbnails.load(self.document,self.current)
        self.selected_image = None
        self.text_selection = None
        self.text_selection_dirty = False
        self.textbar.hide()
        if hasattr(self, 'text_value'):
            self._updating_text_controls = True
            self.text_value.clear()
            self._updating_text_controls = False
            self.set_text_controls_enabled(False)
        self.clip_text = ''
        self.refresh_actions()
        if self.search_input.text():
            self.start_search()

    def edit(self,callback):
        if self.worker or not self.document.page_count or not self.resolve_placement():
            return False
        try:
            callback()
            self.refresh_document()
            self.statusBar().showMessage('변경했습니다 · Ctrl+Z로 실행 취소할 수 있습니다',4000)
            return True
        except Exception as e:
            self.error(e)
            return False

    def undo(self):
        self.edit(self.document.undo)

    def redo(self):
        self.edit(self.document.redo)

    def output_path(self,suffix):
        source = Path(self.document.path) if self.document.path else Path.home()/'문서.pdf'
        path,_ = QFileDialog.getSaveFileName(self,'PDF로 저장',str(source.with_name(source.stem+suffix+'.pdf')),'PDF 문서 (*.pdf)')
        if path and not path.lower().endswith('.pdf'):
            path += '.pdf'
        return path

    def save(self):
        if self.worker or not self.document.page_count or not self.resolve_placement():
            return False
        if not self.document.path:
            return self.save_as()
        try:
            self.document.save(self.document.path)
            self.refresh_actions()
            self.statusBar().showMessage(f'저장 완료 · {self.document.path}', 6000)
            return True
        except Exception as error:
            self.error(error)
            return False

    def save_as(self):
        if not self.document.page_count or not self.resolve_placement():
            return False
        path = self.output_path('_편집')
        if not path:
            return False
        if self.document.path and Path(path).resolve()==Path(self.document.path).resolve():
            if QMessageBox.question(self,'원본 덮어쓰기','원본 PDF를 변경된 내용으로 덮어쓸까요?',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)!=QMessageBox.StandardButton.Yes:
                return False
        try:
            self.document.save(path)
            self.refresh_actions()
            self.statusBar().showMessage(f'저장 완료 · {path}',10000)
            return True
        except Exception as e:
            self.error(e)
            return False

    def close_document(self):
        if self.worker or not self.maybe_save():
            return
        self.fullscreen.exit()
        self.stop_stamp()
        self.search_timer.stop()
        self.search_input.clear()
        self.search_matches = []
        self.searchbar.hide()
        self.clip_text = ''
        self.selected_image = None
        self.document.close()
        self.view.text_mode = False
        self.font_choices.clear()
        self.actions['text'].setChecked(False)
        self.textbar.hide()
        self.clear_text_selection()
        self.view.load(self.document)
        self.thumbnails.load(self.document)
        self.stack.setCurrentIndex(0)
        self.refresh_actions()

        self.statusBar().showMessage('PDF 파일을 열어주세요')

    def page_changed(self,index):
        if index != self.current and self.view.selection_start is None:
            self.clear_content_selection()
        self.current = index
        self.page_spin.blockSignals(True)
        self.page_spin.setValue(index+1)
        self.page_spin.blockSignals(False)
        if self.thumbnails.count() > index and len(self.thumbnails.selectedItems()) <= 1:
            self.thumbnails.blockSignals(True)
            self.thumbnails.setCurrentRow(index)
            self.thumbnails.blockSignals(False)

    def sync_view_buttons(self):
        for mode, button in self.view_buttons.items():
            button.setChecked(self.view.mode == mode)
        for start_right, button in self.direction_buttons.items():
            button.setChecked(self.view.start_right == start_right)
            button.setEnabled(self.view.is_spread)

    def set_view_mode(self, mode):
        if self.resolve_placement():
            self.view.set_mode(mode)
        self.sync_view_buttons()

    def set_start_side(self, start_right):
        if self.view.is_spread and self.resolve_placement():
            self.view.start_right = start_right
            self.settings.setValue('spread_start_right', start_right)
            self.view.set_mode(self.view.mode)
        self.sync_view_buttons()

    def zoom_changed(self,zoom):
        self.zoom.blockSignals(True)
        self.zoom.setEditText(f'{round(zoom*100)}%')
        self.zoom.blockSignals(False)

    def zoom_selected(self,*args):
        text = self.zoom.currentText()
        if text in ('폭 맞춤','페이지 맞춤'):
            self.view.fit('width' if text=='폭 맞춤' else 'page')
        else:
            try:
                self.view.set_zoom(float(text.replace('%','').strip())/100)
            except ValueError:
                self.zoom_changed(self.view.transform().m11())

    def page_context_menu(self,pos):
        menu = QMenu(self)
        for key in ['rotate','rotate_left','blank','replace','delete','up','down','extract','split']:
            menu.addAction(self.actions[key])
        menu.exec(self.thumbnails.mapToGlobal(pos))

    def delete_selection(self):
        if self.view.ink_selection is not None:
            selection = self.view.ink_selection
            index, xref = selection.index, selection.xref
            self.edit(lambda: self.document.delete_ink(index, xref))
            return
        if isinstance(QApplication.focusWidget(),QLineEdit):
            return
        if self.view.placement:
            self.cancel_image()
        elif self.selected_image:
            page,xref = self.selected_image
            self.edit(lambda: self.document.remove_image(page,xref))
        elif self.clip_text or self.view.selection_item is not None:
            self.statusBar().showMessage('선택한 글을 수정하려면 상단 텍스트 수정을 사용하세요', 5000)
        elif self.view.hasFocus():
            self.statusBar().showMessage('페이지를 삭제하려면 왼쪽 페이지 목록에서 선택하세요', 5000)
        else:
            self.edit(lambda: self.document.delete(self.selected_pages()))

    def move_pages(self,direction):
        selected = set(self.selected_pages())
        order = list(range(self.document.page_count))
        scan = range(1,len(order)) if direction<0 else range(len(order)-2,-1,-1)
        for i in scan:
            j = i+direction
            if order[i] in selected and order[j] not in selected:
                order[i],order[j] = order[j],order[i]
        self.edit(lambda: self.document.reorder(order))

    def show_page_add_menu(self, index=None, point=None):
        if not self.document.page_count or not self.document.editable:
            return
        index = max(self.selected_pages()) + 1 if index is None else index
        menu = QMenu(self)
        menu.addAction(icon('plus'), '빈 페이지 추가', lambda: self.insert_blank(index=index))
        clipboard = menu.addAction(icon('image'), '클립보드를 페이지로 추가', lambda: self.insert_clipboard_page(index))
        mime = QApplication.clipboard().mimeData()
        clipboard.setEnabled(bool(mime and (mime.hasImage() or mime.hasUrls())))
        menu.addAction(icon('open'), '파일로 추가…', lambda: self.add_pdf(insert_index=index))
        if point is None:
            button = self.side_buttons['blank']
            point = button.mapToGlobal(button.rect().topLeft())
        menu.exec(point)
        menu.deleteLater()

    def insert_clipboard_page(self, index):
        clipboard = QApplication.clipboard()
        mime = clipboard.mimeData()
        if mime and mime.hasUrls():
            paths = [url.toLocalFile() for url in mime.urls() if url.isLocalFile()]
            if paths:
                self.add_pdf(paths=paths, insert_index=index)
                return
        image = clipboard.image()
        if image.isNull():
            self.statusBar().showMessage('복사한 이미지나 PDF · PNG · JPG 파일이 없습니다', 6000)
            return
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, 'PNG')
        buffer.close()
        if self.edit(lambda: self.document.insert_image_page(bytes(data), index)):
            self.view.goto(index)

    def insert_blank(self, checked=False, index=None):
        dialog = QDialog(self)
        dialog.setWindowTitle('빈 페이지 추가')
        layout = QFormLayout(dialog)
        location = QComboBox()
        location.addItems(['선택 페이지 뒤','선택 페이지 앞'])
        size = QComboBox()
        size.addItems(['현재 페이지와 동일','A4','A3','Letter'])
        if index is None:
            layout.addRow('위치',location)
        layout.addRow('크기',size)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)
        if dialog.exec():
            i = self.selected_pages()[0]
            dimensions = [None,(595,842),(842,1191),(612,792)][size.currentIndex()]
            if dimensions is None:
                rect = self.document.doc[i].rect
                dimensions = (rect.width,rect.height)
            target = i+(location.currentIndex()==0) if index is None else index
            if self.edit(lambda: self.document.insert_blank(target,*dimensions)):
                self.view.goto(target)

    def replace_pages(self):
        from .dialogs import PagePickerDialog
        path,_ = QFileDialog.getOpenFileName(self,'가져올 PDF 선택','','PDF 문서 (*.pdf)')
        if not path:
            return
        dialog = PagePickerDialog(path,parent=self)
        if getattr(dialog,'valid',True) and dialog.exec():
            index = self.selected_pages()[0]
            self.edit(lambda: self.document.replace(index,path,dialog.selected_pages,dialog.password))

    def add_pdf(self, checked=False, paths=None, insert_index=None):
        """Add PDF or image pages at one slot; order multiple files before inserting."""
        from .dialogs import MergeDialog, PAGE_FILE_FILTER, _open_pdf
        if not self.document.page_count or not self.resolve_placement():
            return
        if paths is None:
            paths, _ = QFileDialog.getOpenFileNames(self, '파일을 페이지로 추가', '', PAGE_FILE_FILTER)
        if not paths:
            return
        index = max(self.selected_pages()) + 1 if insert_index is None else insert_index
        passwords, ranges = {}, None
        if len(paths) > 1:
            dialog = MergeDialog(parent=self, paths=paths, purpose='pages', current_page=self.current,
                                 document_pages=self.document.page_count, insert_index=index)
            if not dialog.exec():
                return
            paths, passwords, ranges = dialog.paths, dialog.passwords, dialog.selected_ranges
        elif Path(paths[0]).suffix.lower() == '.pdf':
            try:
                source, password = _open_pdf(paths[0], self)
                if source is None:
                    return
                source.close()
                passwords[str(Path(paths[0]).resolve())] = password
            except Exception as exc:
                self.error(exc)
                return
        if self.edit(lambda: self.document.insert_files(paths, index, passwords=passwords, selected_ranges=ranges)):
            self.view.goto(index)

    def standalone_merge(self, checked=False, paths=None):
        from .dialogs import MergeDialog
        dialog = MergeDialog(parent=self,paths=paths)
        if dialog.exec():
            output,_ = QFileDialog.getSaveFileName(self,'병합한 PDF 저장',str(Path.home()/'병합.pdf'),'PDF 문서 (*.pdf)')
            if output:
                if not output.lower().endswith('.pdf'):
                    output += '.pdf'
                if any(Path(output).resolve()==Path(p).resolve() for p in dialog.paths):
                    self.error('병합 결과는 원본과 다른 파일 이름으로 저장해 주세요.')
                    return
                if Path(output).exists():
                    self.error('같은 이름의 파일이 이미 있습니다. 병합 결과는 새 파일 이름으로 저장해 주세요.')
                    return
                self.run_worker({'operation':'merge','paths':dialog.paths,'output':output,'passwords':getattr(dialog,'passwords',{}),'selected_ranges':getattr(dialog,'selected_ranges',None)},'PDF를 병합하고 있습니다')

    # Kept for callers that explicitly request a new merged output, including
    # the home screen and existing automation.
    def merge(self, checked=False, paths=None):
        return self.standalone_merge(checked, paths)

    def split(self):
        from .dialogs import SplitDialog
        if not self.resolve_placement():
            return
        dialog = SplitDialog(self.document,parent=self,selected_pages=self.selected_pages())
        if dialog.exec():
            folder = QFileDialog.getExistingDirectory(self,'분리한 PDF를 저장할 폴더')
            if folder:
                self.run_worker({'operation':'split','groups':dialog.groups,'output_dir':folder,'stem':Path(self.document.path or '문서.pdf').stem,'include_remaining':dialog.include_remaining},'PDF를 분리하고 있습니다',snapshot=True)

    def extract_pages(self):
        from .dialogs import SplitDialog
        if not self.resolve_placement():
            return
        dialog = SplitDialog(self.document, parent=self, selected_pages=self.selected_pages(), extract=True)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        output = self.output_path('_추출')
        if not output:
            return
        if Path(output).exists():
            self.error('같은 이름의 파일이 이미 있습니다. 추출할 PDF는 새 파일 이름으로 저장해 주세요.')
            return
        self.run_worker({'operation': 'extract', 'pages': dialog.groups[0], 'output': output},
                        '선택한 페이지를 저장하고 있습니다', snapshot=True)

    def number(self):
        from .dialogs import NumberingDialog
        if not self.resolve_placement():
            return
        dialog = NumberingDialog(self.document, current_page=self.current, parent=self,
                                 start_right=self.view.start_right, spread=self.view.is_spread)
        if dialog.exec():
            self.edit(lambda: self.document.number_pages(**dialog.options))

    def remove_numbers(self):
        if not self.resolve_placement():
            return
        pages = self.document.numbered_pages()
        if not pages:
            QMessageBox.information(self, '번호 삭제', 'ADF로 넣은 페이지 번호가 없습니다.\n'
                                    '이전 버전이나 다른 프로그램에서 넣은 번호는 지울 수 없습니다.')
            return
        outcome = []
        if self.edit(lambda: outcome.extend(self.document.remove_page_numbers(pages))):
            removed, kept = outcome
            message = f'페이지 번호 {len(removed):,}개를 지웠습니다 · Ctrl+Z로 되돌릴 수 있습니다'
            if kept:
                message += f' · 번호 주변이 바뀐 {len(kept):,}쪽은 그대로 두었습니다'
            self.statusBar().showMessage(message, 8000)

    def compress(self):
        from .dialogs import CompressionDialog
        if not self.resolve_placement():
            return
        dialog = CompressionDialog(self.document,parent=self)
        if dialog.exec():
            output = self.output_path('_저용량')
            if not output:
                return
            if self.document.path and Path(output).resolve()==Path(self.document.path).resolve():
                self.error('저용량 문서는 원본과 다른 이름으로 저장해 주세요.')
                return
            if Path(output).exists():
                self.error('같은 이름의 파일이 이미 있습니다. 저용량 문서는 새 파일 이름으로 저장해 주세요.')
                return
            self.run_worker({'operation':'compress','output':output,'options':dialog.options},'파일 크기를 최적화하고 있습니다',snapshot=True)

    def home_split(self):
        if self.open_dialog():
            self.split()

    def home_compress(self):
        if self.open_dialog():
            self.compress()

    def run_worker(self,task,title,snapshot=False):
        if self.worker:
            return
        try:
            task = dict(task)
            self.worker_temp = tempfile.TemporaryDirectory(prefix='adf-job-')
            folder = Path(self.worker_temp.name)
            _restrict_worker_directory(folder)
            if snapshot:
                original = self.document.doc
                permission = pymupdf.PDF_PERM_COPY if task['operation'] in ('split', 'extract') else pymupdf.PDF_PERM_MODIFY
                if not original or not (getattr(original,'_adf_owner_authenticated',False) or original.permissions & permission):
                    raise PermissionError('이 PDF는 소유자가 편집 또는 추출을 제한했습니다. 소유자 암호로 다시 열어 주세요.')
                snapshot_path = folder/'document.pdf'
                snapshot_path.write_bytes(self.document.tobytes())
                task['source'] = str(snapshot_path)
                task['source_password'] = getattr(original,'_adf_password','')
                task['original_source'] = self.document.path
                task['original_size'] = Path(self.document.path).stat().st_size if self.document.path and Path(self.document.path).is_file() else snapshot_path.stat().st_size
            task['result'] = str(folder/'result.json')
            # Only the parent publishes completed exports. Killing a canceled
            # child therefore cannot leave partial PDFs in the user's folder.
            task['defer_publish'] = True
            request = folder/'task.json'
            request.write_text(json.dumps(task,ensure_ascii=False),encoding='utf-8')
            self.worker_task = task
            self.worker = QProcess(self)
            self.worker.setProgram(sys.executable)
            self.worker.setArguments(([] if getattr(sys,'frozen',False) else [str(resource_path('main.py'))])+['--worker',str(request)])
            self.progress = QProgressDialog(title,'취소',0,0,getattr(self,'export_parent',self))
            self.progress.setWindowTitle('ADF')
            self.progress.setWindowModality(Qt.WindowModality.ApplicationModal)
            self.progress.setMinimumDuration(0)
            self.progress.canceled.connect(self.cancel_worker)
            self.worker.finished.connect(self.worker_finished)
            self.worker.errorOccurred.connect(self.worker_error)
            self.worker.start()
            self.progress.show()
        except Exception as e:
            if self.worker:
                self.worker.deleteLater()
            self.worker = None
            if self.worker_temp:
                self.worker_temp.cleanup()
            self.worker_temp = None
            self.error(e)

    def worker_error(self,error):
        if error == QProcess.ProcessError.FailedToStart:
            self.worker_finished(-1,QProcess.ExitStatus.CrashExit)

    def cancel_worker(self):
        if self.worker and self.worker.processId():
            try:
                _kill_worker_process_tree(self.worker.processId())
            except Exception:
                self.worker.kill()

    def worker_finished(self,exit_code,status):
        if not self.worker:
            return
        task = self.worker_task
        canceled = self.progress.wasCanceled()
        try:
            result_path = Path(task['result'])
            result = json.loads(result_path.read_text(encoding='utf-8')) if result_path.exists() else {'error':'작업이 중단되었습니다.'}
        except Exception:
            result = {'error':'결과를 읽을 수 없습니다.'}
        if not canceled and exit_code == 0 and 'error' not in result and task['operation']=='compress':
            data = result['result']
            message = f'압축 결과를 저장할까요?\n\n{data["original_size"]/1024**2:.2f} MB → {data["output_size"]/1024**2:.2f} MB'
            if task.get('options',{}).get('target_mb') is not None:
                message += '\n목표 용량: ' + ('도달했습니다.' if data.get('target_met') else '도달하지 못했습니다.')
            if data['output_size'] >= data['original_size']:
                message += '\n이 문서는 현재 설정으로 용량이 줄어들지 않았습니다.'
            message += f'\n\n{task["output"]}'
            self.progress.hide()
            answer = QMessageBox.question(self,'저용량 저장 미리보기',message,
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save)
            canceled = answer != QMessageBox.StandardButton.Save
        if not canceled and exit_code == 0 and 'error' not in result:
            self.progress.setLabelText('완성된 PDF를 저장하고 있습니다')
            self.progress.setCancelButton(None)
            try:
                _publish_worker_outputs(result.get('pending_outputs',[]),Path(task['result']).parent)
            except Exception as error:
                result = {'error':str(error)}
        self.progress.close()
        self.progress.deleteLater()
        self.worker.deleteLater()
        self.worker = None
        self.worker_temp.cleanup()
        self.worker_temp = None
        if canceled:
            self.statusBar().showMessage('작업을 취소했습니다 · 저장된 파일이 없습니다',5000)
            return
        if exit_code != 0 or 'error' in result:
            self.error(result.get('error','작업을 완료하지 못했습니다.'))
            return
        if task['operation']=='compress':
            data = result['result']
            original = data['original_size']/1024**2
            output = data['output_size']/1024**2
            message = f'저장 완료\n\n{original:.2f} MB → {output:.2f} MB\n{task["output"]}'
            if data.get('target_met') is False:
                message += '\n\n설정한 목표 용량에 도달하지 못했습니다. 품질이나 DPI를 낮춰 다시 시도할 수 있습니다.'
            QMessageBox.information(self,'저용량 저장 결과',message)
        elif task['operation']=='split':
            QMessageBox.information(self,'분리 완료',f'{len(result["result"])}개 PDF를 저장했습니다.\n{task["output_dir"]}')
        elif task['operation']=='extract':
            self.statusBar().showMessage(f'페이지 추출 완료 · {task["output"]}', 12000)
        else:
            self.statusBar().showMessage(f'병합 완료 · {task["output"]}',12000)
            self.open_path(task['output'])

    def compare_versions(self):
        from .compare_widgets import CompareDialog
        dialog = CompareDialog(self, self.document.path or '')
        dialog.exec()
        dialog.deleteLater()

    def export_markdown(self):
        if self.worker or not self.document.page_count or not self.resolve_placement():
            return
        from .document import _require_permission
        from .markdown_widgets import MarkdownOptionsDialog, export_markdown
        try:
            _require_permission(self.document.doc, pymupdf.PDF_PERM_COPY)
            dialog = MarkdownOptionsDialog(self.document, self.current, self.selected_pages(), self)
            if not dialog.exec():
                dialog.deleteLater()
                return
            options = dialog.options
            dialog.deleteLater()
            result = export_markdown(self, options)
            if result is None:
                self.statusBar().showMessage('Markdown 내보내기를 취소했습니다 · 결과 파일은 저장하지 않았습니다', 8000)
                return
            self.statusBar().showMessage(f'Markdown 내보내기 완료 · {options["output"]}', 15000)
            message = f'{result["pages"]}페이지 · 표 {result["tables"]}개 · 그림 {result["figures"]}개\n\n{options["output"]}'
            if result['warnings']:
                message += f'\n\n확인이 필요한 항목 {len(result["warnings"])}개가 있습니다. conversion.json과 원본을 비교하세요.'
            box = QMessageBox(QMessageBox.Icon.Information, 'Markdown 내보내기 완료', message,
                              QMessageBox.StandardButton.Close, self)
            folder_button = box.addButton('저장 폴더 열기', QMessageBox.ButtonRole.ActionRole)
            box.exec()
            if box.clickedButton() == folder_button:
                QDesktopServices.openUrl(QUrl.fromLocalFile(options['output']))
        except Exception as error:
            self.error(error)

    def show_stamps(self):
        from .stamps import StampLibrary
        from .stamp_widgets import StampDock
        try:
            if self.stamp_dock is None:
                if self.stamp_library is None:
                    if self.smoke:
                        self.stamp_temp = tempfile.TemporaryDirectory(prefix='adf-stamps-test-')
                        folder = Path(self.stamp_temp.name)
                    else:
                        folder = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
                    self.stamp_library = StampLibrary(folder/'stamps.sqlite3')
                self.stamp_dock = StampDock(self.stamp_library, self)
                self.stamp_dock.stampSelected.connect(self.start_stamp)
                self.stamp_dock.stampRemoved.connect(self.stamp_removed)
                self.stamp_dock.stopRequested.connect(self.stop_stamp)
                self.stamp_dock.visibilityChanged.connect(self.stamp_visibility_changed)
                self.view.stampRequested.connect(self.apply_stamp)
                self.view.stampCanceled.connect(self.stop_stamp)
                self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.stamp_dock)
            self.stamp_dock.set_editable(bool(self.document.page_count and self.document.editable))
            self.stamp_dock.show()
            self.stamp_dock.raise_()
        except Exception as error:
            self.error(error)

    def stamp_visibility_changed(self, visible):
        if not visible:
            self.stop_stamp()

    def start_stamp(self, identifier):
        if not self.stamp_library or not self.document.page_count or not self.document.editable:
            return
        if not self.resolve_placement():
            return
        self.stop_pen()
        try:
            stamp = self.stamp_library.get(identifier)
            pm = QPixmap()
            if not pm.loadFromData(stamp.png):
                raise ValueError('등록된 도장 이미지를 읽을 수 없습니다.')
            self.stop_stamp()
            self.view.text_mode = False
            self.view.image_mode = False
            self.actions['text'].setChecked(False)
            self.actions['select_image'].setChecked(False)
            self.selected_image = None
            self.view.clear_region_selection()
            self.view.set_highlights([])
            self.active_stamp_id = identifier
            self.view.start_stamp(pm, stamp.width_mm)
            self.stamp_dock.set_active(identifier)
            self.statusBar().showMessage(f'{stamp.name} · PDF를 클릭해서 찍으세요 · Esc로 종료')
        except Exception as error:
            self.stop_stamp()
            self.error(error)

    def stop_stamp(self):
        if self.active_stamp_id is None and self.view.stamp_pixmap is None:
            return
        self.active_stamp_id = None
        self.view.stop_stamp()
        self.view.setDragMode(PdfView.DragMode.ScrollHandDrag if self.pointer_mode == 'hand_tool' else PdfView.DragMode.NoDrag)
        if self.stamp_dock:
            self.stamp_dock.set_active(None)

    def stamp_removed(self, identifier):
        if identifier == self.active_stamp_id:
            self.stop_stamp()

    def apply_stamp(self, page_index, rect):
        if self.worker or not self.active_stamp_id or not self.document.editable:
            return
        try:
            # Reload by registered ID on every click; deleted/changed library
            # entries cannot leave an unregistered image active in the viewer.
            stamp = self.stamp_library.get(self.active_stamp_id)
            self.document.add_image(page_index, rect, stamp.png, rotate=self.document.doc[page_index].rotation)
            self.view.invalidate_page(page_index)
            self.thumbnails.invalidate_page(page_index)
            self.refresh_actions()
            self.statusBar().showMessage(f'{stamp.name} · 계속 클릭해서 찍을 수 있습니다 · Ctrl+Z 취소 · Esc 종료')
        except Exception as error:
            self.stop_stamp()
            self.error(error)

    def insert_image(self):
        path,_ = QFileDialog.getOpenFileName(self,'이미지 삽입','','이미지 (*.png *.jpg *.jpeg)')
        if path:
            try:
                self.begin_image(Path(path).read_bytes())
            except Exception as e:
                self.error(e)

    def paste_image(self):
        image = QApplication.clipboard().image()
        if image.isNull():
            self.statusBar().showMessage('클립보드에 이미지가 없습니다',5000)
            return
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer,'PNG')
        self.begin_image(bytes(data))

    def begin_image(self,data, width_mm=None, page_index=None, point=None):
        if not self.resolve_placement():
            return
        self.stop_pen()
        self.stop_stamp()
        pm = QPixmap()
        if not pm.loadFromData(data):
            self.error('이 이미지 파일을 읽을 수 없습니다.')
            return
        self.image_data = data
        self.view.place_image(pm, width_mm=width_mm, page_index=page_index, point=point)
        self.imagebar.show()

    def cancel_image(self):
        self.view.cancel_image()
        self.image_data = None
        self.imagebar.hide()

    def commit_image(self):
        if not self.view.placement:
            return True
        index,rect = self.view.image_rect()
        data = self.image_data
        try:
            self.document.add_image(index,rect,data,rotate=self.document.doc[index].rotation)
            self.cancel_image()
            self.refresh_document()
            return True
        except Exception as e:
            self.error(e)
            return False

    def toggle_text(self,checked):
        self.stop_pen()
        self.stop_stamp()
        if not checked and self.text_selection_dirty and not self.apply_text():
            self.actions['text'].setChecked(True)
            return
        self.view.text_mode = checked
        self.view.image_mode = False
        self.actions['select_image'].setChecked(False)
        self.clear_content_selection()
        self.textbar.setVisible(checked and self.text_selection is not None)
        if checked:
            self.view.setDragMode(PdfView.DragMode.NoDrag)
            self.statusBar().clearMessage()
        else:
            self.clear_text_selection()
            self.view.setDragMode(PdfView.DragMode.NoDrag if self.pointer_mode == 'select_tool' else PdfView.DragMode.ScrollHandDrag)
            self.statusBar().showMessage('텍스트 수정 내용은 문서에 반영되었습니다 · 저장할 때 한 번에 기록됩니다')

    def toggle_image_select(self,checked):
        self.stop_pen()
        self.stop_stamp()
        if self.text_selection_dirty and not self.apply_text():
            self.actions['select_image'].setChecked(False)
            return
        self.view.image_mode = checked
        self.view.text_mode = False
        self.actions['text'].setChecked(False)
        self.textbar.hide()
        self.clear_text_selection()
        self.clear_content_selection()
        self.view.setDragMode(PdfView.DragMode.NoDrag if checked or self.pointer_mode == 'select_tool' else PdfView.DragMode.ScrollHandDrag)
        self.statusBar().showMessage('기존 이미지를 클릭한 뒤 Delete를 누르면 삭제됩니다' if checked else '이미지 선택을 해제했습니다')

    def select_image(self,page,xref):
        self.clip_text = ''
        self.selected_image = (page,xref)
        self.statusBar().showMessage('이미지 선택됨 · Ctrl+C로 복사 · Delete로 삭제 · Esc로 선택 해제')

    def edit_text(self,page,span):
        if self.text_selection_dirty and not self.apply_text():
            return
        if span.get('wmode', 0) or tuple(span.get('dir', (1, 0))) != (1, 0):
            self.statusBar().showMessage('세로쓰기나 기울어진 텍스트는 아직 문단 편집을 지원하지 않습니다', 6000)
            return
        self.clear_text_selection()
        source = original_font(self.document.doc[page], span.get('font', ''), span.get('text', ''))
        if source is None or not self.activate_edit_font(source):
            source = installed_font(span.get('font', '').split('+')[-1])
            if source is None or source.missing(span.get('text', '')) or not self.activate_edit_font(source):
                source = self.choose_replacement_font(span.get('font', ''), span.get('text', ''))
                if source is None or not self.activate_edit_font(source):
                    return
        self.text_selection = (page, span)
        color_value = int(span.get('color', 0))
        color = QColor((color_value >> 16) & 255, (color_value >> 8) & 255, color_value & 255)
        self._updating_text_controls = True
        self.text_value.setPlainText(span.get('text', ''))
        self.text_size.setValue(max(4, min(144, float(span.get('size', 11)))))
        self.text_font.setToolTip(f"원본 글꼴: {span.get('font', '')} · {source.source}")
        self.text_color.color = color
        self.text_color._update()
        self._updating_text_controls = False
        self.text_selection_dirty = False
        self.set_text_controls_enabled(True)
        self.textbar.show()
        self.view.select_text(page, span, editor=self.text_value)
        self._updating_text_controls = True
        self.view.style_text_editor(self.text_font.currentFont(), self.text_size.value(), self.text_color.color)
        self._updating_text_controls = False
        if 'click_point' in span:
            from PySide6.QtCore import QPoint
            x, y = span['click_point']
            self.text_value.setTextCursor(self.text_value.cursorForPosition(QPoint(round(x-span['bbox'][0]), round(y-span['bbox'][1]))))
        self.text_value.setFocus()

    def set_text_controls_enabled(self, enabled):
        for widget in (getattr(self, 'text_value', None), getattr(self, 'text_font', None),
                       getattr(self, 'text_size', None), getattr(self, 'text_color', None),
                       getattr(self, 'text_apply', None)):
            if widget is not None:
                widget.setEnabled(enabled)

    def update_text_preview(self, *_):
        if self._updating_text_controls or not self.text_selection or not getattr(self.view, 'text_placement', None):
            return
        self.text_selection_dirty = True
        if not self.ensure_text_font(prompt=False):
            self.font_check_timer.start(0)
            return
        self.view.style_text_editor(self.text_font.currentFont(), self.text_size.value(), self.text_color.color)
        self.view.update_text_preview(self.text_value.toPlainText(), None,
                                      self.text_size.value(), self.text_color.rgb, fontbuffer=self.edit_font.data)

    def release_edit_font(self):
        if not hasattr(self, 'edit_font_id'):
            return
        self.font_check_timer.stop()
        previous = self._updating_text_controls
        self._updating_text_controls = True
        self.text_font.clearDocumentFonts()
        self._updating_text_controls = previous
        if self.edit_font_id >= 0:
            QFontDatabase.removeApplicationFont(self.edit_font_id)
        self.edit_font_id, self.edit_font, self.edit_font_family = -1, None, None

    def activate_edit_font(self, source):
        try:
            font_id = QFontDatabase.addApplicationFontFromData(source.preview_data())
        except Exception:
            return False
        families = QFontDatabase.applicationFontFamilies(font_id)
        if not families:
            if font_id >= 0:
                QFontDatabase.removeApplicationFont(font_id)
            return False
        self.release_edit_font()
        self.edit_font_id, self.edit_font, self.edit_font_family = font_id, source, families[0]
        previous = self._updating_text_controls
        self._updating_text_controls = True
        self.text_font.setDocumentFont(source, families[0])
        self._updating_text_controls = previous
        return True

    def choose_replacement_font(self, original, text, missing=''):
        choice_key = normal_name(original)
        remembered = self.font_choices.get(choice_key)
        if remembered is not None and not remembered.missing(text):
            return remembered
        candidate = None
        for family in ['Malgun Gothic', 'Apple SD Gothic Neo', 'Arial', 'DejaVu Sans']:
            font = installed_font(family)
            if font and not font.missing(text):
                candidate = font
                break
        if candidate is None:
            QMessageBox.information(self, '글꼴 확인', '이 텍스트를 지원하는 글꼴을 찾지 못했습니다. 현재 문단 편집을 취소합니다.')
            return None
        dialog = QMessageBox(self)
        dialog.setWindowTitle('글꼴 확인')
        dialog.setText(f'「{original}」 글꼴로 이 텍스트를 편집할 수 없습니다.')
        reason = f'PDF 글꼴에 없는 글자: {missing[:30]}\n' if missing else '사용 가능한 원본 내장 글꼴이나 같은 PC 글꼴을 찾지 못했습니다.\n'
        dialog.setInformativeText(reason + f'「{candidate.label}」 글꼴로 바꾸어 편집하시겠습니까?')
        remember = QCheckBox('이 문서의 같은 글꼴에 이 선택 적용', dialog)
        remember.setChecked(False)
        check_icon = resource_path('assets/check-white.svg').as_posix()
        remember.setStyleSheet('QCheckBox::indicator { background: white; border: 1px solid #bfc1c6; border-radius: 3px; } '
                              'QCheckBox::indicator:checked { background: #62656b; border-color: #62656b; image: url("' + check_icon + '"); }')
        dialog.setCheckBox(remember)
        replace = dialog.addButton('대체 글꼴로 편집', QMessageBox.ButtonRole.AcceptRole)
        cancel = dialog.addButton('편집 취소', QMessageBox.ButtonRole.RejectRole)
        dialog.setDefaultButton(cancel)
        dialog.setEscapeButton(cancel)
        dialog.exec()
        if dialog.clickedButton() == replace:
            if remember.isChecked():
                self.font_choices[choice_key] = candidate
            return candidate
        return None

    def ensure_text_font(self, prompt=True):
        if not self.text_selection:
            return False
        text = self.text_value.toPlainText()
        chosen = self.text_font.currentFont().family()
        if chosen != self.edit_font_family:
            candidate = installed_font(chosen)
            if candidate and not candidate.missing(text) and self.activate_edit_font(candidate):
                return True
            original = chosen
            missing = candidate.missing(text) if candidate else ''
        else:
            if self.edit_font and not self.edit_font.missing(text):
                return True
            original = self.edit_font.label if self.edit_font else self.text_selection[1].get('font', '')
            missing = self.edit_font.missing(text) if self.edit_font else ''
            candidate = installed_font(original)
            if candidate and not candidate.missing(text) and self.activate_edit_font(candidate):
                return True
        if not prompt:
            return False
        self.font_check_timer.stop()
        replacement = self.choose_replacement_font(original, text, missing)
        if replacement and self.activate_edit_font(replacement):
            return True
        self.clear_text_selection()
        return False

    def check_text_font(self):
        if self.ensure_text_font():
            self.update_text_preview()

    def text_geometry_changed(self):
        if self.text_selection:
            self.text_selection_dirty = True
            self.update_text_preview()

    def clear_text_selection(self):
        self.view.clear_text_selection()
        self.release_edit_font()
        self.text_selection = None
        self.text_selection_dirty = False
        self.textbar.hide()
        if hasattr(self, 'text_value'):
            self._updating_text_controls = True
            self.text_value.clear()
            self._updating_text_controls = False
            self.set_text_controls_enabled(False)

    def finish_text_selection(self):
        if self.text_selection_dirty and not self.apply_text():
            return False
        self.clear_text_selection()
        return True

    def apply_text(self):
        if not self.text_selection or not getattr(self.view, 'text_placement', None):
            return True
        if not self.text_selection_dirty:
            return True
        page, selection = self.text_selection
        source_rect = selection['bbox']
        target_rect = self.view.text_rect()
        if target_rect is None:
            return True
        if not self.ensure_text_font():
            return not self.text_selection
        try:
            self.document.replace_text(page, source_rect, self.text_value.toPlainText(),
                                       font_size=self.text_size.value(), color=self.text_color.rgb,
                                       fontbuffer=self.edit_font.data, fit=True, target_rect=target_rect,
                                       source_rects=selection.get('source_rects'), lineheight=selection.get('lineheight'))
            self.refresh_document()
            self.statusBar().showMessage('텍스트를 문서에 반영했습니다 · 마지막에 저장하면 모든 변경이 함께 기록됩니다', 6000)
            return True
        except Exception as exc:
            self.error(exc)
            return False

    def change_pointer(self, mode):
        if mode in ('pen', 'eraser') and (not self.document.editable or self.worker):
            self.actions[self.pointer_mode].setChecked(True)
            return False
        if mode == 'region_tool' and not self.copy_allowed():
            self.actions[self.pointer_mode].setChecked(True)
            return False
        if not self.resolve_placement():
            self.actions[self.pointer_mode].setChecked(True)
            return False
        if self.text_selection_dirty and not self.apply_text():
            self.actions[self.pointer_mode].setChecked(True)
            return
        self.pointer_mode = mode
        self.actions[mode].setChecked(True)
        if hasattr(self, 'pen_menu'):
            self.pen_menu.hide()
        self.stop_stamp()
        self.view.setDragMode(PdfView.DragMode.ScrollHandDrag if mode == 'hand_tool' else PdfView.DragMode.NoDrag)
        if mode in ('pen', 'eraser'):
            self.view.pen.tool = mode
        self.view.pen.set_enabled(mode in ('pen', 'eraser'))
        self.view.text_mode = False
        self.view.image_mode = False
        self.view.copy_region_mode = mode == 'region_tool'
        self.actions['text'].setChecked(False)
        self.textbar.hide()
        self.clear_text_selection()
        self.clear_content_selection()
        self.actions['select_image'].setChecked(False)
        self.view.update_content_cursor()
        if mode == 'region_tool':
            self.statusBar().showMessage('복사할 영역을 드래그하세요 · 표·그래프·글을 함께 이미지로 복사 · Esc 종료', 6000)
        return True

    def activate_pen(self):
        self.toggle_drawing_tool('pen')

    def toggle_drawing_tool(self, tool):
        self.change_pointer('select_tool' if self.pointer_mode == tool else tool)

    def show_pen_options(self, tool='pen'):
        if (hasattr(self, 'pen_menu') and self.pen_menu.isVisible() and
                self.view.pen.tool == tool):
            self.pen_menu.hide()
            return
        if not self.change_pointer(tool):
            self.actions[self.pointer_mode].setChecked(True)
            return
        from .pen import PenMenu
        if not hasattr(self, 'pen_menu'):
            self.pen_menu = PenMenu(self.view.pen, self)
        self.pen_menu.change_tool(tool)
        button = self.pen_button if tool == 'pen' else self.eraser_button
        self.pen_menu.popup(button.mapToGlobal(button.rect().bottomLeft()))

    def stop_pen(self):
        if hasattr(self, 'pen_menu'):
            self.pen_menu.hide()
        if self.pointer_mode in ('pen', 'eraser', 'region_tool'):
            self.pointer_mode = 'select_tool'
            self.actions['select_tool'].setChecked(True)
            self.view.pen.set_enabled(False)
            self.view.copy_region_mode = False
            self.view.setDragMode(PdfView.DragMode.NoDrag)

    def apply_eraser(self, page_index, sweeps, radius):
        if self.worker or not self.document.editable:
            return
        try:
            if self.document.erase_ink(page_index, sweeps, radius):
                self.view.invalidate_page(page_index)
                self.view.render_visible()
                self.thumbnails.invalidate_page(page_index)
                self.refresh_actions()
                self.statusBar().showMessage('필기를 지웠습니다 · Ctrl+Z 취소 · Ctrl+S 저장', 4000)
        except Exception as error:
            self.error(error)

    def apply_ink(self, page_index, paths, color, width, kind='pencil', pressures=None):
        if self.worker or not self.document.editable:
            return
        try:
            self.document.add_ink(page_index, paths, color, width, kind, pressures)
            self.view.invalidate_page(page_index)
            self.thumbnails.invalidate_page(page_index)
            self.refresh_actions()
            self.statusBar().showMessage('필기 완료 · Esc 후 필기를 클릭하면 객체 조절 · Ctrl+Z 취소 · Ctrl+S 저장', 6000)
        except Exception as error:
            self.error(error)

    def transform_ink(self, index, xref, paths, frame):
        if self.worker or not self.document.editable:
            self.view.clear_ink_selection()
            return
        try:
            updated = self.document.transform_ink(index, xref, paths, frame)
            self.view.clear_ink_selection()
            self.view.invalidate_page(index)
            self.thumbnails.invalidate_page(index)
            self.view.select_ink(index, updated)
            self.refresh_actions()
            self.statusBar().showMessage('필기를 조절했습니다 · Ctrl+Z 취소 · Ctrl+S 저장', 4000)
        except Exception as error:
            self.view.clear_ink_selection()
            self.error(error)

    def clear_content_selection(self):
        self.view.clear_ink_selection()
        if self.selected_image is not None:
            self.view.set_highlights([])
        self.selected_image = None
        self.clip_text = ''
        self.view.clear_region_selection()

    def set_selected_text(self,text):
        if getattr(self.document.doc,'_adf_owner_authenticated',False) or self.document.doc.permissions & pymupdf.PDF_PERM_COPY:
            self.clip_text = text
            self.statusBar().showMessage('Ctrl+C 텍스트 복사 · Ctrl+Shift+C 영역을 이미지로 복사' if text else
                                        'Ctrl+C 또는 Ctrl+Shift+C로 선택 영역을 이미지로 복사',5000)
        else:
            self.clip_text = ''
            self.statusBar().showMessage('이 PDF는 텍스트 복사를 허용하지 않습니다',5000)

    def copy_text(self):
        if not self.copy_allowed():
            return
        if self.view.copy_region_mode and self.view.selected_region():
            self.copy_region()
            return
        if self.selected_image:
            doc = self.document.doc
            if not (getattr(doc, '_adf_owner_authenticated', False) or doc.permissions & pymupdf.PDF_PERM_COPY):
                self.statusBar().showMessage('이 PDF는 이미지 복사를 허용하지 않습니다', 5000)
                return
            try:
                _, xref = self.selected_image
                pm = pymupdf.Pixmap(doc, xref)
                if pm.colorspace and pm.colorspace.n not in (1, 3):
                    pm = pymupdf.Pixmap(pymupdf.csRGB, pm)
                mask = doc.extract_image(xref).get('smask')
                if mask and not pm.alpha:
                    pm = pymupdf.Pixmap(pm, pymupdf.Pixmap(doc, mask))
                picture = QPixmap()
                if not picture.loadFromData(pm.tobytes('png')):
                    raise ValueError('이미지를 복사할 수 없습니다.')
                QApplication.clipboard().setImage(picture.toImage())
                self.notify_copied('이미지가 복사되었습니다')
            except Exception as error:
                self.error(error)
        elif self.clip_text:
            QApplication.clipboard().setText(self.clip_text)
            self.notify_copied('텍스트가 복사되었습니다')
        elif self.view.selected_region():
            self.copy_region()

    def copy_allowed(self):
        doc = self.document.doc
        if doc is None:
            return False
        if not (getattr(doc, '_adf_owner_authenticated', False) or doc.permissions & pymupdf.PDF_PERM_COPY):
            self.statusBar().showMessage('이 PDF는 복사를 허용하지 않습니다', 5000)
            return False
        return True

    def copy_region(self):
        if self.worker or not self.copy_allowed():
            return
        region = self.view.selected_region()
        if region is None:
            self.change_pointer('region_tool')
            return
        try:
            from .viewer import page_raster
            index, bounds = region
            # Render the PDF itself at 216 dpi, excluding selection overlays.
            picture, _ = page_raster(self.document.doc[index], 3, bounds)
            QApplication.clipboard().setImage(picture.toImage())
            self.notify_copied('캡처가 복사되었습니다')
        except Exception as error:
            self.error(error)

    def notify_copied(self, message):
        from .notice_widgets import CopyNotice
        if not hasattr(self, 'copy_notice'):
            self.copy_notice = CopyNotice(self.view.viewport())
        self.copy_notice.announce(message)
        self.statusBar().showMessage(message+' · 클립보드 · Ctrl+V로 붙여넣기', 4000)

    def toggle_fullscreen(self):
        if self.fullscreen.active:
            self.fullscreen.exit()
        else:
            self.fullscreen.enter()

    def changeEvent(self, event):
        super().changeEvent(event)
        if (event.type() == QEvent.Type.WindowStateChange and hasattr(self, 'fullscreen')
                and self.fullscreen.active and not self.isFullScreen()):
            QTimer.singleShot(0, self.fullscreen.exit)

    def escape(self):
        if hasattr(self, 'pen_menu') and self.pen_menu.isVisible():
            self.pen_menu.hide()
        elif self.view.pen.page is not None:
            self.view.pen.cancel()
        elif self.fullscreen.active:
            self.fullscreen.exit()
        elif self.view.pen.enabled:
            self.change_pointer('select_tool')
        elif self.view.copy_region_mode:
            self.change_pointer('select_tool')
        elif self.active_stamp_id:
            self.stop_stamp()
            self.statusBar().showMessage('스탬프 모드를 종료했습니다', 3000)
        elif self.view.placement:
            self.cancel_image()
        elif self.text_selection:
            self.finish_text_selection()
        elif self.isFullScreen():
            self.showNormal()
        else:
            self.searchbar.hide()
            self.clear_content_selection()
            self.view.set_highlights([])

    def show_search(self):
        self.searchbar.show()
        self.fullscreen.reveal('top')
        self.search_input.setFocus()
        self.search_input.selectAll()

    def start_search(self,*args):
        self.search_matches = []
        self.search_index = -1
        self.search_page = 0
        self.search_timer.stop()
        self.view.set_highlights([])
        if self.search_input.text().strip() and self.document.page_count:
            self.search_count.setText('찾는 중…')
            self.search_timer.start(150)
        else:
            self.search_count.clear()

    def search_step(self):
        if self.search_page >= self.document.page_count:
            self.search_count.setText(f'{len(self.search_matches)}개 결과')
            self.view.set_highlights(self.search_matches)
            if self.search_matches:
                self.next_match()
            return
        try:
            query = self.search_input.text().strip()
            self.search_matches.extend((self.search_page,r) for r in self.document.doc[self.search_page].search_for(query))
            self.search_page += 1
            self.search_timer.start(0)
        except Exception as e:
            self.search_count.setText('검색 실패')
            self.statusBar().showMessage(str(e),7000)

    def next_match(self,direction=1):
        if self.search_matches and self.document.page_count:
            direction = direction if type(direction) is int else 1
            self.search_index = (self.search_index+direction)%len(self.search_matches)
            index,rect = self.search_matches[self.search_index]
            self.view.goto(index)
            page = self.view.pages[index]
            r = rect*self.document.doc[index].rotation_matrix
            self.view.ensureVisible(page.mapRectToScene(r.x0,r.y0,r.width,r.height),40,40)
            self.search_count.setText(f'{self.search_index+1} / {len(self.search_matches)}')

    def print_document(self):
        from PySide6.QtCore import QRectF, QSizeF
        from .dialogs import PrintOptionsDialog
        from .printing import print_rect

        if not self.document.page_count:
            return
        if not (getattr(self.document.doc,'_adf_owner_authenticated',False) or self.document.doc.permissions & pymupdf.PDF_PERM_PRINT):
            self.error('이 PDF는 인쇄를 허용하지 않습니다.')
            return
        if not self.resolve_placement():
            return
        options = PrintOptionsDialog(self.document.page_count, self.current, self.selected_pages(), self)
        if options.exec() != QDialog.DialogCode.Accepted:
            return
        pages = options.pages
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setDocName(self.filename.text())
        printer.setFullPage(False)
        dialog = QPrintDialog(printer,self)
        # The range above supports disjoint pages on every platform. This
        # native dialog only configures the device, paper and copy options.
        for option in (QPrintDialog.PrintDialogOption.PrintPageRange,
                       QPrintDialog.PrintDialogOption.PrintSelection,
                       QPrintDialog.PrintDialogOption.PrintCurrentPage):
            dialog.setOption(option, False)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        printer.setFullPage(False)
        if printer.pageOrder() == QPrinter.PageOrder.LastPageFirst:
            pages = list(reversed(pages))
        progress = QProgressDialog('인쇄 데이터를 준비하고 있습니다','취소',0,len(pages),self)
        progress.setWindowModality(Qt.WindowModality.ApplicationModal)
        progress.setMinimumDuration(0)
        painter = QPainter()
        try:
            if not painter.begin(printer):
                raise RuntimeError('프린터를 시작할 수 없습니다.')
            for position, i in enumerate(pages):
                progress.setValue(position)
                QApplication.processEvents()
                if progress.wasCanceled():
                    printer.abort()
                    break
                page = self.document.doc[i]
                if position and not printer.newPage():
                    raise RuntimeError('다음 페이지를 인쇄할 수 없습니다.')
                pm = page_pixmap(page, 200/72)
                # With fullPage=False, the painter origin is already at the
                # printable area's top-left, not the physical paper corner.
                target = QRectF(0, 0, printer.width(), printer.height())
                rect = print_rect(QSizeF(page.rect.width, page.rect.height), target, printer.resolution(),
                                  options.scale.currentData(), options.percent.value())
                painter.setClipRect(target)
                painter.drawPixmap(rect,pm,QRectF(pm.rect()))
        except Exception as e:
            self.error(e)
        finally:
            if painter.isActive():
                painter.end()
            progress.close()

    def view_settings(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('보기 설정')
        layout = QVBoxLayout(dialog)
        start_right = QCheckBox('두 쪽 보기의 첫 페이지를 오른쪽에 놓기')
        start_right.setChecked(self.view.start_right)
        start_right.setEnabled(self.view.is_spread)
        layout.addWidget(start_right)
        clear = QPushButton('최근 문서 기록 지우기')
        clear.clicked.connect(lambda: (self.settings.remove('recent'),clear.setText('기록을 지웠습니다')))
        layout.addWidget(clear)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec():
            self.view.start_right = start_right.isChecked()
            self.settings.setValue('spread_start_right', self.view.start_right)
            self.view.set_mode(self.view.mode)
            self.sync_view_buttons()

    def default_app(self):
        if sys.platform=='win32':
            QDesktopServices.openUrl(QUrl('ms-settings:defaultapps?registeredAppUser=ADF'))
            self.statusBar().showMessage('Windows 설정에서 ADF를 선택하고 .pdf의 기본 앱으로 지정하세요',15000)
        else:
            QMessageBox.information(self,'기본 PDF 앱 설정','Finder에서 PDF를 선택 → 정보 가져오기 → 다음으로 열기에서 ADF 선택 → 모두 변경을 사용하세요.')

    def open_sample(self):
        folder = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
        folder.mkdir(parents=True,exist_ok=True)
        path = folder/'ADF 시작 안내.pdf'
        if not path.exists():
            sample_pdf(path)
        self.open_path(str(path))

    def show_first_run_intro(self):
        if self.settings.value('intro_seen', False, type=bool) or not self.isVisible():
            return
        if QApplication.activeModalWidget() is not None:
            self.intro_timer.start(500)
            return
        self.show_intro(first_run=True)

    def show_intro(self, checked=False, *, first_run=False):
        if self.intro_dialog is not None:
            self.intro_dialog.raise_()
            self.intro_dialog.activateWindow()
            return
        if self.fullscreen.active:
            self.fullscreen.exit()
        if hasattr(self, 'pen_menu'):
            self.pen_menu.hide()
        from .intro_widgets import IntroDialog
        dialog = IntroDialog(self, first_run=first_run)
        self.intro_dialog = dialog
        dialog.helpRequested.connect(self.actions['help'].trigger)
        dialog.finished.connect(self.finish_intro)
        dialog.open()

    def finish_intro(self, result):
        self.settings.setValue('intro_seen', True)
        self.settings.sync()
        self.intro_timer.stop()
        if self.intro_dialog is not None:
            self.intro_dialog.deleteLater()
            self.intro_dialog = None

    def show_help(self, section='guide'):
        if self.intro_dialog is not None:
            self.intro_dialog.accept()
        if section not in self.help_dialogs:
            from .help_widgets import HelpDialog
            dialog = HelpDialog(resource_path(''), section, self)
            dialog.sourcesRequested.connect(lambda: self.show_help('sources'))
            self.help_dialogs[section] = dialog
            dialog.finished.connect(lambda result, key=section: self.finish_help(key))
        dialog = self.help_dialogs[section]
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def finish_help(self, section):
        dialog = self.help_dialogs.pop(section, None)
        if dialog is not None:
            dialog.deleteLater()

    def about(self):
        QMessageBox.information(self,'ADF 정보',f'ADF {__version__}\n문서 작업, 가볍게.\n\n컴퓨터 안에서 동작하는 PDF 편집기입니다.\n표준 PDF 열기 · 편집 · 병합 · 분할 · 인쇄\n\nADF: GNU AGPL v3 이상 · 보증 없이 제공\nPDF 처리: PyMuPDF / MuPDF (AGPL)\n화면 구성: Qt / PySide6 (LGPL 및 구성 요소별 조건)\n\n도움말에서 사용안내·라이선스 원문·소스코드를 확인하세요.')

    def dragEnterEvent(self,event):
        if event.mimeData().hasUrls() and any(u.toLocalFile().lower().endswith('.pdf') for u in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self,event):
        self.drop_files([u.toLocalFile() for u in event.mimeData().urls() if u.toLocalFile().lower().endswith('.pdf')])
        event.acceptProposedAction()

    def drop_files(self,paths):
        if self.document.page_count:
            self.add_pdf(paths=paths)
        elif len(paths)>1:
            self.standalone_merge(paths=paths)
        elif paths:
            self.open_path(paths[0])

    def closeEvent(self,event):
        if self.loading_dialog is not None:
            self.loading_dialog.reject()
            event.ignore()
            return
        if self.worker:
            self.statusBar().showMessage('진행 중인 작업을 완료하거나 취소한 뒤 닫아주세요',5000)
            event.ignore()
            return
        if not self.maybe_save():
            event.ignore()
            return
        self.fullscreen.exit()
        self.stop_stamp()
        if not self.smoke:
            self.settings.setValue('geometry',self.saveGeometry())
        self.intro_timer.stop()
        if self.intro_dialog is not None:
            self.intro_dialog.reject()
        for dialog in list(self.help_dialogs.values()):
            dialog.close()
        self.search_timer.stop()
        self.view.clear_text_selection()
        self.release_edit_font()
        self.view.pen.cancel()
        self.view.clear_ink_selection()
        self.view.render_timer.stop()
        self.thumbnails.timer.stop()
        self.document.close()
        if self.stamp_temp:
            self.stamp_temp.cleanup()
            self.stamp_temp = None
        event.accept()


def _kill_worker_process_tree(process_id):
    if not isinstance(process_id,int) or process_id <= 0:
        raise ValueError('중단할 작업 프로세스를 확인할 수 없습니다.')
    if os.name=='nt':
        from .windows_process import terminate_worker_tree
        terminate_worker_tree(process_id)
    else:
        import signal
        try:
            os.kill(process_id,signal.SIGKILL)
        except ProcessLookupError:
            pass


def _restrict_worker_directory(folder):
    """Keep snapshots and short-lived password requests private to this user."""
    if os.name != 'nt':
        os.chmod(folder,0o700)
        return
    import ctypes
    from ctypes import wintypes
    security = ctypes.WinDLL('advapi32',use_last_error=True)
    security.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR,wintypes.DWORD,ctypes.POINTER(ctypes.c_void_p),ctypes.POINTER(wintypes.DWORD)]
    security.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    security.SetFileSecurityW.argtypes = [wintypes.LPCWSTR,wintypes.DWORD,ctypes.c_void_p]
    security.SetFileSecurityW.restype = wintypes.BOOL
    kernel = ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    descriptor = ctypes.c_void_p()
    # A protected DACL grants inheritable full access only to the owner. This
    # changes this newly created job folder, never the system temp directory.
    if not security.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            'D:P(A;OICI;FA;;;OW)',1,ctypes.byref(descriptor),None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not security.SetFileSecurityW(str(folder),0x80000004,descriptor):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel.LocalFree(descriptor)


def _publish_worker_outputs(outputs,job_folder):
    """Publish a completed batch without overwriting any existing destination."""
    import shutil
    if not outputs:
        raise ValueError('저장할 PDF 결과가 없습니다.')
    root = Path(job_folder).resolve()
    checked = []
    destinations = set()
    for output in outputs:
        source = Path(output['staged']).resolve()
        target = Path(output['destination']).resolve()
        if not source.is_relative_to(root) or not source.is_file():
            raise ValueError('임시 PDF 결과를 확인할 수 없습니다.')
        if target.suffix.lower() != '.pdf' or not target.parent.is_dir():
            raise ValueError('PDF를 저장할 파일 이름과 폴더를 확인해 주세요.')
        if target in destinations or target.exists():
            raise FileExistsError(f'같은 이름의 파일이 이미 있습니다: {target.name}')
        destinations.add(target)
        checked.append((source,target))
    siblings = []
    published = []
    try:
        # Copy all results fully before making even the first final file visible.
        # This also supports output folders on another volume or a network share.
        for source,target in checked:
            fd,path = tempfile.mkstemp(prefix='.adf-',suffix='.pdf',dir=target.parent)
            siblings.append((Path(path),target))
            with os.fdopen(fd,'wb') as destination,source.open('rb') as original:
                shutil.copyfileobj(original,destination,length=1024*1024)
                destination.flush()
                os.fsync(destination.fileno())
        for temporary,target in siblings:
            os.link(temporary,target)
            published.append(target)
        return [str(path) for path in published]
    except BaseException:
        for path in published:
            path.unlink(missing_ok=True)
        raise
    finally:
        for temporary,_ in siblings:
            temporary.unlink(missing_ok=True)


def execute_worker(path):
    request = Path(path).resolve()
    task = json.loads(request.read_text(encoding='utf-8'))
    try:
        operation = task['operation']
        if operation not in {'merge','split','compress','extract'}:
            raise ValueError('지원하지 않는 PDF 작업입니다.')
        if operation in {'merge','compress','extract'}:
            final = Path(task['output']).expanduser().resolve()
            if final.suffix.lower() != '.pdf' or not final.parent.is_dir():
                raise ValueError('PDF를 저장할 파일 이름과 폴더를 확인해 주세요.')
            if final.exists():
                raise FileExistsError(f'같은 이름의 파일이 이미 있습니다: {final.name}')
        else:
            final_folder = Path(task['output_dir']).expanduser().resolve()
            if not final_folder.is_dir():
                raise FileNotFoundError('분리한 PDF를 저장할 폴더가 없습니다.')
        staging = Path(tempfile.mkdtemp(prefix='exports-',dir=request.parent))
        outputs = []
        if operation=='merge':
            staged = staging/'merged.pdf'
            merge_pdfs(task['paths'],staged,passwords=task.get('passwords'),selected_ranges=task.get('selected_ranges'))
            result = str(final)
            outputs.append({'staged':str(staged),'destination':str(final)})
        else:
            # Reauthenticate the encrypted snapshot. The engine therefore sees
            # the original owner/user permission distinction, including PDFs
            # with an empty opening password but restricted editing/copying.
            with PdfDocument() as document:
                document.open(task['source'],password=task.get('source_password'))
                if operation=='split':
                    staged_paths = split_pdf(document,task['groups'],staging,task['stem'],
                        include_remaining=task.get('include_remaining',False))
                    result = []
                    for staged in staged_paths:
                        destination = str(final_folder/Path(staged).name)
                        if Path(destination).exists():
                            raise FileExistsError(f'같은 이름의 파일이 이미 있습니다: {Path(destination).name}')
                        result.append(destination)
                        outputs.append({'staged':staged,'destination':destination})
                elif operation=='extract':
                    staged = staging/'extracted.pdf'
                    extract_pdf(document, task['pages'], staged)
                    result = str(final)
                    outputs.append({'staged':str(staged),'destination':str(final)})
                else:
                    staged = staging/'compressed.pdf'
                    result = compress_pdf(document,staged,**task['options'])
                    result['path'] = str(final)
                    if 'original_size' in task:
                        result['original_size'] = task['original_size']
                    outputs.append({'staged':str(staged),'destination':str(final)})
        payload = {'result':result,'pending_outputs':outputs}
        if not task.get('defer_publish',False):
            _publish_worker_outputs(outputs,request.parent)
            payload.pop('pending_outputs')
        code = 0
    except Exception as error:
        payload = {'error':str(error)}
        code = 1
    result_path = Path(task['result'])
    temporary = result_path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(payload,ensure_ascii=False,default=str),encoding='utf-8')
    os.replace(temporary,result_path)
    return code


class ADFApplication(QApplication):
    """Handle Finder's file-open events as well as command-line PDF paths."""
    window = None
    pending_files = None

    def event(self,event):
        if event.type()==QEvent.Type.FileOpen:
            path = event.file()
            if path:
                if self.window:
                    QTimer.singleShot(0,lambda p=path:self.window.open_path(p))
                else:
                    if self.pending_files is None:
                        self.pending_files = []
                    self.pending_files.append(path)
                return True
        return super().event(event)


def main():
    parser = argparse.ArgumentParser(description='ADF PDF 편집기')
    parser.add_argument('files',nargs='*')
    parser.add_argument('--merge',nargs='*')
    parser.add_argument('--split')
    parser.add_argument('--shell-request')
    parser.add_argument('--tool-smoke-test')
    parser.add_argument('--worker')
    parser.add_argument('--load-worker')
    parser.add_argument('--markdown-worker')
    parser.add_argument('--compare-worker')
    parser.add_argument('--smoke-test')
    parser.add_argument('--help-section', choices=['guide', 'licenses', 'sources'])
    args = parser.parse_args()
    if args.markdown_worker:
        from .markdown_export import markdown_worker
        return markdown_worker(args.markdown_worker)
    if args.load_worker:
        from .loading import loading_worker
        return loading_worker(args.load_worker)
    if args.worker:
        return execute_worker(args.worker)
    if args.compare_worker:
        from .comparison import comparison_worker
        return comparison_worker(args.compare_worker)
    app = ADFApplication(sys.argv[:1])
    app.setApplicationName('ADF')
    app.setOrganizationName('ADF')
    apply_theme(app)
    font = QFont('Malgun Gothic' if sys.platform=='win32' else 'Apple SD Gothic Neo',10)
    app.setFont(font)
    tool_operation = None
    tool_files = []
    if args.shell_request:
        from .shell_request import read_shell_request
        try:
            tool_operation, tool_files = read_shell_request(args.shell_request)
        except Exception as exc:
            if args.tool_smoke_test:
                Path(args.tool_smoke_test).write_text(json.dumps({'ok':False,'error':str(exc)}),encoding='utf-8')
            else:
                QMessageBox.warning(None,'ADF 탐색기 요청',str(exc))
            return 1
    elif args.merge is not None:
        tool_operation, tool_files = 'merge', args.merge+args.files
    elif args.split:
        tool_operation, tool_files = 'split', [args.split]
    if tool_operation:
        from .tool_window import ToolWindowController
        app.setQuitOnLastWindowClosed(False)
        controller = ToolWindowController(tool_operation,tool_files,on_closed=app.quit)
        controller.show_tool()
        if args.tool_smoke_test:
            QTimer.singleShot(100,lambda:controller.smoke_report(args.tool_smoke_test))
        return app.exec()
    window = MainWindow(smoke=bool(args.smoke_test))
    app.window = window
    window.show()
    if args.help_section:
        window.intro_timer.stop()
        QTimer.singleShot(0, lambda: window.show_help(args.help_section))
    def exception_handler(kind,error,tb):
        if args.smoke_test:
            Path(args.smoke_test).write_text(json.dumps({'ok':False,'error':str(error),
                'traceback':''.join(traceback.format_exception(kind,error,tb))}),encoding='utf-8')
            app.exit(1)
            return
        folder = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
        folder.mkdir(parents=True,exist_ok=True)
        (folder/'error.log').write_text(''.join(traceback.format_exception(kind,error,tb)),encoding='utf-8')
        window.error(error)
    sys.excepthook = exception_handler
    if args.smoke_test:
        def smoke():
            output = Path(args.smoke_test)
            try:
                assert window.document.page_count == 0
                assert window.stack.currentWidget() is window.empty_workspace
                assert not window.actions['save'].isEnabled()
                with tempfile.TemporaryDirectory(prefix='adf-smoke-') as folder:
                    path = Path(folder)/'한글 예제.pdf'
                    sample_pdf(path)
                    window.document.open(str(path))
                    window.refresh_document()
                    window.stack.setCurrentIndex(1)
                    app.processEvents()
                    window.view.render_visible()
                    from PySide6.QtTest import QTest
                    QTest.qWait(180)
                    assert window.document.page_count==3
                    assert window.view.pages[0].pixmap is not None
                    window.grab().save(str(output.with_suffix('.png')))
                    result = {'ok':True,'version':__version__,'pages':3,'qt':True,'pdf_render':True,
                              'empty_workspace':True,'frozen':bool(getattr(sys,'frozen',False))}
                    from .text_groups import text_group_at
                    original_group = text_group_at(window.document.doc[0], (60, 302))
                    window.actions['text'].trigger()
                    window.edit_text(0, original_group)
                    assert window.edit_font is not None
                    assert window.edit_font.source == 'PDF 내장'
                    embedded_bytes = window.edit_font.data
                    window.text_value.setPlainText(original_group['text'] + '\n' + original_group['text'])
                    assert window.edit_font.data == embedded_bytes
                    assert window.apply_text()
                    embedded_saved = Path(folder) / 'embedded-saved.pdf'
                    window.document.save(embedded_saved)
                    with pymupdf.open(embedded_saved) as saved:
                        assert ' '.join(saved[0].get_text().split()).count(' '.join(original_group['text'].split())) >= 2
                    window.undo()
                    window.document.save(embedded_saved)
                    window.actions['text'].trigger()
                    result.update(embedded_font_preview=True, embedded_font_edit_save=True)
                    from .dialogs import NumberingDialog
                    numbering = NumberingDialog(window.document, current_page=1, parent=window,
                                                start_right=True, spread=True)
                    numbering.start.setValue(99)
                    numbering.zero_pad.setChecked(True)
                    numbering.affix.setCurrentIndex(1)
                    numbering.mirror.setChecked(True)
                    numbering._update_preview()
                    assert [p['index'] for p in numbering.preview.pages] == [1, 2]
                    assert [p['position'] for p in numbering.preview.pages] == ['bottom-left', 'bottom-right']
                    assert window.edit(lambda: window.document.number_pages(**numbering._options()))
                    assert 'p.099' in window.document.doc[0].get_text()
                    assert 'p.101' in window.document.doc[2].get_text()
                    left = window.document.doc[1].search_for('p.100')[0]
                    assert abs(left.x0 - 12 * 72 / 25.4) < .1
                    assert window.document.numbered_pages() == [0, 1, 2]
                    assert window.edit(lambda: window.document.remove_page_numbers([0, 1, 2]))
                    assert not window.document.numbered_pages() and 'p.099' not in window.document.doc[0].get_text()
                    assert '가볍게' in window.document.doc[0].get_text()
                    window.undo()
                    window.undo()
                    numbering.reject()
                    numbering.deleteLater()
                    result.update(numbering_preview=True, mirrored_numbering=True, automatic_padding=True, number_removal=True)
                    # Exercise paragraph editing in the installed binary too.
                    from .text_groups import text_group_at
                    paragraph_path = Path(folder) / 'paragraph.pdf'
                    with pymupdf.open() as check:
                        check.new_page().insert_text((40, 70), 'First line\nSecond line', fontsize=14, lineheight=1.4)
                        check.save(paragraph_path)
                    original = paragraph_path.read_bytes()
                    assert window.open_path(paragraph_path)
                    window.actions['text'].trigger()
                    assert window.textbar.isHidden()
                    group = text_group_at(window.document.doc[0], (45, 65))
                    assert group['text'] == 'First line\nSecond line'
                    window.edit_text(0, group)
                    assert not window.textbar.isHidden()
                    assert window.view.text_placement.proxy.widget() is window.text_value
                    QTest.keyClick(window.text_value, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
                    QTest.keyClicks(window.text_value, 'Edited first')
                    QTest.keyClick(window.text_value, Qt.Key.Key_Return)
                    QTest.keyClicks(window.text_value, 'Edited second')
                    window.view.render_text_preview()
                    assert window.view.text_preview_document is not None
                    assert not window.document.dirty
                    saved_path = Path(folder) / 'paragraph-saved.pdf'
                    window.output_path = lambda *a, **k: str(saved_path)
                    assert window.save_as()
                    with pymupdf.open(saved_path) as saved:
                        assert 'Edited first Edited second' in ' '.join(saved[0].get_text().split())
                    assert paragraph_path.read_bytes() == original
                    assert window.textbar.isHidden()
                    window.grab().save(str(output.with_name(output.stem + '-inline.png')))
                    window.set_view_mode('single')
                    assert window.view_buttons['single'].isChecked()
                    window.actions['rotate_left'].trigger()
                    assert window.document.doc[0].rotation == 270
                    window.undo()
                    from PIL import Image
                    png_path = Path(folder) / 'page.png'
                    Image.new('RGB', (100, 80), 'blue').save(png_path)
                    window.add_pdf(paths=[str(png_path)], insert_index=0)
                    assert window.document.page_count == 2
                    assert len(window.document.doc[0].get_images()) == 1
                    window.undo()
                    assert window.document.page_count == 1
                    result.update(inline_typing=True, contextual_toolbar=True, image_page_insert=True, left_rotation=True)
                    window.close_document()
                    assert window.stack.currentWidget() is window.empty_workspace
                    result.update(paragraph_edit=True, deferred_save=True, empty_after_close=True)
                    from .release_checks import check_alignment_and_sidebar, check_intro, check_stamp_and_compare, check_printing_and_extraction, check_fullscreen, check_help, check_ocr_and_loading, check_pen_and_save
                    result.update(check_stamp_and_compare(window, Path(folder), output))
                    result.update(check_alignment_and_sidebar(window, Path(folder), output))
                    result.update(check_intro(window, Path(folder), output))
                    result.update(check_help(window, Path(folder), output))
                    result.update(check_printing_and_extraction(window, Path(folder), output))
                    result.update(check_pen_and_save(window, Path(folder), output))
                    result.update(check_fullscreen(window, Path(folder), output))
                    result.update(check_ocr_and_loading(window, Path(folder), output))
                output.write_text(json.dumps(result),encoding='utf-8')
                app.exit(0)
            except Exception as e:
                window.stop_stamp()
                output.write_text(json.dumps({'ok':False,'error':str(e),'traceback':traceback.format_exc()}),encoding='utf-8')
                app.exit(1)
        QTimer.singleShot(200,smoke)
    elif args.files:
        QTimer.singleShot(0,lambda: window.drop_files(args.files))
    elif app.pending_files:
        QTimer.singleShot(0,lambda: window.drop_files(app.pending_files))
    return app.exec()

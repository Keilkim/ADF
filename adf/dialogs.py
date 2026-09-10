"""Korean desktop dialogs for PDF operations.

Each dialog only gathers an operation. The main window owns document changes
and the final save location. PyMuPDF rendering stays on the Qt GUI thread.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import os
import re
import sys

import pymupdf as fitz
from PySide6.QtCore import Qt, QSize, QSignalBlocker, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QColorDialog,
    QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
    QFontComboBox, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView,
    QInputDialog, QLabel, QLineEdit, QListView, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QScrollArea, QSizePolicy, QSpinBox,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)


PDF_FILTER = "PDF 문서 (*.pdf)"


def _description(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setObjectName("description")
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


def _buttons(dialog: QDialog, label: str) -> QDialogButtonBox:
    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
    )
    buttons.button(QDialogButtonBox.StandardButton.Ok).setText(label)
    buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName("primary")
    buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    return buttons


def _spin(low: int, high: int, value: int, suffix: str = "") -> QSpinBox:
    widget = QSpinBox()
    widget.setRange(low, high)
    widget.setValue(value)
    widget.setSuffix(suffix)
    return widget


def _decimal(low: float, high: float, value: float, suffix: str = "") -> QDoubleSpinBox:
    widget = QDoubleSpinBox()
    widget.setRange(low, high)
    widget.setDecimals(1)
    widget.setValue(value)
    widget.setSuffix(suffix)
    return widget


def pdf_pixmap(page, width: int = 140, height: int = 180) -> QPixmap:
    """Copy pixel memory so the Qt image outlives its MuPDF pixmap."""
    scale = min(width / max(1, page.rect.width), height / max(1, page.rect.height))
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=fitz.csRGB, alpha=False)
    image = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
    return QPixmap.fromImage(image)


def _open_pdf(path: str, parent=None):
    doc = fitz.open(path)
    password = None
    try:
        if not doc.is_pdf:
            raise ValueError("PDF 문서만 열 수 있습니다.")
        while doc.needs_pass:
            password, ok = QInputDialog.getText(
                parent, "PDF 암호", f"{Path(path).name}\n열람 암호를 입력하세요.",
                QLineEdit.EchoMode.Password,
            )
            if not ok:
                doc.close()
                return None, None
            if doc.authenticate(password):
                break
            QMessageBox.warning(parent, "암호 확인", "암호가 맞지 않습니다. 다시 입력해 주세요.")
        if not doc.page_count:
            raise ValueError("문서에 페이지가 없습니다.")
        return doc, password
    except Exception:
        doc.close()
        raise


class ThumbnailList(QListWidget):
    """Render visible thumbnails in small, interruptible GUI event batches."""

    def __init__(self, document, parent=None):
        super().__init__(parent)
        self.document = document
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setLayoutMode(QListView.LayoutMode.Batched)
        self.setBatchSize(100)
        self.setIconSize(QSize(116, 150))
        self.setGridSize(QSize(146, 188))
        self.setSpacing(6)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(False)
        self.setUniformItemSizes(True)
        self.setAccessibleName("PDF 페이지 선택")
        self.setMinimumHeight(220)
        self._rendered: set[int] = set()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._render_visible)
        blank = QPixmap(100, 140)
        blank.fill(QColor("#f1f2f5"))
        icon = QIcon(blank)
        for index in range(document.page_count):
            item = QListWidgetItem(icon, f"{index + 1} 페이지")
            item.setData(Qt.ItemDataRole.UserRole, index)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.addItem(item)
        self.verticalScrollBar().valueChanged.connect(self._schedule)
        self.horizontalScrollBar().valueChanged.connect(self._schedule)

    @property
    def selected_pages(self) -> list[int]:
        return sorted(item.data(Qt.ItemDataRole.UserRole) for item in self.selectedItems())

    def _schedule(self, *_):
        if not self._timer.isActive():
            self._timer.start(0)

    def showEvent(self, event):
        super().showEvent(event)
        self._schedule()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._schedule()

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    def _render_visible(self):
        if not self.isVisible() or self.document.is_closed:
            return
        viewport = self.viewport().rect().adjusted(0, -180, 0, 180)
        done = 0
        for index in range(self.count()):
            if index in self._rendered or not viewport.intersects(self.visualItemRect(self.item(index))):
                continue
            try:
                self.item(index).setIcon(QIcon(pdf_pixmap(self.document[index], 116, 150)))
            except Exception:
                self.item(index).setToolTip("이 페이지의 미리보기를 만들 수 없습니다.")
            self._rendered.add(index)
            done += 1
            if done == 3:
                self._timer.start(0)
                break


class FileList(QTreeWidget):
    """Flat file table with real row ordering and familiar list adapters."""

    filesDropped = Signal(list)
    orderChanged = Signal()
    removeRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("mergeFiles")
        self.setColumnCount(5)
        self.setHeaderLabels(["이름", "페이지 범위", "크기", "수정 날짜", "상태"])
        self.setIconSize(QSize(22, 22))
        self.setRootIsDecorated(False)
        self.setItemsExpandable(False)
        self.setIndentation(0)
        self.setUniformRowHeights(True)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDropIndicatorShown(True)
        self.setAcceptDrops(True)
        self.setSortingEnabled(False)
        self.setAllColumnsShowFocus(True)
        self.header().setSectionsMovable(False)
        self.header().setStretchLastSection(False)
        self.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column, width in ((1, 180), (2, 94), (3, 160), (4, 105)):
            self.header().setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
            self.setColumnWidth(column, width)
        self.setStyleSheet("""
            QTreeWidget#mergeFiles { background: white; border: 1px solid #dce1e8;
                border-radius: 8px; outline: none; }
            QTreeWidget#mergeFiles::item { padding: 10px 12px; border-bottom: 1px solid #edf0f4; }
            QTreeWidget#mergeFiles::item:selected { background: #e7e8ea; color: #303238; }
            QTreeWidget#mergeFiles::item:hover:!selected { background: #f5f5f6; }
            QTreeWidget#mergeFiles::item:focus { border-bottom: 1px solid #afb2b8; }
            QTreeWidget#mergeFiles QHeaderView::section { background: #f3f5f8; color: #657083;
                border: none; border-right: 1px solid #e5e9ef; border-bottom: 1px solid #dce1e8;
                padding: 11px 12px; font-weight: 600; }
            QTreeWidget#mergeFiles::drop-indicator { background: #797d84; height: 3px; }
        """)
        self.setAccessibleName("병합할 PDF 파일 순서")

    def count(self):
        return self.topLevelItemCount()

    def item(self, index):
        return self.topLevelItem(index)

    def row(self, item):
        return self.indexOfTopLevelItem(item)

    def takeItem(self, index):
        return self.takeTopLevelItem(index)

    def insertItem(self, index, item):
        self.insertTopLevelItem(index, item)

    def move_rows(self, selected, target):
        """Move selected rows to an insertion boundary in the original table."""
        selected = sorted(set(selected))
        if not selected:
            return
        rest = [index for index in range(self.count()) if index not in selected]
        insertion = max(0, min(len(rest), target - sum(index < target for index in selected)))
        order = rest[:insertion] + selected + rest[insertion:]
        if order == list(range(self.count())):
            return
        selected_items = [self.item(index) for index in selected]
        with QSignalBlocker(self):
            items = [self.takeItem(0) for _ in range(self.count())]
            for index in order:
                self.addTopLevelItem(items[index])
            for item in selected_items:
                item.setSelected(True)
        self.orderChanged.emit()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
            self.filesDropped.emit(paths)
            event.acceptProposedAction()
        else:
            target_item = self.itemAt(event.position().toPoint())
            target = self.row(target_item) if target_item else self.count()
            if target_item and event.position().y() > self.visualItemRect(target_item).center().y():
                target += 1
            self.move_rows([self.row(item) for item in self.selectedItems()], target)
            event.accept()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Delete:
            self.removeRequested.emit()
            event.accept()
        else:
            super().keyPressEvent(event)


def _file_size(size):
    return f"{size / (1024 * 1024):,.2f} MB" if size >= 1024 * 1024 else f"{size / 1024:,.1f} KB"


def _page_range_text(pages):
    if not pages:
        return ""
    ranges = []
    start = end = pages[0]
    for page in pages[1:]:
        if page == end + 1:
            end = page
        else:
            ranges.append(str(start + 1) if start == end else f"{start + 1}-{end + 1}")
            start = end = page
    ranges.append(str(start + 1) if start == end else f"{start + 1}-{end + 1}")
    return ", ".join(ranges)


PAGE_FILE_FILTER = 'PDF 및 이미지 (*.pdf *.png *.jpg *.jpeg);;PDF (*.pdf);;이미지 (*.png *.jpg *.jpeg)'


class MergeDialog(QDialog):
    def __init__(self, parent=None, paths=None, purpose="merge", current_page=0, document_pages=0, insert_index=None):
        super().__init__(parent)
        from .theme import icon

        self.is_insert = purpose in {"insert", "pages"}
        self.allow_images = purpose == 'pages'
        self.fixed_insert_index = insert_index
        self.setWindowTitle("페이지 추가 — ADF" if self.is_insert else "PDF 파일 병합 — ADF")
        self.resize(1020, 610)
        self.setMinimumSize(850, 460)
        self.setAcceptDrops(True)
        self.passwords: dict[str, str] = {}
        self.submit_handler = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 22, 22, 20)
        layout.setSpacing(14)
        heading = QLabel("페이지 추가" if self.is_insert else "파일 병합")
        heading.setStyleSheet("font-size: 18pt; font-weight: 650; color: #263244;")
        layout.addWidget(heading)
        top = QHBoxLayout()
        self.add_button = QPushButton("파일 추가…")
        self.add_button.setIcon(icon("plus"))
        self.add_button.clicked.connect(self.choose_files)
        top.addWidget(self.add_button)
        top.addSpacing(8)
        add_hint = QLabel("목록 순서대로 현재 문서에 들어갑니다. 파일마다 필요한 페이지만 고를 수도 있습니다."
                          if self.is_insert else "PDF 파일을 끌어 놓거나 추가하세요. 목록 순서대로 하나의 PDF가 됩니다.")
        add_hint.setObjectName("muted")
        top.addWidget(add_hint, 1)
        layout.addLayout(top)
        self.file_list = FileList()
        self.file_list.filesDropped.connect(self.add_paths)
        self.file_list.removeRequested.connect(self.remove_selected)
        self.file_list.itemDoubleClicked.connect(lambda *_: self.choose_pages())
        layout.addWidget(self.file_list, 1)
        actions = QHBoxLayout()
        for attribute, label, slot in (
            ("up_button", "위로", lambda: self.move_selected(-1)),
            ("down_button", "아래로", lambda: self.move_selected(1)),
            ("pages_button", "페이지 선택…", self.choose_pages),
            ("remove_button", "제거", self.remove_selected),
        ):
            button = QPushButton(label)
            button.clicked.connect(slot)
            setattr(self, attribute, button)
            actions.addWidget(button)
        actions.addStretch()
        order_hint = QLabel("끌어서 순서 변경 · Ctrl / Shift로 여러 파일 선택")
        order_hint.setObjectName("muted")
        actions.addWidget(order_hint)
        layout.addLayout(actions)
        self.insert_position = None
        if self.is_insert and insert_index is not None:
            location = '문서 맨 앞' if insert_index == 0 else '문서 맨 끝' if insert_index == document_pages else f'{insert_index}쪽과 {insert_index + 1}쪽 사이'
            layout.addWidget(QLabel(f'추가 위치: {location}'))
        elif self.is_insert:
            position_row = QHBoxLayout()
            position_row.addWidget(QLabel("추가 위치"))
            self.insert_position = QComboBox()
            self.insert_position.addItem(f"현재 {current_page + 1}쪽 앞", current_page)
            self.insert_position.addItem(f"현재 {current_page + 1}쪽 뒤", min(document_pages, current_page + 1))
            self.insert_position.addItem("문서 맨 끝", document_pages)
            self.insert_position.setCurrentIndex(1)
            position_row.addWidget(self.insert_position)
            position_row.addStretch()
            layout.addLayout(position_row)
        self.validation = QLabel()
        self.validation.setObjectName("muted")
        layout.addWidget(self.validation)
        footer = QHBoxLayout()
        self.summary = QLabel()
        self.summary.setStyleSheet("font-weight: 600;")
        footer.addWidget(self.summary)
        footer.addStretch()
        self.buttons = _buttons(self, "문서에 추가" if self.is_insert else "파일 병합")
        footer.addWidget(self.buttons)
        layout.addLayout(footer)
        self.file_list.orderChanged.connect(self._update_summary)
        self.file_list.itemSelectionChanged.connect(self._update_controls)
        self.add_paths(paths or [])

    @property
    def paths(self) -> list[str]:
        return [self.file_list.item(i).data(0, Qt.ItemDataRole.UserRole) for i in range(self.file_list.count())]

    @property
    def selected_ranges(self) -> list[list[int] | None]:
        return [self.file_list.item(i).data(0, Qt.ItemDataRole.UserRole + 3) for i in range(self.file_list.count())]

    @property
    def insert_index(self) -> int:
        if self.fixed_insert_index is not None:
            return self.fixed_insert_index
        return int(self.insert_position.currentData()) if self.insert_position is not None else 0

    def choose_files(self):
        title = "현재 문서에 추가할 PDF" if self.is_insert else "병합할 PDF 추가"
        paths, _ = QFileDialog.getOpenFileNames(self, title, "", PAGE_FILE_FILTER if self.allow_images else PDF_FILTER)
        self.add_paths(paths)

    def add_paths(self, paths):
        from .theme import icon

        for path in paths:
            path = str(Path(path).resolve())
            item = QTreeWidgetItem([Path(path).name, "—", "—", "—", "확인 중"])
            item.setData(0, Qt.ItemDataRole.UserRole, path)
            item.setData(0, Qt.ItemDataRole.UserRole + 1, 0)
            item.setData(0, Qt.ItemDataRole.UserRole + 2, 0)
            item.setData(0, Qt.ItemDataRole.UserRole + 3, None)
            item.setData(0, Qt.ItemDataRole.UserRole + 4, False)
            item.setIcon(0, icon("doc", "#cc5448"))
            item.setToolTip(0, path)
            item.setSizeHint(0, QSize(250, 47))
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsDropEnabled)
            item.setTextAlignment(2, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            try:
                stat = Path(path).stat()
                item.setText(2, _file_size(stat.st_size))
                item.setText(3, datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d  %H:%M"))
                item.setData(0, Qt.ItemDataRole.UserRole + 2, stat.st_size)
                is_image = self.allow_images and Path(path).suffix.lower() in {'.png', '.jpg', '.jpeg'}
                if is_image:
                    from .document import open_page_file
                    doc, password = open_page_file(path), None
                    item.setIcon(0, icon('image', '#327250'))
                elif Path(path).suffix.lower() != ".pdf":
                    raise ValueError("PDF 파일만 추가할 수 있습니다.")
                else:
                    doc, password = _open_pdf(path, self)
                if doc is None:
                    continue
                try:
                    count = doc.page_count
                    item.setData(0, Qt.ItemDataRole.UserRole + 1, count)
                    item.setData(0, Qt.ItemDataRole.UserRole + 4, True)
                    item.setText(1, f"전체 ({count:,}페이지)")
                    item.setText(4, "준비됨")
                    item.setForeground(4, QColor("#327250"))
                    if password is not None:
                        self.passwords[path] = password
                finally:
                    doc.close()
            except Exception as exc:
                item.setText(4, "파일 없음" if not Path(path).is_file() else "읽기 실패")
                item.setForeground(4, QColor("#b9473e"))
                item.setToolTip(4, str(exc))
            self.file_list.addTopLevelItem(item)
        self._update_summary()

    def remove_selected(self):
        for item in self.file_list.selectedItems():
            self.file_list.takeItem(self.file_list.row(item))
        self._update_summary()

    def move_selected(self, offset):
        rows = sorted(self.file_list.row(item) for item in self.file_list.selectedItems())
        if not rows or (offset < 0 and rows[0] == 0) or (offset > 0 and rows[-1] == self.file_list.count() - 1):
            return
        for row in (rows if offset < 0 else reversed(rows)):
            item = self.file_list.takeItem(row)
            self.file_list.insertItem(row + offset, item)
            item.setSelected(True)
        self._update_summary()

    def choose_pages(self):
        selected = self.file_list.selectedItems()
        if len(selected) != 1:
            return
        item = selected[0]
        if not item.data(0, Qt.ItemDataRole.UserRole + 4):
            return
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if Path(path).suffix.lower() != '.pdf':
            return
        current = item.data(0, Qt.ItemDataRole.UserRole + 3)
        dialog = MergePageRangeDialog(path, selected_pages=current, parent=self)
        if dialog.valid and dialog.exec():
            pages = dialog.selected_pages
            count = item.data(0, Qt.ItemDataRole.UserRole + 1)
            all_pages = pages == list(range(count))
            item.setData(0, Qt.ItemDataRole.UserRole + 3, None if all_pages else pages)
            text = f"전체 ({count:,}페이지)" if all_pages else f"{_page_range_text(pages)} ({len(pages):,}페이지)"
            item.setText(1, text)
            item.setToolTip(1, text)
            if dialog.password is not None:
                self.passwords[path] = dialog.password
            self._update_summary()

    def _update_controls(self):
        selected = self.file_list.selectedItems()
        rows = [self.file_list.row(item) for item in selected]
        self.up_button.setEnabled(bool(rows) and min(rows) > 0)
        self.down_button.setEnabled(bool(rows) and max(rows) < self.file_list.count() - 1)
        self.remove_button.setEnabled(bool(rows))
        self.pages_button.setEnabled(len(selected) == 1 and bool(selected[0].data(0, Qt.ItemDataRole.UserRole + 4))
                                     and Path(selected[0].data(0, Qt.ItemDataRole.UserRole)).suffix.lower() == '.pdf')

    def _update_summary(self, *_):
        count = self.file_list.count()
        items = [self.file_list.item(index) for index in range(count)]
        pages = sum(len(item.data(0, Qt.ItemDataRole.UserRole + 3)) if item.data(0, Qt.ItemDataRole.UserRole + 3) is not None
                    else item.data(0, Qt.ItemDataRole.UserRole + 1) for item in items)
        size = sum(item.data(0, Qt.ItemDataRole.UserRole + 2) for item in items)
        invalid = sum(not item.data(0, Qt.ItemDataRole.UserRole + 4) for item in items)
        self.summary.setText(f"{count}개 파일  ·  총 {pages:,}페이지  ·  원본 합계 {_file_size(size)}")
        required = 1 if self.is_insert else 2
        self.validation.setText(f"확인이 필요한 파일이 {invalid}개 있습니다. 상태를 확인하거나 목록에서 제거하세요."
                                if invalid else "PDF 파일을 한 개 이상 추가하세요." if self.is_insert and count < 1
                                else "PDF 파일을 2개 이상 추가하세요." if not self.is_insert and count < 2
                                else "추가한 페이지는 저장 전까지 현재 작업에만 반영됩니다." if self.is_insert
                                else "병합한 PDF는 새 파일로 저장됩니다.")
        self.validation.setStyleSheet("color: #b9473e;" if invalid else "color: #77808e;")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(count >= required and not invalid and pages > 0)
        self._update_controls()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            self.add_paths([url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()])
            event.acceptProposedAction()

    def accept(self):
        for index in range(self.file_list.count()):
            item = self.file_list.item(index)
            if not Path(item.data(0, Qt.ItemDataRole.UserRole)).is_file():
                item.setData(0, Qt.ItemDataRole.UserRole + 4, False)
                item.setText(4, "파일 없음")
                item.setForeground(4, QColor("#b9473e"))
        self._update_summary()
        if self.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled():
            if callable(self.submit_handler):
                self.submit_handler()
                return
            super().accept()


class PrintOptionsDialog(QDialog):
    def __init__(self, page_count, current, selected, parent=None):
        super().__init__(parent)
        self.page_count, self.current, self.selected = page_count, current, selected
        self.pages = []
        self.setObjectName('printOptions')
        self.setStyleSheet('QDialog#printOptions QSpinBox:disabled, QDialog#printOptions QLineEdit:disabled '
                          '{ color: #929ba8; background: #f1f3f6; }')
        self.setWindowTitle('인쇄 설정 — ADF')
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(16)
        form = QFormLayout()
        self.target = QComboBox()
        for title, mode in (('전체 페이지', 'all'), (f'현재 페이지 ({current + 1}쪽)', 'current'),
                            (f'선택한 페이지 ({len(selected)}쪽)', 'selected'), ('페이지 직접 입력', 'range')):
            self.target.addItem(title, mode)
        self.target.setAccessibleName('인쇄 대상')
        form.addRow('인쇄 대상', self.target)
        self.ranges = QLineEdit()
        self.ranges.setPlaceholderText('예: 1, 3, 5-8, 12-끝')
        self.ranges.setAccessibleName('인쇄할 페이지 범위')
        form.addRow('페이지 번호', self.ranges)
        self.scale = QComboBox()
        for title, mode in (('용지에 맞춤', 'fit'), ('큰 페이지만 축소', 'shrink'),
                            ('실제 크기 (100%)', 'actual'), ('배율 지정', 'custom')):
            self.scale.addItem(title, mode)
        self.scale.setAccessibleName('인쇄 크기')
        form.addRow('인쇄 크기', self.scale)
        self.percent = _spin(1, 1000, 100, '%')
        self.percent.setAccessibleName('인쇄 배율')
        form.addRow('배율', self.percent)
        self.percent_label = form.labelForField(self.percent)
        layout.addLayout(form)
        self.summary = _description('')
        layout.addWidget(self.summary)
        layout.addWidget(_description('다음 단계에서 프린터와 용지, 방향, 인쇄 매수를 선택합니다.'))
        self.buttons = _buttons(self, '프린터 선택…')
        layout.addWidget(self.buttons)
        for signal in (self.target.currentIndexChanged, self.ranges.textChanged,
                       self.scale.currentIndexChanged, self.percent.valueChanged):
            signal.connect(self._update)
        self._update()

    def _update(self, *_):
        from .printing import print_pages
        self.ranges.setEnabled(self.target.currentData() == 'range')
        self.percent.setEnabled(self.scale.currentData() == 'custom')
        self.percent_label.setEnabled(self.percent.isEnabled())
        try:
            self.pages = print_pages(self.target.currentData(), self.page_count, self.current,
                                     self.selected, self.ranges.text())
            text = f'{len(self.pages):,}페이지를 인쇄합니다. 가로세로 비율은 유지됩니다.'
            if self.scale.currentData() in ('actual', 'custom'):
                text += ' 인쇄 영역보다 큰 부분은 잘릴 수 있습니다.'
            self.summary.setText(text)
        except ValueError as error:
            self.pages = []
            self.summary.setText(str(error))
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(bool(self.pages))

    def accept(self):
        self._update()
        if self.pages:
            super().accept()


class SplitDialog(QDialog):
    def __init__(self, document, parent=None, selected_pages=None, extract=False):
        super().__init__(parent)
        self.extract = extract
        self.document = document
        self.groups: list[list[int]] = []
        self.include_remaining = False
        self.submit_handler = None
        self.setWindowTitle('페이지 추출 — ADF' if extract else "PDF 분할 — ADF")
        self.resize(890, 710)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(12)
        layout.addWidget(_description("페이지를 골라 새 PDF로 저장합니다. Ctrl / Shift를 누르면 여러 페이지를 선택할 수 있습니다."))
        self.thumbnails = ThumbnailList(document.doc)
        layout.addWidget(self.thumbnails, 1)
        selection_bar = QHBoxLayout()
        all_button = QPushButton("전체 선택")
        all_button.clicked.connect(self.thumbnails.selectAll)
        clear_button = QPushButton("선택 해제")
        clear_button.clicked.connect(self.thumbnails.clearSelection)
        self.selection_label = QLabel("선택된 페이지 없음")
        selection_bar.addWidget(all_button)
        selection_bar.addWidget(clear_button)
        selection_bar.addWidget(self.selection_label)
        selection_bar.addStretch()
        layout.addLayout(selection_bar)
        form = QFormLayout()
        self.mode = QComboBox()
        for title, data in (("선택한 페이지를 한 파일로 추출", "range"), ("입력한 범위마다 파일 만들기", "ranges"),
                            ("모든 페이지를 낱장으로", "single"), ("N페이지 단위로 나누기", "chunks")):
            self.mode.addItem(title, data)
        form.addRow("분할 방식", self.mode)
        self.range_input = QLineEdit("1-끝")
        self.range_input.setPlaceholderText("예: 1-3, 7, 12-끝")
        self.range_input.setAccessibleName("분할할 페이지 범위")
        form.addRow("페이지 범위", self.range_input)
        self.chunk_size = _spin(1, document.page_count, min(5, document.page_count), " 페이지")
        form.addRow("묶음 크기", self.chunk_size)
        layout.addLayout(form)
        self.remaining = QCheckBox("선택하지 않은 나머지 페이지도 별도 PDF로 저장")
        layout.addWidget(self.remaining)
        self.summary = _description("")
        layout.addWidget(self.summary)
        self.buttons = _buttons(self, '다른 이름으로 저장…' if extract else "저장 폴더 선택…")
        layout.addWidget(self.buttons)
        self.mode.currentIndexChanged.connect(self._update)
        self.range_input.textChanged.connect(self._update)
        self.chunk_size.valueChanged.connect(self._update)
        self.remaining.toggled.connect(self._update)
        self.thumbnails.itemSelectionChanged.connect(self._selection_changed)
        if selected_pages is not None:
            self.range_input.setText(', '.join(str(page + 1) for page in selected_pages))
        if extract:
            self.mode.hide()
            form.labelForField(self.mode).hide()
            self.remaining.hide()
            self.chunk_size.hide()
            form.labelForField(self.chunk_size).hide()
        self._update()

    def _selection_changed(self):
        pages = self.thumbnails.selected_pages
        self.selection_label.setText(f"{len(pages):,}페이지 선택" if pages else "선택된 페이지 없음")
        if self.mode.currentData() in ("range", "ranges"):
            if not pages:
                self.range_input.clear()
                return
            ranges = []
            start = end = pages[0]
            for page in pages[1:]:
                if page == end + 1:
                    end = page
                else:
                    ranges.append(str(start + 1) if start == end else f"{start + 1}-{end + 1}")
                    start = end = page
            ranges.append(str(start + 1) if start == end else f"{start + 1}-{end + 1}")
            self.range_input.setText(", ".join(ranges))

    def _get_groups(self):
        from .document import parse_ranges

        count = self.document.page_count
        mode = self.mode.currentData()
        if mode == "single":
            return [[i] for i in range(count)]
        if mode == "chunks":
            step = self.chunk_size.value()
            return [list(range(i, min(count, i + step))) for i in range(0, count, step)]
        groups = parse_ranges(self.range_input.text(), count)
        if not groups:
            raise ValueError("페이지 범위를 입력해 주세요.")
        if mode == "range":
            return [list(dict.fromkeys(page for group in groups for page in group))]
        return groups

    def _update(self, *_):
        ranged = self.mode.currentData() in ("range", "ranges")
        self.range_input.setEnabled(ranged)
        self.chunk_size.setEnabled(self.mode.currentData() == "chunks")
        self.remaining.setEnabled(ranged)
        try:
            groups = self._get_groups()
            chosen = {page for group in groups for page in group}
            with QSignalBlocker(self.thumbnails):
                for index in range(self.thumbnails.count()):
                    self.thumbnails.item(index).setSelected(index in chosen)
            self.selection_label.setText(f"{len(chosen):,}페이지 선택")
            rest = self.document.page_count - len(chosen)
            extra = 1 if ranged and self.remaining.isChecked() and rest else 0
            self.summary.setText(f'{len(chosen):,}페이지를 새 PDF로 저장합니다.' if self.extract else
                                 f"PDF {len(groups) + extra:,}개를 만듭니다. 파일명에 페이지 번호가 자동으로 붙습니다.")
            self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(True)
        except ValueError as exc:
            with QSignalBlocker(self.thumbnails):
                self.thumbnails.clearSelection()
            self.selection_label.setText("선택된 페이지 없음")
            self.summary.setText(str(exc))
            self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)

    def accept(self):
        try:
            self.groups = self._get_groups()
        except ValueError as exc:
            QMessageBox.warning(self, "페이지 범위 확인", str(exc))
            return
        self.include_remaining = self.remaining.isEnabled() and self.remaining.isChecked()
        if callable(self.submit_handler):
            self.submit_handler()
            return
        super().accept()


from .system_fonts import normal_font_name as _normal_font, resolve_font_file


class FontPicker(QWidget):
    changed = Signal()

    def __init__(self, parent=None, family: str | None = None):
        super().__init__(parent)
        self._custom_file = None
        self.combo = QFontComboBox()
        self.combo.setAccessibleName("PDF에 사용할 글꼴")
        self.combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        fallback = "Malgun Gothic" if sys.platform == "win32" else "Apple SD Gothic Neo" if sys.platform == "darwin" else "DejaVu Sans"
        requested_family = family or fallback
        available = QFontDatabase.families()
        matched_family = next((name for name in available if _normal_font(name) == _normal_font(requested_family)), None)
        if matched_family is None:
            path = resolve_font_file(requested_family)
            if path:
                font_id = QFontDatabase.addApplicationFont(path)
                loaded_families = QFontDatabase.applicationFontFamilies(font_id)
                matched_family = loaded_families[0] if loaded_families else None
        self.combo.setCurrentFont(QFont(matched_family or requested_family))
        self.file_button = QPushButton("파일…")
        self.file_button.setToolTip("글꼴 파일 직접 선택 (.ttf, .otf, .ttc)")
        self.status = _description("")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        row = QHBoxLayout()
        row.addWidget(self.combo, 1)
        row.addWidget(self.file_button)
        layout.addLayout(row)
        layout.addWidget(self.status)
        self.combo.currentFontChanged.connect(self._family_changed)
        self.file_button.clicked.connect(self._choose_file)
        self._update_status()

    @property
    def fontfile(self):
        return self._custom_file or resolve_font_file(self.combo.currentFont().family())

    def _family_changed(self, *_):
        self._custom_file = None
        self._update_status()
        self.changed.emit()

    def _choose_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "글꼴 파일 선택", "", "글꼴 파일 (*.ttf *.otf *.ttc)")
        if path:
            try:
                fitz.Font(fontfile=path)
            except Exception as exc:
                QMessageBox.warning(self, "글꼴 확인", f"이 글꼴을 사용할 수 없습니다.\n{exc}")
                return
            self._custom_file = path
            self._update_status()
            self.changed.emit()

    def _update_status(self):
        path = self.fontfile
        self.status.setText(f"{Path(path).name} · PDF에 포함" if path else "글꼴 파일을 찾지 못했습니다. 다른 글꼴이나 파일을 선택하세요.")

    def require_file(self):
        path = self.fontfile
        if not path:
            raise ValueError("PDF에 포함할 수 있는 글꼴을 선택해 주세요. ‘파일…’에서 직접 고를 수도 있습니다.")
        return path


class ColorButton(QPushButton):
    changed = Signal()

    def __init__(self, color=None, parent=None):
        super().__init__(parent)
        self.color = QColor(color or "#222222")
        self.setAccessibleName("글자 색 선택")
        self.clicked.connect(self._choose)
        self._update()

    @property
    def rgb(self):
        return (self.color.redF(), self.color.greenF(), self.color.blueF())

    def _choose(self):
        color = QColorDialog.getColor(self.color, self, "글자 색")
        if color.isValid():
            self.color = color
            self._update()
            self.changed.emit()

    def _update(self):
        swatch = QPixmap(18, 18)
        swatch.fill(self.color)
        self.setIcon(QIcon(swatch))
        self.setText(self.color.name().upper())


class PdfPreview(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._image = None
        self.setObjectName("pdfPreview")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(240, 310)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet("background: #e9ebef; border-radius: 10px; padding: 14px;")

    def set_page(self, page):
        self._image = pdf_pixmap(page, 950, 1200)
        self._refresh()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh()

    def _refresh(self):
        if self._image is not None:
            self.setPixmap(self._image.scaled(
                QSize(max(1, self.width() - 28), max(1, self.height() - 28)),
                Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation,
            ))


class NumberingDialog(QDialog):
    def __init__(self, document, current_page=0, parent=None, *, start_right=None, spread=None):
        super().__init__(parent)
        from .numbering_widgets import NumberingPreview, NumberColor, NumberFontSize
        reader = getattr(parent, 'view', None)
        self.start_right = getattr(reader, 'start_right', False) if start_right is None else start_right
        self.spread = getattr(reader, 'is_spread', False) if spread is None else spread
        self.document = document
        self.options = {}
        self.position = 'bottom-center'
        self.anchor_page = current_page
        self.setWindowTitle("페이지 번호 넣기")
        self.resize(1260, 780)
        self.setMinimumSize(1060, 650)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)
        layout.addWidget(_description("미리보기의 위치 아이콘을 눌러 번호를 배치하세요."))
        body = QHBoxLayout()
        body.setSpacing(20)
        self.preview = NumberingPreview()
        self.preview.positionChosen.connect(self._choose_position)
        preview_column = QVBoxLayout()
        preview_column.addWidget(self.preview, 1)
        page_row = QHBoxLayout()
        self.previous = QPushButton('‹')
        self.previous.setToolTip('이전 미리보기')
        self.previous.setAccessibleName('이전 미리보기')
        self.previous.setFixedWidth(38)
        self.previous.clicked.connect(lambda: self._navigate(-1))
        self.next = QPushButton('›')
        self.next.setToolTip('다음 미리보기')
        self.next.setAccessibleName('다음 미리보기')
        self.next.setFixedWidth(38)
        self.next.clicked.connect(lambda: self._navigate(1))
        self.preview_page = _spin(1, document.page_count, current_page + 1)
        self.preview_page.setAccessibleName('미리보기 페이지')
        self.preview_page.setFixedWidth(86)
        page_row.addWidget(self.previous)
        page_row.addWidget(self.preview_page)
        page_row.addWidget(QLabel(f"/ {document.page_count:,}"))
        page_row.addWidget(self.next)
        page_row.addStretch()
        self.order_hint = QLabel()
        self.order_hint.setObjectName('muted')
        self.order_hint.setMinimumWidth(154)
        self.order_hint.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        page_row.addWidget(self.order_hint)
        preview_column.addLayout(page_row)
        body.addLayout(preview_column, 1)

        settings_widget = QWidget()
        settings_widget.setObjectName("numberSettings")
        settings_widget.setFixedWidth(485)
        settings = QVBoxLayout(settings_widget)
        settings.setContentsMargins(18, 18, 18, 18)
        settings.setSpacing(20)
        self.mirror = QCheckBox('맞쪽 번호 · 좌우 대칭')
        self.mirror.setToolTip('왼쪽·오른쪽 페이지에 번호 위치를 대칭으로 적용합니다')
        settings.addWidget(self.mirror)
        margins = QHBoxLayout()
        margins.setSpacing(14)
        self.margin_x = _decimal(0, 100, 12, " mm")
        self.margin_y = _decimal(0, 100, 12, " mm")
        for title, control in [('좌우 여백', self.margin_x), ('상하 여백', self.margin_y)]:
            column = QVBoxLayout()
            column.setSpacing(6)
            column.addWidget(QLabel(title))
            control.setAccessibleName(title)
            column.addWidget(control)
            margins.addLayout(column, 1)
        settings.addLayout(margins)

        font_row = QHBoxLayout()
        font_row.setSpacing(7)
        font_row.addWidget(QLabel('글꼴'))
        self.font_picker = FontPicker()
        self.font_picker.file_button.hide()
        self.font_picker.status.hide()
        self.font_picker.combo.setMinimumWidth(115)
        self.font_picker.combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        font_row.addWidget(self.font_picker, 1)
        self.font_size = NumberFontSize()
        font_row.addWidget(self.font_size)
        self.color_button = NumberColor()
        font_row.addWidget(self.color_button)
        settings.addLayout(font_row)

        numbers = QHBoxLayout()
        numbers.setSpacing(12)
        self.ranges = QLineEdit()
        self.ranges.setPlaceholderText('전체')
        self.ranges.setToolTip('비워 두면 전체 · 예: 2-끝, 1-3, 7')
        self.ranges.setAccessibleName('번호 적용 페이지')
        self.start = _spin(0, 9999999, 1)
        self.start.setFixedWidth(108)
        self.start.setAccessibleName('시작 번호')
        for title, control, stretch in [('번호 적용 페이지', self.ranges, 1), ('시작 번호', self.start, 0)]:
            column = QVBoxLayout()
            column.setSpacing(6)
            column.addWidget(QLabel(title))
            column.addWidget(control)
            numbers.addLayout(column, stretch)
        zero_column = QVBoxLayout()
        zero_column.setSpacing(6)
        zero_column.addWidget(QLabel('자릿수'))
        self.zero_pad = QCheckBox('앞에 0 넣기')
        self.zero_pad.setToolTip('마지막 번호의 자릿수에 맞춥니다. 예: 001…120')
        zero_column.addWidget(self.zero_pad)
        numbers.addLayout(zero_column)
        settings.addLayout(numbers)

        affix_row = QHBoxLayout()
        affix_row.setSpacing(10)
        affix_row.addWidget(QLabel('번호 앞뒤'))
        self.affix = QComboBox()
        self.affix.addItem('없음 · 1', ('', ''))
        self.affix.addItem('앞에 p. · p.1', ('p.', ''))
        self.affix.addItem('뒤에 p · 1p', ('', 'p'))
        self.affix.setAccessibleName('번호 앞뒤 표시')
        affix_row.addWidget(self.affix)
        affix_row.addStretch()
        settings.addLayout(affix_row)
        self.format_example = QLabel()
        self.format_example.setObjectName('numberExample')
        self.format_example.setWordWrap(True)
        settings.addWidget(self.format_example)
        settings.addStretch()
        self.margin_hint = _description('좌우 여백은 번호가 붙는 쪽 가장자리에서 적용됩니다.')
        self.margin_hint.setObjectName('muted')
        settings.addWidget(self.margin_hint)
        body.addWidget(settings_widget)
        layout.addLayout(body, 1)
        self.preview_status = _description("")
        layout.addWidget(self.preview_status)
        self.buttons = _buttons(self, "번호 적용")
        layout.addWidget(self.buttons)
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(80)
        self._preview_timer.timeout.connect(self._update_preview)
        for widget in (self.margin_x, self.margin_y, self.start, self.preview_page):
            widget.valueChanged.connect(self._schedule_preview)
        self.ranges.textChanged.connect(self._schedule_preview)
        self.zero_pad.toggled.connect(self._schedule_preview)
        self.affix.currentIndexChanged.connect(self._schedule_preview)
        self.font_size.currentTextChanged.connect(self._schedule_preview)
        self.mirror.toggled.connect(self._mirror_changed)
        self.font_picker.changed.connect(self._schedule_preview)
        self.color_button.changed.connect(self._schedule_preview)
        self._schedule_preview()

    def _schedule_preview(self, *_):
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        self._preview_timer.start()

    def _mirror_changed(self, checked):
        from .page_layout import page_side, numbering_position
        current = self.preview_page.value() - 1
        actual = numbering_position(self.position, current, mirror=not checked,
                                    anchor_page=self.anchor_page, start_right=self.start_right)
        self.anchor_page = current
        if checked and actual.endswith('-center'):
            actual = actual.split('-')[0] + '-' + page_side(current, self.start_right)
        self.position = actual
        self._schedule_preview()

    def _choose_position(self, index, position):
        self.position = position
        self.anchor_page = index
        self._schedule_preview()

    def _shown_slots(self):
        from .page_layout import spread_groups
        current = self.preview_page.value() - 1
        if self.spread or self.mirror.isChecked():
            return next(row for row in spread_groups(self.document.page_count, self.start_right) if current in row)
        return [current]

    def _shown_indices(self):
        return [index for index in self._shown_slots() if index is not None]

    def _navigate(self, step):
        if self.spread or self.mirror.isChecked():
            row = self._shown_indices()
            index = min(row) - 1 if step < 0 else max(row) + 1
            self.preview_page.setValue(max(1, min(self.document.page_count, index + 1)))
        else:
            self.preview_page.setValue(self.preview_page.value() + step)

    def _options(self):
        from .document import parse_ranges
        indices = list(dict.fromkeys(page for group in parse_ranges(self.ranges.text().strip() or '전체', self.document.page_count) for page in group))
        prefix, suffix = self.affix.currentData()
        return {
            "indices": indices, "start": self.start.value(), "prefix": prefix,
            "suffix": suffix, "zero_pad": self.zero_pad.isChecked(),
            "position": self.position, "mirror": self.mirror.isChecked(),
            "anchor_page": self.anchor_page, "start_right": self.start_right,
            "margin_x": self.margin_x.value(), "margin_y": self.margin_y.value(),
            "font_size": self.font_size.value(), "color": self.color_button.rgb,
            "fontfile": self.font_picker.require_file(),
        }

    def _update_preview(self):
        from .document import number_page
        from .page_layout import numbering_label, numbering_position

        shown = self._shown_indices()
        current = self.preview_page.value() - 1
        self.previous.setEnabled(min(shown) > 0)
        self.next.setEnabled(max(shown) < self.document.page_count - 1)
        self.order_hint.setText('첫 페이지 · ' + ('오른쪽 시작' if self.start_right else '왼쪽 시작'))
        with fitz.open() as preview_doc:
            for index in shown:
                preview_doc.insert_pdf(self.document.doc, from_page=index, to_page=index)
            error = None
            try:
                options = self._options()
                selected = options['indices']
                labels = {index: numbering_label(options['start'], offset, len(selected),
                                                  prefix=options['prefix'], suffix=options['suffix'], zero_pad=options['zero_pad'])
                          for offset, index in enumerate(selected)}
                examples = [labels[selected[0]]]
                if len(selected) > 1:
                    examples.append(labels[selected[1]])
                if len(selected) > 2:
                    examples += ['…', labels[selected[-1]]]
                self.format_example.setText('  ·  '.join(examples))
                for slot, index in enumerate(shown):
                    if index in labels:
                        position = numbering_position(self.position, index, mirror=self.mirror.isChecked(),
                                                      anchor_page=self.anchor_page, start_right=self.start_right)
                        number_page(preview_doc[slot], labels[index], position=position, **{
                            key: options[key] for key in ('margin_x', 'margin_y', 'font_size', 'color', 'fontfile')
                        })
                self.preview_status.setText(f"{len(selected):,}페이지에 번호를 넣습니다. 다른 편집과 함께 마지막에 저장하세요.")
            except Exception as exc:
                labels = {}
                error = exc
                self.format_example.setText('설정을 확인해 주세요')
                self.preview_status.setText(str(exc))
            pages = []
            for slot, index in enumerate(shown):
                page = preview_doc[slot]
                position = numbering_position(self.position, index, mirror=self.mirror.isChecked(),
                                              anchor_page=self.anchor_page, start_right=self.start_right)
                pages.append({'index': index, 'image': pdf_pixmap(page, 950, 1200),
                              'width': page.rect.width, 'height': page.rect.height,
                              'position': position, 'label': labels.get(index)})
            self.preview.set_pages(pages, current, slots=self._shown_slots())
            self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(error is None)

    def accept(self):
        try:
            self.options = self._options()
        except ValueError as exc:
            QMessageBox.warning(self, "번호 설정 확인", str(exc))
            return
        self._preview_timer.stop()
        super().accept()

    def reject(self):
        self._preview_timer.stop()
        super().reject()


class CompressionDialog(QDialog):
    def __init__(self, document, parent=None):
        super().__init__(parent)
        self.document = document
        self.options = {}
        self.setWindowTitle("저용량 PDF로 저장")
        self.setObjectName('compressionDialog')
        self.resize(565, 485)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(16)
        layout.addWidget(_description("이미지 해상도와 품질을 조절해 PDF 용량을 줄입니다."))
        try:
            size = Path(document.path).stat().st_size / (1024 * 1024) if document.path else None
        except OSError:
            size = None
        layout.addWidget(QLabel(f"현재 파일: {size:,.2f} MB · {document.page_count:,}페이지" if size is not None else f"{document.page_count:,}페이지"))
        form = QFormLayout()
        form.setSpacing(13)
        self.mode = QComboBox()
        self.mode.addItem("이미지만 최적화 · 텍스트 유지", "images")
        self.mode.addItem("전체 페이지를 이미지로 변환", "raster")
        form.addRow("저장 방식", self.mode)
        self.dpi = _spin(72, 300, 150, " dpi")
        self.dpi.setSingleStep(10)
        form.addRow("최대 해상도", self.dpi)
        self.quality = _spin(20, 95, 75, " %")
        self.quality.setSingleStep(5)
        form.addRow("JPEG 품질", self.quality)
        self.target_enabled = QCheckBox("목표 용량에 맞춰 품질 자동 조절")
        form.addRow(self.target_enabled)
        self.target = _decimal(0.1, 100000, 5, " MB 이하")
        form.addRow("목표 용량", self.target)
        self.manual_controls = [self.dpi, self.quality, form.labelForField(self.dpi), form.labelForField(self.quality)]
        self.target_label = form.labelForField(self.target)
        self.target_enabled.toggled.connect(self._sync_controls)
        self._sync_controls()
        layout.addLayout(form)
        self.message = _description("")
        layout.addWidget(self.message)
        layout.addWidget(_description("결과 용량은 문서에 따라 달라집니다. 목표 용량에 도달하지 못할 수 있으며 저장 전에 실제 결과를 확인합니다."))
        layout.addStretch()
        layout.addWidget(_buttons(self, "결과 확인…"))
        self.mode.currentIndexChanged.connect(self._update_message)
        self._update_message()

    def _sync_controls(self, *_):
        automatic = self.target_enabled.isChecked()
        self.target.setEnabled(automatic)
        self.target_label.setEnabled(automatic)
        for widget in self.manual_controls:
            widget.setEnabled(not automatic)

    def _update_message(self, *_):
        raster = self.mode.currentData() == "raster"
        self.message.setText(
            "전체 이미지 변환 후에는 텍스트 검색·복사·수정이 불가능합니다. 링크와 입력 양식도 유지되지 않습니다."
            if raster else "텍스트 검색·복사 기능을 유지하면서 PDF 안의 이미지만 압축합니다."
        )
        self.message.setStyleSheet("color: #a64b10; background: #fff4e7; border-radius: 8px; padding: 12px;" if raster else "")

    def accept(self):
        self.options = {
            "mode": self.mode.currentData(), "dpi": self.dpi.value(),
            "quality": self.quality.value(),
            "target_mb": self.target.value() if self.target_enabled.isChecked() else None,
        }
        super().accept()


class PagePickerDialog(QDialog):
    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.setWindowTitle("가져올 페이지 선택")
        self.resize(800, 650)
        self.selected_pages: list[int] = []
        self.password: str | None = None
        self.valid = False
        self.doc = None
        self.path = str(path)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(14)
        layout.addWidget(_description(f"{Path(path).name}\n가져올 페이지를 선택하세요. Ctrl / Shift로 여러 페이지를 고를 수 있습니다."))
        try:
            self.doc, self.password = _open_pdf(self.path, parent or self)
        except Exception as exc:
            QMessageBox.warning(parent or self, "PDF 열기 실패", str(exc))
        if self.doc is None:
            return
        self.valid = True
        self.thumbnails = ThumbnailList(self.doc)
        layout.addWidget(self.thumbnails, 1)
        row = QHBoxLayout()
        all_button = QPushButton("전체 선택")
        all_button.clicked.connect(self.thumbnails.selectAll)
        clear_button = QPushButton("선택 해제")
        clear_button.clicked.connect(self.thumbnails.clearSelection)
        self.summary = QLabel()
        row.addWidget(all_button)
        row.addWidget(clear_button)
        row.addWidget(self.summary)
        row.addStretch()
        layout.addLayout(row)
        self.buttons = _buttons(self, "선택 페이지 가져오기")
        layout.addWidget(self.buttons)
        self.thumbnails.itemSelectionChanged.connect(self._update)
        self.thumbnails.item(0).setSelected(True)
        self._update()

    def _update(self):
        count = len(self.thumbnails.selected_pages)
        self.summary.setText(f"{count:,} / {self.doc.page_count:,}페이지 선택")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(count > 0)

    def exec(self):
        if not self.valid:
            return QDialog.DialogCode.Rejected
        return super().exec()

    def accept(self):
        if self.valid and self.thumbnails.selected_pages:
            self.selected_pages = self.thumbnails.selected_pages
            super().accept()

    def done(self, result):
        if self.valid:
            self.thumbnails._timer.stop()
        super().done(result)
        if self.doc is not None and not self.doc.is_closed:
            self.doc.close()


class MergePageRangeDialog(PagePickerDialog):
    """The merge range editor keeps typed ranges and page previews in sync."""

    def __init__(self, path, selected_pages=None, parent=None):
        super().__init__(path, parent=parent)
        if not self.valid:
            return
        self.setWindowTitle("병합할 페이지 선택 — ADF")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("페이지 적용")
        self.range_input = QLineEdit("전체" if selected_pages is None else _page_range_text(selected_pages))
        self.range_input.setPlaceholderText("예: 전체 또는 1-3, 7, 12-끝")
        self.range_input.setAccessibleName("병합에 포함할 페이지 범위")
        self.range_error = _description("")
        self.range_error.setStyleSheet("color: #b9473e;")
        row = QFormLayout()
        row.addRow("페이지 범위", self.range_input)
        self.layout().insertLayout(1, row)
        self.layout().insertWidget(2, self.range_error)
        self.range_input.textChanged.connect(self._range_changed)
        self.thumbnails.itemSelectionChanged.connect(self._selection_changed)
        self._range_changed()

    def _range_changed(self, *_):
        from .document import parse_ranges

        try:
            pages = {page for group in parse_ranges(self.range_input.text(), self.doc.page_count) for page in group}
            self.range_error.clear()
        except ValueError as exc:
            pages = set()
            self.range_error.setText(str(exc))
        with QSignalBlocker(self.thumbnails):
            for index in range(self.thumbnails.count()):
                self.thumbnails.item(index).setSelected(index in pages)
        self._update()

    def _selection_changed(self):
        pages = self.thumbnails.selected_pages
        with QSignalBlocker(self.range_input):
            self.range_input.setText("전체" if len(pages) == self.doc.page_count else _page_range_text(pages))
        self.range_error.setText("병합할 페이지를 선택하세요." if not pages else "")


class TextEditDialog(QDialog):
    def __init__(self, span, parent=None):
        super().__init__(parent)
        self.options = {}
        self.setWindowTitle("텍스트 수정")
        self.resize(580, 475)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(14)
        layout.addWidget(_description("선택한 한 줄의 텍스트를 수정합니다. 비워 두면 해당 텍스트를 삭제합니다."))
        original_font = re.sub(r"^[A-Z]{6}\+", "", str(span.get("font", "")))
        families = QFontDatabase.families()
        matching_family = next((name for name in families if _normal_font(name) == _normal_font(original_font)), None)
        if matching_family:
            layout.addWidget(_description(f"원본 글꼴: {original_font}"))
        else:
            notice = _description(f"원본 글꼴 ‘{original_font}’이 시스템에 없습니다. 아래 대체 글꼴을 확인하거나 다른 글꼴을 선택하세요.")
            notice.setStyleSheet("background: #fff4e7; color: #a64b10; padding: 10px; border-radius: 8px;")
            layout.addWidget(notice)
        form = QFormLayout()
        form.setSpacing(12)
        self.text_input = QLineEdit(span.get("text", ""))
        self.text_input.setAccessibleName("교체할 텍스트")
        form.addRow("텍스트", self.text_input)
        self.font_picker = FontPicker(family=matching_family)
        form.addRow("적용 글꼴", self.font_picker)
        self.font_size = _decimal(1, 500, float(span.get("size", 11)), " pt")
        form.addRow("글자 크기", self.font_size)
        color_value = int(span.get("color", 0))
        self.color_button = ColorButton(QColor((color_value >> 16) & 255, (color_value >> 8) & 255, color_value & 255))
        form.addRow("글자 색", self.color_button)
        self.fit = QCheckBox("원래 영역보다 길면 글자 크기를 줄여 맞춤")
        self.fit.setChecked(True)
        form.addRow(self.fit)
        layout.addLayout(form)
        layout.addWidget(_description("자동 줄바꿈이나 주변 문단 재배치는 지원하지 않습니다. 적용 후 화면에서 결과를 확인하세요."))
        layout.addStretch()
        layout.addWidget(_buttons(self, "수정 적용"))
        self.text_input.setFocus()
        self.text_input.selectAll()

    def accept(self):
        try:
            fontfile = self.font_picker.require_file() if self.text_input.text() else self.font_picker.fontfile
        except ValueError as exc:
            QMessageBox.warning(self, "글꼴 확인", str(exc))
            return
        self.options = {
            "text": self.text_input.text(), "font_size": self.font_size.value(),
            "color": self.color_button.rgb, "fontfile": fontfile, "fit": self.fit.isChecked(),
        }
        super().accept()

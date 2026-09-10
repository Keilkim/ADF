"""Registered stamps: management dialogs, cards and click-to-stamp selection."""
from pathlib import Path

from PySide6.QtCore import QEvent, QSize, QTimer, Qt, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QDockWidget, QDoubleSpinBox,
    QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QScrollArea, QSizePolicy, QToolButton, QVBoxLayout, QWidget)

from .stamps import _details, normalize_image


class StampDialog(QDialog):
    def __init__(self, stamp=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle('도장 관리' if stamp else '도장 등록하기')
        self.setMinimumWidth(400)
        self.image_data = stamp.png if stamp else None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)
        self.preview = QLabel('등록할 도장 이미지를 불러오세요')
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(280, 200)
        self.preview.setStyleSheet('background: #eceef0; border-radius: 8px; padding: 12px;')
        layout.addWidget(self.preview)
        self.file_button = QPushButton('파일 불러오기')
        self.file_button.setAutoDefault(False)
        self.file_button.clicked.connect(self.choose_file)
        layout.addWidget(self.file_button)
        self.name = QLineEdit(stamp.name if stamp else '')
        self.name.setMaxLength(80)
        self.name.setPlaceholderText('도장 이름')
        self.name.setAccessibleName('등록할 도장 이름')
        self.width = QDoubleSpinBox()
        self.width.setDecimals(1)
        self.width.setRange(1, 500)
        self.width.setSuffix(' mm')
        self.width.setValue(stamp.width_mm if stamp else 20)
        self.width.setAccessibleName('등록할 도장 너비')
        form = QFormLayout()
        form.addRow('이름', self.name)
        form.addRow('너비', self.width)
        layout.addLayout(form)
        self.status = QLabel('')
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText('저장')
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('취소')
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.name.textChanged.connect(self.validate)
        self.show_preview()
        self.validate()

    def show_preview(self):
        if self.image_data:
            pm = QPixmap()
            pm.loadFromData(self.image_data)
            self.preview.setPixmap(pm.scaled(260, 190, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def load_file(self, path):
        try:
            self.image_data = normalize_image(Path(path).read_bytes())
            if not self.name.text().strip():
                self.name.setText(Path(path).stem[:80])
            self.show_preview()
            self.status.setText(Path(path).name)
            self.validate()
            return True
        except Exception as error:
            self.status.setText(str(error))
            return False

    def choose_file(self):
        path, _ = QFileDialog.getOpenFileName(self, '도장 이미지 불러오기', '', '이미지 (*.png *.jpg *.jpeg)')
        if path:
            self.load_file(path)

    def validate(self):
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(bool(self.image_data and self.name.text().strip()))

    def accept(self):
        try:
            _details(self.name.text(), self.width.value())
            if not self.image_data:
                raise ValueError('도장 이미지 파일을 먼저 불러오세요.')
        except ValueError as error:
            self.status.setText(str(error))
            return
        super().accept()


CARD_STYLE = '''
QFrame#stampCard { background: #edeef0; border: none; border-radius: 8px; }
QFrame#stampCard:hover { background: #e1e3e6; border: none; }
QFrame#stampCard[selected="true"], QFrame#stampCard[selected="true"]:hover { background: #cfd2d6; border: none; }
QToolButton#stampPreview, QToolButton#stampPreview:focus { background: transparent; border: none; padding: 4px; }
QToolButton#stampPreview:hover, QToolButton#stampPreview:pressed { background: #dadddf; border: none; }
QToolButton#stampManage { background: transparent; border: none; padding: 4px 3px; font-size: 9pt; color: #42474e; }
QToolButton#stampManage:hover, QToolButton#stampManage:focus { background: #bfc3c9; border: none; }
'''


class StampCard(QFrame):
    selected = Signal(str)
    editRequested = Signal(str)
    deleteRequested = Signal(str)

    def __init__(self, stamp, parent=None):
        super().__init__(parent)
        self.stamp = stamp
        self.can_place = False
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.setObjectName('stampCard')
        self.setProperty('selected', False)
        self.setStyleSheet(CARD_STYLE)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 10, 8, 10)
        layout.setSpacing(8)
        self.preview = QToolButton()
        self.preview.setObjectName('stampPreview')
        self.preview.setFixedSize(86, 92)
        self.preview.setIconSize(QSize(76, 82))
        pm = QPixmap()
        pm.loadFromData(stamp.png)
        self.preview.setIcon(QIcon(pm))
        self.preview.setAccessibleName(f'{stamp.name} 스탬프 모드')
        self.preview.setToolTip('선택한 뒤 PDF를 클릭해서 도장을 찍으세요')
        self.preview.clicked.connect(lambda: self.selected.emit(stamp.id))
        layout.addWidget(self.preview)
        content = QVBoxLayout()
        self.name = QLabel(stamp.name)
        self.name.setWordWrap(True)
        self.name.setTextFormat(Qt.TextFormat.PlainText)
        self.name.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        content.addWidget(self.name)
        self.width = QLabel(f'너비 {stamp.width_mm:g} mm')
        self.width.setObjectName('muted')
        self.width.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        content.addWidget(self.width)
        content.addStretch()
        actions = QHBoxLayout()
        actions.setSpacing(2)
        self.edit = QToolButton()
        self.edit.setText('이름 수정하기')
        self.edit.setAccessibleName(f'{stamp.name} 이름 수정하기')
        self.edit.clicked.connect(lambda: self.editRequested.emit(stamp.id))
        self.delete = QToolButton()
        self.delete.setText('삭제')
        self.delete.setAccessibleName(f'{stamp.name} 삭제')
        self.delete.clicked.connect(lambda: self.deleteRequested.emit(stamp.id))
        for button in (self.edit, self.delete):
            button.setObjectName('stampManage')
            policy = button.sizePolicy()
            policy.setRetainSizeWhenHidden(True)
            button.setSizePolicy(policy)
            button.hide()
            actions.addWidget(button)
            button.installEventFilter(self)
        self.preview.installEventFilter(self)
        content.addLayout(actions)
        layout.addLayout(content, 1)
        self.action_timer = QTimer(self)
        self.action_timer.setSingleShot(True)
        self.action_timer.timeout.connect(self.update_action_visibility)

    def set_selected(self, selected):
        self.setProperty('selected', selected)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def set_editable(self, editable):
        self.can_place = editable
        self.preview.setEnabled(editable)

    def reveal_actions(self, visible):
        self.edit.setVisible(visible)
        self.delete.setVisible(visible)

    def update_action_visibility(self):
        self.reveal_actions(self.underMouse() or any(button.hasFocus() for button in (self.preview, self.edit, self.delete)))

    def enterEvent(self, event):
        self.reveal_actions(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.action_timer.start(0)
        super().leaveEvent(event)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.FocusIn:
            self.reveal_actions(True)
        elif event.type() == QEvent.Type.FocusOut:
            self.action_timer.start(0)
        return super().eventFilter(watched, event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.can_place:
            self.selected.emit(self.stamp.id)
            event.accept()
        else:
            super().mouseReleaseEvent(event)


class StampDock(QDockWidget):
    stampSelected = Signal(str)
    stampRemoved = Signal(str)
    stopRequested = Signal()

    def __init__(self, library, parent=None):
        super().__init__('도장 보관함', parent)
        self.library = library
        self.removed = None
        self.editable = False
        self.active_id = None
        self.cards = {}
        self.setObjectName('stampLibrary')
        self.setMinimumWidth(300)
        self.setAllowedAreas(Qt.DockWidgetArea.RightDockWidgetArea | Qt.DockWidgetArea.LeftDockWidgetArea)
        self.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetClosable | QDockWidget.DockWidgetFeature.DockWidgetMovable)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)
        self.register = QPushButton('도장 등록하기')
        self.register.clicked.connect(self.register_stamp)
        layout.addWidget(self.register)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet('QScrollArea { background: transparent; border: none; }')
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_body = QWidget()
        self.list_body.setStyleSheet('background: transparent;')
        self.list_layout = QVBoxLayout(self.list_body)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(8)
        self.list_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(self.list_body)
        layout.addWidget(self.scroll, 1)
        self.empty = QLabel('등록된 도장이 없습니다.\n도장 등록하기를 눌러 추가하세요.')
        self.empty.setWordWrap(True)
        self.empty.setObjectName('muted')
        layout.addWidget(self.empty)
        self.status = QLabel('등록된 도장을 선택하고\nPDF를 클릭해서 찍으세요.')
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.status.setObjectName('muted')
        layout.addWidget(self.status)
        self.stop = QPushButton('스탬프 모드 종료 · Esc')
        self.stop.clicked.connect(lambda: self.stopRequested.emit())
        self.stop.hide()
        layout.addWidget(self.stop)
        self.undo = QPushButton('도장 삭제 취소')
        self.undo.clicked.connect(self.undo_remove)
        self.undo.hide()
        layout.addWidget(self.undo)
        self.setWidget(body)
        self.refresh()

    def error(self, error):
        QMessageBox.warning(self, '도장 보관함', str(error))

    def refresh(self):
        while self.list_layout.count():
            widget = self.list_layout.takeAt(0).widget()
            if widget:
                widget.hide()
                widget.deleteLater()
        self.cards = {}
        for stamp in self.library.list():
            card = StampCard(stamp)
            card.set_editable(self.editable)
            card.set_selected(stamp.id == self.active_id)
            card.selected.connect(self.stampSelected)
            card.editRequested.connect(self.edit_stamp)
            card.deleteRequested.connect(self.remove)
            self.list_layout.addWidget(card)
            self.cards[stamp.id] = card
        self.empty.setVisible(not self.cards)

    def set_active(self, identifier):
        self.active_id = identifier
        for key, card in self.cards.items():
            card.set_selected(key == identifier)
        self.stop.setVisible(identifier is not None)
        self.status.setText('PDF를 클릭할 때마다 도장을 찍습니다.\nEsc 또는 우클릭으로 종료합니다.' if identifier else '등록된 도장을 선택하고\nPDF를 클릭해서 찍으세요.')

    def set_editable(self, editable):
        self.editable = editable
        for card in self.cards.values():
            card.set_editable(editable)

    def register_stamp(self):
        self.stopRequested.emit()
        dialog = StampDialog(parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                self.library.add(dialog.name.text(), dialog.image_data, dialog.width.value())
                self.refresh()
            except Exception as error:
                self.error(error)
        dialog.deleteLater()

    def edit_stamp(self, identifier):
        self.stopRequested.emit()
        try:
            dialog = StampDialog(self.library.get(identifier), self)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.library.update(identifier, dialog.name.text(), dialog.width.value(), dialog.image_data)
                self.refresh()
            dialog.deleteLater()
        except Exception as error:
            self.error(error)

    def remove(self, identifier):
        try:
            self.removed = self.library.remove(identifier)
            self.stampRemoved.emit(identifier)
            self.undo.show()
            self.refresh()
        except Exception as error:
            self.error(error)

    def undo_remove(self):
        if self.removed:
            try:
                self.library.restore(self.removed)
                self.removed = None
                self.undo.hide()
                self.refresh()
            except Exception as error:
                self.error(error)

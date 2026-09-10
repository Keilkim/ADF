"""Direct controls for page-number placement and typography."""

import re

from PySide6.QtCore import Qt, QRectF, QSize, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QLineEdit, QPushButton,
                              QToolButton, QColorDialog, QComboBox, QSizePolicy)


POSITIONS = [(f'{v}-{h}', f'{vt} {ht}')
             for v, vt in [('top', '상단'), ('bottom', '하단')]
             for h, ht in [('left', '왼쪽'), ('center', '가운데'), ('right', '오른쪽')]]


def position_icon(position):
    image = QPixmap(22, 24)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor('#797d84'), 1.2))
    painter.drawRoundedRect(QRectF(2, 1, 18, 22), 2, 2)
    vertical, horizontal = position.split('-')
    x = {'left': 4, 'center': 9, 'right': 14}[horizontal]
    y = 4 if vertical == 'top' else 17
    painter.fillRect(QRectF(x, y, 4, 3), QColor('#62656b'))
    painter.end()
    return QIcon(image)


class NumberingPreview(QWidget):
    positionChosen = Signal(int, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(380, 380)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.pages = []
        self.slots = []
        self.page_rects = {}
        self.current = 0
        self.position_buttons = []
        for slot in range(2):
            buttons = {}
            for position, title in POSITIONS:
                button = QToolButton(self)
                button.setCheckable(True)
                button.setFixedSize(30, 32)
                button.setIconSize(QSize(20, 24))
                button.setIcon(position_icon(position))
                button.setProperty('position', position)
                button.setProperty('slot', slot)
                button.setObjectName('numberPosition')
                button.setAccessibleName(title)
                button.setToolTip(title)
                button.clicked.connect(lambda checked=False, b=button: self.positionChosen.emit(b.property('page'), b.property('position')))
                button.hide()
                buttons[position] = button
            self.position_buttons.append(buttons)

    def set_pages(self, pages, current, slots=None):
        # Each page carries its actual PDF aspect ratio and rendered number.
        self.pages = pages
        self.slots = slots if slots is not None else [page['index'] for page in pages]
        self.current = current
        self._layout_pages()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_pages()

    def _layout_pages(self):
        self.page_rects = {}
        for buttons in self.position_buttons:
            for button in buttons.values():
                button.hide()
        if not self.pages:
            self.update()
            return
        gap = 24
        by_index = {page['index']: page for page in self.pages}
        widths = [by_index[index]['width'] if index is not None else self.pages[0]['width'] for index in self.slots]
        available_width = max(1, self.width() - 36 - gap * (len(self.slots) - 1))
        scale = min(available_width / sum(widths),
                    max(1, self.height() - 126) / max(page['height'] for page in self.pages))
        total_width = sum(widths) * scale + gap * (len(self.slots) - 1)
        x = (self.width() - total_width) / 2
        for slot, index in enumerate(self.slots):
            if index is None:
                x += widths[slot] * scale + gap
                continue
            page = by_index[index]
            width, height = page['width'] * scale, page['height'] * scale
            y = (self.height() - 40 - height) / 2
            rect = QRectF(x, y, width, height)
            self.page_rects[page['index']] = rect
            for position, button in self.position_buttons[slot].items():
                vertical, horizontal = position.split('-')
                bx = {'left': rect.left() + 3, 'center': rect.center().x() - 15, 'right': rect.right() - 33}[horizontal]
                by = rect.top() - 35 if vertical == 'top' else rect.bottom() + 3
                button.move(round(bx), round(by))
                button.setProperty('page', page['index'])
                button.setChecked(page['position'] == position)
                button.setAccessibleName(f"{page['index'] + 1}쪽 {dict(POSITIONS)[position]}")
                button.show()
            x += width + gap
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.fillRect(self.rect(), QColor('#e9ecf1'))
        for page in self.pages:
            rect = self.page_rects[page['index']]
            painter.fillRect(rect.translated(2, 3), QColor(0, 0, 0, 18))
            painter.fillRect(rect, Qt.GlobalColor.white)
            painter.drawPixmap(rect, page['image'], QRectF(page['image'].rect()))
            painter.setPen(QPen(QColor('#afb2b8') if page['index'] == self.current else QColor('#d6dce5'), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(rect)
            painter.setPen(QColor('#65758b'))
            caption = f"{page['index'] + 1}쪽" + (f" · {page['label']}" if page['label'] else ' · 적용 제외')
            painter.drawText(QRectF(rect.x() - 5, rect.bottom() + 40, rect.width() + 10, 24), Qt.AlignmentFlag.AlignCenter, caption)


class NumberColor(QWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.color = QColor('#222222')
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(5)
        self.swatch = QPushButton()
        self.swatch.setObjectName('numberColorSwatch')
        self.swatch.setFixedSize(30, 30)
        self.swatch.setAccessibleName('글자 색 선택')
        self.swatch.setToolTip('글자 색 선택')
        self.swatch.clicked.connect(self._choose)
        self.value = QLineEdit('#222222')
        self.value.setAccessibleName('글자 색 HEX 값')
        self.value.setToolTip('색상 코드 · 예: #2463E8')
        self.value.setFixedWidth(85)
        self.value.setMaxLength(7)
        self.value.textChanged.connect(self._typed)
        row.addWidget(self.swatch)
        row.addWidget(self.value)
        self._typed()

    def _typed(self, *_):
        value = self.value.text().strip()
        if re.fullmatch(r'#?[0-9a-fA-F]{6}', value):
            self.color = QColor('#' + value.lstrip('#'))
            self.swatch.setStyleSheet(f'background: {self.color.name()}; border: 1px solid #bac5d4; border-radius: 5px; padding: 0;')
            self.value.setStyleSheet('')
        else:
            self.value.setStyleSheet('border-color: #c65353;')
        self.changed.emit()

    @property
    def rgb(self):
        if not re.fullmatch(r'#?[0-9a-fA-F]{6}', self.value.text().strip()):
            raise ValueError('글자 색을 6자리 색상 코드로 입력해 주세요. 예: #2463E8')
        return self.color.redF(), self.color.greenF(), self.color.blueF()

    def _choose(self):
        selected = QColorDialog.getColor(self.color, self, '글자 색')
        if selected.isValid():
            self.value.setText(selected.name().upper())


class NumberFontSize(QComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.addItems(['6', '8', '9', '10', '11', '12', '14', '16', '18', '20', '24', '28', '36', '48', '72'])
        self.setCurrentText('11')
        self.setFixedWidth(73)
        self.setAccessibleName('글자 크기 (pt)')
        self.setToolTip('글자 크기 (pt)')

    def value(self):
        try:
            size = float(self.currentText())
            if not 4 <= size <= 144:
                raise ValueError
            return size
        except ValueError:
            raise ValueError('글자 크기는 4~144pt 범위로 입력해 주세요.') from None

    def setValue(self, value):
        self.setCurrentText(str(value))

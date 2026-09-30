"""Choosing a stand-in when a PDF font cannot be used for editing, like CorelDRAW's font matching."""
import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFontComboBox, QFormLayout,
                               QLabel, QVBoxLayout)

OTHER_FONT = '다른 글꼴 직접 선택…'
PREVIEW_STYLE = 'background: white; border: 1px solid #dfe3e9; border-radius: 8px; padding: 10px; font-size: 16pt;'


class FontSubstituteDialog(QDialog):
    """Original font, a ranked stand-in with its reason, a preview and whether to remember it."""

    def __init__(self, original, text, candidates, missing='', parent=None):
        super().__init__(parent)
        self.text = text
        self.candidates = candidates
        self.choice = None
        self.preview_id = -1
        shown = re.sub(r'^[A-Z]{6}\+', '', original)
        self.setWindowTitle('글꼴 대체')
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(12)
        self.headline = QLabel(f'「{shown}」 글꼴로 이 텍스트를 편집할 수 없습니다.')
        self.headline.setWordWrap(True)
        self.headline.setStyleSheet('font-weight: 600;')
        layout.addWidget(self.headline)
        reason = (f'PDF 글꼴에 없는 글자: {missing[:30]}' if missing else
                  '원본 내장 글꼴이나 같은 이름의 PC 글꼴이 없어, 이름과 종류가 비슷한 글꼴을 골랐습니다.')
        detail = QLabel(reason)
        detail.setWordWrap(True)
        detail.setObjectName('muted')
        layout.addWidget(detail)
        form = QFormLayout()
        form.setSpacing(10)
        missing_label = QLabel(shown)
        missing_label.setStyleSheet('color: #a64b10;')
        missing_label.setToolTip('이 PC에서 쓸 수 없는 원본 글꼴')
        form.addRow('원본 글꼴', missing_label)
        self.fonts = QComboBox()
        self.fonts.setAccessibleName('대체 글꼴')
        for index, (font, why) in enumerate(candidates):
            self.fonts.addItem(f'{font.label}  ·  {why}', index)
        self.fonts.addItem(OTHER_FONT, -1)
        form.addRow('대체 글꼴', self.fonts)
        self.other = QFontComboBox()
        self.other.setAccessibleName('직접 고른 글꼴')
        # Like CorelDRAW's same-code-page filter: Korean text lists Korean fonts.
        if re.search('[가-힣]', text):
            self.other.setWritingSystem(QFontDatabase.WritingSystem.Korean)
        self.other.hide()
        form.addRow('', self.other)
        layout.addLayout(form)
        self.preview = QLabel()
        self.preview.setAccessibleName('대체 글꼴 미리보기')
        self.preview.setMinimumHeight(64)
        self.preview.setWordWrap(True)
        self.preview.setStyleSheet(PREVIEW_STYLE)
        layout.addWidget(self.preview)
        self.warning = QLabel()
        self.warning.setStyleSheet('color: #a64b10;')
        self.warning.hide()
        layout.addWidget(self.warning)
        self.remember = QCheckBox('이 문서의 같은 글꼴에 이 선택 적용')
        self.save_exception = QCheckBox('다른 문서에서도 이 글꼴 대신 사용')
        self.save_exception.setToolTip('글꼴 대체 예외로 저장합니다. 같은 원본 글꼴이 없을 때 이 글꼴을 먼저 추천합니다.')
        layout.addWidget(self.remember)
        layout.addWidget(self.save_exception)
        self.buttons = QDialogButtonBox()
        self.replace = self.buttons.addButton('대체 글꼴로 편집', QDialogButtonBox.ButtonRole.AcceptRole)
        self.cancel = self.buttons.addButton('편집 취소', QDialogButtonBox.ButtonRole.RejectRole)
        # Enter does not change the document's font without a look at the preview.
        self.replace.setAutoDefault(False)
        self.cancel.setDefault(True)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.fonts.currentIndexChanged.connect(self._changed)
        self.other.currentFontChanged.connect(self._changed)
        self._changed()

    def _changed(self, *_):
        from .fonts import installed_font
        index = self.fonts.currentData()
        self.other.setVisible(index == -1)
        if index == -1:
            font = installed_font(self.other.currentFont().family())
            missing = font.missing(self.text) if font else None
            if font is None or missing:
                self.choice = None
                self.warning.setText('이 글꼴에 없는 글자가 있습니다: ' + missing[:30] if missing else
                                     '이 글꼴 파일을 찾지 못했습니다. 다른 글꼴을 고르세요.')
            else:
                self.choice = font
        else:
            self.choice = self.candidates[index][0]
        self.warning.setVisible(self.choice is None)
        self.replace.setEnabled(self.choice is not None)
        self._preview()

    def _preview(self):
        if self.preview_id >= 0:
            QFontDatabase.removeApplicationFont(self.preview_id)
            self.preview_id = -1
        sample = ' '.join(self.text.split())[:80] or '가나다 ABC 123'
        self.preview.setText(sample)
        if self.choice is None:
            return
        try:
            self.preview_id = QFontDatabase.addApplicationFontFromData(self.choice.preview_data())
        except Exception:
            return
        families = QFontDatabase.applicationFontFamilies(self.preview_id)
        # The application style sheet sets every widget's font, so the preview names its own.
        self.preview.setStyleSheet(PREVIEW_STYLE + (f' font-family: "{families[0]}";' if families else ''))

    def done(self, result):
        if self.preview_id >= 0:
            QFontDatabase.removeApplicationFont(self.preview_id)
            self.preview_id = -1
        super().done(result)

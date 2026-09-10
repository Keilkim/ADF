"""A skippable first-run tour that points to the real toolbar controls."""
from PySide6.QtCore import QEvent, QPoint, QRectF, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout


STEPS = (
    (('open', 'save', 'save_as'), '문서를 열고, 저장하세요',
     '폴더 아이콘이나 Ctrl+O로 PDF를 여세요. 파일을 화면에 끌어 놓아도 됩니다.\n\nCtrl+S는 현재 파일에 저장하고, 겹친 디스크 아이콘은 다른 이름으로 저장합니다.'),
    (('pen', 'eraser'), '펜과 지우개를 바로 켜세요',
     '버튼 본체는 켜기·끄기, 오른쪽 아래 작은 화살표는 옵션입니다. 굵기와 지우개 크기는 미리 보며 고르세요.\n\n선을 그린 뒤 누른 채 잠시 멈추면 직선으로 정리됩니다. Ctrl+Z로 되돌릴 수 있어요.'),
    (('region_tool',), '글자도, 표와 그래프도 복사하세요',
     '글자를 드래그하면 파란색으로 선택됩니다. Ctrl+C로 복사하세요.\n\n표·그래프·그림은 캡처로 영역을 잡거나 Ctrl+Shift+C를 누르세요. 복사 완료 알림이 뜨면 Ctrl+V로 붙여넣습니다.'),
    (('split', 'extract', 'rotate'), '페이지를 원하는 순서로',
     'PDF를 열면 왼쪽에 페이지 미리보기가 나타납니다. 끌어서 순서를 바꾸고, Ctrl·Shift로 여러 장을 고르세요.\n\n상단에서 분할·추출·회전하고, 왼쪽의 + 버튼으로 페이지를 추가할 수 있습니다.'),
    (('snap', 'stamps'), '맞춰 놓고, 반복해서 찍으세요',
     '자석 모양의 스냅을 켜면 글·이미지·도장이 가까운 정렬 위치에 붙습니다. Alt를 누르면 잠시 해제됩니다.\n\n자주 쓰는 도장은 보관함에 등록해 두고 문서를 클릭할 때마다 찍으세요.'),
    (('compare', 'markdown'), '비교하고, 문서로 내보내세요',
     '비교 아이콘으로 두 PDF의 저장본을 나란히 비교합니다. 눈과 겹친 문서 아이콘은 OCR·Markdown 내보내기입니다.\n\n준비됐습니다. 이 안내는 도움말 → 기능 둘러보기에서 언제든 다시 볼 수 있어요.'),
)


class IntroDialog(QDialog):
    helpRequested = Signal()

    def __init__(self, parent=None, first_run=False):
        super().__init__(parent, Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
        self.window = parent
        self.index = 0
        self.highlights = []
        self.setWindowTitle('ADF 시작 안내' if first_run else 'ADF 기능 둘러보기')
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.panel = QFrame(self)
        self.panel.setObjectName('tourCard')
        self.panel.setFixedWidth(376)
        layout = QVBoxLayout(self.panel)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(14)
        top = QHBoxLayout()
        self.counter = QLabel()
        self.counter.setObjectName('tourCounter')
        top.addWidget(self.counter)
        top.addStretch()
        self.skip_button = QPushButton('건너뛰기')
        self.skip_button.setAutoDefault(False)
        self.skip_button.clicked.connect(self.reject)
        top.addWidget(self.skip_button)
        layout.addLayout(top)
        self.title = QLabel()
        self.title.setObjectName('tourTitle')
        self.title.setWordWrap(True)
        layout.addWidget(self.title)
        self.description = QLabel()
        self.description.setWordWrap(True)
        layout.addWidget(self.description)
        bottom = QHBoxLayout()
        self.help_button = QPushButton('사용 안내')
        self.help_button.setAutoDefault(False)
        self.help_button.clicked.connect(self.helpRequested.emit)
        bottom.addWidget(self.help_button)
        bottom.addStretch()
        self.back_button = QPushButton('이전')
        self.back_button.setAutoDefault(False)
        self.back_button.clicked.connect(lambda: self.show_step(self.index-1))
        bottom.addWidget(self.back_button)
        self.start_button = QPushButton('다음')
        self.start_button.setObjectName('tourNext')
        self.start_button.setDefault(True)
        self.start_button.clicked.connect(self.next_step)
        bottom.addWidget(self.start_button)
        layout.addLayout(bottom)
        self.setStyleSheet('''
            QFrame#tourCard { background: #fafafa; border: none; border-radius: 18px; }
            QLabel { background: transparent; color: #505054; font-size: 10pt; }
            QLabel#tourTitle { color: #252528; font-size: 15pt; font-weight: 600; }
            QLabel#tourCounter { color: #7a7a7e; font-size: 9pt; }
            QPushButton { color: #626268; background: transparent; border: none; border-radius: 8px; padding: 7px 10px; }
            QPushButton:hover, QPushButton:focus { background: #e5e5e8; }
            QPushButton:pressed { background: #ceced2; }
            QPushButton#tourNext { background: #dedee2; color: #252528; font-weight: 600; }
            QPushButton#tourNext:hover, QPushButton#tourNext:focus { background: #ceced2; }
        ''')
        self.window.installEventFilter(self)
        self.window.toolbar.installEventFilter(self)
        self.show_step(0)

    def show_step(self, index):
        self.index = max(0, min(index, len(STEPS)-1))
        _, title, description = STEPS[self.index]
        self.title.setText(title)
        self.description.setText(description)
        self.counter.setText(f'ADF 둘러보기  ·  {self.index+1} / {len(STEPS)}')
        self.setAccessibleName(f'{self.index+1} / {len(STEPS)} · {title}')
        self.back_button.setVisible(self.index > 0)
        self.start_button.setText('시작하기' if self.index == len(STEPS)-1 else '다음')
        self.place()
        self.start_button.setFocus()

    def next_step(self):
        if self.index == len(STEPS)-1:
            self.accept()
        else:
            self.show_step(self.index+1)

    def place(self):
        self.setGeometry(self.window.rect().translated(self.window.mapToGlobal(QPoint())))
        toolbar = self.window.toolbar
        targets = {self.window.actions[key] for key in STEPS[self.index][0]}
        self.highlights = [QRectF(button.rect().translated(button.mapToGlobal(QPoint())-self.pos())).adjusted(-4, -4, 4, 4)
                           for button in toolbar.file_items+toolbar.edit_items+toolbar.tail_items
                           if hasattr(button, 'defaultAction') and button.defaultAction() in targets and button.isVisible()]
        self.panel.setFixedWidth(min(376, max(260, self.width()-32)))
        text_width = self.panel.width()-44
        self.title.setFixedHeight(self.title.heightForWidth(text_width))
        self.description.setFixedHeight(self.description.heightForWidth(text_width))
        self.panel.layout().activate()
        self.panel.adjustSize()
        area = self.highlights[0] if self.highlights else QRectF(self.width()/2, 80, 0, 0)
        x = max(16, min(round(area.left()), self.width()-self.panel.width()-16))
        y = max((r.bottom() for r in self.highlights), default=80)+18
        y = max(16, min(round(y), self.height()-self.panel.height()-16))
        self.panel.move(x, y)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        shade = QPainterPath()
        shade.addRect(QRectF(self.rect()))
        for rect in self.highlights:
            hole = QPainterPath()
            hole.addRoundedRect(rect, 10, 10)
            shade = shade.subtracted(hole)
        painter.fillPath(shade, QColor(30, 30, 34, 105))

    def showEvent(self, event):
        super().showEvent(event)
        self.place()

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Move, QEvent.Type.LayoutRequest) and self.isVisible():
            QTimer.singleShot(0, self.place)
        return super().eventFilter(watched, event)

    def done(self, result):
        self.window.removeEventFilter(self)
        self.window.toolbar.removeEventFilter(self)
        super().done(result)

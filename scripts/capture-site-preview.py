"""Capture a real ADF window with synthetic documents and an isolated stamp store."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import pymupdf
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from adf.app import MainWindow
from adf.stamps import StampLibrary
from adf.theme import apply_theme


def capture():
    app = QApplication([])
    for filename in ('segoeui.ttf', 'malgun.ttf'):
        QFontDatabase.addApplicationFont(str(Path('C:/Windows/Fonts') / filename))
    apply_theme(app)
    with tempfile.TemporaryDirectory(prefix='adf-site-preview-') as temp:
        folder = Path(temp)
        path = folder / '분기 운영 계획.pdf'
        with pymupdf.open() as doc:
            for index in range(3):
                page = doc.new_page(width=595, height=842)
                page.insert_font(fontname='ko', fontfile='C:/Windows/Fonts/malgun.ttf')
                def label(x, y, value, size=11, color=(.2, .22, .23)):
                    page.insert_text((x, y), value, fontname='ko', fontsize=size, color=color)
                label(50, 58, 'ADF  /  WORKSPACE', 9, (.5, .51, .48))
                label(50, 112, ['분기 운영 계획', '프로젝트 일정', '검토 및 승인'][index], 25)
                label(50, 140, '2026년 4분기  ·  예시 문서', 10, (.5, .51, .48))
                page.draw_line((50, 165), (545, 165), color=(.83, .84, .81), width=.6)
                label(50, 208, '01   목표와 방향', 14)
                label(50, 237, '일상의 문서 작업을 더 편안하고 명확하게 만듭니다.')
                label(50, 257, '작은 개선을 모아, 중요한 일에 집중할 수 있도록 합니다.')
                label(50, 304, '02   이번 분기 진행 항목', 14)
                rows = [('진행 항목', '일정', '상태'), ('문서 정리 및 검토', '10월', '진행 중'), ('업무 흐름 개선', '11월', '예정'), ('결과 공유 및 회고', '12월', '예정')]
                for row, values in enumerate(rows):
                    y = 325 + row * 41
                    page.draw_rect((50, y, 545, y + 39), color=None, fill=(.94, .945, .925) if row == 0 else (.978, .979, .974))
                    for x, value in zip((65, 360, 450), values):
                        label(x, y + 25, value, 10)
                label(50, 540, '03   검토 메모', 14)
                label(50, 570, '변경 사항을 확인하고 필요한 의견을 기록해 주세요.')
                label(50, 590, '이 문서는 화면 안내를 위해 만든 가상의 예시입니다.')
                label(400, 665, '검토 확인', 10)
                label(50, 796, f'ADF · 예시 문서                                      {index + 1} / 3', 9, (.5, .51, .48))
            doc.save(path)
        with pymupdf.open() as seal:
            page = seal.new_page(width=120, height=120)
            page.insert_font(fontname='ko', fontfile='C:/Windows/Fonts/malgun.ttf')
            page.draw_circle((60, 60), 46, color=(.65, .22, .20), width=2)
            page.draw_circle((60, 60), 41, color=(.65, .22, .20), width=.6)
            page.insert_text((35, 58), '예 시', fontname='ko', fontsize=17, color=(.65, .22, .20))
            page.insert_text((38, 78), '검토용', fontname='ko', fontsize=12, color=(.65, .22, .20))
            stamp_image = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=True).tobytes('png')
        window = MainWindow(smoke=True)
        window.settings = QSettings(str(folder / 'settings.ini'), QSettings.Format.IniFormat)
        window.stamp_library = StampLibrary(folder / 'stamps.sqlite3')
        window.stamp_library.add('검토 확인 · 예시', stamp_image, 20)
        window.resize(1440, 920)
        window.show()
        window.open_path(path)
        window.set_view_mode('single')
        window.show_stamps()
        QTest.qWait(150)
        window.document.add_image(0, pymupdf.Rect(445, 620, 510, 685), stamp_image)
        # Refresh from the document model through the application's existing path.
        window.view.load(window.document)
        QTest.qWait(250)
        target = ROOT / 'site/assets/workspace.png'
        target.parent.mkdir(parents=True, exist_ok=True)
        window.grab().save(str(target))
        with patch.object(window, 'maybe_save', return_value=True):
            window.close()
    print(target)


if __name__ == '__main__':
    capture()

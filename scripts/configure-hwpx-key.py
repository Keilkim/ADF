"""Developer-only setup. API keys are entered privately, never as arguments."""
import getpass
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def gui():
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox,
        QFormLayout, QLabel, QLineEdit, QMessageBox, QVBoxLayout)
    from adf.hwpx_credentials import create_bundle
    from adf.theme import apply_theme
    app = QApplication(sys.argv[:1])
    app.setFont(QFont('Malgun Gothic' if sys.platform == 'win32' else 'Apple SD Gothic Neo', 10))
    apply_theme(app)
    dialog = QDialog()
    dialog.setWindowTitle('XDF 개발자용 · Gemini 배포 키 설정')
    dialog.setMinimumWidth(540)
    outer = QVBoxLayout(dialog)
    outer.setContentsMargins(24, 22, 24, 20)
    title = QLabel('지인 배포용 Gemini 키를 설정합니다')
    title.setObjectName('introTitle')
    outer.addWidget(title)
    text = QLabel('입력한 키는 AES-GCM으로 암호화해 저장합니다.\n'
                  '배포 앱을 분석하는 사람의 키 추출을 막지는 못합니다.\n'
                  '배포 전 Gemini 전용 키와 월 지출 한도를 Google에서 설정하세요.')
    text.setWordWrap(True)
    outer.addWidget(text)
    form = QFormLayout()
    key = QLineEdit()
    key.setEchoMode(QLineEdit.EchoMode.Password)
    key.setStyleSheet('QLineEdit { lineedit-password-character: 42; lineedit-password-mask-delay: 0; }')
    confirmation = QLineEdit()
    confirmation.setEchoMode(QLineEdit.EchoMode.Password)
    confirmation.setStyleSheet('QLineEdit { lineedit-password-character: 42; lineedit-password-mask-delay: 0; }')
    form.addRow('Gemini API 키', key)
    form.addRow('키 다시 입력', confirmation)
    outer.addLayout(form)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
    buttons.button(QDialogButtonBox.StandardButton.Save).setText('암호화해서 저장')
    buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('취소')
    def save():
        if key.text() != confirmation.text():
            QMessageBox.warning(dialog, '키 설정', '두 입력이 다릅니다. 다시 입력해 주세요.')
            return
        try:
            create_bundle(ROOT / '.tools' / 'hwpx-credentials', key.text())
        except Exception:
            QMessageBox.warning(dialog, '키 설정', '키를 저장하지 못했습니다. 입력값과 Windows 사용자 권한을 확인해 주세요.')
            return
        key.clear()
        confirmation.clear()
        QMessageBox.information(dialog, '키 설정 완료', '암호화된 설정을 저장했습니다.\n다음 실행부터 Gemini 연결에 사용합니다.')
        dialog.accept()
    buttons.accepted.connect(save)
    buttons.rejected.connect(dialog.reject)
    outer.addWidget(buttons)
    def reveal():
        dialog.showNormal()
        dialog.raise_()
        dialog.activateWindow()
    # A hidden console launch can suppress the first native ShowWindow call.
    # Reveal the interactive Qt window after entering its event loop.
    QTimer.singleShot(0, reveal)
    return 0 if dialog.exec() else 1


def main():
    if sys.argv[1:] == ['--gui']:
        return gui()
    from adf.hwpx_credentials import create_bundle
    print('XDF PDF → HWPX: 배포용 Gemini 키 설정')
    print('AES-GCM 암호화로 평문 노출을 줄입니다. 앱을 분석하면 키를 추출할 수 있습니다.')
    print('배포 전 Gemini 전용 키와 AI Studio 월 지출 한도를 설정하세요.')
    if not sys.stdin.isatty():
        print('키를 숨겨 입력할 수 있는 PowerShell 터미널에서 이 도구를 직접 실행해 주세요.')
        return 1
    try:
        key = getpass.getpass('Gemini API 키 (화면에 표시되지 않음): ')
        confirmation = getpass.getpass('키 다시 입력: ')
        if key != confirmation:
            print('두 입력이 다릅니다. 저장하지 않았습니다.')
            return 1
        create_bundle(ROOT / '.tools' / 'hwpx-credentials', key)
    except (EOFError, KeyboardInterrupt):
        print('\n설정을 취소했습니다.')
        return 1
    except Exception:
        print('키를 저장하지 못했습니다. 의존성과 Windows 사용자 권한을 확인해 주세요.')
        return 1
    print('암호화된 설정을 Git에서 제외된 .tools/hwpx-credentials에 저장했습니다.')
    print('XDF 파일 → 한글(HWPX)로 변환 → 연결 확인으로 접근 권한을 확인하세요.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

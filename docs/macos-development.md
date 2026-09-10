# Mac에서 개발 이어가기

2026-09-10 기준 인수인계입니다. Windows 개발 소스와 macOS 빌드 스크립트는 `main`에 있습니다. **실제 Mac에서의 실행·빌드·배포 검증은 아직 수행하지 않았습니다.** 현재 공개된 0.3.24 설치 파일은 Windows용입니다.

## 저장소와 개발 환경

이미 받은 저장소에서는 작업 내용을 확인한 뒤 최신 `main`을 가져옵니다.

```bash
git status
git switch main
git pull --ff-only origin main
```

처음 받는 경우:

```bash
git clone https://github.com/Keilkim/ADF.git
cd ADF
```

Mac에 Python 3.12를 준비하고 저장소 루트에서 실행합니다. Windows의 가상 환경은 옮기지 않고 Mac에서 새로 만듭니다.

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python scripts/prepare-ocr.py
.venv/bin/python main.py
```

의존성 설치와 OCR 모델 준비는 인터넷을 사용합니다. 문서 편집·도장 보관·준비된 모델을 사용하는 OCR은 로컬에서 처리합니다. `.venv/`, `.tools/`, `build/`, `dist/`, `release/`는 Git에 포함되지 않아 새 Mac에서 준비해야 합니다.

Apple Silicon과 Intel은 따로 확인합니다. 현재 빌드 설정은 실행 중인 Python 아키텍처를 사용하며 Universal 앱 생성 설정은 없습니다. 개발 기록에 Mac 모델, macOS 버전과 아래 결과를 남기세요.

```bash
uname -m
.venv/bin/python -c 'import platform; print(platform.machine(), platform.python_version())'
```

## 검사와 앱 번들 빌드

OCR 모델을 준비한 뒤 소스 검사를 실행합니다. Windows 전용 검사의 건너뛰기와 Mac에서 실제 발생한 실패를 구분해 기록합니다.

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
```

화면 조작은 일반 실행 환경에서도 직접 확인해야 합니다. 소스 실행을 확인한 뒤 앱 번들을 빌드합니다.

```bash
bash scripts/build-macos.sh
```

스크립트는 아이콘·OCR 모델·라이선스·대응 소스를 준비하고 PyInstaller와 `hdiutil`로 `dist/ADF.app`, `release/ADF-0.3.24-macOS.dmg` 및 소스·해시 자료를 만듭니다. 공식 배포용 서명과 공증 단계는 구현되지 않았습니다. 버전을 올릴 때 DMG 이름과 번들 버전도 함께 갱신해야 합니다.

빌드 후 Mac에서 앱 실행, PDF 저장 후 다시 열기, 앱 번들에 포함된 OCR·도움말·라이선스·소스 자료를 확인하세요. 현재 자동 검증 도구 중 Windows 설치 폴더 구조를 전제로 하는 도구는 Mac 번들 검증에 맞게 수정해야 합니다. `scripts/build-shell.ps1`, `scripts/test-installer.ps1`, `native-shell/`은 Windows 탐색기·설치 프로그램용입니다.

## Mac에서 우선 확인할 부분

1. **키보드와 메뉴:** `adf/app.py`에는 Qt 표준 단축키와 직접 지정한 `Ctrl+…` 단축키·안내 문구가 섞여 있습니다. 저장·다른 이름 저장·닫기·복사·캡처·실행 취소를 Mac의 Command 키와 메뉴 표시 기준으로 확인합니다. `adf/viewer.py`의 확대 조작과 텍스트 입력 완료 키도 확인합니다.
2. **Finder에서 PDF 열기:** `ADF.spec`의 PDF 문서 유형 선언과 `adf/app.py`의 `FileOpen` 처리 코드가 있습니다. 앱이 꺼진 상태와 이미 열린 상태에서 PDF 더블클릭·여러 파일 열기·드래그를 실제 번들로 검증합니다. Windows 탐색기 확장은 Finder 기능으로 제공되지 않습니다.
3. **화면과 입력:** Retina 배율, 트랙패드 스크롤·확대, 전체 화면, 한글 입력·글꼴, 인쇄를 확인합니다. 펜·지우개 토글, 옵션 바깥 클릭, 굵기 미리보기, 누른 채 직선 보정, 선택 강조·움직이는 파선·클립보드 알림이 유지되어야 합니다.
4. **로컬 자료와 작업 취소:** 도장 등록 후 앱을 재시작해 보관 상태를 확인합니다. 저장 위치는 Qt의 `QStandardPaths.AppLocalDataLocation`을 사용합니다. OCR·비교·문서 열기 작업 취소 후 자식 프로세스와 임시 파일 정리도 확인합니다. `adf/windows_process.py`는 Windows 전용 종료 처리이며 Mac 경로와 구분되어 있습니다.
5. **패키징:** 현재 고정된 의존성이 사용하는 Mac 아키텍처에서 설치되는지 확인하고, 바꾸면 고지와 대응 소스도 갱신합니다. Apple Silicon·Intel 지원 범위, 번들 식별자, 버전, 서명·공증과 설치한 앱의 검증 결과를 정한 뒤 공개합니다.

## 현재 검증과 배포 상태

- Windows 소스 기준 커밋: `4e4dcebea34f1108ef40ad57c459d68fc70d7d4b`. 작업 취소 시 Windows 자식 프로세스의 파일 핸들이 해제될 때까지 기다리는 수정까지 포함합니다.
- [GitHub 검토 빌드](https://github.com/Keilkim/ADF/actions/runs/34464937872): 소스 검사 263개·세부 검사 119개, 네이티브 구성 요소 검사 14,313개 assertion, 패키지 정적 검사가 통과했고 검토용 아티팩트가 생성됐습니다.
- 같은 실행의 **실제 Windows 탐색기 메뉴 조합 검사는 실패**했습니다. 해당 단계가 `continue-on-error`이므로 워크플로 전체의 성공 표시만으로 모든 검사가 통과했다고 판단하면 안 됩니다. Windows 10/11 설치·실행 검증도 남아 있습니다.
- SignPath 무료 오픈소스 지원 신청은 CAPTCHA 검증 오류로 **접수 확인이 되지 않았습니다**. 지원 승인과 실제 서명 연동도 아직입니다. 신청자의 개인정보와 브라우저 기록은 공개 저장소에 넣지 않습니다. 최신 공개 정책은 [CODE_SIGNING.md](../CODE_SIGNING.md)에서 확인합니다.
- 공식 사이트의 Mac 다운로드 버튼은 준비 중입니다. 검증된 Mac 배포 파일이 생기면 [site/README.md](../site/README.md)의 배포 방법에 따라 다운로드 링크를 연결합니다.
- 기존 Windows 0.3.24 릴리즈 파일은 유지합니다. 새 배포는 새 버전으로 준비하고 각 운영체제의 실행 파일·대응 소스·해시와 검증 범위를 함께 제공합니다.

개인 PDF, 도장 데이터베이스, 인증서와 토큰은 Git에 올리지 않습니다. Mac 작업 후에도 공유 PDF 편집 기능의 Windows 동작이 유지되는지 확인합니다.

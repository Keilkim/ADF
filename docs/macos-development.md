# Mac에서 개발 이어가기

2026-09-10 기준 인수인계입니다. Apple Silicon(arm64) Mac의 macOS 26.3.1에서 소스 검사, 앱 번들·DMG 빌드, 패키지 정적 검사와 설치 앱 검사를 수행했습니다. 결과와 남은 항목은 [현재 검증과 배포 상태](#현재-검증과-배포-상태)에 있습니다. 현재 공개된 0.3.24 설치 파일은 Windows용입니다.

## 지원 범위

- **Apple Silicon Mac, macOS 15 이상.** PySide6 6.11.2 wheel의 Apple Silicon용 모듈(`QtCore.abi3.so` 등)이 macOS 15.0 기준으로 빌드되어 있습니다. `ADF.spec`은 `LSMinimumSystemVersion`을 15.0으로 두고, `scripts/verify-release.py`가 앱에 든 모든 실행 파일의 최소 버전이 이 값을 넘지 않는지 검사합니다.
- **Intel Mac은 지원하지 않습니다.** OCR에 쓰는 ONNX Runtime 1.29.0이 Intel Mac용 wheel을 제공하지 않습니다.
- Windows 탐색기 확장(`native-shell/`)과 설치 프로그램(`installer/adf.iss`, `scripts/*.ps1`)은 Windows 전용입니다. Mac에서는 Finder의 **다음으로 열기**나 끌어 놓기로 PDF를 엽니다.

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

Python 3.12를 준비합니다. Homebrew의 Python은 설치한 macOS 기준으로 빌드되어 앱에 넣으면 이전 macOS에서 실행되지 않을 수 있습니다. python.org 설치 파일이나 [uv](https://docs.astral.sh/uv/)가 받는 Python을 사용하세요. 2026-09-10 빌드에는 uv의 CPython 3.12.14(macOS 11 대상)를 사용했습니다. Windows의 가상 환경은 옮기지 않고 Mac에서 새로 만듭니다.

```bash
uv python install 3.12
"$(uv python find 3.12)" -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python scripts/build-opencv-macos.py
.venv/bin/python scripts/prepare-ocr.py
.venv/bin/python main.py
```

`build-opencv-macos.py`는 PyPI에서 설치된 OpenCV를 영상 기능 없는 빌드로 바꿉니다([OpenCV](#opencv) 참고). 의존성·OCR 모델·원본 소스 준비는 인터넷을 사용합니다. `.venv/`, `.tools/`, `build/`, `dist/`, `release/`는 Git에 포함되지 않아 새 Mac에서 준비해야 합니다.

## 검사

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
```

Windows 전용 검사 3개는 건너뜁니다. 화면 조작은 일반 실행 환경에서도 직접 확인해야 합니다.

## 앱 번들·DMG 빌드

```bash
bash scripts/build-macos.sh
```

의존성 설치, OpenCV 빌드, 아이콘·OCR 모델·고지·대응 소스 준비, PyInstaller 앱 번들, DMG, 해시와 패키지 정적 검사까지 실행해 `dist/ADF.app`과 `release/ADF-<버전>-macOS.dmg`를 만듭니다. 함께 배포할 대응 소스·외부 라이브러리 소스·해시는 `release/ADF-Source-<버전>-macOS.zip`처럼 `-macOS`가 붙은 이름으로 만들어 Windows 파일과 구분합니다. `python3.12`가 없으면 `PYTHON=/경로/python3.12`로 가상 환경을 만들 Python을 지정합니다. 버전은 `adf/__init__.py`에서 읽습니다.

### 서명과 공증

환경 변수가 없으면 ad-hoc 서명 빌드입니다. 빌드한 Mac에서는 실행되지만 다른 Mac에서는 Gatekeeper가 막으므로 배포하지 않습니다. 배포용은 Developer ID Application 인증서로 서명하고 Apple 공증을 받습니다.

```bash
# 처음 한 번: 앱 암호는 키체인에만 저장됩니다.
xcrun notarytool store-credentials "ADF-notary" --apple-id "<Apple ID>" --team-id "<팀 ID>"

ADF_CODESIGN_IDENTITY="Developer ID Application: <이름> (<팀 ID>)" \
ADF_NOTARY_PROFILE=ADF-notary bash scripts/build-macos.sh
```

PyInstaller가 앱의 모든 실행 파일과 라이브러리를 hardened runtime으로 서명하고 `installer/adf.entitlements`를 적용합니다. 이 권한은 LGPL 라이브러리를 사용자가 교체할 수 있도록 라이브러리 서명자 일치 검사만 끕니다. 스크립트는 앱과 DMG를 각각 공증 제출하고 결과를 붙여(staple) 인터넷 없이 처음 실행해도 Gatekeeper가 확인할 수 있게 합니다. 공증 기록은 `.tools/verification/notarization-*.json`에 남습니다. 인증서, 앱 암호와 공증 기록은 저장소에 넣지 않습니다.

### OpenCV

PyPI의 macOS용 opencv-python 5.0.0.93 wheel(일반판·headless 모두)은 Homebrew FFmpeg와 x264·x265 등 약 100개 라이브러리(78MB)를 함께 담고 있으며 `cv2`가 이를 직접 링크합니다. Windows처럼 코덱 파일만 뺄 수 없고 wheel 고지에도 해당 라이브러리가 없습니다. ADF의 OCR은 이미지 처리만 사용하므로 `scripts/build-opencv-macos.py`가 ADF-ThirdParty-Sources ZIP에 들어 있는 같은 원본 소스를 수정 없이 컴파일합니다.

- core·imgproc·geometry·imgcodecs와, opencv-python의 형식 스텁 생성에 필요한 features·calib만 빌드합니다.
- videoio·video·highgui, FFmpeg·AVFoundation, 외부 이미지 코덱(OpenEXR·AVIF·JPEG XL)을 끄고, 설정 중 내려받는 KleidiCV·Unifont·IPP를 쓰지 않습니다. 내려받은 파일이 생기면 빌드를 중단합니다.
- Homebrew와 `/usr/local`을 검색하지 않고, 결과가 시스템 라이브러리만 링크하는지 검사합니다.
- CMake 옵션과 결과 SHA-256은 `.tools/opencv-macos/build.json`과 앱의 `build-manifest.json` 중 `opencv_build`에 기록합니다. 같은 옵션의 wheel이 있으면 다시 빌드하지 않습니다.

### 고지

`scripts/collect-licenses.py`는 Windows에서 저장소의 `LICENSES`를 갱신하고, Mac에서는 macOS wheel의 고지를 `build/licenses`에 모아 앱에 넣습니다. Mac 빌드는 저장소의 `LICENSES`(Windows 기준)를 바꾸지 않습니다. Windows 전용인 colorama, NativeShell, Inno Setup 고지는 Mac 앱에 넣지 않습니다.

## 빌드 후 확인

```bash
.venv/bin/python scripts/test-frozen-worker.py dist/ADF.app/Contents/MacOS/ADF \
  --report .tools/verification/frozen-worker-macos.json
QT_QPA_PLATFORM=offscreen dist/ADF.app/Contents/MacOS/ADF \
  --smoke-test "$PWD/.tools/verification/smoke-macos/smoke.json"
```

`--smoke-test`는 설치된 앱 안에서 문서 편집·도장·비교·인쇄·전체 화면·도움말·소스·OCR을 확인하고 같은 폴더에 화면을 캡처합니다. 마우스 커서를 움직이므로 offscreen으로 실행합니다. `scripts/test-frozen-tools.py`와 `scripts/test-installer.ps1`은 Windows 탐색기·설치 프로그램용입니다.

## Mac에서 우선 확인할 부분

1. **화면 안내의 Ctrl 표기:** 단축키는 Qt가 Mac에서 ⌘로 바꾸지만 기능 둘러보기, 복사 알림, 상태 표시줄, 대화상자와 사용안내의 "Ctrl+…" 문구는 그대로 표시됩니다(`adf/intro_widgets.py`, `adf/notice_widgets.py`, `adf/app.py`, `adf/dialogs.py`, `docs/사용안내.html`).
2. **Finder에서 PDF 열기:** `ADF.spec`의 PDF 문서 유형 선언과 `adf/app.py`의 `FileOpen` 처리가 있습니다. 앱이 꺼진 상태와 열린 상태에서 PDF 더블클릭·여러 파일 열기·끌어 놓기를 설치한 앱으로 확인합니다.
3. **화면과 입력:** Retina 배율, 트랙패드 스크롤·확대, 한글 입력, 실제 인쇄 대화상자를 확인합니다. 스모크 검사는 offscreen이라 이 부분을 대신하지 않습니다.
4. **PDF 글꼴:** Apple SD Gothic Neo처럼 CID-keyed CFF로 내장된 글꼴은 `adf/fonts.py`가 원본 글꼴로 재사용하지 않아(의도된 제한) 편집할 때 대체 글꼴 확인 창이 나타납니다. 그래서 시작 안내 PDF는 Mac에서 TrueType인 AppleGothic을 넣습니다. 또 MuPDF가 텍스트의 글꼴 이름을 구분 접두어를 포함해 31바이트로 잘라(`WVUWMG+Apple SD Gothic Neo Regu`) 긴 이름의 글꼴은 같은 PC 글꼴이 있어도 찾지 못합니다. Windows에서도 이름이 긴 글꼴이면 같은 확인 창이 나타납니다.
5. **로컬 자료:** 도장 보관함은 Qt의 `AppLocalDataLocation`(`~/Library/Application Support/ADF/ADF`)에 저장합니다. 앱을 다시 실행해 보관 상태를 확인합니다.

## 현재 검증과 배포 상태

2026-09-10, macOS 26.3.1 Apple Silicon Mac, Python 3.12.14 기준입니다.

- 소스 검사: 260개 통과, 3개 건너뜀(Windows 전용), 세부 검사 112개 통과. Windows 글꼴을 전제로 멈추거나 실패하던 검사 2개(`test_inline_click_type_ime_font_and_final_save_on_lightweight_pdf`, `test_real_offline_korean_scan_layout_table_and_images`)는 각 운영체제의 한글 시스템 글꼴을 쓰도록 고쳤습니다.
- 서명·공증: `Developer ID Application: SEONGHUN KIM (TZQ9JL6R7R)`으로 hardened runtime과 보안 타임스탬프를 적용해 서명하고 `codesign --verify --deep --strict`를 통과했습니다. 빌드한 Mac의 키체인에 공증 프로필 `ADF-notary`를 등록했습니다. 버전별 공증 결과와 Gatekeeper 판정은 `release/배포상태-<버전>-macOS.md`에 기록합니다.
- 스모크 검사: 서명한 설치 앱(hardened runtime)에서 87개 항목 모두 통과했습니다. 앱 안의 소스 열람과 설치된 소스 확인, OCR(표 1개·그림 2개 인식), 페이지 번호 삭제를 포함합니다.
- 설치 앱 작업 검사(`test-frozen-worker.py`): 서명한 앱에서 병합·분할·추출·압축, 덮어쓰기 거부, 원본 보존 통과.
- 패키지 정적 검사(`verify-release.py`): 소스 571개와 앱의 코드 모듈 39개·자원 455개 일치, OCR 모델 5개, 외부 원본 소스 31개, 영상 코덱 없음, 끊어진 링크 없음, 실행 파일 최소 macOS 15.0.
- `ADF.spec`이 제외한 Qt 프레임워크(QtQml·QtQuick·QtPdf·가상 키보드)를 가리키는 링크가 남아 서명 검증이 실패하던 문제는 링크 대상까지 걸러 해결했습니다. Windows 빌드에도 같은 필터가 적용되므로 다음 Windows 빌드에서 결과를 확인합니다.
- 번들 식별자는 `io.github.keilkim.adf`입니다. 0.3.25 첫 공개 전에 임시 값 `local.adf.pdf`를 대체했습니다. 바꾸면 Finder 연결과 macOS 권한 설정이 새 앱으로 취급되므로 이후에는 유지합니다.
- Mac 첫 배포는 0.3.25로 준비합니다. 기존 0.3.24 Windows 릴리즈 파일은 유지하고, Mac 배포 파일도 대응 소스·해시·검증 범위와 함께 제공합니다. 변경과 검증 범위는 [0.3.25 검증 기록](검증-0.3.25.md)에 있습니다.
- 공식 사이트의 Mac 다운로드 버튼은 검증된 배포 파일이 게시되면 [site/README.md](../site/README.md)의 방법으로 연결합니다.
- 확인하지 못한 항목: Finder 연결, 실제 화면 조작(Retina·트랙패드·한글 입력·인쇄), macOS 15 실기기 실행, 다른 Mac에서의 설치.

Windows 쪽 기준은 다음과 같습니다.

- Windows 소스 기준 커밋: `4e4dcebea34f1108ef40ad57c459d68fc70d7d4b`. 작업 취소 시 Windows 자식 프로세스의 파일 핸들이 해제될 때까지 기다리는 수정까지 포함합니다.
- [GitHub 검토 빌드](https://github.com/Keilkim/ADF/actions/runs/34464937872): 소스 검사 263개·세부 검사 119개, 네이티브 구성 요소 검사 14,313개 assertion, 패키지 정적 검사가 통과했고 검토용 아티팩트가 생성됐습니다.
- 같은 실행의 **실제 Windows 탐색기 메뉴 조합 검사는 실패**했습니다. 해당 단계가 `continue-on-error`이므로 워크플로 전체의 성공 표시만으로 모든 검사가 통과했다고 판단하면 안 됩니다. Windows 10/11 설치·실행 검증도 남아 있습니다.
- SignPath 무료 오픈소스 지원 신청은 2026년 9월 10일 공식 신청 폼에서 **접수 완료를 확인했으며 심사 대기 중**입니다. 최신 공개 정책은 [CODE_SIGNING.md](../CODE_SIGNING.md)에서 확인합니다.

개인 PDF, 도장 데이터베이스, 인증서와 토큰은 Git에 올리지 않습니다. Mac 작업 후에도 공유 PDF 편집 기능의 Windows 동작이 유지되는지 확인합니다.

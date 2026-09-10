# ADF 오픈소스 고지

이 구현에서 새로 작성한 ADF 프로그램 코드는 **GNU AGPL version 3 or later** 조건으로 제공합니다. 이 라이선스에 따라 프로그램을 사용·수정·재배포할 수 있으며, 프로그램은 보증 없이 제공됩니다. [AGPL 원문 읽기](AGPL-3.0.txt).

**직원에게는 ADF-Setup 설치 EXE 하나를 전달하면 됩니다.** 실행 구성 요소·사용안내·라이선스·소스·OCR 모델이 모두 포함됩니다. 소스는 앱 안에서 읽거나 선택한 파일을 저장할 수 있으며 압축 프로그램을 자동으로 열지 않습니다.

**앱의 도움말 → 사용안내 / 오픈소스 라이선스 / 소스코드**에서 안내와 원문을 오프라인으로 확인할 수 있습니다. 기존 사용자가 제공한 `spec.md`와 각 외부 구성 요소에는 해당 권리자의 조건이 적용됩니다.

설치 시 확인하는 **무단 배포 금지**는 라이선스 조건을 위반한 배포를 뜻합니다. 이 확인 절차는 오픈소스 라이선스가 부여한 사용·수정·재배포 권리에 추가 제한을 부과하지 않습니다. 재배포 시에는 각 라이선스가 요구하는 고지·원문·대응 소스 제공 등의 조건을 따라야 합니다. 문의 주소는 **draftup@naver.com**이며 앱 맨 오른쪽 상단의 **문의**에서 메일을 작성할 수 있습니다.

ADF는 Artifex의 **PyMuPDF / MuPDF**를 사용합니다. 이 빌드는 Artifex 상용 라이선스가 아니라 AGPL 배포판을 사용합니다. PyMuPDF와 MuPDF에 변경 사항은 없습니다. 기존 명세의 “사내 사용은 아무 의무가 없다”는 표현은 배포 주체·수령자·변경·네트워크 제공 형태를 구분하지 못하므로 적용하지 않습니다. 회사 내부 사용이라는 설명만으로 모든 이용 조건이 면제되지는 않습니다.

ADF는 **Qt / PySide6 / Shiboken6**의 커뮤니티 배포판을 사용합니다. 사용한 Qt Core, Gui, Widgets, Network, PrintSupport 및 SVG와 이미지 플러그인에는 LGPLv3와 구성 요소별 라이선스가 적용됩니다. Qt와 PySide의 저작권은 The Qt Company 및 기여자에게 있습니다. `LGPL-3.0.txt`, `GPL-3.0.txt` 및 `upstream` 하위의 개별 고지를 함께 제공합니다. wheel 메타데이터에 상용 라이선스 선택지가 적혀 있어도 이 빌드에 상용 라이선스가 부여되었다는 의미는 아닙니다.

Qt/PySide DLL은 설치 폴더의 `_internal/PySide6` 등에 독립 파일로 존재합니다. 사용자는 LGPL이 허용하는 라이브러리 수정 및 해당 수정의 디버깅을 위한 역공학을 할 수 있습니다. 바이너리 호환성이 있는 수정 라이브러리로 교체하거나, 동봉된 ADF 소스를 수정한 라이브러리가 설치된 Python 환경에서 다시 빌드할 수 있습니다. ADF는 Qt 라이브러리의 서명을 강제로 검사하지 않습니다.

**fontTools**, **Pillow**, **Python**, **PyInstaller**와 **Inno Setup**의 원문 고지도 함께 제공합니다. fontTools는 PDF 내장 글꼴의 문자 연결 정보 복원과 임시 미리보기 글꼴 처리에 사용합니다. PyInstaller에는 생성한 앱의 배포에 관한 bootloader 예외가 적용됩니다. 라이선스 수집 시 업스트림 소스의 다양한 선택 라이선스도 보존하므로, 파일이 있다는 이유만으로 모든 모듈이 앱에 사용되었다는 뜻은 아닙니다.

## 소스 받기 및 다시 빌드

OCR에는 **RapidOCR, RapidLayout, RapidTable**(Apache-2.0), **ONNX Runtime**(MIT), **PaddlePaddle / PaddleOCR 모델**(Apache-2.0)을 사용합니다. 모델 저작권은 PaddlePaddle Authors 및 기여자에게 있으며, RapidAI가 배포한 ONNX 변환본을 수정 없이 포함합니다. [모델 라이선스 원문](PaddleOCR-APACHE-2.0.txt), 모델의 다운로드 출처·SHA-256·역할은 `build-manifest.json`에 있습니다. 모델 파일 5개는 설치 폴더의 `_internal/OCR_MODELS`에 있으며 직원 PC에서 별도 다운로드하지 않습니다.

**OpenCV, NumPy, Shapely**와 OCR의 나머지 Python 의존성도 해당 패키지별 고지와 원본 소스를 함께 제공합니다. ONNX Runtime의 [외부 구성 요소 고지](onnxruntime/onnxruntime/ThirdPartyNotices.txt)도 포함합니다. Shapely가 사용하는 **GEOS 3.13.1**에는 [LGPL-2.1](shapely/licenses/LICENSE_GEOS)이 적용되며, 대응하는 GEOS 소스를 동봉합니다. GEOS DLL은 `_internal/shapely.libs`의 독립 파일입니다. 사용자는 해당 조건에 따른 라이브러리 수정·호환 DLL 교체와 그 수정을 디버깅하기 위한 역공학을 할 수 있으며, 동봉한 소스로 다시 빌드할 수도 있습니다. ADF는 수정한 GEOS DLL의 서명을 강제하지 않습니다. 사용하지 않는 OpenCV의 영상 코덱 DLL은 설치본에 포함하지 않습니다.

정식 설치 파일에는 아래 두 소스 ZIP을 함께 넣습니다. 설치 후 **도움말 → 소스코드 → 소스 파일 폴더 열기**에서 찾을 수 있습니다. 위치는 설치 폴더의 `_internal/SOURCES`이며 파일명에는 설치된 앱의 버전이 들어갑니다.

- **ADF-Source ZIP**: ADF와 네이티브 탐색기 확장 소스, 테스트, 빌드 및 설치 스크립트, 라이선스 전문.
- **ADF-ThirdParty-Sources ZIP**: 사용한 버전의 PyMuPDF, MuPDF, Qt Base, Qt SVG, Qt Image Formats, PySide/Shiboken, Pillow, fontTools, OCR 엔진과 의존성 및 GEOS의 공식 원본 소스.
- **SHA256SUMS.txt**: 두 소스 ZIP의 무결성 확인용 해시. [소스 파일 폴더](../SOURCES/).

버전, 원본 소스 URL, SHA-256은 `build-manifest.json`에 기록됩니다. Python 원본은 [Python 공식 소스](https://www.python.org/downloads/source/), PyInstaller 원본은 [PyInstaller 저장소](https://github.com/pyinstaller/pyinstaller), Inno Setup 원본은 [공식 저장소](https://github.com/jrsoftware/issrc)에서도 받을 수 있습니다. 정확한 Python 버전은 manifest에 있습니다. ADF 빌드 방법은 `README.md`를 참고하세요. 공급받은 라이브러리의 소스 빌드 방법은 각 원본 아카이브의 문서에 포함되어 있습니다.

배포 담당자는 사용안내·고지·소스 묶음이 포함된 정식 설치 파일을 제공합니다. 재배포할 때도 해당 자료를 누락하지 않습니다. 설치 후의 `ADF.exe`만 떼어 복사하면 실행 라이브러리와 안내·소스가 빠지므로, 설치 없는 배포에는 `dist/ADF` 전체 폴더가 필요합니다. 이 안내가 모든 배포 형태의 조건 충족을 보장하는 것은 아니며, 해당 배포 조건은 각 원문을 따릅니다.

ADF 탐색기 확장은 LLVM-MinGW 20260826으로 빌드하며 C++ 런타임을 정적으로 연결합니다. LLVM의 Apache 2.0 및 LLVM 예외, MinGW-w64 런타임의 구성 요소별 고지는 `NativeShell` 폴더에 원문 그대로 제공합니다. 도구 모음과 정확한 LLVM/MinGW 소스 버전은 `build-manifest.json`의 `native_shell`에 기록합니다. `native-shell`의 ADF 자체 코드는 위의 AGPL 조건을 따릅니다.

## macOS 앱

macOS용 `ADF.app`은 Apple Silicon Mac의 macOS 15 이상용입니다. 저장소의 `LICENSES` 폴더에 있는 패키지별 고지와 `build-manifest.json`은 Windows용 wheel 기준입니다. Mac 빌드는 같은 버전의 macOS용 wheel에서 고지를 다시 모아 앱에 넣습니다. 앱 안의 위치는 `ADF.app/Contents/Resources/LICENSES`이며 대응 소스 ZIP은 `Contents/Resources/SOURCES`, OCR 모델은 `Contents/Resources/OCR_MODELS`에 있습니다. Windows 탐색기 확장(NativeShell)과 Inno Setup 고지는 macOS 앱에 해당하지 않아 포함하지 않습니다.

PyPI의 macOS용 opencv-python wheel은 FFmpeg와 x264·x265 등 GPL 영상 코덱을 함께 담고 있습니다. ADF는 영상을 다루지 않으므로 Mac 빌드는 이 wheel 대신 **ADF-ThirdParty-Sources ZIP에 들어 있는 opencv-python 원본 소스를 수정 없이, 영상 입출력을 끄고 컴파일**해 사용합니다. 이미지 코덱은 OpenCV 소스에 포함된 것만 쓰며 빌드 중 외부 구성 요소를 내려받지 않습니다. 사용한 CMake 옵션과 결과물의 SHA-256은 앱의 `build-manifest.json` 중 `opencv_build`에 기록합니다.

Qt / PySide와 GEOS 등의 라이브러리는 `ADF.app/Contents/Frameworks` 아래에 독립 파일로 있습니다. 서명된 앱도 라이브러리의 서명자가 같은지 강제하지 않으므로(`com.apple.security.cs.disable-library-validation`) 사용자가 빌드한 호환 라이브러리로 교체해 실행할 수 있습니다. 파일을 바꾼 앱은 Apple 공증 상태가 유지되지 않으므로 다른 Mac에 배포하려면 다시 서명해야 합니다.

## 참고 원문

- [PyMuPDF 라이선스](https://pymupdf.readthedocs.io/en/latest/about.html)
- [GNU 조직 내부 배포 FAQ](https://www.gnu.org/licenses/gpl-faq.html#InternalDistribution)
- [Qt LGPL 의무](https://www.qt.io/development/open-source-lgpl-obligations)
- [Qt for Python 라이선스](https://doc.qt.io/qtforpython-6/licenses.html)

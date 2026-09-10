# ADF에 기여하기

ADF는 로컬 문서 작업을 위한 Windows 데스크톱 앱입니다. 도장 보관, PDF 편집, 비교, OCR은 사용자 PC에서 처리합니다. 웹사이트는 소개와 배포를 담당합니다.

## 개발

Windows x64와 Python 3.12에서 [README의 개발 방법](README.md#개발-및-windows-설치-파일-빌드)으로 가상 환경을 준비하고 `requirements-dev.txt`를 설치합니다. `python main.py`로 소스를 실행하고 `python -m pytest -q`로 검증합니다. OCR 통합 검사는 `scripts/prepare-ocr.py`로 준비한 로컬 모델이 필요할 수 있습니다.

Mac에서 이어 작업할 때는 [Mac 개발 인수인계](docs/macos-development.md)의 환경 준비·실행·빌드 방법과 아직 검증하지 않은 항목을 확인하세요.

작은 변경은 관련 검사부터 실행하세요. PDF 편집 변경은 저장 후 다시 열어 내용과 실행 취소를 확인하고, UI 변경은 실제 화면과 키보드 조작을 확인합니다. 소스 검사 통과와 설치 파일 실행 검증은 구분해서 기록합니다.

## 이슈와 Pull Request

재현 순서, 예상 동작, 실제 동작, ADF·Windows 버전을 적습니다. 재현 파일은 가상의 문서와 가상의 도장으로 만듭니다. 실제 업무 문서, 도장 데이터베이스, 서명 인증서, 토큰, 사용자별 설정은 저장소와 공개 이슈에 포함하지 않습니다.

Pull Request에는 해결하는 문제와 결과, 확인한 검사를 적고 관련 변경만 포함해 주세요. 새 의존성은 라이선스, 고지와 대응 소스 제공 방법을 함께 검토합니다. 기존 기여자와 외부 라이브러리의 저작권 표시를 유지합니다.

## 라이선스와 배포

ADF 자체 코드는 AGPL-3.0-or-later로 제공합니다. 기여하는 코드에 대한 권한을 보유해야 하며 프로젝트의 라이선스로 제공할 수 있어야 합니다. 외부 코드는 원래 라이선스를 유지합니다. [오픈소스 고지](LICENSES/README.md)를 참고하세요.

설치 파일·대응 소스·외부 라이브러리 소스·해시는 GitHub Releases에 함께 게시합니다. `release/`, `dist/`, `.venv/`, `.tools/`는 Git에 포함하지 않습니다. 코드 서명 현황은 [CODE_SIGNING.md](CODE_SIGNING.md), 웹사이트 배포는 [site/README.md](site/README.md)에 기록합니다.

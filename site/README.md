# ADF 다운로드 사이트

데스크톱 앱 소개·다운로드·사용안내용 정적 사이트입니다. PDF 편집이나 도장 업로드 기능은 없습니다. 외부 폰트, 분석 SDK, 프레임워크 의존성이 없습니다.

- 공개 사이트: https://adf-desktop.vercel.app
- Vercel 프로젝트: `adf-desktop`. 현재 CLI로 게시하며 GitHub 커밋만으로 자동 재배포되지는 않습니다.
- 소스: https://github.com/Keilkim/ADF
- 설치 파일: GitHub Releases의 버전별 자산으로 제공
- 호스팅: Vercel, 프로젝트 루트 `site`, Framework Preset `Other`, 빌드·설치 명령 없음
- 도장 보관: 데스크톱 앱의 로컬 SQLite에만 저장

Windows와 macOS의 다운로드 버튼을 각각 표시합니다. macOS는 Apple 공증을 받은 0.3.25 DMG에 연결합니다. Windows는 코드 서명을 받을 때까지 비활성 버튼과 서명 준비 중 안내를 표시하며 서명되지 않은 EXE를 연결하지 않습니다. 각 버튼에 다른 플랫폼 파일이나 소스 ZIP을 연결하지 않습니다.

이 Mac처럼 연결 정보(`site/.vercel`)가 없는 곳에서는 먼저 `vercel link --cwd site --project adf-desktop --yes`로 기존 프로젝트에 연결합니다. 연결 없이 배포하면 새 프로젝트가 만들어질 수 있습니다.

저장소 최상위에서 `python -m http.server 4173 --directory site --bind 127.0.0.1`로 확인할 수 있습니다. 이 기본 서버에서는 `/guide.html`, `/privacy.html`로 안내를 엽니다. Vercel에서는 `cleanUrls`로 확장자 없이 열립니다.

Vercel CLI가 연결된 계정에서 `vercel --cwd site --prod`로 배포합니다. 저장소 전체나 `release/`를 Vercel에 업로드하지 않습니다. `site/.vercel`과 인증 파일은 Git에 포함하지 않습니다.

새 릴리즈를 게시할 때 `index.html`의 버전·파일 크기·배포일·다운로드 URL·검증 상태와 `guide.html`의 상태 안내를 함께 갱신합니다. 실제 공개 릴리즈의 자산과 SHA-256을 확인한 뒤 사이트를 배포합니다. 서명되지 않은 파일을 서명 완료 또는 정식 검증 완료로 표시하지 않습니다.

`assets/workspace.png`는 가상의 예시 문서와 가상의 도장을 실제 ADF 소스 실행 화면에 표시해 촬영한 이미지입니다. 개인 문서·도장 보관함은 사용하지 않았습니다. 생성 스크립트는 `scripts/capture-site-preview.py`입니다.

# Code signing policy · 코드 서명 및 릴리즈 정책

## 현재 상태

- ADF 0.3.26: Windows는 미서명, macOS는 Developer ID 서명·Apple 공증 완료.
- Windows 소스 검사: 298개와 세부 검사 122개 통과. Windows Server 2022에서 설치·파일 대조·바로가기·앱 실행·문서 처리·제거 확인.
- Windows Server의 탐색기 메뉴 조합 검사는 실패. Windows 10/11의 실제 탐색기 통합과 입력 장치는 추가 확인 필요.
- 2026년 9월 12일 유지관리자 승인으로 미서명 Windows 0.3.26을 홈페이지에 공개하며, 실행 차단·경고 가능성을 다운로드 버튼 옆에 안내.
- GitHub·Vercel 게시 자체가 Windows의 서명·평판 판정을 바꾸지는 않음.
- 보안 정책을 해제하거나 포장 형식을 바꿔 차단을 우회하는 배포 절차는 제공하지 않음.

기존 빌드의 해시와 검증 기록은 해당 GitHub 릴리즈에서 확인합니다. 새로운 서명 또는 재빌드는 새로운 릴리즈로 게시하며 기존 설치 파일을 같은 이름으로 교체하지 않습니다.

## SignPath Foundation 신청 현황

2026년 9월 10일 SignPath Foundation의 무료 오픈소스 서명 지원 신청서를 제출하고 접수 완료 안내를 확인했습니다. 현재 심사를 기다리고 있으며 지원 승인과 실제 Windows 서명은 아직 완료되지 않았습니다.

## 담당자와 승인

현재 저장소 유지관리자는 [Keilkim](https://github.com/Keilkim)입니다.

- Committers and reviewers: [Keilkim](https://github.com/Keilkim).
- Signing approver: [Keilkim](https://github.com/Keilkim).
- 모든 서명 요청은 유지관리자가 소스·빌드 결과와 배포 내용을 검토한 뒤 수동으로 승인합니다.
- SignPath 연동 전에 GitHub와 SignPath 계정의 다중 인증 설정을 확인합니다.

ADF 앱은 사용자 문서와 도장을 네트워크로 전송하지 않습니다. [데이터 보관 안내](https://adf-desktop.vercel.app/privacy)에는 로컬 보관과 웹사이트·다운로드 호스팅의 처리 범위가 있습니다.

## 지원 심사와 연동 전 확인할 조건

공개 저장소, 프로젝트 설명, 라이선스 고지, 설치 파일과 대응 소스 제공 경로를 준비했습니다. 지원 심사와 연동 단계에서 유지관리자가 확인할 항목은 다음과 같습니다.

1. [지원 조건](https://signpath.org/terms.html)의 유지관리·공개 릴리즈·프로젝트 신뢰도 요건과 전체 구성 요소의 라이선스 조건. Qt·PyMuPDF 등 이중 라이선스 구성 요소에 대한 적격성은 제공자에게 확인.
2. GitHub 및 서명 서비스의 다중 인증, 작성·검토·릴리즈 승인 담당자.
3. 공개 소스와 결과물을 연결할 검증 가능한 빌드 및 서명할 바이너리의 범위. ADF EXE, 탐색기 확장 DLL, 설치 파일과 내부 로더의 서명 범위를 확인.
4. 외부 라이브러리의 서명 범위. Foundation 인증서로 타 프로젝트의 바이너리를 임의로 서명할 수 없음.
5. 서비스 승인 후 공식 연동 설정과 릴리즈별 서명 승인. 승인 전부터 자동 서명되는 것으로 표시하지 않음.

서비스 가입, 신원 확인, 다중 인증 및 조직 역할 설정은 유지관리자의 실제 계정에서 완료해야 합니다. 인증서·토큰은 저장소에 넣지 않고 GitHub Actions 비밀 또는 제공자의 지원 경로로 관리합니다.

지원이 승인되고 실제 연동이 완료되면 SignPath가 요구하는 제공자·인증서 고지를 추가합니다. 승인 전에는 SignPath가 ADF를 서명하거나 보증하는 것으로 표시하지 않습니다.

## 검증 가능한 Windows 빌드

[Windows build 워크플로](.github/workflows/windows-build.yml)는 유지관리자가 수동 실행하며 GitHub에서 제공하는 Windows 실행기에서 공개 소스로 빌드합니다. 소스·OCR 검사, 네이티브 DLL 구성 요소 검사와 설치 패키지 정적 검사를 거쳐 커밋과 실행 번호가 붙은 미서명 검토용 아티팩트를 7일간 보관합니다. 보관 대상은 ADF EXE·확장 DLL과 대응 소스, 검사 기록입니다. `retain_installer`를 선택한 수동 실행은 설치 시험에 사용할 미서명 설치 파일도 별도 아티팩트로 7일간 보관합니다. 이 선택은 공개 릴리즈 게시나 서명 승인이 아닙니다. 빌드 결과와 실행 기록은 [GitHub Actions](https://github.com/Keilkim/ADF/actions/workflows/windows-build.yml)에서 확인합니다.

GitHub Windows Server 실행기의 실제 탐색기 메뉴 조합 검사는 [초기 실행](https://github.com/Keilkim/ADF/actions/runs/34458193465)에서 실패했습니다. 워크플로는 이 검사를 계속 별도로 실행하고 실패도 빌드 요약·기록에 표시하지만 미서명 검토용 빌드를 중단시키지는 않습니다. CI 성공을 최종 릴리즈 검증 완료로 해석하지 않으며, 지원 대상 Windows 10/11에서 탐색기 메뉴와 설치한 앱을 별도로 검증해야 합니다. 기본 로컬 빌드에서는 전체 네이티브 검사 실패가 여전히 빌드를 중단시킵니다.

기본 빌드는 GitHub 릴리즈를 자동 게시하거나 서명을 요청하지 않습니다. `release_run_id`와 `release_tag`를 지정하는 별도 수동 실행은 성공한 빌드의 설치 파일에서 대응 소스를 꺼내 해시를 검사하고, 해당 빌드 커밋을 가리키는 기존 초안 릴리즈에만 업로드합니다. 공개 게시는 유지관리자 승인 후 별도로 수행합니다. Windows Server 2022 실행기에서는 격리된 설치·전체 설치 파일 해시 대조·설치 앱 기능·병합/분할 작업·독립 도구 창·제거를 검사합니다. Windows 10/11의 실제 사용자 환경과 탐색기 메뉴는 별도로 확인해야 합니다. 0.3.26 Windows 릴리즈는 이 워크플로에서 만든 미서명 파일이며, SignPath의 실제 연동은 지원 승인 후 설정합니다.

## macOS 서명과 공증

Mac용 앱과 DMG는 Apple Developer ID Application 인증서 **SEONGHUN KIM (팀 ID TZQ9JL6R7R)**으로 서명하고 Apple 공증을 받은 뒤 배포합니다. SignPath 지원을 신청한 Windows 서명과는 별개입니다.

- [Mac 빌드 스크립트](scripts/build-macos.sh)는 PyInstaller로 앱의 모든 실행 파일·라이브러리를 hardened runtime과 보안 타임스탬프로 서명하고, DMG도 같은 인증서로 서명합니다.
- [권한 파일](installer/adf.entitlements)은 LGPL 라이브러리를 사용자가 교체할 수 있도록 라이브러리 서명자 일치 검사만 끕니다.
- 공증은 `xcrun notarytool`로 앱과 DMG를 각각 제출하고, 승인 결과를 붙여(staple) 인터넷 없이 처음 실행해도 Gatekeeper가 확인할 수 있게 합니다.
- 인증서 개인 키와 공증용 앱 암호는 빌드하는 Mac의 키체인에만 두며 저장소에 넣지 않습니다.
- 서명·공증한 파일도 유지관리자가 소스·빌드 결과와 검증 기록을 확인한 뒤 수동으로 게시합니다.

Mac 배포 파일마다 서명·공증 결과와 Gatekeeper 판정을 해당 릴리즈의 배포 상태 기록(`release/배포상태-<버전>-macOS.md`)과 GitHub 릴리즈에 남기며, 공증을 받지 못한 파일은 배포하지 않습니다.

## 서명 후 배포 확인

서명된 앱·탐색기 확장·설치 파일을 확인하고, 새 설치 파일을 서명한 뒤 최종 SHA-256을 생성합니다. 보안 정책을 유지한 Windows에서 설치·실행·문서 저장·도장 보관·OCR 및 업데이트를 검증합니다. 검증되지 않은 항목은 릴리즈 노트에 남깁니다. 유효한 서명이 있어도 모든 SmartScreen 경고가 즉시 사라진다고 보장하지 않습니다.

참고: [Microsoft의 서명·평판 설명](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation), [SignPath Foundation](https://signpath.org/terms.html).

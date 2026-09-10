# Code signing policy · 코드 서명 및 릴리즈 정책

## 현재 상태

- ADF 0.3.24: 서명 없음. GitHub에서는 사전 릴리즈로 제공.
- 최종 소스 검사: 263개와 세부 검사 119개 통과.
- 최종 설치 파일과 앱 EXE: 빌드 PC의 Windows Application Control이 실행을 차단해 설치·실행 검증 미완료.
- GitHub·Vercel 게시 자체가 Windows의 서명·평판 판정을 바꾸지는 않음.
- 보안 정책을 해제하거나 포장 형식을 바꿔 차단을 우회하는 배포 절차는 제공하지 않음.

기존 빌드의 해시와 검증 기록은 해당 GitHub 릴리즈에서 확인합니다. 새로운 서명 또는 재빌드는 새로운 릴리즈로 게시하며 기존 설치 파일을 같은 이름으로 교체하지 않습니다.

## SignPath Foundation 신청 준비

SignPath Foundation의 무료 오픈소스 서명 지원을 신청하기 위해 자료를 준비하고 있습니다. 아직 신청서 제출·지원 승인·실제 서명은 완료되지 않았으며, 이 프로젝트의 적격성을 단정하지 않습니다. 현재 배포 파일은 서명되지 않았습니다.

## 담당자와 승인

현재 저장소 유지관리자는 [Keilkim](https://github.com/Keilkim)입니다.

- Committers and reviewers: [Keilkim](https://github.com/Keilkim).
- Signing approver: [Keilkim](https://github.com/Keilkim).
- 모든 서명 요청은 유지관리자가 소스·빌드 결과와 배포 내용을 검토한 뒤 수동으로 승인합니다.
- SignPath 연동 전에 GitHub와 SignPath 계정의 다중 인증 설정을 확인합니다.

ADF 앱은 사용자 문서와 도장을 네트워크로 전송하지 않습니다. [데이터 보관 안내](https://adf-desktop.vercel.app/privacy)에는 로컬 보관과 웹사이트·다운로드 호스팅의 처리 범위가 있습니다.

## 신청 전 확인할 조건

공개 저장소, 프로젝트 설명, 라이선스 고지, 설치 파일과 대응 소스 제공 경로를 준비했습니다. 신청 전 유지관리자가 확인할 항목은 다음과 같습니다.

1. [지원 조건](https://signpath.org/terms.html)의 유지관리·공개 릴리즈·프로젝트 신뢰도 요건과 전체 구성 요소의 라이선스 조건. Qt·PyMuPDF 등 이중 라이선스 구성 요소에 대한 적격성은 제공자에게 확인.
2. GitHub 및 서명 서비스의 다중 인증, 작성·검토·릴리즈 승인 담당자.
3. 공개 소스와 결과물을 연결할 검증 가능한 빌드 및 서명할 바이너리의 범위. ADF EXE, 탐색기 확장 DLL, 설치 파일과 내부 로더의 서명 범위를 확인.
4. 외부 라이브러리의 서명 범위. Foundation 인증서로 타 프로젝트의 바이너리를 임의로 서명할 수 없음.
5. 서비스 승인 후 공식 연동 설정과 릴리즈별 서명 승인. 승인 전부터 자동 서명되는 것으로 표시하지 않음.

서비스 가입, 신원 확인, 다중 인증 및 조직 역할 설정은 유지관리자의 실제 계정에서 완료해야 합니다. 인증서·토큰은 저장소에 넣지 않고 GitHub Actions 비밀 또는 제공자의 지원 경로로 관리합니다.

지원이 승인되고 실제 연동이 완료되면 SignPath가 요구하는 제공자·인증서 고지를 추가합니다. 승인 전에는 SignPath가 ADF를 서명하거나 보증하는 것으로 표시하지 않습니다.

## 검증 가능한 Windows 빌드

[Windows build 워크플로](.github/workflows/windows-build.yml)는 유지관리자가 수동 실행하며 GitHub에서 제공하는 Windows 실행기에서 공개 소스로 빌드합니다. 소스·OCR 검사, 네이티브 확장 통합 검사와 설치 패키지 정적 검사를 거쳐 커밋과 실행 번호가 붙은 미서명 검토용 아티팩트를 7일간 보관합니다. 빌드 결과와 실행 기록은 [GitHub Actions](https://github.com/Keilkim/ADF/actions/workflows/windows-build.yml)에서 확인합니다.

이 워크플로는 GitHub 릴리즈를 자동 게시하거나 서명을 요청하지 않습니다. 설치한 앱의 실행 검증도 별도로 필요합니다. 기존 0.3.24 릴리즈는 로컬에서 만든 미서명 파일이며, 이 워크플로의 새 검토용 빌드와 구분합니다. SignPath의 실제 연동은 지원 승인 후 설정합니다.

## 서명 후 배포 확인

서명된 앱·탐색기 확장·설치 파일을 확인하고, 새 설치 파일을 서명한 뒤 최종 SHA-256을 생성합니다. 보안 정책을 유지한 Windows에서 설치·실행·문서 저장·도장 보관·OCR 및 업데이트를 검증합니다. 검증되지 않은 항목은 릴리즈 노트에 남깁니다. 유효한 서명이 있어도 모든 SmartScreen 경고가 즉시 사라진다고 보장하지 않습니다.

참고: [Microsoft의 서명·평판 설명](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation), [SignPath Foundation](https://signpath.org/terms.html).

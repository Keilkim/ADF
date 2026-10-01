XDF Type — Original outline prototype, Version 0.200

현재 index.html은 새 세리프·산세리프 이미지 시안을 보여줍니다.
이 README의 0.200 실제 폰트와 새 이미지 시안은 별도 작업입니다.
이전 0.200 실제 폰트 비교 화면은 prototype-0200.html에 보관합니다.
이미지 제작 방향과 프롬프트: concepts/design-brief.txt

Xebatang / 부드러운 끝 처리와 가로·세로 획의 대비
Xedotum / 단정한 비례, 둥근 내부 공간, 부드러운 접합부
두 서체 × Light 300 · Medium 500 · Bold 700 · ExtraBold 800.
각 파일에 영문과 현대 한글이 함께 들어 있습니다.
요청에 쓰인 "dedium"은 Medium으로 표기했습니다.

제작 출처
0.100은 Noto KR을 변형한 버전이었고, 사용자가 그 방향을 거절했습니다.
이 0.200은 그 윤곽을 사용하지 않습니다. 기존 글꼴 파일을 읽거나
글자를 변형하지 않고 알파벳·숫자·문장부호·한글 자모를 직접 구성합니다.
자체 XDF 워드마크의 X·D·F 곡선은 일반 글자의 원본으로 사용하되,
본문에 맞는 단정한 폭으로 좁혔습니다. 다른 글자는 그 둥근 내부 공간,
곡선 접합부, 짧은 끝 처리를 디테일로 이어받아 새로 그렸습니다.
Xebatang의 끝 처리는 짧고 부드러운 돌출부로 설계했습니다.
획과 끝 처리는 굵기에 맞춰 다시 계산하며, 브라우저의 가짜 굵기를 쓰지 않습니다.
로고의 D·F를 가볍게 만든 글자에는 자체 로고 윤곽의 안쪽 오프셋을 사용합니다.

공통 조형 규칙
1. 기준선과 비례: 1000 UPM, 영문 대문자 높이 760, 소문자 높이 550,
   한글 글자 폭 1000, 영문 좌우 기본 여백 28을 공통 기준으로 사용합니다.
2. 접합부의 곡률: Xebatang은 획 두께의 10%, Xedotum은 16%를 기본값으로
   사용하며, 작은 내부 공간이 막히지 않도록 문맥별 상한을 둡니다.
3. 획 끝: 기본 수평 획의 끝 돌출은 두께의 8%로 제한합니다.
   Xebatang의 세리프는 세로 획 폭의 1.30배를 기준으로 짧게 만듭니다.
4. 같은 원리의 굵기: 좁은 한글 자모는 획을 점진적으로 제한하여
   Bold와 ExtraBold가 같은 획으로 합쳐지지 않도록 설계합니다.
규칙의 실제 수치는 manifest.json과 xdf_type_geometry.py에 포함합니다.

한글 구성
직접 그린 초성·중성·종성을 받침 유무와 모음 방향에 따라 여섯 가지
문맥별 배치로 조합합니다. 각 글자는 실제 TrueType 합성 글리프입니다.
현대 한글 U+AC00–U+D7A3 11,172자를 모두 포함합니다.
조합형 한글을 같은 글자로 표시하는 OpenType ccmp 기능도 제공합니다.
이번 버전은 독립 제작한 첫 프로토타입입니다. 긴 본문과 작은 크기에 대한
전체 글자별 광학 보정·힌팅·확장 커닝은 아직 마무리 단계가 아닙니다.
미리보기에서 공문 제목, 11~12pt 본문과 표를 확인할 수 있습니다.

포함 파일
  ttf/                 설치용 실제 굵기 파일 8개
  woff2/               같은 글자를 담은 웹용 파일 8개
  fonts.css            두 서체와 네 가지 굵기의 대응
  index.html           문장·크기·자간을 바꾸는 미리보기와 공문 예시
  preview-cover.png    실제 폰트 8종을 나란히 보는 이미지
  preview.png          전체 미리보기 페이지 이미지
  preview-document.png 실제 12pt 공문 예시 이미지
  letter-anatomy.svg   일반 글자 X D F O M ㅁ ㅇ ㄹ의 실제 윤곽
  original-glyphs.svg  TTF에서 직접 추출한 알파벳·자모·대표 한글 도면
  xdf_type_geometry.py 모든 자체 글자의 조형 원본
  build-xdf-type.py    폰트 생성, 조합 및 패키징 소스
  preview-xdf-type.py  실제 글리프 도면 생성 소스
  requirements-build.txt / OFL-1.1.txt / xdf-wordmark.svg / xdf-mark.svg
  manifest.json        제작 출처, 문자 수, 해시, 각 파일 정보
  OFL.txt              XDF 저작권 고지와 SIL OFL 1.1

설치
TTF 파일을 열어 운영체제의 설치 기능을 사용하세요.
Xebatang / Xedotum과 Light / Medium / Bold / ExtraBold를 선택합니다.
구형 Windows 프로그램에서는 일부 굵기가 별도 서체명으로 보일 수 있습니다.
이 제작 과정에서 운영체제에 폰트를 자동 설치하지는 않습니다.

웹 사용
  <link rel="stylesheet" href="fonts.css">
  font-family: 'Xedotum', sans-serif;
  font-weight: 500; /* 300 / 700 / 800 */
  font-synthesis: none;
기본 X·D·F는 각각 독립 글자입니다. 선택형 dlig 기능을 켜면 원래
로고의 넓은 비례와 촘촘한 간격을 유지하는 단일 로고 글리프로 표시합니다.
  .brand { font-feature-settings: 'dlig' 1; }

지원 범위
영문 A–Z / a–z, 숫자 0–9, ASCII 문장부호, ₩ $ € £ ¥,
주요 따옴표·대시·가운뎃점·줄임표, 현대 한글 자모와 완성형 전체.
한자·옛한글·일본어·확장 라틴 문자는 이번 버전에 포함하지 않습니다.

재생성
저장소에서:
  python -m pip install -r scripts/requirements-brand-fonts.txt
  python scripts/build-xdf-type.py
  python scripts/preview-xdf-type.py
기존 글꼴 다운로드나 시스템 폰트 추출을 하지 않습니다.
소스 ZIP에서 재생성할 때는 별도 폴더 아래 다음 구조로 복사하세요.
  scripts/build-xdf-type.py
  scripts/xdf_type_geometry.py
  scripts/preview-xdf-type.py
  scripts/OFL-1.1.txt
  scripts/requirements-brand-fonts.txt  (requirements-build.txt를 이 이름으로)
  assets/xdf-wordmark.svg
  assets/xdf-mark.svg
생성한 8종과 라이선스·소스는 assets/fonts/에 출력됩니다.
스크린샷은 실제 브라우저에서 폰트 로딩을 확인한 뒤 별도로 만듭니다.

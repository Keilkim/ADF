XDF Type — Original text-outline prototype, Version 0.300

Xebatang (세리프) / Xedotum (산세리프)
각 Light 300 / Medium 500 / Bold 700 / ExtraBold 800
영문 대·소문자, 숫자, 문장부호, 현대 한글 11,172자.

제작 방식
글자 윤곽을 scripts/xdf_type_geometry.py에서 자체 설계합니다.
프로그램은 굵기 보정, 자모 조합, 간격, TTF/WOFF2 파일 생성을 담당합니다.
기성 폰트의 윤곽을 추출하거나 변형하지 않습니다.
Apple, Google, 배민, Gmarket은 인상과 용도에 대한 참고입니다.
해당 회사의 폰트 파일이나 글리프는 빌드 입력에 사용하지 않습니다.

0.300 변경
- 자음을 칸 전체에 억지로 늘리던 방식을 제거했습니다.
- 세로 모음보다 짧은 초성, 좁고 높은 원형 받침으로 비례를 조정했습니다.
- ㅇ/O/o는 둥근 사각형 대신 실제 타원의 곡률을 사용합니다.
- 본문의 X/D/F는 일반 문자 비례로 그립니다. 원래 로고는 선택 기능입니다.
- Xedotum은 가로·세로 굵기의 작은 차이와 넓은 속공간을 사용합니다.
- Xebatang은 방향에 따른 획 대비와 짧은 세리프를 사용합니다.
- 두 서체의 XDF 디테일은 안쪽 연결의 작은 곡선과 획 끝의 얕은 절삭입니다.

현재 수준
실제 설치 가능한 창작 초안입니다. 상용 서체 수준의 교정 완료본이 아닙니다.
한글 전체를 조합했지만 모든 글자의 육안 검수를 마친 것은 아닙니다.
자소별 세밀한 비례, 전체 자간, 작은 화면 크기의 힌팅은 추가 작업이 필요합니다.
한자, 옛한글, 일본어, 확장 라틴 문자는 포함하지 않습니다.
굵기는 각각 별도 윤곽의 정적 폰트입니다. 가변 폰트가 아닙니다.

미리보기
index.html            실제 0.300 파일로 표시하는 한글·영문 8종 비교
preview-cover.png     실제 8개 폰트에서 렌더링한 비교 이미지
preview-document.png  실제 Xebatang Medium / Xedotum Bold 공문 예시
preview-document.pdf  같은 글자 파일을 임베딩한 A4 PDF
original-glyphs.svg   실제 Xedotum Bold에서 추출한 글자 도면
letter-anatomy.svg    실제 Xedotum Medium에서 추출한 도면
concepts.html         이전 AI 이미지 시안, 실제 폰트 출력과 구별

설치
Windows에서 ttf 폴더의 8개 파일을 선택하고 설치하세요.
이전 XDF 버전이 있다면 새 버전으로 교체하고 사용 중인 문서 앱을 다시 여세요.
서체 이름: Xebatang / Xedotum, 굵기: Light / Medium / Bold / ExtraBold.
일부 오래된 Windows 프로그램에서는 굵기가 별도 가족명으로 보일 수 있습니다.
빌드 스크립트는 OS에 폰트를 설치하지 않습니다.

웹 사용
<link rel="stylesheet" href="fonts.css">
font-family: 'Xedotum', sans-serif;
font-weight: 500; /* 300 / 700 / 800 */
font-synthesis: none;
기본 X·D·F는 독립 글자입니다.
font-feature-settings: 'dlig' 1; 로 원래 XDF 로고 형태를 켤 수 있습니다.

검증과 출처
manifest.json에 각 TTF/WOFF2의 SHA-256 해시, 문자 수와 소스 해시가 있습니다.
브라우저에서 커스텀 폰트의 실제 이름을 확인한 뒤 미리보기를 만듭니다.
미리보기용 본문에 다른 폰트의 윤곽이나 이미지 글자를 사용하지 않습니다.
미리보기 페이지의 UI 라벨과 설명에는 시스템 글꼴을 사용합니다.
OFL.txt: Copyright 2026 XDF contributors / SIL Open Font License 1.1.
이전 0.100/0.200 실험은 저장소에서 따로 보관하며 0.300 ZIP에 포함하지 않습니다.

재생성 (저장소)
python -m pip install -r scripts/requirements-brand-fonts.txt
python scripts/build-xdf-type.py
python scripts/preview-xdf-type.py
폰트 스크린샷과 PDF는 브라우저에서 별도로 렌더링한 후 아래 명령으로 패키징합니다.
python scripts/build-xdf-type.py --package-only

소스 ZIP에서 재생성하려면 다음 구조로 복사하세요.
scripts/build-xdf-type.py
scripts/xdf_type_geometry.py
scripts/preview-xdf-type.py
scripts/OFL-1.1.txt
scripts/requirements-brand-fonts.txt (requirements-build.txt의 이름 변경)
assets/xdf-wordmark.svg
assets/xdf-mark.svg
생성 파일은 assets/fonts/에 출력됩니다.

# XDF 아이콘과 컬러

메인 로고는 사용자가 선택한 **첫 번째 XDF 워드마크 시안**입니다. Webflow와 SKIMS의 글자 무게감과 곡선을 참고해 내장 `image_gen`으로 만든 첫 시안의 형태를 제품용 벡터로 옮겼습니다. 넓고 연결된 X, 모서리가 둥근 D와 내부 공간, 곡선으로 마무리한 F의 두 팔과 촘촘한 간격을 유지합니다. 홈페이지와 앱 시작 화면에는 XDF 전체 로고를, 앱 아이콘·파비콘에는 워드마크에서 직접 추출한 동일한 X를 사용합니다. 첨부 컬러 참고 이미지의 주홍색 계열인 `#F04B2D`를 사용하며 배경은 투명합니다.

- 메인 벡터 원본: [xdf-wordmark.svg](xdf-wordmark.svg)
- 메인 투명 PNG: [xdf-wordmark.png](xdf-wordmark.png), 1536×407 RGBA
- 메인 로고에서 추출한 X: [xdf-mark.svg](xdf-mark.svg)
- 투명 PNG: [xdf.png](xdf.png), 1024×1024 RGBA
- Windows: [xdf.ico](xdf.ico), 16·20·24·32·40·48·64·128·256px의 32비트 DIB 프레임
- macOS: [xdf.icns](xdf.icns)
- 홈페이지 벡터: [XDF](../site/assets/xdf-wordmark.svg), [X 아이콘](../site/assets/xdf-mark.svg)
- 선택한 첫 생성 시안: [xdf-wordmark-reference.png](xdf-wordmark-reference.png)
- 메인 로고·작은 아이콘 미리보기: [xdf-logo-preview.html](xdf-logo-preview.html), [PNG](xdf-logo-preview.png)
- 사용한 프롬프트와 제작 기록: [xdf-logo-design.txt](xdf-logo-design.txt)

`scripts/make-icon.py`가 메인 워드마크에서 X를 추출하고 투명 여백을 계산해 각 플랫폼의 아이콘과 홈페이지 로고를 내보냅니다. 앱의 컬러는 `adf/brand.py`, 홈페이지의 컬러는 `site/styles.css`의 CSS 변수에서 관리합니다. 작은 글자와 흰 글자의 버튼에는 더 진한 주홍색을, 선택·호버 배경에는 옅은 주홍색을 사용합니다.

기존 `adf-mark-reference.png`와 `adf-icon-source.png`는 이전 디자인의 보관 자료이며 제품 로고로 사용하지 않습니다. 기존 사용자의 설정·도장 보관함·PDF 편집 기록·자동 업데이트와 이어지도록 내부 저장 식별자와 릴리즈 파일명은 유지합니다. 사용자에게 보이는 제품명·설치 화면·바로가기·탐색기 메뉴는 XDF입니다.

선택하지 않은 두 번째 시안과 벡터는 `brand-exploration/xdf-wordmark-refined.png`와 `brand-exploration/xdf-wordmark-refined.svg`에 보관합니다.

## XDF 영문·한글 폰트

현재 [비교 화면](fonts/index.html)은 공문서에 맞는 형태를 다시 잡기 위한 **세리프·산세리프 이미지 시안**입니다. 한글·영문의 익숙한 구조를 중심에 두고, XDF의 특징은 안쪽 곡선과 짧은 획 끝에 작게 남기는 방향입니다. 이 이미지는 설치용 폰트로 구현된 글자를 보여주는 자료가 아닙니다. [세리프 PNG](fonts/concepts/xebatang-serif-v1.png), [산세리프 PNG](fonts/concepts/xedotum-sans-v2.png), [제작 방향과 정확한 생성 프롬프트](fonts/concepts/design-brief.txt)를 함께 보관합니다. 내장 `image_gen.imagegen`으로 생성했으며 자체 워드마크만 브랜드 참고 이미지로 사용했습니다.

**Xebatang**(바탕)과 **Xedotum**(돋움)은 각각 Light·Medium·Bold·ExtraBold 네 가지 굵기로 제공합니다. 사용자가 적은 `dedium`은 `Medium`으로 해석했습니다. 각 파일에는 영문과 현대 한글 11,172자가 함께 들어 있습니다.

이전 구현 실험본 **0.200은 알파벳·숫자·문장부호·한글 자모부터 자체 설계한 창작 프로토타입**입니다. 새 이미지 시안을 구현한 폰트는 아니며, 형태가 부적합하다는 사용자 피드백을 받아 이미지부터 다시 검토하고 있습니다. Noto를 변형했던 0.100은 사용자가 거절한 방향으로 보관했고, 0.200 폰트 생성에는 기존 글꼴의 윤곽을 사용하지 않습니다. 자체 XDF 워드마크의 X·D·F 곡선을 본문에 맞는 폭으로 조정하고, 다른 글자에도 둥근 내부 공간·곡선 접합부·짧은 끝 처리를 작게 남겼습니다. Xebatang에는 가로·세로 획의 대비를 더했습니다. 한글은 받침 유무와 모음 방향에 맞춘 여섯 가지 자모 배치로 조합하며, 두 서체의 선택형 `dlig` 기능으로 원래 로고의 비례와 간격도 유지할 수 있습니다. 긴 본문·작은 크기에 대한 전체 글자별 광학 보정과 힌팅은 아직 완료하지 않았습니다.

- 이전 0.200 구현을 직접 입력하는 [폰트 비교 화면](fonts/prototype-0200.html)
- 8종을 나란히 보는 [미리보기 이미지](fonts/preview-cover.png), [전체 비교 이미지](fonts/preview.png)
- 실제 TTF의 글자를 벡터로 추출한 [알파벳·한글 도면](fonts/original-glyphs.svg), [로고와 다른 글자의 연결](fonts/letter-anatomy.svg)
- 공문 제목·12pt 본문·표를 보여주는 [공문 미리보기](fonts/preview-document.png)
- 8종 설치용 TTF와 웹용 WOFF2, 라이선스·제작 소스를 묶은 [ZIP](fonts/xdf-fonts-0.200.zip)
- [설치·사용 안내](fonts/README.txt), [라이선스](fonts/OFL.txt), [출처·검증용 해시](fonts/manifest.json)
- 조형 원본: [xdf_type_geometry.py](../scripts/xdf_type_geometry.py)
- 재생성: `python -m pip install -r scripts/requirements-brand-fonts.txt` 후 `python scripts/build-xdf-type.py`, 도면은 `python scripts/preview-xdf-type.py`

폰트를 OS에 설치하거나 기존 앱·홈페이지의 본문 서체를 일괄 교체하지 않습니다. 비교 화면에서 형태를 확인하고 원하는 문서나 화면에 적용할 수 있습니다.

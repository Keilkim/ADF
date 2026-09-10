# ADF 아이콘

승인된 형태는 배경·문서 테두리·접힌 모서리·연필·그림자를 뺀 파란 A다. 채움색은 `#2463E8`이며 바깥과 A 안쪽은 투명하다.

- 제품용 벡터 원본: [adf-mark.svg](adf-mark.svg)
- 투명 PNG: [adf.png](adf.png), 1024×1024 RGBA
- Windows: [adf.ico](adf.ico), 16·20·24·32·40·48·64·128·256px의 32비트 DIB 프레임
- macOS: [adf.icns](adf.icns)
- 승인된 생성 참고 이미지: [adf-mark-reference.png](adf-mark-reference.png)

참고 이미지는 내장 `image_gen` 도구로 기존 아이콘을 편집해 만들었다. CLI는 사용하지 않았다. 제작 요구 프롬프트는 다음과 같다.

> Edit the existing ADF icon. Extract the central A with rounded legs and a curved crossbar. Remove the surrounding blue document, folded corner, pencil, shadow and glow. Use uniform royal blue #2463E8, no 3D or texture, on an actual transparent RGBA background. Keep it bold and legible at 16px, centered in a square.

생성 참고 이미지에는 체크무늬가 픽셀로 들어 있어 제품에서는 사용하지 않는다. 사용자가 승인한 A 모양을 벡터로 정리한 `adf-mark.svg`가 제품의 원본이다. `scripts/make-icon.py`가 이 벡터를 크기별로 렌더링해 PNG·ICO·ICNS를 내보낸다. 기존 `adf-icon-source.png`는 이전 로고의 보관 자료다.

탐색기 병합·분할 메뉴는 `native-shell/shell_icon.h`에서 화면 DPI에 맞는 아이콘을 읽고 투명한 32비트 DIB에 그린다. 알파가 곱해진 색상을 가진 비트맵을 Windows 메뉴에 전달한다. 앱, 열기 메뉴, 바로가기와 PDF 연결 아이콘도 같은 아이콘 파일을 사용한다.

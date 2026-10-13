# pdf2hwpx — PDF → 편집 가능한 한글(HWPX) 변환 컴포넌트

AI 없이 PDF의 글자 위치·글꼴·선을 측정해, 한글에서 원본과 같은 쪽 수와 줄바꿈으로
열리면서도 문단 단위로 고칠 수 있는 HWPX를 만든다. 스캔 PDF만 선택적으로 AI(Gemini)를 쓴다.

```python
from adf.pdf2hwpx import convert_document
result = convert_document('in.pdf', 'out.hwpx')   # {'pages', 'paragraphs', 'tables', 'figures', 'warnings', ...}
```

의존성: `pymupdf`, `python-hwpx`(HWPX 쓰기), `../text_groups.py`(한국어 띄어쓰기 복원, pymupdf만 사용).
앱 전용 부분(키 저장 `hwpx_credentials.py`, 작업 프로세스 `hwpx_export.py`, 창 `hwpx_widgets.py`)은 밖에 있다.

## 구성

| 모듈 | 역할 |
|---|---|
| `pdf_reader.py` | 쪽 측정. 글줄 → 행(같은 기준선) → 문단 재구성, 글꼴명 정리, 자간·진하게·공백 폭 측정, 선·채움·테두리 상자 |
| `appearance.py` | 쪽 렌더 픽셀에서 글자색·셀 배경을 확인(PDF가 다르게 보고하는 경우) |
| `font_metrics.py` | 한글이 그릴 글자 폭과 글꼴 이름. 설치된 모든 글꼴(TTC 안 글꼴 포함)을 글꼴 자체 이름표로 색인하고, 파일로 못 읽는 한컴 HFT 글꼴은 `hancom_widths.json`(한글 실측)을 쓴다 |
| `hancom_widths.json` | 한컴 HFT 글꼴 265종·영문 글꼴의 글자별 폭(em), `scripts/pdf2hwpx/hancom_widths.py`로 한글에서 실측 |
| `symbol_fonts.py` | 글자 정보 없이 내장된 기호 글꼴(Wingdings의 ❷ 등)을 설치 글꼴의 같은 윤곽선으로 찾아 복원 |
| `columns.py` | 다단(신문식 단) 감지 → 테두리 없는 표 |
| `extract.py` | 한 쪽 → 블록 목록(문단·표·그림). 표 감지 보강(열린 표, 점선, 병합), 그림 배치 판정 |
| `schema.py` | 블록 구조 검증(`validate_page`). AI 응답도 같은 구조 |
| `hwpx_writer.py` | 블록 → HWPX. 여백·문단 간격·줄 나눔·셀·그림·꼬리말·자유 배치 |
| `reconcile.py` | AI 전사 결과에 PDF에서 잰 서식을 입힌다 |
| `ai.py` | Gemini/중계 서버 클라이언트(선택) |
| `convert.py` | 문서 전체 파이프라인, 패키지 검증 |

## 블록 구조 (`schema.py`)

쪽 = `{'blocks': [...], 'warnings': [...]}`. 위치 `bbox`는 쪽 기준 0~1000.

- `paragraph`: `text`, `runs`(글자 모양별: `font`, `font_size_pt`, `bold`, `italic`, `color`,
  `letter_spacing`, `font_space`, `highlight`: 글자 뒤 강조 배경색 → 글자 모양 음영), `alignment`, `line_spacing_percent`, `line_count`,
  `indent_left_pt`, `first_line_indent_pt`, `left_pt`(쪽 기준 왼쪽), `line_size_pt`(줄의 최대 글자 크기),
  `wraps_inside_words`, `placement`(`IN_FIGURE`: 도형 위 글자, 자유 배치 쪽에서만 씀)
- `table`: `rows`, `cols`, `cells`(`row`, `col`, `row_span`, `col_span`, `text`, `paragraphs`,
  `borders`, `fill_color`, `margins_pt`, `vertical_alignment`, `text_direction`, `images`),
  `column_widths_pt`, `row_heights_pt`
- `image`: `placement`(`BEHIND_TEXT`: 배경·글 옆 아이콘), `xref`(원본 비트맵),
  `frame`(글·표를 둘러싼 테두리: 선 종류·두께·색 → 글 뒤 사각형 도형으로 그림)

표 감지 보강: 선 4개로 닫힌 채움 경로도 채움으로 읽고, 밝은 채움 칸이 한 줄로 나란한 제목 띠
(`참고1 | 주택도시기금 지원`)는 칸 사이 간격까지 1행 표로 만든다(좁은 간격 칸은 안쪽 여백 0 —
한글 기본 여백 5.1pt가 칸을 넓혀 표 전체가 아래로 밀린다).

## 한글 배치 규칙 (실측으로 확인한 것)

한글은 줄을 열 때마다 다시 계산한다. 원본과 같은 줄바꿈을 얻으려면 아래를 맞춰야 한다.

- **글꼴 이름**: 한글은 PostScript 이름(`HCRBatang`)을 못 찾고 대체 글꼴로 그린다 → 한글 이름(`함초롬바탕`).
  CP949가 깨진 이름(`ÈÞ¸Õ¸íÁ¶`=휴먼명조), HY 글꼴(`H2hdrM`=HY헤드라인M), 굵기별 설치 글꼴
  (`Pretendard Medium`)도 처리. 이 PC에 없는 글꼴은 같은 계열(명조→바탕, 고딕→맑은 고딕)로 바꾼다.
- **자간**은 글자 폭 대비 %(글꼴 크기 대비 아님). 기준 폭은 설치된 글꼴 폭이어야 한다
  (내장 맑은 고딕이 한글을 0.95em으로 선언해도 한글은 1em으로 그린다).
- **공백**은 기본 0.5em(자간도 적용됨). 쪽에서 그 글꼴의 공백이 글꼴 자체 공백 폭과 정확히 같은
  것이 3개 이상이면(정렬 안 된 줄) 글자 모양 `useFontSpace=1`. 비교 전에 자간을 걷어내야 한다.
  PDF 제작 프로그램 정보는 믿을 수 없다(한글 문서를 Acrobat으로 다시 저장한 경우 등).
- **좁은 칸**: 한 줄 글이 칸에 꼭 맞으면 한글 반올림으로 접혀 행이 커지고 표가 다음 쪽으로 밀린다
  → 설치 글꼴로 계산한 폭이 칸 폭의 97%를 넘으면 그 줄 자간을 줄인다(`fit_line`).
- **줄 나눔**: OWPML 이름이 반대다. `breakNonLatinWord="KEEP_WORD"`가 글자 단위, `BREAK_WORD`가 어절 단위.
  원본에서 단어 중간 줄바꿈이 보이는 문단만 글자 단위. `condense`(공백 최소값) 5%.
- **줄 간격 %**는 그 줄에서 가장 큰 글자 기준. 마지막 줄 아래에도 붙는다(셀 높이 계산 주의).
- **줄 위치**: 한글은 줄 윗변에서 가장 큰 글자 크기의 0.85배 아래에 기준선을 둔다. 문단 위치는 PDF 글자
  상자 윗변이 아니라 "첫 기준선 − 0.85×크기"로 잡는다(맑은 고딕은 상자가 기준선 위 1.26em까지 올라간다).
  한 줄 문단은 다음 블록까지의 간격에 맞춰 줄 간격을 줄인다(두 줄 제목).
- **넓은 공백 하나**(라벨과 값 사이)는 한글 공백 폭으로 나눠 여러 칸으로 쓴다(양쪽 정렬 공백은 그대로).
- **음수 들여쓰기**(내어쓰기)는 왼쪽 여백이 첫 줄 기준, 양수는 둘째 줄 이후 기준.
- **그림**: python-hwpx는 표시 크기를 원본 크기로 기록해 한글이 96dpi 기준으로 잘라 보인다 →
  `imgClip`/`imgDim`을 픽셀×75로. 그림 추가 후 `add_section`/`validate`를 하면 매니페스트에서 그림이 빠지는
  python-hwpx 버그가 있어 저장 직전 `_sync_manifest`로 맞춘다.
- `None`으로 넘긴 글자 모양 값(예: `letter_spacing=None`)은 "아무 값"으로 취급돼 다른 글자 모양이 재사용된다 → 0을 명시.
- 템플릿 첫 문단의 `linesegarray`(줄 배치 캐시)는 지워야 그림 위로 글이 겹치지 않는다.

## 쪽 배치 방식

1. **흐름 배치**(기본): 문단을 원본 세로 위치에 맞춘 문단 앞 간격으로 쌓는다. 쪽 여백 = 원본 본문 영역,
   쪽 번호는 꼬리말, 배경·아이콘 그림은 종이 기준 "글 뒤로".
2. **자유 배치**: 흐름으로 쌓은 예상 높이가 쪽을 넘으면(그림 옆 글, 디자인 쪽) 그 쪽만
   글자를 지운 쪽 그림을 깔고 문단·셀 글자를 제자리 글상자로 올린다. 모양은 같고 글은 편집 가능.

## 폭 모델 (줄바꿈이 같아지는 원리)

한글은 줄을 열 때마다 글자 폭으로 다시 나눈다. 원본과 같은 줄바꿈은 "한글이 그릴 폭"을 정확히 알 때만
나온다. 한글로 직접 그려 잰 결과(`width_check.py`):

- 설치된 TrueType/OpenType 글꼴: 한글은 글꼴 파일의 진행 폭을 그대로 쓴다(오차 0.6% 이내).
  예외는 공백(기본 0.5em)과 `hp:fwSpace`(0.25em)뿐이라 전각 공백은 문자 그대로 쓴다.
- 한컴 HFT 글꼴(휴먼명조, 한양신명조, 신명 계열 등): 파일을 읽을 수 없어 한글에서 실측한 표를 쓴다.
  예: 휴먼명조는 가운뎃점(·)·말줄임표(…)를 전각 1em으로 그린다.
- 글꼴 이름: PDF의 PostScript 이름(`KoPubWorldDotumMedium`)을 설치된 글꼴의 이름표로 찾아 한글이
  보여 주는 계열 이름(`KoPubWorld돋움체 Medium`)으로 바꾼다. 굵기별 파일·TTC 안 글꼴도 구분한다.

## 검증 도구 (Windows + 한컴오피스)

```
py -3 -m pip install pywin32        # 렌더러만 필요
.venv\Scripts\python.exe scripts/pdf2hwpx/layout_check.py a.pdf b.pdf --out check
.venv\Scripts\python.exe scripts/pdf2hwpx/layout_check.py a.pdf --per-page   # 넘치는 쪽 찾기
.venv\Scripts\python.exe scripts/pdf2hwpx/width_check.py --out widths      # 폭 모델 대 한글 실측
.venv\Scripts\python.exe scripts/pdf2hwpx/hancom_widths.py --out hw        # HFT 폭 표 다시 만들기
```

`hancom_render.py`가 한글 COM으로 쪽 이미지를 만들고, `layout_check.py`가 원본과 비교해
`compare-N.png`(왼쪽 PDF, 오른쪽 한글)를 남긴다. 한글 PDF 저장은 프린터 대화상자에서 멈출 수 있어 쓰지 않는다.

지표는 **PDF의 글줄마다** 한글 렌더에서 같은 모양의 줄이 6px(약 4.5pt) 안의 같은 높이에서
같은 곳에서 시작해 같은 곳에서 끝나는지다. 끝점이 같으면 그 줄에 같은 단어까지 들어간 것이라
줄바꿈 충실도를 잰다. 표 선·밑줄 같은 18px 이상 직선은 지우고 비교한다. 어긋난 줄은 원인별로
센다: 끝 위치 다름(폭/줄바꿈), 시작 위치 다름(들여쓰기/정렬), 위아래 밀림, 멀리 밀림(위쪽 흐름), 못 찾음.

| 시험 문서(46쪽, 14종) | 쪽 수 | 제자리 글줄 |
|---|---|---|
| 한국어 표·주석(합성) | 1→1 | 100% |
| CorelDRAW 디자인(가로) | 3→3 | 96% |
| 영문 보고서·2단(합성) | 2→2 | 96% |
| 정부 보도자료(한글 2024) | 5→5 | 94% |
| 경관계획 보고서(사진 대지, Acrobat) | 4→4 | 92% |
| Chromium 출력 표지 | 4→4 | 92% |
| 가이드라인 서식(한글 2018) | 4→4 | 91% |
| 과업내용서(한글, 2쪽 모음) | 3→3 | 91% |
| 회의록(macOS) | 3→3 | 90% |
| 가이드라인 본문(복잡한 표, 세로쓰기, 점선 쪽 테두리) | 5→5 | 85% |
| 공고문(한글) | 3→3 | 80% |
| 제안요청서(한글, 2쪽 모음·목차) | 3→3 | 61% |
| 영문 논문(2단, pikepdf) | 3→4 | 54% |
| 윤리 가이드라인(Acrobat Distiller, 디자인 쪽) | 3→3 | 9% |

뒤의 6종은 개발에 쓰지 않은 문서로 따로 검증했다. 남은 차이는 시작 위치(들여쓰기·정렬) 119줄,
끝 위치(줄바꿈) 160줄, 세로 밀림 61줄이다(전체 1785줄 중 1418줄 제자리).

## 한계

- 스캔 PDF는 글자가 없어 AI 변환이 필요하다.
- 사용자 정의 영역 문자(U+E000~F8FF 기호)와 유니코드 대응이 없는 Wingdings 기호는 그 자리에 기호 그림으로 넣는다.
- 회전한 글자는 가로 문단으로 바꾼다. 기울임(합성 기울임)은 판정하지 않는다.
- 유니코드 정보 없는 글꼴로 쓴 글줄은 읽을 수 없어 그 부분을 그림으로 넣는다(편집 불가).
- 자유 배치 쪽의 도형·선은 그림이라 편집할 수 없다.

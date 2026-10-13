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
| `font_metrics.py` | 설치된 글꼴 찾기(Windows 레지스트리)와 글자 폭. 자간을 "한글이 실제로 그릴 폭" 기준으로 잰다 |
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
  `letter_spacing`, `font_space`), `alignment`, `line_spacing_percent`, `line_count`,
  `indent_left_pt`, `first_line_indent_pt`, `left_pt`(쪽 기준 왼쪽), `line_size_pt`(줄의 최대 글자 크기),
  `wraps_inside_words`, `placement`(`IN_FIGURE`: 도형 위 글자, 자유 배치 쪽에서만 씀)
- `table`: `rows`, `cols`, `cells`(`row`, `col`, `row_span`, `col_span`, `text`, `paragraphs`,
  `borders`, `fill_color`, `margins_pt`, `vertical_alignment`, `text_direction`, `images`),
  `column_widths_pt`, `row_heights_pt`
- `image`: `placement`(`BEHIND_TEXT`: 배경·글 옆 아이콘), `xref`(원본 비트맵)

## 한글 배치 규칙 (실측으로 확인한 것)

한글은 줄을 열 때마다 다시 계산한다. 원본과 같은 줄바꿈을 얻으려면 아래를 맞춰야 한다.

- **글꼴 이름**: 한글은 PostScript 이름(`HCRBatang`)을 못 찾고 대체 글꼴로 그린다 → 한글 이름(`함초롬바탕`).
  CP949가 깨진 이름(`ÈÞ¸Õ¸íÁ¶`=휴먼명조), HY 글꼴(`H2hdrM`=HY헤드라인M), 굵기별 설치 글꼴
  (`Pretendard Medium`)도 처리. 이 PC에 없는 글꼴은 같은 계열(명조→바탕, 고딕→맑은 고딕)로 바꾼다.
- **자간**은 글자 폭 대비 %(글꼴 크기 대비 아님). 기준 폭은 설치된 글꼴 폭이어야 한다
  (내장 맑은 고딕이 한글을 0.95em으로 선언해도 한글은 1em으로 그린다).
- **공백**은 기본 0.5em. PDF 공백이 그보다 좁으면(영문 글꼴) 글자 모양 `useFontSpace=1`.
- **줄 나눔**: OWPML 이름이 반대다. `breakNonLatinWord="KEEP_WORD"`가 글자 단위, `BREAK_WORD`가 어절 단위.
  원본에서 단어 중간 줄바꿈이 보이는 문단만 글자 단위. `condense`(공백 최소값) 5%.
- **줄 간격 %**는 그 줄에서 가장 큰 글자 기준. 마지막 줄 아래에도 붙는다(셀 높이 계산 주의).
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

## 검증 도구 (Windows + 한컴오피스)

```
py -3 -m pip install pywin32        # 렌더러만 필요
.venv\Scripts\python.exe scripts/pdf2hwpx/layout_check.py a.pdf b.pdf --out check
.venv\Scripts\python.exe scripts/pdf2hwpx/layout_check.py a.pdf --per-page   # 넘치는 쪽 찾기
```

`hancom_render.py`가 한글 COM으로 쪽 이미지를 만들고, `layout_check.py`가 원본과 쪽 수·글줄 위치를 비교해
`compare-N.png`(왼쪽 PDF, 오른쪽 한글)를 남긴다. 한글 PDF 저장은 프린터 대화상자에서 멈출 수 있어 쓰지 않는다.

시험 묶음(보도자료, 가이드라인 본문·서식, 경관계획 보고서, CorelDRAW 디자인, Chromium 출력,
영문 2단, 한국어 표)에서 모든 쪽이 원본과 같은 쪽 수로 변환된다.

## 한계

- 스캔 PDF는 글자가 없어 AI 변환이 필요하다.
- 사용자 정의 영역 문자(U+E000~F8FF 기호)는 원래 기호 글꼴 없이는 표시되지 않는다.
- 세로쓰기가 두 줄로 겹친 셀, 회전한 글자는 근사치다.
- 자유 배치 쪽의 도형·선은 그림이라 편집할 수 없다.

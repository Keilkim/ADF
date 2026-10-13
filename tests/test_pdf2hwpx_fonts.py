"""Hancom's widths and names for the fonts a PDF uses."""
import sys

import pytest

from adf.pdf2hwpx.font_metrics import _face, _measured, glyph_width, hancom_family
from adf.pdf2hwpx.hwpx_writer import HP, add_runs

windows = pytest.mark.skipif(sys.platform != 'win32', reason='installed Windows fonts')


@windows
def test_faces_are_found_by_their_own_names_and_weights():
    # Arial Narrow also names its typographic family 'Arial'.
    assert _face('Arial')[0].lower().endswith('arial.ttf')
    # A face inside a collection: DotumChe is the fourth face of gulim.ttc.
    face = _face('돋움체')
    assert face and face[0].lower().endswith('gulim.ttc') and face[1] > 0
    assert hancom_family('MalgunGothic') == '맑은 고딕'
    assert hancom_family('TimesNewRomanPSMT') == 'Times New Roman'


@windows
def test_hancom_only_fonts_use_widths_measured_in_hancom():
    if '휴먼명조' not in _measured():
        pytest.skip('no measured table')
    # 휴먼명조 cannot be read from a file; Hancom draws its middle dot a full em.
    assert glyph_width('휴먼명조', '가', 10) == pytest.approx(10, abs=.05)
    assert glyph_width('휴먼명조', '·', 10) == pytest.approx(10, abs=.05)


def test_ideographic_space_is_written_as_the_character():
    from hwpx import HwpxDocument
    doc = HwpxDocument.new()
    paragraph = doc.sections[0].paragraphs[0]
    add_runs(doc, paragraph, dict(runs=[dict(text='a　b\tc', font='맑은 고딕', font_size_pt=10)],
                                  font_size_pt=10, text='a　b\tc'))
    texts = list(paragraph.element.iter(HP + 't'))
    # hp:fwSpace is drawn a quarter em wide; the character is a full em.
    assert texts[-1].text == 'a　b'
    assert not any(element.tag.endswith('fwSpace') for element in paragraph.element.iter())
    assert any(element.tag == HP + 'tab' for element in paragraph.element.iter())

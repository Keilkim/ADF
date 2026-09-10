"""Check real content changes, page correspondence and the subprocess boundary."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import pymupdf
from PIL import Image

from adf.comparison import compare_documents
from adf.document import _open_pdf


def document(labels):
    doc = pymupdf.open()
    for label in labels:
        page = doc.new_page(width=300, height=400)
        if label:
            page.insert_text((30, 60), label, fontsize=14)
    return doc


class ComparisonTests(unittest.TestCase):
    def test_identical_visible_content_ignores_metadata_and_serialization(self):
        with document(['First', 'Second']) as left, document(['First', 'Second']) as right:
            right.set_metadata({'title': 'New metadata'})
            result = compare_documents(left, right)
            self.assertEqual(result['change_count'], 0)
            self.assertEqual([(p['left'], p['right']) for p in result['pairs']], [(0, 0), (1, 1)])

    def test_inserted_page_does_not_shift_later_pages_and_changed_amount_has_boxes(self):
        with document(['Cover', 'Amount 1000', 'Closing']) as left, document(['Cover', 'New attachment', 'Amount 2000', 'Closing']) as right:
            before = [page.get_text() for page in left]
            result = compare_documents(left, right)
            self.assertEqual([(p['left'], p['right']) for p in result['pairs']], [(0,0), (None,1), (1,2), (2,3)])
            changed = result['pairs'][2]['changes']
            text = next(c for c in changed if c['kind'] == 'replace')
            self.assertEqual((text['before'], text['after']), ('1000', '2000'))
            self.assertTrue(text['left_rects'] and text['right_rects'])
            self.assertEqual([page.get_text() for page in left], before)

    def test_deleted_and_duplicate_blank_pages_preserve_sequence(self):
        with document(['A', '', '', 'B', 'C']) as left, document(['A', '', 'C']) as right:
            pairs = compare_documents(left, right)['pairs']
            self.assertEqual([p['left'] for p in pairs if p['left'] is not None], list(range(5)))
            self.assertEqual([p['right'] for p in pairs if p['right'] is not None], list(range(3)))
            self.assertEqual(sum(p['right'] is None for p in pairs), 2)
            self.assertEqual((pairs[-1]['left'], pairs[-1]['right']), (4,2))

    def test_image_and_vector_changes_are_detected_without_text(self):
        with document(['']) as left, document(['']) as right:
            for doc, color in ((left, 'red'), (right, 'blue')):
                png = io.BytesIO()
                Image.new('RGB', (50, 40), color).save(png, 'PNG')
                doc[0].insert_image((30, 60, 80, 100), stream=png.getvalue())
            right[0].draw_rect((180, 210, 230, 240), fill=(0,0,0))
            changes = compare_documents(left, right)['pairs'][0]['changes']
            regions = [pymupdf.Rect(r) for c in changes for r in c['right_rects']]
            self.assertTrue(any(r.contains(pymupdf.Point(50, 80)) for r in regions))
            self.assertTrue(any(r.contains(pymupdf.Point(205, 225)) for r in regions))

    def test_rotated_text_coordinates_are_display_coordinates(self):
        with document(['Old value']) as left, document(['New value']) as right:
            left[0].set_rotation(90)
            right[0].set_rotation(90)
            result = compare_documents(left, right)
            text = next(c for c in result['pairs'][0]['changes'] if c['kind'] == 'replace')
            expected = pymupdf.Rect(right[0].get_text('words')[0][:4]) * right[0].rotation_matrix
            self.assertEqual(text['right_rects'][0], list(expected))

    def test_korean_number_change_and_pure_deletion(self):
        with document(['']) as left, document(['']) as right:
            left[0].insert_text((30,60), '총액 1000원', fontname='korea', fontsize=14)
            right[0].insert_text((30,60), '총액 2000원', fontname='korea', fontsize=14)
            text = next(c for c in compare_documents(left, right)['pairs'][0]['changes'] if c['kind'] == 'replace')
            self.assertIn('1000', text['before'])
            self.assertIn('2000', text['after'])
        with document(['Remove this']) as left, document(['']) as right:
            self.assertTrue(any(c['kind'] == 'delete' for c in compare_documents(left, right)['pairs'][0]['changes']))

    def test_page_size_change_is_detected_even_on_blank_pages(self):
        with document(['']) as left, document(['']) as right:
            right[0].set_mediabox(pymupdf.Rect(0, 0, 320, 400))
            self.assertTrue(any(c['kind'] == 'size' for c in compare_documents(left, right)['pairs'][0]['changes']))

    def test_restricted_copy_and_password_are_enforced_in_real_worker(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            left, right = root/'left.pdf', root/'right.pdf'
            with document(['Old 100']) as doc:
                doc.save(left, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw='reader', owner_pw='owner', permissions=pymupdf.PDF_PERM_PRINT)
            with document(['New 200']) as doc:
                doc.save(right)
            original = left.read_bytes(), right.read_bytes()
            with _open_pdf(left, 'reader') as old, _open_pdf(right) as new:
                with self.assertRaises(PermissionError):
                    compare_documents(old, new)
            for password, success in [('reader', False), ('owner', True)]:
                request = root/'request.json'
                request.write_text(json.dumps({'left':str(left), 'right':str(right), 'left_password':password}), encoding='utf-8')
                process = subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1]/'main.py'), '--compare-worker', str(request)],
                    capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                payload = json.loads((root/'result.json').read_text(encoding='utf-8'))
                self.assertEqual(process.returncode, 0 if success else 1, payload)
                self.assertNotIn('owner', json.dumps(payload))
                if success:
                    self.assertGreater(payload['result']['change_count'], 0)
            self.assertEqual((left.read_bytes(), right.read_bytes()), original)

"""Meaning-based search: passages, the reference tokenizer, ranking and the search bar."""
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np
import pymupdf
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from adf import semantic_search
from adf.app import MainWindow
from adf.semantic_search import Encoder, available, page_passages, rank

MODEL = available()
PARAGRAPHS = ['회의실 예약은 사내 포털에서 신청하고 사용한 뒤에는 정리합니다.',
              '계약을 해지하려면 30일 전에 서면으로 알려야 하며 위약금이 발생할 수 있습니다.',
              '분기별 매출 보고서는 매월 마지막 영업일까지 제출합니다.',
              '신규 직원은 입사 첫 주에 보안 교육을 이수해야 합니다.']


def paragraph_pdf(path, paragraphs=PARAGRAPHS):
    with pymupdf.open() as doc:
        for text in paragraphs:
            doc.new_page(width=420, height=300).insert_textbox(pymupdf.Rect(40, 60, 380, 200), text,
                                                              fontname='korea', fontsize=11)
        doc.save(path)


class KeywordEncoder:
    """A stand-in encoder with one dimension per keyword, so the ranking is predictable."""
    WORDS = ['회의실', '계약', '매출', '보안']
    passage_calls = 0

    def __init__(self, folder=None):
        pass

    def encode(self, texts, kind, cancelled=lambda: False, progress=lambda done: None):
        if kind == 'passage':
            type(self).passage_calls += 1
        vectors = np.array([[1.0 if word in text else 0.0 for word in self.WORDS] + [0.1] for text in texts], np.float32)
        progress(len(texts))
        return vectors/np.linalg.norm(vectors, axis=1, keepdims=True)


class PassageTests(unittest.TestCase):
    def test_blocks_become_passages_and_long_blocks_split_after_sentences(self):
        long = ' '.join(f'{index}번째 문장은 긴 문단을 나누는지 확인합니다.' for index in range(40))
        with pymupdf.open() as doc:
            page = doc.new_page(width=600, height=800)
            page.insert_textbox(pymupdf.Rect(40, 40, 560, 120), PARAGRAPHS[0], fontname='korea', fontsize=11)
            page.insert_textbox(pymupdf.Rect(40, 200, 560, 700), long, fontname='korea', fontsize=9)
            page.insert_text((40, 760), 'x')
            texts = [text for _, text in page_passages(page)]
        self.assertEqual(texts[0], PARAGRAPHS[0])
        self.assertGreater(len(texts), 2)
        self.assertTrue(all(len(text) < semantic_search.PASSAGE_CHARACTERS+60 for text in texts))
        self.assertEqual(' '.join(texts[1:]), long)

    def test_pages_without_text_have_no_passages(self):
        with pymupdf.open() as doc:
            page = doc.new_page()
            page.draw_rect((50, 50, 200, 200), fill=(0, 0, 0))
            self.assertEqual(page_passages(page), [])

    def test_rank_orders_by_similarity(self):
        passages = np.eye(3, dtype=np.float32)
        query = np.array([.2, .9, .5], np.float32)
        self.assertEqual(rank(query, passages), [1, 2, 0])
        self.assertEqual(rank(query, passages, limit=1), [1])


@unittest.skipUnless(MODEL, 'Run scripts/prepare-ocr.py for the search model')
class ModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.encoder = Encoder()

    def test_tokens_match_the_reference_tokenizer(self):
        # Expected ids come from the model's tokenizer.json read by the tokenizers library.
        fixtures = {
            'query: 계약 해지 조건': [0, 41, 1294, 12, 99414, 8280, 1190, 116551, 2],
            'passage: ＡＢＣ　１２３ 全角 문자와 ㈜, ①, ℃, ㎏ 기호':
                [0, 46692, 12, 47457, 37638, 6, 2476, 12514, 149050, 2020, 15, 2688, 247, 106, 4, 16207, 441, 4, 5279, 7463, 7346, 2],
            'passage: 이모지 😀👍 그리고 줄바꿈\n탭\t공백   여러 개':
                [0, 46692, 12, 1504, 7923, 1190, 21119, 118280, 14238, 29800, 12775, 223343, 6, 243421, 9045, 23527, 37747, 16538, 2],
        }
        for text, ids in fixtures.items():
            with self.subTest(text=text):
                self.assertEqual(self.encoder.tokens(text), ids)
        self.assertEqual(self.encoder.tokens('query: 계약 해지 조건  '), fixtures['query: 계약 해지 조건'])
        self.assertEqual(len(self.encoder.tokens('passage: '+'긴 문장 '*2000)), semantic_search.MAX_TOKENS)

    def test_model_loads_from_a_folder_with_a_korean_name(self):
        # An installation under a Korean Windows user name has such a path.
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)/'사용자 이름'
            folder.mkdir()
            (folder/semantic_search.VOCABULARY_FILE).write_bytes((semantic_search.model_folder()/semantic_search.VOCABULARY_FILE).read_bytes())
            # The ONNX model is large; this checks the tokenizer, which could not open such a path.
            with patch('onnxruntime.InferenceSession', return_value=self.encoder.session):
                encoder = Encoder(folder)
            self.assertEqual(encoder.tokens('query: 계약 해지 조건'), self.encoder.tokens('query: 계약 해지 조건'))

    def test_paraphrases_and_other_languages_find_the_related_passage(self):
        vectors = self.encoder.encode(PARAGRAPHS, 'passage')
        self.assertTrue(np.allclose(np.linalg.norm(vectors, axis=1), 1, atol=1e-5))
        for query, expected in [('계약 해지 조건', 1), ('해약하려면 어떻게 해야 하나요', 1),
                                ('how do I terminate the contract', 1), ('보안 교육은 언제 받나요', 3),
                                ('매출 보고 마감일', 2)]:
            with self.subTest(query=query):
                self.assertEqual(rank(self.encoder.encode([query], 'query')[0], vectors)[0], expected)

    def test_encoding_stops_between_batches_when_cancelled(self):
        with self.assertRaises(InterruptedError):
            self.encoder.encode(PARAGRAPHS*10, 'passage', cancelled=lambda: True)


class SearchBarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='adf-semantic-test-')
        self.root = Path(self.directory.name)
        paragraph_pdf(self.root/'source.pdf')
        patcher = patch.object(semantic_search, 'Encoder', KeywordEncoder)
        patcher.start()
        self.addCleanup(patcher.stop)
        KeywordEncoder.passage_calls = 0
        self.window = MainWindow(smoke=True)
        self.window.show()
        self.assertTrue(self.window.open_path(self.root/'source.pdf'))
        self.window.show_search()

    def tearDown(self):
        with patch.object(self.window, 'maybe_save', return_value=True):
            self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.directory.cleanup()

    def wait(self):
        deadline = time.monotonic()+10
        while (self.window.semantic_job is not None or self.window.semantic_timer.isActive()
               or self.window.search_timer.isActive()) and time.monotonic() < deadline:
            QTest.qWait(20)

    def search(self, query):
        self.window.search_input.setText(query)
        self.window.run_semantic_search()
        self.wait()
        return [page for page, _ in self.window.search_matches]

    def test_meaning_search_ranks_pages_and_indexes_once_per_revision(self):
        self.window.semantic_toggle.setChecked(True)
        self.assertEqual(self.window.search_input.placeholderText(), '찾고 싶은 내용을 문장으로 입력하세요')
        self.assertEqual(self.search('계약 문의')[0], 1)
        self.assertEqual(self.window.current, 1)
        self.assertEqual(self.window.search_count.text(), f'1 / {len(self.window.search_matches)}')
        self.assertEqual(self.search('보안 점검')[0], 3)
        self.assertEqual(KeywordEncoder.passage_calls, 1, 'Passages are embedded once per document revision')
        self.assertTrue(self.window.edit(lambda: self.window.document.rotate([0], 90)))
        self.assertEqual(self.search('매출 현황')[0], 2)
        self.assertEqual(KeywordEncoder.passage_calls, 2, 'An edit indexes the document again')

    def test_typing_waits_escape_cancels_and_exact_search_still_works(self):
        self.window.semantic_toggle.setChecked(True)
        self.window.search_input.setText('계약')
        self.assertTrue(self.window.semantic_delay.isActive())
        self.assertIsNone(self.window.semantic_job)
        self.window.escape()
        self.assertFalse(self.window.semantic_delay.isActive())
        self.assertTrue(self.window.searchbar.isHidden())
        self.window.show_search()
        self.window.semantic_toggle.setChecked(False)
        self.assertEqual(self.window.search_input.placeholderText(), '문서에서 찾을 텍스트')
        self.window.search_input.setText('위약금')
        self.window.start_search()
        QTest.qWait(200)
        self.wait()
        self.assertEqual([page for page, _ in self.window.search_matches], [1])
        self.assertEqual(KeywordEncoder.passage_calls, 0)

    def test_documents_without_text_explain_the_limit(self):
        with pymupdf.open() as doc:
            doc.new_page().draw_rect((50, 50, 200, 200), fill=(0, 0, 0))
            doc.save(self.root/'scan.pdf')
        self.assertTrue(self.window.open_path(self.root/'scan.pdf'))
        self.window.show_search()
        self.window.semantic_toggle.setChecked(True)
        self.assertEqual(self.search('계약'), [])
        self.assertEqual(self.window.search_count.text(), '찾을 글자가 없습니다')
        self.assertEqual(KeywordEncoder.passage_calls, 0)

    def test_closing_the_document_forgets_the_index_and_model(self):
        self.window.semantic_toggle.setChecked(True)
        self.search('계약')
        self.assertIsNotNone(self.window.semantic_index)
        self.assertIsNotNone(self.window.semantic_encoder)
        self.window.close_document()
        self.assertIsNone(self.window.semantic_index)
        self.assertIsNone(self.window.semantic_encoder)


@unittest.skipUnless(MODEL, 'Run scripts/prepare-ocr.py for the search model')
class BundledModelSearchTests(unittest.TestCase):
    def test_release_check_finds_a_paraphrase_with_the_real_model(self):
        from adf.release_checks import check_semantic_search
        app = QApplication.instance() or QApplication([])
        app.setQuitOnLastWindowClosed(False)
        with tempfile.TemporaryDirectory(prefix='adf-semantic-release-') as directory:
            window = MainWindow(smoke=True)
            try:
                window.show()
                result = check_semantic_search(window, Path(directory), Path(directory)/'smoke.json')
                self.assertTrue(result['semantic_search'])
            finally:
                with patch.object(window, 'maybe_save', return_value=True):
                    window.close()
                window.deleteLater()
                app.processEvents()


if __name__ == '__main__':
    unittest.main()

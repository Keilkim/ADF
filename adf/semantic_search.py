"""Offline meaning-based search in the open document with multilingual-e5-small.

Passages are the text blocks of each page. ONNX Runtime, already bundled for
OCR, runs the encoder on the CPU, and SentencePiece reproduces the model's
tokenizer. No text leaves the computer.
"""
from __future__ import annotations

from pathlib import Path
import re
import sys

import numpy as np
from PySide6.QtCore import QThread, Signal

MODEL_FILE = 'multilingual-e5-small.onnx'
VOCABULARY_FILE = 'sentencepiece.bpe.model'
MAX_TOKENS = 512
BATCH = 16
# About 200 tokens: short passages point to a paragraph more precisely.
PASSAGE_CHARACTERS = 600
LIMIT = 10


def model_folder() -> Path:
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent.parent))
    return root/'SEARCH_MODEL' if getattr(sys, 'frozen', False) else root/'.tools/search-model'


def available(folder: Path | None = None) -> bool:
    folder = folder or model_folder()
    return (folder/MODEL_FILE).is_file() and (folder/VOCABULARY_FILE).is_file()


def page_passages(page) -> list[tuple[tuple[float, float, float, float], str]]:
    """Text blocks of one page; long blocks are split after sentence ends."""
    passages = []
    for x0, y0, x1, y1, text, _, kind in page.get_text('blocks', sort=True):
        text = ' '.join(text.split())
        if kind != 0 or len(text.replace(' ', '')) < 2:
            continue
        current = ''
        for sentence in re.split(r'(?<=[.!?。！？])\s+', text):
            if current and len(current) + len(sentence) >= PASSAGE_CHARACTERS:
                passages.append(((x0, y0, x1, y1), current))
                current = sentence
            else:
                current = f'{current} {sentence}'.strip()
        passages.append(((x0, y0, x1, y1), current))
    return passages


class Encoder:
    """Mean-pooled, normalized sentence embeddings."""

    def __init__(self, folder: Path | None = None):
        import onnxruntime
        import sentencepiece
        folder = folder or model_folder()
        self.session = onnxruntime.InferenceSession(str(folder/MODEL_FILE), providers=['CPUExecutionProvider'])
        self.vocabulary = sentencepiece.SentencePieceProcessor(model_file=str(folder/VOCABULARY_FILE))
        self.dimension = self.session.get_outputs()[0].shape[-1]

    def tokens(self, text: str) -> list[int]:
        # XLM-RoBERTa ids: <s>=0, <pad>=1, </s>=2, <unk>=3, then SentencePiece ids
        # shifted by one. Stripping matters: SentencePiece drops a trailing space
        # that the model's reference tokenizer would encode.
        pieces = [index + 1 if index else 3 for index in self.vocabulary.encode(text.strip())]
        return [0, *pieces[:MAX_TOKENS - 2], 2]

    def encode(self, texts, kind: str, cancelled=lambda: False, progress=lambda done: None) -> np.ndarray:
        """kind is 'query' or 'passage', the prefixes the model was trained with."""
        rows = [self.tokens(f'{kind}: {text.strip()}') for text in texts]
        vectors = np.zeros((len(rows), self.dimension), np.float32)
        # Batching texts of similar length limits padding.
        order = sorted(range(len(rows)), key=lambda index: len(rows[index]))
        for start in range(0, len(order), BATCH):
            if cancelled():
                raise InterruptedError('검색 준비를 취소했습니다.')
            batch = order[start:start+BATCH]
            ids = np.ones((len(batch), max(len(rows[index]) for index in batch)), np.int64)
            mask = np.zeros_like(ids)
            for line, index in enumerate(batch):
                ids[line, :len(rows[index])] = rows[index]
                mask[line, :len(rows[index])] = 1
            hidden = self.session.run(None, dict(input_ids=ids, attention_mask=mask,
                                                 token_type_ids=np.zeros_like(ids)))[0]
            pooled = (hidden*mask[..., None]).sum(axis=1)/mask.sum(axis=1, keepdims=True)
            vectors[batch] = pooled/np.linalg.norm(pooled, axis=1, keepdims=True)
            progress(min(start+BATCH, len(order)))
        return vectors


def rank(query: np.ndarray, passages: np.ndarray, limit: int = LIMIT) -> list[int]:
    """Passage indices, most similar first."""
    return [int(index) for index in np.argsort(-(passages @ query), kind='stable')[:limit]]


class SearchJob(QThread):
    """Embeds the passages once per document revision, then the query, off the UI thread.

    ONNX Runtime releases the GIL while it runs. Cancellation takes effect
    between batches; the owner reads the results after finished.
    """
    progressed = Signal(int, int)

    def __init__(self, encoder, passages, vectors, query, token, parent=None):
        super().__init__(parent)
        self.encoder, self.passages, self.vectors = encoder, passages, vectors
        self.query, self.token = query, token
        self.cancelled = False
        self.order, self.error = [], None

    def run(self):
        try:
            if self.encoder is None:
                self.encoder = Encoder()
            if self.vectors is None:
                self.vectors = self.encoder.encode(self.passages, 'passage', lambda: self.cancelled,
                                                   lambda done: self.progressed.emit(done, len(self.passages)))
            self.order = rank(self.encoder.encode([self.query], 'query')[0], self.vectors)
        except Exception as error:
            self.error = error

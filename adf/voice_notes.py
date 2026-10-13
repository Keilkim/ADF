"""Immutable voice memos stored as standard PDF file attachments.

The annotation appearance stays hidden: the reader draws the marker separately
so visibility is a reading preference, never a change to the document.
"""
from __future__ import annotations

import io
import json
import math
import uuid
import wave

import pymupdf

SUBJECT = 'XDF voice memo v1'
MAX_AUDIO_BYTES = 64 * 1024 * 1024


def read_note(annotation):
    if annotation.type[0] != pymupdf.PDF_ANNOT_FILE_ATTACHMENT or annotation.info.get('subject') != SUBJECT:
        return None
    try:
        data = json.loads(annotation.info['content'])
        point = data['point']
        if (data.get('version') != 1 or not isinstance(data['id'], str)
                or len(point) != 2 or not all(math.isfinite(float(v)) for v in point)
                or not isinstance(data.get('text', ''), str)):
            return None
        data.setdefault('status', 'ready')
        data.setdefault('context', '')
        data.setdefault('raw_text', '')
        data.setdefault('error', '')
        data.setdefault('duration', 0.)
        if (data['status'] not in ('pending', 'ready', 'error')
                or not all(isinstance(data[key], str) for key in ('context', 'raw_text', 'error'))
                or not isinstance(data['duration'], (int, float)) or not math.isfinite(data['duration'])):
            return None
        return dict(data, xref=annotation.xref)
    except (KeyError, ValueError, TypeError):
        return None


def page_notes(page):
    return [data for annot in page.annots(types=[pymupdf.PDF_ANNOT_FILE_ATTACHMENT]) or ()
            if (data := read_note(annot)) is not None]


def find_note(doc, identifier):
    page_xref, identifier = identifier if isinstance(identifier, tuple) else (None, identifier)
    for page in doc:
        if page_xref is not None and page.xref != page_xref:
            continue
        for annotation in page.annots(types=[pymupdf.PDF_ANNOT_FILE_ATTACHMENT]) or ():
            data = read_note(annotation)
            if data and data['id'] == identifier:
                return page, annotation, data
    return None


def add_note(page, point, audio, duration, context=''):
    if not audio or len(audio) > MAX_AUDIO_BYTES:
        raise ValueError('녹음 데이터가 없거나 너무 큽니다.')
    with wave.open(io.BytesIO(audio), 'rb') as recording:
        if recording.getnframes() < recording.getframerate() // 5:
            raise ValueError('녹음이 너무 짧습니다. 조금 더 말씀해 주세요.')
        duration = recording.getnframes() / recording.getframerate()
    point = pymupdf.Point(point)
    if not point in page.rect * page.derotation_matrix:
        raise ValueError('PDF 페이지 안에 마킹해 주세요.')
    identifier = uuid.uuid4().hex
    data = dict(version=1, id=identifier, point=[point.x, point.y],
                duration=duration, context=context[:2400], text='', raw_text='',
                status='pending', error='')
    annotation = page.add_file_annot(point, audio, f'voice-{identifier}.wav',
                                     desc='XDF 음성 메모', icon='PushPin')
    annotation.set_info(title='XDF 음성 메모', subject=SUBJECT,
                        content=json.dumps(data, ensure_ascii=False))
    annotation.set_flags(pymupdf.PDF_ANNOT_IS_NO_VIEW)
    annotation.update()
    return identifier


def complete_note(annotation, text, raw_text, error=''):
    data = read_note(annotation)
    if data is None:
        raise ValueError('음성 메모를 찾을 수 없습니다.')
    data.pop('xref', None)
    data.update(text=text.strip(), raw_text=raw_text.strip(), error=error,
                status='ready' if not error else 'error')
    annotation.set_info(content=json.dumps(data, ensure_ascii=False))
    annotation.update()

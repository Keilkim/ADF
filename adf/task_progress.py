"""Atomic process progress, based only on completed work and known totals."""
import json
from pathlib import Path
import time


class ProgressWriter:
    def __init__(self, path):
        self.path = Path(path)
        self.previous_stage = None
        self.last_write = 0

    def __call__(self, stage, completed=None, total=None, detail='', unit=''):
        now = time.monotonic()
        if stage == self.previous_stage and completed != total and now-self.last_write < .08:
            return
        data = dict(stage=stage, completed=completed, total=total, detail=detail, unit=unit)
        temporary = self.path.with_suffix('.pending')
        temporary.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        try:
            temporary.replace(self.path)
        except PermissionError:
            return  # A later update retries if Windows briefly locks the file.
        self.previous_stage = stage
        self.last_write = now


def write_result(path, data):
    path = Path(path)
    temporary = path.with_suffix('.pending')
    temporary.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    temporary.replace(path)

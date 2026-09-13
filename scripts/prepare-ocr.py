"""Build-time download of the OCR and search models with publisher SHA-256 verification."""
import hashlib
from pathlib import Path
import shutil
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from adf.ocr_models import MODELS, SEARCH_MODELS


def fetch(models, folder):
    folder.mkdir(parents=True, exist_ok=True)
    for model in models:
        target = folder/model['name']
        if not target.is_file():
            temporary = target.with_suffix('.partial')
            print('Downloading '+model['name'], flush=True)
            with urllib.request.urlopen(model['url'], timeout=120) as source, temporary.open('wb') as output:
                shutil.copyfileobj(source, output, 1024*1024)
            temporary.replace(target)
        with target.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != model['sha256']:
                raise RuntimeError('Model checksum mismatch: '+model['name'])


def main():
    fetch(MODELS, ROOT/'.tools/ocr-models')
    fetch(SEARCH_MODELS, ROOT/'.tools/search-model')
    print('Offline OCR and search models verified.')


if __name__ == '__main__':
    main()

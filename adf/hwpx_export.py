"""App integration of the pdf2hwpx component: credentials and the worker process."""
from __future__ import annotations

import json
import os
from pathlib import Path

from .pdf2hwpx import ConversionError, DEFAULT_MODEL, GeminiClient, RelayClient, convert_document


def make_client(options):
    provider = options['provider']
    if provider == 'local':
        return None
    model = options.get('model', DEFAULT_MODEL)
    if provider == 'gemini':
        from .hwpx_credentials import load_developer_key
        return GeminiClient(load_developer_key(), model)
    return RelayClient(options.get('service_url', ''), os.environ.get('XDF_HWPX_SERVICE_TOKEN', ''), model)


def hwpx_worker(request_path):
    path = Path(request_path)
    result = dict(ok=False, error='HWPX 변환을 완료하지 못했습니다.')
    try:
        task = json.loads(path.read_text(encoding='utf-8'))
        options = task['options']
        client = make_client(options)
        if task.get('operation') == 'check':
            result = dict(ok=True, result=client.check() if client else dict(ok=True, model='local'))
        else:
            def progress(data):
                temporary = path.parent / 'progress.tmp'
                temporary.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
                try:
                    temporary.replace(path.parent / 'progress.json')
                except PermissionError:
                    # Windows may deny replacement while the UI reads the old
                    # progress file. The next update will publish fresh data.
                    # A display update must not discard a completed conversion.
                    pass
            converted = convert_document(path.parent / 'document.pdf', path.parent / 'output.hwpx',
                provider=options['provider'], model=options.get('model', DEFAULT_MODEL), client=client,
                page_numbers=task.get('page_numbers'), progress=progress)
            (path.parent / 'conversion.json').write_text(json.dumps(converted, ensure_ascii=False, indent=2), encoding='utf-8')
            result = dict(ok=True, result=converted)
    except (ConversionError, PermissionError) as error:
        result = dict(ok=False, error=str(error))
    except ImportError:
        result = dict(ok=False, error='HWPX 모듈을 불러오지 못했습니다. python-hwpx가 포함된 XDF 빌드를 사용해 주세요.')
    except Exception:
        # Never print exception bodies, page content, requests or credentials.
        pass
    (path.parent / 'result.json').write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
    return 0 if result['ok'] else 1

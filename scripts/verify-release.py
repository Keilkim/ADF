"""Audit working source, source ZIP, frozen code, resources and release hashes.

Only the requested JSON report is written. No packaged code is executed.
"""
import argparse
import hashlib
import json
import marshal
from pathlib import Path
import re
import types
import zipfile

from PyInstaller.archive.readers import CArchiveReader
from release_common import source_files


ROOT = Path(__file__).resolve().parents[1]


def normalize(code):
    return code.replace(co_filename='<audit>', co_consts=tuple(
        normalize(value) if isinstance(value, types.CodeType) else value for value in code.co_consts))


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def verify():
    version = re.search(r"__version__ = '([^']+)'", (ROOT/'adf/__init__.py').read_text(encoding='utf-8')).group(1)
    release = ROOT/'release'
    files = source_files(ROOT)
    with zipfile.ZipFile(release/f'ADF-Source-{version}.zip') as archive:
        assert archive.testzip() is None, 'Source archive CRC failure'
        assert set(archive.namelist()) == {'ADF/'+name for name in files}, 'Source archive file list mismatch'
        for name, path in files.items():
            assert path.read_bytes() == archive.read('ADF/'+name), f'Unpackaged source change: {name}'
    built = ROOT/'dist/ADF'
    for prefix in ('ADF-Source', 'ADF-ThirdParty-Sources'):
        filename = f'{prefix}-{version}.zip'
        assert digest(built/'_internal/SOURCES'/filename) == digest(release/filename), f'Installed sources differ: {filename}'
    for line in (built/'_internal/SOURCES/SHA256SUMS.txt').read_text(encoding='utf-8').splitlines():
        expected, name = line.split('  ', 1)
        assert digest(built/'_internal/SOURCES'/name) == expected, f'Bundled source checksum mismatch: {name}'
    assert (built/'_internal/LICENSES/index.html').is_file(), 'Readable license entry point missing'
    container = CArchiveReader(str(built/'ADF.exe'))
    pyz = container.open_embedded_archive(next(name for name, entry in container.toc.items() if entry[-1] == 'z'))
    count = 0
    for name, path in files.items():
        if not name.startswith('adf/') or path.suffix != '.py':
            continue
        module = name[:-3].replace('/', '.').removesuffix('.__init__')
        assert module in pyz.toc, f'Missing frozen module: {module}'
        assert normalize(pyz.extract(module)) == normalize(compile(path.read_bytes(), '<audit>', 'exec', optimize=0)), f'Stale frozen module: {module}'
        count += 1
    assert normalize(marshal.loads(container.extract('main'))) == normalize(compile((ROOT/'main.py').read_bytes(), '<audit>', 'exec', optimize=0))
    data_count = 0
    for name, path in files.items():
        if name.split('/')[0] in ('assets', 'docs', 'LICENSES') or name == 'README.md':
            target = built/'_internal'/name
            assert target.is_file() and digest(target) == digest(path), f'Stale frozen resource: {name}'
            data_count += 1
    manifest = json.loads((ROOT/'LICENSES/build-manifest.json').read_text(encoding='utf-8'))
    assert manifest['version'] == version
    for model in manifest.get('ocr_models', []):
        assert digest(built/'_internal/OCR_MODELS'/model['name']) == model['sha256'], 'OCR model mismatch: '+model['name']
    assert {path.name for path in (built/'_internal').rglob('*.onnx')} == {model['name'] for model in manifest['ocr_models']}, 'Unexpected bundled model'
    assert not list((built/'_internal').rglob('opencv_videoio_ffmpeg*')), 'Unused video codecs bundled'
    with zipfile.ZipFile(release/f'ADF-ThirdParty-Sources-{version}.zip') as archive:
        assert archive.testzip() is None, 'Third-party archive CRC failure'
        assert archive.read('build-manifest.json') == (ROOT/'LICENSES/build-manifest.json').read_bytes()
        for source in manifest['source_archives']:
            with archive.open(source['filename']) as stream:
                assert hashlib.file_digest(stream, 'sha256').hexdigest() == source['sha256'], source['filename']
    checksums = {}
    for line in (release/f'SHA256SUMS-{version}.txt').read_text(encoding='utf-8').splitlines():
        expected, name = line.split('  ', 1)
        assert digest(release/name) == expected, f'Release checksum mismatch: {name}'
        checksums[name] = expected
    assert f'ADF-Setup-{version}.exe' in checksums, 'Installer missing from release checksums'
    return dict(ok=True, version=version, source_files=len(files), frozen_modules=count,
                frozen_resources=data_count, source_and_executable_match=True, sources_bundled=True,
                bundled_ocr_models=len(manifest.get('ocr_models', [])),
                source_archives=len(manifest['source_archives']), checksums=checksums)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    result = verify()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(result, indent=2))

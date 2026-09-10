"""Build the exact sources before freezing, so every installer includes them."""
import hashlib
import json
from pathlib import Path
import re
import zipfile

from release_common import source_files, version

ROOT = Path(__file__).resolve().parents[1]
VERSION = version(ROOT)
DEST = ROOT/'build/source-bundle'


def main():
    if not DEST.resolve().is_relative_to(ROOT.resolve()):
        raise RuntimeError('Source bundle directory must be inside the workspace')
    DEST.mkdir(parents=True, exist_ok=True)
    source = DEST/f'ADF-Source-{VERSION}.zip'
    third_party = DEST/f'ADF-ThirdParty-Sources-{VERSION}.zip'
    with zipfile.ZipFile(source, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, path in source_files(ROOT).items():
            archive.write(path, 'ADF/'+name)
    manifest = json.loads((ROOT/'LICENSES/build-manifest.json').read_text(encoding='utf-8'))
    if manifest['version'] != VERSION:
        raise RuntimeError('Collect matching licenses before packaging sources')
    with zipfile.ZipFile(third_party, 'w', compression=zipfile.ZIP_STORED) as archive:
        archive.write(ROOT/'LICENSES/build-manifest.json', 'build-manifest.json')
        for entry in manifest['source_archives']:
            path = ROOT/'.tools/sources'/entry['filename']
            with path.open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != entry['sha256']:
                    raise RuntimeError('Upstream source checksum mismatch: '+entry['filename'])
            archive.write(path, path.name)
    hashes = []
    for path in (source, third_party):
        with path.open('rb') as stream:
            hashes.append(hashlib.file_digest(stream, 'sha256').hexdigest()+'  '+path.name)
    (DEST/'SHA256SUMS.txt').write_text('\n'.join(hashes)+'\n', encoding='utf-8')
    for path in DEST.iterdir():
        if path not in (source, third_party) and re.fullmatch(r'ADF-(Source|ThirdParty-Sources)-\d+\.\d+\.\d+\.zip', path.name):
            if path.is_symlink() or not path.resolve().is_relative_to(DEST.resolve()):
                raise RuntimeError('Unexpected source archive link')
            path.unlink()
    print('Corresponding source archives ready for inclusion in the application.')


if __name__ == '__main__':
    main()

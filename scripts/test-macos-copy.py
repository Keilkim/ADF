"""Check that a signed app survives Finder's filename normalization on copy.

This uses an isolated copy, retaining symlinks with ditto, and rewrites names to
NFD as Finder does. It neither changes the original app nor bypasses Gatekeeper.
Run it before notarization to catch signatures that only work in the build tree.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unicodedata


def verify_copy(application):
    application = Path(application).resolve()
    subprocess.run(['codesign', '--verify', '--deep', '--strict', str(application)], check=True)
    with tempfile.TemporaryDirectory(prefix='adf-install-copy-') as directory:
        copied = Path(directory)/application.name
        subprocess.run(['ditto', str(application), str(copied)], check=True)
        renamed = 0
        for path in sorted(copied.rglob('*'), key=lambda p: len(p.parts), reverse=True):
            name = unicodedata.normalize('NFD', path.name)
            if name != path.name:
                path.rename(path.with_name(name))
                renamed += 1
        subprocess.run(['codesign', '--verify', '--deep', '--strict', str(copied)], check=True)
    return {'ok': True, 'copy_signature_valid': True, 'finder_name_normalization': True,
            'renamed_paths': renamed}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('application', type=Path)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != 'darwin':
        parser.error('Run this check on macOS.')
    result = verify_copy(args.application)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(result))

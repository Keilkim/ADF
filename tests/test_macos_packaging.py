"""A signed Korean resource must remain valid after Finder-style copying."""
import importlib.util
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import unicodedata

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]/'scripts'
sys.path.insert(0, str(SCRIPTS))
from release_common import macos_bundle_entries

spec = importlib.util.spec_from_file_location('macos_copy_check', SCRIPTS/'test-macos-copy.py')
copy_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(copy_check)


def test_bundle_entries_keep_input_paths_and_relative_links():
    source = '/checkout/docs/사용안내.html'
    entries = [('docs/사용안내.html', source, 'DATA'),
               ('help/사용안내.html', '../docs/사용안내.html', 'SYMLINK')]
    data, link = macos_bundle_entries(entries)
    assert data[1] == source
    assert unicodedata.is_normalized('NFD', data[0])
    assert link[1] == '../'+data[0]
    assert macos_bundle_entries([('assets/logo.png', '/checkout/logo.png', 'DATA')]) == [
        ('assets/logo.png', '/checkout/logo.png', 'DATA')]


@pytest.mark.skipif(sys.platform != 'darwin', reason='macOS code signing')
def test_korean_resource_signature_survives_finder_normalization(tmp_path):
    def signed_app(name, destination):
        app = tmp_path/name
        (app/'Contents/MacOS').mkdir(parents=True)
        (app/'Contents/Resources/docs').mkdir(parents=True)
        shutil.copyfile('/usr/bin/true', app/'Contents/MacOS/probe')
        (app/'Contents/MacOS/probe').chmod(0o755)
        (app/'Contents/Info.plist').write_bytes(plistlib.dumps({
            'CFBundleIdentifier': 'io.github.keilkim.adf.copy-test',
            'CFBundleExecutable': 'probe', 'CFBundlePackageType': 'APPL'}))
        (app/'Contents/Resources'/destination).write_text('Korean help fixture', encoding='utf-8')
        subprocess.run(['codesign', '--force', '--sign', '-', str(app)], check=True, capture_output=True)
        return app

    composed = 'docs/사용안내.html'
    # Reproduce the shipped failure: the original verifies but a Finder-style
    # copy changes the sealed spelling, so added/missing resources are detected.
    original = signed_app('Composed.app', composed)
    with pytest.raises(subprocess.CalledProcessError):
        copy_check.verify_copy(original)
    destination, _, _ = macos_bundle_entries([(composed, '/checkout/help.html', 'DATA')])[0]
    fixed = signed_app('Decomposed.app', destination)
    result = copy_check.verify_copy(fixed)
    assert result['copy_signature_valid']
    assert result['renamed_paths'] == 0

"""Update patches contain only what changed and reuse the installed sources."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from release_common import third_party_manifest, write_third_party_archive  # noqa: E402

spec = importlib.util.spec_from_file_location('make_update_patches', ROOT/'scripts/make-update-patches.py')
patches = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patches)


def files(**entries):
    return {'files': {name.replace('__', '/'): {'size': len(value), 'sha256': value*8} for name, value in entries.items()}}


class PatchPlanTests(unittest.TestCase):
    def test_only_changed_files_are_shipped_and_removed_files_deleted(self):
        base = files(**{'ADF.exe': 'a', '_internal__same.pyd': 'b', '_internal__gone.pyd': 'c'})
        new = files(**{'ADF.exe': 'd', '_internal__same.pyd': 'b', '_internal__new.pyd': 'e'})
        self.assertEqual(patches.plan(base, new), (['ADF.exe', '_internal/new.pyd'], [], ['_internal/gone.pyd']))

    def test_renamed_source_archive_is_copied_from_the_installation(self):
        base = files(**{'_internal__SOURCES__ADF-ThirdParty-Sources-0.3.28.zip': 'f',
                        '_internal__SOURCES__ADF-Source-0.3.28.zip': 'g', 'ADFShell-0.3.28.dll': 'h'})
        new = files(**{'_internal__SOURCES__ADF-ThirdParty-Sources-0.3.29.zip': 'f',
                       '_internal__SOURCES__ADF-Source-0.3.29.zip': 'i', 'ADFShell-0.3.29.dll': 'j'})
        ship, copy, delete = patches.plan(base, new)
        self.assertEqual(ship, ['_internal/SOURCES/ADF-Source-0.3.29.zip'])
        self.assertEqual(copy, [('_internal/SOURCES/ADF-ThirdParty-Sources-0.3.29.zip',
                                 '_internal/SOURCES/ADF-ThirdParty-Sources-0.3.28.zip', 'f'*8)])
        # The installer removes other versions' archives and DLLs after copying.
        self.assertEqual(delete, [])

    def test_a_file_that_the_patch_replaces_is_never_a_copy_source(self):
        base = files(**{'a.dll': 'k', 'b.dll': 'l'})
        new = files(**{'a.dll': 'm', 'c.dll': 'k', 'd.dll': 'l', 'b.dll': 'l'})
        ship, copy, delete = patches.plan(base, new)
        self.assertEqual(ship, ['a.dll', 'c.dll'])
        self.assertEqual(copy, [('d.dll', 'b.dll', 'l'*8)])

    def test_installer_sections_check_reused_files_before_installing(self):
        sections, checks = patches.installer_sections('0.3.28', '0.3.29', ['_internal/x.pyd'],
            [('_internal/SOURCES/ADF-ThirdParty-Sources-0.3.29.zip', '_internal/SOURCES/ADF-ThirdParty-Sources-0.3.28.zip', 'f'*64)],
            ['_internal/gone.pyd'])
        self.assertIn(r'Source: "{#AppBuildDir}\_internal\x.pyd"; DestDir: "{app}\_internal"; Flags: ignoreversion', sections)
        self.assertIn(r'Source: "{app}\_internal\SOURCES\ADF-ThirdParty-Sources-0.3.28.zip"; DestDir: "{app}\_internal\SOURCES"; '
                      r'DestName: "ADF-ThirdParty-Sources-0.3.29.zip"; Flags: external ignoreversion', sections)
        self.assertTrue(sections.rstrip().endswith('[InstallDelete]\nType: files; Name: "{app}\\_internal\\gone.pyd"'))
        self.assertIn("PatchFileMatches('_internal\\SOURCES\\ADF-ThirdParty-Sources-0.3.28.zip', '" + 'f'*64 + "')", checks)
        with self.assertRaises(ValueError):
            patches.installer_sections('0.3.28', '0.3.29', ['{app}.dll'], [], [])

    def test_file_manifest_records_every_file(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder)/'sub').mkdir()
            (Path(folder)/'sub/a.txt').write_bytes(b'abc')
            manifest = patches.file_manifest(Path(folder), '0.3.29')
        self.assertEqual(manifest, {'version': '0.3.29', 'files': {'sub/a.txt': {'size': 3, 'sha256': hashlib.sha256(b'abc').hexdigest()}}})


class ThirdPartyArchiveTests(unittest.TestCase):
    def test_same_sources_give_the_same_archive_in_every_version(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            source = folder/'library-1.0.tar.gz'
            source.write_bytes(os.urandom(5000))
            entry = {'name': 'library', 'filename': source.name, 'sha256': hashlib.sha256(source.read_bytes()).hexdigest()}
            archives = []
            for version, mtime in (('0.3.28', 1_600_000_000), ('0.3.29', 1_700_000_000)):
                os.utime(source, (mtime, mtime))
                manifest = {'application': 'ADF', 'version': version, 'source_archives': [entry],
                            'native_shell': {'version': version, 'toolchain': 'LLVM'}}
                target = folder/f'ADF-ThirdParty-Sources-{version}.zip'
                write_third_party_archive(target, manifest, folder)
                archives.append(target.read_bytes())
            self.assertEqual(archives[0], archives[1])
            with zipfile.ZipFile(target) as archive:
                recorded = json.loads(archive.read('build-manifest.json'))
                self.assertEqual(archive.read(source.name), source.read_bytes())
        self.assertNotIn('version', recorded)
        self.assertEqual(recorded['native_shell'], {'toolchain': 'LLVM'})
        self.assertEqual(recorded['source_archives'], [entry])

    def test_changed_upstream_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            (folder/'library.tar.gz').write_bytes(b'changed')
            manifest = {'version': '0.3.29', 'source_archives': [{'filename': 'library.tar.gz', 'sha256': '0'*64}]}
            with self.assertRaises(RuntimeError):
                write_third_party_archive(folder/'out.zip', manifest, folder)

    def test_bundled_manifest_omits_only_adf_versions(self):
        manifest = {'application': 'ADF', 'version': '1', 'python': '3.12.10', 'native_shell': {'version': '1', 'linkage': 'static'}}
        self.assertEqual(json.loads(third_party_manifest(manifest)),
                         {'application': 'ADF', 'python': '3.12.10', 'native_shell': {'linkage': 'static'}})


if __name__ == '__main__':
    unittest.main()

# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import os
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, copy_metadata, collect_submodules

root = Path(SPECPATH)
sys.path.insert(0, str(root / 'scripts'))
from release_common import notice_dir, version
notices = notice_dir(root)
if not (notices / 'build-manifest.json').is_file():
    raise RuntimeError('Run scripts/collect-licenses.py before building the application.')
datas = [(str(root / 'assets'), 'assets'), (str(root / 'docs'), 'docs'), (str(notices), 'LICENSES'), (str(root / 'README.md'), '.')]
source_bundle = root / 'build' / 'source-bundle'
if not source_bundle.is_dir() or not (source_bundle / 'SHA256SUMS.txt').is_file():
    raise RuntimeError('Run scripts/package-sources.py before building the application.')
datas.append((str(source_bundle), 'SOURCES'))
ocr_models = root / '.tools' / 'ocr-models'
if not (ocr_models / 'pp_doc_layoutv3.onnx').is_file():
    raise RuntimeError('Run scripts/prepare-ocr.py before building the application.')
datas.append((str(ocr_models), 'OCR_MODELS'))
for package in ('rapidocr', 'rapid_layout', 'rapid_table', 'onnxruntime'):
    # The wheels include demo/default weights that this application never uses.
    # Only the five verified models in OCR_MODELS are distributed.
    datas += collect_data_files(package, excludes=['**/*.onnx'])
    datas += copy_metadata(package.replace('_', '-'))
# These libraries create their default model folders during import, even when
# every model path is supplied. Include the folders for read-only installations.
for package in ('rapid_layout', 'rapid_table'):
    datas.append((str(root / 'assets' / 'ocr-model-directory.txt'), package + '/models'))
for package in ('PyMuPDF', 'PySide6', 'shiboken6', 'Pillow', 'fonttools'):
    datas += copy_metadata(package)
datas += collect_data_files('pymupdf')
binaries = collect_dynamic_libs('pymupdf')
a = Analysis(
    [str(root / 'main.py')], pathex=[str(root)], binaries=binaries, datas=datas,
    hiddenimports=['pymupdf', 'PIL.Image', 'PIL.ImageQt', 'numpy', 'cv2', 'onnxruntime',
                   'rapidocr.main', 'rapidocr.inference_engine.onnxruntime.main',
                   'rapid_layout.inference_engine.onnxruntime.main',
                   'rapid_table.inference_engine.onnxruntime.main'] + collect_submodules('fontTools.ttLib.tables'),
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['PyQt5', 'PyQt6', 'PySide2', 'tkinter', 'matplotlib', 'IPython', 'pytest',
              'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtQml',
              'PySide6.QtQuick', 'PySide6.QtMultimedia', 'PySide6.Qt3DCore',
              'torch', 'torchvision', 'paddle', 'openvino', 'tensorrt', 'MNN'],
    noarchive=False, optimize=0,
)
# QtGui's generic hook collects every installed image/input plugin, including
# QtPdf and Virtual Keyboard. ADF uses MuPDF and ordinary desktop input, so
# exclude those unused plugins and their QML/Quick dependency tree. Keep SVG.
def needed_qt_path(path):
    path = '/' + path.replace('\\', '/').lower()
    basename = path.rsplit('/', 1)[-1]
    # OCR only decodes still images; the optional video codec DLL is unused.
    if basename.startswith('opencv_videoio_ffmpeg'):
        return False
    if basename.startswith(('qt6pdf', 'qt6qml', 'qt6quick', 'qt6virtualkeyboard',
                            'qpdf.', 'libqpdf.', 'qtvirtualkeyboardplugin.', 'libqtvirtualkeyboardplugin.')):
        return False
    if any('/' + name + '.framework/' in path for name in ('qtpdf', 'qtqml', 'qtqmlmeta', 'qtqmlmodels', 'qtqmlworkerscript',
                                                            'qtquick', 'qtvirtualkeyboard', 'qtvirtualkeyboardqml')):
        return False
    return True
def needed_qt_entry(entry):
    dest, source, typecode = entry
    # macOS also links top-level names such as QtQml to framework binaries.
    # Judge those links by their target too, so none is left dangling.
    return needed_qt_path(dest) and (typecode != 'SYMLINK' or needed_qt_path(source))
a.binaries = [entry for entry in a.binaries if needed_qt_entry(entry)]
a.datas = [entry for entry in a.datas if needed_qt_entry(entry)]
pyz = PYZ(a.pure)
# macOS signs every bundled binary with the hardened runtime when a Developer ID is given, ad hoc otherwise.
codesign_identity = (os.environ.get('ADF_CODESIGN_IDENTITY') or None) if sys.platform == 'darwin' else None
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name='ADF', debug=False,
    bootloader_ignore_signals=False, strip=False, upx=False,
    console=False, disable_windowed_traceback=False,
    argv_emulation=False, target_arch=None, codesign_identity=codesign_identity,
    entitlements_file=str(root / 'installer' / 'adf.entitlements') if codesign_identity else None,
    icon=str(root / 'assets' / ('adf.icns' if sys.platform == 'darwin' else 'adf.ico')),
    version=str(root / 'installer' / 'version-info.txt') if sys.platform == 'win32' else None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='ADF')
if sys.platform == 'darwin':
    app = BUNDLE(coll, name='ADF.app', icon=str(root / 'assets' / 'adf.icns'),
                 bundle_identifier='io.github.keilkim.adf',
                 info_plist={
                     'CFBundleName': 'ADF', 'CFBundleDisplayName': 'ADF',
                     'CFBundleShortVersionString': version(root), 'CFBundleVersion': version(root),
                     # PySide6 6.11.2's arm64 modules are built for macOS 15; verify-release.py checks every binary.
                     'LSMinimumSystemVersion': '15.0',
                     'NSHighResolutionCapable': True,
                     'CFBundleDocumentTypes': [{
                         'CFBundleTypeName': 'PDF document', 'CFBundleTypeRole': 'Editor',
                         'LSHandlerRank': 'Alternate', 'LSItemContentTypes': ['com.adobe.pdf'],
                     }],
                 })

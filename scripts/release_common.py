"""The source inputs shared by bundling and release verification."""
import re
import sys

SOURCE_ITEMS = ['main.py', 'adf', 'assets', 'docs', 'tests', 'scripts', 'installer',
                'native-shell', 'ADF.spec', 'README.md', 'LICENSE', 'LICENSES', 'spec.md',
                '.github', '.gitattributes', 'CODE_SIGNING.md', 'CONTRIBUTING.md', 'SECURITY.md',
                'requirements.txt', 'requirements-ocr-lock.txt', 'requirements-build.txt', 'requirements-dev.txt', '.gitignore']


def ocr_packages(root):
    return [line.split('==')[0] for line in (root/'requirements-ocr-lock.txt').read_text(encoding='utf-8').splitlines()
            if line and not line.startswith('#')]


def notice_dir(root):
    """LICENSES records the Windows wheels. Other platforms bundle notices for their own binaries."""
    return root/'LICENSES' if sys.platform == 'win32' else root/'build/licenses'


def version(root):
    return re.search(r"__version__ = '([^']+)'", (root/'adf/__init__.py').read_text(encoding='utf-8')).group(1)


def source_files(root):
    files = {}
    for name in SOURCE_ITEMS:
        path = root/name
        for file in path.rglob('*') if path.is_dir() else [path]:
            if file.is_file() and '__pycache__' not in file.parts and file.suffix not in ('.pyc', '.log'):
                files[file.relative_to(root).as_posix()] = file
    return files

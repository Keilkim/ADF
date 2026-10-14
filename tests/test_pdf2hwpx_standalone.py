"""The converter is a self-contained package: copied alone, it still works."""
import ast
from pathlib import Path
import shutil
import subprocess
import sys

import pymupdf

PACKAGE = Path(__file__).resolve().parents[1] / 'adf' / 'pdf2hwpx'


def test_package_imports_nothing_from_the_app():
    for path in PACKAGE.glob('*.py'):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.level <= 1, f'{path.name} imports outside the package: {ast.unparse(node)}'
                assert not (node.module or '').startswith('adf'), f'{path.name}: {ast.unparse(node)}'
            elif isinstance(node, ast.Import):
                assert not any(alias.name.startswith('adf') for alias in node.names), f'{path.name}'


def test_copied_package_converts_a_pdf_from_the_command_line(tmp_path):
    copy = tmp_path / 'lib' / 'pdf2hwpx'
    shutil.copytree(PACKAGE, copy, ignore=shutil.ignore_patterns('__pycache__'))
    source = tmp_path / 'in.pdf'
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((72, 100), 'A copied converter still converts this page.', fontsize=12)
        page.draw_rect((72, 120, 300, 160), width=.6)
        page.insert_text((80, 145), 'Boxed note', fontsize=11)
        pdf.save(source)
    target = tmp_path / 'out.hwpx'
    # -I keeps the repository (and the app package) off the import path.
    run = subprocess.run([sys.executable, '-I', '-c', 'import sys; sys.path.insert(0, sys.argv[1]); '
                          'from pdf2hwpx.__main__ import main; raise SystemExit(main(sys.argv[2:]))',
                          str(copy.parent), str(source), str(target)],
                         capture_output=True, text=True, encoding='utf-8', cwd=tmp_path, timeout=300)
    assert run.returncode == 0, run.stderr
    assert '"pages": 1' in run.stdout and target.is_file()

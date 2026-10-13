"""Convert a PDF to HWPX from the command line.

    python -m pdf2hwpx input.pdf output.hwpx

(as adf.pdf2hwpx inside this app). No AI is used; scanned pages need the
Gemini backend through the Python API.
"""
import argparse
import json
import sys

from .convert import convert_document


def main(argv=None):
    parser = argparse.ArgumentParser(prog='pdf2hwpx', description=__doc__.splitlines()[0])
    parser.add_argument('source')
    parser.add_argument('target')
    args = parser.parse_args(argv)
    result = convert_document(args.source, args.target)
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    json.dump({key: result[key] for key in ('pages', 'paragraphs', 'tables', 'figures', 'warnings') if key in result},
              sys.stdout, ensure_ascii=False)
    print()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

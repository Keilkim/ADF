"""Render every page of an HWPX file to images with the installed Hancom Office.

Windows only. Needs pywin32 (`py -3 -m pip install pywin32`) and Hancom
Office; it does not import the app, so any Python with pywin32 can run it:

    py -3 scripts/pdf2hwpx/hancom_render.py result.hwpx out/page- [--dpi 96]

Pages are written as out/page-1.bmp, out/page-2.bmp ... (Hancom chooses
BMP whatever the extension). Hancom's own PDF export can wait on a printer
dialog, so page images are used instead.
"""
import argparse
import os

import win32com.client


def render(source, prefix, dpi=96):
    hwp = win32com.client.gencache.EnsureDispatch('HWPFrame.HwpObject')
    # Answer every message box with its default instead of waiting.
    hwp.SetMessageBoxMode(0x00020000 | 0x00000001 | 0x10 | 0x100)
    try:
        hwp.RegisterModule('FilePathCheckDLL', 'FilePathCheckerModule')
    except Exception:
        pass
    try:
        if not hwp.Open(os.path.abspath(source), 'HWPX', 'forceopen:true'):
            raise SystemExit('Hancom could not open ' + source)
        count = hwp.PageCount
        for index in range(count):
            hwp.CreatePageImage(f'{os.path.abspath(prefix)}{index + 1}.png', index, dpi, 24, 'png')
        return count
    finally:
        hwp.Clear(1)
        hwp.Quit()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('source')
    parser.add_argument('prefix')
    parser.add_argument('--dpi', type=int, default=96)
    args = parser.parse_args()
    print(render(args.source, args.prefix, args.dpi))

#!/usr/bin/env python3
"""Compatibility entry point for the original XDF outline builder.

The rejected 0.100 derivative builder is archived in .tools/xdf-fonts/.
This entry point never downloads or transforms an upstream typeface.
"""
from pathlib import Path
import subprocess
import sys

if __name__ == '__main__':
    subprocess.run([sys.executable, str(Path(__file__).with_name('build-xdf-type.py'))], check=True)

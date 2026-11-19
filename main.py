#!/usr/bin/env python3
"""PyInstaller 打包入口：等价于 python -m linftp。"""
import sys

from linftp.__main__ import main

if __name__ == "__main__":
    sys.exit(main())

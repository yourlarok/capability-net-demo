#!/usr/bin/env python3
"""仓库内入口：等价于安装后的 `capnet` 命令（见 capnet/cli.py）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from capnet.cli import main  # noqa: E402

if __name__ == "__main__":
    main()

# This Python file uses the following encoding: utf-8

"""离线自检入口：``python dev_tools/ai_recovery_selftest.py``。

等价于 ``python -m module.ai_recovery.selftest``，不需要设备与网络。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from module.ai_recovery.selftest import main  # noqa: E402

if __name__ == '__main__':
    sys.exit(main())

# This file uses the following encoding: utf-8
"""Workbench 一站式自检：引擎 / 功能层 / IO 三层全跑。

用法（仓库根目录）：
    .\\toolkit\\python.exe tasks\\Workbench\\run_all.py
    .\\toolkit\\python.exe tasks\\Workbench\\run_all.py --live 127.0.0.1:16448
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

CASES = [
    ("engine  ", "selftest.py"),
    ("features", "selftest_features.py"),
    ("io      ", "selftest_io.py"),
    ("web     ", "selftest_web.py"),
    ("calib   ", "selftest_calibrate.py"),
    ("paz     ", "selftest_paz.py"),
    ("panel   ", "selftest_panel.py"),
    ("domains ", "selftest_domains.py"),
]


def main() -> int:
    extra = sys.argv[1:]
    failed = []
    total_pass = total_fail = 0
    for label, name in CASES:
        cmd = [PY, os.path.join(HERE, name)]
        if name == "selftest_io.py":
            cmd += extra
        proc = subprocess.run(cmd, capture_output=True)
        out = (proc.stdout + proc.stderr).decode("utf-8", "replace")
        tail = [l for l in out.splitlines() if "TOTAL" in l]
        line = tail[-1] if tail else out.strip().splitlines()[-1] if out.strip() else "no output"
        line = line.encode("utf-8", "replace").decode("utf-8", "replace")
        print("%s %s  rc=%d" % (label, line, proc.returncode))
        for token in line.replace(",", " ").split():
            pass
        if "PASS" in line and "FAIL" in line:
            try:
                parts = line.split("TOTAL")[-1].split()
                n_pass = int(parts[parts.index("PASS") + 1])
                n_fail = int(parts[parts.index("FAIL") + 1])
                total_pass += n_pass
                total_fail += n_fail
            except (ValueError, IndexError):
                pass
        if proc.returncode != 0:
            failed.append(name)
            print("---- output tail ----")
            print("\n".join(out.strip().splitlines()[-15:]).encode(
                "utf-8", "replace").decode("utf-8", "replace"))

    print("\nWORKBENCH SUITE: %s   PASS %d  FAIL %d  (%d/%d files)"
          % ("PASSED" if not failed else "FAILED", total_pass, total_fail,
             len(CASES) - len(failed), len(CASES)))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())

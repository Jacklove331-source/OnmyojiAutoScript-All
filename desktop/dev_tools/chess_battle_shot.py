"""Play-time helper: wait for Chess battle mode and grab a screenshot."""

import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(ROOT, 'log', '2026-10-25_oas1.txt')
OUT_DIR = os.path.join(ROOT, 'log', 'chess_ref')
os.makedirs(OUT_DIR, exist_ok=True)


def tail(n=40):
    with open(LOG, encoding='utf-8', errors='ignore') as file:
        lines = file.readlines()
    return [line.strip() for line in lines[-n:]]


def shoot(tag):
    result = subprocess.run(
        [sys.executable, os.path.join(ROOT, 'dev_tools', 'roster_scan.py'), 'shot'],
        cwd=ROOT, capture_output=True, text=True, encoding='utf-8', errors='ignore',
    )
    for line in (result.stdout or '').splitlines():
        if line.startswith('saved:'):
            path = line.split('saved:', 1)[1].strip()
            target = os.path.join(OUT_DIR, f'{tag}_{os.path.basename(path)}')
            os.replace(path, target)
            print('saved', target)
            return target
    print('shot failed', result.stdout, result.stderr)
    return None


deadline = time.time() + 180
while time.time() < deadline:
    lines = tail(60)
    battle = any('[战]' in line for line in lines[-25:])
    if battle:
        print('battle detected, shooting...')
        time.sleep(2)
        shoot('battle')
        time.sleep(4)
        shoot('battle2')
        break
    time.sleep(3)
else:
    print('no battle seen within timeout; last lines:')
    for line in tail(10):
        print(' ', line)

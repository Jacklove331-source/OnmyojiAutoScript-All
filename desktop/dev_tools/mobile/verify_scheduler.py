# This Python file uses the following encoding: utf-8

"""手机端调度器实测：启动调度 → 观察轮次 → 停止。

用法：.\\toolkit\\python.exe dev_tools\\mobile\\verify_scheduler.py [--tasks WantedQuests] [--interval 180]
"""

import argparse
import json
import sys
import time
import urllib.request

BASE = 'http://127.0.0.1:22267'


def post(path: str, timeout: int = 120) -> dict:
    request = urllib.request.Request(BASE + path, data=b'', method='POST')
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode('utf-8'))


def get(path: str, timeout: int = 30) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=timeout) as response:
        return json.loads(response.read().decode('utf-8'))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--tasks', default='WantedQuests')
    parser.add_argument('--interval', type=int, default=180)
    parser.add_argument('--observe', type=int, default=150)
    args = parser.parse_args()

    results = []

    def check(name, ok, detail=''):
        results.append((name, bool(ok)))
        print(f'[{"PASS" if ok else "FAIL"}] {name}' + (f'  ({detail})' if detail and not ok else ''))

    started = post(f'/scheduler/start?tasks={args.tasks}&interval={args.interval}&config=oas1')
    check('调度器启动', started.get('ok') is True, str(started))

    observed_round = 0
    current_task = ''
    deadline = time.time() + args.observe
    while time.time() < deadline:
        time.sleep(10)
        state = get('/scheduler/status')
        observed_round = max(observed_round, int(state.get('round') or 0))
        current_task = state.get('current') or current_task
        last = state['log'][-1] if state.get('log') else ''
        print(f'   running={state["running"]} round={state["round"]} current={state["current"]!r} last={last[:90]}')
        if state.get('last_finished'):
            break

    check('调度器进入运行态并开始跑任务', observed_round >= 1, f'round={observed_round}')
    check('任务线程确实在跑（有当前任务名）', bool(current_task), current_task)

    stopped = post('/scheduler/stop')
    check('调度器可停止', stopped.get('ok') is True, str(stopped))
    time.sleep(2)
    final = get('/scheduler/status')
    check('停止后 running=False', final.get('running') is False, str(final.get('running')))

    failed = [name for name, ok in results if not ok]
    print()
    print(f'TOTAL {len(results)}  PASS {len(results) - len(failed)}  FAIL {len(failed)}')
    return 0 if not failed else 1


if __name__ == '__main__':
    sys.exit(main())

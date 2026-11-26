# This Python file uses the following encoding: utf-8

"""扫描任务模块对公共组件（BaseTask / GameUi）的调用契约是否兼容。

针对"reset 回退公共组件导致棋局运行到某回合才炸"这类问题：
- 方法是否存在
- 关键字参数是否被接受（*args/**kwargs 视为接受）
- 位置参数个数是否超限

用法：``python dev_tools/contract_scan.py [任务目录名...]``（默认 Chess）
"""

import ast
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tasks.base_task import BaseTask  # noqa: E402
from tasks.GameUi.navigator import GameUi  # noqa: E402

PROVIDERS = (BaseTask, GameUi)


def method_specs() -> dict:
    specs = {}
    for cls in PROVIDERS:
        for name, member in inspect.getmembers(cls, predicate=inspect.isfunction):
            try:
                sig = inspect.signature(member)
            except (TypeError, ValueError):
                continue
            params = list(sig.parameters.values())
            names = {p.name for p in params if p.kind in (
                p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)}
            varkw = any(p.kind == p.VAR_KEYWORD for p in params)
            pos_only = sum(1 for p in params if p.kind == p.POSITIONAL_ONLY)
            pos_max = pos_only + sum(1 for p in params if p.kind == p.POSITIONAL_OR_KEYWORD)
            has_varargs = any(p.kind == p.VAR_POSITIONAL for p in params)
            specs[name] = {'names': names, 'varkw': varkw, 'pos_max': pos_max,
                           'varargs': has_varargs, 'self': 'self' in sig.parameters}
        return specs


def scan(folder: Path, specs: dict) -> list:
    problems = []
    for path in sorted(folder.rglob('*.py')):
        tree = ast.parse(path.read_text(encoding='utf-8-sig', errors='ignore'))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if not (isinstance(node.func.value, ast.Name) and node.func.value.id == 'self'):
                continue
            name = node.func.attr
            spec = specs.get(name)
            if spec is None:
                continue
            kwargs = {kw.arg for kw in node.keywords if kw.arg}
            bad = kwargs - spec['names']
            if bad and not spec['varkw']:
                problems.append((path, node.lineno, name, f'不支持的参数 {sorted(bad)}'))
            positional = len(node.args) - (1 if spec['self'] else 0)
            if positional > spec['pos_max'] and not spec['varargs']:
                problems.append((path, node.lineno, name,
                                 f'位置参数过多 {positional}>{spec["pos_max"]}'))
    return problems


def main() -> int:
    targets = sys.argv[1:] or ['Chess']
    specs = method_specs()
    total = 0
    for target in targets:
        folder = ROOT / 'tasks' / target
        if not folder.exists():
            print(f'skip missing {target}')
            continue
        for path, line, name, message in scan(folder, specs):
            print(f'{path.relative_to(ROOT)}:{line}: {name}() {message}')
            total += 1
    print(f'checked {",".join(targets)}: problems {total}')
    return 0 if total == 0 else 1


if __name__ == '__main__':
    sys.exit(main())

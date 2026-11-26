# This Python file uses the following encoding: utf-8

"""检查所有模板图片引用是否真实存在。

reset 事故会留下"代码还在、png 没了"的缺口，运行时才报模板无效。
本脚本扫描 tasks/**/*.py 与 *.json 里的 `file="./tasks/....png"` 引用，逐一核对磁盘。

判定是按「属性 + 引用方式」来的，而不是文件名子串，避免三类假缺失：
  - 运行时保存路径（`tasks/Hyakkiyakou/temp/image.png`）；
  - 只在注释里出现的属性（`# self.I_SKILL103`）；
  - 只当点击坐标传参、从不加载模板图的属性（`clicks = [... self.I_PASS_8]` + `self.click(...)`）。

只有「会被 appear/match/ocr 加载」的缺失才算致命；仅用于 click 的降级为警告。

用法：``python dev_tools/asset_file_check.py [--fix-from <commit>]``
``--fix-from`` 会尝试从指定历史提交里把缺失的 png 取回来。
"""

import ast
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERN = re.compile(r'["\'](\./tasks/[^"\']+\.(?:png|jpg))["\']')
# assets.py 里的声明行：属性名 = RuleImage(... file="./tasks/....png" ...)
DECL_PATTERN = re.compile(
    r'^\s*([A-Z][A-Z0-9_]+)\s*=\s*Rule(?:Image|Click|Ocr|LongClick|Swipe|List)\('
    r'.*?file=["\']([^"\']+\.(?:png|jpg))["\']', re.M)
# 只吃坐标、不加载模板图的控件方法
CLICK_FUNCS = {'click', 'long_click', 'click_random', 'click_relative', 'swipe'}


def collect_refs() -> dict:
    refs = {}
    for path in list(ROOT.glob('tasks/**/*.py')) + list(ROOT.glob('tasks/**/*.json')):
        text = path.read_text(encoding='utf-8', errors='ignore')
        for match in PATTERN.finditer(text):
            ref = match.group(1).lstrip('./')
            refs.setdefault(ref, set()).add(str(path.relative_to(ROOT)))
    return refs


def restore(commit: str, ref: str) -> bool:
    target = ROOT / ref
    target.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(['git', 'show', f'{commit}:{ref}'], cwd=str(ROOT),
                            capture_output=True)
    if result.returncode != 0 or not result.stdout:
        return False
    target.write_bytes(result.stdout)
    return True


def _self_attrs(node) -> set:
    """收集表达式里所有 `self.X` 的属性名（含嵌套的列表/元组/调用实参）。"""
    found = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Attribute) and isinstance(child.value, ast.Name) \
                and child.value.id == 'self':
            found.add(child.attr)
    return found


def collect_usage() -> dict:
    """attr → {'load'|'click'|'other'} 集合，跳过 assets.py 与注释（AST 天然不含注释）。"""
    usage = {}
    for path in ROOT.glob('tasks/**/*.py'):
        if path.name == 'assets.py':
            continue
        try:
            tree = ast.parse(path.read_text(encoding='utf-8', errors='ignore'))
        except (OSError, SyntaxError):
            continue
        called = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = ''
            if isinstance(func, ast.Attribute):
                name = func.attr
            elif isinstance(func, ast.Name):
                name = func.id
            mode = 'click' if name in CLICK_FUNCS else 'load'
            for arg in list(node.args) + [keyword.value for keyword in node.keywords]:
                for attr in _self_attrs(arg):
                    called.add(attr)
                    usage.setdefault(attr, set()).add(mode)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
                    and node.value.id == 'self' and node.attr not in called:
                usage.setdefault(node.attr, set()).add('other')
    return usage


def find_missing_detail(refs: dict = None):
    """返回 (fatal, warn)：fatal 会被运行时加载而炸；warn 只当坐标用，暂不致命。"""
    if refs is None:
        refs = collect_refs()
    usage = collect_usage()

    declared = {}
    for path in ROOT.glob('tasks/**/*.py'):
        if path.name != 'assets.py':
            continue
        try:
            text = path.read_text(encoding='utf-8', errors='ignore')
        except OSError:
            continue
        for attr, file in DECL_PATTERN.findall(text):
            declared.setdefault(file.lstrip('./'), set()).add(attr)

    fatal, warn = [], []
    for ref in sorted(refs):
        if (ROOT / ref).exists():
            continue
        if '/temp/' in f'/{ref}':
            continue
        modes = set()
        for attr in declared.get(ref, set()):
            modes |= usage.get(attr, set())
        if 'load' in modes:
            fatal.append(ref)
        elif modes:
            warn.append(ref)
    return fatal, warn


def find_missing(refs: dict = None) -> list:
    """兼容旧调用：只返回会导致运行时加载失败的缺失。"""
    fatal, _ = find_missing_detail(refs)
    return fatal


def main() -> int:
    commit = None
    if '--fix-from' in sys.argv:
        commit = sys.argv[sys.argv.index('--fix-from') + 1]
    refs = collect_refs()
    fatal, warn = find_missing_detail(refs)
    print(f'引用图片 {len(refs)} 个，致命缺失 {len(fatal)} 个，仅坐标用途缺失 {len(warn)} 个')
    for ref in fatal:
        text = f'  [FAIL] {ref}  ← {", ".join(sorted(refs[ref])[:2])}'
        if commit and restore(commit, ref):
            text += '  [已从历史恢复]'
        print(text)
    for ref in warn:
        print(f'  [warn] {ref}  ← {", ".join(sorted(refs[ref])[:2])}（仅 click 坐标用）')
    return 0 if not fatal else 1


if __name__ == '__main__':
    sys.exit(main())

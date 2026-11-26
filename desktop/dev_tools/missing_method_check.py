# This Python file uses the following encoding: utf-8

"""静态检查：任务类里 self.xxx() 调用的方法是否真的存在。

这次 Chess 的 `AttributeError: 'ScriptTask' object has no attribute
'chess_result_flow_visible'` 就是基类 mixin 被漏掉导致的——import 全绿也发现不了，
只有运行时才炸。本脚本按 MRO 检查每个 script_task 的 self 方法调用。

2026-11 修复（报告 F-1）：旧实现按「类所在源文件的整份 AST」收集 self.xxx()，
把同一个文件里**其它类**的方法调用也算到 ScriptTask 头上，再拿 hasattr(ScriptTask)
判定，于是 tasks/GameUi/navigator.py 里 OCR 辅助类的 self.current_page() /
self._strict_full_ocr() / self.detect_and_ocr() 被算成 63 个任务各 3 条、共 183 条误报
（Chess selftest 因此假红）。现在改成：
- 只在**类体范围**内收集 self.xxx()（不进入嵌套类，嵌套类的 self 属于别的类）；
- 对**调用者类**做 hasattr 判定（谁写的调用就查谁，而不是一律查 ScriptTask）；
- 实例属性（self.foo = ...）按 MRO 汇总，避免把属性当方法误报。

用法：``python dev_tools/missing_method_check.py [任务名...]``
"""

import ast
import importlib
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 运行时才注入/动态生成的属性，静态检查会误报
IGNORE = {
    'appear', 'ocr_appear', 'ocr_appear_click', 'click', 'screenshot', 'device',
    'config', 'logger', 'set_next_run', 'run', 'goto_page', 'wait_until',
    # 可选能力：调用点由 hasattr 守卫（缺失只是少一层缓存，不报错）
    'prepare_appear_cache',
    # RuleGif/RuleImage 上的方法，任务里经 self 转发
    'search', 'search_with_multi_scale',
}


def _iter_statements(node: ast.AST):
    """深度遍历语句，但不进入嵌套类：嵌套类里的 self 不是外层类的实例。"""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.ClassDef):
            continue
        yield child
        yield from _iter_statements(child)


def collect_calls(class_def: ast.ClassDef) -> set:
    """收集**该类体**里出现的 self.xxx() 调用名（不含嵌套类）。"""
    names = set()
    for node in _iter_statements(class_def):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            value = node.func.value
            if isinstance(value, ast.Name) and value.id == 'self':
                names.add(node.func.attr)
    return names


def assigned_attrs(class_def: ast.ClassDef) -> set:
    """收集**该类体**里 self.foo = ... 形式的实例属性名（不含嵌套类）。"""
    names = set()
    for node in _iter_statements(class_def):
        targets = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets = [node.target]
        for target in targets:
            if (isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == 'self'):
                names.add(target.attr)
    return names


def module_tree(cls) -> ast.AST | None:
    """取类所在源文件的 AST。"""
    try:
        source = inspect.getsourcefile(cls)
    except (TypeError, OSError):
        return None
    if not source or not Path(source).exists():
        return None
    try:
        return ast.parse(Path(source).read_text(encoding='utf-8-sig', errors='ignore'))
    except SyntaxError:
        return None


def class_def(tree: ast.AST, cls) -> ast.ClassDef | None:
    """在模块 AST 里定位到「就是这个类」的 ClassDef（同名类靠行号挑最近的）。"""
    candidates = [node for node in ast.walk(tree)
                  if isinstance(node, ast.ClassDef) and node.name == getattr(cls, '__name__', '')]
    if not candidates:
        return None
    try:
        start = inspect.getsourcelines(cls)[1]
    except (TypeError, OSError):
        return candidates[0]
    return min(candidates, key=lambda node: abs(node.lineno - start))


def _where(cls, path: Path, root: Path) -> str:
    """问题位置：优先用调用者类自己的源文件（调用点就在那里）。"""
    try:
        source = inspect.getsourcefile(cls)
    except (TypeError, OSError):
        source = None
    if source:
        try:
            return Path(source).resolve().relative_to(root.resolve()).as_posix()
        except (OSError, ValueError):
            return str(Path(source))
    return path.as_posix()


def _module_name(path: Path, root: Path, module_name: str | None) -> str:
    if module_name:
        return module_name
    try:
        return '.'.join(path.resolve().relative_to(root.resolve()).with_suffix('').parts)
    except ValueError:
        return path.stem


def check(path: Path, *, module_name: str | None = None,
          root: Path | None = None) -> list:
    """检查一个 script_task 模块；返回 [(位置, 说明)]，空列表表示没问题。"""
    root = Path(root or ROOT)
    path = Path(path)
    name = _module_name(path, root, module_name)
    try:
        module = importlib.import_module(name)
    except Exception as error:  # noqa: BLE001
        return [(str(path), f'import failed: {type(error).__name__}: {error}')]
    task_class = getattr(module, 'ScriptTask', None)
    if task_class is None:
        return []
    # 每个类：本类体里的调用 + 本类体里赋值的实例属性
    scoped: dict = {}
    for cls in task_class.__mro__:
        if cls in scoped:
            continue
        tree = module_tree(cls)
        class_ast = class_def(tree, cls) if tree is not None else None
        if class_ast is None:
            scoped[cls] = (set(), set())
            continue
        scoped[cls] = (collect_calls(class_ast), assigned_attrs(class_ast))
    # 实例属性按整个 MRO 汇总（self.foo = ... 常写在基类 __init__ 里）
    instance_attrs = set()
    for assigned in scoped.values():
        instance_attrs |= assigned[1]
    problems = []
    seen = set()
    for cls, (calls, _assigned) in scoped.items():
        if not calls:
            continue
        for called in sorted(calls - IGNORE - instance_attrs):
            # 运行时 self 就是组合后的 ScriptTask：所以「是否存在」以组合类判定。
            # 调用者类只决定「扫描哪段源码」——这样协作式 mixin（假定 self 是任务实例）
            # 不会被误报，也不会再把同文件其它类的 self.xxx() 算进来。
            if hasattr(task_class, called):
                continue
            item = (_where(cls, path, root),
                    f'missing self.{called}() (called in {cls.__name__})')
            if item in seen:
                continue
            seen.add(item)
            problems.append(item)
    return problems


def main() -> int:
    targets = sys.argv[1:]
    files = sorted(ROOT.glob('tasks/*/script_task.py'))
    if targets:
        files = [path for path in files if path.parent.name in targets]
    total = 0
    for path in files:
        problems = check(path)
        for where, message in problems:
            print(f'{where}: {message}')
            total += 1
    print(f'checked {len(files)} tasks, problems {total}')
    return 0 if total == 0 else 1


if __name__ == '__main__':
    sys.exit(main())

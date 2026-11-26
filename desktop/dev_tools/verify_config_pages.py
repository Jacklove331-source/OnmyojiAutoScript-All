# This Python file uses the following encoding: utf-8
"""离线验收：全部任务菜单的「配置页」能否生成（0 失败）。

仓库里没有现成的配置页生成检查脚本（``grep -l "config_page" dev_tools\\*.py`` 为空），
所以这里补一个只读的：直接调用 GUI 真正用的两个入口

    ConfigModel.gui_args(task) -> 页面 schema（pydantic schema JSON）
    ConfigModel.gui_task(task) -> 页面数据（当前配置值 JSON）

但**不**实例化 ``Config('oas3')``——那会触发 ConfigWatcher/线程，而且
``ConfigModel.__setattr__`` 里带 ``self.save()``，可能改写正在跑的实例配置。
这里用一个 shim 对象把「每个任务的 pydantic 模型」按属性名挂上去，
再调用 ``ConfigModel.gui_args/gui_task`` 的**原始实现**，走的是同一条代码路径，
只读、零落盘。

用法：

    .\\toolkit\\python.exe dev_tools\\verify_config_pages.py [config_name]

默认 config_name=template（仓库里的默认模板，只读）。
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from module.config.config_menu import ConfigMenu            # noqa: E402
from module.config.config_model import ConfigModel          # noqa: E402
from module.config.utils import convert_to_underscore, read_file  # noqa: E402

SKIP_GROUPS = ('TaskList', 'Tools')   # 非任务菜单


class PageShim:
    """把任务模型实例挂成属性，喂给 ConfigModel.gui_args/gui_task 的真实实现。"""


def task_names() -> list:
    menu = ConfigMenu()
    names = []
    for group, items in menu.menu.items():
        if group in SKIP_GROUPS:
            continue
        for item in items:
            if item not in names:
                names.append(item)
    return names


def build_shim(config_name: str, problems: list) -> PageShim:
    data = read_file(ROOT / 'config' / f'{config_name}.json')
    shim = PageShim()
    for field, info in ConfigModel.model_fields.items():
        model = info.annotation
        if not isinstance(model, type) or not hasattr(model, 'model_fields'):
            continue                                    # 不是任务模型（如 config_name: str）
        payload = data.get(field)
        try:
            instance = model(**payload) if isinstance(payload, dict) else model()
        except Exception as error:  # noqa: BLE001
            problems.append((field, f'model init failed: {type(error).__name__}: {error}'))
            instance = model()                          # 用默认值再试一次，页面仍需生成
        setattr(shim, field, instance)
    return shim


def main() -> int:
    config_name = sys.argv[1] if len(sys.argv) > 1 else 'template'
    problems = []
    names = task_names()
    shim = build_shim(config_name, problems)
    init_problems = len(problems)

    pages_ok = 0
    for name in names:
        key = convert_to_underscore(name)
        for api in ('gui_args', 'gui_task'):
            try:
                raw = getattr(ConfigModel, api)(shim, name)
            except Exception as error:  # noqa: BLE001
                problems.append((name, f'{api} raised {type(error).__name__}: {error}'))
                continue
            if not raw:
                problems.append((name, f'{api} returned empty string'))
                continue
            try:
                page = json.loads(raw)
            except json.JSONDecodeError as error:
                problems.append((name, f'{api} returned invalid JSON: {error}'))
                continue
            if not isinstance(page, dict) or not page:
                problems.append((name, f'{api} returned empty object'))
                continue
            if api == 'gui_args' and 'properties' not in page:
                problems.append((name, 'gui_args page has no properties (not a form)'))
                continue
        else:
            pages_ok += 1

    print(f'config_name={config_name} | menu tasks={len(names)} | pages ok={pages_ok} '
          f'| failures={len(problems) - init_problems} + init {init_problems}')
    for name, message in problems:
        print(f'  [FAIL] {name}: {message}')
    print('CONFIG PAGES: PASSED' if not problems else 'CONFIG PAGES: FAILED')
    return 0 if not problems else 1


if __name__ == '__main__':
    sys.exit(main())

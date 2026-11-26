# This Python file uses the following encoding: utf-8

"""把新增的庭院皮肤模板注册进 OAS：MainType / main_costume_model / i18n。

配合 `dev_tools/costume_add_skin.py` 使用（那个负责图与 image.json，本脚本负责代码注册）。

配对前提：`tasks/Component/Costume/main<N>/image.json` 已存在，且 `assets.py` 里已有
`I_CHECK_MAIN_<N>` / `I_MAIN_GOTO_EXPLORATION_<N>` / `I_MAIN_GOTO_SUMMON_<N>` /
`I_MAIN_GOTO_TOWN_<N>` / `I_PET_HOUSE_<N>` 五个属性。

用法：
    .\\toolkit\\python.exe dev_tools\\costume_register_skin.py --id 18 --name "神安眠处"
    .\\toolkit\\python.exe dev_tools\\costume_register_skin.py --id 18 --name "神安眠处" --dry-run

幂等：已注册的项会跳过；只做最小文本插入（不整文件重写，避免把 CRLF/排序搅乱）。
"""

import argparse
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIG_PY = ROOT / 'tasks' / 'Component' / 'Costume' / 'config.py'
BASE_PY = ROOT / 'tasks' / 'Component' / 'Costume' / 'costume_base.py'
I18N = ROOT / 'module' / 'config' / 'i18n' / 'zh-CN.json'
ASSETS_PY = ROOT / 'tasks' / 'Component' / 'Costume' / 'assets.py'

REQUIRED_ATTRS = ['I_CHECK_MAIN_{idx}', 'I_MAIN_GOTO_EXPLORATION_{idx}',
                  'I_MAIN_GOTO_SUMMON_{idx}', 'I_MAIN_GOTO_TOWN_{idx}',
                  'I_PET_HOUSE_{idx}']


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding='utf-8')


def write(path: pathlib.Path, text: str) -> None:
    path.write_text(text, encoding='utf-8')


def main() -> int:
    parser = argparse.ArgumentParser(description='注册庭院皮肤到 OAS 代码')
    parser.add_argument('--id', type=int, required=True)
    parser.add_argument('--name', required=True, help='皮肤中文名，如 神安眠处')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    idx = args.id
    enum_name = f'COSTUME_MAIN_{idx}'
    enum_value = f'costume_main_{idx}'
    failures = 0

    def check(name, ok, detail=''):
        nonlocal failures
        if not ok:
            failures += 1
        print(f'[{"PASS" if ok else "FAIL"}] {name}{(": " + detail) if detail else ""}')
        return ok

    # 0) 前置：模板与属性齐备
    missing = [a.format(idx=idx) for a in REQUIRED_ATTRS
               if a.format(idx=idx) not in read(ASSETS_PY)]
    if not check('assets.py 已含 5 个属性', not missing, ', '.join(missing)):
        print('  先跑 dev_tools/costume_add_skin.py 生成模板')
        return 1

    # 1) MainType 枚举
    config_text = read(CONFIG_PY)
    if enum_name in config_text:
        check('MainType 已注册', True, f'{enum_name} 已存在，跳过')
    else:
        anchor = re.search(r"^(\s*)COSTUME_MAIN_(\d+)\s*=\s*'costume_main_\d+'[^\n]*\n",
                           config_text, re.M)
        # 取编号最大的一行作为锚点，按编号顺序排在它后面
        matches = list(re.finditer(
            r"^(\s*)COSTUME_MAIN_(\d+)\s*=\s*'costume_main_\d+'[^\n]*\n", config_text, re.M))
        if not matches:
            check('插入 MainType', False, '找不到 COSTUME_MAIN_* 锚点')
            return 1
        last = max(matches, key=lambda m: int(m.group(2)))
        line = f"{last.group(1)}{enum_name} = '{enum_value}'  # {args.name}\n"
        insertion = last.end()
        new_text = config_text[:insertion] + line + config_text[insertion:]
        if not args.dry_run:
            write(CONFIG_PY, new_text)
            import py_compile
            try:
                py_compile.compile(str(CONFIG_PY), doraise=True)
            except py_compile.PyCompileError as error:
                write(CONFIG_PY, config_text)
                check('插入 MainType', False, f'编译失败已回滚: {error}')
                return 1
        check('插入 MainType', True, f'{enum_name} = {enum_value}')

    # 2) main_costume_model 的范围要覆盖新编号
    base_text = read(BASE_PY)
    match = re.search(r'for i in range\(1, (\d+)\)', base_text)
    if not match:
        check('扩展 main_costume_model', False, '找不到 range(1, N)')
        return 1
    upper = int(match.group(1))
    if upper > idx:
        check('扩展 main_costume_model', True, f'range(1, {upper}) 已覆盖 {idx}，跳过')
    else:
        new_upper = idx + 1
        new_text = base_text.replace(match.group(0), f'for i in range(1, {new_upper})', 1)
        if not args.dry_run:
            write(BASE_PY, new_text)
            import py_compile
            try:
                py_compile.compile(str(BASE_PY), doraise=True)
            except py_compile.PyCompileError as error:
                write(BASE_PY, base_text)
                check('扩展 main_costume_model', False, f'编译失败已回滚: {error}')
                return 1
        check('扩展 main_costume_model', True, f'range(1, {upper}) -> range(1, {new_upper})')

    # 3) i18n 中文名（菜单下拉靠它显示）
    i18n_text = read(I18N)
    data = json.loads(i18n_text)
    if enum_value in data:
        check('i18n 中文名', True, f'{enum_value} = {data[enum_value]}，跳过')
    else:
        if 'costume_main_13' not in i18n_text:
            check('i18n 中文名', False, '找不到 costume_main_13 锚点')
            return 1
        line_re = re.compile(r'^(\s*)"costume_main_13"\s*:\s*"[^"]*",\s*$', re.M)
        found = line_re.search(i18n_text)
        if not found:
            check('i18n 中文名', False, 'costume_main_13 行格式不匹配')
            return 1
        newline = '\r\n' if '\r\n' in i18n_text else '\n'
        insert = found.group(0) + newline + f'  "{enum_value}": "{args.name}",'
        new_text = i18n_text.replace(found.group(0), insert, 1)
        if not args.dry_run:
            write(I18N, new_text)
            try:
                json.loads(new_text)
            except ValueError as error:
                write(I18N, i18n_text)
                check('i18n 中文名', False, f'json 解析失败已回滚: {error}')
                return 1
        check('i18n 中文名', True, f'{enum_value} = {args.name}')

    # 4) 运行时自检：枚举 / model / assets 三处都能对上
    if not args.dry_run:
        sys.path.insert(0, str(ROOT))
        import importlib
        import tasks.Component.Costume.config as costume_config
        import tasks.Component.Costume.costume_base as costume_base
        import tasks.Component.Costume.assets as costume_assets
        for module in (costume_config, costume_base, costume_assets):
            importlib.reload(module)
        enum_member = getattr(costume_config.MainType, enum_name, None)
        check('MainType 可见', enum_member is not None, str(enum_member))
        model = costume_base.main_costume_model.get(enum_member, {})
        check('model 有该皮肤', bool(model), json.dumps(model, ensure_ascii=False))
        mapping_ok = all(hasattr(costume_assets.CostumeAssets(),
                                 model.get(key, '')) if isinstance(model.get(key), str)
                         else True for key in
                         ['I_CHECK_MAIN', 'I_MAIN_GOTO_EXPLORATION', 'I_MAIN_GOTO_SUMMON',
                          'I_MAIN_GOTO_TOWN', 'I_PET_HOUSE'])
        check('model 指向的属性都存在', mapping_ok)

    print(f'\nCOSTUME SKIN {idx} REGISTER: {"PASSED" if failures == 0 else "FAILED"} '
          f'(failures={failures})')
    return 0 if failures == 0 else 1


if __name__ == '__main__':
    sys.exit(main())

# This Python file uses the following encoding: utf-8

"""把新增的结界皮肤注册进 OAS：RealmType / realm_costume_model / check_costume_realm / i18n。

OAS 上游的结界皮肤只留了 `COSTUME_REALM_DEFAULT` 一个枚举，`CostumeBase` 里也没有
`check_costume_realm`，所以选了结界皮肤也不会生效。本脚本补齐这三处：

  1. `config.py`：`RealmType` 增加 `COSTUME_REALM_<N>`
  2. `costume_base.py`：
       - 新增 `realm_costume_model`（编号 -> 4 个 RuleImage 属性名）
       - 新增 `check_costume_realm()`（用 CostumeAssets 覆盖 I_SHI_GROWN / I_SHI_CARD /
         I_SHI_DEFENSE / I_REALM_SHIN）
       - `check_costume()` 里调用它（放在 carpbanner 之前，鲤鱼旗仍可覆盖）
  3. i18n：`costume_realm_<N> = <中文名>`

配对前提：`dev_tools/realm_add_skin.py` 已跑过，`assets.py` 里已有
`I_SHI_GROWN_<N>` / `I_SHI_CARD_<N>` / `I_SHI_DEFENSE_<N>` / `I_REALM_SHIN_<N>`。

用法：
    .\\toolkit\\python.exe dev_tools\\realm_register_skin.py --id 6 --name "神安眠处"
    .\\toolkit\\python.exe dev_tools\\realm_register_skin.py --id 6 --name "神安眠处" --dry-run

幂等；每步改动都先 py_compile / json 解析校验，失败自动回滚。
"""

import argparse
import json
import pathlib
import py_compile
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIG_PY = ROOT / 'tasks' / 'Component' / 'Costume' / 'config.py'
BASE_PY = ROOT / 'tasks' / 'Component' / 'Costume' / 'costume_base.py'
I18N = ROOT / 'module' / 'config' / 'i18n' / 'zh-CN.json'
ASSETS_PY = ROOT / 'tasks' / 'Component' / 'Costume' / 'assets.py'

REQUIRED_ATTRS = ['I_SHI_GROWN_{idx}', 'I_SHI_CARD_{idx}',
                  'I_SHI_DEFENSE_{idx}', 'I_REALM_SHIN_{idx}']

MODEL_BLOCK = '''
# 结界皮肤（阴阳寮 → 结界）：只覆盖结界界面的 4 个入口模板
# 注意：RealmType 只登记实际存在的皮肤，按成员存在性收集（缺号会 AttributeError）
realm_costume_model = {}
for _realm_idx in range(1, __UPPER__):
    _realm_member = getattr(RealmType, f'COSTUME_REALM_{_realm_idx}', None)
    if _realm_member is None:
        continue
    realm_costume_model[_realm_member] = {
        'I_SHI_GROWN': f'I_SHI_GROWN_{_realm_idx}',
        'I_SHI_CARD': f'I_SHI_CARD_{_realm_idx}',
        'I_SHI_DEFENSE': f'I_SHI_DEFENSE_{_realm_idx}',
        'I_REALM_SHIN': f'I_REALM_SHIN_{_realm_idx}',
    }
'''

METHOD_BLOCK = '''
    def check_costume_realm(self, realm_type: RealmType):
        if realm_type == RealmType.COSTUME_REALM_DEFAULT:
            return
        logger.info(f'Switch realm costume to {realm_type} ({I18n.trans_zh_cn(realm_type)})')
        realm_assets = CostumeAssets()
        for key, value in realm_costume_model.get(realm_type, {}).items():
            if not hasattr(realm_assets, value):
                logger.warning(f'Realm costume asset {value} not found, skip')
                continue
            self.replace_img(key, getattr(realm_assets, value))
'''


def main() -> int:
    parser = argparse.ArgumentParser(description='注册结界皮肤到 OAS 代码')
    parser.add_argument('--id', type=int, required=True)
    parser.add_argument('--name', required=True)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    idx, name = args.id, args.name
    enum_member = f'COSTUME_REALM_{idx}'
    enum_value = f'costume_realm_{idx}'
    failures = 0

    def check(label, ok, detail=''):
        nonlocal failures
        if not ok:
            failures += 1
        print(f'[{"PASS" if ok else "FAIL"}] {label}{(": " + detail) if detail else ""}')
        return ok

    assets_text = ASSETS_PY.read_text(encoding='utf-8')
    missing = [a.format(idx=idx) for a in REQUIRED_ATTRS if a.format(idx=idx) not in assets_text]
    if not check('assets.py 已含 4 个结界属性', not missing, ', '.join(missing)):
        print('  先跑 dev_tools/realm_add_skin.py 生成模板')
        return 1

    # ---------- 1) RealmType 枚举 ----------
    config_text = CONFIG_PY.read_text(encoding='utf-8')
    if f"'{enum_value}'" in config_text:
        check('RealmType 已注册', True, f'{enum_member} 已存在，跳过')
    else:
        anchor = re.search(r"^(\s*)COSTUME_REALM_DEFAULT\s*=\s*'costume_realm_default'[^\n]*\n",
                           config_text, re.M)
        if not anchor:
            return 0 if check('插入 RealmType', False, '找不到 COSTUME_REALM_DEFAULT 锚点') else 1
        line = f"{anchor.group(1)}{enum_member} = '{enum_value}'  # {name}\n"
        new_text = config_text[:anchor.end()] + line + config_text[anchor.end():]
        if not args.dry_run:
            CONFIG_PY.write_text(new_text, encoding='utf-8')
            try:
                py_compile.compile(str(CONFIG_PY), doraise=True)
            except py_compile.PyCompileError as error:
                CONFIG_PY.write_text(config_text, encoding='utf-8')
                check('插入 RealmType', False, f'编译失败已回滚: {error}')
                return 1
        check('插入 RealmType', True, f'{enum_member} = {enum_value}')

    # ---------- 2) realm_costume_model + check_costume_realm ----------
    base_text = BASE_PY.read_text(encoding='utf-8')
    if 'realm_costume_model' in base_text:
        check('realm_costume_model', True, '已存在，跳过')
    else:
        anchor = re.search(r'^# 鲤鱼旗皮肤\n', base_text, re.M)
        if not anchor:
            return 0 if check('插入 realm_costume_model', False, '找不到「# 鲤鱼旗皮肤」锚点') else 1
        block = MODEL_BLOCK.replace('__UPPER__', str(idx + 1)).lstrip('\n') + '\n\n'
        new_text = base_text[:anchor.start()] + block + base_text[anchor.start():]
        if not args.dry_run:
            BASE_PY.write_text(new_text, encoding='utf-8')
            try:
                py_compile.compile(str(BASE_PY), doraise=True)
            except py_compile.PyCompileError as error:
                BASE_PY.write_text(base_text, encoding='utf-8')
                check('插入 realm_costume_model', False, f'编译失败已回滚: {error}')
                return 1
            base_text = new_text
        check('插入 realm_costume_model', True, f'range(1, {idx + 1})')

    if 'def check_costume_realm' in base_text:
        check('check_costume_realm', True, '已存在，跳过')
    else:
        anchor = re.search(r'^    def check_costume_carpbanner\(', base_text, re.M)
        if not anchor:
            return 0 if check('插入 check_costume_realm', False, '找不到 check_costume_carpbanner') else 1
        block = METHOD_BLOCK.lstrip('\n') + '\n'
        new_text = base_text[:anchor.start()] + block + base_text[anchor.start():]
        # 在 check_costume() 里挂接
        call_anchor = '        self.check_costume_carpbanner(config.costume_carpbanner_type)'
        if 'self.check_costume_realm(' not in new_text:
            if call_anchor not in new_text:
                return 0 if check('挂接 check_costume', False, '找不到 carpbanner 调用行') else 1
            new_text = new_text.replace(
                call_anchor,
                '        self.check_costume_realm(config.costume_realm_type)\n' + call_anchor, 1)
        if not args.dry_run:
            BASE_PY.write_text(new_text, encoding='utf-8')
            try:
                py_compile.compile(str(BASE_PY), doraise=True)
            except py_compile.PyCompileError as error:
                BASE_PY.write_text(base_text, encoding='utf-8')
                check('插入 check_costume_realm', False, f'编译失败已回滚: {error}')
                return 1
            base_text = new_text
        check('插入 check_costume_realm 并挂接', True)

    # ---------- 3) i18n ----------
    i18n_text = I18N.read_text(encoding='utf-8')
    data = json.loads(i18n_text)
    if enum_value in data:
        check('i18n 中文名', True, f'{enum_value} = {data[enum_value]}，跳过')
    else:
        anchor_key = f'"costume_realm_{idx - 1}"' if f'"costume_realm_{idx - 1}"' in i18n_text \
            else '"costume_realm_default"'
        line_re = re.compile(r'^(\s*)' + re.escape(anchor_key) + r'\s*:\s*"[^"]*",\s*$', re.M)
        found = line_re.search(i18n_text)
        if not found:
            return 0 if check('i18n 中文名', False, f'找不到 {anchor_key} 锚点') else 1
        newline = '\r\n' if '\r\n' in i18n_text else '\n'
        insert = found.group(0) + newline + f'  "{enum_value}": "{name}",'
        new_text = i18n_text.replace(found.group(0), insert, 1)
        if not args.dry_run:
            I18N.write_text(new_text, encoding='utf-8')
            try:
                json.loads(new_text)
            except ValueError as error:
                I18N.write_text(i18n_text, encoding='utf-8')
                check('i18n 中文名', False, f'json 解析失败已回滚: {error}')
                return 1
        check('i18n 中文名', True, f'{enum_value} = {name}')

    # ---------- 4) 运行时自检 ----------
    if not args.dry_run:
        sys.path.insert(0, str(ROOT))
        import importlib
        import tasks.Component.Costume.config as cfg
        import tasks.Component.Costume.costume_base as base
        import tasks.Component.Costume.assets as assets
        for module in (cfg, base, assets):
            importlib.reload(module)
        member = getattr(cfg.MainType, 'COSTUME_MAIN_1')  # 预热，避免导入顺序问题
        realm_member = getattr(cfg.RealmType, enum_member, None)
        check('RealmType 可见', realm_member is not None, str(realm_member))
        model = base.realm_costume_model.get(realm_member, {})
        check('realm_costume_model 有该皮肤', bool(model), json.dumps(model, ensure_ascii=False))
        holder = assets.CostumeAssets()
        check('model 指向的属性都存在',
              all(hasattr(holder, value) for value in model.values()))
        check('check_costume 已挂接',
              'self.check_costume_realm(' in BASE_PY.read_text(encoding='utf-8'))

    print(f'\nREALM SKIN {idx} REGISTER: {"PASSED" if failures == 0 else "FAILED"} '
          f'(failures={failures})')
    return 0 if failures == 0 else 1


if __name__ == '__main__':
    sys.exit(main())

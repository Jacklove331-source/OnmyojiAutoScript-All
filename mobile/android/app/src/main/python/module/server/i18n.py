import json
from pathlib import Path

from module.base.resource import PACKAGE_ROOT, resolve_resource


def _resource_dir(*parts: str) -> Path:
    for root in (Path.cwd(), PACKAGE_ROOT):
        candidate = root.joinpath(*parts)
        if candidate.is_dir():
            return candidate
    return PACKAGE_ROOT.joinpath(*parts)


class Addition:
    @classmethod
    def load_additions(cls) -> dict:
        result = {}
        directory = _resource_dir('assets', 'i18n')
        for file in ('en-US', 'zh-CN'):
            file_path = directory / f'{file}.json'
            result[file] = {}
            if not file_path.exists():
                continue
            with open(file_path, 'r', encoding='utf-8') as stream:
                result[file] = json.load(stream)
        return result


class I18n(Addition):
    file_zh_cn = PACKAGE_ROOT / 'module' / 'config' / 'i18n' / 'zh-CN.json'

    @classmethod
    def trans_zh_cn(cls, text) -> str:
        data = cls.load_zh_cn()
        return data[text] if text in data else text

    @classmethod
    def save_zh_cn(cls, data) -> None:
        path = Path.cwd() / 'module' / 'config' / 'i18n' / 'zh-CN.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as stream:
            stream.write(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=False, default=str))

    @classmethod
    def load_zh_cn(cls) -> dict:
        path = resolve_resource(cls.file_zh_cn)
        if not path.exists():
            return {}
        with open(path, 'r', encoding='utf-8') as stream:
            return json.load(stream)


if __name__ == '__main__':
    print(I18n.load_zh_cn())

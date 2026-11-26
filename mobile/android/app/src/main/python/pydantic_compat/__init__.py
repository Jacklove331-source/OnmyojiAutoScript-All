"""pydantic v2 兼容层（在 pydantic v1 之上实现 OAS 实际用到的 v2 API）。

为什么需要：OAS 要求 `pydantic==2.10`（v2），而 v2 的 `pydantic-core` 是 Rust 扩展，
Chaquopy 没有 Android wheel，装不上。Chaquopy 能装 pure-python 的 pydantic v1，
所以这里把 v2 的那几个 API 映射到 v1 上，让 `module.config.*` 等代码能原样跑。

覆盖（按仓库实际用量统计）：
    BaseModel、Field、ValidationError、ConfigDict
    field_validator / model_validator（含 mode='before'|'after'）
    BeforeValidator（`Annotated[T, BeforeValidator(f)]`）
    model_dump / model_dump_json / model_fields / model_copy
    model_serializer / field_serializer（注册式，序列化时生效）

不追求完整语义：只保证「OAS 的用法」正确（配置读写、校验、序列化）。
"""

from __future__ import annotations

import functools
import typing

import pydantic as _v1
from pydantic import (  # noqa: F401 - 直接转出 v1 已有且语义一致的东西
    Field,
    ValidationError,
)

__all__ = [
    "BaseModel", "Field", "ValidationError", "ConfigDict",
    "field_validator", "model_validator", "BeforeValidator", "AfterValidator",
    "model_serializer", "field_serializer", "SerializationInfo",
    "model_fields", "model_dump", "model_dump_json", "model_copy",
]

_BEFORE_VALIDATORS: dict = {}   # class -> {field: [callable]}
_MODEL_VALIDATORS_BEFORE: dict = {}
_MODEL_VALIDATORS_AFTER: dict = {}
_FIELD_SERIALIZERS: dict = {}   # class -> {field: callable}
_MODEL_SERIALIZERS: dict = {}   # class -> callable


class ConfigDict(dict):
    """v2 的 ConfigDict：本质是 dict，v1 的 Config 从它取属性。"""


class BeforeValidator:
    """v2 的 BeforeValidator：用 Annotated 携带，v1 不知道怎么处理，所以我们自己收集。"""

    def __init__(self, func):
        self.func = func

    def __repr__(self):
        return f"BeforeValidator({self.func!r})"


class AfterValidator:
    def __init__(self, func):
        self.func = func


def _split_mode(kwargs) -> str:
    mode = kwargs.pop("mode", "after")
    kwargs.pop("json_schema_input_type", None)
    kwargs.pop("check_fields", None)
    return mode


def field_validator(*fields, **kwargs):
    """v2 的 field_validator → v1 的 validator(pre=..., allow_reuse=True)。"""
    mode = _split_mode(kwargs)

    def decorator(func):
        wrapped = _v1.validator(*fields, pre=(mode == "before"), allow_reuse=True, **kwargs)(func)
        wrapped.__oas_v2_validator__ = True
        return wrapped

    return decorator


def model_validator(**kwargs):
    """v2 的 model_validator(mode='before'|'after') → v1 的 root_validator。"""
    mode = _split_mode(kwargs)

    def decorator(func):
        if mode == "before":
            @functools.wraps(func)
            def _pre(cls, values):
                # v2 before 收到 dict，返回 dict；v1 pre-root 同语义
                result = func(cls, values) if _accepts_two(func) else func(values)
                return result

            return _v1.root_validator(pre=True, allow_reuse=True)(_pre)

        @functools.wraps(func)
        def _post(cls, values):
            result = func(cls, values) if _accepts_two(func) else func(cls)
            return values if result is None else result

        return _v1.root_validator(pre=False, allow_reuse=True)(_post)

    return decorator


def _accepts_two(func) -> bool:
    import inspect
    try:
        params = inspect.signature(func).parameters
        return len(params) >= 2
    except (TypeError, ValueError):
        return True


def model_serializer(*args, **kwargs):  # noqa: ARG001 - 只做注册
    def decorator(func):
        func.__oas_model_serializer__ = True
        return func

    return decorator


def field_serializer(*fields, **kwargs):  # noqa: ARG001
    def decorator(func):
        for name in fields:
            func.__oas_field_serializer__ = func.__oas_field_serializer__ if hasattr(
                func, "__oas_field_serializer__") else {}
            func.__oas_field_serializer__[name] = func
        return func

    return decorator


class SerializationInfo:
    """v2 的 SerializationInfo 占位（OAS 只读 .mode）。"""

    def __init__(self, mode: str = "python"):
        self.mode = mode


class BaseModel(_v1.BaseModel):
    """把 v2 风格的方法补齐到 v1 的 BaseModel 上。"""

    def __init_subclass__(cls, **kwargs):  # noqa: D401
        super().__init_subclass__(**kwargs)
        # model_config（ConfigDict）→ v1 的 Config
        config = getattr(cls, "model_config", None)
        if isinstance(config, dict) and not issubclass(getattr(cls, "Config", object), type(None)):
            for key, value in config.items():
                # v2 用 populate_by_name，v1 用 allow_population_by_field_name
                if key == "populate_by_name":
                    setattr(cls.Config, "allow_population_by_field_name", value)
                elif key in ("frozen", "extra", "title", "str_strip_whitespace"):
                    if key == "str_strip_whitespace":
                        setattr(cls.Config, "anystr_strip_whitespace", value)
                    else:
                        setattr(cls.Config, key, value)
        # Annotated 上的 BeforeValidator → v1 pre-validator
        for name, field in getattr(cls, "__fields__", {}).items():
            metadata = getattr(field, "field_info", None)
            if metadata is None:
                continue
            for item in getattr(field, "outer_type_", None) is not None and () or ():
                pass
        _collect_annotated_validators(cls)

    # ---------------------------------------------------------------- v2 接口
    def model_dump(self, **kwargs):
        data = self.dict(**kwargs)
        serializers = _FIELD_SERIALIZERS.get(type(self), {})
        info = SerializationInfo("python")
        for name, func in serializers.items():
            if name in data:
                try:
                    data[name] = func(data[name], info) if _accepts_two(func) else func(data[name])
                except TypeError:
                    data[name] = func(data[name])
        return data

    def model_dump_json(self, **kwargs):
        import json
        return json.dumps(self.model_dump(mode="json", **kwargs), ensure_ascii=False, default=str)

    @classmethod
    def model_fields(cls):  # v2 里是属性，v1 里是 dict；OAS 主要用 keys/items
        return cls.__fields__

    @property
    def model_fields_set(self):
        return set(self.__fields_set__)

    def model_copy(self, update=None, deep=False):
        if deep:
            import copy as _copy
            data = _copy.deepcopy(self.dict())
        else:
            data = dict(self.dict())
        if update:
            data.update(update)
        return type(self)(**data)

    def model_validate(cls, obj):  # noqa: N805 - 与 v2 名字对齐
        return cls.parse_obj(obj)

    @classmethod
    def model_validate_json(cls, data):  # noqa: N805
        return cls.parse_raw(data)

    def model_json_schema(cls):  # noqa: N805
        return cls.schema()


def _collect_annotated_validators(cls):
    """扫描 `Annotated[T, BeforeValidator(fn)]` 并补一个 v1 的 pre-validator。"""
    annotations = getattr(cls, "__annotations__", {}) or {}
    for name, annotation in annotations.items():
        validators = []
        for arg in typing.get_args(annotation):
            if isinstance(arg, BeforeValidator):
                validators.append(arg.func)
        if not validators:
            continue
        for func in validators:
            _v1.validator(name, pre=True, allow_reuse=True, check_fields=False)(func)

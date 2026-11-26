"""pydantic v2 兼容实现（内部使用随包携带的 pydantic v1.10）。

覆盖 OAS 实际用到的 v2 API：
    BaseModel / Field / ValidationError / ConfigDict
    field_validator / model_validator（mode before|after）
    BeforeValidator / AfterValidator / PlainSerializer / WrapSerializer（Annotated 标记）
    model_dump / model_dump_json / model_fields / model_fields_set / model_copy
    model_validate / model_validate_json / model_json_schema
    model_serializer / field_serializer / SerializationInfo
同时转出 OAS 仍在使用的 v1 API（validator / root_validator / conint / parse_obj_as 等）。
"""

from __future__ import annotations

import functools
import inspect
import json
import typing

import _pydantic_v1 as _v1
from _pydantic_v1 import (  # noqa: F401 - v1 风格 API 原样转出（OAS 代码 v1/v2 混用）
    BaseSettings,
    Field,
    NonNegativeInt,
    PositiveInt,
    ValidationError,
    confloat,
    conint,
    conlist,
    constr,
    create_model,
    parse_obj_as,
    root_validator,
    validator,
)

__version__ = "2.10.0+oas-mobile-v1-shim"

__all__ = [
    "BaseModel", "Field", "ValidationError", "ConfigDict",
    "field_validator", "model_validator", "BeforeValidator", "AfterValidator",
    "PlainSerializer", "WrapSerializer",
    "model_serializer", "field_serializer", "SerializationInfo", "__version__",
    "validator", "root_validator", "create_model", "parse_obj_as",
    "BaseSettings", "conint", "constr", "confloat", "conlist",
    "PositiveInt", "NonNegativeInt",
    "WithJsonSchema", "SkipValidation", "FieldInfo",
    "computed_field", "validate_call",
]


class WithJsonSchema:
    """v2 的 WithJsonSchema：只影响 JSON Schema 生成，做校验时无副作用。"""

    def __init__(self, json_schema, mode=None):
        self.json_schema = json_schema
        self.mode = mode


class SkipValidation:
    """v2 的 SkipValidation 标记（这里退化为不额外处理）。"""


class FieldInfo:
    """v2 的 FieldInfo 占位（OAS 只用它做类型标注）。"""


def computed_field(*args, **kwargs):
    """v2 的 computed_field：把方法当属性暴露（v1 用 @property 即可）。"""
    def decorator(func):
        return property(func)

    return decorator


def validate_call(*args, **kwargs):
    """v2 的 validate_call：这里不做参数校验，直接返回原函数/装饰器。"""
    if len(args) == 1 and callable(args[0]) and not kwargs:
        return args[0]

    def decorator(func):
        return func

    return decorator


_FIELD_SERIALIZERS = {}
_ANNOTATED_SERIALIZERS = {}
_ANNOTATED_BEFORE = {}
_ANNOTATED_AFTER = {}


class ConfigDict(dict):
    """v2 的 ConfigDict：本质是 dict，由 BaseModel.__init_subclass__ 翻成 v1 Config。"""


class BeforeValidator:
    def __init__(self, func):
        self.func = func

    def __repr__(self):
        return f"BeforeValidator({self.func!r})"


class AfterValidator:
    def __init__(self, func):
        self.func = func


class PlainSerializer:
    def __init__(self, func, return_type=None, when_used="json"):
        self.func = func
        self.return_type = return_type
        self.when_used = when_used


class WrapSerializer:
    def __init__(self, func, return_type=None, when_used="json"):
        self.func = func
        self.return_type = return_type
        self.when_used = when_used


class SerializationInfo:
    def __init__(self, mode: str = "python"):
        self.mode = mode


def _unwrap(func):
    """classmethod/staticmethod 包装后取原始函数。"""
    if isinstance(func, (classmethod, staticmethod)):
        return func.__func__
    return func


def _call_args(func) -> int:
    try:
        return len(inspect.signature(func).parameters)
    except (TypeError, ValueError):
        return 2


def _pop_mode(kwargs) -> str:
    mode = kwargs.pop("mode", "after")
    for key in ("json_schema_input_type", "check_fields", "when_used"):
        kwargs.pop(key, None)
    return mode


def field_validator(*fields, **kwargs):
    mode = _pop_mode(kwargs)

    def decorator(func):
        inner = _unwrap(func)
        return _v1.validator(*fields, pre=(mode == "before"), allow_reuse=True, **kwargs)(inner)

    return decorator


def model_validator(**kwargs):
    mode = _pop_mode(kwargs)

    def decorator(func):
        func = _unwrap(func)
        if mode == "before":
            @functools.wraps(func)
            def _pre(cls, values):
                return func(values) if _call_args(func) == 1 else func(cls, values)

            return _v1.root_validator(pre=True, allow_reuse=True)(_pre)

        @functools.wraps(func)
        def _post(cls, values):
            result = func(cls) if _call_args(func) == 1 else func(cls, values)
            return values if result is None else result

        return _v1.root_validator(pre=False, allow_reuse=True)(_post)

    return decorator


def model_serializer(*args, **kwargs):
    def decorator(func):
        return func

    return decorator


def field_serializer(*fields, **kwargs):
    def decorator(func):
        inner = _unwrap(func)
        existing = tuple(getattr(inner, "__oas_field_serializers__", ()))
        inner.__oas_field_serializers__ = existing + tuple(fields)
        return func

    return decorator


class BaseModel(_v1.BaseModel):
    """v1 BaseModel + v2 方法面。"""

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)

        raw_annotations = {}
        for base in reversed(cls.__mro__):
            raw_annotations.update(getattr(base, "__annotations__", {}) or {})

        config = getattr(cls, "model_config", None)
        if isinstance(config, dict):
            mapping = {
                "populate_by_name": "allow_population_by_field_name",
                "str_strip_whitespace": "anystr_strip_whitespace",
                "validate_assignment": "validate_assignment",
                "frozen": "allow_mutation",
                "extra": "extra",
                "title": "title",
            }
            for key, v1_key in mapping.items():
                if key not in config or not hasattr(cls.Config, v1_key):
                    continue
                value = not bool(config[key]) if key == "frozen" else config[key]
                setattr(cls.Config, v1_key, value)

        annotated_serializers = {}
        before_validators = {}
        after_validators = {}
        for field_name, annotation in raw_annotations.items():
            for arg in typing.get_args(annotation):
                if isinstance(arg, BeforeValidator):
                    before_validators.setdefault(field_name, []).append(arg.func)
                elif isinstance(arg, AfterValidator):
                    after_validators.setdefault(field_name, []).append(arg.func)
                elif isinstance(arg, (PlainSerializer, WrapSerializer)):
                    annotated_serializers[field_name] = arg.func
        if annotated_serializers:
            _ANNOTATED_SERIALIZERS[cls] = annotated_serializers
        if before_validators:
            _ANNOTATED_BEFORE[cls] = before_validators
        if after_validators:
            _ANNOTATED_AFTER[cls] = after_validators

        serializers = {}
        for attr_name, member in list(vars(cls).items()):
            inner = member.__func__ if isinstance(member, (classmethod, staticmethod)) else member
            attached = getattr(inner, "__oas_field_serializers__", None)
            if not attached:
                continue
            # classmethod 需要绑定成「接收 cls」的可调用：用 cls 去取描述符
            bound = getattr(cls, attr_name) if isinstance(member, (classmethod, staticmethod)) else inner
            needs_cls = isinstance(member, classmethod)
            for field_name in attached:
                serializers[field_name] = (bound, needs_cls)
        if serializers:
            _FIELD_SERIALIZERS[cls] = serializers

    def __init__(self, **data):
        before = _ANNOTATED_BEFORE.get(type(self), {})
        if before:
            data = dict(data)
            for field_name, funcs in before.items():
                if field_name not in data:
                    continue
                value = data[field_name]
                for func in funcs:
                    try:
                        value = func(value)
                    except TypeError:
                        value = func(type(self), value)
                data[field_name] = value
        super().__init__(**data)
        after = _ANNOTATED_AFTER.get(type(self), {})
        for field_name, funcs in after.items():
            for func in funcs:
                try:
                    current = getattr(self, field_name)
                    new_value = func(current)
                    object.__setattr__(self, field_name, new_value)
                except Exception:  # noqa: BLE001 - 校验器异常按 v1 行为交给上层
                    raise

    # v2 有、v1 dict() 不认识的参数
    _V2_ONLY = ("context", "mode", "round_trip", "warnings", "serialize_as_any", "fallback")

    def model_dump(self, **kwargs):
        context = kwargs.pop("context", None)
        for key in self._V2_ONLY:
            kwargs.pop(key, None)
        data = self.dict(**kwargs)
        _ = context  # 序列化器里需要时再扩展
        info = SerializationInfo("python")
        for field_name, func in _ANNOTATED_SERIALIZERS.get(type(self), {}).items():
            if field_name in data:
                try:
                    data[field_name] = (func(data[field_name]) if _call_args(func) <= 1
                                        else func(data[field_name], info))
                except TypeError:
                    pass
        for field_name, entry in _FIELD_SERIALIZERS.get(type(self), {}).items():
            if field_name not in data:
                continue
            func, needs_cls = entry if isinstance(entry, tuple) else (entry, False)
            try:
                if needs_cls or _call_args(func) >= 2:
                    data[field_name] = func(data[field_name], info)
                else:
                    data[field_name] = func(data[field_name])
            except TypeError:
                pass
        return data

    def model_dump_json(self, **kwargs):
        return json.dumps(self.model_dump(mode="json", **kwargs), ensure_ascii=False, default=str)

    @classmethod
    def model_fields(cls):
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

    @classmethod
    def model_validate(cls, obj):
        return cls.parse_obj(obj)

    @classmethod
    def model_validate_json(cls, data):
        return cls.parse_raw(data)

    @classmethod
    def model_json_schema(cls, **kwargs):
        """v2 风格的 JSON Schema：把 v1 的 definitions/$ref 转成 $defs 写法。"""
        schema = cls.schema(**kwargs)

        def convert(node):
            if isinstance(node, dict):
                out = {}
                for key, value in node.items():
                    if key == "definitions":
                        out["$defs"] = {k: convert(v) for k, v in value.items()}
                    elif key == "$ref" and isinstance(value, str):
                        out["$ref"] = value.replace("#/definitions/", "#/$defs/")
                    else:
                        out[key] = convert(value)
                return out
            if isinstance(node, list):
                return [convert(item) for item in node]
            return node

        return convert(schema)

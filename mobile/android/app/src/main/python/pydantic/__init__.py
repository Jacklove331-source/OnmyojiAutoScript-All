"""`pydantic` 的 Android 兼容门面（对外 v2/v1 混合 API，内部用随包携带的 pydantic v1.10）。

OAS 要求 pydantic 2.10，但 `pydantic-core` 是 Rust 扩展、Chaquopy 无 Android wheel；
v1.10 是纯 Python，可随包携带。这里把 v2 风格 API 映射到 v1，同时保留 OAS 代码里
仍在用的 v1 API（validator / root_validator / conint / ParseObjAs 等）。
"""

from pydantic_impl import (  # noqa: F401
    WithJsonSchema,
    SkipValidation,
    FieldInfo,
    computed_field,
    validate_call,
    PlainSerializer,
    WrapSerializer,
    # v2 风格
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    SerializationInfo,
    ValidationError,
    __version__,
    field_serializer,
    field_validator,
    model_serializer,
    model_validator,
    # v1 风格（OAS 仍在用）
    BaseSettings,
    NonNegativeInt,
    PositiveInt,
    confloat,
    conint,
    conlist,
    constr,
    create_model,
    parse_obj_as,
    root_validator,
    validator,
)

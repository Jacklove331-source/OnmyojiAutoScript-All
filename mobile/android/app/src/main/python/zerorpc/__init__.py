"""zerorpc 占位：手机端不起 OCR RPC 服务，真调用要显式报错。"""

from __future__ import annotations

_UNSUPPORTED = "zerorpc is not available in the Android build (OCR RPC is desktop-only)."


class Server:
    def __init__(self, *args, **kwargs):
        raise RuntimeError(_UNSUPPORTED)


class Client:
    def __init__(self, *args, **kwargs):
        raise RuntimeError(_UNSUPPORTED)


class Error(Exception):
    pass

"""手机端最小 module.server.setting。

桌面版这里是 WebUI 的运行态（multiprocessing Manager + DeployConfig）；
手机端只需要给 OCR/识别层提供「同名同字段」的对象，避免 AttributeError。
"""

from __future__ import annotations

from types import SimpleNamespace


class DeployConfig:
    """与桌面版 DeployConfig 同名字段的最小实现（OCR 相关）。"""

    def __init__(self, **overrides) -> None:
        # 桌面版字段：ocr 是否启用、模型路径、语言、是否走 OCR RPC 服务
        self.enable_ocr = True
        self.ocr_model_path = ""
        self.ocr_language = "zh"
        # module/ocr/models.py 里会读这三个：
        self.UseOcrServer = False          # 手机端不用 zerorpc OCR 服务
        self.OcrClientAddress = ""         # 上面的服务地址（未用）
        self.OcrServerAddress = ""
        self.ocr_config = SimpleNamespace(
            enable_ocr=True,
            ocr_model_path="",
            language="zh",
        )
        for key, value in overrides.items():
            setattr(self, key, value)


class State:
    """与桌面版同名的最小状态容器（OCR/模型加载会读它）。"""

    _init = True
    _clearup = False
    ocr_enable = True
    ocr_model_path = ""
    deploy_config = DeployConfig()

    @classmethod
    def init(cls) -> None:
        cls._init = True

    @classmethod
    def clearup(cls) -> None:
        cls._clearup = True

    @classmethod
    def reset(cls) -> None:
        cls.deploy_config = DeployConfig()

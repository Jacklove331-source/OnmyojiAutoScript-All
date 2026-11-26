"""桌面版：把素材与截图叠起来做可视化调试；手机端不支持。"""


def detect_image(*args, **kwargs):  # noqa: ARG001
    raise RuntimeError("dev_tools.assets_test.detect_image is desktop-only")

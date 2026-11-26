"""桌面版：从视频/截图里抠素材；手机端不支持。"""


class GetAnimation:
    def __init__(self, *args, **kwargs):  # noqa: ARG002
        raise RuntimeError("dev_tools.get_images.GetAnimation is desktop-only")

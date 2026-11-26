"""极简 PIL 兼容层（只覆盖 OAS 实际用到的那几个 API）。

为什么需要：Chaquopy 的仓库里没有 Pillow 的 Android wheel（构建要 C 工具链），
而 OAS 只在少数地方用 PIL（`load_image`/`save_image` 以及个别任务的图像导出）。
这里用 cv2 + numpy 实现同样的语义，避免为了两个函数把 Pillow 硬编进包。

覆盖面（按 OAS 实际调用统计）：
    Image.open(fp) -> Image
    Image.fromarray(arr) -> Image
    Image.new(mode, size, color)
    实例： .size .mode .shape .crop(box) .resize((w,h)) .convert(mode) .save(fp)
           .copy() .to_numpy() .getpixel((x,y))
"""

from __future__ import annotations

import io
import os

import numpy as np

try:
    import cv2
except Exception:  # noqa: BLE001 - 桌面环境可能没装 cv2，也不该在这里崩
    cv2 = None


class _ImageModule:
    """模拟 `PIL.Image` 模块里常用的常量。"""

    NEAREST = 0
    LANCZOS = 1
    BILINEAR = 2
    BICUBIC = 3
    ANTIALIAS = 1

    @staticmethod
    def open(fp):
        return Image.open(fp)

    @staticmethod
    def fromarray(arr, mode=None):  # noqa: ARG004 - 与 PIL 签名保持一致
        return Image.fromarray(arr)

    @staticmethod
    def new(mode, size, color=0):
        return Image.new(mode, size, color)


class Image:
    """RGB/RGBA/灰度 三态的小图对象，内部统一用 numpy 存。"""

    def __init__(self, array: np.ndarray, mode: str = "RGB") -> None:
        self._array = np.asarray(array)
        self.mode = mode

    # ---------------------------------------------------------------- 构造
    @staticmethod
    def open(fp) -> "Image":
        if hasattr(fp, "read"):
            data = fp.read()
        else:
            with open(fp, "rb") as handle:
                data = handle.read()
        if cv2 is None:
            raise RuntimeError("cv2 required for Image.open")
        buffer = np.frombuffer(data, np.uint8)
        decoded = cv2.imdecode(buffer, cv2.IMREAD_UNCHANGED)
        if decoded is None:
            raise ValueError(f"cannot decode image: {fp!r}")
        mode = "RGB"
        if decoded.ndim == 2:
            mode = "L"
        elif decoded.shape[2] == 4:
            mode = "RGBA"
        return Image(decoded, mode)

    @staticmethod
    def fromarray(arr) -> "Image":
        array = np.asarray(arr)
        mode = "RGB" if array.ndim == 3 and array.shape[2] == 3 else (
            "RGBA" if array.ndim == 3 and array.shape[2] == 4 else "L")
        return Image(array, mode)

    @staticmethod
    def new(mode: str, size, color=0) -> "Image":
        width, height = size
        channels = 1 if mode == "L" else (4 if mode == "RGBA" else 3)
        if channels == 1:
            array = np.zeros((height, width), np.uint8)
        else:
            array = np.zeros((height, width, channels), np.uint8)
        array[:] = color
        return Image(array, mode)

    # ---------------------------------------------------------------- 属性
    @property
    def size(self):
        height, width = self._array.shape[:2]
        return (width, height)

    @property
    def width(self) -> int:
        return self.size[0]

    @property
    def height(self) -> int:
        return self.size[1]

    @property
    def shape(self):
        return self._array.shape

    def to_numpy(self) -> np.ndarray:
        return self._array

    def __array__(self, dtype=None):  # 方便 np.asarray(image)
        if dtype is None:
            return self._array
        return self._array.astype(dtype)

    # ---------------------------------------------------------------- 操作
    def crop(self, box):
        left, upper, right, lower = [int(v) for v in box]
        return Image(self._array[upper:lower, left:right].copy(), self.mode)

    def resize(self, size, resample=None):  # noqa: ARG002
        width, height = [int(v) for v in size]
        if cv2 is None:
            raise RuntimeError("cv2 required for Image.resize")
        interpolation = cv2.INTER_NEAREST if resample in (None, 0) else cv2.INTER_LINEAR
        return Image(cv2.resize(self._array, (width, height), interpolation=interpolation), self.mode)

    def convert(self, mode: str) -> "Image":
        if mode == self.mode:
            return Image(self._array.copy(), self.mode)
        if cv2 is None:
            raise RuntimeError("cv2 required for Image.convert")
        if mode == "L":
            gray = self._array if self._array.ndim == 2 else cv2.cvtColor(self._array, cv2.COLOR_RGB2GRAY)
            return Image(gray, "L")
        if mode == "RGB":
            if self._array.ndim == 2:
                rgb = cv2.cvtColor(self._array, cv2.COLOR_GRAY2RGB)
            else:
                rgb = cv2.cvtColor(self._array, cv2.COLOR_RGBA2RGB)
            return Image(rgb, "RGB")
        if mode == "RGBA":
            if self._array.ndim == 2:
                rgba = cv2.cvtColor(self._array, cv2.COLOR_GRAY2RGBA)
            else:
                rgba = cv2.cvtColor(self._array, cv2.COLOR_RGB2RGBA)
            return Image(rgba, "RGBA")
        raise ValueError(f"unsupported convert mode: {mode}")

    def copy(self) -> "Image":
        return Image(self._array.copy(), self.mode)

    def getpixel(self, xy):
        x, y = xy
        value = self._array[int(y), int(x)]
        return tuple(int(v) for v in value) if hasattr(value, "__len__") else int(value)

    def save(self, fp, format=None) -> None:  # noqa: A002 - 与 PIL 参数名一致
        if cv2 is None:
            raise RuntimeError("cv2 required for Image.save")
        suffix = ""
        if isinstance(fp, (str, os.PathLike)):
            suffix = os.path.splitext(os.fspath(fp))[1]
        fmt = (format or suffix.lstrip(".") or "png").lower()
        ext = ".jpg" if fmt in ("jpg", "jpeg") else ".png"
        ok, encoded = cv2.imencode(ext, self._array)
        if not ok:
            raise ValueError("encode failed")
        data = encoded.tobytes()
        if hasattr(fp, "write"):
            fp.write(data)
        else:
            with open(fp, "wb") as handle:
                handle.write(data)

    def __repr__(self) -> str:  # 便于日志
        return f"<Image mode={self.mode} size={self.size}>"


def __getattr__(name):  # 让 `from PIL import Image` 拿到的是模块对象
    if name == "Image":
        return _ImageModule
    raise AttributeError(name)

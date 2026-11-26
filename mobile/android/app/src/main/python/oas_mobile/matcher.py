# This Python file uses the following encoding: utf-8

"""在手机上做一次真实的「OAS 素材 + cv2 模板匹配」闭环。

OAS 的识图核心是 `cv2.matchTemplate` + 素材 PNG；本模块把两者搬到设备上跑：
    资产(APK assets) → 解码 → 与虚拟屏截图做多尺度匹配 → 返回最佳分数/坐标

素材通过 `android/app/src/main/assets/oas_templates/` 打进包；
读取用 Chaquopy 的 context（`com.chaquo.python.Python.getPlatform().getApplication().getAssets()`）。

对外：`match(json_args)` 返回 JSON 字符串，供 `/match` 端点使用。
"""

from __future__ import annotations

import json

ASSET_PREFIX = "oas_templates"


def _read_asset(name: str) -> bytes:
    """从 APK assets 读文件（桌面端不可用，返回空）。"""
    try:
        from java import jclass  # type: ignore
    except Exception:  # noqa: BLE001
        return b""
    try:
        python_cls = jclass("com.chaquo.python.Python")
        platform = python_cls.getPlatform()
        application = platform.getApplication()
        stream = application.getAssets().open(f"{ASSET_PREFIX}/{name}")
        # InputStream.readAllBytes() 需要 API 33；用逐块读，兼容旧系统
        buffer = bytearray()
        chunk = bytearray(8192)
        while True:
            read = int(stream.read(chunk))
            if read <= 0:
                break
            buffer.extend(chunk[:read])
        stream.close()
        return bytes(buffer)
    except Exception:  # noqa: BLE001
        return b""


def list_assets() -> list:
    try:
        from java import jclass  # type: ignore
        python_cls = jclass("com.chaquo.python.Python")
        assets = python_cls.getPlatform().getApplication().getAssets()
        return sorted(str(item) for item in assets.list(ASSET_PREFIX) or [])
    except Exception:  # noqa: BLE001
        return []


def match(asset_name: str, frame_png: bytes, scales=(0.8, 0.9, 1.0, 1.1, 1.25),
          max_frame_width: int = 540) -> dict:
    """把素材与截图做多尺度匹配，返回最佳结果。

    max_frame_width > 0 时先把大图等比缩小再匹配（主屏截图 1260x2800 直接匹配要几分钟），
    命中坐标会按缩放比换算回原图坐标。
    """
    import cv2
    import numpy as np

    template_bytes = _read_asset(asset_name)
    if not template_bytes:
        return {"ok": False, "error": f"asset not found: {asset_name}", "assets": list_assets()}
    if not frame_png:
        return {"ok": False, "error": "empty frame"}

    template = cv2.imdecode(np.frombuffer(template_bytes, np.uint8), cv2.IMREAD_COLOR)
    frame = cv2.imdecode(np.frombuffer(frame_png, np.uint8), cv2.IMREAD_COLOR)
    if template is None or frame is None:
        return {"ok": False, "error": "decode failed"}

    orig_w, orig_h = int(frame.shape[1]), int(frame.shape[0])
    coord_scale = 1.0
    if max_frame_width and orig_w > int(max_frame_width):
        coord_scale = orig_w / float(max_frame_width)
        new_w = int(max_frame_width)
        new_h = max(1, int(round(orig_h / coord_scale)))
        frame = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)

    import time

    from module.logger import logger

    started = time.perf_counter()
    best = {"score": -1.0, "loc": None, "scale": 1.0}
    for scale in scales:
        if abs(scale - 1.0) < 1e-6:
            scaled = template
        else:
            scaled = cv2.resize(template, None, fx=scale, fy=scale)
        if scaled.shape[0] > frame.shape[0] or scaled.shape[1] > frame.shape[1]:
            continue
        result = cv2.matchTemplate(frame, scaled, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        if max_val > best["score"]:
            best = {"score": float(max_val), "loc": (int(max_loc[0]), int(max_loc[1])),
                    "scale": float(scale), "template": (int(scaled.shape[1]), int(scaled.shape[0]))}

    elapsed_ms = (time.perf_counter() - started) * 1000.0
    if elapsed_ms > 8000:
        logger.warning(f'[match] {asset_name} slow: {elapsed_ms:.0f}ms frame={orig_w}x{orig_h}')
    else:
        logger.info(f'[match] {asset_name} {elapsed_ms:.0f}ms score={best["score"]:.3f} scale={best["scale"]}')

    loc = best["loc"]
    if loc is not None and coord_scale != 1.0:
        loc = (int(round(loc[0] * coord_scale)), int(round(loc[1] * coord_scale)))
    return {
        "ok": True,
        "asset": asset_name,
        "score": round(best["score"], 4),
        "loc": loc,
        "scale": best["scale"],
        "frame": (orig_w, orig_h),
        "template": best.get("template"),
        "downscale": round(coord_scale, 3),
    }


def tap_center(result: dict) -> tuple:
    """由 match() 的返回算目标中心（原图坐标）。

    `loc` 已经乘过 downscale 回到原图，`template` 仍是缩略帧里的尺寸，必须再乘一次；
    少了这一步中心会左偏 `template_w/2*(downscale-1)`（1600x900 实测：错 672,722 / 对 801,745）。
    """
    lx, ly = result["loc"]
    tw, th = result.get("template") or (0, 0)
    scale = float(result.get("downscale") or 1.0)
    return int(lx + tw * scale / 2), int(ly + th * scale / 2)


def match_args(args_json: str) -> str:
    """Kotlin/HTTP 侧入口：args_json = {"asset": "...", "frame_b64": "..."}。"""
    import base64

    try:
        args = json.loads(args_json or "{}")
        asset = str(args.get("asset", ""))
        frame = base64.b64decode(args.get("frame_b64", "")) if args.get("frame_b64") else b""
        result = match(asset, frame)
    except Exception as error:  # noqa: BLE001
        result = {"ok": False, "error": repr(error)}
    return json.dumps(result, ensure_ascii=False)

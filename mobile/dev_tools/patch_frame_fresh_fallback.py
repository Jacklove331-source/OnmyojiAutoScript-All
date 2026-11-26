import io, subprocess, sys
PATH = r"D:\3D\YYS2\oasx_mobile\android\app\src\main\python\oas_mobile\server.py"
text = io.open(PATH, "rb").read().decode("utf-8-sig")
old = """        if not newest:
            return {"ok": True, "has_frame": False,
                    "reason": "\u5386\u53f2\u5e27\u90fd\u662f\u5168\u9ed1\uff08\u6a21\u62df\u5668\u65e0\u969c\u788d\u622a\u5c4f\u9650\u5236\uff09",
                    "image": None}"""
new = """        if not newest:
            # \u5386\u53f2\u5e27\u5168\u662f\u9ed1\u7684\uff1a\u4e0d\u80fd\u5c31\u6b64\u8bf4\u6ca1\u5e27\uff0c\u5f97\u73b0\u6293\u4e00\u5f20\u518d\u5224
            try:
                fresh = bridge.capture_png()
            except Exception:  # noqa: BLE001
                fresh = None
            if fresh and len(fresh) >= 12000:
                _save_frame(fresh)
                return {"ok": True, "has_frame": True, "mtime": 0,
                        "image": base64.b64encode(fresh).decode("ascii")}
            return {"ok": True, "has_frame": False,
                    "reason": "\u6293\u5230\u7684\u5e27\u4ecd\u662f\u5168\u9ed1\uff08\u6a21\u62df\u5668\u65e0\u969c\u788d\u622a\u5c4f\u9650\u5236\uff09",
                    "image": None}"""
if old not in text:
    raise SystemExit("anchor not found")
io.open(PATH, "w", encoding="utf-8", newline="\n").write(text.replace(old, new, 1))
p = subprocess.run([sys.executable, "-m", "py_compile", PATH], capture_output=True, text=True, errors="replace")
io.open(r"D:\3D\YYS2\oasx_mobile\dist\_patch_log28.txt", "w", encoding="utf-8").write("rc=%d\n%s" % (p.returncode, p.stderr[-300:]))
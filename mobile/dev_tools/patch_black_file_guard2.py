import io, subprocess, sys
PATH = r"D:\3D\YYS2\oasx_mobile\android\app\src\main\python\oas_mobile\server.py"
text = io.open(PATH, "rb").read().decode("utf-8-sig")
old = """        newest = files[0]
        with open(newest, "rb") as handle:
            data = base64.b64encode(handle.read()).decode("ascii")"""
new = """        newest = files[0]
        raw = open(newest, "rb").read()
        if len(raw) < 12000:  # 720x1280 \u5168\u9ed1\u5e27\u538b\u7f29\u540e\u4e5f\u5c31\u8fd9\u4e48\u5c0f
            return {"ok": True, "has_frame": False,
                    "reason": "\u5386\u53f2\u5e27\u90fd\u662f\u5168\u9ed1\uff08\u6a21\u62df\u5668\u65e0\u969c\u788d\u622a\u5c4f\u9650\u5236\uff09",
                    "image": None}
        data = base64.b64encode(raw).decode("ascii")"""
if old not in text:
    raise SystemExit("file branch not found")
io.open(PATH, "w", encoding="utf-8", newline="\n").write(text.replace(old, new, 1))
p = subprocess.run([sys.executable, "-m", "py_compile", PATH], capture_output=True, text=True, errors="replace")
io.open(r"D:\3D\YYS2\oasx_mobile\dist\_patch_log21.txt", "w", encoding="utf-8").write("rc=%d\n%s" % (p.returncode, p.stderr[-300:]))
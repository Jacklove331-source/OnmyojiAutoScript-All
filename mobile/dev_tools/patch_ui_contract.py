import io, subprocess, sys
PATH = r"D:\3D\YYS2\oasx_mobile\android\app\src\main\python\oas_mobile\server.py"
text = io.open(PATH, "rb").read().decode("utf-8-sig")
pairs = [
    ('            title = _title_of(mapping, name)\n            if title:\n                field["title_zh"] = title\n',
     '            title = _title_of(mapping, name)\n            if title:\n                field["title_zh"] = title\n                field.setdefault("title_en", field.get("title"))\n                field["title"] = title  # \u524d\u7aef\u8bfb title\uff0c\u76f4\u63a5\u7ed9\u4e2d\u6587\n'),
    ('            field["options"] = [{"key": v, "label": v} for v in candidates]\n',
     '            field["options"] = [{"value": v, "label": v} for v in candidates]\n'),
]
for old, new in pairs:
    if old not in text:
        raise SystemExit("anchor missing: %r" % old[:40])
    text = text.replace(old, new, 1)
io.open(PATH, "w", encoding="utf-8", newline="\n").write(text)
p = subprocess.run([sys.executable, "-m", "py_compile", PATH], capture_output=True, text=True, errors="replace")
io.open(r"D:\3D\YYS2\oasx_mobile\dist\_patch_log13.txt", "w", encoding="utf-8").write("rc=%d\n%s" % (p.returncode, p.stderr[-200:]))
import io, subprocess, sys
PATH = r"D:\3D\YYS2\oasx_mobile\android\app\src\main\python\oas_mobile\server.py"
text = io.open(PATH, "rb").read().decode("utf-8-sig")
old = '            field["option_values"] = list(candidates)\n'
new = '            field["option_values"] = list(candidates)\n            field["option_labels"] = list(candidates)\n'
if old not in text:
    raise SystemExit("anchor missing")
io.open(PATH, "w", encoding="utf-8", newline="\n").write(text.replace(old, new, 1))
p = subprocess.run([sys.executable, "-m", "py_compile", PATH], capture_output=True, text=True, errors="replace")
io.open(r"D:\3D\YYS2\oasx_mobile\dist\_patch_log14.txt", "w", encoding="utf-8").write("rc=%d" % p.returncode)
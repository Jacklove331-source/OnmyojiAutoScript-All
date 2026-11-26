import io, subprocess, sys
PATH = r"D:\3D\YYS2\oasx_mobile\android\app\src\main\python\oas_mobile\server.py"
text = io.open(PATH, "rb").read().decode("utf-8-sig")
old = "    enum_index = _enum_index(task_model) if task_model is not None else {}\n"
new = ("    enum_index = _enum_index(task_model) if task_model is not None else {}\n"
       "    logger.info('[mobile] enum probe model=%r index=%d keys=%r',\n"
       "                type(task_model).__name__, len(enum_index), list(enum_index)[:5])\n")
if old not in text:
    raise SystemExit("anchor missing")
io.open(PATH, "w", encoding="utf-8", newline="\n").write(text.replace(old, new, 1))
p = subprocess.run([sys.executable, "-m", "py_compile", PATH], capture_output=True, text=True, errors="replace")
io.open(r"D:\3D\YYS2\oasx_mobile\dist\_patch_log8.txt", "w", encoding="utf-8").write("rc=%d" % p.returncode)
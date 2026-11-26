import io, subprocess, sys
PATH = r"D:\3D\YYS2\oasx_mobile\android\app\src\main\python\oas_mobile\server.py"
text = io.open(PATH, "rb").read().decode("utf-8-sig")
old = ("    logger.info('[mobile] enum probe model=%r index=%d keys=%r',\n"
       "                type(task_model).__name__, len(enum_index), list(enum_index)[:5])\n")
new = ("    _mf = _model_fields(task_model)\n"
       "    _first = next(iter(_mf.items()), (None, None))\n"
       "    _ga = _field_annotation(_first[1]) if _first[1] is not None else None\n"
       "    logger.info('[mobile] enum probe model=%r index=%d fields=%d first=%r group=%r sub=%d',\n"
       "                type(task_model).__name__, len(enum_index), len(_mf), _first[0], _ga,\n"
       "                len(_model_fields(_ga)))\n")
if old not in text:
    raise SystemExit("anchor missing")
io.open(PATH, "w", encoding="utf-8", newline="\n").write(text.replace(old, new, 1))
p = subprocess.run([sys.executable, "-m", "py_compile", PATH], capture_output=True, text=True, errors="replace")
io.open(r"D:\3D\YYS2\oasx_mobile\dist\_patch_log10.txt", "w", encoding="utf-8").write("rc=%d" % p.returncode)
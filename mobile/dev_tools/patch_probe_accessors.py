import io, subprocess, sys
PATH = r"D:\3D\YYS2\oasx_mobile\android\app\src\main\python\oas_mobile\server.py"
OLD = '''                try:
                    result = shizuku.exec("id", 5000)
                    payload["ok"] = True
                    payload["code"] = int(getattr(result, "code", -999))
                    payload["output"] = str(getattr(result, "output", ""))[:400]
                except Exception as error:  # noqa: BLE001
                    payload["error"] = f"{type(error).__name__}: {error}"'''
NEW = '''                def _pick(obj, name, default=None):
                    try:
                        return getattr(obj, name)
                    except Exception:  # noqa: BLE001
                        pass
                    try:
                        return obj.callAttr(name)
                    except Exception:  # noqa: BLE001
                        return default

                try:
                    result = shizuku.exec("id", 5000)
                    payload["ok"] = True
                    payload["repr"] = repr(result)[:300]
                    payload["code"] = _pick(result, "code", _pick(result, "getCode"))
                    payload["output"] = str(_pick(result, "output", _pick(result, "getOutput", "")))[:400]
                except Exception as error:  # noqa: BLE001
                    payload["error"] = f"{type(error).__name__}: {error}"'''
text = io.open(PATH, "rb").read().decode("utf-8-sig")
if OLD not in text:
    raise SystemExit("anchor missing")
io.open(PATH, "w", encoding="utf-8", newline="\n").write(text.replace(OLD, NEW, 1))
p = subprocess.run([sys.executable, "-m", "py_compile", PATH], capture_output=True, text=True, errors="replace")
io.open(r"D:\3D\YYS2\oasx_mobile\dist\_patch_log41.txt", "w", encoding="utf-8").write("rc=%d\n%s" % (p.returncode, p.stderr[-300:]))
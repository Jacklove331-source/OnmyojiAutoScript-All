import io, subprocess, sys
PATH = r"D:\3D\YYS2\oasx_mobile\android\app\src\main\python\module\device\device.py"
OLD = '''            png = bridge.capture_png()
            if not png:
                raise RuntimeError("no frame: virtual display unavailable and capture failed")
            frame = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)'''
NEW = '''            import cv2 as _cv2
            import numpy as _np

            png = bridge.capture_png()
            if not png:
                raise RuntimeError("no frame: virtual display unavailable and capture failed")
            frame = _cv2.imdecode(_np.frombuffer(png, _np.uint8), _cv2.IMREAD_COLOR)'''
text = io.open(PATH, "rb").read().decode("utf-8-sig")
if OLD not in text:
    raise SystemExit("anchor missing")
text = text.replace(OLD, NEW, 1)
text = text.replace("            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)\n            with self._lock:\n                self._image = frame\n                self._image_frame_id += 1\n            return frame",
                    "            frame = _cv2.cvtColor(frame, _cv2.COLOR_BGR2RGB)\n            with self._lock:\n                self._image = frame\n                self._image_frame_id += 1\n            return frame", 1)
io.open(PATH, "w", encoding="utf-8", newline="\n").write(text)
p = subprocess.run([sys.executable, "-m", "py_compile", PATH], capture_output=True, text=True, errors="replace")
io.open(r"D:\3D\YYS2\oasx_mobile\dist\_patch_log39.txt", "w", encoding="utf-8").write("rc=%d\n%s" % (p.returncode, p.stderr[-400:]))
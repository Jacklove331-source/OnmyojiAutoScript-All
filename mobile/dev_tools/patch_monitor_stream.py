"""把 MAA-Meow 那套「Shizuku 起 scrcpy-server + App 内解码」接进 App。

改动：
1. assets/ 放 scrcpy-server；ScreenStream 启动前把它复制到 /data/local/tmp 并 chmod（shell uid 要能读）；
2. VdHostDisplay 暴露 apkPathForStream()（给 ScreenStream 拼 CLASSPATH）；
3. bridge.py 增加 vdstream_start / vdstream_stop / vdstream_status / vdstream_jpeg；
4. server.py 增加 /monitor/start /monitor/stop /monitor/status /monitor/frame。
"""

import io
import os
import shutil
import subprocess
import sys

ROOT = r"D:\3D\YYS2\oasx_mobile"
ANDROID = os.path.join(ROOT, "android")
KT = os.path.join(ANDROID, "app", "src", "main", "kotlin", "com", "oas", "oasx")
PY = os.path.join(ANDROID, "app", "src", "main", "python", "oas_mobile")
SERVER_JAR_SRC = os.path.join(ROOT, "dist", "scrcpy-server")
ASSET = os.path.join(ANDROID, "app", "src", "main", "assets", "scrcpy-server")

log = []

# ---------------------------------------------------------------- 1) asset
os.makedirs(os.path.dirname(ASSET), exist_ok=True)
if os.path.exists(SERVER_JAR_SRC):
    shutil.copyfile(SERVER_JAR_SRC, ASSET)
    log.append(f"asset copied: {os.path.getsize(ASSET)} bytes")
else:
    log.append("!! scrcpy-server 源文件缺失")

# ---------------------------------------------------------------- 2) VdHostDisplay helper
vd_path = os.path.join(KT, "VdHostDisplay.kt")
vd = io.open(vd_path, "rb").read().decode("utf-8-sig")
HELPER = '''
    /** 给 ScreenStream 用：拿本 APK 路径（app_process 的 CLASSPATH）。 */
    @JvmStatic
    fun apkPathForStream(): String {
        val context = application ?: return ""
        return apkPath(context)
    }
'''
if "apkPathForStream" not in vd:
    anchor = "    @JvmStatic\n    fun isRunning(): Boolean {"
    if anchor not in vd:
        raise SystemExit("anchor missing: isRunning")
    vd = vd.replace(anchor, HELPER + "\n" + anchor, 1)
    io.open(vd_path, "w", encoding="utf-8", newline="\n").write(vd)
    log.append("VdHostDisplay: apkPathForStream added")
else:
    log.append("VdHostDisplay: already patched")

# ---------------------------------------------------------------- 3) ScreenStream: 部署 server
ss_path = os.path.join(KT, "ScreenStream.kt")
ss = io.open(ss_path, "rb").read().decode("utf-8-sig")
OLD_DEPLOY = '''        val displayArg = if (displayId >= 0) "display_id=$displayId" else "new_display=${width}x$height"'''
NEW_DEPLOY = '''        val serverPath = deployServer()
        if (serverPath.isEmpty()) {
            lastError = "deploy scrcpy-server failed"
            return false
        }

        val displayArg = if (displayId >= 0) "display_id=$displayId" else "new_display=${width}x$height"'''
if "deployServer()" not in ss:
    if OLD_DEPLOY not in ss:
        raise SystemExit("anchor missing: displayArg")
    ss = ss.replace(OLD_DEPLOY, NEW_DEPLOY, 1)

    # CLASSPATH 用部署后的路径
    ss = ss.replace('val inner = "CLASSPATH=$apk exec app_process',
                    'val inner = "CLASSPATH=$serverPath exec app_process', 1)

    DEPLOY_FN = '''
    /**
     * 把 assets 里的 scrcpy-server 复制到 /data/local/tmp 并 chmod 644。
     * app 私有目录（/data/user/0/...）是 0700，shell uid 读不到，必须放到 shell 能读的地方。
     */
    private fun deployServer(): String {
        val target = "/data/local/tmp/scrcpy-server"
        return try {
            val context = VdHostDisplay.appContext()
            if (context == null) {
                lastError = "no context for asset"
                return ""
            }
            val local = java.io.File(context.filesDir, "scrcpy-server")
            context.assets.open("scrcpy-server").use { input ->
                local.outputStream().use { output -> input.copyTo(output) }
            }
            val copy = ShizukuShell.exec("cp ${local.absolutePath} $target && chmod 644 $target && ls -l $target", 15000)
            Log.i(TAG, "deploy: code=${copy.code} out=${copy.output.take(120)}")
            if (copy.code != 0) {
                lastError = "deploy: ${copy.output.take(160)}"
                ""
            } else {
                target
            }
        } catch (error: Throwable) {
            lastError = "deploy failed: ${error.message}"
            ""
        }
    }
'''
    anchor = "    private fun hasSu(): Boolean ="
    ss = ss.replace(anchor, DEPLOY_FN + "\n" + anchor, 1)
    io.open(ss_path, "w", encoding="utf-8", newline="\n").write(ss)
    log.append("ScreenStream: deployServer added")
else:
    log.append("ScreenStream: already patched")

# VdHostDisplay 暴露 context
if "fun appContext()" not in vd:
    vd = io.open(vd_path, "rb").read().decode("utf-8-sig")
    CTX = '''
    /** 给其它组件取 Application Context（Chaquopy 侧不方便直连）。 */
    @JvmStatic
    fun appContext(): android.content.Context? = application
'''
    anchor2 = "    @JvmStatic\n    fun isRunning(): Boolean {"
    vd = vd.replace(anchor2, CTX + "\n" + anchor2, 1)
    io.open(vd_path, "w", encoding="utf-8", newline="\n").write(vd)
    log.append("VdHostDisplay: appContext added")

# ---------------------------------------------------------------- 4) bridge.py
bridge_path = os.path.join(PY, "bridge.py")
bridge = io.open(bridge_path, "rb").read().decode("utf-8-sig")
if "def vdstream_start" not in bridge:
    bridge += '''

_STREAM = None
try:
    _STREAM = jclass("com.oas.oasx.ScreenStream")
except Exception:  # noqa: BLE001
    _STREAM = None


def vdstream_start(width: int = 1280, height: int = 720, display_id: int = -1) -> bool:
    """抄 MAA-Meow：Shizuku 起 scrcpy-server，App 侧解码拿帧。"""
    if _STREAM is None:
        return False
    try:
        return bool(_STREAM.start(int(width), int(height), int(display_id)))
    except Exception:  # noqa: BLE001
        return False


def vdstream_stop() -> bool:
    if _STREAM is None:
        return False
    try:
        return bool(_STREAM.stop())
    except Exception:  # noqa: BLE001
        return False


def vdstream_status() -> str:
    if _STREAM is None:
        return "no ScreenStream"
    try:
        return str(_STREAM.status())
    except Exception as error:  # noqa: BLE001
        return f"error: {error}"


def vdstream_jpeg() -> bytes:
    if _STREAM is None:
        return b""
    try:
        data = _STREAM.latestJpeg()
        return bytes(data) if data else b""
    except Exception:  # noqa: BLE001
        return b""
'''
    io.open(bridge_path, "w", encoding="utf-8", newline="\n").write(bridge)
    log.append("bridge.py: vdstream_* added")
else:
    log.append("bridge.py: already patched")

# ---------------------------------------------------------------- 5) server.py
server_path = os.path.join(PY, "server.py")
server = io.open(server_path, "rb").read().decode("utf-8-sig")
if '"/monitor/start"' not in server:
    NEW_ROUTES = '''        if route.path == "/monitor/start":
            ok = bridge.vdstream_start(int(query.get("width", 1280)),
                                       int(query.get("height", 720)),
                                       int(query.get("display", -1)))
            self._send_json(200, {"ok": ok, "status": bridge.vdstream_status()})
            return
        if route.path == "/monitor/stop":
            self._send_json(200, {"ok": bridge.vdstream_stop()})
            return
        if route.path == "/monitor/status":
            self._send_json(200, {"ok": True, "status": bridge.vdstream_status()})
            return
        if route.path == "/monitor/frame":
            data = bridge.vdstream_jpeg()
            if not data:
                self._send_json(503, {"ok": False, "error": "no frame", "status": bridge.vdstream_status()})
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return
'''
    anchor3 = '        if route.path == "/vd/probe":'
    if anchor3 not in server:
        raise SystemExit("anchor missing: /vd/probe")
    server = server.replace(anchor3, NEW_ROUTES + anchor3, 1)
    io.open(server_path, "w", encoding="utf-8", newline="\n").write(server)
    log.append("server.py: /monitor/* added")
else:
    log.append("server.py: already patched")

rc = subprocess.run([sys.executable, "-m", "py_compile", bridge_path, server_path],
                    capture_output=True, text=True, errors="replace")
log.append(f"py_compile rc={rc.returncode} {rc.stderr[-200:]}")
io.open(os.path.join(ROOT, "dist", "_patch_log42.txt"), "w", encoding="utf-8").write("\n".join(log))

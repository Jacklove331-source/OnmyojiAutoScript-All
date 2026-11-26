"""最小 WebSocket 服务端（stdlib 实现），给 OASX 界面推送状态/日志。

OASX 的协议（从 lib/service/script_service.dart 读出）：
- 客户端发文本帧：`start` / `stop` / `get_state`
- 服务端推：
  · 纯文本行 = 日志（界面直接 append 到日志面板）
  · JSON：`{"state": "running"|"idle"}`、`{"schedule": {...}}`（可选）

实现要点：ThreadingHTTPServer 每条连接一个线程，所以在 do_GET 里做 101 握手后
直接进入该连接的循环即可（阻塞不影响其它请求）。
"""

from __future__ import annotations

import base64
import hashlib
import json
import socket
import struct
import time

MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_TIMEOUT_OPCODE = -1


def handshake_response(key: str) -> bytes:
    """根据 Sec-WebSocket-Key 生成 101 响应头。"""
    accept = base64.b64encode(hashlib.sha1((key + MAGIC).encode()).digest()).decode()
    return (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
    ).encode()


def encode_text(payload: str) -> bytes:
    """服务端→客户端：不加掩码的文本帧。"""
    data = payload.encode("utf-8")
    header = bytearray([0x81])
    length = len(data)
    if length < 126:
        header.append(length)
    elif length < (1 << 16):
        header.append(126)
        header += struct.pack(">H", length)
    else:
        header.append(127)
        header += struct.pack(">Q", length)
    return bytes(header) + data


def _recv_exact(sock, count: int) -> bytes:
    buf = bytearray()
    while len(buf) < count:
        chunk = sock.recv(count - len(buf))
        if not chunk:
            return b""
        buf += chunk
    return bytes(buf)


def read_frame(sock):
    """读一帧客户端消息（带掩码）。

    返回:
      (_TIMEOUT_OPCODE, None) 表示读取超时（连接仍存活，可推进定时推送）；
      (None, None) 表示对端已断开或 socket 异常（应退出循环）；
      (opcode, payload) 表示完整收到一帧。
    """
    try:
        first = _recv_exact(sock, 2)
        if len(first) < 2:
            return None, None
        opcode = first[0] & 0x0F
        masked = first[1] & 0x80
        length = first[1] & 0x7F
        if length == 126:
            ext = _recv_exact(sock, 2)
            if len(ext) < 2:
                return None, None
            length = struct.unpack(">H", ext)[0]
        elif length == 127:
            ext = _recv_exact(sock, 8)
            if len(ext) < 8:
                return None, None
            length = struct.unpack(">Q", ext)[0]
        mask = _recv_exact(sock, 4) if masked else b"\x00\x00\x00\x00"
        if len(mask) < 4:
            return None, None
        payload = bytearray(_recv_exact(sock, length)) if length else bytearray()
        if len(payload) < length:
            return None, None
        if masked:
            payload = bytearray(b ^ mask[i % 4] for i, b in enumerate(payload))
        return opcode, bytes(payload)
    except (socket.timeout, TimeoutError):
        return _TIMEOUT_OPCODE, None
    except OSError:
        return None, None


def serve_connection(sock, name: str, on_message) -> None:
    """连接循环：处理客户端消息 + 定时推送状态/日志。"""
    last_push = 0.0
    sock.settimeout(1.0)
    while True:
        try:
            opcode, payload = read_frame(sock)
        except Exception:  # noqa: BLE001
            break
        if opcode is None:
            break
        if opcode == 0x8:  # close
            break
        if opcode in (0x1, 0x2) and payload:
            text = payload.decode("utf-8", "replace").strip()
            try:
                reply = on_message(name, text)
            except Exception as error:  # noqa: BLE001
                reply = [f'error: {error!r}']
            try:
                for line in reply or []:
                    sock.sendall(encode_text(line))
            except OSError:
                break
        now = time.time()
        if now - last_push >= 2.0:
            last_push = now
            try:
                for line in on_message(name, "__tick__") or []:
                    sock.sendall(encode_text(line))
            except Exception:  # noqa: BLE001
                break
    try:
        sock.close()
    except OSError:
        pass

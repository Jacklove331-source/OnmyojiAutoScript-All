# This Python file uses the following encoding: utf-8

"""验证 APK 内的 WebSocket（/ws/<config>）能握手、能回 get_state、能推日志。

用法：.\\toolkit\\python.exe dev_tools\\mobile\\verify_ws.py [--name oas1]
前置：adb forward tcp:22267 tcp:22267
"""

import argparse
import base64
import os
import socket
import struct
import sys


def handshake(sock: socket.socket, host: str, port: int, path: str) -> bool:
    key = base64.b64encode(os.urandom(16)).decode()
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n\r\n"
    )
    sock.sendall(request.encode())
    response = sock.recv(4096).decode("utf-8", "replace")
    return "101" in response.split("\r\n")[0]


def send_text(sock: socket.socket, text: str) -> None:
    payload = text.encode()
    mask = os.urandom(4)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    header = bytearray([0x81])
    length = len(payload)
    if length < 126:
        header.append(0x80 | length)
    else:
        header.append(0x80 | 126)
        header += struct.pack(">H", length)
    sock.sendall(bytes(header) + mask + masked)


def read_text(sock: socket.socket, timeout: float = 6.0):
    sock.settimeout(timeout)
    try:
        first = sock.recv(2)
        if len(first) < 2:
            return None
        opcode = first[0] & 0x0F
        length = first[1] & 0x7F
        if length == 126:
            length = struct.unpack(">H", sock.recv(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", sock.recv(8))[0]
        payload = b""
        while len(payload) < length:
            chunk = sock.recv(length - len(payload))
            if not chunk:
                break
            payload += chunk
        if opcode == 0x8:
            return None
        return payload.decode("utf-8", "replace")
    except socket.timeout:
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--name', default='oas1')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=22267)
    args = parser.parse_args()

    results = []

    def check(name, ok, detail=''):
        results.append((name, bool(ok)))
        print(f'[{"PASS" if ok else "FAIL"}] {name}' + (f'  ({detail})' if detail and not ok else ''))

    sock = socket.create_connection((args.host, args.port), timeout=8)
    ok = handshake(sock, args.host, args.port, f'/ws/{args.name}')
    check('WebSocket 握手成功（101）', ok)
    if not ok:
        return 1

    send_text(sock, 'get_state')
    state = read_text(sock)
    check('get_state 返回 JSON 状态', bool(state and state.startswith('{')), str(state)[:80])
    if state:
        print('   state frame:', state[:120])

    # 等一拍心跳日志（服务端每 2s 推一次）
    tick = read_text(sock, timeout=8)
    check('收到服务端推送（心跳/日志）', bool(tick), str(tick)[:80])
    if tick:
        print('   push frame:', tick[:120])

    sock.close()
    failed = [name for name, ok in results if not ok]
    print()
    print(f'TOTAL {len(results)}  PASS {len(results) - len(failed)}  FAIL {len(failed)}')
    return 0 if not failed else 1


if __name__ == '__main__':
    sys.exit(main())

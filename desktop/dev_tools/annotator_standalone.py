# This Python file uses the following encoding: utf-8

"""独立启动 OAS 官方的「素材标注器」（annotator）。

OAS 官方本来就带了一套画面适配工具：`module/server/tool_router.py` 的 `/tool/annotator`
（前端在 `module/server/web/annotator/`）。它能：
  - 从模拟器实时抓帧（或者自己上传截图）；
  - 按任务列出 `image_*.json` 里的规则，逐条跑模板匹配、显示相似度；
  - 在图上画 ROI 改框，保存规则并自动重新生成 `assets.py`；
  - OCR 规则同样能测试并回填 roi。

但它平时只能从 OAS 的 WebUI 里进（`http://127.0.0.1:<WebuiPort>/tool/annotator`），
要先把整个 OAS Web 服务开起来。本脚本把这一页单独跑起来：

  * 只挂 `/tool` 路由 + annotator 静态资源，不启动调度器、不拉起实例、不动配置；
  * 独立端口（默认 23333，被占会自动顺延），不和正在跑的 OAS WebUI 冲突；
  * 启动后自动打开浏览器。

用法：
    .\\toolkit\\python.exe dev_tools\\annotator_standalone.py
    .\\toolkit\\python.exe dev_tools\\annotator_standalone.py --port 23456 --no-open
    .\\toolkit\\python.exe dev_tools\\annotator_standalone.py --capture-log   # 同时写日志

注意：页面里点「抓取模拟器画面」时才会去连 MuMu（走 nemu_ipc）。OAS 实例正在跑任务时
不要同时抓帧（两个设备进程抢 IPC 会成片截图超时），先停实例或直接用上传截图的方式。
"""

import argparse
import os
import pathlib
import socket
import sys
import threading
import time
import webbrowser

ROOT = pathlib.Path(__file__).resolve().parents[1]


def find_free_port(start: int, limit: int = 20) -> int:
    for port in range(start, start + limit):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(('127.0.0.1', port))
            except OSError:
                continue
            return port
    raise SystemExit(f'{start}~{start + limit} 都被占用，请用 --port 指定其它端口')


def build_app():
    """最小 FastAPI：只要 /tool 路由和 annotator 静态资源。"""
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from starlette.staticfiles import StaticFiles

    from module.server.tool_router import tool_app

    app = FastAPI(title='OAS Annotator Standalone', version='1.0')
    app.add_middleware(
        CORSMiddleware,
        allow_origins=['*'],
        allow_credentials=True,
        allow_methods=['*'],
        allow_headers=['*'],
    )
    app.include_router(tool_app)

    static_dir = ROOT / 'module' / 'server' / 'web' / 'annotator' / 'static'
    if not static_dir.exists():
        raise SystemExit(f'找不到 annotator 前端资源：{static_dir}')

    class NoCacheStaticFiles(StaticFiles):
        def file_response(self, *args, **kwargs):
            response = super().file_response(*args, **kwargs)
            response.headers['Cache-Control'] = 'no-cache'
            return response

    app.mount('/tool/annotator/static',
              NoCacheStaticFiles(directory=str(static_dir)),
              name='annotator_static')
    return app


def main() -> int:
    parser = argparse.ArgumentParser(description='独立启动 OAS 素材标注器')
    parser.add_argument('--port', type=int, default=23333, help='监听端口，默认 23333')
    parser.add_argument('--no-open', action='store_true', help='不自动打开浏览器')
    parser.add_argument('--capture-log', action='store_true',
                        help='把 annotator 的 logger 输出也写到控制台')
    args = parser.parse_args()

    if not (ROOT / 'module' / 'server' / 'web' / 'annotator' / 'index.html').exists():
        print('[FAIL] 当前仓库没有 annotator 前端（module/server/web/annotator/index.html）')
        return 1

    # tool.py 用 Path.cwd() 当 PROJECT_ROOT，必须切到仓库根
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))

    if args.capture_log:
        from module.logger import logger
        logger.add(sys.stdout, level='INFO')

    port = find_free_port(args.port)
    url = f'http://127.0.0.1:{port}/tool/annotator'

    print('=' * 68)
    print('OAS 素材标注器（独立模式）')
    print(f'仓库 : {ROOT}')
    print(f'地址 : {url}')
    print('用法 : 左侧选任务 → 导入/抓取截图 → 点规则看相似度 → 画框保存 → 自动重生成 assets.py')
    print('退出 : 本窗口 Ctrl+C')
    print('=' * 68)

    if not args.no_open:
        threading.Thread(target=lambda: (time.sleep(1.2), webbrowser.open(url)),
                         daemon=True).start()

    try:
        import uvicorn
    except ImportError:
        print('[FAIL] 缺少 uvicorn，请在仓库根执行：'
              r'.\toolkit\python.exe -m pip install uvicorn')
        return 1

    app = build_app()
    uvicorn.run(app, host='127.0.0.1', port=port, log_level='info')
    return 0


if __name__ == '__main__':
    sys.exit(main())

# This Python file uses the following encoding: utf-8

"""截图降级（nemu_ipc → ADB）回归测试：验证状态机，与实机截图健康**完全无关**。

背景：MuMu 渲染 IPC 会偶发卡死（`nemu_capture_display` 每次 0.15s 超时，重试 3 次后抛
`RequestHumanTakeover`），而同一时刻 ADB screencap 正常。`Device.screenshot()` 因此增加
降级 + 每约 30s 探测一次主方式、恢复即切回，且**不改写**用户配置。

设计约束（2026-10-28 返工）：本脚本是 AC05 的回归项，绝不能依赖 live 截图——
否则通道抖动时（实测 48 次 timeout）AC05 会 FAIL，把「代码回归」和「环境健康」混在一起。
因此：
- 状态机全部跑在**合成帧** + 假的 `screenshot_adb` 上，任何环境下结果一致；
- 实机健康探测只在显式 `--live` 时执行，且只打印信息、不参与判定、失败也不影响后续步骤。

用法：
    .\\toolkit\\python.exe dev_tools\\screenshot_fallback_check.py            # 纯回归（AC05 用这个）
    .\\toolkit\\python.exe dev_tools\\screenshot_fallback_check.py --live     # 额外报告实机健康
"""

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from module.config.config import Config  # noqa: E402
from module.device.device import Device  # noqa: E402
from module.exception import RequestHumanTakeover  # noqa: E402

RESULTS = []
SYNTHETIC = np.zeros((720, 1280, 3), dtype=np.uint8)


def check(name, condition, detail=''):
    RESULTS.append((name, bool(condition), detail))
    print(f'[{"PASS" if condition else "FAIL"}] {name}'
          + (f'  ({detail})' if detail and not condition else ''))


def boom(*args, **kwargs):
    raise RequestHumanTakeover('simulated nemu_ipc failure')


def report_live_capture(device) -> str:
    """可选：报告实机截图健康。任何异常都只记成信息，不影响判定。"""
    try:
        image = device.screenshot()
        if image is None or getattr(image, 'size', 0) == 0:
            return 'empty frame'
        return f'ok shape={getattr(image, "shape", None)}'
    except Exception as error:  # noqa: BLE001 - 环境问题不阻塞回归
        return f'degraded ({type(error).__name__})'


def main() -> int:
    args = [item for item in sys.argv[1:] if not item.startswith('--')]
    live_mode = '--live' in sys.argv
    config_name = args[0] if args else 'oas3'
    device = Device(Config(config_name))
    # 前台判定依赖实机状态（ADB 焦点），必须打桩；否则游戏不在前台时本测试会假失败。
    device._app_foreground_ok = lambda: True
    device.app_is_running = lambda: True
    method_key = device.config.script.device.screenshot_method

    print(f'configured screenshot_method = {method_key}')
    print(f'force_adb flag(before) = {getattr(device, "_screenshot_force_adb", None)}')
    if live_mode:
        print(f'live_capture = {report_live_capture(device)}（仅信息，不参与判定）')

    # 状态机从第一行起只用合成帧：不读取任何 live 帧
    frame = SYNTHETIC.copy()

    # 1) 主方式故障 → 降级到 ADB（ADB 用假方法，与实机无关）
    device.screenshot_adb = lambda: frame.copy()
    for name in ('nemu_ipc', 'uiautomator2', 'DroidCast', 'DroidCast_raw',
                 'window_background'):
        device.screenshot_methods[name] = boom
    image = device.screenshot()
    check('主方式故障时仍拿到帧（降级成功）',
          image is not None and getattr(image, 'shape', (0,))[0] > 0,
          str(getattr(image, 'shape', None)))
    check('降级后置位 _screenshot_force_adb',
          getattr(device, '_screenshot_force_adb', False) is True)
    check('降级后帧号递增', int(device.image_frame_id) >= 1,
          str(device.image_frame_id))

    # 2) 后续帧直接走 ADB（不再触碰会抛异常的主方式）
    calls = {'n': 0}

    def counting_boom(*args, **kwargs):
        calls['n'] += 1
        raise RequestHumanTakeover('should not be called again')

    device.screenshot_methods['nemu_ipc'] = counting_boom
    image = device.screenshot()
    check('降级后第二帧仍成功',
          image is not None and getattr(image, 'shape', (0,))[0] > 0)
    check('降级后不再调用故障方法', calls['n'] == 0, str(calls['n']))
    check('screenshot_method 配置未被改写',
          device.config.script.device.screenshot_method == method_key,
          str(device.config.script.device.screenshot_method))

    # 3) 主方式恢复 → 低频探测命中 → 清降级标记并切回主方式
    restored = {'n': 0}

    def restored_method(*args, **kwargs):
        restored['n'] += 1
        return frame.copy()

    device.screenshot_methods[method_key] = restored_method
    device._screenshot_probe_interval = 0  # 测试里跳过 30s 等待，立即探测
    device._screenshot_probe_last = 0.0
    image = device.screenshot()
    check('主方式恢复后探测命中且仍拿到帧',
          image is not None and getattr(image, 'shape', (0,))[0] > 0,
          str(getattr(image, 'shape', None)))
    check('探测命中后清除 _screenshot_force_adb',
          getattr(device, '_screenshot_force_adb', True) is False)
    check('探测命中后确实调用了恢复的主方式', restored['n'] >= 1,
          str(restored['n']))
    check('切回后 screenshot_method 配置未被改写',
          device.config.script.device.screenshot_method == method_key,
          str(device.config.script.device.screenshot_method))

    # 4) 主方式仍坏 → 探测失败后继续走 ADB，不退出、不粘住配置
    device._screenshot_force_adb = True
    device.screenshot_methods[method_key] = counting_boom
    calls['n'] = 0
    device._screenshot_probe_last = 0.0
    image = device.screenshot()
    check('主方式仍坏时探测后继续走 ADB 拿到帧',
          image is not None and getattr(image, 'shape', (0,))[0] > 0,
          str(getattr(image, 'shape', None)))
    check('主方式仍坏时保持 _screenshot_force_adb',
          getattr(device, '_screenshot_force_adb', False) is True)
    check('主方式仍坏时探测确实重试过主方式', calls['n'] >= 1,
          str(calls['n']))
    check('继续 ADB 时 screenshot_method 配置未被改写',
          device.config.script.device.screenshot_method == method_key,
          str(device.config.script.device.screenshot_method))

    # 5) 主方式抛裸 TimeoutError（IPC 卡死的另一种真实表现）也要降级，而不是崩掉
    released = {'n': 0}
    device._screenshot_force_adb = False
    device._screenshot_fail_count = 0
    device._screenshot_release_at = 0.0
    device.nemu_ipc_release = lambda: released.__setitem__('n', released['n'] + 1)
    device.app_is_running = lambda: True   # 隔离实机：本用例只测「游戏在前台」分支

    def timeout_method(*args, **kwargs):
        raise TimeoutError('nemu capture timeout')

    device.screenshot_methods[method_key] = timeout_method
    image = device.screenshot()
    check('主方式抛 TimeoutError 时仍拿到帧（降级成功）',
          image is not None and getattr(image, 'shape', (0,))[0] > 0,
          str(getattr(image, 'shape', None)))
    check('TimeoutError 后置位 _screenshot_force_adb',
          getattr(device, '_screenshot_force_adb', False) is True)
    # 连续失败 2 次才释放（单次失败可能只是加载/切页，不该重置 IPC）
    image = device.screenshot()
    check('连续两次 TimeoutError 后释放过 nemu IPC',
          released['n'] >= 1, str(released['n']))
    device.screenshot()
    check('释放有冷却期（60s 内不重复释放）', released['n'] == 1,
          str(released['n']))

    # 6) 客户端不在前台时的截图超时 → 必须报「游戏没跑」，而不是降级硬撑
    from module.exception import GameNotRunningError

    device._screenshot_force_adb = False
    device.app_is_running = lambda: False
    device._app_foreground_ok = lambda: False
    raised = ''
    try:
        device.screenshot()
    except GameNotRunningError:
        raised = 'GameNotRunningError'
    except Exception as error:  # noqa: BLE001
        raised = type(error).__name__
    check('前台不是游戏时抛 GameNotRunningError', raised == 'GameNotRunningError',
          raised)

    failed = [name for name, ok, _ in RESULTS if not ok]
    print()
    print(f'TOTAL {len(RESULTS)}  PASS {len(RESULTS) - len(failed)}  FAIL {len(failed)}')
    if failed:
        for name in failed:
            print(f'  FAILED: {name}')
        return 1
    print('SCREENSHOT FALLBACK: PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())

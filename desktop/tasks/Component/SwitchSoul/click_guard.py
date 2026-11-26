# This Python file uses the following encoding: utf-8
"""点击熔断（GameTooManyClickError）的公共处理工具。

## 背景

`Device.handle_control_check()`（module/device/device.py）把**每一次** click /
long_click / **swipe** 的 `control_name` 都记进同一个 `deque(maxlen=15)`，
然后在 `click_record_check()` 里判定：

* 同一个名字在最近 15 条记录里出现 **>= 10 次** → `GameTooManyClickError`
* 任意两个名字各出现 **>= 6 次** → `GameTooManyClickError`

问题在于**滑动和点击共用同一套记录**，而滑动翻页（御魂分组/队伍列表、buff 列表）
本身就是「同一个控制名连续做很多次」的合法动作。2026-11-17~20 的 18 次 B 类失败里
`ss_group_swipe_down`(5)、`buff_up`(1) 都是这样被误判成连点的。

## 两种重复的区别

* **合法重复**：翻页滑动、反复点同一个入口直到子界面出现、反复点同一个筛选项
  直到它变成目标状态。这些重复是游戏机制决定的，改动点是「不累计熔断」
  → 动作前调用 `clear_click_record(device, control)`（等价于官方
  `device.click_record_remove_rule()`，但也能直接传字符串控制名）+ 自身尝试次数上限。
* **无意义重复**：点了没反应还在点（模板不匹配、前置条件不满足、被弹窗挡住）。
  这类必须修判定或提前退出，**不许**靠清记录把它变成无限循环。

## 为什么不用 `click_record_remove(obj)`

`click_record_remove(button)` 内部是 `str(button)`，而记录里存的是
`control_name` 字符串；直接传规则对象会得到 `<... object at 0x..>`，一条也删不掉。
所以这里统一取 `control.name`（`RuleSwipe/RuleClick/RuleImage/RuleOcr` 都有 `name`）。
"""
from module.exception import GameTooManyClickError, TaskEnd
from module.logger import logger


def control_name(control) -> str:
    """取控制名：规则对象取 `.name`，字符串原样返回。"""
    name = getattr(control, 'name', None)
    return str(name if name else control)


def clear_click_record(device, control) -> int:
    """清掉某个控制名在点击记录里的历史条目，返回删除条数。

    用于「合法重复动作」：翻页滑动、反复点同一入口直到子界面出现等。
    设备层没有这个方法时（假 device / 禁用了检测）静默返回 0。
    """
    remove = getattr(device, 'click_record_remove', None)
    if not callable(remove):
        return 0
    try:
        return remove(control_name(control))
    except Exception as error:  # noqa: BLE001 - 记录清理失败不能影响任务主流程
        logger.warning(f'clear_click_record({control_name(control)}) failed: '
                       f'{type(error).__name__}: {error}')
        return 0


def clear_all_click_records(device) -> None:
    """清空整份点击记录（熔断后的恢复动作）。"""
    clear = getattr(device, 'click_record_clear', None)
    if callable(clear):
        try:
            clear()
        except Exception as error:  # noqa: BLE001
            logger.warning(f'click_record_clear failed: {type(error).__name__}: {error}')


def close_blocking_popup(owner) -> None:
    """熔断后的一次关弹窗尝试；失败只记日志，不抛异常。"""
    closer = getattr(owner, 'close_unknown_pages', None)
    if not callable(closer):
        return
    try:
        closer(skip_first_screenshot=False, allow_ai=False)
        logger.info('Close popups once after click guard triggered')
    except Exception as error:  # noqa: BLE001
        logger.warning(f'close popups after click guard failed: '
                       f'{type(error).__name__}: {error}')


def retry_once_then_soft_fail(owner, action, task_name: str):
    """连点熔断时给一次「关弹窗 + 重试」的机会，仍失败则软失败收尾。

    熔断（GameTooManyClickError）在旧逻辑里会被 `script.py` 当成需要
    app stop/start 的硬错误，几次之后整个实例进程 `exit(1)`。
    这里改成任务层自愈：

    1. 清空点击记录 + 关一次弹窗 → 重试 `action()`；
    2. 仍熔断 → `warning` + `set_next_run(success=False)` + `TaskEnd`，
       让本任务今天不再重排，也不把实例拖死（下一次调度照常）。

    `action` 返回什么就返回什么；`TaskEnd` 会向上冒泡给调度器。
    """
    try:
        return action()
    except GameTooManyClickError as error:
        logger.warning(f'[{task_name}] 连点熔断：{error}')
        logger.warning(f'[{task_name}] 先清空点击记录并关一次弹窗，然后重试一次')
        clear_all_click_records(getattr(owner, 'device', None))
        close_blocking_popup(owner)
        try:
            return action()
        except GameTooManyClickError as again:
            logger.warning(f'[{task_name}] 重试后仍然熔断：{again}')
            logger.warning(f'[{task_name}] 放弃本次运行（软失败），不重启实例')
            owner.set_next_run(task=task_name, success=False)
            raise TaskEnd(task_name) from again

# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey

from functools import wraps
import zerorpc
import zmq
import msgpack
import random
import re
import cv2
import time
import os
import inflection
import asyncio
import json

from datetime import date
import threading
from typing import Callable
from datetime import datetime, timedelta
from pathlib import Path
from cached_property import cached_property
from pydantic import BaseModel, ValidationError
from threading import Thread
from multiprocessing.queues import Queue


from module.config.utils import convert_to_underscore
from module.config.config import Config
from module.config.config_model import ConfigModel
from module.config.instance_guard import (InstanceGuard, StaggerPreempted,
                                           should_close_while_waiting,
                                           should_release_before_idle_wait)
from module.config.anti_ban import AntiBanGuard
from module.device.device import Device
from module.device.env import IS_WINDOWS
from module.base.utils import load_module
from module.base.decorator import del_cached_property
from module.atom.ocr import RuleOcr
from module.logger import logger
from module.exception import *
from module.server.i18n import I18n
from module.ocr.rpc import ensure_ocr_server_started



_log_switch_lock = threading.Lock()#线程锁


class Script:
    # 错峰豁免任务：恢复类任务不排队、不等执行权，避免游戏崩溃后卡在错峰队列里。
    # 这些任务即使同组另一个实例正在运行也直接执行（能顺手拿到执行权就拿）。
    STAGGER_EXEMPT_TASKS = {'Restart'}

    def __init__(self, config_name: str ='oas') -> None:
        logger.hr('Start', level=0)
        self.server = None
        self.state_queue: Queue = None
        self._emulator_down = False
        self.gui_update_task: Callable = None  # 回调函数, gui进程注册当每次config更新任务的时候更新gui的信息
        self.config_name = config_name
        # Skip first restart
        self.is_first_task = True
        # Failure count of tasks
        # Key: str, task name, value: int, failure count
        self.failure_record = {}
        # 运行loop的线程
        self.loop_thread: Thread = None
        # 跨进程排队管理器（仅在 queue_mode=True 时初始化）
        self.instance_guard: InstanceGuard = None
        # 当前任务是否因 Restart 抢占而暂停（不算失败）
        self._preempted = False
        self.anti_ban_guard: AntiBanGuard = AntiBanGuard()

    @cached_property
    def config(self) -> "Config":
        try:
            from module.config.config import Config
            config = Config(config_name=self.config_name)
            return config
        except RequestHumanTakeover:
            logger.critical('Request human takeover')
            exit(1)
        except Exception as e:
            logger.exception(e)
            exit(1)

    @cached_property
    def device(self) -> "Device":
        try:
            from module.device.device import Device
            device = Device(config=self.config)
            return device
        except RequestHumanTakeover:
            logger.critical('Request human takeover')
            exit(1)
        except Exception as e:
            logger.exception(e)
            exit(1)

    @cached_property
    def checker(self):
        """
        占位函数，在alas中是检查服务器是否正常的
        :return:
        """
        return None

    def save_error_log(self):
        """
        Save last 60 screenshots in ./log/error/<timestamp>
        Save logs to ./log/error/<timestamp>/log.txt
        """
        from module.base.utils import save_image
        from module.handler.sensitive_info import (handle_sensitive_image,
                                                   handle_sensitive_logs)
        if self.config.script.error.save_error:
            if not os.path.exists('./log/error'):
                os.mkdir('./log/error')
            folder_name = str(int(time.time() * 1000))
            folder = f'./log/error/{folder_name}'
            logger.warning(f'Saving error: {folder}')
            logger.info('保存详细错误的日志和截图到路径:')
            logger.info(f'{str( Path.cwd() / "log" / "error" / folder_name)}')
            os.mkdir(folder)
            for data in self.device.screenshot_deque:
                image_time = datetime.strftime(data['time'], '%Y-%m-%d_%H-%M-%S-%f')
                image = handle_sensitive_image(data['image'])
                save_image(image, f'{folder}/{image_time}.png')
            with open(logger.log_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                start = 0
                for index, line in enumerate(lines):
                    line = line.strip(' \r\t\n')
                    if re.match('^═{15,}$', line):
                        start = index
                lines = lines[start - 2:]
                lines = handle_sensitive_logs(lines)
            with open(f'{folder}/log.txt', 'w', encoding='utf-8') as f:
                f.writelines(lines)

    def init_server(self, port: int) -> int:
        """
        初始化zerorpc服务，返回端口号
        :return:
        """
        self.server = zerorpc.Server(self)
        try:
            self.server.bind(f'tcp://127.0.0.1:{port}')
            return port
        except zmq.error.ZMQError:
            logger.error(f"Ocr server cannot bind on port {port}")
            return None

    def run_server(self) -> None:
        """
        启动zerorpc服务
        :return:
        """
        self.server.run()

    def gui_args(self, task: str) -> str:
        """
        获取给gui显示的参数
        :return:
        """
        return self.config.gui_args(task=task)

    def gui_menu(self) -> str:
        """
        获取给gui显示的菜单
        :return:
        """
        return self.config.gui_menu

    def gui_task(self, task: str) -> str:
        """
        获取给gui显示的任务 的参数的具体值
        :return:
        """
        return self.config.model.gui_task(task=task)

    def gui_set_task(self, task: str, group: str, argument: str, value) -> bool:
        """
        设置给gui显示的任务 的参数的具体值
        :return:
        """
        # 验证参数
        task = convert_to_underscore(task)
        group = convert_to_underscore(group)
        argument = convert_to_underscore(argument)
        # pandtic验证
        if isinstance(value, str):
            if len(value) == 8:
                try:
                    value = datetime.strptime(value, '%H:%M:%S').time()
                except ValueError:
                    pass


        path = f'{task}.{group}.{argument}'
        task_object = getattr(self.config.model, task, None)
        group_object = getattr(task_object, group, None)
        argument_object = getattr(group_object, argument, None)

        if argument_object is None:
            logger.error(f'Set arg {task}.{group}.{argument}.{value} failed')
            return False

        try:
            setattr(group_object, argument, value)
            argument_object = getattr(group_object, argument, None)
            logger.info(f'Set arg {task}.{group}.{argument}.{argument_object}')
            self.config.save()  # 我是没有想到什么方法可以使得属性改变自动保存的
            return True
        except ValidationError as e:
            logger.error(e)
            return False

    @zerorpc.stream
    def gui_mirror_image(self):
        """
        获取给gui显示的镜像
        :return: cv2的对象将 numpy 数组转换为字节串。接下来MsgPack 进行序列化发送方将图像数据转换为字节串
        """
        # return msgpack.packb(cv2.imencode('.jpg', self.device.screenshot())[1].tobytes())
        img = cv2.cvtColor(self.device.screenshot(), cv2.COLOR_RGB2BGR)
        self.device.stuck_record_clear()
        ret, buffer = cv2.imencode('.jpg', img)
        yield buffer.tobytes()

    def _gui_update_tasks(self) -> None:
        """
        获取更新任务后 pending waiting 的任务 和 当前的任务的数据。打包给gui显示
        :return:
        """
        data = {}
        pending = []
        waiting = []
        task = {}
        if self.config.task is not None and self.config.task.next_run < datetime.now():
            task["name"] = self.config.task.command
            task["next_run"] = str(self.config.task.next_run)
        data["task"] = task

        for p in self.config.pending_task[1:]:
            item = {"name": p.command, "next_run": str(p.next_run)}
            pending.append(item)

        for w in self.config.waiting_task:
            item = {"name": w.command, "next_run": str(w.next_run)}
            waiting.append(item)


        data["pending"] = pending
        data["waiting"] = waiting

        if self.gui_update_task is not None:
            self.gui_update_task(data)

    def _gui_set_status(self, status: str) -> None:
        """
        设置给gui显示的状态
        :param status: 可以在gui中显示的状态 有 "Init", "Empty"(不显示), "Run"(运行中), "Error", "Free"(空闲)
        :return:
        """
        data = {"status": status}
        if self.gui_update_task is not None:
            self.gui_update_task(data)

    def gui_task_list(self) -> str:
        """
        获取给gui显示的任务列表
        :return:
        """
        result = {}
        for key, value in self.config.model.dict().items():
            if isinstance(value, str):
                continue
            if key == "restart":
                continue
            if "scheduler" not in value:
                continue

            scheduler = value["scheduler"]
            item = {"enable": scheduler["enable"],
                    "next_run": str(scheduler["next_run"])}
            key = self.config.model.type(key)
            result[key] = item
        return json.dumps(result)

    def _release_token_before_wait(func):
        @wraps(func)
        def wrapper(self, future):
            if self.instance_guard and self.instance_guard.should_release(
                pending_task=self.config.pending_task,
                waiting_task=self.config.waiting_task,
                idle_threshold_minutes=self.config.script.optimization.queue_idle_threshold
            ):
                self.instance_guard.release()
            return func(self, future)
        return wrapper

    @_release_token_before_wait
    def wait_until(self, future):
        """
        Wait until a specific time.

        Args:
            future (datetime):

        Returns:
            bool: True if wait finished, False if config changed.
        """
        future = future + timedelta(seconds=1)
        self.config.start_watching()
        while 1:
            self._repair_gate('idle',wake=future.timestamp())
            if datetime.now() > future:
                self._repair_gate('scheduling',wake=0)
                return True
            # if self.stop_event is not None:
            #     if self.stop_event.is_set():
            #         logger.info("Update event detected")
            #         logger.info(f"[{self.config_name}] exited. Reason: Update")
            #         exit(0)

            time.sleep(5)

            if self.config.should_reload():
                return False

    def get_next_task(self) -> str:
        """
        获取下一个任务的名字, 大驼峰。
        :return:
        """
        while True:
            self._repair_gate('scheduling',wake=0)
            task = self.config.get_next()
            self.config.task = task
            if self.state_queue:
                self.state_queue.put({"schedule": self.config.get_schedule_data()})
            now = datetime.now()
            antiban_wake = self.anti_ban_guard.wake_time(now, self.config.script.anti_ban)
            if antiban_wake is not None:
                task.next_run = max(task.next_run, antiban_wake)
            # 恢复类任务（Restart）：只负责启动/重启游戏，不抢占、不排队。
            # 能顺手拿到执行权就拿（避免和对方同时抢 CPU），拿不到也照常执行：
            # 抢占会把对方正在跑的任务打断并重排，连续几次就变成"重启循环"。
            if task.command in self.STAGGER_EXEMPT_TASKS:
                if self._ensure_instance_guard():
                    self.instance_guard.try_acquire(queue=False, force=False)
            elif not self._try_acquire_queue_token():
                del_cached_property(self, "config")
                continue
            # 任务时间到了返回任务名称
            if task.next_run <= now:
                return task.command
            # 错峰是「任务级互斥」：还没到点就不该占着执行权。
            # 否则持有者在等自己的下一个任务时，同组另一个实例一直抢不到，
            # 它会等满 90s 关掉模拟器，拿回令牌后又得 Restart 登录，形成循环。
            if should_release_before_idle_wait(
                    stagger_on=self.config.script.optimization.stagger_enable,
                    has_guard=self.instance_guard is not None,
                    task_due=False):
                self.instance_guard.release()
            # 根据策略执行等待逻辑
            if not self._handle_wait_during_idle(task.next_run):
                # 若等待被打断, 则刷新配置
                del_cached_property(self, "config")

    def _ensure_instance_guard(self) -> bool:
        """按需创建跨实例执行权守卫（排队模式或错峰模式开启时）。

        Returns:
            True: 守卫可用（调用方需要/可以获取执行权）。
            False: 两种模式都没开，或创建失败。
        """
        optimization = self.config.script.optimization
        if not optimization.queue_mode and not optimization.stagger_enable:
            if self.instance_guard:
                self.instance_guard.remove_from_queue()
                self.instance_guard = None
            return False

        if self.instance_guard is None:
            group = []
            if optimization.stagger_enable and optimization.stagger_group:
                raw = (optimization.stagger_group.replace('；', ',').replace('，', ',')
                       .replace(';', ',').replace(' ', ','))
                group = [name for name in raw.split(',') if name]
            try:
                self.instance_guard = InstanceGuard(
                    self.config_name,
                    stagger_group=group,
                    gap_minutes=optimization.stagger_gap_minutes if optimization.stagger_enable else 0,
                )
                logger.info(
                    f"[Queue] Guard enabled for '{self.config_name}', "
                    f"queue_mode={optimization.queue_mode}, "
                    f"stagger={group}, gap={optimization.stagger_gap_minutes if optimization.stagger_enable else 0}min")
            except Exception:
                self.instance_guard = None
                return False
        return self.instance_guard is not None

    def _try_acquire_queue_token(self) -> bool:
        """
        尝试获取排队执行权。
        如果排队模式/错峰模式都未启用，返回 True。
        否则阻塞等待执行权，直到获取成功或配置变更。

        Returns:
            True: 获取得执行权，可以执行任务
            False: 等待被配置变更打断，调用方应重新加载配置后重试
        """
        if not self._ensure_instance_guard():
            return True

        # 尝试获取执行权
        if self.instance_guard.try_acquire():
            return True

        # 执行权获取失败，进入等待循环
        logger.info(f"[Queue] '{self.config_name}' waiting for execution token...")
        self.config.start_watching()
        # 错峰模式下对方一跑完就要尽快接手，用更短的轮询间隔
        stagger_on = self.config.script.optimization.stagger_enable
        poll_interval = 5 if stagger_on else 30
        close_policy = self.config.script.optimization.when_task_queue_empty
        # 空闲宽限：错峰交接常常是秒级，先等一会儿再关模拟器；
        # 真关掉也没关系，跑任务前会检查并按需走 Restart 重新拉起。
        close_after = 90 if stagger_on else 30
        wait_start = time.time()
        emulator_closed = False
        while True:
            time.sleep(poll_interval)

            if self.config.should_reload():
                logger.info(f"[Queue] Config changed, re-evaluating")
                return False

            if self.instance_guard.try_acquire():
                return True

            # 等了一段时间还轮不到自己，说明对方任务不短：关机省资源。
            # 注意：错峰模式不关（否则会「等待→关机→拿到→冷启动+Restart→再等待→再关机」死循环）。
            if not emulator_closed and should_close_while_waiting(
                    stagger_on=stagger_on,
                    close_policy=close_policy,
                    emulator_down=self._emulator_down,
                    device_ready='device' in self.__dict__,
                    waited=time.time() - wait_start,
                    close_after=close_after):
                emulator_closed = True
                try:
                    self.device.emulator_stop()
                    self._emulator_down = True
                    logger.info("[Queue] Idle while waiting token, emulator closed to save resources")
                except Exception as e:
                    logger.warning(f"[Queue] Close emulator failed: {e}")

    def _handle_wait_during_idle(self, next_run: datetime) -> bool:
        """
        处理任务空闲期间的行为策略
        :param next_run: 下一个任务的时间
        :return: True 表示等待成功完成, False 表示等待被中断
        """
        method = self.config.script.optimization.when_task_queue_empty
        strategy_map = {
            "close_game": self._wait_close_game,
            "goto_main": self._wait_goto_main,
        }
        func = strategy_map.get(method)
        if func is None:
            logger.warning(f"Invalid Optimization_WhenTaskQueueEmpty: {method}, fallback to stay_there")
            func = self._wait_stay_there
        return func(next_run)

    @staticmethod
    def _time_to_timedelta(value) -> timedelta:
        if value is None:
            return timedelta(0)
        return timedelta(hours=value.hour, minutes=value.minute, seconds=value.second)

    def _wait_until_with_emulator_preheat(self, next_run: datetime) -> bool:
        """Wait until next_run; if emulator is down, preheat startup before next task."""
        if not self._emulator_down:
            return self.wait_until(next_run)

        startup_lead = self._time_to_timedelta(self.config.script.optimization.emulator_startup_lead_time)
        now = datetime.now()
        wake_time = next_run - startup_lead if startup_lead > timedelta(0) else next_run
        if wake_time < now:
            wake_time = now

        now = datetime.now()
        if wake_time > now:
            logger.info(f"Wait before wake emulator: {wake_time.strftime('%Y-%m-%d %H:%M:%S')}")
            if not self.wait_until(wake_time):
                return False

        if self._emulator_down:
            logger.info("Wake emulator before next task")
            if not self._try_acquire_queue_token():
                return False
            self.device = Device(self.config)
            self._emulator_down = False

        if wake_time < next_run:
            return self.wait_until(next_run)
        return True

    def _stop_emulator_for_idle(self):
        """An idle worker must not construct Device just to stop an emulator.

        Device auto-starts a disconnected emulator during construction. Use the
        platform's existing stop operation until a device is actually needed.
        """
        desktop = getattr(getattr(self.config.script, 'desktop', None), 'enable', False)
        if 'device' in self.__dict__ or not IS_WINDOWS or desktop:
            return self.device.emulator_stop()
        from module.device.platform2 import Platform
        platform = Platform()
        platform.config = self.config
        platform.serial = self.config.script.device.serial
        if not platform._emulator_function_wrapper(platform._emulator_stop):
            raise RequestHumanTakeover('Cannot stop idle emulator without starting it')
        return True

    def _wait_close_game(self, next_run: datetime) -> bool:
        if self._emulator_down:
            logger.info("Emulator is down, skip close_game/goto_main action and wait with preheat")
            return self._wait_until_with_emulator_preheat(next_run)

        close_game_wait_duration = self.config.script.optimization.close_game_wait_duration
        close_game_wait = self._time_to_timedelta(close_game_wait_duration)
        close_emulator_wait_duration = self.config.script.optimization.close_emulator_wait_duration
        close_emulator_wait = self._time_to_timedelta(close_emulator_wait_duration)

        # 错峰执行空闲时优先关模拟器：交接期间留着白占内存/CPU，
        # 下一轮任务开始前会先检查，没开就自动走 Restart 拉起来。
        # 但离下个任务不到 3 分钟时，关机再开反而更慢，保持开着。
        stagger_on = self.config.script.optimization.stagger_enable
        # 错峰：只有空闲足够久（≥ close_emulator_wait，默认 25/30 分钟）才值得关机，
        # 否则短任务之间反复「关机 → Restart → 关机」，任务永远在重启里打转。
        stagger_idle_gate = (close_emulator_wait
                             if close_emulator_wait > timedelta(0)
                             else timedelta(minutes=3))
        min_idle_saved = next_run - datetime.now() >= stagger_idle_gate
        close_emulator_now = stagger_on and min_idle_saved
        if close_emulator_now or (close_emulator_wait > timedelta(0)
                                  and next_run > datetime.now() + close_emulator_wait):
            logger.info("Close emulator during wait"
                        + (" (stagger idle)" if close_emulator_now else ""))
            self._stop_emulator_for_idle()
            self._emulator_down = True

            if not self._wait_until_with_emulator_preheat(next_run):
                return False

            self.run("Restart")
            return True

        if close_game_wait <= timedelta(0):
            logger.info("Close game during wait")
            self.device.app_stop()
            self.device.release_during_wait()
            if not self.wait_until(next_run):
                return False
            self.run("Restart")
            return True

        if next_run > datetime.now() + close_game_wait:
            logger.info("Close game during wait")
            self.device.app_stop()
            self.device.release_during_wait()
            if not self.wait_until(next_run):
                return False
            self.run("Restart")
            return True

        logger.info("Wait without closing game (close_game wait duration not reached)")
        self.device.release_during_wait()
        if not self.wait_until(next_run):
            return False
        return True

    def _wait_goto_main(self, next_run: datetime) -> bool:
        if self._emulator_down:
            logger.info("Emulator is down, skip goto_main and wait with preheat")
            return self._wait_until_with_emulator_preheat(next_run)

        close_emulator_wait_duration = self.config.script.optimization.close_emulator_wait_duration
        close_emulator_wait = self._time_to_timedelta(close_emulator_wait_duration)
        if close_emulator_wait > timedelta(0) and next_run > datetime.now() + close_emulator_wait:
            logger.info("Close emulator during wait")
            self._stop_emulator_for_idle()
            self._emulator_down = True

            if not self._wait_until_with_emulator_preheat(next_run):
                return False

            self.run("Restart")
            return True

        logger.info("Goto main page during wait")
        self.run("GotoMain")
        self.device.release_during_wait()
        return self.wait_until(next_run)

    def _wait_stay_there(self, next_run: datetime) -> bool:
        if self._emulator_down:
            logger.info("Stay_there during wait (emulator is down, with preheat)")
            return self._wait_until_with_emulator_preheat(next_run)

        logger.info("Stay_there (no action) during wait")
        self.device.release_during_wait()
        return self.wait_until(next_run)

    def _roster_scan_due(self) -> bool:
        """每周扫描是否到点（按配置的周几 + 时间，且距上次扫描满 7 天）。"""
        cfg = getattr(self.config.script, 'roster_scan', None)
        if cfg is None or not getattr(cfg, 'enable', False):
            return False
        if self.config.model.running_task:
            return False
        now = datetime.now()
        try:
            weekday = int(getattr(cfg.weekday, 'value', cfg.weekday))
            t = cfg.time
            hh, mm = int(t.hour), int(t.minute)
        except Exception:
            return False
        delta = (now.isoweekday() - weekday) % 7
        occurrence = datetime.combine((now - timedelta(days=delta)).date(),
                                      datetime.min.time()).replace(hour=hh, minute=mm)
        if occurrence > now:
            occurrence -= timedelta(days=7)
        last_raw = str(getattr(cfg, 'last_scan', '') or '')
        if not last_raw:
            return True
        try:
            last = datetime.fromisoformat(last_raw)
        except ValueError:
            return True
        return last < occurrence

    def _run_roster_scan(self) -> None:
        """执行一次式神池更新：默认截图 OCR；source=paz-db 时直接读平安志库。"""
        self._repair_gate('running',wake=0)
        cfg = self.config.script.roster_scan
        logger.hr('Roster scan', level=1)
        source = str(getattr(cfg.source, 'value', cfg.source) or 'scan')
        if source == 'paz-db':
            from module.lineup import roster_paz
            logger.info(f'[RosterScan] source=paz-db, rarities={cfg.rarities}')
            result = roster_paz.build_and_save(cfg)
            if not result.get('ok'):
                logger.warning(f"[RosterScan] paz-db 不可用：{result.get('reason')}；本次跳过（不改 roster）")
                return
            cfg.last_scan = datetime.now().isoformat(timespec='seconds')
            logger.info(f"[RosterScan] done(paz-db): {result['count']} shikigami -> {result['path']} "
                        f"(unknown={result.get('unknown_catalog')}, "
                        f"skipped_by_rarity={result.get('skipped_by_rarity')})")
            return
        from module.lineup import scan as roster_scan
        logger.info(f'[RosterScan] start: rarities={cfg.rarities}, pages={cfg.pages_per_rarity}')
        result = roster_scan.run_scan(self.device, cfg)
        cfg.last_scan = datetime.now().isoformat(timespec='seconds')
        logger.info(f"[RosterScan] done: {result['count']} shikigami -> {result['path']}")

    def _bind_auto_lineup_preset(self, command: str, preset_group: int, preset_team: int,
                                 lineup_name: str, auto_cfg) -> None:
        """把自动阵容写入的第十组预设绑定到当前任务配置并解除旧锁阵，确保实战真正换人上场。"""
        from module.config.utils import convert_to_underscore
        from module.lineup import apply as lineup_apply

        conf = getattr(self.config.model, convert_to_underscore(command), None)
        if conf is None:
            return
        if bool(getattr(auto_cfg, 'ignore_task_battle_config', True)):
            lineup_apply._disable_task_overrides(conf)
        gb = lineup_apply._general_battle(conf)
        if gb is not None:
            if getattr(gb, 'lock_team_enable', False):
                gb.lock_team_enable = False
            gb.preset_enable = True
            gb.preset_group = int(preset_group)
            gb.preset_team = int(preset_team)
            logger.info(
                f'[AutoLineup] `{command}` 绑定第 {preset_group} 组第 {preset_team} 队预设 ({lineup_name})'
            )
            try:
                self.config.save()
            except Exception as save_error:  # noqa: BLE001
                logger.warning(f'[AutoLineup] save config failed: {save_error}')

    def _is_lineup_eligible_task(self, command: str, auto_cfg, target_group: int) -> tuple[bool, str]:
        """判断当前任务是否真正需要自动阵容（过滤百鬼棋局/重启/日常杂项等非配阵任务，并尊重 overwrite_manual）。"""
        from module.config.utils import convert_to_underscore
        from module.lineup import apply as lineup_apply

        task_key = convert_to_underscore(command)
        # 明确不需要式神阵容/御魂的非配阵玩法与工具任务
        non_lineup_tasks = {
            'restart', 'goto_main', 'chess', 'daily_trivial', 'weekly_purchase',
            'rich_man', 'souls_tidy', 'courtyard_affairs', 'kekkai_utilize',
            'kekkai_activation', 'pets', 'talisman_pass', 'summon', 'wish',
            'mystery_shop', 'memory_scrolls', 'wanted_quests', 'guild_banquet',
            'delegation', 'nura_rift', 'sub_account', 'roster_scan',
        }
        if task_key in non_lineup_tasks:
            return False, f'非配阵任务({command})'

        conf = getattr(self.config.model, task_key, None)
        if conf is None:
            return False, f'无任务配置({command})'

        gb = lineup_apply._general_battle(conf)
        soul = lineup_apply._switch_soul(conf)
        if gb is None and soul is None:
            return False, f'任务无战斗预设/御魂配置({command})'

        # 未开启「覆盖手动配置」时，保留用户已手动配置好的预设/换魂任务
        overwrite_manual = bool(getattr(auto_cfg, 'overwrite_manual', False))
        if not overwrite_manual:
            has_manual_preset = (
                gb is not None
                and bool(getattr(gb, 'preset_enable', False))
                and int(getattr(gb, 'preset_group', 0) or 0) != int(target_group)
            )
            has_manual_soul = (
                soul is not None
                and (
                    bool(getattr(soul, 'enable', False))
                    or bool(getattr(soul, 'enable_switch_by_name', False))
                    or bool(getattr(soul, 'auto_switch_soul', False))
                )
            )
            if has_manual_preset or has_manual_soul:
                return False, f'已有手动预设/换魂配置且未开启覆盖({command})'

        return True, ''

    def _run_lineup_assistant(self, command: str, auto_cfg, mode: str = 'official') -> bool:
        """驱动游戏内「阵容助手」：支持 official 官方试算 与 ai 极速定制建阵，统一落第 10 组预设。"""
        from module.lineup.planner import plan_lineup
        from module.lineup.spec import (
            DEFAULT_PRESET_GROUP,
            DEFAULT_TEAM_GROUP,
            LineupCache,
            dungeon_key_of,
            resolve_task_dungeon,
        )
        from tasks.Component.LineupAssistant.assistant import LineupAssistantMixin
        from tasks.GameUi.game_ui import GameUi

        class _AssistantRunner(LineupAssistantMixin, GameUi):
            pass

        preset_group = int(getattr(auto_cfg, 'default_preset_group', DEFAULT_PRESET_GROUP) or DEFAULT_PRESET_GROUP)
        eligible, skip_reason = self._is_lineup_eligible_task(command, auto_cfg, preset_group)
        if not eligible:
            logger.info(f'[AutoLineup] 跳过 `{command}`: {skip_reason}')
            return False

        dungeon_override = str(getattr(auto_cfg, 'assistant_dungeon', '') or '')
        dungeon_tuple = resolve_task_dungeon(command, config_model=self.config.model, override=dungeon_override)
        dungeon_key = dungeon_key_of(dungeon_tuple)
        team_group = str(getattr(auto_cfg, 'team_group', DEFAULT_TEAM_GROUP) or DEFAULT_TEAM_GROUP)
        save_to_preset = bool(getattr(auto_cfg, 'save_to_preset', True))
        cache_hours = float(getattr(auto_cfg, 'cache_hours', 24) or 0)
        cfg_name = str(getattr(self, 'config_name', '') or 'default')

        cache = LineupCache()
        # 快路径：若同副本在缓存有效期内已落第 10 组预设，直接秒切预设开打（~2s）
        cached = cache.get(cfg_name, command, dungeon_key, cache_hours=cache_hours)
        if cached and save_to_preset:
            c_group = int(cached.get('preset_group') or preset_group)
            c_team = int(cached.get('preset_team') or 1)
            logger.info(
                f'[AutoLineup] `{command}` 命中缓存预设（第 {c_group} 组第 {c_team} 队: '
                f'{cached.get("lineup_name")}），免重算直接上阵'
            )
            self._bind_auto_lineup_preset(command, c_group, c_team, cached.get('lineup_name', ''), auto_cfg)
            return True

        preset_team = cache.allocate_team_slot(
            cfg_name, command, dungeon_key,
            preset_group=preset_group,
            default_team=int(getattr(auto_cfg, 'default_preset_team', 1) or 1),
        )
        runner = _AssistantRunner(config=self.config, device=self.device)

        try:
            if mode == 'ai':
                planned = plan_lineup(
                    task_name=command,
                    dungeon=dungeon_override,
                    mode='hybrid',
                    preset_group=preset_group,
                    preset_team=preset_team,
                    config_model=self.config.model,
                    roster_file=getattr(auto_cfg, 'roster_file', None),
                )
                if planned.get('ok') and planned.get('spec') is not None:
                    spec = planned['spec']
                    logger.info(f'[AutoLineup] `{command}` {planned.get("reason")}')
                    res = runner.apply_or_build_spec(
                        spec=spec,
                        dungeon_tuple=dungeon_tuple,
                        reuse_existing=True,
                        save_to_preset=save_to_preset,
                        team_group=team_group,
                        preset_group=preset_group,
                        preset_team=preset_team,
                    )
                    if res.get('ok'):
                        if save_to_preset:
                            cache.put(cfg_name, command, dungeon_key, spec)
                            self._bind_auto_lineup_preset(command, preset_group, preset_team, spec.name, auto_cfg)
                        logger.info(
                            f'[AutoLineup] `{command}` AI阵容已落地实战 '
                            f'(reused={res.get("reused")}, {res.get("elapsed_s")}s)'
                        )
                        return True
                    logger.warning(f'[AutoLineup] AI建阵落地失败({res.get("reason")})，降级官方模式')

            # 官方模式（或 AI 失败后的自动降级）
            ok = runner.apply_official_best(
                task_name=command,
                dungeon=dungeon_key or dungeon_override or None,
                save_to_preset=save_to_preset,
                team_group=team_group,
                preset_group=preset_group,
                preset_team=preset_team,
            )
            if ok and save_to_preset:
                self._bind_auto_lineup_preset(command, preset_group, preset_team, f'官方-{dungeon_key or command}', auto_cfg)
            logger.info(f'[AutoLineup] `{command}` 官方阵容助手: {"已应用" if ok else "未应用"}')
            return ok
        except Exception as error:  # noqa: BLE001 - 失败不影响任务继续跑
            logger.warning(f'[AutoLineup] 阵容助手执行失败: {error}')
            return False

    def _apply_auto_lineup(self, command: str) -> None:
        """战斗任务开始前，按全局「自动阵容」设置套用阵容。"""
        try:
            auto_cfg = self.config.script.auto_lineup
        except AttributeError:
            return
        if not auto_cfg.enable:
            return

        mode = getattr(getattr(auto_cfg, 'mode', None), 'value', getattr(auto_cfg, 'mode', 'official'))
        if mode in ('official', 'ai'):
            self._run_lineup_assistant(command, auto_cfg, mode=mode)
            return

        try:
            from module.lineup import apply as lineup_apply
            from module.lineup import data as lineup_data
            lineup_data.ensure_default_file(auto_cfg.data_file)
            result = lineup_apply.apply_for_task(
                self.config.model, command,
                apply_mode=getattr(auto_cfg.apply_mode, 'value', auto_cfg.apply_mode),
            )
            if result.get('applied'):
                logger.info(
                    f"[AutoLineup] `{command}` -> {result.get('lineup')} "
                    f"({', '.join(result.get('fields', []))})")
            else:
                logger.info(f"[AutoLineup] `{command}` skipped: {result.get('reason')}")
            disabled = result.get('disabled') or []
            if disabled:
                logger.info(
                    f"[AutoLineup] `{command}` ignored task config: "
                    f"{', '.join(disabled)}")
            if result.get('applied') or disabled:
                # 立即落盘：这些字段是嵌套模型赋值，不会触发 ConfigModel 的
                # 自动保存；不保存的话下一次配置重载就会把改动丢掉。
                try:
                    self.config.save()
                except Exception as save_error:
                    logger.warning(f'[AutoLineup] save config failed: {save_error}')
        except Exception as e:
            logger.warning(f'[AutoLineup] failed for `{command}`: {e}')

    def exception_handler(self, e: Exception, command: str) -> None:
        self._ai_task_outcome(False)
        # 处理御魂溢出
        from tasks.Utils.post_diagnotor import PostDiagnotor, AnalyzeType
        image = getattr(self.device, 'image', None)
        # image为None则不做处理
        if image is None:
            return
        analyse_type = PostDiagnotor().handle(e=e, command=command, image=image)
        if analyse_type == AnalyzeType.SoulOverflow:
            self.config.task_call('SoulsTidy')
            time.sleep(1)

    # 「退出游戏？取消/确认」这类系统弹窗会整屏拦截点击：导航点不动底栏，
    # 表现为 "Transition failed before leaving page_main" 反复重试直到 GameTooManyClickError。
    # 2026-10-28 实测：登录兜底误按返回键会弹出它，于是组队入口永远点不到。
    _DIALOG_OCR = RuleOcr(roi=(0, 0, 1280, 720), area=(0, 0, 1280, 720),
                          mode='Full', method='Default', keyword='', name='sys_dialog')

    def _dismiss_system_dialog(self) -> bool:
        """出现「退出游戏/是否再次打开」类弹窗时点掉它（优先取消）。返回是否处理过。"""
        try:
            self.device.screenshot()
            items = self._DIALOG_OCR.detect_and_ocr(self.device.image) or []
        except Exception:
            return False
        boxes = {}
        for item in items:
            name = str(getattr(item, 'ocr_text', '') or '').strip()
            box = getattr(item, 'box', None)
            if name and box is not None:
                boxes.setdefault(name, box)
        joined = ' '.join(boxes)
        if '退出游戏' not in joined and '是否再次打开' not in joined:
            return False
        # 注意：box 是 numpy 数组，不能直接 `a or b`（bool(array) 会抛 ValueError）
        target = boxes.get('取消')
        if target is None:
            target = boxes.get('确认')
        if target is None:
            return False
        x = (int(target[0][0]) + int(target[2][0])) // 2
        y = (int(target[0][1]) + int(target[2][1])) // 2
        logger.warning(f'关闭系统弹窗 ({x},{y})')
        self.device.click(x, y, control_check=False)
        time.sleep(1.0)
        return True

    def _repair_gate(self, phase, *, wake=0):
        reporter = getattr(self, '_repair_runtime', None)
        if reporter is not None:
            try:
                reporter.gate(phase,wake=wake)
            except Exception as error:
                logger.warning(f'[AI维修] 运行状态记录失败：{type(error).__name__}')

    def _ai_task_outcome(self, success: bool) -> None:
        callback = getattr(getattr(self, '_active_ai_task', None), 'ai_record_task_outcome', None)
        if callable(callback):
            try:
                callback(success)
            except Exception as error:
                logger.warning(f'[AI学习] 任务结果归档失败：{type(error).__name__}')

    def _ai_defer_failed_task(self, task, error: Exception, command: str) -> bool:
        if task is None:
            # Import/constructor failures also need a source repair job.
            try:
                from module.ai_recovery.repair_runtime import task_feedback
                task_feedback(self.config_name,command,False)
            except Exception as feedback_error:
                logger.warning(f'[AI维修] 导入失败反馈无法归档：{type(feedback_error).__name__}')
            from module.ai_recovery.integration import AiRecoveryMixin
            task = AiRecoveryMixin()
            task.config = self.config
            task.device = self.__dict__.get('device')
            task.get_task_name = lambda: command
        callback = getattr(task, 'ai_handle_task_error', None)
        if not callable(callback):
            return False
        try:
            decision = callback(error)
            if not decision:
                return False
            seconds = max(60.0, float(decision['retry_after_s']))
            scheduler = getattr(getattr(self.config.model, convert_to_underscore(command), None),
                                'scheduler', None)
            if scheduler is None:
                return False
            interval = getattr(scheduler, 'failure_interval', None)
            if isinstance(interval, timedelta):
                seconds = max(seconds, interval.total_seconds())
            # task_delay selects the earliest supplied deadline. Supplying both
            # success=False and target would bypass a longer learned backoff.
            self.config.task_delay(task=command, server=False,
                                   target=datetime.now() + timedelta(seconds=seconds))
            logger.warning(f'[AI学习] {command} 已延期 {int(seconds)} 秒：{decision["message"]}；继续其他任务')
            return True
        except Exception as recovery_error:
            logger.warning(f'[AI学习] 错误接管失败，保留原处理：{type(recovery_error).__name__}')
            return False

    def run(self, command: str) -> bool:
        """
        :param command:  大写驼峰命名的任务名字
        :return:
        """
        self._repair_gate('running',wake=0)
        if command == 'start' or command == 'goto_main':
            logger.error(f'Invalid command `{command}`')

        # 恢复类任务（Restart）：只启动/重启游戏，不抢占对方的执行权。
        # 能拿到就拿，拿不到照常跑（它只操作自己的模拟器，不会碰到对方实例）。
        if command in self.STAGGER_EXEMPT_TASKS:
            if self._ensure_instance_guard():
                self.instance_guard.try_acquire(queue=False, force=False)
        elif not self._try_acquire_queue_token():
            return False

        if self.instance_guard and self.instance_guard.token_lost:
            logger.warning(f'Token lost, stopping emulator and rejoining queue')
            try:
                self.device.emulator_stop()
            except Exception:
                pass
            self._emulator_down = True
            self.instance_guard.release()
            return False

        # 弹窗（退出游戏/加成提示）会挡住所有点击，先清一次
        self._dismiss_system_dialog()

        # 自动阵容：战斗任务开始前挑并套用最合适的预设队伍 / QuickLoadout
        self._apply_auto_lineup(command)

        task_instance = None
        self._active_ai_task = None
        try:
            self.device.screenshot()
            module_name = 'script_task'
            module_path = str(Path.cwd() / 'tasks' / command / (module_name+'.py'))
            logger.info(f'module_path: {module_path}, module_name: {module_name}')
            task_module = load_module(module_name, module_path)
            task_instance = task_module.ScriptTask(config=self.config, device=self.device)
            self._active_ai_task = task_instance
            from module.task_calendar.runtime import track_task
            with track_task(self.config_name, command):
                task_instance.run()
            self._ai_task_outcome(True)
        except StaggerPreempted:
            # 执行权被 Restart 抢占：暂停当前任务，等 Restart 释放后重新排队
            logger.warning(
                f'[{self.config_name}] task `{command}` paused: execution token '
                f'preempted by Restart')
            self._preempted = True
            if self.instance_guard:
                self.instance_guard.release()
            return False
        except TaskEnd:
            self._ai_task_outcome(True)
            return True
        except GameNotRunningError as e:
            logger.warning(e)
            self.exception_handler(e=e, command=command)
            self.config.task_call('Restart')
            return True
        except (GameStuckError, GameTooManyClickError) as e:
            logger.error(e)
            self.save_error_log()
            self.exception_handler(e=e, command=command)
            logger.warning(f'Game stuck, {self.device.package} will be restarted in 10 seconds')
            logger.warning('If you are playing by hand, please stop Alas')
            self.config.notifier.push(title=f'{I18n.trans_zh_cn(command)}{command}', content=f"<{self.config_name}> GameStuckError or GameTooManyClickError")
            self.config.task_call('Restart')
            self.device.sleep(10)
            return False
        except GameBugError as e:
            logger.warning(e)
            self.save_error_log()
            self.exception_handler(e=e, command=command)
            logger.warning('An error has occurred in Azur Lane game client, Alas is unable to handle')
            logger.warning(f'Restarting {self.device.package} to fix it')
            self.config.task_call('Restart')
            self.device.sleep(10)
            return False
        except GamePageUnknownError as e:
            logger.info('Game server may be under maintenance or network may be broken, check server status now')
            # 这个还不重要 留着坑填
            logger.critical('Game page unknown')
            self.save_error_log()
            self.exception_handler(e=e, command=command)
            self.config.notifier.push(title=f'{I18n.trans_zh_cn(command)}{command}', content=f"<{self.config_name}> GamePageUnknownError")
            self.config.task_call('Restart')
            self.device.sleep(10)
            return False
        except ScriptError as e:
            logger.critical(e)
            self.exception_handler(e=e, command=command)
            self.save_error_log()
            if self._ai_defer_failed_task(task_instance, e, command):
                return True
            logger.critical('This is likely to be a mistake of developers, but sometimes just random issues')
            self.config.notifier.push(title=f'{I18n.trans_zh_cn(command)}{command}', content=f"<{self.config_name}> ScriptError")
            exit(1)
        except RequestHumanTakeover as e:
            logger.critical(e)
            self.exception_handler(e=e, command=command)
            logger.critical('Request human takeover')
            self.config.notifier.push(title=f'{I18n.trans_zh_cn(command)}{command}', content=f"<{self.config_name}> RequestHumanTakeover")
            exit(1)
        except Exception as e:
            logger.exception(e)
            self.exception_handler(e=e, command=command)
            self.save_error_log()
            if self._ai_defer_failed_task(task_instance, e, command):
                return True
            self.config.notifier.push(title=f'{I18n.trans_zh_cn(command)}{command}', content=f"<{self.config_name}> Exception occured")
            exit(1)

    def loop(self):
        """
        Main loop of scheduler.
        :return:
        """
        with _log_switch_lock:
            logger.set_file_logger(self.config_name, do_cleanup=True)
        start_day = date.today()
        logger.info(f'Start scheduler loop: {self.config_name}')
        try:
            from module.ai_recovery.config import load_config
            ai_cfg = load_config(config_name=self.config_name)
            if ai_cfg.enabled and ai_cfg.learning.enabled:
                logger.info('[AI学习] 累计学习已加载：持久记忆、失败方案停用、任务完成反馈、重复错误退避')
            if ai_cfg.enabled and ai_cfg.auto and ai_cfg.code_repair.enabled:
                from module.ai_recovery.repair_runtime import RuntimeReporter, ensure_worker
                self._repair_runtime = RuntimeReporter(self.config_name)
                self._repair_runtime.write('starting')
                ensure_worker(self.config_name)
                logger.info('[AI维修] 代码维修已启动：源码定位、隔离补丁、行为回归、安全重载、失败回滚')
        except Exception as error:
            logger.warning(f'[AI学习] 启动检查失败：{type(error).__name__}')
        self.config.model.running_task = ''
        self.anti_ban_guard.reset()

        # Update GUI 防呆, 读取设置并立刻显示后台模拟器到前台
        if not self.config.script.device.run_background_only and IS_WINDOWS:
            from module.device.platform2.platform_windows import minimize_by_name, show_window_by_name
            target_window_name = self.config.script.device.handle  # 在这里输入你的具体窗口名称
            if self.config.script.device.emulator_window_minimize:
                minimize_by_name(target_window_name)
                logger.info(f'重新显示: {target_window_name}')
            else:
                show_window_by_name(target_window_name)
                
        while 1:
            if date.today() > start_day:
                with _log_switch_lock:
                    logger.set_file_logger(self.config_name, do_cleanup=True)
                start_day = date.today()
            # 每周式神池扫描（全局配置；默认开启，可自定义周几/几点）
            try:
                if self._roster_scan_due():
                    self._run_roster_scan()
            except Exception as e:
                logger.warning(f'[RosterScan] skip this round: {e}')
            # Check update event from GUI
            # if self.stop_event is not None:
            #     if self.stop_event.is_set():
            #         logger.info("Update event detected")
            #         logger.info(f"Alas [{self.config_name}] exited.")
            #         break

            # Check game server maintenance
            # self.checker.wait_until_available()
            # if self.checker.is_recovered():
            #     # There is an accidental bug hard to reproduce
            #     # Sometimes, config won't be updated due to blocking
            #     # even though it has been changed
            #     # So update it once recovered
            #     del_cached_property(self, 'config')
            #     logger.info('Server or network is recovered. Restart game client')
            #     self.config.task_call('Restart')

            # Get task
            task = self.get_next_task()
            # Skip first restart
            if self.is_first_task and task == 'Restart':
                logger.info('Skip task `Restart` at scheduler start')
                self.config.task_delay(task='Restart', success=True, server=True)
                del_cached_property(self, 'config')
                continue

            if self._emulator_down:
                self.device = Device(self.config)
                self._emulator_down = False
            else:
                _ = self.device

            # 模拟器/游戏兜底检查：错峰空闲时可能已经把模拟器关了，
            # 直接跑任务会一路 GameNotRunningError。这里先确认模拟器和游戏都在线，
            # 不在线就先把这一轮改写成 Restart（它会拉起模拟器并完成登录）。
            if task != 'Restart' and not getattr(self.device, 'is_desktop', False):
                game_running = True
                try:
                    game_running = self.device.app_is_running()
                except Exception as e:
                    logger.warning(f'Check game running failed: {e}')
                    game_running = False
                if not game_running:
                    last = getattr(self, '_prestart_restart', None)
                    cooled = (last is not None and last[0] == task
                              and (datetime.now() - last[1]).total_seconds() < 600)
                    if cooled:
                        logger.warning(
                            f'Game still not running after Restart, run `{task}` anyway')
                    else:
                        logger.info(
                            'Emulator/game not running, run `Restart` before task')
                        self._prestart_restart = (task, datetime.now())
                        task = 'Restart'
                        self.is_first_task = False

            # 桌面模式：客户端刚被自动拉起时停在登录页（_desktop_login_done=False），
            # 此时 app_is_running() 返回 False。若下一个任务是 gameplay，先强制走 Restart
            # 登录流程把游戏带到庭院主界面，避免 gameplay 在登录页盲点后被迫重启。
            # 注意：不能只靠 task_call('Restart') 排队，因为 get_next_task 按 next_run 取最早
            # 到期的任务，Exploration 等的 next_run 是 2023 永远更优先，Restart 永远排不上去，
            # 所以这里直接把 task 改写成 Restart 并落进下方 Run 分支执行。
            if task != 'Restart' and getattr(self.device, 'is_desktop', False) \
                    and not self.device.app_is_running():
                logger.info('Desktop client not logged in, run `Restart` to login first')
                task = 'Restart'
                self.is_first_task = False

            # Run
            logger.info(f'Scheduler: Start task `{task}`')
            self.device.stuck_record_clear()
            self.device.click_record_clear()
            logger.hr(task, level=0)
            self.config.model.running_task = task
            _task_start = datetime.now()
            success = self.run(inflection.camelize(task))
            self.config.model.running_task = ''
            # 错峰模式：每跑完一个任务就释放执行权，让同组实例立刻接手，
            # 保证 oas1/oas3 的任务不会同时进行（而不是一直占着到空闲）。
            if self.instance_guard and self.config.script.optimization.stagger_enable:
                self.instance_guard.release()
            logger.info(f'Scheduler: End task `{task}`')
            self.is_first_task = False
            self.anti_ban_guard.record_active((datetime.now() - _task_start).total_seconds())

            # Check failures
            # 被 Restart 抢占而暂停的任务不算失败
            preempted = getattr(self, '_preempted', False)
            if preempted:
                self._preempted = False
            # failed = deep_get(self.failure_record, keys=task, default=0)
            failed = self.failure_record[task] if task in self.failure_record else 0
            failed = failed if preempted else (0 if success else failed + 1)
            # deep_set(self.failure_record, keys=task, value=failed)
            self.failure_record[task] = failed
            if failed >= 3:
                logger.critical(f"Task `{task}` failed 3 or more times.")
                logger.critical("Possible reason #1: You haven't used it correctly. "
                                "Please read the help text of the options.")
                logger.critical("Possible reason #2: There is a problem with this task. "
                                "Please contact developers or try to fix it yourself.")
                logger.critical('Request human takeover')
                # 添加失败三次的推送通知
                self.config.notifier.push(
                    title=f'{I18n.trans_zh_cn(task)}{task}',
                    content=f"<{self.config_name}> 任务连续失败三次，请上线查看"
                )
                # 关闭模拟器
                if self.config.script.error.error_repeated:
                    self.device.emulator_stop()
                exit(1)

            if success:
                del_cached_property(self, 'config')
                continue
            elif self.config.script.error.handle_error:
                # self.config.task_delay(success=False)
                del_cached_property(self, 'config')
                # self.checker.check_now()
                continue
            else:
                break

    def start_loop(self) -> None:
        """
        创建一个线程，运行loop
        :return:
        """
        if self.loop_thread is None:
            self.loop_thread = Thread(target=self.loop, name='Script_loop')
            self.loop_thread.start()


if __name__ == "__main__":
    ensure_ocr_server_started()
    script = Script("oas1")
    script.loop()

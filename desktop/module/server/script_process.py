# This Python file uses the following encoding: utf-8
# @author runhey
# 脚本进程
# github https://github.com/runhey
import sys, os
import signal
import multiprocessing
from contextlib import asynccontextmanager
from threading import Lock
from asyncio import QueueEmpty, CancelledError, sleep
from enum import Enum

from module.logger import logger
from module.server.config_manager import ConfigManager
from module.server.script_websocket import ScriptWSManager


class ScriptState(int, Enum):
    INACTIVE = 0
    RUNNING = 1
    WARNING = 2
    UPDATING = 3


class ScriptProcess(ScriptWSManager):

    def __init__(self, config_name: str) -> None:
        super().__init__()
        if config_name not in ConfigManager.all_script_files():
            raise FileNotFoundError(f'{config_name}.json not found')
        self.config_name = config_name  # config_name
        self.log_pipe_out, self.log_pipe_in = multiprocessing.Pipe(False)
        self.state_queue = multiprocessing.Queue()
        self.state: ScriptState = ScriptState.INACTIVE
        self._process = None
        self._lifecycle_lock = Lock()

    @asynccontextmanager
    async def _lifecycle(self):
        # Start/stop can also be called by the manager's background event loop.
        while not self._lifecycle_lock.acquire(blocking=False):
            await sleep(0.01)
        try:
            yield
        finally:
            self._lifecycle_lock.release()

    async def start(self):
        async with self._lifecycle():
            if self._process and self._process.is_alive():
                self.state = ScriptState.RUNNING
                await self.broadcast_state({"state": self.state})
                logger.info(f'Script {self.config_name} is already running; keep the existing process')
                return
            try:
                process = multiprocessing.Process(target=func,
                                                  args=(self.config_name, self.state_queue, self.log_pipe_in,),
                                                  name=self.config_name,
                                                  daemon=True)
                process.start()
            except Exception:
                self._process = None
                self.state = ScriptState.WARNING
                await self.broadcast_state({"state": self.state})
                raise
            self._process = process
            self.state = ScriptState.RUNNING
            await self.broadcast_state({"state": self.state})

    async def stop(self):
        async with self._lifecycle():
            processes = [self._process] if self._process is not None else []
            # Include children whose handles were overwritten by an earlier
            # duplicate start. active_children only returns our own children.
            for child in multiprocessing.active_children():
                if child.name == self.config_name and child not in processes:
                    processes.append(child)
            for process in processes:
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=0.7)
                    if process.is_alive():
                        logger.error(f'Script {self.config_name} subprocess terminate failed')
                        process.kill()
                        process.join(timeout=0.7)
            self._process = None
            self.state = ScriptState.INACTIVE
            await self.broadcast_state({"state": self.state})

    async def coroutine_broadcast_state(self):
        try:
            while 1:
                if self.state == ScriptState.INACTIVE:
                    await sleep(1)
                    continue
                await sleep(0.1)
                try:
                    if self.state_queue.empty():
                        await sleep(1)
                        continue
                    data = self.state_queue.get_nowait()
                    if not data:
                        await sleep(0.5)
                        continue
                    if 'state' in data and data['state'] == ScriptState.WARNING:
                        self.state = ScriptState.WARNING
                    await self.broadcast_state(data)
                except QueueEmpty as e:
                    logger.warning(f'QueueEmpty: {e}')
                    await sleep(0.5)
                    continue
                except Exception as e:
                    logger.error(f'Error: {e}')
                    continue
        except CancelledError as e:
            logger.warning(f'{self.config_name} state coroutine is cancelled')
            return

    async def coroutine_broadcast_log(self):
        try:
            while 1:
                if self.state == ScriptState.INACTIVE:
                    await sleep(1)
                    continue
                await sleep(0.05)
                try:
                    if not self.log_pipe_out.poll():
                        await sleep(0.3)
                        continue
                    log = self.log_pipe_out.recv()
                    if not log:
                        await sleep(0.5)
                        continue
                    await self.broadcast_log(log)
                except EOFError as e:
                    await sleep(0.5)
                    logger.warning(f'EOFError: {e}')
                    continue
                except Exception as e:
                    logger.error(f'Log Error: {e}')
                    continue
        except CancelledError as e:
            logger.warning(f'{self.config_name} log coroutine is cancelled')
            return


def func(config: str, state_queue: multiprocessing.Queue, log_pipe_in) -> None:
    def signal_handler(signum, frame):
        logger.info(f'Script {config} received signal {signum}, exiting gracefully')
        log_pipe_in.close()
        state_queue.close()
        sys.exit(0)

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    def start_log() -> None:
        try:
            from module.logger import set_file_logger, set_func_logger
            set_file_logger(name=config)
            # 管道对端（GUI 服务进程）可能已经退出：写入前先判流状态，
            # 关闭后由 logger 自动降级为只写文件日志，而不是抛 BrokenPipeError 打断任务
            set_func_logger(log_pipe_in.send, is_open=lambda: not log_pipe_in.closed)
        except Exception as e:
            logger.exception(f'Start log error')
            logger.error(f'Error: {e}')
            raise
    start_log()
    import time
    try:
        # while 1:
        #     time.sleep(1)
        #     logger.info(f'Script {config} is running')
        #     state_queue.put({"state": ScriptState.RUNNING})
        from script import Script
        script = Script(config_name=config)
        script.state_queue = state_queue
        script.loop()
    except SystemExit as e:
        logger.info(f'Script {config} process exit')
        logger.error(f'Error: {e}')
        state_queue.put({"state": ScriptState.WARNING})
        time.sleep(0.1)
        exit(-1)
    except Exception as e:
        logger.exception(f'Run script {config} error')
        logger.error(f'Error: {e}')
        raise


if __name__ == '__main__':
    p = ScriptProcess('oas1')
    p.start()
    from time import sleep
    sleep(10)
    logger.info(p._process.exitcode)



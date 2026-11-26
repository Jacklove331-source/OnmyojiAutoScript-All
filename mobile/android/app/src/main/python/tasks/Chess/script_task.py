# This Python file uses the following encoding: utf-8

import time
from pathlib import Path
from datetime import datetime, timedelta

from module.exception import (DeviceDisconnectedError,
                              GameNotRunningError,
                              GameStuckError,
                              TaskEnd)
from module.logger import logger
from module.atom.image import RuleImage
from tasks.Chess.assets import ChessAssets
from tasks.Chess.config import Chess
from tasks.Chess.runtime.economy import ChessEconomyMixin
from tasks.Chess.runtime.hand_operations import ChessHandOperationsMixin
from tasks.Chess.runtime.hyakki_draft import ChessHyakkiDraftMixin
from tasks.Chess.runtime.recognition import ChessRecognitionMixin
from tasks.Chess.runtime.round_state import ChessRoundStateMixin
from tasks.Chess.runtime.scout import ChessScoutMixin
from tasks.Chess.runtime.settings import ChessRuntimeSettings
from tasks.Chess.runtime.state_loop import ChessStateLoopMixin
from tasks.Component.GeneralBattle.general_battle import GeneralBattle
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_chess
from tasks.GameUi.page_check_assets import PageCheckAssets


class ChessLegacyScriptTask(
    ChessHyakkiDraftMixin,
    ChessRecognitionMixin,
    ChessScoutMixin,
    ChessHandOperationsMixin,
    ChessRoundStateMixin,
    ChessEconomyMixin,
    ChessRuntimeSettings,
    GameUi,
    GeneralBattle,
    ChessAssets,
):
    """百鬼棋局任务入口：仅负责任务、整局与回目编排。"""

    conf: Chess = None

    # port 分支把 82 个页面标志放在 page_check_assets.PageCheckAssets，
    # 而 GameUi 只继承原 GameUiAssets；棋局用到的页面标志在这里显式补齐。
    # 结算流程方法由 ChessBattleNavigationMixin（GameUi 基类）提供。
    I_CHECK_CHESS = PageCheckAssets.I_CHECK_CHESS
    I_CHECK_CHESS_RANK = PageCheckAssets.I_CHECK_CHESS_RANK
    I_CHESS_EXIT_TO_LOBBY = PageCheckAssets.I_CHESS_EXIT_TO_LOBBY
    I_CHESS_EXIT_TO_LOBBY_2 = PageCheckAssets.I_CHESS_EXIT_TO_LOBBY_2
    I_CHESS_RANK_GOTO_LOBBY = PageCheckAssets.I_CHESS_RANK_GOTO_LOBBY

    # 2026-11 手机真机 UI 模板。电脑版旧模板仍按 1280x720 标注，
    # 但新版游戏皮肤已不再命中，因此这里直接从当前手机帧裁取稳定标志。
    I_CHECK_MAIN_MOBILE = RuleImage(
        roi_front=(650, 250, 180, 150),
        roi_back=(650, 250, 180, 150),
        threshold=0.72,
        method="Template matching",
        file="./tasks/GameUi/page_checks/page_main_mobile.png",
    )
    I_CHECK_TOWN_MOBILE = RuleImage(
        roi_front=(980, 185, 160, 170),
        roi_back=(980, 185, 160, 170),
        threshold=0.72,
        method="Template matching",
        file="./tasks/GameUi/page_checks/page_town_mobile.png",
    )
    I_CHECK_ENTERTAINMENT_MOBILE = RuleImage(
        roi_front=(40, 0, 330, 105),
        roi_back=(40, 0, 330, 105),
        threshold=0.72,
        method="Template matching",
        file="./tasks/GameUi/page_checks/page_entertainment_mobile.png",
    )

    # 浮窗红叉只认右上角（弹窗关闭按钮的位置）。棋盘中央的红色特效、
    # 单位血条容易被 I_CLOSE_RED 误匹配，之前会在同一处反复点击直到
    # GameTooManyClickError。
    RED_CROSS_REGION = (940, 0, 340, 240)
    RED_CROSS_MAX_CONSECUTIVE = 3
    RED_CROSS_COOLDOWN = 20.0

    def _burst(self) -> bool:
        """棋局任务的所有截图均检查红叉，关闭遮挡操作的浮窗。"""
        if super()._burst():
            return True
        if self._close_floating_red_cross():
            return True
        return False

    def _close_floating_red_cross(self) -> bool:
        """命中右上角红叉才点击，并限制连续无效点击次数。"""
        if time.monotonic() < getattr(self, '_red_cross_cooldown_until', 0.0):
            return False
        matches = self.I_CLOSE_RED.match_all(self.device.image)
        if not matches:
            self._red_cross_clicks = 0
            return False
        _, x, y, width, height = max(matches, key=lambda item: item[0])
        center_x = x + width / 2
        center_y = y + height / 2
        rx, ry, rw, rh = self.RED_CROSS_REGION
        if not (rx <= center_x <= rx + rw and ry <= center_y <= ry + rh):
            return False
        if not self.appear_then_click(self.I_CLOSE_RED, interval=0.8):
            return False
        logger.info('Chess: close floating window with red cross')
        clicks = getattr(self, '_red_cross_clicks', 0) + 1
        self._red_cross_clicks = clicks
        if clicks >= self.RED_CROSS_MAX_CONSECUTIVE:
            self._red_cross_clicks = 0
            self._red_cross_cooldown_until = time.monotonic() + self.RED_CROSS_COOLDOWN
            logger.warning(
                'Chess: red cross has no effect after repeated clicks; '
                'ignore it for a while'
            )
        return True

    def _wait_chess_action_state(self, condition) -> bool:
        """限时确认动作结果；截图节奏交给设备层，不额外固定等待。"""
        deadline = time.monotonic() + self.ACTION_ICON_WAIT_TIMEOUT
        while time.monotonic() < deadline:
            self.device.stuck_record_clear()
            self.screenshot()
            if condition():
                return True
        self.device.stuck_record_clear()
        self.screenshot()
        return bool(condition())

    def _mobile_match(self, target, timeout: float = 5.0) -> bool:
        """在当前手机虚显帧上等待一个页面标志。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.screenshot()
            if self.appear(target):
                return True
            time.sleep(0.5)
        return False

    def _ensure_mobile_chess_lobby(self) -> bool:
        """使用真机 1280x720 标定坐标，从庭院/町中/鼬乐园进入棋局大厅。"""
        if self._mobile_match(self.I_CHECK_CHESS, timeout=2.0):
            return True

        if self._mobile_match(self.I_CHECK_MAIN_MOBILE, timeout=2.0):
            logger.info("Chess mobile: click 町中 entrance at (740,330)")
            self.device.click(740, 330, control_name="CHESS_MOBILE_TOWN")
            time.sleep(3.0)

        if self._mobile_match(self.I_CHECK_TOWN_MOBILE, timeout=6.0):
            logger.info("Chess mobile: click 鼬乐园 entrance at (220,160)")
            self.device.click(220, 160, control_name="CHESS_MOBILE_ENTERTAINMENT")
            time.sleep(3.0)

        if self._mobile_match(self.I_CHECK_ENTERTAINMENT_MOBILE, timeout=6.0):
            logger.info("Chess mobile: click 百鬼棋局 entrance at (324,239)")
            self.device.click(324, 239, control_name="CHESS_MOBILE_LOBBY")
            time.sleep(5.0)

        return self._mobile_match(self.I_CHECK_CHESS, timeout=8.0)


    OUTCOME_SERIES_COMPLETED = 'series_completed'
    OUTCOME_RECOVERED_CONTINUE = 'recovered_continue'
    OUTCOME_RECOVERED_ABORTED = 'recovered_aborted'
    OUTCOME_FAILED = 'failed'
    OUTCOME_STOPPED = 'stopped'

    MATCH_LOG = Path('./log/chess_matches.jsonl')
    # 连续多少局读不到结算名次就放弃（避免白烧局数）
    UNVERIFIED_MATCH_LIMIT = 2

    def _device_recovered_this_run(self) -> bool:
        """本轮是否发生过成功的 ADB 重连（由 reconnect 在 Device 上置位）。"""
        return bool(
            getattr(
                getattr(self, 'device', None),
                '_recovered_this_run',
                False,
            )
        )

    def _mark_outcome(self, status: str, reason: str = '') -> None:
        """记录本局结果状态（不改变 TaskEnd 语义，只做分类与留痕）。"""
        import json
        from datetime import datetime as _dt
        self._chess_outcome = status
        payload = {
            'time': _dt.now().strftime('%Y-%m-%d %H:%M:%S'),
            'config': getattr(self.config, 'config_name', ''),
            'status': status,
            'reason': reason,
            'matches_started': int(getattr(self, '_match_started_count', 0)),
            'matches_verified': int(getattr(self, '_match_verified_count', 0)),
        }
        logger.info(f'[ChessOutcome] status={status} reason={reason}')
        try:
            log_dir = Path('./log')
            log_dir.mkdir(parents=True, exist_ok=True)
            with (log_dir / 'chess_outcome.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(payload, ensure_ascii=False) + '\n')
        except Exception:  # noqa: BLE001 - 记录失败不影响任务
            pass

    # ------------------------------------------------------------ 逐局记录
    # 只在整个任务结束时写一条 outcome 无法回答「第几局在第几回合、名次多少、
    # 有没有真的退回大厅」，也无法区分「这局跑完了」和「这局被 Restart 接管」。
    # 因此每局单独落一条 chess_matches.jsonl，字段与验收标准一一对应。

    def _begin_match(self, index: int, target: int, mode: str = 'normal') -> dict:
        """开始一局，返回可不断补充字段的记录字典。"""
        self._match_started_count = int(getattr(self, '_match_started_count', 0)) + 1
        record = {
            'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'config': getattr(self.config, 'config_name', ''),
            'index': int(index),
            'target': int(target) if target != -1 else -1,
            'mode': mode,
            'started_monotonic': time.monotonic(),
            'round_reached': 0,
            'final_rank': None,
            'game_over_detected': False,
            'exit_confirmed': False,
            'match_completed': False,
            'error_kind': None,
            'error_reason': None,
            'recovered': False,
        }
        self._match_round_reached = 0
        self._last_game_exit_confirmed = False
        logger.info(
            f'[ChessMatch] start index={index} target={target} mode={mode}'
        )
        return record

    def _finish_match(self, record: dict) -> dict:
        """收尾一局记录：补时长/最高回合，落盘并输出一行摘要。"""
        if record.get('finished'):
            return record
        record['finished'] = True
        started = record.pop('started_monotonic', None)
        if started is not None:
            record['duration_s'] = round(time.monotonic() - started, 1)
        try:
            record['round_reached'] = max(
                int(record.get('round_reached') or 0),
                int(getattr(self, '_match_round_reached', 0) or 0),
                # 双保险：实际跑的是 state_loop.run_one_round，它维护
                # _current_round_no；某条路径漏更新 _match_round_reached 时
                # 仍能取到回合数（首版曾因此把 round_reached 记成 0）。
                int(getattr(self, '_current_round_no', 0) or 0),
            )
        except (TypeError, ValueError):
            pass
        if record.get('final_rank') is None:
            record['final_rank'] = getattr(self, '_last_game_rank', None)
        if self._device_recovered_this_run():
            record['recovered'] = True
        self._last_game_exit_confirmed = bool(record.get('exit_confirmed'))
        if record.get('match_completed'):
            self._match_verified_count = int(
                getattr(self, '_match_verified_count', 0)
            ) + 1
        logger.info(
            '[ChessMatch] end '
            f'index={record.get("index")} '
            f'round={record.get("round_reached")} '
            f'rank={record.get("final_rank")} '
            f'game_over={record.get("game_over_detected")} '
            f'exit_confirmed={record.get("exit_confirmed")} '
            f'completed={record.get("match_completed")} '
            f'error={record.get("error_kind")} '
            f'duration={record.get("duration_s")}s'
        )
        if record.get('recovered') and record.get('match_completed'):
            # 本局是在 ADB 恢复成功后打完的，单独留一条可机读状态；
            # 恢复失败的对局仍由 run() 记为 recovered_aborted。
            self._mark_outcome(
                self.OUTCOME_RECOVERED_CONTINUE,
                f'match index={record.get("index")} '
                f'rank={record.get("final_rank")}',
            )
        try:
            record['saved_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            self.MATCH_LOG.parent.mkdir(parents=True, exist_ok=True)
            import json
            with self.MATCH_LOG.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + '\n')
        except Exception:  # noqa: BLE001 - 记录失败不影响任务
            logger.warning('Chess match record write failed')
        return record


    def run(self):
        """任务入口：包一层，把异常/中断归类成明确的对局状态。"""
        try:
            return self._run_impl()
        except TaskEnd:
            raise
        except DeviceDisconnectedError as error:
            # ADB 掉线（自动重连失败）与「游戏真的没跑」分开记账，
            # 两者都交给调度器的 Restart 恢复，但原因必须可区分。
            self._mark_outcome(
                self.OUTCOME_RECOVERED_ABORTED,
                f'adb disconnected / restart: {error}',
            )
            raise
        except GameNotRunningError as error:
            self._mark_outcome(
                self.OUTCOME_RECOVERED_ABORTED,
                f'game died / restart: {error}',
            )
            raise
        except Exception as error:  # noqa: BLE001
            self._mark_outcome(self.OUTCOME_FAILED, repr(error))
            raise

    def _run_impl(self):
        """按执行次数循环百鬼棋局，并可在鼬乐币刷满时提前结束。"""
        chess_task_config = getattr(self.config, 'chess', None)
        chess_config = getattr(chess_task_config, 'chess_config', None)
        selected_lineup = getattr(
            chess_config,
            'lineup_bond',
            self.DEFAULT_LINEUP_KEY,
        )
        strategy = self.select_lineup_strategy(selected_lineup)

        # 启动恢复会主动退出遗留对局；正常循环只保留保段位退两局。
        self._recover_interrupted_chess_game()
        if self._ensure_mobile_chess_lobby():
            self.navigator.current_page = page_chess
        else:
            self.goto_page(page_chess)

        # Config 持有完整 ConfigModel，Chess 专属配置位于
        # self.config.chess.chess_config。旧配置未包含新字段时使用默认值，
        # 避免升级后任务在进入棋局大厅时直接崩溃。
        target_count = int(getattr(chess_config, 'run_count', 1))
        configured_limit_time = getattr(chess_config, 'limit_time', None)
        if configured_limit_time is None:
            self.limit_time = timedelta(minutes=30)
        else:
            self.limit_time = timedelta(
                hours=configured_limit_time.hour,
                minutes=configured_limit_time.minute,
                seconds=configured_limit_time.second,
            )
        coin_full_exit = bool(
            getattr(chess_config, 'coin_full_exit', False)
        )
        rank_protection = bool(
            getattr(chess_config, 'rank_protection', False)
        )
        self._matchmaking_timeout_seconds = max(
            10,
            int(getattr(chess_config, 'matchmaking_timeout_seconds', 60)),
        )
        self._consecutive_matchmaking_timeouts = 0
        self._match_started_count = 0
        self._match_verified_count = 0
        completed = 0
        unverified_streak = 0
        coin_limit_reached = False
        rank_protection_exits_remaining = 0
        next_game_already_started = False
        logger.info(
            'Chess task constraints: '
            f'lineup={strategy["key"]} ({strategy["display_name"]}), '
            f'run_count={target_count}, limit_time={self.limit_time}, '
            f'coin_full_exit={coin_full_exit}, '
            f'rank_protection={rank_protection}, '
            f'matchmaking_timeout={self._matchmaking_timeout_seconds}s'
        )

        while (
            target_count == -1
            or completed < target_count
            or rank_protection_exits_remaining > 0
        ):
            # 排名页“再来一局”已经发起匹配时，本轮必须直接接管该局；
            # 时间和满币条件已在点击按钮前检查，不能再次检查后把新局遗留。
            game_already_started = next_game_already_started
            next_game_already_started = False
            if not game_already_started:
                if datetime.now() - self.start_time >= self.limit_time:
                    logger.info(
                        'Chess task time reached; '
                        'stop before starting next game'
                    )
                    break
                self.screenshot()
                if coin_full_exit and self._coin_is_full():
                    logger.info(
                        'Stop Chess task before next game: '
                        'coin reached 600/600'
                    )
                    coin_limit_reached = True
                    break

            logger.debug(
                'Chess game loop '
                f'{completed + 1}/'
                f'{"infinite" if target_count == -1 else target_count}'
            )
            self._rank_protection_exit_requested = bool(
                rank_protection and rank_protection_exits_remaining > 0
            )
            self._rank_protection_exit_succeeded = False
            match = self._begin_match(
                index=completed + 1,
                target=target_count,
                mode=(
                    'rank_protection'
                    if self._rank_protection_exit_requested
                    else 'normal'
                ),
            )
            try:
                game_rank = self.run_one_game()
            except TaskEnd as error:
                match['error_kind'] = 'task_end'
                match['error_reason'] = str(error)
                self._finish_match(match)
                raise
            except DeviceDisconnectedError as error:
                match['error_kind'] = 'device_disconnected'
                match['error_reason'] = str(error)
                self._finish_match(match)
                raise
            except GameNotRunningError as error:
                match['error_kind'] = 'game_not_running'
                match['error_reason'] = str(error)
                self._finish_match(match)
                raise
            except Exception as error:  # noqa: BLE001 - 记录后按原样抛给调度器
                match['error_kind'] = type(error).__name__
                match['error_reason'] = str(error)
                self._finish_match(match)
                raise

            match['final_rank'] = game_rank
            match['game_over_detected'] = isinstance(game_rank, int)
            rank_verified = (
                isinstance(game_rank, int) and 1 <= game_rank <= 8
            )

            if (
                self._rank_protection_exit_requested
                and self._rank_protection_exit_succeeded
            ):
                rank_protection_exits_remaining -= 1
                logger.info(
                    'Chess rank-protection exit completed: '
                    f'remaining={rank_protection_exits_remaining}/'
                    f'{self.RANK_PROTECTION_EXIT_COUNT}, '
                    f'completed_games={completed}'
                )
                # 主动退出不计入完成局数（本局没有打完），但要记录退出是否
                # 真的回到了大厅/新局。
                match['error_kind'] = 'rank_protection_exit'
                if rank_protection_exits_remaining > 0:
                    next_game_already_started = (
                        self._restart_chess_game_from_result()
                    )
                    match['exit_confirmed'] = bool(next_game_already_started)
                else:
                    match['exit_confirmed'] = bool(
                        self.return_to_chess_lobby()
                    )
                self._finish_match(match)
                continue

            if not rank_verified:
                # 结算名次读不到 = 没有证据证明这一局真的打完。不能计入
                # completed（否则被 Restart/中断接管的局会被当成成功），
                # 退回大厅后再开一局；连续出现则直接判定失败退出。
                unverified_streak += 1
                logger.warning(
                    'Chess game ended without a readable rank; '
                    f'not counted (rank={game_rank}, '
                    f'streak={unverified_streak}/{self.UNVERIFIED_MATCH_LIMIT})'
                )
                match['error_kind'] = 'rank_ocr_unavailable'
                match['error_reason'] = f'rank={game_rank}'
                try:
                    match['exit_confirmed'] = bool(
                        self.return_to_chess_lobby()
                    )
                except Exception as error:  # noqa: BLE001
                    match['error_reason'] = (
                        f'rank={game_rank}; lobby return failed: {error!r}'
                    )
                    self._finish_match(match)
                    raise
                self._finish_match(match)
                if unverified_streak >= self.UNVERIFIED_MATCH_LIMIT:
                    logger.error(
                        'Chess: consecutive games without rank evidence, '
                        'stop the task instead of burning more games'
                    )
                    break
                continue
            unverified_streak = 0

            completed += 1
            match['match_completed'] = True
            if rank_protection and game_rank is not None and game_rank <= 4:
                rank_protection_exits_remaining = (
                    self.RANK_PROTECTION_EXIT_COUNT
                )
                logger.info(
                    'Chess rank protection activated: '
                    f'last_rank=第{game_rank}名, schedule '
                    f'{self.RANK_PROTECTION_EXIT_COUNT} active exits'
                )
            else:
                rank_protection_exits_remaining = 0
                if rank_protection:
                    logger.info(
                        'Chess rank protection not activated: '
                        f'last_rank='
                        f'{"未知" if game_rank is None else f"第{game_rank}名"}, '
                        'requires 第1至第4名'
                    )
            logger.info(
                f'Chess completed games: {completed}/'
                f'{"infinite" if target_count == -1 else target_count}, '
                f'rank_protection_exits_pending='
                f'{rank_protection_exits_remaining}'
            )

            # “再来一局”只用于保段位的两次快速退出。正常对局即使还
            # 有运行次数，也仍返回大厅，再由原大厅开局流程开始下一场。
            if rank_protection_exits_remaining > 0:
                if datetime.now() - self.start_time >= self.limit_time:
                    logger.info(
                        'Chess task time reached; '
                        'skip rank-protection restart and return to lobby'
                    )
                    match['exit_confirmed'] = bool(self.return_to_chess_lobby())
                    self._finish_match(match)
                    break
                next_game_already_started = (
                    self._restart_chess_game_from_result()
                )
                match['exit_confirmed'] = bool(next_game_already_started)
                self._finish_match(match)
                continue

            # 结算流程必须真的回到棋局大厅；return_to_chess_lobby() 内部
            # 逐帧确认 I_CHECK_CHESS，返回 True 才算退出成功，失败会抛
            # GameStuckError（此时逐局记录里 exit_confirmed 保持 False）。
            match['exit_confirmed'] = bool(self.return_to_chess_lobby())
            self._finish_match(match)

            # 鼬乐币总数只在大厅可读；普通对局回到大厅后先核对，再
            # 决定是否继续下一场。保段位两局一旦开始则连续执行完毕。
            if coin_full_exit:
                self.screenshot()
                if self._coin_is_full():
                    logger.info(
                        'Stop Chess task after game: coin reached 600/600'
                    )
                    coin_limit_reached = True
                    break

        logger.info(
            f'Chess task loop finished: completed={completed}, '
            f'target={target_count}, coin_full_exit={coin_full_exit}, '
            f'rank_protection={rank_protection}'
        )
        if coin_limit_reached:
            # 金币已满：等到下周一 09:00 再跑（原先调用的 set_next_run_next_monday 并不存在）
            now = datetime.now()
            days = 7 - now.weekday()
            next_monday = (now + timedelta(days=days)).replace(
                hour=9, minute=0, second=0, microsecond=0)
            self.set_next_run(task='Chess', target=next_monday)
        else:
            pass  # 成功与否延后到 success 计算之后再设置，避免 RESULT True 与 outcome 不一致
        # 完整对局成功必须同时满足：局数达标 + 最近一局检测到结算名次（OCR 证据）
        # + 结算页确实退回到棋局大厅。只有局数达标却未识别到结算，说明对局可能
        # 被打断或被恢复流程接管，不能算成功。
        settled = isinstance(getattr(self, '_last_game_rank', None), int)
        exit_confirmed = bool(getattr(self, '_last_game_exit_confirmed', False))
        started = int(getattr(self, '_match_started_count', 0))
        verified = int(getattr(self, '_match_verified_count', 0))
        success = (
            target_count != -1
            and completed >= target_count
            and settled
            and exit_confirmed
        )
        if not coin_limit_reached:
            self.set_next_run(task='Chess', success=success, finish=True)
        if success and self._device_recovered_this_run():
            status = self.OUTCOME_RECOVERED_CONTINUE
        else:
            status = (
                self.OUTCOME_SERIES_COMPLETED if success
                else self.OUTCOME_FAILED
            )
        self._mark_outcome(
            status,
            f'completed={completed}/{target_count} settled={settled} '
            f'exit_confirmed={exit_confirmed} '
            f'matches={verified}/{started} verified '
            f'recovered_continue={self._device_recovered_this_run()} '
            f'limit_time={self.limit_time}',
        )
        raise TaskEnd('Chess')

    def run_one_game(self) -> int | None:
        """运行一局，停在结算流程并返回 OCR 得到的实际名次。"""
        self._start_chess_game()
        self._run_round_loop()
        rank = getattr(self, '_last_game_rank', None)
        if isinstance(rank, int) and 1 <= rank <= 8:
            logger.info(f'Chess game ended: 第{rank}名')
            return rank
        logger.warning(
            f'Chess game ended: result-page rank OCR unavailable [{rank}]'
        )
        return None

    def run_one_round(self, round_no: int) -> int | None:
        """执行一个回目：回合开始 -> 备 -> 战/鬼/待 -> 等待新回合。"""
        self._current_round_no = int(round_no)
        # 逐局记录要能回答「这局打到第几回合」，即使中途异常也保留最大值。
        self._match_round_reached = max(
            int(getattr(self, '_match_round_reached', 0) or 0),
            int(round_no),
        )
        logger.debug(f'Chess round {round_no}')
        phase = 'await_preparation'
        preparation_done = False
        next_round_candidate = None
        next_round_confirmed = 0
        unknown_since = None
        battle_economy_done = False
        battle_hand_cleanup_done = False
        hyakki_round_seen = False
        hyakki_lineup_checked = False

        while True:
            self.device.stuck_record_clear()
            if self._refresh_round_state_screenshot():
                time.sleep(self.SLOW_POLL_INTERVAL)
                continue
            if self._finish_chess_game_after_markers_missing(
                f'round_{round_no}'
            ):
                return None
            if getattr(self, '_rank_protection_exit_requested', False):
                logger.info(
                    'Chess rank protection: actively exit this game; '
                    'the game will not count toward completed runs'
                )
                if self.Chess_Exit(return_to_lobby=False):
                    self._rank_protection_exit_succeeded = True
                    return None
                logger.warning(
                    'Chess rank-protection exit was unavailable; '
                    'retry on the next state refresh'
                )
            observed_round = self._read_round_number()
            mode = self._read_chess_mode()

            round_transition_pending = False
            if observed_round is not None and observed_round != round_no:
                if observed_round == next_round_candidate:
                    next_round_confirmed += 1
                else:
                    next_round_candidate = observed_round
                    next_round_confirmed = 1
                round_transition_pending = True
                if next_round_confirmed >= self.ROUND_CONFIRM_FRAMES:
                    if hyakki_round_seen and not hyakki_lineup_checked:
                        self._reconcile_lineup_after_hyakki_round(
                            next_round_no=observed_round,
                        )
                        hyakki_lineup_checked = True
                    logger.debug(
                        f'Chess round boundary confirmed: {round_no} -> '
                        f'{observed_round}, phase={phase}, '
                        f'preparation_done={preparation_done}'
                    )
                    return observed_round
            else:
                next_round_candidate = None
                next_round_confirmed = 0

            # 回目数字第一次变化时暂停所有旧回目动作，等待第二帧确认。
            if round_transition_pending:
                time.sleep(self.SLOW_POLL_INTERVAL)
                continue

            in_game = mode is not None or self._is_in_chess_game()
            if in_game:
                unknown_since = None
            elif unknown_since is None:
                unknown_since = time.monotonic()
            elif time.monotonic() - unknown_since >= self.UNKNOWN_STATE_TIMEOUT:
                raise GameStuckError(
                    f'Chess: lost all markers during round {round_no}'
                )

            if mode in ('战', '鬼', '待'):
                if mode == '鬼':
                    hyakki_round_seen = True
                if mode == '战':
                    if not battle_hand_cleanup_done:
                        battle_hand_cleanup_done = (
                            self._handle_battle_sell_stage()
                        )
                    # 战阶段必须先完成非体系卡清理，再进入刷新升级。
                    # 若卖卡过程中阶段发生变化，本回目不再启动经济循环。
                    if (
                        battle_hand_cleanup_done
                        and not battle_economy_done
                        and self._read_chess_mode() == '战'
                    ):
                        self._run_battle_economy_until_budget_limit()
                        battle_economy_done = True
                    self._handle_passive_stage('战')
                else:
                    self._handle_passive_stage(mode)
                if phase == 'await_preparation':
                    preparation_done = True
                    phase = 'await_battle_end'
                    logger.warning(
                        f'Chess round {round_no}: resumed in passive mode '
                        f'{mode}; treat preparation as already passed'
                    )

            elif mode == '备':
                # 符咒面板是当前备阶段的高优先级中断事件。选完后不推进
                # phase/preparation_count；下一轮循环会把它当作新的同阶段备，
                # 从上式神、上御魂的起点重新执行。
                if self.appear(self.I_SELECT_GRIGRI):
                    logger.debug('Chess preparation interrupted by grigri selection')
                    self.select_grigri()
                    continue

                if phase == 'await_preparation':
                    # 与 state_loop 的 PREPARE 分支保持同一顺序：先买一次，
                    # 再立刻上阵/上魂，最后才是刷新/升级（结束时自动关店）。
                    self._refresh_adaptive_before_decision()
                    self.purchase_lineup_cards_once()
                    deployed = False
                    if self._is_preparation_mode():
                        deployed = self._handle_preparation_stage(1)
                    if not self._is_preparation_mode():
                        time.sleep(self.SLOW_POLL_INTERVAL)
                        continue
                    self._run_preparation_economy_until_time_limit()
                    if not self._is_preparation_mode():
                        time.sleep(self.SLOW_POLL_INTERVAL)
                        continue
                    final = self._handle_preparation_stage(1)
                    if deployed or final:
                        preparation_done = True
                        phase = 'await_battle'
                        logger.debug(
                            f'Chess round {round_no}: preparation '
                            f'complete (deploy_first={deployed}, '
                            f'after_economy={final}); wait for 战/鬼/待'
                        )

            interval = (
                3 * self.SLOW_POLL_INTERVAL
                if mode == '鬼'
                else self.SLOW_POLL_INTERVAL
            )
            time.sleep(interval)

    def _run_round_loop(self) -> None:
        """运行单局回目循环；Chess 自行持续刷新通用卡死计时。"""
        self.device.stuck_record_clear()
        try:
            self._run_game_by_rounds_without_device_stuck_timeout()
        finally:
            self.device.stuck_record_clear()

    # ------------------------------------------------------------ 阶段时间预算
    # 经济循环的停止阈值不能写死 15 秒：截图方式（nemu 约 0.2s、ADB 约 0.9s）、
    # 上阵/上魂的实测耗时都会变。这里用**实测 EMA** 推算「留给关店+上阵+上魂」
    # 需要多少秒，再拿它当停止阈值；没有历史时退回保守的 20 秒。
    ECONOMY_RESERVE_FALLBACK = 20.0
    ECONOMY_RESERVE_MINIMUM = 12.0
    ECONOMY_RESERVE_MARGIN = 3.0
    PHASE_COST_ALPHA = 0.3

    def _update_phase_cost(self, key: str, seconds: float) -> None:
        """记录某个阶段动作的实测耗时（指数滑动平均）。"""
        costs = dict(getattr(self, '_phase_costs', {}) or {})
        previous = costs.get(key)
        if previous is None:
            costs[key] = round(float(seconds), 2)
        else:
            costs[key] = round(
                previous + (float(seconds) - previous) * self.PHASE_COST_ALPHA,
                2,
            )
        self._phase_costs = costs

    def _phase_time_budget(self) -> float:
        """留给「关店 + 上阵 + 上魂 + 状态确认」的时间预算（秒）。"""
        costs = getattr(self, '_phase_costs', {}) or {}
        deploy = costs.get('deploy')
        soul = costs.get('soul')
        if deploy is None and soul is None:
            return self.ECONOMY_RESERVE_FALLBACK
        # +2s：两段操作之间的确认/截图开销；再留 margin 吸收抖动
        budget = (deploy or 0.0) + (soul or 0.0) + 2.0 + self.ECONOMY_RESERVE_MARGIN
        return round(max(budget, self.ECONOMY_RESERVE_MINIMUM), 1)

    def _handle_preparation(self) -> bool:
        """统一备阶段：符咒弹窗优先处理，随后上式神、上御魂。"""
        if self.appear(self.I_SELECT_GRIGRI):
            self.select_grigri()
            return False
        if not self._is_preparation_mode():
            return False
        # deploy_shikigami_from_hand 内部负责确认商店关闭、人数上限和
        # 每次拖动后的重新定位，准备阶段不重复实现这些约束。
        started = time.monotonic()
        self.deploy_shikigami_from_hand()
        self._update_phase_cost('deploy', time.monotonic() - started)
        if self._is_shop_open() or not self._is_preparation_mode():
            logger.warning(
                'Stop Chess preparation before soul phase: '
                'shop is open or mode left preparation'
            )
            return False
        # 上式神与上御魂保持为两个独立操作，由准备阶段明确编排顺序。
        verified_board_names = {
            name
            for name in getattr(self, '_board_lineup_names', set())
            if name in self.shikigami_deploy_positions
        }
        started = time.monotonic()
        self.equip_souls_from_hand(verified_board_names)
        self._update_phase_cost('soul', time.monotonic() - started)
        return self._is_preparation_mode()

    def _run_preparation_economy_until_time_limit(self) -> None:
        """备阶段升级/刷新循环：仅这部分受剩余时间限制，结束时关商店。"""
        if not self._is_preparation_mode():
            return

        try:
            self._schedule_economy_cycle()
            while getattr(self, '_economy_pending', False):
                if not self._is_preparation_mode():
                    return
                if self.appear(self.I_SELECT_GRIGRI):
                    return
                # 只有升级/刷新循环受剩余时间限制；第二套布局从第一套阶段
                # 框读取倒计时，第一套布局使用独立 now_time 区域。
                remaining = self._read_remaining_time()
                budget = self._phase_time_budget()
                if remaining is not None and remaining < budget:
                    logger.info(
                        'Stop Chess preparation upgrade/refresh loop: '
                        f'remaining_time={remaining} < budget={budget} '
                        f'(costs={getattr(self, "_phase_costs", {})})'
                    )
                    return
                result = self._run_economy_atomic_batch(battle_mode=False)
                if result in ('complete', 'blocked'):
                    return
                self.screenshot()
        finally:
            # 上阵/上魂要求商店关闭。经济循环把时间用完后如果留着商店，
            # 紧接着的布置会因「商店未关闭」被整段跳过——实测三局连跑里
            # 出现 20 次，直接导致每局只上阵 1~4 个单位。
            self._leave_shop_closed_after_economy()

    def _leave_shop_closed_after_economy(self) -> None:
        """经济动作结束后确保商店关闭；阶段已切换时只清内部状态，不盲点。"""
        try:
            visible = self._shop_refresh_marker_visible()
            assumed = bool(getattr(self, '_shop_assumed_open', False))
            if not visible and not assumed:
                return
            mode = self._read_chess_mode()
            if mode in ('备', '战') and self._ensure_shop_closed(
                allowed_modes=(mode,)
            ):
                self._shop_assumed_open = False
                logger.debug('Chess economy ended with the shop closed')
                return
            if not visible:
                # 阶段变化后画面里已经没有刷新按钮：清掉脚本侧状态即可，
                # 此时点市场要么无效、要么会把商店重新打开。
                self._shop_assumed_open = False
                logger.debug(
                    'Chess shop marker gone after economy; clear stale state'
                )
        except Exception as error:  # noqa: BLE001 - 关店失败不能中断回合
            logger.debug(f'Chess leave-shop-closed failed: {error!r}')

    def _run_battle_economy_until_budget_limit(self) -> None:
        """战阶段购买体系卡后继续升级/刷新，按实测时间预算收手。"""
        if self._read_chess_mode() != '战':
            return
        # 与备阶段使用同一入口：先确保商店打开并购买当前体系卡，
        # 再根据金币门槛进入升级/刷新循环。
        self._refresh_adaptive_before_decision()
        self.purchase_lineup_cards_once()
        if self._read_chess_mode() != '战':
            return
        self._schedule_economy_cycle()
        while (
            self._read_chess_mode() == '战'
            and getattr(self, '_economy_pending', False)
        ):
            remaining = self._read_remaining_time()
            budget = self._phase_time_budget()
            if remaining is not None and remaining < budget:
                logger.info(
                    'Stop Chess battle upgrade/refresh loop: '
                    f'remaining_time={remaining} < budget={budget}'
                )
                return
            result = self._run_economy_atomic_batch(battle_mode=True)
            if result in ('complete', 'blocked'):
                return
            self.screenshot()

    def _handle_preparation_stage(self, stage_index: int) -> bool:
        """执行一次完整备阶段；卖卡已移至独立的战阶段环节。"""
        logger.debug(f'Chess preparation stage {stage_index}/2')
        return self._handle_preparation() and self._is_preparation_mode()

    def _handle_battle_sell_stage(self) -> bool:
        """战阶段独立卖卡：持续清理杂卡和纹章，直到确认手牌干净。"""
        if self._read_chess_mode() != '战':
            return False
        logger.debug('Chess battle hand-card cleanup')
        self._refresh_adaptive_before_decision()
        sold = self.cleanup_non_lineup_hand_cards(allowed_modes=('战',))
        logger.debug(f'Chess battle hand-card cleanup complete: sold={sold}')
        return self._read_chess_mode() == '战'

    def _handle_passive_stage(self, mode: str) -> bool:
        """战和待阶段等待；鬼阶段执行试行投掷。"""
        if mode == '鬼':
            self._handle_hyakki_draft()
        if mode == '战':
            self._scout_opponents_once()
        return mode in ('战', '鬼', '待')

    def _scout_opponents_once(self) -> None:
        """战阶段侦察一次对手阵容，供「随机应变」判断抢卡。"""
        round_no = int(getattr(self, '_current_round_no', 0) or 0)
        if getattr(self, '_scout_done_round', None) == round_no:
            return
        try:
            names = self.scout_opponent_lineup()
        except Exception as error:  # noqa: BLE001 - 侦察失败不影响棋局
            logger.debug(f'Chess opponent scout failed: {error}')
            return
        if len(names) < 2:
            # 战斗刚开始时顶部阵容条可能还没画出来，下一轮再试。
            attempts = getattr(self, '_scout_attempts', 0) + 1
            self._scout_attempts = attempts
            if attempts >= 5:
                self._scout_done_round = round_no
                self._scout_attempts = 0
            return
        self._scout_done_round = round_no
        self._scout_attempts = 0
        self._opponent_bonds = self.scout_opponent_bonds(names)

    def _refresh_adaptive_before_decision(self) -> None:
        """随机应变：在买卡/卖卡前按当前局面重算一次阵容。"""
        if not getattr(self, 'is_adaptive_lineup', None):
            return
        try:
            if self.is_adaptive_lineup():
                self.refresh_adaptive_strategy()
        except Exception as error:  # noqa: BLE001 - 决策失败不应中断棋局
            logger.warning(f'Chess adaptive refresh failed: {error}')



    def _refresh_round_state_screenshot(self) -> bool:
        """刷新回目状态截图；选符咒出现时必须优先处理完毕。"""
        self.screenshot()
        if self.select_chess_sixrealms_if_visible():
            self.screenshot()
            return True
        if not self.appear(self.I_SELECT_GRIGRI):
            return False
        logger.info(
            'Chess round-state refresh interrupted by grigri selection; '
            'resolve grigri before reading round and mode'
        )
        self.select_grigri()
        self.screenshot()
        return True

    def _finish_chess_game_after_markers_missing(
        self,
        context: str,
    ) -> bool:
        """两个局内标志连续三帧消失后，等待结算页并读取实际名次。"""
        if self._is_in_chess_game():
            return False

        for frame in range(2, self.GAME_END_CONFIRM_FRAMES + 1):
            time.sleep(2 * self.SLOW_POLL_INTERVAL)
            self.device.stuck_record_clear()
            self.screenshot()
            if self._is_in_chess_game():
                logger.debug(
                    'Chess game-end confirmation cancelled: '
                    f'in-game marker recovered at frame={frame}, '
                    f'context={context}'
                )
                return False

        logger.info(
            'Chess game end detected: all in-game markers absent for '
            f'{self.GAME_END_CONFIRM_FRAMES} frames, context={context}'
        )
        deadline = time.monotonic() + self.GAME_OVER_WAIT_TIMEOUT
        while time.monotonic() < deadline:
            self.device.stuck_record_clear()
            if self._is_in_chess_game():
                logger.info(
                    'Chess game-end wait cancelled: in-game marker recovered; '
                    f'reset missing-frame count, context={context}'
                )
                return False
            if self.appear(self.I_GAME_OVER_1) or self.appear(self.I_GAME_OVER_2):
                rank, raw = self._read_game_rank()
                # 结算横幅有出现动画：单帧 OCR 常读到空串（实测一局 26 回合被判
                # rank_ocr_unavailable，直接导致该局记为未完成）。这里重拍重读，
                # 最多 3 次，仍读不到才按失败记录。
                attempts = 1
                while rank is None and attempts < 3:
                    time.sleep(1.5)
                    self.device.stuck_record_clear()
                    self.screenshot()
                    if not (self.appear(self.I_GAME_OVER_1)
                            or self.appear(self.I_GAME_OVER_2)):
                        break
                    rank, raw = self._read_game_rank()
                    attempts += 1
                self._last_game_rank = rank
                if rank is None:
                    logger.warning(
                        f'Chess game-over rank OCR invalid: [{raw}] '
                        f'attempts={attempts}'
                    )
                else:
                    logger.info(
                        f'Chess game-over rank OCR: [{raw}] -> 第{rank}名'
                    )
                return True
            time.sleep(2 * self.SLOW_POLL_INTERVAL)
            self.screenshot()

        raise GameStuckError(
            'Chess: all in-game markers disappeared, but I_GAME_OVER did not '
            f'appear within {self.GAME_OVER_WAIT_TIMEOUT:.0f}s'
        )

    def _restart_chess_game_from_result(self) -> bool:
        """推进到排名页并点击“再来一局”；回到大厅时返回 False。"""
        logger.debug('Chess result flow: restart from rank page')
        self._chess_share_advancing = False
        deadline = time.monotonic() + self.CHESS_EXIT_TIMEOUT
        exit_clicked = False
        share_seen = False
        safe_clicks = 0
        next_safe_click_at = 0.0
        fallback_exit_at = time.monotonic() + 1.5

        while time.monotonic() < deadline:
            self.device.stuck_record_clear()
            self.screenshot()

            if self._advance_chess_result_stage():
                exit_clicked = True
                share_seen = True
                time.sleep(self.CHESS_EXIT_SCREENSHOT_INTERVAL)
                continue

            if self.appear(self.I_RESTART_AGAIN):
                for attempt in range(
                    1,
                    self.ACTION_ICON_MAX_ATTEMPTS + 1,
                ):
                    # 设备层的点击记录存的是 control_name 字符串（规则名），
                    # 传 RuleImage 对象什么也删不掉（实测 no-op）。
                    self.device.click_record_remove(self.I_RESTART_AGAIN.name)
                    self.click(self.I_RESTART_AGAIN)
                    if self._wait_chess_action_state(
                        lambda: not self.appear(self.I_RESTART_AGAIN)
                    ):
                        logger.info(
                            'Chess restart-again clicked from rank page: '
                            f'attempt={attempt}/'
                            f'{self.ACTION_ICON_MAX_ATTEMPTS}'
                        )
                        return True
                    logger.warning(
                        'Chess restart-again remained visible after click: '
                        f'attempt={attempt}/'
                        f'{self.ACTION_ICON_MAX_ATTEMPTS}'
                    )
                raise GameStuckError(
                    'Chess: failed to start next game from rank page '
                    'after 3 attempts'
                )

            if self.appear(self.I_CHECK_CHESS):
                logger.warning(
                    'Chess result flow reached lobby before restart-again; '
                    'start the next game from lobby instead'
                )
                return False

            # 已到排名页但按钮仍在动画中时只等待，不执行安全区点击，
            # 避免误触左侧“返回大厅”。
            if self.appear(self.I_CHECK_CHESS_RANK):
                time.sleep(self.CHESS_EXIT_SCREENSHOT_INTERVAL)
                continue

            if not exit_clicked:
                if self.appear(self.I_CHESS_EXIT_TO_LOBBY):
                    self.appear_then_click(
                        self.I_CHESS_EXIT_TO_LOBBY,
                        interval=1.5,
                    )
                    exit_clicked = True
                    time.sleep(self.CHESS_EXIT_SCREENSHOT_INTERVAL)
                    continue
                if self.appear(self.I_CHESS_EXIT_TO_LOBBY_2):
                    self.appear_then_click(
                        self.I_CHESS_EXIT_TO_LOBBY_2,
                        interval=1.5,
                    )
                    exit_clicked = True
                    time.sleep(self.CHESS_EXIT_SCREENSHOT_INTERVAL)
                    continue
                if self.appear(ChessAssets.I_SHARE):
                    exit_clicked = True
                    share_seen = True
                    continue
                if time.monotonic() >= fallback_exit_at:
                    logger.warning(
                        'Chess result flow missed return image before '
                        'restart; click fixed return area'
                    )
                    self.click(self.I_CHESS_EXIT_TO_LOBBY)
                    exit_clicked = True
                    time.sleep(self.CHESS_EXIT_SCREENSHOT_INTERVAL)
                    continue
                time.sleep(self.CHESS_EXIT_SCREENSHOT_INTERVAL)
                continue

            if not share_seen and self.appear(ChessAssets.I_SHARE):
                share_seen = True

            now = time.monotonic()
            if share_seen and now >= next_safe_click_at:
                from tasks.GameUi.default_pages import random_click
                self.click(random_click(), interval=1.5)
                safe_clicks += 1
                next_safe_click_at = now + 1.5
            time.sleep(self.CHESS_EXIT_SCREENSHOT_INTERVAL)

        raise GameStuckError(
            'Chess: restart-again did not appear within '
            f'{self.CHESS_EXIT_TIMEOUT:.0f}s, safe_clicks={safe_clicks}'
        )

    def _wait_for_round_start(self) -> int | None:
        """等待稳定回目数字；返回 None 表示本局已经结算。"""
        candidate = None
        confirmed = 0
        while True:
            self.device.stuck_record_clear()
            if self._refresh_round_state_screenshot():
                candidate = None
                confirmed = 0
                time.sleep(self.SLOW_POLL_INTERVAL)
                continue
            if self._finish_chess_game_after_markers_missing(
                'wait_for_round_start'
            ):
                return None

            round_no = self._read_round_number()
            if round_no is not None:
                if round_no == candidate:
                    confirmed += 1
                else:
                    candidate = round_no
                    confirmed = 1
                if confirmed >= self.ROUND_CONFIRM_FRAMES:
                    return round_no
            else:
                candidate = None
                confirmed = 0
            time.sleep(self.SLOW_POLL_INTERVAL)

    def _is_in_chess_game(self) -> bool:
        """以阵容入口、问号或特殊回合标志确认当前处于对局内。"""
        return (
            self.appear(self.I_OPEN_LINEUP)
            or self.appear(self.I_QUESTION_CHECK)
            or self._special_chess_mode() is not None
            or self._stage_marker_visible(self.I_STAGE_ORCHI)
            or self._stage_marker_visible(self.I_STAGE_DEMON)
        )

    def _close_chess_lobby_abnormal_page(self) -> bool:
        """关闭棋局大厅开战时偶发出现的红色返回键异常页面。"""
        if not self.appear(self.I_BACK_RED):
            return False
        logger.warning(
            'Chess start blocked by abnormal lobby page; '
            'close it with back red'
        )
        for attempt in range(1, self.ACTION_ICON_MAX_ATTEMPTS + 1):
            self.device.click_record_remove(self.I_BACK_RED.name)
            self.click(self.I_BACK_RED)
            if self._wait_chess_action_state(
                lambda: not self.appear(self.I_BACK_RED)
            ):
                logger.info(
                    'Chess abnormal lobby page closed; '
                    f'attempt={attempt}/{self.ACTION_ICON_MAX_ATTEMPTS}'
                )
                return True
            logger.warning(
                'Chess abnormal lobby page still visible after click: '
                f'attempt={attempt}/{self.ACTION_ICON_MAX_ATTEMPTS}, '
                f'timeout={self.ACTION_ICON_WAIT_TIMEOUT:.0f}s'
            )
        raise GameStuckError(
            'Chess: failed to close abnormal lobby page after 3 attempts'
        )

    def _wait_until_in_chess_game(
        self,
        timeout: float,
        retry_start: bool = False,
    ) -> None:
        """点击开始后依次等待匹配、加载和局内标志。"""
        deadline = time.monotonic() + timeout
        waiting_logged = False
        waiting_started_at = None
        waiting_seen = False
        matched_loading_started_at = None
        matched_loading_logged = False
        matchmaking_timeout = float(
            getattr(self, '_matchmaking_timeout_seconds', 60)
        )
        while True:
            # 匹配可能持续很久。截图本身会先检查设备卡死计时，因此必须
            # 在每次截图之前清除，不能等识别到“取消等待”后才清除。
            self.device.stuck_record_clear()
            self.screenshot()

            if self.select_chess_sixrealms_if_visible():
                deadline = time.monotonic() + timeout
                continue

            # 以阵容入口或问号图标确认真正进入棋局；商店按钮不再作为
            # 开局成功标志，避免大厅或匹配动画中的相似区域造成误判。
            if self._is_in_chess_game():
                logger.info('Chess matchmaking complete: entered battle')
                self._consecutive_matchmaking_timeouts = 0
                return

            # 极慢加载可能越过原有进入超时，并直接落到名次结算页。
            # 先完整返回大厅，再由本方法重新点击开始，避免下一次任务导航
            # 将该页当作未知页面。
            if self.chess_result_page_visible():
                logger.warning(
                    'Chess matched loading ended on result page before '
                    'in-game markers appeared; recover to lobby and retry'
                )
                self.return_to_chess_lobby()
                deadline = time.monotonic() + timeout
                waiting_logged = False
                waiting_started_at = None
                waiting_seen = False
                matched_loading_started_at = None
                matched_loading_logged = False
                continue

            if self.appear(self.I_CANCEL_WAITING):
                now = time.monotonic()
                waiting_seen = True
                matched_loading_started_at = None
                matched_loading_logged = False
                if waiting_started_at is None:
                    waiting_started_at = now
                if not waiting_logged:
                    logger.info(
                        'Chess matchmaking: waiting for other players; '
                        f'limit={matchmaking_timeout:.0f}s'
                    )
                    waiting_logged = True
                self.device.stuck_record_clear()
                if now - waiting_started_at >= matchmaking_timeout:
                    # 超时点必须使用新截图复核；标志自然消失说明已经匹配，
                    # 此时中断取消流程，转入加载等待。
                    self.device.stuck_record_clear()
                    self.screenshot()
                    if not self.appear(self.I_CANCEL_WAITING):
                        logger.info(
                            'Chess matchmaking completed while timeout exit '
                            'was being verified; abort cancellation'
                        )
                        waiting_started_at = None
                        waiting_logged = False
                        matched_loading_started_at = time.monotonic()
                        matched_loading_logged = False
                        time.sleep(self.SLOW_POLL_INTERVAL)
                        continue
                    if not self.appear_then_click(
                        self.I_CANCEL_WAITING,
                        interval=0,
                    ):
                        self.device.stuck_record_clear()
                        self.screenshot()
                        if self._is_in_chess_game():
                            logger.info(
                                'Chess entered battle while matchmaking exit '
                                'failed; abort cancellation'
                            )
                            self._consecutive_matchmaking_timeouts = 0
                            return
                        if (
                            not self.appear(self.I_CANCEL_WAITING)
                            and not self.appear(self.I_CHESS_START)
                        ):
                            logger.info(
                                'Chess matchmaking completed while exit '
                                'click failed; wait for battle loading'
                            )
                            waiting_started_at = None
                            waiting_logged = False
                            matched_loading_started_at = time.monotonic()
                            matched_loading_logged = False
                            time.sleep(self.SLOW_POLL_INTERVAL)
                            continue
                        if self.appear(self.I_CANCEL_WAITING):
                            time.sleep(self.SLOW_POLL_INTERVAL)
                            continue
                        logger.info(
                            'Chess matchmaking exit completed before click; '
                            'retry from lobby'
                        )

                    # 点击取消后再次确认实际结果。仍显示取消按钮表示退出失败；
                    # 未回大厅则是匹配成功与退出动作发生竞态。
                    time.sleep(self.ACTION_SETTLE_INTERVAL)
                    self.device.stuck_record_clear()
                    self.screenshot()
                    if self._is_in_chess_game():
                        logger.info(
                            'Chess entered battle during matchmaking exit; '
                            'abort cancellation'
                        )
                        self._consecutive_matchmaking_timeouts = 0
                        return
                    if self.appear(self.I_CANCEL_WAITING):
                        logger.warning(
                            'Chess matchmaking exit was not completed; retry'
                        )
                        continue
                    if not self.appear(self.I_CHESS_START):
                        logger.info(
                            'Chess matchmaking marker disappeared without '
                            'returning to lobby; treat as matched and wait '
                            'for battle loading'
                        )
                        waiting_started_at = None
                        waiting_logged = False
                        matched_loading_started_at = time.monotonic()
                        matched_loading_logged = False
                        time.sleep(self.SLOW_POLL_INTERVAL)
                        continue
                    self._consecutive_matchmaking_timeouts += 1
                    logger.warning(
                        'Chess matchmaking timeout: '
                        f'{self._consecutive_matchmaking_timeouts}/3, '
                        f'limit={matchmaking_timeout:.0f}s'
                    )
                    if self._consecutive_matchmaking_timeouts >= 3:
                        logger.warning('Chess matchmaking: 等待过多，结束任务')
                        self.set_next_run(
                            task='Chess',
                            success=False,
                            finish=True,
                        )
                        raise TaskEnd('Chess: 等待过多')
                    waiting_started_at = None
                    waiting_logged = False
                    waiting_seen = False
                    deadline = now + timeout
                    time.sleep(self.ACTION_SETTLE_INTERVAL)
                    continue
                time.sleep(self.SLOW_POLL_INTERVAL)
                continue

            waiting_started_at = None
            waiting_logged = False

            if (
                retry_start
                and self.appear(self.I_BACK_RED)
                and self._close_chess_lobby_abnormal_page()
            ):
                self.device.stuck_record_clear()
                deadline = time.monotonic() + timeout
                waiting_seen = False
                matched_loading_started_at = None
                matched_loading_logged = False
                continue

            if retry_start and self.appear(self.I_CHESS_START):
                waiting_seen = False
                matched_loading_started_at = None
                matched_loading_logged = False
                if self.appear_then_click(
                    self.I_CHESS_START,
                    interval=2 * self.SLOW_POLL_INTERVAL,
                ):
                    self.device.stuck_record_clear()
                    deadline = time.monotonic() + timeout

            # 只有确实见过取消匹配按钮、且它未经脚本取消便自然消失，
            # 才进入“已匹配加载”状态。此时不再使用 GAME_ENTER_TIMEOUT，
            # 防止加载中 100% 尚未切入棋局就被当作报错。
            if waiting_seen:
                now = time.monotonic()
                if matched_loading_started_at is None:
                    matched_loading_started_at = now
                    # 已自然离开等待队列，说明本轮匹配成功；此前的排队
                    # 超时不再属于连续超时。
                    self._consecutive_matchmaking_timeouts = 0
                if not matched_loading_logged:
                    logger.info(
                        'Chess matchmaking complete: waiting for battle '
                        'loading; '
                        f'limit={self.MATCHED_LOADING_TIMEOUT:.0f}s'
                    )
                    matched_loading_logged = True
                if (
                    now - matched_loading_started_at
                    >= self.MATCHED_LOADING_TIMEOUT
                ):
                    raise GameStuckError(
                        'Chess: matched successfully, but loading did not '
                        'reach battle or result page within '
                        f'{self.MATCHED_LOADING_TIMEOUT:.0f}s'
                    )
                time.sleep(self.SLOW_POLL_INTERVAL)
                continue

            if time.monotonic() >= deadline:
                raise GameStuckError(
                    'Chess: timeout waiting for in-game markers'
                )
            time.sleep(self.SLOW_POLL_INTERVAL)

    def _start_chess_game(self) -> None:
        """从棋局大厅开战，确认进入局内后直接开始回合流程。"""
        logger.debug('Chess game start')
        # 式神本局属性：(守护之印, 御魂1, 御魂2)。
        # 守护之印不占普通御魂槽，三个属性均允许为空。
        self._board_shikigami_attributes = {}
        self._board_lineup_names = set()
        self._board_actual_positions = {}
        self._player_deployed_positions = set()
        self._arakawa_goldfish_current_position = None
        self._board_special_units = {}
        # 过渡位登记只在本局有效：新局棋盘是空的，不能沿用上一局的淘汰名单。
        self._board_transition_names = set()
        self._last_game_rank = None
        # 等级缓存只在本局内有效：新局从一阶起，不能拿上一局的七阶当兜底。
        self._last_confirmed_level = None
        self._reset_economy_state()
        strategy = self.get_lineup_strategy()
        logger.debug(
            'Reset Chess per-game state: '
            f'lineup={strategy["key"]} ({strategy["display_name"]})'
        )
        self._wait_until_in_chess_game(
            timeout=self.GAME_ENTER_TIMEOUT,
            retry_start=True,
        )
        logger.debug(
            'Chess entered game; skip lineup preset and start round loop'
        )

    def _recover_interrupted_chess_game(self) -> bool:
        """仅在任务启动时清理上次脚本中断后遗留的棋局。"""
        self.screenshot()

        # 已在大厅时无需恢复。
        if self.appear(self.I_CHECK_CHESS):
            return False

        # 上次可能已经进入结算、分享或排名阶段，直接继续既有返回流程。
        if self.chess_result_flow_visible():
            logger.debug('Chess startup recovery: unfinished result flow detected')
            self.return_to_chess_lobby()
            return True

        # 启动恢复属于全局页面判断，只允许使用 c_open_lineup.png 或
        # c_question_check.png。
        # chess_mode OCR 仅供已确认在局内后的回目状态机使用，不能拿来
        # 判断全局页面，否则庭院等页面中的任意非空误识别都会触发退出。
        selected_sixrealms = self.select_chess_sixrealms_if_visible()
        if not selected_sixrealms and not self._is_in_chess_game():
            return False

        logger.warning(
            'Chess startup recovery: interrupted in-game state detected '
            'by I_OPEN_LINEUP or I_QUESTION_CHECK'
        )
        if not self.Chess_Exit():
            raise GameStuckError(
                'Chess: interrupted game detected but active exit was unavailable'
            )
        return True

    def _run_game_by_rounds_without_device_stuck_timeout(self) -> None:
        """单局协调器：逐个调用单回目函数，直至完成结算返回。"""
        round_no = self._wait_for_round_start()
        while round_no is not None:
            round_no = self.run_one_round(round_no)


class ScriptTask(ChessStateLoopMixin, ChessLegacyScriptTask):
    """正式 Chess 入口：状态驱动回合循环复用原有执行动作。"""

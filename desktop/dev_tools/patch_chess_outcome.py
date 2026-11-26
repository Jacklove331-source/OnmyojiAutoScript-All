# This Python file uses the following encoding: utf-8

"""给百鬼棋局加"对局结果状态"记录，区分完整结算/恢复/失败。

- 保留原 `run()` 接口（TaskEnd 语义不变），内部改为 `_run_impl()` + 包装。
- 每次结束写 `log/chess_outcome.jsonl` 并打印 `[ChessOutcome] status=...`，
  避免"Restart 恢复成功"被当成"完整对局成功"。
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / 'tasks/Chess/script_task.py'

METHODS = '''
    OUTCOME_SERIES_COMPLETED = 'series_completed'
    OUTCOME_RECOVERED_CONTINUE = 'recovered_continue'
    OUTCOME_RECOVERED_ABORTED = 'recovered_aborted'
    OUTCOME_FAILED = 'failed'
    OUTCOME_STOPPED = 'stopped'

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
        }
        logger.info(f'[ChessOutcome] status={status} reason={reason}')
        try:
            log_dir = Path('./log')
            log_dir.mkdir(parents=True, exist_ok=True)
            with (log_dir / 'chess_outcome.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(payload, ensure_ascii=False) + '\\n')
        except Exception:  # noqa: BLE001 - 记录失败不影响任务
            pass

    def run(self):
        """任务入口：包一层，把异常/中断归类成明确的对局状态。"""
        try:
            return self._run_impl()
        except TaskEnd:
            raise
        except GameNotRunningError:
            self._mark_outcome(self.OUTCOME_RECOVERED_ABORTED, 'game died / restart')
            raise
        except Exception as error:  # noqa: BLE001
            self._mark_outcome(self.OUTCOME_FAILED, repr(error))
            raise

    def _run_impl(self):
'''


def main() -> None:
    text = TARGET.read_text(encoding='utf-8')
    if '_mark_outcome' in text:
        print('already patched')
        return
    if 'GameNotRunningError' not in text:
        text = text.replace(
            'from module.exception import GameStuckError, TaskEnd',
            'from module.exception import GameNotRunningError, GameStuckError, TaskEnd',
            1,
        )
    text = text.replace('    def run(self):\n', METHODS, 1)
    # 循环正常结束（完成目标局数）→ 标记完整结算
    text = text.replace(
        "        raise TaskEnd('Chess')",
        "        self._mark_outcome(\n"
        "            self.OUTCOME_SERIES_COMPLETED if completed >= target_count else self.OUTCOME_FAILED,\n"
        "            f'completed={completed}/{target_count}')\n"
        "        raise TaskEnd('Chess')",
        1,
    )
    TARGET.write_text(text, encoding='utf-8')
    print('patched')


if __name__ == '__main__':
    main()

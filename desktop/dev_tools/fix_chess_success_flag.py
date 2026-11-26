# This Python file uses the following encoding: utf-8

"""修正上一次误插入的两行，并把 set_next_run 放到 _run_impl 结尾的正确位置。"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / 'tasks/Chess/script_task.py'

BAD = (
    "            if not coin_limit_reached:\n"
    "            self.set_next_run(task='Chess', success=success, finish=True)\n"
)
ANCHOR = "        status = self.OUTCOME_SERIES_COMPLETED if success else self.OUTCOME_FAILED\n"
INSERT = (
    "        if not coin_limit_reached:\n"
    "            self.set_next_run(task='Chess', success=success, finish=True)\n"
)


def main() -> None:
    text = TARGET.read_text(encoding='utf-8')
    if BAD in text:
        text = text.replace(BAD, '', 1)
        print('removed misplaced lines')
    if 'success=success, finish=True' in text:
        print('already has correct call')
    else:
        if ANCHOR not in text:
            print('anchor missing')
            return
        text = text.replace(ANCHOR, INSERT + ANCHOR, 1)
        print('inserted correct call')
    TARGET.write_text(text, encoding='utf-8')


if __name__ == '__main__':
    main()

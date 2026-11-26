# This Python file uses the following encoding: utf-8

"""把 set_next_run 的成功标记挪到 success 计算之后，避免 RESULT True 与 outcome 不一致。"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / 'tasks/Chess/script_task.py'
ANCHOR = "        self._mark_outcome(\n"
INSERT = (
    "        if not coin_limit_reached:\n"
    "            self.set_next_run(task='Chess', success=success, finish=True)\n"
)


def main() -> None:
    text = TARGET.read_text(encoding='utf-8')
    if 'success=success, finish=True' in text:
        print('already patched')
        return
    if ANCHOR not in text:
        print('anchor missing')
        return
    text = text.replace(ANCHOR, INSERT + ANCHOR, 1)
    TARGET.write_text(text, encoding='utf-8')
    print('patched')


if __name__ == '__main__':
    main()

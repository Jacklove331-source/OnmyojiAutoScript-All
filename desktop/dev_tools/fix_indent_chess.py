# This Python file uses the following encoding: utf-8

"""修复 except 块里被误缩进的两行。"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / 'tasks/Chess/script_task.py'

BAD = (
    "        self._mark_outcome(\n"
    "                self.OUTCOME_RECOVERED_ABORTED,\n"
    "                f'adb disconnected / restart: {error}',\n"
    "            )\n"
)
GOOD = (
    "            self._mark_outcome(\n"
    "                self.OUTCOME_RECOVERED_ABORTED,\n"
    "                f'adb disconnected / restart: {error}',\n"
    "            )\n"
)


def main() -> None:
    text = TARGET.read_text(encoding='utf-8')
    if BAD not in text:
        print('pattern not found')
        return
    TARGET.write_text(text.replace(BAD, GOOD, 1), encoding='utf-8')
    print('reindented')


if __name__ == '__main__':
    main()

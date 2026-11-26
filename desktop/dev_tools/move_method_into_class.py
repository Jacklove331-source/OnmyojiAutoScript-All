# This Python file uses the following encoding: utf-8

"""把误放进 __main__ 块的方法移回类体内（room_check_can_fire）。"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / 'tasks/Component/GeneralInvite/general_invite.py'
MARKER = '    def room_check_can_fire('
ANCHOR = "if __name__ == '__main__':"


def main() -> None:
    text = TARGET.read_text(encoding='utf-8')
    if MARKER not in text:
        print('method not found')
        return
    start = text.index(MARKER)
    block = text[start:].rstrip() + '\n'
    head = text[:start].rstrip() + '\n'
    anchor_index = head.index(ANCHOR)
    fixed = head[:anchor_index] + block + '\n' + head[anchor_index:]
    TARGET.write_text(fixed, encoding='utf-8')
    print('moved into class')


if __name__ == '__main__':
    main()

# 判断审计里"缺失素材"是否真的在活跃路径上：把缺失文件映射回规则常量，再查非注释引用。
import json
import re
import sys
from pathlib import Path

W = Path(r"E:\OASX-Android\work\oasx-deepseek-20261119\full-handoff-20261120-evening\workspace")
PY = W / "android" / "app" / "src" / "main" / "python"
audit = json.loads((W / "release-menu-audit.json").read_text(encoding="utf-8"))

RULE_RE = re.compile(r"^\s*(I_[A-Z0-9_]+)\s*=\s*Rule\w+\(")
FILE_RE = re.compile(r'file="\./(tasks/[^"]+)"')


def consts_for(task: str, rel: str) -> list[str]:
    assets = PY / "tasks" / task / "assets.py"
    if not assets.is_file():
        return []
    names, current = [], None
    for line in assets.read_text(encoding="utf-8", errors="replace").splitlines():
        m = RULE_RE.match(line)
        if m:
            current = m.group(1)
        f = FILE_RE.search(line)
        if f and current and f.group(1) == rel:
            names.append(current)
    return names


def usages(task: str, const: str):
    live, dead = [], []
    for py in (PY / "tasks" / task).rglob("*.py"):
        if py.name == "assets.py":
            continue
        for i, line in enumerate(py.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if re.search(rf"\b{const}\b", line):
                (dead if line.lstrip().startswith("#") else live).append(f"{py.relative_to(PY)}:{i}")
    return live, dead


rows = []
for row in audit["rows"]:
    for rel in row["missing_assets"]:
        task = row["task"]
        consts = consts_for(task, rel)
        for const in consts:
            live, dead = usages(task, const)
            rows.append({
                "task": task, "file": rel, "rule": const,
                "live_refs": live, "comment_refs": dead,
                "verdict": "ACTIVE" if live else ("commented-out" if dead else "unreferenced"),
            })

print(json.dumps(rows, ensure_ascii=False, indent=2))
live_only = [r for r in rows if r["verdict"] == "ACTIVE"]
print(f"\nSUMMARY rules={len(rows)} active={len(live_only)} "
      f"commented={sum(1 for r in rows if r['verdict'] == 'commented-out')} "
      f"unreferenced={sum(1 for r in rows if r['verdict'] == 'unreferenced')}")
for r in live_only:
    print(f"ACTIVE {r['task']} {r['rule']} -> {', '.join(r['live_refs'])}")

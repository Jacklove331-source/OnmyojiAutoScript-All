"""Static audit of the packaged task menu: import, config, and local assets."""
from __future__ import annotations

import importlib
import json
import re
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PY = ROOT / "android" / "app" / "src" / "main" / "python"
sys.path.insert(0, str(PY))
TASKS = PY / "tasks"
OUT = ROOT / "release-menu-audit.json"
INTERNAL = {"GotoMain"}
ASSET_RE = re.compile(r'(?:file|folder)="\./(tasks/[^"]+)"')


def local_assets(name: str) -> tuple[int, list[str]]:
    assets = TASKS / name / "assets.py"
    if not assets.is_file():
        return 0, []
    text = assets.read_text(encoding="utf-8", errors="replace")
    refs = sorted(set(ASSET_RE.findall(text)))
    missing = []
    for ref in refs:
        path = PY / ref
        if not path.exists():
            missing.append(ref)
    return len(refs), missing


def main() -> int:
    names = sorted(p.name for p in TASKS.iterdir()
                   if p.is_dir() and (p / "script_task.py").is_file() and p.name not in INTERNAL)
    rows = []
    for name in names:
        row = {"task": name, "has_config": (TASKS / name / "config.py").is_file(),
               "import_ok": False, "import_error": "", "asset_refs": 0, "missing_assets": []}
        try:
            importlib.import_module(f"tasks.{name}.script_task")
            row["import_ok"] = True
        except Exception as error:  # noqa: BLE001
            row["import_error"] = f"{type(error).__name__}: {error}"
            row["traceback_tail"] = traceback.format_exc().strip().splitlines()[-4:]
        refs, missing = local_assets(name)
        row["asset_refs"] = refs
        row["missing_assets"] = missing
        rows.append(row)
        print(f"{name}: import={row['import_ok']} config={row['has_config']} "
              f"assets={refs} missing={len(missing)} {row['import_error']}", flush=True)
    OUT.write_text(json.dumps({"count": len(rows), "rows": rows}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    ok = sum(1 for row in rows if row["import_ok"])
    print(f"SUMMARY total={len(rows)} import_ok={ok} import_fail={len(rows)-ok} "
          f"missing_asset_tasks={sum(1 for row in rows if row['missing_assets'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

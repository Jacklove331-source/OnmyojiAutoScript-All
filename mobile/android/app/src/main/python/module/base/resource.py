"""Resolve OAS static resources in both desktop and Chaquopy environments."""

from __future__ import annotations

from pathlib import Path


# module/base/resource.py -> module/base -> module -> Python package root
PACKAGE_ROOT = Path(__file__).resolve().parents[2]


def resolve_resource(value: str | Path) -> Path:
    """Return the first existing path from cwd and the packaged Python root."""
    path = Path(value)
    if path.is_absolute():
        return path
    candidates = (Path.cwd() / path, PACKAGE_ROOT / path)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[-1]

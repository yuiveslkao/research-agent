"""Minimal TOML writer for run specs (stdlib only reads TOML)."""
from __future__ import annotations

import json
import math
import re

_BARE = re.compile(r"^[A-Za-z0-9_-]+$")


def _key(k: str) -> str:
    return k if _BARE.match(k) else json.dumps(k)


def _val(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if not math.isfinite(v):
            raise ValueError("non-finite float in TOML")
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_val(x) for x in v) + "]"
    raise TypeError(f"unsupported TOML value: {type(v).__name__}")


def dumps(d: dict, _prefix: str = "") -> str:
    scalars = [(k, v) for k, v in d.items() if not isinstance(v, dict)]
    tables = [(k, v) for k, v in d.items() if isinstance(v, dict)]
    out = [f"{_key(k)} = {_val(v)}" for k, v in scalars]
    text = "\n".join(out) + ("\n" if out else "")
    for k, v in tables:
        name = f"{_prefix}.{_key(k)}" if _prefix else _key(k)
        text += f"\n[{name}]\n" + dumps(v, name)
    return text

"""RunSpec loading, validation and canonical hashing (schema run/0.1)."""
from __future__ import annotations

import hashlib
import json
import tomllib
from pathlib import Path

SCHEMA = "run/0.1"
KINDS = {"experiment", "check"}  # Phase 0: lit_lookup / review come with the ledger
MODES = {"exploration", "confirmation"}
ARITHMETIC = {"Float32", "Float64", "BigFloat", "Arb", "Interval", "Exact"}   # Exact = integers/rationals
DETERMINISM = {"bitwise", "tolerance"}  # "statistical" deferred
MAX_MEM_GB = 12  # 16 GB workstation, shared with the agent runtime and Docker


class SpecError(ValueError):
    pass


def load_spec(path: str | Path) -> tuple[dict, str]:
    """Return (parsed spec, original text)."""
    text = Path(path).read_text(encoding="utf-8")
    try:
        spec = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise SpecError(f"invalid TOML: {e}") from e
    validate(spec)
    return spec, text


def _need(table: dict, key: str, types: tuple, where: str):
    if key not in table:
        raise SpecError(f"missing {where}.{key}")
    v = table[key]
    if isinstance(v, bool) and bool not in types:
        raise SpecError(f"{where}.{key} has wrong type")
    if not isinstance(v, types):
        raise SpecError(f"{where}.{key} has wrong type")
    return v


def validate(spec: dict) -> None:
    if spec.get("schema") != SCHEMA:
        raise SpecError(f"schema must be {SCHEMA!r}")
    if _need(spec, "kind", (str,), "spec") not in KINDS:
        raise SpecError(f"kind must be one of {sorted(KINDS)}")
    mode = _need(spec, "mode", (str,), "spec")
    if mode not in MODES:
        raise SpecError(f"mode must be one of {sorted(MODES)}")
    _need(spec, "purpose", (str,), "spec")
    tests = spec.get("tests", [])
    if not isinstance(tests, list) or not all(isinstance(t, str) for t in tests):
        raise SpecError("tests must be a list of claim ids")
    if mode == "confirmation" and not tests:
        raise SpecError("confirmation runs must declare tests")

    code = _need(spec, "code", (dict,), "spec")
    _need(code, "commit", (str,), "code")
    cmd = code.get("cmd")
    if not isinstance(cmd, list) or not cmd or not all(isinstance(c, str) for c in cmd):
        raise SpecError("code.cmd must be a non-empty argv list of strings")
    if not isinstance(code.get("cwd", "."), str):
        raise SpecError("code.cwd must be a string")
    if "paths" in code:
        ps = code["paths"]
        if not isinstance(ps, list) or not ps or not all(isinstance(x, str) and x for x in ps):
            raise SpecError("code.paths must be a non-empty list of repository-relative paths")
        for x in ps:
            parts = x.replace("\\", "/").split("/")
            if x.startswith("/") or ".." in parts or x.startswith("-"):
                raise SpecError(f"code.paths entry {x!r} must be relative, without '..'")

    env = _need(spec, "env", (dict,), "spec")
    if _need(env, "kind", (str,), "env") != "julia-project":
        raise SpecError("env.kind must be 'julia-project' in Phase 0")
    _need(env, "project", (str,), "env")
    _need(env, "julia", (str,), "env")

    num = _need(spec, "numerics", (dict,), "spec")
    if _need(num, "arithmetic", (str,), "numerics") not in ARITHMETIC:
        raise SpecError(f"numerics.arithmetic must be one of {sorted(ARITHMETIC)}")
    det = _need(num, "determinism", (str,), "numerics")
    if det not in DETERMINISM:
        raise SpecError(f"numerics.determinism must be one of {sorted(DETERMINISM)}")
    if det == "tolerance":
        _need(num, "rel_tol", (int, float), "numerics")
    if num.get("device", "cpu") != "cpu":
        raise SpecError("Phase 0 supports device = 'cpu' only")

    res = _need(spec, "resources", (dict,), "spec")
    if _need(res, "threads", (int,), "resources") < 1:
        raise SpecError("resources.threads must be >= 1")
    mem = _need(res, "mem_gb", (int, float), "resources")
    if not 0 < mem <= MAX_MEM_GB:
        raise SpecError(f"resources.mem_gb must be in (0, {MAX_MEM_GB}]")
    if _need(res, "walltime_s", (int,), "resources") < 1:
        raise SpecError("resources.walltime_s must be >= 1")
    if res.get("gpu", False):
        raise SpecError("Phase 0 supports gpu = false only")


def canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def spec_hash(spec: dict) -> str:
    """sha256 of the canonical JSON of the parsed spec (comments/whitespace do not matter)."""
    return "sha256:" + hashlib.sha256(canonical_json(spec).encode("utf-8")).hexdigest()

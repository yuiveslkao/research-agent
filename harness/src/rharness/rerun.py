"""`rerun` check: execute the same spec again and compare per its determinism policy."""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext

from .executor import Executor
from .runner import Paths, load_result, submit_run
from .spec import load_spec


def _flatten(obj, prefix=""):
    if isinstance(obj, dict):
        for k in sorted(obj):
            yield from _flatten(obj[k], f"{prefix}{k}.")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _flatten(v, f"{prefix}{i}.")
    else:
        yield prefix.rstrip("."), obj


def _as_decimal(v):
    if isinstance(v, bool):
        return None
    try:
        return Decimal(str(v))
    except InvalidOperation:
        return None


def compare_metrics(a: dict, b: dict, rel_tol: float) -> list[str]:
    """Return a list of mismatch descriptions (empty means equal within tolerance)."""
    fa, fb = dict(_flatten(a)), dict(_flatten(b))
    problems = [f"key set differs: {sorted(set(fa) ^ set(fb))}"] if fa.keys() != fb.keys() else []
    tol = Decimal(str(rel_tol))
    with localcontext() as ctx:
        ctx.prec = 100
        for k in sorted(fa.keys() & fb.keys()):
            x, y = _as_decimal(fa[k]), _as_decimal(fb[k])
            if x is None or y is None:
                if fa[k] != fb[k]:
                    problems.append(f"{k}: {fa[k]!r} != {fb[k]!r}")
                continue
            scale = max(abs(x), abs(y))
            if scale != 0 and abs(x - y) / scale > tol:
                problems.append(f"{k}: {x} vs {y} exceeds rel_tol {rel_tol}")
    return problems


def _write_check(paths: Paths, record: dict) -> dict:
    out = paths.ledger / "checks"
    out.mkdir(parents=True, exist_ok=True)
    name = "K-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(3)
    record = {**record, "created": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    (out / f"{name}.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return {**record, "check_id": name}


def compare(run_a: str, run_b: str, rel_tol: float, paths: Paths) -> dict:
    """Compare reported metrics of two runs, e.g. the same study at 512 vs 1024 bits."""
    a, b = load_result(paths, run_a), load_result(paths, run_b)
    problems = [f"{r['run_id']} status is {r['status']}" for r in (a, b) if r["status"] != "completed"]
    if not problems:
        problems = compare_metrics((a["reported"] or {}).get("metrics", {}),
                                   (b["reported"] or {}).get("metrics", {}), rel_tol)
    return _write_check(paths, {"kind": "compare", "a": run_a, "b": run_b, "rel_tol": rel_tol,
                                "outcome": "pass" if not problems else "fail", "problems": problems})


def rerun(run_id: str, paths: Paths, executor: Executor) -> dict:
    spec_path = paths.ledger / "runs" / run_id / "spec.toml"
    spec, _ = load_spec(spec_path)
    new_id = submit_run(spec_path, paths, executor)
    a, b = load_result(paths, run_id), load_result(paths, new_id)

    policy = spec["numerics"]["determinism"]
    problems: list[str] = []
    if a["spec_hash"] != b["spec_hash"]:
        problems.append("spec_hash differs")  # cannot happen unless the ledger was tampered with
    for r in (a, b):
        if r["status"] != "completed":
            problems.append(f"{r['run_id']} status is {r['status']}")
    if not problems:
        if policy == "bitwise":
            oa = {o["path"]: o["sha256"] for o in a["harness"]["outputs"]}
            ob = {o["path"]: o["sha256"] for o in b["harness"]["outputs"]}
            for p in sorted(oa.keys() | ob.keys()):
                if oa.get(p) != ob.get(p):
                    problems.append(f"output differs: {p}")
        else:
            problems += compare_metrics((a["reported"] or {}).get("metrics", {}),
                                        (b["reported"] or {}).get("metrics", {}),
                                        spec["numerics"]["rel_tol"])

    return _write_check(paths, {
        "kind": "rerun", "original": run_id, "rerun": new_id, "policy": policy,
        "outcome": "pass" if not problems else "fail", "problems": problems,
    })

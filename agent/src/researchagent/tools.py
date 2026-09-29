"""Research tools. Plain functions (testable without MCP); mcp_server.py exposes them.

Trust boundary: agents can only (1) write Julia source under workspace/agent/{experiments,statements}
via the lab worktree, (2) start runs through the harness (Docker, no network, capped resources),
(3) read records, (4) write role-restricted notes. They cannot write run records, grades, checkers,
the library, or anything else.
"""
from __future__ import annotations

import json
import math
import re
import secrets
import tomllib
from decimal import Decimal, localcontext
from datetime import datetime, timezone
from pathlib import Path

from rharness.rerun import compare as _compare, rerun as _rerun
from rharness.runner import load_result, submit_run
from rharness.spec import load_spec
from rharness.store import get_path

from . import notes as notes_mod
from . import tomlw
from .config import MAX_MEM_GB, MAX_THREADS, MAX_WALLTIME_S, PRECISION_RANGE, Ctx

RUN_ID = re.compile(r"^R-[0-9]{8}T[0-9]{6}-[0-9a-f]{6}$")
IDENT = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,40}$")
MAX_SOURCE = 60_000


class ToolError(ValueError):
    """Bad request from the agent; the message is shown to it."""


_NUM = re.compile(r"(?<![\w.])[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?(?![\w])")
SHOW_DIGITS = 20


def _shorten_token(m: re.Match, digits: int = SHOW_DIGITS) -> str:
    tok = m.group(0)
    mant = re.split(r"[eE]", tok)[0]
    if len(re.sub(r"\D", "", mant).lstrip("0")) <= digits + 5:
        return tok
    with localcontext() as ctx:
        ctx.prec = max(len(tok), 50)
        return f"{Decimal(tok):.{digits - 1}E}"


def shorten_text(text: str, digits: int = SHOW_DIGITS) -> str:
    """Round every numeric token with many more than `digits` significant digits (agent-facing views only)."""
    return _NUM.sub(lambda m: _shorten_token(m, digits), text)


def shorten_numbers(obj, digits: int = SHOW_DIGITS):
    if isinstance(obj, str):
        return shorten_text(obj, digits)
    if isinstance(obj, dict):
        return {k: shorten_numbers(v, digits) for k, v in obj.items()}
    if isinstance(obj, list):
        return [shorten_numbers(v, digits) for v in obj]
    return obj


def _fit(obj, n: int):
    """Shrink obj to about n JSON characters by dropping whole entries; never cuts inside a number or key."""
    if len(json.dumps(obj, ensure_ascii=False)) <= n:
        return obj, 0
    if isinstance(obj, dict):
        out, omitted, used = {}, 0, 2
        for k, v in obj.items():
            whole = len(json.dumps({k: v}, ensure_ascii=False))
            if used + whole <= n:
                out[k], used = v, used + whole
            elif isinstance(v, (dict, list)) and n - used > 200:
                sub, om = _fit(v, n - used - len(json.dumps(k)) - 10)
                out[k], omitted, used = sub, omitted + om, n
            else:
                omitted, used = omitted + 1, n
        return out, omitted
    if isinstance(obj, list):
        out, omitted, used = [], 0, 2
        for v in obj:
            whole = len(json.dumps(v, ensure_ascii=False)) + 2
            if used + whole <= n:
                out.append(v)
                used += whole
            else:
                omitted, used = omitted + 1, n
        return out, omitted
    if isinstance(obj, str):
        return obj[:n] + "...[cut]", 1
    return obj, 0


def _clip(obj, n: int):
    shrunk, omitted = _fit(obj, n)
    if not omitted:
        return obj
    return {"_partial": True, "omitted_entries": omitted, "data": shrunk,
            "hint": "this view drops whole entries only; read_artifact(run_id, 'out/reported.json') has everything"}


def _tail(path: Path, n: int) -> str:
    text = path.read_text(errors="replace")
    return text if len(text) <= n else "...[truncated]\n" + text[-n:]


def _check_run_id(run_id: str) -> str:
    if not isinstance(run_id, str) or not RUN_ID.match(run_id):
        raise ToolError(f"invalid run id {run_id!r}")
    return run_id


def _load(ctx: Ctx, run_id: str) -> tuple[dict, dict]:
    _check_run_id(run_id)
    d = ctx.paths.ledger / "runs" / run_id
    if not (d / "result.json").exists():
        raise ToolError(f"unknown run {run_id}")
    spec, _ = load_spec(d / "spec.toml")
    return spec, load_result(ctx.paths, run_id)


def _audit_records(ledger: Path, campaign: str) -> list[dict]:
    f = ledger / "audit.jsonl"
    if not f.exists():
        return []
    recs = [json.loads(line) for line in f.read_text().splitlines() if line.strip()]
    return [r for r in recs if r["campaign"] == campaign]


def role_limit(role: str, max_runs: int) -> int:
    """The critic keeps ~40% of the campaign budget in reserve; the researcher cannot spend it."""
    reserve = math.ceil(0.4 * max_runs) if max_runs >= 2 else 0
    return max_runs if role == "critic" else max_runs - reserve


MAX_FAILED_RUNS = 3   # per role and campaign; failed runs are refunded from the run budget but capped


def _check_budget(ctx: Ctx) -> None:
    if ctx.settings.role == "human":
        return
    c, role = ctx.campaign, ctx.settings.role
    recs = _audit_records(ctx.paths.ledger, c["id"])
    done = [r for r in recs if r.get("status", "completed") == "completed"]
    failed = sum(1 for r in recs if r["role"] == role and r.get("status", "completed") != "completed")
    if failed >= MAX_FAILED_RUNS:
        raise ToolError(f"{failed} of your runs failed in this campaign; stop retrying. Report the blocker "
                        "(missing package, bug you cannot fix) in your finding or critique.")
    mine, limit = sum(1 for r in done if r["role"] == role), role_limit(role, c["max_runs"])
    if len(done) >= c["max_runs"] or (role != "critic" and len(done) >= limit):
        raise ToolError(f"run budget exhausted for campaign {c['id']!r}: {len(done)}/{c['max_runs']} completed runs "
                        f"({mine} by you; your share is {limit}, the rest is reserved for the critic; failed runs "
                        "are not counted). Do not start more runs; record your best conclusion.")


def _require_role(ctx: Ctx, allowed: set[str]) -> None:
    if ctx.settings.role not in allowed:
        raise ToolError(f"role {ctx.settings.role!r} may not use this tool")


def _run_campaign(ctx: Ctx, run_id: str) -> str | None:
    if not isinstance(run_id, str) or not RUN_ID.match(run_id):
        return None
    f = ctx.paths.ledger / "runs" / run_id / "spec.toml"
    return tomllib.loads(f.read_text()).get("meta", {}).get("campaign") if f.exists() else None


def _require_hypothesis(ctx: Ctx) -> None:
    """Pre-registration: the researcher must have written a hypothesis note since its last finding."""
    if ctx.settings.role != "researcher":
        return
    ns = notes_mod.read_notes(ctx.paths.ledger, ctx.campaign["id"], limit=100_000)
    last_finding = max((i for i, n in enumerate(ns) if n["kind"] == "finding" and n["role"] == "researcher"),
                       default=-1)
    if not any(n["kind"] == "hypothesis" and n["role"] == "researcher" for n in ns[last_finding + 1:]):
        raise ToolError("Pre-register first: call add_note(kind='hypothesis') stating what you expect and what "
                        "result would refute it, then submit. One hypothesis covers your runs (retries, reruns, "
                        "precision checks) until your next finding.")


def run_summary(ctx: Ctx, run_id: str) -> dict:
    spec, res = _load(ctx, run_id)
    meta = spec.get("meta", {})
    h = res["harness"]
    out = {
        "run_id": run_id, "kind": spec["kind"], "status": res["status"],
        "title": meta.get("title"), "hypothesis": meta.get("hypothesis"), "role": meta.get("role"),
        "campaign": meta.get("campaign"),
        "params": spec.get("params", {}),
        "numerics": {k: spec["numerics"].get(k) for k in ("arithmetic", "precision_bits", "determinism")},
        "wall_s": h["wall_s"], "exit_code": h["exit_code"], "code_commit": h["code_commit"][:12],
        "outputs": [{"path": o["path"], "bytes": o["bytes"]} for o in h["outputs"]],
        "reported": _clip(shorten_numbers(res["reported"]), 2000),
    }
    if json.dumps(shorten_numbers(res["reported"])) != json.dumps(res["reported"]):
        out["numbers_shortened"] = (f"reals shown to {SHOW_DIGITS} significant digits to save tokens; full digits: "
                                    "read_artifact(run_id, 'out/reported.json', full_precision=true)")
    err = get_path(ctx.paths.store, h["stderr"])
    if err.exists() and err.stat().st_size:
        out["stderr_tail"] = _tail(err, 1500)
    return out


def _base_spec(ctx: Ctx, *, kind: str, purpose: str, commit: str, cmd: list[str], meta: dict,
               precision_bits: int, walltime_s: int, threads: int, mem_gb: float, seed: int,
               determinism: str, rel_tol: float | None, params: dict) -> dict:
    lo, hi = PRECISION_RANGE
    if not lo <= precision_bits <= hi:
        raise ToolError(f"precision_bits must be in [{lo}, {hi}]")
    if not 1 <= walltime_s <= MAX_WALLTIME_S:
        raise ToolError(f"walltime_s must be in [1, {MAX_WALLTIME_S}]")
    if not 1 <= threads <= MAX_THREADS:
        raise ToolError(f"threads must be in [1, {MAX_THREADS}]")
    if not 0 < mem_gb <= MAX_MEM_GB:
        raise ToolError(f"mem_gb must be in (0, {MAX_MEM_GB}]")
    if determinism not in ("bitwise", "tolerance"):
        raise ToolError("determinism must be 'bitwise' or 'tolerance'")
    if determinism == "tolerance" and not rel_tol:
        raise ToolError("determinism='tolerance' needs rel_tol")
    num = {"arithmetic": "BigFloat", "precision_bits": precision_bits, "rng": "Xoshiro",
           "seeds": [seed], "determinism": determinism, "device": "cpu"}
    if rel_tol:
        num["rel_tol"] = float(rel_tol)
    return {
        "schema": "run/0.1", "kind": kind, "mode": "exploration", "purpose": purpose,
        "meta": meta,
        "code": {"commit": commit, "cwd": "workspace", "cmd": cmd},
        "env": {"kind": "julia-project", "project": "envs/main", "julia": ctx.settings.julia_version},
        "numerics": num,
        "resources": {"threads": threads, "mem_gb": float(mem_gb), "gpu": False, "walltime_s": walltime_s},
        "params": params,
    }


def _run(ctx: Ctx, spec: dict, tag: str) -> str:
    d = ctx.settings.root / "work" / "specs"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{tag}.toml"
    try:
        p.write_text(tomlw.dumps(spec), encoding="utf-8")
    except (TypeError, ValueError) as e:
        raise ToolError(f"params cannot be written as TOML: {e}") from e
    return submit_run(p, ctx.paths, ctx.executor)


def _audit(ctx: Ctx, tool: str, run_id: str) -> None:
    """Append-only log of who started which run (a rerun copies the original spec, so meta can't say)."""
    d = ctx.paths.ledger
    d.mkdir(parents=True, exist_ok=True)
    rec = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "role": ctx.settings.role,
           "campaign": ctx.campaign["id"], "tool": tool, "run_id": run_id,
           "status": load_result(ctx.paths, run_id)["status"]}
    with open(d / "audit.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


def attack_count(ledger: Path, campaign: str, role: str) -> int:
    """Runs/checks/reruns a role started in a campaign (used by the driver to police the critic)."""
    return sum(1 for r in _audit_records(ledger, campaign) if r["role"] == role)


def _new_tag(prefix: str) -> str:
    return f"{prefix}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{secrets.token_hex(3)}"


# ---------------------------------------------------------------- experiments and checks

def _script_of(ctx: Ctx, run_id: str) -> str:
    spec, res = _load(ctx, run_id)
    rel = spec.get("meta", {}).get("script")
    if not rel or spec["kind"] != "experiment":
        raise ToolError(f"{run_id} has no experiment script to reuse")
    return ctx.lab.show(res["harness"]["code_commit"], rel)


def apply_edits(script: str, edits: list[dict]) -> str:
    for i, e in enumerate(edits):
        old, new = e.get("old"), e.get("new")
        if not isinstance(old, str) or not isinstance(new, str) or not old:
            raise ToolError(f"edit {i}: needs non-empty string 'old' and string 'new'")
        n = script.count(old)
        if n != 1:
            raise ToolError(f"edit {i}: 'old' must occur exactly once in the base script (found {n})")
        script = script.replace(old, new)
    return script


def submit_experiment(ctx: Ctx, title: str, hypothesis: str, script: str = "", params: dict | None = None,
                      precision_bits: int = 256, walltime_s: int = 600, threads: int = 1,
                      mem_gb: float = 4, seed: int = 1, determinism: str = "bitwise",
                      rel_tol: float | None = None, base_run: str | None = None,
                      edits: list[dict] | None = None) -> dict:
    _require_role(ctx, {"researcher", "critic", "human"})
    _require_hypothesis(ctx)
    if not title.strip() or len(title) > 100:
        raise ToolError("title must be 1..100 characters")
    if base_run:
        if script.strip():
            raise ToolError("give either `script` or `base_run`, not both")
        script = apply_edits(_script_of(ctx, base_run), edits or [])
        params = {**_load(ctx, base_run)[0].get("params", {}), **(params or {})}   # inherit, then override
    elif edits:
        raise ToolError("`edits` needs `base_run`")
    if not script.strip() or len(script) > MAX_SOURCE:
        raise ToolError(f"script must be 1..{MAX_SOURCE} characters")
    _check_budget(ctx)
    tag = _new_tag("E")
    rel = f"workspace/agent/experiments/{tag}.jl"
    commit = ctx.lab.commit_file(rel, script, f"experiment {tag}: {title[:60]} [{ctx.settings.role}]")
    meta = {"role": ctx.settings.role, "campaign": ctx.campaign["id"], "title": title,
            "hypothesis": hypothesis, "script": rel}
    spec = _base_spec(
        ctx, kind="experiment", purpose=title, commit=commit,
        cmd=[ctx.settings.julia_cmd, "--project=envs/main", rel.removeprefix("workspace/")],
        meta=meta, precision_bits=precision_bits, walltime_s=walltime_s, threads=threads,
        mem_gb=mem_gb, seed=seed, determinism=determinism, rel_tol=rel_tol, params=params or {})
    run_id = _run(ctx, spec, tag)
    _audit(ctx, "submit_experiment", run_id)
    return run_summary(ctx, run_id)


def submit_check(ctx: Ctx, kind: str, name: str, module: str, statement_source: str,
                 precision_bits: int = 256, evals: int = 1000, starts: int = 6, grid: int = 3,
                 walltime_s: int = 900, seed: int = 1) -> dict:
    _require_role(ctx, {"researcher", "critic", "human"})
    _require_hypothesis(ctx)
    if kind not in ("highprec", "falsify"):
        raise ToolError("kind must be 'highprec' or 'falsify'")
    if not IDENT.match(name) or not IDENT.match(module):
        raise ToolError("name and module must match [A-Za-z][A-Za-z0-9_]{0,40}")
    if not re.search(rf"^\s*module\s+{re.escape(module)}\b", statement_source, re.M):
        raise ToolError(f"statement_source must define `module {module}`")
    if len(statement_source) > MAX_SOURCE:
        raise ToolError("statement_source too long")
    _check_budget(ctx)
    tag = _new_tag("S")
    rel = f"workspace/agent/statements/{name}_{tag}.jl"
    commit = ctx.lab.commit_file(rel, statement_source, f"statement {name} {kind} [{ctx.settings.role}]")
    params = {"statement": rel.removeprefix("workspace/"), "module": module}
    params.update({"evals": evals, "starts": starts} if kind == "falsify" else {"grid": grid})
    meta = {"role": ctx.settings.role, "campaign": ctx.campaign["id"],
            "title": f"{kind} {name}", "hypothesis": f"statement {module} holds on its DOMAIN",
            "script": rel}
    spec = _base_spec(
        ctx, kind="check", purpose=f"{kind} check of {module}", commit=commit,
        cmd=[ctx.settings.julia_cmd, "--project=envs/main", f"../checkers/{kind}.jl"],
        meta=meta, precision_bits=precision_bits, walltime_s=walltime_s, threads=1, mem_gb=4,
        seed=seed, determinism="bitwise", rel_tol=None, params=params)
    run_id = _run(ctx, spec, tag)
    _audit(ctx, "submit_check", run_id)
    return run_summary(ctx, run_id)


# ---------------------------------------------------------------- inspection

def inspect_run(ctx: Ctx, run_id: str) -> dict:
    return run_summary(ctx, run_id)


def read_artifact(ctx: Ctx, run_id: str, path: str, max_bytes: int = 8000, tail: bool = False,
                  full_precision: bool = False) -> dict:
    """path: an output path from inspect_run, or 'stdout', 'stderr', 'script'."""
    spec, res = _load(ctx, run_id)
    max_bytes = max(1, min(int(max_bytes), 100_000))
    h = res["harness"]
    if path in ("stdout", "stderr"):
        text = get_path(ctx.paths.store, h[path]).read_text(errors="replace")
    elif path == "script":
        rel = spec.get("meta", {}).get("script")
        if not rel:
            raise ToolError("this run has no agent-written script")
        text = ctx.lab.show(h["code_commit"], rel)
    else:
        match = [o for o in h["outputs"] if o["path"] == path]
        if not match:
            raise ToolError(f"no such output {path!r}; available: {[o['path'] for o in h['outputs']]}")
        text = get_path(ctx.paths.store, "sha256:" + match[0]["sha256"]).read_text(errors="replace")
    if not full_precision and path not in ("script", "stdout", "stderr"):
        text = shorten_text(text)
    total = len(text)
    text = text[-max_bytes:] if tail else text[:max_bytes]
    out = {"run_id": run_id, "path": path, "total_chars": total, "truncated": total > max_bytes, "text": text}
    if not full_precision and path not in ("script", "stdout", "stderr"):
        out["digits"] = f"reals rounded to {SHOW_DIGITS} significant digits; pass full_precision=true for all digits"
    return out


def list_runs(ctx: Ctx, limit: int = 20, campaign_only: bool = True) -> list[dict]:
    cid = ctx.campaign["id"]
    rows = []
    for d in sorted((ctx.paths.ledger / "runs").glob("R-*")):
        if not (d / "result.json").exists():
            continue
        spec, _ = load_spec(d / "spec.toml")
        meta = spec.get("meta", {})
        if campaign_only and meta.get("campaign") != cid:
            continue
        res = load_result(ctx.paths, d.name)
        rows.append({"run_id": d.name, "kind": spec["kind"], "status": res["status"],
                     "role": meta.get("role"), "title": meta.get("title"),
                     "hypothesis": (meta.get("hypothesis") or "")[:120],
                     "wall_s": res["harness"]["wall_s"]})
    return rows[-max(1, min(int(limit), 100)):]


def rerun_run(ctx: Ctx, run_id: str) -> dict:
    _require_role(ctx, {"researcher", "critic", "human"})
    _load(ctx, run_id)
    _check_budget(ctx)
    rec = _rerun(run_id, ctx.paths, ctx.executor)
    _audit(ctx, "rerun_run", rec["rerun"])
    return {**rec, "rerun_summary": run_summary(ctx, rec["rerun"])}


def compare_runs(ctx: Ctx, run_a: str, run_b: str, rel_tol: float = 1e-20) -> dict:
    _load(ctx, run_a)
    _load(ctx, run_b)
    if not 0 < rel_tol < 1:
        raise ToolError("rel_tol must be in (0, 1)")
    return _compare(run_a, run_b, rel_tol, ctx.paths)


# ---------------------------------------------------------------- notes

def add_note(ctx: Ctx, kind: str, text: str, status: str | None = None,
             run_ids: list[str] | None = None) -> dict:
    run_ids = list(run_ids or [])
    if kind in notes_mod.KINDS and kind not in notes_mod.ROLE_KINDS.get(ctx.settings.role, set()):
        raise ToolError(f"role {ctx.settings.role!r} may not write {kind!r} notes "
                        f"(allowed: {sorted(notes_mod.ROLE_KINDS.get(ctx.settings.role, set()))})")
    if kind == "finding" and not run_ids:
        raise ToolError("a finding must cite at least one run in run_ids; run an experiment first")
    if kind == "critique":
        cid = ctx.campaign["id"]
        if not any(_run_campaign(ctx, r) == cid for r in run_ids):
            raise ToolError(f"a critique must cite at least one run from this campaign ({cid}) in run_ids: "
                            "the run you examined or the attack you started")
    try:
        n = notes_mod.add_note(ctx.paths.ledger, ctx.settings.role, ctx.campaign["id"], kind, text,
                               status, run_ids)
    except notes_mod.NoteError as e:
        raise ToolError(str(e)) from e
    return {"id": n["id"], "kind": kind, "status": status}


def read_notes(ctx: Ctx, kind: str | None = None, limit: int = 30) -> list[dict]:
    ns = notes_mod.read_notes(ctx.paths.ledger, ctx.campaign["id"], kind, max(1, min(int(limit), 100)))
    return [{k: n[k] for k in ("id", "created", "role", "kind", "status", "text", "run_ids")} for n in ns]

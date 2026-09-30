"""Evaluation: golden questions, scoring, runner and regression summaries (research-eval).

A golden question is a TOML file (eval/questions/*.toml, or a private directory for hold-out questions) with the
question text given to the agent, a known answer for humans, a budget, and checks. Checks are scored automatically
from the campaign's end-of-run report.json and the ledger, or by a human (`research-eval grade`). Automatic results
can be overridden by a human grade. Scoring uses the claims' *derived* status (what the agents produced), not later
human overrides.

Evaluation campaigns have ids starting with EV- and are sealed (config.isolated): the agents see only their own
records, never earlier campaigns or other repetitions, and agent runs cannot read eval/ (spec code.paths).

Records: ledger/eval/records/E-*.json (one per repetition, written once), ledger/eval/grades.jsonl (append-only).
Design and policy: docs/evaluation.md.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from . import usage
from .config import EVAL_PREFIX
from .glossary import STATUSES
from .install_profiles import ROLES

EVAL_SCHEMA = "eval/0.1"
QUESTION_TYPES = {"rate-estimation", "planted-flaw", "refute-false-statement", "method-comparison",
                  "point-configuration", "conformal-map", "proof-required", "mixed-verification", "out-of-scope"}
SPLITS = {"dev", "holdout"}
VERIFICATION = {"verified", "partly-verified", "campaign", "unverified"}
CHECK_KINDS = {"claim", "no_claim", "scope", "critic_runs", "checks_run", "attempts", "stopped_by", "human", "novelty"}
WHEN = {"always", "proven_AI", "no_proven_AI"}
PROVEN = {"proven_AI", "proven"}


class EvalError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------------ questions

def validate_question(q: dict, where: str = "question") -> None:
    def need(k, t):
        if not isinstance(q.get(k), t):
            raise EvalError(f"{where}: `{k}` is required ({t.__name__ if isinstance(t, type) else t})")
    for k in ("id", "title", "type", "split", "verification", "question", "known_answer"):
        need(k, str)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,40}", q["id"]):
        raise EvalError(f"{where}: bad id {q['id']!r}")
    for k, allowed in (("type", QUESTION_TYPES), ("split", SPLITS), ("verification", VERIFICATION)):
        if q[k] not in allowed:
            raise EvalError(f"{where}: {k} must be one of {sorted(allowed)}")
    b = q.get("budget")
    if not isinstance(b, dict) or not all(isinstance(b.get(k), int) and b[k] > 0 for k in ("rounds", "max_runs")):
        raise EvalError(f"{where}: [budget] needs positive integers rounds and max_runs")
    checks = q.get("check")
    if not isinstance(checks, list) or not checks:
        raise EvalError(f"{where}: at least one [[check]] is required")
    ids = set()
    for c in checks:
        cid = c.get("id")
        if not isinstance(cid, str) or not cid or cid in ids or cid.startswith("_"):
            raise EvalError(f"{where}: every check needs a unique id (not starting with _), got {cid!r}")
        ids.add(cid)
        if c.get("kind") not in CHECK_KINDS:
            raise EvalError(f"{where}: check {cid}: kind must be one of {sorted(CHECK_KINDS)}")
        if not isinstance(c.get("what"), str) or not c["what"]:
            raise EvalError(f"{where}: check {cid}: `what` (one line for the grader) is required")
        if c.get("when", "always") not in WHEN:
            raise EvalError(f"{where}: check {cid}: when must be one of {sorted(WHEN)}")
        for k in ("match", "forbid"):
            for rx in c.get(k, []):
                try:
                    re.compile(rx)
                except re.error as e:
                    raise EvalError(f"{where}: check {cid}: bad regex {rx!r}: {e}") from e
        bad = set(c.get("status", [])) - set(STATUSES)
        if bad:
            raise EvalError(f"{where}: check {cid}: unknown statuses {sorted(bad)}")
        if c["kind"] in ("claim", "no_claim", "scope") and not c.get("status"):
            raise EvalError(f"{where}: check {cid}: `status` (a list) is required for kind {c['kind']}")


def load_question(path: Path) -> dict:
    raw = Path(path).read_bytes()
    try:
        q = tomllib.loads(raw.decode("utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise EvalError(f"{path}: invalid TOML: {e}") from e
    validate_question(q, str(path))
    q["_sha256"] = hashlib.sha256(raw).hexdigest()
    q["_path"] = str(path)
    return q


def question_dirs(root: Path, extra: list[str] | None = None) -> list[Path]:
    return [root / "eval" / "questions"] + [Path(x) for x in extra or []]


def find_questions(dirs: list[Path]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for d in dirs:
        for p in sorted(Path(d).glob("*.toml")):
            q = load_question(p)
            if q["id"] in out:
                raise EvalError(f"duplicate question id {q['id']} in {p} and {out[q['id']]['_path']}")
            out[q["id"]] = q
    return out


# ------------------------------------------------------------------ configuration (what is being evaluated)

def _sha(p: Path) -> str | None:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16] if p.exists() else None


def capture_config(root: Path, hermes_home: Path | None = None) -> dict:
    """Model per role, prompt (SOUL.md) versions, code commit: everything a regression comparison groups by."""
    import yaml
    home = Path(hermes_home or os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
    git = lambda *a: subprocess.run(["git", "-C", str(root), *a], capture_output=True, text=True).stdout.strip()
    profiles_src = Path(__file__).parent / "profiles"
    roles = {}
    for role in ROLES:
        pdir = home / "profiles" / role
        model = None
        try:
            cfg = yaml.safe_load((pdir / "config.yaml").read_text()) or {}
            m = cfg.get("model")
            model = m.get("default") or m.get("name") if isinstance(m, dict) else m
        except (OSError, yaml.YAMLError):
            pass
        repo_soul, installed = _sha(profiles_src / f"{role}_SOUL.md"), _sha(pdir / "SOUL.md")
        roles[role] = {"model": model, "soul": repo_soul, "installed_soul": installed,
                       "soul_matches_repo": installed == repo_soul if installed else None}
    commit = git("rev-parse", "HEAD") or None
    dirty = bool(git("status", "--porcelain", "--", "agent", "harness", "checkers", "workspace"))
    key = json.dumps({"roles": {r: [v["model"], v["installed_soul"] or v["soul"]] for r, v in roles.items()},
                      "commit": commit, "dirty": dirty}, sort_keys=True)
    return {"roles": roles, "commit": commit, "dirty": dirty,
            "config_id": hashlib.sha256(key.encode()).hexdigest()[:12]}


# ------------------------------------------------------------------ scoring

def claim_text(c: dict, scope_only: bool = False) -> str:
    sa = c.get("supported_as") or {}
    parts = [c.get("scope"), sa.get("scope"), sa.get("limits")] if scope_only else \
        [c.get("statement"), c.get("scope"), sa.get("statement"), sa.get("scope"), sa.get("limits")]
    return " ".join(" ".join(str(p).split()) for p in parts if p)


def _matches(text: str, rxs: list[str], forbid: list[str] = ()) -> bool:
    """All `rxs` match and no `forbid` pattern does (case-insensitive)."""
    return all(re.search(rx, text, re.I) for rx in rxs) and not any(re.search(rx, text, re.I) for rx in forbid)


def _audit(ledger: Path, cid: str) -> list[dict]:
    f = ledger / "audit.jsonl"
    rows = [json.loads(x) for x in f.read_text().splitlines() if x.strip()] if f.exists() else []
    return [r for r in rows if r.get("campaign") == cid]


def evaluate_check(c: dict, rep: dict, ledger: Path, cid: str) -> tuple[str, str]:
    """(result, detail) with result pass | fail | pending | n/a."""
    claims = rep.get("claims", [])
    status = lambda x: x["status"]["derived"]
    any_proof = any(status(x) in PROVEN for x in claims)
    when = c.get("when", "always")
    if (when == "proven_AI" and not any_proof) or (when == "no_proven_AI" and any_proof):
        return "n/a", f"not applicable (when={when})"
    kind, want = c["kind"], set(c.get("status", []))
    if kind == "claim":
        hits = [x for x in claims if status(x) in want and _matches(claim_text(x), c.get("match", []), c.get("forbid", []))
                and (not c.get("counterexample") or any(ce.get("named") for ce in x["evidence"]["counterexamples"]))]
        n = c.get("min", 1)
        return ("pass" if len(hits) >= n else "fail"), f"{len(hits)} matching claim(s) with status in {sorted(want)}" + \
            (f": {', '.join(h['id'] for h in hits[:3])}" if hits else "")
    if kind == "no_claim":
        hits = [x for x in claims if status(x) in want and _matches(claim_text(x), c.get("match", []), c.get("forbid", []))]
        return ("fail" if hits else "pass"), (f"offending: {', '.join(h['id'] + ' [' + status(h) + ']' for h in hits[:3])}"
                                               if hits else "none")
    if kind == "scope":
        bad = [x for x in claims if status(x) in want
               and not _matches(claim_text(x, True), c.get("match", []), c.get("forbid", []))]
        return ("fail" if bad else "pass"), (f"scope not as required: {', '.join(b['id'] for b in bad[:3])}"
                                             if bad else f"{sum(status(x) in want for x in claims)} claim(s) checked")
    if kind == "critic_runs":
        n = sum(1 for r in _audit(ledger, cid) if r["role"] == "critic")
        return ("pass" if n >= c.get("min", 1) else "fail"), f"critic started {n} run(s)"
    if kind == "checks_run":
        want_kind = c.get("checker", "falsify")
        n = sum(1 for r in rep.get("runs", []) if r.get("checker") == want_kind and r.get("status") == "completed")
        return ("pass" if n >= c.get("min", 1) else "fail"), f"{n} completed {want_kind} check(s)"
    if kind == "attempts":
        atts = [a for x in claims for a in x["attempts"]] + rep.get("attempts", [])
        atts = [a for a in atts if not c.get("stage") or a["stage"] == c["stage"]]
        return ("pass" if len(atts) >= c.get("min", 1) else "fail"), f"{len(atts)} attempt(s)"
    if kind == "novelty":                    # claims (with a status in `status`, if given) that had a literature check
        hits = [x for x in claims if x.get("novelty") and (not want or status(x) in want)
                and (not c.get("verdict") or x["novelty"]["verdict"] in c["verdict"])]
        return ("pass" if len(hits) >= c.get("min", 1) else "fail"), f"{len(hits)} claim(s) with a novelty check"
    if kind == "stopped_by":
        got = (rep.get("campaign") or {}).get("stopped_by")
        return ("pass" if got in c.get("values", []) else "fail"), f"stopped_by={got}"
    if kind == "human":
        return "pending", "needs a human grade: research-eval grade <eval-id> " + c["id"] + " pass|fail"
    raise EvalError(f"unknown check kind {kind}")


def _standard_checks(q: dict, record: dict) -> list[dict]:
    """Checks every question gets: the campaign ran, and cost stayed under the question's ceilings."""
    b, u = q["budget"], record.get("usage", {})
    out = [{"id": "_ran", "kind": "_ran", "critical": False, "what": "campaign finished without an agent/infra error"}]
    if b.get("max_turn_minutes"):
        out.append({"id": "_time", "kind": "_cost", "critical": False, "what": f"turn time <= {b['max_turn_minutes']} min",
                    "value": u.get("turn_minutes"), "limit": b["max_turn_minutes"]})
    if b.get("max_est_tokens"):
        out.append({"id": "_tokens", "kind": "_cost", "critical": False, "what": f"est. tokens <= {b['max_est_tokens']}",
                    "value": u.get("est_tokens"), "limit": b["max_est_tokens"]})
    return out


def load_grades(ledger: Path) -> dict[tuple[str, str], dict]:
    f = ledger / "eval" / "grades.jsonl"
    out: dict[tuple[str, str], dict] = {}
    for ln in (f.read_text().splitlines() if f.exists() else []):
        if ln.strip():
            g = json.loads(ln)
            out[(g["eval"], g["check"])] = g          # latest grade wins; history stays in the file
    return out


def score(record: dict, q: dict, ledger: Path, grades: dict | None = None) -> dict:
    grades = load_grades(ledger) if grades is None else grades
    rp = ledger / "campaigns" / record["campaign"] / "report.json"
    rep = json.loads(rp.read_text()) if rp.exists() else {"claims": [], "runs": [], "attempts": []}
    results = []
    for c in _standard_checks(q, record) + q["check"]:
        if c["kind"] == "_ran":
            ok = not str(record.get("outcome", {}).get("stopped_by", "")).startswith("error")
            res, detail = ("pass" if ok and rp.exists() else "fail"), str(record.get("outcome", {}).get("stopped_by"))
        elif c["kind"] == "_cost":
            res = "n/a" if c["value"] is None else ("pass" if c["value"] <= c["limit"] else "fail")
            detail = f"{c['value']} (limit {c['limit']})"
        else:
            res, detail = evaluate_check(c, rep, ledger, record["campaign"])
        source = "auto" if c["kind"] != "human" else "human"
        g = grades.get((record["id"], c["id"]))
        if g and res != "n/a":
            res, detail, source = g["result"], f"graded by {g['by']}: {g['reason']}", "human"
        results.append({"id": c["id"], "kind": c["kind"], "critical": bool(c.get("critical")), "what": c["what"],
                        "result": res, "source": source, "detail": detail})
    crit_fail = [r["id"] for r in results if r["critical"] and r["result"] == "fail"]
    pending = [r["id"] for r in results if r["result"] == "pending"]
    applicable = [r for r in results if r["result"] != "n/a"]
    verdict = ("fail" if crit_fail else "pending" if pending
               else "pass" if all(r["result"] == "pass" for r in applicable) else "partial")
    return {"eval": record["id"], "question": q["id"], "verdict": verdict, "critical_failures": crit_fail,
            "pending": pending, "checks": results,
            "question_changed": q["_sha256"] != record["question"]["sha256"]}


# ------------------------------------------------------------------ records, grades, runner

def records_dir(ledger: Path) -> Path:
    return ledger / "eval" / "records"


def load_records(ledger: Path) -> list[dict]:
    d = records_dir(ledger)
    return [json.loads(p.read_text()) for p in sorted(d.glob("E-*.json"))] if d.exists() else []


def load_record(ledger: Path, eid: str) -> dict:
    if not re.fullmatch(r"E-[0-9T]+-[0-9a-f]+", eid or ""):
        raise EvalError(f"bad evaluation id {eid!r}")
    p = records_dir(ledger) / f"{eid}.json"
    if not p.exists():
        raise EvalError(f"unknown evaluation {eid}")
    return json.loads(p.read_text())


def grade(ledger: Path, eid: str, check: str, result: str, by: str, reason: str, questions: dict) -> dict:
    rec = load_record(ledger, eid)
    q = questions.get(rec["question"]["id"])
    if q is None:
        raise EvalError(f"question {rec['question']['id']} not found (pass --questions for private sets)")
    ids = {c["id"] for c in q["check"]} | {c["id"] for c in _standard_checks(q, rec)}
    if check not in ids:
        raise EvalError(f"{rec['question']['id']} has no check {check!r}; checks: {sorted(ids)}")
    if result not in ("pass", "fail"):
        raise EvalError("result must be pass or fail")
    if not by.strip() or not reason.strip():
        raise EvalError("--by and --reason are required")
    g = {"at": _now(), "eval": eid, "check": check, "result": result, "by": by.strip(), "reason": reason.strip()}
    d = ledger / "eval"
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "grades.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(g) + "\n")
    return g


def _usage(ledger: Path, cid: str) -> dict:
    calls = usage.summarize_calls(ledger, cid)
    turns = usage.summarize_turns(ledger / "campaigns" / cid)
    runs = sum(1 for r in _audit(ledger, cid) if r.get("status", "completed") == "completed")
    return {"turn_minutes": round(sum(t.get("seconds", 0) for t in turns) / 60, 2),
            "est_tokens": sum(s["est_out_tokens"] + s["est_tool_result_tokens"] for s in calls.values()),
            "tool_calls": sum(s["calls"] for s in calls.values()), "runs": runs, "turns": len(turns)}


def run_question(root: Path, q: dict, agent, repeat: int = 1, progress=None, max_minutes: float | None = None,
                 hermes_home: Path | None = None) -> list[dict]:
    """Run `repeat` sealed campaigns on one golden question and write one record per repetition."""
    ledger = root / "ledger"
    config = capture_config(root, hermes_home)
    current = ledger / "campaigns" / "CURRENT"
    previous = current.read_text() if current.exists() else None
    try:
        return _run_repetitions(root, ledger, q, agent, repeat, progress, max_minutes, config)
    finally:                                   # do not leave later ad-hoc tool use inside a sealed campaign
        if previous is None:
            current.unlink(missing_ok=True)
        else:
            current.write_text(previous)


def _run_repetitions(root: Path, ledger: Path, q: dict, agent, repeat: int, progress, max_minutes, config) -> list[dict]:
    from .loop import run_campaign
    out, spent = [], 0.0
    for i in range(1, repeat + 1):
        if max_minutes is not None and spent >= max_minutes:
            (progress or print)(f"cost ceiling reached ({spent:.1f} >= {max_minutes} min); skipping the rest")
            break
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        cid = f"{EVAL_PREFIX}{q['id']}-{stamp}-{secrets.token_hex(2)}"
        started = _now()
        res = run_campaign(q["question"], q["budget"]["rounds"], root, agent, q["budget"]["max_runs"],
                           campaign_id=cid, progress=progress, prior=False)
        rec = {"schema": EVAL_SCHEMA, "id": f"E-{stamp}-{secrets.token_hex(3)}",
               "question": {"id": q["id"], "sha256": q["_sha256"], "split": q["split"], "type": q["type"],
                            "path": q["_path"]},
               "campaign": cid, "repetition": i, "started": started, "finished": _now(), "config": config,
               "outcome": {"stopped_by": res.stopped_by, "rounds_run": res.rounds_run, "errors": res.errors},
               "usage": _usage(ledger, cid)}
        d = records_dir(ledger)
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{rec['id']}.json").write_text(json.dumps(rec, indent=2) + "\n", encoding="utf-8")
        spent += rec["usage"]["turn_minutes"]
        out.append(rec)
    return out


def summary(ledger: Path, questions: dict, only: str | None = None) -> list[dict]:
    """Per (question, config): runs, verdicts, per-check pass counts, cost. The basis for regression comparisons."""
    grades = load_grades(ledger)
    groups: dict[tuple, dict] = {}
    for rec in load_records(ledger):
        qid = rec["question"]["id"]
        if (only and qid != only) or qid not in questions:
            continue
        s = score(rec, questions[qid], ledger, grades)
        key = (qid, rec["config"]["config_id"])
        g = groups.setdefault(key, {"question": qid, "split": rec["question"]["split"],
                                    "config_id": key[1], "models": {r: v["model"] for r, v in rec["config"]["roles"].items()},
                                    "commit": (rec["config"]["commit"] or "")[:10], "runs": 0,
                                    "verdicts": {}, "checks": {}, "critical_failures": 0, "minutes": [], "tokens": [],
                                    "question_changed": False})
        g["runs"] += 1
        g["verdicts"][s["verdict"]] = g["verdicts"].get(s["verdict"], 0) + 1
        g["critical_failures"] += bool(s["critical_failures"])
        g["question_changed"] |= s["question_changed"]
        g["minutes"].append(rec["usage"]["turn_minutes"])
        g["tokens"].append(rec["usage"]["est_tokens"])
        for c in s["checks"]:
            k = g["checks"].setdefault(c["id"], {"pass": 0, "fail": 0, "pending": 0, "n/a": 0})
            k[c["result"]] += 1
    rows = []
    for g in groups.values():
        n = g["runs"]
        g["mean_minutes"] = round(sum(g.pop("minutes")) / n, 2)
        g["mean_est_tokens"] = int(sum(g.pop("tokens")) / n)
        rows.append(g)
    return rows


# ------------------------------------------------------------------ CLI

def _print_score(s: dict) -> None:
    print(f"{s['eval']}  {s['question']}  verdict: {s['verdict']}"
          + (f"  CRITICAL: {', '.join(s['critical_failures'])}" if s["critical_failures"] else "")
          + ("  (question file changed since this run)" if s["question_changed"] else ""))
    for c in s["checks"]:
        print(f"  {'!' if c['critical'] else ' '} {c['result']:<7} {c['id']:<22} {c['source']:<5} {c['what']}  [{c['detail']}]")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="research-eval", description="Golden-question evaluation (docs/evaluation.md)")
    ap.add_argument("--root", default=".")
    ap.add_argument("--questions", action="append", default=[], help="extra question directory (e.g. private hold-out)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("validate")
    p = sub.add_parser("run")
    p.add_argument("question")
    p.add_argument("--repeat", type=int, default=1)
    p.add_argument("--holdout", action="store_true", help="required to run a hold-out question")
    p.add_argument("--max-minutes", type=float, help="stop starting repetitions after this much turn time")
    p.add_argument("--turn-timeout", type=int, default=None)
    p = sub.add_parser("score")
    p.add_argument("eval_id")
    p.add_argument("--json", action="store_true")
    sub.add_parser("pending")
    p = sub.add_parser("grade")
    p.add_argument("eval_id")
    p.add_argument("check")
    p.add_argument("result", choices=["pass", "fail"])
    p.add_argument("--by", required=True)
    p.add_argument("--reason", required=True)
    p = sub.add_parser("summary")
    p.add_argument("--question")
    p.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    root = Path(a.root).resolve()
    ledger = root / "ledger"
    try:
        qs = find_questions([d for d in question_dirs(root, a.questions) if d.exists()])
        if a.cmd == "list":
            for q in qs.values():
                print(f"{q['id']:<14} {q['split']:<8} {q['type']:<24} {q['verification']:<16} {q['title']}")
        elif a.cmd == "validate":
            print(f"{len(qs)} question file(s) valid")
        elif a.cmd == "run":
            q = qs.get(a.question)
            if q is None:
                raise EvalError(f"unknown question {a.question!r}; see research-eval list")
            if q["split"] == "holdout" and not a.holdout:
                raise EvalError("hold-out question: pass --holdout (see the hold-out policy in docs/evaluation.md)")
            from .loop import HermesCLI

            def progress(msg: str) -> None:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)
            agent = HermesCLI(timeout=a.turn_timeout or q["budget"].get("turn_timeout_s", 1800))
            for rec in run_question(root, q, agent, a.repeat, progress, a.max_minutes):
                _print_score(score(rec, q, ledger))
        elif a.cmd == "score":
            rec = load_record(ledger, a.eval_id)
            q = qs.get(rec["question"]["id"])
            if q is None:
                raise EvalError(f"question {rec['question']['id']} not found (pass --questions)")
            s = score(rec, q, ledger)
            print(json.dumps(s, indent=2)) if a.json else _print_score(s)
        elif a.cmd == "pending":
            grades = load_grades(ledger)
            for rec in load_records(ledger):
                q = qs.get(rec["question"]["id"])
                if q is None:
                    continue
                for c in score(rec, q, ledger, grades)["checks"]:
                    if c["result"] == "pending":
                        print(f"{rec['id']}  {q['id']:<12} {c['id']:<18} {c['what']}\n"
                              f"    review: research-claims report --campaign {rec['campaign']} --md")
        elif a.cmd == "grade":
            g = grade(ledger, a.eval_id, a.check, a.result, a.by, a.reason, qs)
            print(f"{g['eval']} {g['check']}: {g['result']} (by {g['by']})")
        elif a.cmd == "summary":
            rows = summary(ledger, qs, a.question)
            if a.json:
                print(json.dumps(rows, indent=2))
            for r in [] if a.json else rows:
                v = ", ".join(f"{k} {n}" for k, n in sorted(r["verdicts"].items()))
                print(f"{r['question']:<14} {r['split']:<8} config {r['config_id']} ({r['commit']}) runs {r['runs']}: {v}; "
                      f"critical-failure runs {r['critical_failures']}; mean {r['mean_minutes']} min, "
                      f"~{r['mean_est_tokens']} est. tokens" + ("  [question changed]" if r["question_changed"] else ""))
                for cid, k in r["checks"].items():
                    print(f"    {cid:<22} pass {k['pass']}/{r['runs'] - k['n/a']}" + (f", pending {k['pending']}" if k["pending"] else ""))
    except (EvalError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

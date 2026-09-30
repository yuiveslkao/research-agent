"""Structured JSON report (schema report/0.1) and its Markdown view.

The JSON is the primary output: one entry per claim with its text, derived/human/effective status and flags, the
evidence (findings, critiques, counterexamples, cited runs), proof references, failed attempts, citations, the
status history and reproduction commands. The Markdown report is rendered from it. The JSON Schema lives in
schemas/report-0.1.schema.json; bump REPORT_SCHEMA (and add a new schema file) on any incompatible change.
"""
from __future__ import annotations

import json
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from . import claims as claims_mod
from . import proofs as proofs_mod
from . import status as status_mod
from .glossary import CERTIFIED, EXHAUSTIVE, MEANING, SET_BY, STATUSES

REPORT_SCHEMA = "report/0.1"
SCHEMA_FILE = Path(__file__).parent / "schemas" / "report-0.1.schema.json"

GRADE_MEANING = {
    "none": "no cited runs",
    "run": "the cited runs completed once",
    "reproduced": "a cited run was re-executed and matched (rerun_run)",
    "cross-checked": "agreement across precisions (compare_runs or a passing highprec check) or a run by the other role",
    "falsification-tested": "a falsify check is among the cited runs (search effort, not a certificate)",
}


def _drop(d: dict) -> dict:
    return {k: v for k, v in d.items() if v not in (None, "", [], {})}


def _run_row(ledger: Path, rid: str) -> dict | None:
    d = ledger / "runs" / rid
    try:
        spec = tomllib.loads((d / "spec.toml").read_text())
        res = json.loads((d / "result.json").read_text())
    except (OSError, ValueError):
        return None
    meta = spec.get("meta", {})
    row = {"run_id": rid, "kind": spec.get("kind"), "status": res.get("status"), "role": meta.get("role"),
           "campaign": meta.get("campaign"), "title": meta.get("title"),
           "precision_bits": spec.get("numerics", {}).get("precision_bits"),
           "code_commit": (res.get("harness") or {}).get("code_commit"),
           "spec": f"ledger/runs/{rid}/spec.toml", "reproduce": f"rh rerun {rid}"}
    chk = ((res.get("reported") or {}).get("check") or {}).get("outcome")
    if chk:
        row["check_outcome"] = chk
    if spec.get("kind") == "check":
        script = str((spec.get("code") or {}).get("cmd", [""])[-1])
        row["checker"] = Path(script).stem                    # falsify | highprec
    return _drop(row)


def _note_entry(n: dict) -> dict:
    c = n.get("claim") or {}
    ev = n.get("evidence") or {}
    return _drop({"id": n["id"], "campaign": n["campaign"], "role": n["role"], "created": n["created"],
                  "verdict": n.get("status") if n["kind"] == "critique" else None, "result": c.get("result"),
                  "statement": c.get("statement"), "objection": c.get("objection"), "scope": c.get("scope"),
                  "limits": c.get("limits"), "resolve_by": c.get("resolve_by"), "counterexample": c.get("counterexample"),
                  "grade": ev.get("grade"), "run_ids": n.get("run_ids"), "text": n.get("text")})


def _proof_entry(snap: status_mod.Snapshot, p: dict, head: str, fp: str) -> dict:
    v = proofs_mod.view(snap, p)
    chars = len(p["proof"]) if "proof" in p else sum(len(s["statement"]) + len(s["proof"]) for s in p["steps"])
    return {"id": p["id"], "schema": p.get("schema", "proof/0.1"), "author": p["author"], "created": p["created"],
            "version": p["version"], "current": p["version"] == head and p["fingerprint"] == fp, "state": v["state"],
            "sha256": p["sha256"], "chars": chars, "steps": len(p.get("steps") or []),
            "supporting_runs": p.get("supporting_runs") or [], "path": f"ledger/proofs/{p['id']}.json",
            "reviews": [{k: r[k] for k in ("at", "reviewer", "verdict", "objections")} for r in v["reviews"]]}


def claim_entry(snap: status_mod.Snapshot, root: str) -> dict:
    v = snap.view(root)
    d = v["derived"]
    h = v["human"]
    notes = d["findings"] + d["critiques"]
    cited = sorted({r for n in notes for r in n.get("run_ids") or []}
                   | {r for p in v["proofs"] for r in p.get("supporting_runs") or []})
    cites = {}
    for n in notes:
        for s in (n.get("evidence") or {}).get("sources") or []:
            cites[s["id"]] = s
    for n in v["attempts"]:
        for sid in (n.get("claim") or {}).get("sources") or []:
            cites.setdefault(sid, {"id": sid})
    return {
        "id": root, "version": v["version"], "versions": v["versions"], "campaigns": v["campaigns"],
        "statement": v["statement"], "scope": v["scope"], "refuted_if": v["refuted_if"],
        "fingerprint": v["fingerprint"],
        "status": {
            "effective": v["effective"], "meaning": v["meaning"], "derived": d["status"],
            "human": _drop({k: h.get(k) for k in ("status", "by", "at", "reason", "proof", "counterexample",
                                                  "derived_at_set", "version")}) if h else None,
            "stale": v["stale"], "derived_changed": v["derived_changed"], "conflict": d["conflict"],
            "narrowed": d["narrowed"], "awaiting_review": v["awaiting_review"], "review_reasons": v["review_reasons"],
        },
        "supported_as": d["supported_as"],
        "evidence": {"grade": d["grade"], "certified": any((n.get("evidence") or {}).get("certified")
                                                            for n in notes if n["id"] in d["support"]),
                     "exhaustive": any((n.get("evidence") or {}).get("exhaustive")
                                       for n in notes if n["id"] in d["support"]),
                     "findings": [_note_entry(n) for n in d["findings"]],
                     "critiques": [_note_entry(n) for n in d["critiques"]],
                     "counterexamples": d["counterexamples"], "runs": cited},
        "proofs": [_proof_entry(snap, p, v["version"], v["fingerprint"]) for p in v["proofs"]],
        "attempts": [claims_mod.compact_attempt(n, root) for n in v["attempts"]],
        "novelty": v["novelty"],
        "citations": list(cites.values()),
        "history": v["history"],
        "reproduce": [f"rh rerun {r}" for r in cited],
    }


def build_report(ledger: Path, campaign: str | None = None, meta: dict | None = None) -> dict:
    """The report for one campaign (claims it touched, its runs) or, with campaign=None, for the whole ledger."""
    snap = status_mod.Snapshot(ledger)
    roots = snap.claim_ids(campaign)
    claims = [claim_entry(snap, r) for r in roots]
    reg = claims_mod.registry(ledger, campaign)
    linked = {a["id"] for c in claims for a in c["attempts"]}
    if campaign:
        run_ids = sorted(p.name for p in (ledger / "runs").glob("R-*")
                         if (_run_row(ledger, p.name) or {}).get("campaign") == campaign) if (ledger / "runs").exists() else []
    else:
        run_ids = sorted({r for c in claims for r in c["evidence"]["runs"]})
    return {
        "schema": REPORT_SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "campaign": _drop(dict(meta or {}, id=campaign)) if campaign else None,
        "glossary": {"statuses": {s: MEANING[s] for s in STATUSES}, "set_by": SET_BY, "grades": GRADE_MEANING,
                     "certified": CERTIFIED, "exhaustive": EXHAUSTIVE},
        "claims": claims,
        "open_objections": reg["open_objections"],
        "attempts": [a for a in reg["failed_attempts"] if a["id"] not in linked],
        "review_queue": [c["id"] for c in claims if c["status"]["awaiting_review"]],
        "runs": [r for r in (_run_row(ledger, x) for x in run_ids) if r],
    }


# ------------------------------------------------------------------ Markdown view

def _status_line(c: dict) -> str:
    s = c["status"]
    out = f"**{s['effective']}**: {s['meaning']}"
    if s["human"]:
        h = s["human"]
        out += f" Set by human {h.get('by')} on {str(h.get('at'))[:10]} ({h.get('reason')}); derived status: {s['derived']}."
    flags = [f for f in ("stale", "derived_changed", "narrowed") if s[f]]
    if flags:
        out += f" Flags: {', '.join(flags)}."
    if s["conflict"]:
        out += f" CONFLICT: {s['conflict']}."
    return out


def markdown_claim(c: dict, objections: list[dict] | None = None) -> list[str]:
    L = [f"### {c['id']}", "", f"- **statement:** {c['statement']}", f"- **scope:** {c['scope']}",
         f"- **status:** {_status_line(c)}"]
    if c["version"] != c["id"]:
        L.append(f"- **current version:** {c['version']} (versions: {', '.join(c['versions'])})")
    sa = c["supported_as"]
    if sa and (c["status"]["narrowed"] or sa.get("limits")):
        L.append(f"- **supported as** ({sa['note']}): {sa['statement']} | {sa['scope']}"
                 + (f" | limits: {sa['limits']}" if sa.get("limits") else ""))
    for o in objections or []:
        L.append(f"- **open objection** ({o['id']}): {o['objection']}"
                 + (f" (resolve by: {o['resolve_by']})" if o.get("resolve_by") else ""))
    for ce in c["evidence"]["counterexamples"]:
        L.append(f"- **{'certified ' if ce.get('certified') else ''}counterexample** ({ce['note']}, runs "
                 f"{', '.join(ce['run_ids']) or '-'}): {ce.get('statement')}")
    if c["evidence"].get("certified"):
        L.append("- **certified at the evaluated points** (interval arithmetic; finite, not a proof)")
    if c["evidence"].get("exhaustive"):
        L.append("- **exhaustively checked on a finite domain** (exact enumeration; nothing beyond that domain)")
    state_text = {"accepted": "accepted by the AI review pass, not human-reviewed", "pending": "awaiting the AI review pass",
                  "objected": "blocking objection in the AI review pass", "human": "written by the human",
                  "invalid": "INVALID: its content does not match its recorded sha256"}
    for p in c["proofs"]:
        who = "the AI" if not p["author"].startswith("human:") else p["author"]
        L.append(f"- **proof** {p['id']} by {who} ({state_text.get(p['state'], p['state'])})"
                 + ("" if p["current"] else " (bound to an earlier version)") + f": `{p['path']}`")
        for r in p["reviews"][-1:]:
            L += [f"  - review [{o['severity']}] {o['step']}: {o['issue']}" for o in r["objections"]]
    for a in c["attempts"]:
        L.append(f"- **tried, {a['outcome']}** ({a['stage']}): {a['approach']}; {a['reason']}")
    nv = c.get("novelty")
    if nv:
        found = "; ".join(f"{i} {t}" for i, t in zip(nv["sources"], nv["titles"] + [""] * len(nv["sources"]))) or "-"
        L.append(f"- **novelty ({nv['verdict']})**: arXiv searched for {'; '.join(repr(q) for q in nv['queries'])}; "
                 f"sources: {found}" + (f". {nv['explanation']}" if nv.get("explanation") else ""))
    else:
        L.append("- **novelty**: not checked (no literature search recorded)")
    if c["reproduce"]:
        L.append(f"- **reproduce:** {'; '.join(f'`{x}`' for x in c['reproduce'][:6])}"
                 + (" ..." if len(c["reproduce"]) > 6 else ""))
    return L + [""]


def markdown_claims(report: dict) -> list[str]:
    L = ["## Claims and status", "", "Statuses:"]
    L += [f"- `{k}`: {v}" for k, v in report["glossary"]["statuses"].items()] + [""]
    if not report["claims"]:
        return L + ["(no claims)", ""]
    L += ["| claim | status | evidence | critic | statement |", "|---|---|---|---|---|"]
    for c in report["claims"]:
        crit = c["evidence"]["critiques"][-1]["verdict"] if c["evidence"]["critiques"] else "-"
        L.append(f"| {c['id']} | {c['status']['effective']} | {c['evidence']['grade']} | {crit} | "
                 f"{c['statement'].replace('|', '/')} |")
    L.append("")
    for c in report["claims"]:
        L += markdown_claim(c, [o for o in report.get("open_objections", []) if o.get("claim") == c["id"]])
    if report["attempts"]:
        L += ["### Failed attempts not tied to a claim", ""]
        L += [f"- **{a['outcome']}** ({a['stage']}, {a['id']}): {a['approach']}; {a['reason']}" for a in report["attempts"]]
        L.append("")
    return L


def write_campaign_report(ledger: Path, campaign: str, meta: dict) -> tuple[dict, Path]:
    rep = build_report(ledger, campaign, meta)
    p = ledger / "campaigns" / campaign / "report.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rep, indent=2) + "\n", encoding="utf-8")
    return rep, p

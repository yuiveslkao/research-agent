"""Proof stage (build step 6): structured proofs written by the AI, the rule for starting one, and the review pass.

Proof record (schema proof/0.2, ledger/proofs/P-*.json): the exact statement and scope of the claim version it proves
(fingerprint-bound, see status.py), `assumptions`, and `steps`, each {id, kind, statement, proof, uses, runs}:
  kind       definition | lemma | claim | computation | conclusion (exactly one conclusion, the last step)
  uses       ids of earlier steps this step relies on (no forward references; every step is used by a later one)
  runs       for computation steps: runs that establish the finite fact (small cases, identities); they support the
             written argument and never replace it
A proof starts only for a claim that is numerically supported and survived a check (a cited falsify, highprec,
certify or exhaustive run with a passing outcome): "prove survivors". At most MAX_ATTEMPTS proofs per claim version
and campaign; after that, log an attempt (stage=prove) and stop.

Review pass: another role (the critic) reads the proof and records a review bound to its sha256
(ledger/proofs/reviews.jsonl, append-only): verdict accept (no blocking objection) or object (at least one blocking
objection naming a step). Only an accepted proof makes the derived status proven_AI; the objections stay next to the
proof. Human review and the status `proven` stay with research-claims.
"""
from __future__ import annotations

import json
import re
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from . import status as status_mod
from .status import Snapshot, StatusError, norm

PROOF_SCHEMA = "proof/0.2"
STEP_KINDS = ("definition", "lemma", "claim", "computation", "conclusion")
ARGUMENT_KINDS = ("lemma", "claim", "conclusion")
MAX_STEPS, MAX_ATTEMPTS = 40, 3
MIN_ARGUMENT, MAX_PROOF = 200, 60_000
STEP_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,15}$")
PASSING = {"falsify": {"survived"}, "highprec": {"pass"}, "certify": {"certified"}, "exhaustive": {"exhausted", "partial"}}
SEVERITIES = ("blocking", "minor")


def check_outcome(ledger: Path, run_id: str) -> tuple[str, str] | None:
    """(checker, outcome) of a check run, or None for experiments and unreadable records."""
    d = ledger / "runs" / run_id
    try:
        spec = tomllib.loads((d / "spec.toml").read_text())
        res = json.loads((d / "result.json").read_text())
    except (OSError, ValueError):
        return None
    if spec.get("kind") != "check":
        return None
    checker = Path(str((spec.get("code") or {}).get("cmd", [""])[-1])).stem
    return checker, (((res.get("reported") or {}).get("check") or {}).get("outcome") or "")


def survived_checks(snap: Snapshot, version: str) -> list[str]:
    """Passing check runs cited by the notes that support this claim version."""
    d = snap.derive(version)
    out = []
    for nid in d["support"]:
        for r in snap.by_id[nid].get("run_ids") or []:
            co = check_outcome(snap.ledger, r)
            if co and co[1] in PASSING.get(co[0], set()) and r not in out:
                out.append(r)
    return out


def _run_ok(ledger: Path, r: str) -> None:
    if not isinstance(r, str) or not (ledger / "runs" / r / "result.json").exists():
        raise StatusError(f"unknown run id {r!r}")
    res = json.loads((ledger / "runs" / r / "result.json").read_text())
    if res.get("status") != "completed":
        raise StatusError(f"run {r} did not complete; it cannot support a proof step")
    co = check_outcome(ledger, r)
    if co and co[0] in PASSING and co[1] not in PASSING[co[0]]:
        raise StatusError(f"run {r} is a {co[0]} check with outcome {co[1]!r}; it does not support the step")


def validate_steps(ledger: Path, steps, assumptions) -> tuple[list[dict], list[str]]:
    if not isinstance(assumptions, list) or not all(isinstance(a, str) and 0 < len(a) <= 400 for a in assumptions):
        raise StatusError("assumptions must be a list of short strings (the hypotheses the proof uses)")
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        raise StatusError(f"steps must be a list of 1..{MAX_STEPS} steps "
                          "{id, kind, statement, proof, uses, runs}; the last one has kind 'conclusion'")
    seen: list[str] = []
    used: set[str] = set()
    out = []
    for i, st in enumerate(steps):
        if not isinstance(st, dict):
            raise StatusError(f"step {i + 1} must be an object")
        unknown = set(st) - {"id", "kind", "statement", "proof", "uses", "runs"}
        if unknown:
            raise StatusError(f"step {i + 1}: unknown fields {sorted(unknown)}")
        sid, kind = st.get("id"), st.get("kind")
        if not isinstance(sid, str) or not STEP_ID.match(sid) or sid in seen:
            raise StatusError(f"step {i + 1}: id must be unique and short, e.g. 'L1' (got {sid!r})")
        if kind not in STEP_KINDS:
            raise StatusError(f"step {sid}: kind must be one of {list(STEP_KINDS)}")
        stmt, prf = st.get("statement"), st.get("proof", "")
        if not isinstance(stmt, str) or not 0 < len(stmt.strip()) <= 1000:
            raise StatusError(f"step {sid}: `statement` is required (at most 1000 characters)")
        if not isinstance(prf, str) or len(prf) > 12_000:
            raise StatusError(f"step {sid}: `proof` must be a string of at most 12000 characters")
        if kind in ARGUMENT_KINDS and not prf.strip():
            raise StatusError(f"step {sid}: a {kind} needs its `proof` (the argument for this step)")
        uses, runs = st.get("uses", []), st.get("runs", [])
        if not isinstance(uses, list) or not all(isinstance(u, str) for u in uses):
            raise StatusError(f"step {sid}: `uses` must be a list of earlier step ids")
        bad = [u for u in uses if u not in seen]
        if bad:
            raise StatusError(f"step {sid}: uses {bad}, which are not earlier steps (no forward references)")
        if not isinstance(runs, list):
            raise StatusError(f"step {sid}: `runs` must be a list of run ids")
        if kind == "computation" and not runs:
            raise StatusError(f"step {sid}: a computation step must cite the runs that establish it")
        for r in runs:
            _run_ok(ledger, r)
        if kind == "conclusion" and (i != len(steps) - 1 or not uses and not prf.strip()):
            raise StatusError("exactly one conclusion, as the last step, which uses earlier steps or argues directly")
        seen.append(sid)
        used.update(uses)
        out.append({"id": sid, "kind": kind, "statement": stmt.strip(), "proof": prf.strip(), "uses": uses,
                    "runs": runs})
    if out[-1]["kind"] != "conclusion" or sum(s["kind"] == "conclusion" for s in out) != 1:
        raise StatusError("the last step (and only that step) must have kind 'conclusion'")
    dangling = [s["id"] for s in out[:-1] if s["id"] not in used]
    if dangling:
        raise StatusError(f"steps {dangling} are not used by any later step; connect them or remove them")
    argument = sum(len(s["proof"]) for s in out if s["kind"] in ARGUMENT_KINDS)
    if argument < MIN_ARGUMENT:
        raise StatusError(f"the written argument (proofs of lemmas, claims and the conclusion) is {argument} characters; "
                          f"a proof needs at least {MIN_ARGUMENT}. Computation steps support an argument, they do not "
                          "replace it")
    if sum(len(json.dumps(s)) for s in out) > MAX_PROOF:
        raise StatusError(f"the proof exceeds {MAX_PROOF} characters")
    return out, [a.strip() for a in assumptions]


def submit(ledger: Path, author: str, campaign: str, version: str, statement: str, scope: str, steps,
           assumptions=None) -> dict:
    """Store a structured proof of the exact current version of a claim. It awaits the review pass."""
    snap = Snapshot(ledger)
    root = snap.resolve(version)
    head = snap.head(root)
    if head["id"] != version:
        raise StatusError(f"{version} was revised; the current version of claim {root} is {head['id']}. "
                          "Prove the current version, or add a hypothesis with revises=<current> first")
    c = head["claim"]
    if (norm(statement), norm(scope)) != (norm(c["statement"]), norm(c["scope"])):
        raise StatusError("statement and scope must repeat the claim exactly (whitespace aside), so the proof is "
                          f"bound to what it proves. Claim statement: {c['statement']!r}; scope: {c['scope']!r}. "
                          "To prove a different statement, first add a hypothesis with revises=" + version)
    d = snap.derive(version)
    if d["counterexamples"]:
        raise StatusError(f"claim {root} has a recorded counterexample ({d['counterexamples'][-1]['note']}); a proof "
                          "cannot be recorded for it. Re-examine the counterexample or revise the claim")
    if d["status"] not in ("numerically-supported", "proven_AI") or not survived_checks(snap, version):
        raise StatusError("prove only claims that survived a check: first cite a falsify, highprec, certify or "
                          "exhaustive run with a passing outcome in a supporting finding about this claim")
    tries = sum(1 for p in snap.proofs if p["version"] == version and p.get("campaign") == campaign)
    if tries >= MAX_ATTEMPTS:
        raise StatusError(f"{tries} proofs of this claim were submitted in this campaign (limit {MAX_ATTEMPTS}). "
                          "Log what failed as add_note(kind='attempt', stage='prove') and stop")
    steps, assumptions = validate_steps(ledger, steps, list(assumptions or []))
    body = {"schema": PROOF_SCHEMA, "format": "steps", "assumptions": assumptions, "steps": steps,
            "supporting_runs": sorted({r for s in steps for r in s["runs"]})}
    rec = status_mod.write_proof_record(ledger, snap, version, author, campaign, body,
                                        {"statement": c["statement"], "scope": c["scope"],
                                         "assumptions": assumptions, "steps": steps})
    status_mod.sync(ledger, root, author, f"proof {rec['id']} submitted (awaiting review)")
    return rec


def find(snap: Snapshot, proof_id: str) -> dict:
    p = next((x for x in snap.proofs if x["id"] == proof_id), None)
    if p is None:
        raise StatusError(f"unknown proof {proof_id!r}")
    return p


def review(ledger: Path, proof_id: str, reviewer: str, campaign: str, verdict: str, objections=None,
           run_ids=None) -> dict:
    """Record the review pass of a proof. accept = no blocking objection; object = at least one blocking objection."""
    snap = Snapshot(ledger)
    p = find(snap, proof_id)
    if reviewer == p["author"] or str(p["author"]).startswith("human:"):
        raise StatusError("a proof is reviewed by a different role than its author (and human proofs by the human)")
    head = snap.head(p["claim"])
    if p["version"] != head["id"]:
        raise StatusError(f"proof {proof_id} is for version {p['version']}; the claim was revised to {head['id']}")
    if verdict not in ("accept", "object"):
        raise StatusError("verdict must be 'accept' or 'object'")
    objections = list(objections or [])
    ids = {s["id"] for s in p.get("steps", [])} | {"statement", "overall"}
    clean = []
    for o in objections:
        if not isinstance(o, dict) or set(o) - {"step", "issue", "severity"}:
            raise StatusError("each objection is {step, issue, severity}")
        if o.get("step") not in ids:
            raise StatusError(f"objection step must be one of {sorted(ids)} (got {o.get('step')!r})")
        if not isinstance(o.get("issue"), str) or not 0 < len(o["issue"].strip()) <= 1000:
            raise StatusError("objection `issue` must say what is wrong (at most 1000 characters)")
        if o.get("severity") not in SEVERITIES:
            raise StatusError(f"objection severity must be one of {list(SEVERITIES)}")
        clean.append({"step": o["step"], "issue": o["issue"].strip(), "severity": o["severity"]})
    blocking = [o for o in clean if o["severity"] == "blocking"]
    if verdict == "accept" and blocking:
        raise StatusError("an accept has no blocking objections; use verdict 'object'")
    if verdict == "object" and not blocking:
        raise StatusError("an objection verdict needs at least one blocking objection naming the step")
    for r in run_ids or []:
        if not isinstance(r, str) or not (ledger / "runs" / r / "result.json").exists():
            raise StatusError(f"unknown run id {r!r}")
    rec = {"at": datetime.now(timezone.utc).isoformat(timespec="microseconds"), "proof": proof_id,
           "sha256": p["sha256"], "claim": p["claim"], "reviewer": reviewer, "campaign": campaign,
           "verdict": verdict, "objections": clean, "run_ids": list(run_ids or [])}
    d = ledger / "proofs"
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "reviews.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    status_mod.sync(ledger, p["claim"], reviewer, f"review of {proof_id}: {verdict}")
    return rec


def pending(ledger: Path, campaign: str) -> list[str]:
    """Proofs of this campaign bound to their claim's current version that have no review yet."""
    snap = Snapshot(ledger)
    out = []
    for p in snap.proofs:
        if p.get("campaign") == campaign and snap.proof_state(p) == "pending" \
                and p["version"] == snap.head(p["claim"])["id"]:
            out.append(p["id"])
    return out


def view(snap: Snapshot, p: dict) -> dict:
    """The proof with its state and reviews (agent- and human-facing)."""
    reviews = [r for r in snap.reviews if r["proof"] == p["id"]]
    out = {"id": p["id"], "claim": p["claim"], "version": p["version"], "author": p["author"],
           "state": snap.proof_state(p), "statement": p["statement"], "scope": p["scope"]}
    if p.get("steps") is not None:
        out.update(assumptions=p.get("assumptions", []), steps=p["steps"])
    else:
        out["proof"] = p.get("proof", "")
    out["reviews"] = [{k: r[k] for k in ("at", "reviewer", "verdict", "objections", "run_ids")}
                      for r in reviews if r["sha256"] == p["sha256"]]
    return out


def render(v: dict) -> str:
    """Markdown for a human reviewer: statement, assumptions, steps with dependencies, reviews."""
    L = [f"### {v['id']} ({v['state']}; author {v['author']})", "", f"**Statement:** {v['statement']}",
         f"**Scope:** {v['scope']}", ""]
    if "steps" in v:
        if v["assumptions"]:
            L += ["**Assumptions:**"] + [f"- {a}" for a in v["assumptions"]] + [""]
        for s in v["steps"]:
            deps = f" (uses {', '.join(s['uses'])})" if s["uses"] else ""
            runs = f" [runs: {', '.join(s['runs'])}]" if s["runs"] else ""
            L += [f"**{s['id']}. {s['kind'].capitalize()}.**{deps}{runs} {s['statement']}", ""]
            if s["proof"]:
                L += [s["proof"], ""]
    else:
        L += [v.get("proof", ""), ""]
    for r in v["reviews"]:
        L.append(f"**Review** by {r['reviewer']} ({r['at'][:19]}): {r['verdict']}")
        L += [f"- [{o['severity']}] {o['step']}: {o['issue']}" for o in r["objections"]]
        L.append("")
    return "\n".join(L)

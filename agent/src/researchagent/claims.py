"""Structured claims: schema for hypothesis/finding/critique/attempt notes, server-derived evidence, and the registry.

Design rule: the agent supplies short structured fields (statement, scope, ...); the server derives the evidence
grade from what actually ran (reruns, precision comparisons, independent runs, falsification) and the claim status
from records (status.py). Agents never assert their own evidence grade or status, and they do not spend tokens
restating it.
"""
from __future__ import annotations

import json
import tomllib
from pathlib import Path

from . import status as status_mod
from .glossary import MEANING

FIELDS = {  # kind[/status] -> (required, optional), all string fields unless noted
    "hypothesis": (("statement", "scope", "refuted_if"), ("revises",)),
    "finding": (("statement", "scope", "result"), ("limits", "about", "sources", "counterexample")),
    "critique/accept": (("statement", "scope"), ("limits", "sources", "about")),
    "critique/challenge": (("objection",), ("resolve_by", "sources", "about", "counterexample")),
    "attempt": (("approach", "stage", "outcome", "reason"), ("about", "sources")),
}
MAXLEN = {"statement": 400, "scope": 300, "refuted_if": 300, "limits": 300, "objection": 500, "resolve_by": 300,
          "result": 20, "about": 60, "revises": 60, "counterexample": 40, "approach": 300, "stage": 20,
          "outcome": 20, "reason": 400}
RESULTS = {"supports", "refutes", "inconclusive"}
STAGES = {"explore", "falsify", "prove", "literature"}
OUTCOMES = {"failed", "inconclusive", "abandoned"}     # an attempt never records a success; that is a finding or proof
MAX_TEXT_WITH_CLAIM = 800
GRADES = status_mod.GRADES


class ClaimError(ValueError):
    pass


def _key(kind: str, status: str | None) -> str | None:
    if kind == "hypothesis":
        return "hypothesis"
    if kind == "finding":
        return "finding"
    if kind == "critique":
        return f"critique/{status}"
    if kind == "attempt":
        return "attempt"
    return None


def validate_claim(kind: str, status: str | None, claim: dict | None) -> dict | None:
    """Return the cleaned claim (or None for kinds without one). Raises ClaimError with a fix-it message."""
    key = _key(kind, status)
    if kind == "critique" and key not in FIELDS:
        raise ClaimError("critique notes need status in ['accept', 'challenge']")
    if key is None:
        if claim:
            raise ClaimError(f"{kind} notes take no `claim`")
        return None
    if not isinstance(claim, dict) or not claim:
        req, _ = FIELDS[key]
        raise ClaimError(f"a {kind}" + (f" ({status})" if kind == "critique" else "")
                         + f" needs claim={{{', '.join(req)}}} (short strings; put the substance here, not in text)")
    req, opt = FIELDS[key]
    unknown = sorted(set(claim) - set(req) - set(opt))
    if unknown:
        raise ClaimError(f"unknown claim fields {unknown}; allowed: {list(req) + list(opt)}")
    out: dict = {}
    for f in req:
        v = claim.get(f)
        if not isinstance(v, str) or not v.strip():
            raise ClaimError(f"claim.{f} is required (non-empty string)")
    for f, v in claim.items():
        if f == "sources":
            if not isinstance(v, list) or not all(isinstance(s, str) for s in v) or len(v) > 5:
                raise ClaimError("claim.sources must be a list of at most 5 arXiv ids")
            out[f] = [s.strip() for s in v]
            continue
        if not isinstance(v, str):
            raise ClaimError(f"claim.{f} must be a string")
        if len(v) > MAXLEN[f]:
            raise ClaimError(f"claim.{f} is {len(v)} characters; the limit is {MAXLEN[f]}. Shorten it")
        out[f] = v.strip()
    if key == "finding" and out["result"] not in RESULTS:
        raise ClaimError(f"claim.result must be one of {sorted(RESULTS)}")
    if key == "finding" and (out["result"] == "refutes") != bool(out.get("counterexample")):
        raise ClaimError("result=refutes needs claim.counterexample = the run id (from run_ids) that shows the "
                         "violation, with the parameters in `statement`; other results take no counterexample")
    if key == "attempt":
        if out["stage"] not in STAGES:
            raise ClaimError(f"claim.stage must be one of {sorted(STAGES)}")
        if out["outcome"] not in OUTCOMES:
            raise ClaimError(f"claim.outcome must be one of {sorted(OUTCOMES)} (a success is a finding or a proof)")
    return out


# ------------------------------------------------------------------ derived evidence

def _audit_role(ledger: Path) -> dict[str, str]:
    f = ledger / "audit.jsonl"
    if not f.exists():
        return {}
    recs = (json.loads(ln) for ln in f.read_text().splitlines() if ln.strip())
    return {r["run_id"]: r["role"] for r in recs}


def _checks(ledger: Path) -> list[dict]:
    d = ledger / "checks"
    return [json.loads(p.read_text()) for p in sorted(d.glob("K-*.json"))] if d.exists() else []


def _spec(ledger: Path, run_id: str) -> dict:
    return tomllib.loads((ledger / "runs" / run_id / "spec.toml").read_text())


def derive_evidence(ledger: Path, run_ids: list[str], author_role: str) -> dict:
    """What the cited runs actually establish. Computed from ledger records only; never from agent statements."""
    ids = set(run_ids)
    roles = _audit_role(ledger)
    checks = _checks(ledger)
    reproduced = any(c.get("kind") == "rerun" and c.get("outcome") == "pass"
                     and (c.get("original") in ids or c.get("rerun") in ids) for c in checks)
    bits = {r: _spec(ledger, r).get("numerics", {}).get("precision_bits") for r in ids}
    precision_checked = any(
        c.get("kind") == "compare" and c.get("outcome") == "pass" and c.get("a") in ids and c.get("b") in ids
        and bits.get(c["a"]) != bits.get(c["b"]) for c in checks)
    independent = any(roles.get(r) not in (None, author_role) for r in ids)
    own = sum(1 for r in ids if roles.get(r) == author_role)
    falsify = None
    for r in ids:
        spec = _spec(ledger, r)
        if spec.get("kind") == "check" and str(spec["code"]["cmd"][-1]).endswith("highprec.jl"):
            try:   # a passing high-precision recheck of the statement counts as a precision check
                res = json.loads((ledger / "runs" / r / "result.json").read_text())
                if (res.get("reported") or {}).get("check", {}).get("outcome") == "pass":
                    precision_checked = True
            except (OSError, ValueError):
                pass
        if spec.get("kind") == "check" and str(spec["code"]["cmd"][-1]).endswith("falsify.jl"):
            try:
                res = json.loads((ledger / "runs" / r / "result.json").read_text())
                falsify = (res.get("reported") or {}).get("check", {}).get("outcome") or falsify
            except (OSError, ValueError):
                pass
    if falsify in ("survived", "violated"):
        grade = "falsification-tested"
    elif precision_checked or independent:
        grade = "cross-checked"
    elif reproduced:
        grade = "reproduced"
    elif ids:
        grade = "run"
    else:
        grade = "none"
    return {"grade": grade, "runs": len(ids), "own_runs": own, "reproduced": reproduced,
            "precision_checked": precision_checked, "independent": independent, "falsify": falsify}


# ------------------------------------------------------------------ registry across campaigns

def compact_claim(v: dict) -> dict:
    """Agent-facing view of one claim (status.Snapshot.view), token-lean."""
    d = v["derived"]
    out = {"id": v["id"], "status": v["effective"], "statement": v["statement"], "scope": v["scope"],
           "grade": d["grade"] if d["grade"] != "none" else None, "critic": d["critic"],
           "campaign": v["campaigns"][-1] if v["campaigns"] else None}
    if v["version"] != v["id"]:
        out["version"] = v["version"]
    if v["human"]:
        out["set_by_human"] = True
        if d["status"] != v["effective"]:
            out["derived_status"] = d["status"]
    sa = d["supported_as"]
    if sa and (d["narrowed"] or sa.get("limits")):
        out["supported_as"] = {k: sa[k] for k in ("statement", "scope", "limits") if sa.get(k)}
    if d["counterexamples"]:
        ce = d["counterexamples"][-1]
        out["counterexample"] = {"note": ce["note"], "runs": ce["run_ids"], "statement": ce["statement"]}
    for flag in ("stale", "derived_changed"):
        if v[flag]:
            out[flag] = True
    if d["conflict"]:
        out["conflict"] = d["conflict"]
    return {k: x for k, x in out.items() if x not in (None, "", [])}


def compact_attempt(n: dict, claim: str | None) -> dict:
    c = n["claim"]
    out = {"id": n["id"], "claim": claim, "campaign": n["campaign"], "stage": c["stage"], "approach": c["approach"],
           "outcome": c["outcome"], "reason": c["reason"]}
    return {k: x for k, x in out.items() if x}


def registry(ledger: Path, campaign: str | None = None) -> dict:
    """Claims with their status, open objections and failed attempts, as the ledger currently stands (oldest first)."""
    snap = status_mod.Snapshot(ledger)
    claims, objections = [], []
    for root in snap.claim_ids(campaign):
        v = snap.view(root)
        claims.append(compact_claim(v))
        for nid in v["derived"]["open_objections"]:
            n = snap.by_id[nid]
            objections.append({"id": nid, "claim": root, "campaign": n["campaign"], "objection": n["claim"]["objection"],
                               "resolve_by": n["claim"].get("resolve_by")})
    attempts = []
    for n in snap.notes:
        if n["kind"] == "attempt" and n.get("claim") and campaign in (None, n["campaign"]):
            a = snap.about_of.get(n["id"])
            attempts.append(compact_attempt(n, snap.root_of(a) if a else None))
    return {"claims": claims, "open_objections": [{k: x for k, x in o.items() if x} for o in objections],
            "failed_attempts": attempts}


def _line(prefix: str, r: dict, keys: tuple[str, ...]) -> str:
    return f"- {prefix} " + " | ".join(str(r[k]) for k in keys if r.get(k))


BRIEF_ORDER = ("proven", "proven_AI", "numerically-supported", "refuted")
BRIEF_LABEL = {"proven": "proven, human-reviewed", "proven_AI": "proven_AI: proved by the AI, not human-reviewed",
               "numerically-supported": "numerically-supported, not a proof", "refuted": "refuted"}


def brief(ledger: Path, limit: int = 6, max_chars: int = 2200, exclude_campaign: str | None = None) -> str:
    """Compact text of earlier claims with their status, open objections and failed attempts. Newest first."""
    reg = registry(ledger)

    def keep(rs):
        return [r for r in reversed(rs) if r.get("campaign") != exclude_campaign][:limit]
    lines = []
    for st in BRIEF_ORDER:
        for r in keep([c for c in reg["claims"] if c["status"] == st]):
            tag = BRIEF_LABEL[st] + (f", {r['grade']}" if st == "numerically-supported" and r.get("grade") else "")
            line = _line(f"[{tag}; {r.get('campaign')}]", r, ("statement", "scope"))
            if r.get("supported_as"):
                line += " | supported only as: " + " | ".join(str(x) for x in r["supported_as"].values())
            if st == "refuted" and r.get("counterexample"):
                line += " | counterexample: " + str(r["counterexample"].get("statement") or r["counterexample"]["runs"])
            lines.append(line)
    for r in keep(reg["open_objections"]):
        lines.append(_line(f"[open objection; {r['campaign']}]", r, ("objection",)))
    for r in keep(reg["failed_attempts"]):
        lines.append(_line(f"[tried, {r['outcome']}: {r['stage']}; {r['campaign']}]", r, ("approach", "reason")))
    text = "\n".join(lines)
    return text if len(text) <= max_chars else text[:max_chars].rsplit("\n", 1)[0] + "\n- (older entries omitted; call read_claims)"


def status_legend() -> str:
    return "; ".join(f"{k} = {MEANING[k]}" for k in ("numerically-supported", "proven_AI", "proven", "refuted"))


def compact_note(n: dict) -> dict:
    """Agent-facing view of a note: no timestamps, empty fields dropped (token-lean)."""
    out = {"id": n["id"], "role": n["role"], "kind": n["kind"], "status": n.get("status"),
           "claim": n.get("claim"), "grade": (n.get("evidence") or {}).get("grade"),
           "run_ids": n.get("run_ids") or None, "text": n.get("text") or None}
    return {k: v for k, v in out.items() if v not in (None, "", [])}

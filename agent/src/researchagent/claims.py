"""Structured claims: schema for hypothesis/finding/critique notes, server-derived evidence, and the claim registry.

Design rule: the agent supplies short structured fields (statement, scope, ...); the server derives the evidence
grade from what actually ran (reruns, precision comparisons, independent runs, falsification). Agents never
assert their own evidence grade, and they do not spend tokens restating it.
"""
from __future__ import annotations

import json
import tomllib
from pathlib import Path

FIELDS = {  # kind[/status] -> (required, optional), all string fields unless noted
    "hypothesis": (("statement", "scope", "refuted_if"), ()),
    "finding": (("statement", "scope", "result"), ("limits", "about", "sources")),
    "critique/accept": (("statement", "scope"), ("limits", "sources")),
    "critique/challenge": (("objection",), ("resolve_by", "sources")),
}
MAXLEN = {"statement": 400, "scope": 300, "refuted_if": 300, "limits": 300, "objection": 500, "resolve_by": 300,
          "result": 20, "about": 60}
RESULTS = {"supports", "refutes", "inconclusive"}
MAX_TEXT_WITH_CLAIM = 800
GRADES = ("none", "run", "reproduced", "cross-checked", "falsification-tested")


class ClaimError(ValueError):
    pass


def _key(kind: str, status: str | None) -> str | None:
    if kind == "hypothesis":
        return "hypothesis"
    if kind == "finding":
        return "finding"
    if kind == "critique":
        return f"critique/{status}"
    return None


def validate_claim(kind: str, status: str | None, claim: dict | None) -> dict | None:
    """Return the cleaned claim (or None for kinds without one). Raises ClaimError with a fix-it message."""
    key = _key(kind, status)
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

def _all_notes(ledger: Path) -> list[dict]:
    d = ledger / "notes"
    return [json.loads(p.read_text()) for p in sorted(d.glob("N-*.json"))] if d.exists() else []


def build_registry(ledger: Path, campaign: str | None = None) -> dict:
    """Claims as the ledger currently stands: accepted (by the critic), open challenges, refuted, unreviewed."""
    notes = [n for n in _all_notes(ledger) if campaign in (None, n["campaign"])]
    accepted, open_ch, refuted, unreviewed = [], [], [], []
    by_campaign: dict[str, list[dict]] = {}
    for n in notes:
        by_campaign.setdefault(n["campaign"], []).append(n)
    for cid, ns in by_campaign.items():
        hyps = {n["id"]: n for n in ns if n["kind"] == "hypothesis" and n.get("claim")}
        last_accept = max((i for i, n in enumerate(ns) if n["kind"] == "critique" and n["status"] == "accept"),
                          default=-1)
        for i, n in enumerate(ns):
            c = n.get("claim")
            if not c:
                continue
            g = (n.get("evidence") or {}).get("grade", "none")
            if n["kind"] == "critique" and n["status"] == "accept":
                accepted.append({"id": n["id"], "campaign": cid, "grade": g, **c})
            elif n["kind"] == "critique" and n["status"] == "challenge" and i > last_accept:
                open_ch.append({"id": n["id"], "campaign": cid, "objection": c["objection"]})
            elif n["kind"] == "finding":
                h = hyps.get(c.get("about") or "")
                rec = {"id": n["id"], "campaign": cid, "grade": g, "result": c["result"],
                       "statement": c["statement"], "scope": c["scope"], "limits": c.get("limits"),
                       "hypothesis": h["claim"]["statement"] if h else None}
                if c["result"] == "refutes":
                    refuted.append(rec)
                elif i > last_accept:
                    unreviewed.append(rec)
    return {"accepted": accepted, "open_challenges": open_ch, "refuted": refuted, "unreviewed": unreviewed}


def _line(prefix: str, r: dict, keys: tuple[str, ...]) -> str:
    return f"- {prefix} " + " | ".join(str(r[k]) for k in keys if r.get(k))


def brief(ledger: Path, limit: int = 6, max_chars: int = 2200, exclude_campaign: str | None = None) -> str:
    """Compact text of what earlier campaigns established (empty string if nothing). Newest first."""
    reg = build_registry(ledger)

    def keep(rs):
        return [r for r in reversed(rs) if r["campaign"] != exclude_campaign][:limit]
    lines = []
    for r in keep(reg["accepted"]):
        lines.append(_line(f"ACCEPTED [{r['grade']}, {r['campaign']}]", r, ("statement", "scope", "limits")))
    for r in keep(reg["open_challenges"]):
        lines.append(_line(f"OPEN OBJECTION [{r['campaign']}]", r, ("objection",)))
    for r in keep(reg["refuted"]):
        lines.append(_line(f"REFUTED [{r['grade']}, {r['campaign']}]", r, ("hypothesis", "statement", "scope")))
    text = "\n".join(lines)
    return text if len(text) <= max_chars else text[:max_chars].rsplit("\n", 1)[0] + "\n- (older entries omitted; call read_claims)"


def compact_note(n: dict) -> dict:
    """Agent-facing view of a note: no timestamps, empty fields dropped (token-lean)."""
    out = {"id": n["id"], "role": n["role"], "kind": n["kind"], "status": n.get("status"),
           "claim": n.get("claim"), "grade": (n.get("evidence") or {}).get("grade"),
           "run_ids": n.get("run_ids") or None, "text": n.get("text") or None}
    return {k: v for k, v in out.items() if v not in (None, "", [])}

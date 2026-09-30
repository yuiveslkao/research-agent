"""Claim status: the server/AI-derived status next to an optional human status, with an append-only history.

Identity. A hypothesis note is one version of a claim. A later hypothesis with `revises=<id>` is the next version;
the chain is a thread whose id is its first hypothesis id and whose head (latest version) is the current text.
Evidence, counterexamples and proofs count only for the exact version they are about, so a revision starts the
derived status again from `open` (evidence for other wording does not transfer).

Derived status of the head, in order of precedence (glossary.MEANING has the wording):
  refuted                a finding (result=refutes) or a challenge names a counterexample run about this version
  proven_AI              a proof record (ledger/proofs/P-*.json) is bound to this version's fingerprint
  numerically-supported  a supporting finding or a critic accept cites runs about this version
  open                   otherwise
A counterexample next to a proof is reported as a conflict (status refuted).

Human status. Only `set_human_status` (the `research-claims` CLI) writes it; no MCP tool can. `proven` is
human-only and needs a proof record bound to the current version. The effective status is the human's if set.
A human status is flagged `stale` when the claim was revised after it was set, and `derived_changed` when the
derived status moved since. Every change (derived or human) is appended to ledger/status/history.jsonl.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

from .glossary import MEANING, STATUSES

GRADES = ("none", "run", "reproduced", "cross-checked", "falsification-tested")
PROOF_SCHEMA = "proof/0.1"
MIN_PROOF, MAX_PROOF = 200, 60_000
AGENT_ROLES = {"researcher", "critic", "driver"}


class StatusError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def norm(s: str | None) -> str:
    return " ".join((s or "").split())


def fingerprint(statement: str, scope: str) -> str:
    blob = json.dumps({"statement": norm(statement), "scope": norm(scope)}, sort_keys=True)
    return "sha256:" + hashlib.sha256(blob.encode()).hexdigest()


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()] if path.exists() else []


def _append(ledger: Path, rec: dict) -> dict:
    d = ledger / "status"
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "history.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    return rec


def default_about(prior: list[dict], kind: str, role: str) -> str | None:
    """The version a note is about when it does not say: a researcher's latest hypothesis; for the critic, what the
    researcher's latest finding was about (else the latest researcher hypothesis). `prior` = earlier campaign notes."""
    if kind in ("critique", "attempt") and role != "researcher":
        f = next((n for n in reversed(prior) if n["kind"] == "finding" and n["role"] == "researcher"
                  and (n.get("claim") or {}).get("about")), None)
        if f:
            return f["claim"]["about"]
    h = next((n for n in reversed(prior) if n["kind"] == "hypothesis" and n["role"] == "researcher"), None)
    return h["id"] if h else None


class Snapshot:
    """Read-only view of notes, proofs and history, built once per request."""

    def __init__(self, ledger: Path):
        self.ledger = ledger
        d = ledger / "notes"
        self.notes = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(d.glob("N-*.json"))] if d.exists() else []
        self.by_id = {n["id"]: n for n in self.notes}
        self.hyps = {n["id"]: n for n in self.notes if n["kind"] == "hypothesis" and n.get("claim")}
        pd = ledger / "proofs"
        self.proofs = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(pd.glob("P-*.json"))] if pd.exists() else []
        self.history = _jsonl(ledger / "status" / "history.jsonl")
        self.threads: dict[str, list[str]] = {}
        for hid in self.hyps:                               # chronological, so a parent precedes its revision
            self.threads.setdefault(self.root_of(hid), []).append(hid)
        self.about: dict[str, list[dict]] = {}              # version id -> notes about it (explicit or inferred)
        self.about_of: dict[str, str] = {}                  # note id -> version id
        by_campaign: dict[str, list[dict]] = {}
        for n in self.notes:
            prior = by_campaign.setdefault(n["campaign"], [])
            if n["kind"] in ("finding", "critique", "attempt"):
                a = (n.get("claim") or {}).get("about")
                a = a if a in self.hyps else default_about(prior, n["kind"], n["role"])   # old notes lack `about`
                if a:
                    self.about.setdefault(a, []).append(n)
                    self.about_of[n["id"]] = a
            prior.append(n)

    # ---- identity
    def root_of(self, version: str) -> str:
        seen, cur = set(), version
        while cur in self.hyps and (p := self.hyps[cur]["claim"].get("revises")) in self.hyps and cur not in seen:
            seen.add(cur)
            cur = p
        return cur

    def resolve(self, ref: str) -> str:
        """Thread id for a claim id or any of its version ids."""
        if ref not in self.hyps:
            raise StatusError(f"unknown claim {ref!r} (a claim id is its first hypothesis note id, N-...)")
        return self.root_of(ref)

    def head(self, root: str) -> dict:
        return self.hyps[self.threads[root][-1]]

    def notes_about(self, version: str) -> list[dict]:
        return self.about.get(version, [])

    # ---- derived status
    def derive(self, version: str) -> dict:
        v = self.hyps[version]
        fp = fingerprint(v["claim"]["statement"], v["claim"]["scope"])
        about = self.notes_about(version)
        findings = [n for n in about if n["kind"] == "finding" and n.get("claim")]
        critiques = [n for n in about if n["kind"] == "critique" and n.get("claim")]
        counter = []
        for n in findings + critiques:
            c = n["claim"]
            if (n["kind"] == "finding" and c.get("result") == "refutes") or c.get("counterexample"):
                runs = [c["counterexample"]] if c.get("counterexample") else list(n.get("run_ids") or [])
                counter.append({"note": n["id"], "role": n["role"], "run_ids": runs, "named": bool(c.get("counterexample")),
                                "statement": c.get("statement") or c.get("objection")})
        support = [n for n in findings if n["claim"].get("result") == "supports" and n.get("run_ids")]
        support += [n for n in critiques if n.get("status") == "accept"]
        proofs = [p for p in self.proofs if p["version"] == version and p["fingerprint"] == fp]
        if counter:
            status = "refuted"
        elif proofs:
            status = "proven_AI"
        elif support:
            status = "numerically-supported"
        else:
            status = "open"
        grade = max((GRADES.index((n.get("evidence") or {}).get("grade", "none")) for n in support), default=0)
        last_crit = critiques[-1] if critiques else None
        accept = next((n for n in reversed(critiques) if n.get("status") == "accept"), None)
        after = critiques[critiques.index(accept) + 1:] if accept else critiques
        src = accept or (support[-1] if support else None)
        supported_as = None
        if src:
            c = src["claim"]
            supported_as = {"note": src["id"], "statement": c["statement"], "scope": c["scope"], "limits": c.get("limits")}
        narrowed = bool(accept) and (norm(accept["claim"]["statement"]), norm(accept["claim"]["scope"])) != \
            (norm(v["claim"]["statement"]), norm(v["claim"]["scope"]))
        return {
            "version": version, "fingerprint": fp, "status": status, "grade": GRADES[grade],
            "conflict": (f"proof {proofs[-1]['id']} and counterexample in {counter[-1]['note']} both exist for this "
                         "version; one of them is wrong") if counter and proofs else None,
            "counterexamples": counter, "proofs": [p["id"] for p in proofs], "support": [n["id"] for n in support],
            "critic": last_crit["status"] if last_crit else None, "narrowed": narrowed, "supported_as": supported_as,
            "open_objections": [n["id"] for n in after if n.get("status") == "challenge"],
            "findings": findings, "critiques": critiques,
        }

    # ---- human status and history
    def records(self, root: str) -> list[dict]:
        return [r for r in self.history if r["claim"] == root]

    def human(self, root: str) -> dict | None:
        last = next((r for r in reversed(self.records(root)) if r["source"] == "human"), None)
        return last if last and last.get("status") else None

    def view(self, root: str) -> dict:
        """Everything known about one claim thread: text, derived/human/effective status, flags, evidence."""
        head = self.head(root)
        d = self.derive(head["id"])
        h = self.human(root)
        stale = bool(h) and h["fingerprint"] != d["fingerprint"]
        changed = bool(h) and h.get("derived_at_set") != d["status"]
        effective = h["status"] if h else d["status"]
        why = []
        if not h and d["status"] == "proven_AI":
            why.append("proven_AI awaiting human review")
        if stale:
            why.append("claim revised since the human set its status")
        if changed:
            why.append(f"derived status moved from {h.get('derived_at_set')} to {d['status']} since the human set its status")
        if d["conflict"]:
            why.append("conflict: " + d["conflict"])
        c = head["claim"]
        versions = self.threads[root]
        campaigns = sorted({self.hyps[x]["campaign"] for x in versions}
                           | {n["campaign"] for x in versions for n in self.notes_about(x)}
                           | {p["campaign"] for p in self.proofs if p["claim"] == root and p.get("campaign")})
        return {
            "id": root, "version": head["id"], "versions": versions, "campaigns": campaigns,
            "statement": c["statement"], "scope": c["scope"], "refuted_if": c.get("refuted_if"),
            "fingerprint": d["fingerprint"], "effective": effective, "derived": d, "human": h,
            "stale": stale, "derived_changed": changed, "awaiting_review": bool(why), "review_reasons": why,
            "meaning": MEANING[effective],
            "attempts": [n for x in versions for n in self.notes_about(x) if n["kind"] == "attempt"],
            "proofs": [p for p in self.proofs if p["claim"] == root],
            "history": self.records(root),
        }

    def claim_ids(self, campaign: str | None = None) -> list[str]:
        if campaign is None:
            return list(self.threads)
        return [r for r in self.threads if campaign in self.view(r)["campaigns"]]


# ------------------------------------------------------------------ writes

def sync(ledger: Path, ref: str, actor: str, reason: str) -> dict | None:
    """Append a history record if the derived status (or the current version) of the claim changed."""
    snap = Snapshot(ledger)
    root = snap.resolve(ref)
    d = snap.derive(snap.head(root)["id"])
    last = next((r for r in reversed(snap.records(root)) if r["source"] != "human"), None)
    if last and last["status"] == d["status"] and last["version"] == d["version"]:
        return None
    return _append(ledger, {
        "at": _now(), "claim": root, "version": d["version"], "fingerprint": d["fingerprint"],
        "source": "ai" if d["status"] == "proven_AI" else "server", "actor": actor, "status": d["status"],
        "previous": last["status"] if last else None, "reason": reason,
        "basis": {"counterexamples": [c["note"] for c in d["counterexamples"]], "proofs": d["proofs"],
                  "support": d["support"]}})


def _write_proof(ledger: Path, snap: Snapshot, version: str, author: str, campaign: str | None, proof_text: str,
                 supporting_runs: list[str]) -> dict:
    v = snap.hyps[version]
    now = datetime.now(timezone.utc)
    pid = "P-" + now.strftime("%Y%m%dT%H%M%S%f") + "-" + secrets.token_hex(2)
    rec = {"schema": PROOF_SCHEMA, "id": pid, "created": now.isoformat(timespec="microseconds"), "author": author,
           "campaign": campaign, "claim": snap.root_of(version), "version": version,
           "fingerprint": fingerprint(v["claim"]["statement"], v["claim"]["scope"]),
           "statement": v["claim"]["statement"], "scope": v["claim"]["scope"], "format": "markdown",
           "proof": proof_text, "sha256": hashlib.sha256(proof_text.encode()).hexdigest(),
           "supporting_runs": supporting_runs}
    d = ledger / "proofs"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{pid}.json").write_text(json.dumps(rec, indent=2) + "\n", encoding="utf-8")
    return rec


def add_proof(ledger: Path, author: str, campaign: str, version: str, statement: str, scope: str,
              proof_text: str, supporting_runs: list[str] | None = None) -> dict:
    """Store a written proof bound to the exact current version of a claim (sets derived status proven_AI)."""
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
    proof_text = (proof_text or "").strip()
    if not MIN_PROOF <= len(proof_text) <= MAX_PROOF:
        raise StatusError(f"proof is {len(proof_text)} characters; it must be {MIN_PROOF}..{MAX_PROOF} and contain "
                          "the whole argument (every step and lemma, not a sketch)")
    runs = list(supporting_runs or [])
    for r in runs:
        if not (ledger / "runs" / r / "result.json").exists():
            raise StatusError(f"unknown run id {r!r} in supporting_runs")
    rec = _write_proof(ledger, snap, version, author, campaign, proof_text, runs)
    sync(ledger, root, author, f"proof {rec['id']} submitted")
    return rec


def set_human_status(ledger: Path, ref: str, status: str | None, reason: str, by: str, proof: str | None = None,
                     proof_text: str | None = None, counterexample: str | None = None, env=os.environ) -> dict:
    """The human's status for a claim (None clears it). The only writer of human statuses; never exposed over MCP."""
    if env.get("RESEARCH_ROLE") in AGENT_ROLES:
        raise StatusError("human statuses cannot be set from an agent process (RESEARCH_ROLE is set)")
    if status is not None and status not in STATUSES:
        raise StatusError(f"status must be one of {list(STATUSES)}")
    if not (reason or "").strip() or not (by or "").strip():
        raise StatusError("a reason and the reviewer id (--by) are required")
    snap = Snapshot(ledger)
    root = snap.resolve(ref)
    head = snap.head(root)
    d = snap.derive(head["id"])
    if status == "proven":
        if proof_text:
            proof = _write_proof(ledger, snap, head["id"], f"human:{by}", None, proof_text.strip(), [])["id"]
        elif proof:
            p = next((x for x in snap.proofs if x["id"] == proof), None)
            if p is None or p["claim"] != root:
                raise StatusError(f"proof {proof!r} is not a proof of claim {root}")
            if p["version"] != head["id"] or p["fingerprint"] != d["fingerprint"]:
                raise StatusError(f"proof {proof} is bound to version {p['version']}, but the current version is "
                                  f"{head['id']}; review a proof of the current text or pass --proof-file")
        else:
            raise StatusError("proven needs the reviewed proof: --proof P-... (a stored proof of the current version) "
                              "or --proof-file FILE")
    if status == "refuted" and not (counterexample or "").strip():
        raise StatusError("refuted needs --counterexample (a run id or a description of the counterexample)")
    h = snap.human(root)
    rec = {"at": _now(), "claim": root, "version": head["id"], "fingerprint": d["fingerprint"], "source": "human",
           "actor": "human", "by": by.strip(), "status": status,
           "previous": (h["status"] if h else d["status"]), "reason": reason.strip(), "derived_at_set": d["status"]}
    if proof and status == "proven":
        rec["proof"] = proof
    if status == "refuted":
        rec["counterexample"] = counterexample.strip()
    return _append(ledger, rec)


def review_queue(ledger: Path) -> list[dict]:
    snap = Snapshot(ledger)
    out = []
    for root in snap.claim_ids():
        v = snap.view(root)
        if v["awaiting_review"]:
            out.append({"claim": root, "statement": v["statement"], "effective": v["effective"],
                        "derived": v["derived"]["status"], "why": v["review_reasons"],
                        "proofs": v["derived"]["proofs"]})
    return out

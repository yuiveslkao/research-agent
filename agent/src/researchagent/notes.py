"""Append-only research notes shared between roles (ledger/notes/N-*.json)."""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from pathlib import Path

from .claims import MAX_TEXT_WITH_CLAIM, ClaimError, validate_claim

KINDS = {"question", "hypothesis", "finding", "decision", "critique", "attempt"}
ROLE_KINDS = {
    "researcher": {"question", "hypothesis", "finding", "decision", "attempt"},
    "critic": {"critique", "attempt"},
    "driver": {"question"},
    "human": set(KINDS),
}
STATUS = {"decision": {"continue", "conclude"}, "critique": {"accept", "challenge"}}
MAX_TEXT = 4000


class NoteError(ValueError):
    pass


def add_note(ledger: Path, role: str, campaign: str, kind: str, text: str = "",
             status: str | None = None, run_ids: list[str] | None = None, claim: dict | None = None,
             evidence: dict | None = None) -> dict:
    if kind not in KINDS:
        raise NoteError(f"kind must be one of {sorted(KINDS)}")
    if kind not in ROLE_KINDS.get(role, set()):
        raise NoteError(f"role {role!r} may not write {kind!r} notes "
                        f"(allowed: {sorted(ROLE_KINDS.get(role, set()))})")
    if kind in STATUS:
        if status not in STATUS[kind]:
            raise NoteError(f"{kind} notes need status in {sorted(STATUS[kind])}")
    elif status is not None:
        raise NoteError(f"{kind} notes take no status")
    try:
        claim = validate_claim(kind, status, claim)
    except ClaimError as e:
        raise NoteError(str(e)) from e
    limit = MAX_TEXT_WITH_CLAIM if claim else MAX_TEXT
    if len(text) > limit or (not claim and not text.strip()):
        raise NoteError(f"text is {len(text)} characters; it must be {'0' if claim else '1'}..{limit}. Shorten it "
                        "(the substance belongs in `claim`; text is optional extra context)")
    run_ids = list(run_ids or [])
    for rid in run_ids:
        if not (ledger / "runs" / rid / "result.json").exists():
            raise NoteError(f"unknown run id {rid!r}")
    now = datetime.now(timezone.utc)
    note_id = "N-" + now.strftime("%Y%m%dT%H%M%S%f") + "-" + secrets.token_hex(2)
    note = {"id": note_id, "created": now.isoformat(timespec="microseconds"), "role": role,
            "campaign": campaign, "kind": kind, "status": status, "text": text, "run_ids": run_ids,
            "claim": claim, "evidence": evidence}
    d = ledger / "notes"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{note_id}.json").write_text(json.dumps(note, indent=2) + "\n", encoding="utf-8")
    return note


def read_notes(ledger: Path, campaign: str | None = None, kind: str | None = None,
               limit: int = 50) -> list[dict]:
    """Chronological order, newest `limit` notes."""
    d = ledger / "notes"
    if not d.exists():
        return []
    notes = [json.loads(p.read_text()) for p in sorted(d.glob("N-*.json"))]
    if campaign is not None:
        notes = [n for n in notes if n["campaign"] == campaign]
    if kind is not None:
        notes = [n for n in notes if n["kind"] == kind]
    return notes[-limit:]

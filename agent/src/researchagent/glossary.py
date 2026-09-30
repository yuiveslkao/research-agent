"""Fixed vocabulary: the five claim statuses and the words agents may not use to describe their own results.

Statuses are conferred by records (runs, counterexamples, proof artifacts, a human review), never by wording.
The server therefore rejects status vocabulary ("proved", "established", "rigorous", ...) in the assertion fields of
agent-written notes. Negated uses ("not proven", "no proof found") are allowed; attempt notes, objections, challenge
text and test plans (`refuted_if`, `resolve_by`) are exempt because they do not assert that something holds.
"""
from __future__ import annotations

import re

STATUSES = ("open", "refuted", "numerically-supported", "proven_AI", "proven")

MEANING = {
    "open": "No conclusion yet.",
    "refuted": "A counterexample was recorded (a named run, or set by the human).",
    "numerically-supported": "No counterexample found; cited runs support the claim within the stated scope and "
                             "limits. Not a proof.",
    "proven_AI": "Proved by the AI: a structured proof of this exact statement is stored and the AI review pass raised "
                 "no blocking objection. Not human-reviewed.",
    "proven": "Proof reviewed by a human (reviewer and date recorded). Only a human can set this.",
}

# Evidence flags (not statuses): what `certify` and `exhaustive` checks establish.
CERTIFIED = ("the claim's margin was enclosed above 0 in interval arithmetic (MPFR outward rounding) at every evaluated "
             "point: it holds exactly at those finitely many points. Not a proof over the whole scope; it never sets "
             "proven or proven_AI. A certified counterexample is a violation enclosed below 0 (certify) or found in "
             "exact arithmetic (exhaustive).")
EXHAUSTIVE = ("an exact check held at every point of a finite DOMAIN (complete enumeration). It settles that finite "
              "statement only; a claim about a larger range still needs a proof, and it never sets proven or proven_AI.")

# Who may confer each status (documentation and CLI help; enforcement lives in status.py and tools.py).
SET_BY = {
    "open": "server",
    "refuted": "server (a note naming a counterexample run) or human",
    "numerically-supported": "server (a supporting finding or critic accept citing runs)",
    "proven_AI": "AI: submit_proof (researcher) and an accepting review_proof (critic)",
    "proven": "human only (research-claims set ... proven)",
}

RESERVED = re.compile(
    r"\b(prov(?:e|es|ed|en|ing|able|ably)|proofs?|establish(?:es|ed|ing)?|rigorous(?:ly)?|"
    r"certif(?:y|ies|ied|ication|icate)|guarantee[sd]?|q\.?e\.?d)\b", re.I)
_NEGATION = re.compile(r"\b(?:not|no|without|never|nor|cannot|unable|neither)\b(?:\W+\w+){0,2}\W*$", re.I)

# Note fields checked for reserved words. Attempt notes are exempt (their outcome is never success).
ASSERTION_FIELDS = ("statement", "scope", "limits")

HINT = ("Reserved words (prove/proof/established/rigorous/certified/guaranteed) name statuses that only proof "
        "records or the human confer. Say what was observed: 'held on the tested grid', 'supported for N<=128', "
        "'survived falsification'. For literature write 'shown in arXiv:ID' and put the id in sources. "
        "Negations ('not proven', 'no proof found') are allowed.")


class GlossaryError(ValueError):
    pass


def reserved_uses(text: str) -> list[str]:
    """Reserved words used affirmatively in text (a negation within the two preceding words excuses a use)."""
    bad = []
    for m in RESERVED.finditer(text or ""):
        if not _NEGATION.search(text[:m.start()]):
            bad.append(m.group(0))
    return bad


def check(field: str, text: str) -> None:
    bad = reserved_uses(text)
    if bad:
        raise GlossaryError(f"{field} uses reserved word(s) {sorted(set(w.lower() for w in bad))}. {HINT}")


def check_note(kind: str, claim: dict | None, text: str, status: str | None = None) -> None:
    """Server-side vocabulary rule for agent-written notes. A challenge argues against a claim, so its text is exempt."""
    if kind in ("attempt", "question", "novelty"):          # novelty notes may say what a paper proves
        return
    for f in ASSERTION_FIELDS:
        if claim and isinstance(claim.get(f), str):
            check(f"claim.{f}", claim[f])
    if not (kind == "critique" and status == "challenge"):
        check("text", text)

"""research-claims: the human's view of claim statuses, the review queue, and the only way to set a human status.

  research-claims list [--status S] [--campaign C] [--json]
  research-claims queue [--json]                    proven_AI claims awaiting review, stale or changed human statuses
  research-claims show CLAIM [--proof] [--json]     text, statuses, evidence, proofs, attempts, history
  research-claims set CLAIM STATUS --reason R --by ID [--proof P-... | --proof-file F] [--counterexample X]
  research-claims clear CLAIM --reason R --by ID    remove the human status (the derived one applies again)
  research-claims report [--campaign C] [--out FILE] [--md]

Agents cannot run this (they have no shell); status.set_human_status also refuses inside an agent's MCP process.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import report as report_mod
from . import status as status_mod
from .claims import compact_claim, registry
from .glossary import MEANING, STATUSES


def _print_json(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def _row(c: dict) -> str:
    flags = [k for k in ("set_by_human", "stale", "derived_changed") if c.get(k)] + (["CONFLICT"] if c.get("conflict") else [])
    return (f"{c['id']}  {c['status']:<22} {c.get('grade') or '-':<20} {c.get('campaign') or '-'}"
            + (f"  [{', '.join(flags)}]" if flags else "") + f"\n    {c['statement']}\n    scope: {c['scope']}")


def cmd_list(ledger: Path, a) -> int:
    cs = registry(ledger, a.campaign)["claims"]
    if a.status:
        cs = [c for c in cs if c["status"] == a.status]
    if a.json:
        _print_json(cs)
    else:
        print("\n".join(_row(c) for c in cs) or "(no claims)")
    return 0


def cmd_queue(ledger: Path, a) -> int:
    q = status_mod.review_queue(ledger)
    if a.json:
        _print_json(q)
        return 0
    if not q:
        print("review queue is empty")
    for item in q:
        print(f"{item['claim']}  {item['effective']} (derived {item['derived']})  proofs: {', '.join(item['proofs']) or '-'}"
              f"\n    {item['statement']}\n    why: {'; '.join(item['why'])}")
    return 0


def cmd_show(ledger: Path, a) -> int:
    snap = status_mod.Snapshot(ledger)
    root = snap.resolve(a.claim)
    entry = report_mod.claim_entry(snap, root)
    if a.json:
        _print_json(entry)
        return 0
    print(_row(compact_claim(snap.view(root))))
    objections = [o for o in registry(ledger)["open_objections"] if o["claim"] == root]
    print("\n".join(report_mod.markdown_claim(entry, objections)[2:]))
    print("history:")
    for r in entry["history"]:
        who = r.get("by") or r["actor"]
        print(f"  {r['at'][:19]}  {r['source']:<6} {who:<12} {r.get('previous')} -> {r.get('status')}  {r.get('reason', '')}")
    if a.proof:
        for p in snap.view(root)["proofs"]:
            print(f"\n===== {p['id']} ({p['author']}, {p['created'][:19]}) =====\n"
                  f"Statement: {p['statement']}\nScope: {p['scope']}\n\n{p['proof']}")
    return 0


def cmd_set(ledger: Path, a) -> int:
    text = Path(a.proof_file).read_text(encoding="utf-8") if a.proof_file else None
    rec = status_mod.set_human_status(ledger, a.claim, a.status, a.reason, a.by, proof=a.proof, proof_text=text,
                                      counterexample=a.counterexample)
    print(f"{rec['claim']}: human status {rec['previous']} -> {rec['status']} ({MEANING[rec['status']]})")
    return 0


def cmd_clear(ledger: Path, a) -> int:
    rec = status_mod.set_human_status(ledger, a.claim, None, a.reason, a.by)
    print(f"{rec['claim']}: human status cleared (derived status {rec['derived_at_set']} applies)")
    return 0


def cmd_report(ledger: Path, a) -> int:
    rep = report_mod.build_report(ledger, a.campaign)
    if a.md:
        text = "\n".join(report_mod.markdown_claims(rep)) + "\n"
    else:
        text = json.dumps(rep, indent=2, ensure_ascii=False) + "\n"
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
        print(f"wrote {a.out}")
    else:
        sys.stdout.write(text)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="research-claims", description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", default=".", help="repository root (default: cwd)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("list")
    p.add_argument("--status", choices=STATUSES)
    p.add_argument("--campaign")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("queue")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("show")
    p.add_argument("claim")
    p.add_argument("--proof", action="store_true", help="print the full proof texts")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("set", help="set the human status of a claim")
    p.add_argument("claim")
    p.add_argument("status", choices=STATUSES)
    p.add_argument("--reason", required=True)
    p.add_argument("--by", required=True, help="reviewer id")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--proof", help="for proven: the reviewed proof record P-... (bound to the current version)")
    g.add_argument("--proof-file", help="for proven: a file with your own proof (stored as a proof record)")
    p.add_argument("--counterexample", help="for refuted: a run id or a description")
    p = sub.add_parser("clear", help="remove the human status")
    p.add_argument("claim")
    p.add_argument("--reason", required=True)
    p.add_argument("--by", required=True)
    p = sub.add_parser("report", help="JSON report (schema report/0.1) of a campaign or the whole ledger")
    p.add_argument("--campaign")
    p.add_argument("--out")
    p.add_argument("--md", action="store_true", help="Markdown claims view instead of JSON")
    a = ap.parse_args(argv)
    ledger = Path(a.root).resolve() / "ledger"
    try:
        return {"list": cmd_list, "queue": cmd_queue, "show": cmd_show, "set": cmd_set, "clear": cmd_clear,
                "report": cmd_report}[a.cmd](ledger, a)
    except (status_mod.StatusError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

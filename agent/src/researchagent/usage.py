"""Usage accounting: tool-call sizes (a token proxy), turn times, and best-effort Hermes token counts.

The MCP server logs every tool call to ledger/calls.jsonl (chars of arguments and results); the driver logs each
turn to ledger/campaigns/<id>/turns.jsonl. Estimated tokens = chars / 3.5 (numbers tokenize worse, so treat it as
a rough lower bound). Exact model tokens are only available if Hermes stores them: `--hermes` tries its session
database, which is schema-agnostic and may report nothing.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

CHARS_PER_TOKEN = 3.5


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()] if path.exists() else []


def summarize_calls(ledger: Path, campaign: str | None = None) -> dict:
    """Per role and tool: calls, failed calls, argument chars (written by the agent), result chars (read by it)."""
    out: dict[str, dict] = {}
    for r in _jsonl(ledger / "calls.jsonl"):
        if campaign and r["campaign"] != campaign:
            continue
        s = out.setdefault(r["role"], {"calls": 0, "failed": 0, "args_chars": 0, "result_chars": 0, "by_tool": {}})
        s["calls"] += 1
        s["failed"] += 0 if r.get("ok", True) else 1
        s["args_chars"] += r["args_chars"]
        s["result_chars"] += r["result_chars"]
        s["by_tool"][r["tool"]] = s["by_tool"].get(r["tool"], 0) + 1
    for s in out.values():
        s["est_out_tokens"] = int(s["args_chars"] / CHARS_PER_TOKEN)
        s["est_tool_result_tokens"] = int(s["result_chars"] / CHARS_PER_TOKEN)
    return out


def summarize_turns(campaign_dir: Path) -> list[dict]:
    return _jsonl(campaign_dir / "turns.jsonl")


def _epoch(v) -> float | None:
    if isinstance(v, (int, float)):
        return float(v) / (1000 if v > 1e12 else 1)
    if isinstance(v, str):
        try:
            dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
        return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()
    return None


def hermes_usage(profile_dir: Path, since: float, until: float) -> dict | None:
    """Sum token columns of Hermes' session database between two epoch times (best effort, schema-agnostic)."""
    db = profile_dir / "state.db"
    if not db.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        found: dict | None = None
        for (table,) in con.execute("select name from sqlite_master where type='table'").fetchall():
            cols = [c[1] for c in con.execute(f'pragma table_info("{table}")').fetchall()]
            tok = [c for c in cols if "token" in c.lower()]
            tcol = next((c for c in cols if c.lower() in ("started_at", "created_at", "timestamp", "start_time", "ts",
                                                           "updated_at", "ended_at")), None)
            if not tok or not tcol:
                continue
            sums = {c: 0 for c in tok}
            n = 0
            for row in con.execute(f'select "{tcol}", ' + ", ".join(f'"{c}"' for c in tok) + f' from "{table}"'):
                t = _epoch(row[0])
                if t is not None and since <= t <= until:
                    n += 1
                    for c, v in zip(tok, row[1:]):
                        sums[c] += v if isinstance(v, (int, float)) else 0
            if n and any(sums.values()):
                found = {"table": table, "rows": n, **sums}
                break
        con.close()
        return found
    except sqlite3.Error:
        return None


def render(campaign: str, calls: dict, turns: list[dict], hermes: dict[str, dict | None] | None = None) -> str:
    L = [f"Usage for campaign {campaign} (estimates; tokens ~ chars/{CHARS_PER_TOKEN})", ""]
    if turns:
        L += ["| round | role | minutes |", "|---|---|---|"]
        L += [f"| {t['round']} | {t['role']} | {t['seconds'] / 60:.1f} |" for t in turns]
        L.append(f"\nTotal turn time: {sum(t['seconds'] for t in turns) / 60:.1f} min\n")
    L += ["| role | tool calls | failed | agent-written (est. tokens) | tool results read (est. tokens) |",
          "|---|---|---|---|---|"]
    for role, s in sorted(calls.items()):
        L.append(f"| {role} | {s['calls']} | {s['failed']} | {s['est_out_tokens']} | {s['est_tool_result_tokens']} |")
    for role, s in sorted(calls.items()):
        L.append(f"\n{role} calls by tool: " + ", ".join(f"{k}={v}" for k, v in sorted(s["by_tool"].items())))
    if hermes:
        L.append("\nHermes session database (may be incomplete):")
        for role, h in hermes.items():
            L.append(f"- {role}: " + (", ".join(f"{k}={v}" for k, v in h.items()) if h else "no token counts found"))
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="research-usage")
    ap.add_argument("--root", default=".")
    ap.add_argument("--campaign", help="campaign id (default: the latest)")
    ap.add_argument("--hermes", action="store_true", help="also try Hermes' session database for token counts")
    ap.add_argument("--hermes-home", default=os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
    a = ap.parse_args(argv)
    ledger = Path(a.root).resolve() / "ledger"
    cid = a.campaign
    if not cid:
        cur = ledger / "campaigns" / "CURRENT"
        if not cur.exists():
            print("no campaign found", file=sys.stderr)
            return 2
        cid = json.loads(cur.read_text())["id"]
    turns = summarize_turns(ledger / "campaigns" / cid)
    hermes = None
    if a.hermes and turns:
        since = min(_epoch(t["started"]) or 0 for t in turns) - 5
        until = max(_epoch(t["ended"]) or 0 for t in turns) + 5
        hermes = {r: hermes_usage(Path(a.hermes_home) / "profiles" / r, since, until) for r in ("researcher", "critic")}
    print(render(cid, summarize_calls(ledger, cid), turns, hermes))
    return 0


if __name__ == "__main__":
    sys.exit(main())

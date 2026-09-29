"""Campaign driver: researcher turn -> critic turn -> repeat, through Hermes profiles.

The driver only sequences turns, enforces the round limit, and renders a report. All research
actions happen through the MCP tools inside each Hermes turn.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from . import notes as notes_mod
from .config import Ctx, Settings, set_current_campaign
from . import tools

DEFAULT_HERMES_CMD = ["hermes", "-p", "{profile}", "-z", "{prompt}"]


class Agent(Protocol):
    def ask(self, profile: str, prompt: str) -> str: ...


class HermesCLI:
    """Runs one Hermes turn as a subprocess. Override the command with RESEARCH_HERMES_CMD (JSON list)."""

    def __init__(self, cmd: list[str] | None = None, timeout: int = 3600):
        env = os.environ.get("RESEARCH_HERMES_CMD")
        self.cmd = cmd or (json.loads(env) if env else DEFAULT_HERMES_CMD)
        self.timeout = timeout

    def ask(self, profile: str, prompt: str) -> str:
        argv = [c.replace("{profile}", profile).replace("{prompt}", prompt) for c in self.cmd]
        r = subprocess.run(argv, capture_output=True, text=True, timeout=self.timeout)
        if r.returncode != 0:
            raise RuntimeError(f"hermes exited {r.returncode}: {r.stderr.strip()[-800:]}")
        return r.stdout.strip()


def researcher_prompt(question: str, rnd: int, rounds: int) -> str:
    return (f"Campaign round {rnd} of {rounds}.\n\nResearch question:\n{question}\n\n"
            "Follow your round procedure: read_notes first, then hypothesis, experiment, inspect, "
            "finding, decision. Use only the research tools.")


def critic_prompt(question: str, rnd: int, rounds: int) -> str:
    return (f"Campaign round {rnd} of {rounds}. Critic turn.\n\nResearch question:\n{question}\n\n"
            "Read the notes and the runs they cite, run at least one independent attack, then record "
            "your critique. If the researcher recorded nothing new this round, challenge that.")


def campaign_state(notes: list[dict]) -> dict:
    """Pure function of the chronological notes: is the campaign finished?"""
    dec = next((n for n in reversed(notes) if n["kind"] == "decision"), None)
    crit = next((n for n in reversed(notes) if n["kind"] == "critique"), None)
    accepted = bool(dec and crit and dec["status"] == "conclude" and crit["status"] == "accept"
                    and crit["created"] > dec["created"])
    return {"accepted": accepted, "last_decision": dec and dec["status"],
            "last_critique": crit and crit["status"]}


@dataclass
class CampaignResult:
    campaign_id: str
    stopped_by: str
    rounds_run: int
    report_path: Path
    errors: list[str] = field(default_factory=list)


def render_report(cid: str, question: str, stopped_by: str, notes: list[dict], runs: list[dict],
                  transcript: list[tuple[int, str, str]]) -> str:
    L = [f"# Campaign {cid}", "", f"**Question:** {question}", "", f"**Stopped by:** {stopped_by}", "",
         "## Notes (chronological)", ""]
    for n in notes:
        st = f" [{n['status']}]" if n["status"] else ""
        runs_s = f" (runs: {', '.join(n['run_ids'])})" if n["run_ids"] else ""
        L += [f"### {n['created'][:19]} {n['role']} / {n['kind']}{st}{runs_s}", "", n["text"], ""]
    L += ["## Runs", "", "| run | kind | status | role | title |", "|---|---|---|---|---|"]
    L += [f"| {r['run_id']} | {r['kind']} | {r['status']} | {r['role']} | {r['title']} |" for r in runs]
    L += ["", "## Agent turn summaries", ""]
    for rnd, who, text in transcript:
        L += [f"**Round {rnd}, {who}:**", "", text or "(no output)", ""]
    return "\n".join(L) + "\n"


def run_campaign(question: str, rounds: int, root: Path, agent: Agent, max_runs: int = 30,
                 campaign_id: str | None = None) -> CampaignResult:
    root = Path(root).resolve()
    cid = campaign_id or "C-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(2)
    ctx = Ctx(Settings(root=root, role="driver", executor="local"))   # driver never runs jobs
    ledger = ctx.paths.ledger
    set_current_campaign(ledger, cid, max_runs)
    cdir = ledger / "campaigns" / cid
    cdir.mkdir(parents=True, exist_ok=True)
    notes_mod.add_note(ledger, "driver", cid, "question", question)
    (cdir / "campaign.json").write_text(json.dumps(
        {"id": cid, "question": question, "rounds": rounds, "max_runs": max_runs}, indent=2))

    def critic_runs() -> int:
        return tools.attack_count(ledger, cid, "critic")

    transcript: list[tuple[int, str, str]] = []
    errors: list[str] = []
    stopped_by, rnd = "max_rounds", 0
    try:
        for rnd in range(1, rounds + 1):
            transcript.append((rnd, "researcher", agent.ask("researcher", researcher_prompt(question, rnd, rounds))))
            before = critic_runs()
            transcript.append((rnd, "critic", agent.ask("critic", critic_prompt(question, rnd, rounds))))
            attacked = critic_runs() > before
            if campaign_state(notes_mod.read_notes(ledger, cid))["accepted"]:
                if attacked:
                    stopped_by = "accepted"
                    break
                transcript.append((rnd, "driver", "Critique 'accept' ignored: the critic ran no independent "
                                                  "attack (no run or check) this round."))
    except Exception as e:  # agent/CLI failure: keep everything recorded so far
        stopped_by = f"error: {e}"
        errors.append(str(e))
    ctx.settings = Settings(root=root, role="human", executor="local")
    report = render_report(cid, question, stopped_by, notes_mod.read_notes(ledger, cid),
                           tools.list_runs(ctx, limit=100), transcript)
    (cdir / "report.md").write_text(report, encoding="utf-8")
    return CampaignResult(cid, stopped_by, rnd, cdir / "report.md", errors)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="research-loop")
    ap.add_argument("--root", default=".")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--question")
    g.add_argument("--question-file")
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--max-runs", type=int, default=30)
    ap.add_argument("--turn-timeout", type=int, default=3600)
    a = ap.parse_args(argv)
    q = a.question or Path(a.question_file).read_text().strip()
    res = run_campaign(q, a.rounds, Path(a.root), HermesCLI(timeout=a.turn_timeout), a.max_runs)
    print(f"campaign {res.campaign_id}: {res.stopped_by} after {res.rounds_run} round(s)\nreport: {res.report_path}")
    return 0 if not res.errors else 1


if __name__ == "__main__":
    sys.exit(main())

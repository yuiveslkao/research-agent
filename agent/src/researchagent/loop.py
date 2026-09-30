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
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from . import claims as claims_mod
from . import notes as notes_mod
from . import report as report_mod
from . import tools, usage
from .config import Ctx, Settings, set_current_campaign

DEFAULT_HERMES_CMD = ["hermes", "-p", "{profile}", "-z", "{prompt}"]


class Agent(Protocol):
    def ask(self, profile: str, prompt: str) -> str: ...


class HermesCLI:
    """Runs one Hermes turn as a subprocess. Override the command with RESEARCH_HERMES_CMD (JSON list).

    `on_wait(elapsed_s)` is called every `heartbeat_s` seconds while the turn is running.
    """

    def __init__(self, cmd: list[str] | None = None, timeout: int = 3600, heartbeat_s: float = 60.0):
        env = os.environ.get("RESEARCH_HERMES_CMD")
        self.cmd = cmd or (json.loads(env) if env else DEFAULT_HERMES_CMD)
        self.timeout = timeout
        self.heartbeat_s = heartbeat_s
        self.on_wait = None

    def ask(self, profile: str, prompt: str) -> str:
        argv = [c.replace("{profile}", profile).replace("{prompt}", prompt) for c in self.cmd]
        t0 = time.monotonic()
        p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                             stdin=subprocess.DEVNULL)
        while True:
            try:
                out, err = p.communicate(timeout=self.heartbeat_s)
                break
            except subprocess.TimeoutExpired:
                elapsed = time.monotonic() - t0
                if elapsed > self.timeout:
                    p.kill()
                    p.communicate()
                    raise RuntimeError(f"hermes turn for {profile} exceeded {self.timeout} s and was killed")
                if self.on_wait:
                    self.on_wait(elapsed)
        if p.returncode != 0:
            raise RuntimeError(f"hermes exited {p.returncode}\n  stderr: {err.strip()[-800:] or '(empty)'}"
                               f"\n  stdout: {out.strip()[-800:] or '(empty)'}")
        return out.strip()


def activity(ledger: Path, cid: str) -> dict:
    """Progress signal for the driver: counts and seconds since the ledger last changed."""
    files = list((ledger / "notes").glob("N-*.json")) + list((ledger / "runs").glob("R-*/result.json"))
    audit = ledger / "audit.jsonl"
    if audit.exists():
        files.append(audit)
    last = max((f.stat().st_mtime for f in files), default=None)
    return {"notes": len(notes_mod.read_notes(ledger, cid, limit=10_000)),
            "runs_started": sum(1 for _ in (ledger / "runs").glob("R-*/result.json")),
            "idle_s": None if last is None else max(0.0, time.time() - last)}


def researcher_prompt(question: str, rnd: int, rounds: int) -> str:
    return (f"Round {rnd}/{rounds}. Question:\n{question}\n\n"
            "Follow your round procedure (read_notes first). Use only the research tools.")


def critic_prompt(question: str, rnd: int, rounds: int) -> str:
    return (f"Round {rnd}/{rounds}, critic turn. Question:\n{question}\n\n"
            "Read the notes and the runs they cite, run at least one independent attack, then record your "
            "critique. If the researcher recorded nothing new this round, challenge that.")


def campaign_state(notes: list[dict]) -> dict:
    """Pure function of the chronological notes: is the campaign finished?"""
    dec = next((n for n in reversed(notes) if n["kind"] == "decision"), None)
    crit = next((n for n in reversed(notes) if n["kind"] == "critique"), None)
    accepted = bool(dec and crit and dec["status"] == "conclude" and crit["status"] == "accept"
                    and crit["created"] > dec["created"])
    return {"accepted": accepted, "last_decision": dec and dec["status"],
            "last_critique": crit and crit["status"]}


def next_step(notes: list[dict]) -> tuple[int, str]:
    """Where an interrupted campaign continues: (round, role). One decision per researcher turn, one critique per critic turn."""
    decisions = sum(1 for n in notes if n["kind"] == "decision")
    critiques = sum(1 for n in notes if n["kind"] == "critique")
    return (decisions, "critic") if decisions > critiques else (critiques + 1, "researcher")


@dataclass
class CampaignResult:
    campaign_id: str
    stopped_by: str
    rounds_run: int
    report_path: Path
    errors: list[str] = field(default_factory=list)


def _claim_lines(n: dict) -> list[str]:
    c = n.get("claim") or {}
    lines = [f"- **{k}:** {v}" for k, v in c.items() if k != "sources"]
    ev = n.get("evidence") or {}
    if ev:
        flags = [k for k in ("reproduced", "precision_checked", "independent") if ev.get(k)]
        if ev.get("falsify"):
            flags.append(f"falsify={ev['falsify']}")
        lines.append(f"- **evidence (server-derived):** {ev['grade']}" + (f" ({', '.join(flags)})" if flags else "")
                     + f", {ev['runs']} run(s)")
        if ev.get("sources"):
            lines.append("- **sources (verified on arXiv):** " + "; ".join(f"{s['id']} {s['title']}" for s in ev["sources"]))
    return lines


def render_report(cid: str, question: str, stopped_by: str, notes: list[dict], runs: list[dict],
                  transcript: list[tuple[int, str, str]], usage_md: str = "", falsify_runs: int = 0,
                  report: dict | None = None) -> str:
    """Markdown view. The claims section is rendered from the JSON report (report.json), which is primary."""
    dec = next((n for n in reversed(notes) if n["kind"] == "decision"), None)
    crit = next((n for n in reversed(notes) if n["kind"] == "critique"), None)
    L = [f"# Campaign {cid}", "", f"**Question:** {question}", "", f"**Stopped by:** {stopped_by}", "",
         "Nothing in this report is a proof unless a claim's status is `proven` (human-reviewed) or `proven_AI` "
         "(proved by the AI, not human-reviewed).", "", "## Outcome", ""]
    if crit and crit["status"] == "accept":
        L += ["**Critic's last verdict: could not break it.** The critic's restatement below held in the tested "
              "scope (numerically supported, not a proof):"] + _claim_lines(crit) + [""]
    elif crit:
        L += ["**Critic's last verdict: open objection:**"] + _claim_lines(crit) + [""]
    L += [f"**Last researcher decision** [{dec['status'] if dec else 'none'}]: {(dec or {}).get('text') or '(none)'}", "",
          f"**Falsification attempted:** {'yes (' + str(falsify_runs) + ' check run(s))' if falsify_runs else 'no'}", ""]
    if report is not None:
        L += report_mod.markdown_claims(report)
    L += ["## Notes (chronological)", ""]
    for n in notes:
        st = f" [{n['status']}]" if n.get("status") else ""
        runs_s = f" (runs: {', '.join(n['run_ids'])})" if n.get("run_ids") else ""
        L += [f"### {n['created'][:19]} {n['role']} / {n['kind']}{st}{runs_s}", ""]
        body = _claim_lines(n)
        L += body + ([""] if body else [])
        if n.get("text"):
            L += [n["text"], ""]
    L += ["## Runs", "", "| run | kind | status | role | title |", "|---|---|---|---|---|"]
    L += [f"| {r['run_id']} | {r['kind']} | {r['status']} | {r['role']} | {r['title']} |" for r in runs]
    if usage_md:
        L += ["", "## Usage", "", usage_md]
    L += ["", "## Agent turn summaries", ""]
    for rnd, who, text in transcript:
        L += [f"**Round {rnd}, {who}:**", "", text or "(no output)", ""]
    return "\n".join(L) + "\n"


def _load_turns(cdir: Path) -> list[dict]:
    return usage.summarize_turns(cdir)


def run_campaign(question: str | None, rounds: int, root: Path, agent: Agent, max_runs: int = 30,
                 campaign_id: str | None = None, progress=None, resume: bool = False, prior: bool = True,
                 prior_limit: int = 6) -> CampaignResult:
    progress = progress or (lambda msg: None)
    root = Path(root).resolve()
    ctx = Ctx(Settings(root=root, role="driver", executor="local"))   # driver never runs jobs
    ledger = ctx.paths.ledger
    if resume:
        if not campaign_id:
            raise ValueError("resume needs a campaign id")
        meta = json.loads((ledger / "campaigns" / campaign_id / "campaign.json").read_text())
        question = question or meta["question"]
        rounds = rounds or meta["rounds"]
        max_runs = max_runs or meta["max_runs"]
    cid = campaign_id or "C-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(2)
    set_current_campaign(ledger, cid, max_runs)
    cdir = ledger / "campaigns" / cid
    cdir.mkdir(parents=True, exist_ok=True)
    if not resume:
        seed = question
        if prior:
            b = claims_mod.brief(ledger, prior_limit, exclude_campaign=cid)
            if b:
                seed += ("\n\nEarlier campaigns' claims with their status (build on them, do not re-derive or "
                         "retry failed attempts; challenge them only with new evidence; only `proven` is "
                         "human-reviewed):\n" + b)
        notes_mod.add_note(ledger, "driver", cid, "question", seed)
        (cdir / "campaign.json").write_text(json.dumps(
            {"id": cid, "question": question, "rounds": rounds, "max_runs": max_runs}, indent=2))

    def critic_runs() -> int:
        return tools.attack_count(ledger, cid, "critic")

    turns_log = cdir / "turns.jsonl"

    def turn(rnd: int, who: str, prompt: str) -> str:
        t0, started = time.monotonic(), datetime.now(timezone.utc).isoformat(timespec="seconds")
        progress(f"round {rnd}/{rounds} {who}: turn started")

        def waiting(elapsed: float) -> None:
            a = activity(ledger, cid)
            idle = a["idle_s"]
            idle_s = "no ledger activity yet" if idle is None else f"last ledger change {idle:.0f}s ago"
            warn = ""
            if idle is not None and idle > 600 or (idle is None and elapsed > 600):
                warn = "  <-- no progress for 10+ min; check `docker ps` and ~/.hermes/profiles/%s/logs/agent.log" % who
            progress(f"  {who} still running {elapsed/60:.1f} min | notes {a['notes']}, runs {a['runs_started']} | {idle_s}{warn}")

        if hasattr(agent, "on_wait"):
            agent.on_wait = waiting
        out = agent.ask(who, prompt)
        secs = time.monotonic() - t0
        with open(turns_log, "a", encoding="utf-8") as f:
            f.write(json.dumps({"round": rnd, "role": who, "seconds": round(secs, 1), "started": started,
                                "ended": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                "summary": out}) + "\n")
        progress(f"round {rnd}/{rounds} {who}: turn finished in {secs/60:.1f} min")
        return out

    transcript: list[tuple[int, str, str]] = [(t["round"], t["role"], t.get("summary", "")) for t in _load_turns(cdir)]
    errors: list[str] = []
    stopped_by, rnd = "max_rounds", 0
    notes = notes_mod.read_notes(ledger, cid, limit=100_000)
    start_round, start_role = next_step(notes) if resume else (1, "researcher")
    try:
        if resume and campaign_state(notes)["accepted"]:
            stopped_by = "accepted"
        else:
            for rnd in range(start_round, rounds + 1):
                if not (rnd == start_round and start_role == "critic"):
                    transcript.append((rnd, "researcher", turn(rnd, "researcher", researcher_prompt(question, rnd, rounds))))
                before = critic_runs()
                transcript.append((rnd, "critic", turn(rnd, "critic", critic_prompt(question, rnd, rounds))))
                attacked = critic_runs() > before
                if campaign_state(notes_mod.read_notes(ledger, cid, limit=100_000))["accepted"]:
                    if attacked:
                        stopped_by = "accepted"
                        break
                    transcript.append((rnd, "driver", "Critique 'accept' ignored: the critic ran no independent "
                                                      "attack (no run or check) this round."))
    except Exception as e:  # agent/CLI failure: keep everything recorded so far
        stopped_by = f"error: {e}"
        errors.append(str(e))
    ctx.settings = Settings(root=root, role="human", executor="local")
    all_notes = notes_mod.read_notes(ledger, cid, limit=100_000)
    runs = tools.list_runs(ctx, limit=100)
    usage_md = usage.render(cid, usage.summarize_calls(ledger, cid), _load_turns(cdir))
    falsify_runs = sum(1 for r in runs if r["kind"] == "check" and (r.get("title") or "").startswith("falsify"))
    rep, _ = report_mod.write_campaign_report(ledger, cid, {"question": question, "stopped_by": stopped_by,
                                                            "rounds": rounds, "max_runs": max_runs})
    md = render_report(cid, question, stopped_by, all_notes, runs, transcript, usage_md, falsify_runs, rep)
    (cdir / "report.md").write_text(md, encoding="utf-8")
    return CampaignResult(cid, stopped_by, rnd, cdir / "report.md", errors)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="research-loop")
    ap.add_argument("--root", default=".")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--question")
    g.add_argument("--question-file")
    ap.add_argument("--resume", metavar="CAMPAIGN_ID", help="continue an interrupted campaign")
    ap.add_argument("--rounds", type=int, default=None, help="default 3 (or the campaign's own with --resume)")
    ap.add_argument("--max-runs", type=int, default=None, help="default 30 (or the campaign's own with --resume)")
    ap.add_argument("--no-prior", action="store_true", help="do not seed the campaign with earlier accepted claims")
    ap.add_argument("--prior-limit", type=int, default=6, help="max entries per kind in the earlier-claims brief")
    ap.add_argument("--turn-timeout", type=int, default=1800, help="kill a Hermes turn after this many seconds")
    ap.add_argument("--heartbeat", type=float, default=60.0, help="seconds between progress lines")
    a = ap.parse_args(argv)
    if not (a.resume or a.question or a.question_file):
        ap.error("give --question, --question-file or --resume")
    q = a.question or (Path(a.question_file).read_text().strip() if a.question_file else None)

    def progress(msg: str) -> None:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)
    rounds = a.rounds or (None if a.resume else 3)
    max_runs = a.max_runs or (None if a.resume else 30)
    res = run_campaign(q, rounds, Path(a.root), HermesCLI(timeout=a.turn_timeout, heartbeat_s=a.heartbeat),
                       max_runs, campaign_id=a.resume, progress=progress, resume=bool(a.resume),
                       prior=not a.no_prior, prior_limit=a.prior_limit)
    print(f"campaign {res.campaign_id}: {res.stopped_by} after {res.rounds_run} round(s)\nreport: {res.report_path}"
          f"\njson:   {res.report_path.with_suffix('.json')}")
    return 0 if not res.errors else 1


if __name__ == "__main__":
    sys.exit(main())

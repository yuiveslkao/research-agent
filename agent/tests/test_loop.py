import json

from conftest import ACC, CHAL, FND, HYP
from researchagent import tools
from researchagent.config import Ctx, Settings
from researchagent.loop import HermesCLI, campaign_state, next_step, run_campaign


def note(kind, status, t):
    return {"kind": kind, "status": status, "created": t}


def test_campaign_state():
    assert not campaign_state([])["accepted"]
    ns = [note("decision", "continue", "1"), note("critique", "accept", "2")]
    assert not campaign_state(ns)["accepted"]                       # accept without conclude
    ns = [note("decision", "conclude", "1"), note("critique", "challenge", "2")]
    assert not campaign_state(ns)["accepted"]
    ns = [note("critique", "accept", "1"), note("decision", "conclude", "2")]
    assert not campaign_state(ns)["accepted"]                       # accept predates the conclusion
    ns = [note("decision", "conclude", "1"), note("critique", "accept", "2")]
    assert campaign_state(ns)["accepted"]


class ScriptedAgent:
    """Plays researcher/critic by calling the real tools, standing in for Hermes."""

    def __init__(self, root, julia):
        self.root, self.julia, self.turn = root, julia, 0

    def ctx(self, role):
        return Ctx(Settings(root=self.root, role=role, executor="local", julia_cmd=self.julia))

    def ask(self, profile, prompt):
        self.turn += 1
        c = self.ctx(profile)
        assert "read_notes" in prompt or "Read the notes" in prompt
        seen = tools.read_notes(c)
        assert seen[0]["kind"] == "question"                        # the driver posted the question
        if profile == "researcher":
            rnd = (self.turn + 1) // 2
            tools.add_note(c, "hypothesis", claim=HYP(f"H{rnd}"))
            r = tools.submit_experiment(c, f"exp{rnd}", f"H{rnd}", "println(1)\n", params={"x": str(rnd)})
            tools.add_note(c, "finding", claim=FND(f"F{rnd}"), run_ids=[r["run_id"]])
            tools.add_note(c, "decision", "done" if rnd == 2 else "next", status="conclude" if rnd == 2 else "continue")
            return f"researcher round {rnd} ok"
        last_run = tools.list_runs(c)[-1]["run_id"]
        rr = tools.rerun_run(c, last_run)
        rnd = self.turn // 2
        tools.add_note(c, "critique", status="accept" if rnd == 2 else "challenge",
                       claim={**ACC, "statement": "reproduced claim"} if rnd == 2 else {**CHAL, "objection": "scope too broad"},
                       run_ids=[last_run, rr["rerun"]])
        return f"critic round {rnd} ok"


def test_campaign_runs_until_critic_accepts(root, tmp_path):
    agent = ScriptedAgent(root, str(tmp_path / "fakejulia"))
    res = run_campaign("Does the error decay?", rounds=5, root=root, agent=agent, max_runs=20)
    assert res.stopped_by == "accepted" and res.rounds_run == 2 and not res.errors
    text = res.report_path.read_text()
    assert "Does the error decay?" in text and "scope too broad" in text and "reproduced claim" in text
    assert "## Outcome" in text and text.index("## Outcome") < text.index("## Notes")
    assert "**Accepted claim**" in text and "evidence (server-derived)" in text
    assert text.count("| experiment |") == 4 and "researcher round 2 ok" in text
    meta = json.loads((res.report_path.parent / "campaign.json").read_text())
    assert meta["max_runs"] == 20


def test_campaign_stops_at_round_limit(root, tmp_path):
    class Stubborn(ScriptedAgent):
        def ask(self, profile, prompt):
            self.turn += 1
            c = self.ctx(profile)
            if profile == "researcher":
                tools.add_note(c, "hypothesis", claim=HYP("H"))
                tools.submit_experiment(c, "e", "H", "println(1)\n")
                tools.add_note(c, "decision", "more", status="continue")
            else:
                tools.add_note(c, "critique", status="challenge", claim=CHAL, run_ids=[tools.list_runs(c)[-1]["run_id"]])
            return ""
    res = run_campaign("q", rounds=2, root=root, agent=Stubborn(root, str(tmp_path / "fakejulia")))
    assert res.stopped_by == "max_rounds" and res.rounds_run == 2


def test_agent_failure_is_recorded_not_raised(root):
    class Broken:
        def ask(self, profile, prompt):
            raise RuntimeError("hermes exited 2: boom")
    res = run_campaign("q", rounds=2, root=root, agent=Broken())
    assert res.stopped_by.startswith("error:") and res.errors and res.report_path.exists()


class FakePopen:
    calls = []
    slow = 0

    def __init__(self, argv, **kw):
        FakePopen.calls.append(argv)
        self.returncode, self._n = 0, 0

    def communicate(self, timeout=None):
        import subprocess
        self._n += 1
        if timeout is not None and self._n <= FakePopen.slow:
            raise subprocess.TimeoutExpired("hermes", timeout)
        return " answer \n", ""

    def kill(self):
        pass


def test_hermes_cli_command_template(monkeypatch):
    FakePopen.calls, FakePopen.slow = [], 0
    monkeypatch.setattr("subprocess.Popen", FakePopen)
    out = HermesCLI().ask("critic", "hello world")
    assert out == "answer" and FakePopen.calls[0] == ["hermes", "-p", "critic", "-z", "hello world"]
    monkeypatch.setenv("RESEARCH_HERMES_CMD", json.dumps(["h", "--profile", "{profile}", "-q", "{prompt}"]))
    HermesCLI().ask("researcher", "p")
    assert FakePopen.calls[1] == ["h", "--profile", "researcher", "-q", "p"]


def test_hermes_cli_heartbeat_and_timeout(monkeypatch):
    FakePopen.calls, FakePopen.slow = [], 2
    monkeypatch.setattr("subprocess.Popen", FakePopen)
    beats = []
    h = HermesCLI(heartbeat_s=0.01, timeout=100)
    h.on_wait = beats.append
    assert h.ask("critic", "p") == "answer" and len(beats) == 2
    FakePopen.slow = 10**6
    import pytest
    with pytest.raises(RuntimeError, match="exceeded 0 s"):
        HermesCLI(heartbeat_s=0.01, timeout=0).ask("critic", "p")


def test_campaign_reports_progress(root, tmp_path):
    lines = []
    agent = ScriptedAgent(root, str(tmp_path / "fakejulia"))
    run_campaign("q", rounds=1, root=root, agent=agent, progress=lines.append)
    assert any("round 1/1 researcher: turn started" in l for l in lines)
    assert any("critic: turn finished" in l for l in lines)


def test_accept_without_independent_attack_is_ignored(root, tmp_path):
    class Lazy(ScriptedAgent):
        def ask(self, profile, prompt):
            self.turn += 1
            c = self.ctx(profile)
            if profile == "researcher":
                tools.add_note(c, "hypothesis", claim=HYP("H"))
                tools.submit_experiment(c, "e", "H", "println(1)\n")
                tools.add_note(c, "decision", "done", status="conclude")
            else:   # cites the researcher's run but starts no attack of its own
                tools.add_note(c, "critique", status="accept", claim=ACC,
                               run_ids=[tools.list_runs(c)[-1]["run_id"]])
            return ""
    res = run_campaign("q", rounds=2, root=root, agent=Lazy(root, str(tmp_path / "fakejulia")))
    assert res.stopped_by == "max_rounds"
    assert "ran no independent attack" in res.report_path.read_text()


def test_next_step_from_notes():
    d = lambda: {"kind": "decision", "status": "continue", "created": "1"}
    c = lambda: {"kind": "critique", "status": "challenge", "created": "1"}
    assert next_step([]) == (1, "researcher")
    assert next_step([d()]) == (1, "critic")
    assert next_step([d(), c()]) == (2, "researcher")
    assert next_step([d(), c(), d()]) == (2, "critic")


class Recording(ScriptedAgent):
    def __init__(self, *a):
        super().__init__(*a)
        self.asked = []

    def ask(self, profile, prompt):
        self.asked.append(profile)
        return super().ask(profile, prompt)


def test_second_campaign_is_seeded_with_earlier_accepted_claims(root, tmp_path):
    julia = str(tmp_path / "fakejulia")
    first = run_campaign("Q1?", rounds=5, root=root, agent=Recording(root, julia), max_runs=20)
    assert first.stopped_by == "accepted"

    class Quiet:
        def ask(self, profile, prompt):
            return ""
    second = run_campaign("Q2?", rounds=1, root=root, agent=Quiet(), max_runs=20)
    ctx = Ctx(Settings(root=root, role="human", executor="local"))
    seed = tools.notes_mod.read_notes(ctx.paths.ledger, second.campaign_id)[0]["text"]
    assert seed.startswith("Q2?") and "Established by earlier campaigns" in seed
    assert "ACCEPTED" in seed and "reproduced claim" in seed
    third = run_campaign("Q3?", rounds=1, root=root, agent=Quiet(), max_runs=20, prior=False)
    assert tools.notes_mod.read_notes(ctx.paths.ledger, third.campaign_id)[0]["text"] == "Q3?"


def test_resume_continues_where_the_campaign_stopped(root, tmp_path):
    julia = str(tmp_path / "fakejulia")

    class Flaky(Recording):
        failed = False

        def ask(self, profile, prompt):
            if profile == "critic" and not Flaky.failed:
                Flaky.failed = True
                raise RuntimeError("hermes exited 2: boom")
            return super().ask(profile, prompt)

    agent = Flaky(root, julia)
    res = run_campaign("Resume?", rounds=4, root=root, agent=agent, max_runs=20)
    assert res.stopped_by.startswith("error:") and agent.asked == ["researcher"]
    agent.asked.clear()
    res2 = run_campaign(None, None, root, agent, max_runs=None, campaign_id=res.campaign_id, resume=True)
    assert agent.asked == ["critic", "researcher", "critic"]        # starts with the critic of round 1
    assert res2.stopped_by == "accepted" and res2.campaign_id == res.campaign_id
    turns = [json.loads(x) for x in (res.report_path.parent / "turns.jsonl").read_text().splitlines()]
    assert [(t["round"], t["role"]) for t in turns] == [(1, "researcher"), (1, "critic"), (2, "researcher"), (2, "critic")]
    report = res2.report_path.read_text()
    assert "Resume?" in report and report.count("**Round") == 4
    agent.asked.clear()
    res3 = run_campaign(None, None, root, agent, campaign_id=res.campaign_id, resume=True)   # already accepted
    assert agent.asked == [] and res3.stopped_by == "accepted"


def test_report_shows_structured_claims_usage_and_falsification(root, tmp_path):
    res = run_campaign("Q?", rounds=3, root=root, agent=Recording(root, str(tmp_path / "fakejulia")), max_runs=20)
    text = res.report_path.read_text()
    assert "- **statement:** reproduced claim" in text and "- **scope:** test scope" in text
    assert "- **evidence (server-derived):**" in text and "## Usage" in text
    assert "**Falsification attempted:** no" in text and "Total turn time" in text

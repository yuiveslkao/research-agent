import json

from researchagent import tools
from researchagent.config import Ctx, Settings
from researchagent.loop import HermesCLI, campaign_state, run_campaign


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
            tools.add_note(c, "hypothesis", f"H{rnd}")
            r = tools.submit_experiment(c, f"exp{rnd}", f"H{rnd}", "println(1)\n", params={"x": str(rnd)})
            tools.add_note(c, "finding", f"F{rnd}", run_ids=[r["run_id"]])
            tools.add_note(c, "decision", "done" if rnd == 2 else "next", status="conclude" if rnd == 2 else "continue")
            return f"researcher round {rnd} ok"
        last_run = tools.list_runs(c)[-1]["run_id"]
        rr = tools.rerun_run(c, last_run)
        rnd = self.turn // 2
        tools.add_note(c, "critique", "reproduced" if rnd == 2 else "scope too broad",
                       status="accept" if rnd == 2 else "challenge", run_ids=[last_run, rr["rerun"]])
        return f"critic round {rnd} ok"


def test_campaign_runs_until_critic_accepts(root, tmp_path):
    agent = ScriptedAgent(root, str(tmp_path / "fakejulia"))
    res = run_campaign("Does the error decay?", rounds=5, root=root, agent=agent, max_runs=20)
    assert res.stopped_by == "accepted" and res.rounds_run == 2 and not res.errors
    text = res.report_path.read_text()
    assert "Does the error decay?" in text and "scope too broad" in text and "reproduced" in text
    assert text.count("| experiment |") == 4 and "researcher round 2 ok" in text
    meta = json.loads((res.report_path.parent / "campaign.json").read_text())
    assert meta["max_runs"] == 20


def test_campaign_stops_at_round_limit(root, tmp_path):
    class Stubborn(ScriptedAgent):
        def ask(self, profile, prompt):
            self.turn += 1
            c = self.ctx(profile)
            if profile == "researcher":
                tools.add_note(c, "decision", "more", status="continue")
            else:
                tools.add_note(c, "critique", "no", status="challenge")
            return ""
    res = run_campaign("q", rounds=2, root=root, agent=Stubborn(root, str(tmp_path / "fakejulia")))
    assert res.stopped_by == "max_rounds" and res.rounds_run == 2


def test_agent_failure_is_recorded_not_raised(root):
    class Broken:
        def ask(self, profile, prompt):
            raise RuntimeError("hermes exited 2: boom")
    res = run_campaign("q", rounds=2, root=root, agent=Broken())
    assert res.stopped_by.startswith("error:") and res.errors and res.report_path.exists()


def test_hermes_cli_command_template(monkeypatch):
    calls = []

    class R:
        returncode, stdout, stderr = 0, " answer \n", ""
    monkeypatch.setattr("subprocess.run", lambda argv, **kw: calls.append(argv) or R())
    out = HermesCLI().ask("critic", "hello world")
    assert out == "answer" and calls[0] == ["hermes", "-p", "critic", "-z", "hello world"]
    monkeypatch.setenv("RESEARCH_HERMES_CMD", json.dumps(["h", "--profile", "{profile}", "-q", "{prompt}"]))
    HermesCLI().ask("researcher", "p")
    assert calls[1] == ["h", "--profile", "researcher", "-q", "p"]


def test_accept_without_independent_attack_is_ignored(root, tmp_path):
    class Lazy(ScriptedAgent):
        def ask(self, profile, prompt):
            self.turn += 1
            c = self.ctx(profile)
            if profile == "researcher":
                tools.add_note(c, "decision", "done", status="conclude")
            else:
                tools.add_note(c, "critique", "looks fine", status="accept")   # no run, no check
            return ""
    res = run_campaign("q", rounds=2, root=root, agent=Lazy(root, str(tmp_path / "fakejulia")))
    assert res.stopped_by == "max_rounds"
    assert "ran no independent attack" in res.report_path.read_text()

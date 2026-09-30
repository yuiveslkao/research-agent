"""Evaluation: sealed EV- campaigns, golden-question files, scoring, runner, grades and summaries."""
import pytest

from conftest import FND, HYP, STEPS, GOOD_CHECK
from researchagent import claims, status, tools
from researchagent.config import set_current_campaign
from researchagent.tools import ToolError

SCRIPT = 'println("x")\n'
PROOF = "Lemma 1 with its proof, Lemma 2 with its proof, and the combination of both. " * 4


def test_evaluation_campaigns_are_sealed_both_ways(make_ctx):
    res, cri = make_ctx("researcher"), make_ctx("critic")
    led = res.paths.ledger
    set_current_campaign(led, "C-normal", 20)
    tools.add_note(res, "hypothesis", claim=HYP("the answer is 42"))
    old = tools.submit_experiment(res, "t", "h", script=SCRIPT)["run_id"]
    tools.add_note(res, "finding", claim=FND("42 held"), run_ids=[old])
    h_old = tools.read_claims(res)["claims"][0]["id"]

    set_current_campaign(led, "EV-test-1", 20)
    assert tools.read_claims(res)["claims"] == [] and tools.read_claims(res, campaign="C-normal")["claims"] == []
    assert tools.list_runs(res, campaign_only=False) == []
    for fn in (lambda: tools.inspect_run(cri, old), lambda: tools.read_artifact(res, old, "script"),
               lambda: tools.rerun_run(cri, old), lambda: tools.compare_runs(res, old, old),
               lambda: tools.submit_experiment(cri, "reuse", "h", base_run=old)):
        with pytest.raises(ToolError, match="unknown run"):
            fn()
    with pytest.raises(ToolError, match="must be a hypothesis note id"):
        tools.add_note(res, "hypothesis", claim={**HYP("x"), "revises": h_old})
    with pytest.raises(ToolError, match="unknown claim"):
        tools.submit_proof(res, h_old, "the answer is 42", "test scope", PROOF)
    tools.add_note(res, "hypothesis", claim=HYP("eval claim"))
    mine = tools.submit_experiment(res, "t", "h", script=SCRIPT)["run_id"]
    with pytest.raises(ToolError, match="unknown run id"):
        tools.add_note(res, "finding", claim=FND("x"), run_ids=[mine, old])
    tools.add_note(res, "finding", claim=FND("eval finding"), run_ids=[mine])
    assert [c["statement"] for c in tools.read_claims(res)["claims"]] == ["eval claim"]

    set_current_campaign(led, "C-later", 20)                        # normal campaigns do not see evaluation claims
    assert [c["statement"] for c in tools.read_claims(res)["claims"]] == ["the answer is 42"]
    assert "eval claim" not in claims.brief(led)
    assert status.review_queue(led) == []
    assert [c["statement"] for c in claims.registry(led, "EV-test-1")["claims"]] == ["eval claim"]


# ------------------------------------------------------------------ golden questions, scoring, runner

import json
from pathlib import Path

from researchagent import evaluation as ev
from researchagent import proofs as proofs_mod
from researchagent.config import Ctx, Settings

REPO_QUESTIONS = Path(__file__).resolve().parents[2] / "eval" / "questions"
STMT = "power-bounded 0/1 matrices are nilpotent or permutation matrices"


class MatAgent:
    """Plays GQ-MAT-02 with the real tools. good=True refutes the statement; good=False 'proves' it."""

    def __init__(self, root, julia, good):
        self.root, self.julia, self.good = root, julia, good

    def ctx(self, role):
        return Ctx(Settings(root=self.root, role=role, executor="local", julia_cmd=self.julia))

    def ask(self, profile, prompt):
        c = self.ctx(profile)
        if profile == "researcher":
            h = tools.add_note(c, "hypothesis", claim={"statement": STMT, "scope": "N <= 3",
                                                       "refuted_if": "a bounded matrix that is neither"})["id"]
            r = tools.submit_experiment(c, "enumerate", "h", script=SCRIPT)["run_id"]
            if self.good:
                tools.add_note(c, "finding", run_ids=[r], claim={
                    "statement": "A = [1 0; 0 0] has bounded powers and is neither nilpotent nor a permutation",
                    "scope": "N = 2", "result": "refutes", "counterexample": r})
            else:                                   # checks only N = 1, then "proves" the statement
                chk = tools.submit_check(c, "exhaustive", "One", "One", "module One\nconst DOMAIN = (N = 1:1,)\n"
                                         "holds(θ) = true\nend\n")["run_id"]
                tools.add_note(c, "finding", run_ids=[r, chk], claim=FND("held for N = 1", scope="N = 1"))
                tools.submit_proof(c, h, STMT, "N <= 3", STEPS())
            tools.add_note(c, "decision", "done", status="conclude")
            return "researcher done"
        for pid in proofs_mod.pending(c.paths.ledger, c.campaign["id"]):
            tools.review_proof(c, pid, "accept")          # a colluding reviewer, for the bad agent
        last = tools.list_runs(c)[-1]["run_id"]
        rr = tools.rerun_run(c, last)
        tools.add_note(c, "critique", status="accept", run_ids=[last, rr["rerun"]],
                       claim={"statement": "held on the cases run", "scope": "N <= 2"})
        return "critic done"


def mat02():
    return ev.find_questions([REPO_QUESTIONS])["GQ-MAT-02"]


def test_shipped_questions_are_valid():
    qs = ev.find_questions([REPO_QUESTIONS])
    assert {"GQ-MAT-01", "GQ-MAT-02", "GQ-DE-01", "GQ-DE-02", "GQ-OOS-01", "GQ-PRF-01"} <= set(qs)
    assert {q["type"] for q in qs.values()} >= {"proof-required", "refute-false-statement", "planted-flaw",
                                                 "rate-estimation", "out-of-scope"}
    assert all(q["split"] == "dev" for q in qs.values())            # hold-out questions live outside the repo


def test_good_agent_passes_and_false_proof_is_a_critical_failure(root, tmp_path):
    julia = str(tmp_path / "fakejulia")
    q = mat02()
    good = ev.run_question(root, q, MatAgent(root, julia, True), repeat=1, hermes_home=tmp_path)
    bad = ev.run_question(root, q, MatAgent(root, julia, False), repeat=2, hermes_home=tmp_path)
    led = root / "ledger"
    sg = ev.score(good[0], q, led)
    assert sg["verdict"] == "pass" and not sg["critical_failures"], sg
    assert good[0]["campaign"].startswith("EV-GQ-MAT-02-") and good[0]["usage"]["runs"] >= 2
    sb = ev.score(bad[1], q, led)
    assert sb["verdict"] == "fail" and set(sb["critical_failures"]) == {"refuted", "no-false-support"}
    assert [r["repetition"] for r in bad] == [1, 2] and len({r["campaign"] for r in bad}) == 2
    seed = tools.notes_mod.read_notes(led, bad[1]["campaign"])[0]["text"]
    assert seed == q["question"]                                   # no earlier results leak into the question
    assert not (led / "campaigns" / "CURRENT").exists()                 # the sealed campaign is not left active
    rows = ev.summary(led, {"GQ-MAT-02": q})
    assert len(rows) == 1 and rows[0]["runs"] == 3 and rows[0]["verdicts"] == {"pass": 1, "fail": 2}
    assert rows[0]["checks"]["no-false-support"] == {"pass": 1, "fail": 2, "pending": 0, "n/a": 0}
    assert rows[0]["critical_failures"] == 2


HUMAN_Q = '''
id = "GQ-T"
title = "t"
type = "proof-required"
split = "{split}"
verification = "verified"
question = "Is H true?"
known_answer = "yes"
[budget]
rounds = 1
max_runs = 6
max_turn_minutes = 1000
[[check]]
id = "supported"
kind = "claim"
match = ['\\bH\\b']
status = ["numerically-supported", "proven_AI"]
what = "H is supported"
[[check]]
id = "proof-ok"
kind = "human"
when = "proven_AI"
critical = true
what = "proof is right"
[[check]]
id = "tried"
kind = "attempts"
stage = "prove"
when = "no_proven_AI"
what = "failed proof attempts logged"
'''


class HAgent:
    def __init__(self, root, julia, prove):
        self.root, self.julia, self.prove = root, julia, prove

    def ask(self, profile, prompt):
        c = Ctx(Settings(root=self.root, role=profile, executor="local", julia_cmd=self.julia))
        if profile == "researcher":
            h = tools.add_note(c, "hypothesis", claim=HYP("H"))["id"]
            r = tools.submit_experiment(c, "t", "h", script=SCRIPT)["run_id"]
            chk = tools.submit_check(c, "falsify", "Good", "Good", GOOD_CHECK)["run_id"]
            tools.add_note(c, "finding", claim=FND("H held"), run_ids=[r, chk])
            if self.prove:
                tools.submit_proof(c, h, "H", "test scope", STEPS())
            tools.add_note(c, "decision", "done", status="conclude")
        else:
            for pid in proofs_mod.pending(c.paths.ledger, c.campaign["id"]):
                tools.review_proof(c, pid, "accept")
            r = tools.list_runs(c)[-1]["run_id"]
            tools.rerun_run(c, r)
            tools.add_note(c, "critique", status="accept", claim={"statement": "H", "scope": "test scope"}, run_ids=[r])
        return ""


def test_human_grades_pending_override_and_question_change(root, tmp_path, capsys):
    qdir = tmp_path / "private"
    qdir.mkdir()
    (qdir / "GQ-T.toml").write_text(HUMAN_Q.format(split="holdout"))
    q = ev.find_questions([qdir])["GQ-T"]
    julia = str(tmp_path / "fakejulia")
    proved = ev.run_question(root, q, HAgent(root, julia, True), hermes_home=tmp_path)[0]
    plain = ev.run_question(root, q, HAgent(root, julia, False), hermes_home=tmp_path)[0]
    led = root / "ledger"
    s = ev.score(proved, q, led)
    by = {c["id"]: c for c in s["checks"]}
    assert s["verdict"] == "pending" and by["proof-ok"]["result"] == "pending" and by["tried"]["result"] == "n/a"
    s2 = ev.score(plain, q, led)
    by2 = {c["id"]: c for c in s2["checks"]}
    assert by2["proof-ok"]["result"] == "n/a" and by2["tried"]["result"] == "fail" and s2["verdict"] == "partial"

    qs = {"GQ-T": q}
    with pytest.raises(ev.EvalError, match="has no check"):
        ev.grade(led, proved["id"], "nope", "pass", "owner", "x", qs)
    ev.grade(led, proved["id"], "proof-ok", "fail", "owner", "gap in lemma 2", qs)
    ev.grade(led, proved["id"], "proof-ok", "pass", "owner", "gap closed on re-read", qs)     # latest grade wins
    ev.grade(led, plain["id"], "tried", "pass", "owner", "attempt recorded in the text", qs)   # overrides an auto check
    assert ev.score(proved, q, led)["verdict"] == "pass"
    s2 = ev.score(plain, q, led)
    assert s2["verdict"] == "pass" and {c["id"]: c for c in s2["checks"]}["tried"]["source"] == "human"
    assert len((led / "eval" / "grades.jsonl").read_text().splitlines()) == 3

    (qdir / "GQ-T.toml").write_text(HUMAN_Q.format(split="holdout") + "\n# edited\n")
    q2 = ev.find_questions([qdir])["GQ-T"]
    assert ev.score(proved, q2, led)["question_changed"]

    base = ["--root", str(root), "--questions", str(qdir)]
    assert ev.main(base + ["run", "GQ-T"]) == 2 and "hold-out question" in capsys.readouterr().err
    assert ev.main(base + ["score", proved["id"]]) == 0 and "verdict: pass" in capsys.readouterr().out
    assert ev.main(base + ["summary", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["question"] == "GQ-T" and rows[0]["runs"] == 2 and rows[0]["split"] == "holdout"
    assert ev.main(base + ["grade", plain["id"], "tried", "fail", "--by", "owner", "--reason", "r"]) == 0
    assert ev.main(base + ["pending"]) == 0
    assert ev.main(base + ["score", "E-bogus"]) == 2


@pytest.mark.parametrize("mutate,msg", [
    (lambda q: q.pop("known_answer"), "known_answer"),
    (lambda q: q.update(type="vibes"), "type must be one of"),
    (lambda q: q.update(budget={"rounds": 1}), "max_runs"),
    (lambda q: q["check"].append(dict(q["check"][0])), "unique id"),
    (lambda q: q["check"][0].update(kind="guess"), "kind must be one of"),
    (lambda q: q["check"][0].update(match=["("]), "bad regex"),
    (lambda q: q["check"][0].update(status=["proved"]), "unknown statuses"),
    (lambda q: q["check"][0].pop("status"), "`status`"),
    (lambda q: q["check"][1].update(when="sometimes"), "when must be one of"),
])
def test_question_validation(mutate, msg):
    import tomllib
    q = tomllib.loads(HUMAN_Q.format(split="dev"))
    mutate(q)
    with pytest.raises(ev.EvalError, match=msg):
        ev.validate_question(q)


def test_capture_config_reads_models_and_prompt_versions(root, tmp_path):
    import yaml
    from researchagent.install_profiles import install
    home = tmp_path / "h"
    install("researcher", root, home, create=False)
    cfgp = home / "profiles" / "researcher" / "config.yaml"
    cfg = yaml.safe_load(cfgp.read_text())
    cfg["model"] = {"default": "some-model"}
    cfgp.write_text(yaml.safe_dump(cfg))
    c = ev.capture_config(root, home)
    assert c["roles"]["researcher"]["model"] == "some-model" and c["roles"]["researcher"]["soul_matches_repo"]
    assert c["roles"]["critic"]["installed_soul"] is None and len(c["config_id"]) == 12
    (home / "profiles" / "researcher" / "SOUL.md").write_text("edited by hand")
    c2 = ev.capture_config(root, home)
    assert c2["roles"]["researcher"]["soul_matches_repo"] is False and c2["config_id"] != c["config_id"]

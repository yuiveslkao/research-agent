"""Proof stage: structured proofs, the start rule, attempt limits, the review pass, integrity, loop integration."""
import json

import pytest

from conftest import ACC, ARG, FND, HYP, STEPS, prove, survive
from researchagent import proofs, status, tools
from researchagent.config import set_current_campaign
from researchagent.loop import run_campaign
from researchagent.tools import ToolError

SCRIPT = 'println("x")\n'


def supported(res, text="H"):
    h = tools.add_note(res, "hypothesis", claim=HYP(text))["id"]
    r = tools.submit_experiment(res, "t", "h", script=SCRIPT)["run_id"]
    tools.add_note(res, "finding", claim=FND("held for N<=128"), run_ids=[r])
    return h, r


def lemma(i, uses=(), proof=ARG, kind="lemma", **kw):
    return {"id": i, "kind": kind, "statement": f"statement {i}", "proof": proof, "uses": list(uses), **kw}


@pytest.mark.parametrize("steps,msg", [
    ([lemma("L1"), lemma("C", ["L2"], kind="conclusion")], "not earlier steps"),
    ([lemma("C", kind="conclusion"), lemma("L1")], "last step"),
    ([lemma("L1"), lemma("L1", ["L1"], kind="conclusion")], "unique"),
    ([lemma("L1"), lemma("L2", ["L1"])], "kind 'conclusion'"),
    ([lemma("L1"), lemma("L2"), lemma("C", ["L2"], kind="conclusion")], "not used by any later step"),
    ([lemma("K", kind="computation", proof="see run"), lemma("C", ["K"], kind="conclusion")], "must cite the runs"),
    ([lemma("L1", proof=""), lemma("C", ["L1"], kind="conclusion")], "needs its `proof`"),
    ([lemma("C", kind="conclusion", proof="Obvious from the table.")], "Computation steps support an argument"),
    ([lemma("L1", color="red"), lemma("C", ["L1"], kind="conclusion")], "unknown fields"),
    ([lemma("L1", kind="remark"), lemma("C", ["L1"], kind="conclusion")], "kind must be one of"),
])
def test_step_validation(make_ctx, steps, msg):
    res, cri = make_ctx("researcher"), make_ctx("critic")
    h, _ = supported(res)
    survive(cri, h)
    with pytest.raises(ToolError, match=msg):
        tools.submit_proof(res, h, "H", "test scope", steps)


def test_computation_steps_need_completed_passing_runs(make_ctx):
    res, cri = make_ctx("researcher"), make_ctx("critic")
    h, r = supported(res)
    survive(cri, h)
    bad = tools.submit_check(cri, "falsify", "Bad", "Bad", "module Bad\nconst DOMAIN = (N = 8:16,)\n# PLANTED\nend\n")
    with pytest.raises(ToolError, match="does not support the step"):
        tools.submit_proof(res, h, "H", "test scope", STEPS(runs=[bad["run_id"]]))
    failed = tools.submit_experiment(cri, "broken", "h", script="FAILME\n")["run_id"]
    with pytest.raises(ToolError, match="did not complete"):
        tools.submit_proof(res, h, "H", "test scope", STEPS(runs=[failed]))
    pid = prove(res, cri, h, runs=[r])
    rec = json.loads((res.paths.ledger / "proofs" / f"{pid}.json").read_text())
    assert rec["supporting_runs"] == [r] and rec["steps"][1]["kind"] == "computation"


def test_review_objection_resubmission_and_attempt_limit(make_ctx):
    res, cri = make_ctx("researcher"), make_ctx("critic")
    h, _ = supported(res)
    survive(cri, h)
    p1 = tools.submit_proof(res, h, "H", "test scope", STEPS())["proof"]
    with pytest.raises(ToolError, match="may not use this tool"):
        tools.review_proof(res, p1, "accept")                          # no self-review
    with pytest.raises(ToolError, match="no blocking objections"):
        tools.review_proof(cri, p1, "accept", [{"step": "L1", "issue": "gap", "severity": "blocking"}])
    with pytest.raises(ToolError, match="at least one blocking"):
        tools.review_proof(cri, p1, "object", [{"step": "L1", "issue": "style", "severity": "minor"}])
    with pytest.raises(ToolError, match="objection step must be one of"):
        tools.review_proof(cri, p1, "object", [{"step": "L9", "issue": "gap", "severity": "blocking"}])
    out = tools.review_proof(cri, p1, "object", [{"step": "L1", "issue": "the strip bound is assumed, not shown",
                                                  "severity": "blocking"}])
    assert out["claim_status"] == "numerically-supported"
    d = status.Snapshot(res.paths.ledger).derive(h)
    assert d["proofs_objected"] == [p1] and d["proofs"] == []
    seen = tools.read_proof(res, p1)
    assert seen["state"] == "objected" and seen["reviews"][0]["objections"][0]["step"] == "L1"
    p2 = prove(res, cri, h)                                            # revised proof, accepted
    assert status.Snapshot(res.paths.ledger).derive(h)["status"] == "proven_AI"
    tools.submit_proof(res, h, "H", "test scope", STEPS())
    with pytest.raises(ToolError, match="limit 3"):
        tools.submit_proof(res, h, "H", "test scope", STEPS())
    set_current_campaign(res.paths.ledger, "C-next", 20)               # the limit is per campaign
    assert tools.submit_proof(res, h, "H", "test scope", STEPS())["state"] == "pending"
    assert p2 != p1


def test_reviews_bind_to_the_text_and_tampering_invalidates(make_ctx):
    res, cri = make_ctx("researcher"), make_ctx("critic")
    h, _ = supported(res)
    survive(cri, h)
    pid = prove(res, cri, h)
    assert status.Snapshot(res.paths.ledger).derive(h)["status"] == "proven_AI"
    f = res.paths.ledger / "proofs" / f"{pid}.json"
    rec = json.loads(f.read_text())
    rec["steps"][0]["proof"] = "A shorter, different argument."
    f.write_text(json.dumps(rec))
    snap = status.Snapshot(res.paths.ledger)
    assert snap.proof_state(rec) == "invalid" and snap.derive(h)["status"] == "numerically-supported"


def test_old_version_and_human_proofs(make_ctx):
    res, cri = make_ctx("researcher"), make_ctx("critic")
    h, _ = supported(res)
    survive(cri, h)
    pid = tools.submit_proof(res, h, "H", "test scope", STEPS())["proof"]
    tools.add_note(res, "hypothesis", claim={**HYP("H, narrower"), "revises": h})
    with pytest.raises(ToolError, match="the claim was revised"):
        tools.review_proof(cri, pid, "accept")
    h2, _ = supported(res, "K")
    status.set_human_status(res.paths.ledger, h2, "proven", "my own argument", "owner", proof_text=ARG, env={})
    v = status.Snapshot(res.paths.ledger).view(h2)
    assert v["effective"] == "proven" and v["derived"]["status"] == "numerically-supported"   # never proven_AI


def test_sealed_campaigns_hide_other_proofs(make_ctx):
    res, cri = make_ctx("researcher"), make_ctx("critic")
    set_current_campaign(res.paths.ledger, "C-a", 20)
    h, _ = supported(res)
    survive(cri, h)
    pid = tools.submit_proof(res, h, "H", "test scope", STEPS())["proof"]
    set_current_campaign(res.paths.ledger, "EV-x", 20)
    for fn in (lambda: tools.read_proof(res, pid), lambda: tools.review_proof(cri, pid, "accept")):
        with pytest.raises(ToolError, match="unknown proof"):
            fn()


class ProvingAgent:
    """Round 1: the researcher proves and concludes, the critic accepts without reviewing the proof.
    Round 2: the critic reviews it; only then may the campaign stop."""

    def __init__(self, root, julia):
        self.root, self.julia, self.critic_turns, self.prompts = root, julia, 0, []

    def ask(self, profile, prompt):
        from researchagent.config import Ctx, Settings
        c = Ctx(Settings(root=self.root, role=profile, executor="local", julia_cmd=self.julia))
        if profile == "researcher":
            if not tools.read_claims(c)["claims"]:
                h = tools.add_note(c, "hypothesis", claim=HYP("H"))["id"]
                r = tools.submit_experiment(c, "t", "h", script=SCRIPT)["run_id"]
                chk = tools.submit_check(c, "falsify", "Good", "Good",
                                         "module Good\nconst DOMAIN = (N = 8:16,)\nend\n")["run_id"]
                tools.add_note(c, "finding", claim=FND("survived falsify"), run_ids=[r, chk])
                tools.submit_proof(c, h, "H", "test scope", STEPS())
            tools.add_note(c, "decision", "argument submitted, awaiting review", status="conclude")
            return "researcher"
        self.critic_turns += 1
        self.prompts.append(prompt)
        if self.critic_turns == 2:
            for pid in proofs.pending(c.paths.ledger, c.campaign["id"]):
                tools.review_proof(c, pid, "accept")
        r = tools.list_runs(c)[-1]["run_id"]
        tools.rerun_run(c, r)
        tools.add_note(c, "critique", status="accept", claim=ACC, run_ids=[r])
        return "critic"


def test_campaign_does_not_stop_while_a_proof_awaits_review(root, tmp_path):
    agent = ProvingAgent(root, str(tmp_path / "fakejulia"))
    res = run_campaign("Prove H", rounds=3, root=root, agent=agent, max_runs=20)
    assert res.stopped_by == "accepted" and res.rounds_run == 2
    assert "First review these proofs" in agent.prompts[0] and "First review these proofs" in agent.prompts[1]
    text = res.report_path.read_text()
    assert "proofs still await review" in text and "accepted by the AI review pass" in text

"""Novelty search (logged arXiv queries, novelty notes) and the human review loop back to the agents."""
import json

import pytest

from conftest import FND, HYP, prove, survive
from researchagent import claims, evaluation as ev, report, status, tools
from researchagent.arxiv import ArxivClient
from researchagent.tools import ToolError

FEED = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
<entry><id>http://arxiv.org/abs/2305.05127v4</id><published>2023-05-09T00:00:00Z</published>
<title>Accelerated gradient descent method</title><summary>We consider problems.</summary>
<author><name>A</name></author><arxiv:primary_category term="math.OC"/></entry></feed>"""


def offline(ctx):
    ctx.arxiv = ArxivClient(ctx.paths.ledger.parent / "arxiv-cache", fetch=lambda url: FEED.encode(), min_interval=0)
    return ctx


def supported(res, text="H"):
    h = tools.add_note(res, "hypothesis", claim=HYP(text))["id"]
    r = tools.submit_experiment(res, "t", "h", script='println(1)\n')["run_id"]
    tools.add_note(res, "finding", claim=FND("held for N<=128"), run_ids=[r])
    return h, r


def test_novelty_notes_need_searches_that_ran(make_ctx):
    res = offline(make_ctx("researcher"))
    h, _ = supported(res)
    q = "double exponential quadrature   endpoint singularity"
    with pytest.raises(ToolError, match="were not searched in this campaign"):
        tools.add_note(res, "novelty", claim={"verdict": "not-found", "queries": [q]})
    assert tools.arxiv(res, query=q)[0]["id"] == "2305.05127v4"
    rows = [json.loads(x) for x in (res.paths.ledger / "searches.jsonl").read_text().splitlines()]
    assert rows[0]["query"] == "double exponential quadrature endpoint singularity" and rows[0]["ids"] == ["2305.05127v4"]
    with pytest.raises(ToolError, match="needs claim.sources"):
        tools.add_note(res, "novelty", claim={"verdict": "known", "queries": [q]})
    with pytest.raises(ToolError, match="verdict must be one of"):
        tools.add_note(res, "novelty", claim={"verdict": "maybe", "queries": [q]})
    out = tools.add_note(res, "novelty", claim={"verdict": "known", "queries": [q.upper()], "sources": ["2305.05127"],
                                                "explanation": "Theorem 2 of the paper proves the rate."})
    assert out["claim_status"]["claim"] == h                       # about defaults to the current claim
    c = tools.read_claims(res)["claims"][0]
    assert c["novelty"] == {"verdict": "known", "sources": ["2305.05127"]}
    assert "known: 2305.05127" in claims.brief(res.paths.ledger)
    entry = report.claim_entry(status.Snapshot(res.paths.ledger), h)
    assert entry["novelty"]["verdict"] == "known" and entry["novelty"]["titles"] == ["Accelerated gradient descent method"]
    md = "\n".join(report.markdown_claim(entry))
    assert "novelty (known)" in md and "Accelerated gradient" in md
    h2, _ = supported(res, "K")
    md2 = "\n".join(report.markdown_claim(report.claim_entry(status.Snapshot(res.paths.ledger), h2)))
    assert "not checked" in md2


def test_review_queue_shows_novelty_and_human_reasons_reach_the_agents(make_ctx):
    res, cri = offline(make_ctx("researcher")), make_ctx("critic")
    h, _ = supported(res)
    survive(cri, h)
    prove(res, cri, h)
    q = status.review_queue(res.paths.ledger)
    assert q[0]["claim"] == h and q[0]["novelty"] == "unchecked"
    status.set_human_status(res.paths.ledger, h, "numerically-supported", "the proof skips the case a = b in L1",
                            "owner", env={})
    c = tools.read_claims(res)["claims"][0]
    assert c["status"] == "numerically-supported" and c["derived_status"] == "proven_AI"
    assert c["human_reason"] == "the proof skips the case a = b in L1"
    assert "human: the proof skips the case a = b in L1" in claims.brief(res.paths.ledger)


def test_novelty_evaluation_check(make_ctx):
    res = offline(make_ctx("researcher"))
    h, _ = supported(res)
    tools.arxiv(res, query="tanh-sinh error")
    tools.add_note(res, "novelty", claim={"verdict": "not-found", "queries": ["tanh-sinh error"]})
    rep = report.build_report(res.paths.ledger, "adhoc")
    chk = {"id": "lit", "kind": "novelty", "what": "x", "status": ["numerically-supported"]}
    assert ev.evaluate_check(chk, rep, res.paths.ledger, "adhoc")[0] == "pass"
    assert ev.evaluate_check({**chk, "verdict": ["known"]}, rep, res.paths.ledger, "adhoc")[0] == "fail"
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.validate(rep, json.loads(report.SCHEMA_FILE.read_text()))

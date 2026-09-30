"""Status model: derived status, proofs, counterexamples, revisions, human override, history, glossary, report."""
import json
from pathlib import Path

import pytest

from conftest import ACC, CHAL, FND, HYP, STEPS, prove, survive
from researchagent import claims_cli, glossary, report, status, tools
from researchagent.status import StatusError
from researchagent.tools import ToolError

SCRIPT = 'println("x")\n'
PROOF = ("Reformulation. Write E(N) for the quadrature error. Lemma 1: the transformed integrand is analytic in the "
         "strip |Im z| < pi/2 and bounded there; proof: ... Lemma 2: the discretization error is bounded by "
         "C exp(-pi^2/h); proof: ... Combining Lemma 1 and Lemma 2 with the truncation bound gives the statement.")
GOOD = "module Good\nconst DOMAIN = (N = 8:16,)\nmargin(t, ::Type{T}) where {T<:Real} = one(T)\nend\n"
BAD = GOOD.replace("Good", "Bad") + "# PLANTED flaw\n"


def hyp(ctx, text="H", **kw):
    return tools.add_note(ctx, "hypothesis", claim={**HYP(text), **kw})["id"]


def exp(ctx):
    return tools.submit_experiment(ctx, "t", "h", script=SCRIPT, params={"x": "1"})["run_id"]


def view(ctx, cid):
    s = status.Snapshot(ctx.paths.ledger)
    return s.view(s.resolve(cid))


def supported(ctx, text="H"):
    h = hyp(ctx, text)
    r = exp(ctx)
    tools.add_note(ctx, "finding", claim=FND("held for N<=128"), run_ids=[r])
    return h, r


def test_lifecycle_open_supported_proven_ai_then_human_proven(make_ctx):
    res = make_ctx("researcher")
    h = hyp(res)
    assert view(res, h)["effective"] == "open"
    r = exp(res)
    out = tools.add_note(res, "finding", claim=FND("held for N<=128"), run_ids=[r])
    assert out["claim_status"] == {"claim": h, "status": "numerically-supported"}
    cri = make_ctx("critic")
    survive(cri, h)
    p = tools.submit_proof(res, h, "H", "test  scope", STEPS())                   # whitespace-insensitive match
    assert p["state"] == "pending"
    v = view(res, h)
    assert v["effective"] == "numerically-supported" and v["derived"]["proofs_pending"] == [p["proof"]]
    assert status.review_queue(res.paths.ledger) == []                         # the AI review pass comes first
    out = tools.review_proof(cri, p["proof"], "accept", [{"step": "C", "issue": "cite L1 by name", "severity": "minor"}])
    assert out["claim_status"] == "proven_AI"
    v = view(res, h)
    assert v["effective"] == "proven_AI" and v["awaiting_review"] and v["human"] is None
    assert [q["claim"] for q in status.review_queue(res.paths.ledger)] == [h]
    rec = json.loads((res.paths.ledger / "proofs" / f"{p['proof']}.json").read_text())
    assert rec["schema"] == "proof/0.2" and rec["version"] == h and rec["fingerprint"] == v["fingerprint"]
    assert [s["id"] for s in rec["steps"]] == ["L1", "C"]

    status.set_human_status(res.paths.ledger, h, "proven", "checked every lemma", "owner", proof=p["proof"], env={})
    v = view(res, h)
    assert v["effective"] == "proven" and v["derived"]["status"] == "proven_AI" and not v["awaiting_review"]
    hist = [(r["source"], r["status"]) for r in v["history"]]
    assert hist == [("server", "open"), ("server", "numerically-supported"), ("ai", "proven_AI"), ("human", "proven")]
    assert status.review_queue(res.paths.ledger) == []


def test_submit_proof_is_bound_to_the_exact_claim(make_ctx):
    res, cri = make_ctx("researcher"), make_ctx("critic")
    h, _ = supported(res)
    with pytest.raises(ToolError, match="survived a check"):                   # prove survivors only
        tools.submit_proof(res, h, "H", "test scope", STEPS())
    survive(cri, h)
    with pytest.raises(ToolError, match="must repeat the claim exactly"):
        tools.submit_proof(res, h, "H for all N", "test scope", STEPS())
    short = [{"id": "C", "kind": "conclusion", "statement": "H", "proof": "Obvious."}]
    with pytest.raises(ToolError, match="written argument .* is 8 characters"):
        tools.submit_proof(res, h, "H", "test scope", short)
    with pytest.raises(ToolError, match="may not use this tool"):
        tools.submit_proof(cri, h, "H", "test scope", STEPS())
    with pytest.raises(ToolError, match="unknown claim"):
        tools.submit_proof(res, "N-nope", "H", "test scope", STEPS())
    assert not (res.paths.ledger / "proofs").exists()


def test_counterexample_refutes_and_blocks_proofs(make_ctx):
    res = make_ctx("researcher")
    h = hyp(res, "the bound holds")
    ok = tools.submit_check(res, "falsify", "Good", "Good", GOOD)["run_id"]
    bad = tools.submit_check(res, "falsify", "Bad", "Bad", BAD)["run_id"]
    with pytest.raises(ToolError, match="needs claim.counterexample"):
        tools.add_note(res, "finding", claim=FND("violated", "refutes"), run_ids=[bad])
    with pytest.raises(ToolError, match="one of this note's run_ids"):
        tools.add_note(res, "finding", claim=FND("violated", "refutes", counterexample=bad), run_ids=[ok])
    with pytest.raises(ToolError, match="found no counterexample"):
        tools.add_note(res, "finding", claim=FND("violated", "refutes", counterexample=ok), run_ids=[ok])
    with pytest.raises(ToolError, match="take no counterexample"):
        tools.add_note(res, "finding", claim=FND("fine", "supports", counterexample=ok), run_ids=[ok])
    hint = tools.add_note(res, "finding", claim=FND("inconclusive so far", "inconclusive"), run_ids=[bad])
    assert "found a violation" in hint["hint"]
    out = tools.add_note(res, "finding", claim=FND("violated at N=9", "refutes", counterexample=bad), run_ids=[bad])
    assert out["claim_status"]["status"] == "refuted"
    with pytest.raises(ToolError, match="recorded counterexample"):
        tools.submit_proof(res, h, "the bound holds", "test scope", STEPS())


def test_critic_counterexample_after_a_proof_is_a_conflict(make_ctx):
    res, cri = make_ctx("researcher"), make_ctx("critic")
    h, r = supported(res, "the bound holds")
    survive(cri, h)
    prove(res, cri, h, "the bound holds")
    bad = tools.submit_check(cri, "falsify", "Bad", "Bad", BAD)["run_id"]
    out = tools.add_note(cri, "critique", status="challenge", run_ids=[r, bad],
                         claim={"objection": "violated at N=9", "counterexample": bad})
    assert out["claim_status"] == {"claim": h, "status": "refuted"}
    v = view(res, h)
    assert v["derived"]["conflict"] and "one of them is wrong" in v["derived"]["conflict"] and v["awaiting_review"]


def test_revision_resets_derived_status_and_flags_human_status_stale(make_ctx):
    res, cri = make_ctx("researcher"), make_ctx("critic")
    h1, _ = supported(res)
    survive(cri, h1)
    p = prove(res, cri, h1)
    status.set_human_status(res.paths.ledger, h1, "proven", "reviewed", "owner", proof=p, env={})
    h2 = hyp(res, "H on a wider range", revises=h1)
    v = view(res, h1)
    assert v["id"] == h1 and v["version"] == h2 and v["versions"] == [h1, h2]
    assert v["derived"]["status"] == "open" and v["effective"] == "proven"      # the human's status still applies ...
    assert v["stale"] and v["awaiting_review"]                                     # ... but is flagged
    assert not report.claim_entry(status.Snapshot(res.paths.ledger), h1)["proofs"][0]["current"]
    with pytest.raises(ToolError, match="was revised; the current version is"):
        hyp(res, "fork", revises=h1)
    r = exp(res)
    with pytest.raises(ToolError, match="was revised"):
        tools.add_note(res, "finding", claim=FND("x", about=h1), run_ids=[r])
    with pytest.raises(StatusError, match="bound to version"):
        status.set_human_status(res.paths.ledger, h1, "proven", "again", "owner", proof=p, env={})
    with pytest.raises(ToolError, match="must be a hypothesis note id"):
        hyp(res, "x", revises="N-bogus")


def test_human_override_rules_and_history(make_ctx):
    res, cri = make_ctx("researcher"), make_ctx("critic")
    h, _ = supported(res)
    led = res.paths.ledger
    with pytest.raises(StatusError, match="proven needs the reviewed proof"):
        status.set_human_status(led, h, "proven", "looks right", "owner", env={})
    with pytest.raises(StatusError, match="refuted needs --counterexample"):
        status.set_human_status(led, h, "refuted", "found one by hand", "owner", env={})
    with pytest.raises(StatusError, match="reason and the reviewer id"):
        status.set_human_status(led, h, "open", "", "owner", env={})
    for role in ("researcher", "critic", "driver"):
        with pytest.raises(StatusError, match="agent process"):
            status.set_human_status(led, h, "open", "x", "owner", env={"RESEARCH_ROLE": role})
    before = (led / "status" / "history.jsonl").read_text()
    status.set_human_status(led, h, "refuted", "fails at N=3 by hand", "owner", counterexample="N=3, d=1", env={})
    v = view(res, h)
    assert v["effective"] == "refuted" and v["derived"]["status"] == "numerically-supported" and not v["awaiting_review"]
    r = exp(cri)                                          # the derived status moves after the human's decision
    tools.add_note(cri, "critique", status="challenge", claim={"objection": "fails at N=3", "counterexample": r},
                   run_ids=[r])
    v = view(res, h)
    assert v["derived_changed"] and v["effective"] == "refuted" and v["awaiting_review"]
    status.set_human_status(led, h, None, "derived status is right", "owner", env={})
    v = view(res, h)
    assert v["human"] is None and v["effective"] == "refuted" and not v["derived_changed"]
    after = (led / "status" / "history.jsonl").read_text()
    assert after.startswith(before) and len(after.splitlines()) == len(before.splitlines()) + 3   # append-only
    status.set_human_status(led, h, "proven", "my own argument", "owner", proof_text=PROOF, env={})
    v = view(res, h)
    assert v["effective"] == "proven" and v["proofs"][-1]["author"] == "human:owner"


def test_no_agent_tool_can_set_proven(make_ctx):
    res, cri = make_ctx("researcher"), make_ctx("critic")
    h, r = supported(res)
    for bad in ({**FND("x"), "status": "proven"}, {**FND("x"), "evidence": "proven"}):
        with pytest.raises(ToolError, match="unknown claim fields"):
            tools.add_note(res, "finding", claim=bad, run_ids=[r])
    for role_ctx, kind in ((res, "decision"), (cri, "critique")):
        with pytest.raises(ToolError):
            tools.add_note(role_ctx, kind, "x", status="proven", run_ids=[r], claim=ACC if kind == "critique" else None)
    survive(cri, h)
    prove(res, cri, h)
    assert view(res, h)["effective"] == "proven_AI"
    src = Path(tools.__file__).parent
    import ast
    for f in ("tools.py", "mcp_server.py"):                       # the agent-facing code never writes human statuses
        calls = {getattr(n.func, "attr", getattr(n.func, "id", None))
                 for n in ast.walk(ast.parse((src / f).read_text())) if isinstance(n, ast.Call)}
        assert "set_human_status" not in calls and "_append" not in calls
    assert all(r["source"] != "human" for r in view(res, h)["history"])


def test_glossary_rejects_status_vocabulary(make_ctx):
    res, cri = make_ctx("researcher"), make_ctx("critic")
    assert glossary.reserved_uses("improved, provided, approved, provenance") == []
    assert glossary.reserved_uses("not proven; no proof found; without rigorous error bounds") == []
    assert [w.lower() for w in glossary.reserved_uses("We proved it. It is established. QED")] == ["proved", "established", "qed"]
    with pytest.raises(ToolError, match="reserved word"):
        hyp(res, "the rate is proven")
    h, r = supported(res, "rate is pi^2/h")
    with pytest.raises(ToolError, match=r"claim.limits uses reserved word\(s\) \['rigorous'\]"):
        tools.add_note(res, "finding", claim=FND("held", limits="rigorous for N<=64"), run_ids=[r])
    with pytest.raises(ToolError, match="text uses reserved"):
        tools.add_note(res, "decision", "conclude: established", status="conclude")
    tools.add_note(res, "decision", "conclude: supported for N<=128, not proven", status="conclude")
    tools.add_note(cri, "critique", status="challenge", run_ids=[r], text="calling this proven is premature",
                   claim={"objection": "the finding reads like a proof but is one run", "resolve_by": "prove it or rerun"})
    with pytest.raises(ToolError, match="claim.statement uses reserved"):
        tools.add_note(cri, "critique", status="accept", run_ids=[r], claim={**ACC, "statement": "proven for N<=64"})
    tools.add_note(res, "attempt", claim={"approach": "prove the bound via Poisson summation", "stage": "prove",
                                          "outcome": "failed", "reason": "the proof needs a strip width bound we lack"})


def test_attempts_are_recorded_and_briefed(make_ctx):
    res, cri = make_ctx("researcher"), make_ctx("critic")
    h, r = supported(res, "rate is pi^2/h")
    a = {"approach": "fit c in exp(-cN/log N)", "stage": "explore", "outcome": "failed", "reason": "residuals grow with N"}
    with pytest.raises(ToolError, match="claim.outcome must be one of"):
        tools.add_note(res, "attempt", claim={**a, "outcome": "succeeded"})
    with pytest.raises(ToolError, match="claim.stage must be one of"):
        tools.add_note(res, "attempt", claim={**a, "stage": "vibes"})
    out = tools.add_note(res, "attempt", claim=a, run_ids=[r])
    assert out["claim_status"]["claim"] == h                            # about defaults to the current hypothesis
    tools.add_note(cri, "attempt", claim={**a, "approach": "reimplement with Gauss-Legendre", "stage": "falsify",
                                          "outcome": "abandoned", "reason": "too slow at 1024 bits"})
    got = tools.read_claims(res)["failed_attempts"]
    assert [x["approach"] for x in got] == ["reimplement with Gauss-Legendre", "fit c in exp(-cN/log N)"]
    assert all(x["claim"] == h for x in got)
    from researchagent import claims
    assert "[tried, failed: explore;" in claims.brief(res.paths.ledger)


def test_json_report_validates_against_schema(make_ctx):
    jsonschema = pytest.importorskip("jsonschema")
    res, cri = make_ctx("researcher"), make_ctx("critic")
    h, r = supported(res)
    tools.add_note(cri, "critique", status="accept", claim=ACC, run_ids=[r])
    survive(cri, h)
    prove(res, cri, h, runs=[r])
    h2 = hyp(res, "rate is 2 pi d")
    r2 = exp(res)
    tools.add_note(res, "finding", claim=FND("violated at N=64", "refutes", counterexample=r2), run_ids=[r2])
    tools.add_note(res, "attempt", claim={"approach": "a", "stage": "prove", "outcome": "failed", "reason": "b"})
    status.set_human_status(res.paths.ledger, h2, "refuted", "agree", "owner", counterexample=r2, env={})
    schema = json.loads(report.SCHEMA_FILE.read_text())
    for camp in (None, "adhoc"):
        rep = report.build_report(res.paths.ledger, camp, {"question": "q"} if camp else None)
        jsonschema.validate(rep, schema)
    by = {c["id"]: c for c in rep["claims"]}
    assert by[h]["status"]["effective"] == "proven_AI" and by[h]["status"]["narrowed"]
    assert by[h]["proofs"][0]["current"] and f"rh rerun {r}" in by[h]["reproduce"]
    assert by[h]["proofs"][0]["state"] == "accepted" and by[h]["proofs"][0]["reviews"][0]["verdict"] == "accept"
    assert by[h2]["status"]["human"]["by"] == "owner" and by[h2]["evidence"]["counterexamples"][0]["run_ids"] == [r2]
    assert rep["review_queue"] == [h] and {r, r2} <= {x["run_id"] for x in rep["runs"]}
    md = "\n".join(report.markdown_claims(rep))
    assert "proven_AI" in md and "accepted by the AI review pass, not human-reviewed" in md and "counterexample" in md


def test_research_claims_cli(make_ctx, capsys):
    res, cri = make_ctx("researcher"), make_ctx("critic")
    h, _ = supported(res)
    survive(cri, h)
    p = prove(res, cri, h)
    root = ["--root", str(res.settings.root)]
    assert claims_cli.main(root + ["queue"]) == 0 and h in capsys.readouterr().out
    assert claims_cli.main(root + ["set", h, "proven", "--reason", "ok", "--by", "owner"]) == 2
    assert "proven needs the reviewed proof" in capsys.readouterr().err
    assert claims_cli.main(root + ["set", h, "proven", "--reason", "all lemmas checked", "--by", "owner", "--proof", p]) == 0
    assert "-> proven" in capsys.readouterr().out
    assert claims_cli.main(root + ["list", "--status", "proven", "--json"]) == 0
    assert [c["id"] for c in json.loads(capsys.readouterr().out)] == [h]
    assert claims_cli.main(root + ["show", h, "--proof"]) == 0
    out = capsys.readouterr().out
    assert "human" in out and "L1. Lemma." in out and "all lemmas checked" in out and "Review" in out
    out_file = res.settings.root / "rep.json"
    assert claims_cli.main(root + ["report", "--out", str(out_file)]) == 0
    assert json.loads(out_file.read_text())["claims"][0]["status"]["effective"] == "proven"
    assert claims_cli.main(root + ["clear", h, "--reason", "undo", "--by", "owner"]) == 0
    assert "derived status proven_AI applies" in capsys.readouterr().out


def test_older_ledgers_without_about_or_counterexample(make_ctx):
    """Notes written before the status model: critiques had no `about`, refutations named no counterexample run."""
    from datetime import datetime, timezone
    res = make_ctx("researcher")
    led = res.paths.ledger
    h1, r = supported(res, "old supported")
    h2 = hyp(res, "old refuted")
    r2 = exp(res)

    def legacy(role, kind, st, claim, n):                       # written as the old code stored them
        nid = "N-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f") + f"-{n:04x}"
        rec = {"id": nid, "created": datetime.now(timezone.utc).isoformat(), "role": role, "campaign": "adhoc",
               "kind": kind, "status": st, "text": "", "run_ids": [r2], "claim": claim, "evidence": {"grade": "run"}}
        (led / "notes" / f"{nid}.json").write_text(json.dumps(rec))
    legacy("researcher", "finding", None, {"statement": "fails", "scope": "s", "result": "refutes", "about": h2}, 1)
    legacy("critic", "critique", "accept", {"statement": "x", "scope": "y"}, 2)
    s = status.Snapshot(led)
    assert s.view(h1)["effective"] == "numerically-supported"
    v2 = s.view(h2)
    assert v2["effective"] == "refuted" and v2["derived"]["counterexamples"][0]["run_ids"] == [r2]
    assert v2["derived"]["critic"] == "accept"                      # attributed to the finding's hypothesis

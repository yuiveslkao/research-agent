import json

import pytest

from conftest import ACC, CHAL, FND, HYP
from researchagent import claims, tools
from researchagent.claims import ClaimError, validate_claim
from researchagent.config import set_current_campaign
from researchagent.tools import ToolError

SCRIPT = 'println("x")\n'


def run(ctx, **kw):
    if ctx.settings.role == "researcher":
        tools.add_note(ctx, "hypothesis", claim=HYP())
    return tools.submit_experiment(ctx, "t", "h", script=SCRIPT, params={"x": "1"}, **kw)


def test_schema_errors_are_specific():
    with pytest.raises(ClaimError, match="needs claim=\\{statement, scope, refuted_if\\}"):
        validate_claim("hypothesis", None, None)
    with pytest.raises(ClaimError, match="claim.refuted_if is required"):
        validate_claim("hypothesis", None, {"statement": "s", "scope": "x"})
    with pytest.raises(ClaimError, match="unknown claim fields"):
        validate_claim("hypothesis", None, {**HYP(), "confidence": "high"})
    with pytest.raises(ClaimError, match="claim.result must be one of"):
        validate_claim("finding", None, FND(result="probably"))
    with pytest.raises(ClaimError, match="the limit is 400"):
        validate_claim("finding", None, FND("x" * 401))
    with pytest.raises(ClaimError, match="needs claim=\\{objection\\}"):
        validate_claim("critique", "challenge", None)
    with pytest.raises(ClaimError, match="take no `claim`"):
        validate_claim("decision", "continue", {"statement": "x"})
    assert validate_claim("critique", "accept", ACC) == ACC
    assert validate_claim("decision", "continue", None) is None


def test_text_is_optional_but_capped_when_a_claim_exists(ctx):
    tools.add_note(ctx, "hypothesis", claim=HYP())                                   # no text needed
    with pytest.raises(ToolError, match="text is 801 characters"):
        tools.add_note(ctx, "hypothesis", "x" * 801, claim=HYP())
    with pytest.raises(ToolError, match="needs claim="):
        tools.add_note(ctx, "hypothesis", "prose only, no structure")


def test_finding_about_defaults_to_latest_hypothesis(ctx):
    r = run(ctx)
    tools.add_note(ctx, "finding", claim=FND("F"), run_ids=[r["run_id"]])
    n = [x for x in tools.read_notes(ctx) if x["kind"] == "finding"][0]
    hyp = [x for x in tools.read_notes(ctx) if x["kind"] == "hypothesis"][-1]
    assert n["claim"]["about"] == hyp["id"]


def test_evidence_is_derived_from_what_ran(make_ctx):
    res, cri = make_ctx("researcher"), make_ctx("critic")
    r1 = run(res, precision_bits=256)
    out = tools.add_note(res, "finding", claim=FND("one run"), run_ids=[r1["run_id"]])
    assert out["derived_evidence"]["grade"] == "run"

    rr = tools.rerun_run(res, r1["run_id"])
    out = tools.add_note(res, "finding", claim=FND("reproduced"), run_ids=[r1["run_id"]])
    assert out["derived_evidence"]["grade"] == "reproduced" and rr["outcome"] == "pass"

    tools.add_note(res, "hypothesis", claim=HYP("second"))
    r2 = tools.submit_experiment(res, "hi-res", "h", script=SCRIPT, params={"x": "1"}, precision_bits=512)
    assert tools.compare_runs(res, r1["run_id"], r2["run_id"], 1e-20)["outcome"] == "pass"
    out = tools.add_note(res, "finding", claim=FND("cross-checked by precision"), run_ids=[r1["run_id"], r2["run_id"]])
    ev = out["derived_evidence"]
    assert ev["grade"] == "cross-checked" and ev["precision_checked"] and not ev["independent"]

    crit_run = tools.submit_experiment(cri, "independent", "h", script=SCRIPT, params={"x": "1"})
    out = tools.add_note(res, "finding", claim=FND("independent"), run_ids=[crit_run["run_id"]])
    assert out["derived_evidence"]["independent"] and out["derived_evidence"]["grade"] == "cross-checked"

    good = "module Good\nconst DOMAIN = (N = 8:16,)\nmargin(t, ::Type{T}) where {T<:Real} = one(T)\nend\n"
    tools.add_note(res, "hypothesis", claim=HYP("third"))
    chk = tools.submit_check(res, "falsify", "Good", "Good", good)
    out = tools.add_note(res, "finding", claim=FND("falsify"), run_ids=[chk["run_id"]])
    assert out["derived_evidence"]["grade"] == "falsification-tested"
    assert out["derived_evidence"]["falsify"] == "survived"


def test_agent_cannot_assert_evidence(ctx):
    r = run(ctx)
    with pytest.raises(ToolError, match="unknown claim fields"):
        tools.add_note(ctx, "finding", claim={**FND(), "evidence": "proved"}, run_ids=[r["run_id"]])


def test_registry_and_brief_across_campaigns(make_ctx):
    res, cri = make_ctx("researcher"), make_ctx("critic")
    set_current_campaign(res.paths.ledger, "C-1", 20)
    r = run(res)
    tools.add_note(res, "finding", claim=FND("rate is pi^2", "supports", limits="N<=128"), run_ids=[r["run_id"]])
    tools.add_note(res, "hypothesis", claim=HYP("rate is 2 pi d"))
    r2 = tools.submit_experiment(res, "t", "h", script=SCRIPT)
    tools.add_note(res, "finding", claim=FND("rate is not 2 pi d", "refutes"), run_ids=[r2["run_id"]])
    tools.add_note(cri, "critique", status="challenge", claim=CHAL, run_ids=[r["run_id"]])
    tools.add_note(cri, "critique", status="accept", claim=ACC, run_ids=[r["run_id"]])
    set_current_campaign(res.paths.ledger, "C-2", 20)
    r3 = run(res)
    tools.add_note(cri, "critique", status="challenge", claim={"objection": "prefactor confounded"}, run_ids=[r3["run_id"]])

    reg = claims.build_registry(res.paths.ledger)
    assert [a["statement"] for a in reg["accepted"]] == ["the supported claim"]
    assert [o["objection"] for o in reg["open_challenges"]] == ["prefactor confounded"]   # C-1's was answered by its accept
    assert [x["statement"] for x in reg["refuted"]] == ["rate is not 2 pi d"]
    b = claims.brief(res.paths.ledger, exclude_campaign="C-2")
    assert "ACCEPTED" in b and "the supported claim" in b and "REFUTED" in b and "prefactor confounded" not in b
    assert "OPEN OBJECTION" in claims.brief(res.paths.ledger)
    assert claims.brief(res.paths.ledger, max_chars=60).endswith("(older entries omitted; call read_claims)")
    got = tools.read_claims(res, limit=5)
    assert set(got) == {"accepted", "open_challenges", "refuted", "unreviewed"} and got["accepted"]


def test_read_notes_is_compact(ctx):
    r = run(ctx)
    tools.add_note(ctx, "finding", claim=FND("F"), run_ids=[r["run_id"]])
    n = [x for x in tools.read_notes(ctx) if x["kind"] == "finding"][0]
    assert set(n) == {"id", "role", "kind", "claim", "grade", "run_ids"}          # no timestamps, no empty fields
    assert n["grade"] == "run"

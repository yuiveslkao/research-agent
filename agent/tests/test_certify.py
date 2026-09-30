"""The `certify` checker (interval arithmetic) as a record type, and the real Julia checker when available.

Set RESEARCH_TEST_JULIA=/path/to/julia to also run checkers/test_intervals.jl and checkers/certify.jl on the real
DE library and statements (a stub supplies SpecialFunctions.gamma when the package is not installed).
"""
import json
import os
import subprocess
from pathlib import Path

import pytest

from conftest import FND, HYP
from researchagent import report, status, templates, tools
from researchagent.tools import ToolError

REPO = Path(__file__).resolve().parents[2]
GOOD = "module Good\nconst DOMAIN = (N = 8:16,)\nmargin(t, ::Type{T}) where {T<:Real} = one(T)\nend\n"
BAD = GOOD.replace("Good", "Bad") + "# PLANTED flaw\n"


def test_certify_runs_and_validates_points(ctx):
    tools.add_note(ctx, "hypothesis", claim=HYP("the bound holds"))
    r = tools.submit_check(ctx, "certify", "Good", "Good", GOOD, points=[{"N": 8}, {"N": "12"}])
    assert r["reported"]["check"]["outcome"] == "certified"
    assert r["params"]["points"] == [{"N": 8}, {"N": "12"}] and r["params"]["grid"] == 0
    assert r["numerics"]["arithmetic"] == "Interval"
    assert tools.submit_check(ctx, "certify", "Good", "Good", GOOD, grid=2)["params"]["grid"] == 2
    for bad in ([{"N": 1.5}], [{"bad key": 1}], [{}], "N=8", [{"N": True}]):
        with pytest.raises(ToolError, match="point|points"):
            tools.submit_check(ctx, "certify", "Good", "Good", GOOD, points=bad)
    with pytest.raises(ToolError, match="only used by kind='certify'"):
        tools.submit_check(ctx, "falsify", "Good", "Good", GOOD, points=[{"N": 8}])
    g = tools.submit_falsify_bound(ctx, "exp(-pi*d*N/log(4*d*N/alpha))", kind="certify",
                                   points=[{"d": "1", "a": "1", "b": "1", "N": 8}])
    assert g["params"]["points"] == [{"d": "1", "a": "1", "b": "1", "N": 8}]


def test_certified_evidence_and_certified_counterexample(make_ctx):
    res, cri = make_ctx("researcher"), make_ctx("critic")
    h = tools.add_note(res, "hypothesis", claim=HYP("the bound holds"))["id"]
    ok = tools.submit_check(res, "certify", "Good", "Good", GOOD, points=[{"N": 8}])["run_id"]
    out = tools.add_note(res, "finding", claim=FND("held at N = 8 in interval arithmetic"), run_ids=[ok])
    assert out["derived_evidence"]["certified"] and out["derived_evidence"]["precision_checked"]
    assert out["claim_status"]["status"] == "numerically-supported"            # certified points never prove
    bad = tools.submit_check(cri, "certify", "Bad", "Bad", BAD, points=[{"N": 9}])["run_id"]
    tools.add_note(cri, "critique", status="challenge", run_ids=[bad],
                   claim={"objection": "violated at N = 9 (interval enclosure below 0)", "counterexample": bad})
    snap = status.Snapshot(res.paths.ledger)
    v = snap.view(h)
    assert v["effective"] == "refuted" and v["derived"]["counterexamples"][0]["certified"]
    entry = report.claim_entry(snap, h)
    assert entry["evidence"]["certified"]
    md = "\n".join(report.markdown_claim(entry))
    assert "certified counterexample" in md and "not a proof" in md
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.validate(report.build_report(res.paths.ledger), json.loads(report.SCHEMA_FILE.read_text()))


# ------------------------------------------------------------------ real Julia (optional)

JULIA = os.environ.get("RESEARCH_TEST_JULIA")
needs_julia = pytest.mark.skipif(not JULIA, reason="set RESEARCH_TEST_JULIA to a julia binary")
STUB = '''module SpecialFunctions   # test stub: gamma for BigFloat via MPFR (round to nearest)
export gamma
function gamma(x::BigFloat)
    z = BigFloat()
    ccall((:mpfr_gamma, Base.MPFR.libmpfr), Cint, (Ref{BigFloat}, Ref{BigFloat}, Cint), z, x, Cint(0))
    return z
end
end
'''


def _certify(tmp_path, statement_rel, module, extra, bits=256, cwd=REPO / "workspace"):
    stub = tmp_path / "stub" / "SpecialFunctions" / "src"
    stub.mkdir(parents=True, exist_ok=True)
    (stub / "SpecialFunctions.jl").write_text(STUB)
    spec, out = tmp_path / f"{module}.toml", tmp_path / f"out-{module}"
    out.mkdir()
    spec.write_text(f'schema = "run/0.1"\n[numerics]\nprecision_bits = {bits}\n[params]\n'
                    f'statement = "{statement_rel}"\nmodule = "{module}"\n{extra}\n')
    env = {**os.environ, "RUN_SPEC": str(spec), "RUN_OUT": str(out),
           "JULIA_LOAD_PATH": f"{tmp_path / 'stub'}{os.pathsep}@stdlib"}
    p = subprocess.run([JULIA, str(REPO / "checkers" / "certify.jl")], cwd=cwd, env=env, capture_output=True,
                       text=True, timeout=900)
    assert p.returncode == 0, p.stderr[-2000:]
    return json.loads((out / "reported.json").read_text())["check"]


@needs_julia
def test_interval_library_julia_tests():
    p = subprocess.run([JULIA, str(REPO / "checkers" / "test_intervals.jl")], capture_output=True, text=True,
                       timeout=900)
    assert p.returncode == 0, p.stdout[-3000:] + p.stderr[-3000:]


@needs_julia
def test_certify_on_the_planted_flaw_and_its_correct_twin(tmp_path):
    c3 = _certify(tmp_path, "statements/C0003.jl", "C0003",
                  'grid = 0\npoints = [{d = "1", a = "1", b = "1", N = 8}, {d = "1//2", a = "0", b = "0", N = 16}]')
    assert c3["outcome"] == "violated" and c3["points"] == 1                   # stops at the first violation
    lo, hi = (float(x) for x in c3["violation_margin"])
    assert -82.3503 < lo <= hi < -82.3502                                      # err/bound = 83.3502 (mpmath)
    c2 = _certify(tmp_path, "statements/C0002.jl", "C0002",
                  'grid = 0\npoints = [{d = "1", a = "-1//2", b = "1", N = 8}, {d = "1", a = "1", b = "1", N = 8}]')
    assert c2["outcome"] == "certified" and c2["certified_points"] == 2


@needs_julia
def test_certify_on_a_template_statement(tmp_path):
    ws = tmp_path / "ws"
    (ws / "agent" / "statements").mkdir(parents=True)
    (ws / "library").symlink_to(REPO / "workspace" / "library")
    src = templates.bound_statement("GBtest", "exp(-K*pi*d*N/log(4*d*N/alpha))", consts={"K": "2"})
    (ws / "agent" / "statements" / "GBtest.jl").write_text(src)
    c = _certify(tmp_path, "agent/statements/GBtest.jl", "GBtest",
                 'grid = 0\npoints = [{d = "1", a = "1", b = "1", N = 8}]', cwd=ws)
    assert c["outcome"] == "violated"

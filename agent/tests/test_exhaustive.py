"""Exact/finite statements: the `exhaustive` checker as a record type, and the real Julia tools when available."""
import json
import os
import subprocess
from pathlib import Path

import pytest

from conftest import FND, HYP
from researchagent import report, status, tools
from researchagent.tools import ToolError

REPO = Path(__file__).resolve().parents[2]
TRUE_STMT = "module Count\nconst DOMAIN = (N = 1:3,)\nholds(θ) = true\nend\n"
FALSE_STMT = TRUE_STMT.replace("Count", "Nil") + "# PLANTED flaw\n"


def test_exhaustive_record_type(make_ctx):
    res, cri = make_ctx("researcher"), make_ctx("critic")
    h = tools.add_note(res, "hypothesis", claim=HYP("every case holds for N <= 3"))["id"]
    r = tools.submit_check(res, "exhaustive", "Count", "Count", TRUE_STMT, limit=1000)
    assert r["reported"]["check"]["outcome"] == "exhausted"
    assert r["params"]["limit"] == 1000 and r["numerics"]["arithmetic"] == "Exact"
    for bad in (0, 10**9, "all"):
        with pytest.raises(ToolError, match="limit must be"):
            tools.submit_check(res, "exhaustive", "Count", "Count", TRUE_STMT, limit=bad)
    out = tools.add_note(res, "finding", claim=FND("held for all N <= 3", scope="N <= 3"), run_ids=[r["run_id"]])
    ev = out["derived_evidence"]
    assert ev["exhaustive"] and ev["falsify"] == "exhausted" and ev["grade"] == "falsification-tested"
    assert out["claim_status"]["status"] == "numerically-supported"          # a finite check never proves
    entry = report.claim_entry(status.Snapshot(res.paths.ledger), h)
    assert entry["evidence"]["exhaustive"] and "exhaustively checked" in "\n".join(report.markdown_claim(entry))

    bad = tools.submit_check(cri, "exhaustive", "Nil", "Nil", FALSE_STMT)["run_id"]
    tools.add_note(cri, "critique", status="challenge", run_ids=[bad],
                   claim={"objection": "fails at N = 2 with witness [1 0; 0 0]", "counterexample": bad})
    v = status.Snapshot(res.paths.ledger).view(h)
    assert v["effective"] == "refuted" and v["derived"]["counterexamples"][0]["certified"]   # exact counterexample


JULIA = os.environ.get("RESEARCH_TEST_JULIA")
needs_julia = pytest.mark.skipif(not JULIA, reason="set RESEARCH_TEST_JULIA to a julia binary")
NILPERM = '''module NilPerm
include(joinpath(@__DIR__, "..", "..", "library", "Exact.jl")); using .Exact
const DOMAIN = (N = 1:3,)
function bounded(A)            # integer powers eventually periodic <=> bounded (exact BigInt powers)
    seen = Set{Vector{BigInt}}()
    P = big.(A)
    for _ in 1:64
        vec(P) in seen && return true
        push!(seen, vec(P))
        P = P * big.(A)
    end
    return false
end
isperm01(A) = all(sum(A; dims = 1) .== 1) && all(sum(A; dims = 2) .== 1)
nilpotent(A) = iszero(big.(A)^size(A, 1))
function holds(θ)
    for A in binary_matrices(θ.N)
        bounded(A) && !nilpotent(A) && !isperm01(A) && return A
    end
    return true
end
cases(θ) = 2^(θ.N^2)
end
'''
SIMPSON = '''module Simpson
include(joinpath(@__DIR__, "..", "..", "library", "Exact.jl")); using .Exact
const DOMAIN = (k = 0:3,)
# Simpson's weights on [-1, 1] integrate x^k exactly for k <= 3
holds(θ) = sum(interpolatory_weights([-1, 0, 1], [2, 0, 2//3]) .* Q[-1, 0, 1] .^ θ.k) == (iseven(θ.k) ? Q(2, θ.k + 1) : 0)
end
'''


def _exhaustive(tmp_path, module, src, limit=100):
    ws = tmp_path / "ws"
    (ws / "agent" / "statements").mkdir(parents=True, exist_ok=True)
    if not (ws / "library").exists():
        (ws / "library").symlink_to(REPO / "workspace" / "library")
    (ws / "agent" / "statements" / f"{module}.jl").write_text(src)
    spec, out = tmp_path / f"{module}.toml", tmp_path / f"out-{module}"
    out.mkdir()
    spec.write_text(f'schema = "run/0.1"\n[params]\nstatement = "agent/statements/{module}.jl"\n'
                    f'module = "{module}"\nlimit = {limit}\n')
    p = subprocess.run([JULIA, str(REPO / "checkers" / "exhaustive.jl")], cwd=ws, capture_output=True, text=True,
                       env={**os.environ, "RUN_SPEC": str(spec), "RUN_OUT": str(out)}, timeout=900)
    assert p.returncode == 0, p.stderr[-2000:]
    return json.loads((out / "reported.json").read_text())["check"]


@needs_julia
def test_exact_library_julia_tests():
    p = subprocess.run([JULIA, str(REPO / "workspace" / "library" / "test_exact.jl")], capture_output=True,
                       text=True, timeout=900)
    assert p.returncode == 0, p.stdout[-3000:] + p.stderr[-3000:]


@needs_julia
def test_exhaustive_checker_on_real_statements(tmp_path):
    c = _exhaustive(tmp_path, "NilPerm", NILPERM)
    assert c["outcome"] == "violated" and c["violation_theta"] == {"N": "2"} and c["witness"] == "[1 0; 0 0]"
    assert c["points"] == 2 and c["cases"] == "18" and c["domain_size"] == "3"     # stopped at the first
    s = _exhaustive(tmp_path, "Simpson", SIMPSON)
    assert s["outcome"] == "exhausted" and s["points"] == 4
    part = _exhaustive(tmp_path, "Simpson2", SIMPSON.replace("Simpson", "Simpson2"), limit=2)
    assert part["outcome"] == "partial" and part["points"] == 2

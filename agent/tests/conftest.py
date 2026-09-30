import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from researchagent.config import Ctx, Settings

FAKE_JULIA = '''#!{python}
"""Stand-in for `julia`: obeys the run contract without running Julia.
argv: julia --project=envs/main <script>   (cwd = workspace)"""
import json, os, sys, tomllib
spec = tomllib.load(open(os.environ["RUN_SPEC"], "rb"))
script = sys.argv[-1]
src = open(script).read() if os.path.exists(script) else ""
out = os.environ["RUN_OUT"]
if "FAILME" in src:
    print("ERROR: LoadError: FAILME requested", file=sys.stderr); sys.exit(1)
if script.endswith(("falsify.jl", "highprec.jl", "certify.jl", "exhaustive.jl")):
    kind = os.path.basename(script)[:-3]
    bad, good = {{"falsify": ("violated", "survived"), "highprec": ("fail", "pass"), "certify": ("violated", "certified"), "exhaustive": ("violated", "exhausted")}}[kind]
    outcome = bad if "PLANTED" in open(os.path.join(os.getcwd(), spec["params"]["statement"])).read() else good
    json.dump({{"check": {{"kind": kind, "outcome": outcome}}, "metrics": {{"score": "0.5"}}}}, open(os.path.join(out, "reported.json"), "w"))
    sys.exit(0)
p = spec.get("params", {{}})
json.dump({{"metrics": {{"x": str(p.get("x", "none"))}}, "notes": "bits=" + str(spec["numerics"]["precision_bits"])}},
          open(os.path.join(out, "reported.json"), "w"))
open(os.path.join(out, "data.csv"), "w").write("N,err\\n8,1e-7\\n16,1e-13\\n")
'''


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "repo"
    (r / "workspace" / "envs" / "main").mkdir(parents=True)
    (r / "workspace" / "library").mkdir()
    (r / "checkers").mkdir()
    (r / "workspace" / "envs" / "main" / "Project.toml").write_text("name='x'\n")
    (r / "workspace" / "envs" / "main" / "Manifest.toml").write_text("# m\n")
    (r / ".gitignore").write_text(".rh/\nwork/\nstore/\nledger/\n")
    fake = tmp_path / "fakejulia"
    fake.write_text(FAKE_JULIA.format(python=sys.executable))
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    subprocess.run(["git", "init", "-q", str(r)], check=True)
    for k, v in (("user.email", "t@t"), ("user.name", "t")):
        subprocess.run(["git", "-C", str(r), "config", k, v], check=True)
    subprocess.run(["git", "-C", str(r), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(r), "commit", "-qm", "init"], check=True)
    return r


@pytest.fixture
def make_ctx(root, tmp_path):
    def _make(role="researcher"):
        return Ctx(Settings(root=root, role=role, executor="local", julia_cmd=str(tmp_path / "fakejulia")))
    return _make


@pytest.fixture
def ctx(make_ctx):
    return make_ctx("researcher")


def HYP(text="H"):
    return {"statement": text, "scope": "test scope", "refuted_if": "the fake result differs"}


def FND(text="F", result="supports", **kw):
    return {"statement": text, "scope": "test scope", "result": result, **kw}


ACC = {"statement": "the supported claim", "scope": "test scope", "limits": "tested range only"}
CHAL = {"objection": "not convinced: scope too broad", "resolve_by": "a wider sweep"}


GOOD_CHECK = "module Good\nconst DOMAIN = (N = 8:16,)\nmargin(t, ::Type{T}) where {T<:Real} = one(T)\nend\n"
ARG = ("Write E(N) for the quadrature error and use the strip of analyticity of the transformed integrand; the "
       "discretization error is bounded by the integral of the integrand over the strip boundary, which is finite by "
       "the assumptions on a and b. ")


def STEPS(runs=None):
    """A minimal well-formed structured proof: one lemma, optionally a computation step, and the conclusion."""
    steps = [{"id": "L1", "kind": "lemma", "statement": "the discretization error is bounded", "proof": ARG, "uses": []}]
    if runs:
        steps.append({"id": "K1", "kind": "computation", "statement": "the constant is below 1 for N <= 16",
                      "proof": "see the cited run", "uses": [], "runs": list(runs)})
    steps.append({"id": "C", "kind": "conclusion", "statement": "the claim follows",
                  "proof": "Combine L1 with the truncation estimate. " + ARG,
                  "uses": ["L1"] + (["K1"] if runs else [])})
    return steps


def survive(cri, about, run_ids=()):
    """The critic runs a passing falsify check and accepts the claim citing it (a survived check in its support)."""
    from researchagent import tools
    chk = tools.submit_check(cri, "falsify", "Good", "Good", GOOD_CHECK)["run_id"]
    tools.add_note(cri, "critique", status="accept", run_ids=[chk, *run_ids],
                   claim={"statement": "held on the tested range", "scope": "test scope", "about": about})
    return chk


def prove(res, cri, h, statement="H", scope="test scope", verdict="accept", runs=None, objections=None):
    """Submit a structured proof of claim version h and have the critic review it; returns the proof id."""
    from researchagent import tools
    pid = tools.submit_proof(res, h, statement, scope, STEPS(runs))["proof"]
    if verdict:
        tools.review_proof(cri, pid, verdict, objections or (
            [{"step": "L1", "issue": "the bound on the strip boundary is not justified", "severity": "blocking"}]
            if verdict == "object" else []))
    return pid

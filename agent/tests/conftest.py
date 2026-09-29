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
if script.endswith("falsify.jl") or script.endswith("highprec.jl"):
    kind = "falsify" if script.endswith("falsify.jl") else "highprec"
    outcome = ("violated" if kind == "falsify" else "fail") if "PLANTED" in open(os.path.join(os.getcwd(), spec["params"]["statement"])).read() \\
        else ("survived" if kind == "falsify" else "pass")
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

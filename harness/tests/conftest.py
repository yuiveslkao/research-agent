import subprocess
import textwrap
from pathlib import Path

import pytest

from rharness.runner import Paths

SPEC = """
schema = "run/0.1"
kind = "experiment"
mode = "exploration"
purpose = "test"

[code]
commit = "HEAD"
cwd = "workspace"
cmd = ["python3", "scripts/job.py"]

[env]
kind = "julia-project"
project = "envs/main"
julia = "1.11.3"

[numerics]
arithmetic = "BigFloat"
precision_bits = 256
determinism = "{det}"
{extra_num}

[resources]
threads = 1
mem_gb = 1
walltime_s = {walltime}

[params]
x = 1
"""


def make_spec(dir: Path, det="bitwise", walltime=30, extra_num="") -> Path:
    p = dir / "spec.toml"
    p.write_text(SPEC.format(det=det, walltime=walltime, extra_num=extra_num))
    return p


@pytest.fixture
def repo(tmp_path):
    """A git repo with a workspace whose job script is controlled by the test."""
    root = tmp_path / "repo"
    (root / "workspace" / "envs" / "main").mkdir(parents=True)
    (root / "workspace" / "scripts").mkdir()
    (root / "workspace" / "envs" / "main" / "Project.toml").write_text("name = 'x'\n")
    (root / "workspace" / "envs" / "main" / "Manifest.toml").write_text("# manifest\n")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    for k, v in (("user.email", "t@t"), ("user.name", "t")):
        subprocess.run(["git", "-C", str(root), "config", k, v], check=True)
    return root


def commit_job(root: Path, body: str) -> None:
    (root / "workspace" / "scripts" / "job.py").write_text(textwrap.dedent(body))
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "job"], check=True)


@pytest.fixture
def paths(repo):
    return Paths.under(repo)

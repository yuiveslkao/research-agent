"""Submit a run: materialize code, execute, record a sealed spec/result pair."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
import subprocess
import tarfile
import io
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .executor import Executor, Job
from .spec import load_spec, spec_hash
from .store import put_file, sha256_file


@dataclass
class Paths:
    """Where things live. `repo` is the git repo containing the code to run."""
    repo: Path
    ledger: Path
    store: Path
    work: Path

    @classmethod
    def under(cls, root: str | Path) -> "Paths":
        root = Path(root)
        return cls(repo=root, ledger=root / "ledger", store=root / "store", work=root / "work")


def new_run_id() -> str:
    return "R-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(3)


def _git(repo: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True).stdout


def resolve_commit(repo: Path, rev: str) -> str:
    try:
        return _git(repo, "rev-parse", "--verify", f"{rev}^{{commit}}").decode().strip()
    except subprocess.CalledProcessError as e:
        raise ValueError(f"cannot resolve commit {rev!r}: {e.stderr.decode().strip()}") from e


def materialize_code(repo: Path, commit: str, dest: Path, paths: list[str] | None = None) -> None:
    """Extract exactly the committed tree (or only `paths` of it); the dirty working tree is never executed.

    `paths` (spec code.paths) limits what a run can read, e.g. agent runs see workspace/ and checkers/ only, so
    documents outside them (evaluation answer keys, notes) are not visible to agent-written code."""
    dest.mkdir(parents=True, exist_ok=True)
    if paths:
        present = [p for p in paths if subprocess.run(["git", "-C", str(repo), "cat-file", "-e", f"{commit}:{p}"],
                                                      capture_output=True).returncode == 0]
        if not present:
            raise ValueError(f"none of code.paths {paths} exist at commit {commit[:8]}")
        paths = present
    data = _git(repo, "archive", "--format=tar", commit, *(["--", *paths] if paths else []))
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        tar.extractall(dest, filter="data")


def env_hash(code_dir: Path, cwd: str, project: str, julia_version: str) -> str:
    """Hash of Project.toml + Manifest.toml (+ LocalPreferences.toml) and the Julia version."""
    pdir = code_dir / cwd / project
    h = hashlib.sha256(f"julia={julia_version}\n".encode())
    for name in ("Project.toml", "Manifest.toml", "LocalPreferences.toml"):
        f = pdir / name
        if name == "Manifest.toml" and not f.exists():
            raise FileNotFoundError(f"{f.relative_to(code_dir)} missing: commit a Manifest.toml")
        if f.exists():
            h.update(name.encode() + b"\0" + f.read_bytes() + b"\0")
    return "sha256:" + h.hexdigest()


def _seal(path: Path) -> None:
    """Make record files read-only. Directories stay writable so that git and rm can still
    remove or move a run directory (a read-only directory blocks unlinking its files)."""
    for p in path.rglob("*"):
        if p.is_file():
            os.chmod(p, stat.S_IMODE(p.stat().st_mode) & ~0o222)


def submit_run(spec_path: str | Path, paths: Paths, executor: Executor) -> str:
    """Run one spec to completion and return its run_id. Blocks (Phase 0)."""
    spec, text = load_spec(spec_path)
    sh = spec_hash(spec)
    run_id = new_run_id()
    code = spec["code"]
    commit = resolve_commit(paths.repo, code["commit"])
    cwd = code.get("cwd", ".")

    work = paths.work / run_id
    code_dir, out_dir, log_dir = work / "code", work / "out", work / "logs"
    out_dir.mkdir(parents=True)
    materialize_code(paths.repo, commit, code_dir, code.get("paths"))
    if not (code_dir / cwd).is_dir():
        raise ValueError(f"code.cwd {cwd!r} does not exist at commit {commit[:8]}")
    eh = env_hash(code_dir, cwd, spec["env"]["project"], spec["env"]["julia"])

    run_dir = paths.ledger / "runs" / run_id
    run_dir.mkdir(parents=True)
    spec_copy = run_dir / "spec.toml"
    spec_copy.write_text(text, encoding="utf-8")

    res = spec["resources"]
    job = Job(run_id=run_id, argv=code["cmd"], code_dir=code_dir, cwd=cwd, out_dir=out_dir,
              log_dir=log_dir, spec_path=spec_copy, threads=res["threads"], mem_gb=res["mem_gb"],
              walltime_s=res["walltime_s"], env_project=spec["env"]["project"],
              julia_version=spec["env"]["julia"], env_hash=eh)
    handle = executor.submit(job)
    er = executor.fetch(handle)

    outputs = []
    for f in sorted(p for p in out_dir.rglob("*") if p.is_file()):
        outputs.append({"path": "out/" + f.relative_to(out_dir).as_posix(),
                        "sha256": put_file(paths.store, f).removeprefix("sha256:"),
                        "bytes": f.stat().st_size})
    reported = None
    rp = out_dir / "reported.json"
    if rp.exists():
        try:
            reported = json.loads(rp.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            reported = {"_error": "reported.json is not valid JSON"}

    if er.timed_out:
        status = "timeout"
    elif er.exit_code == 0:
        status = "completed"
    else:
        status = "failed"

    result = {
        "run_id": run_id,
        "spec_hash": sh,
        "status": status,
        "harness": {  # trusted: written by the harness, never by the job
            "started": er.started, "finished": er.finished, "wall_s": er.wall_s,
            "exit_code": er.exit_code, "host": executor.describe(),
            "env_hash": eh, "code_commit": commit,
            "outputs": outputs,
            "stdout": put_file(paths.store, er.stdout), "stderr": put_file(paths.store, er.stderr),
        },
        "reported": reported,  # untrusted: whatever the job wrote to reported.json
    }
    (run_dir / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n",
                                         encoding="utf-8")
    _seal(run_dir)
    return run_id


def load_result(paths: Paths, run_id: str) -> dict:
    return json.loads((paths.ledger / "runs" / run_id / "result.json").read_text())

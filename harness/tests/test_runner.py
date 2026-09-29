import pytest

from conftest import commit_job, make_spec
from rharness.executor import DockerExecutor, Job, LocalProcessExecutor
from rharness.rerun import compare, compare_metrics, rerun
from rharness.runner import load_result, submit_run
from rharness.store import get_path

DET = """
import json, os, pathlib
out = pathlib.Path(os.environ["RUN_OUT"])
(out / "data.csv").write_text("N,err\\n8,1e-7\\n")
(out / "reported.json").write_text(json.dumps({"metrics": {"c": "6.5"}}))
"""


def test_completed_run_is_recorded_and_sealed(repo, paths, tmp_path):
    commit_job(repo, DET)
    rid = submit_run(make_spec(tmp_path), paths, LocalProcessExecutor())
    r = load_result(paths, rid)
    assert r["status"] == "completed"
    assert r["reported"]["metrics"]["c"] == "6.5"
    assert [o["path"] for o in r["harness"]["outputs"]] == ["out/data.csv", "out/reported.json"]
    assert get_path(paths.store, "sha256:" + r["harness"]["outputs"][0]["sha256"]).exists()
    assert r["harness"]["env_hash"].startswith("sha256:")
    run_dir = paths.ledger / "runs" / rid
    assert (run_dir / "result.json").stat().st_mode & 0o222 == 0  # sealed (root ignores W_OK)
    assert run_dir.stat().st_mode & 0o200  # directory stays deletable


def test_executes_committed_tree_not_dirty_tree(repo, paths, tmp_path):
    commit_job(repo, DET)
    (repo / "workspace" / "scripts" / "job.py").write_text("raise SystemExit(3)\n")  # uncommitted
    rid = submit_run(make_spec(tmp_path), paths, LocalProcessExecutor())
    assert load_result(paths, rid)["status"] == "completed"


def test_failure_and_timeout(repo, paths, tmp_path):
    commit_job(repo, "raise SystemExit(3)\n")
    rid = submit_run(make_spec(tmp_path), paths, LocalProcessExecutor())
    r = load_result(paths, rid)
    assert (r["status"], r["harness"]["exit_code"]) == ("failed", 3)

    commit_job(repo, "import time; time.sleep(30)\n")
    rid = submit_run(make_spec(tmp_path, walltime=1), paths, LocalProcessExecutor())
    assert load_result(paths, rid)["status"] == "timeout"


def test_missing_manifest_is_rejected(repo, paths, tmp_path):
    import subprocess
    commit_job(repo, DET)
    subprocess.run(["git", "-C", str(repo), "rm", "-q", "workspace/envs/main/Manifest.toml"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "rm"], check=True)
    with pytest.raises(FileNotFoundError, match="Manifest.toml"):
        submit_run(make_spec(tmp_path), paths, LocalProcessExecutor())


def test_rerun_bitwise_pass_and_fail(repo, paths, tmp_path):
    commit_job(repo, DET)
    rid = submit_run(make_spec(tmp_path), paths, LocalProcessExecutor())
    rec = rerun(rid, paths, LocalProcessExecutor())
    assert rec["outcome"] == "pass"
    assert (paths.ledger / "checks" / f"{rec['check_id']}.json").exists()

    commit_job(repo, DET.replace('"6.5"', 'str(__import__("random").random())'))
    rid = submit_run(make_spec(tmp_path), paths, LocalProcessExecutor())
    rec = rerun(rid, paths, LocalProcessExecutor())
    assert rec["outcome"] == "fail" and any("reported.json" in p for p in rec["problems"])


def test_rerun_tolerance(repo, paths, tmp_path):
    body = DET.replace('"6.5"', 'str(6.5 + __import__("random").random() * 1e-12)')
    commit_job(repo, body)
    spec = make_spec(tmp_path, det="tolerance", extra_num="rel_tol = 1e-9")
    rid = submit_run(spec, paths, LocalProcessExecutor())
    assert rerun(rid, paths, LocalProcessExecutor())["outcome"] == "pass"


def test_compare_metrics_handles_high_precision_strings():
    a = {"e": {"8": "1.000000000000000000000000000000000001e-86"}}
    b = {"e": {"8": "1.000000000000000000000000000000000002e-86"}}
    assert compare_metrics(a, b, 1e-30) == []
    assert compare_metrics(a, b, 1e-40)
    assert compare_metrics(a, {"e": {}}, 1e-9)


def test_docker_command_shape(tmp_path):
    job = Job(run_id="R-x", argv=["julia", "--project=envs/main", "scripts/a.jl"],
              code_dir=tmp_path / "code", cwd="workspace", out_dir=tmp_path / "out",
              log_dir=tmp_path / "logs", spec_path=tmp_path / "spec.toml", threads=4, mem_gb=8,
              walltime_s=60, env_project="envs/main", julia_version="1.11.3", env_hash="sha256:" + "a" * 64)
    ex = DockerExecutor(depot_root=tmp_path / "depot")
    run = ex.run_cmd(job)
    assert run[:2] == ["docker", "run"] and "julia:1.11.3" in run
    assert run[run.index("--network") + 1] == "none"
    assert run[run.index("--memory") + 1] == "8g" and run[run.index("--cpus") + 1] == "4"
    assert f"{tmp_path / 'code'}:/work/code:ro" in run
    assert run[-3:] == job.argv
    prep = ex.prepare_cmd(job)
    assert "--network" not in prep and "Pkg.instantiate()" in prep[-1]
    assert run[run.index("-w") + 1] == "/work/code/workspace"


def test_compare_two_runs(repo, paths, tmp_path):
    commit_job(repo, DET)
    a = submit_run(make_spec(tmp_path), paths, LocalProcessExecutor())
    commit_job(repo, DET.replace('"6.5"', '"6.5000001"'))
    b = submit_run(make_spec(tmp_path), paths, LocalProcessExecutor())
    assert compare(a, b, 1e-6, paths)["outcome"] == "pass"
    assert compare(a, b, 1e-9, paths)["outcome"] == "fail"


def test_cli_logs(repo, paths, tmp_path, capsys):
    from rharness.cli import main
    commit_job(repo, "import sys; print('hello'); print('boom', file=sys.stderr); raise SystemExit(1)\n")
    rid = submit_run(make_spec(tmp_path), paths, LocalProcessExecutor())
    assert main(["--root", str(repo), "logs", rid]) == 0
    out = capsys.readouterr().out
    assert "hello" in out and "boom" in out

"""Executor interface: submit / status / fetch, so a TSUBAME executor can be added later.

The spec is executor-independent; everything host-specific (Docker flags, depot paths,
schedulers) lives here.
"""
from __future__ import annotations

import os
import platform
import posixpath
import signal
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol


@dataclass
class Job:
    run_id: str
    argv: list[str]
    code_dir: Path          # clean checkout of the requested commit
    cwd: str                # relative to code_dir
    out_dir: Path           # writable; the job writes its outputs here
    log_dir: Path           # harness-owned; stdout/stderr land here
    spec_path: Path
    threads: int
    mem_gb: float
    walltime_s: int
    env_project: str        # Julia project dir, relative to code_dir/cwd
    julia_version: str
    env_hash: str


@dataclass
class ExecResult:
    exit_code: int | None
    timed_out: bool
    started: str
    finished: str
    wall_s: float
    stdout: Path
    stderr: Path


@dataclass
class Handle:
    job: Job
    proc: subprocess.Popen
    started: str
    t0: float
    stdout: Path
    stderr: Path
    extra: dict = field(default_factory=dict)


class Executor(Protocol):
    name: str

    def submit(self, job: Job) -> Handle: ...
    def status(self, handle: Handle) -> str: ...      # "running" | "done"
    def fetch(self, handle: Handle) -> ExecResult: ... # blocks until finished or walltime hit
    def describe(self) -> dict: ...


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _finish(handle: Handle, kill) -> ExecResult:
    timed_out = False
    remaining = max(0.0, handle.job.walltime_s - (time.monotonic() - handle.t0))
    try:
        handle.proc.wait(timeout=remaining)
    except subprocess.TimeoutExpired:
        timed_out = True
        kill(handle)
        handle.proc.wait()
    return ExecResult(
        exit_code=None if timed_out else handle.proc.returncode,
        timed_out=timed_out,
        started=handle.started,
        finished=_now(),
        wall_s=round(time.monotonic() - handle.t0, 3),
        stdout=handle.stdout,
        stderr=handle.stderr,
    )


def _spawn(argv, job: Job, cwd: Path, env: dict, **popen_kw) -> Handle:
    job.log_dir.mkdir(parents=True, exist_ok=True)
    out, err = job.log_dir / "stdout.txt", job.log_dir / "stderr.txt"
    proc = subprocess.Popen(
        argv, cwd=cwd, env=env,
        stdout=open(out, "wb"), stderr=open(err, "wb"), **popen_kw,
    )
    return Handle(job=job, proc=proc, started=_now(), t0=time.monotonic(), stdout=out, stderr=err)


class LocalProcessExecutor:
    """Runs argv directly on the host. NO ISOLATION: for tests and harness development only."""

    name = "local-process"

    def submit(self, job: Job) -> Handle:
        env = {**os.environ, "RUN_SPEC": str(job.spec_path), "RUN_OUT": str(job.out_dir)}
        return _spawn(job.argv, job, job.code_dir / job.cwd, env, start_new_session=True)

    def status(self, handle: Handle) -> str:
        return "running" if handle.proc.poll() is None else "done"

    def fetch(self, handle: Handle) -> ExecResult:
        def kill(h: Handle):
            os.killpg(os.getpgid(h.proc.pid), signal.SIGKILL)
        return _finish(handle, kill)

    def describe(self) -> dict:
        return {"executor": self.name, "isolated": False, "platform": platform.platform(),
                "cpu_count": os.cpu_count()}


class DockerExecutor:
    """Docker with no network at run time, resource limits, read-only code.

    Package installation happens in a separate 'prepare' step (network on) into a
    per-environment depot directory that is bind-mounted into the run.
    UNTESTED against a real daemon in the development sandbox; see docs/phase0.md.
    """

    name = "docker"

    def __init__(self, depot_root: str | Path | None = None, docker: str = "docker"):
        self.docker = docker
        self.depot_root = Path(depot_root or Path.home() / ".rh" / "depot")

    # ---- command construction (unit-tested) ----
    def depot_dir(self, job: Job) -> Path:
        return self.depot_root / job.env_hash.removeprefix("sha256:")[:16]

    def _common(self, job: Job) -> list[str]:
        uid_gid = f"{os.getuid()}:{os.getgid()}" if hasattr(os, "getuid") else None
        args = ["--rm", "-v", f"{job.code_dir}:/work/code:ro",
                "-v", f"{self.depot_dir(job)}:/depot",
                "-e", "JULIA_DEPOT_PATH=/depot", "-e", "HOME=/tmp"]
        if uid_gid:
            args += ["--user", uid_gid]
        return args

    def prepare_cmd(self, job: Job) -> list[str]:
        workdir = posixpath.normpath(f"/work/code/{job.cwd}")
        return [self.docker, "run", *self._common(job), "-w", workdir,
                f"julia:{job.julia_version}", "julia", f"--project={job.env_project}",
                "-e", "using Pkg; Pkg.instantiate(); Pkg.precompile()"]

    def run_cmd(self, job: Job) -> list[str]:
        workdir = posixpath.normpath(f"/work/code/{job.cwd}")
        return [self.docker, "run", "--name", f"rh-{job.run_id}", *self._common(job),
                "--network", "none", "--cpus", str(job.threads),
                "--memory", f"{job.mem_gb:g}g", "--pids-limit", "512",
                "-v", f"{job.out_dir}:/work/out",
                "-v", f"{job.spec_path}:/work/spec.toml:ro",
                "-e", "RUN_SPEC=/work/spec.toml", "-e", "RUN_OUT=/work/out",
                "-e", f"JULIA_NUM_THREADS={job.threads}",
                "-w", workdir, f"julia:{job.julia_version}", *job.argv]

    # ---- Executor interface ----
    def submit(self, job: Job) -> Handle:
        depot = self.depot_dir(job)
        depot.mkdir(parents=True, exist_ok=True)
        if not (depot / ".prepared").exists():
            subprocess.run(self.prepare_cmd(job), check=True)
            (depot / ".prepared").touch()
        return _spawn(self.run_cmd(job), job, job.code_dir, dict(os.environ))

    def status(self, handle: Handle) -> str:
        return "running" if handle.proc.poll() is None else "done"

    def fetch(self, handle: Handle) -> ExecResult:
        def kill(h: Handle):
            subprocess.run([self.docker, "kill", f"rh-{h.job.run_id}"], check=False)
        return _finish(handle, kill)

    def describe(self) -> dict:
        try:
            v = subprocess.run([self.docker, "version", "--format", "{{.Server.Version}}"],
                               capture_output=True, text=True, timeout=10).stdout.strip()
        except Exception:
            v = None
        return {"executor": self.name, "isolated": True, "docker_server": v,
                "platform": platform.platform(), "cpu_count": os.cpu_count()}

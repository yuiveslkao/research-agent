"""A git worktree on branch `agent/lab` where agent-written code is committed.

The harness runs committed trees only. Agent code lands in this worktree, never on the
user's branch. Each use merges the user's current HEAD into the lab so checkers, the
library and the pinned Julia environment stay current.
"""
from __future__ import annotations

import fcntl
import subprocess
from contextlib import contextmanager
from pathlib import Path

BRANCH = "agent/lab"
ALLOWED = {"experiments", "statements"}   # only workspace/agent/<one of these>/ is writable


class LabError(RuntimeError):
    pass


class Lab:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.dir = self.root / ".rh" / "lab"
        self._lockfile = self.root / ".rh" / "lab.lock"

    def _git(self, *args: str, cwd: Path | None = None, check: bool = True):
        return subprocess.run(
            ["git", "-c", "user.name=research-agent", "-c", "user.email=agent@localhost", *args],
            cwd=cwd or self.dir, check=check, capture_output=True, text=True)

    @contextmanager
    def _locked(self):
        self._lockfile.parent.mkdir(parents=True, exist_ok=True)
        with open(self._lockfile, "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def _ensure(self) -> None:
        head = self._git("rev-parse", "HEAD", cwd=self.root).stdout.strip()
        if not (self.dir / ".git").exists():
            exists = self._git("rev-parse", "--verify", "--quiet", f"refs/heads/{BRANCH}",
                               cwd=self.root, check=False).returncode == 0
            args = ["worktree", "add", str(self.dir)] + ([BRANCH] if exists else ["-b", BRANCH, head])
            self._git(*args, cwd=self.root)
        r = self._git("merge", "--no-edit", "-q", head, check=False)
        if r.returncode != 0:
            self._git("merge", "--abort", check=False)
            raise LabError(f"cannot merge {head[:8]} into {BRANCH}: {r.stderr.strip() or r.stdout.strip()}")

    def commit_file(self, relpath: str, text: str, message: str) -> str:
        """Write workspace/agent/{experiments,statements}/<file> in the lab and commit it."""
        p = Path(relpath)
        if p.is_absolute() or ".." in p.parts or len(p.parts) != 4 \
                or p.parts[:2] != ("workspace", "agent") or p.parts[2] not in ALLOWED:
            raise LabError(f"refusing to write outside workspace/agent/{{experiments,statements}}: {relpath}")
        with self._locked():
            self._ensure()
            dest = self.dir / p
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text, encoding="utf-8")
            self._git("add", str(p))
            self._git("commit", "-q", "-m", message)
            return self._git("rev-parse", "HEAD").stdout.strip()

    def show(self, commit: str, relpath: str) -> str:
        return self._git("show", f"{commit}:{relpath}").stdout

    def ls(self, commit: str, directory: str) -> list[str]:
        """Entries directly under `directory` at `commit` (repository-relative paths)."""
        out = self._git("ls-tree", "--name-only", commit, directory.rstrip("/") + "/").stdout
        return [x for x in out.splitlines() if x]

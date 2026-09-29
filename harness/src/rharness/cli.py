"""rh submit SPEC | logs RUN_ID | rerun RUN_ID | compare A B --rel-tol X | show RUN_ID  (run from the repo root)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .executor import DockerExecutor, LocalProcessExecutor
from .rerun import compare, rerun
from .runner import Paths, load_result, submit_run
from .store import get_path
from .spec import SpecError


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="rh")
    ap.add_argument("--root", default=".", help="repository root (default: cwd)")
    ap.add_argument("--executor", choices=["docker", "local"], default="docker",
                    help="'local' has no isolation and is for development only")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("submit").add_argument("spec")
    sub.add_parser("rerun").add_argument("run_id")
    sub.add_parser("show").add_argument("run_id")
    sub.add_parser("logs", help="print a run's stdout and stderr").add_argument("run_id")
    cp = sub.add_parser("compare", help="compare reported metrics of two runs")
    cp.add_argument("run_a"); cp.add_argument("run_b")
    cp.add_argument("--rel-tol", type=float, required=True)
    args = ap.parse_args(argv)

    paths = Paths.under(Path(args.root).resolve())
    ex = DockerExecutor() if args.executor == "docker" else LocalProcessExecutor()
    try:
        if args.cmd == "submit":
            rid = submit_run(args.spec, paths, ex)
            print(rid, load_result(paths, rid)["status"])
        elif args.cmd == "rerun":
            rec = rerun(args.run_id, paths, ex)
            print(json.dumps(rec, indent=2))
            return 0 if rec["outcome"] == "pass" else 1
        elif args.cmd == "logs":
            h = load_result(paths, args.run_id)["harness"]
            for stream in ("stdout", "stderr"):
                print(f"--- {stream} ---")
                print(get_path(paths.store, h[stream]).read_text(errors="replace"), end="")
        elif args.cmd == "compare":
            rec = compare(args.run_a, args.run_b, args.rel_tol, paths)
            print(json.dumps(rec, indent=2))
            return 0 if rec["outcome"] == "pass" else 1
        else:
            print(json.dumps(load_result(paths, args.run_id), indent=2))
    except (SpecError, ValueError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Content-addressed file store (outside git)."""
from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def put_file(store_root: str | Path, path: str | Path) -> str:
    """Copy a file into the store; returns 'sha256:<hex>'. Stored files are read-only."""
    digest = sha256_file(path)
    dest = Path(store_root) / "sha256" / digest[:2] / digest
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".tmp")
        shutil.copyfile(path, tmp)
        os.chmod(tmp, 0o444)
        os.replace(tmp, dest)
    return "sha256:" + digest


def get_path(store_root: str | Path, ref: str) -> Path:
    digest = ref.removeprefix("sha256:")
    return Path(store_root) / "sha256" / digest[:2] / digest

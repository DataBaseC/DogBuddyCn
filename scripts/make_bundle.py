#!/usr/bin/env python3
# DogBuddyCn — release bundle builder
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""Build an installable DogBuddyCn bundle (the artifact `manage.py update` consumes).

    python scripts/make_bundle.py                     # code only  -> dist/dogbuddy-<ver>.tar.gz
    python scripts/make_bundle.py --with-wheels --platform manylinux2014_aarch64 \
        --python-version 3.12 --abi cp312             # + wheels/ for a different machine

The bundle contains a single top-level `DogBuddyCn/` directory holding the
working tree (git-tracked files when the repo is a git checkout). `--with-wheels`
additionally runs `pip download` so the target machine can install fully offline
(crucial on PRoot/container boxes with no usable PyPI route).
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

EXCLUDE_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", "dist",
                "wheels", "node_modules", ".mypy_cache", ".ruff_cache"}
EXCLUDE_FILES = {".DS_Store", "config.yaml"}
EXCLUDE_SUFFIX = {".pyc", ".pyo", ".db", ".db-wal", ".db-shm", ".log"}


def _version() -> str:
    ns: dict = {}
    exec((REPO / "dogbuddy" / "__init__.py").read_text(encoding="utf-8"), ns)  # noqa: S102
    return str(ns.get("__version__", "0.0.0"))


def _tracked_files() -> list[Path] | None:
    """git-tracked files (respects .gitignore); None when not a git checkout."""
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=str(REPO),
                             capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0 or not out.stdout:
        return None
    return [REPO / p for p in out.stdout.split("\0") if p]


def _walk_files() -> list[Path]:
    files = []
    for p in REPO.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(REPO)
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            continue
        if p.name in EXCLUDE_FILES or p.suffix in EXCLUDE_SUFFIX:
            continue
        files.append(p)
    return files


def _collect(extra: list[Path] | None = None) -> list[Path]:
    files = _tracked_files()
    if files is None:
        files = _walk_files()
    files = [f for f in files if f.exists() and f.is_file()]
    for e in extra or []:
        if e.is_dir():
            files += [p for p in e.rglob("*") if p.is_file()]
    return sorted(set(files))


def _download_wheels(dest: Path, platform: str, pyver: str, impl: str, abi: str) -> int:
    req = REPO / "requirements.txt"
    args = [sys.executable, "-m", "pip", "download", "-r", str(req), "-d", str(dest),
            "--only-binary=:all:", "--disable-pip-version-check"]
    if platform:
        args += ["--platform", platform, "--python-version", pyver,
                 "--implementation", impl, "--abi", abi]
    print("== downloading wheels:", " ".join(args[2:]))
    return subprocess.run(args).returncode


def main() -> int:
    ap = argparse.ArgumentParser(description="build a DogBuddyCn release bundle")
    ap.add_argument("--out", default=str(REPO / "dist"))
    ap.add_argument("--with-wheels", action="store_true", help="download wheels for offline install")
    ap.add_argument("--platform", default="manylinux2014_aarch64")
    ap.add_argument("--python-version", default="3.12")
    ap.add_argument("--implementation", default="cp")
    ap.add_argument("--abi", default="cp312")
    ap.add_argument("--zip", action="store_true", help="produce a .zip instead of .tar.gz")
    ap.add_argument("--keep-wheels", action="store_true",
                    help="leave wheels/ in the repo after building")
    args = ap.parse_args()

    ver = _version()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = ".zip" if args.zip else ".tar.gz"
    archive = out_dir / f"dogbuddy-{ver}{suffix}"

    work = Path(tempfile.mkdtemp(prefix="dogbuddy-bundle-"))
    stage = work / "DogBuddyCn"
    stage.mkdir(parents=True)

    extra: list[Path] = []
    if args.with_wheels:
        wheels = work / "wheels"
        wheels.mkdir()
        if _download_wheels(wheels, args.platform, args.python_version,
                            args.implementation, args.abi) != 0:
            print("ERROR: wheel download failed", file=sys.stderr)
            shutil.rmtree(work, ignore_errors=True)
            return 1
        extra = [wheels]
        if args.keep_wheels:
            shutil.copytree(wheels, REPO / "wheels", dirs_exist_ok=True)

    files = _collect(extra)
    wheels_dir = (work / "wheels").resolve()
    for f in files:
        try:
            f.resolve().relative_to(wheels_dir)
            rel = Path("wheels") / f.name
        except ValueError:
            rel = f.resolve().relative_to(REPO.resolve())
        target = stage / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, target)

    if archive.exists():
        archive.unlink()
    if suffix == ".zip":
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
            for p in sorted(stage.rglob("*")):
                if p.is_file():
                    z.write(p, p.relative_to(work))
    else:
        with tarfile.open(archive, "w:gz") as t:
            t.add(stage, arcname="DogBuddyCn")

    size = archive.stat().st_size
    print(f"== built {archive}  ({size/1024/1024:.2f} MB, {len(files)} files, version {ver})")
    shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

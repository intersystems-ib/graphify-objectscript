#!/usr/bin/env python3
"""Sync graphify-objectscript's extractor + fixtures from a sibling fork checkout.

Usage:
    python scripts/sync_from_fork.py [FORK_DIR]

FORK_DIR defaults to ``../graphify`` next to this package -- i.e. the
``graphify-objectscript/graphify`` fork checkout referenced throughout
PLAN.md, the one with the authoritative, already-tested extractor. Copies:

    <fork>/graphify/extractors/objectscript.py -> src/graphify_objectscript/extractor.py
    <fork>/tests/fixtures/objectscript/*        -> tests/fixtures/objectscript/*

and writes ``SYNC_SHA`` at the package root recording the fork's HEAD commit
and each copied file's sha256, so drift between this package and the fork it
tracks is detectable even without the fork checked out: ``tests/test_sync.py``
re-derives the same hashes from whatever files this package currently has and
compares them against ``SYNC_SHA``, and additionally re-verifies against a
live fork checkout when one happens to be present next to this package.
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FORK = PACKAGE_ROOT.parent / "graphify"

EXTRACTOR_REL = Path("graphify/extractors/objectscript.py")
FIXTURES_REL = Path("tests/fixtures/objectscript")

DEST_EXTRACTOR = PACKAGE_ROOT / "src" / "graphify_objectscript" / "extractor.py"
DEST_FIXTURES = PACKAGE_ROOT / "tests" / "fixtures" / "objectscript"
SYNC_SHA_PATH = PACKAGE_ROOT / "SYNC_SHA"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fork_head_sha(fork_dir: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(fork_dir), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip()
    except Exception as exc:
        raise RuntimeError(f"could not read {fork_dir} HEAD sha: {exc}") from exc


def sync(fork_dir: Path) -> dict[str, str]:
    """Copy the extractor + fixtures from ``fork_dir`` and write SYNC_SHA.

    Returns the same {relative_path: sha256} mapping (plus "fork_head") that
    gets written to SYNC_SHA, for the CLI entry point to print and for tests
    to reuse without re-parsing the file they just asked us to write.
    """
    fork_dir = fork_dir.resolve()
    src_extractor = fork_dir / EXTRACTOR_REL
    src_fixtures = fork_dir / FIXTURES_REL
    if not src_extractor.is_file():
        raise FileNotFoundError(f"{src_extractor} not found -- wrong fork checkout?")
    if not src_fixtures.is_dir():
        raise FileNotFoundError(f"{src_fixtures} not found -- wrong fork checkout?")

    DEST_EXTRACTOR.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src_extractor, DEST_EXTRACTOR)

    DEST_FIXTURES.mkdir(parents=True, exist_ok=True)
    copied_fixture_names: set[str] = set()
    for src_file in sorted(src_fixtures.iterdir()):
        if not src_file.is_file():
            continue
        shutil.copyfile(src_file, DEST_FIXTURES / src_file.name)
        copied_fixture_names.add(src_file.name)
    # Remove any fixture this package had that the fork no longer has, so a
    # rename/deletion upstream doesn't leave a stale copy behind here.
    for existing in DEST_FIXTURES.iterdir():
        if existing.is_file() and existing.name not in copied_fixture_names:
            existing.unlink()

    fork_head = _fork_head_sha(fork_dir)
    hashes = {"extractor.py": _sha256(DEST_EXTRACTOR)}
    for name in sorted(copied_fixture_names):
        hashes[f"fixtures/objectscript/{name}"] = _sha256(DEST_FIXTURES / name)

    lines = [f"fork_head={fork_head}"]
    lines += [f"{path}={digest}" for path, digest in sorted(hashes.items())]
    SYNC_SHA_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return {"fork_head": fork_head, **hashes}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    fork_dir = Path(argv[0]) if argv else DEFAULT_FORK
    if not fork_dir.is_dir():
        print(f"sync_from_fork: fork checkout not found at {fork_dir}", file=sys.stderr)
        return 1
    try:
        info = sync(fork_dir)
    except Exception as exc:
        print(f"sync_from_fork: {exc}", file=sys.stderr)
        return 1
    print(f"sync_from_fork: synced from {fork_dir} @ {info['fork_head']}")
    for key, value in sorted(info.items()):
        if key == "fork_head":
            continue
        print(f"  {key}  {value}")
    print(f"sync_from_fork: wrote {SYNC_SHA_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Verifies this package's extractor + fixtures still match SYNC_SHA, and --
when a sibling fork checkout is present -- the fork itself."""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
SYNC_SHA_PATH = PACKAGE_ROOT / "SYNC_SHA"
DEFAULT_FORK = PACKAGE_ROOT.parent / "graphify"


def _parse_sync_sha() -> dict[str, str]:
    lines = SYNC_SHA_PATH.read_text(encoding="utf-8").splitlines()
    return dict(line.split("=", 1) for line in lines if line.strip())


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_sync_from_fork():
    spec = importlib.util.spec_from_file_location(
        "graphify_objectscript_sync_from_fork", PACKAGE_ROOT / "scripts" / "sync_from_fork.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sync_sha_file_exists_and_is_well_formed():
    assert SYNC_SHA_PATH.is_file(), "run scripts/sync_from_fork.py to generate SYNC_SHA"
    entries = _parse_sync_sha()
    assert entries.get("fork_head")
    assert len(entries.get("extractor.py", "")) == 64  # sha256 hex digest length
    fixture_keys = [k for k in entries if k.startswith("fixtures/objectscript/")]
    assert fixture_keys, "SYNC_SHA should record at least one fixture hash"


def test_local_copy_matches_sync_sha():
    """The files this package currently ships must match the hashes SYNC_SHA
    claims they were synced to -- catches extractor.py or a fixture having
    been hand-edited after a sync, which is exactly the drift SYNC_SHA exists
    to catch, independent of whether the fork sibling checkout still exists."""
    entries = _parse_sync_sha()

    extractor_path = PACKAGE_ROOT / "src" / "graphify_objectscript" / "extractor.py"
    assert _sha256(extractor_path) == entries["extractor.py"]

    for key, digest in entries.items():
        if not key.startswith("fixtures/objectscript/"):
            continue
        rel = key[len("fixtures/objectscript/") :]
        fixture_path = PACKAGE_ROOT / "tests" / "fixtures" / "objectscript" / rel
        assert fixture_path.is_file(), f"missing fixture {fixture_path}"
        assert _sha256(fixture_path) == digest


@pytest.mark.skipif(not DEFAULT_FORK.is_dir(), reason="no sibling fork checkout at ../graphify")
def test_matches_live_fork_checkout():
    """When the sibling fork checkout is present, compare directly against
    it (not just against SYNC_SHA), so a fork change nobody re-ran
    sync_from_fork.py for is caught too."""
    sync_from_fork = _load_sync_from_fork()

    fork_extractor = DEFAULT_FORK / sync_from_fork.EXTRACTOR_REL
    fork_fixtures = DEFAULT_FORK / sync_from_fork.FIXTURES_REL
    if not fork_extractor.is_file() or not fork_fixtures.is_dir():
        pytest.skip("sibling checkout exists but does not look like the graphify fork")

    local_extractor = PACKAGE_ROOT / "src" / "graphify_objectscript" / "extractor.py"
    assert _sha256(fork_extractor) == _sha256(local_extractor), (
        "extractor.py has drifted from the fork -- rerun scripts/sync_from_fork.py"
    )

    local_fixtures = PACKAGE_ROOT / "tests" / "fixtures" / "objectscript"
    fork_names = {p.name for p in fork_fixtures.iterdir() if p.is_file()}
    local_names = {p.name for p in local_fixtures.iterdir() if p.is_file()}
    assert fork_names == local_names, "fixture set has drifted from the fork"
    for name in fork_names:
        assert _sha256(fork_fixtures / name) == _sha256(local_fixtures / name), (
            f"fixtures/objectscript/{name} has drifted from the fork"
        )


def test_sync_from_fork_is_idempotent(tmp_path):
    """Running sync() against the real fork (when present) twice in a row,
    or against a copy of this package's own current state, reproduces the
    same hashes -- a basic sanity check on the script itself."""
    sync_from_fork = _load_sync_from_fork()
    if not DEFAULT_FORK.is_dir():
        pytest.skip("no sibling fork checkout at ../graphify")
    fork_extractor = DEFAULT_FORK / sync_from_fork.EXTRACTOR_REL
    if not fork_extractor.is_file():
        pytest.skip("sibling checkout does not look like the graphify fork")

    import shutil

    # Sync into a scratch copy of the package layout so this test never
    # writes to the real package tree.
    scratch = tmp_path / "pkg"
    shutil.copytree(PACKAGE_ROOT, scratch, ignore=shutil.ignore_patterns(".git"))
    sync_from_fork.PACKAGE_ROOT = scratch
    sync_from_fork.DEST_EXTRACTOR = scratch / "src" / "graphify_objectscript" / "extractor.py"
    sync_from_fork.DEST_FIXTURES = scratch / "tests" / "fixtures" / "objectscript"
    sync_from_fork.SYNC_SHA_PATH = scratch / "SYNC_SHA"

    first = sync_from_fork.sync(DEFAULT_FORK)
    second = sync_from_fork.sync(DEFAULT_FORK)
    assert first == second

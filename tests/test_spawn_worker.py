"""Proves the .pth autoload hook works from a cold interpreter start-up and
inside graphify's own `parallel=True` ProcessPoolExecutor (spawn on macOS) --
not just in a process that already `import graphify_objectscript`d by hand.

This deliberately shells out to a fresh `python -c "..."` subprocess rather
than using `multiprocessing.Process` with a function defined in this test
module: spawn pickles a target function by (module, qualname) and re-imports
that module in the child, which is fragile under pytest's various import
modes. A `python -c` subprocess sidesteps that entirely and is also the exact
real-world shape this hook exists for -- the `/graphify` skill invokes
`python -c "from graphify.extract import ..."`, and extraction itself then
fans out across a `ProcessPoolExecutor` (spawn) for the actual per-file work.
"""
from __future__ import annotations

import importlib.util as _ilu
import json
import subprocess
import sys
import textwrap
from importlib import metadata as _importlib_metadata
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
OS_DIR = FIXTURES / "objectscript"

_needs_objectscript = pytest.mark.skipif(
    _ilu.find_spec("tree_sitter_objectscript_udl") is None
    or _ilu.find_spec("tree_sitter_objectscript_routine") is None,
    reason="tree-sitter-objectscript not installed",
)


def _package_genuinely_installed() -> bool:
    """True only when graphify-objectscript is a real installed distribution
    (its .pth file processed by `site`), not merely importable because e.g.
    its src/ directory happens to be on sys.path in this dev checkout -- a
    child `python -c` subprocess only inherits the former."""
    try:
        _importlib_metadata.distribution("graphify-objectscript")
        return True
    except _importlib_metadata.PackageNotFoundError:
        return False


_needs_installed_package = pytest.mark.skipif(
    not _package_genuinely_installed(),
    reason="graphify-objectscript is not installed as a distribution in the "
    "running interpreter (only importable via a dev sys.path entry, whose "
    ".pth file a fresh subprocess would not have) -- nothing to prove here",
)


_CHILD_SCRIPT = textwrap.dedent(
    """
    import json
    import sys
    from pathlib import Path

    import graphify.extract as extract

    os_dir = Path(sys.argv[1])
    result = extract.extract(sorted(os_dir.glob("*")), parallel=True)
    languages = sorted(
        {n.get("metadata", {}).get("language") for n in result.get("nodes", [])}
    )
    print(json.dumps({"n_nodes": len(result.get("nodes", [])), "languages": languages}))
    """
)


@_needs_objectscript
@_needs_installed_package
def test_pth_hook_registers_in_a_fresh_interpreter_with_parallel_extraction():
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD_SCRIPT, str(OS_DIR)],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, (
        f"child interpreter failed (rc={proc.returncode})\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )

    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["n_nodes"] > 0, "child produced no nodes at all"
    assert "objectscript" in payload["languages"], (
        "child produced nodes but none tagged language=objectscript -- the "
        ".pth hook did not register ObjectScript support in a fresh "
        f"interpreter (languages seen: {payload['languages']})\nstderr:\n{proc.stderr}"
    )

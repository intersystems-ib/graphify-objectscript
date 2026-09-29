"""Console entry point: ``graphify-objectscript``.

Two modes:

- No args, or anything other than the ``sidegraph`` subcommand: registers
  ObjectScript support (belt-and-suspenders on top of the ``.pth`` hook,
  which should already have done this by the time Python got this far) and
  delegates straight to ``graphify``'s own CLI with the same argv, so e.g.
  ``graphify-objectscript update .`` behaves exactly like
  ``graphify update .`` -- just with ObjectScript support guaranteed active,
  even in an environment where the ``.pth`` hook could not run.
- ``sidegraph <path> [--out FILE]``: the B1 fallback. Extracts just the
  ObjectScript files under ``path`` into a standalone ``{"nodes", "edges"}``
  graph JSON file, for ``graphify merge-graphs`` to fold into a normal
  ``graphify-out/graph.json`` built by any other means (stock graphify with
  no wrapper installed at all, a different machine, CI).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_OBJECTSCRIPT_SUFFIXES = frozenset({".cls", ".mac", ".inc", ".rtn"})


def _sidegraph(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="graphify-objectscript sidegraph",
        description=(
            "Extract InterSystems ObjectScript files under PATH into a standalone "
            "nodes/edges graph JSON file, for `graphify merge-graphs`."
        ),
    )
    parser.add_argument("path", type=Path, help="file or directory to extract")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("sidegraph.json"),
        help="output JSON path (default: ./sidegraph.json)",
    )
    args = parser.parse_args(argv)

    from graphify_objectscript import register

    register()

    import graphify.extract as extract

    root = args.path.resolve()
    if not root.exists():
        print(f"graphify-objectscript: {root} does not exist", file=sys.stderr)
        return 1

    all_paths = extract.collect_files(root)
    paths = sorted(p for p in all_paths if p.suffix.lower() in _OBJECTSCRIPT_SUFFIXES)
    if not paths:
        print(
            f"graphify-objectscript: no .cls/.mac/.inc/.rtn files found under {root}",
            file=sys.stderr,
        )

    result = extract.extract(paths, root=root)
    out = {"nodes": result.get("nodes", []), "edges": result.get("edges", [])}

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(
        f"graphify-objectscript: wrote {len(out['nodes'])} nodes, "
        f"{len(out['edges'])} edges to {args.out}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    call_argv = sys.argv[1:] if argv is None else list(argv)

    if call_argv and call_argv[0] == "sidegraph":
        return _sidegraph(call_argv[1:])

    from graphify_objectscript import register

    register()

    from graphify.__main__ import main as graphify_main

    # graphify.__main__.main() reads sys.argv directly rather than taking a
    # parameter, so delegating "with the same argv" means putting it there
    # ourselves -- restored afterward so a caller that invoked us in-process
    # (a test, another tool) does not leak a mutated sys.argv.
    original_argv = sys.argv
    sys.argv = ["graphify", *call_argv]
    try:
        graphify_main()
    finally:
        sys.argv = original_argv
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

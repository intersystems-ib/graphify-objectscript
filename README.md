# graphify-objectscript

[![PyPI](https://img.shields.io/pypi/v/graphify-objectscript.svg)](https://pypi.org/project/graphify-objectscript/)
[![CI](https://github.com/intersystems-ib/graphify-objectscript/actions/workflows/ci.yml/badge.svg)](https://github.com/intersystems-ib/graphify-objectscript/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

InterSystems ObjectScript (`.cls`/`.mac`/`.inc`/`.rtn`) support for
[graphify](https://github.com/Graphify-Labs/graphify) (PyPI: `graphifyy`),
for customers running the current release rather than a fork -- until this
ships natively upstream.

> **A bridge, not a fork.** The extractor in this package is the same code
> proposed upstream in [Graphify-Labs/graphify#3921](https://github.com/Graphify-Labs/graphify/pull/3921).
> Once graphify releases native support (`uv tool install "graphifyy[objectscript]"`),
> this package detects it and becomes a no-op; a final release will then
> mark it deprecated. Supported range today: `graphifyy>=0.9.71,<0.10`.

Without this package, stock `graphifyy`:

- mis-parses `.cls` class files as Salesforce Apex,
- mis-parses `.inc` macro includes as Pascal,
- skips `.mac`/`.rtn` routines entirely (no extractor is registered for
  them), and
- never appears in `graphify-out/graph.json` with `metadata.language ==
  "objectscript"`, so `/graphify` and the knowledge graph it builds have no
  idea your IRIS code exists.

`graphify-objectscript` patches a running `graphify` in place, the moment it
is imported, so `.cls`/`.mac`/`.inc`/`.rtn` files get real ObjectScript
extraction instead: class/method/routine/label/macro nodes, and cross-file
call resolution (`##class(...)`  calls, `Do Label^Routine`, `$$$MACRO`
usage, XData-referenced classes, inheritance).

## Install

Either of these puts `graphifyy` and this package's plugin into the same
environment:

```bash
uv tool install graphifyy --with graphify-objectscript
```

```bash
pip install graphifyy graphify-objectscript
```

Both work with any recent `graphifyy` release in the `>=0.9.71,<0.10` range
(see [Limitations](#limitations) below for what that floor buys you).

### Verify it worked

```bash
uvx --from graphifyy python -c "import graphify.extract as e; print('.mac' in e._DISPATCH)"
```

should print `True`. This intentionally never imports `graphify_objectscript`
itself -- that is the whole point: installing this package alongside
`graphifyy` is enough, because of the import hook described below.

### Alternative: install graphify from the fork instead

If you would rather run the graphify build that carries the extractor natively
(the exact code proposed upstream in
[Graphify-Labs/graphify#3921](https://github.com/Graphify-Labs/graphify/pull/3921)),
install the `intersystems-ib/graphify` fork's branch with the `objectscript`
extra; no plug-in is needed then:

```bash
uv tool install "graphifyy[objectscript] @ git+https://github.com/intersystems-ib/graphify@feat/objectscript-extractor"
```

Trade-offs versus this package: it needs `git` on the machine and a corporate
PyPI proxy will not serve it; it pins you to a fork branch that is rebased on
upstream releases rather than tracking them; and `uv tool upgrade` does not
apply. It is the right choice for evaluating exactly what the upstream PR
does, or when the `.pth` hook is unwelcome in your environment. Once upstream
ships native support, `uv tool install "graphifyy[objectscript]"` replaces
both routes.

## How it works

Installing this package drops one `.pth` file at the site-packages root
(`graphify_objectscript.pth`, containing a single
`import graphify_objectscript._autoload` line). Python's `site` module runs
every `.pth` file at interpreter start-up, before your own code runs -- so
`graphify_objectscript._autoload` installs a lightweight import hook and
returns in well under a millisecond, long before anything imports
`graphify` itself.

The moment something later does `import graphify.extract` or
`import graphify.detect` (directly, or indirectly via `from graphify.extract
import ...`), the hook lets that import finish normally and then calls
`graphify_objectscript.register()`, which:

- adds `.mac`/`.rtn` to `graphify.extract._DISPATCH`, pointing at this
  package's ObjectScript extractor,
- wraps `graphify.extract._get_extractor` so a `.cls`/`.inc` file is
  content-sniffed: it only reroutes to the ObjectScript extractor when the
  file positively looks like ObjectScript (a line-anchored `Class Name ...`
  header, `ROUTINE ... [Type=INC]`, `#define`/`#include` directives, ...),
  so every existing Apex `.cls` and Pascal `.inc` corpus is unaffected,
  files whose sniff is inconclusive keep their historical default,
- adds `.mac`/`.rtn` to `graphify.detect.CODE_EXTENSIONS` (and to
  `graphify.watch._WATCHED_EXTENSIONS`, if `graphify.watch` happens to be
  imported already), and
- registers a cross-file resolver (`objectscript_calls`) that turns raw
  in-body calls into real graph edges once every file has been parsed.

This works identically inside a `ProcessPoolExecutor` worker: macOS's
default `spawn` start method re-runs a full interpreter start-up (and thus
this `.pth` hook) in every worker process, which is exactly why this has to
be an import-time hook rather than, say, a console-script wrapper -- nothing
else would run early enough. It also works for the exact invocation the
`/graphify` skill itself uses,
`python -c "from graphify.extract import ..."`, since the hook is already
installed by the time that one-line script starts running.

Set `GRAPHIFY_OBJECTSCRIPT_DEBUG=1` to get one line on stderr if the hook or
`register()` ever declines to do something (unsupported `graphifyy` version,
an unexpected module shape, ...) -- by design this package never raises or
prints noisily on its own; it fails open and stock behavior continues.

## The `sidegraph` fallback

Some environments cannot run a `.pth` hook at all -- a locked-down sandbox,
an interpreter started with `-S`/`-I`, a `zipapp`, or simply a machine where
you only have stock `graphifyy` installed and want an ObjectScript graph
built somewhere else and merged in later. For those, `graphify-objectscript`
installs a `sidegraph` subcommand that does not depend on the import hook at
all -- it calls `register()` directly in its own process:

```bash
graphify-objectscript sidegraph /path/to/iris/code --out sidegraph.json
```

This walks `/path/to/iris/code`, keeps only `.cls`/`.mac`/`.inc`/`.rtn`
files, extracts them, and writes a standalone `{"nodes": [...], "edges":
[...]}` JSON file. Fold it into a normal graph built by plain `graphify`
(run anywhere, even a machine without this package at all) with:

```bash
graphify merge-graphs graphify-out/graph.json sidegraph.json --out graphify-out/graph.json
```

(see `graphify merge-graphs --help` for the exact flags your installed
`graphifyy` version supports).

The `graphify-objectscript` console script itself is also a drop-in
`graphify` replacement for everything else -- `graphify-objectscript update
.` registers ObjectScript support and then delegates straight to `graphify
update .` with the same arguments, which is handy in an environment where
the `.pth` hook did not run but a console script still can.

## Limitations

- **Supported `graphifyy` range: `>=0.9.71,<0.10`.** Below the floor, the
  private names this package patches
  (`graphify.extract._DISPATCH`/`_get_extractor`/`register_language_resolver`,
  `graphify.detect.CODE_EXTENSIONS`) may not exist yet or have a different
  shape; `register()` checks this and fails open (one stderr line, no
  patching) rather than guessing. Comes the day a `graphifyy` release ships
  ObjectScript support natively, `register()` notices (`.mac` is already in
  `_DISPATCH`) and does nothing further -- this package quietly becomes a
  no-op rather than fighting the native implementation.
- **The Pascal-resolver guard is fork-only.** The fork this extractor comes
  from also patches `graphify.pascal_resolution._pascal_raw_calls` to ignore
  raw calls tagged `language="objectscript"`, so a `.inc` file that happens
  to also get swept up by the shared Pascal unit-name resolver never gets a
  spurious Pascal edge. On stock `graphifyy` an ObjectScript raw call is
  never a Pascal call owner, so this does not come up in practice -- it is
  called out here only so it is not mistaken for a bug.
- **The qualified-call gate is emulated.** The fork's `extract.py` skips raw
  calls tagged `is_qualified_call` in its shared bare-name resolution pass
  (every ObjectScript call names its own target scope: `##class(X).M()`,
  `$$Label^Routine`, `$$$MACRO`). Stock `graphifyy` has no such gate, so
  `extract_objectscript_stock` re-applies the flag it does honour,
  `is_member_call: True`, to every qualified call before the pass sees it.
  Either way only this package's own `objectscript_calls` resolver turns
  these calls into graph edges, with that qualified evidence.
- **No language-family table entries.** Unlike the fork, this package does
  not (and safely cannot) add `.cls`/`.inc`/`.mac`/`.rtn` to graphify's
  suffix-keyed language-family tables (used by `build`/`analyze`, not by
  `extract`); the qualified-call gate above is what keeps cross-language
  bare-name matches out.
- **`.int` (compiled output) is intentionally never dispatched**, on stock
  or on the fork -- it duplicates every `.mac`/`.cls` symbol and would only
  produce double-counted nodes.

## Development

```bash
uv build
uv venv .venv-test --python 3.13
uv pip install --python .venv-test/bin/python "graphifyy==0.9.71" dist/*.whl pytest
.venv-test/bin/python -m pytest tests -q
```

`scripts/sync_from_fork.py` re-copies `extractor.py` and
`tests/fixtures/objectscript/` from a sibling
`graphify-objectscript/graphify` fork checkout and writes `SYNC_SHA`; run it
after the fork's extractor changes. `tests/test_sync.py` checks this
package still matches `SYNC_SHA` unconditionally, and additionally against
a live fork checkout when one is present next to this package.

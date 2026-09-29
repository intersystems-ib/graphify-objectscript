"""InterSystems ObjectScript support for stock ``graphify`` (``graphifyy`` on
PyPI), until that support ships natively upstream.

Patches a running ``graphify.extract`` / ``graphify.detect`` module in place
so ``.cls``/``.mac``/``.inc``/``.rtn`` files get real ObjectScript extraction
-- class/method/routine/label/macro nodes, cross-file call resolution --
instead of ``.cls`` being mis-parsed as Salesforce Apex, ``.inc`` as Pascal,
and ``.mac``/``.rtn`` being skipped outright.

You do not usually call anything in this module directly: merely having
``graphify-objectscript`` installed alongside ``graphifyy`` is enough. Its
``.pth`` file (see ``_autoload.py``) calls :func:`register` automatically,
the first time ``graphify.detect`` or ``graphify.extract`` is imported in
this process -- including inside a ``ProcessPoolExecutor`` spawn worker, and
before a bare ``python -c "from graphify.extract import ..."`` invocation.
Call :func:`register` yourself only for the unusual case where graphify was
already fully imported before that hook could see it, or to check status
with :func:`is_registered`.

If a future ``graphifyy`` release ships ObjectScript support natively,
:func:`register` notices (``.mac`` already present in
``graphify.extract._DISPATCH``) and does nothing -- this package becomes an
inert no-op rather than double-registering or fighting the native
implementation.
"""
from __future__ import annotations

import re
import sys
from importlib import metadata as _importlib_metadata

__all__ = ["register", "is_registered", "__version__"]

__version__ = "0.1.0"

# Keep in lockstep with the `graphifyy` dependency bound in pyproject.toml:
# below this floor the private names we patch may not exist yet; at or above
# the ceiling graphify may have reshaped them (or shipped native support,
# which the `.mac in _DISPATCH` check below already handles separately).
_MIN_GRAPHIFYY = (0, 9, 71)
_MAX_GRAPHIFYY_EXCLUSIVE = (0, 10, 0)

_RESOLVER_NAME = "objectscript_calls"
_RESOLVER_SUFFIXES = frozenset({".cls", ".mac", ".inc", ".rtn"})
_PATCHED_EXTENSIONS = frozenset({".mac", ".rtn"})
_DEBUG_ENV = "GRAPHIFY_OBJECTSCRIPT_DEBUG"

# Best-effort bookkeeping only -- `is_registered()` re-checks the live module
# by default rather than trusting this, since registration can also happen
# through a second import of this package, or ship natively upstream.
_registered = False


def _debug(msg: str) -> None:
    import os

    if not os.environ.get(_DEBUG_ENV):
        return
    try:
        print(f"[graphify-objectscript] {msg}", file=sys.stderr)
    except Exception:
        pass


def _warn(msg: str) -> None:
    try:
        print(f"graphify-objectscript: {msg}", file=sys.stderr)
    except Exception:
        pass


def _parse_version(raw: str) -> tuple[int, int, int] | None:
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)", raw)
    if not m:
        return None
    a, b, c = m.groups()
    return int(a), int(b), int(c)


def _graphifyy_version_ok() -> bool:
    try:
        raw = _importlib_metadata.version("graphifyy")
    except Exception as exc:
        _debug(f"could not read the installed graphifyy version: {exc!r}")
        return False
    version = _parse_version(raw)
    if version is None:
        _debug(f"could not parse graphifyy version {raw!r}")
        return False
    return _MIN_GRAPHIFYY <= version < _MAX_GRAPHIFYY_EXCLUSIVE


def _resolver_api(extract_module):
    """Return (register_fn, LanguageResolver, registered_resolvers_fn).

    Prefers the names re-exported from ``graphify.extract`` (per
    MIGRATION.md, every extractor-facing name lands there); falls back to
    ``graphify.resolver_registry`` directly, since ``registered_resolvers``
    itself is only ever defined there, never re-exported.
    """
    register_fn = getattr(extract_module, "register_language_resolver", None)
    resolver_cls = getattr(extract_module, "LanguageResolver", None)
    registered_fn = getattr(extract_module, "registered_resolvers", None)

    registry = sys.modules.get("graphify.resolver_registry")
    if registry is None:
        try:
            # importlib.import_module, not `import graphify.resolver_registry
            # as registry`: the latter is an attribute walk (`registry =
            # graphify.resolver_registry`) *after* ensuring the submodule is
            # loaded, so it is one more name that would be vulnerable to the
            # same re-entrancy hazard explained in _register() below, if
            # `graphify.__init__`'s lazy __getattr__ map ever grows a
            # same-named entry. import_module() always reads sys.modules
            # directly instead.
            import importlib  # noqa: PLC0415

            registry = importlib.import_module("graphify.resolver_registry")
        except Exception as exc:
            _debug(f"graphify.resolver_registry not importable: {exc!r}")
            registry = None

    if register_fn is None and registry is not None:
        register_fn = getattr(registry, "register", None)
    if resolver_cls is None and registry is not None:
        resolver_cls = getattr(registry, "LanguageResolver", None)
    if registered_fn is None and registry is not None:
        registered_fn = getattr(registry, "registered_resolvers", None)

    return register_fn, resolver_cls, registered_fn


def _load_extractor():
    """Import this package's own ``extractor`` module -- lazily.

    Importing it eagerly at ``graphify_objectscript`` module scope would
    mean every ``import graphify_objectscript`` pulls in ``tree_sitter`` and
    compiles every regex ``extractor.py`` defines at module scope. That
    import is *unavoidable* the moment Python initializes this package on
    the way to ``graphify_objectscript._autoload`` -- which the ``.pth``
    file imports on every interpreter start-up, whether or not that
    particular process ever touches graphify -- so doing it eagerly would
    both add real start-up cost to unrelated Python programs on the machine,
    and (worse) mean a missing/broken ``tree-sitter``/
    ``tree-sitter-objectscript`` install breaks ``site.py``'s ``.pth``
    processing outright instead of failing open inside ``register()`` where
    callers actually expect failures to be handled.
    """
    from graphify_objectscript import extractor as extractor_module  # noqa: PLC0415

    return extractor_module


def _register_resolver(extract_module, extractor_module) -> None:
    register_fn, resolver_cls, registered_fn = _resolver_api(extract_module)
    if register_fn is None or resolver_cls is None:
        _debug("no resolver registration API found; skipping the cross-file resolver")
        return
    if registered_fn is not None:
        try:
            existing_names = {r.name for r in registered_fn()}
        except Exception as exc:
            _debug(f"registered_resolvers() failed: {exc!r}")
            existing_names = set()
        if _RESOLVER_NAME in existing_names:
            return
    try:
        register_fn(
            resolver_cls(
                _RESOLVER_NAME, _RESOLVER_SUFFIXES, extractor_module.resolve_objectscript_calls
            )
        )
    except Exception as exc:
        _debug(f"register_language_resolver() failed: {exc!r}")


def _wrap_get_extractor(extract_module, extractor_module) -> None:
    """Replace ``_get_extractor`` with a version that also content-routes
    ``.cls``/``.inc`` to the ObjectScript extractor.

    Mirrors the fork's placement in ``graphify/extract.py`` exactly: the
    sniff runs for every ``.cls``/``.inc`` file, ahead of (and overriding)
    the plain suffix default -- ``.cls`` -> Apex and ``.inc`` -> Pascal stay
    the default only for files that do not positively look like
    ObjectScript, so every existing Apex/Pascal corpus is unaffected. The
    real ``_get_extractor`` still runs first on every call, so any
    filename-based routing it does (MCP config, package manifests, ...)
    keeps its precedence for the extensions it actually claims.
    """
    original = extract_module._get_extractor
    if getattr(original, "_graphify_objectscript_wrapped", False):
        return  # already wrapped in this process

    def _get_extractor(path):
        result = original(path)
        suffix = path.suffix.lower()
        if suffix == ".cls" and extractor_module._is_objectscript_class(path):
            return extractor_module.extract_objectscript
        if suffix == ".inc" and extractor_module._is_objectscript_include(path):
            return extractor_module.extract_objectscript
        return result

    _get_extractor._graphify_objectscript_wrapped = True
    _get_extractor._graphify_objectscript_original = original
    extract_module._get_extractor = _get_extractor


def _register(extract_module, detect_module) -> bool:
    global _registered

    if extract_module is None or detect_module is None:
        # importlib.import_module(), not `import graphify.extract as _extract`:
        # this function can run *re-entrantly*, from inside the .pth hook's
        # own post-exec callback (see _autoload.py), which fires while we are
        # still nested inside graphify.extract's own exec_module() call --
        # i.e. before the import system has gotten back to binding `extract`
        # as an attribute on the `graphify` package object. `import X.Y as z`
        # resolves via *that* attribute (`z = X.Y`), not via sys.modules
        # directly, and graphify/__init__.py happens to define a module-level
        # __getattr__ that maps the name "extract" to the *function*
        # `graphify.extract.extract` (for `graphify.extract(...)` lazy
        # top-level convenience) -- so mid-reentrancy, `import graphify.extract
        # as _extract` can silently bind _extract to that function instead of
        # the module. import_module() always reads sys.modules directly and
        # is immune to this.
        import importlib  # noqa: PLC0415

        if extract_module is None:
            extract_module = importlib.import_module("graphify.extract")
        if detect_module is None:
            detect_module = importlib.import_module("graphify.detect")

    dispatch = getattr(extract_module, "_DISPATCH", None)
    if not isinstance(dispatch, dict):
        _warn("graphify.extract._DISPATCH not found or not a dict; skipping ObjectScript registration")
        return False

    if ".mac" in dispatch:
        # Either graphify now ships native ObjectScript support, or this
        # package already registered in this process (possibly through a
        # second import path) -- either way there is nothing left to do.
        _registered = True
        return True

    if not hasattr(extract_module, "_get_extractor"):
        _warn("graphify.extract._get_extractor not found; skipping ObjectScript registration")
        return False

    code_extensions = getattr(detect_module, "CODE_EXTENSIONS", None)
    if not isinstance(code_extensions, set):
        _warn("graphify.detect.CODE_EXTENSIONS not found or not a set; skipping ObjectScript registration")
        return False

    if not _graphifyy_version_ok():
        _warn(
            "installed graphifyy version is outside the supported range "
            "(>=0.9.71,<0.10); skipping ObjectScript registration"
        )
        return False

    try:
        extractor_module = _load_extractor()
    except ImportError as exc:
        _warn(
            f"tree-sitter-objectscript not installed ({exc}); skipping ObjectScript "
            "registration -- reinstall graphify-objectscript so its "
            "tree-sitter-objectscript dependency comes along with it"
        )
        return False

    dispatch[".mac"] = extractor_module.extract_objectscript
    dispatch[".rtn"] = extractor_module.extract_objectscript

    _wrap_get_extractor(extract_module, extractor_module)

    code_extensions |= _PATCHED_EXTENSIONS

    watch_module = sys.modules.get("graphify.watch")
    if watch_module is not None:
        watched = getattr(watch_module, "_WATCHED_EXTENSIONS", None)
        if isinstance(watched, set):
            watched |= _PATCHED_EXTENSIONS

    _register_resolver(extract_module, extractor_module)

    _registered = True
    return True


def register(extract_module=None, detect_module=None) -> bool:
    """Patch a running ``graphify.extract``/``graphify.detect`` in place.

    Idempotent: a second call (from this package's own ``.pth`` hook firing
    more than once, from a second copy of the package, or from a caller)
    sees ``".mac" in _DISPATCH`` already and returns immediately. Fails
    open -- on any unexpected shape from the installed ``graphifyy`` release
    (version out of range, a renamed/removed private name) it prints one
    line to stderr and returns ``False`` rather than raising, since this
    runs from an import hook at interpreter start-up where a crash would
    break unrelated code.

    ``extract_module``/``detect_module`` default to the real
    ``graphify.extract``/``graphify.detect``; pass fakes to test this
    function in isolation without a real graphify installed.
    """
    try:
        return _register(extract_module, detect_module)
    except Exception as exc:  # pragma: no cover - defensive, fail open
        _warn(f"failed to register ObjectScript support ({exc!r}); continuing without it")
        return False


def is_registered(extract_module=None) -> bool:
    """Whether ObjectScript support is active in ``graphify.extract`` now.

    Asks the live module directly (``".mac" in _DISPATCH``) rather than
    trusting this process's own bookkeeping, so it is correct regardless of
    *how* registration happened -- this package, a second copy of it, or a
    future graphifyy release shipping native support.
    """
    try:
        if extract_module is None:
            import importlib  # noqa: PLC0415

            # importlib.import_module(), not `import graphify.extract as
            # extract_module` -- see the comment in _register() for why the
            # attribute-walk form is unsafe to use for this in general.
            extract_module = importlib.import_module("graphify.extract")
        dispatch = getattr(extract_module, "_DISPATCH", None)
        if not isinstance(dispatch, dict):
            return False
        return ".mac" in dispatch
    except Exception:
        return _registered

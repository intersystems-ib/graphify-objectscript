"""One-shot import hook that registers ObjectScript support into stock
``graphify`` (the ``graphifyy`` PyPI package) the moment it is imported.

This module is loaded by ``graphify_objectscript.pth`` (dropped at the
site-packages root by the wheel via ``force-include``), which means it runs
during interpreter *start-up* -- before user code, before ``graphify`` itself
is imported, and inside every worker a ``ProcessPoolExecutor`` spawns (macOS
uses the ``spawn`` start method, so each worker re-runs ``site`` and thus this
``.pth`` hook fresh). It also runs before a bare
``python -c "from graphify.extract import ..."`` invocation such as the one
``/graphify`` shells out to -- which is exactly why this has to be an
import-time hook rather than, say, a console-script wrapper: nothing else
runs early enough to patch ``graphify.extract``/``graphify.detect`` before
that one-shot subprocess reads their names.

Contract:
- Import nothing from ``graphify`` at module scope. This file runs on every
  Python start-up in an environment where the package is installed, whether
  or not that particular process ever touches graphify, so it must stay
  import-light (import ``os``/``sys`` only, do no I/O, allocate nothing
  beyond a couple of small objects) -- on the order of microseconds, not
  milliseconds.
- Never raise. A broken hook must not break the interpreter for unrelated
  code. Every externally-visible entry point below is wrapped in
  ``try/except Exception`` with a single, opt-in stderr line on failure
  (``GRAPHIFY_OBJECTSCRIPT_DEBUG=1``) and otherwise silent.
- Be safe when ``graphify``/``graphifyy`` is not installed at all: the target
  module names simply never resolve, ``find_spec`` returns ``None`` for
  everything else unconditionally, and this hook then just sits inert.
"""
from __future__ import annotations

import os
import sys

_TARGET_MODULES = frozenset({"graphify.detect", "graphify.extract"})
_DEBUG_ENV = "GRAPHIFY_OBJECTSCRIPT_DEBUG"


def _debug(msg: str) -> None:
    if not os.environ.get(_DEBUG_ENV):
        return
    try:
        print(f"[graphify-objectscript] {msg}", file=sys.stderr)
    except Exception:
        pass


def _try_register() -> None:
    """Best-effort: import the plugin package and call register(). Never raises."""
    try:
        from graphify_objectscript import register
    except Exception as exc:  # pragma: no cover - defensive
        _debug(f"could not import graphify_objectscript.register: {exc!r}")
        return
    try:
        register()
    except Exception as exc:  # pragma: no cover - defensive
        _debug(f"register() raised: {exc!r}")


class _ExecWrapper:
    """Loader proxy: run the real loader's exec_module, then register().

    Delegates every other attribute to the wrapped loader via __getattr__ so
    it stays correct across whatever concrete Loader implementation resolved
    the target module (typically importlib.machinery.SourceFileLoader, but
    zipimport / a custom finder are also possible), without depending on
    exactly which optional Loader protocol methods that implementation
    provides.
    """

    __slots__ = ("_loader", "_on_done")

    def __init__(self, loader, on_done) -> None:
        self._loader = loader
        self._on_done = on_done

    def create_module(self, spec):
        create = getattr(self._loader, "create_module", None)
        if create is None:
            return None  # tells the import system to use the default module type
        return create(spec)

    def exec_module(self, module) -> None:
        try:
            self._loader.exec_module(module)
        finally:
            try:
                _try_register()
            except Exception as exc:  # pragma: no cover - defensive
                _debug(f"post-exec registration failed: {exc!r}")
            try:
                self._on_done()
            except Exception as exc:  # pragma: no cover - defensive
                _debug(f"finder cleanup failed: {exc!r}")

    def __getattr__(self, name):
        return getattr(self._loader, name)


class _OneShotFinder:
    """meta_path finder that wraps the loader for graphify.detect/.extract.

    ``_fired`` makes this one-shot *before* any list mutation happens: the
    very first call for either target name flips it, and every call after
    that -- including a nested call for the *same* name triggered from
    inside our own ``importlib.util.find_spec`` delegation below, which is
    what would otherwise recurse -- returns None immediately. We rely on
    that flag rather than removing ourselves from ``sys.meta_path`` inside
    ``find_spec``: this method runs while the import system is itself
    iterating that list, and mutating a list mid-iteration can skip the
    finder that would otherwise run right after us.

    Once the target module has actually finished executing (``_ExecWrapper``
    above calls ``on_done`` at the end of ``exec_module``, which runs
    strictly after this method has already returned and the surrounding
    ``sys.meta_path`` iteration has ended), ``on_done`` removes this finder
    for good -- there is nothing left for it to do.
    """

    def __init__(self, on_done) -> None:
        self._fired = False
        self._on_done = on_done

    def find_spec(self, name, path, target=None):
        if self._fired or name not in _TARGET_MODULES:
            return None
        self._fired = True
        try:
            import importlib.util

            spec = importlib.util.find_spec(name)
        except Exception as exc:  # pragma: no cover - defensive
            _debug(f"find_spec({name!r}) failed: {exc!r}")
            return None
        if spec is None or spec.loader is None:
            return spec
        try:
            spec.loader = _ExecWrapper(spec.loader, self._on_done)
        except Exception as exc:  # pragma: no cover - defensive
            _debug(f"could not wrap loader for {name!r}: {exc!r}")
        return spec


_finder: "_OneShotFinder | None" = None


def _uninstall() -> None:
    global _finder
    finder, _finder = _finder, None
    if finder is None:
        return
    try:
        sys.meta_path.remove(finder)
    except ValueError:
        pass


def _install() -> None:
    global _finder
    if _finder is not None:
        return
    finder = _OneShotFinder(on_done=_uninstall)
    sys.meta_path.insert(0, finder)
    _finder = finder


def _autoload() -> None:
    try:
        if "graphify.detect" in sys.modules or "graphify.extract" in sys.modules:
            # graphify already fully imported by something ahead of this hook
            # (unusual, but possible under some loaders/entry points) --
            # register directly instead of trying to hook an import that has
            # already happened.
            _try_register()
            return
        _install()
    except Exception as exc:  # pragma: no cover - defensive
        _debug(f"autoload install failed: {exc!r}")


_autoload()

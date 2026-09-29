"""register() against stock graphifyy: dispatch, routing, resolver, idempotency."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
OS_DIR = FIXTURES / "objectscript"

PASCAL_INC = "{$IFDEF FPC} const MaxItems = 10; {$ENDIF}\n"


def test_register_patches_stock_dispatch_and_code_extensions():
    import graphify_objectscript as go
    from graphify_objectscript import extractor as ext

    assert go.register() is True

    import graphify.detect as detect
    import graphify.extract as extract

    assert extract._DISPATCH[".mac"] is go.extract_objectscript_stock
    assert extract._DISPATCH[".rtn"] is go.extract_objectscript_stock
    # The historical owners of the shared extensions are untouched: only the
    # content sniff (tested below) reroutes a specific .cls/.inc file.
    assert extract._DISPATCH[".cls"] is extract.extract_apex
    assert extract._DISPATCH[".inc"] is extract.extract_pascal

    assert {".mac", ".rtn"} <= detect.CODE_EXTENSIONS
    assert go.is_registered() is True


def test_register_registers_the_cross_file_resolver():
    import graphify_objectscript as go

    go.register()

    from graphify.resolver_registry import registered_resolvers

    resolvers = registered_resolvers()
    matches = [r for r in resolvers if r.name == "objectscript_calls"]
    assert len(matches) == 1
    assert matches[0].suffixes == frozenset({".cls", ".mac", ".inc", ".rtn"})

    from graphify_objectscript import extractor as ext

    assert matches[0].resolve is ext.resolve_objectscript_calls


def test_register_is_idempotent():
    import graphify_objectscript as go
    import graphify.extract as extract

    assert go.register() is True
    dispatch_before = dict(extract._DISPATCH)
    get_extractor_before = extract._get_extractor

    assert go.register() is True
    assert go.register() is True

    assert extract._DISPATCH == dispatch_before
    assert extract._get_extractor is get_extractor_before  # not re-wrapped

    from graphify.resolver_registry import registered_resolvers

    names = [r.name for r in registered_resolvers()]
    assert names.count("objectscript_calls") == 1


def test_get_extractor_routes_objectscript_fixtures():
    import graphify_objectscript as go
    from graphify_objectscript import extractor as ext

    go.register()

    import graphify.extract as extract

    assert extract._get_extractor(OS_DIR / "Demo.Base.cls") is go.extract_objectscript_stock
    assert extract._get_extractor(OS_DIR / "Demo.Service.cls") is go.extract_objectscript_stock
    assert extract._get_extractor(OS_DIR / "DemoMacros.inc") is go.extract_objectscript_stock


def test_get_extractor_keeps_apex_and_pascal(tmp_path):
    import graphify_objectscript as go

    go.register()

    import graphify.extract as extract

    # Real Apex source (not ObjectScript-shaped) stays on extract_apex.
    assert extract._get_extractor(FIXTURES / "sample.cls") is extract.extract_apex

    # Real Pascal include (not ObjectScript-shaped) stays on extract_pascal.
    pascal_inc = tmp_path / "consts.inc"
    pascal_inc.write_text(PASCAL_INC, encoding="utf-8")
    assert extract._get_extractor(pascal_inc) is extract.extract_pascal


def test_register_noop_when_native_support_present():
    """A monkeypatched _DISPATCH that already has ".mac" (simulating a future
    graphifyy release with native ObjectScript support, or a second copy of
    this package already having registered) must not be touched further."""
    import graphify_objectscript as go

    sentinel = object()
    fake_extract = SimpleNamespace(
        _DISPATCH={".mac": sentinel, ".cls": object(), ".inc": object()},
        _get_extractor=lambda path: None,
    )
    fake_detect = SimpleNamespace(CODE_EXTENSIONS={".cls", ".inc"})

    assert go.register(extract_module=fake_extract, detect_module=fake_detect) is True

    # Untouched: still the sentinel, and detect's set was never written to.
    assert fake_extract._DISPATCH[".mac"] is sentinel
    assert ".rtn" not in fake_detect.CODE_EXTENSIONS
    assert fake_detect.CODE_EXTENSIONS == {".cls", ".inc"}


def test_register_fails_open_on_unexpected_shape():
    """A structurally-broken module object (missing _DISPATCH) must not raise
    -- register() prints one line and returns False."""
    import graphify_objectscript as go

    fake_extract = SimpleNamespace()  # no _DISPATCH at all
    fake_detect = SimpleNamespace(CODE_EXTENSIONS=set())

    assert go.register(extract_module=fake_extract, detect_module=fake_detect) is False


def test_is_registered_reports_false_for_an_unpatched_module():
    import graphify_objectscript as go

    fake_extract = SimpleNamespace(_DISPATCH={".cls": object()})
    assert go.is_registered(extract_module=fake_extract) is False

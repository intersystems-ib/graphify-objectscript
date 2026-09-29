"""End-to-end extraction through stock graphify + this wrapper's register().

Mirrors the key assertions of the fork's
tests/test_objectscript_extractor.py::test_objectscript_cross_file_end_to_end
-- this is the same fixture set (byte-identical, see tests/test_sync.py) and
the same extractor module (verbatim copy), so the graph it produces through
`graphify.extract.extract()` on stock graphifyy should look the same.
"""
from __future__ import annotations

import importlib.util as _ilu
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
OS_DIR = FIXTURES / "objectscript"

_needs_objectscript = pytest.mark.skipif(
    _ilu.find_spec("tree_sitter_objectscript_udl") is None
    or _ilu.find_spec("tree_sitter_objectscript_routine") is None,
    reason="tree-sitter-objectscript not installed",
)


def _edge_labels(result: dict, relation: str, *, confidence: str | None = None) -> set[tuple[str, str]]:
    labels = {node["id"]: node["label"] for node in result["nodes"]}
    return {
        (labels.get(edge["source"], edge["source"]), labels.get(edge["target"], edge["target"]))
        for edge in result["edges"]
        if edge["relation"] == relation
        and (confidence is None or edge.get("confidence") == confidence)
    }


@_needs_objectscript
def test_objectscript_cross_file_end_to_end(tmp_path):
    import graphify_objectscript as go

    assert go.register() is True

    import graphify.extract as extract

    result = extract.extract(sorted(OS_DIR.glob("*")), cache_root=tmp_path)
    labels_map = {n["id"]: n["label"] for n in result["nodes"]}

    # `.Run()` -> `.Start()`: once via `##class(Demo.Base).Start()`, once via
    # `..Start()` (inherited) -- the resolver must dedupe these to one edge.
    run_start_edges = [
        e
        for e in result["edges"]
        if e["relation"] == "calls"
        and labels_map.get(e["source"]) == ".Run()"
        and labels_map.get(e["target"]) == ".Start()"
    ]
    assert len(run_start_edges) == 1
    assert run_start_edges[0]["confidence"] == "EXTRACTED"

    call_labels = _edge_labels(result, "calls")
    assert (".Run()", "Calc()") in call_labels
    assert ("Main()", "Calc()") in call_labels

    assert ("Service", "Base") in _edge_labels(result, "inherits")

    # Class/include stubs must be retargeted to the real cross-file nodes,
    # not left dangling as external_class/external_include placeholders.
    all_labels = {n["label"] for n in result["nodes"]}
    assert "Demo.Base" not in all_labels
    assert "Demo.Util" not in all_labels
    # %Library.RegisteredObject (never defined in the fixtures) and
    # Demo.DT.Map (deliberately undefined, see below) legitimately remain
    # stubs -- only the two classes actually defined in the fixture corpus
    # must have been retargeted off their stubs.
    stub_labels = {
        n["label"] for n in result["nodes"] if n.get("metadata", {}).get("kind") == "external_class"
    }
    assert "Demo.Base" not in stub_labels
    assert "Demo.Util" not in stub_labels

    include_edges = [e for e in result["edges"] if e["relation"] == "includes"]
    assert len(include_edges) == 2
    assert len({e["target"] for e in include_edges}) == 1
    assert labels_map[include_edges[0]["target"]] == "DemoMacros"

    # `Demo.DT.Map` is only ever referenced from XData -- it must never be
    # EXTRACTED, and it must stay a dangling-free stub reference.
    xdata_edges = [e for e in result["edges"] if labels_map.get(e["target"]) == "Demo.DT.Map"]
    assert xdata_edges, "expected at least one edge referencing the undefined Demo.DT.Map stub"
    assert all(e["confidence"] in ("INFERRED", "AMBIGUOUS") for e in xdata_edges)
    assert all(e["confidence"] != "EXTRACTED" for e in xdata_edges)

    uses_inferred = _edge_labels(result, "uses", confidence="INFERRED")
    assert (".Run()", "$$$DEMOOK") in uses_inferred
    assert ("Main()", "$$$DEMOOK") in uses_inferred


@_needs_objectscript
def test_objectscript_no_dangling_edges(tmp_path):
    import graphify_objectscript as go

    go.register()

    import graphify.extract as extract

    result = extract.extract(sorted(OS_DIR.glob("*")), cache_root=tmp_path)
    node_ids = {n["id"] for n in result["nodes"]}
    for edge in result["edges"]:
        assert edge["source"] in node_ids, edge
        assert edge["target"] in node_ids, edge


@_needs_objectscript
def test_apex_and_pascal_unaffected(tmp_path):
    """Registering ObjectScript support must not change extraction of real
    Apex/Pascal files that merely share the .cls/.inc suffix."""
    import graphify_objectscript as go

    go.register()

    import graphify.extract as extract

    result = extract.extract([FIXTURES / "sample.cls"], cache_root=tmp_path)
    assert any(n["label"] == "AccountService" for n in result["nodes"])


def test_qualified_calls_never_bind_by_bare_name_on_stock_graphify(tmp_path):
    # Stock graphify has no `is_qualified_call` gate in its shared bare-name
    # pass; the shim re-applies `is_member_call=True` so `$$Calc^Missing(1)`
    # and `##class(Gone.Cls).Helper()` never bind to a Python `Calc()` /
    # `Helper()` that happens to share the corpus.
    import graphify_objectscript as go
    from graphify.extract import extract

    go.register()  # idempotent; may already be registered by the .pth hook
    (tmp_path / "util.py").write_text(
        "def Calc():\n    return 1\n\n\ndef Helper():\n    return 2\n", encoding="utf-8"
    )
    (tmp_path / "r.mac").write_text(
        "ROUTINE r\nMain\n Set x = $$Calc^Missing(1)\n Do ##class(Gone.Cls).Helper()\n Quit\n",
        encoding="utf-8",
    )
    result = extract(
        [tmp_path / "util.py", tmp_path / "r.mac"], cache_root=tmp_path / "cache", root=tmp_path
    )
    labels = {n["id"]: n["label"] for n in result["nodes"]}
    calls = {
        (labels.get(e["source"]), labels.get(e["target"]))
        for e in result["edges"]
        if e["relation"] == "calls"
    }
    assert ("Main()", "Calc()") not in calls
    assert ("Main()", "Helper()") not in calls
    for rc in result.get("raw_calls", []):
        if rc.get("language") == "objectscript":
            assert rc["is_member_call"] is True

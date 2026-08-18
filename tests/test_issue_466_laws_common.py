"""#466 — generate_all_laws uses riigiteataja_common and one emission path."""

from __future__ import annotations

from pathlib import Path

import generate_all_laws
import law_structure
import riigiteataja_common


REPO = Path(__file__).resolve().parent.parent


def test_laws_import_commons_ln_ct_and_errors() -> None:
    assert generate_all_laws.ln is riigiteataja_common.ln
    assert generate_all_laws.ct is riigiteataja_common.ct
    assert generate_all_laws.SourceListFetchError is riigiteataja_common.SourceListFetchError
    assert generate_all_laws.fetch_acts is riigiteataja_common.fetch_acts


def test_laws_fetch_xml_delegates_to_commons(monkeypatch, tmp_path) -> None:
    seen: dict[str, object] = {}

    def fake_common_fetch_xml(url, cache_name, **kwargs):
        seen["url"] = url
        seen["cache_name"] = cache_name
        seen["kwargs"] = kwargs
        return None

    monkeypatch.setattr(generate_all_laws, "DATA_DIR", tmp_path)
    monkeypatch.setattr(generate_all_laws, "common_fetch_xml", fake_common_fetch_xml)
    generate_all_laws.fetch_xml("/akt/123?version=3", "slugX", tid="99")
    assert seen["url"] == "/akt/123?version=3"
    assert seen["cache_name"] == "slugX__tid99"
    kwargs = seen["kwargs"]
    assert kwargs["cache_dir"] == tmp_path
    assert kwargs["fallback_cache_name"] == "slugX"
    assert kwargs["validate_root"] is generate_all_laws._is_trustworthy_xml_root
    assert kwargs["min_size"] == generate_all_laws.MIN_XML_BYTES


def test_single_and_multipart_share_emit() -> None:
    assert generate_all_laws.emit_hierarchy_and_provisions is (
        law_structure.emit_hierarchy_and_provisions
    )
    source = Path(generate_all_laws.__file__).read_text(encoding="utf-8")
    assert source.count("def generate_law_jsonld") == 1
    assert source.count("def generate_multipart_law") == 1
    assert "emit_hierarchy_and_provisions(" in source
    # The chapter walk is no longer duplicated in the two builders.
    assert source.count("Issue #371 (gap 1)") == 0


def test_generate_all_laws_no_longer_owns_duplicate_emission() -> None:
    lines = Path(generate_all_laws.__file__).read_text(encoding="utf-8").count("\n")
    # Pre-fix the module was 3,599 lines with two copy-pasted emitters.
    assert lines < 2300, lines
    assert (REPO / "scripts" / "law_structure.py").is_file()


def test_used_prefixes_proxy_class_is_gone() -> None:
    assert not hasattr(generate_all_laws, "_UsedPrefixesProxy")
    assert isinstance(generate_all_laws._used_prefixes, dict)

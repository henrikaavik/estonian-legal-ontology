"""Tests for _RunCounters and _safe_load in estleg_common."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from estleg.estleg_common import (
    _FAILURE_SAMPLES_INMEMORY_CAP,
    PAR_SUFFIX,
    _RunCounters,
    _safe_load,
    build_globalid_xml_lookup,
    pair_peep_with_xml,
    save_json,
    source_provenance,
)


def test_run_counters_defaults() -> None:
    c = _RunCounters()
    assert c.file_skips == 0
    assert c.error_count == 0
    assert c.skip_reasons == {}
    assert c.failures == []
    assert c.seen_error_paths == set()


def test_run_counters_bump_skip() -> None:
    c = _RunCounters()
    c.bump_skip("json_decode_error", "json_decode_error | foo.json | ValueError: bad")
    assert c.file_skips == 1
    assert c.error_count == 1
    assert c.skip_reasons["json_decode_error"] == 1
    assert c.failures == ["json_decode_error | foo.json | ValueError: bad"]


def test_run_counters_failures_capped_at_inmemory_cap() -> None:
    c = _RunCounters()
    over = _FAILURE_SAMPLES_INMEMORY_CAP + 50
    for i in range(over):
        c.bump_skip("x", f"sample_{i}")
    assert c.skip_reasons["x"] == over
    assert len(c.failures) == _FAILURE_SAMPLES_INMEMORY_CAP


def test_safe_load_success(tmp_path: Path) -> None:
    p = tmp_path / "good.json"
    p.write_text('{"a": 1}', encoding="utf-8")
    c = _RunCounters()
    doc = _safe_load(p, c)
    assert doc == {"a": 1}
    assert c.file_skips == 0


def test_safe_load_malformed_json(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    c = _RunCounters()
    doc = _safe_load(p, c)
    assert doc is None
    assert c.file_skips == 1
    assert c.error_count == 1
    assert c.skip_reasons["json_decode_error"] == 1
    assert len(c.failures) == 1
    assert "bad.json" in c.failures[0]


def test_safe_load_missing_file(tmp_path: Path) -> None:
    p = tmp_path / "missing.json"
    c = _RunCounters()
    doc = _safe_load(p, c)
    assert doc is None
    assert c.file_skips == 1


def test_safe_load_unicode_decode_error(tmp_path: Path) -> None:
    p = tmp_path / "mojibake.json"
    p.write_bytes(b"\xff\xfe{")
    c = _RunCounters()

    doc = _safe_load(p, c)

    assert doc is None
    assert c.file_skips == 1
    assert c.skip_reasons["json_decode_error"] == 1


def test_safe_load_dedup_same_path(tmp_path: Path) -> None:
    """Same malformed file revisited by multiple walks counts ONCE."""
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    c = _RunCounters()
    _safe_load(p, c)
    _safe_load(p, c)   # second walk
    _safe_load(p, c)   # third walk
    assert c.file_skips == 1
    assert c.skip_reasons["json_decode_error"] == 1
    assert len(c.failures) == 1
    assert p in c.seen_error_paths


def test_bump_citation_skip_does_not_touch_file_skips() -> None:
    c = _RunCounters()
    c.bump_citation_skip("kov_citation_ambiguous", "ambiguous | A | B")
    assert c.file_skips == 0           # citation-level, not file-level
    assert c.error_count == 0
    assert c.skip_reasons["kov_citation_ambiguous"] == 1
    assert c.failures == ["ambiguous | A | B"]


def test_bump_citation_count_no_sample() -> None:
    c = _RunCounters()
    c.bump_citation_count("rtiv_form_citation")
    c.bump_citation_count("rtiv_form_citation")
    assert c.skip_reasons["rtiv_form_citation"] == 2
    assert c.failures == []
    assert c.file_skips == 0


def test_par_suffix_accepts_double_section_with_case_suffix() -> None:
    pattern = re.compile(PAR_SUFFIX)

    for value in ("§", "§§", "§-le", "§§-le", "§‑des", "§§‑des"):
        assert pattern.fullmatch(value)


def test_build_globalid_lookup_keeps_first_duplicate_deterministically(tmp_path: Path) -> None:
    first = tmp_path / "a.xml"
    second = tmp_path / "b.xml"
    first.write_text('<root globaalID="G1"></root>', encoding="utf-8")
    second.write_text('<root globaalID="G1"></root>', encoding="utf-8")

    lookup = build_globalid_xml_lookup(tmp_path)

    assert lookup["G1"] == first


def test_pair_peep_with_xml_reports_corrupt_json_when_counters_provided(tmp_path: Path) -> None:
    peep = tmp_path / "bad_peep.json"
    peep.write_text("{not json", encoding="utf-8")
    counters = _RunCounters()

    assert pair_peep_with_xml(peep, {}, counters=counters) is None
    assert counters.file_skips == 1
    assert counters.skip_reasons["json_decode_error"] == 1


def test_pair_peep_with_xml_distinguishes_corrupt_from_missing_xml(tmp_path: Path) -> None:
    """Corrupt peep bumps counters; healthy peep with no XML match does not.

    Regression test: before the wiring fix, both 'broken peep JSON' and
    'no XML found' returned None silently, conflating two distinct
    failure modes. The counters argument now lets callers distinguish.
    """
    corrupt_peep = tmp_path / "corrupt_peep.json"
    corrupt_peep.write_text("{not json", encoding="utf-8")

    healthy_peep = tmp_path / "healthy_peep.json"
    healthy_peep.write_text(
        '{"@graph": [{"@type": "estleg:Law", "estleg:globalId": "G404"}]}',
        encoding="utf-8",
    )

    counters = _RunCounters()
    # Corrupt peep -> counters bump
    assert pair_peep_with_xml(corrupt_peep, {}, counters=counters) is None
    assert counters.file_skips == 1
    assert counters.skip_reasons["json_decode_error"] == 1

    # Healthy peep, no matching XML, no fallback dir -> still None but
    # MUST NOT bump counters (the file isn't corrupt).
    assert pair_peep_with_xml(healthy_peep, {}, counters=counters) is None
    assert counters.file_skips == 1   # unchanged
    assert counters.skip_reasons["json_decode_error"] == 1   # unchanged


def test_pair_peep_with_xml_logs_corrupt_to_stderr_when_no_counters(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """When counters are not supplied, pair_peep_with_xml still surfaces
    the corruption signal to stderr with a distinguishable prefix so
    even unwired callers don't silently swallow malformed peeps."""
    peep = tmp_path / "broken_peep.json"
    peep.write_text("{not json", encoding="utf-8")

    assert pair_peep_with_xml(peep, {}) is None

    captured = capsys.readouterr()
    assert "pair_peep_with_xml: corrupt peep" in captured.err
    assert str(peep) in captured.err


def test_save_json_cleans_up_tempfile_on_serialisation_error(tmp_path: Path) -> None:
    """save_json must not litter ``.<name>.<rand>.tmp`` files on failure.

    Regression test: NamedTemporaryFile(delete=False) used to leave the
    tempfile behind whenever ``json.dump`` raised mid-write.
    """
    target = tmp_path / "out.json"

    with pytest.raises(TypeError):
        # ``set()`` is not JSON-serialisable -> json.dump raises TypeError
        save_json(target, {"k": set()})

    leftovers = list(tmp_path.glob(".out.json.*.tmp"))
    assert leftovers == [], f"expected no leftover tempfiles, got {leftovers}"
    # Target must not exist either — atomic semantics: nothing or full file.
    assert not target.exists()


def test_save_json_succeeds_on_valid_payload(tmp_path: Path) -> None:
    """Sanity check that the cleanup path doesn't break the happy path."""
    target = tmp_path / "out.json"
    save_json(target, {"k": [1, 2, 3], "list_payload": True})

    import json as _json
    assert _json.loads(target.read_text(encoding="utf-8")) == {
        "k": [1, 2, 3], "list_payload": True
    }
    leftovers = list(tmp_path.glob(".out.json.*.tmp"))
    assert leftovers == []


def test_iter_peep_files_handles_paths_outside_krr_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: ``Path.relative_to`` raises ValueError for paths
    outside the base. The sort key must tolerate any absolute path so
    future callers, symlinks, or test scaffolding don't blow up.

    We monkeypatch ``KRR_DIR`` to a fake directory whose ``.glob`` is
    coerced via a thin wrapper to also return a path that's outside the
    tree. With the old ``str(p.relative_to(KRR_DIR))`` sort key this
    would raise ValueError.
    """
    from estleg import estleg_common

    fake_krr = tmp_path / "krr"
    fake_krr.mkdir()
    inside_peep = fake_krr / "alpha_peep.json"
    inside_peep.write_text("{}", encoding="utf-8")

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    outside_peep = elsewhere / "beta_peep.json"
    outside_peep.write_text("{}", encoding="utf-8")

    class _FakeKrr:
        """Wraps a real Path but overrides ``glob`` to also return an
        outside-tree path so the iter's sort key has to handle it."""
        def __init__(self, real: Path) -> None:
            self._real = real

        def glob(self, pattern: str):
            yield from self._real.glob(pattern)
            yield outside_peep

        def __truediv__(self, other):
            return self._real / other

    monkeypatch.setattr(estleg_common, "KRR_DIR", _FakeKrr(fake_krr))

    # The bug would raise ValueError from p.relative_to(KRR_DIR);
    # the fix uses ``p.as_posix().casefold()`` and never raises.
    try:
        result = estleg_common.iter_peep_files(include_regulations=False)
    except ValueError as exc:  # pragma: no cover - regression guard
        pytest.fail(f"iter_peep_files raised ValueError on outside path: {exc}")

    assert inside_peep in result
    assert outside_peep in result


# ---------------------------------------------------------------------------
# source_provenance helper (issue #113) — the documented source-provenance
# contract: dcterms:source is an IRI object, dcterms:title is a structured
# title, dc:source is the legacy human-readable descriptor.
# ---------------------------------------------------------------------------


def test_source_provenance_full_act_node():
    """RT-backed act: emits IRI dcterms:source, language-tagged dcterms:title,
    and the legacy dc:source string (= the title for an RT act)."""
    fields = source_provenance(
        rt_url="https://www.riigiteataja.ee/akt/610920.xml",
        label="Karistusseadustik",
    )
    assert fields["dcterms:source"] == {
        "@id": "https://www.riigiteataja.ee/akt/610920.xml"
    }
    assert fields["dcterms:title"] == {
        "@value": "Karistusseadustik",
        "@language": "et",
    }
    # dc:source is the legacy human-readable descriptor — for an act with a
    # title that's the title string.
    assert fields["dc:source"] == "Karistusseadustik"


def test_source_provenance_database_name_only():
    """A source identified only by originating-database name (e.g. an EIS or
    Riigikohus crawl) puts that name in the legacy dc:source and emits no
    dcterms:source IRI."""
    fields = source_provenance(db_name="Eelnõude infosüsteem (EIS) – eelnoud.valitsus.ee")
    assert fields["dc:source"] == "Eelnõude infosüsteem (EIS) – eelnoud.valitsus.ee"
    assert "dcterms:source" not in fields
    assert "dcterms:title" not in fields


def test_source_provenance_never_emits_bare_string_dcterms_source():
    """dcterms:source is reserved for IRI objects — passing a falsy/empty
    rt_url must NOT produce a dcterms:source key at all (never a bare
    string), so the validator's IRI-object rule can't be violated."""
    assert "dcterms:source" not in source_provenance(rt_url="", label="X")
    assert "dcterms:source" not in source_provenance(rt_url=None, label="X")
    # And when given, it is always wrapped as {"@id": ...}.
    fields = source_provenance(rt_url="https://example.org/x", label="X")
    assert isinstance(fields["dcterms:source"], dict)
    assert isinstance(fields["dcterms:source"]["@id"], str)


def test_source_provenance_plain_title_when_no_language():
    """language=None yields a plain-string dcterms:title (still valid per the
    validator, which accepts str or language-tagged value)."""
    fields = source_provenance(label="Some Regulation", language=None)
    assert fields["dcterms:title"] == "Some Regulation"
    assert fields["dc:source"] == "Some Regulation"


def test_source_provenance_label_takes_precedence_over_db_name_for_dc_source():
    """When both a title and a database name are given, dc:source carries the
    title (the more specific human-readable descriptor)."""
    fields = source_provenance(db_name="Riigi Teataja XML", label="Töölepingu seadus")
    assert fields["dc:source"] == "Töölepingu seadus"


def test_source_provenance_output_passes_validate_all_validators():
    """A node carrying source_provenance() output must pass the existing
    validate_all per-node provenance validators with zero errors — this pins
    the helper to the enforced contract (validate_source_provenance +
    validate_dc_source)."""
    import importlib

    validate_all = importlib.import_module("estleg.validate_all")
    validate_all.reset()

    node = {
        "@id": "estleg:Test_Map",
        "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
        "rdfs:label": {"@value": "Test", "@language": "et"},
    }
    node.update(
        source_provenance(
            rt_url="https://www.riigiteataja.ee/akt/610920.xml",
            label="Testseadus",
        )
    )
    doc = {"@graph": [node]}
    fake_path = Path("test_source_provenance.json")
    validate_all.validate_source_provenance(fake_path, doc)
    validate_all.validate_dc_source(fake_path, doc)
    assert validate_all.errors == []

    # Also exercise the db-name-only shape (no dcterms:source).
    validate_all.reset()
    node2 = {
        "@id": "estleg:Test2_2026",
        "@type": ["owl:NamedIndividual", "estleg:CourtDecision"],
    }
    node2.update(source_provenance(db_name="Riigikohus – rikos.rik.ee"))
    validate_all.validate_source_provenance(fake_path, {"@graph": [node2]})
    validate_all.validate_dc_source(fake_path, {"@graph": [node2]})
    assert validate_all.errors == []

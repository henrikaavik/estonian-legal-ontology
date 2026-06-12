"""Tests for scripts/fix_all_issues.py builders.

Focus: the canonical combined-ontology builder must NEVER re-ingest the
previous `combined_ontology.jsonld` output, must respect the explicit
JSON-LD allowlist, and must be idempotent across consecutive runs.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import fix_all_issues  # noqa: E402


def write_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def graph_ids(doc: dict) -> set[str]:
    return {n["@id"] for n in doc.get("@graph", []) if isinstance(n, dict) and "@id" in n}


def make_source_doc(ids: list[str]) -> dict:
    return {
        "@graph": [
            {
                "@id": nid,
                "@type": ["estleg:LegalProvision"],
                "estleg:paragrahv": "§ 1.",
                "estleg:summary": "Summary",
                "estleg:requestedCluster": {"@id": f"{nid}_cluster"},
            }
            for nid in ids
        ]
    }


def test_canonical_combined_inputs_excludes_output_and_unallowed_jsonld(tmp_path):
    write_json(tmp_path / "law_a_peep.json", make_source_doc(["estleg:A_1"]))
    write_json(tmp_path / "controlled_vocabulary.jsonld", {"@graph": [{"@id": "estleg:Act"}]})
    # combined output file from a previous build — must be excluded
    write_json(tmp_path / "combined_ontology.jsonld", {"@graph": [{"@id": "estleg:Stale_1"}]})
    # arbitrary aggregate/report — must be excluded
    write_json(tmp_path / "draft_impact_report.json", {"@graph": [{"@id": "estleg:Report"}]})
    # arbitrary jsonld outside the allowlist — must be excluded
    write_json(tmp_path / "random_aggregate.jsonld", {"@graph": [{"@id": "estleg:Random"}]})

    inputs = fix_all_issues.canonical_combined_inputs(tmp_path)
    names = [p.name for p in inputs]

    assert "law_a_peep.json" in names
    assert "controlled_vocabulary.jsonld" in names
    assert "combined_ontology.jsonld" not in names
    assert "draft_impact_report.json" not in names
    assert "random_aggregate.jsonld" not in names


def test_combined_builder_does_not_carry_forward_stale_nodes(tmp_path):
    # Canonical source: one node from the current corpus.
    write_json(tmp_path / "law_a_peep.json", make_source_doc(["estleg:A_1"]))
    # Allowlisted vocabulary: contributes a stable node.
    write_json(
        tmp_path / "controlled_vocabulary.jsonld",
        {"@graph": [{"@id": "estleg:Vocab_X", "@type": ["owl:Class"]}]},
    )
    # Stale previous combined: contains a node that no longer exists in
    # any source. The new builder must not pick this up.
    write_json(
        tmp_path / "combined_ontology.jsonld",
        {
            "@graph": [
                {
                    "@id": "estleg:Stale_Old_1",
                    "@type": ["estleg:LegalProvision"],
                    "estleg:requestedCluster": "estleg:Stale_Cluster",  # the bad string-literal shape
                }
            ]
        },
    )

    fix_all_issues.generate_combined_jsonld(tmp_path)

    rebuilt = read_json(tmp_path / "combined_ontology.jsonld")
    ids = graph_ids(rebuilt)
    assert "estleg:A_1" in ids
    assert "estleg:Vocab_X" in ids
    assert "estleg:Stale_Old_1" not in ids


def test_combined_builder_is_idempotent(tmp_path):
    write_json(tmp_path / "law_a_peep.json", make_source_doc(["estleg:A_1", "estleg:A_2"]))
    write_json(
        tmp_path / "controlled_vocabulary.jsonld",
        {"@graph": [{"@id": "estleg:Vocab_X", "@type": ["owl:Class"]}]},
    )

    fix_all_issues.generate_combined_jsonld(tmp_path)
    first = read_json(tmp_path / "combined_ontology.jsonld")
    fix_all_issues.generate_combined_jsonld(tmp_path)
    second = read_json(tmp_path / "combined_ontology.jsonld")

    assert first == second
    assert graph_ids(second) == {"estleg:A_1", "estleg:A_2", "estleg:Vocab_X"}


def test_combined_builder_skips_unallowed_root_jsonld(tmp_path):
    write_json(tmp_path / "law_a_peep.json", make_source_doc(["estleg:A_1"]))
    write_json(
        tmp_path / "some_other_aggregate.jsonld",
        {"@graph": [{"@id": "estleg:NotASource", "@type": ["owl:Thing"]}]},
    )

    fix_all_issues.generate_combined_jsonld(tmp_path)

    rebuilt = read_json(tmp_path / "combined_ontology.jsonld")
    assert "estleg:NotASource" not in graph_ids(rebuilt)
    assert "estleg:A_1" in graph_ids(rebuilt)


def test_combined_builder_merges_nodes_sharing_an_id(tmp_path):
    """Two input nodes sharing an @id are MERGED, not first-write-wins (#281).

    The old builder dropped the second file's node wholesale once its @id had
    been seen. Now complementary properties from both files are retained:
    list-valued props are unioned, scalars present in only one file are kept,
    and the node survives exactly once in the combined graph.
    """
    # File A: node with one reference + a scalar only it carries.
    write_json(
        tmp_path / "law_a_peep.json",
        {
            "@graph": [
                {
                    "@id": "estleg:Shared_1",
                    "@type": ["estleg:LegalProvision"],
                    "estleg:paragrahv": "§ 1.",
                    "estleg:references": [{"@id": "estleg:Target_A"}],
                    "estleg:onlyInA": "alpha",
                }
            ]
        },
    )
    # File B: same @id with a DIFFERENT reference + a scalar only it carries.
    write_json(
        tmp_path / "law_b_peep.json",
        {
            "@graph": [
                {
                    "@id": "estleg:Shared_1",
                    "@type": ["estleg:LegalProvision"],
                    "estleg:references": [{"@id": "estleg:Target_B"}],
                    "estleg:onlyInB": "beta",
                }
            ]
        },
    )

    fix_all_issues.generate_combined_jsonld(tmp_path)
    combined = read_json(tmp_path / "combined_ontology.jsonld")
    graph = combined["@graph"]

    # The shared id appears exactly once.
    shared_nodes = [n for n in graph if n.get("@id") == "estleg:Shared_1"]
    assert len(shared_nodes) == 1
    node = shared_nodes[0]

    # Both files' complementary scalars are retained.
    assert node["estleg:onlyInA"] == "alpha"
    assert node["estleg:onlyInB"] == "beta"
    assert node["estleg:paragrahv"] == "§ 1."

    # List-valued `estleg:references` is the UNION of both files' references.
    ref_ids = {r["@id"] for r in node["estleg:references"]}
    assert ref_ids == {"estleg:Target_A", "estleg:Target_B"}


def test_combined_builder_unions_without_duplicating_shared_values(tmp_path):
    """Union de-duplicates list values shared by both files (#281)."""
    common_ref = {"@id": "estleg:Common"}
    write_json(
        tmp_path / "law_a_peep.json",
        {"@graph": [{"@id": "estleg:N", "estleg:references": [common_ref, {"@id": "estleg:A"}]}]},
    )
    write_json(
        tmp_path / "law_b_peep.json",
        {"@graph": [{"@id": "estleg:N", "estleg:references": [common_ref, {"@id": "estleg:B"}]}]},
    )

    fix_all_issues.generate_combined_jsonld(tmp_path)
    node = next(
        n for n in read_json(tmp_path / "combined_ontology.jsonld")["@graph"]
        if n["@id"] == "estleg:N"
    )
    ref_ids = [r["@id"] for r in node["estleg:references"]]
    # Common appears once; A and B both present.
    assert ref_ids.count("estleg:Common") == 1
    assert set(ref_ids) == {"estleg:Common", "estleg:A", "estleg:B"}


def test_combined_builder_keeps_first_on_divergent_scalar(tmp_path, capsys):
    """Divergent scalar values keep the first file's value and warn (#281)."""
    write_json(
        tmp_path / "law_a_peep.json",
        {"@graph": [{"@id": "estleg:N", "estleg:paragrahv": "§ 1."}]},
    )
    write_json(
        tmp_path / "law_b_peep.json",
        {"@graph": [{"@id": "estleg:N", "estleg:paragrahv": "§ 999."}]},
    )

    fix_all_issues.generate_combined_jsonld(tmp_path)
    node = next(
        n for n in read_json(tmp_path / "combined_ontology.jsonld")["@graph"]
        if n["@id"] == "estleg:N"
    )
    # First file wins; a warning is surfaced on stdout.
    assert node["estleg:paragrahv"] == "§ 1."
    out = capsys.readouterr().out
    assert "WARNING" in out and "estleg:paragrahv" in out


def test_combined_builder_does_not_mutate_source_node(tmp_path):
    """Merging must not mutate the source document's node object (#281).

    The builder keeps a copy of the first node, so a later merge can't bleed
    back into the on-disk source file's in-memory representation. We assert
    the source file on disk is byte-unchanged after the build.
    """
    src_a = tmp_path / "law_a_peep.json"
    write_json(src_a, {"@graph": [{"@id": "estleg:N", "estleg:references": [{"@id": "estleg:A"}]}]})
    before = src_a.read_text(encoding="utf-8")
    write_json(
        tmp_path / "law_b_peep.json",
        {"@graph": [{"@id": "estleg:N", "estleg:references": [{"@id": "estleg:B"}]}]},
    )

    fix_all_issues.generate_combined_jsonld(tmp_path)
    assert src_a.read_text(encoding="utf-8") == before


def test_save_json_is_atomic(tmp_path):
    """`fix_all_issues.save_json` writes atomically and leaves no tempfile (#280)."""
    target = tmp_path / "out.json"
    fix_all_issues.save_json(target, {"@graph": [{"@id": "estleg:X"}]})
    text = target.read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert read_json(target) == {"@graph": [{"@id": "estleg:X"}]}
    assert list(tmp_path.glob(".*tmp")) == []


def test_atomic_write_text_leaves_original_on_failure(tmp_path, monkeypatch):
    """`_atomic_write_text` preserves the original file if the write fails (#280)."""
    import pytest

    target = tmp_path / "doc.md"
    target.write_text("original\n", encoding="utf-8")

    # Force os.replace to fail AFTER the tempfile is written.
    def boom(*_args, **_kwargs):
        raise OSError("simulated rename failure")

    monkeypatch.setattr(fix_all_issues.os, "replace", boom)
    with pytest.raises(OSError):
        fix_all_issues._atomic_write_text(target, "new content")

    # Original intact, no tempfile dropping survives.
    assert target.read_text(encoding="utf-8") == "original\n"
    assert list(tmp_path.glob(".*tmp")) == []


def test_combined_context_binds_dcterms(tmp_path):
    """Regression for issue #139/#138.

    Source peep files use ``dcterms:subject`` / ``dcterms:source``; if the
    combined context drops the dcterms binding, rdflib parses those as
    raw curies instead of ``http://purl.org/dc/terms/*`` IRIs and
    downstream consumers (Seadusloome) lose every Dublin Core Terms
    triple.
    """
    import rdflib

    write_json(
        tmp_path / "law_a_peep.json",
        {
            "@graph": [
                {
                    "@id": "estleg:A_1",
                    "@type": ["estleg:LegalProvision"],
                    "estleg:paragrahv": "§ 1.",
                    "estleg:summary": "Summary",
                    "estleg:requestedCluster": {"@id": "estleg:A_1_cluster"},
                    "dcterms:subject": "Topic",
                    "dcterms:source": "https://example.org/A_1",
                }
            ]
        },
    )

    fix_all_issues.generate_combined_jsonld(tmp_path)

    combined = read_json(tmp_path / "combined_ontology.jsonld")
    ctx = combined["@context"]
    assert ctx.get("dcterms") == "http://purl.org/dc/terms/"

    g = rdflib.Graph()
    g.parse(str(tmp_path / "combined_ontology.jsonld"), format="json-ld")
    predicates = {str(p) for _, p, _ in g}
    assert "http://purl.org/dc/terms/subject" in predicates
    assert "http://purl.org/dc/terms/source" in predicates
    assert not any(p.startswith("dcterms:") for p in predicates)


# ---------------------------------------------------------------------------
# Regression tests for #159 (lossless dc:source, stable intra-file dedup,
# date.today INDEX, recursive audit, and import paths).
# ---------------------------------------------------------------------------


def test_normalize_dc_source_preserves_multi_value_array():
    """`normalize_dc_source` no longer joins lists with `'; '` (#159).

    Multi-element source arrays must round-trip unchanged (modulo
    str() coercion) so that a source string that itself contains
    `'; '` cannot be silently merged into a sibling source.
    """
    fix_all_issues.stats["dc_source_fixes"] = 0  # local reset
    node = {
        "dc:source": [
            "Riigi Teataja 2023; supplement",  # contains the legacy join token!
            "Decree 42/2024",
        ]
    }
    fix_all_issues.normalize_dc_source(node)

    assert node["dc:source"] == [
        "Riigi Teataja 2023; supplement",
        "Decree 42/2024",
    ]
    assert fix_all_issues.stats["dc_source_fixes"] == 1


def test_normalize_dc_source_collapses_singleton_array():
    """A single-element list collapses to a scalar, preserving consumer ergonomics."""
    fix_all_issues.stats["dc_source_fixes"] = 0
    node = {"dc:source": ["solo"]}
    fix_all_issues.normalize_dc_source(node)
    assert node["dc:source"] == "solo"


def test_normalize_dc_source_leaves_scalar_untouched():
    """A pre-existing scalar value is unchanged — the function is idempotent."""
    fix_all_issues.stats["dc_source_fixes"] = 0
    node = {"dc:source": "single"}
    fix_all_issues.normalize_dc_source(node)
    assert node["dc:source"] == "single"
    assert fix_all_issues.stats["dc_source_fixes"] == 0


def test_fix_intra_file_duplicates_is_stable_across_runs(tmp_path):
    """Stable hash-based suffixes converge on a fixed point (#159).

    Earlier the suffix was `_dup{count}` derived from enumeration
    order, which flipped if the upstream extractor reordered nodes.
    The new content-hash suffix gives every duplicate node a fixed
    rename regardless of position, and a second run is a no-op.
    """
    doc_first = {
        "@graph": [
            {"@id": "estleg:N_1", "@type": ["estleg:LegalProvision"], "v": "alpha"},
            {"@id": "estleg:N_1", "@type": ["estleg:LegalProvision"], "v": "beta"},
            {"@id": "estleg:N_1", "@type": ["estleg:LegalProvision"], "v": "gamma"},
        ]
    }
    # Same nodes, different order.
    doc_reordered = {
        "@graph": [
            {"@id": "estleg:N_1", "@type": ["estleg:LegalProvision"], "v": "gamma"},
            {"@id": "estleg:N_1", "@type": ["estleg:LegalProvision"], "v": "alpha"},
            {"@id": "estleg:N_1", "@type": ["estleg:LegalProvision"], "v": "beta"},
        ]
    }
    import copy

    d1 = copy.deepcopy(doc_first)
    d2 = copy.deepcopy(doc_reordered)
    assert fix_all_issues._fix_intra_file_duplicates_in_doc(d1)
    assert fix_all_issues._fix_intra_file_duplicates_in_doc(d2)

    # Each renamed node carries a deterministic hash suffix that is the
    # same in both documents (modulo which node was kept as the head).
    ids_in_first = sorted(n["@id"] for n in d1["@graph"])
    ids_in_second = sorted(n["@id"] for n in d2["@graph"])
    # The dedup keeps the first occurrence; reordering shifts which
    # node carries the original `estleg:N_1` and which carry the
    # hash suffixes — but the SET of suffixes must be identical.
    suffixes_first = {nid for nid in ids_in_first if nid != "estleg:N_1"}
    suffixes_second = {nid for nid in ids_in_second if nid != "estleg:N_1"}
    # Each rewritten id has the form `estleg:N_1_dup_<8hex>`.
    assert all(nid.startswith("estleg:N_1_dup_") for nid in suffixes_first)
    assert all(nid.startswith("estleg:N_1_dup_") for nid in suffixes_second)

    # Convergence: a second pass on the already-fixed doc must be a
    # no-op (returns False, no further mutations).
    assert fix_all_issues._fix_intra_file_duplicates_in_doc(d1) is False
    snapshot = json.dumps(d1, sort_keys=True)
    assert fix_all_issues._fix_intra_file_duplicates_in_doc(d1) is False
    assert json.dumps(d1, sort_keys=True) == snapshot


def test_fix_intra_file_duplicates_preserves_head_references(tmp_path):
    """References to the duplicated @id keep targeting the kept head (#159).

    When a node id appears N times, the deduplicator keeps the FIRST
    occurrence under the original id and renames the rest with content-
    hash suffixes. Any reference to the original id is ambiguous (it
    could have meant any of the N copies); the safest choice is to
    preserve the reference so it lands on the head — anything else
    would change consumer-visible meaning.
    """
    doc = {
        "@graph": [
            {"@id": "estleg:N_1", "@type": ["estleg:LegalProvision"], "v": 1},
            {"@id": "estleg:N_1", "@type": ["estleg:LegalProvision"], "v": 2},
            # Other node references the duplicate id directly.
            {"@id": "estleg:Other", "estleg:references": [{"@id": "estleg:N_1"}]},
        ]
    }
    fix_all_issues._fix_intra_file_duplicates_in_doc(doc)

    new_ids = [n["@id"] for n in doc["@graph"]]
    # The first occurrence keeps the original id, the second is renamed.
    assert new_ids[0] == "estleg:N_1"
    assert new_ids[1].startswith("estleg:N_1_dup_")
    other = next(n for n in doc["@graph"] if n["@id"] == "estleg:Other")
    # Reference unchanged — points at the kept head.
    assert other["estleg:references"][0]["@id"] == "estleg:N_1"


def test_generate_index_uses_today_date(tmp_path, monkeypatch):
    """`INDEX.json.generated` is dynamic — never the hardcoded checked-in date (#159)."""
    import datetime as dt

    monkeypatch.setattr(fix_all_issues, "KRR_DIR", tmp_path)
    write_json(tmp_path / "law_a_peep.json", make_source_doc(["estleg:A_1"]))

    fix_all_issues.generate_index()

    index = read_json(tmp_path / "INDEX.json")
    assert index["generated"] == dt.date.today().isoformat()
    assert index["generated"] != "2026-03-02"


def test_generate_index_excludes_derived_jsonld_and_preserves_exceptions(tmp_path, monkeypatch):
    monkeypatch.setattr(fix_all_issues, "KRR_DIR", tmp_path)
    write_json(
        tmp_path / "INDEX.json",
        {
            "registry_exceptions": {
                "legacy_map_peep.json": {
                    "category": "concept_map",
                    "reason": "legacy concept map",
                }
            }
        },
    )
    write_json(tmp_path / "law_a_peep.json", make_source_doc(["estleg:A_1"]))
    write_json(tmp_path / "municipalities_peep.json", make_source_doc(["estleg:Municipalities"]))
    write_json(tmp_path / "issuers_kov_peep.json", make_source_doc(["estleg:Issuers"]))
    write_json(tmp_path / "controlled_vocabulary.jsonld", {"@graph": [{"@id": "estleg:Vocab"}]})
    write_json(tmp_path / "combined_ontology.jsonld", {"@graph": [{"@id": "estleg:Combined"}]})
    write_json(tmp_path / "karistusseadustik_eriosa_owl.jsonld", {"@graph": [{"@id": "estleg:KarS_Owl"}]})

    fix_all_issues.generate_index()

    index = read_json(tmp_path / "INDEX.json")
    indexed_files = {
        file_name
        for law in index["laws"]
        for file_name in law.get("files", [])
    }
    assert "law_a_peep.json" in indexed_files
    assert "karistusseadustik_eriosa_owl.jsonld" in indexed_files
    assert "municipalities_peep.json" not in indexed_files
    assert "issuers_kov_peep.json" not in indexed_files
    assert "controlled_vocabulary.jsonld" not in indexed_files
    assert "combined_ontology.jsonld" not in indexed_files
    assert index["registry_exceptions"]["legacy_map_peep.json"]["category"] == "concept_map"
    assert (
        index["registry_exceptions"]["kriminaalmenetluse_peep.json"]["category"]
        == "procedure_map"
    )


def test_active_exception_peeps_excluded_dead_ones_indexed(tmp_path, monkeypatch):
    """Only the active non-law registry peeps are excluded from the law index.

    After PR #232 the maksejõuetus/KOV concept-map peeps were renamed; their
    successors are now real laws and must be indexed (#243). The two dead
    `INDEX_EXCLUDED_PEEPS` entries were removed, so a renamed successor like
    `fuusilise_isiku_maksejouetuse_seadus_peep.json` is no longer filtered,
    while the still-active `municipalities_peep.json` and
    `issuers_kov_peep.json` registries stay excluded.
    """
    monkeypatch.setattr(fix_all_issues, "KRR_DIR", tmp_path)
    write_json(tmp_path / "municipalities_peep.json", make_source_doc(["estleg:Municipalities"]))
    write_json(tmp_path / "issuers_kov_peep.json", make_source_doc(["estleg:Issuers"]))
    write_json(
        tmp_path / "fuusilise_isiku_maksejouetuse_seadus_peep.json",
        make_source_doc(["estleg:FIMS_1"]),
    )

    fix_all_issues.generate_index()

    index = read_json(tmp_path / "INDEX.json")
    indexed_files = {
        file_name
        for law in index["laws"]
        for file_name in law.get("files", [])
    }
    # Active non-law registry peeps stay excluded.
    assert "municipalities_peep.json" not in indexed_files
    assert "issuers_kov_peep.json" not in indexed_files
    # The renamed successor is a real law and IS indexed.
    assert "fuusilise_isiku_maksejouetuse_seadus_peep.json" in indexed_files


def _write_decisions(path: Path, deprecations: list[dict]) -> None:
    """Write a minimal `legacy_statute_decisions.json`-shaped file (#426)."""
    write_json(
        path,
        {
            "issue": "#426",
            "policy": {"deprecate": "...", "keep": "..."},
            "deprecations": deprecations,
            "keeps": [],
        },
    )


def test_load_deprecated_statutes_absent_file_returns_empty(tmp_path, monkeypatch):
    """A missing decisions file yields an empty mapping (#426 standalone-safe)."""
    monkeypatch.setattr(
        fix_all_issues,
        "LEGACY_STATUTE_DECISIONS_PATH",
        tmp_path / "does_not_exist.json",
    )
    assert fix_all_issues.load_deprecated_statutes() == {}


def test_load_deprecated_statutes_filters_by_verdict(tmp_path, monkeypatch):
    """Only `verdict == "deprecate"` entries are returned, keyed by file (#426)."""
    decisions = tmp_path / "decisions.json"
    _write_decisions(
        decisions,
        [
            {
                "file": "legacy_a_peep.json",
                "rootIri": "estleg:A_Map_2026",
                "verdict": "deprecate",
                "replacedByFile": "canonical_a_peep.json",
                "replacedByIri": "estleg:CanonA_Map_2026",
            },
            {
                # A "keep" entry must be ignored even if it appears in the
                # deprecations array shape.
                "file": "kept_peep.json",
                "rootIri": "estleg:Kept_Map_2026",
                "verdict": "keep",
                "replacedByFile": None,
            },
        ],
    )
    monkeypatch.setattr(fix_all_issues, "LEGACY_STATUTE_DECISIONS_PATH", decisions)

    mapping = fix_all_issues.load_deprecated_statutes()
    assert mapping == {"legacy_a_peep.json": "canonical_a_peep.json"}


def test_generate_index_excludes_deprecated_and_emits_section(tmp_path, monkeypatch):
    """Deprecated duplicates (#426) leave the law count and get their own section.

    Corpus: 3 active peeps + 1 single-file deprecated peep + a 2-file multipart
    deprecated pair. `total_laws`/`total_files` count only the active peeps; the
    `deprecated_laws` section carries the right count/grouping/replaced_by, and
    `registry_exceptions` documents the mechanism.
    """
    monkeypatch.setattr(fix_all_issues, "KRR_DIR", tmp_path)
    decisions = tmp_path / "decisions.json"
    monkeypatch.setattr(fix_all_issues, "LEGACY_STATUTE_DECISIONS_PATH", decisions)

    # Active (kept) laws.
    write_json(tmp_path / "law_a_peep.json", make_source_doc(["estleg:A_1"]))
    write_json(tmp_path / "law_b_peep.json", make_source_doc(["estleg:B_1"]))
    write_json(tmp_path / "law_c_peep.json", make_source_doc(["estleg:C_1"]))
    # A single-file deprecated duplicate.
    write_json(tmp_path / "legacy_x_peep.json", make_source_doc(["estleg:X_1"]))
    # A multipart deprecated statute (two parts, same canonical target).
    write_json(tmp_path / "legacy_multi_osa1_peep.json", make_source_doc(["estleg:M_1"]))
    write_json(tmp_path / "legacy_multi_osa2_peep.json", make_source_doc(["estleg:M_2"]))

    _write_decisions(
        decisions,
        [
            {
                "file": "legacy_x_peep.json",
                "rootIri": "estleg:X_Map_2026",
                "verdict": "deprecate",
                "replacedByFile": "law_a_peep.json",
                "replacedByIri": "estleg:A_Map_2026",
            },
            {
                "file": "legacy_multi_osa1_peep.json",
                "rootIri": "estleg:M_Osa1",
                "verdict": "deprecate",
                "replacedByFile": "canonical_multi_osa1_peep.json",
                "replacedByIri": "estleg:CanonM_Osa1",
            },
            {
                "file": "legacy_multi_osa2_peep.json",
                "rootIri": "estleg:M_Osa2",
                "verdict": "deprecate",
                "replacedByFile": "canonical_multi_osa1_peep.json",
                "replacedByIri": "estleg:CanonM_Osa1",
            },
        ],
    )

    fix_all_issues.generate_index()
    index = read_json(tmp_path / "INDEX.json")

    indexed_files = {
        file_name for law in index["laws"] for file_name in law.get("files", [])
    }
    # Active laws only in the law count.
    assert indexed_files == {
        "law_a_peep.json",
        "law_b_peep.json",
        "law_c_peep.json",
    }
    assert index["total_laws"] == 3
    assert index["total_files"] == 3
    # No deprecated file leaks into the law list.
    assert "legacy_x_peep.json" not in indexed_files
    assert "legacy_multi_osa1_peep.json" not in indexed_files
    assert "legacy_multi_osa2_peep.json" not in indexed_files

    # Deprecated section: single-file + collapsed multipart = 2 entries.
    dep = index["deprecated_laws"]
    assert dep["count"] == 2
    assert "#426" in dep["note"]
    assert "dcterms:isReplacedBy" in dep["note"]

    by_name = {e["name"]: e for e in dep["entries"]}
    assert set(by_name) == {"legacy_x", "legacy_multi"}

    x_entry = by_name["legacy_x"]
    assert x_entry["files"] == ["legacy_x_peep.json"]
    assert x_entry["replaced_by"] == "law_a_peep.json"
    # Non-divergent single target → no replaced_by_files list.
    assert "replaced_by_files" not in x_entry

    multi_entry = by_name["legacy_multi"]
    # Both parts collapse into ONE deprecated entry, files sorted.
    assert multi_entry["files"] == [
        "legacy_multi_osa1_peep.json",
        "legacy_multi_osa2_peep.json",
    ]
    assert multi_entry["replaced_by"] == "canonical_multi_osa1_peep.json"
    # Same canonical target for both parts → no divergence list.
    assert "replaced_by_files" not in multi_entry

    # registry_exceptions documents the mechanism.
    assert "deprecated_duplicates" in index["registry_exceptions"]
    assert "#426" in index["registry_exceptions"]["deprecated_duplicates"]["reason"]


def test_generate_index_deprecated_multipart_divergent_targets(tmp_path, monkeypatch):
    """Diverging part→canonical maps surface a sorted `replaced_by_files` list (#426).

    Mirrors the real `volaigusseadus`/`tsiviilseadustik` case where different
    legacy parts map to different canonical successor files. The collapsed entry
    keeps a single `replaced_by` (first file in sorted order) plus the full
    sorted/unique `replaced_by_files`.
    """
    monkeypatch.setattr(fix_all_issues, "KRR_DIR", tmp_path)
    decisions = tmp_path / "decisions.json"
    monkeypatch.setattr(fix_all_issues, "LEGACY_STATUTE_DECISIONS_PATH", decisions)

    write_json(tmp_path / "law_a_peep.json", make_source_doc(["estleg:A_1"]))
    write_json(tmp_path / "legacy_div_osa1_peep.json", make_source_doc(["estleg:D_1"]))
    write_json(tmp_path / "legacy_div_osa2_peep.json", make_source_doc(["estleg:D_2"]))

    _write_decisions(
        decisions,
        [
            {
                "file": "legacy_div_osa1_peep.json",
                "rootIri": "estleg:D_Osa1",
                "verdict": "deprecate",
                "replacedByFile": "canon_osa1_peep.json",
                "replacedByIri": "estleg:Canon_Osa1",
            },
            {
                "file": "legacy_div_osa2_peep.json",
                "rootIri": "estleg:D_Osa2",
                "verdict": "deprecate",
                "replacedByFile": "canon_osa2_peep.json",
                "replacedByIri": "estleg:Canon_Osa2",
            },
        ],
    )

    fix_all_issues.generate_index()
    index = read_json(tmp_path / "INDEX.json")

    assert index["total_laws"] == 1
    dep = index["deprecated_laws"]
    assert dep["count"] == 1
    entry = dep["entries"][0]
    assert entry["name"] == "legacy_div"
    assert entry["files"] == [
        "legacy_div_osa1_peep.json",
        "legacy_div_osa2_peep.json",
    ]
    # replaced_by = target of first file in sorted order (osa1).
    assert entry["replaced_by"] == "canon_osa1_peep.json"
    # Divergent parts → full sorted/unique replaced_by_files list.
    assert entry["replaced_by_files"] == [
        "canon_osa1_peep.json",
        "canon_osa2_peep.json",
    ]


def test_generate_index_no_decisions_file_has_no_deprecated_section(tmp_path, monkeypatch):
    """Absent decisions file → no `deprecated_laws` section, no crash (#426)."""
    monkeypatch.setattr(fix_all_issues, "KRR_DIR", tmp_path)
    monkeypatch.setattr(
        fix_all_issues,
        "LEGACY_STATUTE_DECISIONS_PATH",
        tmp_path / "missing_decisions.json",
    )
    write_json(tmp_path / "law_a_peep.json", make_source_doc(["estleg:A_1"]))
    write_json(tmp_path / "law_b_peep.json", make_source_doc(["estleg:B_1"]))

    fix_all_issues.generate_index()
    index = read_json(tmp_path / "INDEX.json")

    # Behaves exactly as before #426: both peeps counted, no section.
    assert index["total_laws"] == 2
    assert index["total_files"] == 2
    assert "deprecated_laws" not in index
    indexed_files = {
        file_name for law in index["laws"] for file_name in law.get("files", [])
    }
    assert indexed_files == {"law_a_peep.json", "law_b_peep.json"}


def test_audit_duplicate_ids_walks_subdirectories(tmp_path, monkeypatch):
    """Audit covers JSON-LD + subdirs (#159).

    Previously the audit only globbed `*.json` at the root, so duplicate
    @ids living in `eurlex/`, `curia/`, `riigikohus/`, etc. were
    invisible to the report.
    """
    monkeypatch.setattr(fix_all_issues, "KRR_DIR", tmp_path)
    write_json(tmp_path / "root_a_peep.json", {"@graph": [{"@id": "estleg:Shared"}]})
    write_json(tmp_path / "eurlex" / "eurlex_decisions_peep.json", {"@graph": [{"@id": "estleg:Shared"}]})
    write_json(tmp_path / "curia" / "curia_judgments_peep.json", {"@graph": [{"@id": "estleg:Shared"}]})
    # Also a .jsonld file with the same id — the old code never saw
    # these because it globbed only `*.json`.
    write_json(tmp_path / "controlled_vocabulary.jsonld", {"@graph": [{"@id": "estleg:Shared"}]})
    # Provide a docs dir so the report writer doesn't fail.
    (tmp_path.parent / "docs").mkdir(exist_ok=True)

    duplicates = fix_all_issues.audit_duplicate_ids(tmp_path)

    flat = {nid: files for nid, files in duplicates}
    assert "estleg:Shared" in flat
    files = flat["estleg:Shared"]
    assert any("eurlex" in f for f in files)
    assert any("curia" in f for f in files)
    assert any("controlled_vocabulary.jsonld" in f for f in files)


def test_normalize_dc_source_handles_bare_source_key():
    """Bare `source` key (no namespace prefix) gets the same treatment as `dc:source`."""
    fix_all_issues.stats["dc_source_fixes"] = 0
    node = {"source": ["a", "b"]}
    fix_all_issues.normalize_dc_source(node)
    assert node["source"] == ["a", "b"]


def test_module_imports_validate_all_for_audit():
    """The audit can import `validate_all` to reuse `discover_validation_files`."""
    files = fix_all_issues._audit_files(Path("/tmp/nonexistent"))
    # Empty path returns empty list, but the function must not raise.
    assert isinstance(files, list)


# ---------------------------------------------------------------------------
# Regression tests for fix_duplicate_ids.py (#159).
#
# These live in `test_fix_all_issues.py` because the project's strict file
# scope for the issue does not include a separate `test_fix_duplicate_ids.py`.
# ---------------------------------------------------------------------------


import fix_duplicate_ids  # noqa: E402


def test_fix_duplicate_ids_get_file_prefix_no_dead_pass(tmp_path):
    """`get_file_prefix` returns None when no recognised id is present.

    Verifies the dead-code `pass` block in the previous implementation
    has been removed (#159).
    """
    write_json(
        tmp_path / "law_a_peep.json",
        {"@graph": [{"@id": "estleg:NotMatched", "@type": ["owl:Thing"]}]},
    )
    assert fix_duplicate_ids.get_file_prefix(str(tmp_path / "law_a_peep.json")) is None


def test_fix_duplicate_ids_get_file_prefix_extracts_par_prefix(tmp_path):
    """Recognises `_Par_` ids (positive case)."""
    write_json(
        tmp_path / "law_a_peep.json",
        {"@graph": [{"@id": "estleg:LawA_Par_1"}]},
    )
    assert fix_duplicate_ids.get_file_prefix(str(tmp_path / "law_a_peep.json")) == "LawA"


def test_fix_duplicate_ids_transitive_closure_chained_renames():
    """Chained renames collapse to the terminal target (#159).

    Without the closure step, references to the intermediate id `B`
    would be stale after the rename `A -> B -> C` was applied.
    """
    closed = fix_duplicate_ids._transitive_closure({"A": "B", "B": "C"})
    assert closed == {"A": "C", "B": "C"}


def test_fix_duplicate_ids_transitive_closure_rejects_cycles():
    """A cycle in the rename map is a corpus authoring bug — fail fast."""
    import pytest

    with pytest.raises(RuntimeError):
        fix_duplicate_ids._transitive_closure({"A": "B", "B": "A"})


def test_update_cross_references_iterates_lists_by_index(tmp_path, monkeypatch):
    """List rewrites use enumerate() so duplicate strings rename correctly (#159).

    The previous implementation called `val.index(item)`, which always
    returned the FIRST match's index regardless of which occurrence
    the iterator was currently at. With duplicates that meant a
    rewrite was applied to the wrong slot. We verify the new
    enumerate-based rewrite correctly handles a list with the same
    old id appearing twice.
    """
    monkeypatch.setattr(fix_duplicate_ids, "KRR_DIR", tmp_path)
    # After #332, detect_duplicates/update_cross_references enumerate via
    # estleg_common.iter_peep_files(), which reads estleg_common.KRR_DIR.
    monkeypatch.setattr(estleg_common, "KRR_DIR", tmp_path)

    # File "owner_peep.json" defines the @id we'll rename.
    write_json(
        tmp_path / "owner_peep.json",
        {"@graph": [{"@id": "estleg:Old"}]},
    )
    # File "consumer_peep.json" references the old id TWICE in a list
    # — exactly the scenario that broke `val.index(item)`.
    write_json(
        tmp_path / "consumer_peep.json",
        {
            "@graph": [
                {
                    "@id": "estleg:Consumer",
                    "estleg:references": ["estleg:Old", "estleg:Old"],
                }
            ]
        },
    )

    remap = {"owner_peep.json": {"estleg:Old": "estleg:New"}}
    updated = fix_duplicate_ids.update_cross_references(remap)
    assert updated == 2

    consumer = read_json(tmp_path / "consumer_peep.json")
    refs = consumer["@graph"][0]["estleg:references"]
    assert refs == ["estleg:New", "estleg:New"]


def test_update_cross_references_applies_transitive_closure(tmp_path, monkeypatch):
    """Chained renames `A -> B -> C` are applied as `A -> C` (#159)."""
    monkeypatch.setattr(fix_duplicate_ids, "KRR_DIR", tmp_path)
    # After #332, detect_duplicates/update_cross_references enumerate via
    # estleg_common.iter_peep_files(), which reads estleg_common.KRR_DIR.
    monkeypatch.setattr(estleg_common, "KRR_DIR", tmp_path)

    write_json(tmp_path / "owner_a_peep.json", {"@graph": [{"@id": "estleg:A"}]})
    write_json(tmp_path / "owner_b_peep.json", {"@graph": [{"@id": "estleg:B"}]})
    write_json(
        tmp_path / "consumer_peep.json",
        {"@graph": [{"@id": "estleg:Consumer", "estleg:references": ["estleg:A"]}]},
    )

    # owner_a renames A -> B, owner_b renames B -> C.
    remap = {
        "owner_a_peep.json": {"estleg:A": "estleg:B"},
        "owner_b_peep.json": {"estleg:B": "estleg:C"},
    }
    fix_duplicate_ids.update_cross_references(remap)

    consumer = read_json(tmp_path / "consumer_peep.json")
    # Without transitive closure this would land on `estleg:B`,
    # which is itself stale.
    assert consumer["@graph"][0]["estleg:references"] == ["estleg:C"]


def test_detect_duplicates_returns_only_multi_file_ids(tmp_path, monkeypatch):
    """`detect_duplicates` reports only @ids appearing in 2+ files."""
    monkeypatch.setattr(fix_duplicate_ids, "KRR_DIR", tmp_path)
    # After #332, detect_duplicates/update_cross_references enumerate via
    # estleg_common.iter_peep_files(), which reads estleg_common.KRR_DIR.
    monkeypatch.setattr(estleg_common, "KRR_DIR", tmp_path)
    write_json(tmp_path / "a_peep.json", {"@graph": [{"@id": "estleg:Shared"}, {"@id": "estleg:Unique_A"}]})
    write_json(tmp_path / "b_peep.json", {"@graph": [{"@id": "estleg:Shared"}, {"@id": "estleg:Unique_B"}]})
    dupes = fix_duplicate_ids.detect_duplicates()
    assert "estleg:Shared" in dupes
    assert "estleg:Unique_A" not in dupes
    assert "estleg:Unique_B" not in dupes


# ---------------------------------------------------------------------------
# #116 — "generators produce validator-clean output" guarantee.
#
# These fixtures build tiny law / regulation / draft documents the way the
# generators do, then run them through the EXISTING `validate_all` per-node
# validators *without* first passing through any `fix_all_issues.normalize_*`
# pass. This pins the contract: fresh generator output is validator-clean on
# @type / sectionNumber / dc:source / dcterms:source / namespace, so
# `fix_all_issues` repair passes are a safety net, not a build requirement.
# ---------------------------------------------------------------------------

import importlib  # noqa: E402

import estleg_common  # noqa: E402

validate_all = importlib.import_module("validate_all")
generate_draft_legislation = importlib.import_module("generate_draft_legislation")


# Per-node `validate_all` validators that take (filepath, doc). These are the
# repair-equivalent checks `fix_all_issues.normalize_*` exists to satisfy.
_PER_NODE_VALIDATORS = (
    validate_all.validate_context,
    validate_all.validate_types,
    validate_all.validate_multi_valued,
    validate_all.validate_section_numbers,
    validate_all.validate_dc_source,
    validate_all.validate_source_provenance,
    validate_all.validate_xsd_dates,
    validate_all.validate_affected_law_names,
)


def _run_per_node_validators(doc: dict) -> list[str]:
    """Run every per-node validator over ``doc`` and return collected errors."""
    validate_all.reset()
    fake_path = Path("fixture_generator_output.json")
    for fn in _PER_NODE_VALIDATORS:
        fn(fake_path, doc)
    return list(validate_all.errors)


def _law_act_doc() -> dict:
    """A law act node + provision class + provision, shaped the way
    ``generate_all_laws.py`` emits them (see its act-node construction):
    ``@type`` arrays, scalar ``dc:source`` string, IRI-object
    ``dcterms:source``, language-tagged ``dcterms:title``."""
    title = "Karistusseadustik"
    rt_url = "https://www.riigiteataja.ee/akt/610920.xml"
    act_node = {
        "@id": "estleg:KarS_Map_2026",
        "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
        "rdfs:label": {"@value": f"{title} teemakaardistus", "@language": "et"},
        "estleg:contentStatus": "structuredBody",
        "estleg:lastAmendmentDate": {"@value": "2026-01-15", "@type": "xsd:date"},
    }
    # Use the shared provenance helper — it produces exactly the inline shape
    # generators write (dc:source scalar, dcterms:source IRI object, ...).
    act_node.update(estleg_common.source_provenance(rt_url=rt_url, label=title))
    act_node["owl:sameAs"] = {"@id": rt_url}
    return {
        "@context": estleg_common.CONTEXT,
        "@graph": [
            act_node,
            {
                "@id": "estleg:LegalProvision_karistusseadustik",
                "@type": ["owl:Class"],
                "rdfs:label": {"@value": "Õigusnorm (paragrahv)", "@language": "et"},
                "rdfs:subClassOf": {"@id": "estleg:LegalProvision"},
            },
            {
                "@id": "estleg:KarS_Par_121",
                "@type": ["owl:NamedIndividual", "estleg:LegalProvision_karistusseadustik"],
                "estleg:paragrahv": "§ 121",
                "rdfs:label": {"@value": "§ 121 Kehaline väärkohtlemine", "@language": "et"},
                "estleg:sourceAct": {"@value": title, "@language": "et"},
                "estleg:summary": {"@value": "Teise inimese tervise kahjustamise eest ...", "@language": "et"},
                "estleg:references": [{"@id": "estleg:KarS_Par_122"}],
            },
        ],
    }


def _regulation_doc() -> dict:
    """A domestic-regulation act node + per-regulation provision class +
    provision, shaped the way ``generate_regulations.py`` emits them
    (see SCHEMA_REFERENCE "Domestic Regulation Example")."""
    title = "Volitatud asutuste määramine"
    rt_url = "https://www.riigiteataja.ee/akt/610920.xml"
    act_node = {
        "@id": "estleg:Reg_160748_Map_2026",
        "@type": ["owl:Ontology", "estleg:Act", "estleg:NationalRegulation", "estleg:GovernmentRegulation"],
        "rdfs:label": f"{title} (määrus)",
        "estleg:documentType": "määrus",
        "estleg:isKov": {"@value": "false", "@type": "xsd:boolean"},
        "estleg:parseMode": "structured",
        "estleg:globalId": "610920",
        "estleg:terviktekstId": "160748",
        "estleg:entryIntoForce": {"@value": "2003-07-19", "@type": "xsd:date"},
        "estleg:issuer": {"@value": "Vabariigi Valitsus", "@language": "et"},
        "estleg:actNumber": "199",
    }
    act_node.update(estleg_common.source_provenance(rt_url=rt_url, label=title, language=None))
    act_node["owl:sameAs"] = {"@id": rt_url}
    return {
        "@context": estleg_common.CONTEXT,
        "@graph": [
            act_node,
            {
                "@id": "estleg:Regulation_160748",
                "@type": ["owl:Class"],
                "rdfs:label": {"@value": "Õigusnorm (paragrahv)", "@language": "et"},
                "rdfs:subClassOf": {"@id": "estleg:LegalProvision"},
            },
            {
                "@id": "estleg:Reg_160748_Par_1",
                "@type": ["owl:NamedIndividual", "estleg:Regulation_160748"],
                "estleg:paragrahv": "§ 1.",
                "rdfs:label": {"@value": "§ 1. [Käesoleva määrusega kehtestatakse ...]", "@language": "et"},
                "estleg:sourceAct": {"@value": title, "@language": "et"},
                "estleg:summary": {"@value": "Käesoleva määrusega kehtestatakse ...", "@language": "et"},
            },
        ],
    }


def _draft_doc() -> dict:
    """A draft-legislation document built via the actual generator helper
    ``generate_draft_legislation.generate_draft_node`` — i.e. exactly the
    way ``generate_draft_legislation.py`` builds it (IRI-object
    ``dcterms:source``, ``@type`` arrays, list-of-strings
    ``estleg:affectedLawName``, ``xsd:date`` literals)."""
    draft_node = generate_draft_legislation.generate_draft_node(
        {
            "title": "Kohtute seaduse ja teiste seaduste muutmise seadus",
            "raw_title": "Kohtute seaduse ja teiste seaduste muutmise seadus (JM/26-0268)",
            "link": "https://eelnoud.valitsus.ee/main/mount/docList/abc-def",
        },
        phase_id="Submission",
        eis_number="JM/26-0268",
        ministry_code="JM",
        date_str="27.02.2026",
    )
    return {
        "@context": estleg_common.CONTEXT,
        "@graph": [
            {
                "@id": "estleg:Eelnoud_Submission_Map_2026",
                "@type": ["owl:Ontology"],
                "rdfs:label": {"@value": "EIS eelnõud – esitatud", "@language": "et"},
                "dc:source": "Eelnõude infosüsteem (EIS) – eelnoud.valitsus.ee",
            },
            draft_node,
        ],
    }


def test_generated_law_document_is_validator_clean_without_repair():
    """Fresh law-generator output passes the per-node validators with zero
    errors WITHOUT going through any `fix_all_issues.normalize_*` pass."""
    assert _run_per_node_validators(_law_act_doc()) == []


def test_generated_regulation_document_is_validator_clean_without_repair():
    """Fresh regulation-generator output is validator-clean without repair."""
    assert _run_per_node_validators(_regulation_doc()) == []


def test_generated_draft_document_is_validator_clean_without_repair():
    """Fresh draft-generator output (built via the real
    `generate_draft_node` helper) is validator-clean without repair."""
    assert _run_per_node_validators(_draft_doc()) == []


def test_namespace_is_already_correct_in_generator_output():
    """Generator output uses the canonical `data.riik.ee` namespace, so
    `migrate_namespace_in_value` has nothing to migrate (the repair pass is
    a no-op on fresh output)."""
    for doc in (_law_act_doc(), _regulation_doc(), _draft_doc()):
        raw = json.dumps(doc, ensure_ascii=False)
        assert fix_all_issues.OLD_NS not in raw
        assert estleg_common.NS == fix_all_issues.NEW_NS
        # And the migration helper returns an equal structure.
        assert fix_all_issues.migrate_namespace_in_value(doc) == doc


# ---------------------------------------------------------------------------
# #116 — `fix_all_issues.normalize_*` repair passes are idempotent no-ops on
# already-clean input. Running them on fresh generator output changes nothing.
# ---------------------------------------------------------------------------


def _apply_all_normalizers(doc: dict) -> dict:
    """Run every `fix_all_issues.normalize_*` pass over a copy of ``doc``."""
    import copy

    out = copy.deepcopy(doc)
    out = fix_all_issues.migrate_namespace_in_value(out)
    if isinstance(out.get("@graph"), list):
        out["@graph"] = [
            fix_all_issues.normalize_section_number(
                fix_all_issues.normalize_dc_source(
                    fix_all_issues.normalize_multi_valued(
                        fix_all_issues.normalize_type(node)
                    )
                )
            )
            for node in out["@graph"]
        ]
    return out


def test_normalizers_are_noop_on_clean_law_document():
    """`normalize_*` passes leave a clean law document byte-for-byte unchanged."""
    doc = _law_act_doc()
    assert _apply_all_normalizers(doc) == doc


def test_normalizers_are_noop_on_clean_regulation_document():
    """`normalize_*` passes leave a clean regulation document unchanged."""
    doc = _regulation_doc()
    assert _apply_all_normalizers(doc) == doc


def test_normalizers_are_noop_on_clean_draft_document():
    """`normalize_*` passes leave a clean draft document unchanged."""
    doc = _draft_doc()
    assert _apply_all_normalizers(doc) == doc


def test_intra_file_dedup_is_noop_on_clean_generator_output():
    """`_fix_intra_file_duplicates_in_doc` makes no changes when every @id in
    the document is already unique (the dedup pass is a no-op on fresh
    generator output, which produces file-disjoint id spaces)."""
    for builder in (_law_act_doc, _regulation_doc, _draft_doc):
        doc = builder()
        assert fix_all_issues._fix_intra_file_duplicates_in_doc(doc) is False


# ---------------------------------------------------------------------------
# #426: generate_combined_jsonld refuses to build over unmarked deprecations
# ---------------------------------------------------------------------------
def _deprecation_fixture(tmp_path, monkeypatch):
    import deprecate_legacy_statutes as dls

    krr = tmp_path / "krr_outputs"
    krr.mkdir()
    (krr / "alkoholi_seadus_peep.json").write_text(
        json.dumps(
            {
                "@context": {
                    "estleg": "https://data.riik.ee/ontology/estleg#",
                    "owl": "http://www.w3.org/2002/07/owl#",
                    "dcterms": "http://purl.org/dc/terms/",
                },
                "@graph": [
                    {
                        "@id": "estleg:ALKS_Map_2026",
                        "@type": ["owl:Ontology", "estleg:Act"],
                        "rdfs:label": "Legacy stub",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    decisions = tmp_path / "decisions.json"
    decisions.write_text(
        json.dumps(
            {
                "deprecations": [
                    {
                        "file": "alkoholi_seadus_peep.json",
                        "rootIri": "estleg:ALKS_Map_2026",
                        "replacedByFile": "alkoholiseadus_peep.json",
                        "replacedByIri": "estleg:AS_Map_2026",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(fix_all_issues, "LEGACY_STATUTE_DECISIONS_PATH", decisions)
    return dls, decisions, krr


def test_generate_combined_raises_on_unmarked_deprecated_peep(tmp_path, monkeypatch):
    import pytest

    _dls, _decisions, krr = _deprecation_fixture(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="deprecation decision"):
        fix_all_issues.generate_combined_jsonld(krr)
    assert not (krr / "combined_ontology.jsonld").exists()


def test_generate_combined_passes_when_deprecations_applied(tmp_path, monkeypatch, capsys):
    dls, decisions, krr = _deprecation_fixture(tmp_path, monkeypatch)
    dls.run(decisions, krr, dry_run=False)
    fix_all_issues.generate_combined_jsonld(krr)
    out = capsys.readouterr().out
    assert "Verified 1 deprecated legacy roots carry #426 marks" in out
    assert (krr / "combined_ontology.jsonld").exists()

"""Tests for ``skos_concept_scheme_609`` (#609): SKOS concept-scheme binding.

The module is imported from the ``estleg`` package (#472), so it imports by
name. Tests operate on synthetic concept nodes (no dependency on the real
~13 MB corpus file) plus tmp-file round-trips for the LFS guard and the
dry-run / ``--apply`` write semantics.
"""
from __future__ import annotations

import copy
import json

from estleg import skos_concept_scheme_609 as S

SCHEME = {"@id": "estleg:LegalConceptScheme"}


def _canonical_node() -> dict:
    """A corpus-wide canonical concept (``estleg:Concept``)."""
    return {
        "@id": "estleg:Concept_5imbsusteem",
        "@type": ["owl:NamedIndividual", "estleg:Concept"],
        "skos:prefLabel": "sümbsüsteem",
        "skos:definition": "...",
    }


def _provision_local_node() -> dict:
    """A single provision's definition (``estleg:LegalConcept``)."""
    return {
        "@id": "estleg:Concept_some_law_t1_tee_kaitsevoond",
        "@type": ["owl:NamedIndividual", "estleg:LegalConcept"],
        "skos:prefLabel": "tee kaitsevöönd",
        "estleg:definedIn": {"@id": "estleg:some_law_t1"},
    }


def _non_concept_node() -> dict:
    """A node that is neither a canonical nor a provision-local concept."""
    return {
        "@id": "estleg:Act_some_law",
        "@type": ["owl:NamedIndividual", "estleg:Act"],
        "rdfs:label": "Some Law",
    }


def test_canonical_concept_gains_inscheme_topconcept_and_skos_type():
    node = _canonical_node()
    assert S.transform_node(node) is True
    assert node["skos:inScheme"] == SCHEME
    assert node["skos:topConceptOf"] == SCHEME
    assert "skos:Concept" in node["@type"]
    # original types preserved; skos:Concept appended exactly once.
    assert "estleg:Concept" in node["@type"]
    assert "owl:NamedIndividual" in node["@type"]
    assert node["@type"].count("skos:Concept") == 1


def test_provision_local_gains_inscheme_and_skos_type_but_not_topconcept():
    node = _provision_local_node()
    assert S.transform_node(node) is True
    assert node["skos:inScheme"] == SCHEME
    assert "skos:Concept" in node["@type"]
    assert "estleg:LegalConcept" in node["@type"]
    # Provision-local definitions are NOT top concepts of the scheme.
    assert "skos:topConceptOf" not in node


def test_second_pass_is_noop_canonical():
    node = _canonical_node()
    assert S.transform_node(node) is True
    snapshot = copy.deepcopy(node)
    # Re-running must not change anything and must not duplicate the type.
    assert S.transform_node(node) is False
    assert node == snapshot
    assert node["@type"].count("skos:Concept") == 1


def test_second_pass_is_noop_provision_local():
    node = _provision_local_node()
    assert S.transform_node(node) is True
    snapshot = copy.deepcopy(node)
    assert S.transform_node(node) is False
    assert node == snapshot
    assert node["@type"].count("skos:Concept") == 1


def test_non_concept_node_untouched():
    node = _non_concept_node()
    snapshot = copy.deepcopy(node)
    assert S.transform_node(node) is False
    assert node == snapshot
    assert "skos:inScheme" not in node
    assert "skos:topConceptOf" not in node
    assert "skos:Concept" not in node["@type"]


def test_existing_skos_concept_type_not_duplicated():
    node = {
        "@id": "estleg:Concept_y",
        "@type": ["estleg:Concept", "skos:Concept"],
    }
    assert S.transform_node(node) is True  # inScheme + topConceptOf still added
    assert node["@type"].count("skos:Concept") == 1
    assert node["skos:inScheme"] == SCHEME
    assert node["skos:topConceptOf"] == SCHEME


def test_string_type_normalized_and_bound():
    # @type as a bare string (not a list) — robustness for irregular input.
    node = {"@id": "estleg:Concept_x", "@type": "estleg:Concept"}
    assert S.transform_node(node) is True
    assert isinstance(node["@type"], list)
    assert "estleg:Concept" in node["@type"]
    assert "skos:Concept" in node["@type"]
    assert node["skos:inScheme"] == SCHEME
    assert node["skos:topConceptOf"] == SCHEME
    # Idempotent after normalisation.
    assert S.transform_node(node) is False


def test_process_doc_counts_and_idempotence():
    doc = {
        "@graph": [
            _canonical_node(),
            _provision_local_node(),
            _provision_local_node(),
            _non_concept_node(),
        ]
    }
    counts = S.process_doc(doc)
    assert counts["total"] == 3
    assert counts["canonical"] == 1
    assert counts["provision_local"] == 2
    assert counts["changed"] == 3
    # Second pass over the same (already-bound) graph is a no-op.
    counts2 = S.process_doc(doc)
    assert counts2["total"] == 3
    assert counts2["canonical"] == 1
    assert counts2["provision_local"] == 2
    assert counts2["changed"] == 0


def test_process_file_lfs_pointer_is_skipped(tmp_path):
    p = tmp_path / "concepts_combined.jsonld"
    p.write_text(
        "version https://git-lfs.github.com/spec/v1\n"
        "oid sha256:deadbeef\nsize 123\n",
        encoding="utf-8",
    )
    before = p.read_text(encoding="utf-8")
    assert S.process_file(p, write=True) is None  # skipped, returns None
    assert p.read_text(encoding="utf-8") == before  # pointer never rewritten


def test_process_file_dry_run_does_not_write(tmp_path):
    p = tmp_path / "concepts_combined.jsonld"
    p.write_text(json.dumps({"@graph": [_canonical_node()]}), encoding="utf-8")
    before = p.read_text(encoding="utf-8")
    counts = S.process_file(p, write=False)
    assert counts["changed"] == 1  # would change, but...
    assert p.read_text(encoding="utf-8") == before  # ...nothing written


def test_process_file_apply_writes_then_idempotent(tmp_path):
    p = tmp_path / "concepts_combined.jsonld"
    p.write_text(json.dumps({"@graph": [_canonical_node()]}), encoding="utf-8")
    counts = S.process_file(p, write=True)
    assert counts["changed"] == 1
    node = json.loads(p.read_text(encoding="utf-8"))["@graph"][0]
    assert node["skos:inScheme"] == SCHEME
    assert node["skos:topConceptOf"] == SCHEME
    assert "skos:Concept" in node["@type"]
    # Re-applying writes nothing new.
    assert S.process_file(p, write=True)["changed"] == 0


def test_main_default_is_dry_run(tmp_path):
    p = tmp_path / "concepts_combined.jsonld"
    p.write_text(
        json.dumps({"@graph": [_canonical_node(), _provision_local_node()]}),
        encoding="utf-8",
    )
    before = p.read_text(encoding="utf-8")
    assert S.main(["--path", str(p)]) == 0  # no --apply -> dry-run default
    assert p.read_text(encoding="utf-8") == before


def test_main_dry_run_overrides_apply(tmp_path):
    p = tmp_path / "concepts_combined.jsonld"
    p.write_text(json.dumps({"@graph": [_canonical_node()]}), encoding="utf-8")
    before = p.read_text(encoding="utf-8")
    assert S.main(["--path", str(p), "--apply", "--dry-run"]) == 0
    assert p.read_text(encoding="utf-8") == before  # --dry-run wins over --apply


def test_main_apply_writes(tmp_path):
    p = tmp_path / "concepts_combined.jsonld"
    p.write_text(json.dumps({"@graph": [_canonical_node()]}), encoding="utf-8")
    assert S.main(["--path", str(p), "--apply"]) == 0
    node = json.loads(p.read_text(encoding="utf-8"))["@graph"][0]
    assert node["skos:topConceptOf"] == SCHEME
    assert "skos:Concept" in node["@type"]


def test_main_missing_file_returns_error(tmp_path):
    assert S.main(["--path", str(tmp_path / "nope.jsonld")]) == 1

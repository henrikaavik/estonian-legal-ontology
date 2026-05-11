import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import validate_all


@pytest.fixture(autouse=True)
def _reset_reporter():
    """Clear the module-level Reporter between every test (#158).

    Previously each test called `validate_all.errors.clear()` by hand,
    which was easy to forget and caused error/warning leakage between
    cases. Using an autouse fixture removes that footgun and exercises
    the new `validate_all.reset()` helper.
    """
    validate_all.reset()
    yield
    validate_all.reset()


def write_json(path: Path, doc: dict) -> None:
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")


def act_doc(*, provisions: bool = True) -> dict:
    graph = [
        {
            "@id": "estleg:Act_1",
            "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
        }
    ]
    if provisions:
        graph.append(
            {
                "@id": "estleg:Act_1_Par_1",
                "@type": ["owl:NamedIndividual", "estleg:LegalProvision_Act1"],
                "estleg:paragrahv": "§ 1.",
            }
        )
    return {"@graph": graph}


def test_validate_xsd_dates_accepts_strict_iso_date(tmp_path):
    validate_all.errors.clear()
    doc = {
        "@graph": [
            {
                "@id": "estleg:Act",
                "estleg:publicationDate": {
                    "@value": "2012-01-01",
                    "@type": "xsd:date",
                },
            }
        ]
    }

    validate_all.validate_xsd_dates(tmp_path / "valid.json", doc)

    assert validate_all.errors == []


def test_validate_xsd_dates_rejects_offset_year_fallback(tmp_path):
    validate_all.errors.clear()
    doc = {
        "@graph": [
            {
                "@id": "estleg:Act",
                "estleg:publicationDate": {
                    "@value": "2012+02:00-01-01",
                    "@type": "xsd:date",
                },
            }
        ]
    }

    validate_all.validate_xsd_dates(tmp_path / "invalid.json", doc)

    assert validate_all.errors
    assert "2012+02:00-01-01" in validate_all.errors[0]


def test_validate_registry_index_accepts_valid_multipart_files(tmp_path):
    validate_all.errors.clear()
    krr = tmp_path / "krr_outputs"
    write_json(krr / "part1_peep.json", act_doc())
    write_json(krr / "part2_peep.json", act_doc())
    write_json(
        krr / "INDEX.json",
        {
            "total_laws": 1,
            "total_files": 2,
            "laws": [{"name": "multipart", "files": ["part1_peep.json", "part2_peep.json"]}],
        },
    )

    validate_all.validate_registry_index(krr)

    assert validate_all.errors == []


def test_validate_registry_index_reports_missing_files(tmp_path):
    validate_all.errors.clear()
    krr = tmp_path / "krr_outputs"
    write_json(
        krr / "INDEX.json",
        {
            "laws": [{"name": "stale", "files": ["missing_peep.json"]}],
        },
    )

    validate_all.validate_registry_index(krr)

    assert any("indexed file does not exist: missing_peep.json" in err for err in validate_all.errors)


def test_validate_registry_index_allows_documented_zero_provision_exception(tmp_path):
    validate_all.errors.clear()
    krr = tmp_path / "krr_outputs"
    write_json(krr / "procedure_map_peep.json", act_doc(provisions=False))
    write_json(
        krr / "INDEX.json",
        {
            "laws": [{"name": "procedure", "files": ["procedure_map_peep.json"]}],
            "registry_exceptions": {
                "procedure_map_peep.json": {
                    "category": "procedure_map",
                    "reason": "Procedure-stage map, not provision extraction output.",
                }
            },
        },
    )

    validate_all.validate_registry_index(krr)

    assert validate_all.errors == []


def test_validate_registry_index_reports_malformed_entries(tmp_path):
    validate_all.errors.clear()
    krr = tmp_path / "krr_outputs"
    write_json(
        krr / "INDEX.json",
        {
            "laws": [{"name": "", "files": "not-a-list"}],
        },
    )

    validate_all.validate_registry_index(krr)

    assert any("name is missing or invalid" in err for err in validate_all.errors)
    assert any("files is missing or empty" in err for err in validate_all.errors)


def test_validate_affected_law_name_requires_string_array(tmp_path):
    validate_all.errors.clear()
    doc = {
        "@graph": [
            {
                "@id": "estleg:Draft_1",
                "estleg:affectedLawName": [
                    {"@value": "Riigi Teataja seaduse", "@language": "et"}
                ],
            }
        ]
    }

    validate_all.validate_affected_law_names(tmp_path / "draft.json", doc)

    assert any("affectedLawName must be an array of strings" in err for err in validate_all.errors)


def test_validate_source_provenance_requires_source_iri_object(tmp_path):
    validate_all.errors.clear()
    doc = {
        "@graph": [
            {
                "@id": "estleg:Registry",
                "dcterms:source": "https://example.test/source.csv",
            }
        ]
    }

    validate_all.validate_source_provenance(tmp_path / "registry.json", doc)

    assert any("dcterms:source must be an IRI object" in err for err in validate_all.errors)


def test_validate_internal_references_reports_missing_estleg_ref():
    validate_all.errors.clear()
    all_ids = {"estleg:Known": ["known.json"]}
    refs = [("file.json", "estleg:Known", "estleg:related", "estleg:Missing")]

    validate_all.validate_internal_references(all_ids, refs)

    assert any("internal estleg: object references do not resolve" in err for err in validate_all.errors)


def test_validate_internal_references_accepts_known_estleg_ref():
    validate_all.errors.clear()
    all_ids = {"estleg:Known": ["known.json"], "estleg:Target": ["target.json"]}
    refs = [("file.json", "estleg:Known", "estleg:related", "estleg:Target")]

    validate_all.validate_internal_references(all_ids, refs)

    assert validate_all.errors == []


def test_collect_used_vocabulary_terms_finds_predicates_and_classes():
    doc = {
        "@graph": [
            {
                "@id": "estleg:Act_1",
                "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
                "estleg:customPredicate": "value",
            }
        ]
    }

    predicates, classes = validate_all.collect_used_vocabulary_terms(doc)

    assert "estleg:customPredicate" in predicates
    assert "estleg:Act" in classes
    assert "estleg:Law" in classes


def test_validate_regulation_indexes_reports_count_drift(tmp_path):
    validate_all.errors.clear()
    krr = tmp_path / "krr_outputs"
    riik = krr / "regulations" / "riik"
    riik.mkdir(parents=True)
    write_json(riik / "reg_1_peep.json", {"@graph": []})
    write_json(riik / "REGULATIONS_RIIK_INDEX.json", {"kov": False, "totalRegulations": 0})

    validate_all.validate_regulation_indexes(krr)

    assert any("totalRegulations=0 but filesystem has 1 peep files" in err for err in validate_all.errors)


def _provision_node(nid: str, *, cluster_as_string: bool = False, summary: str | None = "topic") -> dict:
    node = {
        "@id": nid,
        "@type": ["estleg:LegalProvision"],
        "estleg:paragrahv": "§ 1.",
        "estleg:sourceAct": "Test Act",
    }
    if summary is not None:
        node["estleg:summary"] = summary
    cluster_value = (
        f"estleg:Cluster_{nid}" if cluster_as_string else {"@id": f"estleg:Cluster_{nid}"}
    )
    node["estleg:requestedCluster"] = cluster_value
    return node


def _write_combined(krr: Path, nodes: list[dict]) -> None:
    write_json(krr / "combined_ontology.jsonld", {"@graph": nodes})


def test_validate_combined_ontology_rejects_unexpected_extra_node(tmp_path):
    validate_all.errors.clear()
    krr = tmp_path / "krr_outputs"
    write_json(krr / "law_a_peep.json", {"@graph": [_provision_node("estleg:A_1")]})
    _write_combined(
        krr,
        [
            _provision_node("estleg:A_1"),
            _provision_node("estleg:Stale_Old_1"),
        ],
    )

    validate_all.validate_combined_ontology(krr)

    assert any(
        "stale extra IDs" in err for err in validate_all.errors
    ), validate_all.errors


def test_validate_combined_ontology_rejects_requested_cluster_string_drift(tmp_path):
    validate_all.errors.clear()
    krr = tmp_path / "krr_outputs"
    write_json(krr / "law_a_peep.json", {"@graph": [_provision_node("estleg:A_1")]})
    _write_combined(
        krr,
        [_provision_node("estleg:A_1", cluster_as_string=True)],
    )

    validate_all.validate_combined_ontology(krr)

    assert any(
        "drift from source on SHACL-sensitive fields" in err for err in validate_all.errors
    ), validate_all.errors


def test_validate_combined_ontology_rejects_dropped_summary(tmp_path):
    validate_all.errors.clear()
    krr = tmp_path / "krr_outputs"
    write_json(krr / "law_a_peep.json", {"@graph": [_provision_node("estleg:A_1")]})
    _write_combined(
        krr,
        [_provision_node("estleg:A_1", summary=None)],
    )

    validate_all.validate_combined_ontology(krr)

    assert any(
        "drift from source on SHACL-sensitive fields" in err for err in validate_all.errors
    ), validate_all.errors


def test_validate_combined_ontology_allows_vocabulary_only_node(tmp_path):
    validate_all.errors.clear()
    krr = tmp_path / "krr_outputs"
    write_json(krr / "law_a_peep.json", {"@graph": [_provision_node("estleg:A_1")]})
    write_json(
        krr / "controlled_vocabulary.jsonld",
        {"@graph": [{"@id": "estleg:Act", "@type": ["owl:Class"]}]},
    )
    _write_combined(
        krr,
        [
            _provision_node("estleg:A_1"),
            {"@id": "estleg:Act", "@type": ["owl:Class"]},
        ],
    )

    validate_all.validate_combined_ontology(krr)

    assert validate_all.errors == [], validate_all.errors


def test_validate_combined_ontology_passes_on_clean_corpus(tmp_path):
    validate_all.errors.clear()
    krr = tmp_path / "krr_outputs"
    write_json(krr / "law_a_peep.json", {"@graph": [_provision_node("estleg:A_1")]})
    _write_combined(krr, [_provision_node("estleg:A_1")])

    validate_all.validate_combined_ontology(krr)

    assert validate_all.errors == [], validate_all.errors


def test_validate_institution_duplicates_reports_normalized_label_collision(tmp_path):
    validate_all.errors.clear()
    inst = tmp_path / "krr_outputs" / "institutions"
    write_json(
        inst / "institution_a.json",
        {"@graph": [{"@id": "estleg:Institution_a", "@type": ["estleg:Institution"], "rdfs:label": "Politsei- ja Piirivalveamet"}]},
    )
    write_json(
        inst / "institution_b.json",
        {"@graph": [{"@id": "estleg:Institution_b", "@type": ["estleg:Institution"], "rdfs:label": "Politsei - ja piirivalveamet"}]},
    )

    validate_all.validate_institution_duplicates(tmp_path / "krr_outputs")

    assert any("duplicate normalized institution label groups" in err for err in validate_all.errors)


# ---------------------------------------------------------------------------
# Regression tests for #158 (subcorpus parity, classifier, reporter, mtime).
# ---------------------------------------------------------------------------


def test_collect_source_nodes_is_recursive_when_glob_uses_double_star(tmp_path):
    """`collect_source_nodes` now accepts a `**` glob and walks subdirs.

    Regression for #158: previously the helper only globbed
    `*_peep.json` at `krr_dir`, so peep files in `eurlex/`, `curia/`,
    `riigikohus/`, `eelnoud/`, and `regulations/` were silently ignored
    by the parity gate.
    """
    krr = tmp_path / "krr_outputs"
    write_json(krr / "root_peep.json", {"@graph": [{"@id": "estleg:Root_1"}]})
    write_json(krr / "eurlex" / "eurlex_decisions_peep.json", {"@graph": [{"@id": "estleg:EU_1"}]})
    write_json(krr / "curia" / "curia_judgments_peep.json", {"@graph": [{"@id": "estleg:CURIA_1"}]})

    nodes, _allow, files = validate_all.collect_source_nodes(
        krr, peep_glob="**/*_peep.json", allowlist_jsonld=()
    )

    assert "estleg:Root_1" in nodes
    assert "estleg:EU_1" in nodes
    assert "estleg:CURIA_1" in nodes
    assert any(p.parent.name == "eurlex" for p in files)


def test_validate_subcorpus_combined_detects_missing_peep_id(tmp_path):
    """A subcorpus peep ID missing from its combined fails the gate.

    This is the canonical sanity check requested in #158: a subdirectory
    peep file's @id must be present in the corresponding subcorpus
    combined artefact, otherwise the parity gate must error.
    """
    krr = tmp_path / "krr_outputs"
    # eurlex peep file with a real provision-like @id.
    write_json(
        krr / "eurlex" / "eurlex_directives_peep.json",
        {
            "@graph": [
                {
                    "@id": "estleg:EU_Drift_Provision",
                    "@type": ["estleg:LegalProvision"],
                    "estleg:paragrahv": "Article 1",
                }
            ]
        },
    )
    # The eurlex combined drops that ID entirely.
    write_json(
        krr / "eurlex" / "eurlex_combined.jsonld",
        {"@graph": [{"@id": "estleg:EURlex_Combined_Map_2026", "@type": ["owl:Ontology"]}]},
    )

    validate_all.validate_subcorpus_combined_ontologies(krr)

    assert any(
        "missing 1 source graph IDs" in err
        for err in validate_all.errors
    ), validate_all.errors


def test_validate_subcorpus_combined_drift_in_extended_field(tmp_path):
    """Drift in newly-tracked SHACL fields (e.g. estleg:references) fails.

    Regression for #158: `PROVISION_PARITY_FIELDS` now includes
    `estleg:references`, `dcterms:subject`, `legalText`, etc. Any
    structural difference in those between source and combined must
    trip the gate.
    """
    krr = tmp_path / "krr_outputs"
    write_json(
        krr / "eelnoud" / "eelnoud_submission_peep.json",
        {
            "@graph": [
                {
                    "@id": "estleg:Drift_Node",
                    "@type": ["estleg:LegalProvision"],
                    "estleg:references": [{"@id": "estleg:Other"}],
                    "dcterms:subject": "TopicA",
                }
            ]
        },
    )
    # Combined drops estleg:references and changes dcterms:subject.
    write_json(
        krr / "eelnoud" / "eelnoud_combined.jsonld",
        {
            "@graph": [
                {
                    "@id": "estleg:Drift_Node",
                    "@type": ["estleg:LegalProvision"],
                    "dcterms:subject": "TopicB",
                }
            ]
        },
    )

    validate_all.validate_subcorpus_combined_ontologies(krr)

    assert any("drift from source on SHACL-sensitive fields" in err for err in validate_all.errors), validate_all.errors


def test_is_aggregate_index_file_recognises_regulation_index(tmp_path):
    """Catalogue files (with totalRegulations and no @graph) classify as aggregates.

    Replaces the brittle `EXCLUDE_PREFIXES` list with content-based
    detection per #158.
    """
    krr = tmp_path / "krr_outputs"
    write_json(
        krr / "regulations" / "riik" / "REGULATIONS_RIIK_INDEX.json",
        {"kov": False, "totalRegulations": 3, "files": []},
    )
    path = krr / "regulations" / "riik" / "REGULATIONS_RIIK_INDEX.json"

    assert validate_all.is_aggregate_index_file(path)


def test_is_aggregate_index_file_skips_legitimate_peep_file(tmp_path):
    """Real peep files (with @graph) are NOT classified as aggregates."""
    krr = tmp_path / "krr_outputs"
    write_json(
        krr / "law_peep.json",
        {"@graph": [{"@id": "estleg:Act_1", "@type": ["estleg:Act"]}]},
    )

    assert not validate_all.is_aggregate_index_file(krr / "law_peep.json")


def test_is_combined_artifact_file_matches_root_and_subcorpus(tmp_path):
    """Both root combined and subcorpus *_combined.jsonld are recognised.

    Regression for #158: `combined_ontology.jsonld` does NOT match
    `*_combined.jsonld` (no underscore prefix), but must still be
    excluded from the per-file validation pipeline.
    """
    assert validate_all.is_combined_artifact_file(tmp_path / "combined_ontology.jsonld")
    assert validate_all.is_combined_artifact_file(tmp_path / "eurlex_combined.jsonld")
    assert validate_all.is_combined_artifact_file(tmp_path / "curia_combined.jsonld")
    # Negative cases:
    assert not validate_all.is_combined_artifact_file(tmp_path / "law_peep.json")
    assert not validate_all.is_combined_artifact_file(tmp_path / "controlled_vocabulary.jsonld")


def test_provision_parity_fields_includes_extended_set():
    """`PROVISION_PARITY_FIELDS` covers the SHACL-sensitive predicates.

    Locks in the extended field list mandated by #158.
    """
    extended = {
        "estleg:legalText",
        "estleg:sectionNumber",
        "estleg:hasSanction",
        "estleg:competentAuthority",
        "estleg:references",
        "estleg:interpretedBy",
        "dcterms:subject",
        "dcterms:source",
    }
    assert extended.issubset(set(validate_all.PROVISION_PARITY_FIELDS))


def test_reporter_dataclass_is_independent_of_module_state():
    """A standalone `Reporter()` does not affect the module-level lists.

    This protects callers that want to validate concurrently or in a
    nested fashion (#158).
    """
    rep = validate_all.Reporter()
    rep.error("local error")
    rep.warn("local warning")
    assert rep.errors == ["local error"]
    assert rep.warnings == ["local warning"]
    # Module-level reporter is unchanged.
    assert "local error" not in validate_all.errors
    assert "local warning" not in validate_all.warnings


def test_reset_clears_module_level_reporter():
    """`validate_all.reset()` empties the global error/warning lists.

    The autouse fixture relies on this contract; codify it explicitly.
    """
    validate_all.error("noise")
    validate_all.warn("noise warning")
    assert validate_all.errors and validate_all.warnings
    validate_all.reset()
    assert validate_all.errors == []
    assert validate_all.warnings == []


def test_validate_dc_source_accepts_array_of_strings(tmp_path):
    """`validate_dc_source` allows arrays of strings now (#159).

    `normalize_dc_source` no longer joins with `'; '`, so the validator
    must allow the multi-valued shape.
    """
    doc = {
        "@graph": [
            {
                "@id": "estleg:Act_1",
                "dc:source": ["Riigi Teataja 2023", "Riigi Teataja 2024"],
            }
        ]
    }

    validate_all.validate_dc_source(tmp_path / "ok.json", doc)

    assert validate_all.errors == []


def test_validate_dc_source_rejects_array_with_non_string():
    """`validate_dc_source` still rejects non-string array elements."""
    doc = {
        "@graph": [
            {
                "@id": "estleg:Act_1",
                "dc:source": ["valid", 42],
            }
        ]
    }

    validate_all.validate_dc_source(Path("bad.json"), doc)

    assert any("contains non-string item" in e for e in validate_all.errors)


def test_validate_dc_source_rejects_dict_value():
    """A dict value (not string, not list) is still an error."""
    doc = {
        "@graph": [
            {"@id": "estleg:Act_1", "dc:source": {"@id": "https://example.com"}}
        ]
    }

    validate_all.validate_dc_source(Path("bad.json"), doc)

    assert any("must be a string or array of strings" in e for e in validate_all.errors)


def test_validate_combined_ontology_mtime_check_uses_le(tmp_path):
    """Tied mtime + structural drift triggers the staleness error (#158).

    Previously the comparison was strict-less-than (`<`), so a source
    file regenerated in the same epoch second as the combined would
    silently pass. The check now uses `<=` and is gated on structural
    drift to avoid spurious failures from build-ordering.
    """
    import os

    krr = tmp_path / "krr_outputs"
    # Source has an extra ID that combined is missing -> structural drift.
    write_json(
        krr / "law_a_peep.json",
        {"@graph": [_provision_node("estleg:A_1"), _provision_node("estleg:A_2")]},
    )
    _write_combined(krr, [_provision_node("estleg:A_1")])

    # Force the combined and the peep to share the same mtime exactly.
    peep_path = krr / "law_a_peep.json"
    combined_path = krr / "combined_ontology.jsonld"
    common = peep_path.stat().st_mtime
    os.utime(combined_path, (common, common))

    validate_all.validate_combined_ontology(krr)

    assert any(
        "older than at least one canonical source file" in err
        for err in validate_all.errors
    ), validate_all.errors


def test_discover_validation_files_skips_combined_and_index_files(tmp_path):
    """`discover_validation_files` returns the validator-quality file set.

    Verifies #158 fix where `EXCLUDE_PREFIXES` was replaced by a
    content/path-aware classifier.
    """
    krr = tmp_path / "krr_outputs"
    # Files that MUST be returned (peeps + allowlist):
    write_json(krr / "law_a_peep.json", {"@graph": [{"@id": "estleg:A_1"}]})
    write_json(krr / "controlled_vocabulary.jsonld", {"@graph": [{"@id": "estleg:Vocab"}]})
    # Files that MUST be excluded:
    write_json(krr / "combined_ontology.jsonld", {"@graph": [{"@id": "estleg:A_1"}]})
    write_json(krr / "eurlex" / "eurlex_combined.jsonld", {"@graph": [{"@id": "estleg:EU_1"}]})
    write_json(krr / "REGULATIONS_RIIK_INDEX.json", {"kov": False, "totalRegulations": 0})
    write_json(krr / "draft_impact_report.json", {"@graph": []})

    files = {p.name for p in validate_all.discover_validation_files(krr)}

    assert "law_a_peep.json" in files
    assert "controlled_vocabulary.jsonld" in files
    assert "combined_ontology.jsonld" not in files
    assert "eurlex_combined.jsonld" not in files
    assert "REGULATIONS_RIIK_INDEX.json" not in files
    assert "draft_impact_report.json" not in files

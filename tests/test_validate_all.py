import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import estleg_common
import fix_all_issues
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


def test_validate_registry_index_allows_no_structured_body_stub(tmp_path):
    krr = tmp_path / "krr_outputs"
    doc = act_doc(provisions=False)
    doc["@graph"][0]["estleg:contentStatus"] = "noStructuredBody"
    write_json(krr / "stub_law_peep.json", doc)
    write_json(
        krr / "INDEX.json",
        {
            "total_laws": 1,
            "total_files": 1,
            "laws": [{"name": "stub_law", "files": ["stub_law_peep.json"]}],
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


def test_validate_bare_namespace_act_ids_rejects_act_node(tmp_path):
    doc = {
        "@graph": [
            {
                "@id": "https://data.riik.ee/ontology/estleg#",
                "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
            }
        ]
    }

    validate_all.validate_bare_namespace_act_ids(tmp_path / "multipart_peep.json", doc)

    assert any("act/law node uses bare estleg namespace @id" in err for err in validate_all.errors)


def test_validate_bare_namespace_act_ids_allows_vocabulary_node(tmp_path):
    doc = {
        "@graph": [
            {
                "@id": "https://data.riik.ee/ontology/estleg#",
                "@type": ["owl:Ontology"],
            }
        ]
    }

    validate_all.validate_bare_namespace_act_ids(tmp_path / "vocabulary.jsonld", doc)

    assert validate_all.errors == []


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
# Issue #118 — validate_institution_registry_consistency
# ---------------------------------------------------------------------------


def _inst_file(inst_dir: Path, slug: str, *, id_override: str | None = None) -> None:
    write_json(
        inst_dir / f"institution_{slug}.json",
        {
            "@graph": [
                {
                    "@id": id_override or f"estleg:Institution_{slug}",
                    "@type": ["owl:NamedIndividual", "estleg:Institution"],
                    "rdfs:label": slug.replace("_", " ").title(),
                    "estleg:institutionType": "agency",
                },
                {
                    "@id": (id_override or f"estleg:Institution_{slug}") + "_competence_general",
                    "@type": ["owl:NamedIndividual", "estleg:Competence"],
                    "estleg:competenceType": "general",
                },
            ]
        },
    )


def test_registry_consistency_passes_for_well_formed_registry_and_aliases(tmp_path, monkeypatch):
    krr = tmp_path / "krr_outputs"
    inst = krr / "institutions"
    _inst_file(inst, "maksu_ja_tolliamet")
    _inst_file(inst, "riigikohus")
    # Alias table whose canonical target exists.
    import json as _json
    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True)
    (repo / "data" / "institution_aliases.json").write_text(
        _json.dumps({
            "version": 1,
            "aliases": {
                "maksuamet": {"canonical": "maksu_ja_tolliamet", "evidence": "merger 2004"},
            },
        }),
        encoding="utf-8",
    )
    validate_all.validate_institution_registry_consistency(krr, repo)
    assert validate_all.errors == [], validate_all.errors


def test_registry_consistency_tolerates_double_underscore_canonical(tmp_path):
    """The on-disk registry has a few stale ``__`` filenames; an alias
    target written with single underscores must still resolve via the
    collapse-tolerant compare."""
    krr = tmp_path / "krr_outputs"
    inst = krr / "institutions"
    _inst_file(inst, "maksu__ja_tolliamet")  # double underscore on disk
    import json as _json
    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True)
    (repo / "data" / "institution_aliases.json").write_text(
        _json.dumps({"version": 1, "aliases": {"maksuamet": {"canonical": "maksu_ja_tolliamet"}}}),
        encoding="utf-8",
    )
    validate_all.validate_institution_registry_consistency(krr, repo)
    assert validate_all.errors == [], validate_all.errors


def test_registry_consistency_flags_unknown_institution_id(tmp_path):
    krr = tmp_path / "krr_outputs"
    inst = krr / "institutions"
    # File named riigikohus but its @id points at a non-canonical slug.
    _inst_file(inst, "riigikohus", id_override="estleg:Institution_riigikohus_typo")
    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True)
    validate_all.validate_institution_registry_consistency(krr, repo)
    assert any("are not canonical institution slugs" in e
               for e in validate_all.errors), validate_all.errors


def test_registry_consistency_flags_dangling_alias_target(tmp_path):
    krr = tmp_path / "krr_outputs"
    inst = krr / "institutions"
    _inst_file(inst, "riigikohus")
    import json as _json
    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True)
    (repo / "data" / "institution_aliases.json").write_text(
        _json.dumps({
            "version": 1,
            "aliases": {"some_old_amet": {"canonical": "nonexistent_amet"}},
        }),
        encoding="utf-8",
    )
    validate_all.validate_institution_registry_consistency(krr, repo)
    assert any("no institution file" in e for e in validate_all.errors), validate_all.errors


def test_load_institution_alias_canonicals_handles_missing_and_malformed(tmp_path):
    # Missing file -> empty set.
    assert validate_all.load_institution_alias_canonicals(tmp_path) == set()
    # Malformed -> empty set.
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "institution_aliases.json").write_text("not json", encoding="utf-8")
    assert validate_all.load_institution_alias_canonicals(tmp_path) == set()


def test_registry_consistency_passes_on_real_regenerated_corpus():
    """Issue #118: after re-running extract_institutional_competence.py the
    on-disk registry must satisfy validate_institution_registry_consistency
    — every Institution_* @id is a canonical slug and every alias
    `canonical` target resolves to an institution file (in particular the
    two targets, Maksu- ja Tolliamet and Põllumajandus- ja Toiduamet, that
    only ever appear via predecessor names)."""
    validate_all.validate_institution_registry_consistency(
        validate_all.KRR_DIR, validate_all.REPO_ROOT
    )
    assert validate_all.errors == [], validate_all.errors


def test_real_institution_slugs_have_no_double_underscore():
    """The pre-#118 corpus shipped stale ``institution_x__y.json`` names;
    the re-run renames them to the single-underscore convention the
    extractor emits, so none should remain on disk."""
    inst_dir = validate_all.KRR_DIR / "institutions"
    offenders = [p.name for p in inst_dir.glob("institution_*__*.json")]
    assert offenders == [], offenders


def test_applies_to_provision_count_in_controlled_vocabulary():
    """Issue #118 / #170: the extractor writes estleg:appliesToProvisionCount
    onto Competence nodes, so it must be a defined reusable term or the
    vocabulary-coverage gate fails on re-run."""
    defined = validate_all.collect_defined_vocabulary_terms()
    assert "estleg:appliesToProvisionCount" in defined


@pytest.mark.parametrize(
    "term",
    [
        "estleg:LegalProvision",
        "estleg:LegalConcept",
        "estleg:Part",
        "estleg:TopicCluster",
        "estleg:chapterNumber",
        "estleg:contentStatus",
        "estleg:contentStatusReason",
        "estleg:hasPart",
        "estleg:isPartOf",
        "estleg:kehtiv",
        "estleg:provisionCount",
    ],
)
def test_generated_law_terms_are_declared_in_controlled_vocabulary(term):
    defined = validate_all.collect_defined_vocabulary_terms()
    assert term in defined


# ---------------------------------------------------------------------------
# Issue #119 — validate_act_coverage_reconciliation
# ---------------------------------------------------------------------------


def _laws_manifest(krr: Path, entries: list[dict]) -> None:
    import json as _json
    krr.mkdir(parents=True, exist_ok=True)
    (krr / "generation_manifest_laws.json").write_text(
        _json.dumps({
            "generated": "2026-05-11T00:00:00+00:00",
            "mode": "missing-only",
            "counts": {"sourceActs": len(entries)},
            "outputsAll": entries,
        }),
        encoding="utf-8",
    )


def _full_peep(krr: Path, slug: str) -> None:
    write_json(
        krr / f"{slug}_peep.json",
        {
            "@graph": [
                {"@id": f"estleg:X_{slug}_Map_2026", "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
                 "dc:source": slug, "estleg:contentStatus": "structuredBody"},
                {"@id": f"estleg:X_{slug}_Par_1", "@type": ["owl:NamedIndividual"], "estleg:paragrahv": "§ 1."},
            ]
        },
    )


def _stub_peep(krr: Path, slug: str, *, content_status: str | None = "noStructuredBody") -> None:
    ont: dict = {"@id": f"estleg:X_{slug}_Map_2026", "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
                 "dc:source": slug}
    if content_status is not None:
        ont["estleg:contentStatus"] = content_status
    write_json(krr / f"{slug}_peep.json", {"@graph": [ont]})


def test_act_coverage_reconciliation_missing_manifest_is_warning(tmp_path):
    krr = tmp_path / "krr_outputs"
    krr.mkdir()
    validate_all.validate_act_coverage_reconciliation(krr)
    assert validate_all.errors == []
    assert any("not found" in w for w in validate_all.warnings)


def test_act_coverage_reconciliation_clean(tmp_path):
    krr = tmp_path / "krr_outputs"
    _full_peep(krr, "law_a")
    _stub_peep(krr, "law_b")
    _laws_manifest(krr, [
        {"title": "Law A", "slug": "law_a", "status": "full"},
        {"title": "Law B", "slug": "law_b", "status": "stub", "reason": "no paragraphs"},
        {"title": "Law C", "slug": "law_c", "status": "failed", "reason": "fetch failed"},
    ])
    validate_all.validate_act_coverage_reconciliation(krr)
    assert validate_all.errors == [], validate_all.errors


def test_act_coverage_reconciliation_flags_manifest_act_without_file(tmp_path):
    krr = tmp_path / "krr_outputs"
    _full_peep(krr, "law_a")
    # 'law_b' is in the manifest with status full but has no peep file.
    _laws_manifest(krr, [
        {"title": "Law A", "slug": "law_a", "status": "full"},
        {"title": "Law B", "slug": "law_b", "status": "full"},
    ])
    validate_all.validate_act_coverage_reconciliation(krr)
    assert any("no peep file on disk" in e for e in validate_all.errors), validate_all.errors


def test_act_coverage_reconciliation_flags_orphan_peep(tmp_path):
    krr = tmp_path / "krr_outputs"
    _full_peep(krr, "law_a")
    _full_peep(krr, "orphan_law")  # on disk, not in manifest
    _laws_manifest(krr, [{"title": "Law A", "slug": "law_a", "status": "full"}])
    validate_all.validate_act_coverage_reconciliation(krr)
    assert any("not listed in" in e for e in validate_all.errors), validate_all.errors


def test_act_coverage_reconciliation_accepts_reported_source_removed_peep(tmp_path):
    krr = tmp_path / "krr_outputs"
    _full_peep(krr, "law_a")
    _full_peep(krr, "old_law")  # on disk, explicitly reported as removed from source snapshot.
    import json as _json

    (krr / "generation_manifest_laws.json").write_text(
        _json.dumps({
            "generated": "2026-05-11T00:00:00+00:00",
            "mode": "missing-only",
            "counts": {"sourceActs": 1},
            "run": {"sourceRemovedFromSnapshot": ["old_law_peep.json"]},
            "outputsAll": [{"title": "Law A", "slug": "law_a", "status": "full"}],
        }),
        encoding="utf-8",
    )

    validate_all.validate_act_coverage_reconciliation(krr)
    assert validate_all.errors == [], validate_all.errors


@pytest.mark.parametrize("run_block", [None, [], 42, "not a dict"])
def test_act_coverage_reconciliation_warns_on_malformed_run_block(
    tmp_path, run_block
):
    krr = tmp_path / "krr_outputs"
    _full_peep(krr, "law_a")
    import json as _json

    (krr / "generation_manifest_laws.json").write_text(
        _json.dumps({
            "generated": "2026-05-11T00:00:00+00:00",
            "mode": "missing-only",
            "counts": {"sourceActs": 1},
            "run": run_block,
            "outputsAll": [{"title": "Law A", "slug": "law_a", "status": "full"}],
        }),
        encoding="utf-8",
    )

    validate_all.validate_act_coverage_reconciliation(krr)
    assert validate_all.errors == [], validate_all.errors
    assert any("malformed run block" in w for w in validate_all.warnings)


@pytest.mark.parametrize(
    "run_block, expected_warning",
    [
        (
            {"sourceRemovedFromSnapshot": "not-a-list"},
            "malformed run.sourceRemovedFromSnapshot",
        ),
        (
            {"sourceRemovedFromSnapshot": [42, None]},
            "ignoring non-string run.sourceRemovedFromSnapshot entries",
        ),
    ],
)
def test_act_coverage_reconciliation_warns_on_malformed_source_removed(
    tmp_path, run_block, expected_warning
):
    krr = tmp_path / "krr_outputs"
    _full_peep(krr, "law_a")
    import json as _json

    (krr / "generation_manifest_laws.json").write_text(
        _json.dumps({
            "generated": "2026-05-11T00:00:00+00:00",
            "mode": "missing-only",
            "counts": {"sourceActs": 1},
            "run": run_block,
            "outputsAll": [{"title": "Law A", "slug": "law_a", "status": "full"}],
        }),
        encoding="utf-8",
    )

    validate_all.validate_act_coverage_reconciliation(krr)
    assert validate_all.errors == [], validate_all.errors
    assert any(expected_warning in w for w in validate_all.warnings)


def test_act_coverage_reconciliation_flags_stub_without_marker(tmp_path):
    krr = tmp_path / "krr_outputs"
    # Manifest says law_b is a stub, but its peep file lacks the
    # contentStatus marker.
    _stub_peep(krr, "law_b", content_status=None)
    _laws_manifest(krr, [{"title": "Law B", "slug": "law_b", "status": "stub"}])
    validate_all.validate_act_coverage_reconciliation(krr)
    assert any("noStructuredBody" in e for e in validate_all.errors), validate_all.errors


def test_act_coverage_reconciliation_accepts_multipart_osa_files(tmp_path):
    krr = tmp_path / "krr_outputs"
    # Multipart law: two osa peep files, one manifest entry.
    write_json(krr / "big_law_osa1_peep.json", {"@graph": [
        {"@id": "estleg:BIG_Osa1_Map_2026", "@type": ["owl:Ontology", "estleg:Act", "estleg:Law", "estleg:Part"],
         "dc:source": "big_law", "estleg:contentStatus": "structuredBody"},
    ]})
    write_json(krr / "big_law_osa2_peep.json", {"@graph": [
        {"@id": "estleg:BIG_Osa2_Map_2026", "@type": ["owl:Ontology", "estleg:Act", "estleg:Law", "estleg:Part"],
         "dc:source": "big_law", "estleg:contentStatus": "structuredBody"},
    ]})
    _laws_manifest(krr, [{"title": "Big Law", "slug": "big_law", "status": "full"}])
    validate_all.validate_act_coverage_reconciliation(krr)
    assert validate_all.errors == [], validate_all.errors


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


# ---------------------------------------------------------------------------
# Regression tests for #128 — act-level temporal property placement gate.
# ---------------------------------------------------------------------------


def test_validate_temporal_property_targets_rejects_concept_node(tmp_path):
    """`estleg:temporalStatus` on a non-Act node is a hard error (#128).

    Reproduces the historical `graph[0]` fallback in
    `extract_temporal_data.py` that stamped temporal props onto an
    `estleg:LegalConcept`.
    """
    doc = {
        "@graph": [
            {
                "@id": "estleg:Concept_time_limits",
                "@type": ["owl:NamedIndividual", "estleg:LegalConcept"],
                "estleg:temporalStatus": "inForce",
            }
        ]
    }
    p = tmp_path / "tsiviilseadustik_osa6_peep.json"
    write_json(p, doc)

    validate_all.validate_temporal_property_targets([p])

    assert any(
        "act-level temporal properties on non-Act nodes" in e
        for e in validate_all.errors
    ), validate_all.errors


def test_validate_temporal_property_targets_accepts_act_node(tmp_path):
    """Temporal props on an `estleg:Act` node are fine."""
    doc = {
        "@graph": [
            {
                "@id": "estleg:AlkS_Map_2026",
                "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
                "estleg:temporalStatus": "inForce",
                "estleg:entryIntoForce": {"@value": "2003-07-19", "@type": "xsd:date"},
                "estleg:lastAmendmentDate": {"@value": "2024-01-01", "@type": "xsd:date"},
            }
        ]
    }
    p = tmp_path / "alkoholiseadus_peep.json"
    write_json(p, doc)

    validate_all.validate_temporal_property_targets([p])

    assert validate_all.errors == [], validate_all.errors


def test_validate_temporal_property_targets_allows_amendment_event_entry_into_force(tmp_path):
    """Reified `estleg:AmendmentEvent` nodes legitimately carry
    `estleg:entryIntoForce` (the date that amendment took effect)."""
    doc = {
        "@graph": [
            {
                "@id": "estleg:Amendment_demo_1",
                "@type": ["owl:NamedIndividual", "estleg:AmendmentEvent"],
                "estleg:entryIntoForce": {"@value": "2020-01-01", "@type": "xsd:date"},
            }
        ]
    }
    p = tmp_path / "amendments_demo.json"
    write_json(p, doc)

    validate_all.validate_temporal_property_targets([p])

    assert validate_all.errors == [], validate_all.errors


def test_validate_temporal_property_targets_flags_repeal_date_on_amendment_event(tmp_path):
    """The AmendmentEvent exception is scoped to `entryIntoForce` only —
    `estleg:repealDate` on an AmendmentEvent is still flagged."""
    doc = {
        "@graph": [
            {
                "@id": "estleg:Amendment_demo_1",
                "@type": ["owl:NamedIndividual", "estleg:AmendmentEvent"],
                "estleg:repealDate": {"@value": "2020-01-01", "@type": "xsd:date"},
            }
        ]
    }
    p = tmp_path / "amendments_demo.json"
    write_json(p, doc)

    validate_all.validate_temporal_property_targets([p])

    assert any(
        "act-level temporal properties on non-Act nodes" in e
        for e in validate_all.errors
    ), validate_all.errors


# ---------------------------------------------------------------------------
# Regression tests for #129 — transposition mapping gate.
# ---------------------------------------------------------------------------


def test_validate_transposition_mapping_rejects_legacy_shape(tmp_path):
    """The legacy `{matched, unmatched, mappings}` report shape fails (#129)."""
    krr = tmp_path / "krr_outputs"
    write_json(
        krr / "transposition_mapping.json",
        {
            "generated": "2026-03-21",
            "source": "https://publications.europa.eu/webapi/rdf/sparql",
            "total_measures_fetched": 0,
            "matched": 0,
            "unmatched": 0,
            "mappings": [],
        },
    )

    validate_all.validate_transposition_mapping(krr)

    assert any(
        "stale/legacy report shape" in e for e in validate_all.errors
    ), validate_all.errors


def test_validate_transposition_mapping_rejects_empty_unflagged(tmp_path):
    """Current shape but empty `mappings` and no `documented_empty` -> error."""
    krr = tmp_path / "krr_outputs"
    write_json(
        krr / "transposition_mapping.json",
        {
            "generated": "2026-05-11",
            "source": "https://publications.europa.eu/webapi/rdf/sparql",
            "country": "EST",
            "total_measures_fetched": 0,
            "total_matched": 0,
            "total_unmatched": 0,
            "unique_directives": 0,
            "unique_laws": 0,
            "mappings": [],
        },
    )

    validate_all.validate_transposition_mapping(krr)

    assert any(
        "advertised but unpopulated" in e for e in validate_all.errors
    ), validate_all.errors


def test_validate_transposition_mapping_accepts_documented_empty(tmp_path):
    """An explicitly flagged empty snapshot passes."""
    krr = tmp_path / "krr_outputs"
    write_json(
        krr / "transposition_mapping.json",
        {
            "generated": "2026-05-11",
            "source": "https://publications.europa.eu/webapi/rdf/sparql",
            "country": "EST",
            "documented_empty": True,
            "total_measures_fetched": 0,
            "total_matched": 0,
            "total_unmatched": 0,
            "unique_directives": 0,
            "unique_laws": 0,
            "mappings": [],
        },
    )

    validate_all.validate_transposition_mapping(krr)

    assert validate_all.errors == [], validate_all.errors


def test_validate_transposition_mapping_accepts_populated(tmp_path):
    """A non-empty `mappings` array in the current shape passes."""
    krr = tmp_path / "krr_outputs"
    write_json(
        krr / "transposition_mapping.json",
        {
            "generated": "2026-05-11",
            "source": "https://publications.europa.eu/webapi/rdf/sparql",
            "country": "EST",
            "total_measures_fetched": 5,
            "total_matched": 2,
            "total_unmatched": 3,
            "unique_directives": 2,
            "unique_laws": 2,
            "mappings": [
                {"directive_celex": "32000L0001", "matched_law_name": "alkoholiseadus"},
                {"directive_celex": "32000L0002", "matched_law_name": "tubakaseadus"},
            ],
        },
    )

    validate_all.validate_transposition_mapping(krr)

    assert validate_all.errors == [], validate_all.errors


def test_validate_transposition_mapping_flags_stale_count(tmp_path):
    """#631 review: total_matched must equal len(mappings) — a manual edit that
    forgets to recompute the header otherwise ships silently."""
    krr = tmp_path / "krr_outputs"
    write_json(
        krr / "transposition_mapping.json",
        {
            "generated": "2026-05-11", "source": "x", "country": "EST",
            "total_measures_fetched": 5, "total_matched": 5, "total_unmatched": 0,
            "unique_directives": 1, "unique_laws": 1,
            "mappings": [
                {"directive_celex": "32000L0001", "matched_law_name": "alkoholiseadus"},
            ],
        },
    )
    validate_all.validate_transposition_mapping(krr)
    assert any("total_matched" in e for e in validate_all.errors), validate_all.errors


def test_validate_transposition_mapping_flags_peep_named_law(tmp_path):
    """#631 review: matched_law_name must be the INDEX name, not a peep filename
    stem (a retarget that used Path(file).stem leaves a trailing '_peep')."""
    krr = tmp_path / "krr_outputs"
    write_json(
        krr / "transposition_mapping.json",
        {
            "generated": "2026-05-11", "source": "x", "country": "EST",
            "total_measures_fetched": 1, "total_matched": 1, "total_unmatched": 0,
            "unique_directives": 1, "unique_laws": 1,
            "mappings": [
                {"directive_celex": "31996L0034", "matched_law_name": "toolepingu_seadus_peep"},
            ],
        },
    )
    validate_all.validate_transposition_mapping(krr)
    assert any("_peep" in e for e in validate_all.errors), validate_all.errors


def test_validate_transposition_mapping_missing_file_is_only_a_warning(tmp_path):
    """If `transposition_mapping.json` does not exist, warn (not error)."""
    krr = tmp_path / "krr_outputs"
    krr.mkdir()

    validate_all.validate_transposition_mapping(krr)

    assert validate_all.errors == []
    assert any("transposition_mapping.json: not found" in w for w in validate_all.warnings)


# ---------------------------------------------------------------------------
# Regression tests for #110 — catalog metadata count cross-checks.
# ---------------------------------------------------------------------------


def test_jsonld_file_count_excludes_operational_state_files(tmp_path):
    krr = tmp_path / "krr_outputs"
    files = [
        krr / "law_a_peep.json",
        krr / "combined_ontology.jsonld",
        krr / "sidecars" / "annotations.jsonld",
        krr / ".regen_state.json",
        krr / "generation_manifest_laws.json",
        krr / "latest_pipeline_manifest.json",
    ]
    for path in files:
        write_json(path, {"@graph": []})

    expected = sum(
        1
        for path in files
        if path.suffix in {".json", ".jsonld"}
        and path.name not in validate_all.OPERATIONAL_STATE_FILES
    )

    assert validate_all.jsonld_file_count(krr) == expected


def test_operational_state_files_single_source():
    """`OPERATIONAL_STATE_FILES` has exactly one definition (#240).

    Both `validate_all` and `fix_all_issues` must re-export the
    `estleg_common` frozenset by identity. If anyone reintroduces a
    literal copy in either module this identity check fails — which is
    the regression guard against the PR #232 count drift.
    """
    assert (
        validate_all.OPERATIONAL_STATE_FILES is estleg_common.OPERATIONAL_STATE_FILES
    )
    assert (
        fix_all_issues.OPERATIONAL_STATE_FILES is estleg_common.OPERATIONAL_STATE_FILES
    )


def test_file_counters_share_operational_exclusion(tmp_path):
    """`jsonld_file_count` and `discover_validation_files` exclude the same
    operational-state files, so adding one drops both counts equally (#240).
    """
    krr = tmp_path / "krr_outputs"
    # A baseline corpus of plain data files that both counters keep.
    write_json(krr / "law_a_peep.json", {"@graph": [{"@id": "estleg:A_1"}]})
    write_json(krr / "law_b_peep.json", {"@graph": [{"@id": "estleg:B_1"}]})
    write_json(krr / "sidecars" / "annotations.jsonld", {"@graph": []})

    count_before = validate_all.jsonld_file_count(krr)
    discover_before = len(validate_all.discover_validation_files(krr))

    # Add one operational-named state file — both counters must ignore it.
    write_json(krr / "generation_manifest_laws.json", {"outputsAll": []})

    count_after = validate_all.jsonld_file_count(krr)
    discover_after = len(validate_all.discover_validation_files(krr))

    assert count_after == count_before
    assert discover_after == discover_before


def test_nested_state_manifest_excluded(tmp_path):
    """A generated state manifest under `reports/integration/` is excluded
    even when its basename is not in `OPERATIONAL_STATE_FILES` (#240).

    The conservative directory pattern-guard stops a *new* manifest
    dropped into that operational directory from silently inflating the
    corpus count.
    """
    krr = tmp_path / "krr_outputs"
    write_json(krr / "law_a_peep.json", {"@graph": [{"@id": "estleg:A_1"}]})

    nested = krr / "reports" / "integration" / "phase3_manifest.json"
    write_json(nested, {"phases": []})

    # The classifier and the shared enumerator both exclude it.
    assert estleg_common.is_operational_state_file(nested)
    enumerated = set(estleg_common.iter_krr_jsonld_files(krr))
    assert nested not in enumerated

    # And the count reflects the exclusion (only the one law peep remains).
    assert validate_all.jsonld_file_count(krr) == 1


def _minimal_corpus_for_metadata(krr: Path) -> None:
    """Stage a tiny corpus so `metadata_stats()` returns deterministic counts.

    `metadata_stats()` reads a fixed set of combined files by path, so
    those must exist (even if empty); all other counts come out as zero.
    """
    write_json(krr / "law_a_peep.json", {"@graph": [{"@id": "estleg:A"}]})
    write_json(krr / "INDEX.json", {"laws": [{"name": "law_a", "files": ["law_a_peep.json"]}]})
    write_json(krr / "eelnoud" / "eelnoud_combined.jsonld", {"@graph": []})
    write_json(krr / "eurlex" / "eurlex_combined.jsonld", {"@graph": []})
    write_json(krr / "curia" / "curia_combined.jsonld", {"@graph": []})


def test_validate_metadata_catalog_flags_distribution_count_drift(tmp_path, monkeypatch):
    """A stale `estleg:*Count` key in `dcat:distribution` fails (#110).

    Previously only the `estleg:statistics` block was cross-checked; the
    parallel per-distribution count keys could drift unnoticed.
    """
    krr = tmp_path / "krr_outputs"
    _minimal_corpus_for_metadata(krr)
    # Build a metadata.jsonld whose statistics block is correct but whose
    # distribution count is wrong.
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import validate_all as va

    actual = va.metadata_stats(krr)
    metadata = {
        "estleg:statistics": actual,
        "dcat:distribution": [
            {
                "dcterms:title": "JSON-LD ontology files (complete dataset)",
                "estleg:fileCount": actual["estleg:totalFiles"] + 999,  # wrong
            }
        ],
    }
    md_path = tmp_path / "metadata.jsonld"
    write_json(md_path, metadata)
    monkeypatch.setattr(va, "REPO_ROOT", tmp_path)

    va.validate_metadata_catalog(krr)

    assert any(
        "dcat:distribution" in e and "estleg:fileCount" in e for e in va.errors
    ), va.errors


def test_validate_metadata_catalog_passes_when_distribution_counts_agree(tmp_path, monkeypatch):
    """Correct `estleg:statistics` AND `dcat:distribution` counts -> no error."""
    krr = tmp_path / "krr_outputs"
    _minimal_corpus_for_metadata(krr)
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import validate_all as va

    actual = va.metadata_stats(krr)
    metadata = {
        "estleg:statistics": actual,
        "dcat:distribution": [
            {
                "dcterms:title": "JSON-LD ontology files (complete dataset)",
                "estleg:fileCount": actual["estleg:totalFiles"],
            },
            {
                "dcterms:title": "Combined enacted laws ontology",
                "estleg:lawIndexRecordCount": actual["estleg:enactedLawCount"],
                "estleg:lawFileCount": actual["estleg:enactedLawFileCount"],
            },
        ],
    }
    md_path = tmp_path / "metadata.jsonld"
    write_json(md_path, metadata)
    monkeypatch.setattr(va, "REPO_ROOT", tmp_path)

    va.validate_metadata_catalog(krr)

    assert va.errors == [], va.errors


def test_distribution_count_keys_cover_riigikohus_and_kov():
    """Riigikohus + KOV distributions are cross-checked (#400).

    `metadata.jsonld` carries structured counts on the
    "Combined Supreme Court decisions ontology (Riigikohus)" and
    "Municipal regulations ontology (KOV)" distributions. Without a
    `DISTRIBUTION_COUNT_KEYS` mapping their `estleg:*Count` keys drift
    unnoticed while the catalog validator stays green. Pin that both
    titles are covered and map to real `metadata_stats()` keys.
    """
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import validate_all as va

    stats_keys = set(va.metadata_stats(va.KRR_DIR).keys())
    expected = {
        "Combined Supreme Court decisions ontology (Riigikohus)": {
            "estleg:courtDecisionCount": "estleg:courtDecisionCount",
        },
        "Municipal regulations ontology (KOV)": {
            "estleg:municipalRegulationCount": "estleg:municipalRegulationCount",
        },
    }
    for title, mapping in expected.items():
        assert title in va.DISTRIBUTION_COUNT_KEYS, title
        assert va.DISTRIBUTION_COUNT_KEYS[title] == mapping
        for stats_key in mapping.values():
            assert stats_key in stats_keys, stats_key


def test_readme_counts_match_metadata():
    """README's status line agrees with metadata.jsonld's estleg:statistics (#110).

    The README hard-codes corpus counts in its status header; this test
    pins them to the single source of truth so the two cannot drift
    independently again.
    """
    import json
    import re

    repo_root = Path(__file__).resolve().parent.parent
    metadata = json.loads((repo_root / "metadata.jsonld").read_text(encoding="utf-8"))
    stats = metadata["estleg:statistics"]
    readme = (repo_root / "README.md").read_text(encoding="utf-8")

    # Find the status header line.
    status_line = next(
        (ln for ln in readme.splitlines() if ln.startswith("**Status:")), None
    )
    assert status_line is not None, "README is missing its **Status:** header"

    def _ints(text: str) -> list[int]:
        return [int(m.replace(",", "")) for m in re.findall(r"\d[\d,]*", text)]

    nums = set(_ints(status_line))
    for key in (
        "estleg:enactedLawCount",
        "estleg:enactedLawFileCount",
        "estleg:domesticRegulationCount",
        "estleg:municipalRegulationCount",
        "estleg:draftLegislationCount",
        "estleg:courtDecisionCount",
        "estleg:euLegislationCount",
        "estleg:euCourtDecisionCount",
        "estleg:totalFiles",
    ):
        assert stats[key] in nums, (
            f"README status line is missing/stale for {key}={stats[key]}; "
            f"line numbers seen: {sorted(nums)}"
        )


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
    write_json(krr / "annotations_pdf_probe.json", {"recommendation": "pdf_text_layer_ok"})
    write_json(krr / ".regen_state.json", {"completed": {}})
    write_json(krr / "generation_manifest_laws.json", {"outputsAll": []})
    write_json(krr / "reports" / "integration" / "latest_pipeline_manifest.json", {"phases": []})

    files = {p.name for p in validate_all.discover_validation_files(krr)}

    assert "law_a_peep.json" in files
    assert "controlled_vocabulary.jsonld" in files
    assert "combined_ontology.jsonld" not in files
    assert "eurlex_combined.jsonld" not in files
    assert "REGULATIONS_RIIK_INDEX.json" not in files
    assert "draft_impact_report.json" not in files
    assert "annotations_pdf_probe.json" not in files
    assert ".regen_state.json" not in files
    assert "generation_manifest_laws.json" not in files
    assert "latest_pipeline_manifest.json" not in files


# ---------------------------------------------------------------------------
# validate_context — namespace check across @context shapes (issue #286)
# ---------------------------------------------------------------------------

_GOOD_NS = validate_all.EXPECTED_NS
_BAD_NS = "https://WRONG.example/ontology/estleg#"


def test_validate_context_dict_form_correct_passes(tmp_path):
    doc = {"@context": {"estleg": _GOOD_NS}, "@graph": []}

    validate_all.validate_context(tmp_path / "good.jsonld", doc)

    assert validate_all.errors == []
    assert validate_all.warnings == []


def test_validate_context_dict_form_wrong_namespace_errors(tmp_path):
    doc = {"@context": {"estleg": _BAD_NS}, "@graph": []}

    validate_all.validate_context(tmp_path / "bad.jsonld", doc)

    assert len(validate_all.errors) == 1
    assert _BAD_NS in validate_all.errors[0]


def test_validate_context_dict_form_expanded_term_definition(tmp_path):
    """``estleg`` declared via an expanded ``{"@id": …}`` term definition."""
    good = {"@context": {"estleg": {"@id": _GOOD_NS}}, "@graph": []}
    validate_all.validate_context(tmp_path / "expanded_good.jsonld", good)
    assert validate_all.errors == []

    validate_all.reset()
    bad = {"@context": {"estleg": {"@id": _BAD_NS}}, "@graph": []}
    validate_all.validate_context(tmp_path / "expanded_bad.jsonld", bad)
    assert len(validate_all.errors) == 1
    assert _BAD_NS in validate_all.errors[0]


def test_validate_context_list_form_wrong_namespace_is_caught(tmp_path):
    """Regression for issue #286: list-form context must not bypass the check."""
    doc = {
        "@context": ["https://example.com/remote/context.jsonld", {"estleg": _BAD_NS}],
        "@graph": [],
    }

    validate_all.validate_context(tmp_path / "list_bad.jsonld", doc)

    assert any(_BAD_NS in e for e in validate_all.errors), validate_all.errors


def test_validate_context_list_form_correct_namespace_passes(tmp_path):
    doc = {
        "@context": [{"owl": "http://www.w3.org/2002/07/owl#"}, {"estleg": _GOOD_NS}],
        "@graph": [],
    }

    validate_all.validate_context(tmp_path / "list_good.jsonld", doc)

    assert validate_all.errors == []


def test_validate_context_list_form_remote_member_warns(tmp_path):
    """A remote-IRI member of a list context is surfaced as a warning."""
    doc = {
        "@context": ["https://example.com/remote/context.jsonld", {"estleg": _GOOD_NS}],
        "@graph": [],
    }

    validate_all.validate_context(tmp_path / "list_remote.jsonld", doc)

    assert validate_all.errors == []
    assert any("remote @context" in w for w in validate_all.warnings), validate_all.warnings


def test_validate_context_string_form_warns_not_silent(tmp_path):
    """A bare remote-string context cannot be inspected — warn, never pass silently."""
    doc = {"@context": "https://example.com/remote/context.jsonld", "@graph": []}

    validate_all.validate_context(tmp_path / "string_ctx.jsonld", doc)

    assert validate_all.errors == []
    assert len(validate_all.warnings) == 1
    assert "remote @context" in validate_all.warnings[0]


# ---------------------------------------------------------------------------
# Issue #314 — per-entry file existence for regulation indexes.
# ---------------------------------------------------------------------------


def _reg_index_corpus(tmp_path: Path, *, files: list[str], on_disk: list[str]) -> Path:
    """Stage a riik regulation subcorpus.

    `on_disk` peep files are written to the riik directory; the index
    advertises `files` (paths relative to that directory) with a matching
    `totalRegulations` count.
    """
    krr = tmp_path / "krr_outputs"
    riik = krr / "regulations" / "riik"
    riik.mkdir(parents=True)
    for name in on_disk:
        write_json(riik / name, {"@graph": []})
    write_json(
        riik / "REGULATIONS_RIIK_INDEX.json",
        {"kov": False, "totalRegulations": len(on_disk), "files": files},
    )
    return krr


def test_validate_regulation_indexes_accepts_existing_files(tmp_path):
    """Every listed regulation file exists -> no error (#314)."""
    krr = _reg_index_corpus(
        tmp_path,
        files=["reg_a_peep.json", "reg_b_peep.json"],
        on_disk=["reg_a_peep.json", "reg_b_peep.json"],
    )

    validate_all.validate_regulation_indexes(krr)

    assert validate_all.errors == [], validate_all.errors


def test_validate_regulation_indexes_reports_missing_listed_file(tmp_path):
    """A renamed file (count intact, path stale) is caught per-entry (#314).

    The index lists `reg_b_peep.json` but the file on disk is
    `reg_renamed_peep.json`; the count still matches (2 == 2) so only the
    new per-entry existence check can detect the drift.
    """
    krr = _reg_index_corpus(
        tmp_path,
        files=["reg_a_peep.json", "reg_b_peep.json"],
        on_disk=["reg_a_peep.json", "reg_renamed_peep.json"],
    )

    validate_all.validate_regulation_indexes(krr)

    assert any(
        "indexed file does not exist: reg_b_peep.json" in e
        for e in validate_all.errors
    ), validate_all.errors


def test_validate_regulation_indexes_rejects_absolute_listed_path(tmp_path):
    """An absolute / traversal path in the regulation index is rejected (#314)."""
    krr = _reg_index_corpus(
        tmp_path,
        files=["/etc/passwd"],
        on_disk=["reg_a_peep.json"],
    )

    validate_all.validate_regulation_indexes(krr)

    assert any(
        "is not directory-relative: /etc/passwd" in e for e in validate_all.errors
    ), validate_all.errors


def test_validate_regulation_indexes_resolves_kov_nested_paths(tmp_path):
    """KOV index entries are nested under municipality dirs; existence still
    resolves relative to the index directory (#314)."""
    krr = tmp_path / "krr_outputs"
    kov = krr / "regulations" / "kov"
    nested = kov / "abja_vallavolikogu"
    nested.mkdir(parents=True)
    write_json(nested / "reg_1_peep.json", {"@graph": []})
    write_json(
        kov / "REGULATIONS_KOV_INDEX.json",
        {
            "kov": True,
            "totalRegulations": 1,
            "files": ["abja_vallavolikogu/reg_1_peep.json"],
        },
    )

    validate_all.validate_regulation_indexes(krr)

    assert validate_all.errors == [], validate_all.errors


# ---------------------------------------------------------------------------
# Issue #315 — missing INDEX.json / metadata.jsonld is a hard error.
# ---------------------------------------------------------------------------


def test_validate_registry_index_missing_is_error_by_default(tmp_path):
    """A missing INDEX.json fails the gate (no warn-only false green) (#315)."""
    krr = tmp_path / "krr_outputs"
    krr.mkdir()

    validate_all.validate_registry_index(krr)

    assert any("registry not found" in e for e in validate_all.errors), validate_all.errors


def test_validate_registry_index_missing_allowed_with_flag(tmp_path):
    """`allow_missing_index=True` downgrades a missing INDEX.json to a warning (#315)."""
    krr = tmp_path / "krr_outputs"
    krr.mkdir()

    validate_all.validate_registry_index(krr, allow_missing_index=True)

    assert validate_all.errors == [], validate_all.errors
    assert any("registry not found" in w for w in validate_all.warnings), validate_all.warnings


def test_validate_metadata_catalog_missing_is_error_by_default(tmp_path, monkeypatch):
    """A missing metadata.jsonld fails the gate (#315)."""
    krr = tmp_path / "krr_outputs"
    krr.mkdir()
    # Point REPO_ROOT at an empty dir so metadata.jsonld is absent.
    monkeypatch.setattr(validate_all, "REPO_ROOT", tmp_path)

    validate_all.validate_metadata_catalog(krr)

    assert any("metadata.jsonld: not found" in e for e in validate_all.errors), validate_all.errors


def test_validate_metadata_catalog_missing_allowed_with_flag(tmp_path, monkeypatch):
    """`allow_missing_index=True` downgrades a missing metadata.jsonld to a warning (#315)."""
    krr = tmp_path / "krr_outputs"
    krr.mkdir()
    monkeypatch.setattr(validate_all, "REPO_ROOT", tmp_path)

    validate_all.validate_metadata_catalog(krr, allow_missing_index=True)

    assert validate_all.errors == [], validate_all.errors
    assert any("metadata.jsonld: not found" in w for w in validate_all.warnings), validate_all.warnings


# ---------------------------------------------------------------------------
# Issue #313 — subcorpus INDEX count fields cross-checked vs node counts.
# ---------------------------------------------------------------------------


def _subcorpus_count_corpus(
    tmp_path: Path,
    *,
    riigikohus_total: int = 2,
    eelnoud_total: int = 1,
    eurlex_total: int = 1,
    curia_total: int = 1,
) -> Path:
    """Stage subcorpora whose node counts are fixed and whose INDEX totals
    are supplied by the caller (so a test can introduce a single drift).

    Node counts as wired in `metadata_stats()`:
      * 2x estleg:CourtDecision  across riigikohus_*_peep.json
      * 1x estleg:DraftLegislation in eelnoud/eelnoud_combined.jsonld
      * 1x estleg:EULegislation    in eurlex/eurlex_combined.jsonld
      * 1x estleg:EUCourtDecision  in curia/curia_combined.jsonld
    """
    krr = tmp_path / "krr_outputs"

    # riigikohus: two CourtDecision nodes across two peep files.
    write_json(
        krr / "riigikohus" / "riigikohus_2020_peep.json",
        {"@graph": [{"@id": "estleg:RK_1", "@type": ["estleg:CourtDecision"]}]},
    )
    write_json(
        krr / "riigikohus" / "riigikohus_2021_peep.json",
        {"@graph": [{"@id": "estleg:RK_2", "@type": ["estleg:CourtDecision"]}]},
    )
    write_json(
        krr / "riigikohus" / "RIIGIKOHUS_INDEX.json",
        {"source": "x", "total_decisions": riigikohus_total},
    )

    # eelnoud combined: one DraftLegislation node.
    write_json(
        krr / "eelnoud" / "eelnoud_combined.jsonld",
        {"@graph": [{"@id": "estleg:Draft_1", "@type": ["estleg:DraftLegislation"]}]},
    )
    write_json(
        krr / "eelnoud" / "EELNOUD_INDEX.json",
        {"source": "x", "total_drafts": eelnoud_total},
    )

    # eurlex combined: one EULegislation node.
    write_json(
        krr / "eurlex" / "eurlex_combined.jsonld",
        {"@graph": [{"@id": "estleg:EU_1", "@type": ["estleg:EULegislation"]}]},
    )
    write_json(
        krr / "eurlex" / "EURLEX_INDEX.json",
        {"source": "x", "total_acts": eurlex_total},
    )

    # curia combined: one EUCourtDecision node.
    write_json(
        krr / "curia" / "curia_combined.jsonld",
        {"@graph": [{"@id": "estleg:CURIA_1", "@type": ["estleg:EUCourtDecision"]}]},
    )
    write_json(
        krr / "curia" / "CURIA_INDEX.json",
        {"source": "x", "total_decisions": curia_total},
    )
    return krr


def test_subcorpus_index_counts_pass_when_matching(tmp_path):
    """All four subcorpus INDEX totals equal their node counts -> no error (#313)."""
    krr = _subcorpus_count_corpus(tmp_path)

    validate_all.validate_subcorpus_index_counts(krr)

    assert validate_all.errors == [], validate_all.errors


@pytest.mark.parametrize(
    "kwargs, needle",
    [
        ({"riigikohus_total": 99}, "RIIGIKOHUS_INDEX.json: total_decisions=99"),
        ({"eelnoud_total": 99}, "EELNOUD_INDEX.json: total_drafts=99"),
        ({"eurlex_total": 99}, "EURLEX_INDEX.json: total_acts=99"),
        ({"curia_total": 99}, "CURIA_INDEX.json: total_decisions=99"),
    ],
)
def test_subcorpus_index_counts_flag_each_drift(tmp_path, kwargs, needle):
    """A single drifted INDEX total in any subcorpus is reported (#313)."""
    krr = _subcorpus_count_corpus(tmp_path, **kwargs)

    validate_all.validate_subcorpus_index_counts(krr)

    assert any(needle in e for e in validate_all.errors), validate_all.errors


def test_subcorpus_index_counts_missing_field_is_error(tmp_path):
    """A present INDEX lacking its count field is an error (#313)."""
    krr = _subcorpus_count_corpus(tmp_path)
    # Strip the count field from the eurlex index.
    write_json(krr / "eurlex" / "EURLEX_INDEX.json", {"source": "x"})

    validate_all.validate_subcorpus_index_counts(krr)

    assert any(
        "EURLEX_INDEX.json: total_acts is missing or not an integer" in e
        for e in validate_all.errors
    ), validate_all.errors


def test_subcorpus_index_counts_missing_index_is_error_by_default(tmp_path):
    """A missing subcorpus INDEX file fails by default (#313/#315)."""
    krr = _subcorpus_count_corpus(tmp_path)
    (krr / "curia" / "CURIA_INDEX.json").unlink()

    validate_all.validate_subcorpus_index_counts(krr)

    assert any(
        "curia/CURIA_INDEX.json: subcorpus index not found" in e
        for e in validate_all.errors
    ), validate_all.errors


def test_subcorpus_index_counts_missing_index_allowed_with_flag(tmp_path):
    """`allow_missing_index=True` downgrades a missing subcorpus INDEX to a warning (#313)."""
    krr = _subcorpus_count_corpus(tmp_path)
    (krr / "curia" / "CURIA_INDEX.json").unlink()

    validate_all.validate_subcorpus_index_counts(krr, allow_missing_index=True)

    assert validate_all.errors == [], validate_all.errors
    assert any(
        "curia/CURIA_INDEX.json: index not found" in w for w in validate_all.warnings
    ), validate_all.warnings


def test_subcorpus_index_counts_real_corpus_matches():
    """The committed subcorpus INDEX totals match the real corpus node counts (#313).

    This pins the live invariant: if a regenerated subcorpus ledger ever
    drifts from the nodes it summarises, this gate (and CI) must fail.
    """
    validate_all.validate_subcorpus_index_counts(validate_all.KRR_DIR)

    assert validate_all.errors == [], validate_all.errors


# ---------------------------------------------------------------------------
# Issue #316 — main() fails when zero corpus files are discovered.
# ---------------------------------------------------------------------------


def test_main_exits_nonzero_on_empty_corpus(tmp_path):
    """An empty/misconfigured --krr-dir must exit 1, not false-green (#316)."""
    empty = tmp_path / "empty_krr"
    empty.mkdir()

    with pytest.raises(SystemExit) as exc:
        validate_all.main(["--krr-dir", str(empty)])

    assert exc.value.code == 1
    assert any("No validatable corpus files found" in e for e in validate_all.errors), validate_all.errors


def test_parse_args_defaults():
    """`parse_args` defaults: real krr dir, flag off (#315/#316)."""
    args = validate_all.parse_args([])
    assert args.krr_dir == validate_all.KRR_DIR
    assert args.allow_missing_index is False


def test_parse_args_allow_missing_index_flag():
    """`--allow-missing-index` toggles the opt-out flag (#315)."""
    args = validate_all.parse_args(["--allow-missing-index"])
    assert args.allow_missing_index is True
# Regen-pending staleness guards (#366, #348, #384).
#
# All three are WARNING-only gates: they make stale artifacts visible but
# must never push an error (which would fail CI before the deferred regen
# runs). Each pair of tests asserts (a) a crafted stale fixture emits the
# warning and pushes NO error, and (b) a clean fixture is silent.
# ---------------------------------------------------------------------------


def _vocab_with_inverses(krr: Path) -> None:
    """Write a controlled_vocabulary declaring the 3 PR #297 inverse axioms."""
    write_json(
        krr / "controlled_vocabulary.jsonld",
        {
            "@graph": [
                {"@id": "estleg:amendedBy", "@type": ["owl:ObjectProperty"],
                 "owl:inverseOf": {"@id": "estleg:amends"}},
                {"@id": "estleg:interpretedBy", "@type": ["owl:ObjectProperty"],
                 "owl:inverseOf": {"@id": "estleg:interpretsLaw"}},
                {"@id": "estleg:references", "@type": ["owl:ObjectProperty"],
                 "owl:inverseOf": {"@id": "estleg:referencedBy"}},
            ]
        },
    )


def test_validate_tbox_inverse_parity_warns_on_stale_combined(tmp_path):
    """#366: a combined lacking the vocabulary's inverse axioms warns, not errors."""
    krr = tmp_path / "krr_outputs"
    _vocab_with_inverses(krr)
    # Combined re-declares the forward properties but drops owl:inverseOf
    # (the exact stale-artifact shape PR #297 left behind).
    write_json(
        krr / "combined_ontology.jsonld",
        {
            "@graph": [
                {"@id": "estleg:amendedBy", "@type": ["owl:ObjectProperty"]},
                {"@id": "estleg:interpretedBy", "@type": ["owl:ObjectProperty"]},
                {"@id": "estleg:references", "@type": ["owl:ObjectProperty"]},
            ]
        },
    )

    validate_all.validate_tbox_inverse_parity(krr)

    assert validate_all.errors == [], validate_all.errors
    assert any(
        "missing 3 owl:inverseOf" in w for w in validate_all.warnings
    ), validate_all.warnings
    # Each missing axiom is named in the warning.
    joined = " ".join(validate_all.warnings)
    assert "estleg:amendedBy owl:inverseOf estleg:amends" in joined
    assert "estleg:interpretedBy owl:inverseOf estleg:interpretsLaw" in joined
    assert "estleg:references owl:inverseOf estleg:referencedBy" in joined


def test_validate_tbox_inverse_parity_silent_when_combined_has_axioms(tmp_path):
    """#366: a combined that propagated all inverse axioms is silent."""
    krr = tmp_path / "krr_outputs"
    _vocab_with_inverses(krr)
    write_json(
        krr / "combined_ontology.jsonld",
        {
            "@graph": [
                {"@id": "estleg:amendedBy", "@type": ["owl:ObjectProperty"],
                 "owl:inverseOf": {"@id": "estleg:amends"}},
                {"@id": "estleg:interpretedBy", "@type": ["owl:ObjectProperty"],
                 "owl:inverseOf": {"@id": "estleg:interpretsLaw"}},
                {"@id": "estleg:references", "@type": ["owl:ObjectProperty"],
                 "owl:inverseOf": {"@id": "estleg:referencedBy"}},
            ]
        },
    )

    validate_all.validate_tbox_inverse_parity(krr)

    assert validate_all.errors == [], validate_all.errors
    assert validate_all.warnings == [], validate_all.warnings


def _eu_node(nid: str, *, with_provenance: bool) -> dict:
    node = {
        "@id": nid,
        "@type": ["owl:NamedIndividual", "estleg:EULegislation"],
        "estleg:celexNumber": nid.removeprefix("estleg:EU_"),
    }
    if with_provenance:
        node["owl:sameAs"] = {"@id": "http://data.europa.eu/eli/dir/2000/1/oj"}
        node["dcterms:source"] = {"@id": "https://eur-lex.europa.eu/"}
        node["eli:id_local"] = "32000L0001"
    return node


def test_validate_eu_provenance_fields_warns_on_stale_artifact(tmp_path):
    """#348: EULegislation nodes lacking provenance fields warn, not error."""
    krr = tmp_path / "krr_outputs"
    write_json(
        krr / "eurlex" / "eurlex_directives_peep.json",
        {
            "@graph": [
                _eu_node(f"estleg:EU_3200{i}L0001", with_provenance=False)
                for i in range(5)
            ]
        },
    )

    validate_all.validate_eu_provenance_fields(krr)

    assert validate_all.errors == [], validate_all.errors
    assert any("lack" in w and "provenance" in w for w in validate_all.warnings), (
        validate_all.warnings
    )
    joined = " ".join(validate_all.warnings)
    assert "owl:sameAs missing on 5/5" in joined
    assert "dcterms:source missing on 5/5" in joined
    assert "eli:id_local missing on 5/5" in joined


def test_validate_eu_provenance_fields_silent_when_present(tmp_path):
    """#348: nodes carrying all provenance fields are silent."""
    krr = tmp_path / "krr_outputs"
    write_json(
        krr / "eurlex" / "eurlex_directives_peep.json",
        {
            "@graph": [
                _eu_node(f"estleg:EU_3200{i}L0001", with_provenance=True)
                for i in range(5)
            ]
        },
    )

    validate_all.validate_eu_provenance_fields(krr)

    assert validate_all.errors == [], validate_all.errors
    assert validate_all.warnings == [], validate_all.warnings


def _amendment_event(nid: str, rt: str, eif: str) -> dict:
    return {
        "@id": nid,
        "@type": ["owl:NamedIndividual", "estleg:AmendmentEvent"],
        "estleg:rtReference": rt,
        "estleg:entryIntoForce": {"@value": eif, "@type": "xsd:date"},
    }


def test_validate_amendment_duplicate_bloat_warns_on_duplicates(tmp_path):
    """#384: a chain with many events but few unique pairs warns, not errors."""
    krr = tmp_path / "krr_outputs"
    # 6 events but only 1 unique (rtReference, entryIntoForce) pair.
    write_json(
        krr / "amendments" / "amendments_bloated.json",
        {
            "@graph": [
                _amendment_event(f"estleg:Amendment_X_{i}", "RT I, 2007, 13, 69", "2007-07-01")
                for i in range(6)
            ]
        },
    )

    validate_all.validate_amendment_duplicate_bloat(krr)

    assert validate_all.errors == [], validate_all.errors
    assert any(
        "more" in w and "AmendmentEvent" in w for w in validate_all.warnings
    ), validate_all.warnings
    # The offending chain is listed with its ratio.
    printed = " ".join(validate_all.warnings)
    assert "6 events vs 1 unique" in printed


def test_validate_amendment_duplicate_bloat_silent_on_clean_chain(tmp_path):
    """#384: a chain with one event per unique pair is silent."""
    krr = tmp_path / "krr_outputs"
    write_json(
        krr / "amendments" / "amendments_clean.json",
        {
            "@graph": [
                _amendment_event("estleg:Amendment_Y_1", "RT I, 2010, 1, 1", "2010-01-01"),
                _amendment_event("estleg:Amendment_Y_2", "RT I, 2011, 2, 2", "2011-02-02"),
                _amendment_event("estleg:Amendment_Y_3", "RT I, 2012, 3, 3", "2012-03-03"),
            ]
        },
    )

    validate_all.validate_amendment_duplicate_bloat(krr)

    assert validate_all.errors == [], validate_all.errors
    assert validate_all.warnings == [], validate_all.warnings


def test_regen_pending_guards_never_error_on_missing_artifacts(tmp_path):
    """All three guards degrade to a warning (never error) when inputs absent.

    Mirrors the missing-file handling of the other gates so an empty tree
    cannot fail CI through these checks.
    """
    krr = tmp_path / "krr_outputs"
    krr.mkdir()

    validate_all.validate_tbox_inverse_parity(krr)
    validate_all.validate_eu_provenance_fields(krr)
    validate_all.validate_amendment_duplicate_bloat(krr)

    assert validate_all.errors == [], validate_all.errors
    assert len(validate_all.warnings) == 3, validate_all.warnings


class TestCiRegressionGuards:
    """Regression guards for the 3 CI failures fixed on regen/corpus-data:
    LFS-pointer count gates (#400 follow-up), combined graph-closure of the
    interpretedBy/interpretsLaw inverse (#366), and hasVersion's spurious
    rdfs:range that phantom-typed ProvisionVersion nodes in the laws bucket."""

    def test_is_lfs_pointer_detects_pointer_vs_json(self, tmp_path):
        import json as _json
        ptr = tmp_path / "p.jsonld"
        ptr.write_text("version https://git-lfs.github.com/spec/v1\noid sha256:x\nsize 1\n")
        real = tmp_path / "r.jsonld"
        real.write_text(_json.dumps({"@graph": []}))
        assert validate_all._is_lfs_pointer(ptr) is True
        assert validate_all._is_lfs_pointer(real) is False
        assert validate_all._is_lfs_pointer(tmp_path / "missing.jsonld") is False

    def test_metadata_stats_skips_lfs_pointer_combined(self, tmp_path):
        """An un-materialised LFS *_combined.jsonld yields None (not a 0 count +
        'Invalid JSON' error) so corpus-count gates skip it under pytest lfs:false."""
        import json as _json
        for d in ("eelnoud", "eurlex", "curia", "riigikohus"):
            (tmp_path / d).mkdir()
        (tmp_path / "regulations" / "riik").mkdir(parents=True)
        (tmp_path / "regulations" / "kov").mkdir(parents=True)
        (tmp_path / "eelnoud" / "eelnoud_combined.jsonld").write_text(_json.dumps({"@graph": []}))
        (tmp_path / "curia" / "curia_combined.jsonld").write_text(_json.dumps({"@graph": []}))
        (tmp_path / "eurlex" / "eurlex_combined.jsonld").write_text(
            "version https://git-lfs.github.com/spec/v1\noid sha256:x\nsize 1\n")
        stats = validate_all.metadata_stats(tmp_path)
        assert stats["estleg:euLegislationCount"] is None      # pointer -> skipped
        assert stats["estleg:euCourtDecisionCount"] == 0       # real (empty) graph

    @staticmethod
    def _stage_metadata_tree(tmp_path):
        """Stage the dir layout + empty combined siblings metadata_stats reads.

        Leaves ``riigikohus/`` empty so each test can drop in its own peep
        files (real or LFS pointer) to exercise ``courtDecisionCount``.
        """
        import json as _json
        for d in ("eelnoud", "eurlex", "curia", "riigikohus"):
            (tmp_path / d).mkdir()
        (tmp_path / "regulations" / "riik").mkdir(parents=True)
        (tmp_path / "regulations" / "kov").mkdir(parents=True)
        for d, fn in (
            ("eelnoud", "eelnoud_combined.jsonld"),
            ("eurlex", "eurlex_combined.jsonld"),
            ("curia", "curia_combined.jsonld"),
        ):
            (tmp_path / d / fn).write_text(_json.dumps({"@graph": []}))

    def test_court_decision_count_none_when_all_peeps_are_pointers(self, tmp_path):
        """All-LFS-pointer riigikohus peeps -> courtDecisionCount is None, not 0
        — matching the 3 sibling combined-file counts so an unverifiable corpus
        stays distinguishable from a genuinely-empty one (#605)."""
        self._stage_metadata_tree(tmp_path)
        for yr in (2023, 2024):
            (tmp_path / "riigikohus" / f"riigikohus_{yr}_peep.json").write_text(
                "version https://git-lfs.github.com/spec/v1\noid sha256:x\nsize 1\n")
        stats = validate_all.metadata_stats(tmp_path)
        assert stats["estleg:courtDecisionCount"] is None

    def test_court_decision_count_sums_materialised_peeps(self, tmp_path):
        """Real peeps still sum, and a pointer mixed in is simply skipped (not
        allowed to null the whole count) — the #605 fix must not regress the
        normal path."""
        import json as _json
        self._stage_metadata_tree(tmp_path)
        (tmp_path / "riigikohus" / "riigikohus_2023_peep.json").write_text(
            _json.dumps({"@graph": [
                {"@id": "estleg:D1", "@type": ["estleg:CourtDecision"]},
                {"@id": "estleg:D2", "@type": ["estleg:CourtDecision"]},
            ]}))
        (tmp_path / "riigikohus" / "riigikohus_2024_peep.json").write_text(
            _json.dumps({"@graph": [
                {"@id": "estleg:D3", "@type": ["estleg:CourtDecision"]},
            ]}))
        # A pointer alongside real peeps must not collapse the count to None.
        (tmp_path / "riigikohus" / "riigikohus_2025_peep.json").write_text(
            "version https://git-lfs.github.com/spec/v1\noid sha256:x\nsize 1\n")
        stats = validate_all.metadata_stats(tmp_path)
        assert stats["estleg:courtDecisionCount"] == 3

    def test_court_decision_count_zero_when_no_peeps(self, tmp_path):
        """A riigikohus/ dir with no peeps at all is 'genuinely zero', not
        unverifiable, so the count stays 0 — the #605 None case is specifically
        'peeps exist but every one is an LFS pointer', not 'no peeps'."""
        self._stage_metadata_tree(tmp_path)
        stats = validate_all.metadata_stats(tmp_path)
        assert stats["estleg:courtDecisionCount"] == 0

    def test_controlled_vocab_has_no_dangling_inverseof(self):
        """Every owl:inverseOf target in controlled_vocabulary must itself be a
        node there, so combined_ontology graph-closure (Seadusloome gate) holds
        without pulling in riigikohus_schema.json."""
        import json as _json
        d = _json.loads((validate_all.KRR_DIR / "controlled_vocabulary.jsonld").read_text())
        ids = {n.get("@id") for n in d["@graph"] if isinstance(n, dict)}
        dangling = [
            (n.get("@id"), n["owl:inverseOf"].get("@id"))
            for n in d["@graph"]
            if isinstance(n, dict) and isinstance(n.get("owl:inverseOf"), dict)
            and n["owl:inverseOf"].get("@id") not in ids
        ]
        assert dangling == [], f"dangling owl:inverseOf targets: {dangling}"

    def test_hasversion_has_no_rdfs_range(self):
        """estleg:hasVersion must NOT carry rdfs:range estleg:ProvisionVersion:
        under the SHACL gate's RDFS inference a range types the bare hasVersion
        objects (full nodes live in unloaded provision_versions/ sidecars) as
        ProvisionVersion in the 'laws' bucket -> false versionValidFrom violations."""
        import json as _json
        d = _json.loads((validate_all.KRR_DIR / "controlled_vocabulary.jsonld").read_text())
        hv = next(n for n in d["@graph"]
                  if isinstance(n, dict) and n.get("@id") == "estleg:hasVersion")
        assert "rdfs:range" not in hv


# ---------------------------------------------------------------------------
# validate_legacy_deprecations (#426)
# ---------------------------------------------------------------------------
def _legacy_deprecation_corpus(tmp_path: Path, *, marked: bool) -> tuple[Path, Path]:
    import json

    import deprecate_legacy_statutes as dls

    krr = tmp_path / "krr_outputs"
    krr.mkdir()
    write_json(
        krr / "alkoholi_seadus_peep.json",
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
        },
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
    if marked:
        dls.run(decisions, krr, dry_run=False)
    return decisions, krr


def test_validate_legacy_deprecations_errors_on_unmarked_root(tmp_path: Path):
    decisions, krr = _legacy_deprecation_corpus(tmp_path, marked=False)
    validate_all.validate_legacy_deprecations(krr, decisions_path=decisions)
    assert validate_all.errors
    assert "owl:deprecated" in validate_all.errors[0]


def test_validate_legacy_deprecations_ok_when_marked(tmp_path: Path, capsys):
    decisions, krr = _legacy_deprecation_corpus(tmp_path, marked=True)
    validate_all.validate_legacy_deprecations(krr, decisions_path=decisions)
    assert validate_all.errors == []
    assert "OK: 1 deprecated legacy roots" in capsys.readouterr().out


def test_validate_legacy_deprecations_noop_without_decisions_file(tmp_path: Path, capsys):
    validate_all.validate_legacy_deprecations(
        tmp_path, decisions_path=tmp_path / "absent.json"
    )
    assert validate_all.errors == []
    assert "no deprecation decisions to enforce" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# #416: combined graph-closure gate + stub-aware parity
# ---------------------------------------------------------------------------


def _court_stub(nid: str, label: str = "RK") -> dict:
    return {
        "@id": nid,
        "@type": ["owl:NamedIndividual", "estleg:CourtDecision"],
        "rdfs:label": label,
        estleg_common.STUB_NODE_MARKER: True,
    }


def test_validate_combined_graph_closure_passes_on_closed_graph(tmp_path):
    krr = tmp_path / "krr_outputs"
    _write_combined(
        krr,
        [
            {
                "@id": "estleg:A_1",
                "@type": ["estleg:LegalProvision"],
                "estleg:interpretedBy": {"@id": "estleg:RK_1"},
            },
            _court_stub("estleg:RK_1"),
        ],
    )
    validate_all.validate_combined_graph_closure(krr)
    assert validate_all.errors == [], validate_all.errors


def test_validate_combined_graph_closure_flags_dangling_ref(tmp_path):
    """Synthetic staleness: a referenced overlay/stub node missing → CI fails."""
    krr = tmp_path / "krr_outputs"
    _write_combined(
        krr,
        [
            {
                "@id": "estleg:A_1",
                "@type": ["estleg:LegalProvision"],
                "estleg:hasSanction": {"@id": "estleg:Sanction_missing"},
            }
        ],
    )
    validate_all.validate_combined_graph_closure(krr)
    assert any("dangling estleg" in e for e in validate_all.errors), validate_all.errors


def test_validate_combined_graph_closure_flags_unstripped_version_edge(tmp_path):
    # #561/#589: hasVersion is no longer exempt. The builder strips version
    # forward edges (the version layer is a separate load surface), so a combined
    # that still carries a dangling hasVersion is a stripping regression the gate
    # must flag — not silently pass via an exemption.
    krr = tmp_path / "krr_outputs"
    _write_combined(
        krr,
        [
            {
                "@id": "estleg:A_1",
                "@type": ["estleg:LegalProvision"],
                "estleg:hasVersion": {"@id": "estleg:A_1_v1"},  # should have been stripped
            }
        ],
    )
    validate_all.validate_combined_graph_closure(krr)
    assert any("dangling estleg" in e for e in validate_all.errors), validate_all.errors


def test_combined_overlay_census_flags_unmerged_overlay_node(tmp_path):
    # #561 census: a node present in an overlay SOURCE file but absent from
    # combined (a partial-merge regression) must fail closure even when no
    # dangling edge references it — the dangling check alone would miss it.
    krr = tmp_path / "krr_outputs"
    write_json(
        krr / "sanctions" / "sanctions_x.json",
        {"@graph": [{"@id": "estleg:Sanction_X", "@type": ["estleg:Sanction"], "rdfs:label": "X"}]},
    )
    _write_combined(krr, [{"@id": "estleg:A_1", "@type": ["estleg:LegalProvision"]}])
    validate_all.validate_combined_graph_closure(krr)
    assert any("merged-overlay node(s) absent" in e for e in validate_all.errors), validate_all.errors


def test_combined_overlay_census_passes_when_overlay_merged(tmp_path):
    # The same overlay node, this time PRESENT in combined → census passes.
    krr = tmp_path / "krr_outputs"
    write_json(
        krr / "sanctions" / "sanctions_x.json",
        {"@graph": [{"@id": "estleg:Sanction_X", "@type": ["estleg:Sanction"], "rdfs:label": "X"}]},
    )
    _write_combined(
        krr,
        [
            {"@id": "estleg:A_1", "@type": ["estleg:LegalProvision"]},
            {"@id": "estleg:Sanction_X", "@type": ["estleg:Sanction"], "rdfs:label": "X"},
        ],
    )
    validate_all.validate_combined_graph_closure(krr)
    assert validate_all.errors == [], validate_all.errors


def test_combined_overlay_census_errors_on_unreadable_overlay(tmp_path, capsys):
    # The census must surface a malformed overlay source, not silently report
    # "all present" — its nodes can't be checked, so the success line is gated.
    krr = tmp_path / "krr_outputs"
    bad = krr / "sanctions" / "sanctions_bad.json"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("{ this is not valid json", encoding="utf-8")
    _write_combined(krr, [{"@id": "estleg:A_1", "@type": ["estleg:LegalProvision"]}])
    validate_all.validate_combined_graph_closure(krr)
    out = capsys.readouterr().out
    assert any("cannot read overlay source" in e for e in validate_all.errors), validate_all.errors
    # the "all merged-overlay nodes present" success line must NOT print
    assert "all merged-overlay nodes present" not in out


def _pv(vid, vfrom, vto=None, superseded_by=None):
    node = {
        "@id": f"estleg:{vid}", "@type": ["estleg:ProvisionVersion"],
        "estleg:versionOf": {"@id": "estleg:X_Par_1"},
        "estleg:versionValidFrom": {"@value": vfrom, "@type": "xsd:date"},
    }
    if vto is not None:
        node["estleg:versionValidTo"] = {"@value": vto, "@type": "xsd:date"}
    if superseded_by is not None:
        node["estleg:supersededByVersion"] = {"@id": f"estleg:{superseded_by}"}
    return node


def test_provision_version_monotonicity_flags_overlap(tmp_path):
    # #574 gate: a superseded validTo == successor validFrom (1-day overlap) must
    # fail — and crucially the successor here is the open-ended CURRENT version
    # (no validTo), the closed-to-current transition an earlier validFrom-sort
    # gate silently dropped.
    krr = tmp_path / "krr_outputs"
    write_json(krr / "provision_versions" / "law_x.jsonld", {"@graph": [
        _pv("X_Par_1_v1", "2010-01-01", vto="2015-01-01", superseded_by="X_Par_1_v2"),
        _pv("X_Par_1_v2", "2015-01-01"),  # current, open-ended (no validTo)
    ]})
    validate_all.validate_provision_version_monotonicity(krr)
    assert any("overlapping" in e for e in validate_all.errors), validate_all.errors


def test_provision_version_monotonicity_flags_zero_duration(tmp_path):
    # A version superseded the same day it starts (validFrom == successor
    # validFrom) never had a distinct active period — must be flagged (EVERASGN).
    krr = tmp_path / "krr_outputs"
    write_json(krr / "provision_versions" / "law_x.jsonld", {"@graph": [
        _pv("X_Par_1_v1", "2002-06-01", vto="2002-06-01", superseded_by="X_Par_1_v2"),
        _pv("X_Par_1_v2", "2002-06-01"),  # current, same day
    ]})
    validate_all.validate_provision_version_monotonicity(krr)
    assert any("zero" in e or "overlapping" in e for e in validate_all.errors), validate_all.errors


def test_provision_version_monotonicity_flags_missing_validto(tmp_path):
    # A superseded version with NO validTo is corrupt (it is closed by definition).
    krr = tmp_path / "krr_outputs"
    write_json(krr / "provision_versions" / "law_x.jsonld", {"@graph": [
        _pv("X_Par_1_v1", "2010-01-01", superseded_by="X_Par_1_v2"),  # superseded but no validTo
        _pv("X_Par_1_v2", "2015-01-01"),
    ]})
    validate_all.validate_provision_version_monotonicity(krr)
    assert any("missing-validTo" in e for e in validate_all.errors), validate_all.errors


def test_provision_version_monotonicity_passes_on_exclusive_end(tmp_path):
    # Exclusive end (validTo = day before successor validFrom) → monotone, passes;
    # the successor is the open-ended current version.
    krr = tmp_path / "krr_outputs"
    write_json(krr / "provision_versions" / "law_x.jsonld", {"@graph": [
        _pv("X_Par_1_v1", "2010-01-01", vto="2014-12-31", superseded_by="X_Par_1_v2"),
        _pv("X_Par_1_v2", "2015-01-01"),  # current
    ]})
    validate_all.validate_provision_version_monotonicity(krr)
    assert validate_all.errors == [], validate_all.errors


def test_provision_text_quality_flags_sup(tmp_path):
    # #572: literal <sup> markup leaked into a law-peep string must fail.
    krr = tmp_path / "krr_outputs"
    write_json(krr / "x_peep.json", {"@graph": [
        {"@id": "estleg:X_Par_1", "rdfs:label": "§ 1 lg 1<sup>1</sup>", "estleg:legalText": "Body."},
    ]})
    validate_all.validate_provision_text_quality(krr)
    assert any("<sup>" in e for e in validate_all.errors), validate_all.errors


def test_provision_text_quality_flags_doubled_summary(tmp_path):
    # #613: estleg:summary that is exactly legalText repeated must fail.
    krr = tmp_path / "krr_outputs"
    write_json(krr / "x_peep.json", {"@graph": [
        {"@id": "estleg:X_Par_1", "estleg:summary": "Body. Body.", "estleg:legalText": "Body."},
    ]})
    validate_all.validate_provision_text_quality(krr)
    assert any("repeated verbatim" in e for e in validate_all.errors), validate_all.errors


def test_provision_text_quality_passes_clean(tmp_path):
    # Unicode superscript + a non-doubled summary → clean.
    krr = tmp_path / "krr_outputs"
    write_json(krr / "x_peep.json", {"@graph": [
        {"@id": "estleg:X_Par_1", "rdfs:label": "§ 1 lg 1¹",
         "estleg:summary": "Body.", "estleg:legalText": "Body."},
    ]})
    validate_all.validate_provision_text_quality(krr)
    assert validate_all.errors == [], validate_all.errors


def _harm_pair(krr, *, backed):
    """Stage a peep + a harm_* inverse file; the peep's harmonisedWith is only
    written when ``backed`` (so the harmonises→act edge is symmetric or not)."""
    act = {"@id": "estleg:ACT_Map", "@type": ["owl:Ontology"]}
    if backed:
        act["estleg:harmonisedWith"] = [{"@id": "estleg:Harmonisation_X"}]
    write_json(krr / "act_peep.json", {"@graph": [act]})
    write_json(krr / "harmonisation" / "harmonisation_by_directive" / "harm_X.json", {"@graph": [
        {"@id": "estleg:Harmonisation_X", "@type": ["estleg:HarmonisationLink"],
         "estleg:harmonises": [{"@id": "estleg:ACT_Map"}]},
    ]})


def test_harmonisation_symmetry_flags_unbacked_inverse(tmp_path):
    # #631: a harm_* harmonises→act edge with no backing harmonisedWith fails
    # (the deprecated-rejection asymmetry the #632 review found).
    krr = tmp_path / "krr_outputs"
    _harm_pair(krr, backed=False)
    validate_all.validate_harmonisation_symmetry(krr)
    assert any("asymmetric" in e for e in validate_all.errors), validate_all.errors


def test_harmonisation_symmetry_passes_when_backed(tmp_path):
    krr = tmp_path / "krr_outputs"
    _harm_pair(krr, backed=True)
    validate_all.validate_harmonisation_symmetry(krr)
    assert validate_all.errors == [], validate_all.errors


def test_harmonisation_symmetry_flags_deprecated_carrying_links(tmp_path):
    # #631 review: a deprecated act must carry NEITHER estleg:harmonisedWith nor
    # estleg:transposesDirective — both belong on the isReplacedBy canonical.
    krr = tmp_path / "krr_outputs"
    write_json(krr / "dep_peep.json", {"@graph": [
        {"@id": "estleg:DEP_Map", "@type": ["owl:Ontology"],
         "owl:deprecated": True, "dcterms:isReplacedBy": {"@id": "estleg:CANON_Map"},
         "estleg:transposesDirective": [{"@id": "estleg:EU_X"}]},
    ]})
    # the harm dir must exist or the gate short-circuits
    write_json(krr / "harmonisation" / "harmonisation_by_directive" / "harm_X.json", {"@graph": []})
    validate_all.validate_harmonisation_symmetry(krr)
    assert any("deprecated act" in e for e in validate_all.errors), validate_all.errors


def test_harmonisation_symmetry_flags_report_count_drift(tmp_path):
    # #631 review: harmonisation_report directives_with_parallels must equal
    # len(harmonisation_entries) — wholesale entry deletion otherwise drifts silent.
    krr = tmp_path / "krr_outputs"
    write_json(krr / "act_peep.json", {"@graph": []})
    write_json(krr / "harmonisation" / "harmonisation_by_directive" / "harm_X.json", {"@graph": []})
    write_json(krr / "harmonisation" / "harmonisation_report.json", {
        "directives_with_parallels": 3,
        "harmonisation_entries": [{"directive_celex": "32000L0001"}],
    })
    validate_all.validate_harmonisation_symmetry(krr)
    assert any("directives_with_parallels" in e for e in validate_all.errors), validate_all.errors


def test_harmonisation_symmetry_flags_report_harm_iri_mismatch(tmp_path):
    # #631 review: a report entry's estonian_laws IRIs must equal its harm_*
    # harmonises set — body disagreeing with the per-directive file must fail.
    krr = tmp_path / "krr_outputs"
    write_json(krr / "act_peep.json", {"@graph": []})
    write_json(krr / "harmonisation" / "harmonisation_by_directive" / "harm_32000L0001.json", {"@graph": [
        {"@id": "estleg:Harmonisation_32000L0001", "@type": ["estleg:HarmonisationLink"],
         "estleg:harmonises": [{"@id": "estleg:A_Map"}, {"@id": "estleg:B_Map"}]},
    ]})
    write_json(krr / "harmonisation" / "harmonisation_report.json", {
        "directives_with_parallels": 1,
        "harmonisation_entries": [
            {"directive_celex": "32000L0001",
             "estonian_laws": [{"name": "a", "iri": "estleg:A_Map"}]},  # missing B_Map
        ],
    })
    validate_all.validate_harmonisation_symmetry(krr)
    assert any("estonian_laws IRIs differ" in e for e in validate_all.errors), validate_all.errors


def test_harmonisation_symmetry_flags_unbacked_mapping(tmp_path):
    # #631 review: a harmonisedWith link whose act peep is NOT in the
    # transposition_mapping row for that directive is not reproducible → fail.
    krr = tmp_path / "krr_outputs"
    write_json(krr / "toolepingu_seadus_peep.json", {"@graph": [
        {"@id": "estleg:TOOLEP_Map", "@type": ["owl:Ontology"],
         "estleg:harmonisedWith": [{"@id": "estleg:Harmonisation_32002L0073"}]},
    ]})
    write_json(krr / "harmonisation" / "harmonisation_by_directive" / "harm_32002L0073.json", {"@graph": [
        {"@id": "estleg:Harmonisation_32002L0073", "@type": ["estleg:HarmonisationLink"],
         "estleg:harmonises": [{"@id": "estleg:TOOLEP_Map"}]},
    ]})
    write_json(krr / "transposition_mapping.json", {
        "generated": "x", "source": "x", "country": "EST", "total_measures_fetched": 1,
        "total_matched": 1, "total_unmatched": 0, "unique_directives": 1, "unique_laws": 1,
        "mappings": [{"directive_celex": "32002L0073", "matched_law_name": "vordse_kohtlemise_seadus",
                      "law_files": ["vordse_kohtlemise_seadus_peep.json"]}],  # not toolepingu_seadus
    })
    validate_all.validate_harmonisation_symmetry(krr)
    assert any("not backed by a transposition_mapping" in e for e in validate_all.errors), validate_all.errors


def test_validate_combined_graph_closure_flags_orphan_stub(tmp_path):
    krr = tmp_path / "krr_outputs"
    _write_combined(
        krr,
        [
            {"@id": "estleg:A_1", "@type": ["estleg:LegalProvision"]},
            _court_stub("estleg:RK_unreferenced"),  # referenced by nothing
        ],
    )
    validate_all.validate_combined_graph_closure(krr)
    assert any("stale stub" in e for e in validate_all.errors), validate_all.errors


def test_validate_combined_graph_closure_flags_non_leaf_stub(tmp_path):
    krr = tmp_path / "krr_outputs"
    leaky = _court_stub("estleg:RK_1")
    # estleg:interpretsLaw is NOT a whitelisted shaped-closure edge (#488), so a
    # stub carrying it is still a stripping regression.
    leaky["estleg:interpretsLaw"] = {"@id": "estleg:A_1"}
    _write_combined(
        krr,
        [
            {
                "@id": "estleg:A_1",
                "@type": ["estleg:LegalProvision"],
                "estleg:interpretedBy": {"@id": "estleg:RK_1"},
            },
            leaky,
        ],
    )
    validate_all.validate_combined_graph_closure(krr)
    assert any("disallowed estleg" in e for e in validate_all.errors), validate_all.errors


def test_validate_combined_graph_closure_allows_resolved_shaped_stub_edge(tmp_path):
    """#488: a stub may carry a whitelisted semantic edge (estleg:partOfAct) as
    long as its target resolves in-graph — that is no longer a 'leaky' stub."""
    krr = tmp_path / "krr_outputs"
    kov_provision = {
        "@id": "estleg:Reg_1_Par_1",
        "@type": ["owl:NamedIndividual", "estleg:KovProvision"],
        "rdfs:label": "§ 1",
        "estleg:partOfAct": {"@id": "estleg:Reg_1_Map_2026"},
        estleg_common.STUB_NODE_MARKER: True,
    }
    parent_act = {
        "@id": "estleg:Reg_1_Map_2026",
        "@type": ["estleg:Act", "estleg:MunicipalRegulation"],
        "rdfs:label": "Määrus",
        estleg_common.STUB_NODE_MARKER: True,
    }
    _write_combined(
        krr,
        [
            {
                "@id": "estleg:A_1",
                "@type": ["estleg:LegalProvision"],
                "estleg:references": {"@id": "estleg:Reg_1_Par_1"},
            },
            kov_provision,
            parent_act,
        ],
    )
    validate_all.validate_combined_graph_closure(krr)
    assert validate_all.errors == [], validate_all.errors


def test_validate_combined_graph_closure_flags_unresolved_shaped_stub_edge(tmp_path):
    """A whitelisted edge whose target is absent is still a dangling ref."""
    krr = tmp_path / "krr_outputs"
    kov_provision = {
        "@id": "estleg:Reg_2_Par_1",
        "@type": ["owl:NamedIndividual", "estleg:KovProvision"],
        "rdfs:label": "§ 1",
        "estleg:partOfAct": {"@id": "estleg:Reg_2_Map_2026"},  # absent
        estleg_common.STUB_NODE_MARKER: True,
    }
    _write_combined(
        krr,
        [
            {
                "@id": "estleg:A_1",
                "@type": ["estleg:LegalProvision"],
                "estleg:references": {"@id": "estleg:Reg_2_Par_1"},
            },
            kov_provision,
        ],
    )
    validate_all.validate_combined_graph_closure(krr)
    assert any("dangling estleg" in e for e in validate_all.errors), validate_all.errors


def _annotation_node(nid: str, label: str = "a") -> dict:
    return {
        "@id": nid,
        "@type": ["owl:NamedIndividual", "estleg:Annotation"],
        "rdfs:label": label,
    }


def test_validate_combined_ontology_allows_stub_and_sourced_overlay_extras(tmp_path):
    """Stub nodes are always allowed extras; a sourced overlay node isn't an extra (#416)."""
    krr = tmp_path / "krr_outputs"
    write_json(krr / "law_a_peep.json", {"@graph": [_provision_node("estleg:A_1")]})
    # materialised annotations overlay defines the annotation -> it is a source
    write_json(
        krr / "annotations" / "oiguskantsler_seisukohad.jsonld",
        {"@graph": [_annotation_node("estleg:Annotation_x")]},
    )
    _write_combined(
        krr,
        [
            _provision_node("estleg:A_1"),
            _court_stub("estleg:RK_1"),
            _annotation_node("estleg:Annotation_x"),
        ],
    )
    validate_all.validate_combined_ontology(krr)
    assert not any("stale extra" in e for e in validate_all.errors), validate_all.errors


def test_validate_combined_ontology_flags_stale_annotation_when_source_materialized(tmp_path):
    """Finding 2: an Annotation absent from a materialised source IS a stale extra (#416)."""
    krr = tmp_path / "krr_outputs"
    write_json(krr / "law_a_peep.json", {"@graph": [_provision_node("estleg:A_1")]})
    write_json(
        krr / "annotations" / "oiguskantsler_seisukohad.jsonld",
        {"@graph": [_annotation_node("estleg:Annotation_real")]},
    )
    _write_combined(
        krr,
        [
            _provision_node("estleg:A_1"),
            _annotation_node("estleg:Annotation_real"),
            _annotation_node("estleg:Annotation_STALE"),  # not in source
        ],
    )
    validate_all.validate_combined_ontology(krr)
    assert any("stale extra" in e for e in validate_all.errors), validate_all.errors


def test_validate_combined_ontology_exempts_annotation_when_source_is_lfs_pointer(tmp_path):
    """The Annotation exemption applies ONLY when the source is an un-materialised pointer (#416)."""
    krr = tmp_path / "krr_outputs"
    write_json(krr / "law_a_peep.json", {"@graph": [_provision_node("estleg:A_1")]})
    (krr / "annotations").mkdir(parents=True, exist_ok=True)
    (krr / "annotations" / "oiguskantsler_seisukohad.jsonld").write_text(
        "version https://git-lfs.github.com/spec/v1\noid sha256:deadbeef\nsize 1\n",
        encoding="utf-8",
    )
    _write_combined(
        krr,
        [
            _provision_node("estleg:A_1"),
            _annotation_node("estleg:Annotation_x"),
            # the overlay's generic owl:Ontology wrapper must be exempt too
            {"@id": "estleg:Annotations_Oiguskantsler_Map", "@type": ["owl:Ontology"]},
        ],
    )
    validate_all.validate_combined_ontology(krr)
    assert not any("stale extra" in e for e in validate_all.errors), validate_all.errors
    assert any("pointer" in w for w in validate_all.warnings), validate_all.warnings


def test_validate_combined_graph_closure_detects_expanded_iri_target(tmp_path):
    """Finding 1: an internal ref written as a full IRI must still be checked (#416)."""
    krr = tmp_path / "krr_outputs"
    _write_combined(
        krr,
        [
            {
                "@id": "estleg:A_1",
                "@type": ["estleg:LegalProvision"],
                "estleg:interpretedBy": {
                    "@id": "https://data.riik.ee/ontology/estleg#RK_missing"
                },
            }
        ],
    )
    validate_all.validate_combined_graph_closure(krr)
    assert any("dangling estleg" in e for e in validate_all.errors), validate_all.errors


def test_validate_combined_graph_closure_resolves_expanded_iri_node(tmp_path):
    """A target present only in expanded-IRI @id form still counts as resolved (#416)."""
    krr = tmp_path / "krr_outputs"
    _write_combined(
        krr,
        [
            {
                "@id": "estleg:A_1",
                "@type": ["estleg:LegalProvision"],
                "estleg:interpretedBy": {"@id": "estleg:RK_1"},
            },
            {
                "@id": "https://data.riik.ee/ontology/estleg#RK_1",
                "@type": ["estleg:CourtDecision"],
                "rdfs:label": "x",
                "estleg:isStubNode": True,
            },
        ],
    )
    validate_all.validate_combined_graph_closure(krr)
    assert validate_all.errors == [], validate_all.errors


# (Removed test_validate_combined_graph_closure_flags_stub_referenced_only_via_exempt:
# #561/#589 emptied COMBINED_CLOSURE_EXEMPT_PREDICATES — there is no longer an
# exempt predicate that could keep a stub alive, so the scenario it guarded
# cannot arise. The genuinely-unreferenced-stub orphan check is covered by
# test_validate_combined_graph_closure_flags_orphan_stub.)


def test_iter_node_estleg_refs_canonicalizes_and_attributes_nested_predicate():
    """Finding 1 unit: expanded IRIs canonicalised; nested ref keeps its inner predicate."""
    expanded = {
        "@id": "estleg:A",
        "estleg:interpretedBy": {"@id": "https://data.riik.ee/ontology/estleg#RK_x"},
    }
    assert list(estleg_common.iter_node_estleg_refs(expanded)) == [
        ("estleg:interpretedBy", "estleg:RK_x")
    ]
    nested = {"@id": "estleg:B", "estleg:wrap": {"estleg:hasVersion": {"@id": "estleg:B_v1"}}}
    assert list(estleg_common.iter_node_estleg_refs(nested)) == [
        ("estleg:hasVersion", "estleg:B_v1")
    ]
    assert estleg_common.canonical_estleg_ref("http://example.org/x") is None

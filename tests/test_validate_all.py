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
            ],
        },
    )

    validate_all.validate_transposition_mapping(krr)

    assert validate_all.errors == [], validate_all.errors


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

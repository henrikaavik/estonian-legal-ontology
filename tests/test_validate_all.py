import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import validate_all


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

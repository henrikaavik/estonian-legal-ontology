import json
from pathlib import Path

from estleg import shacl_validate_all


def touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")


def test_collect_files_splits_laws_and_state_regulations(tmp_path):
    krr = tmp_path / "krr_outputs"
    touch(krr / "law_peep.json")
    touch(krr / "regulations" / "riik" / "state_peep.json")
    touch(krr / "regulations" / "kov" / "issuer" / "kov_peep.json")
    (krr / "INDEX.json").write_text(
        json.dumps({"laws": [{"name": "law", "files": ["law_peep.json"]}]}),
        encoding="utf-8",
    )

    files = shacl_validate_all.collect_files("laws", krr=krr)

    assert files == [
        krr / "law_peep.json",
        krr / "regulations" / "riik" / "state_peep.json",
    ]


def test_kov_bucket_registered_and_resolves_kov_peep_files(tmp_path):
    # Guards the CI ``semantic-validation`` matrix entry: the ``kov``
    # bucket must exist and must collect municipal regulation peep
    # files from ``krr_outputs/regulations/kov/**`` (issue #105).
    assert "kov" in shacl_validate_all.BUCKETS

    krr = tmp_path / "krr_outputs"
    touch(krr / "regulations" / "kov" / "tallinn" / "rule_peep.json")
    touch(krr / "regulations" / "kov" / "tartu" / "nested" / "rule2_peep.json")

    files = shacl_validate_all.collect_files("kov", krr=krr)

    assert krr / "regulations" / "kov" / "tallinn" / "rule_peep.json" in files
    assert krr / "regulations" / "kov" / "tartu" / "nested" / "rule2_peep.json" in files
    assert all(p.name.endswith("_peep.json") for p in files)


def test_collect_files_splits_kov_with_registries(tmp_path):
    krr = tmp_path / "krr_outputs"
    touch(krr / "municipalities_peep.json")
    touch(krr / "issuers_kov_peep.json")
    touch(krr / "regulations" / "kov" / "issuer" / "kov_peep.json")
    touch(krr / "regulations" / "kov" / "REGULATIONS_KOV_INDEX.json")

    files = shacl_validate_all.collect_files("kov", krr=krr)

    assert files == [
        krr / "issuers_kov_peep.json",
        krr / "municipalities_peep.json",
        krr / "regulations" / "kov" / "issuer" / "kov_peep.json",
    ]


def test_sidecars_bucket_registered_and_resolves_enrichment_files(tmp_path):
    # Guards the CI ``semantic-validation`` matrix entry: the
    # ``sidecars`` bucket must exist and must collect the enrichment
    # outputs that carry ``estleg:LegalConcept`` / ``estleg:Sanction`` /
    # ``estleg:AmendmentEvent`` / ``estleg:Institution`` shaped nodes,
    # which no ``*_peep.json`` bucket covers (issue #106).
    assert "sidecars" in shacl_validate_all.BUCKETS

    krr = tmp_path / "krr_outputs"
    touch(krr / "concepts" / "concepts_combined.jsonld")
    touch(krr / "concepts" / "concept_crossref_report.json")
    touch(krr / "sanctions" / "sanctions_alkoholiseadus.json")
    touch(krr / "amendments" / "amendments_alkoholiseadus.json")
    touch(krr / "institutions" / "institution_halduskohus.json")
    touch(krr / "controlled_vocabulary.jsonld")

    files = shacl_validate_all.collect_files("sidecars", krr=krr)

    assert files == [
        krr / "amendments" / "amendments_alkoholiseadus.json",
        krr / "concepts" / "concepts_combined.jsonld",
        krr / "controlled_vocabulary.jsonld",
        krr / "institutions" / "institution_halduskohus.json",
        krr / "sanctions" / "sanctions_alkoholiseadus.json",
    ]
    # Report/aggregate files that lack ``@graph`` must not be handed to pyshacl.
    assert krr / "concepts" / "concept_crossref_report.json" not in files


def test_collect_files_covers_semantic_corpus_buckets(tmp_path):
    krr = tmp_path / "krr_outputs"
    touch(krr / "law_peep.json")
    touch(krr / "regulations" / "riik" / "state_peep.json")
    touch(krr / "regulations" / "kov" / "issuer" / "kov_peep.json")
    touch(krr / "riigikohus" / "riigikohus_2026_peep.json")
    touch(krr / "eelnoud" / "eelnoud_review_peep.json")
    touch(krr / "eurlex" / "eurlex_regulations_peep.json")
    touch(krr / "curia" / "curia_judgments_peep.json")
    touch(krr / "concepts" / "concepts_combined.jsonld")
    touch(krr / "sanctions" / "sanctions_alkoholiseadus.json")
    touch(krr / "amendments" / "amendments_alkoholiseadus.json")
    touch(krr / "institutions" / "institution_halduskohus.json")
    (krr / "INDEX.json").write_text(
        json.dumps({"laws": [{"name": "law", "files": ["law_peep.json"]}]}),
        encoding="utf-8",
    )

    files = shacl_validate_all.collect_files(all_buckets=True, krr=krr)

    assert files == sorted(
        {
            krr / "law_peep.json",
            krr / "regulations" / "riik" / "state_peep.json",
            krr / "regulations" / "kov" / "issuer" / "kov_peep.json",
            krr / "riigikohus" / "riigikohus_2026_peep.json",
            krr / "eelnoud" / "eelnoud_review_peep.json",
            krr / "eurlex" / "eurlex_regulations_peep.json",
            krr / "curia" / "curia_judgments_peep.json",
            krr / "concepts" / "concepts_combined.jsonld",
            krr / "sanctions" / "sanctions_alkoholiseadus.json",
            krr / "amendments" / "amendments_alkoholiseadus.json",
            krr / "institutions" / "institution_halduskohus.json",
        }
    )


def test_sidecars_bucket_resolves_against_live_corpus():
    # End-to-end discovery check against the real ``krr_outputs/`` tree:
    # the bucket must pick up the four sidecar dirs' shaped data files and
    # drop the lone non-data report. Mirrors the ``--bucket sidecars`` CI
    # job's file-collection step (issue #106).
    files = shacl_validate_all.collect_files("sidecars")

    by_dir = {p.parent.name for p in files}
    assert {"amendments", "sanctions", "institutions"} <= by_dir
    assert any(p.name == "concepts_combined.jsonld" for p in files)
    assert all(not p.name.endswith("_report.json") for p in files)
    assert all(p.suffix in {".json", ".jsonld"} for p in files)


def test_kov_bucket_includes_historical_municipalities_live_corpus():
    # The kov bucket is the live validation path for HistoricalMunicipalityShape
    # (issue #285): the source file lives under data/ehak/ rather than
    # krr_outputs/, so the bucket must list it explicitly.
    files = shacl_validate_all.collect_files("kov")

    assert any(
        p.name == "historical_municipalities.jsonld" and p.parent.name == "ehak"
        for p in files
    ), [str(p) for p in files if "ehak" in str(p)]


# ---------------------------------------------------------------------------
# 0-file bucket guard (issue #338)
# ---------------------------------------------------------------------------


def test_main_errors_when_bucket_collects_zero_files(monkeypatch, capsys):
    # A collector returning 0 files (missing/renamed INDEX.json or corpus
    # subdir) must make ``main`` exit non-zero instead of validating an empty
    # graph that pyshacl would report as conforming (issue #338). The guard
    # fires before any rdflib/pyshacl work, so no real corpus is needed.
    monkeypatch.setattr(shacl_validate_all, "collect_files", lambda *a, **k: [])

    rc = shacl_validate_all.main(["--bucket", "laws"])

    assert rc == 2
    out = capsys.readouterr().out
    assert "no corpus files collected" in out
    # The guard must short-circuit before the graph-loading banner.
    assert "into a single graph" not in out


def test_main_errors_when_only_controlled_vocabulary_present(monkeypatch, capsys):
    # #590 regression: controlled_vocabulary.jsonld is appended to EVERY bucket,
    # so a vanished/renamed corpus subdir still yields exactly that one file. The
    # old ``if not files:`` guard saw 1 file and PASSED, validating only the TBox
    # and certifying a missing corpus as green. The guard must exclude the
    # always-present vocab file from the emptiness test.
    from pathlib import Path

    vocab_only = [Path("/whatever/krr_outputs/controlled_vocabulary.jsonld")]
    monkeypatch.setattr(shacl_validate_all, "collect_files", lambda *a, **k: vocab_only)

    rc = shacl_validate_all.main(["--bucket", "curia"])

    assert rc == 2
    out = capsys.readouterr().out
    assert "no corpus files collected" in out
    assert "into a single graph" not in out


def test_main_zero_file_guard_matches_seadusloome_sync_contract():
    # Both SHACL gates must treat an empty corpus subset as a hard error
    # (return code 2), not a trivially-passing run. This pins the shared
    # contract referenced by issue #338, with the #590 vocab-exclusion fix.
    import inspect

    src = inspect.getsource(shacl_validate_all.main)
    assert "if not corpus_files:" in src
    assert "return 2" in src


# ---------------------------------------------------------------------------
# SHACL string-enum closure + uniform graphClosureExempt (issue #290)
# ---------------------------------------------------------------------------

SHAPES_TTL = Path(__file__).resolve().parent.parent / "shacl" / "estonian_legal_shapes.ttl"
_NS = "https://w3id.org/estleg/"
_SH = "http://www.w3.org/ns/shacl#"


def _load_shapes_graph():
    import rdflib

    return rdflib.Graph().parse(str(SHAPES_TTL), format="turtle")


def _sh_in_values_for_path(graph, predicate_local: str) -> set[str]:
    """Collect every literal in any ``sh:in`` list on a property shape whose
    ``sh:path`` is ``estleg:<predicate_local>``."""
    import rdflib
    from rdflib.collection import Collection

    sh_path = rdflib.URIRef(_SH + "path")
    sh_in = rdflib.URIRef(_SH + "in")
    target = rdflib.URIRef(_NS + predicate_local)

    values: set[str] = set()
    for shape in graph.subjects(sh_path, target):
        for list_head in graph.objects(shape, sh_in):
            for item in Collection(graph, list_head):
                values.add(str(item))
    return values


def test_sanction_type_has_closed_value_set():
    graph = _load_shapes_graph()
    values = _sh_in_values_for_path(graph, "sanctionType")
    assert values == {
        "imprisonment",
        "fine",
        "pecuniary_punishment",
        "arrest",
        "coercive_payment",
        # Issue #681: KarS's two remaining "other punishments" —
        # konfiskeerimine (KarS §§ 83-85) and sundlõpetamine (KarS § 46).
        "confiscation",
        "compulsory_dissolution",
    }, values


def test_competence_type_has_closed_value_set_including_general():
    graph = _load_shapes_graph()
    values = _sh_in_values_for_path(graph, "competenceType")
    # InstitutionShape.competenceType and CompetenceShape.competenceType must
    # both close the set and both must admit "general".
    assert {
        "supervision",
        "licensing",
        "enforcement",
        "regulation",
        "general",
    } <= values, values


def test_enum_iri_predicates_marked_graph_closure_exempt_in_shapes():
    import rdflib

    graph = _load_shapes_graph()
    sh_path = rdflib.URIRef(_SH + "path")
    marker = rdflib.URIRef(_NS + "graphClosureExempt")

    exempt_predicates: set[str] = set()
    for shape, _, value in graph.triples((None, marker, None)):
        if str(value).lower() not in {"true", "1"}:
            continue
        predicate = graph.value(shape, sh_path)
        if isinstance(predicate, rdflib.URIRef) and str(predicate).startswith(_NS):
            exempt_predicates.add(str(predicate)[len(_NS):])

    required = {
        "normativeType",
        "legislativePhase",
        "draftType",
        "euDocumentType",
        "euInstitution",
        "euCourt",
        "caseType",
        "decisionType",
        "euCourtDecisionType",
    }
    assert required <= exempt_predicates, sorted(required - exempt_predicates)


# ---------------------------------------------------------------------------
# Structural & integration shape coverage (issues #357, #341, #345, #389)
# ---------------------------------------------------------------------------


def _target_class_of(graph, shape_local: str) -> str | None:
    import rdflib

    shape = rdflib.URIRef(_NS + shape_local)
    tc = graph.value(shape, rdflib.URIRef(_SH + "targetClass"))
    return str(tc) if tc is not None else None


def _paths_on_shape(graph, shape_local: str) -> set[str]:
    """Local names of every estleg: predicate constrained by a property shape
    of ``estleg:<shape_local>``."""
    import rdflib

    shape = rdflib.URIRef(_NS + shape_local)
    sh_property = rdflib.URIRef(_SH + "property")
    sh_path = rdflib.URIRef(_SH + "path")
    paths: set[str] = set()
    for prop in graph.objects(shape, sh_property):
        path = graph.value(prop, sh_path)
        if isinstance(path, rdflib.URIRef) and str(path).startswith(_NS):
            paths.add(str(path)[len(_NS):])
    return paths


def test_chapter_and_division_nodeshapes_present():
    graph = _load_shapes_graph()
    assert _target_class_of(graph, "ChapterShape") == _NS + "Chapter"
    assert _target_class_of(graph, "DivisionShape") == _NS + "Division"
    assert "chapterNumber" in _paths_on_shape(graph, "ChapterShape")
    assert "isPartOf" in _paths_on_shape(graph, "DivisionShape")


def test_amendment_event_shape_constrains_core_predicates():
    # issue #357 (amends/entryIntoForce/rtReference) + issue #389
    # (isCurrentAmendment).
    graph = _load_shapes_graph()
    paths = _paths_on_shape(graph, "AmendmentEventShape")
    assert {"amends", "entryIntoForce", "rtReference", "isCurrentAmendment"} <= paths, paths


def test_legal_concept_shape_constrains_definedin_and_sourceact():
    graph = _load_shapes_graph()
    paths = _paths_on_shape(graph, "LegalConceptShape")
    assert {"definedIn", "sourceAct"} <= paths, paths


def test_institution_type_has_closed_value_set():
    graph = _load_shapes_graph()
    values = _sh_in_values_for_path(graph, "institutionType")
    assert values == {
        "ministry",
        "agency",
        "court",
        "local_government",
        "parliament",
        "head_of_state",
        "government",
    }, values


def test_act_temporal_shape_constrains_kehtiv_and_content_status():
    graph = _load_shapes_graph()
    paths = _paths_on_shape(graph, "ActTemporalShape")
    assert {"kehtiv", "contentStatus"} <= paths, paths
    assert _sh_in_values_for_path(graph, "contentStatus") == {
        "structuredBody",
        "noStructuredBody",
    }


def test_legal_provision_shape_constrains_hasversion_as_iri():
    # issue #345 (SHACL part): the hasVersion back-link must be shaped as an
    # IRI on the provision shape so the materialisation lands validated.
    import rdflib

    graph = _load_shapes_graph()
    assert "hasVersion" in _paths_on_shape(graph, "LegalProvisionShape")
    # And it must be constrained to sh:nodeKind sh:IRI.
    shape = rdflib.URIRef(_NS + "LegalProvisionShape")
    sh_property = rdflib.URIRef(_SH + "property")
    sh_path = rdflib.URIRef(_SH + "path")
    sh_nodekind = rdflib.URIRef(_SH + "nodeKind")
    target = rdflib.URIRef(_NS + "hasVersion")
    nodekinds = {
        str(graph.value(prop, sh_nodekind))
        for prop in graph.objects(shape, sh_property)
        if graph.value(prop, sh_path) == target
    }
    assert _SH + "IRI" in nodekinds, nodekinds

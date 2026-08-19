"""Tests for extract_court_provision_links.py — Layer 2c PR #3."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from estleg.estleg_common import _RunCounters
from estleg.extract_court_provision_links import (
    PAT_KOV_ACT,
    PAT_RTIV,
    _expand_par_range,
    _trim_municipality_to_known_issuer,
    build_abbreviation_to_prefix,
    build_kov_act_index,
    build_provision_index,
    expand_two_digit_year,
    extract_citations_from_text,
    process_court_files,
    resolve_citations,
    resolve_kov_citation,
)

# ---------------------------------------------------------------------------
# Group 1a — expand_two_digit_year
# ---------------------------------------------------------------------------

def test_year_99_to_1999() -> None:
    assert expand_two_digit_year("21.06.99") == 1999


def test_year_51_to_1951() -> None:
    assert expand_two_digit_year("01.01.51") == 1951


def test_year_50_to_2050_boundary() -> None:
    # y > 50 means y == 50 falls into the 20xx bucket
    assert expand_two_digit_year("01.01.50") == 2050


def test_year_25_to_2025() -> None:
    assert expand_two_digit_year("13.10.25") == 2025


def test_year_00_to_2000() -> None:
    assert expand_two_digit_year("13.10.00") == 2000


def test_year_four_digit_passthrough() -> None:
    assert expand_two_digit_year("13.10.2009") == 2009


def test_year_long_form_estonian_month() -> None:
    assert expand_two_digit_year("18. juuni 2020") == 2020


# ---------------------------------------------------------------------------
# Group 1b — PAT_KOV_ACT regex
# ---------------------------------------------------------------------------

def _first_match(text: str):
    return PAT_KOV_ACT.search(text)


def test_pat_numeric_date_genitive_body() -> None:
    m = _first_match("Keila Vallavolikogu 21.06.99 määrus nr 55")
    assert m is not None
    assert m.group("municipality").strip() == "Keila"
    assert m.group("body").lower() == "vallavolikogu"
    assert m.group("date") == "21.06.99"
    assert m.group("num") == "55"


def test_pat_long_date_form() -> None:
    m = _first_match("Tallinna Linnavolikogu 18. juuni 2020. a määruse nr 15")
    assert m is not None
    assert m.group("municipality").strip() == "Tallinna"
    assert m.group("body").lower() == "linnavolikogu"
    assert m.group("date") == "18. juuni 2020"
    assert m.group("num") == "15"


def test_pat_instrumental() -> None:
    m = _first_match("Tallinna Linnavalitsuse 14.05.2009 määrusega nr 23")
    assert m is not None
    assert m.group("num") == "23"


def test_pat_nominative_body() -> None:
    m = _first_match("Tallinna Linnavalitsus 14.05.2009 määrus nr 23")
    assert m is not None
    assert m.group("body").lower() == "linnavalitsus"


def test_pat_no_match_vague_plural() -> None:
    assert _first_match("Pärnu Linnavalitsuse määruste peale") is None


def test_pat_no_match_case_name() -> None:
    # "Tallinna Linna määruskaebus" is a case-name pattern, not an act citation.
    assert _first_match("Tallinna Linna määruskaebus Tallinna Ringkonnakohtu otsuse peale") is None


def test_pat_no_overcapture_lowercase_prefix_long() -> None:
    # Pins the case-sensitive municipality match against IGNORECASE-driven regression.
    m = _first_match("Õiguskantsleri taotlus Tallinna Linnavolikogu 19.02.2009 määruse nr 3 muutmise kohta")
    assert m is not None
    assert m.group("municipality").strip() == "Tallinna"


def test_pat_no_overcapture_lowercase_prefix_short() -> None:
    m = _first_match("tunnistada kehtetuks Keila Vallavolikogu 21.06.99 määrus nr 55")
    assert m is not None
    assert m.group("municipality").strip() == "Keila"


def test_pat_rtiv_canonical_form() -> None:
    m = PAT_RTIV.search("vt RT IV, 2020-04-15, 7")
    assert m is not None
    assert m.group(0).startswith("RT IV")
    assert "2020-04-15" in m.group(0)


def test_pat_rtiv_alternate_form() -> None:
    m = PAT_RTIV.search("avaldatud RT IV 2018, 3, 5")
    assert m is not None
    assert m.group(0).startswith("RT IV")
    assert "2018" in m.group(0)


def test_pat_rtiv_no_match_plain_text() -> None:
    assert PAT_RTIV.search("KarS § 121") is None


# ---------------------------------------------------------------------------
# Group 1c — extract_citations_from_text returns (state, kov) tuple
# ---------------------------------------------------------------------------

def test_extract_returns_tuple() -> None:
    result = extract_citations_from_text("KarS § 121")
    assert isinstance(result, tuple)
    assert len(result) == 2


def test_extract_state_only_text() -> None:
    state, kov = extract_citations_from_text("KarS § 121")
    assert state                      # at least one state-law citation
    assert kov == []


def test_extract_kov_only_text() -> None:
    state, kov = extract_citations_from_text(
        "Viimsi Vallavolikogu 13.10.2009 määruse nr 22"
    )
    assert state == []
    assert len(kov) == 1
    assert kov[0]["municipality"].strip() == "Viimsi"
    assert kov[0]["body"].lower() == "vallavolikogu"
    assert kov[0]["num"] == "22"
    assert kov[0]["raw_text"].startswith("Viimsi Vallavolikogu")


def test_extract_mixed_text() -> None:
    state, kov = extract_citations_from_text(
        "Vt KarS § 121 ja Viimsi Vallavolikogu 13.10.2009 määruse nr 22"
    )
    assert len(state) >= 1
    assert len(kov) == 1


def test_extract_empty_text() -> None:
    assert extract_citations_from_text("") == ([], [])
    assert extract_citations_from_text(None) == ([], [])


def test_vtms_hkms_do_not_resolve_to_tms_kms() -> None:
    """#350: no word boundary used to match TMS inside VTMS and KMS inside HKMS."""
    state, _ = extract_citations_from_text("kohus kohaldas VTMS § 12")
    assert [c["law_ref"] for c in state] != ["TMS"]
    assert all(c["law_ref"] != "TMS" for c in state)
    state, _ = extract_citations_from_text("viidatud HKMS § 3")
    assert all(c["law_ref"] != "KMS" for c in state)
    state, _ = extract_citations_from_text("TMS § 8")
    assert any(c["law_ref"] == "TMS" and c["paragraphs"] == ["8"] for c in state)


# ---------------------------------------------------------------------------
# Group 2 — build_kov_act_index
# ---------------------------------------------------------------------------

FIXTURE_KOV = Path(__file__).parent / "fixtures" / "kov_court_provision_links" / "kov"


def test_build_kov_index_resolves_viimsi(monkeypatch) -> None:
    """Index walks fixture KOV peeps and registers the Viimsi 2009 #22 act."""
    fixture_files = list((FIXTURE_KOV / "viimsi_vallavolikogu").glob("*_peep.json"))

    def fake_iter(*, include_kov: bool = True):
        return iter(fixture_files)

    monkeypatch.setattr(
        "estleg.extract_court_provision_links.iter_peep_files", fake_iter
    )

    counters = _RunCounters()
    kov_index, collisions, iri_to_file, known_issuers = build_kov_act_index(counters)

    assert ("viimsi vallavolikogu", 2009, "22") in kov_index
    assert kov_index[("viimsi vallavolikogu", 2009, "22")] == "estleg:Reg_1024484_Map"
    assert "viimsi vallavolikogu" in known_issuers
    assert collisions == set()


def test_build_kov_index_detects_collisions(monkeypatch) -> None:
    fixture_files = list((FIXTURE_KOV / "collision_pair").glob("*_peep.json"))

    def fake_iter(*, include_kov: bool = True):
        return iter(fixture_files)

    monkeypatch.setattr(
        "estleg.extract_court_provision_links.iter_peep_files", fake_iter
    )

    counters = _RunCounters()
    kov_index, collisions, iri_to_file, known_issuers = build_kov_act_index(counters)

    key = ("test vallavolikogu", 2016, "5")
    assert key in collisions
    assert key not in kov_index            # removed on collision detection
    # Both files still recorded in iri_to_file (cleanup pass needs them)
    assert "estleg:Reg_2000001_Map" in iri_to_file
    assert "estleg:Reg_2000002_Map" in iri_to_file


# ---------------------------------------------------------------------------
# Group 2b — resolve_kov_citation
# ---------------------------------------------------------------------------

def _kc(municipality="Viimsi ", body="Vallavolikogu", date="13.10.2009", num="22"):
    return {
        "municipality": municipality,
        "body": body,
        "date": date,
        "num": num,
        "raw_text": f"{municipality}{body} {date} määruse nr {num}",
    }


def test_resolver_strict_match() -> None:
    idx = {("viimsi vallavolikogu", 2009, "22"): "estleg:Reg_1024484_Map"}
    iri, reason, _issuer = resolve_kov_citation(_kc(), idx, set(), {"viimsi vallavolikogu"})
    assert iri == "estleg:Reg_1024484_Map"
    assert reason is None


def test_resolver_year_plus_one_alternate() -> None:
    # Index has 2010 entry; query year 2009 resolves via +1 alternate.
    idx = {("tallinna linnavalitsus", 2010, "75"): "estleg:Reg_1014396_Map"}
    iri, reason, _issuer = resolve_kov_citation(
        _kc(municipality="Tallinna ", body="Linnavalitsus", date="14.05.2009", num="75"),
        idx, set(), {"tallinna linnavalitsus"},
    )
    assert iri == "estleg:Reg_1014396_Map"
    assert reason is None


def test_resolver_ambiguous_primary_short_circuits() -> None:
    collisions = {("viimsi vallavolikogu", 2009, "22")}
    iri, reason, _issuer = resolve_kov_citation(_kc(), {}, collisions, {"viimsi vallavolikogu"})
    assert iri is None
    assert reason == "ambiguous_key"


def test_resolver_ambiguous_alternate_short_circuits() -> None:
    # Primary 2009 missing; alternate 2010 collision-tracked.
    collisions = {("viimsi vallavolikogu", 2010, "22")}
    iri, reason, _issuer = resolve_kov_citation(_kc(), {}, collisions, {"viimsi vallavolikogu"})
    assert iri is None
    assert reason == "ambiguous_key"


def test_resolver_unmatched() -> None:
    iri, reason, _issuer = resolve_kov_citation(_kc(), {}, set(), {"viimsi vallavolikogu"})
    assert iri is None
    assert reason == "issuer_year_num_unmatched"


def test_resolver_unknown_issuer_short_circuits() -> None:
    # Issuer NOT in known_issuer_norms → unknown_issuer, no index lookup.
    iri, reason, _issuer = resolve_kov_citation(_kc(), {}, set(), set())
    assert iri is None
    assert reason == "unknown_issuer"


# ---------------------------------------------------------------------------
# Group 3 — end-to-end fixture run
# ---------------------------------------------------------------------------

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "kov_court_provision_links"


def _stage_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Stage fixture into tmp_path so the script writes to a copy.

    Layout under tmp_path:
        krr_outputs/riigikohus/riigikohus_*_peep.json
        krr_outputs/regulations/<state-laws>
        krr_outputs/regulations/kov/<KOV peeps>
        krr_outputs/reports/kov/  (for coverage report output)
    """
    krr = tmp_path / "krr_outputs"
    rk = krr / "riigikohus"
    laws = krr / "regulations"
    kov = krr / "regulations" / "kov"
    (krr / "reports" / "kov").mkdir(parents=True, exist_ok=True)
    rk.mkdir(parents=True, exist_ok=True)
    laws.mkdir(parents=True, exist_ok=True)
    kov.mkdir(parents=True, exist_ok=True)

    for f in (FIXTURE_ROOT / "riigikohus").glob("*_peep.json"):
        shutil.copy(f, rk / f.name)
    for f in (FIXTURE_ROOT / "state_laws").glob("*_peep.json"):
        shutil.copy(f, laws / f.name)
    for sub in (FIXTURE_ROOT / "kov").iterdir():
        if sub.is_dir():
            dst = kov / sub.name
            dst.mkdir(parents=True, exist_ok=True)
            for f in sub.glob("*_peep.json"):
                shutil.copy(f, dst / f.name)

    return krr, rk, kov


def _run_script_against(krr: Path, monkeypatch, *, enable_kov: bool = True) -> dict:
    """Run main() with the fixture as the active KRR root; return coverage dict."""
    from estleg import extract_court_provision_links as ecpl

    monkeypatch.setattr(ecpl, "KRR_DIR", krr)
    monkeypatch.setattr(ecpl, "RK_DIR", krr / "riigikohus")

    # Override iter_peep_files to walk the fixture's regulations directory.
    def fake_iter(*, include_kov: bool = True):
        files = list((krr / "regulations").rglob("*_peep.json"))
        if not include_kov:
            files = [f for f in files if "kov" not in f.parts]
        return files

    monkeypatch.setattr(ecpl, "iter_peep_files", fake_iter)

    ecpl.main(enable_kov=enable_kov)
    cov_path = krr / "reports" / "kov" / "court_provision_links_coverage.json"
    return json.loads(cov_path.read_text(encoding="utf-8"))


def test_no_kov_flag_skips_kov_resolution(tmp_path, monkeypatch) -> None:
    """--no-kov: KOV act resolution is skipped (triples_emitted_kov == 0) while
    state-law interpretsLaw/interpretedBy is unchanged."""
    krr, rk, kov = _stage_fixture(tmp_path)
    cov = _run_script_against(krr, monkeypatch, enable_kov=False)

    # No KOV links emitted.
    assert cov["triples_emitted_kov"] == 0
    assert cov["files_with_output_kov"] == 0

    # State-law resolution unchanged: the court decision still interprets the
    # TsMS state provision; the Viimsi KOV act IRI is NOT linked.
    rk_doc = json.loads((rk / "riigikohus_2009_peep.json").read_text(encoding="utf-8"))
    decision = next(
        n for n in rk_doc["@graph"] if n["@id"] == "estleg:RK_FIXT_VIIMSI_2009"
    )
    iris = [v["@id"] for v in decision.get("estleg:interpretsLaw", [])]
    assert "estleg:Tsiviilkohtumenetluse_seadustik_Par_208" in iris  # state link kept
    assert "estleg:Reg_1024484_Map" not in iris                 # KOV link skipped

    # KOV act file carries no interpretedBy.
    viimsi_doc = json.loads(
        (kov / "viimsi_vallavolikogu" / "maarus_22_t1024484_peep.json").read_text(
            encoding="utf-8"
        )
    )
    viimsi_act = next(
        n for n in viimsi_doc["@graph"] if n["@id"] == "estleg:Reg_1024484_Map"
    )
    assert not viimsi_act.get("estleg:interpretedBy")


def test_end_to_end_resolves_viimsi_unresolved_keila(tmp_path, monkeypatch) -> None:
    krr, rk, kov = _stage_fixture(tmp_path)
    cov = _run_script_against(krr, monkeypatch)

    # Stable report shape: pre-seeded keys present.
    for key in (
        "kov_citation_ambiguous",
        "kov_citation_unknown_issuer",
        "rtiv_form_citation",
        "json_decode_error",
    ):
        assert key in cov["skip_reasons"], f"missing pre-seeded key: {key}"

    # Resolved exactly 1 KOV citation (Viimsi 2009 #22).
    assert cov["triples_emitted_kov"] == 1

    # Keila citation lands in unresolved (issuer known via fixture peep, year/num miss).
    assert cov["unresolved_references"] == 1

    # Test Vallavolikogu 2016 #5 → ambiguous (collision pair).
    assert cov["skip_reasons"]["kov_citation_ambiguous"] == 1

    # No RT IV in fixture summaries.
    assert cov["skip_reasons"]["rtiv_form_citation"] == 0

    # Keila Vallavolikogu IS a known issuer (Layer 1 fixture peep).
    assert cov["skip_reasons"].get("kov_citation_unknown_issuer", 0) == 0

    # Subset invariants.
    assert cov["files_processed_kov"] <= cov["files_processed"]
    assert cov["files_with_output_kov"] <= cov["files_with_output"]


def test_end_to_end_writes_interpretsLaw_and_interpretedBy(tmp_path, monkeypatch) -> None:
    krr, rk, kov = _stage_fixture(tmp_path)
    _run_script_against(krr, monkeypatch)

    # Court decision file: interpretsLaw includes the Viimsi act IRI.
    rk_doc = json.loads((rk / "riigikohus_2009_peep.json").read_text(encoding="utf-8"))
    decision = next(
        n for n in rk_doc["@graph"]
        if n["@id"] == "estleg:RK_FIXT_VIIMSI_2009"
    )
    iris = [v["@id"] for v in decision.get("estleg:interpretsLaw", [])]
    assert "estleg:Reg_1024484_Map" in iris
    # State-law TsMS provision regression check.
    assert "estleg:Tsiviilkohtumenetluse_seadustik_Par_208" in iris

    # KOV act file: interpretedBy points back to the court decision.
    viimsi_doc = json.loads(
        (kov / "viimsi_vallavolikogu" / "maarus_22_t1024484_peep.json").read_text(encoding="utf-8")
    )
    viimsi_act = next(
        n for n in viimsi_doc["@graph"]
        if n["@id"] == "estleg:Reg_1024484_Map"
    )
    by = [v["@id"] for v in viimsi_act.get("estleg:interpretedBy", [])]
    assert "estleg:RK_FIXT_VIIMSI_2009" in by

    # State-law provision file: interpretedBy regression check.
    tsms_doc = json.loads(
        (krr / "regulations" / "tsiviilkohtumenetluse_seadustik_peep.json").read_text(encoding="utf-8")
    )
    tsms_par = next(
        n for n in tsms_doc["@graph"]
        if n["@id"] == "estleg:Tsiviilkohtumenetluse_seadustik_Par_208"
    )
    assert any(
        v["@id"] == "estleg:RK_FIXT_VIIMSI_2009"
        for v in tsms_par.get("estleg:interpretedBy", [])
    )


def test_deprecated_legacy_peep_excluded_from_provision_index(
    tmp_path, monkeypatch
) -> None:
    """Issue #427: a peep whose act root carries ``owl:deprecated`` must not
    enter the provision index. Without the skip, a deprecated duplicate that
    declares the same ``estleg:sourceAct`` fans the abbreviation out across
    the legacy/canonical pair, and the deterministic cross-Part tie-break
    (lexicographic min) picks the legacy IRI whenever it sorts first — the
    exact mechanism that split Constitution case-law between
    ``estleg:PS_Par_*`` and ``estleg:eesti_vabariigi_pohiseadus_Par_*``."""
    krr, rk, kov = _stage_fixture(tmp_path)

    # Deprecated duplicate of the TsMS fixture: same sourceAct, same §208,
    # and an IRI ("Aaa_Par_208") that sorts lexicographically BEFORE the
    # canonical "Tsiviilkohtumenetluse_seadustik_Par_208" — so without the
    # #427 skip the tie-break would select it. Stamps mirror
    # scripts/deprecate_legacy_statutes.py. The stale interpretedBy seeded on
    # the provision must be cleared by the run even though the file no longer
    # receives links (clear walk 2 covers deprecated peeps).
    legacy = {
        "@context": {"estleg": "https://w3id.org/estleg/"},
        "@graph": [
            {
                "@id": "estleg:Aaa_Map",
                "@type": ["estleg:Act", "estleg:Law"],
                "owl:deprecated": True,
                "dcterms:isReplacedBy": {
                    "@id": "estleg:Tsiviilkohtumenetluse_seadustik"
                },
                "estleg:sourceAct": "Tsiviilkohtumenetluse seadustik",
            },
            {
                "@id": "estleg:Aaa_Par_208",
                "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                "estleg:sourceAct": "Tsiviilkohtumenetluse seadustik",
                "estleg:interpretedBy": [{"@id": "estleg:RK_STALE_FROM_LAST_RUN"}],
            },
        ],
    }
    legacy_path = krr / "regulations" / "aaa_legacy_tsms_peep.json"
    legacy_path.write_text(
        json.dumps(legacy, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    _run_script_against(krr, monkeypatch)

    # Citation resolves to the canonical provision, never the deprecated one.
    rk_doc = json.loads((rk / "riigikohus_2009_peep.json").read_text(encoding="utf-8"))
    decision = next(
        n for n in rk_doc["@graph"] if n["@id"] == "estleg:RK_FIXT_VIIMSI_2009"
    )
    iris = [v["@id"] for v in decision.get("estleg:interpretsLaw", [])]
    assert "estleg:Tsiviilkohtumenetluse_seadustik_Par_208" in iris
    assert "estleg:Aaa_Par_208" not in iris

    # The deprecated peep gained no interpretedBy, and its stale one is gone.
    legacy_doc = json.loads(legacy_path.read_text(encoding="utf-8"))
    assert not any(
        "estleg:interpretedBy" in n for n in legacy_doc["@graph"]
    )


# ---------------------------------------------------------------------------
# Group 4a — idempotency
# ---------------------------------------------------------------------------

VOLATILE_KEYS = (
    "run_timestamp",
    "wall_time_seconds",
    "items_per_second",
    "peak_memory_mb",
)


def _stable(cov: dict) -> dict:
    return {k: v for k, v in cov.items() if k not in VOLATILE_KEYS}


def test_two_runs_byte_identical(tmp_path, monkeypatch) -> None:
    krr, rk, kov = _stage_fixture(tmp_path)
    cov1 = _run_script_against(krr, monkeypatch)
    # Snapshot every touched file's bytes after run 1.
    bytes1 = {p: p.read_bytes() for p in krr.rglob("*_peep.json")}

    cov2 = _run_script_against(krr, monkeypatch)
    bytes2 = {p: p.read_bytes() for p in krr.rglob("*_peep.json")}

    assert _stable(cov1) == _stable(cov2), \
        "coverage report differs across runs (modulo volatile fields)"
    assert bytes1 == bytes2, \
        "peep files differ across runs (idempotency violated)"


# ---------------------------------------------------------------------------
# Group 4b — targeted SHACL widening test
# ---------------------------------------------------------------------------

def test_shacl_widened_range_validates() -> None:
    pyshacl = pytest.importorskip("pyshacl")
    rdflib = pytest.importorskip("rdflib")

    repo_root = Path(__file__).resolve().parents[1]
    shapes = rdflib.Graph()
    shapes.parse(str(repo_root / "shacl" / "estonian_legal_shapes.ttl"), format="turtle")

    # CourtDecisionShape requires caseNumber (xsd:string, minCount 1) and
    # caseType (IRI, minCount 1). Provide both so we're exercising the
    # widening, not falling over on unrelated mandatory fields.
    data_jsonld = {
        "@context": {"estleg": "https://w3id.org/estleg/",
                     "rdfs":   "http://www.w3.org/2000/01/rdf-schema#"},
        "@graph": [
            {
                "@id": "estleg:RK_TEST_WIDEN_001",
                "@type": ["estleg:CourtDecision"],
                "rdfs:label": "Test decision (widened range)",
                "estleg:caseNumber": "TEST-WIDEN-001",
                "estleg:caseType": {"@id": "estleg:CaseType_Other"},
                "estleg:interpretsLaw": [
                    {"@id": "estleg:Some_Provision_Par_5"},
                    {"@id": "estleg:Reg_1024484_Map"},
                ],
            },
            {
                "@id": "estleg:Some_Provision_Par_5",
                "@type": ["estleg:LegalProvision"],
                "rdfs:label": "Test provision",
                # LegalProvisionShape now also targets sh:targetClass
                # LegalProvision (#450), so a typed provision must carry
                # paragrahv / summary / partOfAct — this fixture is about
                # the widened interpretsLaw range, not those fields.
                "estleg:paragrahv": "§ 5",
                "estleg:summary": "Test provision summary",
                "estleg:partOfAct": {"@id": "estleg:Reg_1024484_Map"},
                "estleg:interpretedBy": [{"@id": "estleg:RK_TEST_WIDEN_001"}],
            },
            {
                "@id": "estleg:Reg_1024484_Map",
                "@type": ["estleg:MunicipalRegulation", "estleg:Act"],
                "rdfs:label": "Viimsi 2009 #22 (test)",
                # MunicipalRegulationLayer1Shape mandatory fields — provide them
                # so we're exercising the widening, not falling over on
                # unrelated mandatory fields. documentType/terviktekstId are
                # required since NationalRegulationShape also targets
                # MunicipalRegulation (#424 DomesticRegulation split).
                "estleg:enactedBy": {"@id": "estleg:Issuer_viimsi_vv_test"},
                "estleg:enactedByMunicipality": {"@id": "estleg:Municipality_EHAK_8905"},
                "estleg:titleNormalized": "viimsi 2009 #22 (test)",
                "estleg:documentType": "määrus",
                "estleg:terviktekstId": "1024484",
                "estleg:interpretedBy": [{"@id": "estleg:RK_TEST_WIDEN_001"}],
            },
            {
                # IRI must match IssuerShape sh:pattern
                # ^https://w3id.org/estleg/Issuer_[a-z0-9_]+$
                "@id": "estleg:Issuer_viimsi_vv_test",
                "@type": ["estleg:Issuer"],
                "rdfs:label": "Viimsi Vallavolikogu (test)",
                "estleg:bodyType": "volikogu",
                "estleg:currentMunicipality": {"@id": "estleg:Municipality_EHAK_8905"},
                "estleg:mappingSource": "test",
            },
            {
                # IRI must match MunicipalityShape sh:pattern
                # ^https://w3id.org/estleg/Municipality_EHAK_[0-9]{4}$
                "@id": "estleg:Municipality_EHAK_8905",
                "@type": ["estleg:Municipality"],
                "rdfs:label": "Viimsi vald (test)",
                "estleg:ehakCode": "8905",
                "estleg:county": "Harju maakond",
            },
        ],
    }
    data = rdflib.Graph()
    data.parse(data=json.dumps(data_jsonld), format="json-ld")

    conforms, _, msg = pyshacl.validate(
        data, shacl_graph=shapes, inference="rdfs", abort_on_first=False,
    )
    assert conforms, f"SHACL validation failed:\n{msg}"


def test_shacl_municipal_regulation_shape_has_interpreted_by_property() -> None:
    """Direct shapes-graph assertion that pins the new SHACL change.

    SHACL allows extra properties by default — a CourtDecision pointing at
    a MunicipalRegulation passes validation even WITHOUT the new
    MunicipalRegulationShape interpretedBy property. To pin the new shape
    explicitly, query the loaded shapes graph for a property shape on
    MunicipalRegulationShape with sh:path estleg:interpretedBy.
    """
    rdflib = pytest.importorskip("rdflib")
    SH = rdflib.Namespace("http://www.w3.org/ns/shacl#")
    ESTLEG = rdflib.Namespace("https://w3id.org/estleg/")

    repo_root = Path(__file__).resolve().parents[1]
    shapes = rdflib.Graph()
    shapes.parse(str(repo_root / "shacl" / "estonian_legal_shapes.ttl"), format="turtle")

    matched = False
    for prop_shape in shapes.objects(ESTLEG.MunicipalRegulationShape, SH.property):
        for path in shapes.objects(prop_shape, SH.path):
            if path == ESTLEG.interpretedBy:
                matched = True
                break
        if matched:
            break

    assert matched, (
        "MunicipalRegulationShape is missing the sh:path estleg:interpretedBy "
        "property shape introduced in Layer 2c PR #3."
    )


# ---------------------------------------------------------------------------
# Issue #172 regression tests
# ---------------------------------------------------------------------------


class TestIssue172BuildKovActIndexContinue:
    """Finding 1 (#172): validation failures inside build_kov_act_index
    must use ``continue`` (skip this node) rather than ``break``
    (silently drop the entire peep). A peep with a malformed early
    MunicipalRegulation node followed by a valid one MUST be indexed."""

    def test_peep_with_malformed_node_before_valid_node(
        self, tmp_path, monkeypatch
    ):
        """Stage a peep where the FIRST MunicipalRegulation node is missing
        the actNumber (validation failure → previously triggered `break`),
        and the SECOND MunicipalRegulation node is valid. Index MUST
        register the second."""
        from estleg.extract_court_provision_links import build_kov_act_index

        peep = tmp_path / "act_peep.json"
        peep.write_text(json.dumps({
            "@context": {
                "estleg": "https://w3id.org/estleg/",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                # Malformed early node — missing actNumber.
                {"@id": "estleg:Reg_BadNode_Map_2024",
                 "@type": ["estleg:Act", "estleg:MunicipalRegulation"],
                 "estleg:issuer": "Test Vallavolikogu",
                 "estleg:entryIntoForce": {"@value": "2020-01-01"},
                 # actNumber intentionally omitted
                 },
                # Valid node — must reach the index even though the
                # early one was malformed.
                {"@id": "estleg:Reg_GoodNode_Map_2024",
                 "@type": ["estleg:Act", "estleg:MunicipalRegulation"],
                 "estleg:issuer": "Test Vallavolikogu",
                 "estleg:entryIntoForce": {"@value": "2020-01-01"},
                 "estleg:actNumber": "42"},
            ],
        }), encoding="utf-8")

        def fake_iter(*, include_kov: bool = True):
            return iter([peep])

        monkeypatch.setattr(
            "estleg.extract_court_provision_links.iter_peep_files", fake_iter,
        )

        counters = _RunCounters()
        kov_index, collisions, iri_to_file, known_issuers = build_kov_act_index(counters)

        # The valid node MUST be indexed.
        assert ("test vallavolikogu", 2020, "42") in kov_index, (
            f"build_kov_act_index dropped a peep whose FIRST "
            f"MunicipalRegulation node was malformed; index keys: "
            f"{list(kov_index.keys())}"
        )
        assert kov_index[("test vallavolikogu", 2020, "42")] == "estleg:Reg_GoodNode_Map_2024"

    def test_peep_with_malformed_year_before_valid(
        self, tmp_path, monkeypatch
    ):
        """Same scenario but the early node has a malformed year
        (ValueError on int(entry_force[:4])). Must still continue."""
        from estleg.extract_court_provision_links import build_kov_act_index

        peep = tmp_path / "act_peep.json"
        peep.write_text(json.dumps({
            "@context": {
                "estleg": "https://w3id.org/estleg/",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                # Malformed early node — year not parseable as int.
                {"@id": "estleg:Reg_BadYear_Map_2024",
                 "@type": ["estleg:Act", "estleg:MunicipalRegulation"],
                 "estleg:issuer": "Test Vallavolikogu",
                 "estleg:entryIntoForce": {"@value": "abcd-01-01"},
                 "estleg:actNumber": "1"},
                # Valid node.
                {"@id": "estleg:Reg_GoodYear_Map_2024",
                 "@type": ["estleg:Act", "estleg:MunicipalRegulation"],
                 "estleg:issuer": "Test Vallavolikogu",
                 "estleg:entryIntoForce": {"@value": "2020-01-01"},
                 "estleg:actNumber": "99"},
            ],
        }), encoding="utf-8")

        def fake_iter(*, include_kov: bool = True):
            return iter([peep])

        monkeypatch.setattr(
            "estleg.extract_court_provision_links.iter_peep_files", fake_iter,
        )

        counters = _RunCounters()
        kov_index, _, _, _ = build_kov_act_index(counters)

        assert ("test vallavolikogu", 2020, "99") in kov_index, (
            f"index missed valid node after malformed year; "
            f"index keys: {list(kov_index.keys())}"
        )

    def test_peep_with_non_municipal_node_first(self, tmp_path, monkeypatch):
        """A peep with a non-MunicipalRegulation @type node FIRST,
        followed by a valid MunicipalRegulation node — the
        ``if 'estleg:MunicipalRegulation' not in types: continue``
        gate already worked in the original code, but pin it here."""
        from estleg.extract_court_provision_links import build_kov_act_index

        peep = tmp_path / "act_peep.json"
        peep.write_text(json.dumps({
            "@context": {
                "estleg": "https://w3id.org/estleg/",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                # Random ontology header.
                {"@id": "estleg:Misc_Map_2024",
                 "@type": ["owl:Ontology"]},
                # Random class.
                {"@id": "estleg:SomeClass",
                 "@type": ["owl:Class"]},
                # Then the actual MunicipalRegulation.
                {"@id": "estleg:Reg_Real_Map_2024",
                 "@type": ["estleg:Act", "estleg:MunicipalRegulation"],
                 "estleg:issuer": "Test Vallavolikogu",
                 "estleg:entryIntoForce": {"@value": "2020-01-01"},
                 "estleg:actNumber": "1"},
            ],
        }), encoding="utf-8")

        def fake_iter(*, include_kov: bool = True):
            return iter([peep])

        monkeypatch.setattr(
            "estleg.extract_court_provision_links.iter_peep_files", fake_iter,
        )

        counters = _RunCounters()
        kov_index, _, _, _ = build_kov_act_index(counters)
        assert ("test vallavolikogu", 2020, "1") in kov_index


class TestIssue172ExpandTwoDigitYear:
    """Finding 2 (#172): tokenize via re.findall(r'\\d{4}', ...);
    long-form dates with a 4-digit year tail should resolve; missing
    year bumps a counter."""

    def test_long_form_date_with_4_digit_year(self):
        assert expand_two_digit_year("18. juuni 2020") == 2020
        assert expand_two_digit_year("13. detsembri 1995") == 1995

    def test_numeric_date_with_4_digit_year(self):
        assert expand_two_digit_year("14.05.2009") == 2009

    def test_numeric_date_with_2_digit_year_high(self):
        assert expand_two_digit_year("21.06.99") == 1999

    def test_numeric_date_with_2_digit_year_low(self):
        assert expand_two_digit_year("01.01.50") == 2050

    def test_unparseable_input_bumps_counter(self):
        counters = _RunCounters()
        result = expand_two_digit_year("Lorem ipsum dolor", counters=counters)
        assert result == 0
        assert counters.skip_reasons.get("date_parse_failed", 0) >= 1

    def test_empty_input_bumps_counter(self):
        counters = _RunCounters()
        result = expand_two_digit_year("", counters=counters)
        assert result == 0
        assert counters.skip_reasons.get("date_parse_failed", 0) >= 1

    def test_no_counter_no_bump_on_empty(self):
        # Backwards compat: passing no counter must not error.
        assert expand_two_digit_year("") == 0
        assert expand_two_digit_year("garbage") == 0


class TestIssue172ExpandParRange:
    """Finding 3 (#172): wide ranges return [] with skip-reason bump;
    malformed ranges return [] (not garbage); scalar par numbers still
    expand via the digit-fallback."""

    def test_wide_range_returns_empty_and_bumps(self):
        counters = _RunCounters()
        result = _expand_par_range("100-200", counters=counters)
        assert result == []
        assert counters.skip_reasons.get("par_range_too_wide", 0) >= 1

    def test_malformed_range_returns_empty_and_bumps(self):
        counters = _RunCounters()
        result = _expand_par_range("foo-bar", counters=counters)
        assert result == []
        assert counters.skip_reasons.get("par_range_malformed", 0) >= 1

    def test_inverted_range_returns_empty(self):
        counters = _RunCounters()
        result = _expand_par_range("210-208", counters=counters)
        assert result == []
        assert counters.skip_reasons.get("par_range_too_wide", 0) >= 1

    def test_normal_range_still_expands(self):
        result = _expand_par_range("208-210")
        assert result == ["208", "209", "210"]

    def test_scalar_still_works(self):
        # Backwards-compat: pure-numeric scalar still passes.
        result = _expand_par_range("208")
        assert result == ["208"]

    def test_no_counter_does_not_error(self):
        # Backwards-compat: callers that don't pass counters still work.
        assert _expand_par_range("100-200") == []
        assert _expand_par_range("foo-bar") == []


class TestIssue172TrimMunicipality:
    """Finding 4 (#172): the {1,3} greedy quantifier in PAT_KOV_ACT can
    overcapture preceding titlecase words. The trim helper recovers
    by trying right-suffix substrings against known_issuer_norms."""

    def test_trim_finds_known_suffix(self):
        known = {"tallinna linnavolikogu", "tartu vallavolikogu"}
        result = _trim_municipality_to_known_issuer(
            "Pärnu Maakohtu Tallinna ", "linnavolikogu", known,
        )
        assert result.strip() == "Tallinna"

    def test_trim_falls_back_to_rightmost_word(self):
        # No suffix matches a known issuer — heuristic returns
        # rightmost word so the resolver still has something to try.
        known = {"unrelated entity"}
        result = _trim_municipality_to_known_issuer(
            "A B C ", "linnavolikogu", known,
        )
        assert result.strip() == "C"

    def test_trim_keeps_full_string_when_already_known(self):
        known = {"tallinna linnavolikogu"}
        result = _trim_municipality_to_known_issuer(
            "Tallinna ", "linnavolikogu", known,
        )
        assert result.strip() == "Tallinna"


class TestIssue172PatKovActLeftAnchor:
    """Finding 4 (#172): the regex now requires a left anchor (start,
    whitespace, comma, semicolon, or open paren) so it can't fire
    mid-word."""

    def test_left_anchor_rejects_mid_word_match(self):
        # Substring inside a word — no left-anchor character precedes.
        m = PAT_KOV_ACT.search(
            "xxxTallinna Linnavolikogu 18. juuni 2020. a määruse nr 15"
        )
        assert m is None

    def test_left_anchor_allows_sentence_start(self):
        m = PAT_KOV_ACT.search(
            "Tallinna Linnavolikogu 18. juuni 2020. a määruse nr 15"
        )
        assert m is not None

    def test_left_anchor_allows_after_comma(self):
        m = PAT_KOV_ACT.search(
            "vrd, Tallinna Linnavolikogu 18. juuni 2020. a määruse nr 15"
        )
        assert m is not None

    def test_left_anchor_allows_after_open_paren(self):
        m = PAT_KOV_ACT.search(
            "(Tallinna Linnavolikogu 18. juuni 2020. a määruse nr 15)"
        )
        assert m is not None

    def test_resolver_recovers_overcaptured_municipality(self):
        """When PAT_KOV_ACT overcaptures (multiple titlecase words),
        the resolver's trim step still resolves to the right issuer."""
        from estleg.extract_court_provision_links import resolve_kov_citation
        # Synthetic: regex captured "Pärnu Maakohtu Tallinna" but only
        # "tallinna linnavolikogu" is in known_issuer_norms.
        match = {
            "municipality": "Pärnu Maakohtu Tallinna ",
            "body": "Linnavolikogu",
            "date": "18. juuni 2020",
            "num": "15",
            "raw_text": "Pärnu Maakohtu Tallinna Linnavolikogu 18. juuni 2020. a määruse nr 15",
        }
        idx = {("tallinna linnavolikogu", 2020, "15"): "estleg:Reg_TLN_15_Map"}
        iri, reason, _issuer = resolve_kov_citation(
            match, idx, set(), {"tallinna linnavolikogu"},
        )
        assert iri == "estleg:Reg_TLN_15_Map", (
            f"trim should have recovered the runaway municipality; "
            f"got iri={iri}, reason={reason}"
        )
        # #389: the returned issuer is the post-trim form, not the
        # overcaptured "parnu maakohtu tallinna linnavolikogu".
        assert _issuer == "tallinna linnavolikogu"


# ---------------------------------------------------------------------------
# Regression for #121: estleg:summary on court decisions and estleg:sourceAct
# on provisions must be read through jsonld_text so a fresh-generator
# value-object corpus resolves identically to a normalized plain-string one.
# ---------------------------------------------------------------------------

def _write_provision_peep(path: Path, source_act_value) -> None:
    path.write_text(json.dumps({
        "@context": {"estleg": "https://w3id.org/estleg/",
                     "owl": "http://www.w3.org/2002/07/owl#"},
        "@graph": [
            {"@id": "estleg:Karistusseadustik_Map",
             "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"]},
            {"@id": "estleg:Karistusseadustik_Par_121",
             "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
             "estleg:sourceAct": source_act_value},
        ],
    }), encoding="utf-8")


def test_build_provision_index_handles_value_object_source_act(tmp_path, monkeypatch):
    """estleg:sourceAct as a {@value,@language} object keys
    source_act_to_prefixes by the unwrapped string, same as a plain string.

    Issue #256: source_act_to_prefixes is now one-to-many — a single-Part
    act still maps to a one-element SET ``{"Karistusseadustik"}``."""
    plain_peep = tmp_path / "kars_plain_peep.json"
    _write_provision_peep(plain_peep, "Karistusseadustik")
    vo_peep = tmp_path / "kars_vo_peep.json"
    _write_provision_peep(vo_peep, {"@value": "Karistusseadustik", "@language": "et"})

    def run(peep):
        monkeypatch.setattr(
            "estleg.extract_court_provision_links.iter_peep_files",
            lambda *, include_kov=True: [peep],
        )
        counters = _RunCounters()
        return build_provision_index(counters)

    _, plain_map, _ = run(plain_peep)
    _, vo_map, _ = run(vo_peep)

    assert plain_map == {"Karistusseadustik": {"Karistusseadustik"}}
    assert vo_map == plain_map
    # And the abbreviation bridge still resolves KarS → the prefix set.
    assert build_abbreviation_to_prefix(vo_map).get("KarS") == {"Karistusseadustik"}


def test_process_court_files_handles_value_object_summary(tmp_path, monkeypatch):
    """A court decision whose estleg:summary is a value object yields the
    same interpretsLaw / interpretedBy links as a plain-string summary."""
    from estleg import extract_court_provision_links as ecpl

    abbrev_to_prefix = {"KarS": "Karistusseadustik"}
    prefix_to_provisions = {
        "Karistusseadustik": {"121": "estleg:Karistusseadustik_Par_121"},
    }
    summary_text = "Riigikohus tugines otsuses sättele KarS § 121."

    def run(summary_value):
        rk_dir = tmp_path / f"rk_{'vo' if isinstance(summary_value, dict) else 'plain'}"
        rk_dir.mkdir()
        (rk_dir / "riigikohus_2009_peep.json").write_text(json.dumps({
            "@context": {"estleg": "https://w3id.org/estleg/",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:RK_FIXT_2009_1",
                 "@type": ["owl:NamedIndividual", "estleg:CourtDecision"],
                 "estleg:summary": summary_value},
            ],
        }), encoding="utf-8")
        monkeypatch.setattr(ecpl, "RK_DIR", rk_dir)
        counters = _RunCounters()
        result = process_court_files(
            abbrev_to_prefix,
            prefix_to_provisions,
            kov_index={},
            kov_collision_keys=set(),
            known_issuer_norms=set(),
            counters=counters,
        )
        doc = json.loads(
            (rk_dir / "riigikohus_2009_peep.json").read_text(encoding="utf-8")
        )
        decision = doc["@graph"][0]
        iris = [v["@id"] for v in decision.get("estleg:interpretsLaw", [])]
        return result, iris

    plain_result, plain_iris = run(summary_text)
    vo_result, vo_iris = run({"@value": summary_text, "@language": "et"})

    assert plain_result.state_link_count == 1
    assert plain_iris == ["estleg:Karistusseadustik_Par_121"]
    assert vo_result.state_link_count == plain_result.state_link_count
    assert vo_iris == plain_iris
    assert dict(vo_result.interpreted_by) == dict(plain_result.interpreted_by)


def test_process_court_files_handles_value_object_legal_text(tmp_path, monkeypatch):
    from estleg import extract_court_provision_links as ecpl

    abbrev_to_prefix = {"KarS": "Karistusseadustik"}
    prefix_to_provisions = {
        "Karistusseadustik": {
            "121": "estleg:Karistusseadustik_Par_121",
            "122": "estleg:Karistusseadustik_Par_122",
        },
    }
    legal_text = "Täistekst lahendab asja KarS § 121 ja KarS § 122 alusel."

    def run(legal_text_value):
        rk_dir = tmp_path / f"rk_legal_text_{'vo' if isinstance(legal_text_value, dict) else 'plain'}"
        rk_dir.mkdir()
        (rk_dir / "riigikohus_2026_peep.json").write_text(
            json.dumps({
                "@context": {"estleg": "https://w3id.org/estleg/"},
                "@graph": [
                    {
                        "@id": "estleg:RK_LEGAL_TEXT_1",
                        "@type": ["owl:NamedIndividual", "estleg:CourtDecision"],
                        "estleg:legalText": legal_text_value,
                    },
                ],
            }),
            encoding="utf-8",
        )
        monkeypatch.setattr(ecpl, "RK_DIR", rk_dir)
        result = process_court_files(
            abbrev_to_prefix,
            prefix_to_provisions,
            kov_index={},
            kov_collision_keys=set(),
            known_issuer_norms=set(),
            counters=_RunCounters(),
        )
        doc = json.loads(
            (rk_dir / "riigikohus_2026_peep.json").read_text(encoding="utf-8")
        )
        decision = doc["@graph"][0]
        iris = [v["@id"] for v in decision.get("estleg:interpretsLaw", [])]
        return result, iris

    plain_result, plain_iris = run(legal_text)
    vo_result, vo_iris = run({"@value": legal_text, "@language": "et"})

    assert plain_iris == [
        "estleg:Karistusseadustik_Par_121",
        "estleg:Karistusseadustik_Par_122",
    ]
    assert vo_iris == plain_iris
    assert plain_result.state_link_count == 2
    assert vo_result.state_link_count == plain_result.state_link_count
    stats = vo_result.per_file_stats[0]
    assert stats["decisions_with_full_text"] == 1
    assert stats["legalText_citations_found"] == 2
    assert stats["summary_fallback_citations_found"] == 0


def test_process_court_files_prefers_legal_text_over_summary(
    tmp_path, monkeypatch
):
    from estleg import extract_court_provision_links as ecpl

    abbrev_to_prefix = {"KarS": "Karistusseadustik"}
    prefix_to_provisions = {
        "Karistusseadustik": {
            "121": "estleg:Karistusseadustik_Par_121",
            "122": "estleg:Karistusseadustik_Par_122",
        },
    }
    rk_dir = tmp_path / "rk_full_text"
    rk_dir.mkdir()
    (rk_dir / "riigikohus_2026_peep.json").write_text(
        json.dumps({
            "@context": {"estleg": "https://w3id.org/estleg/"},
            "@graph": [
                {
                    "@id": "estleg:RK_FULL_TEXT_1",
                    "@type": ["owl:NamedIndividual", "estleg:CourtDecision"],
                    "estleg:summary": "Kokkuvõte mainib KarS § 121.",
                    "estleg:legalText": "Täistekst lahendab asja KarS § 121 ja KarS § 122 alusel.",
                },
            ],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(ecpl, "RK_DIR", rk_dir)

    result = process_court_files(
        abbrev_to_prefix,
        prefix_to_provisions,
        kov_index={},
        kov_collision_keys=set(),
        known_issuer_norms=set(),
        counters=_RunCounters(),
    )

    doc = json.loads(
        (rk_dir / "riigikohus_2026_peep.json").read_text(encoding="utf-8")
    )
    decision = doc["@graph"][0]
    assert decision["estleg:interpretsLaw"] == [
        {"@id": "estleg:Karistusseadustik_Par_121"},
        {"@id": "estleg:Karistusseadustik_Par_122"},
    ]
    stats = result.per_file_stats[0]
    assert stats["decisions_with_full_text"] == 1
    assert stats["legalText_citations_found"] == 2
    assert stats["summary_fallback_citations_found"] == 0
    assert stats["summary_baseline_citations_resolved"] == 1
    assert stats["full_text_recall_lift"] == 1


def test_process_court_files_splits_mixed_full_text_and_summary_only(
    tmp_path, monkeypatch
):
    from estleg import extract_court_provision_links as ecpl

    abbrev_to_prefix = {"KarS": "Karistusseadustik"}
    prefix_to_provisions = {
        "Karistusseadustik": {
            "121": "estleg:Karistusseadustik_Par_121",
            "122": "estleg:Karistusseadustik_Par_122",
        },
    }
    rk_dir = tmp_path / "rk_mixed_sources"
    rk_dir.mkdir()
    (rk_dir / "riigikohus_2026_peep.json").write_text(
        json.dumps({
            "@context": {"estleg": "https://w3id.org/estleg/"},
            "@graph": [
                {
                    "@id": "estleg:RK_FULL_TEXT_1",
                    "@type": ["owl:NamedIndividual", "estleg:CourtDecision"],
                    "estleg:summary": "Kokkuvõte mainib KarS § 121.",
                    "estleg:legalText": "Täistekst mainib KarS § 121 ja KarS § 122.",
                },
                {
                    "@id": "estleg:RK_SUMMARY_1",
                    "@type": ["owl:NamedIndividual", "estleg:CourtDecision"],
                    "estleg:summary": "Kokkuvõte tugineb KarS § 121 sättele.",
                },
                {
                    "@id": "estleg:RK_WHITESPACE_SUMMARY",
                    "@type": ["owl:NamedIndividual", "estleg:CourtDecision"],
                    "estleg:summary": "   ",
                    "estleg:legalText": "Täistekst tugineb KarS § 121 sättele.",
                },
            ],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(ecpl, "RK_DIR", rk_dir)

    result = process_court_files(
        abbrev_to_prefix,
        prefix_to_provisions,
        kov_index={},
        kov_collision_keys=set(),
        known_issuer_norms=set(),
        counters=_RunCounters(),
    )

    stats = result.per_file_stats[0]
    assert stats["decisions_scanned"] == 3
    assert stats["decisions_with_full_text"] == 2
    assert stats["decisions_with_citations"] == 3
    assert stats["legalText_citations_found"] == 3
    assert stats["summary_fallback_citations_found"] == 1
    assert stats["decisions_with_summary_baseline"] == 2
    assert stats["summary_baseline_citations_resolved"] == 2
    assert stats["state_citations_resolved"] == 4
    assert stats["full_text_recall_lift"] == 2


# ---------------------------------------------------------------------------
# Issue #256 regression: multi-Part laws must not collapse to one prefix.
#
# Large codes are split across per-Part peep files that SHARE one
# estleg:sourceAct but use DIFFERENT IRI prefixes (e.g. KARIST_2_Osa1
# General Part vs KARIST_2_Osa2 Special Part, both sourceAct
# "Karistusseadustik"). The old last-writer-wins
# ``source_act_to_prefix[source_act] = file_prefix`` kept only the
# last-iterated Part, so resolve_citations could only reach provisions in
# that one Part and silently dropped citations to every other Part.
#
# These fixtures use prefix != source-act name (UNLIKE
# _write_provision_peep, where prefix == full name) so they actually
# reproduce the collapse.
# ---------------------------------------------------------------------------


def _write_part_peep(
    path: Path,
    prefix: str,
    source_act,
    par_numbers: list[str],
) -> None:
    """Write a single-Part peep: IRI prefix ``prefix`` (deliberately NOT the
    source-act name) with one LegalProvision node per § in ``par_numbers``,
    each carrying ``estleg:sourceAct`` so several Parts share one act."""
    graph: list[dict] = [
        {"@id": f"estleg:{prefix}_Map",
         "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"]},
    ]
    for par in par_numbers:
        graph.append({
            "@id": f"estleg:{prefix}_Par_{par}",
            "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
            "estleg:sourceAct": source_act,
        })
    path.write_text(json.dumps({
        "@context": {"estleg": "https://w3id.org/estleg/",
                     "owl": "http://www.w3.org/2002/07/owl#"},
        "@graph": graph,
    }), encoding="utf-8")


class TestIssue256MultiPartLaws:
    """Two peep fixtures share one sourceAct but use distinct prefixes
    (X_Osa1 / X_Osa2). A citation to a § living in the NON-LAST Part must
    still resolve — the headline regression."""

    def _stage_two_parts(self, tmp_path):
        """KarS split into General Part (Osa1, §§ 1/121) and Special Part
        (Osa2, §§ 200/263), both sourceAct 'Karistusseadustik'. Returns the
        files in [Osa1, Osa2] order so Osa2 is the 'last writer' — under
        the old code Osa1's §§ would be the ones dropped."""
        osa1 = tmp_path / "karist_2_osa1_peep.json"
        osa2 = tmp_path / "karist_2_osa2_peep.json"
        _write_part_peep(osa1, "KARIST_2_Osa1", "Karistusseadustik", ["1", "121"])
        _write_part_peep(osa2, "KARIST_2_Osa2", "Karistusseadustik", ["200", "263"])
        return [osa1, osa2]

    def test_index_retains_both_part_prefixes(self, tmp_path, monkeypatch):
        files = self._stage_two_parts(tmp_path)
        monkeypatch.setattr(
            "estleg.extract_court_provision_links.iter_peep_files",
            lambda *, include_kov=True: iter(files),
        )
        counters = _RunCounters()
        prefix_to_provisions, source_act_to_prefixes, _ = build_provision_index(
            counters
        )

        # One-to-many: BOTH Part prefixes are retained (old code kept one).
        assert source_act_to_prefixes == {
            "Karistusseadustik": {"KARIST_2_Osa1", "KARIST_2_Osa2"}
        }
        assert set(prefix_to_provisions["KARIST_2_Osa1"]) == {"1", "121"}
        assert set(prefix_to_provisions["KARIST_2_Osa2"]) == {"200", "263"}

    def test_abbrev_maps_to_union_of_prefixes(self, tmp_path, monkeypatch):
        files = self._stage_two_parts(tmp_path)
        monkeypatch.setattr(
            "estleg.extract_court_provision_links.iter_peep_files",
            lambda *, include_kov=True: iter(files),
        )
        _, source_act_to_prefixes, _ = build_provision_index(_RunCounters())
        abbrev_to_prefixes = build_abbreviation_to_prefix(source_act_to_prefixes)
        assert abbrev_to_prefixes["KarS"] == {"KARIST_2_Osa1", "KARIST_2_Osa2"}

    def test_citation_to_non_last_part_resolves(self, tmp_path, monkeypatch):
        """THE bug: § 121 lives in Osa1 (the NON-last Part). Under the old
        last-writer-wins map KarS pointed only at Osa2, so § 121 silently
        dropped. It must now resolve to the Osa1 IRI."""
        files = self._stage_two_parts(tmp_path)
        monkeypatch.setattr(
            "estleg.extract_court_provision_links.iter_peep_files",
            lambda *, include_kov=True: iter(files),
        )
        prefix_to_provisions, source_act_to_prefixes, _ = build_provision_index(
            _RunCounters()
        )
        abbrev_to_prefixes = build_abbreviation_to_prefix(source_act_to_prefixes)

        # § 121 (General Part, non-last) resolves.
        resolved_general = resolve_citations(
            [{"law_ref": "KarS", "paragraphs": ["121"]}],
            abbrev_to_prefixes,
            prefix_to_provisions,
        )
        assert resolved_general == ["estleg:KARIST_2_Osa1_Par_121"]

        # § 200 (Special Part, last) still resolves too.
        resolved_special = resolve_citations(
            [{"law_ref": "KarS", "paragraphs": ["200"]}],
            abbrev_to_prefixes,
            prefix_to_provisions,
        )
        assert resolved_special == ["estleg:KARIST_2_Osa2_Par_200"]

        # A citation spanning both Parts resolves to both IRIs.
        resolved_both = resolve_citations(
            [{"law_ref": "KarS", "paragraphs": ["121", "200"]}],
            abbrev_to_prefixes,
            prefix_to_provisions,
        )
        assert resolved_both == [
            "estleg:KARIST_2_Osa1_Par_121",
            "estleg:KARIST_2_Osa2_Par_200",
        ]

    def test_end_to_end_court_decision_links_general_part(
        self, tmp_path, monkeypatch
    ):
        """A court decision citing KarS § 121 (General Part) gets an
        interpretsLaw arc to the Osa1 provision — end-to-end through
        process_court_files, not just the resolver."""
        from estleg import extract_court_provision_links as ecpl

        files = self._stage_two_parts(tmp_path)
        monkeypatch.setattr(
            ecpl, "iter_peep_files",
            lambda *, include_kov=True: iter(files),
        )
        prefix_to_provisions, source_act_to_prefixes, _ = build_provision_index(
            _RunCounters()
        )
        abbrev_to_prefixes = build_abbreviation_to_prefix(source_act_to_prefixes)

        rk_dir = tmp_path / "rk"
        rk_dir.mkdir()
        (rk_dir / "riigikohus_2026_peep.json").write_text(json.dumps({
            "@context": {"estleg": "https://w3id.org/estleg/",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:RK_FIXT_KARS_GENERAL",
                 "@type": ["owl:NamedIndividual", "estleg:CourtDecision"],
                 "estleg:legalText": "Süüpõhimõte tuleneb KarS § 121 sättest."},
            ],
        }), encoding="utf-8")
        monkeypatch.setattr(ecpl, "RK_DIR", rk_dir)

        result = process_court_files(
            abbrev_to_prefixes,
            prefix_to_provisions,
            kov_index={},
            kov_collision_keys=set(),
            known_issuer_norms=set(),
            counters=_RunCounters(),
        )

        assert result.state_link_count == 1
        doc = json.loads(
            (rk_dir / "riigikohus_2026_peep.json").read_text(encoding="utf-8")
        )
        decision = doc["@graph"][0]
        iris = [v["@id"] for v in decision.get("estleg:interpretsLaw", [])]
        assert iris == ["estleg:KARIST_2_Osa1_Par_121"]
        # And the inverse map points the General-Part provision back.
        assert result.interpreted_by["estleg:KARIST_2_Osa1_Par_121"] == [
            "estleg:RK_FIXT_KARS_GENERAL"
        ]

    def test_cross_part_same_par_resolves_deterministically(
        self, tmp_path, monkeypatch
    ):
        """When the SAME § exists in two Parts (distinct IRIs) the resolver
        picks deterministically (smallest IRI) and surfaces the anomaly via
        the cross_part_par_collision counter rather than dropping it."""
        osa1 = tmp_path / "karist_2_osa1_peep.json"
        osa2 = tmp_path / "karist_2_osa2_peep.json"
        # § 5 deliberately present in BOTH Parts.
        _write_part_peep(osa1, "KARIST_2_Osa1", "Karistusseadustik", ["5"])
        _write_part_peep(osa2, "KARIST_2_Osa2", "Karistusseadustik", ["5"])
        files = [osa1, osa2]
        monkeypatch.setattr(
            "estleg.extract_court_provision_links.iter_peep_files",
            lambda *, include_kov=True: iter(files),
        )
        prefix_to_provisions, source_act_to_prefixes, _ = build_provision_index(
            _RunCounters()
        )
        abbrev_to_prefixes = build_abbreviation_to_prefix(source_act_to_prefixes)

        counters = _RunCounters()
        resolved = resolve_citations(
            [{"law_ref": "KarS", "paragraphs": ["5"]}],
            abbrev_to_prefixes,
            prefix_to_provisions,
            counters=counters,
        )
        # Deterministic: min() of the two candidate IRIs.
        assert resolved == [
            min(
                "estleg:KARIST_2_Osa1_Par_5",
                "estleg:KARIST_2_Osa2_Par_5",
            )
        ]
        # Anomaly surfaced (not silently dropped).
        assert counters.skip_reasons.get("cross_part_par_collision", 0) == 1

    def test_resolve_citations_accepts_plain_str_mapping(self):
        """Backward compatibility: resolve_citations still accepts a plain
        ``dict[str, str]`` (single prefix) value, not only a set."""
        prefix_to_provisions = {
            "Karistusseadustik": {"121": "estleg:Karistusseadustik_Par_121"},
        }
        resolved = resolve_citations(
            [{"law_ref": "KarS", "paragraphs": ["121"]}],
            {"KarS": "Karistusseadustik"},   # plain str value, legacy shape
            prefix_to_provisions,
        )
        assert resolved == ["estleg:Karistusseadustik_Par_121"]


class TestIssue602MonthsAndYearPivot:
    """#602 — the Estonian month table is hoisted to one shared source, and
    the 2-digit-year century pivot is a named constant."""

    def test_kov_act_regex_uses_shared_month_table(self):
        from estleg import estleg_common
        from estleg import extract_court_provision_links as cpl

        # Every shared genitive month form must match in the KOV act pattern.
        for month in estleg_common.ESTONIAN_MONTHS_GENITIVE:
            text = f" Tallinna Linnavolikogu 12. {month} 2019 määrus nr 5"
            assert cpl.PAT_KOV_ACT.search(text), month

    def test_two_digit_year_pivot_is_named(self):
        from estleg import extract_court_provision_links as cpl

        assert cpl.TWO_DIGIT_YEAR_PIVOT == 50
        # Boundary behaviour: <= pivot → 2000s, > pivot → 1900s.
        assert cpl.expand_two_digit_year("01.01.09") == 2009
        assert cpl.expand_two_digit_year("01.01.50") == 2050
        assert cpl.expand_two_digit_year("01.01.51") == 1951
        assert cpl.expand_two_digit_year("01.01.95") == 1995

    def test_four_digit_year_unchanged(self):
        from estleg import extract_court_provision_links as cpl

        assert cpl.expand_two_digit_year("18. juuni 2019") == 2019
        assert cpl.expand_two_digit_year("14.05.2009") == 2009

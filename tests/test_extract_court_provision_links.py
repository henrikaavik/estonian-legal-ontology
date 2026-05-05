"""Tests for extract_court_provision_links.py — Layer 2c PR #3."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from estleg_common import _RunCounters

from extract_court_provision_links import (
    PAT_KOV_ACT,
    PAT_RTIV,
    build_kov_act_index,
    expand_two_digit_year,
    extract_citations_from_text,
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
        "extract_court_provision_links.iter_peep_files", fake_iter
    )

    counters = _RunCounters()
    kov_index, collisions, iri_to_file, known_issuers = build_kov_act_index(counters)

    assert ("viimsi vallavolikogu", 2009, "22") in kov_index
    assert kov_index[("viimsi vallavolikogu", 2009, "22")] == "estleg:Reg_1024484_Map_2026"
    assert "viimsi vallavolikogu" in known_issuers
    assert collisions == set()


def test_build_kov_index_detects_collisions(monkeypatch) -> None:
    fixture_files = list((FIXTURE_KOV / "collision_pair").glob("*_peep.json"))

    def fake_iter(*, include_kov: bool = True):
        return iter(fixture_files)

    monkeypatch.setattr(
        "extract_court_provision_links.iter_peep_files", fake_iter
    )

    counters = _RunCounters()
    kov_index, collisions, iri_to_file, known_issuers = build_kov_act_index(counters)

    key = ("test vallavolikogu", 2016, "5")
    assert key in collisions
    assert key not in kov_index            # removed on collision detection
    # Both files still recorded in iri_to_file (cleanup pass needs them)
    assert "estleg:Reg_2000001_Map_2026" in iri_to_file
    assert "estleg:Reg_2000002_Map_2026" in iri_to_file


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
    idx = {("viimsi vallavolikogu", 2009, "22"): "estleg:Reg_1024484_Map_2026"}
    iri, reason = resolve_kov_citation(_kc(), idx, set(), {"viimsi vallavolikogu"})
    assert iri == "estleg:Reg_1024484_Map_2026"
    assert reason is None


def test_resolver_year_plus_one_alternate() -> None:
    # Index has 2010 entry; query year 2009 resolves via +1 alternate.
    idx = {("tallinna linnavalitsus", 2010, "75"): "estleg:Reg_1014396_Map_2026"}
    iri, reason = resolve_kov_citation(
        _kc(municipality="Tallinna ", body="Linnavalitsus", date="14.05.2009", num="75"),
        idx, set(), {"tallinna linnavalitsus"},
    )
    assert iri == "estleg:Reg_1014396_Map_2026"
    assert reason is None


def test_resolver_ambiguous_primary_short_circuits() -> None:
    collisions = {("viimsi vallavolikogu", 2009, "22")}
    iri, reason = resolve_kov_citation(_kc(), {}, collisions, {"viimsi vallavolikogu"})
    assert iri is None
    assert reason == "ambiguous_key"


def test_resolver_ambiguous_alternate_short_circuits() -> None:
    # Primary 2009 missing; alternate 2010 collision-tracked.
    collisions = {("viimsi vallavolikogu", 2010, "22")}
    iri, reason = resolve_kov_citation(_kc(), {}, collisions, {"viimsi vallavolikogu"})
    assert iri is None
    assert reason == "ambiguous_key"


def test_resolver_unmatched() -> None:
    iri, reason = resolve_kov_citation(_kc(), {}, set(), {"viimsi vallavolikogu"})
    assert iri is None
    assert reason == "issuer_year_num_unmatched"


def test_resolver_unknown_issuer_short_circuits() -> None:
    # Issuer NOT in known_issuer_norms → unknown_issuer, no index lookup.
    iri, reason = resolve_kov_citation(_kc(), {}, set(), set())
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


def _run_script_against(krr: Path, monkeypatch) -> dict:
    """Run main() with the fixture as the active KRR root; return coverage dict."""
    import extract_court_provision_links as ecpl

    monkeypatch.setattr(ecpl, "KRR_DIR", krr)
    monkeypatch.setattr(ecpl, "RK_DIR", krr / "riigikohus")

    # Override iter_peep_files to walk the fixture's regulations directory.
    def fake_iter(*, include_kov: bool = True):
        files = list((krr / "regulations").rglob("*_peep.json"))
        if not include_kov:
            files = [f for f in files if "kov" not in f.parts]
        return files

    monkeypatch.setattr(ecpl, "iter_peep_files", fake_iter)

    ecpl.main()
    cov_path = krr / "reports" / "kov" / "court_provision_links_coverage.json"
    return json.loads(cov_path.read_text(encoding="utf-8"))


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
    assert "estleg:Reg_1024484_Map_2026" in iris
    # State-law TsMS provision regression check.
    assert "estleg:Tsiviilkohtumenetluse_seadustik_Par_208" in iris

    # KOV act file: interpretedBy points back to the court decision.
    viimsi_doc = json.loads(
        (kov / "viimsi_vallavolikogu" / "maarus_22_t1024484_peep.json").read_text(encoding="utf-8")
    )
    viimsi_act = next(
        n for n in viimsi_doc["@graph"]
        if n["@id"] == "estleg:Reg_1024484_Map_2026"
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

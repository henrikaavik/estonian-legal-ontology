"""Tests for extract_temporal_data — recursive XML discovery + globaalID pairing."""
from __future__ import annotations

import ast
import inspect
import json
from datetime import date as date_cls
from datetime import timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "kov_layer2a"
PEEP_FIXTURE = FIXTURE_DIR / "sample_kov_act.json"
XML_FIXTURE = FIXTURE_DIR / "sample_kov_xml.xml"


class TestBuildGlobalIdLookup:
    def test_recursive_xml_discovery(self, tmp_path):
        from estleg.extract_temporal_data import build_globalid_xml_lookup

        # Stage three XML files in subdirectories to mimic the real layout
        rt = tmp_path / "data" / "riigiteataja"
        (rt / "maarus").mkdir(parents=True)
        (rt / "maarus_kov").mkdir(parents=True)
        (rt / "law_alpha.xml").write_text(
            '<akt globaalID="111"><metaandmed></metaandmed></akt>',
            encoding="utf-8",
        )
        (rt / "maarus" / "reg_222.xml").write_text(
            '<akt globaalID="222"><metaandmed></metaandmed></akt>',
            encoding="utf-8",
        )
        (rt / "maarus_kov" / "reg_333.xml").write_text(
            '<akt globaalID="333"><metaandmed></metaandmed></akt>',
            encoding="utf-8",
        )

        lookup = build_globalid_xml_lookup(rt)
        assert lookup["111"].name == "law_alpha.xml"
        assert lookup["222"].name == "reg_222.xml"
        assert lookup["333"].name == "reg_333.xml"

    def test_kov_xml_in_maarus_kov_subdir(self, tmp_path):
        """KOV XML lives at data/riigiteataja/maarus_kov/reg_<gid>.xml;
        the recursive walk must find it."""
        from estleg.extract_temporal_data import build_globalid_xml_lookup
        rt = tmp_path / "data" / "riigiteataja"
        (rt / "maarus_kov").mkdir(parents=True)
        # Use the kov fixture's globalId
        (rt / "maarus_kov" / "reg_999999999.xml").write_text(
            '<akt globaalID="999999999"><metaandmed>'
            '<vastuvotjaLiik>Tallinna Linnavolikogu</vastuvotjaLiik>'
            '</metaandmed></akt>',
            encoding="utf-8",
        )
        lookup = build_globalid_xml_lookup(rt)
        assert "999999999" in lookup
        assert "maarus_kov" in str(lookup["999999999"])


class TestPublicationYearFallback:
    def test_parse_rt_year_accepts_plain_year_and_timezone_offsets(self):
        from estleg.extract_temporal_data import parse_rt_year

        assert parse_rt_year("2012") == "2012"
        assert parse_rt_year("2012+02:00") == "2012"
        assert parse_rt_year("2012+03:00") == "2012"

    @pytest.mark.parametrize("value", ["", "12", "201", "20122", "abcd", "2012-01"])
    def test_parse_rt_year_rejects_malformed_years(self, value):
        from estleg.extract_temporal_data import parse_rt_year

        assert parse_rt_year(value) is None

    def test_extract_temporal_rtaasta_fallback_is_year_precision(self, tmp_path):
        # #571: a year-only record (RTaasta, no avaldamineKuupaev) must NOT get a
        # fabricated YYYY-01-01 publicationDate — only a year-precision
        # publication_year (emitted as estleg:publicationYear, xsd:gYear).
        from estleg.extract_temporal_data import extract_temporal_from_xml

        xml_path = tmp_path / "rt.xml"
        xml_path.write_text(
            "<akt><metaandmed><avaldamismarge>"
            "<RTaasta>2012+02:00</RTaasta>"
            "</avaldamismarge></metaandmed></akt>",
            encoding="utf-8",
        )

        temporal = extract_temporal_from_xml(xml_path)

        assert temporal["publication_date"] is None
        assert temporal["publication_year"] == "2012"

    def test_extract_temporal_reads_real_avaldamine_tag(self, tmp_path):
        # #571 regression: the real RT tag is "avaldamineKuupaev" (with "ne") —
        # a precise publication date must be read, not the year-only fallback.
        from estleg.extract_temporal_data import extract_temporal_from_xml

        xml_path = tmp_path / "rt.xml"
        xml_path.write_text(
            "<akt><metaandmed>"
            "<avaldamineKuupaev>2019-07-12</avaldamineKuupaev>"
            "<avaldamismarge><RTaasta>2019</RTaasta></avaldamismarge>"
            "</metaandmed></akt>",
            encoding="utf-8",
        )

        temporal = extract_temporal_from_xml(xml_path)

        assert temporal["publication_date"] == "2019-07-12"
        assert temporal["publication_year"] is None

    def test_clear_temporal_keys_scrubs_publication_year(self):
        # #571 (review): publicationYear must be in TEMPORAL_KEYS_TO_CLEAR so a
        # re-derivation can't leave a stale year on a record that later gains an
        # exact publicationDate or loses publication metadata.
        from estleg.extract_temporal_data import clear_temporal_keys

        graph = [{"@id": "estleg:A", "@type": ["estleg:Act"],
                  "estleg:publicationYear": {"@value": "2012", "@type": "xsd:gYear"}}]
        assert clear_temporal_keys(graph) is True
        assert "estleg:publicationYear" not in graph[0]

    def test_index_fallback_reads_real_avaldamine_tag(self):
        # #571 (review): the INDEX fallback map must also use the real tag
        # "avaldamineKuupaev" — otherwise fallback records lose publication_date.
        from estleg.extract_temporal_data import _index_to_temporal

        assert _index_to_temporal({"avaldamineKuupaev": "2020-03-04"})["publication_date"] == "2020-03-04"


class TestTemporalStatusEvaluationDate:
    def test_evaluation_date_controls_not_yet_effective_status(self):
        from estleg.extract_temporal_data import determine_temporal_status

        temporal = {"entry_into_force": "2026-06-01"}

        assert determine_temporal_status(temporal, evaluation_date="2026-05-01") == "notYetEffective"
        assert determine_temporal_status(temporal, evaluation_date="2026-06-01") == "inForce"

    def test_evaluation_date_controls_repealed_status(self):
        from estleg.extract_temporal_data import determine_temporal_status

        temporal = {"valid_until": "2026-05-15"}

        assert determine_temporal_status(temporal, evaluation_date="2026-05-14") == "inForce"
        assert determine_temporal_status(temporal, evaluation_date="2026-05-15") == "repealed"

    def test_default_evaluation_date_is_build_pin_not_wall_clock(self):
        """#470: omitted evaluation_date uses BUILD_EVALUATION_DATE, not date.today()."""
        from estleg.estleg_common import BUILD_EVALUATION_DATE
        from estleg.extract_temporal_data import determine_temporal_status

        pin = date_cls.fromisoformat(BUILD_EVALUATION_DATE)
        after_pin = (pin + timedelta(days=1)).isoformat()
        temporal = {"entry_into_force": after_pin}

        assert determine_temporal_status(temporal) == "notYetEffective"
        assert determine_temporal_status(temporal, evaluation_date=None) == "notYetEffective"
        # Explicit pin matches the implicit default.
        assert determine_temporal_status(
            temporal, evaluation_date=BUILD_EVALUATION_DATE
        ) == "notYetEffective"


class TestNoDateTodayEvaluationFallback:
    """#470: standalone runs must not default evaluation to wall-clock today()."""

    def test_module_does_not_call_date_today(self):
        from estleg import extract_temporal_data as mod

        tree = ast.parse(inspect.getsource(mod))
        today_calls = [
            ast.unparse(node)
            for node in ast.walk(tree)
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "today"
            )
        ]
        assert today_calls == [], (
            f"extract_temporal_data must not call date.today() for the "
            f"evaluation/default date (#470); found {today_calls}"
        )
        status_src = inspect.getsource(mod.determine_temporal_status)
        main_src = inspect.getsource(mod.main)
        assert "BUILD_EVALUATION_DATE" in status_src
        assert "BUILD_EVALUATION_DATE" in main_src
        assert "date.today" not in status_src

    def test_main_defaults_evaluation_date_to_build_pin(
        self, tmp_path, monkeypatch
    ):
        from estleg.estleg_common import BUILD_EVALUATION_DATE

        act, report = _run_main_over_single_peep(
            tmp_path, monkeypatch,
            {"joustumine": "2010-01-01"},
            evaluation_date=None,
        )
        assert report["evaluationDate"] == BUILD_EVALUATION_DATE
        assert act.get("estleg:temporalStatus") == "inForce"


class TestPairPeepWithXml:
    def test_pairs_via_globalid_attribute(self, tmp_path):
        from estleg.extract_temporal_data import pair_peep_with_xml

        peep = tmp_path / "act.json"
        peep.write_text(json.dumps({
            "@context": {"estleg": "https://w3id.org/estleg/"},
            "@graph": [
                {"@id": "estleg:Reg_9999_Map_2026",
                 "estleg:globalId": "999",
                 "@type": ["estleg:MunicipalRegulation"]}
            ],
        }), encoding="utf-8")

        xml = tmp_path / "reg_999.xml"
        xml.write_text(
            '<akt globaalID="999"><metaandmed></metaandmed></akt>',
            encoding="utf-8",
        )

        lookup = {"999": xml}
        result = pair_peep_with_xml(peep, lookup)
        assert result == xml

    def test_unpaired_returns_none(self, tmp_path):
        from estleg.extract_temporal_data import pair_peep_with_xml
        peep = tmp_path / "act.json"
        peep.write_text(json.dumps({
            "@context": {"estleg": "https://w3id.org/estleg/"},
            "@graph": [
                {"@id": "estleg:Reg_X",
                 "estleg:globalId": "missing-id",
                 "@type": ["estleg:Law"]}
            ],
        }), encoding="utf-8")
        assert pair_peep_with_xml(peep, lookup={}) is None

    def test_slug_fallback_for_law_without_globalid(self, tmp_path):
        """The 615 indexed laws predate the estleg:globalId field — their
        act nodes carry estleg:Law but no globalId. Without a slug
        fallback, the new pairing would silently drop XML for every
        law, regressing existing temporal/amendment-history coverage.
        """
        from estleg.extract_temporal_data import pair_peep_with_xml

        # Stage a law peep file with NO estleg:globalId
        rt = tmp_path / "data" / "riigiteataja"
        rt.mkdir(parents=True)
        krr = tmp_path / "krr_outputs"
        krr.mkdir()

        peep = krr / "alkoholiseadus_peep.json"
        peep.write_text(json.dumps({
            "@context": {"estleg": "https://w3id.org/estleg/",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:AlkS_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
                 "rdfs:label": "Alkoholiseadus"}
                # NO estleg:globalId — matches real law peep shape
            ],
        }), encoding="utf-8")
        # Slug-named XML at the data root (laws are at the top level,
        # NOT under maarus/ or maarus_kov/)
        (rt / "alkoholiseadus.xml").write_text(
            '<akt><metaandmed></metaandmed></akt>',
            encoding="utf-8",
        )

        # globalId path returns None (no globalId on the act);
        # slug path resolves alkoholiseadus_peep -> alkoholiseadus.xml.
        result = pair_peep_with_xml(peep, lookup={}, data_dir=rt)
        assert result is not None
        assert result.name == "alkoholiseadus.xml"

    def test_slug_fallback_handles_osa_suffix(self, tmp_path):
        """Multi-part laws like asjaoigusseadus_osa1 share their
        XML with the base slug (asjaoigusseadus.xml). The slug
        fallback strips _osaN before re-resolving."""
        from estleg.extract_temporal_data import pair_peep_with_xml

        rt = tmp_path / "data" / "riigiteataja"
        rt.mkdir(parents=True)
        krr = tmp_path / "krr_outputs"
        krr.mkdir()

        peep = krr / "asjaoigusseadus_osa3_peep.json"
        peep.write_text(json.dumps({
            "@context": {"estleg": "https://w3id.org/estleg/",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:AOS_Osa3_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Law"]}
            ],
        }), encoding="utf-8")
        # XML lives at base-slug name only (no per-osa XML)
        (rt / "asjaoigusseadus.xml").write_text(
            '<akt><metaandmed></metaandmed></akt>',
            encoding="utf-8",
        )

        result = pair_peep_with_xml(peep, lookup={}, data_dir=rt)
        assert result is not None
        assert result.name == "asjaoigusseadus.xml"


class TestMainUsesGlobalIdLookup:
    """Pin the contract that main() actually wires the new helpers.
    The helper unit tests above pass even if main() still uses the old
    slug-based pairing (the helpers exist and work in isolation but the
    pipeline doesn't call them). This test runs main() with monkeypatched
    paths over a tiny KOV-like fixture and asserts that an XML file under
    maarus_kov/ paired by globalId actually contributed temporal data
    to the corresponding peep file.
    """

    def test_main_pairs_kov_via_globalid(self, tmp_path, monkeypatch):
        from estleg import estleg_common
        from estleg import extract_temporal_data as mod

        # Stage a tiny tree
        krr = tmp_path / "krr_outputs"
        rt = tmp_path / "data" / "riigiteataja"
        kov = krr / "regulations" / "kov" / "tallinna_linnavolikogu"
        kov.mkdir(parents=True)
        (rt / "maarus_kov").mkdir(parents=True)

        # Peep file with globalId 999 — slug-based pairing would fail
        # (filename doesn't share a slug with the XML)
        peep = kov / "test_act_peep.json"
        peep.write_text(json.dumps({
            "@context": {"estleg": "https://w3id.org/estleg/",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Reg_X_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Act",
                           "estleg:MunicipalRegulation"],
                 "rdfs:label": "Test KOV act",
                 "estleg:globalId": "999",
                 "estleg:enactedBy": {
                     "@id": "estleg:Issuer_tallinna_linnavolikogu"},
                 "estleg:enactedByMunicipality": {
                     "@id": "estleg:Municipality_EHAK_0784"},
                 "estleg:titleNormalized": "test"}
            ],
        }), encoding="utf-8")

        # XML uses the actual tags the extractor reads:
        # vastuvoetud/aktikuupaev (adoption) and vastuvoetud/joustumine
        # (entry into force). See scripts/extract_temporal_data.py
        # lines 133-145 for the field reader.
        (rt / "maarus_kov" / "reg_999.xml").write_text(
            '<akt globaalID="999"><metaandmed>'
            '<vastuvoetud>'
            '<aktikuupaev>2023-12-15</aktikuupaev>'
            '<joustumine>2024-01-01</joustumine>'
            '</vastuvoetud>'
            '</metaandmed></akt>',
            encoding="utf-8",
        )

        # Critical: iter_peep_files() is imported from estleg_common,
        # whose own KRR_DIR was bound at import time. Patch BOTH the
        # extractor module's KRR_DIR/DATA_DIR AND estleg_common.KRR_DIR
        # — otherwise the iterator will scan the real repo regardless
        # of what extract_temporal_data thinks KRR_DIR is.
        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "DATA_DIR", rt)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        if hasattr(mod, "REPO_ROOT"):
            monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)

        # Run main() — should write back temporal triples to the peep
        # file based on the paired XML.
        rc = mod.main()
        assert rc in (None, 0)

        # Re-read the peep file and verify a temporal triple was added
        # from the paired XML. The script writes estleg:entryIntoForce
        # (and possibly estleg:adoptionDate / estleg:repealDate /
        # estleg:lastAmendmentDate). Any of these is acceptable
        # evidence that pairing worked.
        with open(peep, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        act = next(n for n in doc["@graph"]
                   if "estleg:MunicipalRegulation" in (n.get("@type") or []))
        date_keys = {"estleg:entryIntoForce", "estleg:adoptionDate",
                     "estleg:repealDate", "estleg:lastAmendmentDate",
                     "estleg:publicationDate"}
        present = date_keys.intersection(act.keys())
        assert present, (
            f"main() ran but no date triples appeared on the act node "
            f"after pairing — pair_peep_with_xml may not be wired into "
            f"main(). Act keys: {list(act.keys())}"
        )


# --------------------------------------------------------------------- #
# Regression tests for ontology-review-plan issue #168.
# --------------------------------------------------------------------- #


class TestParseDateTimezoneOffsets:
    """``parse_date`` must handle trailing AND embedded timezone
    offsets (positive and negative) without eating date separators.
    """

    @pytest.mark.parametrize(
        "value, expected",
        [
            ("2024-01-15", "2024-01-15"),
            ("2024-01-15+02:00", "2024-01-15"),
            ("2024-01-15-02:00", "2024-01-15"),
            ("2024-01-15+03:00", "2024-01-15"),
            # Embedded offset (malformed corpus data).
            ("2011+02:00-01-01", "2011-01-01"),
            ("2024+02:00-12-31", "2024-12-31"),
            # Estonian-style D.M.YYYY (regex fallback).
            ("15.01.2024", "2024-01-15"),
            # ISO datetime with offset still strips to date.
            ("2024-01-15T08:00:00+02:00", "2024-01-15"),
        ],
    )
    def test_parses_various_offset_positions(self, value, expected):
        from estleg.extract_temporal_data import parse_date
        assert parse_date(value) == expected

    def test_negative_trailing_offset_does_not_eat_date_separator(self):
        """The previous regex stripped any trailing ``-DD:DD`` which
        could have damaged dates like ``2024-12-31-04:00`` if the
        regex were ambiguous. Confirm the new fromisoformat-first
        path keeps the day component intact.
        """
        from estleg.extract_temporal_data import parse_date
        assert parse_date("2024-12-31-04:00") == "2024-12-31"

    def test_invalid_returns_none(self):
        from estleg.extract_temporal_data import parse_date
        assert parse_date("not-a-date") is None
        assert parse_date("") is None


class TestParseDateYearRangeGuard:
    """#352: ``parse_date`` must reject well-formed but logically
    impossible YYYY-MM-DD values whose year falls outside [1900, 2100].

    These are syntactically valid ISO dates the date-validator can't
    catch — source digit-transpositions (2918→2018, 3006→2006,
    0218→2018) and EUR-Lex null-sentinels (1001-01-01, 1002-02-02) —
    that otherwise become typed ``xsd:date`` literals and break every
    as-of-date / 'most recently amended' ordering query.
    """

    @pytest.mark.parametrize(
        "value",
        [
            "2918-10-17",   # millennium typo (intended 2018)
            "3006-03-02",   # millennium typo (intended 2006)
            "0218-02-04",   # leading-zero transposition (intended 2018)
            "1001-01-01",   # EUR-Lex sentinel
            "1002-02-02",   # EUR-Lex sentinel
            "1899-12-31",   # just below lower bound
            "2101-01-01",   # just above upper bound
        ],
    )
    def test_implausible_year_returns_none(self, value):
        from estleg.extract_temporal_data import parse_date
        assert parse_date(value) is None

    @pytest.mark.parametrize(
        "value, expected",
        [
            ("2024-06-15", "2024-06-15"),   # ordinary in-range date
            ("1900-01-01", "1900-01-01"),   # lower bound is inclusive
            ("2100-12-31", "2100-12-31"),   # upper bound is inclusive
        ],
    )
    def test_plausible_year_passes(self, value, expected):
        from estleg.extract_temporal_data import parse_date
        assert parse_date(value) == expected

    def test_guard_applies_to_regex_fallback_path(self):
        """The DD.MM.YYYY branch is only reached via the regex fallback
        (fromisoformat rejects it), so the guard must fire there too."""
        from estleg.extract_temporal_data import parse_date
        # In range -> passes via the fallback.
        assert parse_date("15.06.2024") == "2024-06-15"
        # Out of range via the same fallback branch -> dropped.
        assert parse_date("17.10.2918") is None


class TestCoerceIsoDate:
    """``_coerce_iso_date`` is the assertion helper that prevents
    accidental string-comparison regressions."""

    def test_returns_date_object(self):
        from datetime import date as _date

        from estleg.extract_temporal_data import _coerce_iso_date
        assert _coerce_iso_date("2024-01-15") == _date(2024, 1, 15)

    def test_assertion_on_non_string(self):
        from estleg.extract_temporal_data import _coerce_iso_date
        with pytest.raises(AssertionError):
            _coerce_iso_date(20240115)  # type: ignore[arg-type]


class TestDetermineTemporalStatusUnknown:
    """The new fourth status: ``unknown`` is returned when no actionable
    date fields are present, instead of falling through to ``inForce``."""

    def test_empty_temporal_returns_unknown(self):
        from estleg.extract_temporal_data import determine_temporal_status
        assert determine_temporal_status({}) == "unknown"

    def test_all_none_temporal_returns_unknown(self):
        from estleg.extract_temporal_data import determine_temporal_status
        temporal = {
            "entry_into_force": None,
            "valid_from": None,
            "valid_until": None,
            "invalidation_date": None,
            "last_amendment_date": None,
            "publication_date": None,
            "adoption_date": None,
        }
        assert determine_temporal_status(temporal) == "unknown"

    def test_only_publication_date_returns_unknown(self):
        """publication_date alone is metadata, not actionable for
        in-force/repealed determination."""
        from estleg.extract_temporal_data import determine_temporal_status
        temporal = {"publication_date": "2024-01-01"}
        assert determine_temporal_status(temporal) == "unknown"

    def test_with_actionable_date_returns_in_force(self):
        from estleg.extract_temporal_data import determine_temporal_status
        temporal = {"entry_into_force": "2020-01-01"}
        assert determine_temporal_status(
            temporal, evaluation_date="2024-01-01"
        ) == "inForce"


class TestDetermineTemporalStatusDateComparison:
    """Compare ``date`` objects rather than ISO strings."""

    def test_repealed_uses_date_objects(self):
        """A subtle string-vs-date bug is that DD.MM.YYYY would sort
        wrong if it slipped through. We force-parse and verify the
        comparison still works on date objects."""
        from estleg.extract_temporal_data import determine_temporal_status
        temporal = {"valid_until": "2024-05-15"}
        assert determine_temporal_status(
            temporal, evaluation_date="2024-05-14"
        ) == "inForce"
        assert determine_temporal_status(
            temporal, evaluation_date="2024-05-15"
        ) == "repealed"

    def test_invalidation_date_repealed(self):
        from estleg.extract_temporal_data import determine_temporal_status
        temporal = {"invalidation_date": "2024-06-30"}
        assert determine_temporal_status(
            temporal, evaluation_date="2024-07-01"
        ) == "repealed"


class TestMuutmisKuupaevAllOccurrences:
    """``extract_temporal_from_xml`` must iterate ALL ``muutmisKuupaev``
    elements and keep the latest (no early ``break``).
    """

    def test_picks_latest_of_multiple_muutmis_kuupaev(self, tmp_path):
        from estleg.extract_temporal_data import extract_temporal_from_xml
        xml_path = tmp_path / "many_amendments.xml"
        xml_path.write_text(
            "<akt><metaandmed>"
            "<muutmisKuupaev>2018-03-15</muutmisKuupaev>"
            "<muutmisKuupaev>2024-08-22</muutmisKuupaev>"
            "<muutmisKuupaev>2021-11-09</muutmisKuupaev>"
            "</metaandmed></akt>",
            encoding="utf-8",
        )
        out = extract_temporal_from_xml(xml_path)
        assert out["last_amendment_date"] == "2024-08-22"

    def test_combines_muutmismarge_and_muutmis_kuupaev(self, tmp_path):
        """Latest wins regardless of which element sourced it."""
        from estleg.extract_temporal_data import extract_temporal_from_xml
        xml_path = tmp_path / "mixed.xml"
        xml_path.write_text(
            "<akt><metaandmed>"
            "<muutmismarge><aktikuupaev>2010-01-01</aktikuupaev></muutmismarge>"
            "<muutmismarge><aktikuupaev>2024-12-31</aktikuupaev></muutmismarge>"
            "<muutmisKuupaev>2015-06-15</muutmisKuupaev>"
            "</metaandmed></akt>",
            encoding="utf-8",
        )
        out = extract_temporal_from_xml(xml_path)
        assert out["last_amendment_date"] == "2024-12-31"


class TestIndexFallbackDoesNotMisclassifyAsInForce:
    """The headline #168 bug: when the XML temporal dict is empty AND
    INDEX.json provides no real dates, ``determine_temporal_status``
    must yield ``unknown`` (NOT ``inForce``).
    """

    def test_index_fallback_with_no_dates_yields_unknown(
        self, tmp_path, monkeypatch
    ):
        from estleg import estleg_common
        from estleg import extract_temporal_data as mod
        krr = tmp_path / "krr_outputs"
        rt = tmp_path / "data" / "riigiteataja"
        krr.mkdir()
        rt.mkdir(parents=True)
        # Reports dir is created by main() if missing, but the
        # measure_runtime helper needs the parent path to be writable.
        (krr / "reports" / "kov").mkdir(parents=True)
        # INDEX.json with a law that has no dates AT ALL.
        (krr / "INDEX.json").write_text(json.dumps({
            "laws": [
                {"name": "ghost_law", "files": ["ghost_law_peep.json"]}
            ],
        }), encoding="utf-8")
        # The peep file with NO XML and NO globalId.
        peep = krr / "ghost_law_peep.json"
        peep.write_text(json.dumps({
            "@context": {
                "estleg": "https://w3id.org/estleg/",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:GhostLaw_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
                 "rdfs:label": "Ghost Law"},
            ],
        }), encoding="utf-8")

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "DATA_DIR", rt)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        if hasattr(mod, "REPO_ROOT"):
            monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)

        rc = mod.main()
        assert rc in (None, 0)

        with open(peep, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        ont = next(n for n in doc["@graph"]
                   if "owl:Ontology" in (n.get("@type") or []))
        # Must NOT be classified inForce by the all-None fallback.
        assert ont.get("estleg:temporalStatus") == "unknown", (
            "INDEX-fallback path must produce 'unknown' (not 'inForce') "
            "when no actionable date is available"
        )

    def test_index_fallback_with_kehtivus_block_dates_classifies(
        self, tmp_path, monkeypatch
    ):
        """When INDEX.json carries a ``kehtivus`` block with real
        dates, those dates are mapped onto the temporal dict so the
        status reflects them.
        """
        from estleg import estleg_common
        from estleg import extract_temporal_data as mod
        krr = tmp_path / "krr_outputs"
        rt = tmp_path / "data" / "riigiteataja"
        krr.mkdir()
        rt.mkdir(parents=True)
        (krr / "reports" / "kov").mkdir(parents=True)
        (krr / "INDEX.json").write_text(json.dumps({
            "laws": [{
                "name": "old_law",
                "files": ["old_law_peep.json"],
                "kehtivus": {
                    "joustumine": "2010-01-01",
                    "kehtetuKuupaev": "2020-12-31",
                },
            }],
        }), encoding="utf-8")
        peep = krr / "old_law_peep.json"
        peep.write_text(json.dumps({
            "@context": {
                "estleg": "https://w3id.org/estleg/",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:OldLaw_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
                 "rdfs:label": "Old Law"},
            ],
        }), encoding="utf-8")

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "DATA_DIR", rt)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        if hasattr(mod, "REPO_ROOT"):
            monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)

        # Evaluation date AFTER the kehtetuKuupaev — so status must
        # be ``repealed`` (not ``inForce`` and not ``unknown``).
        rc = mod.main(evaluation_date="2024-01-01")
        assert rc in (None, 0)
        with open(peep, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        ont = next(n for n in doc["@graph"]
                   if "owl:Ontology" in (n.get("@type") or []))
        assert ont.get("estleg:temporalStatus") == "repealed"


class TestActLevelPlacement:
    """Temporal props go onto act / owl:Ontology nodes ONLY — no graph[0]
    fallback (#128). A file whose graph carries neither an owl:Ontology
    node nor an estleg:Act node is skipped (counted as no_act_node), and
    any stale temporal props on its non-Act nodes are scrubbed.
    """

    def test_is_act_level_node(self):
        from estleg.extract_temporal_data import is_act_level_node
        assert is_act_level_node({"@type": ["owl:Ontology", "estleg:Act", "estleg:Law"]})
        assert is_act_level_node({"@type": ["estleg:Act"]})
        assert not is_act_level_node({"@type": ["owl:Ontology"]})
        assert is_act_level_node({"@type": "estleg:Act"})
        assert not is_act_level_node({"@type": ["owl:NamedIndividual", "estleg:LegalConcept"]})
        assert not is_act_level_node({"@type": ["estleg:LegalProvision_Foo"]})
        assert not is_act_level_node({})

    def test_find_act_node_prefers_first_domain_individual(self):
        from estleg.extract_temporal_data import find_act_node
        graph = [
            {"@id": "estleg:P1", "@type": ["estleg:LegalProvision"]},
            {"@id": "estleg:A1", "@type": ["estleg:Act"]},
            {"@id": "estleg:M1", "@type": ["owl:Ontology", "estleg:Act"]},
        ]
        assert find_act_node(graph)["@id"] == "estleg:A1"
        assert find_act_node(graph[:2])["@id"] == "estleg:A1"
        assert find_act_node([{"@id": "estleg:Catalog", "@type": ["owl:Ontology"]}]) is None
        # Neither -> None (no graph[0] fallback).
        assert find_act_node([{"@id": "estleg:C1", "@type": ["estleg:LegalConcept"]}]) is None
        assert find_act_node([]) is None

    def test_clear_temporal_keys_scrubs_non_act_nodes(self):
        from estleg.extract_temporal_data import clear_temporal_keys
        graph = [
            {"@id": "estleg:M1", "@type": ["owl:Ontology", "estleg:Act"],
             "estleg:temporalStatus": "inForce"},
            {"@id": "estleg:C1", "@type": ["estleg:LegalConcept"],
             "estleg:temporalStatus": "inForce",
             "estleg:entryIntoForce": {"@value": "1999-01-01", "@type": "xsd:date"}},
        ]
        assert clear_temporal_keys(graph) is True
        assert "estleg:temporalStatus" not in graph[0]
        assert "estleg:temporalStatus" not in graph[1]
        assert "estleg:entryIntoForce" not in graph[1]
        # Idempotent: nothing left to remove.
        assert clear_temporal_keys(graph) is False

    def test_main_skips_concept_only_file_and_scrubs_stale_temporal(
        self, tmp_path, monkeypatch
    ):
        """A concept-only peep (the tsiviilseadustik_osa6/osa7 case): no
        owl:Ontology, no estleg:Act, just an estleg:LegalConcept. main()
        must NOT stamp temporal props onto it (no graph[0] fallback),
        must strip any stale temporal props it already carries, and must
        count it under skipped_no_act_node in the report.
        """
        from estleg import estleg_common
        from estleg import extract_temporal_data as mod

        krr = tmp_path / "krr_outputs"
        rt = tmp_path / "data" / "riigiteataja"
        krr.mkdir()
        rt.mkdir(parents=True)
        (krr / "reports" / "kov").mkdir(parents=True)

        peep = krr / "concept_only_peep.json"
        peep.write_text(json.dumps({
            "@context": {
                "estleg": "https://w3id.org/estleg/",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:Concept_time_limits",
                 "@type": ["owl:NamedIndividual", "estleg:LegalConcept"],
                 "rdfs:label": "Time limits",
                 # Stale temporal props from an old graph[0] fallback.
                 "estleg:temporalStatus": "inForce",
                 "estleg:entryIntoForce": {"@value": "1999-01-01",
                                            "@type": "xsd:date"}},
            ],
        }), encoding="utf-8")

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "DATA_DIR", rt)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        if hasattr(mod, "REPO_ROOT"):
            monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)

        rc = mod.main()
        assert rc in (None, 0)

        doc = json.loads(peep.read_text(encoding="utf-8"))
        concept = doc["@graph"][0]
        # Stale temporal props scrubbed; none re-added (no act node).
        assert "estleg:temporalStatus" not in concept
        assert "estleg:entryIntoForce" not in concept

        report = json.loads(
            (krr / "reports" / "temporal_data_report.json").read_text(encoding="utf-8")
        )
        assert report["summary"]["skipped_no_act_node"] >= 1
        entry = next(e for e in report["laws"] if e["file"] == "concept_only_peep.json")
        assert entry["status"] == "no_act_node"

    def test_main_does_not_write_temporal_onto_provision_node(
        self, tmp_path, monkeypatch
    ):
        """When an act node IS present, only it (not sibling provision
        nodes) receives temporal props."""
        from estleg import estleg_common
        from estleg import extract_temporal_data as mod

        krr = tmp_path / "krr_outputs"
        rt = tmp_path / "data" / "riigiteataja"
        krr.mkdir()
        rt.mkdir(parents=True)
        (krr / "reports" / "kov").mkdir(parents=True)
        (krr / "INDEX.json").write_text(json.dumps({
            "laws": [{
                "name": "demo_law",
                "files": ["demo_law_peep.json"],
                "kehtivus": {"joustumine": "2010-01-01"},
            }],
        }), encoding="utf-8")

        peep = krr / "demo_law_peep.json"
        peep.write_text(json.dumps({
            "@context": {
                "estleg": "https://w3id.org/estleg/",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:Demo_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
                 "rdfs:label": "Demo Law"},
                {"@id": "estleg:Demo_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision_Demo"],
                 "estleg:paragrahv": "§ 1."},
            ],
        }), encoding="utf-8")

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "DATA_DIR", rt)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        if hasattr(mod, "REPO_ROOT"):
            monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)

        rc = mod.main(evaluation_date="2024-01-01")
        assert rc in (None, 0)

        doc = json.loads(peep.read_text(encoding="utf-8"))
        act = next(n for n in doc["@graph"] if "owl:Ontology" in n["@type"])
        provision = next(n for n in doc["@graph"] if "estleg:Demo_Par_1" == n["@id"])
        assert act.get("estleg:temporalStatus") == "inForce"
        assert "estleg:temporalStatus" not in provision
        assert "estleg:entryIntoForce" not in provision


# --------------------------------------------------------------------- #
# Regression tests for issue #287:
#   (a) symmetric repealDate emission for valid_until AND invalidation_date
#       (both sources emit it; future dates are emitted too)
#   (b) repeal/invalidation/valid_until < entry_into_force -> ``unknown``
# --------------------------------------------------------------------- #


class TestRepealBeforeEntrySanityGuard:
    """#287(b): a repeal/invalidation/valid_until date that precedes
    entry_into_force is contradictory source data and must classify
    ``unknown`` (a law cannot be repealed before it ever took force),
    not ``repealed``.
    """

    def test_invalidation_before_entry_yields_unknown(self):
        from estleg.extract_temporal_data import determine_temporal_status
        temporal = {
            "entry_into_force": "2025-01-01",
            "invalidation_date": "2020-12-31",
        }
        # Without the guard this was classified ``repealed`` (invalidation
        # in the past relative to the 2026 evaluation date).
        assert determine_temporal_status(
            temporal, evaluation_date="2026-01-01"
        ) == "unknown"

    def test_valid_until_before_entry_yields_unknown(self):
        from estleg.extract_temporal_data import determine_temporal_status
        temporal = {
            "entry_into_force": "2025-01-01",
            "valid_until": "2020-12-31",
        }
        assert determine_temporal_status(
            temporal, evaluation_date="2026-01-01"
        ) == "unknown"

    def test_equal_dates_are_not_contradictory(self):
        """entry == end is a same-day open/close, not a contradiction;
        the guard uses strict ``<`` so this falls through to the normal
        repealed/in-force logic (repealed here, as end <= today)."""
        from estleg.extract_temporal_data import determine_temporal_status
        temporal = {
            "entry_into_force": "2025-01-01",
            "invalidation_date": "2025-01-01",
        }
        assert determine_temporal_status(
            temporal, evaluation_date="2026-01-01"
        ) == "repealed"

    def test_end_after_entry_classifies_normally(self):
        """Sanity: a well-ordered law (entry < end) is unaffected by the
        guard and classifies on the usual past/future logic."""
        from estleg.extract_temporal_data import determine_temporal_status
        temporal = {
            "entry_into_force": "2010-01-01",
            "invalidation_date": "2020-12-31",
        }
        # End in the past -> repealed.
        assert determine_temporal_status(
            temporal, evaluation_date="2024-01-01"
        ) == "repealed"
        # End in the future -> in force.
        assert determine_temporal_status(
            temporal, evaluation_date="2015-01-01"
        ) == "inForce"

    def test_no_entry_into_force_skips_guard(self):
        """When there is no entry_into_force the guard cannot fire; a
        past valid_until still classifies ``repealed`` as before."""
        from estleg.extract_temporal_data import determine_temporal_status
        temporal = {"valid_until": "2020-12-31"}
        assert determine_temporal_status(
            temporal, evaluation_date="2024-01-01"
        ) == "repealed"


def _run_main_over_single_peep(tmp_path, monkeypatch, temporal_kehtivus,
                               evaluation_date="2026-01-01"):
    """Helper: stage one law peep whose temporal data comes from an
    INDEX.json ``kehtivus`` block, run main(), and return the act node.
    Keeps the #287 enrichment tests free of XML-pairing boilerplate.
    """
    from estleg import estleg_common
    from estleg import extract_temporal_data as mod

    krr = tmp_path / "krr_outputs"
    rt = tmp_path / "data" / "riigiteataja"
    krr.mkdir()
    rt.mkdir(parents=True)
    (krr / "reports" / "kov").mkdir(parents=True)
    (krr / "INDEX.json").write_text(json.dumps({
        "laws": [{
            "name": "guard_law",
            "files": ["guard_law_peep.json"],
            "kehtivus": temporal_kehtivus,
        }],
    }), encoding="utf-8")
    peep = krr / "guard_law_peep.json"
    peep.write_text(json.dumps({
        "@context": {
            "estleg": "https://w3id.org/estleg/",
            "owl": "http://www.w3.org/2002/07/owl#",
        },
        "@graph": [
            {"@id": "estleg:GuardLaw_Map_2026",
             "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
             "rdfs:label": "Guard Law"},
        ],
    }), encoding="utf-8")

    monkeypatch.setattr(mod, "KRR_DIR", krr)
    monkeypatch.setattr(mod, "DATA_DIR", rt)
    monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
    if hasattr(mod, "REPO_ROOT"):
        monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)

    rc = mod.main(evaluation_date=evaluation_date)
    assert rc in (None, 0)
    doc = json.loads(peep.read_text(encoding="utf-8"))
    act = next(n for n in doc["@graph"] if "owl:Ontology" in n["@type"])
    report = json.loads(
        (krr / "reports" / "temporal_data_report.json").read_text(encoding="utf-8")
    )
    return act, report


class TestRepealDateSymmetricEmission:
    """#287(a): ``repealDate`` is emitted whenever a repeal/invalidation/
    valid_until date is present, from EITHER source, regardless of
    past/future. Previously ``valid_until`` only wrote ``repealDate``
    when status was ``repealed`` while ``invalidation_date`` wrote it
    unconditionally — an asymmetry that made the field's meaning depend
    on which source populated it.
    """

    def test_valid_until_emits_repeal_date_even_when_in_force(
        self, tmp_path, monkeypatch
    ):
        """A FUTURE valid_until (scheduled repeal): status is ``inForce``
        but ``repealDate`` must still be emitted (the old code skipped it
        because status != 'repealed')."""
        act, _ = _run_main_over_single_peep(
            tmp_path, monkeypatch,
            {"joustumine": "2010-01-01", "kehtivKuni": "2030-12-31"},
            evaluation_date="2026-01-01",
        )
        assert act.get("estleg:temporalStatus") == "inForce"
        assert act.get("estleg:repealDate") == {
            "@value": "2030-12-31", "@type": "xsd:date"
        }

    def test_invalidation_date_emits_repeal_date_when_future(
        self, tmp_path, monkeypatch
    ):
        """Symmetric companion: a FUTURE invalidation_date with status
        ``inForce`` also emits ``repealDate`` (this path always did, and
        must keep doing so after the refactor)."""
        act, _ = _run_main_over_single_peep(
            tmp_path, monkeypatch,
            {"joustumine": "2010-01-01", "kehtetuKuupaev": "2030-12-31"},
            evaluation_date="2026-01-01",
        )
        assert act.get("estleg:temporalStatus") == "inForce"
        assert act.get("estleg:repealDate") == {
            "@value": "2030-12-31", "@type": "xsd:date"
        }

    def test_invalidation_date_takes_precedence_over_valid_until(
        self, tmp_path, monkeypatch
    ):
        """When both sources are present, invalidation_date wins (the
        more specific field), matching the prior precedence."""
        act, _ = _run_main_over_single_peep(
            tmp_path, monkeypatch,
            {"joustumine": "2010-01-01",
             "kehtivKuni": "2030-12-31",
             "kehtetuKuupaev": "2028-06-30"},
            evaluation_date="2026-01-01",
        )
        assert act.get("estleg:repealDate") == {
            "@value": "2028-06-30", "@type": "xsd:date"
        }

    def test_past_repeal_still_emits_repeal_date(
        self, tmp_path, monkeypatch
    ):
        """A genuinely repealed law (past invalidation) still gets
        ``repealDate`` plus ``temporalStatus == repealed``."""
        act, _ = _run_main_over_single_peep(
            tmp_path, monkeypatch,
            {"joustumine": "2010-01-01", "kehtetuKuupaev": "2020-12-31"},
            evaluation_date="2026-01-01",
        )
        assert act.get("estleg:temporalStatus") == "repealed"
        assert act.get("estleg:repealDate") == {
            "@value": "2020-12-31", "@type": "xsd:date"
        }

    def test_repeal_before_entry_records_unknown_with_repeal_date(
        self, tmp_path, monkeypatch
    ):
        """#287(b) end-to-end: a law whose invalidation precedes its
        entry_into_force is classified ``unknown`` in both the act node
        and the report, while ``repealDate`` is still emitted so the
        contradictory date remains visible to consumers."""
        act, report = _run_main_over_single_peep(
            tmp_path, monkeypatch,
            {"joustumine": "2025-01-01", "kehtetuKuupaev": "2020-12-31"},
            evaluation_date="2026-01-01",
        )
        assert act.get("estleg:temporalStatus") == "unknown"
        # repealDate still emitted (presence no longer implies repealed).
        assert act.get("estleg:repealDate") == {
            "@value": "2020-12-31", "@type": "xsd:date"
        }
        # The contradictory file is recorded in the report with its
        # dates, so the data-quality signal is preserved.
        entry = next(
            e for e in report["laws"]
            if e["file"] == "guard_law_peep.json"
        )
        assert entry["status"] == "unknown"
        assert entry["temporal"].get("entry_into_force") == "2025-01-01"
        assert entry["temporal"].get("invalidation_date") == "2020-12-31"


class TestReportDeterminism:
    """#295: the git-tracked ``temporal_data_report.json`` must not embed
    a wall-clock ``generated`` timestamp, and the coverage report's
    ``run_timestamp`` must be pinned to the declared evaluation date so
    reruns are byte-stable when the data is unchanged.
    """

    def test_report_has_no_wallclock_generated_field(
        self, tmp_path, monkeypatch
    ):
        _, report = _run_main_over_single_peep(
            tmp_path, monkeypatch,
            {"joustumine": "2010-01-01"},
            evaluation_date="2026-01-01",
        )
        assert "generated" not in report, (
            "temporal_data_report.json must not embed a wall-clock "
            "'generated' timestamp (#295 churn)"
        )
        # The declared evaluation date is retained (it is deterministic).
        assert report["evaluationDate"] == "2026-01-01"

    def test_report_body_is_byte_stable_across_reruns(
        self, tmp_path, monkeypatch
    ):
        """Two runs with identical inputs and the same evaluation date
        must produce byte-identical report bytes (no timestamp churn)."""
        from estleg import estleg_common
        from estleg import extract_temporal_data as mod

        krr = tmp_path / "krr_outputs"
        rt = tmp_path / "data" / "riigiteataja"
        krr.mkdir()
        rt.mkdir(parents=True)
        (krr / "reports" / "kov").mkdir(parents=True)
        (krr / "INDEX.json").write_text(json.dumps({
            "laws": [{
                "name": "stable_law",
                "files": ["stable_law_peep.json"],
                "kehtivus": {"joustumine": "2010-01-01"},
            }],
        }), encoding="utf-8")
        peep = krr / "stable_law_peep.json"
        peep.write_text(json.dumps({
            "@context": {
                "estleg": "https://w3id.org/estleg/",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:StableLaw_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
                 "rdfs:label": "Stable Law"},
            ],
        }), encoding="utf-8")

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "DATA_DIR", rt)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        if hasattr(mod, "REPO_ROOT"):
            monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)

        report_path = krr / "reports" / "temporal_data_report.json"
        mod.main(evaluation_date="2026-01-01")
        first = report_path.read_bytes()
        mod.main(evaluation_date="2026-01-01")
        second = report_path.read_bytes()
        assert first == second, (
            "report bytes changed across identical reruns -> "
            "timestamp-only churn was reintroduced"
        )

    def test_coverage_run_timestamp_pinned_to_evaluation_date(
        self, tmp_path, monkeypatch
    ):
        """The coverage report's run_timestamp is derived from the
        declared evaluation date (midnight UTC), not wall-clock now()."""
        _run_main_over_single_peep(
            tmp_path, monkeypatch,
            {"joustumine": "2010-01-01"},
            evaluation_date="2026-01-01",
        )
        cov = json.loads(
            (tmp_path / "krr_outputs" / "reports" / "kov"
             / "extract_temporal_data_coverage.json").read_text(
                encoding="utf-8")
        )
        assert cov["run_timestamp"] == "2026-01-01T00:00:00+00:00"


class TestSinglePassClearAndEnrich:
    """The merged single-pass pattern: open + clear + enrich + save in
    one iteration. The previous double-pass pattern saved the same
    file twice. We assert the file is saved at most once per run.
    """

    def test_single_save_per_file(self, tmp_path, monkeypatch):
        from estleg import estleg_common
        from estleg import extract_temporal_data as mod

        krr = tmp_path / "krr_outputs"
        rt = tmp_path / "data" / "riigiteataja"
        krr.mkdir()
        rt.mkdir(parents=True)
        (krr / "reports" / "kov").mkdir(parents=True)

        peep = krr / "single_pass_peep.json"
        peep.write_text(json.dumps({
            "@context": {
                "estleg": "https://w3id.org/estleg/",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:SP_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
                 "rdfs:label": "Single Pass Law",
                 # Stale temporal data from a prior run.
                 "estleg:entryIntoForce": {"@value": "1999-01-01",
                                            "@type": "xsd:date"},
                 "estleg:temporalStatus": "inForce"},
            ],
        }), encoding="utf-8")

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "DATA_DIR", rt)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        if hasattr(mod, "REPO_ROOT"):
            monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)

        # Wrap save_json to count writes per path.
        write_counts: dict[Path, int] = {}
        original_save = mod.save_json

        def counting_save(path, doc):
            write_counts[path] = write_counts.get(path, 0) + 1
            return original_save(path, doc)

        monkeypatch.setattr(mod, "save_json", counting_save)

        rc = mod.main()
        assert rc in (None, 0)

        # The peep file must be touched at most once (the single-pass
        # save replaces the prior clear-then-enrich pair). It might
        # be 0 if neither clear nor enrich changed the file, but the
        # fixture has stale temporal data that must be cleared, so
        # exactly 1 save is expected.
        peep_writes = write_counts.get(peep, 0)
        assert peep_writes <= 1, (
            f"single-pass clear+enrich must save at most once per file; "
            f"got {peep_writes} saves for {peep}"
        )


class TestIssue602NamedYearBand:
    """#602 — the year plausibility band is named (was a magic 1900/2100
    repeated in both date-parse paths); boundary behaviour is unchanged."""

    def test_band_constants_present(self):
        from estleg import extract_temporal_data as td

        assert td.MIN_PLAUSIBLE_YEAR == 1900
        assert td.MAX_PLAUSIBLE_YEAR == 2100

    def test_implausible_years_rejected_both_paths(self):
        from estleg.extract_temporal_data import parse_date

        # ISO path (fromisoformat) and strptime fallback both drop out-of-band.
        assert parse_date("2918-01-01") is None      # digit transposition
        assert parse_date("1001-01-01") is None      # EUR-Lex null sentinel
        assert parse_date("31.12.1899") is None      # below band (strptime)

    def test_plausible_years_kept(self):
        from estleg.extract_temporal_data import parse_date

        assert parse_date("2019-06-18") == "2019-06-18"
        assert parse_date("01.01.2100") == "2100-01-01"
        assert parse_date("01.01.1900") == "1900-01-01"

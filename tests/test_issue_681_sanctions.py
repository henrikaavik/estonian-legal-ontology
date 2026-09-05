"""Regression tests for issue #681 (rebuild and constrain the sanctions
layer) and the issue #682 SHACL shape.

The three worked examples are the real ``estleg:legalText`` of
Karistusseadustik §§ 114, 141 and 400 as they stand in
``krr_outputs/karistusseadustik_osa2_peep.json``. They are inlined here
rather than read from the corpus so the tests pin the *behaviour* and
keep working when the corpus is regenerated.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from estleg.extract_sanctions import (
    PENALTY_UNIT_PERCENT_OF_TURNOVER,
    PENALTY_UNITS,
    SANCTION_TYPE_LABELS,
    SEVERITY,
    _MAX_DETERMINATE_YEARS,
    _find_act_node,
    _is_general_part,
    _parse_penalty_to_structured,
    _structured_penalty_fields,
    _years_value,
    extract_confiscation,
    extract_dissolution,
    extract_imprisonment,
    extract_sanctions,
    extract_turnover_percentage,
    split_loiked,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

# --- real KarS Eriosa legal texts (verbatim) ------------------------- #

KARS_114 = (
    "Tapmise eest, kui see on toime pandud: piinaval või julmal viisil; "
    "üldohtlikul viisil; kahe või enama inimese suhtes; vähemalt teist "
    "korda; seoses röövimisega või omakasu motiivil; teise süüteo "
    "varjamise või selle toimepanemise hõlbustamise eesmärgil; "
    "lõhkeseadeldise või lõhkeaine kasutamisega, –karistatakse kaheksa- "
    "kuni kahekümneaastase või eluaegse vangistusega."
)

KARS_141 = (
    "(1) Inimese tahte vastaselt temaga suguühtesse astumise või muu "
    "sugulise iseloomuga teo toimepanemise eest vägivallaga või ära "
    "kasutades tema seisundit, milles ta ei olnud võimeline vastupanu "
    "osutama või toimunust aru saama, –karistatakse ühe- kuni "
    "viieaastase vangistusega. (2) Sama teo eest: kui see on toime "
    "pandud noorema kui kaheksateistaastase isiku suhtes; kui see on "
    "toime pandud kahe või enama isiku poolt; kui sellega on tekitatud "
    "kannatanule raske tervisekahjustus; kui sellega on põhjustatud "
    "kannatanu surm; kui sellega on kannatanu viidud enesetapuni või "
    "selle katseni või kui see on toime pandud isiku poolt, kes on "
    "varem toime pannud käesolevas jaos sätestatud kuriteo, "
    "–karistatakse kuue- kuni viieteistaastase vangistusega. (3) Sama "
    "teo eest, kui selle on toime pannud juriidiline isik, "
    "–karistatakse rahalise karistusega."
)

KARS_400 = (
    "(1) Konkurentsi kahjustava eesmärgi või tagajärjega "
    "ettevõtjatevahelise kokkuleppe, otsuse või kooskõlastatud tegevuse "
    "eest –karistatakse rahalise karistuse või kuni üheaastase "
    "vangistusega. (2) Konkurentidevahelise kokkuleppe, otsuse või "
    "kooskõlastatud tegevuse eest, sealhulgas riigihankes osalemisel, "
    "kui sellega: määratakse otseselt või kaudselt kolmandate isikute "
    "suhtes kindlaks hind või muud kauplemistingimused; piiratakse "
    "tootmist, teenindamist, kaubaturgu, tehnilist arengut või "
    "investeerimist või jagatakse kaubaturgu või varustusallikat, "
    "piiratakse kolmanda isiku pääsu kaubaturule või püütakse teda "
    "sealt välja tõrjuda –karistatakse rahalise karistuse või ühe- kuni "
    "kolmeaastase vangistusega. (3) Käesoleva paragrahvi lõikes 1 "
    "sätestatud teo eest, kui selle on toime pannud juriidiline isik, "
    "–karistatakse rahalise karistusega kuni 5 protsenti juriidilise "
    "isiku käibest. (4) Käesoleva paragrahvi lõikes 2 sätestatud teo "
    "eest, kui selle on toime pannud juriidiline isik, –karistatakse "
    "rahalise karistusega 5 kuni 10 protsenti juriidilise isiku "
    "käibest."
)


def _keyset(records: list[dict]) -> set[tuple[str, str, str]]:
    """Reduce sanction records to comparable (type, min, max) triples."""
    return {
        (
            r["sanction_type"],
            r.get("min_penalty", ""),
            r.get("max_penalty", ""),
        )
        for r in records
    }


# --------------------------------------------------------------------- #
# The three worked examples
# --------------------------------------------------------------------- #


class TestKarS114Murder:
    """§ 114 imposes "kaheksa- kuni kahekümneaastase või eluaegse
    vangistusega" — 8 to 20 years OR life. The shipped sidecar recorded
    only ``life``: the range patterns required ``aastase vangistus`` with
    nothing in between, so the interposed "või eluaegse" stopped them
    from ever reaching ``vangistus``."""

    def test_recovers_both_the_range_and_life(self):
        assert _keyset(extract_sanctions(KARS_114)) == {
            ("imprisonment", "8 years", "20 years"),
            ("imprisonment", "", "life"),
        }

    def test_life_record_is_kept_separate_from_the_range(self):
        records = extract_sanctions(KARS_114)
        life = [r for r in records if r.get("max_penalty") == "life"]
        assert len(life) == 1
        assert "min_penalty" not in life[0]

    def test_upper_bound_is_not_also_emitted_as_a_bare_ceiling(self):
        """"kuni kahekümneaastase …" sits inside the range match, so the
        max-only pattern must not add a second "max 20 years" record."""
        records = extract_sanctions(KARS_114)
        assert ("imprisonment", "", "20 years") not in _keyset(records)


class TestKarS141Rape:
    """§ 141 punishes three subsections differently: lg 1 = 1-5 years,
    lg 2 = 6-15 years, lg 3 = pecuniary punishment. The shipped sidecar
    carried only lg 1's 1-5 years because it was built from the
    500-character ``summary``, which truncates mid-lg-2."""

    def test_all_three_subsections_are_recovered(self):
        assert _keyset(extract_sanctions(KARS_141)) == {
            ("imprisonment", "1 years", "5 years"),
            ("imprisonment", "6 years", "15 years"),
            ("pecuniary_punishment", "", "500 daily rates"),
        }

    def test_bare_pecuniary_keeps_the_statutory_default_flag(self):
        record = next(
            r for r in extract_sanctions(KARS_141)
            if r["sanction_type"] == "pecuniary_punishment"
        )
        # lg 3 says only "rahalise karistusega", so 500 daily rates is
        # the KarS § 44 ceiling, not a value read out of the text.
        assert record.get("is_statutory_default") is True


class TestKarS400Competition:
    """§ 400 exercises both remaining defects at once: the dedup bug that
    swallowed lg 1's one-year ceiling, and the missing turnover-percentage
    pattern that made lg 3/lg 4 fall back to 500 daily rates."""

    def test_exact_expected_record_set(self):
        assert _keyset(extract_sanctions(KARS_400)) == {
            ("imprisonment", "", "1 years"),
            ("imprisonment", "1 years", "3 years"),
            ("pecuniary_punishment", "", "500 daily rates"),
            ("pecuniary_punishment", "", "5 % of turnover"),
            ("pecuniary_punishment", "5 % of turnover", "10 % of turnover"),
        }

    def test_no_other_records(self):
        assert len(extract_sanctions(KARS_400)) == 5

    def test_statutory_default_survives_alongside_turnover_fines(self):
        """lg 1 and lg 2 fine a natural person with a bare "rahalise
        karistusega", so the KarS § 44 default must still be emitted even
        though lg 3/lg 4 state explicit turnover ceilings."""
        default = [
            r for r in extract_sanctions(KARS_400)
            if r.get("max_penalty") == "500 daily rates"
        ]
        assert len(default) == 1
        assert default[0].get("is_statutory_default") is True

    def test_turnover_lõiked_do_not_get_the_daily_rates_default(self):
        """Run lg 3 and lg 4 in isolation: an explicit turnover ceiling
        replaces the natural-person fallback rather than joining it."""
        for chunk in split_loiked(KARS_400)[2:]:
            records = extract_sanctions(chunk)
            assert not any(
                r.get("max_penalty") == "500 daily rates" for r in records
            ), f"statutory default leaked into {chunk[:40]!r}"


# --------------------------------------------------------------------- #
# Deduplication
# --------------------------------------------------------------------- #


class TestExactPairDeduplication:
    """Dedup is on the exact (min, max) pair. The old ``_already()``
    compared a candidate against both bounds of every record already
    held, so a max-only ceiling was dropped whenever a range shared that
    value as its lower bound."""

    def test_max_only_survives_alongside_a_range_starting_at_that_value(self):
        text = (
            "(1) Teo eest –karistatakse kuni üheaastase vangistusega. "
            "(2) Sama teo eest –karistatakse ühe- kuni kolmeaastase "
            "vangistusega."
        )
        assert _keyset(extract_imprisonment(text)) == {
            ("imprisonment", "", "1 years"),
            ("imprisonment", "1 years", "3 years"),
        }

    def test_identical_pair_is_emitted_once(self):
        text = (
            "karistatakse ühe- kuni kolmeaastase vangistusega, "
            "ja samuti ühe- kuni kolmeaastase vangistusega."
        )
        assert len(extract_imprisonment(text)) == 1

    def test_range_upper_bound_does_not_duplicate_as_a_ceiling(self):
        text = "karistatakse kuue- kuni viieteistaastase vangistusega."
        assert _keyset(extract_imprisonment(text)) == {
            ("imprisonment", "6 years", "15 years"),
        }


# --------------------------------------------------------------------- #
# Life imprisonment gating
# --------------------------------------------------------------------- #


class TestLifeImprisonmentRequiresSentencingFormula:
    """KarS Üldosa §§ 4, 22¹, 45, 53, 77 and 83² mention life
    imprisonment while defining the sanction system. Without a
    ``karistatakse`` formula the provision imposes nothing."""

    def test_descriptive_mention_emits_no_life_sanction(self):
        text = (
            "Eluaegne vangistus on käesolevas seadustikus sätestatud "
            "karistus, mida kohaldatakse kohtu poolt."
        )
        assert extract_imprisonment(text) == []

    def test_sentencing_formula_still_emits_life(self):
        text = "Tapmise eest –karistatakse eluaegse vangistusega."
        assert _keyset(extract_imprisonment(text)) == {
            ("imprisonment", "", "life"),
        }

    def test_kars_45_style_definition_is_not_a_sanction(self):
        text = (
            "Vangistust võib mõista kolmekümnest päevast kahekümne "
            "aastani või eluaegselt. Eluaegne vangistus mõistetakse "
            "käesoleva seadustiku eriosas sätestatud juhtudel."
        )
        assert not any(
            r.get("max_penalty") == "life" for r in extract_imprisonment(text)
        )


# --------------------------------------------------------------------- #
# Statutory ceiling on determinate imprisonment
# --------------------------------------------------------------------- #


class TestImprisonmentStatutoryCeiling:
    """KarS § 45 lg 1 caps a determinate sentence at 20 years; beyond
    that only life imprisonment exists. The extractor must not be able to
    emit a term that ``estleg:SanctionImprisonmentMaxYearsShape`` would
    then reject — otherwise a parse artefact reds the release gate.

    The pre-#681 cap of 25 lived only in the ``karistatakse`` digit
    fallback, which is not the binding branch: the uncapped digit
    max-only pattern matched first and emitted 25 years anyway.
    """

    def test_ceiling_matches_the_shacl_bound(self):
        assert _MAX_DETERMINATE_YEARS == 20

    @pytest.mark.parametrize("text", [
        "Selle eest karistatakse kuni 25 aastase vangistusega.",
        "Selle eest karistatakse kuni 30 aastase vangistusega.",
        "Selle eest karistatakse kuni kahekümneviieaastase vangistusega.",
        "Selle eest karistatakse 25 kuni 30 aastase vangistusega.",
    ])
    def test_over_ceiling_terms_are_discarded(self, text):
        assert extract_imprisonment(text) == []

    @pytest.mark.parametrize("text,expected", [
        ("Selle eest karistatakse kuni 12 aastase vangistusega.", "12 years"),
        ("Selle eest karistatakse kuni 20 aastase vangistusega.", "20 years"),
    ])
    def test_terms_at_or_below_the_ceiling_survive(self, text, expected):
        assert [r["max_penalty"] for r in extract_imprisonment(text)] == [expected]

    def test_range_with_an_over_ceiling_upper_bound_is_dropped_whole(self):
        """An out-of-statute maximum means the whole match is untrusted,
        so the range is discarded rather than clamped."""
        text = "karistatakse kaheksa- kuni kolmekümneaastase vangistusega."
        assert extract_imprisonment(text) == []

    def test_life_is_unaffected_by_the_ceiling(self):
        text = "karistatakse eluaegse vangistusega."
        assert _keyset(extract_imprisonment(text)) == {
            ("imprisonment", "", "life"),
        }

    def test_kars_114_range_sits_exactly_at_the_ceiling(self):
        assert ("imprisonment", "8 years", "20 years") in _keyset(
            extract_sanctions(KARS_114)
        )

    def test_non_year_units_are_not_capped(self):
        """30 days and 24 months are not 30 or 24 YEARS; the ceiling
        applies only to a determinate term expressed in years."""
        assert [r["max_penalty"] for r in
                extract_imprisonment("vangistusega 24 päeva")] == ["24 days"]
        assert [r["max_penalty"] for r in
                extract_imprisonment("vangistusega 24 kuud")] == ["24 months"]

    @pytest.mark.parametrize("penalty,expected", [
        ("20 years", 20),
        ("1 years", 1),
        ("life", None),
        ("30 days", None),
        ("18 months", None),
        ("500 daily rates", None),
        (None, None),
        ("", None),
    ])
    def test_years_value_helper(self, penalty, expected):
        assert _years_value(penalty) == expected


# --------------------------------------------------------------------- #
# General-part (üldosa) suppression
# --------------------------------------------------------------------- #


class TestGeneralPartDetection:
    """The general part of a code is identified by a *parenthesised*
    part designation in the root node's label."""

    @pytest.mark.parametrize("label", [
        "Karistusseadustik Osa 1 (ÜLDOSA) §1–87 kaardistus",
        "VÕS Osa 1 (Üldosa) §1–207 kaardistus",
        "Asjaõigusseadus Osa 1 (ÜLDOSA) §1–31 kaardistus",
    ])
    def test_code_general_parts_are_detected(self, label):
        assert _is_general_part({"rdfs:label": label}) is True

    @pytest.mark.parametrize("label", [
        # Standalone acts whose NAME contains the word. These carry their
        # own penalty provisions and must keep their sanctions.
        "Keskkonnaseadustiku üldosa seadus teemakaardistus",
        "Majandustegevuse seadustiku üldosa seadus teemakaardistus",
        "Sotsiaalseadustiku üldosa seadus teemakaardistus",
        "Tsiviilseadustiku üldosa seadus teemakaardistus",
        # NON-general parts of such an act.
        "Tsiviilseadustiku üldosa seadus Osa 2 (ISIKUD) §7–47 kaardistus",
        "Tsiviilseadustiku üldosa seadus Osa 4 (TEHINGUD) §67–131 kaardistus",
        "Karistusseadustik Osa 2 (ERIOSA) §88–451 kaardistus",
    ])
    def test_standalone_uldosa_acts_are_not_suppressed(self, label):
        assert _is_general_part({"rdfs:label": label}) is False

    def test_langstring_dict_and_list_titles_are_read(self):
        node = {
            "dcterms:title": [
                {"@value": "Karistusseadustik Osa 1 (ÜLDOSA)", "@language": "et"},
                {"@value": "Penal Code", "@language": "en"},
            ],
        }
        assert _is_general_part(node) is True

    def test_node_without_any_label_is_not_a_general_part(self):
        assert _is_general_part({"@id": "estleg:X"}) is False


class TestGeneralPartFileIsSkipped:
    """A general-part peep produces no sanctions, and a regeneration
    RETRACTS what a previous run wrote for it."""

    def _stage(self, tmp_path, label):
        krr = tmp_path / "krr_outputs"
        sanctions_dir = krr / "sanctions"
        sanctions_dir.mkdir(parents=True)
        peep = krr / "code_osa1_peep.json"
        peep.write_text(json.dumps({
            "@context": {
                "estleg": "https://w3id.org/estleg/",
                "owl": "http://www.w3.org/2002/07/owl#",
                "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
            },
            "@graph": [
                {"@id": "estleg:CODE_Osa1",
                 "@type": ["estleg:Part"],
                 "rdfs:label": label},
                {"@id": "estleg:CODE_Osa1_Par_45",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 45.",
                 "estleg:legalText":
                     "Vangistust võib mõista kolmekümnest päevast "
                     "kahekümne aastani. Eluaegne vangistus mõistetakse "
                     "eriosas sätestatud juhtudel, kui karistatakse "
                     "raskeima kuriteo eest.",
                 # Stale output from a previous run.
                 "estleg:hasSanction": [
                     {"@id": "estleg:Sanction_CODE_Osa1_Par_45_imprisonment"}],
                 "estleg:enforcedAtLevel": "state"},
            ],
        }), encoding="utf-8")
        # Stale sidecar from a previous run.
        (sanctions_dir / "sanctions_code_osa1.json").write_text(
            json.dumps({"@graph": []}), encoding="utf-8")
        return krr, sanctions_dir, peep

    def _run(self, tmp_path, monkeypatch, label):
        from estleg import estleg_common
        from estleg import extract_sanctions as mod
        krr, sanctions_dir, peep = self._stage(tmp_path, label)
        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "SANCTION_DIR", sanctions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        assert mod.main() in (0, None)
        return krr, sanctions_dir, peep

    def test_general_part_yields_no_sanctions_and_clears_stale_state(
        self, tmp_path, monkeypatch
    ):
        _, sanctions_dir, peep = self._run(
            tmp_path, monkeypatch,
            "Karistusseadustik Osa 1 (ÜLDOSA) §1–87 kaardistus",
        )
        saved = json.loads(peep.read_text(encoding="utf-8"))
        prov = next(n for n in saved["@graph"]
                    if n.get("@id") == "estleg:CODE_Osa1_Par_45")
        assert "estleg:hasSanction" not in prov
        assert "estleg:enforcedAtLevel" not in prov
        assert not (sanctions_dir / "sanctions_code_osa1.json").exists()

    def test_skip_is_recorded_in_the_coverage_report(
        self, tmp_path, monkeypatch
    ):
        krr, _, _ = self._run(
            tmp_path, monkeypatch,
            "Karistusseadustik Osa 1 (ÜLDOSA) §1–87 kaardistus",
        )
        report = json.loads(
            (krr / "reports" / "kov" / "extract_sanctions_coverage.json")
            .read_text(encoding="utf-8")
        )
        assert report["skip_reasons"].get("general_part") == 1

    def test_special_part_with_the_same_shape_is_not_skipped(
        self, tmp_path, monkeypatch
    ):
        krr, _, _ = self._run(
            tmp_path, monkeypatch,
            "Karistusseadustik Osa 2 (ERIOSA) §88–451 kaardistus",
        )
        report = json.loads(
            (krr / "reports" / "kov" / "extract_sanctions_coverage.json")
            .read_text(encoding="utf-8")
        )
        assert "general_part" not in report.get("skip_reasons", {})


class TestPartRootNodeIsAnActRoot:
    """Multi-part laws root their peep on an ``estleg:Part`` node with no
    ``estleg:Act`` typing. An Act-only match returned None for 37 root
    peeps — including both Karistusseadustik parts — so the whole Penal
    Code was skipped as ``missing_act_node``."""

    def test_part_only_root_is_found(self):
        doc = {"@graph": [
            {"@id": "estleg:KARIST_2_Osa2", "@type": ["estleg:Part"]},
        ]}
        assert _find_act_node(doc)["@id"] == "estleg:KARIST_2_Osa2"

    def test_act_wins_when_both_typings_are_present(self):
        doc = {"@graph": [
            {"@id": "estleg:Some_Part", "@type": ["estleg:Part"]},
            {"@id": "estleg:Some_Act", "@type": ["estleg:Act"]},
        ]}
        assert _find_act_node(doc)["@id"] == "estleg:Some_Act"

    def test_unrelated_nodes_still_return_none(self):
        doc = {"@graph": [
            {"@id": "estleg:Header", "@type": ["owl:Ontology"]},
        ]}
        assert _find_act_node(doc) is None


# --------------------------------------------------------------------- #
# New sanction types
# --------------------------------------------------------------------- #


class TestDissolutionAndConfiscation:

    def test_dissolution_detected(self):
        text = (
            "Sama teo eest, kui selle on toime pannud juriidiline isik, "
            "–karistatakse rahalise karistuse või sundlõpetamisega."
        )
        assert {"sanction_type": "compulsory_dissolution"} in extract_sanctions(text)

    def test_confiscation_detected(self):
        text = (
            "Kohus kohaldab käesolevas paragrahvis sätestatud süüteo "
            "toimepanemise vahendi konfiskeerimist."
        )
        assert extract_confiscation(text) == [{"sanction_type": "confiscation"}]

    def test_neither_carries_an_amount(self):
        for record in (
            extract_dissolution("Karistatakse sundlõpetamisega.")[0],
            extract_confiscation("Kohus konfiskeerib vara.")[0],
        ):
            assert "max_penalty" not in record
            assert "min_penalty" not in record

    def test_absent_when_the_words_do_not_appear(self):
        text = "karistatakse rahatrahviga kuni 100 trahviühikut."
        assert extract_dissolution(text) == []
        assert extract_confiscation(text) == []

    def test_registered_in_severity_and_labels(self):
        # Confiscation sits with the monetary sanctions; compulsory
        # dissolution between the monetary sanctions and imprisonment.
        assert SEVERITY["confiscation"] == SEVERITY["fine"] == 2
        assert SEVERITY["compulsory_dissolution"] == 4
        assert SEVERITY["fine"] < SEVERITY["compulsory_dissolution"]
        assert SEVERITY["compulsory_dissolution"] < SEVERITY["imprisonment"]
        assert SANCTION_TYPE_LABELS["confiscation"] == "Confiscation"
        assert (
            SANCTION_TYPE_LABELS["compulsory_dissolution"]
            == "Compulsory dissolution"
        )


# --------------------------------------------------------------------- #
# Turnover percentages
# --------------------------------------------------------------------- #


class TestTurnoverPercentage:

    def test_ceiling_form(self):
        text = (
            "karistatakse rahalise karistusega kuni 5 protsenti "
            "juriidilise isiku käibest."
        )
        assert extract_turnover_percentage(text) == [{
            "sanction_type": "pecuniary_punishment",
            "max_penalty": "5 % of turnover",
        }]

    def test_range_form(self):
        text = (
            "karistatakse rahalise karistusega 5 kuni 10 protsenti "
            "juriidilise isiku käibest."
        )
        assert extract_turnover_percentage(text) == [{
            "sanction_type": "pecuniary_punishment",
            "min_penalty": "5 % of turnover",
            "max_penalty": "10 % of turnover",
        }]

    def test_decimal_comma_is_normalised_to_a_dot(self):
        text = "karistatakse rahalise karistusega kuni 2,5 protsenti käibest."
        assert extract_turnover_percentage(text)[0]["max_penalty"] == (
            "2.5 % of turnover"
        )

    def test_no_match_without_kaibest(self):
        text = "karistatakse rahalise karistusega kuni 5 protsenti summast."
        assert extract_turnover_percentage(text) == []

    def test_structured_fields(self):
        parsed = _parse_penalty_to_structured("5 % of turnover")
        assert parsed is not None
        amount, unit, currency = parsed
        assert str(amount) == "5"
        assert unit == PENALTY_UNIT_PERCENT_OF_TURNOVER
        # A relative ceiling has no currency.
        assert currency is None

    def test_structured_block_omits_currency(self):
        fields, unparsed = _structured_penalty_fields("10 % of turnover", "max")
        assert unparsed == 0
        assert fields == {
            "estleg:maxPenaltyAmount": {"@value": "10", "@type": "xsd:decimal"},
            "estleg:maxPenaltyUnit": "percent_of_turnover",
        }
        assert "estleg:maxPenaltyCurrency" not in fields

    def test_unit_is_in_the_controlled_set(self):
        assert PENALTY_UNIT_PERCENT_OF_TURNOVER in PENALTY_UNITS


# --------------------------------------------------------------------- #
# Lõige splitting
# --------------------------------------------------------------------- #


class TestSplitLoiked:

    def test_text_without_markers_is_returned_whole(self):
        assert split_loiked(KARS_114) == [KARS_114]

    def test_each_chunk_starts_at_its_marker(self):
        chunks = split_loiked(KARS_141)
        assert len(chunks) == 3
        assert [c[:3] for c in chunks] == ["(1)", "(2)", "(3)"]

    def test_preamble_before_the_first_marker_is_kept(self):
        chunks = split_loiked("Sissejuhatus. (1) Esimene. (2) Teine.")
        assert chunks[0] == "Sissejuhatus."
        assert len(chunks) == 3

    def test_parenthesised_year_is_not_a_loige_marker(self):
        text = "Viide seadusele (2003) ja karistus kuni üheaastase vangistusega."
        assert split_loiked(text) == [text]

    def test_output_order_is_deterministic(self):
        assert [
            (r["sanction_type"], r.get("min_penalty"), r.get("max_penalty"))
            for r in extract_sanctions(KARS_400)
        ] == [
            (r["sanction_type"], r.get("min_penalty"), r.get("max_penalty"))
            for r in extract_sanctions(KARS_400)
        ]


# --------------------------------------------------------------------- #
# SHACL: statutory bounds (issue #681) and deprecated acts (issue #682)
# --------------------------------------------------------------------- #

_CTX = {
    "estleg": "https://w3id.org/estleg/",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "owl": "http://www.w3.org/2002/07/owl#",
}

# Both validator surfaces documented in shacl/README.md: the bucket gate
# runs pyshacl with RDFS inference, the sync gate with none. A shape that
# behaves differently between them is a policy violation, so every bound
# below is asserted under both.
_INFERENCE_MODES = ("rdfs", "none")


def _shapes_graph():
    rdflib = pytest.importorskip("rdflib")
    graph = rdflib.Graph()
    graph.parse(
        str(REPO_ROOT / "shacl" / "estonian_legal_shapes.ttl"), format="turtle"
    )
    return graph


def _validate(node: dict, inference: str):
    pyshacl = pytest.importorskip("pyshacl")
    rdflib = pytest.importorskip("rdflib")
    data = rdflib.Graph()
    data.parse(
        data=json.dumps({"@context": _CTX, "@graph": [node]}), format="json-ld"
    )
    conforms, _, message = pyshacl.validate(
        data,
        shacl_graph=_shapes_graph(),
        inference=inference,
        abort_on_first=False,
    )
    return conforms, message


def _sanction(**overrides) -> dict:
    node = {
        "@id": "estleg:Sanction_TEST_681",
        "@type": ["owl:NamedIndividual", "estleg:Sanction"],
        "rdfs:label": "Test sanction (issue #681)",
        "estleg:sanctionType": "imprisonment",
        "estleg:applicableProvision": {"@id": "estleg:Test_Par_1"},
    }
    node.update(overrides)
    return node


def _decimal(value: str) -> dict:
    return {"@value": value, "@type": "xsd:decimal"}


class TestShapesFileParses:

    def test_turtle_is_valid(self):
        assert len(_shapes_graph()) > 0

    def test_new_shapes_are_declared(self):
        rdflib = pytest.importorskip("rdflib")
        graph = _shapes_graph()
        SH = rdflib.Namespace("http://www.w3.org/ns/shacl#")
        ESTLEG = rdflib.Namespace("https://w3id.org/estleg/")
        for name in (
            "SanctionImprisonmentMaxYearsShape",
            "SanctionArrestMaxDaysShape",
            "SanctionDailyRatesMaxShape",
            "DeprecatedActNotInForceShape",
        ):
            assert (ESTLEG[name], rdflib.RDF.type, SH.NodeShape) in graph, name

    def test_every_new_shape_carries_a_message(self):
        rdflib = pytest.importorskip("rdflib")
        graph = _shapes_graph()
        SH = rdflib.Namespace("http://www.w3.org/ns/shacl#")
        ESTLEG = rdflib.Namespace("https://w3id.org/estleg/")
        for name in (
            "SanctionImprisonmentMaxYearsShape",
            "SanctionArrestMaxDaysShape",
            "SanctionDailyRatesMaxShape",
            "DeprecatedActNotInForceShape",
        ):
            assert graph.value(ESTLEG[name], SH.message) is not None, name

    def test_no_sparql_constraints_were_introduced(self):
        """The bounds must stay core SHACL so both inference surfaces
        agree (shacl/README.md)."""
        rdflib = pytest.importorskip("rdflib")
        graph = _shapes_graph()
        SH = rdflib.Namespace("http://www.w3.org/ns/shacl#")
        assert len(list(graph.subject_objects(SH.sparql))) == 0


@pytest.mark.parametrize("inference", _INFERENCE_MODES)
class TestSanctionStatutoryBounds:

    def test_conforming_imprisonment_validates(self, inference):
        conforms, message = _validate(_sanction(
            **{
                "estleg:maxPenalty": "20 years",
                "estleg:minPenalty": "8 years",
                "estleg:maxPenaltyAmount": _decimal("20"),
                "estleg:maxPenaltyUnit": "years",
                "estleg:minPenaltyAmount": _decimal("8"),
                "estleg:minPenaltyUnit": "years",
            }
        ), inference)
        assert conforms, message

    def test_imprisonment_over_twenty_years_is_rejected(self, inference):
        conforms, _ = _validate(_sanction(
            **{
                "estleg:maxPenalty": "25 years",
                "estleg:maxPenaltyAmount": _decimal("25"),
                "estleg:maxPenaltyUnit": "years",
            }
        ), inference)
        assert not conforms, "25 years exceeds the KarS § 45 ceiling"

    def test_arrest_over_thirty_days_is_rejected(self, inference):
        conforms, _ = _validate(_sanction(
            **{
                "estleg:sanctionType": "arrest",
                "estleg:maxPenalty": "45 days",
                "estleg:maxPenaltyAmount": _decimal("45"),
                "estleg:maxPenaltyUnit": "days",
            }
        ), inference)
        assert not conforms, "45 days exceeds the KarS § 48 ceiling"

    def test_daily_rates_over_five_hundred_is_rejected(self, inference):
        conforms, _ = _validate(_sanction(
            **{
                "estleg:sanctionType": "pecuniary_punishment",
                "estleg:maxPenalty": "600 daily rates",
                "estleg:maxPenaltyAmount": _decimal("600"),
                "estleg:maxPenaltyUnit": "daily_rates",
            }
        ), inference)
        assert not conforms, "600 daily rates exceeds the KarS § 44 ceiling"

    def test_inverted_range_is_rejected(self, inference):
        conforms, _ = _validate(_sanction(
            **{
                "estleg:maxPenalty": "5 years",
                "estleg:minPenalty": "10 years",
                "estleg:maxPenaltyAmount": _decimal("5"),
                "estleg:maxPenaltyUnit": "years",
                "estleg:minPenaltyAmount": _decimal("10"),
                "estleg:minPenaltyUnit": "years",
            }
        ), inference)
        assert not conforms, "minPenaltyAmount must not exceed maxPenaltyAmount"

    def test_boundary_values_conform(self, inference):
        for stype, unit, amount in (
            ("imprisonment", "years", "20"),
            ("arrest", "days", "30"),
            ("pecuniary_punishment", "daily_rates", "500"),
        ):
            conforms, message = _validate(_sanction(
                **{
                    "estleg:sanctionType": stype,
                    "estleg:maxPenaltyAmount": _decimal(amount),
                    "estleg:maxPenaltyUnit": unit,
                }
            ), inference)
            assert conforms, f"{amount} {unit} is exactly the ceiling: {message}"

    def test_ceilings_do_not_fire_on_a_different_unit(self, inference):
        """A 25-MONTH sentence is not 25 years; the years ceiling must
        not apply to it."""
        conforms, message = _validate(_sanction(
            **{
                "estleg:maxPenalty": "25 months",
                "estleg:maxPenaltyAmount": _decimal("25"),
                "estleg:maxPenaltyUnit": "months",
            }
        ), inference)
        assert conforms, message

    def test_ceilings_do_not_fire_on_a_different_type(self, inference):
        """A 20,000,000 EUR coercive payment is not an imprisonment
        term, an arrest or a daily-rate count."""
        conforms, message = _validate(_sanction(
            **{
                "estleg:sanctionType": "coercive_payment",
                "estleg:maxPenalty": "20000000 EUR",
                "estleg:maxPenaltyAmount": _decimal("20000000"),
                "estleg:maxPenaltyUnit": "monetary",
                "estleg:maxPenaltyCurrency": "EUR",
            }
        ), inference)
        assert conforms, message

    def test_life_imprisonment_still_conforms(self, inference):
        conforms, message = _validate(_sanction(
            **{"estleg:maxPenalty": "life"}
        ), inference)
        assert conforms, message


@pytest.mark.parametrize("inference", _INFERENCE_MODES)
class TestNewSanctionVocabularyAccepted:

    @pytest.mark.parametrize(
        "sanction_type", ["confiscation", "compulsory_dissolution"]
    )
    def test_new_types_validate(self, inference, sanction_type):
        conforms, message = _validate(_sanction(
            **{"estleg:sanctionType": sanction_type}
        ), inference)
        assert conforms, message

    def test_turnover_unit_validates(self, inference):
        conforms, message = _validate(_sanction(
            **{
                "estleg:sanctionType": "pecuniary_punishment",
                "estleg:maxPenalty": "10 % of turnover",
                "estleg:minPenalty": "5 % of turnover",
                "estleg:maxPenaltyAmount": _decimal("10"),
                "estleg:maxPenaltyUnit": "percent_of_turnover",
                "estleg:minPenaltyAmount": _decimal("5"),
                "estleg:minPenaltyUnit": "percent_of_turnover",
            }
        ), inference)
        assert conforms, message

    def test_out_of_set_type_still_rejected(self, inference):
        conforms, _ = _validate(_sanction(
            **{"estleg:sanctionType": "community_service"}
        ), inference)
        assert not conforms


@pytest.mark.parametrize("inference", _INFERENCE_MODES)
class TestDeprecatedActNotInForce:
    """Issue #682: a retired Act IRI must keep temporalStatus
    "unknown"; it must never claim to be in force."""

    def _act(self, **overrides) -> dict:
        node = {
            "@id": "estleg:TEST_Map",
            "@type": ["owl:NamedIndividual", "estleg:Act"],
            "rdfs:label": "Test act (issue #682)",
        }
        node.update(overrides)
        return node

    def test_deprecated_and_in_force_is_rejected(self, inference):
        conforms, _ = _validate(self._act(**{
            "owl:deprecated": True,
            "estleg:temporalStatus": "inForce",
        }), inference)
        assert not conforms, (
            "a deprecated Act must not also be marked inForce"
        )

    def test_deprecated_and_unknown_conforms(self, inference):
        conforms, message = _validate(self._act(**{
            "owl:deprecated": True,
            "estleg:temporalStatus": "unknown",
        }), inference)
        assert conforms, message

    def test_non_deprecated_and_in_force_conforms(self, inference):
        conforms, message = _validate(self._act(**{
            "estleg:temporalStatus": "inForce",
        }), inference)
        assert conforms, message

    def test_deprecated_false_and_in_force_conforms(self, inference):
        conforms, message = _validate(self._act(**{
            "owl:deprecated": False,
            "estleg:temporalStatus": "inForce",
        }), inference)
        assert conforms, message

    def test_deprecated_with_no_temporal_status_conforms(self, inference):
        conforms, message = _validate(self._act(**{
            "owl:deprecated": True,
        }), inference)
        assert conforms, message


class TestStaleInlineSanctionAnchorsArePurged:
    """Peeps used to carry minimal inline ``estleg:Sanction`` anchor nodes
    (``@id`` / ``@type`` / ``estleg:sanctionType`` only) alongside the
    ``estleg:hasSanction`` edges. The extractor never wrote them and never
    cleared them, so a regeneration that no longer emits a given sanction
    left its anchor behind as an orphan — 482 such nodes reached the
    combined artifact as label-less, provision-less Sanction nodes. The
    canonical Sanction nodes live in the ``sanctions/`` sidecar; the peep
    must carry none."""

    _CONTEXT = {
        "estleg": "https://w3id.org/estleg/",
        "owl": "http://www.w3.org/2002/07/owl#",
        "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    }

    def _stage(self, tmp_path, *, with_offence: bool):
        krr = tmp_path / "krr_outputs"
        sanctions_dir = krr / "sanctions"
        sanctions_dir.mkdir(parents=True)
        peep = krr / "test_seadus_peep.json"
        graph = [
            {"@id": "estleg:TS_Map", "@type": ["owl:Ontology", "estleg:Act"],
             "rdfs:label": "Testseadus"},
            {"@id": "estleg:TS_Par_43",
             "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
             "estleg:paragrahv": "§ 43.",
             "estleg:legalText": (
                 "Käesoleva seaduse nõuete rikkumise eest karistatakse "
                 "rahatrahviga kuni 300 trahviühikut."
                 if with_offence else
                 "Käesolev seadus jõustub 2026. aasta 1. jaanuaril."
             ),
             # Stale edge from a previous run.
             "estleg:hasSanction": [
                 {"@id": "estleg:Sanction_TS_Par_43_arrest_2"}],
             "estleg:enforcedAtLevel": "state"},
            # Stale inline anchors from a previous run: one for the stale
            # edge above, one already orphaned.
            {"@id": "estleg:Sanction_TS_Par_43_arrest_2",
             "@type": ["estleg:Sanction"],
             "estleg:sanctionType": "arrest"},
            {"@id": "estleg:Sanction_TS_Par_43_fine",
             "@type": ["estleg:Sanction"],
             "estleg:sanctionType": "fine"},
        ]
        peep.write_text(
            json.dumps({"@context": self._CONTEXT, "@graph": graph}),
            encoding="utf-8",
        )
        return krr, sanctions_dir, peep

    def _run(self, tmp_path, monkeypatch, *, with_offence: bool):
        from estleg import estleg_common
        from estleg import extract_sanctions as mod
        krr, sanctions_dir, peep = self._stage(tmp_path, with_offence=with_offence)
        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "SANCTION_DIR", sanctions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        assert mod.main() in (0, None)
        return json.loads(peep.read_text(encoding="utf-8")), sanctions_dir

    @staticmethod
    def _sanction_nodes(doc):
        return [
            n for n in doc["@graph"]
            if "estleg:Sanction" in (n.get("@type") or [])
        ]

    def test_anchors_are_removed_and_edges_point_at_the_sidecar(
        self, tmp_path, monkeypatch
    ):
        saved, sanctions_dir = self._run(tmp_path, monkeypatch, with_offence=True)
        assert self._sanction_nodes(saved) == []
        prov = next(n for n in saved["@graph"] if n["@id"] == "estleg:TS_Par_43")
        refs = {r["@id"] for r in prov["estleg:hasSanction"]}
        assert refs == {"estleg:Sanction_TS_Par_43_fine"}
        sidecar = json.loads(
            (sanctions_dir / "sanctions_test_seadus.json").read_text(encoding="utf-8")
        )
        emitted = {n["@id"] for n in sidecar["@graph"] if "estleg:sanctionType" in n}
        assert emitted == refs

    def test_peep_with_only_stale_anchors_is_rewritten_clean(
        self, tmp_path, monkeypatch
    ):
        saved, sanctions_dir = self._run(tmp_path, monkeypatch, with_offence=False)
        assert self._sanction_nodes(saved) == []
        prov = next(n for n in saved["@graph"] if n["@id"] == "estleg:TS_Par_43")
        assert "estleg:hasSanction" not in prov
        assert "estleg:enforcedAtLevel" not in prov
        assert not (sanctions_dir / "sanctions_test_seadus.json").exists()

    def test_is_inline_sanction_node_ignores_other_types(self):
        from estleg.extract_sanctions import _is_inline_sanction_node
        assert _is_inline_sanction_node(
            {"@id": "estleg:Sanction_X", "@type": ["estleg:Sanction"]}
        )
        assert _is_inline_sanction_node(
            {"@id": "estleg:Sanction_X", "@type": "estleg:Sanction"}
        )
        assert not _is_inline_sanction_node(
            {"@id": "estleg:X_Par_1", "@type": ["estleg:LegalProvision"],
             "estleg:hasSanction": [{"@id": "estleg:Sanction_X"}]}
        )
        assert not _is_inline_sanction_node("not a node")


@pytest.mark.parametrize('text', [
    'Taotleja ei ole pankrotis, likvideerimisel ega sundlõpetamisel.',
    'Registrisse kantakse andmed sundlõpetamise kohta.',
    '(1) Rikkumise eest karistatakse rahatrahviga. (2) Sundlõpetamise kohta peetakse registrit.',
])
def test_dissolution_mentions_are_not_penalties(text):
    assert extract_dissolution(text) == []
    assert not any(s['sanction_type'] == 'compulsory_dissolution' for s in extract_sanctions(text))


@pytest.mark.parametrize('text', [
    'Ettevõtlustulust ei ole lubatud maha arvata maksumaksjalt erikonfiskeeritud vara maksumust.',
    'Registrisse kantakse konfiskeerimise andmed.',
    'Isikul on ese, mille võib võtta seaduse alusel hoiule, hõivata või konfiskeerida.',
    'Vara ei konfiskeerita.',
])
def test_confiscation_mentions_are_not_penalties(text):
    assert extract_confiscation(text) == []


@pytest.mark.parametrize('text', [
    'Kohus konfiskeerib süüteo toimepanemise vahendi.',
    'Kohus võib konfiskeerida süüteo toimepanemise vahendi.',
    'Käesoleva paragrahvi rikkumise korral konfiskeeritakse ese.',
    'Kohus võib kohaldada kuriteoga saadud vara laiendatud konfiskeerimist.',
])
def test_operative_confiscation_is_preserved(text):
    assert extract_confiscation(text) == [{'sanction_type': 'confiscation'}]


def test_committed_sanctions_do_not_turn_eligibility_or_tax_rules_into_penalties():
    from pathlib import Path
    import json

    root = Path(__file__).resolve().parents[1] / 'krr_outputs' / 'sanctions'
    excluded = {
        ('estleg:Reg_1061969_Par_8', 'compulsory_dissolution'),
        ('estleg:TULUMA_Par_34_Lg_1', 'confiscation'),
        ('estleg:KARIST_2_Osa2_Par_308', 'confiscation'),
    }
    for path in root.glob('*.json'):
        for node in json.loads(path.read_text()).get('@graph', []):
            pair = (node.get('estleg:applicableProvision', {}).get('@id'),
                    node.get('estleg:sanctionType'))
            assert pair not in excluded, (path.name, pair)


@pytest.mark.parametrize('inference', _INFERENCE_MODES)
def test_range_ordering_does_not_compare_different_units(inference):
    conforms, message = _validate(_sanction(**{
        'estleg:minPenalty': '30 days', 'estleg:maxPenalty': '1 years',
        'estleg:minPenaltyAmount': _decimal('30'), 'estleg:maxPenaltyAmount': _decimal('1'),
        'estleg:minPenaltyUnit': 'days', 'estleg:maxPenaltyUnit': 'years',
    }), inference)
    assert conforms, message

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from generate_draft_legislation import (
    classify_draft_type,
    detect_affected_laws,
    generate_draft_node,
)


# --------------------------------------------------------------------- #
# Issue #304 — classify_draft_type must test the väljatöötamiskavatsus
# (DraftIntent) guard BEFORE the broad ``seadus`` arm. A pre-draft
# intention title routinely embeds a law name (e.g.
# "Kliimaseaduse eelnõu väljatöötamise kavatsus") and was being mis-typed
# as DraftType_Bill.
# --------------------------------------------------------------------- #


class TestClassifyDraftTypeIntentBeforeBill:
    def test_vtk_with_embedded_law_name_is_draft_intent_not_bill(self):
        # Exact evidence case from #304 (estleg:Draft_KLIM23_1259).
        type_id, _label = classify_draft_type(
            "Kliimaseaduse eelnõu väljatöötamise kavatsus"
        )
        assert type_id == "DraftIntent", type_id

    def test_bare_kavatsus_is_draft_intent(self):
        type_id, _label = classify_draft_type(
            "Mingi seaduse muutmise väljatöötamiskavatsus"
        )
        assert type_id == "DraftIntent", type_id

    def test_vtk_node_gets_draftintent_individual_iri(self):
        # End-to-end through generate_draft_node: the draftType IRI must
        # resolve to DraftType_DraftIntent, not DraftType_Bill.
        node = generate_draft_node(
            {
                "title": "Kliimaseaduse eelnõu väljatöötamise kavatsus",
                "link": "https://eelnoud.valitsus.ee/main/mount/docList/"
                "12345678-1234-1234-1234-123456789abc?activity=1",
            },
            phase_id="PublicConsultation",
            eis_number="KLIM/23-1259",
            ministry_code="KLIM",
            date_str="01.02.2026",
        )
        assert node["estleg:draftType"] == {
            "@id": "estleg:DraftType_DraftIntent"
        }, node["estleg:draftType"]

    def test_plain_bill_still_classifies_as_bill(self):
        # Regression guard: an ordinary bill (no kavatsus/väljatöötamis
        # marker) is still typed Bill, so moving the guard up did not
        # swallow the seadus arm.
        type_id, _label = classify_draft_type("Alkoholiseaduse eelnõu")
        assert type_id == "Bill", type_id

    def test_amendment_bill_still_classifies_as_amendment(self):
        type_id, _label = classify_draft_type(
            "Riigi Teataja seaduse muutmise seadus"
        )
        assert type_id == "AmendmentBill", type_id


# --------------------------------------------------------------------- #
# Issue #382 — estleg:initiator must be a plain xsd:string, matching its
# rdfs:range and the SHACL DraftLegislationShape (sh:datatype xsd:string),
# NOT a {@value,@language} language-tagged value-object.
# --------------------------------------------------------------------- #


class TestInitiatorIsPlainString:
    def test_initiator_is_plain_string(self):
        node = generate_draft_node(
            {
                "title": "Alkoholiseaduse muutmise seadus",
                "link": "https://eelnoud.valitsus.ee/main/mount/docList/"
                "12345678-1234-1234-1234-123456789abc?activity=2",
            },
            phase_id="Review",
            eis_number="JDM/26-0214",
            ministry_code="JDM",
            date_str="17.02.2026",
        )
        assert node["estleg:initiator"] == "Justiitsministeerium"
        assert isinstance(node["estleg:initiator"], str)

    def test_unknown_ministry_code_passthrough_is_plain_string(self):
        # Unmapped codes fall back to the raw code; still a plain string.
        node = generate_draft_node(
            {
                "title": "Mingi määruse eelnõu",
                "link": "https://eelnoud.valitsus.ee/main/mount/docList/"
                "abcdef00-1111-2222-3333-444455556666?activity=1",
            },
            phase_id="Submission",
            eis_number="ZZZ/26-0001",
            ministry_code="ZZZ",
            date_str="03.03.2026",
        )
        assert node["estleg:initiator"] == "ZZZ"
        assert isinstance(node["estleg:initiator"], str)


def test_generate_draft_node_emits_affected_law_names_as_strings():
    node = generate_draft_node(
        {
            "title": "Riigi Teataja seaduse muutmise seadus",
            "link": "https://eelnoud.valitsus.ee/main/mount/docList/12345678-1234-1234-1234-123456789abc?activity=2",
        },
        phase_id="Review",
        eis_number="JDM/26-0214",
        ministry_code="JDM",
        date_str="17.02.2026",
    )

    assert node["estleg:affectedLawName"]
    assert all(isinstance(value, str) for value in node["estleg:affectedLawName"])


# --------------------------------------------------------------------- #
# Regression tests for issue #266: detect_affected_laws must NOT emit the
# bill's own full title (".. muutmise seadus") as an affected law, because
# it resolves to the same IRI as the real target and yields duplicate
# ``amendsLaw`` IRIs downstream.
# --------------------------------------------------------------------- #


class TestDetectAffectedLawsDropsBillTitle:
    def test_amendment_title_yields_only_real_target(self):
        # The bare ``...seadus`` pattern would also match the whole bill
        # title "Riigi Teataja seaduse muutmise seadus". That candidate
        # carries the change-verb stem "muutmi" and must be dropped.
        result = detect_affected_laws("Riigi Teataja seaduse muutmise seadus")
        assert result == ["Riigi Teataja seaduse"], result
        # The bill title itself must not appear.
        assert "Riigi Teataja seaduse muutmise seadus" not in result
        # No candidate may still carry a change-verb stem.
        for name in result:
            assert "muutmi" not in name.lower()

    def test_supplements_title_yields_only_real_target(self):
        result = detect_affected_laws("Maksukorralduse seaduse täiendamise seadus")
        assert result == ["Maksukorralduse seaduse"], result
        for name in result:
            assert "täiendami" not in name.lower()

    def test_repeal_title_drops_kehtetuks_candidate(self):
        # The only candidate the bare-seadus pattern yields for this title is
        # the whole bill title, which carries the "kehtetuks" stem and must be
        # dropped — leaving no phantom. (These patterns capture a genitive
        # real-target only for muutmi/täiendami, so the result is empty here,
        # which is correct: no bill-title leak.)
        result = detect_affected_laws(
            "Mingi seaduse kehtetuks tunnistamise seadus"
        )
        assert "Mingi seaduse kehtetuks tunnistamise seadus" not in result
        for name in result:
            assert "kehtetuks" not in name.lower()

    def test_enacts_title_drops_kehtestami_candidate(self):
        result = detect_affected_laws("Mingi seaduse kehtestamise seadus")
        assert "Mingi seaduse kehtestamise seadus" not in result
        for name in result:
            assert "kehtestami" not in name.lower()

    def test_plain_law_title_still_detected(self):
        # A non-amendment title (no change verb) with a space before
        # "seadus" still resolves normally and is not dropped.
        result = detect_affected_laws("Alkoholi seadus")
        assert "Alkoholi seadus" in result

    def test_node_amends_law_has_no_duplicate_iri_inputs(self):
        # End-to-end on the node: affectedLawName must not contain both the
        # real target and the bill title (which would resolve to one IRI and
        # be appended twice in extract_draft_impact).
        node = generate_draft_node(
            {
                "title": "Riigi Teataja seaduse muutmise seadus",
                "link": "https://eelnoud.valitsus.ee/main/mount/docList/"
                "12345678-1234-1234-1234-123456789abc?activity=2",
            },
            phase_id="Review",
            eis_number="JDM/26-0214",
            ministry_code="JDM",
            date_str="17.02.2026",
        )
        names = node["estleg:affectedLawName"]
        # Exactly one distinct target; no duplicate that resolves to same IRI.
        assert len(names) == len(set(names))
        assert names == ["Riigi Teataja seaduse"], names


# --------------------------------------------------------------------- #
# Issue #295 — EELNOUD_INDEX.json must not embed a wall-clock ``generated``
# field, so reruns of the same inputs are byte-stable.
# --------------------------------------------------------------------- #


class TestIndexIsByteStable:
    _SYNTHETIC_ITEM = {
        "raw_title": "Alkoholiseaduse muutmise seadus - JDM/26-0001 (01.02.2026)",
        "title": "Alkoholiseaduse muutmise seadus",
        "link": "https://eelnoud.valitsus.ee/main/mount/docList/"
        "11111111-2222-3333-4444-555555555555?activity=1",
        "pub_date": "Mon, 02 Feb 2026 00:00:00 +0200",
    }

    def _run(self, tmp_path, monkeypatch):
        import generate_draft_legislation as mod

        eelnoud = tmp_path / "eelnoud"
        eelnoud.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(mod, "EELNOUD_DIR", eelnoud)
        # main()'s closing summary prints EELNOUD_DIR.relative_to(REPO_ROOT);
        # point REPO_ROOT at tmp_path so that stays valid under the tmp dir.
        monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
        # Only the first feed yields the synthetic item; the rest are empty
        # so the dedup key is hit once.
        calls = {"n": 0}

        def fake_fetch(_url):
            calls["n"] += 1
            return [dict(self._SYNTHETIC_ITEM)] if calls["n"] == 1 else []

        monkeypatch.setattr(mod, "fetch_rss", fake_fetch)
        mod.main()
        return eelnoud / "EELNOUD_INDEX.json"

    def test_index_has_no_generated_timestamp(self, tmp_path, monkeypatch):
        index_path = self._run(tmp_path, monkeypatch)
        index = json.loads(index_path.read_text(encoding="utf-8"))
        assert "generated" not in index, (
            "EELNOUD_INDEX.json must not embed a wall-clock 'generated' "
            "timestamp (#295 churn)"
        )
        # Sanity: the synthetic draft was still indexed.
        assert index["total_drafts"] == 1

    def test_two_runs_are_byte_identical(self, tmp_path, monkeypatch):
        first = self._run(tmp_path, monkeypatch).read_text(encoding="utf-8")
        second = self._run(tmp_path, monkeypatch).read_text(encoding="utf-8")
        assert first == second


# --------------------------------------------------------------------- #
# Issue #341 (item 12) — draft nodes must be written in a stable order
# (sorted by @id), not RSS arrival order, so the per-phase peeps, the
# combined .jsonld and the index don't churn between runs.
# --------------------------------------------------------------------- #


def _item(eis_number: str, date_str: str) -> dict:
    """A synthetic RSS item whose @id is derived from ``eis_number``."""
    return {
        "raw_title": f"Mingi seaduse muutmise seadus - {eis_number} ({date_str})",
        "title": "Mingi seaduse muutmise seadus",
        "link": "https://eelnoud.valitsus.ee/main/mount/docList/"
        "11111111-2222-3333-4444-555555555555?activity=1",
        "pub_date": "Mon, 02 Feb 2026 00:00:00 +0200",
    }


class TestDraftOutputIsSortedById:
    # EIS numbers fed to the first feed in deliberately DESCENDING @id order
    # (Draft_JDM_26_0099 > _0050 > _0001). A correct generator re-sorts the
    # written nodes ascending by @id.
    _RSS_ORDER = [
        _item("JDM/26-0099", "03.02.2026"),
        _item("JDM/26-0050", "02.02.2026"),
        _item("JDM/26-0001", "01.02.2026"),
    ]
    # sanitize_id strips the "/" and turns "-" into "_": "JDM/26-0099"
    # -> "JDM26_0099". The point of the test is the ASCENDING order, not the
    # exact spelling, but pin the spelling so a regression in sanitize_id is
    # also caught.
    _EXPECTED_IDS = [
        "estleg:Draft_JDM26_0001",
        "estleg:Draft_JDM26_0050",
        "estleg:Draft_JDM26_0099",
    ]

    def _run(self, tmp_path, monkeypatch):
        import generate_draft_legislation as mod

        eelnoud = tmp_path / "eelnoud"
        eelnoud.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(mod, "EELNOUD_DIR", eelnoud)
        monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)

        # All three drafts arrive on the first feed; the other two are empty.
        calls = {"n": 0}

        def fake_fetch(_url):
            calls["n"] += 1
            return [dict(it) for it in self._RSS_ORDER] if calls["n"] == 1 else []

        monkeypatch.setattr(mod, "fetch_rss", fake_fetch)
        mod.main()
        return eelnoud

    @staticmethod
    def _draft_ids(graph: list[dict]) -> list[str]:
        """The @ids of just the Draft_* individuals, in file order."""
        return [
            n["@id"]
            for n in graph
            if isinstance(n.get("@id"), str) and n["@id"].startswith("estleg:Draft_")
        ]

    def test_phase_peep_draft_nodes_are_sorted_by_id(self, tmp_path, monkeypatch):
        eelnoud = self._run(tmp_path, monkeypatch)
        peep = json.loads(
            (eelnoud / "eelnoud_publicconsultation_peep.json").read_text(
                encoding="utf-8"
            )
        )
        ids = self._draft_ids(peep["@graph"])
        assert ids == self._EXPECTED_IDS, ids

    def test_combined_draft_nodes_are_sorted_by_id(self, tmp_path, monkeypatch):
        eelnoud = self._run(tmp_path, monkeypatch)
        combined = json.loads(
            (eelnoud / "eelnoud_combined.jsonld").read_text(encoding="utf-8")
        )
        ids = self._draft_ids(combined["@graph"])
        assert ids == self._EXPECTED_IDS, ids

    def test_index_drafts_are_sorted_by_id(self, tmp_path, monkeypatch):
        # The index carries eis_number, not @id; sorting all_drafts by @id
        # places JDM/26-0001 first, ..., JDM/26-0099 last.
        eelnoud = self._run(tmp_path, monkeypatch)
        index = json.loads(
            (eelnoud / "EELNOUD_INDEX.json").read_text(encoding="utf-8")
        )
        eis = [d["eis_number"] for d in index["drafts"]]
        assert eis == ["JDM/26-0001", "JDM/26-0050", "JDM/26-0099"], eis

    def test_output_is_byte_identical_regardless_of_rss_order(
        self, tmp_path, monkeypatch
    ):
        # Run once with the descending RSS order, once with the reversed
        # (ascending) order; the combined file must be byte-identical.
        eelnoud_a = self._run(tmp_path, monkeypatch)
        combined_a = (eelnoud_a / "eelnoud_combined.jsonld").read_text(
            encoding="utf-8"
        )

        import generate_draft_legislation as mod

        eelnoud_b = tmp_path / "eelnoud_b"
        eelnoud_b.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(mod, "EELNOUD_DIR", eelnoud_b)
        monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
        calls = {"n": 0}

        def fake_fetch_reversed(_url):
            calls["n"] += 1
            if calls["n"] == 1:
                return [dict(it) for it in reversed(self._RSS_ORDER)]
            return []

        monkeypatch.setattr(mod, "fetch_rss", fake_fetch_reversed)
        mod.main()
        combined_b = (eelnoud_b / "eelnoud_combined.jsonld").read_text(
            encoding="utf-8"
        )
        assert combined_a == combined_b


class TestDetectAffectedLawsYearPrefix:
    """#380 — leading year token must be captured, not stripped."""

    def test_year_prefixed_law_name_keeps_year(self):
        from generate_draft_legislation import detect_affected_laws

        result = detect_affected_laws(
            "2016. aasta riigieelarve seaduse muutmise seadus"
        )
        # The year must stay in the affected-law name so it does not
        # mis-resolve to the corpus's only (2026) budget law.
        assert any("2016." in name for name in result)
        assert not any(name.lower().startswith("aasta ") for name in result)

    def test_plain_multiword_law_name_still_detected(self):
        from generate_draft_legislation import detect_affected_laws

        # The [\w.]+ leading-token change must not break ordinary multi-word
        # genitive detection.
        result = detect_affected_laws("Riigi Teataja seaduse muutmise seadus")
        assert any("riigi teataja seaduse" in name.lower() for name in result)


class TestClassifyDraftTypeRegulationBeforeBill:
    """#599 — `määrus`/`korraldus` form keywords must be checked BEFORE the
    broad ``seadus`` substring. A regulation draft routinely embeds its
    enabling law's name (``... seaduse alusel`` / ``... seaduse muutmine``),
    which previously matched the ``seadus`` arm first and mis-typed the
    regulation as a Bill / AmendmentBill (a regulation is legally not a bill,
    and the mis-type also routes it past bill-only impact analysis)."""

    def test_ministerial_regulation_with_enabling_law_is_not_bill(self):
        type_id, _label = classify_draft_type(
            "Sotsiaalministri määrus X seaduse alusel"
        )
        assert type_id == "MinisterialRegulation", type_id

    def test_government_regulation_amending_law_is_not_amendmentbill(self):
        # "... määruse ja Y seaduse muutmine" contains both "määrus" and
        # "seadus" + "muutmi"; the regulation arm must win.
        type_id, _label = classify_draft_type(
            "Vabariigi Valitsuse määruse ja Y seaduse muutmine"
        )
        assert type_id == "GovernmentRegulation", type_id

    def test_bare_regulation_title_is_regulation(self):
        type_id, _label = classify_draft_type(
            "Mingi valdkonna korraldamise määrus"
        )
        assert type_id == "Regulation", type_id

    def test_korraldus_with_seadus_reference_is_order(self):
        type_id, _label = classify_draft_type(
            "Vabariigi Valitsuse korraldus seaduse alusel"
        )
        assert type_id == "GovernmentOrder", type_id

    def test_plain_bill_still_classifies_as_bill(self):
        # Regression: a genuine bill (no määrus/korraldus) must still be Bill.
        type_id, _label = classify_draft_type("Kliimaseaduse eelnõu")
        assert type_id == "Bill", type_id

    def test_plain_amendment_bill_still_classifies(self):
        type_id, _label = classify_draft_type(
            "Tulumaksuseaduse muutmise seaduse eelnõu"
        )
        assert type_id == "AmendmentBill", type_id

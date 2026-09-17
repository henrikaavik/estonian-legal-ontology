import json
from pathlib import Path

from estleg.generate_draft_legislation import (
    TITLE_KEY_LEN,
    classify_draft_type,
    detect_affected_laws,
    generate_draft_node,
    sanitize_id,
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

    def test_draft_klim23_1259_in_publicconsultation_peep_is_draft_intent(self):
        # Committed corpus was stale (#304): Draft_KLIM23_1259 was typed
        # DraftType_Bill despite a VTK label. The peep must now match the
        # classifier.
        peep_path = (
            Path(__file__).resolve().parent.parent
            / "krr_outputs"
            / "eelnoud"
            / "eelnoud_publicconsultation_peep.json"
        )
        peep = json.loads(peep_path.read_text(encoding="utf-8"))
        node = next(
            n
            for n in peep["@graph"]
            if n.get("@id") == "estleg:Draft_KLIM23_1259"
        )
        assert node["estleg:draftType"] == {
            "@id": "estleg:DraftType_DraftIntent"
        }, node["estleg:draftType"]


# --------------------------------------------------------------------- #
# Issue #296 — title-only fallback @id and main() dedup must share one
# TITLE_KEY_LEN (60). generate_draft_node used title[:40] while main()
# used title[:60], so a title-only draft's @id slice did not match the
# key that would collapse duplicates.
# --------------------------------------------------------------------- #


class TestTitleKeyLen:
    @staticmethod
    def _long_title() -> str:
        # Distinct characters after index 40 so a 40-char slice would
        # produce a different sanitized @id than the 60-char prefix.
        return ("ABCDEFGHIJ" * 4) + ("KLMNOPQRST" * 2) + "UVWXYZ"

    def test_title_only_draft_uses_same_prefix_as_dedup_key(self):
        title = self._long_title()
        assert len(title) > TITLE_KEY_LEN
        assert TITLE_KEY_LEN == 60
        node = generate_draft_node(
            {
                "title": title,
                "link": "https://eelnoud.valitsus.ee/main/mount/docList/not-a-uuid",
            },
            phase_id="PublicConsultation",
            eis_number="",
            ministry_code="",
            date_str="",
        )
        prefix = title[:TITLE_KEY_LEN]
        assert node["@id"] == f"estleg:Draft_{sanitize_id(prefix)}"
        # main() dedup_key = eis_number or uuid or title[:TITLE_KEY_LEN].
        # With neither eis_number nor uuid, the key is this same prefix.
        dedup_key = title[:TITLE_KEY_LEN]
        assert dedup_key == prefix
        assert sanitize_id(dedup_key) == node["@id"].removeprefix("estleg:Draft_")
        # Prove the old 40-char fallback is gone.
        assert node["@id"] != f"estleg:Draft_{sanitize_id(title[:40])}"

    def test_main_dedups_title_only_drafts_on_same_sixty_char_prefix(
        self, tmp_path, monkeypatch
    ):
        from estleg import generate_draft_legislation as mod

        title_a = self._long_title()
        title_b = title_a[:TITLE_KEY_LEN] + "OTHER_TAIL"
        assert title_a[:TITLE_KEY_LEN] == title_b[:TITLE_KEY_LEN]
        assert title_a != title_b

        eelnoud = tmp_path / "eelnoud"
        eelnoud.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(mod, "EELNOUD_DIR", eelnoud)
        monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)

        items = [
            {
                "raw_title": title_a,
                "title": title_a,
                "link": "https://eelnoud.valitsus.ee/main/mount/docList/not-a-uuid-a",
                "pub_date": "",
            },
            {
                "raw_title": title_b,
                "title": title_b,
                "link": "https://eelnoud.valitsus.ee/main/mount/docList/not-a-uuid-b",
                "pub_date": "",
            },
        ]
        calls = {"n": 0}

        def fake_fetch(_url):
            calls["n"] += 1
            return [dict(it) for it in items] if calls["n"] == 1 else []

        monkeypatch.setattr(mod, "fetch_rss", fake_fetch)
        mod.main()

        index = json.loads(
            (eelnoud / "EELNOUD_INDEX.json").read_text(encoding="utf-8")
        )
        assert index["total_drafts"] == 1
        peep = json.loads(
            (eelnoud / "eelnoud_publicconsultation_peep.json").read_text(
                encoding="utf-8"
            )
        )
        draft_ids = [
            n["@id"]
            for n in peep["@graph"]
            if isinstance(n.get("@id"), str) and n["@id"].startswith("estleg:Draft_")
        ]
        assert draft_ids == [
            f"estleg:Draft_{sanitize_id(title_a[:TITLE_KEY_LEN])}"
        ]


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

    def test_unknown_ministry_code_marker_is_plain_string(self):
        # Issue #606 (item 9) changed the policy: an unmapped code is no
        # longer passed through as estleg:initiator. It is surfaced under
        # estleg:initiatorCodeUnmapped — which is still a plain xsd:string
        # (the #382 datatype guard applies to the marker too).
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
        assert "estleg:initiator" not in node
        assert node["estleg:initiatorCodeUnmapped"] == "ZZZ"
        assert isinstance(node["estleg:initiatorCodeUnmapped"], str)


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
        from estleg import generate_draft_legislation as mod

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
        from estleg import generate_draft_legislation as mod

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

        from estleg import generate_draft_legislation as mod

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
        from estleg.generate_draft_legislation import detect_affected_laws

        result = detect_affected_laws(
            "2016. aasta riigieelarve seaduse muutmise seadus"
        )
        # The year must stay in the affected-law name so it does not
        # mis-resolve to the corpus's only (2026) budget law.
        assert any("2016." in name for name in result)
        assert not any(name.lower().startswith("aasta ") for name in result)

    def test_plain_multiword_law_name_still_detected(self):
        from estleg.generate_draft_legislation import detect_affected_laws

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


# --------------------------------------------------------------------- #
# Issue #606 (item 9) — an unmapped EIS ministry code must NOT leak
# verbatim as estleg:initiator (after the 2023 reorg new codes appear).
# A KNOWN code maps to its ministry name; an unknown non-empty code is
# surfaced under estleg:initiatorCodeUnmapped plus a stderr warning; an
# empty code yields neither property.
# --------------------------------------------------------------------- #


class TestInitiatorUnmappedCode:
    @staticmethod
    def _node(ministry_code: str, eis_number: str) -> dict:
        return generate_draft_node(
            {
                "title": "Mingi seaduse muutmise seadus",
                "link": "https://eelnoud.valitsus.ee/main/mount/docList/"
                "12345678-1234-1234-1234-123456789abc?activity=1",
            },
            phase_id="Review",
            eis_number=eis_number,
            ministry_code=ministry_code,
            date_str="15.03.2024",
        )

    def test_known_code_sets_initiator_and_no_marker(self, capsys):
        node = self._node("JDM", "JDM/24-0001")
        assert node["estleg:initiator"] == "Justiitsministeerium"
        assert "estleg:initiatorCodeUnmapped" not in node
        # A known code must not emit a warning.
        assert "WARNING" not in capsys.readouterr().err

    def test_unknown_code_sets_marker_not_initiator_and_warns(self, capsys):
        node = self._node("XYZ", "XYZ/24-0001")
        assert "estleg:initiator" not in node
        assert node["estleg:initiatorCodeUnmapped"] == "XYZ"
        assert isinstance(node["estleg:initiatorCodeUnmapped"], str)
        err = capsys.readouterr().err
        assert "WARNING" in err
        assert "XYZ" in err

    def test_empty_code_sets_neither_property(self, capsys):
        node = self._node("", "")
        assert "estleg:initiator" not in node
        assert "estleg:initiatorCodeUnmapped" not in node
        assert "WARNING" not in capsys.readouterr().err


# --------------------------------------------------------------------- #
# Issue #606 (item 13b) — publicationDate must be RANGE-checked, not just
# format-checked. EIS drafts are recent, so an implausible year
# (1900/2099) must be dropped; a recent valid date is kept. The upper
# bound tracks datetime.now().year + 1, never a hardcoded future year.
# --------------------------------------------------------------------- #


class TestPublicationDateRangeGuard:
    @staticmethod
    def _node(date_str: str) -> dict:
        return generate_draft_node(
            {
                "title": "Mingi seaduse muutmise seadus",
                "link": "https://eelnoud.valitsus.ee/main/mount/docList/"
                "12345678-1234-1234-1234-123456789abc?activity=1",
            },
            phase_id="Review",
            eis_number="JDM/24-0001",
            ministry_code="JDM",
            date_str=date_str,
        )

    def test_year_1900_is_rejected(self):
        node = self._node("01.01.1900")
        assert "estleg:publicationDate" not in node

    def test_year_2099_is_rejected(self):
        node = self._node("31.12.2099")
        assert "estleg:publicationDate" not in node

    def test_recent_valid_date_is_kept(self):
        node = self._node("15.03.2024")
        assert node["estleg:publicationDate"] == {
            "@value": "2024-03-15",
            "@type": "xsd:date",
        }

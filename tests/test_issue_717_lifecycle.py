"""#717 — draft lifecycle (eli-dl:ProcessStep), initiatedBy, provenance stamps.

Unit tests for the lifecycle machinery in ``generate_draft_legislation`` and
the provenance helpers, plus contract gates over the committed drafts layer.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from estleg import generate_draft_legislation as gdl
from estleg.estleg_common import (
    DERIVATION_METHODS,
    add_derivation_method,
    derivation_methods,
    remove_derivation_method,
)

REPO = Path(__file__).resolve().parent.parent
EELNOUD = REPO / "krr_outputs" / "eelnoud"
INSTITUTIONS = REPO / "krr_outputs" / "institutions"


def _spec(phase: str, day: str, method: str = "eis-feed", status: str = "") -> dict:
    spec = {"phase": phase, "day": day, "method": method, "source": None, "label": f"{phase} {day}"}
    if status:
        spec["extra"] = {"estleg:riigikoguStatus": status}
    return spec


# --------------------------------------------------------------------------- #
# F1 / F2 (the #724 study's rights-free bugs)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Avaliku teabe seaduse § 32 1 muutmise seadus", ["Avaliku teabe seaduse"]),
        ("Erakooliseaduse § 22`2 muutmise seadus", ["Erakooliseaduse"]),
        ("Kodakondsuse seaduse § 28 täiendamise seadus", ["Kodakondsuse seaduse"]),
        ("Töölepingu seaduse § 10 ja § 12 lõike 2 muutmise seadus", ["Töölepingu seaduse"]),
    ],
)
def test_f1_section_clause_no_longer_hides_the_law(title, expected):
    assert gdl.detect_affected_laws(title) == expected


def test_f2_coordinated_phrase_splits_into_member_laws():
    title = (
        "Karistusseadustiku ja tervishoiuteenuste korraldamise seaduse muutmise "
        "seaduse eelnõu väljatöötamiskavatsus"
    )
    assert gdl.detect_affected_laws(title) == [
        "Karistusseadustiku",
        "tervishoiuteenuste korraldamise seaduse",
    ]


def test_f2_split_keeps_a_member_without_its_own_law_head_glued():
    assert gdl.split_coordinated_law_names(
        "Õppetoetuste ja õppelaenu seaduse ning maksukorralduse seaduse"
    ) == ["Õppetoetuste ja õppelaenu seaduse", "maksukorralduse seaduse"]
    assert gdl.split_coordinated_law_names(
        "Eesti Vabariigi haridusseaduse ja teiste seaduste"
    ) == ["Eesti Vabariigi haridusseaduse"]


def test_f2_resolves_both_acts_not_the_last_named(monkeypatch):
    from estleg import extract_draft_impact as x

    node = {
        "rdfs:label": "Muu",
        "estleg:affectedLawName": [
            "Karistusseadustiku ja tervishoiuteenuste korraldamise seaduse"
        ],
    }
    assert x.law_names_for_draft(node) == [
        "Karistusseadustiku",
        "tervishoiuteenuste korraldamise seaduse",
    ]


# --------------------------------------------------------------------------- #
# enactedAs tightening
# --------------------------------------------------------------------------- #


def test_enacted_as_needs_an_exact_act_name():
    from estleg.extract_draft_impact import resolve_enacted_as

    lookup = {"vabariigi valitsuse seadus": {"name": "vabariigi_valitsuse_seadus", "files": ["v.json"], "slug": "x"}}
    iri_map = {"v.json": "estleg:VVS_Map"}
    # A regulation that "kehtestab" conditions no longer fuzzy-matches an act.
    assert resolve_enacted_as(
        "Vabariigi Valitsuse 8. novembri 2012. a määruse nr 92 kehtestamine",
        "enacts",
        lookup,
        iri_map,
    ) is None


def test_enacted_as_from_riigikogu_adoption_of_a_new_act():
    from estleg.extract_draft_impact import resolve_enacted_as

    lookup = {"kliimaseadus": {"name": "kliimaseadus", "files": ["k.json"], "slug": "kliimaseadus"}}
    iri_map = {"k.json": "estleg:KLIMA_Map"}
    assert resolve_enacted_as("Kliimaseaduse eelnõu (123 SE)", None, lookup, iri_map) is None
    assert (
        resolve_enacted_as("Kliimaseaduse eelnõu (123 SE)", None, lookup, iri_map, riigikogu_enacted=True)
        == "estleg:KLIMA_Map"
    )
    # An amending bill names the amended act, not an enacted one.
    assert (
        resolve_enacted_as("Kliimaseaduse muutmise seadus", None, lookup, iri_map, riigikogu_enacted=True)
        is None
    )


# --------------------------------------------------------------------------- #
# Steps, ids and the derived phase
# --------------------------------------------------------------------------- #


def test_step_ids_are_stable_and_only_append():
    draft = "estleg:Draft_X"
    first = gdl.merge_steps(draft, [], [_spec("Review", "2020-01-02")])
    assert [s["@id"] for s in first] == ["estleg:Draft_X_Step_1"]
    # An EARLIER observation arriving later still appends (no renumbering).
    second = gdl.merge_steps(draft, first, [_spec("PublicConsultation", "2019-05-01"), _spec("Review", "2020-01-02")])
    assert [s["@id"] for s in second] == ["estleg:Draft_X_Step_1", "estleg:Draft_X_Step_2"]
    assert gdl.step_phase(second[1]) == "PublicConsultation"
    # Idempotent.
    third = gdl.merge_steps(draft, second, [_spec("PublicConsultation", "2019-05-01")])
    assert [s["@id"] for s in third] == [s["@id"] for s in second]


def test_replace_methods_drop_steps_a_source_no_longer_reports():
    draft = "estleg:Draft_X"
    steps = gdl.merge_steps(
        draft,
        [],
        [_spec("Review", "2020-01-02"), _spec("FirstReading", "2020-03-01", "riigikogu-mark", "I_LUGEMINE")],
    )
    kept = gdl.merge_steps(draft, steps, [], replace_methods=frozenset({"riigikogu-mark"}))
    assert [gdl.step_phase(s) for s in kept] == ["Review"]


def test_step_node_shape():
    step = gdl.make_step(
        "estleg:Draft_X", 3, phase="Enacted", day="2021-01-01", method="riigikogu-mark",
        source="https://api.riigikogu.ee/api/volumes/drafts/u", label="l",
    )
    assert step["@type"] == ["owl:NamedIndividual", "estleg:ProcessStep", "eli-dl:ProcessStep"]
    assert step["@id"] == "estleg:Draft_X_Step_3"
    assert step["estleg:processStage"] == {"@id": "estleg:Phase_Enacted"}
    assert step["dcterms:date"] == {"@value": "2021-01-01", "@type": "xsd:date"}
    assert step["estleg:stepOrder"] == {"@value": "3", "@type": "xsd:integer"}
    with pytest.raises(ValueError):
        gdl.make_step("estleg:Draft_X", 1, phase="Nope", day="", method="eis-feed", source=None, label="")


def test_phase_comes_from_the_latest_step_not_the_first_feed():
    steps = gdl.merge_steps(
        "estleg:Draft_X",
        [],
        [_spec("PublicConsultation", "2019-01-01"), _spec("Submission", "2019-06-01"), _spec("Review", "2019-03-01")],
    )
    assert gdl.derive_phase(steps) == "Submission"


def test_same_day_terminal_outcome_wins():
    steps = gdl.merge_steps(
        "estleg:Draft_X",
        [],
        [_spec("ThirdReading", "2021-02-03", "riigikogu-mark", "III"), _spec("Enacted", "2021-02-03", "riigikogu-mark", "VASTU_VOETUD")],
    )
    assert gdl.derive_phase(steps) == "Enacted"


def test_enacted_as_implies_enacted_unless_a_later_terminal_step():
    steps = gdl.merge_steps("estleg:Draft_X", [], [_spec("Review", "2020-01-01")])
    assert gdl.derive_phase(steps, enacted=True) == "Enacted"
    rejected = gdl.merge_steps("estleg:Draft_X", steps, [_spec("Rejected", "2021-01-01", "riigikogu-mark", "TAGASI_LYKATUD")])
    assert gdl.derive_phase(rejected, enacted=True) == "Rejected"


def test_stale_flag_only_for_old_public_consultation():
    old = gdl.merge_steps("estleg:Draft_X", [], [_spec("PublicConsultation", "2012-04-01")])
    new = gdl.merge_steps("estleg:Draft_X", [], [_spec("PublicConsultation", "2026-02-17")])
    review = gdl.merge_steps("estleg:Draft_X", [], [_spec("Review", "2012-04-01")])
    assert gdl.is_stale(old, "PublicConsultation")
    assert not gdl.is_stale(new, "PublicConsultation")
    assert not gdl.is_stale(review, "Review")


# --------------------------------------------------------------------------- #
# initiatedBy
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def identity() -> dict:
    return gdl.load_institution_identity()


@pytest.mark.parametrize(
    ("code", "day", "slug"),
    [
        ("KLIM", "2012-05-01", "keskkonnaministeerium"),
        ("KLIM", "2024-01-01", "kliimaministeerium"),
        ("JDM", "2024-12-31", "justiitsministeerium"),
        ("JDM", "2025-02-01", "justiits_ja_digiministeerium"),
        ("JUM", "2019-01-01", "justiitsministeerium"),
        ("REM", "2014-01-01", "pollumajandusministeerium"),
        ("REM", "2020-01-01", "maaeluministeerium"),
        ("REM", "2024-01-01", "regionaal_ja_pollumajandusministeerium"),
        ("RIIGIKOGU", "2020-01-01", "riigikogu"),
    ],
)
def test_initiated_by_follows_same_legal_person_renames(identity, code, day, slug):
    assert gdl.initiated_by_slug(code, day, identity) == slug
    assert (INSTITUTIONS / f"institution_{slug}.json").exists()


def test_riigikantselei_has_no_institution_iri(identity):
    node = {"estleg:eisNumber": "RK/20-0001", "estleg:publicationDate": {"@value": "2020-01-01"}}
    gdl.apply_initiator(node, identity)
    assert node["estleg:initiator"] == "Riigikantselei"
    assert "estleg:initiatedBy" not in node


def test_rem_literal_is_the_ministry_not_the_minister(identity):
    node = {"estleg:eisNumber": "REM/24-0368", "estleg:publicationDate": {"@value": "2024-03-01"}}
    gdl.apply_initiator(node, identity)
    assert node["estleg:initiator"] == "Regionaal- ja Põllumajandusministeerium"
    assert node["estleg:initiatedBy"] == {"@id": "estleg:Institution_regionaal_ja_pollumajandusministeerium"}


# --------------------------------------------------------------------------- #
# Offline lifecycle pass on a tiny corpus
# --------------------------------------------------------------------------- #


def _tiny_docs() -> dict:
    def draft(i, day):
        return {
            "@id": f"estleg:Draft_JDM20_000{i}",
            "@type": ["owl:NamedIndividual", "estleg:DraftLegislation"],
            "rdfs:label": f"Draft {i}",
            "estleg:legislativePhase": {"@id": "estleg:Phase_PublicConsultation"},
            "estleg:eisNumber": f"JDM/20-000{i}",
            "estleg:publicationDate": {"@value": day, "@type": "xsd:date"},
        }

    return {
        "publicConsultation": {
            "@context": {"estleg": "https://w3id.org/estleg/"},
            "@graph": [
                {"@id": "estleg:Eelnoud_PublicConsultation_Map", "@type": ["owl:Ontology"],
                 "dc:description": "Eelnõud, mis on hetkel etapis: Avalik konsultatsioon"},
                draft(2, "2012-01-01"),
                draft(1, "2026-02-01"),
            ],
        }
    }


def test_apply_eis_lifecycle_is_idempotent_and_writes_the_index(tmp_path, identity):
    docs = _tiny_docs()
    stats = gdl.apply_eis_lifecycle(docs, identity=identity, institutions_dir=INSTITUTIONS)
    assert stats["eis_steps_reconstructed"] == 2
    graph = docs["publicConsultation"]["@graph"]
    assert docs["publicConsultation"]["@context"]["eli-dl"] == gdl.ELI_DL_NS
    assert "esmakordselt" in graph[0]["dc:description"]
    # Drafts keep their committed order; steps follow, grouped per draft.
    assert [n["@id"] for n in graph[1:]] == [
        "estleg:Draft_JDM20_0002",
        "estleg:Draft_JDM20_0001",
        "estleg:Draft_JDM20_0002_Step_1",
        "estleg:Draft_JDM20_0001_Step_1",
    ]
    by_id = {n["@id"]: n for n in graph}
    assert by_id["estleg:Draft_JDM20_0002"]["estleg:lifecycleStale"]["@value"] == "true"
    assert "estleg:lifecycleStale" not in by_id["estleg:Draft_JDM20_0001"]
    assert by_id["estleg:Draft_JDM20_0002_Step_1"]["dcterms:date"]["@value"] == "2012-01-01"
    snapshot = json.dumps(docs, sort_keys=True)
    again = gdl.apply_eis_lifecycle(docs, identity=identity, institutions_dir=INSTITUTIONS)
    assert again["eis_steps_reconstructed"] == 0
    assert json.dumps(docs, sort_keys=True) == snapshot
    index = gdl.write_index(docs, tmp_path)
    assert index["total_drafts"] == 2
    assert index["phases"]["PublicConsultation"]["count"] == 2
    assert index["files"]["publicConsultation"]["count"] == 2


# --------------------------------------------------------------------------- #
# derivationMethod helpers
# --------------------------------------------------------------------------- #


def test_derivation_method_helpers():
    node: dict = {}
    assert add_derivation_method(node, "minted-ecli")
    assert not add_derivation_method(node, "minted-ecli")
    assert add_derivation_method(node, "rederived-case-type")
    assert derivation_methods(node) == ["minted-ecli", "rederived-case-type"]
    assert remove_derivation_method(node, "minted-ecli")
    assert node["estleg:derivationMethod"] == "rederived-case-type"
    with pytest.raises(ValueError):
        add_derivation_method(node, "guessed")
    assert DERIVATION_METHODS >= {"eis-feed", "title-regex", "cellar-interprets"}


def test_court_ecli_and_case_type_stamps():
    from estleg.generate_court_decisions import backfill_ecli_derivation
    from estleg.rederive_riigikohus_case_types import retype_node, stamp_previously_retyped

    node = {"@type": ["estleg:CourtDecision"], "estleg:ecliIdentifier": "ECLI:EE:RK:2017:3.17.1"}
    assert backfill_ecli_derivation(node)
    assert derivation_methods(node) == ["minted-ecli"]
    legacy = {
        "@type": ["estleg:CourtDecision"],
        "estleg:caseNumber": "III-4/1-5/94",
        "estleg:caseType": {"@id": "estleg:CaseType_Other"},
        "estleg:legalText": "Riigikohtu põhiseaduslikkuse järelevalve kolleegium …",
    }
    assert retype_node(legacy) == "estleg:CaseType_ConstitutionalReview"
    assert derivation_methods(legacy) == ["rederived-case-type"]
    legacy.pop("estleg:derivationMethod")
    assert stamp_previously_retyped(legacy)
    # A chamber the case number already resolves (III-1 = criminal) is the
    # generator's own classification, not a re-derivation.
    criminal = {
        "estleg:caseNumber": "III-1/1-5/94",
        "estleg:caseType": {"@id": "estleg:CaseType_Criminal"},
        "estleg:legalText": "kriminaalkolleegium",
    }
    assert not stamp_previously_retyped(criminal)


def test_curia_linker_prefers_cellar_over_title_regex():
    from estleg import link_curia_eu_legislation as linker

    known = {"estleg:EU_31995L0046", "estleg:EU_31980L0987"}
    node = {
        "@type": ["estleg:EUCourtDecision"],
        "estleg:celexNumber": "62014CJ0362",
        "rdfs:label": "Direktiiv 80/987/EMÜ",
    }
    assert linker.link_decision_node(node, known, {})
    assert node["estleg:interpretsEULaw"] == {"@id": "estleg:EU_31980L0987"}
    assert derivation_methods(node) == ["title-regex"]
    assert linker.link_decision_node(node, known, {"62014CJ0362": ["31995L0046", "12007P047"]})
    assert node["estleg:interpretsEULaw"] == {"@id": "estleg:EU_31995L0046"}
    assert derivation_methods(node) == ["cellar-interprets"]
    assert not linker.link_decision_node(node, known, {"62014CJ0362": ["31995L0046"]})


# --------------------------------------------------------------------------- #
# Contract gates on the committed drafts layer
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def committed() -> dict[str, dict]:
    docs = gdl.load_phase_peeps(EELNOUD)
    if not docs:
        raise AssertionError("committed eelnoud peeps missing")
    return docs


def test_every_draft_has_steps_and_a_derived_phase(committed):
    drafts = 0
    for doc in committed.values():
        assert doc["@context"].get("eli-dl") == gdl.ELI_DL_NS
        for draft, steps in gdl.drafts_with_steps(doc):
            drafts += 1
            assert steps, draft["@id"]
            assert all(s["@id"].startswith(draft["@id"] + gdl.STEP_INFIX) for s in steps)
            enacted = bool(gdl._refs(draft.get("estleg:enactedAs")))
            assert gdl.phase_of_iri(draft["estleg:legislativePhase"]["@id"]) == gdl.derive_phase(
                steps, enacted=enacted
            ), draft["@id"]
    index = json.loads((EELNOUD / "EELNOUD_INDEX.json").read_text(encoding="utf-8"))
    assert index["total_drafts"] == drafts


def test_index_phase_counts_match_the_drafts(committed):
    counts: Counter = Counter()
    for doc in committed.values():
        for node in doc["@graph"]:
            if gdl.is_draft(node):
                counts[gdl.phase_of_iri(node["estleg:legislativePhase"]["@id"])] += 1
    index = json.loads((EELNOUD / "EELNOUD_INDEX.json").read_text(encoding="utf-8"))
    assert {k: v["count"] for k, v in index["phases"].items()} == dict(counts)


def test_step_methods_are_in_the_closed_vocabulary(committed):
    for doc in committed.values():
        for node in doc["@graph"]:
            if gdl.is_process_step(node):
                assert set(derivation_methods(node)) <= DERIVATION_METHODS, node["@id"]


def test_initiated_by_points_at_existing_institutions(committed):
    targets = {
        node["estleg:initiatedBy"]["@id"]
        for doc in committed.values()
        for node in doc["@graph"]
        if gdl.is_draft(node) and "estleg:initiatedBy" in node
    }
    assert len(targets) >= 10
    for iri in targets:
        slug = iri.removeprefix("estleg:Institution_")
        assert (INSTITUTIONS / f"institution_{slug}.json").exists(), iri

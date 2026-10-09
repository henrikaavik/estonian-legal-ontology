"""Regression tests for the legacy-module § / lõige normaliser (#709).

PR #741's ProvisionRequires{Paragrahv,Summary,PartOfAct}Shape failed on 533 §
nodes of the two hand-modelled OWL modules plus 138 legacy-peep Section nodes
and 71 untyped lõiked. ``estleg.normalise_legacy_modules`` backfills them.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from estleg import normalise_legacy_modules as nlm
from estleg.estleg_common import save_json

KRR = Path(__file__).resolve().parent.parent / "krr_outputs"


def _doc(*nodes: dict) -> dict:
    return {"@context": {"estleg": "https://w3id.org/estleg/"}, "@graph": list(nodes)}


ROOT = {"@id": "estleg:Mod_v1", "@type": ["estleg:Act", "estleg:Law"]}


def _owl_section(**extra) -> dict:
    node = {
        "@id": "https://w3id.org/estleg/KarS_Par_88",
        "@type": ["estleg:Section", "owl:NamedIndividual", "estleg:LegalProvision"],
        "rdfs:label": "§ 88. Karistamine",
        "estleg:sectionNumber": "88",
        "estleg:legalText": "(1) Esimene lause. (2) Teine lause.",
    }
    node.update(extra)
    return node


def _by_id(doc: dict) -> dict[str, dict]:
    return {n["@id"]: n for n in doc["@graph"]}


# --------------------------------------------------------------------------- #
# § nodes
# --------------------------------------------------------------------------- #
def test_section_gets_paragrahv_summary_and_part_of_act():
    doc = _doc(ROOT, _owl_section())
    changes = nlm.normalise_document(doc)
    node = _by_id(doc)["https://w3id.org/estleg/KarS_Par_88"]
    assert node["estleg:paragrahv"] == "§ 88."
    assert node["estleg:summary"] == "(1) Esimene lause. (2) Teine lause."
    assert node["estleg:partOfAct"] == {"@id": "estleg:Mod_v1"}
    assert changes == {"paragrahv": 1, "summary": 1, "partOfAct": 1}


def test_new_keys_are_appended_after_existing_keys():
    doc = _doc(ROOT, _owl_section())
    original_keys = list(_owl_section())
    nlm.normalise_document(doc)
    keys = list(_by_id(doc)["https://w3id.org/estleg/KarS_Par_88"])
    assert keys[: len(original_keys)] == original_keys
    assert keys[len(original_keys) :] == ["estleg:paragrahv", "estleg:summary", "estleg:partOfAct"]


def test_existing_values_are_never_overwritten():
    section = _owl_section(
        **{
            "estleg:paragrahv": "KarS § 88",
            "estleg:summary": "Olemasolev",
            "estleg:partOfAct": {"@id": "estleg:Other"},
        }
    )
    doc = _doc(ROOT, section)
    assert not nlm.normalise_document(doc)
    node = _by_id(doc)["https://w3id.org/estleg/KarS_Par_88"]
    assert node["estleg:paragrahv"] == "KarS § 88"
    assert node["estleg:summary"] == "Olemasolev"
    assert node["estleg:partOfAct"] == {"@id": "estleg:Other"}


@pytest.mark.parametrize(
    ("section_number", "expected"),
    [
        ("88", "§ 88."),
        ("22¹", "§ 22¹."),
        ("22^1", "§ 22¹."),
        ("133_2", "§ 133²."),
        ("276-308", "§§ 276–308."),
        ("garbage", None),
    ],
)
def test_format_paragrahv_mirrors_canonical_convention(section_number, expected):
    assert nlm.format_paragrahv(section_number) == expected


def test_paragrahv_falls_back_to_label_prefix_without_section_number():
    section = _owl_section()
    del section["estleg:sectionNumber"]
    section["rdfs:label"] = "§ 93¹. Pealkiri"
    doc = _doc(ROOT, section)
    nlm.normalise_document(doc)
    assert _by_id(doc)[section["@id"]]["estleg:paragrahv"] == "§ 93¹."


def test_summary_is_truncated_like_the_canonical_builder():
    long_text = " ".join(["sõna"] * 300)
    doc = _doc(ROOT, _owl_section(**{"estleg:legalText": long_text}))
    nlm.normalise_document(doc)
    summary = _by_id(doc)["https://w3id.org/estleg/KarS_Par_88"]["estleg:summary"]
    assert len(summary) <= nlm.SUMMARY_MAX_LEN
    assert long_text.startswith(summary)
    assert summary.endswith("sõna")  # cut on a word boundary, not mid-token


def test_summary_falls_back_to_loige_text_then_label():
    section = _owl_section(**{"estleg:hasProvision": [{"@id": "estleg:Par88_Lg1"}]})
    del section["estleg:legalText"]
    loige = {
        "@id": "estleg:Par88_Lg1",
        "@type": ["estleg:LegalProvision", "owl:NamedIndividual"],
        "rdfs:label": "§88 lg 1",
        "estleg:legalText": "(1) Lõike tekst.",
    }
    doc = _doc(ROOT, section, loige)
    nlm.normalise_document(doc)
    assert _by_id(doc)[section["@id"]]["estleg:summary"] == "(1) Lõike tekst."

    bare = _owl_section()
    del bare["estleg:legalText"]
    doc = _doc(ROOT, bare)
    nlm.normalise_document(doc)
    assert _by_id(doc)[bare["@id"]]["estleg:summary"] == "§ 88. Karistamine"


def test_section_only_node_gains_legal_provision_type():
    # validate_all (#434): a node with estleg:paragrahv must be a LegalProvision.
    part = {"@id": "estleg:VOS_Osa8", "@type": ["estleg:Part"]}
    section = {
        "@id": "estleg:VOS_Osa8_Par_1",
        "@type": ["owl:NamedIndividual", "estleg:Section"],
        "estleg:sectionNumber": "1",
        "rdfs:label": "§ 1. Mõiste",
        "estleg:summary": "Olemasolev kokkuvõte",
    }
    doc = _doc(part, section)
    changes = nlm.normalise_document(doc)
    node = _by_id(doc)["estleg:VOS_Osa8_Par_1"]
    assert node["@type"] == ["owl:NamedIndividual", "estleg:Section", "estleg:LegalProvision"]
    assert node["estleg:summary"] == "Olemasolev kokkuvõte"
    assert node["estleg:partOfAct"] == {"@id": "estleg:VOS_Osa8"}
    assert changes == {"paragrahv": 1, "partOfAct": 1, "provisionType": 1}


def test_no_part_of_act_when_root_is_ambiguous_or_missing():
    second_root = {"@id": "estleg:Other_Part", "@type": ["estleg:Part"]}
    for doc in (_doc(_owl_section()), _doc(ROOT, second_root, _owl_section())):
        nlm.normalise_document(doc)
        assert "estleg:partOfAct" not in _by_id(doc)["https://w3id.org/estleg/KarS_Par_88"]


def test_non_section_nodes_are_untouched():
    chapter = {"@id": "estleg:Ch1", "@type": ["estleg:Chapter", "owl:NamedIndividual"], "rdfs:label": "1. peatükk"}
    concept = {"@id": "estleg:C1", "@type": ["estleg:LegalConcept"], "rdfs:label": "§ 5. Not a provision"}
    doc = _doc(ROOT, chapter, concept)
    before = copy.deepcopy(doc)
    assert not nlm.normalise_document(doc)
    assert doc == before


# --------------------------------------------------------------------------- #
# lõige nodes
# --------------------------------------------------------------------------- #
def _tsus_doc() -> dict:
    section = {
        "@id": "https://w3id.org/estleg/TsUS_Par_138",
        "@type": ["estleg:Section", "owl:NamedIndividual", "estleg:LegalProvision"],
        "estleg:sectionNumber": "138",
        "rdfs:label": "§ 138. Hea usu põhimõte",
        "estleg:hasProvision": [
            {"@id": "https://w3id.org/estleg/Par138_Lg1"},
            {"@id": "https://w3id.org/estleg/Par138_Lg2_1"},
        ],
    }
    loiked = [
        {
            "@id": f"https://w3id.org/estleg/Par138_{suffix}",
            "@type": ["estleg:LegalProvision", "owl:NamedIndividual"],
            "rdfs:label": label,
            "estleg:legalText": text,
        }
        for suffix, label, text in (
            ("Lg1", "§138 lg 1", "(1) Heas usus."),
            ("Lg2_1", "§138 lg 2¹", "(2¹) Lisalõige."),
        )
    ]
    return _doc({"@id": "estleg:TsUS_Osa7_v1", "@type": ["estleg:Act"]}, section, *loiked)


def test_loige_becomes_subsection_with_parent_and_number():
    doc = _tsus_doc()
    changes = nlm.normalise_document(doc)
    nodes = _by_id(doc)
    lg1 = nodes["https://w3id.org/estleg/Par138_Lg1"]
    assert lg1["@type"] == ["estleg:LegalProvision", "owl:NamedIndividual", "estleg:Subsection"]
    assert lg1["estleg:subsectionNumber"] == "1"
    assert lg1["estleg:parentProvision"] == {"@id": "https://w3id.org/estleg/TsUS_Par_138"}
    assert nodes["https://w3id.org/estleg/Par138_Lg2_1"]["estleg:subsectionNumber"] == "2¹"
    # A lõige is excused from the § trio and must not receive it.
    assert "estleg:paragrahv" not in lg1 and "estleg:partOfAct" not in lg1
    # The § itself takes its summary from its lõiked, in hasProvision order.
    section = nodes["https://w3id.org/estleg/TsUS_Par_138"]
    assert section["estleg:summary"] == "(1) Heas usus. (2¹) Lisalõige."
    assert changes["subsectionType"] == changes["parentProvision"] == changes["subsectionNumber"] == 2


def test_loige_existing_parent_and_number_are_kept():
    doc = _tsus_doc()
    lg1 = _by_id(doc)["https://w3id.org/estleg/Par138_Lg1"]
    lg1["estleg:subsectionNumber"] = "1a"
    lg1["estleg:parentProvision"] = {"@id": "estleg:Elsewhere"}
    nlm.normalise_document(doc)
    assert lg1["estleg:subsectionNumber"] == "1a"
    assert lg1["estleg:parentProvision"] == {"@id": "estleg:Elsewhere"}


def test_has_provision_child_without_loige_id_is_not_a_subsection():
    doc = _tsus_doc()
    child = _by_id(doc)["https://w3id.org/estleg/Par138_Lg1"]
    child["@id"] = "https://w3id.org/estleg/SomeProvision"
    _by_id(doc)["https://w3id.org/estleg/TsUS_Par_138"]["estleg:hasProvision"][0] = {"@id": child["@id"]}
    nlm.normalise_document(doc)
    assert "estleg:Subsection" not in child["@type"]


# --------------------------------------------------------------------------- #
# idempotence + file round trip
# --------------------------------------------------------------------------- #
def test_second_run_changes_nothing():
    doc = _doc(ROOT, _owl_section())
    doc["@graph"].extend(_tsus_doc()["@graph"][1:])
    assert nlm.normalise_document(doc)
    snapshot = copy.deepcopy(doc)
    assert not nlm.normalise_document(doc)
    assert doc == snapshot


def test_normalise_files_rewrites_only_changed_files(tmp_path):
    changed = tmp_path / "karistusseadustik_eriosa_owl.jsonld"
    clean = tmp_path / "volaigusseadus_osa1_peep.json"
    save_json(changed, _doc(ROOT, _owl_section()))
    save_json(clean, _doc(ROOT))
    clean_bytes = clean.read_bytes()

    assert nlm.main(["--krr-dir", str(tmp_path), "--check"]) == 1
    assert "estleg:paragrahv" not in changed.read_text(encoding="utf-8")

    report = nlm.normalise_files(nlm.iter_target_files(tmp_path))
    assert set(report) == {changed.name}
    assert clean.read_bytes() == clean_bytes
    text = changed.read_text(encoding="utf-8")
    assert text == json.dumps(json.loads(text), ensure_ascii=False, indent=2) + "\n"
    assert nlm.main(["--krr-dir", str(tmp_path), "--check"]) == 0


# --------------------------------------------------------------------------- #
# real corpus
# --------------------------------------------------------------------------- #
def _real_targets() -> list[Path]:
    return list(nlm.iter_target_files(KRR))


@pytest.mark.corpus
@pytest.mark.parametrize("name", nlm.MODULE_FILES)
def test_owl_module_sections_carry_the_section_trio(name):
    with open(KRR / name, encoding="utf-8") as fh:
        graph = json.load(fh)["@graph"]
    sections = [n for n in graph if "estleg:Section" in n.get("@type", [])]
    assert sections, f"{name}: no § nodes found"
    missing = [
        (n["@id"], key)
        for n in sections
        for key in ("estleg:paragrahv", "estleg:summary", "estleg:partOfAct")
        if not n.get(key)
    ]
    assert not missing, missing[:5]
    loiked = [
        n
        for n in graph
        if "estleg:LegalProvision" in n.get("@type", []) and "estleg:Section" not in n.get("@type", [])
    ]
    assert all("estleg:Subsection" in n["@type"] and n.get("estleg:parentProvision") for n in loiked)


@pytest.mark.corpus
def test_committed_legacy_modules_are_already_normalised():
    targets = _real_targets()
    assert len(targets) >= 2 + 2  # both OWL modules + some legacy peeps
    assert nlm.normalise_files(targets, write=False) == {}

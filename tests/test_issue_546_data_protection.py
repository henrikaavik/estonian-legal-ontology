"""#546 — court subcorpora carry a GDPR notice and machine-readable flags."""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
NOTICE = REPO / "docs" / "DATA_PROTECTION.md"
METADATA = REPO / "metadata.jsonld"


def test_data_protection_doc_exists_and_warns_controllers() -> None:
    assert NOTICE.is_file()
    text = NOTICE.read_text(encoding="utf-8")
    assert "GDPR" in text
    assert "independent" in text.casefold() and "controller" in text.casefold()
    assert "krr_outputs/riigikohus/" in text
    assert "krr_outputs/curia/" in text
    assert "krr_outputs/kohtud/" in text
    assert "Article 10" in text or "Art. 10" in text


def test_court_distributions_flag_personal_data() -> None:
    meta = json.loads(METADATA.read_text(encoding="utf-8"))
    flagged = []
    for dist in meta.get("dcat:distribution") or []:
        if not isinstance(dist, dict):
            continue
        if dist.get("estleg:containsPersonalData") is True:
            flagged.append(dist.get("dcterms:title"))
            rights = dist.get("dcterms:rights") or ""
            # #710: DCAT-AP 3.0.1 types distribution rights as a
            # dcterms:RightsStatement node; its text is the rdfs:label.
            if isinstance(rights, dict):
                rights = rights.get("rdfs:label") or ""
            assert "PERSONAL DATA" in rights or "personal data" in rights.casefold()
    assert len(flagged) >= 2, flagged
    blob = " ".join(str(t) for t in flagged)
    assert "Riigikohus" in blob or "Supreme Court" in blob
    assert "EU court" in blob or "CURIA" in blob or "EU Court" in blob


# --- #720: in-band estleg:containsPersonalData on the combined Dataset heads ---

import pytest  # noqa: E402

from estleg import estleg_common  # noqa: E402

#: Expected flag per combined target. CURIA carries named-party EU decisions;
#: the flagship carries only label-only Riigikohus closure stubs, so it is
#: not flagged (and agrees with the un-flagged laws distribution).
EXPECTED_PERSONAL_DATA = {
    "combined_ontology.jsonld": False,
    "eurlex/eurlex_combined.jsonld": False,
    "curia/curia_combined.jsonld": True,
    "eelnoud/eelnoud_combined.jsonld": False,
    "concepts/concepts_combined.jsonld": False,
    "act_expressions_combined.jsonld": False,
}


def test_every_combined_target_declares_the_flag() -> None:
    declared = {
        t["relpath"]: t.get("contains_personal_data")
        for t in estleg_common.COMBINED_JSONLD_TARGETS
    }
    assert declared == EXPECTED_PERSONAL_DATA


@pytest.mark.parametrize(
    "target", estleg_common.COMBINED_JSONLD_TARGETS, ids=lambda t: str(t["relpath"])
)
def test_stamped_head_carries_contains_personal_data(target) -> None:
    doc = {"@context": {}, "@graph": [{"@id": "estleg:X", "@type": "estleg:CourtDecision"}]}
    estleg_common.stamp_combined_dataset_head(
        doc,
        flagship=bool(target.get("flagship")),
        label=target.get("label"),
        ontology_id=target.get("ontology_id"),
        contains_personal_data=target.get("contains_personal_data"),
    )
    head = doc["@graph"][0]
    assert head["@id"] != "estleg:X"
    assert head["estleg:containsPersonalData"] == {
        "@value": EXPECTED_PERSONAL_DATA[target["relpath"]],
        "@type": "xsd:boolean",
    }


def test_existing_ontology_head_is_upgraded_with_the_flag() -> None:
    doc = {
        "@context": {},
        "@graph": [{"@id": "estleg:CURIA_Combined_Map", "@type": ["owl:Ontology"]}],
    }
    estleg_common.stamp_combined_dataset_head(doc, label="L", contains_personal_data=True)
    assert doc["@graph"][0]["@id"] == "estleg:CURIA_Combined_Map"
    assert doc["@graph"][0]["estleg:containsPersonalData"]["@value"] is True


def test_flag_omitted_when_unspecified() -> None:
    doc = {"@context": {}, "@graph": []}
    estleg_common.stamp_combined_dataset_head(doc, label="L")
    assert "estleg:containsPersonalData" not in doc["@graph"][0]


def test_metadata_flags_agree_with_combined_targets() -> None:
    """A distribution flagged in metadata.jsonld that ships a combined target
    must have that target flagged in-band too."""
    by_name = {
        Path(str(t["relpath"])).name: t.get("contains_personal_data")
        for t in estleg_common.COMBINED_JSONLD_TARGETS
    }
    meta = json.loads(METADATA.read_text(encoding="utf-8"))
    matched = []
    for dist in meta.get("dcat:distribution") or []:
        if not isinstance(dist, dict) or dist.get("estleg:containsPersonalData") is not True:
            continue
        url = dist.get("dcat:downloadURL")
        url = url.get("@id") if isinstance(url, dict) else url
        name = str(url or "").rsplit("/", 1)[-1].removesuffix(".gz")
        if name in by_name:
            assert by_name[name] is True, name
            matched.append(name)
    assert "curia_combined.jsonld" in matched, matched


def test_flagship_build_stamps_the_flag(tmp_path) -> None:
    from estleg import fix_all_issues

    (tmp_path / "a_peep.json").write_text(
        json.dumps({"@graph": [{"@id": "estleg:A_Map", "@type": ["estleg:Law"]}]}),
        encoding="utf-8",
    )
    fix_all_issues.generate_combined_jsonld(tmp_path)
    doc = json.loads((tmp_path / "combined_ontology.jsonld").read_text(encoding="utf-8"))
    head = doc["@graph"][0]
    assert head["@id"] == estleg_common.ONTOLOGY_IRI
    assert head["estleg:containsPersonalData"] == {
        "@value": EXPECTED_PERSONAL_DATA["combined_ontology.jsonld"],
        "@type": "xsd:boolean",
    }

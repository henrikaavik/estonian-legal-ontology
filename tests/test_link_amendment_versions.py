"""#704: the #429 amendment/version join is a DAG step, offline and idempotent."""

from __future__ import annotations

import json
from pathlib import Path

from estleg import link_amendment_versions as lav


def _write(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _version(nid: str, valid_from: str) -> dict:
    return {
        "@id": nid,
        "@type": ["owl:NamedIndividual", "estleg:ProvisionVersion"],
        "estleg:versionValidFrom": {"@value": valid_from, "@type": "xsd:date"},
    }


def _tree(krr: Path) -> None:
    _write(krr / "toyseadus_peep.json", {"@graph": [
        {"@id": "estleg:TOY_Map", "@type": ["estleg:Act"], "rdfs:label": "Toy"},
    ]})
    _write(krr / "provision_versions" / "toyseadus.jsonld", {"@graph": [
        _version("estleg:TOY_Par_1_v1", "2020-01-01"),
        _version("estleg:TOY_Par_1_v2", "2021-05-01"),
    ]})
    _write(krr / "amendments" / "amendments_toyseadus.json", {"@graph": [
        {"@id": "estleg:AmendmentChain_TOY", "@type": ["owl:Ontology"]},
        {
            "@id": "estleg:Amendment_TOY_abc",
            "@type": ["owl:NamedIndividual", "estleg:AmendmentEvent"],
            "estleg:amends": {"@id": "estleg:TOY_Map"},
            "estleg:entryIntoForce": {"@value": "2020-01-01", "@type": "xsd:date"},
        },
    ]})
    # A regulation chain with a sidecar must be left alone (#431 snapshots).
    _write(krr / "regulations" / "riik" / "reg_t1_peep.json", {"@graph": []})
    _write(krr / "provision_versions" / "reg_t1.jsonld", {"@graph": [
        _version("estleg:Reg_1_Par_1_v1", "2024-01-01"),
    ]})
    _write(krr / "amendments" / "amendments_reg_t1.json", {"@graph": [
        {"@id": "estleg:AmendmentChain_Reg_1", "@type": ["owl:Ontology"]},
    ]})


def test_join_mints_version_events_and_last_amendment_date(tmp_path: Path) -> None:
    _tree(tmp_path)
    reg_before = (tmp_path / "amendments" / "amendments_reg_t1.json").read_bytes()
    stats = lav.join_version_layer(tmp_path)
    assert stats.as_dict() == {
        "laws_with_versions": 1, "chains_seen": 1, "chains_changed": 1,
        "chains_absent": 0, "peeps_changed": 1,
    }
    chain = json.loads((tmp_path / "amendments" / "amendments_toyseadus.json").read_text())
    events = {n["@id"]: n for n in chain["@graph"] if n["@id"].startswith("estleg:Amendment_")}
    assert events["estleg:Amendment_TOY_abc"]["estleg:resultedInVersion"] == [
        {"@id": "estleg:TOY_Par_1_v1"}]
    minted = events["estleg:Amendment_TOY_vf_20210501"]
    assert minted["estleg:amends"] == {"@id": "estleg:TOY_Map"}
    assert minted["estleg:resultedInVersion"] == [{"@id": "estleg:TOY_Par_1_v2"}]
    peep = json.loads((tmp_path / "toyseadus_peep.json").read_text())
    assert peep["@graph"][0]["estleg:lastAmendmentDate"]["@value"] == "2021-05-01"
    assert (tmp_path / "amendments" / "amendments_reg_t1.json").read_bytes() == reg_before


def test_join_is_idempotent_and_dry_run_writes_nothing(tmp_path: Path) -> None:
    _tree(tmp_path)
    snapshot = {p: p.read_bytes() for p in tmp_path.rglob("*.json*")}
    stats = lav.join_version_layer(tmp_path, dry_run=True)
    assert stats.chains_changed == 1
    assert {p: p.read_bytes() for p in tmp_path.rglob("*.json*")} == snapshot
    lav.join_version_layer(tmp_path)
    again = lav.join_version_layer(tmp_path)
    assert (again.chains_changed, again.peeps_changed) == (0, 0)


def test_chain_regeneration_cannot_lose_version_events(tmp_path: Path) -> None:
    """A chain rewritten without the join (what generate_amendment_history
    main() produces) gets its _vf_ events back from the step."""
    _tree(tmp_path)
    lav.join_version_layer(tmp_path)
    joined = (tmp_path / "amendments" / "amendments_toyseadus.json").read_bytes()
    _tree(tmp_path)  # regenerate the chain without the join
    lav.join_version_layer(tmp_path)
    assert (tmp_path / "amendments" / "amendments_toyseadus.json").read_bytes() == joined

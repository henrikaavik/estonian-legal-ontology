"""KOV enabling-provision staleness derivation (#712)."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from estleg import derive_kov_enabling_staleness as mod
from estleg.derive_kov_enabling_staleness import (
    PROP_OUTDATED,
    PROP_SUPERSEDING,
    STATUS_ACT_LEVEL,
    STATUS_NO_TARGET,
    STATUS_NO_VERSIONS,
    STATUS_PREDATES,
    STATUS_RESOLVED,
    build_version_layer,
    derive_node,
    evaluate_provision,
    graph_index,
)

REPO = Path(__file__).resolve().parents[1]


def _date(value: str) -> dict:
    return {"@value": value, "@type": "xsd:date"}


def _version(iri: str, of: str, start: str, end: str | None, text: str) -> dict:
    node = {
        "@id": iri,
        "@type": ["owl:NamedIndividual", "estleg:ProvisionVersion"],
        "estleg:versionOf": {"@id": of},
        "estleg:versionValidFrom": _date(start),
        "estleg:versionText": text,
    }
    if end:
        node["estleg:versionValidTo"] = _date(end)
    return node


def _write_versions(tmp_path: Path) -> Path:
    """KOKS § 22: three redactions, the middle one re-issues identical text.

    § 6: one open-ended redaction from 2002 (never superseded).
    """
    vdir = tmp_path / "provision_versions"
    vdir.mkdir()
    graph = [
        {"@id": "estleg:ProvisionVersions_koks_Map", "@type": ["owl:Ontology"]},
        _version("estleg:KOKS_Par_22_v1", "estleg:KOKS_Par_22", "2002-06-01", "2010-12-31", "A"),
        _version("estleg:KOKS_Par_22_v2", "estleg:KOKS_Par_22", "2011-01-01", "2014-12-31", "A"),
        _version("estleg:KOKS_Par_22_v3", "estleg:KOKS_Par_22", "2015-01-01", None, "B"),
        _version("estleg:KOKS_Par_6_v1", "estleg:KOKS_Par_6", "2002-06-01", None, "C"),
    ]
    (vdir / "koks.jsonld").write_text(json.dumps({"@graph": graph}), encoding="utf-8")
    # A not-pulled LFS pointer must be skipped, not crash.
    (vdir / "lfs.jsonld").write_text(
        "version https://git-lfs.github.com/spec/v1\noid sha256:x\n", encoding="utf-8"
    )
    return vdir


@pytest.fixture
def layer(tmp_path: Path) -> mod.VersionLayer:
    return build_version_layer(_write_versions(tmp_path))


def _kov_doc(entry: str | None, targets: list[str | None], reg: str = "1") -> dict:
    root: dict = {
        "@id": f"estleg:Reg_{reg}_Map",
        "@type": ["estleg:Act", "estleg:MunicipalRegulation"],
        "estleg:enactedBy": {"@id": "estleg:Issuer_x_vallavolikogu"},
    }
    if entry:
        root["estleg:entryIntoForce"] = _date(entry)
    graph: list[dict] = [root]
    refs = []
    for i, target in enumerate(targets, 1):
        cid = f"estleg:Citation_Reg_{reg}_Map_{i}"
        refs.append({"@id": cid})
        cit: dict = {"@id": cid, "@type": ["owl:NamedIndividual", "estleg:Citation"]}
        if target:
            cit["estleg:citationTarget"] = {"@id": target}
        graph.append(cit)
    if refs:
        root["estleg:implementsCitation"] = refs
    return {"@context": {}, "@graph": graph}


def _derive(doc: dict, layer, eval_date=None):
    root = doc["@graph"][0]
    return derive_node(root, graph_index(doc), layer, eval_date)


# --- version-layer index -----------------------------------------------------


def test_version_layer_index_sorted_and_lfs_skipped(layer) -> None:
    assert [row[2] for row in layer.chains["estleg:KOKS_Par_22"]] == [
        "estleg:KOKS_Par_22_v1",
        "estleg:KOKS_Par_22_v2",
        "estleg:KOKS_Par_22_v3",
    ]
    assert len(layer) == 2
    assert layer.text_digest["estleg:KOKS_Par_22_v1"] == layer.text_digest["estleg:KOKS_Par_22_v2"]


# --- chain resolution and as-of edge cases -----------------------------------


def test_lg_target_rolls_up_to_paragraph_chain(layer) -> None:
    ev = evaluate_provision("estleg:KOKS_Par_22_Lg_1", "2005-03-01", layer)
    assert ev.status == STATUS_RESOLVED
    assert ev.provision == "estleg:KOKS_Par_22"
    assert ev.version_in_force == "estleg:KOKS_Par_22_v1"
    # v2 re-issues identical text → skipped; v3 is the real change.
    assert ev.superseding_date == "2015-01-01"
    assert ev.outdated


def test_version_starting_on_as_of_day_is_in_force(layer) -> None:
    ev = evaluate_provision("estleg:KOKS_Par_22", "2015-01-01", layer)
    assert ev.version_in_force == "estleg:KOKS_Par_22_v3"
    assert ev.superseding_date == ""
    assert not ev.outdated


def test_version_ending_on_as_of_day_is_in_force(layer) -> None:
    ev = evaluate_provision("estleg:KOKS_Par_22", "2014-12-31", layer)
    assert ev.version_in_force == "estleg:KOKS_Par_22_v2"
    assert ev.superseding_date == "2015-01-01"


def test_open_ended_version_is_current(layer) -> None:
    ev = evaluate_provision("estleg:KOKS_Par_6_Lg_3", "2020-01-01", layer)
    assert ev.status == STATUS_RESOLVED
    assert ev.version_in_force == "estleg:KOKS_Par_6_v1"
    assert not ev.outdated


def test_as_of_before_history_is_unresolved(layer) -> None:
    ev = evaluate_provision("estleg:KOKS_Par_22", "1999-05-01", layer)
    assert ev.status == STATUS_PREDATES
    assert not ev.outdated


def test_act_level_map_fallback_and_unversioned_provision(layer) -> None:
    assert evaluate_provision("estleg:KOKS_Map", "2020-01-01", layer).status == STATUS_ACT_LEVEL
    assert evaluate_provision("estleg:KOKS_Osa2", "2020-01-01", layer).status == STATUS_ACT_LEVEL
    ev = evaluate_provision("estleg:FOO_Par_9_Lg_1", "2020-01-01", layer)
    assert ev.status == STATUS_NO_VERSIONS
    assert ev.provision == "estleg:FOO_Par_9"


def test_eval_date_ignores_later_redactions(layer) -> None:
    ev = evaluate_provision("estleg:KOKS_Par_22", "2005-01-01", layer, eval_date="2012-01-01")
    assert ev.version_in_force == "estleg:KOKS_Par_22_v1"
    assert not ev.outdated  # v3 (2015) is not yet known on 2012-01-01


# --- node stamping -----------------------------------------------------------


def test_outdated_root_gets_flag_and_earliest_date(layer) -> None:
    doc = _kov_doc("2005-03-01", ["estleg:KOKS_Par_22_Lg_1", "estleg:KOKS_Par_6"])
    changed, ev = _derive(doc, layer)
    root = doc["@graph"][0]
    assert changed
    assert root[PROP_OUTDATED] == {"@value": True, "@type": "xsd:boolean"}
    assert root[PROP_SUPERSEDING] == {"@value": "2015-01-01", "@type": "xsd:date"}
    assert len(ev.resolved) == 2


def test_current_root_gets_false_and_no_date(layer) -> None:
    doc = _kov_doc("2016-01-01", ["estleg:KOKS_Par_22_Lg_2"])
    _derive(doc, layer)
    root = doc["@graph"][0]
    assert root[PROP_OUTDATED]["@value"] is False
    assert PROP_SUPERSEDING not in root


def test_no_citation_root_is_left_unstamped(layer) -> None:
    doc = _kov_doc("2016-01-01", [])
    changed, ev = _derive(doc, layer)
    assert not changed and ev.provisions == []
    assert PROP_OUTDATED not in doc["@graph"][0]


def test_only_map_fallback_citation_is_left_unstamped(layer) -> None:
    doc = _kov_doc("2016-01-01", ["estleg:KOKS_Map", None])
    _, ev = _derive(doc, layer)
    assert [p.status for p in ev.provisions] == [STATUS_ACT_LEVEL, STATUS_NO_TARGET]
    assert PROP_OUTDATED not in doc["@graph"][0]


def test_missing_entry_into_force_is_not_evaluated(layer) -> None:
    doc = _kov_doc(None, ["estleg:KOKS_Par_22"])
    _, ev = _derive(doc, layer)
    assert ev.as_of is None
    assert PROP_OUTDATED not in doc["@graph"][0]


def test_stale_stamp_is_stripped_when_no_longer_derivable(layer) -> None:
    doc = _kov_doc("2016-01-01", [])
    root = doc["@graph"][0]
    root[PROP_OUTDATED] = {"@value": True, "@type": "xsd:boolean"}
    root[PROP_SUPERSEDING] = _date("2020-01-01")
    changed, _ = _derive(doc, layer)
    assert changed
    assert PROP_OUTDATED not in root and PROP_SUPERSEDING not in root


def test_derive_is_idempotent(layer) -> None:
    doc = _kov_doc("2005-03-01", ["estleg:KOKS_Par_22_Lg_1"])
    assert _derive(doc, layer)[0] is True
    snapshot = json.dumps(doc, sort_keys=True)
    assert _derive(doc, layer)[0] is False
    assert json.dumps(doc, sort_keys=True) == snapshot


# --- CLI over a tmp corpus ---------------------------------------------------


def test_main_apply_then_rerun_is_noop(tmp_path: Path, monkeypatch, capsys) -> None:
    vdir = _write_versions(tmp_path)
    monkeypatch.setattr(mod, "VERSION_DIR", vdir)
    monkeypatch.setattr(mod, "build_version_layer", lambda: build_version_layer(vdir))
    kov = tmp_path / "kov" / "x_vallavolikogu"
    kov.mkdir(parents=True)
    peep = kov / "a_t1_peep.json"
    peep.write_text(
        json.dumps(_kov_doc("2005-03-01", ["estleg:KOKS_Par_22_Lg_1"]), indent=2) + "\n",
        encoding="utf-8",
    )
    (kov / "b_t2_peep.json").write_text(
        json.dumps(_kov_doc("2016-01-01", ["estleg:KOKS_Map"], reg="2"), indent=2) + "\n",
        encoding="utf-8",
    )
    report = tmp_path / "report.json"
    args = ["--kov-dir", str(tmp_path / "kov"), "--report", str(report)]

    assert mod.main(args) == 0  # dry run
    assert PROP_OUTDATED not in json.loads(peep.read_text())["@graph"][0]
    assert not report.exists()

    assert mod.main([*args, "--apply"]) == 0
    root = json.loads(peep.read_text())["@graph"][0]
    assert root[PROP_OUTDATED]["@value"] is True
    data = json.loads(report.read_text())
    assert data["roots_evaluated"] == 2
    assert data["roots_resolved_to_version"] == 1
    assert data["roots_outdated"] == 1
    assert data["citation_status"] == {"act_level": 1, "resolved": 1}
    assert data["earliest_superseding_year"] == {"2015": 1}

    before = peep.read_text()
    capsys.readouterr()
    assert mod.main([*args, "--apply"]) == 0
    assert "Roots changed: 0" in capsys.readouterr().out
    assert peep.read_text() == before


def test_process_file_skips_lfs_pointer(tmp_path: Path, layer) -> None:
    peep = tmp_path / "p_peep.json"
    peep.write_text("version https://git-lfs.github.com/spec/v1\n", encoding="utf-8")
    stats: Counter = Counter()
    mod.process_file(peep, layer, apply=True, stats=stats)
    assert stats["files_unreadable"] == 1


# --- corpus gate -------------------------------------------------------------


@pytest.mark.corpus
def test_corpus_every_resolved_kov_root_carries_the_flag() -> None:
    """Every KOV root whose enabling provision resolves to a version is stamped.

    Also asserts the stamp matches a fresh evaluation (no stale stamps) and
    that unresolved roots carry no flag.
    """
    layer = build_version_layer()
    assert len(layer) > 10_000, "provision_versions/ not materialised (git lfs pull?)"
    peeps = mod.iter_kov_peeps(mod.KOV_DIR)
    assert len(peeps) > 10_000
    resolved = mismatched = 0
    for path in peeps:
        doc = json.loads(path.read_text(encoding="utf-8"))
        by_id = graph_index(doc)
        for node in doc["@graph"]:
            if not mod.is_kov_root(node):
                continue
            ev = mod.evaluate_root(node, by_id, layer)
            stamp = node.get(PROP_OUTDATED)
            if ev.resolved:
                resolved += 1
                if stamp != {"@value": ev.outdated, "@type": "xsd:boolean"}:
                    mismatched += 1
                elif ev.outdated and node.get(PROP_SUPERSEDING, {}).get("@value") != (
                    ev.earliest_superseding_date
                ):
                    mismatched += 1
            elif stamp is not None:
                mismatched += 1
    assert resolved > 9_000
    assert mismatched == 0, (
        f"{mismatched} KOV roots have a missing/stale enablingProvisionOutdated stamp; "
        "run scripts/derive_kov_enabling_staleness.py --apply"
    )

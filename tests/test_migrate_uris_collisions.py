"""Corpus-prefix collisions after transliteration (REÕS vs REOS, ÜKS vs UKS).

``assign_abbreviations`` used to key reservations on the RAW registry string,
but every ``@id`` is minted through ``sanitize_id``, so ``REÕS`` and ``REOS``
both produced ``estleg:REOS_*``. These tests pin the corpus-form reservation
and the file-scoped ``resolve-collisions`` repair.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from estleg.migrate_uris import (
    assign_abbreviations,
    compute_registry_hash,
    corpus_prefix,
    law_own_files,
    plan_registry_collisions,
    rename_prefix_in_iri,
    reserve_abbreviations,
    resolve_collisions,
)

# ── Reservation keyed on the corpus (transliterated) form ────────────────────


def test_corpus_prefix_transliterates() -> None:
    assert corpus_prefix("REÕS") == "REOS"
    assert corpus_prefix("TsÜS") == "TsUS"
    assert corpus_prefix("RÕS_2") == "ROS_2"


def test_rt_api_pair_gets_distinct_corpus_prefixes() -> None:
    """REÕS and REOS are both rt_api: slug order decides, prefixes differ."""
    assigned = reserve_abbreviations([
        ("riigi_eraoiguslikes_x", "REOS", "rt_api"),
        ("rahvusvahelise_eraoiguse_seadus", "REÕS", "rt_api"),
    ])
    assert assigned["rahvusvahelise_eraoiguse_seadus"] == "REÕS"
    assert assigned["riigi_eraoiguslikes_x"] == "REOS_2"
    prefixes = {corpus_prefix(a) for a in assigned.values()}
    assert prefixes == {"REOS", "REOS_2"}


def test_deprecated_existing_prefix_yields_to_rt_api() -> None:
    """A legacy ``existing`` UKS loses to the rt_api ÜKS even though it sorts first."""
    assigned = reserve_abbreviations([
        ("ulikooli", "UKS", "existing"),
        ("ulikooliseadus", "ÜKS", "rt_api"),
    ])
    assert assigned == {"ulikooliseadus": "ÜKS", "ulikooli": "UKS_2"}


def test_suffix_skips_values_taken_in_corpus_form() -> None:
    """The ``_N`` suffix must be free in corpus form too (ROS_2 already held)."""
    assigned = reserve_abbreviations([
        ("a_law", "ROS", "rt_api"),
        ("b_law", "ROS_2", "rt_api"),
        ("c_law", "RÕS", "rt_api"),
    ])
    assert assigned["c_law"] == "RÕS_3"
    assert len({corpus_prefix(a) for a in assigned.values()}) == 3


def test_assign_abbreviations_uses_corpus_form() -> None:
    peep = {
        "tsiviilseadustik": {"title": "TsÜS legacy", "prefix": "TsUS"},
        "tsiviilseadustiku_uldosa_seadus": {"title": "TsÜS", "prefix": "TsS"},
    }
    registry, stats = assign_abbreviations(
        peep, {"tsiviilseadustiku_uldosa_seadus": "TsÜS"}
    )
    assert registry["tsiviilseadustiku_uldosa_seadus"]["abbrev"] == "TsÜS"
    assert registry["tsiviilseadustik"]["abbrev"] == "TsUS_2"
    assert stats == {"rt_api": 1, "existing": 1, "auto": 0}
    # Registry keeps the historical tier-then-slug emission order.
    assert list(registry) == ["tsiviilseadustiku_uldosa_seadus", "tsiviilseadustik"]


def test_plan_registry_collisions_is_idempotent() -> None:
    registry = {
        "rahvusooperi_seadus": {"abbrev": "ROS", "source": "rt_api", "title": "R", "old_prefix": "ROS"},
        "riigi_oigusabi_seadus": {"abbrev": "RÕS", "source": "rt_api", "title": "S", "old_prefix": "RS"},
        "pks": {"abbrev": "PKS", "source": "rt_api", "title": "P", "old_prefix": "PKS"},
    }
    plan = plan_registry_collisions(registry)
    assert [(r.slug, r.keeper_slug, r.new_abbrev, r.from_prefix, r.to_prefix) for r in plan] == [
        ("riigi_oigusabi_seadus", "rahvusooperi_seadus", "RÕS_2", "ROS", "ROS_2")
    ]
    registry["riigi_oigusabi_seadus"]["abbrev"] = "RÕS_2"
    assert plan_registry_collisions(registry) == []


@pytest.mark.parametrize(
    ("iri", "expected"),
    [
        ("estleg:ROS_Map", "estleg:ROS_2_Map"),
        ("estleg:ROS_Par_6_Lg_1", "estleg:ROS_2_Par_6_Lg_1"),
        ("estleg:Cluster_ROS_2", "estleg:Cluster_ROS_2_2"),
        ("estleg:Chapter_ROS_1", "estleg:Chapter_ROS_2_1"),
        ("estleg:AmendmentChain_ROS", "estleg:AmendmentChain_ROS_2"),
        ("estleg:Amendment_ROS_vf_20140701", "estleg:Amendment_ROS_2_vf_20140701"),
        ("estleg:AmendmentLink_Draft_ROS_12_ROS", "estleg:AmendmentLink_Draft_ROS_12_ROS_2"),
        ("estleg:Sanction_ROS_Par_4_coercive_payment", "estleg:Sanction_ROS_2_Par_4_coercive_payment"),
        ("estleg:ROSE_Par_1", None),
        ("estleg:KROS_Par_1", None),
    ],
)
def test_rename_prefix_in_iri(iri: str, expected: str | None) -> None:
    assert rename_prefix_in_iri(iri, "ROS", "ROS_2") == expected


def test_law_own_files_exact_slug_match(tmp_path: Path) -> None:
    for name in (
        "tsiviilseadustik_osa1_peep.json",
        "tsiviilseadustik_osa12_peep.json",
        "tsiviilseadustiku_uldosa_seadus_osa2_peep.json",
        "tsiviilseadustik_peep.json",
    ):
        (tmp_path / name).write_text("{}", encoding="utf-8")
    names = [p.name for p in law_own_files("tsiviilseadustik", tmp_path)]
    assert names == [
        "tsiviilseadustik_osa12_peep.json",
        "tsiviilseadustik_osa1_peep.json",
        "tsiviilseadustik_peep.json",
    ]


# ── End-to-end file-scoped repair ────────────────────────────────────────────

CTX = {"estleg": "https://w3id.org/estleg/", "owl": "http://www.w3.org/2002/07/owl#",
       "dcterms": "http://purl.org/dc/terms/"}


def _write(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _graph(*nodes: dict) -> dict:
    return {"@context": CTX, "@graph": list(nodes)}


@pytest.fixture
def corpus(tmp_path: Path) -> dict[str, Path]:
    krr = tmp_path / "krr_outputs"
    data = tmp_path / "data"
    data.mkdir()
    registry = {
        "rahvusooperi_seadus": {"abbrev": "ROS", "source": "rt_api", "title": "Rahvusooperi seadus", "old_prefix": "ROS"},
        "riigi_oigusabi_seadus": {"abbrev": "RÕS", "source": "rt_api", "title": "Riigi õigusabi seadus", "old_prefix": "RS"},
        "ulikooliseadus": {"abbrev": "ÜKS", "source": "rt_api", "title": "Ülikooliseadus", "old_prefix": "UlikooliS"},
        "ulikooli": {"abbrev": "UKS", "source": "existing", "title": "Ülikooliseadus", "old_prefix": "UKS"},
    }
    (data / "law_abbreviations.json").write_text(
        json.dumps(registry, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (data / "migration_state.json").write_text("{}\n", encoding="utf-8")
    decisions = {"deprecations": [{
        "file": "ulikooli_peep.json", "rootIri": "estleg:UKS_Map",
        "replacedByFile": "ulikooliseadus_peep.json", "replacedByIri": "estleg:UKS_Map",
    }], "keeps": []}
    (data / "legacy_statute_decisions.json").write_text(
        json.dumps(decisions, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    # Keeper ROS (rahvusooperi) and the yielding RÕS (riigi_oigusabi).
    _write(krr / "rahvusooperi_seadus_peep.json", _graph(
        {"@id": "estleg:ROS_Map", "@type": ["estleg:Act"]},
        {"@id": "estleg:ROS_Par_1", "@type": ["estleg:Section"], "estleg:partOfAct": {"@id": "estleg:ROS_Map"}},
        {"@id": "estleg:Cluster_ROS_2", "@type": ["estleg:TopicCluster"]},
    ))
    _write(krr / "riigi_oigusabi_seadus_peep.json", _graph(
        {"@id": "estleg:ROS_Map", "@type": ["estleg:Act"],
         "estleg:hasProposedAmendment": [{"@id": "estleg:AmendmentLink_Draft_X_1_ROS"}]},
        {"@id": "estleg:ROS_Par_1", "@type": ["estleg:Section"], "estleg:partOfAct": {"@id": "estleg:ROS_Map"},
         "estleg:requestedCluster": {"@id": "estleg:Cluster_ROS_2"}},
        {"@id": "estleg:ROS_Par_9", "@type": ["estleg:Section"], "estleg:partOfAct": {"@id": "estleg:ROS_Map"}},
        {"@id": "estleg:Cluster_ROS_2", "@type": ["estleg:TopicCluster"]},
    ))
    _write(krr / "amendments" / "amendments_riigi_oigusabi_seadus.json", _graph(
        {"@id": "estleg:AmendmentChain_ROS", "@type": ["estleg:AmendmentChain"],
         "estleg:amends": {"@id": "estleg:ROS_Map"}},
        {"@id": "estleg:AmendmentLink_Draft_X_1_ROS", "@type": ["estleg:AmendmentLink"]},
    ))
    _write(krr / "sanctions" / "sanctions_riigi_oigusabi_seadus.json", _graph(
        {"@id": "estleg:Sanction_ROS_Par_9_fine", "@type": ["estleg:Sanction"],
         "estleg:sanctionedBy": {"@id": "estleg:ROS_Par_9"}},
    ))
    # Another law citing an ambiguous (Par_1) and an unambiguous (Par_9) id.
    _write(krr / "other_seadus_peep.json", _graph(
        {"@id": "estleg:OS_Par_1", "@type": ["estleg:Section"],
         "estleg:references": [{"@id": "estleg:ROS_Par_1"}, {"@id": "estleg:ROS_Par_9"}]},
    ))
    # Live ÜKS and its #426 deprecated duplicate sharing the root IRI.
    _write(krr / "ulikooliseadus_peep.json", _graph(
        {"@id": "estleg:UKS_Map", "@type": ["estleg:Act"]},
        {"@id": "estleg:ulikooliseadus_Par_1", "@type": ["estleg:Section"]},
    ))
    _write(krr / "ulikooli_peep.json", _graph(
        {"@id": "estleg:UKS_Map", "@type": ["estleg:Act"], "owl:deprecated": True,
         "dcterms:isReplacedBy": {"@id": "estleg:UKS_Map"}},
        {"@id": "estleg:UKS_Par_1", "@type": ["estleg:Section"], "estleg:partOfAct": {"@id": "estleg:UKS_Map"}},
    ))
    return {"krr": krr, "data": data}


def _run(corpus: dict[str, Path], **kw) -> dict:
    data = corpus["data"]
    return resolve_collisions(
        krr_dir=corpus["krr"],
        registry_path=data / "law_abbreviations.json",
        state_path=data / "migration_state.json",
        decisions_path=data / "legacy_statute_decisions.json",
        **kw,
    )


def _ids(path: Path) -> list[str]:
    return [n["@id"] for n in json.loads(path.read_text(encoding="utf-8"))["@graph"]]


def _snapshot(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*.json"))}


def test_resolve_collisions_end_to_end(corpus: dict[str, Path]) -> None:
    krr, data = corpus["krr"], corpus["data"]
    keeper_before = (krr / "rahvusooperi_seadus_peep.json").read_bytes()
    summary = _run(corpus)

    # Registry + state bookkeeping.
    registry = json.loads((data / "law_abbreviations.json").read_text(encoding="utf-8"))
    assert registry["riigi_oigusabi_seadus"]["abbrev"] == "RÕS_2"
    assert registry["riigi_oigusabi_seadus"]["old_prefix"] == "ROS_2"
    assert registry["ulikooli"]["abbrev"] == "UKS_2"
    assert registry["ulikooliseadus"]["abbrev"] == "ÜKS"
    state = json.loads((data / "migration_state.json").read_text(encoding="utf-8"))
    assert state["collision_resolutions"]["riigi_oigusabi_seadus"]["previous_old_prefix"] == "RS"
    # The #178 sentinel stays bound to the registry this layer wrote.
    assert state["last_applied_registry_hash"] == compute_registry_hash(
        data / "law_abbreviations.json"
    )

    # The keeper is byte-identical; the yielding law's own files are renamed.
    assert (krr / "rahvusooperi_seadus_peep.json").read_bytes() == keeper_before
    assert _ids(krr / "riigi_oigusabi_seadus_peep.json") == [
        "estleg:ROS_2_Map", "estleg:ROS_2_Par_1", "estleg:ROS_2_Par_9", "estleg:Cluster_ROS_2_2",
    ]
    peep = json.loads((krr / "riigi_oigusabi_seadus_peep.json").read_text(encoding="utf-8"))
    assert peep["@graph"][1]["estleg:requestedCluster"] == {"@id": "estleg:Cluster_ROS_2_2"}
    assert peep["@graph"][0]["estleg:hasProposedAmendment"] == [
        {"@id": "estleg:AmendmentLink_Draft_X_1_ROS_2"}
    ]
    assert _ids(krr / "amendments" / "amendments_riigi_oigusabi_seadus.json") == [
        "estleg:AmendmentChain_ROS_2", "estleg:AmendmentLink_Draft_X_1_ROS_2",
    ]
    sanction = json.loads((krr / "sanctions" / "sanctions_riigi_oigusabi_seadus.json").read_text(encoding="utf-8"))
    assert sanction["@graph"][0]["@id"] == "estleg:Sanction_ROS_2_Par_9_fine"
    assert sanction["@graph"][0]["estleg:sanctionedBy"] == {"@id": "estleg:ROS_2_Par_9"}

    # Cross-file: the unambiguous Par_9 follows the rename, the ambiguous
    # Par_1 stays on the keeper and is reported.
    other = json.loads((krr / "other_seadus_peep.json").read_text(encoding="utf-8"))
    assert other["@graph"][0]["estleg:references"] == [
        {"@id": "estleg:ROS_Par_1"}, {"@id": "estleg:ROS_2_Par_9"},
    ]
    ros = next(law for law in summary["laws"] if law["slug"] == "riigi_oigusabi_seadus")
    assert ros["ambiguous_refs"] == {"estleg:ROS_Par_1": {"other_seadus_peep.json": 1}}

    # Legacy duplicate: renamed root, isReplacedBy still on the live root,
    # and the #426 record is no longer self-referential.
    legacy = json.loads((krr / "ulikooli_peep.json").read_text(encoding="utf-8"))
    root = legacy["@graph"][0]
    assert root["@id"] == "estleg:UKS_2_Map"
    assert root["owl:deprecated"] is True
    assert root["dcterms:isReplacedBy"] == {"@id": "estleg:UKS_Map"}
    assert legacy["@graph"][1]["estleg:partOfAct"] == {"@id": "estleg:UKS_2_Map"}
    # #427 bridge now possible: legacy UKS_2_Par_1 -> canonical ulikooliseadus_Par_1.
    assert legacy["@graph"][1]["owl:sameAs"] == {"@id": "estleg:ulikooliseadus_Par_1"}
    record = json.loads((data / "legacy_statute_decisions.json").read_text(encoding="utf-8"))
    entry = record["deprecations"][0]
    assert entry["rootIri"] == "estleg:UKS_2_Map"
    assert entry["replacedByIri"] == "estleg:UKS_Map"


def test_resolve_collisions_second_run_is_a_noop(corpus: dict[str, Path]) -> None:
    _run(corpus)
    before = _snapshot(corpus["krr"].parent)
    summary = _run(corpus)
    assert _snapshot(corpus["krr"].parent) == before
    assert summary["planned"] == []
    assert all(not law["own_writes"] and not law["cross_writes"] for law in summary["laws"])
    assert summary["legacy_changes"] == []


def test_resolve_collisions_dry_run_writes_nothing(corpus: dict[str, Path]) -> None:
    before = _snapshot(corpus["krr"].parent)
    summary = _run(corpus, dry_run=True)
    assert _snapshot(corpus["krr"].parent) == before
    assert {r["slug"] for r in summary["planned"]} == {"riigi_oigusabi_seadus", "ulikooli"}


def test_resolve_collisions_refuses_existing_target(corpus: dict[str, Path]) -> None:
    """A minted IRI that another file already uses aborts before any write."""
    _write(corpus["krr"] / "squatter_peep.json", _graph(
        {"@id": "estleg:ROS_2_Par_1", "@type": ["estleg:Section"]},
    ))
    target = corpus["krr"] / "riigi_oigusabi_seadus_peep.json"
    before = target.read_bytes()
    with pytest.raises(RuntimeError, match="refusing to mint"):
        _run(corpus)
    assert target.read_bytes() == before


def test_deprecated_duplicate_of_unregistered_live_law(tmp_path: Path) -> None:
    """TsMS shape: the live law is NOT in the registry, the #426 stub is."""
    krr, data = tmp_path / "krr_outputs", tmp_path / "data"
    data.mkdir()
    (data / "law_abbreviations.json").write_text(json.dumps({
        "tsiviilkohtumenetluse": {"abbrev": "TsMS", "source": "existing", "title": "T", "old_prefix": "TsMS"},
    }, indent=2), encoding="utf-8")
    (data / "migration_state.json").write_text("{}\n", encoding="utf-8")
    (data / "legacy_statute_decisions.json").write_text(json.dumps({"deprecations": [{
        "file": "tsiviilkohtumenetluse_peep.json", "rootIri": "estleg:TsMS_Map",
        "replacedByFile": "tsiviilkohtumenetluse_seadustik_osa1_peep.json",
        "replacedByIri": "estleg:tsiviilkohtumenetluse_seadustik_Osa1",
    }]}, indent=2) + "\n", encoding="utf-8")
    _write(krr / "tsiviilkohtumenetluse_peep.json", _graph(
        {"@id": "estleg:TsMS_Map", "@type": ["estleg:Act"], "owl:deprecated": True,
         "dcterms:isReplacedBy": {"@id": "estleg:tsiviilkohtumenetluse_seadustik_Osa1"}},
        {"@id": "estleg:TsMS_Norm_001", "@type": ["estleg:LegalNorm"],
         "estleg:partOfAct": {"@id": "estleg:TsMS_Map"}},
    ))
    _write(krr / "tsiviilkohtumenetluse_seadustik_map_peep.json", _graph(
        {"@id": "estleg:TsMS_Map", "@type": ["estleg:Act"],
         "estleg:hasPart": [{"@id": "estleg:tsiviilkohtumenetluse_seadustik_Osa1"}]},
    ))
    _write(krr / "tsiviilkohtumenetluse_seadustik_osa1_peep.json", _graph(
        {"@id": "estleg:tsiviilkohtumenetluse_seadustik_Osa1", "@type": ["estleg:Part"],
         "estleg:isPartOf": {"@id": "estleg:TsMS_Map"}},
    ))
    live_before = (krr / "tsiviilkohtumenetluse_seadustik_map_peep.json").read_bytes()
    corpus = {"krr": krr, "data": data}
    summary = _run(corpus)

    assert [(r["slug"], r["keeper_slug"], r["new_abbrev"]) for r in summary["planned"]] == [
        ("tsiviilkohtumenetluse", "tsiviilkohtumenetluse_seadustik", "TsMS_2")
    ]
    assert (krr / "tsiviilkohtumenetluse_seadustik_map_peep.json").read_bytes() == live_before
    assert _ids(krr / "tsiviilkohtumenetluse_peep.json") == ["estleg:TsMS_2_Map", "estleg:TsMS_2_Norm_001"]
    entry = json.loads((data / "legacy_statute_decisions.json").read_text(encoding="utf-8"))["deprecations"][0]
    assert entry["rootIri"] == "estleg:TsMS_2_Map"
    assert entry["replacedByIri"] == "estleg:tsiviilkohtumenetluse_seadustik_Osa1"

    before = _snapshot(tmp_path)
    assert _run(corpus)["planned"] == []
    assert _snapshot(tmp_path) == before

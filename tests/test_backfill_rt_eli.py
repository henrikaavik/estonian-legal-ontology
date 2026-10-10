"""#707 — backfill_rt_eli: legacy roots, multipart map English text, legacy IRI bridge."""

from __future__ import annotations

import json
from pathlib import Path

from estleg import backfill_rt_eli as be

EN = "https://www.riigiteataja.ee/en/eli/505022026001"


def _write(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def test_legacy_root_is_split_and_given_ids() -> None:
    root = {
        "@id": "estleg:AVRS_Map", "@type": ["estleg:Act", "estleg:Law"],
        "dcterms:title": "Abieluvararegistri seadus",
        "dcterms:source": {"@id": "https://www.riigiteataja.ee/akt/114032025013.xml"},
    }
    assert be.backfill_root_identity(root) is True
    assert root["dcterms:source"] == {"@id": "https://www.riigiteataja.ee/akt/114032025013"}
    assert root["estleg:sourceXml"]["@id"].endswith("/akt/114032025013/xml")
    assert root["estleg:globalId"] == root["eli:id_local"] == "114032025013"
    assert list(root)[:5] == ["@id", "@type", "dcterms:title", "dcterms:source", "estleg:sourceXml"]
    assert be.backfill_root_identity(root) is False


def test_root_without_rt_link_is_untouched() -> None:
    root = {"@id": "estleg:X_Map", "@type": ["estleg:Act", "estleg:Law"],
            "dcterms:source": {"@id": "https://www.stat.ee/ehak.csv"}}
    assert be.backfill_root_identity(root) is False


def test_generator_stamped_redaction_wins_over_an_older_link() -> None:
    root = {"@id": "estleg:X_Map", "@type": ["estleg:Act", "estleg:Law"],
            "estleg:globalId": "2",
            "dcterms:source": {"@id": "https://www.riigiteataja.ee/akt/1.xml"}}
    be.backfill_root_identity(root)
    assert root["dcterms:source"] == {"@id": "https://www.riigiteataja.ee/akt/2"}


def test_map_root_takes_the_english_text_its_parts_agree_on() -> None:
    map_doc = {"@graph": [{"@id": "estleg:AOS_Map", "@type": ["estleg:Act", "estleg:Law"]}]}
    parts = [{"@graph": [{"@id": f"estleg:AOS_Osa{i}", "@type": ["estleg:Part"],
                          "estleg:officialEnglishText": {"@id": EN}}]} for i in (1, 2)]
    assert be.backfill_map_root_english(map_doc, parts) is True
    assert map_doc["@graph"][0]["estleg:officialEnglishText"] == {"@id": EN}
    disagree = [*parts, {"@graph": [{"@id": "estleg:AOS_Osa3", "@type": ["estleg:Part"],
                                     "estleg:officialEnglishText": {"@id": EN + "9"}}]}]
    fresh = {"@graph": [{"@id": "estleg:AOS_Map", "@type": ["estleg:Act", "estleg:Law"]}]}
    assert be.backfill_map_root_english(fresh, disagree) is False


def test_backfill_corpus_dry_run_then_apply(tmp_path) -> None:
    krr = tmp_path / "krr_outputs"
    law = {"@graph": [{
        "@id": "estleg:AVRS_Map", "@type": ["estleg:Act", "estleg:Law"],
        "dcterms:source": {"@id": "https://www.riigiteataja.ee/akt/114032025013.xml"},
        "estleg:officialEnglishText": {"@id": EN},
    }]}
    _write(krr / "abieluvararegistri_seadus_peep.json", law)
    before = (krr / "abieluvararegistri_seadus_peep.json").read_text(encoding="utf-8")
    stats = be.backfill_corpus(krr, apply=False)
    assert stats["changed"] == 1
    assert (krr / "abieluvararegistri_seadus_peep.json").read_text(encoding="utf-8") == before
    be.backfill_corpus(krr, apply=True)
    doc = json.loads((krr / "abieluvararegistri_seadus_peep.json").read_text(encoding="utf-8"))
    assert doc["@graph"][0]["eli:id_local"] == "114032025013"
    assert [n["@id"] for n in doc["@graph"]] == ["estleg:AVRS_Map", EN]
    assert be.backfill_corpus(krr, apply=True)["changed"] == 0


def test_bridge_is_inverted_and_unresolvable_rows_are_dropped() -> None:
    rows = [
        {"@id": "estleg:AVRS_Map", "owl:sameAs": {"@id": "estleg:AVRS_Map_2026"}},
        {"@id": "estleg:AVRS_Map", "owl:sameAs": {"@id": "estleg:AVRS_Map_2025"}},
        {"@id": "estleg:Gone_Map", "owl:sameAs": {"@id": "estleg:Gone_Map_2026"}},
    ]
    graph, dropped = be.build_bridge(rows, {"estleg:AVRS_Map"})
    assert [n["@id"] for n in graph] == ["estleg:AVRS_Map_2025", "estleg:AVRS_Map_2026"]
    assert all(n["owl:sameAs"] == {"@id": "estleg:AVRS_Map"} for n in graph)
    assert dropped == ["estleg:Gone_Map_2026 -> estleg:Gone_Map"]


def test_publish_bridge_writes_under_bridges(tmp_path) -> None:
    krr = tmp_path / "krr_outputs"
    _write(krr / "abieluvararegistri_seadus_peep.json",
           {"@graph": [{"@id": "estleg:AVRS_Map", "@type": ["estleg:Act", "estleg:Law"]}]})
    _write(krr / "regulations" / "riik" / "r_peep.json",
           {"@graph": [{"@id": "estleg:Reg_1_Map", "@type": ["estleg:Act"]}]})
    source = tmp_path / "act_iri_v2_sameas.jsonld"
    _write(source, {"@graph": [
        {"@id": "estleg:AVRS_Map", "owl:sameAs": {"@id": "estleg:AVRS_Map_2026"}},
        {"@id": "estleg:Reg_1_Map", "owl:sameAs": {"@id": "estleg:Reg_1_Map_2026"}},
    ]})
    stats = be.publish_bridge(krr, source=source, apply=True)
    assert stats["published"] == 2 and stats["dropped"] == 0
    out = json.loads((krr / "bridges" / "act_iri_v2_sameas.jsonld").read_text(encoding="utf-8"))
    assert {n["@id"] for n in out["@graph"]} == {"estleg:AVRS_Map_2026", "estleg:Reg_1_Map_2026"}
    assert "@context" in out

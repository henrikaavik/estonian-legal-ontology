"""#707 — RT identity on act roots, English expressions, legacy bridge on the load surface."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

from estleg import generate_all_laws as gal
from estleg.backfill_rt_eli import (
    ENGLISH_LANGUAGE_IRI,
    promote_english_expression,
    rt_identity_fields,
)
from estleg.estleg_common import (
    COMBINED_OVERLAY_SUBDIRS,
    PUBLIC_LOAD_SUBDIRS,
    iter_public_load_files,
    jsonld_id_values,
)

REPO = Path(__file__).resolve().parent.parent
EN = "https://www.riigiteataja.ee/en/eli/524032026004"
XML = """<oigusakt><metaandmed><globaalID>112052026033</globaalID>
<terviktekstiGrupiID>163422</terviktekstiGrupiID></metaandmed><sisu>
<paragrahv><paragrahvNr>1</paragrahvNr><kuvatavNr>§ 1.</kuvatavNr>
<loige><loigeNr>1</loigeNr><sisuTekst><tavatekst>Tekst.</tavatekst></sisuTekst></loige>
</paragrahv></sisu></oigusakt>"""


def _law() -> dict:
    return gal.generate_law_jsonld(
        "Jäätmeseadus", "jaatmeseadus", ET.fromstring(XML), "JäätS",
        "/akt/112052026033.xml", kehtiv="2026-10-09",
        allocator=gal.PrefixAllocator(registry={}),
    )


def test_source_is_the_human_page_and_xml_is_a_separate_manifestation() -> None:
    head = _law()["@graph"][0]
    assert head["dcterms:source"] == {"@id": "https://www.riigiteataja.ee/akt/112052026033"}
    assert head["estleg:sourceXml"] == {
        "@id": "https://www.riigiteataja.ee/public-api/api/v1/akt/112052026033/xml"
    }
    assert head["eli:id_local"] == head["estleg:globalId"] == "112052026033"


def test_no_owl_sameas_to_an_unconfirmed_estonian_eli() -> None:
    # The Estonian ELI template is not confirmed by Riigi Teataja; the
    # globaalID is evidently not its key (english_eli.json maps act
    # 101032023045 to English 504042023007). Nothing may mint that join.
    head = _law()["@graph"][0]
    assert not any("/eli/" in iri for iri in jsonld_id_values(head.get("owl:sameAs")))
    fields = rt_identity_fields(global_id="112052026033")
    assert "owl:sameAs" not in fields


def test_part_roots_get_no_eli_id_local() -> None:
    assert "eli:id_local" not in rt_identity_fields(global_id="1", act_root=False)
    assert "eli:id_local" in rt_identity_fields(global_id="1", act_root=True)


def test_english_text_becomes_a_legal_expression() -> None:
    doc = _law()
    head = doc["@graph"][0]
    head["estleg:officialEnglishText"] = {"@id": EN}
    assert promote_english_expression(doc, english_titles={EN: "Waste Act"}) is True
    expr = next(n for n in doc["@graph"] if n["@id"] == EN)
    assert expr["@type"] == ["eli:LegalExpression"]
    assert expr["eli:language"] == {"@id": ENGLISH_LANGUAGE_IRI}
    assert expr["eli:realizes"] == {"@id": head["@id"]}
    assert expr["rdfs:label"] == {"@value": "Waste Act", "@language": "en"}
    assert head["eli:is_realized_by"] == {"@id": EN}
    # Idempotent.
    assert promote_english_expression(doc, english_titles={EN: "Waste Act"}) is False


def test_a_new_english_consolidation_replaces_the_old_expression() -> None:
    doc = _law()
    head = doc["@graph"][0]
    head["estleg:officialEnglishText"] = {"@id": EN}
    promote_english_expression(doc)
    newer = "https://www.riigiteataja.ee/en/eli/599999999999"
    head["estleg:officialEnglishText"] = {"@id": newer}
    promote_english_expression(doc)
    ids = [n["@id"] for n in doc["@graph"]]
    assert EN not in ids and newer in ids
    assert head["eli:is_realized_by"] == {"@id": newer}
    del head["estleg:officialEnglishText"]
    promote_english_expression(doc)
    assert newer not in [n["@id"] for n in doc["@graph"]]
    assert "eli:is_realized_by" not in head


def test_part_roots_are_not_given_an_expression() -> None:
    doc = {"@graph": [{"@id": "estleg:AOS_Osa1", "@type": ["estleg:Part"],
                       "estleg:officialEnglishText": {"@id": EN}}]}
    assert promote_english_expression(doc) is False
    assert len(doc["@graph"]) == 1


def test_write_law_output_promotes_the_merged_english_text(tmp_path) -> None:
    doc = _law()
    head_id = doc["@graph"][0]["@id"]
    path = tmp_path / "jaatmeseadus_peep.json"
    # officialEnglishText is an enrichment (backfill_official_english); the
    # regen merges it back and must emit the expression in the same write.
    path.write_text(json.dumps({"@graph": [{"@id": head_id, "estleg:officialEnglishText": {"@id": EN}}]}),
                    encoding="utf-8")
    gal.write_law_output(path, doc, mode="refresh")
    written = json.loads(path.read_text(encoding="utf-8"))
    assert EN in [n["@id"] for n in written["@graph"]]


def test_bridge_is_on_the_load_surface_but_not_merged_into_combined() -> None:
    assert "bridges" in PUBLIC_LOAD_SUBDIRS
    assert "bridges" not in COMBINED_OVERLAY_SUBDIRS


def test_bridge_file_is_picked_up_by_the_public_loader(tmp_path) -> None:
    bridges = tmp_path / "bridges"
    bridges.mkdir()
    (bridges / "act_iri_v2_sameas.jsonld").write_text('{"@graph": []}', encoding="utf-8")
    assert bridges / "act_iri_v2_sameas.jsonld" in iter_public_load_files(tmp_path)


def test_controlled_vocabulary_declares_the_new_terms() -> None:
    vocab = json.loads((REPO / "krr_outputs" / "controlled_vocabulary.jsonld").read_text(encoding="utf-8"))
    by_id = {n.get("@id"): n for n in vocab["@graph"] if isinstance(n, dict)}
    assert by_id["estleg:sourceXml"]["@type"] == ["owl:ObjectProperty"]
    assert by_id["estleg:sourceXml"]["rdfs:subPropertyOf"] == {"@id": "prov:wasDerivedFrom"}
    assert by_id["estleg:skeemiNimi"]["@type"] == ["owl:DatatypeProperty"]
    assert by_id["estleg:skeemiNimi"]["rdfs:domain"] == {"@id": "estleg:Act"}
    assert "eli:is_realized_by" in json.dumps(by_id["estleg:officialEnglishText"]["rdfs:comment"])

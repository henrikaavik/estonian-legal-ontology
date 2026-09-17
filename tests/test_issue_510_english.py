"""#510 — official English RT /en/eli/ links + CELLAR English titles."""

from __future__ import annotations

import inspect
import json
from pathlib import Path

from estleg.backfill_official_english import (
    akt_id_from_source,
    apply_row_to_node,
    attach_official_english,
    english_eli_from_meta,
    is_english_eli_id,
    official_english_url,
)
from estleg.estleg_common import merge_title_langstring, title_langstrings
from estleg import generate_eu_legislation as eu_mod
from estleg import generate_transposition_mapping as tx_mod

REPO = Path(__file__).resolve().parent.parent
VOCAB = REPO / "krr_outputs" / "controlled_vocabulary.jsonld"
SCHEMA_REF = REPO / "docs" / "SCHEMA_REFERENCE.md"
PS_PEEP = REPO / "krr_outputs" / "eesti_vabariigi_pohiseadus_peep.json"


def _vocab_index() -> dict[str, dict]:
    doc = json.loads(VOCAB.read_text(encoding="utf-8"))
    return {
        node["@id"]: node
        for node in doc.get("@graph", [])
        if isinstance(node, dict) and isinstance(node.get("@id"), str)
    }


def _as_ids(value: object) -> set[str]:
    if isinstance(value, dict):
        iri = value.get("@id")
        return {iri} if isinstance(iri, str) else set()
    if isinstance(value, list):
        ids: set[str] = set()
        for item in value:
            ids |= _as_ids(item)
        return ids
    if isinstance(value, str):
        return {value}
    return set()


def test_cv_declares_official_english_text() -> None:
    node = _vocab_index()["estleg:officialEnglishText"]
    assert "owl:ObjectProperty" in node["@type"]
    assert _as_ids(node.get("rdfs:subPropertyOf")) == {"rdfs:seeAlso"}
    assert _as_ids(node.get("rdfs:domain")) == {"estleg:Act"}
    assert _as_ids(node.get("rdfs:range")) == {"rdfs:Resource"}
    labels = {
        item["@language"]: item["@value"]
        for item in node["rdfs:label"]
    }
    assert labels["et"] == "ametlik ingliskeelne tekst"
    assert labels["en"] == "official English text"


def test_schema_reference_documents_official_english_text() -> None:
    text = SCHEMA_REF.read_text(encoding="utf-8")
    assert "estleg:officialEnglishText" in text
    assert "tolkeSeosId" in text
    assert "/en/eli/" in text


def test_akt_id_from_source_shapes() -> None:
    assert akt_id_from_source(
        {"@id": "https://www.riigiteataja.ee/akt/111042025003.xml"}
    ) == "111042025003"
    assert akt_id_from_source("https://www.riigiteataja.ee/akt/106032026003") == (
        "106032026003"
    )
    assert akt_id_from_source({"@id": "http://publications.europa.eu/resource/celex/32016L0798"}) is None
    assert akt_id_from_source(None) is None


def test_english_eli_band_and_url() -> None:
    assert is_english_eli_id("523042025002")
    assert is_english_eli_id(519112018001)
    assert not is_english_eli_id("111042025003")  # current ET consolidation
    assert not is_english_eli_id("not-a-number")
    assert official_english_url("523042025002") == (
        "https://www.riigiteataja.ee/en/eli/523042025002"
    )


def test_english_eli_from_meta_requires_band() -> None:
    assert english_eli_from_meta({"tolkeSeosId": 523042025002}) == "523042025002"
    assert english_eli_from_meta({"tolkeSeosId": 111042025003}) is None
    assert english_eli_from_meta({"tolkeSeosId": None}) is None
    assert english_eli_from_meta({}) is None


def test_attach_official_english_is_idempotent() -> None:
    node: dict = {"@id": "estleg:PS_Map"}
    assert attach_official_english(node, "523042025002") is True
    assert node["estleg:officialEnglishText"] == {
        "@id": "https://www.riigiteataja.ee/en/eli/523042025002"
    }
    assert attach_official_english(node, "523042025002") is False
    assert attach_official_english(node, "111042025003") is False


def test_attach_does_not_use_current_akt_id_as_eli() -> None:
    """The current Estonian /akt/ id is not the English ELI slug."""
    node = {"dcterms:source": {"@id": "https://www.riigiteataja.ee/akt/111042025003.xml"}}
    assert attach_official_english(node, "111042025003") is False
    assert "estleg:officialEnglishText" not in node


def test_apply_row_merges_english_title() -> None:
    node = {"dcterms:title": "Eesti Vabariigi põhiseadus"}
    changed = apply_row_to_node(
        node,
        {
            "eli_id": "523042025002",
            "title_en": "The Constitution of the Republic of Estonia",
        },
    )
    assert changed is True
    assert node["estleg:officialEnglishText"]["@id"].endswith("/en/eli/523042025002")
    titles = node["dcterms:title"]
    assert isinstance(titles, list)
    by_lang = {item["@language"]: item["@value"] for item in titles}
    assert by_lang["et"] == "Eesti Vabariigi põhiseadus"
    assert by_lang["en"] == "The Constitution of the Republic of Estonia"


def test_title_langstrings_omits_empty_english() -> None:
    assert title_langstrings("Raudteeseadus") == {
        "@value": "Raudteeseadus",
        "@language": "et",
    }
    both = title_langstrings("Raudteeseadus", "Railway Act")
    assert both == [
        {"@value": "Raudteeseadus", "@language": "et"},
        {"@value": "Railway Act", "@language": "en"},
    ]


def test_merge_title_langstring_promotes_plain_string() -> None:
    node = {"dcterms:title": "Avaliku teabe seadus"}
    assert merge_title_langstring(node, "en", "Public Information Act") is True
    assert merge_title_langstring(node, "en", "Public Information Act") is False


def test_transposition_query_keeps_et_filter_and_binds_title_en() -> None:
    src = inspect.getsource(tx_mod.fetch_transposition_measures)
    assert "lang(?title_nat) = 'et'" in src
    assert "lang(?title_nat) = ''" in src
    assert "lang(?title_en) = 'en'" in src
    assert "?title_en" in src


def test_fetch_retains_title_en_without_using_it_for_matching(monkeypatch) -> None:
    """English is stored on the item; it is not a second match key (#318/#510)."""

    def _row(celex: str, title: str, lang: str | None, title_en: str = "") -> dict:
        title_val: dict[str, str] = {"type": "literal", "value": title}
        if lang is not None:
            title_val["xml:lang"] = lang
        row = {
            "nim": {"type": "uri", "value": f"http://nim/{celex}/{lang or 'none'}"},
            "directive": {"type": "uri", "value": f"http://dir/{celex}"},
            "celex_dir": {"type": "literal", "value": celex},
            "title_nat": title_val,
        }
        if title_en:
            row["title_en"] = {
                "type": "literal",
                "value": title_en,
                "xml:lang": "en",
            }
        return row

    rows = [
        _row("32016L0798", "Commentaire en francais", "fr", "Railway Safety"),
        _row("32016L0798", "Some English comment", "en", "Railway Safety"),
        _row("32016L0798", "Raudteeseadus", "et", "Railway Act"),
        _row("32016L0798", "Liiklusseadus", None),
    ]

    def _one_page(_query, **_kwargs):
        if not getattr(_one_page, "served", False):
            _one_page.served = True
            return rows
        return []

    monkeypatch.setattr(tx_mod, "sparql_query_with_retry", _one_page)
    items, partial = tx_mod.fetch_transposition_measures(allow_partial=False)
    assert partial is False
    by_title = {item["title_nat"]: item for item in items}
    assert set(by_title) == {"Raudteeseadus", "Liiklusseadus"}
    assert by_title["Raudteeseadus"]["title_en"] == "Railway Act"
    assert by_title["Liiklusseadus"].get("title_en") == ""
    assert "Some English comment" not in by_title
    assert "Commentaire en francais" not in by_title


def test_legislation_node_emits_bilingual_title_when_en_present() -> None:
    item = {
        "celex": "32016R0679",
        "title": "Isikuandmete kaitse üldmäärus",
        "title_en": "General Data Protection Regulation",
        "authors": [],
    }
    node = eu_mod.legislation_to_node(item, "Regulation")
    assert node["rdfs:label"] == {
        "@value": "Isikuandmete kaitse üldmäärus",
        "@language": "et",
    }
    assert node["dcterms:title"] == [
        {"@value": "Isikuandmete kaitse üldmäärus", "@language": "et"},
        {"@value": "General Data Protection Regulation", "@language": "en"},
    ]


def test_eu_fetch_query_binds_english_expression_title() -> None:
    src = inspect.getsource(eu_mod.fetch_legislation_type)
    assert "language/ENG" in src
    assert "?title_en" in src
    # Estonian expression remains the required title (do not drop #318).
    assert "language/EST" in src


def test_constitution_peep_has_official_english() -> None:
    """Corpus pin: Constitution English ELI is not the current ET akt id."""
    graph = json.loads(PS_PEEP.read_text(encoding="utf-8")).get("@graph", [])
    node = next(
        n
        for n in graph
        if isinstance(n, dict)
        and "estleg:Act" in (
            n.get("@type") if isinstance(n.get("@type"), list) else [n.get("@type")]
        )
    )
    iri = node["estleg:officialEnglishText"]["@id"]
    assert iri == "https://www.riigiteataja.ee/en/eli/523042025002"
    source = node["dcterms:source"]["@id"]
    assert "111042025003" in source
    assert "523042025002" not in source


def test_corpus_official_english_urls_are_english_eli() -> None:
    peeps = list((REPO / "krr_outputs").glob("*_peep.json"))
    urls: list[str] = []
    for path in peeps:
        doc = json.loads(path.read_text(encoding="utf-8"))
        for node in doc.get("@graph") or []:
            if not isinstance(node, dict):
                continue
            value = node.get("estleg:officialEnglishText")
            if not value:
                continue
            iri = value.get("@id") if isinstance(value, dict) else value
            assert isinstance(iri, str)
            assert iri.startswith("https://www.riigiteataja.ee/en/eli/")
            eli_id = iri.rsplit("/", 1)[-1]
            assert is_english_eli_id(eli_id)
            source = node.get("dcterms:source")
            src_iri = source.get("@id") if isinstance(source, dict) else source
            if isinstance(src_iri, str):
                assert src_iri.rstrip(".xml").rsplit("/", 1)[-1] != eli_id
            urls.append(iri)
    assert len(urls) >= 300

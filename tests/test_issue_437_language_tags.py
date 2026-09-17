"""#437: CV labels bilingual; new generator labels carry @et."""

from __future__ import annotations

from estleg.estleg_common import bilingual_label, et_literal
from estleg.generate_regulations import build_regulation_jsonld
from estleg.tag_vocabulary_labels import VOCAB_PATH, load_jsonld, missing_bilingual
from tests.test_generate_regulations import STRUCTURED_FIXTURE, _parse


def test_et_literal_shape() -> None:
    lit = et_literal("Karistusseadustik")
    assert lit == {"@value": "Karistusseadustik", "@language": "et"}


def test_bilingual_label_shape() -> None:
    pair = bilingual_label("Akt", "Act")
    langs = {item["@language"] for item in pair}
    assert langs == {"et", "en"}


def test_every_cv_label_is_bilingual() -> None:
    leftover = missing_bilingual(load_jsonld(VOCAB_PATH))
    assert leftover == [], leftover[:20]


def test_new_regulation_labels_are_et() -> None:
    root = _parse(STRUCTURED_FIXTURE)
    doc, _ = build_regulation_jsonld("Katse määrus", {}, root, is_kov=False)
    act = doc["@graph"][0]
    assert act["rdfs:label"]["@language"] == "et"
    provisions = [n for n in doc["@graph"] if "estleg:paragrahv" in n]
    assert provisions
    assert provisions[0]["rdfs:label"]["@language"] == "et"
    assert provisions[0]["estleg:summary"]["@language"] == "et"

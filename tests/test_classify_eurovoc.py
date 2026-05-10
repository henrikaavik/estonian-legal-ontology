"""Discovery and output-path tests for classify_eurovoc."""
from __future__ import annotations

from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))



REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = (Path(__file__).parent / "fixtures" / "kov_layer2a"
           / "sample_kov_act.json")


def _read_act_metadata(path: Path):
    """The helper under test: pull title/source from a peep file's
    act-typed node without consulting INDEX.json."""
    from classify_eurovoc import read_act_metadata_from_peep
    return read_act_metadata_from_peep(path)


class TestReadActMetadataFromPeep:
    def test_reads_title_from_kov_act(self):
        meta = _read_act_metadata(FIXTURE)
        assert meta["title"] == "Test KOV act"
        assert meta["source"] == "Test KOV act"
        assert meta["@id"] == "estleg:Reg_9999_Map_2026"

    def test_returns_none_for_provision_only_file(self, tmp_path):
        # A file with no act-typed node should return None, not crash.
        bad = tmp_path / "no_act.json"
        bad.write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:LooseProvision",
                 "@type": ["owl:NamedIndividual"],
                 "rdfs:label": "stray"}
            ],
        }), encoding="utf-8")
        assert _read_act_metadata(bad) is None

    def test_skips_municipality_registry_file(self, tmp_path):
        # municipalities_peep.json's top node is typed only owl:Ontology.
        # Without the registry exclusion, the broad acceptance of
        # owl:Ontology would let EuroVoc tag it as an act.
        registry = tmp_path / "municipalities_peep.json"
        registry.write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Municipalities_Map_2026",
                 "@type": ["owl:Ontology"],
                 "rdfs:label": "Estonian Municipalities (current EHAK)"},
                {"@id": "estleg:Municipality_EHAK_0784",
                 "@type": ["owl:NamedIndividual", "estleg:Municipality"]},
            ],
        }), encoding="utf-8")
        # The registry header node has owl:Ontology but matches the
        # IRI prefix exclusion — read_act_metadata_from_peep returns
        # None and the file is skipped.
        assert _read_act_metadata(registry) is None

    def test_raises_parse_error_for_malformed_json(self, tmp_path):
        from classify_eurovoc import PeepMetadataParseError

        bad = tmp_path / "bad_peep.json"
        bad.write_text("{not json", encoding="utf-8")

        try:
            _read_act_metadata(bad)
        except PeepMetadataParseError as exc:
            assert "bad_peep.json" in str(exc)
        else:
            raise AssertionError("expected PeepMetadataParseError")


def test_extract_text_normalizes_to_nfc_casefold():
    from classify_eurovoc import extract_text_from_law

    text = extract_text_from_law({"@graph": [{"estleg:summary": "ÕIGUS TÖÖ"}]})

    assert text == "õigus töö"


def test_classify_text_normalises_regex_pattern(monkeypatch):
    """Regression for Finding 5: ``r:`` regex patterns must be NFC +
    casefold normalised before being matched, so that authors can write
    them with uppercase letters and decomposed diacritics and still get
    a match against the (already normalised) corpus."""
    import classify_eurovoc

    # ``r:`` pattern with decomposed diacritic (O + COMBINING TILDE) and
    # uppercase letters. After NFC + casefold normalisation in
    # classify_text it becomes ``\bõigus\b`` and matches the corpus
    # produced by extract_text_from_law.
    decomposed_pattern = "r:\\bÕIGUS\\b"
    monkeypatch.setattr(
        classify_eurovoc,
        "EUROVOC_DOMAINS",
        {
            "9999": (
                "test-domain", "test", "Test domain",
                [decomposed_pattern],
            ),
        },
    )

    text = classify_eurovoc.extract_text_from_law(
        {"@graph": [{"estleg:summary": "Isikul on õigus saada teavet."}]}
    )

    results = classify_eurovoc.classify_text(text)

    assert any(code == "9999" for code, *_ in results), (
        "expected r: pattern to match NFC + casefold normalised corpus"
    )

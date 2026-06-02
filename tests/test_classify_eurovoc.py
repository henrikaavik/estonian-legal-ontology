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


# ---------------------------------------------------------------------------
# Regression for #121: extract_text_from_law / read_act_metadata_from_peep
# must unwrap JSON-LD value objects so a fresh-generator corpus and a
# normalized one yield the same search blob / metadata.
# ---------------------------------------------------------------------------

def test_extract_text_unwraps_value_object_fields():
    from classify_eurovoc import extract_text_from_law

    plain = extract_text_from_law({"@graph": [{
        "rdfs:label": "Toolepingu seadus",
        "estleg:summary": "Tooandja peab maksma tootasu.",
    }]})
    value_object = extract_text_from_law({"@graph": [{
        "rdfs:label": {"@value": "Toolepingu seadus", "@language": "et"},
        "estleg:summary": {"@value": "Tooandja peab maksma tootasu.",
                           "@language": "et"},
    }]})
    listed = extract_text_from_law({"@graph": [{
        "rdfs:label": [{"@value": "Toolepingu seadus", "@language": "et"}],
        "estleg:summary": [{"@value": "Tooandja peab maksma tootasu.",
                            "@language": "et"}],
    }]})

    assert plain == value_object == listed
    assert "toolepingu seadus" in plain
    # A stringified dict would leak '@value' / '@language' into the blob.
    assert "@value" not in plain
    assert "@language" not in plain


def test_read_act_metadata_unwraps_value_object_label(tmp_path):
    from classify_eurovoc import read_act_metadata_from_peep

    peep = tmp_path / "act_peep.json"
    peep.write_text(json.dumps({
        "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                     "owl": "http://www.w3.org/2002/07/owl#"},
        "@graph": [
            {"@id": "estleg:Act_1_Map_2026",
             "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
             "rdfs:label": {"@value": "Mingi seadus", "@language": "et"}},
        ],
    }), encoding="utf-8")
    meta = read_act_metadata_from_peep(peep)
    assert meta is not None
    assert meta["title"] == "Mingi seadus"
    assert meta["source"] == "Mingi seadus"


# ---------------------------------------------------------------------------
# Regression for #126: short-stem-prone domains require >=2 distinct keyword
# hits; long/distinctive domains stay at >=1.
# ---------------------------------------------------------------------------

def test_risky_domain_single_short_stem_hit_gets_no_subject():
    """One occurrence of a short stem in a risky domain (health, 'arst')
    must NOT assign that domain — the distinct-keyword gate (>=2) blocks it."""
    from classify_eurovoc import classify_text, extract_text_from_law

    text = extract_text_from_law(
        {"@graph": [{"estleg:summary": "Asutuse juures tootab arst."}]}
    )
    results = classify_text(text)
    assert all(code != "5631" for code, *_ in results), (
        f"health (5631) should not be assigned on a single 'arst' hit; got {results}"
    )


def test_risky_domain_two_distinct_hits_gets_subject():
    """Two distinct keyword hits in the same risky domain clear the gate."""
    from classify_eurovoc import classify_text, extract_text_from_law

    text = extract_text_from_law({"@graph": [{
        "estleg:summary": "Arst suunab patsiendi haiglasse ravimite saamiseks.",
    }]})
    results = classify_text(text)
    assert any(code == "5631" for code, *_ in results), (
        f"health (5631) should be assigned with 'arst'+'haigla'+'ravim'; got {results}"
    )
    # The matched_keywords payload is exposed for review tooling.
    health = next(r for r in results if r[0] == "5631")
    assert len(health) == 6
    matched_keywords = health[5]
    assert "arst" in matched_keywords
    assert "haigla" in matched_keywords


def test_distinctive_domain_single_hit_still_assigned():
    """A domain made of long/distinctive keywords keeps the >=1 default —
    one hit on 'autoriõigus' assigns intellectual-property (3216)."""
    from classify_eurovoc import classify_text, extract_text_from_law

    text = extract_text_from_law(
        {"@graph": [{"estleg:summary": "Teos on kaitstud autoriõigusega."}]}
    )
    results = classify_text(text)
    assert any(code == "3216" for code, *_ in results), (
        f"intellectual-property (3216) should be assigned on a single distinctive hit; got {results}"
    )


def test_advertising_single_keyword_domain_stays_at_one():
    """Advertising (2836) has a single keyword ('reklaam') — it must stay at
    the >=1 default, otherwise it could never be assigned."""
    import classify_eurovoc

    assert classify_eurovoc.MIN_DISTINCT_KEYWORDS_OVERRIDES.get("2836", 1) == 1
    text = classify_eurovoc.extract_text_from_law(
        {"@graph": [{"estleg:summary": "Reklaam peab olema selgelt eristatav."}]}
    )
    results = classify_eurovoc.classify_text(text)
    assert any(code == "2836" for code, *_ in results)


# ---------------------------------------------------------------------------
# Regression for #275: short substring-prone stems (nõue, kunst, vesi, ...)
# must not assign their domain on a single mid-word substring hit. The
# distinct-keyword gate for these domains is now >=2.
# ---------------------------------------------------------------------------

def test_nouetele_substring_does_not_assign_civil_law():
    """'nõue' (civil-law 2431) matches as a substring inside 'nõuetele'/
    'nõuetekohastele' ("requirements"). A technical-requirements act that
    mentions no other civil-law term must NOT be tagged civil-law (#275)."""
    from classify_eurovoc import classify_text, extract_text_from_law

    text = extract_text_from_law({"@graph": [{
        "estleg:summary": "Toode peab vastama nõuetekohastele nõuetele.",
    }]})
    results = classify_text(text)
    assert all(code != "2431" for code, *_ in results), (
        f"civil-law (2431) must not fire on the 'nõuetele' substring; got {results}"
    )


def test_kunstlik_substring_does_not_assign_culture():
    """'kunst' (culture 3221) matches inside 'kunstlik' ("artificial"). A lone
    such substring hit must not assign culture (#275)."""
    from classify_eurovoc import classify_text, extract_text_from_law

    text = extract_text_from_law(
        {"@graph": [{"estleg:summary": "Kunstlik valgustus peab olema piisav."}]}
    )
    results = classify_text(text)
    assert all(code != "3221" for code, *_ in results), (
        f"culture (3221) must not fire on the 'kunstlik' substring; got {results}"
    )


def test_civil_law_assigned_with_two_distinct_keywords():
    """With a genuine civil-law context ('leping' + 'kahju') the domain still
    clears the bumped distinct-keyword gate (#275)."""
    from classify_eurovoc import classify_text, extract_text_from_law

    text = extract_text_from_law({"@graph": [{
        "estleg:summary": "Lepingu rikkumisega tekitatud kahju tuleb hüvitada.",
    }]})
    results = classify_text(text)
    assert any(code == "2431" for code, *_ in results), (
        f"civil-law (2431) should be assigned on 'leping'+'kahju'; got {results}"
    )


def test_new_short_stem_overrides_require_two_distinct_keywords():
    """The #275 short-stem domains are all gated at >=2 distinct keywords and
    every one of them has at least two keywords (so the gate is reachable)."""
    import classify_eurovoc

    for code in ("2431", "3221", "5216", "5611", "4421", "6411", "3611"):
        assert classify_eurovoc.MIN_DISTINCT_KEYWORDS_OVERRIDES[code] == 2
        keyword_count = len(classify_eurovoc.EUROVOC_DOMAINS[code][3])
        assert keyword_count >= 2, f"{code} has only {keyword_count} keyword(s)"


def test_emit_sample_writes_well_formed_file(tmp_path, monkeypatch):
    """The --emit-sample flag produces a well-formed JSON sample of
    (act, assigned_subjects, matched_keywords) tuples and the report
    advertises the precision gates."""
    import classify_eurovoc

    # Re-point the module's KRR_DIR at an isolated tmp tree containing a
    # single classifiable act, so main() does not touch the real corpus.
    krr = tmp_path / "krr_outputs"
    krr.mkdir()
    act_peep = krr / "sample_act_peep.json"
    act_peep.write_text(json.dumps({
        "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                     "owl": "http://www.w3.org/2002/07/owl#",
                     "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
                     "dcterms": "http://purl.org/dc/terms/"},
        "@graph": [
            {"@id": "estleg:Sample_1_Map_2026",
             "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
             "rdfs:label": {"@value": "Autoriõiguse seadus",
                            "@language": "et"}},
            {"@id": "estleg:Sample_1_Par_1",
             "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
             "estleg:summary": {
                 "@value": ("Arst suunab patsiendi haiglasse ravimite "
                            "saamiseks ja teos on kaitstud autoriõigusega."),
                 "@language": "et"}},
        ],
    }), encoding="utf-8")

    monkeypatch.setattr(classify_eurovoc, "KRR_DIR", krr)
    monkeypatch.setattr(
        classify_eurovoc, "iter_peep_files", lambda *a, **k: [act_peep]
    )
    # Keep the coverage report out of the repo tree.
    monkeypatch.setattr(classify_eurovoc, "write_coverage_report",
                        lambda *a, **k: None)

    classify_eurovoc.main(["--emit-sample", "5", "--sample-seed", "1"])

    sample_path = krr / "reports" / "eurovoc_classification_sample.json"
    assert sample_path.exists()
    doc = json.loads(sample_path.read_text(encoding="utf-8"))
    assert doc["classification_level"] == "act"
    assert doc["sample_size"] == doc["population_size"] == 1
    assert doc["random_seed"] == 1
    (row,) = doc["samples"]
    assert row["act"] == "estleg:Sample_1_Map_2026"
    assert isinstance(row["assigned_subjects"], list)
    assert all("eurovoc_uri" in s and "code" in s for s in row["assigned_subjects"])
    assert isinstance(row["matched_keywords"], dict)
    for s in row["assigned_subjects"]:
        assert s["code"] in row["matched_keywords"]

    # The classification report now advertises the sample tooling and gates.
    report = json.loads((krr / "eurovoc_classification.json").read_text("utf-8"))
    qe = report["quality_evaluation"]
    assert qe["status"] == "tooling_available"
    assert qe["sample_emitted"]
    assert "precision_gates" in qe
    assert qe["precision_gates"]["min_distinct_keywords_default"] == 1
    assert "5631" in qe["precision_gates"]["min_distinct_keywords_overrides"]
    # domain_statistics covers every defined domain and carries thresholds.
    assert len(report["domain_statistics"]) == report["eurovoc_domains_defined"]
    for stat in report["domain_statistics"]:
        assert "min_distinct_keywords" in stat
        assert "min_total_hits" in stat
        assert "keyword_count" in stat

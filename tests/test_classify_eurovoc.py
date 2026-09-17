"""Discovery and output-path tests for classify_eurovoc."""
from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = (Path(__file__).parent / "fixtures" / "kov_layer2a"
           / "sample_kov_act.json")


def _read_act_metadata(path: Path):
    """The helper under test: pull title/source from a peep file's
    act-typed node without consulting INDEX.json."""
    from estleg.classify_eurovoc import read_act_metadata_from_peep
    return read_act_metadata_from_peep(path)


class TestReadActMetadataFromPeep:
    def test_reads_title_from_kov_act(self):
        meta = _read_act_metadata(FIXTURE)
        assert meta["title"] == "Test KOV act"
        assert meta["source"] == "Test KOV act"
        assert meta["@id"] == "estleg:Reg_9999_Map"

    def test_returns_none_for_provision_only_file(self, tmp_path):
        # A file with no act-typed node should return None, not crash.
        bad = tmp_path / "no_act.json"
        bad.write_text(json.dumps({
            "@context": {"estleg": "https://w3id.org/estleg/",
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
            "@context": {"estleg": "https://w3id.org/estleg/",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Municipalities_Map",
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
        from estleg.classify_eurovoc import PeepMetadataParseError

        bad = tmp_path / "bad_peep.json"
        bad.write_text("{not json", encoding="utf-8")

        try:
            _read_act_metadata(bad)
        except PeepMetadataParseError as exc:
            assert "bad_peep.json" in str(exc)
        else:
            raise AssertionError("expected PeepMetadataParseError")


def test_extract_text_normalizes_to_nfc_casefold():
    from estleg.classify_eurovoc import extract_text_from_law

    text = extract_text_from_law({"@graph": [{"estleg:summary": "ÕIGUS TÖÖ"}]})

    assert text == "õigus töö"


def test_classify_text_normalises_regex_pattern(monkeypatch):
    """Regression for Finding 5: ``r:`` regex patterns must be NFC +
    casefold normalised before being matched, so that authors can write
    them with uppercase letters and decomposed diacritics and still get
    a match against the (already normalised) corpus."""
    from estleg import classify_eurovoc

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
    from estleg.classify_eurovoc import extract_text_from_law

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
    from estleg.classify_eurovoc import read_act_metadata_from_peep

    peep = tmp_path / "act_peep.json"
    peep.write_text(json.dumps({
        "@context": {"estleg": "https://w3id.org/estleg/",
                     "owl": "http://www.w3.org/2002/07/owl#"},
        "@graph": [
            {"@id": "estleg:Act_1_Map",
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
    """One occurrence of a short stem in a risky domain (health care, 'arst')
    must NOT assign that domain — the distinct-keyword gate (>=2) blocks it."""
    from estleg.classify_eurovoc import classify_text, extract_text_from_law

    text = extract_text_from_law(
        {"@graph": [{"estleg:summary": "Asutuse juures tootab arst."}]}
    )
    results = classify_text(text)
    assert all(code != "5899" for code, *_ in results), (
        f"health care (5899) should not be assigned on a single 'arst' hit; got {results}"
    )


def test_risky_domain_two_distinct_hits_gets_subject():
    """Two distinct keyword hits in the same risky domain clear the gate."""
    from estleg.classify_eurovoc import classify_text, extract_text_from_law

    text = extract_text_from_law({"@graph": [{
        "estleg:summary": "Arst suunab patsiendi haiglasse ravimite saamiseks.",
    }]})
    results = classify_text(text)
    assert any(code == "5899" for code, *_ in results), (
        f"health care (5899) should be assigned with 'arst'+'haigla'+'ravim'; got {results}"
    )
    # The matched_keywords payload is exposed for review tooling.
    health = next(r for r in results if r[0] == "5899")
    assert len(health) == 6
    matched_keywords = health[5]
    assert "arst" in matched_keywords
    assert "haigla" in matched_keywords


def test_distinctive_domain_single_hit_still_assigned():
    """A domain made of long/distinctive keywords keeps the >=1 default —
    one hit on 'autoriõigus' assigns intellectual-property (2817)."""
    from estleg.classify_eurovoc import classify_text, extract_text_from_law

    text = extract_text_from_law(
        {"@graph": [{"estleg:summary": "Teos on kaitstud autoriõigusega."}]}
    )
    results = classify_text(text)
    assert any(code == "2817" for code, *_ in results), (
        f"intellectual-property (2817) should be assigned on a single distinctive hit; got {results}"
    )


def test_advertising_single_keyword_domain_stays_at_one():
    """Advertising (2862) has a single keyword ('reklaam') — it must stay at
    the >=1 default, otherwise it could never be assigned."""
    from estleg import classify_eurovoc

    assert classify_eurovoc.MIN_DISTINCT_KEYWORDS_OVERRIDES.get("2862", 1) == 1
    text = classify_eurovoc.extract_text_from_law(
        {"@graph": [{"estleg:summary": "Reklaam peab olema selgelt eristatav."}]}
    )
    results = classify_eurovoc.classify_text(text)
    assert any(code == "2862" for code, *_ in results)


# ---------------------------------------------------------------------------
# Regression for #275: short substring-prone stems (nõue, kunst, vesi, ...)
# must not assign their domain on a single mid-word substring hit. The
# distinct-keyword gate for these domains is now >=2.
# ---------------------------------------------------------------------------

def test_nouetele_substring_does_not_assign_civil_law():
    """'nõue' (civil-law 523) matches as a substring inside 'nõuetele'/
    'nõuetekohastele' ("requirements"). A technical-requirements act that
    mentions no other civil-law term must NOT be tagged civil-law (#275)."""
    from estleg.classify_eurovoc import classify_text, extract_text_from_law

    text = extract_text_from_law({"@graph": [{
        "estleg:summary": "Toode peab vastama nõuetekohastele nõuetele.",
    }]})
    results = classify_text(text)
    assert all(code != "523" for code, *_ in results), (
        f"civil-law (523) must not fire on the 'nõuetele' substring; got {results}"
    )


def test_kunstlik_substring_does_not_assign_culture():
    """'kunst' (culture 317) matches inside 'kunstlik' ("artificial"). A lone
    such substring hit must not assign culture (#275)."""
    from estleg.classify_eurovoc import classify_text, extract_text_from_law

    text = extract_text_from_law(
        {"@graph": [{"estleg:summary": "Kunstlik valgustus peab olema piisav."}]}
    )
    results = classify_text(text)
    assert all(code != "317" for code, *_ in results), (
        f"culture (317) must not fire on the 'kunstlik' substring; got {results}"
    )


def test_civil_law_assigned_with_two_distinct_keywords():
    """With a genuine civil-law context ('leping' + 'kahju') the domain still
    clears the bumped distinct-keyword gate (#275)."""
    from estleg.classify_eurovoc import classify_text, extract_text_from_law

    text = extract_text_from_law({"@graph": [{
        "estleg:summary": "Lepingu rikkumisega tekitatud kahju tuleb hüvitada.",
    }]})
    results = classify_text(text)
    assert any(code == "523" for code, *_ in results), (
        f"civil-law (523) should be assigned on 'leping'+'kahju'; got {results}"
    )


def test_new_short_stem_overrides_require_two_distinct_keywords():
    """The #275 short-stem domains are all gated at >=2 distinct keywords and
    every one of them has at least two keywords (so the gate is reachable).
    Keys are the post-#421 verified descriptor ids (civil law 523, culture
    317, customs 502, environmental protection 2825, maritime transport 4522,
    construction policy 2475, social security 4050)."""
    from estleg import classify_eurovoc

    for code in ("523", "317", "502", "2825", "4522", "2475", "4050"):
        assert classify_eurovoc.MIN_DISTINCT_KEYWORDS_OVERRIDES[code] == 2
        keyword_count = len(classify_eurovoc.EUROVOC_DOMAINS[code][3])
        assert keyword_count >= 2, f"{code} has only {keyword_count} keyword(s)"


def test_emit_sample_writes_well_formed_file(tmp_path, monkeypatch):
    """The --emit-sample flag produces a well-formed JSON sample of
    (act, assigned_subjects, matched_keywords) tuples and the report
    advertises the precision gates."""
    from estleg import classify_eurovoc

    # Re-point the module's KRR_DIR at an isolated tmp tree containing a
    # single classifiable act, so main() does not touch the real corpus.
    krr = tmp_path / "krr_outputs"
    krr.mkdir()
    act_peep = krr / "sample_act_peep.json"
    act_peep.write_text(json.dumps({
        "@context": {"estleg": "https://w3id.org/estleg/",
                     "owl": "http://www.w3.org/2002/07/owl#",
                     "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
                     "dcterms": "http://purl.org/dc/terms/"},
        "@graph": [
            {"@id": "estleg:Sample_1_Map",
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
    # #295: tracked artifact must carry the pinned date, not wall-clock.
    assert doc["generated"] == classify_eurovoc.BUILD_EVALUATION_DATE
    assert doc["classification_level"] == "act"
    assert doc["sample_size"] == doc["population_size"] == 1
    assert doc["random_seed"] == 1
    (row,) = doc["samples"]
    assert row["act"] == "estleg:Sample_1_Map"
    assert isinstance(row["assigned_subjects"], list)
    assert all("eurovoc_uri" in s and "code" in s for s in row["assigned_subjects"])
    assert isinstance(row["matched_keywords"], dict)
    for s in row["assigned_subjects"]:
        assert s["code"] in row["matched_keywords"]

    # The classification report now advertises the sample tooling and gates.
    report = json.loads((krr / "reports" / "eurovoc_classification.json").read_text("utf-8"))
    qe = report["quality_evaluation"]
    assert qe["status"] == "tooling_available"
    assert qe["sample_emitted"]
    assert "precision_gates" in qe
    assert qe["precision_gates"]["min_distinct_keywords_default"] == 1
    assert "5899" in qe["precision_gates"]["min_distinct_keywords_overrides"]
    # domain_statistics covers every defined domain and carries thresholds.
    assert len(report["domain_statistics"]) == report["eurovoc_domains_defined"]
    for stat in report["domain_statistics"]:
        assert "min_distinct_keywords" in stat
        assert "min_total_hits" in stat
        assert "keyword_count" in stat


# ---------------------------------------------------------------------------
# Regression for #394 item 2: unclassified acts were counted as both
# processed and skipped, so files_processed + files_skipped exceeded
# input_files_total. Unclassified files were processed (no EuroVoc
# output). JSON / no-act-node failures remain skipped.
# ---------------------------------------------------------------------------


def _write_act_peep(path: Path, *, act_id: str, title: str, summary: str) -> Path:
    path.write_text(json.dumps({
        "@context": {"estleg": "https://w3id.org/estleg/",
                     "owl": "http://www.w3.org/2002/07/owl#",
                     "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
                     "dcterms": "http://purl.org/dc/terms/"},
        "@graph": [
            {"@id": act_id,
             "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
             "rdfs:label": {"@value": title, "@language": "et"}},
            {"@id": act_id.replace("_Map", "_Par_1"),
             "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
             "estleg:summary": {"@value": summary, "@language": "et"}},
        ],
    }), encoding="utf-8")
    return path


def test_unclassified_files_are_processed_not_skipped(tmp_path, monkeypatch):
    """#394 item 2: unclassified acts count as processed, not skipped."""
    from estleg import classify_eurovoc

    krr = tmp_path / "krr_outputs"
    krr.mkdir()

    classified = _write_act_peep(
        krr / "classified_peep.json",
        act_id="estleg:Classified_1_Map",
        title="Autoriõiguse seadus",
        summary=(
            "Arst suunab patsiendi haiglasse ravimite saamiseks "
            "ja teos on kaitstud autoriõigusega."
        ),
    )
    unclassified = _write_act_peep(
        krr / "unclassified_peep.json",
        act_id="estleg:Bland_1_Map",
        title="Zzzq",
        summary="Zzzq.",
    )
    no_act = krr / "provision_only_peep.json"
    no_act.write_text(json.dumps({
        "@context": {"estleg": "https://w3id.org/estleg/",
                     "owl": "http://www.w3.org/2002/07/owl#"},
        "@graph": [
            {"@id": "estleg:LooseProvision",
             "@type": ["owl:NamedIndividual"],
             "rdfs:label": "stray"},
        ],
    }), encoding="utf-8")
    bad_json = krr / "bad_peep.json"
    bad_json.write_text("{not json", encoding="utf-8")

    peeps = [classified, unclassified, no_act, bad_json]
    captured: dict = {}

    monkeypatch.setattr(classify_eurovoc, "KRR_DIR", krr)
    monkeypatch.setattr(classify_eurovoc, "iter_peep_files", lambda *a, **k: peeps)

    def _capture_coverage(report, path):
        captured["report"] = report
        captured["path"] = path

    monkeypatch.setattr(classify_eurovoc, "write_coverage_report",
                        _capture_coverage)

    classify_eurovoc.main([])

    cov = captured["report"]
    assert cov.input_files_total == 4
    assert cov.files_processed == 2
    assert cov.files_skipped == 2
    assert cov.files_processed + cov.files_skipped == cov.input_files_total
    assert cov.files_with_output == 1
    assert "unclassified" not in cov.skip_reasons
    assert cov.skip_reasons.get("no_act_node") == 1
    assert cov.skip_reasons.get("parse_error") == 1

    report = json.loads(
        (krr / "reports" / "eurovoc_classification.json").read_text(encoding="utf-8")
    )
    assert report["total_classified"] == 1
    assert report["total_unclassified"] == 1
    assert report["files_with_no_output"] == 1
    assert report["files_with_no_output"] <= report["total_laws_processed"]


# ---------------------------------------------------------------------------
# Regression for #331 + #360: the international-affairs gate (descriptor 3474,
# pre-#421 code "0411") is raised to >=2 distinct keywords so a lone
# context-ambiguous term no longer tags a domestic act. #331 covers
# 'protokoll' (matches domestic 'protokollitakse', meeting minutes / court
# transcript); #360 covers 'rahvusvaheli' (234 domestic functional laws that
# merely cite an international standard once). Both vectors are reconciled
# into the single >=2 gate.
# ---------------------------------------------------------------------------


def test_protokoll_substring_does_not_assign_international_affairs():
    """A domestic procedural act whose only international-affairs signal is
    'protokoll' inside 'protokollitakse' ("is recorded in minutes") must NOT
    be tagged 3474 (#331)."""
    from estleg.classify_eurovoc import classify_text, extract_text_from_law

    text = extract_text_from_law({"@graph": [{
        "estleg:summary": "Menetlustoiming protokollitakse ja allkirjastatakse.",
    }]})
    results = classify_text(text)
    assert all(code != "3474" for code, *_ in results), (
        f"international-affairs (3474) must not fire on a lone 'protokoll' "
        f"substring; got {results}"
    )


def test_lone_rahvusvaheli_does_not_assign_international_affairs():
    """A purely domestic law that mentions an international standard once
    ('rahvusvaheline') but carries no second international-affairs keyword
    must NOT be tagged 3474 (#360)."""
    from estleg.classify_eurovoc import classify_text, extract_text_from_law

    text = extract_text_from_law({"@graph": [{
        "estleg:summary": (
            "Toetuse arvestamisel kohaldatakse rahvusvahelist arvestusstandardit."
        ),
    }]})
    results = classify_text(text)
    assert all(code != "3474" for code, *_ in results), (
        f"international-affairs (3474) must not fire on a lone 'rahvusvaheli' "
        f"hit; got {results}"
    )


def test_international_affairs_assigned_with_two_distinct_keywords():
    """A genuine international-affairs act ('rahvusvaheline' + 'konventsioon')
    still clears the raised >=2 distinct-keyword gate (#331/#360)."""
    from estleg.classify_eurovoc import classify_text, extract_text_from_law

    text = extract_text_from_law({"@graph": [{
        "estleg:summary": (
            "Rahvusvahelise konventsiooni ratifitseerimise seadus."
        ),
    }]})
    results = classify_text(text)
    assert any(code == "3474" for code, *_ in results), (
        f"international-affairs (3474) should be assigned on "
        f"'rahvusvaheli'+'konventsioon'+'ratifitseerimise'; got {results}"
    )


# ---------------------------------------------------------------------------
# Regression for #383: 'ratifitseerimise' is an international-affairs (3474)
# keyword (treaty-ratification recall) and 'topeltmaksustamise' is a
# tax-system (1021) keyword (double-taxation treaties).
# ---------------------------------------------------------------------------


def test_ratifitseerimise_is_an_international_affairs_keyword():
    """'ratifitseerimise' must be in the 3474 keyword list so ratification acts
    that mention it plus one more international-affairs term are recalled
    (#383)."""
    from estleg import classify_eurovoc

    keywords = classify_eurovoc.EUROVOC_DOMAINS["3474"][3]
    assert "ratifitseerimise" in keywords
    # The 3474 gate is >=2 but the list now has 5 keywords, so it is reachable.
    assert len(keywords) >= classify_eurovoc.MIN_DISTINCT_KEYWORDS_OVERRIDES["3474"]


def test_topeltmaksustamise_is_a_taxation_keyword():
    """'topeltmaksustamise' must be in the 1021 (tax system) keyword list
    (#383). 1021 is gated at >=2 distinct keywords; a double-taxation treaty
    co-mentions a 'maks'-family stem, so the gate stays reachable."""
    from estleg import classify_eurovoc

    keywords = classify_eurovoc.EUROVOC_DOMAINS["1021"][3]
    assert "topeltmaksustamise" in keywords

    # A double-taxation-avoidance act co-mentions 'topeltmaksustamise' and the
    # 'maks'/'tulumaks' stems, clearing the >=2 gate.
    text = classify_eurovoc.extract_text_from_law({"@graph": [{
        "estleg:summary": (
            "Tulumaksuga topeltmaksustamise vältimise lepingu "
            "ratifitseerimise seadus."
        ),
    }]})
    results = classify_eurovoc.classify_text(text)
    assert any(code == "1021" for code, *_ in results), (
        f"tax system (1021) should be assigned on "
        f"'topeltmaksustamise'+'maks'/'tulumaks'; got {results}"
    )


# ---------------------------------------------------------------------------
# Regression for #360: the consumer-protection gate (descriptor 2836,
# pre-#421 code "2806") is raised to >=2 so district-heating ('kaugkütt')
# ordinances whose only consumer-protection signal is 'tarbija' (meaning
# "heat-subscriber", not consumer-protection) are no longer tagged.
# ---------------------------------------------------------------------------


def test_lone_tarbija_does_not_assign_consumer_protection():
    """A district-heating ordinance whose only consumer-protection signal is
    'tarbija' (heat-subscriber) must NOT be tagged consumer-protection
    (#360)."""
    from estleg.classify_eurovoc import classify_text, extract_text_from_law

    text = extract_text_from_law({"@graph": [{
        "estleg:summary": (
            "Ala küla kaugküttepiirkonna kehtestamine ja tarbija liitumine."
        ),
    }]})
    results = classify_text(text)
    assert all(code != "2836" for code, *_ in results), (
        f"consumer-protection (2836) must not fire on a lone 'tarbija' hit in "
        f"a district-heating ordinance; got {results}"
    )


def test_consumer_protection_assigned_with_two_distinct_keywords():
    """A genuine consumer-protection act ('tarbija' + 'garantii') still clears
    the raised >=2 distinct-keyword gate (#360)."""
    from estleg.classify_eurovoc import classify_text, extract_text_from_law

    text = extract_text_from_law({"@graph": [{
        "estleg:summary": (
            "Tarbija võib esitada nõude müüjale toote garantii alusel."
        ),
    }]})
    results = classify_text(text)
    assert any(code == "2836" for code, *_ in results), (
        f"consumer-protection (2836) should be assigned on "
        f"'tarbija'+'garantii'; got {results}"
    )


def test_international_affairs_and_consumer_protection_gates_reachable():
    """The reconciled #331/#360 change gates international-affairs (3474) and
    consumer-protection (2836) at >=2 distinct keywords, and both keyword
    lists are long enough to reach the gate."""
    from estleg import classify_eurovoc

    for code in ("3474", "2836"):
        assert classify_eurovoc.MIN_DISTINCT_KEYWORDS_OVERRIDES[code] == 2
        keyword_count = len(classify_eurovoc.EUROVOC_DOMAINS[code][3])
        assert keyword_count >= 2, f"{code} has only {keyword_count} keyword(s)"


# ---------------------------------------------------------------------------
# Regression for #421 (supersedes the #392 label-only fix): every
# EUROVOC_DOMAINS key is a verified real EuroVoc descriptor id and the
# in-table labels are the official et/en prefLabels recorded in the audit
# trail data/eurovoc_domain_mapping.json (verified against the Publications
# Office on 2026-06-10). Keyword lists were carried over unchanged from the
# pre-#421 table, so matching behaviour is untouched.
# ---------------------------------------------------------------------------


def test_eurovoc_table_matches_verified_mapping_file():
    """EUROVOC_DOMAINS keys/labels agree 1:1 with the #421 audit trail in
    data/eurovoc_domain_mapping.json (verified official prefLabels)."""
    from estleg import classify_eurovoc

    mapping_path = REPO_ROOT / "data" / "eurovoc_domain_mapping.json"
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))

    by_new_id = {m["newId"]: m for m in mapping}
    assert len(by_new_id) == len(mapping), "duplicate newId in mapping file"
    assert set(by_new_id) == set(classify_eurovoc.EUROVOC_DOMAINS), (
        "EUROVOC_DOMAINS keys diverge from the verified mapping file"
    )
    for code, (_slug, label_et, label_en, _kws) in (
        classify_eurovoc.EUROVOC_DOMAINS.items()
    ):
        entry = by_new_id[code]
        assert label_et == entry["labelEt"], (
            f"{code} label_et {label_et!r} != verified {entry['labelEt']!r}"
        )
        assert label_en == entry["labelEn"], (
            f"{code} label_en {label_en!r} != verified {entry['labelEn']!r}"
        )


def test_eurovoc_ids_are_numeric_for_shacl():
    """All descriptor ids must be purely numeric so the minted
    http://eurovoc.europa.eu/{id} IRIs keep satisfying the SHACL pattern
    ^http://eurovoc\\.europa\\.eu/[0-9]+$ on dcterms:subject (#421)."""
    from estleg import classify_eurovoc

    for code in classify_eurovoc.EUROVOC_DOMAINS:
        assert code.isdigit(), f"non-numeric EuroVoc id {code!r}"
    # The threshold-override keys must reference defined domains.
    for code in classify_eurovoc.MIN_HITS_OVERRIDES:
        assert code in classify_eurovoc.EUROVOC_DOMAINS, (
            f"MIN_HITS_OVERRIDES key {code!r} is not a defined domain"
        )
    for code in classify_eurovoc.MIN_DISTINCT_KEYWORDS_OVERRIDES:
        assert code in classify_eurovoc.EUROVOC_DOMAINS, (
            f"MIN_DISTINCT_KEYWORDS_OVERRIDES key {code!r} is not a defined domain"
        )


def test_eurovoc_descriptors_match_official_labels():
    """Spot-check (#392 lineage, re-keyed by #421): maritime transport,
    social security and fundamental rights carry the official descriptor
    ids and prefLabels."""
    from estleg import classify_eurovoc

    expected = {
        "4522": ("maritime-transport", "meretransport", "maritime transport"),
        "4050": ("social-security", "sotsiaalkindlustus", "social security"),
        "538": ("fundamental-rights", "põhiõigused", "fundamental rights"),
    }
    for code, (slug, label_et, label_en) in expected.items():
        actual_slug, actual_et, actual_en, _kws = classify_eurovoc.EUROVOC_DOMAINS[code]
        assert actual_slug == slug, f"{code} slug: {actual_slug!r} != {slug!r}"
        assert actual_et == label_et, f"{code} label_et: {actual_et!r} != {label_et!r}"
        assert actual_en == label_en, f"{code} label_en: {actual_en!r} != {label_en!r}"


def test_eurovoc_descriptor_fix_preserves_keywords():
    """#421 remapped identifiers/labels only — the keyword lists (and
    therefore the matching behaviour) are carried over unchanged (shown here
    for the #392-lineage trio under their new ids)."""
    from estleg import classify_eurovoc

    assert classify_eurovoc.EUROVOC_DOMAINS["4522"][3] == [
        "meresõit", "laev", "sadam", "merendus",
    ]
    assert classify_eurovoc.EUROVOC_DOMAINS["4050"][3] == [
        "sotsiaal", "pension", "toetus", "hüvitis", "ravikindlust",
        "töötuskindlust", "puue", "hooldus",
    ]
    assert classify_eurovoc.EUROVOC_DOMAINS["538"][3] == [
        "inimõig", "põhiõig", "vabadus", "võrdsus", "diskrimineeri",
        "soolise võrdõiguslikkuse",
    ]

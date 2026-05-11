import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import generate_similarity_index as similarity


def write_doc(path: Path, act_type: str = "estleg:Law") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "@graph": [
                    {
                        "@id": "estleg:Act_1",
                        "@type": ["owl:Ontology", "estleg:Act", act_type],
                    },
                    {
                        "@id": "estleg:Act_1_Par_1",
                        "@type": ["owl:NamedIndividual", "estleg:LegalProvision_Act1"],
                        "rdfs:label": "§ 1. Maakasutuse kontroll ja teavitamine",
                        "estleg:sourceAct": "Test act",
                        "estleg:summary": (
                            "Maakasutuse kontroll teavitamine ehitusluba "
                            "planeering järelevalve"
                        ),
                    },
                ]
            }
        ),
        encoding="utf-8",
    )


def write_fresh_generator_shape_doc(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "@graph": [
                    {
                        "@id": "estleg:Act_1",
                        "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
                    },
                    {
                        "@id": "estleg:Act_1_Par_1",
                        "@type": ["owl:NamedIndividual", "estleg:LegalProvision_Act1"],
                        "rdfs:label": {
                            "@value": "§ 1. Maakasutuse kontroll ja teavitamine",
                            "@language": "et",
                        },
                        "estleg:sourceAct": {"@value": "Test act", "@language": "et"},
                        "estleg:summary": {
                            "@value": (
                                "Maakasutuse kontroll teavitamine ehitusluba "
                                "planeering järelevalve"
                            ),
                            "@language": "et",
                        },
                    },
                ]
            }
        ),
        encoding="utf-8",
    )


def test_state_regulation_file_contributes_similarity_candidates(tmp_path, monkeypatch):
    krr = tmp_path / "krr_outputs"
    path = krr / "regulations" / "riik" / "state_peep.json"
    write_doc(path, "estleg:NationalRegulation")
    monkeypatch.setattr(similarity, "KRR_DIR", krr)

    provisions, act_type, excluded = similarity.extract_provisions_from_file(path)

    assert act_type == "state_regulation"
    assert len(provisions) == 1
    assert provisions[0]["file"] == "regulations/riik/state_peep.json"
    assert provisions[0]["act_type"] == "state_regulation"
    assert excluded == 0


def test_non_act_file_is_not_a_similarity_candidate(tmp_path):
    path = tmp_path / "krr_outputs" / "concepts" / "concept_peep.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"@graph": [{"@id": "estleg:Concept", "@type": ["estleg:LegalConcept"]}]}),
        encoding="utf-8",
    )

    provisions, act_type, excluded = similarity.extract_provisions_from_file(path)

    assert provisions == []
    assert act_type is None
    assert excluded == 0


def test_fresh_generator_language_maps_contribute_similarity_candidates(tmp_path):
    path = tmp_path / "krr_outputs" / "law_peep.json"
    write_fresh_generator_shape_doc(path)

    provisions, act_type, excluded = similarity.extract_provisions_from_file(path)

    assert act_type == "law"
    assert len(provisions) == 1
    assert provisions[0]["label"] == "§ 1. Maakasutuse kontroll ja teavitamine"
    assert provisions[0]["source_act"] == "Test act"
    assert "maakasutuse" in provisions[0]["keywords"]
    assert excluded == 0


def test_label_keywords_do_not_make_similarity_candidate(tmp_path):
    path = tmp_path / "krr_outputs" / "law_peep.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "@graph": [
                    {
                        "@id": "estleg:Act_1",
                        "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
                    },
                    {
                        "@id": "estleg:Act_1_Par_1",
                        "@type": ["owl:NamedIndividual", "estleg:LegalProvision_Act1"],
                        "rdfs:label": "§ 1. Maakasutuse kontroll ja teavitamine",
                        "estleg:summary": "Maakasutus",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    provisions, act_type, excluded = similarity.extract_provisions_from_file(path)

    assert act_type == "law"
    assert provisions == []
    assert excluded == 1


def test_keyword_floor_exclusions_are_reported_and_logged(tmp_path, monkeypatch, capsys):
    """Provisions filtered by MIN_SHARED_KEYWORDS must be reported, not dropped silently."""
    krr = tmp_path / "krr_outputs"
    krr.mkdir(parents=True)
    short_summary_path = krr / "short_summary_peep.json"
    short_summary_path.write_text(
        json.dumps(
            {
                "@graph": [
                    {
                        "@id": "estleg:Act_1",
                        "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
                    },
                    {
                        "@id": "estleg:Act_1_Par_1",
                        "@type": [
                            "owl:NamedIndividual",
                            "estleg:LegalProvision_Act1",
                        ],
                        "rdfs:label": "§ 1. Maakasutus",
                        "estleg:sourceAct": "Test act",
                        "estleg:summary": "Maakasutus",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(similarity, "KRR_DIR", krr)
    monkeypatch.setattr(similarity, "iter_peep_files", lambda include_kov=False: [short_summary_path])
    monkeypatch.setattr(
        similarity,
        "save_json",
        lambda path, payload: Path(path).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        ),
    )

    similarity.main()

    report_path = krr / "similarity_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert "excluded_provisions" in report
    assert report["excluded_provisions"]["law"] == 1

    captured = capsys.readouterr()
    assert (
        f"Excluded due to keyword-floor (<{similarity.MIN_SHARED_KEYWORDS} keywords required):"
        in captured.out
    )
    assert "law: 1 provisions" in captured.out


# ---------------------------------------------------------------------------
# Helpers for the full-pipeline tests below
# ---------------------------------------------------------------------------

def _provision_node(node_id: str, label: str, summary: str) -> dict:
    return {
        "@id": node_id,
        "@type": ["owl:NamedIndividual", "estleg:LegalProvision_X"],
        "rdfs:label": label,
        "estleg:sourceAct": label,
        "estleg:summary": summary,
    }


def _act_doc(act_id: str, provisions: list[dict]) -> dict:
    return {
        "@graph": [
            {"@id": act_id, "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"]},
            *provisions,
        ]
    }


def _wire_pipeline(
    monkeypatch, krr: Path, peep_files: list[Path], *, generic_cap: float | None = None
) -> None:
    """Point the generator at ``krr`` / ``peep_files`` with a dir-creating save.

    ``generic_cap`` overrides ``GENERIC_DOC_FREQUENCY_CAP``: pass ``1.0`` to
    disable the corpus-generic-token strip in tiny fixtures where every
    shared token would otherwise exceed the cap (a non-issue at real
    corpus scale, but unavoidable with 2-4 provisions).
    """
    reports = krr / "reports"
    monkeypatch.setattr(similarity, "KRR_DIR", krr)
    monkeypatch.setattr(similarity, "REPORTS_DIR", reports)
    if generic_cap is not None:
        monkeypatch.setattr(similarity, "GENERIC_DOC_FREQUENCY_CAP", generic_cap)
    monkeypatch.setattr(
        similarity, "iter_peep_files", lambda include_kov=False: list(peep_files)
    )

    def _save(path, payload):
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    monkeypatch.setattr(similarity, "save_json", _save)


def _make_two_act_corpus(krr: Path, summary_a: str, summary_b: str) -> list[Path]:
    """Two single-provision acts in separate files; callers pick the summaries."""
    krr.mkdir(parents=True, exist_ok=True)
    path_a = krr / "act_a_peep.json"
    path_b = krr / "act_b_peep.json"
    path_a.write_text(
        json.dumps(_act_doc("estleg:ActA", [
            _provision_node("estleg:ActA_Par_1", "Act A", summary_a),
        ])),
        encoding="utf-8",
    )
    path_b.write_text(
        json.dumps(_act_doc("estleg:ActB", [
            _provision_node("estleg:ActB_Par_1", "Act B", summary_b),
        ])),
        encoding="utf-8",
    )
    return [path_a, path_b]


# ---------------------------------------------------------------------------
# Generic-but-not-boilerplate token filtering
# ---------------------------------------------------------------------------

def test_generic_legal_connectives_are_stopwords():
    """Tokens like 'kohaldama'/'sätestama'/'tähendus' never enter a keyword set."""
    kws = similarity.extract_keywords(
        "kohaldama sätestama tähendus alusel korras puhul tulenevalt"
    )
    assert kws == set()


def test_pair_sharing_only_generic_legal_tokens_is_not_a_candidate(tmp_path, monkeypatch):
    """Two provisions whose only overlap is generic legal connectives must not link.

    Corpus-generic cap is disabled here so the negative result is owed
    purely to the static stopword layer (kohaldama / sätestama / tähendus
    / alusel / korras / puhul / tulenevalt) — not to the document-frequency
    strip, which has its own test.
    """
    krr = tmp_path / "krr_outputs"
    # 6 generic shared tokens (all stopwords) + 3 discriminative tokens
    # each side. Without the stopword layer the shared count would be 6 and
    # a pair would form; with it the shared count is 0.
    peep = _make_two_act_corpus(
        krr,
        "Maakasutuse planeering ehitusluba kohaldama sätestama tähendus alusel korras puhul tulenevalt",
        "Kalapüügiluba veekogu kalavaru kohaldama sätestama tähendus alusel korras puhul tulenevalt",
    )
    _wire_pipeline(monkeypatch, krr, peep, generic_cap=1.0)

    similarity.main()

    index = json.loads((krr / "similarity_index.json").read_text(encoding="utf-8"))
    assert index["total_pairs"] == 0
    assert index["pairs"] == []


def test_corpus_generic_keyword_cap_strips_high_frequency_tokens(tmp_path, monkeypatch):
    """A keyword present in >cap fraction of provisions is dropped before scoring."""
    krr = tmp_path / "krr_outputs"
    krr.mkdir(parents=True)
    # Five acts. "ametiasutus halduskorraldus teavitamine" appears in ALL
    # five (100% >> 45% cap) so it is a corpus-generic token and gets
    # stripped. After stripping, acts A and B each have only 1 remaining
    # keyword ("maakasutus"/"ehitusluba" vs "kalavaru"/"veekogu"), well
    # under MIN_SHARED_KEYWORDS, so no pair forms even though they shared
    # 3 tokens pre-strip.
    common = "ametiasutus halduskorraldus teavitamine"
    summaries = {
        "estleg:ActA": ("estleg:ActA_Par_1", f"Maakasutus ehitusluba {common}"),
        "estleg:ActB": ("estleg:ActB_Par_1", f"Kalavaru veekogu {common}"),
        "estleg:ActC": ("estleg:ActC_Par_1", f"Sotsiaaltoetus pension {common}"),
        "estleg:ActD": ("estleg:ActD_Par_1", f"Maksukohustus deklaratsioon {common}"),
        "estleg:ActE": ("estleg:ActE_Par_1", f"Liiklusohutus sõiduk {common}"),
    }
    peep = []
    for i, (act_id, (prov_id, summary)) in enumerate(summaries.items()):
        p = krr / f"act_{i}_peep.json"
        p.write_text(
            json.dumps(_act_doc(act_id, [_provision_node(prov_id, act_id, summary)])),
            encoding="utf-8",
        )
        peep.append(p)
    _wire_pipeline(monkeypatch, krr, peep)

    similarity.main()

    index = json.loads((krr / "similarity_index.json").read_text(encoding="utf-8"))
    report = json.loads((krr / "similarity_report.json").read_text(encoding="utf-8"))
    assert index["total_pairs"] == 0
    dropped = set(report["generic_keywords_dropped"])
    assert {"ametiasutus", "halduskorraldus", "teavitamine"} <= dropped
    # The discriminative tokens survive the cap.
    assert "maakasutus" not in dropped
    assert index["algorithm"]["generic_keyword_doc_frequency_cap"] == (
        similarity.GENERIC_DOC_FREQUENCY_CAP
    )


# ---------------------------------------------------------------------------
# Per-link provenance on injected estleg:semanticallySimilarTo nodes
# ---------------------------------------------------------------------------

def test_injected_similarity_links_carry_score_and_status(tmp_path, monkeypatch):
    """Each estleg:semanticallySimilarTo value object gets score + status."""
    krr = tmp_path / "krr_outputs"
    # Identical 5-token discriminative summaries in two different acts =>
    # Jaccard 1.0, a guaranteed candidate pair. Disable the corpus-generic
    # cap: with only 2 provisions every shared token is "100%-frequent".
    shared = "maakasutuse planeering ehitusluba detailplaneering järelevalve"
    peep = _make_two_act_corpus(krr, shared, shared)
    _wire_pipeline(monkeypatch, krr, peep, generic_cap=1.0)

    similarity.main()

    doc_a = json.loads(peep[0].read_text(encoding="utf-8"))
    prov = next(n for n in doc_a["@graph"] if n["@id"] == "estleg:ActA_Par_1")
    links = prov["estleg:semanticallySimilarTo"]
    assert isinstance(links, list) and links
    link = links[0]
    assert link["@id"] == "estleg:ActB_Par_1"
    assert link["estleg:similarityStatus"] == "candidate"
    assert link["estleg:similarityScore"]["@type"] == "xsd:decimal"
    # Jaccard of identical sets is 1.0.
    assert float(link["estleg:similarityScore"]["@value"]) == 1.0
    # The index advertises the link-provenance shape too.
    index = json.loads((krr / "similarity_index.json").read_text(encoding="utf-8"))
    assert index["link_provenance"]["status_literal"] == "candidate"
    assert "estleg:similarityScore" in index["link_provenance"]["shape"]


def test_similarity_link_value_shape():
    """The link-value builder produces the documented minimal shape."""
    value = similarity.similarity_link_value("estleg:Foo_Par_2", 0.333333)
    assert value == {
        "@id": "estleg:Foo_Par_2",
        "estleg:similarityScore": {"@value": "0.333", "@type": "xsd:decimal"},
        "estleg:similarityStatus": "candidate",
    }


# ---------------------------------------------------------------------------
# --emit-sample precision-review tooling
# ---------------------------------------------------------------------------

def test_emit_sample_writes_well_formed_precision_sample(tmp_path, monkeypatch):
    """--emit-sample N writes reports/similarity_sample.json with N tuples."""
    krr = tmp_path / "krr_outputs"
    shared = "maakasutuse planeering ehitusluba detailplaneering järelevalve"
    peep = _make_two_act_corpus(krr, shared, shared)
    _wire_pipeline(monkeypatch, krr, peep, generic_cap=1.0)

    similarity.main(["--emit-sample", "5"])

    sample_path = krr / "reports" / "similarity_sample.json"
    assert sample_path.exists()
    sample = json.loads(sample_path.read_text(encoding="utf-8"))
    assert sample["relation"] == "estleg:semanticallySimilarTo"
    assert sample["relation_semantics"] == "candidate"
    assert sample["sample_seed"] == similarity.SAMPLE_SEED
    assert sample["requested_sample_size"] == 5
    # Only 2 directed pairs available (A->B, B->A) in this tiny corpus, so
    # the sample is clamped to what exists.
    assert sample["sample_size"] == len(sample["samples"]) <= sample["available_pairs"]
    for row in sample["samples"]:
        assert set(row) >= {
            "source_provision", "target_provision", "score",
            "shared_keywords", "reviewed_relevant", "reviewer_note",
        }
        assert row["reviewed_relevant"] is None
        assert isinstance(row["score"], (int, float))
    # The report cross-references the sample.
    report = json.loads((krr / "similarity_report.json").read_text(encoding="utf-8"))
    assert report["precision_sample"]["path"] == "reports/similarity_sample.json"
    assert report["precision_sample"]["size"] == sample["sample_size"]


def test_emit_sample_is_deterministic(tmp_path, monkeypatch):
    """Same corpus + seed => byte-identical sample file across two runs."""
    krr = tmp_path / "krr_outputs"
    # Build a 4-act corpus so there are several candidate pairs to sample
    # from. Disable the corpus-generic cap (tiny fixture: shared tokens
    # would all be ">=45% frequent").
    krr.mkdir(parents=True)
    base_tokens = "maakasutuse planeering ehitusluba detailplaneering järelevalve"
    extra = ["kontroll", "menetlus", "haldusakt", "kaebus", "tähtaeg", "otsus"]
    peep = []
    for i in range(4):
        act_id = f"estleg:Act{i}"
        summary = base_tokens + " " + " ".join(extra[i:i + 2])
        p = krr / f"act_{i}_peep.json"
        p.write_text(
            json.dumps(_act_doc(act_id, [
                _provision_node(f"{act_id}_Par_1", act_id, summary),
            ])),
            encoding="utf-8",
        )
        peep.append(p)
    _wire_pipeline(monkeypatch, krr, peep, generic_cap=1.0)

    similarity.main(["--emit-sample", "3"])
    first = (krr / "reports" / "similarity_sample.json").read_text(encoding="utf-8")
    similarity.main(["--emit-sample", "3"])
    second = (krr / "reports" / "similarity_sample.json").read_text(encoding="utf-8")
    assert first == second
    payload = json.loads(first)
    assert payload["sample_size"] >= 1
    # Sorted by (source, target) for stable diffs.
    keys = [(r["source_provision"], r["target_provision"]) for r in payload["samples"]]
    assert keys == sorted(keys)

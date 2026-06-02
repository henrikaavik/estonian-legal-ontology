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
# Boilerplate provision detection (#367a)
# ---------------------------------------------------------------------------

def test_regulation_entry_into_force_is_boilerplate():
    """'Määrus jõustub …' entry-into-force clauses are boilerplate (#367a).

    Pre-fix BOILERPLATE_PATTERNS covered the LAW form ('käesolev seadus
    jõustub' / 'seadus jõustub') but not the REGULATION form, so identical
    'Määrus jõustub … aastal.' / '… üldises korras.' / '… kolmandal päeval
    …' clauses produced huge Jaccard=1.0 false-similarity clusters.
    """
    for text in (
        "Määrus jõustub 21. novembril 2022. aastal.",
        "Määrus jõustub 2012. aasta 1. jaanuaril.",
        "Määrus jõustub üldises korras.",
        "Määrus jõustub seadusega sätestatud korras.",
        "Määrus jõustub kolmandal päeval pärast Riigi Teatajas avaldamist.",
        "Määrus jõustub kolmandal päeval pärast avalikustamist.",
        "Käesolev määrus jõustub 1. jaanuaril 2003. a.",
    ):
        assert similarity.is_boilerplate(text), text


def test_substantive_maarus_provision_is_not_boilerplate():
    """A substantive court-order provision mentioning 'määrus jõustub' is kept.

    The #367a pattern is anchored to an entry-into-force complement (date /
    aasta / päev / korras / avalikustam / avaldam) so it does NOT suppress
    real provisions whose 'määrus jõustub' is followed by ', kui …' or 'ja
    kuulub täitmisele …' (procedural content, not an entry-into-force date).
    """
    for text in (
        "Kohtuotsus või -määrus jõustub, kui seda ei saa enam vaidlustada "
        "muul viisil kui teistmismenetluses.",
        "Hüvitise suuruse määramise avalduse kohta tehtud määrus jõustub ja "
        "kuulub täitmisele, kui seaduse järgi ei saa.",
        "Määrus jõustub ja kuulub täitmisele selle alaealisele "
        "kättetoimetamisega.",
    ):
        assert not similarity.is_boilerplate(text), text


def test_nature_reserve_administrator_verbatim_is_boilerplate():
    """'<X> valitseja on Keskkonnaamet.' verbatim statutory text is boilerplate.

    Every protected-area regulation repeats the same administrator sentence
    (#367a). Identical across thousands of acts, it produced a degenerate
    Jaccard=1.0 hub (in-degree 293 on a single nature-reserve §3), so it must
    be filtered before keyword extraction.
    """
    for text in (
        "Kaitseala valitseja on Keskkonnaamet.",
        "Püsielupaiga valitseja on Keskkonnaamet.",
        "Looduspargi valitseja on Keskkonnaamet.",
    ):
        assert similarity.is_boilerplate(text), text
    # A 'valitseja' sentence that is NOT the fixed administrator clause stays
    # substantive (carries real topical content).
    assert not similarity.is_boilerplate(
        "Kaitseala valitseja kooskõlastab kavandatava tegevuse ja annab "
        "nõusoleku metsaraieks."
    )


def test_boilerplate_provision_excluded_from_similarity_candidates(tmp_path):
    """A provision whose summary is regulation boilerplate is excluded (#367a)."""
    krr = tmp_path / "krr_outputs"
    path = krr / "regulations" / "riik" / "boiler_peep.json"
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
                        "rdfs:label": "§ 5. Jõustumine",
                        "estleg:sourceAct": "Test act",
                        # 'Määrus jõustub üldises korras.' is caught ONLY by the
                        # new #367a regulation pattern (no Riigi Teataja /
                        # avaldamisele wording the old patterns matched), so this
                        # is a true regression guard for the new pattern.
                        "estleg:summary": "Määrus jõustub üldises korras.",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    provisions, act_type, excluded = similarity.extract_provisions_from_file(path)

    # Boilerplate is dropped before the keyword floor, so it is neither a
    # candidate nor counted as a keyword-floor exclusion. (The file lives
    # under regulations/riik/, so it classifies as a state_regulation.)
    assert act_type == "state_regulation"
    assert provisions == []
    assert excluded == 0


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
# Symmetric provision-level semanticallySimilarTo (#264)
# ---------------------------------------------------------------------------

def _make_n_act_corpus(krr: Path, summaries: list[str]) -> list[Path]:
    """N single-provision acts, each in its own file/law, with given summaries."""
    krr.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for i, summary in enumerate(summaries):
        act_id = f"estleg:Act{i}"
        p = krr / f"act_{i}_peep.json"
        p.write_text(
            json.dumps(_act_doc(act_id, [
                _provision_node(f"{act_id}_Par_1", act_id, summary),
            ])),
            encoding="utf-8",
        )
        paths.append(p)
    return paths


def _similar_targets(path: Path, prov_id: str) -> list[str]:
    """The estleg:semanticallySimilarTo target IRIs of a provision node."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    prov = next(n for n in doc["@graph"] if n["@id"] == prov_id)
    return [link["@id"] for link in prov.get("estleg:semanticallySimilarTo", [])]


def test_similarity_is_symmetric_reciprocal_backlinks(tmp_path, monkeypatch):
    """Two provisions with identical keyword sets each get the reciprocal link.

    Pre-fix the forward-only scan wrote A->B only and left B with no
    estleg:semanticallySimilarTo at all (the upper-triangular sink bug,
    #264). The relation is semantically symmetric, so both files must carry
    the back-link to the other.
    """
    krr = tmp_path / "krr_outputs"
    shared = "maakasutuse planeering ehitusluba detailplaneering järelevalve"
    peep = _make_two_act_corpus(krr, shared, shared)
    _wire_pipeline(monkeypatch, krr, peep, generic_cap=1.0)

    similarity.main(["--no-kov"])

    # Reciprocal: A links to B AND B links back to A.
    assert _similar_targets(peep[0], "estleg:ActA_Par_1") == ["estleg:ActB_Par_1"]
    assert _similar_targets(peep[1], "estleg:ActB_Par_1") == ["estleg:ActA_Par_1"]

    index = json.loads((krr / "similarity_index.json").read_text(encoding="utf-8"))
    # Both directed edges are present in the index, and nothing was capped.
    directed = {(p["source"], p["target"]) for p in index["pairs"]}
    assert directed == {
        ("estleg:ActA_Par_1", "estleg:ActB_Par_1"),
        ("estleg:ActB_Par_1", "estleg:ActA_Par_1"),
    }
    assert index["total_pairs"] == 2
    assert index["pairs_truncated_by_cap"] == 0


def test_cap_applied_per_provision_after_symmetrization(tmp_path, monkeypatch):
    """N identical peers, cap K: each provision keeps min(N,K); drops counted.

    With four identical provisions across four laws every provision has
    N=3 equally-scored peers. With MAX_SIMILAR_PER_PROVISION=K=2 each keeps
    min(N,K)=2 (deterministic (-score, target_id) tie-break -> lowest IRIs),
    so 8 directed edges survive and pairs_truncated_by_cap == 4 (one drop per
    provision). The cap is enforced *per provision after symmetrization*, not
    over a forward half, and the drop is reported (no silent truncation).
    """
    krr = tmp_path / "krr_outputs"
    monkeypatch.setattr(similarity, "MAX_SIMILAR_PER_PROVISION", 2)
    shared = "maakasutuse planeering ehitusluba detailplaneering järelevalve"
    peep = _make_n_act_corpus(krr, [shared] * 4)
    _wire_pipeline(monkeypatch, krr, peep, generic_cap=1.0)

    similarity.main(["--no-kov"])

    # Every provision keeps exactly K=2 reciprocal links.
    for i in range(4):
        targets = _similar_targets(peep[i], f"estleg:Act{i}_Par_1")
        assert len(targets) == 2, targets
        assert all(t != f"estleg:Act{i}_Par_1" for t in targets)
        # Deterministic tie-break keeps the lowest-IRI peers (excluding self).
        expected = sorted(
            f"estleg:Act{j}_Par_1" for j in range(4) if j != i
        )[:2]
        assert sorted(targets) == expected

    index = json.loads((krr / "similarity_index.json").read_text(encoding="utf-8"))
    assert index["total_pairs"] == 8  # 4 provisions * min(3, 2)
    assert index["pairs_truncated_by_cap"] == 4  # 4 provisions * (3 - 2)
    report = json.loads((krr / "similarity_report.json").read_text(encoding="utf-8"))
    assert report["pairs_truncated_by_cap"] == 4


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


# ===========================================================================
# KOV act-level similarity (Layer 3)
# ===========================================================================

# Distinct, content-rich token pools so two acts can be made either highly
# similar (same pool) or dissimilar (different pools) under TF-IDF cosine.
_WASTE_TEXT = (
    "jaatmevedu konteiner pakend prugi sortimine kogumine vedu olmejaatmed "
    "jaatmejaam korraldatud biolagunev pakendijaatmed"
)
_WATER_TEXT = (
    "veevark kanalisatsioon reovesi joogivesi pumpla puhasti liitumine "
    "torustik veemoot heitvesi sademevesi"
)

# Graded-overlap pools (for the intra-bucket symmetry test, #367b). A shared
# 20-token core makes every act in the bucket pairwise-comparable; a small
# shared "bonus" set lifts the hub + its two close peers well above a
# weaker fourth act, so a small KOV_TOP_K forces the hub to drop its weakest
# peer — the case that produced one-directional pairs under the old
# per-source cap.
_SYM_CORE = " ".join(f"core{c}" for c in "abcdefghijklmnopqrst")
_SYM_BONUS = "bonusa bonusb bonusc bonusd bonuse bonusf"
_SYM_HUB = f"{_SYM_CORE} {_SYM_BONUS}"
_SYM_NEAR_A = f"{_SYM_CORE} {_SYM_BONUS} uniqaa"
_SYM_NEAR_B = f"{_SYM_CORE} {_SYM_BONUS} uniqbb"
_SYM_WEAK = (
    f"{_SYM_CORE} tailaa tailbb tailcc taildd tailee tailff "
    "tailgg tailhh tailii tailjj tailkk tailll"
)


def _kov_act_doc(
    reg_id: str,
    title_normalized: str,
    provision_summaries: list[str],
    *,
    issued_under: list[str] | None = None,
    preamble: str = "kohaliku omavalitsuse korralduse seaduse alusel",
) -> dict:
    """Build a KOV peep doc whose act node is an estleg:MunicipalRegulation.

    Mirrors the real Layer-1 shape: the act node carries
    ``estleg:titleNormalized`` (municipality prefix already stripped) and
    optional ``estleg:issuedUnder`` enabling-act links; child provisions use
    ``Reg_<id>_Par_<n>`` ids with ``estleg:summary`` + ``estleg:legalText``.
    """
    act_id = f"estleg:Reg_{reg_id}_Map_2026"
    act_node = {
        "@id": act_id,
        "@type": ["owl:Ontology", "estleg:Act", "estleg:MunicipalRegulation"],
        "rdfs:label": title_normalized,
        "estleg:titleNormalized": title_normalized,
        "estleg:preambleText": preamble,
    }
    if issued_under:
        act_node["estleg:issuedUnder"] = [{"@id": iri} for iri in issued_under]
    graph = [act_node]
    for i, summary in enumerate(provision_summaries, 1):
        graph.append({
            "@id": f"estleg:Reg_{reg_id}_Par_{i}",
            "@type": ["owl:NamedIndividual", f"estleg:Regulation_{reg_id}"],
            "estleg:summary": summary,
            "estleg:legalText": summary,
        })
    return {"@graph": graph}


def _state_act_doc(act_id: str, provision_summaries: list[str]) -> dict:
    """Build a state-law peep doc (for the cross-layer issuedUnder target)."""
    graph = [{
        "@id": f"estleg:{act_id}",
        "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
        "rdfs:label": act_id,
    }]
    for i, summary in enumerate(provision_summaries, 1):
        graph.append({
            "@id": f"estleg:{act_id}_Par_{i}",
            "@type": ["owl:NamedIndividual", "estleg:LegalProvision_X"],
            "estleg:summary": summary,
        })
    return {"@graph": graph}


def _wire_kov(monkeypatch, krr: Path, peep_files: list[Path]) -> None:
    """Point the KOV pass at ``krr`` / ``peep_files`` with a dir-creating save.

    The KOV pass discovers files through the module-level ``iter_peep_files``
    name and filters to the ``regulations/kov/`` subtree, so both the KOV
    file list and the corpus act index resolve from ``peep_files``. ``KRR_DIR``
    is redirected so the consolidated index is written under ``krr``.
    """
    monkeypatch.setattr(similarity, "KRR_DIR", krr)
    monkeypatch.setattr(similarity, "REPORTS_DIR", krr / "reports")
    monkeypatch.setattr(
        similarity, "iter_peep_files", lambda include_kov=True: list(peep_files)
    )

    def _save(path, payload):
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    monkeypatch.setattr(similarity, "save_json", _save)


def _write_kov(krr: Path, slug: str, reg_id: str, doc: dict) -> Path:
    path = krr / "regulations" / "kov" / slug / f"{slug}_t{reg_id}_peep.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def _kov_index(krr: Path) -> dict:
    return json.loads(
        (krr / "similarity" / "kov_similarity_index.json").read_text(encoding="utf-8")
    )


def _act_node(path: Path, act_id: str) -> dict:
    doc = json.loads(path.read_text(encoding="utf-8"))
    return next(n for n in doc["@graph"] if n["@id"] == act_id)


# ---------------------------------------------------------------------------
# bucket_key algorithm
# ---------------------------------------------------------------------------

def test_bucket_key_two_token_tail():
    """The bucket is the two trailing content tokens ``"{qualifier} {head}"``."""
    assert similarity.bucket_key("opetajate tunnustamise kord") == "tunnustamise kord"
    assert similarity.bucket_key("kooli arengukava") == "kooli arengukava"


def test_bucket_key_single_token_tail():
    """A title with one content token buckets as that bare head."""
    assert similarity.bucket_key("jaatmehoolduseeskiri") == "jaatmehoolduseeskiri"


def test_bucket_key_strips_trailing_action_noun():
    """Trailing action nouns (muutmine/kinnitamine/...) are stripped first."""
    # "pohimaaruse" survives once the trailing "muutmine" is stripped (one
    # content token left -> bare head), then #317 stem-normalizes the
    # genitive to the nominative "pohimaarus".
    assert similarity.bucket_key("pohimaaruse muutmine") == "pohimaarus"
    # A repeated trailing action noun is stripped down to the content tail.
    # "hankekorra" is a fused compound, not a standalone "korra" token, so it
    # is NOT stem-normalized (only the bare type nouns are).
    assert (
        similarity.bucket_key("hankekorra kehtestamine")
        == "hankekorra"
    )
    # The two trailing content tokens survive once the action noun is gone.
    assert (
        similarity.bucket_key("toetuse andmise kord kinnitamine")
        == "andmise kord"
    )


def test_bucket_key_strips_connectives_and_year_words():
    """Trailing connectives / year-words and <3-char tokens are dropped."""
    assert (
        similarity.bucket_key("kooli arengukava aastateks 2024 2028")
        == "kooli arengukava"
    )
    # Trailing connective "ja" is stripped; the remaining tail is the two
    # trailing content tokens, with the genitive "eeskirja" stem-normalized to
    # the nominative "eeskiri" (#317).
    assert similarity.bucket_key("jaatmevaldkonna eeskirja ja") == "jaatmevaldkonna eeskiri"


def test_bucket_key_filters_mid_title_action_noun(tmp_path):
    """A mid-title action noun followed by another word is NOT the qualifier.

    Regression for #292(b): with the old single-pass trailing strip,
    bucket_key("eeskirja muutmine ja kehtetuks") bucketed on the amendment
    verb -> "muutmine kehtetuks". Action nouns are now filtered out of the
    whole content list (like connectives), so the title buckets on the
    eeskiri, not "muutmine". The surviving "eeskirja" is additionally
    stem-normalized to "eeskiri" by #317 (the genitive collapses onto the
    nominative wherever the type noun sits).
    """
    bucket = similarity.bucket_key("eeskirja muutmine ja kehtetuks")
    assert "muutmine" not in bucket
    # #317: the genitive "eeskirja" is normalized to the nominative "eeskiri".
    assert "eeskiri" in bucket
    assert bucket == "eeskiri kehtetuks"
    # The action noun is dropped wherever it sits, even between two content
    # tokens.
    assert similarity.bucket_key("vee muutmine kasutamise kord") == "kasutamise kord"


def test_bucket_key_uncategorized_on_empty_or_no_content():
    """Empty / all-connective / all-short titles fall to _uncategorized."""
    assert similarity.bucket_key("") == similarity.UNCATEGORIZED_BUCKET
    assert similarity.bucket_key("ja ning voi") == similarity.UNCATEGORIZED_BUCKET
    assert similarity.bucket_key("a bc") == similarity.UNCATEGORIZED_BUCKET
    # An only-action-noun title strips to nothing -> uncategorized.
    assert similarity.bucket_key("muutmine") == similarity.UNCATEGORIZED_BUCKET


def test_bucket_key_inflected_type_noun_merges_with_nominative():
    """Genitive/partitive type nouns bucket with their nominative (#317).

    Pre-fix an act that *amends* a regulation type ('kooli pohimaaruse
    muutmine' -> head 'pohimaaruse', genitive) never bucketed with acts that
    *are* that type ('kooli pohimaarus' -> 'pohimaarus', nominative) — the
    closest possible matches, never compared. The stem map collapses the
    inflected form onto the nominative so both land in one bucket. Verified
    for the families that actually split in the corpus: pohimaarus / eeskiri
    / kord / piirmaara.
    """
    assert (
        similarity.bucket_key("kooli pohimaaruse muutmine")
        == similarity.bucket_key("kooli pohimaarus")
        == "kooli pohimaarus"
    )
    # The diacritic form (which the tokenizer still accepts) collapses too.
    assert similarity.bucket_key("kooli põhimääruse muutmine") == "kooli pohimaarus"
    # Normalization is per-token, so a type noun in the QUALIFIER slot merges
    # just like one in the head slot.
    assert (
        similarity.bucket_key("vee kasutamise eeskirja muutmine")
        == similarity.bucket_key("vee kasutamise eeskiri")
        == "kasutamise eeskiri"
    )
    # Irregular d-stem genitive 'korra' -> 'kord'.
    assert (
        similarity.bucket_key("hanke korra muutmine")
        == similarity.bucket_key("hanke kord")
        == "hanke kord"
    )
    # Partitive-plural 'piirmaarade' -> 'piirmaara'.
    assert (
        similarity.bucket_key("jaatmevaldkonna piirmaarade kehtestamine")
        == similarity.bucket_key("jaatmevaldkonna piirmaara")
        == "jaatmevaldkonna piirmaara"
    )
    # A fused compound is NOT a standalone type noun, so it is left intact
    # (no over-merging) — 'jaatmehoolduseeskiri' stays as-is.
    assert similarity.bucket_key("jaatmehoolduseeskiri") == "jaatmehoolduseeskiri"


# ---------------------------------------------------------------------------
# Bucketing behaviour
# ---------------------------------------------------------------------------

def test_two_municipalities_same_type_share_bucket_and_pair(tmp_path, monkeypatch):
    """Two municipalities' same-type acts bucket together and pair."""
    krr = tmp_path / "krr_outputs"
    files = [
        _write_kov(krr, "tallinna_vv", "1001",
                   _kov_act_doc("1001", "jaatmehoolduseeskiri", [_WASTE_TEXT])),
        _write_kov(krr, "tartu_vv", "1002",
                   _kov_act_doc("1002", "jaatmehoolduseeskiri", [_WASTE_TEXT])),
    ]
    _wire_kov(monkeypatch, krr, files)

    summary = similarity.run_kov_similarity_pass()

    assert summary["comparedBuckets"] == 1
    bucket = summary["buckets"]["jaatmehoolduseeskiri"]
    assert bucket["regulationCount"] == 2
    # Symmetric: both acts list the other as a peer.
    sources = {p["source"] for p in bucket["pairs"]}
    assert sources == {"estleg:Reg_1001_Map_2026", "estleg:Reg_1002_Map_2026"}
    peer = bucket["pairs"][0]["peers"][0]
    assert peer["score"] >= similarity.KOV_SIMILARITY_THRESHOLD


def test_inflected_and_nominative_type_acts_share_bucket_and_pair(tmp_path, monkeypatch):
    """An act amending a type buckets+pairs with an act that IS that type (#317).

    'kooli pohimaaruse muutmine' (genitive head) and 'kooli pohimaarus'
    (nominative) previously landed in two singleton buckets and were never
    compared. With stem normalization they share one 'kooli pohimaarus'
    bucket and, given similar text, form a symmetric pair.
    """
    krr = tmp_path / "krr_outputs"
    files = [
        _write_kov(krr, "amend_vv", "18001",
                   _kov_act_doc("18001", "kooli pohimaaruse muutmine", [_WATER_TEXT])),
        _write_kov(krr, "base_vv", "18002",
                   _kov_act_doc("18002", "kooli pohimaarus", [_WATER_TEXT])),
    ]
    _wire_kov(monkeypatch, krr, files)

    summary = similarity.run_kov_similarity_pass()

    assert summary["comparedBuckets"] == 1
    assert "kooli pohimaarus" in summary["buckets"]
    bucket = summary["buckets"]["kooli pohimaarus"]
    assert bucket["regulationCount"] == 2
    sources = {p["source"] for p in bucket["pairs"]}
    assert sources == {"estleg:Reg_18001_Map_2026", "estleg:Reg_18002_Map_2026"}
    # Both acts carry the SAME normalized bucket label.
    for path, act_id in (
        (files[0], "estleg:Reg_18001_Map_2026"),
        (files[1], "estleg:Reg_18002_Map_2026"),
    ):
        node = _act_node(path, act_id)
        assert node["estleg:regulationTypeBucket"] == "kooli pohimaarus"


def test_no_cross_bucket_pairs(tmp_path, monkeypatch):
    """Acts in different buckets never pair, even with similar text."""
    krr = tmp_path / "krr_outputs"
    # Identical waste text but different regulation-type buckets.
    files = [
        _write_kov(krr, "a_vv", "2001",
                   _kov_act_doc("2001", "jaatmehoolduseeskiri", [_WASTE_TEXT])),
        _write_kov(krr, "b_vv", "2002",
                   _kov_act_doc("2002", "kasutamise eeskiri", [_WASTE_TEXT])),
    ]
    _wire_kov(monkeypatch, krr, files)

    summary = similarity.run_kov_similarity_pass()

    # Two singleton buckets, nothing compared, no pairs.
    assert summary["comparedBuckets"] == 0
    assert summary["totalPairs"] == 0
    for act_id, path in (
        ("estleg:Reg_2001_Map_2026", files[0]),
        ("estleg:Reg_2002_Map_2026", files[1]),
    ):
        node = _act_node(path, act_id)
        assert "estleg:similarAct" not in node


def test_singleton_and_uncategorized_get_bucket_but_no_pairs(tmp_path, monkeypatch):
    """Singletons + _uncategorized acts get the bucket stamp but no pairs."""
    krr = tmp_path / "krr_outputs"
    singleton = _write_kov(krr, "c_vv", "3001",
                           _kov_act_doc("3001", "pohimaarus", [_WATER_TEXT]))
    uncategorized = _write_kov(krr, "d_vv", "3002",
                               _kov_act_doc("3002", "ja ning", [_WASTE_TEXT]))
    _wire_kov(monkeypatch, krr, [singleton, uncategorized])

    summary = similarity.run_kov_similarity_pass()

    assert summary["totalPairs"] == 0
    assert summary["uncategorizedCount"] == 1

    s_node = _act_node(singleton, "estleg:Reg_3001_Map_2026")
    assert s_node["estleg:regulationTypeBucket"] == "pohimaarus"
    assert "estleg:similarAct" not in s_node

    u_node = _act_node(uncategorized, "estleg:Reg_3002_Map_2026")
    assert u_node["estleg:regulationTypeBucket"] == similarity.UNCATEGORIZED_BUCKET
    assert "estleg:similarAct" not in u_node


def test_bucket_coverage_at_least_80_percent(tmp_path, monkeypatch):
    """A mixed fixture lands >=80% of acts in a non-uncategorized bucket."""
    krr = tmp_path / "krr_outputs"
    titles = [
        "jaatmehoolduseeskiri",
        "kasutamise eeskiri",
        "tunnustamise kord",
        "kooli arengukava",
        "andmise kord",
        "hankekord",
        "lasteaia pohimaarus",
        "kohanime maaramine",
        "registri pohimaarus",
        # one genuinely uncategorized (all connectives) -> 1/10 = 10% uncat
        "ja ning voi",
    ]
    files = []
    for i, title in enumerate(titles):
        files.append(
            _write_kov(krr, f"m{i}_vv", f"40{i:02d}",
                       _kov_act_doc(f"40{i:02d}", title, [_WATER_TEXT]))
        )
    _wire_kov(monkeypatch, krr, files)

    summary = similarity.run_kov_similarity_pass()

    coverage = (summary["totalActs"] - summary["uncategorizedCount"]) / summary["totalActs"]
    assert coverage >= 0.80


# ---------------------------------------------------------------------------
# TF-IDF cosine, top-K, output schema, IRIs
# ---------------------------------------------------------------------------

def test_dissimilar_acts_in_same_bucket_do_not_pair(tmp_path, monkeypatch):
    """Same bucket but disjoint vocabulary -> cosine below threshold, no pair."""
    krr = tmp_path / "krr_outputs"
    files = [
        _write_kov(krr, "e_vv", "5001",
                   _kov_act_doc("5001", "jaatmehoolduseeskiri", [_WASTE_TEXT])),
        _write_kov(krr, "f_vv", "5002",
                   _kov_act_doc("5002", "jaatmehoolduseeskiri", [_WATER_TEXT])),
    ]
    _wire_kov(monkeypatch, krr, files)

    summary = similarity.run_kov_similarity_pass()

    # Bucket is comparable (2 members) but disjoint vocab -> no pair survives.
    assert summary["buckets"]["jaatmehoolduseeskiri"]["regulationCount"] == 2
    assert summary["totalPairs"] == 0


def test_output_ids_are_act_iris_not_provisions(tmp_path, monkeypatch):
    """Every source/target/Similarity id is an act IRI (Map_), never _Par_."""
    krr = tmp_path / "krr_outputs"
    files = [
        _write_kov(krr, "g_vv", "6001",
                   _kov_act_doc("6001", "jaatmehoolduseeskiri", [_WASTE_TEXT])),
        _write_kov(krr, "h_vv", "6002",
                   _kov_act_doc("6002", "jaatmehoolduseeskiri", [_WASTE_TEXT])),
    ]
    _wire_kov(monkeypatch, krr, files)

    summary = similarity.run_kov_similarity_pass()

    for bucket in summary["buckets"].values():
        for pair in bucket["pairs"]:
            assert "_Par_" not in pair["source"]
            assert pair["source"].endswith("_Map_2026")
            for peer in pair["peers"]:
                assert "_Par_" not in peer["target"]
                assert peer["target"].endswith("_Map_2026")

    # Reified Similarity nodes in the source peep also use act IRIs.
    doc = json.loads(files[0].read_text(encoding="utf-8"))
    sim_nodes = [
        n for n in doc["@graph"]
        if isinstance(n.get("@id"), str) and n["@id"].startswith("estleg:Similarity_")
    ]
    assert sim_nodes
    for node in sim_nodes:
        assert "_Par_" not in node["@id"]
        assert "_Par_" not in node["estleg:similarTarget"]["@id"]


def test_reified_similarity_node_schema(tmp_path, monkeypatch):
    """Back-links are reified estleg:Similarity nodes with the documented shape."""
    krr = tmp_path / "krr_outputs"
    files = [
        _write_kov(krr, "i_vv", "7001",
                   _kov_act_doc("7001", "jaatmehoolduseeskiri", [_WASTE_TEXT])),
        _write_kov(krr, "j_vv", "7002",
                   _kov_act_doc("7002", "jaatmehoolduseeskiri", [_WASTE_TEXT])),
    ]
    _wire_kov(monkeypatch, krr, files)

    similarity.run_kov_similarity_pass()

    doc = json.loads(files[0].read_text(encoding="utf-8"))
    act = next(n for n in doc["@graph"] if n["@id"] == "estleg:Reg_7001_Map_2026")
    # similarAct is a list of IRI references into the same graph.
    refs = act["estleg:similarAct"]
    assert isinstance(refs, list) and refs
    ref_id = refs[0]["@id"]
    assert ref_id.startswith("estleg:Similarity_")

    node = next(n for n in doc["@graph"] if n.get("@id") == ref_id)
    assert node["@type"] == ["owl:NamedIndividual", "estleg:Similarity"]
    assert node["estleg:similarTarget"] == {"@id": "estleg:Reg_7002_Map_2026"}
    assert node["estleg:similarityScore"]["@type"] == "xsd:decimal"
    assert float(node["estleg:similarityScore"]["@value"]) >= similarity.KOV_SIMILARITY_THRESHOLD
    assert node["estleg:similarityModel"] == "tfidf-cosine"


def test_kov_similarity_link_value_builder():
    """The reified-node builder produces the documented shape."""
    value = similarity.kov_similarity_link_value(
        "Reg_1_Map_2026", "Reg_2_Map_2026", 0.873123
    )
    assert value == {
        "@id": "estleg:Similarity_Reg_1_Map_2026_Reg_2_Map_2026",
        "@type": ["owl:NamedIndividual", "estleg:Similarity"],
        "estleg:similarTarget": {"@id": "estleg:Reg_2_Map_2026"},
        "estleg:similarityScore": {"@value": "0.873", "@type": "xsd:decimal"},
        "estleg:similarityModel": "tfidf-cosine",
    }


def test_top_k_bound_respected(tmp_path, monkeypatch):
    """No source keeps more than KOV_TOP_K peers."""
    krr = tmp_path / "krr_outputs"
    monkeypatch.setattr(similarity, "KOV_TOP_K", 3)
    # Six identical-text acts in one bucket -> each has 5 candidate peers,
    # capped to 3.
    files = []
    for i in range(6):
        files.append(
            _write_kov(krr, f"k{i}_vv", f"80{i:02d}",
                       _kov_act_doc(f"80{i:02d}", "jaatmehoolduseeskiri", [_WASTE_TEXT]))
        )
    _wire_kov(monkeypatch, krr, files)

    summary = similarity.run_kov_similarity_pass()

    bucket = summary["buckets"]["jaatmehoolduseeskiri"]
    for pair in bucket["pairs"]:
        assert len(pair["peers"]) <= 3
    # The reified back-links are likewise capped.
    doc = json.loads(files[0].read_text(encoding="utf-8"))
    act = next(n for n in doc["@graph"] if n["@id"] == "estleg:Reg_8000_Map_2026")
    assert len(act["estleg:similarAct"]) <= 3


def _intra_directed_edges(index: dict) -> set:
    """All (source, target) intra-bucket KOV->KOV peer edges in the index."""
    edges = set()
    for bucket in index["buckets"].values():
        for pair in bucket["pairs"]:
            for peer in pair["peers"]:
                if peer["target"].startswith("estleg:Reg_"):
                    edges.add((pair["source"], peer["target"]))
    return edges


def test_intra_bucket_pairs_are_symmetric_after_top_k_cap(tmp_path, monkeypatch):
    """Intra-bucket KOV pairs stay symmetric even when the top-K cap evicts (#367b).

    Fixture: one bucket of four acts — a hub H, two close peers A/B, and a
    weaker fourth act W — with KOV_TOP_K=2. Pairwise cosines are
    H-A=H-B>A-B (all strong) >> H-W=A-W=B-W (weak but >= threshold). Under
    the OLD per-source ``del peers[src][KOV_TOP_K:]`` the hub kept its two
    strongest peers and evicted W, while W (whose only strong-enough peers
    were H and A) still listed them — leaving W->H / W->A one-directional
    (the ~49.5% asymmetry this fixes). The greedy symmetric cap instead drops
    the weak pairs in BOTH directions, so every surviving edge is reciprocal.
    """
    monkeypatch.setattr(similarity, "KOV_TOP_K", 2)
    krr = tmp_path / "krr_outputs"
    files = [
        _write_kov(krr, "hub_vv", "19001",
                   _kov_act_doc("19001", "jaatmehoolduseeskiri", [_SYM_HUB])),
        _write_kov(krr, "near_a_vv", "19002",
                   _kov_act_doc("19002", "jaatmehoolduseeskiri", [_SYM_NEAR_A])),
        _write_kov(krr, "near_b_vv", "19003",
                   _kov_act_doc("19003", "jaatmehoolduseeskiri", [_SYM_NEAR_B])),
        _write_kov(krr, "weak_vv", "19004",
                   _kov_act_doc("19004", "jaatmehoolduseeskiri", [_SYM_WEAK])),
    ]
    _wire_kov(monkeypatch, krr, files)

    similarity.run_kov_similarity_pass()
    index = _kov_index(krr)

    # Every emitted intra-bucket directed edge has its reciprocal.
    edges = _intra_directed_edges(index)
    assert edges, "expected at least one intra-bucket pair"
    asymmetric = {(a, b) for (a, b) in edges if (b, a) not in edges}
    assert asymmetric == set(), f"one-directional intra pairs: {asymmetric}"

    # The reified per-act back-links agree with the index and are symmetric:
    # A lists B iff B lists A. Build the act-node similar-act target map.
    def _similar_act_targets(path: Path, act_id: str) -> set:
        node = _act_node(path, act_id)
        doc = json.loads(path.read_text(encoding="utf-8"))
        out = set()
        for ref in node.get("estleg:similarAct", []):
            sim = next(n for n in doc["@graph"] if n.get("@id") == ref["@id"])
            out.add(sim["estleg:similarTarget"]["@id"])
        return out

    backlink_edges = set()
    id_by_file = {
        files[i]: f"estleg:Reg_1900{i + 1}_Map_2026" for i in range(4)
    }
    for path, act_id in id_by_file.items():
        for tgt in _similar_act_targets(path, act_id):
            if tgt.startswith("estleg:Reg_"):
                backlink_edges.add((act_id, tgt))
    assert backlink_edges == edges
    # No source exceeds the cap.
    for path, act_id in id_by_file.items():
        node = _act_node(path, act_id)
        assert len(node.get("estleg:similarAct", [])) <= similarity.KOV_TOP_K


def test_consolidated_index_schema(tmp_path, monkeypatch):
    """The single index file carries every documented top-level field."""
    krr = tmp_path / "krr_outputs"
    files = [
        _write_kov(krr, "l_vv", "9001",
                   _kov_act_doc("9001", "jaatmehoolduseeskiri", [_WASTE_TEXT])),
        _write_kov(krr, "n_vv", "9002",
                   _kov_act_doc("9002", "jaatmehoolduseeskiri", [_WASTE_TEXT])),
    ]
    _wire_kov(monkeypatch, krr, files)

    similarity.run_kov_similarity_pass()
    index = _kov_index(krr)

    for key in (
        "generated", "scoreModel", "topK", "threshold", "totalActs",
        "totalBuckets", "comparedBuckets", "uncategorizedCount", "totalPairs",
        "largestBucket", "crossLayerPairs", "buckets",
    ):
        assert key in index, key
    assert index["scoreModel"] == "tfidf-cosine"
    assert index["topK"] == similarity.KOV_TOP_K
    assert index["threshold"] == similarity.KOV_SIMILARITY_THRESHOLD
    assert set(index["largestBucket"]) == {"key", "size"}
    # Only ONE consolidated file under similarity/ (no per-bucket files).
    sim_dir = krr / "similarity"
    assert [p.name for p in sim_dir.glob("*.json")] == ["kov_similarity_index.json"]


# ---------------------------------------------------------------------------
# Cross-layer KOV -> state via issuedUnder
# ---------------------------------------------------------------------------

def test_cross_layer_via_issued_under(tmp_path, monkeypatch):
    """A KOV act similar to its issuedUnder state act gets a cross-layer peer."""
    krr = tmp_path / "krr_outputs"
    krr.mkdir(parents=True, exist_ok=True)
    state = krr / "jaatmeseadus_peep.json"
    state.write_text(
        json.dumps(_state_act_doc("JAATS_Map_2026", [_WASTE_TEXT])),
        encoding="utf-8",
    )
    kov = _write_kov(
        krr, "o_vv", "10001",
        _kov_act_doc("10001", "jaatmehoolduseeskiri", [_WASTE_TEXT],
                     issued_under=["estleg:JAATS_Map_2026"]),
    )
    _wire_kov(monkeypatch, krr, [state, kov])

    summary = similarity.run_kov_similarity_pass()

    assert summary["crossLayerPairs"] >= 1
    node = _act_node(kov, "estleg:Reg_10001_Map_2026")
    targets = {r["@id"] for r in node["estleg:similarAct"]}
    assert "estleg:Similarity_Reg_10001_Map_2026_JAATS_Map_2026" in targets
    # The cross-layer Similarity node points at the state act IRI.
    doc = json.loads(kov.read_text(encoding="utf-8"))
    cl = next(
        n for n in doc["@graph"]
        if n.get("@id") == "estleg:Similarity_Reg_10001_Map_2026_JAATS_Map_2026"
    )
    assert cl["estleg:similarTarget"] == {"@id": "estleg:JAATS_Map_2026"}


def test_cross_layer_skips_kov_to_kov_issued_under(tmp_path, monkeypatch):
    """issuedUnder pointing at another KOV act is not a cross-layer peer."""
    krr = tmp_path / "krr_outputs"
    # Two KOV acts in DIFFERENT buckets; one issuedUnder the other.
    enabling = _write_kov(krr, "p_vv", "11001",
                          _kov_act_doc("11001", "pohimaarus", [_WASTE_TEXT]))
    dependent = _write_kov(
        krr, "q_vv", "11002",
        _kov_act_doc("11002", "kasutamise eeskiri", [_WASTE_TEXT],
                     issued_under=["estleg:Reg_11001_Map_2026"]),
    )
    _wire_kov(monkeypatch, krr, [enabling, dependent])

    summary = similarity.run_kov_similarity_pass()

    # KOV->KOV issuedUnder is skipped; different buckets => no pairs at all.
    assert summary["crossLayerPairs"] == 0
    assert summary["totalPairs"] == 0
    dep_node = _act_node(dependent, "estleg:Reg_11002_Map_2026")
    assert "estleg:similarAct" not in dep_node


# ---------------------------------------------------------------------------
# Determinism + idempotency
# ---------------------------------------------------------------------------

def test_kov_pass_is_deterministic(tmp_path, monkeypatch):
    """Two runs over the same corpus produce byte-identical index + peeps."""
    krr = tmp_path / "krr_outputs"
    files = []
    for i in range(4):
        files.append(
            _write_kov(krr, f"r{i}_vv", f"120{i:02d}",
                       _kov_act_doc(f"120{i:02d}", "jaatmehoolduseeskiri", [_WASTE_TEXT]))
        )
    _wire_kov(monkeypatch, krr, files)

    similarity.run_kov_similarity_pass()
    index_1 = _kov_index(krr)
    peep_1 = files[0].read_text(encoding="utf-8")

    similarity.run_kov_similarity_pass()
    index_2 = _kov_index(krr)
    peep_2 = files[0].read_text(encoding="utf-8")

    # The only volatile field is the generated date; everything else is stable.
    index_1.pop("generated")
    index_2.pop("generated")
    assert index_1 == index_2
    assert peep_1 == peep_2
    # Bucket keys, sources, and peers are all deterministically ordered.
    assert list(index_1["buckets"]) == sorted(index_1["buckets"])
    for bucket in index_1["buckets"].values():
        sources = [p["source"] for p in bucket["pairs"]]
        assert sources == sorted(sources)
        for pair in bucket["pairs"]:
            peers = [(-p["score"], p["target"]) for p in pair["peers"]]
            assert peers == sorted(peers)


def test_kov_rerun_does_not_duplicate_backlinks(tmp_path, monkeypatch):
    """Idempotent: a second run leaves exactly one similarAct + Similarity set."""
    krr = tmp_path / "krr_outputs"
    files = [
        _write_kov(krr, "s_vv", "13001",
                   _kov_act_doc("13001", "jaatmehoolduseeskiri", [_WASTE_TEXT])),
        _write_kov(krr, "t_vv", "13002",
                   _kov_act_doc("13002", "jaatmehoolduseeskiri", [_WASTE_TEXT])),
    ]
    _wire_kov(monkeypatch, krr, files)

    similarity.run_kov_similarity_pass()
    similarity.run_kov_similarity_pass()
    similarity.run_kov_similarity_pass()

    doc = json.loads(files[0].read_text(encoding="utf-8"))
    act = next(n for n in doc["@graph"] if n["@id"] == "estleg:Reg_13001_Map_2026")
    # Exactly one peer, one ref, one reified node — no accumulation.
    assert len(act["estleg:similarAct"]) == 1
    sim_nodes = [
        n for n in doc["@graph"]
        if isinstance(n.get("@id"), str) and n["@id"].startswith("estleg:Similarity_")
    ]
    assert len(sim_nodes) == 1
    sim_ids = [n["@id"] for n in sim_nodes]
    assert len(sim_ids) == len(set(sim_ids))
    # The act node still appears exactly once.
    act_nodes = [n for n in doc["@graph"] if n["@id"] == "estleg:Reg_13001_Map_2026"]
    assert len(act_nodes) == 1


def test_oversize_bucket_subsplit_or_skipped(tmp_path, monkeypatch):
    """A bucket over the size cap is sub-split (or recorded + skipped)."""
    krr = tmp_path / "krr_outputs"
    monkeypatch.setattr(similarity, "_BUCKET_MAX_SIZE", 3)
    # Five acts share the base bucket "kasutamise kord" but split on the
    # preceding token (vee/tee) -> two sub-buckets, both under the cap.
    files = []
    files.append(_write_kov(krr, "u0", "1401",
                            _kov_act_doc("1401", "vee kasutamise kord", [_WATER_TEXT])))
    files.append(_write_kov(krr, "u1", "1402",
                            _kov_act_doc("1402", "vee kasutamise kord", [_WATER_TEXT])))
    files.append(_write_kov(krr, "u2", "1403",
                            _kov_act_doc("1403", "vee kasutamise kord", [_WATER_TEXT])))
    files.append(_write_kov(krr, "u3", "1404",
                            _kov_act_doc("1404", "tee kasutamise kord", [_WASTE_TEXT])))
    files.append(_write_kov(krr, "u4", "1405",
                            _kov_act_doc("1405", "tee kasutamise kord", [_WASTE_TEXT])))
    _wire_kov(monkeypatch, krr, files)

    summary = similarity.run_kov_similarity_pass()

    # The base "kasutamise kord" bucket was sub-split, so no single bucket
    # exceeds the cap.
    for bucket_key_, bucket in summary["buckets"].items():
        assert bucket["regulationCount"] <= 3
    # The sub-split keys carry the preceding qualifier token.
    assert "vee kasutamise kord" in summary["buckets"]
    assert "tee kasutamise kord" in summary["buckets"]


# ---------------------------------------------------------------------------
# --no-kov leaves the provision-level pass untouched
# ---------------------------------------------------------------------------

def test_no_kov_flag_skips_kov_pass(tmp_path, monkeypatch):
    """--no-kov produces no KOV index and writes no KOV back-links."""
    krr = tmp_path / "krr_outputs"
    files = [
        _write_kov(krr, "v_vv", "15001",
                   _kov_act_doc("15001", "jaatmehoolduseeskiri", [_WASTE_TEXT])),
        _write_kov(krr, "w_vv", "15002",
                   _kov_act_doc("15002", "jaatmehoolduseeskiri", [_WASTE_TEXT])),
    ]
    _wire_kov(monkeypatch, krr, files)

    similarity.main(["--no-kov"])

    # No consolidated KOV index file.
    assert not (krr / "similarity" / "kov_similarity_index.json").exists()
    # No KOV back-links stamped onto act nodes.
    node = _act_node(files[0], "estleg:Reg_15001_Map_2026")
    assert "estleg:regulationTypeBucket" not in node
    assert "estleg:similarAct" not in node


def test_no_kov_leaves_provision_index_unchanged(tmp_path, monkeypatch):
    """--no-kov vs default: provision similarity_index.json is byte-identical.

    The provision-level pass runs over laws+state only and must be wholly
    unaffected by the KOV act-level pass running (or not). Here the only
    inputs are KOV acts, so the provision index is empty either way — the
    point is that the KOV pass never touches similarity_index.json.
    """
    def _run(include_kov_flag: list[str]) -> str:
        krr = tmp_path / ("with_kov" if include_kov_flag == [] else "no_kov")
        files = [
            _write_kov(krr, "x_vv", "16001",
                       _kov_act_doc("16001", "jaatmehoolduseeskiri", [_WASTE_TEXT])),
            _write_kov(krr, "y_vv", "16002",
                       _kov_act_doc("16002", "jaatmehoolduseeskiri", [_WASTE_TEXT])),
        ]
        with monkeypatch.context() as m:
            m.setattr(similarity, "KRR_DIR", krr)
            m.setattr(similarity, "REPORTS_DIR", krr / "reports")
            m.setattr(
                similarity, "iter_peep_files", lambda include_kov=True: list(files)
            )

            def _save(path, payload):
                p = Path(path)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

            m.setattr(similarity, "save_json", _save)
            similarity.main(include_kov_flag)
        return (krr / "similarity_index.json").read_text(encoding="utf-8")

    with_kov = _run([])
    no_kov = _run(["--no-kov"])
    assert with_kov == no_kov


def test_no_kov_clears_existing_kov_output(tmp_path, monkeypatch):
    """--no-kov is effective on an ALREADY-generated tree (#250 review): a
    prior default run's back-links, reified Similarity nodes, and consolidated
    index are all removed, not left stale."""
    krr = tmp_path / "krr_outputs"
    files = [
        _write_kov(krr, "v_vv", "17001",
                   _kov_act_doc("17001", "jaatmehoolduseeskiri", [_WASTE_TEXT])),
        _write_kov(krr, "w_vv", "17002",
                   _kov_act_doc("17002", "jaatmehoolduseeskiri", [_WASTE_TEXT])),
    ]
    _wire_kov(monkeypatch, krr, files)

    # Default run generates KOV output.
    similarity.main([])
    assert (krr / "similarity" / "kov_similarity_index.json").exists()
    node = _act_node(files[0], "estleg:Reg_17001_Map_2026")
    assert "estleg:regulationTypeBucket" in node
    assert "estleg:similarAct" in node
    doc = json.loads(files[0].read_text(encoding="utf-8"))
    assert any(
        n.get("@id", "").startswith("estleg:Similarity_") for n in doc["@graph"]
    )

    # --no-kov clears all of it.
    similarity.main(["--no-kov"])
    assert not (krr / "similarity" / "kov_similarity_index.json").exists()
    node = _act_node(files[0], "estleg:Reg_17001_Map_2026")
    assert "estleg:regulationTypeBucket" not in node
    assert "estleg:similarAct" not in node
    doc = json.loads(files[0].read_text(encoding="utf-8"))
    assert not any(
        n.get("@id", "").startswith("estleg:Similarity_") for n in doc["@graph"]
    )


def test_cross_layer_pairs_count_is_post_cap(tmp_path, monkeypatch):
    """crossLayerPairs counts EMITTED (post-top-K) cross-layer peers, not
    pre-cap candidates (#250 review). With top-K=2, the lower-scored
    cross-layer peer is evicted by the two identical intra-bucket peers, so
    the emitted cross-layer count — and crossLayerPairs — is 0 even though one
    cross-layer candidate existed pre-cap."""
    monkeypatch.setattr(similarity, "KOV_TOP_K", 2)
    krr = tmp_path / "krr_outputs"
    krr.mkdir(parents=True, exist_ok=True)
    # State act shares the waste tokens (+1 extra) so cosine with a pure-waste
    # KOV act is a candidate (>=0.3) yet below the identical intra peers (1.0).
    state = krr / "jaatmeseadus_peep.json"
    state.write_text(
        json.dumps(_state_act_doc("JAATS_Map_2026", [_WASTE_TEXT + " reovesi"])),
        encoding="utf-8",
    )
    files = [state]
    # Three identical-text KOV acts in one bucket (pairwise cosine 1.0); only
    # the first is issuedUnder the state act.
    for n in (1, 2, 3):
        files.append(_write_kov(
            krr, f"c{n}_vv", f"3000{n}",
            _kov_act_doc(f"3000{n}", "jaatmehoolduseeskiri", [_WASTE_TEXT],
                         issued_under=["estleg:JAATS_Map_2026"] if n == 1 else None),
        ))
    _wire_kov(monkeypatch, krr, files)

    summary = similarity.run_kov_similarity_pass()
    index = _kov_index(krr)

    # act 30001 is capped at top-K=2 intra peers; its cross-layer candidate
    # is evicted, so no cross-layer (non-Reg target) peer is emitted anywhere.
    node = _act_node(files[1], "estleg:Reg_30001_Map_2026")
    assert len(node.get("estleg:similarAct", [])) == 2
    emitted_cross = sum(
        1
        for bucket in index["buckets"].values()
        for pair in bucket["pairs"]
        for peer in pair["peers"]
        if not peer["target"].startswith("estleg:Reg_")
    )
    assert emitted_cross == 0
    assert summary["crossLayerPairs"] == 0

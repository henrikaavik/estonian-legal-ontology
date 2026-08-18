"""Tests for issue #598 — the DATA residual of the pre-#651 ``find_in_title`` bug.

``generate_annotations.find_in_title`` is now token-bounded (#651), but the
shipped ``oiguskantsler_seisukohad.jsonld`` still carries the shorter-act edges
the OLD unbounded substring match leaked alongside a longer co-annotated act
(``gaasiseadus`` ⊂ ``maagaasiseadus`` …). ``remove_spurious_annotations_598``
re-derives with the FIXED matcher and drops exactly those edges.

The detection is exercised against a small but REAL ``generate_annotations._LawIndex``
(so the actual token-bounded ``find_in_title`` / ``find_in_body`` engine runs),
plus tmp_path file fixtures (no real corpus).
"""
from __future__ import annotations

import json
from pathlib import Path

from estleg import generate_annotations as ga
from estleg import remove_spurious_annotations_598 as rsa

# ---------------------------------------------------------------------------
# Fixtures: a tiny real _LawIndex + matching iri_title, and node/doc builders.
# ---------------------------------------------------------------------------

def _make_index(titles: dict[str, str]):
    """Build a real ``_LawIndex`` from {normalized-nominative-title: act-IRI}.

    Registers genitive/oblique variants exactly like ``build_law_index`` so the
    real token-bounded matcher resolves inflected opinion-title mentions, and
    returns the paired ``iri_title`` (act IRI -> nominative title).
    """
    idx = ga._LawIndex()
    for norm, iri in titles.items():
        idx.by_name.setdefault(norm, iri)
        for variant in ga._genitive_variants(norm):
            idx.by_name.setdefault(variant, iri)
    idx._ordered_names = sorted(idx.by_name, key=len, reverse=True)
    iri_title = {iri: norm for norm, iri in titles.items()}
    return idx, iri_title


def _annotation(iri: str, act: str, title: str, *, url: str, text: str = "") -> dict:
    return {
        "@id": iri,
        "@type": ["owl:NamedIndividual", "estleg:Annotation"],
        "estleg:annotates": {"@id": act},
        "estleg:annotationText": text or title,
        "estleg:annotationType": "interpretation",
        "estleg:annotationSource": "Õiguskantsler",
        "rdfs:label": {"@value": f"Õiguskantsleri seisukoht: {title}", "@language": "et"},
        "estleg:annotationSourceUrl": {"@value": url, "@type": "xsd:anyURI"},
    }


def _header(count: int) -> dict:
    return {
        "@id": rsa.HEADER_ID,
        "@type": ["owl:Ontology"],
        "rdfs:label": {
            "@value": f"Õiguskantsleri seisukohad — praktiku annotatsioonid ({count} annotatsiooni)",
            "@language": "et",
        },
        "dc:source": "Õiguskantsler",
    }


def _doc(nodes: list[dict]) -> dict:
    return {"@context": {"estleg": "x"}, "@graph": [_header(len(nodes)), *nodes]}


# A reusable substring pair: gaasiseadus ⊂ maagaasiseadus.
_GAS_TITLES = {"gaasiseadus": "estleg:GAAS", "maagaasiseadus": "estleg:MGS"}


# ---------------------------------------------------------------------------
# Detection — the exact bug signature.
# ---------------------------------------------------------------------------

def test_substring_pair_in_title_drops_shorter_keeps_longer():
    idx, iri_title = _make_index(_GAS_TITLES)
    graph = [
        _annotation("estleg:Annotation_OK_a_MGS", "estleg:MGS", "Maagaasiseaduse muutmine", url="u1"),
        _annotation("estleg:Annotation_OK_a_GAAS", "estleg:GAAS", "Maagaasiseaduse muutmine", url="u1"),
    ]
    spurious = rsa.detect_spurious(graph, idx, iri_title)
    assert len(spurious) == 1
    s = spurious[0]
    assert s.dropped_iri == "estleg:GAAS"
    assert s.dropped_title == "gaasiseadus"
    assert s.kept_iri == "estleg:MGS"
    assert s.kept_title == "maagaasiseadus"


def test_two_unrelated_acts_both_kept():
    # Neither title contains the other; both legitimately token-match the title.
    idx, iri_title = _make_index(
        {"gaasiseadus": "estleg:GAAS", "nimeseadus": "estleg:NS"}
    )
    graph = [
        _annotation("estleg:Annotation_OK_b_GAAS", "estleg:GAAS", "Gaasiseaduse ja nimeseaduse kooskõla", url="u2"),
        _annotation("estleg:Annotation_OK_b_NS", "estleg:NS", "Gaasiseaduse ja nimeseaduse kooskõla", url="u2"),
    ]
    assert rsa.detect_spurious(graph, idx, iri_title) == []


def test_shorter_act_with_standalone_body_mention_is_kept():
    # Body-guard: the opinion title names only the longer act, but the body
    # genuinely references the shorter act by bare name -> the fixed find_in_body
    # would re-introduce it, so it is NOT spurious.
    idx, iri_title = _make_index(_GAS_TITLES)
    body = "Maagaasiseaduse eelnõu. Varasem gaasiseadus reguleeris turgu teisiti."
    graph = [
        _annotation("estleg:Annotation_OK_c_MGS", "estleg:MGS", "Maagaasiseaduse muutmine", url="u3", text=body),
        _annotation("estleg:Annotation_OK_c_GAAS", "estleg:GAAS", "Maagaasiseaduse muutmine", url="u3", text=body),
    ]
    assert rsa.detect_spurious(graph, idx, iri_title) == []


def test_co_occurrence_without_longer_in_title_is_kept():
    # Both acts annotated (e.g. both from the body), but the longer act's title
    # is NOT in the opinion title -> there is no find_in_title leak -> keep both.
    # Guards against treating mere co-occurrence as the bug.
    idx, iri_title = _make_index(_GAS_TITLES)
    graph = [
        _annotation("estleg:Annotation_OK_d_MGS", "estleg:MGS", "Energiaturu ülevaade", url="u4"),
        _annotation("estleg:Annotation_OK_d_GAAS", "estleg:GAAS", "Energiaturu ülevaade", url="u4"),
    ]
    assert rsa.detect_spurious(graph, idx, iri_title) == []


def test_self_inflection_without_longer_sibling_is_kept():
    # Partitive "planeerimisseadust" is not a registered token, so the fixed
    # find_in_title drops it — but there is no longer co-annotated act, so this
    # is NOT the #598 cross-act bug and the node is left untouched.
    idx, iri_title = _make_index({"planeerimisseadus": "estleg:PLAN"})
    graph = [
        _annotation("estleg:Annotation_OK_e_PLAN", "estleg:PLAN", "Planeerimisseadust kohaldamata", url="u5"),
    ]
    assert rsa.detect_spurious(graph, idx, iri_title) == []


def test_unknown_act_title_is_never_touched():
    # An annotated act with no corpus title in iri_title must be left alone.
    idx, iri_title = _make_index(_GAS_TITLES)
    graph = [
        _annotation("estleg:Annotation_OK_f_MGS", "estleg:MGS", "Maagaasiseaduse muutmine", url="u6"),
        _annotation("estleg:Annotation_OK_f_X", "estleg:UNKNOWN", "Maagaasiseaduse muutmine", url="u6"),
    ]
    assert rsa.detect_spurious(graph, idx, iri_title) == []


def test_detection_is_idempotent_after_removal():
    idx, iri_title = _make_index(_GAS_TITLES)
    nodes = [
        _annotation("estleg:Annotation_OK_g_MGS", "estleg:MGS", "Maagaasiseaduse muutmine", url="u7"),
        _annotation("estleg:Annotation_OK_g_GAAS", "estleg:GAAS", "Maagaasiseaduse muutmine", url="u7"),
    ]
    doc = _doc(nodes)
    spurious = rsa.detect_spurious(doc["@graph"], idx, iri_title)
    assert len(spurious) == 1
    rsa.apply_removal(doc, spurious)
    # Re-deriving over the cleaned graph finds nothing (longer act still matches).
    assert rsa.detect_spurious(doc["@graph"], idx, iri_title) == []


def test_longest_containing_sibling_is_chosen_as_kept():
    # A ⊂ B ⊂ C all annotated and all matched in the title; the leaked shortest
    # A is attributed to the LONGEST container C.
    idx, iri_title = _make_index(
        {
            "seadus": "estleg:S",
            "raamseadus": "estleg:RS",
            "uue raamseadus": "estleg:URS",
        }
    )
    # Title carries the longest; "seadus" leaks only as a substring of it.
    graph = [
        _annotation("estleg:Annotation_OK_h_URS", "estleg:URS", "Uue raamseadus arutelu", url="u8"),
        _annotation("estleg:Annotation_OK_h_S", "estleg:S", "Uue raamseadus arutelu", url="u8"),
    ]
    spurious = rsa.detect_spurious(graph, idx, iri_title)
    assert len(spurious) == 1
    assert spurious[0].dropped_iri == "estleg:S"
    assert spurious[0].kept_title == "uue raamseadus"


# ---------------------------------------------------------------------------
# Node helpers.
# ---------------------------------------------------------------------------

def test_node_helpers():
    node = _annotation("estleg:Annotation_OK_z", "estleg:MGS", "Maagaasiseaduse muutmine", url="u9")
    assert rsa._is_annotation(node) is True
    assert rsa._is_annotation({"@type": ["owl:Ontology"]}) is False
    assert rsa._is_annotation("not-a-dict") is False
    assert rsa._annotates_iri(node) == "estleg:MGS"
    assert rsa._annotates_iri({"estleg:annotates": "estleg:BARE"}) == "estleg:BARE"
    assert rsa._opinion_title(node) == "Maagaasiseaduse muutmine"
    assert rsa._opinion_key(node) == "url::u9"
    # Falls back to the label when the source URL is absent.
    no_url = dict(node)
    del no_url["estleg:annotationSourceUrl"]
    assert rsa._opinion_key(no_url).startswith("label::Õiguskantsleri seisukoht:")
    assert rsa._scalar({"@value": "x"}) == "x"
    assert rsa._scalar("y") == "y"
    assert rsa._scalar(None) == ""


def test_update_header_count():
    doc = _doc(
        [
            _annotation("estleg:Annotation_OK_i_MGS", "estleg:MGS", "Maagaasiseaduse muutmine", url="u10"),
            _annotation("estleg:Annotation_OK_i_GAAS", "estleg:GAAS", "Maagaasiseaduse muutmine", url="u10"),
        ]
    )
    assert "(2 annotatsiooni)" in doc["@graph"][0]["rdfs:label"]["@value"]
    rsa._update_header_count(doc["@graph"], 1)
    assert "(1 annotatsiooni)" in doc["@graph"][0]["rdfs:label"]["@value"]


# ---------------------------------------------------------------------------
# File processing — dry-run / apply / idempotency / LFS guard.
# ---------------------------------------------------------------------------

def _write_doc(path: Path, doc: dict) -> None:
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def _spurious_doc() -> dict:
    return _doc(
        [
            _annotation("estleg:Annotation_OK_MGS", "estleg:MGS", "Maagaasiseaduse muutmine", url="op1"),
            _annotation("estleg:Annotation_OK_GAAS", "estleg:GAAS", "Maagaasiseaduse muutmine", url="op1"),
            _annotation("estleg:Annotation_OK_NS", "estleg:NS", "Nimeseaduse muutmine", url="op2"),
        ]
    )


def _index_for_file():
    return _make_index(
        {"gaasiseadus": "estleg:GAAS", "maagaasiseadus": "estleg:MGS", "nimeseadus": "estleg:NS"}
    )


def test_process_file_dry_run_detects_but_does_not_write(tmp_path: Path):
    idx, iri_title = _index_for_file()
    path = tmp_path / "annotations.jsonld"
    _write_doc(path, _spurious_doc())
    before = path.read_text(encoding="utf-8")

    report = rsa.process_file(path, idx, iri_title, dry_run=True)

    assert report.scanned == 3
    assert report.removed == 1
    assert report.remaining == 2
    assert report.spurious[0].dropped_iri == "estleg:GAAS"
    # Dry-run never touches the file.
    assert path.read_text(encoding="utf-8") == before


def test_process_file_apply_removes_node_updates_header_and_is_idempotent(tmp_path: Path):
    idx, iri_title = _index_for_file()
    path = tmp_path / "annotations.jsonld"
    _write_doc(path, _spurious_doc())

    report = rsa.process_file(path, idx, iri_title, dry_run=False)
    assert report.removed == 1

    doc = json.loads(path.read_text(encoding="utf-8"))
    annotations = [n for n in doc["@graph"] if rsa._is_annotation(n)]
    acts = {rsa._annotates_iri(n) for n in annotations}
    assert len(annotations) == 2
    assert "estleg:GAAS" not in acts          # the leaked shorter act is gone
    assert {"estleg:MGS", "estleg:NS"} <= acts  # legitimate acts retained
    # Header count refreshed to the surviving total.
    assert "(2 annotatsiooni)" in doc["@graph"][0]["rdfs:label"]["@value"]

    # Re-running over the cleaned file removes nothing and leaves it unchanged.
    after = path.read_text(encoding="utf-8")
    report2 = rsa.process_file(path, idx, iri_title, dry_run=False)
    assert report2.removed == 0
    assert path.read_text(encoding="utf-8") == after


def test_process_file_leaves_clean_file_untouched(tmp_path: Path):
    idx, iri_title = _index_for_file()
    clean = _doc(
        [
            _annotation("estleg:Annotation_OK_MGS", "estleg:MGS", "Maagaasiseaduse muutmine", url="opx"),
            _annotation("estleg:Annotation_OK_NS", "estleg:NS", "Nimeseaduse muutmine", url="opy"),
        ]
    )
    path = tmp_path / "annotations.jsonld"
    _write_doc(path, clean)
    before = path.read_text(encoding="utf-8")

    report = rsa.process_file(path, idx, iri_title, dry_run=False)
    assert report.removed == 0
    assert path.read_text(encoding="utf-8") == before


def test_process_file_skips_lfs_pointer(tmp_path: Path):
    idx, iri_title = _index_for_file()
    pointer = tmp_path / "annotations.jsonld"
    pointer.write_text(
        "version https://git-lfs.github.com/spec/v1\n"
        "oid sha256:0123456789abcdef\nsize 123456\n",
        encoding="utf-8",
    )
    try:
        rsa.process_file(pointer, idx, iri_title, dry_run=True)
    except SystemExit as exc:
        assert "Git-LFS pointer" in str(exc)
    else:
        raise AssertionError("expected SystemExit on an LFS pointer stub")


# ---------------------------------------------------------------------------
# Corpus act-title index builder.
# ---------------------------------------------------------------------------

def test_build_iri_title_map(tmp_path: Path):
    peep = {
        "@graph": [
            {
                "@id": "estleg:MGS_Map_2026",
                "@type": ["owl:Ontology"],
                "dc:source": "Maagaasiseadus (RT I, 2003)",
            },
            {"@id": "estleg:MGS_Par_1", "@type": ["estleg:Provision"]},
        ]
    }
    (tmp_path / "maagaasiseadus_peep.json").write_text(
        json.dumps(peep, ensure_ascii=False), encoding="utf-8"
    )
    mapping = rsa.build_iri_title_map(tmp_path)
    # Parenthetical "(RT I, …)" stripped and diacritics normalized by _norm_name.
    assert mapping == {"estleg:MGS_Map_2026": "maagaasiseadus"}

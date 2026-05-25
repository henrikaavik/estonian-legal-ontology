"""Tests for scripts/generate_annotations.py (issue #199).

Locks down the practitioner-annotation ingestion mechanism: the law-name -> act-IRI
index, the curated-seed source, the oiguskantsler.ee listing parser, one-annotation-per-
(opinion, resolved-law) synthesis with dedup, the "skip + record" behaviour for law names
that do not resolve to a corpus node, the coverage report, and SHACL conformance of the
emitted ``estleg:Annotation`` nodes against ``estleg:AnnotationShape``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import generate_annotations as ga
from generate_annotations import (
    Opinion,
    build_annotations_for_opinion,
    build_law_index,
    load_seed_opinions,
    parse_listing_html,
    write_sidecar,
)

_SHAPES_PATH = ga.REPO_ROOT / "shacl" / "estonian_legal_shapes.ttl"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _shacl_conforms(graph_json: dict) -> tuple[bool, str]:
    pyshacl = pytest.importorskip("pyshacl")
    rdflib = pytest.importorskip("rdflib")
    data_graph = rdflib.Graph()
    data_graph.parse(data=json.dumps(graph_json), format="json-ld")
    shapes_graph = rdflib.Graph().parse(str(_SHAPES_PATH), format="turtle")
    conforms, _, msg = pyshacl.validate(data_graph, shacl_graph=shapes_graph, inference="rdfs")
    return conforms, str(msg)


def _write_peep(krr: Path, slug: str, ont_iri: str, *, title: str) -> None:
    """Write a minimal law peep: an owl:Ontology act node (the link target) + a provision."""
    graph = [
        {"@id": ont_iri, "@type": ["estleg:Act", "estleg:Law", "owl:Ontology"],
         "rdfs:label": f"{title} kaardistus", "dc:source": title},
        {"@id": f"estleg:LegalProvision_{slug.upper()}", "@type": ["owl:Class"]},
        {"@id": f"estleg:{slug.upper()}_Par_1",
         "@type": ["owl:NamedIndividual", f"estleg:LegalProvision_{slug.upper()}"],
         "estleg:paragrahv": "§ 1.", "estleg:sourceAct": title},
    ]
    (krr / f"{slug}_peep.json").write_text(
        json.dumps({"@context": {"estleg": ga.CONTEXT["estleg"]}, "@graph": graph}, ensure_ascii=False),
        encoding="utf-8",
    )


def _fixture_corpus(krr: Path) -> None:
    """Two laws an opinion can cite, plus one it can't (no peep) — wired into ``krr``."""
    krr.mkdir(parents=True, exist_ok=True)
    _write_peep(krr, "tsiviilseadustiku_uldosa_seadus", "estleg:TSYS_Map_2026", title="Tsiviilseadustiku üldosa seadus")
    _write_peep(krr, "vorulaoigusseadus", "estleg:VOS_Map_2026", title="Võlaõigusseadus")


def _seed_file(tmp_path: Path, opinions: list[dict]) -> Path:
    path = tmp_path / "seed.json"
    path.write_text(
        json.dumps({"source_name": "Õiguskantsler", "annotation_type": "interpretation", "opinions": opinions},
                   ensure_ascii=False),
        encoding="utf-8",
    )
    return path


# A listing-HTML fixture mirroring the real oiguskantsler.ee markup (header row + two
# opinion rows; one opinion's title names a law in the corpus, the other does not).
_LISTING_HTML = """
<div class="region region-content">
  <div class="views-row">
    <article data-history-node-id="43" class="node node--type-page node--view-mode-header">
      <h1><span class="field field--name-title">Seisukohad</span></h1>
    </article>
  </div>
  <div class="view-content">
    <div class="views-row"><div class="list-item doc">
      <div class="list-title">
        <span class="file file--mime-application-pdf file--application-pdf">
          <a href="/seisukohad-ja-algatused/seisukohad/voorulaoigusseaduse-40-tolgendamine" type="application/pdf"
             title="Voorulaoigusseaduse 40 tolgendamine.pdf">Võlaõigusseaduse § 40 tõlgendamine</a>
          <span class="file-meta">(pdf; 123.45 KB)</span></span>
      </div>
      <div class="list-body"></div>
      <div class="list-meta"><div class="list-tags"><a class="tag" href="/otsing?tags%5B31%5D=31" data-tagid="31">Raha ja vara</a><a class="tag" href="/otsing?tags%5B28%5D=28" data-tagid="28">Hea haldus</a></div>15.03.2024</div>
    </div></div>
    <div class="views-row"><div class="list-item doc">
      <div class="list-title">
        <span class="file file--mime-application-pdf file--application-pdf">
          <a href="/sites/default/files/2024-02/Politsei%20tegevuse%20vaidlustamine.pdf" type="application/pdf"
             title="Politsei tegevuse vaidlustamine.pdf">Politsei tegevuse vaidlustamine</a>
          <span class="file-meta">(pdf; 99.00 KB)</span></span>
      </div>
      <div class="list-body"></div>
      <div class="list-meta"><div class="list-tags"><a class="tag" href="/otsing?tags%5B28%5D=28" data-tagid="28">Hea haldus</a></div>04.02.2024</div>
    </div></div>
  </div>
</div>
"""


# ---------------------------------------------------------------------------
# Law index — name -> act-IRI resolution
# ---------------------------------------------------------------------------


class TestLawIndex:
    def test_resolves_nominative_and_genitive_law_names_to_act_iri(self, tmp_path: Path):
        krr = tmp_path / "krr_outputs"
        _fixture_corpus(krr)
        idx = build_law_index(krr)
        # Nominative dc:source title resolves.
        assert idx.resolve("Võlaõigusseadus") == "estleg:VOS_Map_2026"
        assert idx.resolve("Tsiviilseadustiku üldosa seadus") == "estleg:TSYS_Map_2026"
        # Genitive form (as titles cite laws) also resolves.
        assert idx.resolve("võlaõigusseaduse") == "estleg:VOS_Map_2026"
        # Diacritics + parenthetical suffix tolerated.
        assert idx.resolve("VÕLAÕIGUSSEADUS") == "estleg:VOS_Map_2026"
        # A law not in the corpus does not resolve.
        assert idx.resolve("Mingi olematu seadus") is None

    def test_find_in_title_greedy_and_deduped(self, tmp_path: Path):
        krr = tmp_path / "krr_outputs"
        _fixture_corpus(krr)
        idx = build_law_index(krr)
        # The genitive name occurs in the title -> the act IRI is found.
        assert idx.find_in_title("Võlaõigusseaduse § 40 tõlgendamine") == ["estleg:VOS_Map_2026"]
        # No law name in the title -> nothing.
        assert idx.find_in_title("Politsei tegevuse vaidlustamine") == []
        # Both laws named -> both, in first-occurrence order, no duplicates.
        found = idx.find_in_title("Tsiviilseadustiku üldosa seaduse ja võlaõigusseaduse koostoime")
        assert found == ["estleg:TSYS_Map_2026", "estleg:VOS_Map_2026"]

    def test_no_spurious_substring_match_from_short_abbreviation(self, tmp_path: Path):
        # A short token like "EKS" must not substring-match inside "ülaindEKSiga": only full
        # law names are title-scan keys (regression — see the #199 implementation note).
        krr = tmp_path / "krr_outputs"
        _fixture_corpus(krr)
        _write_peep(krr, "elektroonilise_side_seadus", "estleg:ESS_Map_2026", title="Elektroonilise side seadus")
        idx = build_law_index(krr)
        assert idx.find_in_title("Karistusseadustiku § 381 ülaindeksiga 1 põhiseaduspärasus") == []


# ---------------------------------------------------------------------------
# Curated-seed source parsing
# ---------------------------------------------------------------------------


class TestSeedSource:
    def test_load_seed_opinions_shapes_records(self, tmp_path: Path):
        seed = _seed_file(tmp_path, [
            {"id": "vos-40", "title": "Võlaõigusseaduse § 40 tõlgendamine", "date": "2024-03-15",
             "url": "https://www.oiguskantsler.ee/x/vos-40", "laws": ["Võlaõigusseadus"],
             "summary": "Õiguskantsler selgitas tahteavalduse tõlgendamise põhimõtteid."},
            {"title": "Pealkiri ilma id-ta", "date": "01.02.2024", "laws": [], "summary": ""},
        ])
        ops = load_seed_opinions(seed)
        assert len(ops) == 2
        assert ops[0].opinion_id == "vos-40"
        assert ops[0].law_names == ("Võlaõigusseadus",)
        assert ops[0].date_iso == "2024-03-15"
        assert ops[0].summary
        # id derived from the title when absent; DD.MM.YYYY date accepted.
        assert ops[1].opinion_id and ops[1].opinion_id != ""
        assert ops[1].date_iso == "2024-02-01"

    def test_repo_seed_file_parses(self):
        # The shipped seed file is valid and non-empty.
        ops = load_seed_opinions(ga.SEED_PATH)
        assert ops, "data/annotations/seed_annotations.json should yield opinions"
        for op in ops:
            assert op.title and op.url and op.law_names and op.summary
            assert op.date_iso is None or len(op.date_iso) == 10


# ---------------------------------------------------------------------------
# Live-listing HTML parser
# ---------------------------------------------------------------------------


class TestListingParser:
    def test_parse_listing_html_yields_title_date_url_tags(self):
        ops = parse_listing_html(_LISTING_HTML)
        # The leading header row (no list-item doc) is skipped; two opinion rows parsed.
        assert len(ops) == 2
        first = ops[0]
        assert first.title == "Võlaõigusseaduse § 40 tõlgendamine"
        assert first.date_iso == "2024-03-15"
        assert first.url == "https://www.oiguskantsler.ee/seisukohad-ja-algatused/seisukohad/voorulaoigusseaduse-40-tolgendamine"
        assert first.tags == ("Raha ja vara", "Hea haldus")
        assert first.law_names == ()  # filled by the caller from the title
        assert first.summary == ""    # body is in the PDF
        # PDF-URL slug is URL-decoded (no "%20" leaking into the opinion id).
        second = ops[1]
        assert "%20" not in second.opinion_id and "20" not in second.opinion_id.split("_")
        assert second.date_iso == "2024-02-04"


# ---------------------------------------------------------------------------
# Annotation synthesis (one per (opinion, resolved law); skip + record otherwise)
# ---------------------------------------------------------------------------


class TestAnnotationTextTruncation:
    def test_under_cap_input_is_rstripped(self):
        assert ga._truncate_to_sentence("Lühike tekst.   ", 100) == "Lühike tekst."

    def test_truncates_at_late_sentence_boundary(self):
        text = "A" * 90 + ". " + "B" * 80
        assert ga._truncate_to_sentence(text, 120) == "A" * 90 + "."

    def test_early_sentence_boundary_falls_back_to_space(self):
        text = "Algus. " + ("B" * 30) + " keskel " + ("C" * 80)
        assert ga._truncate_to_sentence(text, 50) == "Algus. " + ("B" * 30) + " keskel…"

    def test_no_space_fallback_keeps_head(self):
        assert ga._truncate_to_sentence("A" * 100, 20) == ("A" * 20) + "…"

    @pytest.mark.parametrize(
        ("fragment", "continuation"),
        [
            ("§ 47 lg 1. p 3", "kohaselt"),
            ("§ 47 p 3. lg 2", "järgi"),
            ("vt. Riigikohtu praktikat", "täpsemalt"),
            ("nt. avalduse läbivaatamist", "hiljem"),
            ("jt. menetlusosalisi", "eraldi"),
            ("nn. halduspraktikat", "edaspidi"),
        ],
    )
    def test_estonian_citation_abbreviation_boundary_is_not_used(
        self, fragment: str, continuation: str
    ):
        text = (
            ("A" * 70)
            + f" {fragment} {continuation} tuleb asjaolusid hinnata "
            + ("B" * 80)
        )
        max_chars = text.index("tuleb") + len("tuleb")

        result = ga._truncate_to_sentence(text, max_chars)

        assert f"{fragment} {continuation}" in result
        assert result.endswith(f"{continuation}…")

    def test_falls_back_to_prior_valid_sentence_before_false_citation_boundary(self):
        text = (
            ("A" * 90)
            + ". "
            + ("B" * 5)
            + " § 47 lg 1. p 3 kohaselt tuleb asjaolusid hinnata "
            + ("C" * 80)
        )
        max_chars = text.index("kohaselt") + len("kohaselt")

        assert ga._truncate_to_sentence(text, max_chars) == ("A" * 90) + "."


class TestBuildAnnotations:
    def test_opinion_citing_two_laws_yields_one_annotation_each(self, tmp_path: Path):
        krr = tmp_path / "krr_outputs"
        _fixture_corpus(krr)
        idx = build_law_index(krr)
        op = Opinion(
            opinion_id="vos-tsys-koostoime",
            title="Võlaõigusseaduse ja tsiviilseadustiku üldosa seaduse koostoime",
            url="https://www.oiguskantsler.ee/x/vos-tsys",
            date_iso="2024-05-01",
            law_names=("Võlaõigusseadus", "Tsiviilseadustiku üldosa seadus"),
            summary="Õiguskantsleri tõlgenduslik seisukoht kahe seaduse koosmõjust.",
        )
        res = build_annotations_for_opinion(op, idx)
        assert res.unresolved_names == []
        assert set(res.resolved_iris) == {"estleg:VOS_Map_2026", "estleg:TSYS_Map_2026"}
        # Two annotation nodes — one per annotated law; abbrev suffix disambiguates IRIs.
        assert len(res.annotations) == 2
        annotated = {n["estleg:annotates"]["@id"] for n in res.annotations}
        assert annotated == {"estleg:VOS_Map_2026", "estleg:TSYS_Map_2026"}
        for n in res.annotations:
            assert n["@type"] == ["owl:NamedIndividual", "estleg:Annotation"]
            assert n["@id"].startswith("estleg:Annotation_OK_vos")  # opinion-slug prefix
            assert isinstance(n["estleg:annotationText"], str) and n["estleg:annotationText"].strip()
            assert n["estleg:annotationType"] == "interpretation"
            assert n["estleg:annotationSource"] == "Õiguskantsler"
            assert n["estleg:annotationSourceUrl"] == {"@value": op.url, "@type": "xsd:anyURI"}
            assert n["estleg:annotationDate"] == {"@value": "2024-05-01", "@type": "xsd:date"}
        # Distinct @ids (the per-law abbrev suffix).
        assert len({n["@id"] for n in res.annotations}) == 2

    def test_single_law_opinion_iri_has_no_abbrev_suffix(self, tmp_path: Path):
        krr = tmp_path / "krr_outputs"
        _fixture_corpus(krr)
        idx = build_law_index(krr)
        op = Opinion("vos-40", "Võlaõigusseaduse § 40 tõlgendamine", "https://x/vos-40", "2024-03-15",
                     ("Võlaõigusseadus",), "Tõlgenduslik seisukoht.")
        res = build_annotations_for_opinion(op, idx)
        assert len(res.annotations) == 1
        assert res.annotations[0]["@id"] == "estleg:Annotation_OK_vos40"
        assert res.annotations[0]["estleg:annotates"] == {"@id": "estleg:VOS_Map_2026"}

    def test_unresolvable_law_is_skipped_and_recorded(self, tmp_path: Path):
        krr = tmp_path / "krr_outputs"
        _fixture_corpus(krr)
        idx = build_law_index(krr)
        op = Opinion("olematu", "Mingi olematu seaduse tõlgendamine", "https://x/olematu", "2024-01-01",
                     ("Mingi olematu seadus",), "Sisu.")
        res = build_annotations_for_opinion(op, idx)
        assert res.annotations == []                       # no dangling estleg:annotates
        assert res.unresolved_names == ["Mingi olematu seadus"]
        assert res.resolved_iris == []

    def test_scrape_opinion_resolves_law_from_title(self, tmp_path: Path):
        krr = tmp_path / "krr_outputs"
        _fixture_corpus(krr)
        idx = build_law_index(krr)
        # Scrape-source opinion: no explicit law_names; the title names a law.
        op = Opinion("vos-40-scrape", "Võlaõigusseaduse § 40 tõlgendamine", "https://x/vos-40", "2024-03-15",
                     (), "", tags=("Raha ja vara",))
        res = build_annotations_for_opinion(op, idx)
        assert len(res.annotations) == 1
        assert res.annotations[0]["estleg:annotates"] == {"@id": "estleg:VOS_Map_2026"}
        # The annotationText surfaces the topic tags when there is no PDF-body summary.
        assert "Raha ja vara" in res.annotations[0]["estleg:annotationText"]

    def test_scrape_opinion_with_no_law_in_title_yields_nothing(self, tmp_path: Path):
        krr = tmp_path / "krr_outputs"
        _fixture_corpus(krr)
        idx = build_law_index(krr)
        op = Opinion("politsei", "Politsei tegevuse vaidlustamine", "https://x/politsei", "2024-02-04", (), "")
        res = build_annotations_for_opinion(op, idx)
        assert res.annotations == []
        assert res.resolved_iris == []
        assert res.unresolved_names  # records "no law name resolvable from title"

    def test_annotates_targets_resolve_to_corpus_nodes(self, tmp_path: Path):
        # Every estleg:annotates target must be a real node @id somewhere in the fixture corpus.
        krr = tmp_path / "krr_outputs"
        _fixture_corpus(krr)
        idx = build_law_index(krr)
        op = Opinion("vos-tsys", "Võlaõigusseaduse ja tsiviilseadustiku üldosa seaduse koostoime",
                     "https://x/vos-tsys", "2024-05-01",
                     ("Võlaõigusseadus", "Tsiviilseadustiku üldosa seadus"), "Sisu.")
        res = build_annotations_for_opinion(op, idx)
        corpus_ids = set()
        for peep in krr.glob("*_peep.json"):
            doc = json.loads(peep.read_text(encoding="utf-8"))
            corpus_ids.update(n["@id"] for n in doc["@graph"] if isinstance(n, dict) and isinstance(n.get("@id"), str))
        for n in res.annotations:
            assert n["estleg:annotates"]["@id"] in corpus_ids


# ---------------------------------------------------------------------------
# Sidecar writing
# ---------------------------------------------------------------------------


def test_write_sidecar_shape_and_context(tmp_path: Path):
    out_path = tmp_path / "annotations" / "oiguskantsler_seisukohad.jsonld"
    nodes = [
        {"@id": "estleg:Annotation_OK_x", "@type": ["owl:NamedIndividual", "estleg:Annotation"],
         "estleg:annotates": {"@id": "estleg:VOS_Map_2026"}, "estleg:annotationText": "tekst",
         "estleg:annotationType": "interpretation", "estleg:annotationSource": "Õiguskantsler"},
    ]
    written = write_sidecar(nodes, out_path=out_path)
    assert written == out_path
    doc = json.loads(out_path.read_text(encoding="utf-8"))
    assert doc["@context"]["estleg"] == ga.CONTEXT["estleg"]
    graph = doc["@graph"]
    # First node is the owl:Ontology header; the rest are Annotation nodes.
    assert graph[0]["@type"] == ["owl:Ontology"]
    assert graph[0]["@id"] == "estleg:Annotations_Oiguskantsler_Map"
    assert graph[0]["dc:source"] == "Õiguskantsler"
    assert not any(k.startswith("estleg:total") for k in graph[0])  # no undefined count predicate
    assert all("estleg:Annotation" in n["@type"] for n in graph[1:])


# ---------------------------------------------------------------------------
# SHACL conformance against estleg:AnnotationShape
# ---------------------------------------------------------------------------


def _well_formed_annotation() -> dict:
    return {
        "@id": "estleg:Annotation_OK_vos40",
        "@type": ["owl:NamedIndividual", "estleg:Annotation"],
        "estleg:annotates": {"@id": "estleg:VOS_Map_2026"},
        "estleg:annotationText": "Õiguskantsler selgitas tahteavalduse tõlgendamise põhimõtteid.",
        "estleg:annotationType": "interpretation",
        "estleg:annotationSource": "Õiguskantsler",
        "estleg:annotationSourceUrl": {"@value": "https://www.oiguskantsler.ee/x/vos-40", "@type": "xsd:anyURI"},
        "estleg:annotationDate": {"@value": "2024-03-15", "@type": "xsd:date"},
    }


def test_shacl_accepts_well_formed_annotation():
    graph_json = {"@context": dict(ga.CONTEXT), "@graph": [_well_formed_annotation()]}
    conforms, msg = _shacl_conforms(graph_json)
    assert conforms, msg


@pytest.mark.parametrize("drop_prop", ["estleg:annotates", "estleg:annotationText", "estleg:annotationType"])
def test_shacl_rejects_annotation_missing_required_property(drop_prop: str):
    node = _well_formed_annotation()
    node.pop(drop_prop)
    graph_json = {"@context": dict(ga.CONTEXT), "@graph": [node]}
    conforms, _ = _shacl_conforms(graph_json)
    assert not conforms


def test_shacl_rejects_annotation_type_outside_enum():
    node = _well_formed_annotation()
    node["estleg:annotationType"] = "something_else"
    graph_json = {"@context": dict(ga.CONTEXT), "@graph": [node]}
    conforms, _ = _shacl_conforms(graph_json)
    assert not conforms


def test_shacl_rejects_literal_annotates():
    node = _well_formed_annotation()
    node["estleg:annotates"] = "estleg:VOS_Map_2026"  # a literal string, not an IRI object
    graph_json = {"@context": dict(ga.CONTEXT), "@graph": [node]}
    conforms, _ = _shacl_conforms(graph_json)
    assert not conforms


# ---------------------------------------------------------------------------
# End-to-end run() with a fixture corpus + seed file
# ---------------------------------------------------------------------------


def test_run_with_seed_writes_sidecar_and_coverage(tmp_path: Path):
    krr = tmp_path / "krr_outputs"
    _fixture_corpus(krr)
    seed = _seed_file(tmp_path, [
        {"id": "vos-40", "title": "Võlaõigusseaduse § 40 tõlgendamine", "date": "2024-03-15",
         "url": "https://www.oiguskantsler.ee/x/vos-40", "laws": ["Võlaõigusseadus"],
         "summary": "Tahteavalduse tõlgendamine: arvestada tuleb poolte tegelikku tahet."},
        {"id": "vos-tsys", "title": "Võlaõigusseaduse ja tsiviilseadustiku üldosa seaduse koostoime",
         "date": "2024-05-01", "url": "https://www.oiguskantsler.ee/x/vos-tsys",
         "laws": ["Võlaõigusseadus", "Tsiviilseadustiku üldosa seadus"],
         "summary": "Kahe seaduse koosmõju tõlgendamine."},
        {"id": "olematu", "title": "Olematu seaduse tõlgendamine", "date": "2024-01-01",
         "url": "https://www.oiguskantsler.ee/x/olematu", "laws": ["Mingi olematu seadus"],
         "summary": "Sisu."},
    ])
    out_path = krr / "annotations" / "oiguskantsler_seisukohad.jsonld"
    cov_path = krr / "reports" / "kov" / "extract_annotations_coverage.json"
    rc = ga.run(scrape=False, limit=10, seed_path=seed, krr_dir=krr, out_path=out_path,
                coverage_path=cov_path, cache_dir=None)
    assert rc == 0

    doc = json.loads(out_path.read_text(encoding="utf-8"))
    ann_nodes = [n for n in doc["@graph"] if "estleg:Annotation" in n.get("@type", [])]
    # vos-40: 1 ; vos-tsys: 2 ; olematu: 0 -> 3 annotation nodes.
    assert len(ann_nodes) == 3
    targets = {n["estleg:annotates"]["@id"] for n in ann_nodes}
    assert targets == {"estleg:VOS_Map_2026", "estleg:TSYS_Map_2026"}
    # Every annotates target is a real corpus node @id.
    corpus_ids = set()
    for peep in krr.glob("*_peep.json"):
        d = json.loads(peep.read_text(encoding="utf-8"))
        corpus_ids.update(n["@id"] for n in d["@graph"] if isinstance(n, dict) and isinstance(n.get("@id"), str))
    assert targets <= corpus_ids

    cov = json.loads(cov_path.read_text(encoding="utf-8"))
    assert cov["pipeline"] == "extract_annotations"
    assert cov["files_processed"] == 3              # opinions processed
    assert cov["files_with_output"] == 2            # opinions that produced ≥1 annotation
    assert cov["files_skipped"] == 1                # the olematu opinion
    assert cov["triples_emitted"] == 3              # estleg:Annotation node count
    assert cov["unresolved_references"] == 1        # one law name didn't resolve
    assert "law_name_not_resolved_to_corpus_node" in cov["skip_reasons"]


def test_run_limit_zero_writes_zeroed_coverage_and_no_sidecar(tmp_path: Path):
    krr = tmp_path / "krr_outputs"
    _fixture_corpus(krr)
    out_path = krr / "annotations" / "oiguskantsler_seisukohad.jsonld"
    cov_path = krr / "reports" / "kov" / "extract_annotations_coverage.json"
    rc = ga.run(scrape=False, limit=0, seed_path=ga.SEED_PATH, krr_dir=krr, out_path=out_path,
                coverage_path=cov_path, cache_dir=None)
    assert rc == 0
    assert not out_path.exists()
    cov = json.loads(cov_path.read_text(encoding="utf-8"))
    assert cov["files_processed"] == 0 and cov["triples_emitted"] == 0


def test_run_scrape_does_not_hit_network_with_limit_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    krr = tmp_path / "krr_outputs"
    _fixture_corpus(krr)

    def _boom(*a, **k):  # noqa: ANN001
        raise AssertionError("--limit 0 must not hit the network")

    monkeypatch.setattr(ga, "_fetch_url", _boom)
    rc = ga.run(scrape=True, limit=0, krr_dir=krr,
                out_path=krr / "annotations" / "oiguskantsler_seisukohad.jsonld",
                coverage_path=krr / "reports" / "kov" / "extract_annotations_coverage.json",
                cache_dir=None)
    assert rc == 0


def test_run_scrape_with_mocked_listing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    krr = tmp_path / "krr_outputs"
    _fixture_corpus(krr)
    pages = {ga.SEISUKOHAD_LISTING_URL: _LISTING_HTML}

    def _fake_fetch(url, **k):  # noqa: ANN001
        return pages.get(url)  # page 0 only; page 1 returns None -> stop

    monkeypatch.setattr(ga, "_fetch_url", _fake_fetch)
    out_path = krr / "annotations" / "oiguskantsler_seisukohad.jsonld"
    cov_path = krr / "reports" / "kov" / "extract_annotations_coverage.json"
    rc = ga.run(scrape=True, limit=5, krr_dir=krr, out_path=out_path, coverage_path=cov_path, cache_dir=None)
    assert rc == 0
    doc = json.loads(out_path.read_text(encoding="utf-8"))
    ann_nodes = [n for n in doc["@graph"] if "estleg:Annotation" in n.get("@type", [])]
    # Only the "Võlaõigusseaduse § 40 tõlgendamine" opinion resolves a law from its title.
    assert len(ann_nodes) == 1
    assert ann_nodes[0]["estleg:annotates"] == {"@id": "estleg:VOS_Map_2026"}
    assert ann_nodes[0]["estleg:annotationDate"] == {"@value": "2024-03-15", "@type": "xsd:date"}
    cov = json.loads(cov_path.read_text(encoding="utf-8"))
    assert cov["files_processed"] == 2 and cov["files_with_output"] == 1 and cov["triples_emitted"] == 1


def test_pdf_text_layer_probe_reports_ocr_recommendation():
    opinions = [
        ga.Opinion(
            opinion_id="usable",
            title="Usable PDF",
            url="https://www.oiguskantsler.ee/usable.pdf",
            date_iso=None,
            law_names=(),
            summary="",
        ),
        ga.Opinion(
            opinion_id="scan",
            title="Scanned PDF",
            url="https://www.oiguskantsler.ee/scan.pdf",
            date_iso=None,
            law_names=(),
            summary="",
        ),
    ]

    def _fetcher(url, **_kwargs):  # noqa: ANN001
        return url.encode("utf-8")

    def _extractor(pdf_bytes):  # noqa: ANN001
        if b"usable" in pdf_bytes:
            return "Õiguskantsleri seisukoha tekst. " * 30, None
        return "", None

    report = ga.probe_pdf_text_layers(
        opinions,
        sample_size=2,
        fetcher=_fetcher,
        extractor=_extractor,
        cache_dir=None,
        sleep=0,
    )

    assert report["sampled"] == 2
    assert report["usable_text_layer_count"] == 1
    assert report["usable_text_layer_ratio"] == 0.5
    assert report["recommendation"] == "evaluate_ocr_before_full_ingestion"


def test_pdf_text_layer_probe_skips_non_pdf_urls():
    opinions = [
        ga.Opinion(
            opinion_id="html",
            title="HTML detail page",
            url="https://www.oiguskantsler.ee/seisukohad/detail",
            date_iso=None,
            law_names=(),
            summary="",
        ),
        ga.Opinion(
            opinion_id="one",
            title="PDF one",
            url="https://www.oiguskantsler.ee/one.pdf",
            date_iso=None,
            law_names=(),
            summary="",
        ),
        ga.Opinion(
            opinion_id="two",
            title="PDF two",
            url="https://www.oiguskantsler.ee/two.pdf?download=1",
            date_iso=None,
            law_names=(),
            summary="",
        ),
    ]
    fetched: list[str] = []

    def _fetcher(url, **_kwargs):  # noqa: ANN001
        fetched.append(url)
        return url.encode("utf-8")

    def _extractor(_pdf_bytes):  # noqa: ANN001
        return "Õiguskantsleri seisukoha tekst. " * 30, None

    report = ga.probe_pdf_text_layers(
        opinions,
        sample_size=2,
        fetcher=_fetcher,
        extractor=_extractor,
        cache_dir=None,
        sleep=0,
    )

    assert report["sampled"] == 2
    assert report["non_pdf_urls_skipped"] == 1
    assert fetched == [
        "https://www.oiguskantsler.ee/one.pdf",
        "https://www.oiguskantsler.ee/two.pdf?download=1",
    ]

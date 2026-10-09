"""#711 contract gate: the three-valued transposition status on the shipped corpus.

Executable acceptance checks for the monitoring product: every directive with
a deadline carries exactly one of the three values, there is no "not
transposed" value anywhere, the gap CSV agrees with the directives peep, and
the vocabulary + SHACL declare the same closed value set.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import pytest

from estleg import generate_transposition_mapping as mod

REPO_ROOT = Path(__file__).resolve().parent.parent
KRR = REPO_ROOT / "krr_outputs"
DIRECTIVES = KRR / "eurlex" / "eurlex_directives_peep.json"
GAP_CSV = KRR / "exports" / "transposition_gap.csv"
SHAPES = REPO_ROOT / "shacl" / "estonian_legal_shapes.ttl"
VOCAB = KRR / "controlled_vocabulary.jsonld"
ALLOWED = {"transposed", "no_measure_required", "no_evidence_in_corpus"}


@pytest.fixture(scope="module")
def directives() -> list[dict]:
    return json.loads(DIRECTIVES.read_text(encoding="utf-8"))["@graph"]


@pytest.fixture(scope="module")
def gap_rows() -> list[dict]:
    with open(GAP_CSV, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _deadline(node: dict) -> str:
    raw = node.get("estleg:transpositionDeadline")
    return raw.get("@value", "") if isinstance(raw, dict) else (raw or "")


def test_every_deadline_directive_has_a_three_valued_status(directives):
    with_deadline = [n for n in directives if _deadline(n)]
    assert len(with_deadline) >= 2599
    for node in with_deadline:
        assert node.get("estleg:transpositionStatus") in ALLOWED, node["@id"]


def test_status_values_are_closed_and_consistent_with_edges(directives):
    for node in directives:
        status = node.get("estleg:transpositionStatus")
        if status is None:
            continue
        assert status in ALLOWED, (node["@id"], status)
        if node.get("estleg:transposedBy"):
            assert status == "transposed", node["@id"]


def test_naive_infringement_query_is_not_the_product(directives):
    """deadline past ∧ ¬transposedBy (2,358 at the build date) must not be read
    as non-transposition: those directives are `no_evidence_in_corpus` unless
    other evidence lifts them, never a 'not transposed' value."""
    as_of = mod.BUILD_EVALUATION_DATE
    naive = [
        n for n in directives
        if _deadline(n) and _deadline(n) < as_of and not n.get("estleg:transposedBy")
    ]
    assert naive, "expected overdue-looking directives in the corpus"
    for node in naive:
        assert node["estleg:transpositionStatus"] in {"no_evidence_in_corpus", "transposed", "no_measure_required"}


def test_gap_csv_matches_the_peep(directives, gap_rows):
    assert tuple(gap_rows[0].keys()) == mod.GAP_CSV_COLUMNS
    by_celex = {n["estleg:celexNumber"]: n for n in directives if n.get("estleg:celexNumber")}
    stamped = {c for c, n in by_celex.items() if n.get("estleg:transpositionStatus")}
    assert {row["celex"] for row in gap_rows} == stamped
    assert [row["celex"] for row in gap_rows] == sorted(row["celex"] for row in gap_rows)
    for row in gap_rows:
        assert row["transposition_status"] in ALLOWED
        assert row["transposition_status"] == by_celex[row["celex"]]["estleg:transpositionStatus"]
        assert row["status_as_of"] == mod.BUILD_EVALUATION_DATE
        if row["transposition_status"] == "transposed":
            assert row["notified_acts"] or row["asserted_acts"]


def test_no_not_transposed_wording_in_outputs():
    pattern = re.compile(r"not[ _-]?transposed", re.IGNORECASE)
    assert not pattern.search(GAP_CSV.read_text(encoding="utf-8"))
    for node in json.loads(DIRECTIVES.read_text(encoding="utf-8"))["@graph"]:
        assert not pattern.search(str(node.get("estleg:transpositionStatus", "")))


def test_report_status_block_matches_the_csv(gap_rows):
    report = json.loads((KRR / "reports" / "transposition_mapping.json").read_text(encoding="utf-8"))
    summary = report["transposition_status"]
    counts = {status: 0 for status in ALLOWED}
    for row in gap_rows:
        counts[row["transposition_status"]] += 1
    assert summary["counts"] == counts
    assert summary["directives_with_status"] == len(gap_rows)
    assert summary["deadline_past_no_evidence_in_corpus"] <= summary["deadline_past_without_transposedBy"]


def test_act_level_unknown_status_is_retired():
    """The pre-#711 act-level 'unknown' would RDFS-type laws as EULegislation
    now that the property's domain is the directive."""
    files = list(KRR.glob("*_peep.json")) + list((KRR / "regulations" / "riik").glob("*_peep.json"))
    offenders = [p.name for p in files if '"estleg:transpositionStatus"' in p.read_text(encoding="utf-8")]
    assert offenders == []


def test_vocabulary_declares_status_and_asserted_property():
    nodes = {n["@id"]: n for n in json.loads(VOCAB.read_text(encoding="utf-8"))["@graph"]}
    status = nodes["estleg:transpositionStatus"]
    assert status["rdfs:domain"] == {"@id": "estleg:EULegislation"}
    assert status["rdfs:range"] == {"@id": "xsd:string"}
    comment = json.dumps(status["rdfs:comment"])
    for value in ALLOWED:
        assert value in comment
    assert "retired" in comment  # full / partial / unknown wording deprecated
    asserted = nodes["estleg:transposesDirectiveAsserted"]
    assert "owl:ObjectProperty" in asserted["@type"]
    assert asserted["rdfs:domain"] == {"@id": "estleg:Act"}
    assert "rdfs:range" not in asserted  # cross-bucket stubs (#563/#570)


def test_shacl_closes_the_value_set():
    rdflib = pytest.importorskip("rdflib")
    from rdflib.collection import Collection
    from rdflib.namespace import SH

    g = rdflib.Graph().parse(SHAPES, format="turtle")
    path = rdflib.URIRef("https://w3id.org/estleg/transpositionStatus")
    shapes = list(g.subjects(SH.path, path))
    assert len(shapes) == 1
    values = {str(v) for v in Collection(g, g.value(shapes[0], SH["in"]))}
    assert values == ALLOWED
    owner = next(g.subjects(SH.property, shapes[0]))
    assert g.value(owner, SH.targetClass) == rdflib.URIRef("https://w3id.org/estleg/EULegislation")

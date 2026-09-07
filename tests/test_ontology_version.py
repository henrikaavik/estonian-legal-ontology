"""Ontology version single-source-of-truth guards (#616).

The published version lives in exactly one place — ``estleg_common.ONTOLOGY_VERSION``
— and is stamped onto the ontology headers so consumers can pin/cite a release.
These tests fail CI if the version drifts between the sources, if the committed
``metadata.jsonld`` header falls out of sync, or if the combined-graph header
node stops being inert for the closure gate.
"""

import json
import tomllib
from pathlib import Path

from estleg import estleg_common

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_pyproject_version_matches_ontology_version():
    """``pyproject.toml`` and ``ONTOLOGY_VERSION`` must not drift."""
    with open(REPO_ROOT / "pyproject.toml", "rb") as f:
        pyproject = tomllib.load(f)
    assert pyproject["project"]["version"] == estleg_common.ONTOLOGY_VERSION, (
        "pyproject.toml version and estleg_common.ONTOLOGY_VERSION have drifted — "
        "bump both together on a release"
    )


def test_metadata_header_carries_current_version():
    """The committed ``metadata.jsonld`` dataset header advertises the version."""
    with open(REPO_ROOT / "metadata.jsonld", encoding="utf-8") as f:
        meta = json.load(f)
    assert meta.get("owl:versionInfo") == estleg_common.ONTOLOGY_VERSION
    version_iri = meta.get("owl:versionIRI", {})
    assert isinstance(version_iri, dict)
    assert version_iri.get("@id", "").endswith(estleg_common.ONTOLOGY_VERSION)
    # The header must declare itself an ontology for versionIRI to be well-typed.
    assert "owl:Ontology" in meta.get("@type", [])


def test_combined_ontology_header_is_wellformed_and_inert():
    """The build-time combined header carries the version and is inert for the
    graph-closure gate (no NON-exempt ``estleg:`` object references to dangle)."""
    header = estleg_common.combined_ontology_header()
    assert header["@id"] == estleg_common.ONTOLOGY_IRI
    assert "owl:Ontology" in header["@type"]
    assert "void:Dataset" in header["@type"]
    assert "dcat:Dataset" in header["@type"]
    assert header["dcterms:license"]["@id"].endswith("/by/4.0/")
    assert header["owl:versionInfo"] == estleg_common.ONTOLOGY_VERSION
    assert header["owl:versionIRI"]["@id"].endswith(estleg_common.ONTOLOGY_VERSION)
    # #516: under the w3id SLASH namespace the version IRI compacts to
    # ``estleg:<version>``, so the header now carries a single estleg: ref —
    # ``owl:versionIRI`` — which is deliberately closure-EXEMPT
    # (COMBINED_CLOSURE_EXEMPT_PREDICATES). It contributes no NON-exempt ref to
    # the graph-closure gate, which is what "inert" means here.
    refs = list(estleg_common.iter_node_estleg_refs(header))
    assert all(
        pred in estleg_common.COMBINED_CLOSURE_EXEMPT_PREDICATES for pred, _ in refs
    ), refs


def test_combined_ontology_header_tracks_version_argument():
    """The helper stamps whatever version it is given (single-source from the
    builder), so a bump flows through without editing the helper."""
    header = estleg_common.combined_ontology_header("9.9.9")
    assert header["owl:versionInfo"] == "9.9.9"
    assert header["owl:versionIRI"]["@id"].endswith("/9.9.9")


def test_version_header_is_exempt_from_combined_parity(tmp_path):
    """The synthesised version header is a build-time extra (like closure stubs),
    so the combined-parity stale-extra gate must not flag it (#616).

    Regression guard: the #616 PR added the header injection but did not rebuild
    combined, so the parity gate only saw the header on the next rebuild — this
    locks in the exemption so it can't silently regress.
    """
    from estleg import validate_all

    # A combined carrying one real source node + the synthesised version header.
    combined = {
        "@graph": [
            estleg_common.combined_ontology_header(),
            {"@id": "estleg:A", "@type": ["estleg:Act"]},
        ]
    }
    combined_path = tmp_path / "combined_ontology.jsonld"
    combined_path.write_text(json.dumps(combined), encoding="utf-8")
    # A real source file on disk so the mtime staleness check has something to stat.
    source_peep = tmp_path / "a_peep.json"
    source_peep.write_text(
        json.dumps({"@graph": [{"@id": "estleg:A", "@type": ["estleg:Act"]}]}),
        encoding="utf-8",
    )

    target = validate_all.CombinedParityTarget(
        label="combined_ontology.jsonld",
        combined_path=combined_path,
        source_files=[tmp_path / "a_peep.json"],
        source_nodes={"estleg:A": {"@id": "estleg:A", "@type": ["estleg:Act"]}},
        allowlist_ids=set(),
        expected_extra_ids={estleg_common.ONTOLOGY_IRI},
    )
    validate_all.errors.clear()
    validate_all._check_combined_parity(target)
    assert validate_all.errors == [], validate_all.errors

    # And WITHOUT the exemption the header WOULD be flagged — proving the test bites.
    target_no_exempt = validate_all.CombinedParityTarget(
        label="combined_ontology.jsonld",
        combined_path=combined_path,
        source_files=[tmp_path / "a_peep.json"],
        source_nodes={"estleg:A": {"@id": "estleg:A", "@type": ["estleg:Act"]}},
        allowlist_ids=set(),
    )
    validate_all.errors.clear()
    validate_all._check_combined_parity(target_no_exempt)
    assert any("stale extra" in e for e in validate_all.errors)
    validate_all.errors.clear()

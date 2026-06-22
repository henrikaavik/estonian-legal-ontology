"""Ontology version single-source-of-truth guards (#616).

The published version lives in exactly one place — ``estleg_common.ONTOLOGY_VERSION``
— and is stamped onto the ontology headers so consumers can pin/cite a release.
These tests fail CI if the version drifts between the sources, if the committed
``metadata.jsonld`` header falls out of sync, or if the combined-graph header
node stops being inert for the closure gate.
"""

import json
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import estleg_common  # noqa: E402

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
    graph-closure gate (no ``estleg:`` object references to dangle)."""
    header = estleg_common.combined_ontology_header()
    assert header["@id"] == estleg_common.ONTOLOGY_IRI
    assert header["@type"] == ["owl:Ontology"]
    assert header["owl:versionInfo"] == estleg_common.ONTOLOGY_VERSION
    assert header["owl:versionIRI"]["@id"].endswith(estleg_common.ONTOLOGY_VERSION)
    # Inert for closure: iter_node_estleg_refs yields nothing.
    assert list(estleg_common.iter_node_estleg_refs(header)) == []


def test_combined_ontology_header_tracks_version_argument():
    """The helper stamps whatever version it is given (single-source from the
    builder), so a bump flows through without editing the helper."""
    header = estleg_common.combined_ontology_header("9.9.9")
    assert header["owl:versionInfo"] == "9.9.9"
    assert header["owl:versionIRI"]["@id"].endswith("/9.9.9")

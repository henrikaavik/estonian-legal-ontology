"""#551: read-only estleg_client (load_law / provisions / sanctions).

Uses the shipped client only — this file does not walk INDEX.json.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from rdflib import Graph

from estleg_client import (
    LawNotFoundError,
    corpus_root,
    load_law,
    provisions_of,
    resolve_iri,
    sanctions_of,
)
from estleg_client.cli import main as estleg_load_main

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def abipol_graph() -> Graph:
    return load_law("abipolitseiniku_seadus")


def test_load_law_abipol_has_provision_and_act_iri(abipol_graph: Graph) -> None:
    assert isinstance(abipol_graph, Graph)
    assert len(abipol_graph) > 0
    provisions = provisions_of(abipol_graph)
    assert provisions, "expected at least one LegalProvision"
    subjects = {str(subject) for subject, _, _ in abipol_graph}
    assert any("ABIPOL" in iri for iri in subjects)


def test_provisions_of_and_sanctions_of(abipol_graph: Graph) -> None:
    provisions = provisions_of(abipol_graph)
    sanctions = sanctions_of(abipol_graph)
    assert any("ABIPOL_Par_" in iri for iri in provisions)
    assert len(sanctions) == 2
    assert all("Sanction" in iri for iri in sanctions)


def test_load_law_by_abbreviation() -> None:
    graph = load_law("ABIPOL")
    assert provisions_of(graph)
    assert any("ABIPOL" in str(subject) for subject, _, _ in graph)


def test_missing_name_raises_clear_exception() -> None:
    missing = "definitely-not-a-real-law-xyz"
    with pytest.raises(LawNotFoundError, match=missing) as caught:
        load_law(missing)
    message = str(caught.value)
    assert "INDEX" in message or "abbreviation" in message


def test_resolve_iri_on_loaded_graph(abipol_graph: Graph) -> None:
    found = resolve_iri("estleg:ABIPOL_Par_1", graph=abipol_graph)
    assert found is not None
    assert found.endswith("ABIPOL_Par_1")
    full = resolve_iri(
        "https://w3id.org/estleg/ABIPOL_Par_1",
        graph=abipol_graph,
    )
    assert full == found


def test_corpus_root_finds_index() -> None:
    root = corpus_root()
    assert (root / "krr_outputs" / "INDEX.json").is_file()
    assert root == REPO


def test_cli_prints_triple_and_provision_counts(capsys: pytest.CaptureFixture[str]) -> None:
    assert estleg_load_main(["abipolitseiniku_seadus"]) == 0
    out = capsys.readouterr().out
    assert "triples:" in out
    assert "provisions:" in out
    triples = int(out.split("triples:", 1)[1].splitlines()[0])
    provisions = int(out.split("provisions:", 1)[1].splitlines()[0])
    assert triples > 0
    assert provisions > 0


def test_pyproject_packages_estleg_client_policy() -> None:
    """#551 consumer package plus #472 producer package."""
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert 'packages = ["estleg", "estleg_client"]' in text
    assert "py-modules = []" not in text
    assert "#551" in text
    assert "#472" in text
    assert 'packages = ["scripts"]' not in text
    assert 'estleg-load = "estleg_client.cli:main"' in text
    assert 'estleg-generate-laws = "estleg.generate_all_laws:main"' in text
    assert 'estleg-run-pipeline = "estleg.run_all_integration:main"' in text
    assert 'estleg-validate = "estleg.validate_all:main"' in text


def test_readme_quick_start_has_client_snippet() -> None:
    text = (REPO / "README.md").read_text(encoding="utf-8")
    after_qs = text.split("## Quick Start", 1)[1]
    five_min, _, _rest = after_qs.partition("### Load surfaces")
    assert "### 5-minute start" in five_min
    assert "from estleg_client import load_law" in five_min

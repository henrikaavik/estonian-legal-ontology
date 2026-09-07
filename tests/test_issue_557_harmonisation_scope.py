"""Issue #557 — honest scope of the harmonisation layer.

This is a documentation/scope gate only. It does **not** claim the
draft-amendsLaw coverage half of #557 is fixed, and it does not rewrite
combined HarmonisationLink stubs.

The shipped layer is comparative NIM measures for LV/LT/FI/SE, not
Estonian article-level transposition.
"""

from __future__ import annotations

from pathlib import Path

from estleg import generate_harmonisation_links as harmonisation

REPO = Path(__file__).resolve().parent.parent


def test_target_countries_are_neighbors_not_estonia() -> None:
    assert set(harmonisation.TARGET_COUNTRIES) == {"LVA", "LTU", "FIN", "SWE"}
    assert "EST" not in harmonisation.TARGET_COUNTRIES


def test_schema_or_readme_says_comparative_neighbor_not_estonian_article() -> None:
    schema = (REPO / "docs" / "SCHEMA_REFERENCE.md").read_text(encoding="utf-8")
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    blob = f"{schema}\n{readme}"
    lower = blob.lower()
    assert "comparative" in lower
    assert "neighbor" in lower or "neighbour" in lower
    assert "not estonian article" in lower


def test_generator_docstring_mentions_latvia_or_lv_and_comparative() -> None:
    doc = harmonisation.__doc__ or ""
    lower = doc.lower()
    assert "comparative" in lower
    assert "latvia" in lower or "lv" in lower

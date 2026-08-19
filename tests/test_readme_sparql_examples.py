"""The four README SPARQL examples must still return rows (#614).

The README ships four ```sparql examples in its "SPARQL query examples"
section and tells consumers these queries work against the corpus. Nothing
ever asserted that they actually return anything, so they could silently
rot — which is exactly the #614 failure: the flagship
``combined_ontology.jsonld`` answers 0 rows for all four because its
court/EU nodes are predicate-less stubs there.

This module parses the four ```sparql blocks straight out of ``README.md``
(so the test tracks the doc rather than a hard-coded copy) and runs each
against the *minimal* rdflib graph it needs.

Crucially it does NOT load the ~8M-triple combined seadusloome surface,
which OOMs and (per #614) lacks the queried predicates anyway. Each
documented query joins onto exactly one committed, non-LFS subcorpus, so we
load just that directory's ``*_peep.json`` files — plus
``controlled_vocabulary.jsonld`` for the queries that read a label off a
controlled-vocabulary node — one file at a time, stopping as soon as a query
yields its first row. Peak graph size stays in the low-hundred-thousand
triples.

Marked ``@pytest.mark.corpus``: like ``test_real_corpus_invariants.py`` these
read the real ``krr_outputs/`` tree, so they are skipped by default
(``tests/conftest.py``) and opt in via ``pytest -m corpus`` in the
LFS-materialised CI tier. Any individual source file that is an
un-materialised git-LFS pointer is skipped via ``validate_all._is_lfs_pointer``
(the same guard the corpus-invariant suite uses); a query whose inputs are
entirely unavailable skips rather than fails.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from estleg import validate_all

REPO_ROOT = Path(__file__).resolve().parent.parent
README = REPO_ROOT / "README.md"
KRR = REPO_ROOT / "krr_outputs"

# Fenced ```sparql blocks in the README, in document order.
_SPARQL_BLOCK = re.compile(r"```sparql\n(.*?)```", re.DOTALL)


def _read_readme_sparql_blocks() -> list[str]:
    """Return SPARQL bodies from the README 'SPARQL query examples' section.

    Other README fences (e.g. the #474 GRAPH inventory query) are not
    counted here — QUERY_SPECS indexes only the four worked examples.
    """
    text = README.read_text(encoding="utf-8")
    marker = "### SPARQL query examples"
    start = text.find(marker)
    section = text[start:] if start >= 0 else text
    next_heading = section.find("\n## ", 1)
    if next_heading > 0:
        section = section[:next_heading]
    return [block.strip() for block in _SPARQL_BLOCK.findall(section)]


@dataclass(frozen=True)
class _QuerySpec:
    """Binds a README ```sparql block to the minimal corpus it joins onto.

    ``index`` is the block's position in the README (0-based). ``body_glob``
    (relative to ``krr_outputs/``) is the subcorpus the query filters over;
    its peep files are loaded one at a time until the query first returns a
    row. ``base`` lists prerequisites always loaded first (e.g. the
    controlled-vocabulary nodes a query reads a ``rdfs:label`` off of).
    ``reverse`` front-loads the body file that satisfies the query so the
    incremental loader can stop after a single file — a pure memory/speed
    knob, never a correctness one (the loop falls through to later files if
    the data ever shifts).
    """

    index: int
    name: str
    body_glob: str
    base: tuple[str, ...] = ()
    reverse: bool = False

    @property
    def body_files(self) -> list[Path]:
        return sorted(KRR.glob(self.body_glob), reverse=self.reverse)

    @property
    def base_files(self) -> list[Path]:
        return [KRR / rel for rel in self.base]


# One spec per README query. None of them touch the combined graph.
QUERY_SPECS: tuple[_QuerySpec, ...] = (
    _QuerySpec(
        index=0,
        name="Supreme Court decisions referencing KarS",
        body_glob="riigikohus/riigikohus_*_peep.json",
        # KarS (in force from 2002) is densest in the most recent years, so
        # newest-first finds a referencing decision in the first file loaded.
        reverse=True,
    ),
    _QuerySpec(
        index=1,
        name="drafts amending karistusseadustik",
        body_glob="eelnoud/*_peep.json",
        # The query joins ?phaseNode rdfs:label, which lives in the vocab.
        base=("controlled_vocabulary.jsonld",),
        reverse=True,
    ),
    _QuerySpec(
        index=2,
        name="EU directives in force",
        body_glob="eurlex/*_peep.json",
        base=("controlled_vocabulary.jsonld",),
    ),
    _QuerySpec(
        index=3,
        name="EU Court of Justice judgments",
        body_glob="curia/*_peep.json",
    ),
)


def _is_loadable(path: Path) -> bool:
    """True when ``path`` exists and is not an un-materialised LFS pointer."""
    return path.is_file() and not validate_all._is_lfs_pointer(path)


def _rows_for(spec: _QuerySpec, query: str) -> list:
    """Build the minimal graph for ``spec`` and return ``query``'s result rows.

    Loads the base prerequisites, then the body peep files one at a time,
    re-running ``query`` after each and stopping at the first file that yields
    a row — so the graph never grows past what the query needs (never the
    combined surface). Skips the test if a prerequisite is unavailable or no
    body file is materialised, rather than failing on a half-populated tree.
    """
    from rdflib import Graph

    base = spec.base_files
    missing_base = [p for p in base if not _is_loadable(p)]
    if missing_base:
        pytest.skip(
            f"{spec.name}: prerequisite artifact missing or an LFS pointer: "
            f"{[p.name for p in missing_base]}"
        )
    body = [p for p in spec.body_files if _is_loadable(p)]
    if not body:
        pytest.skip(
            f"{spec.name}: no materialised peep files under {spec.body_glob}"
        )

    graph = Graph()
    for path in base:
        graph.parse(source=str(path), format="json-ld")
    rows: list = []
    for path in body:
        graph.parse(source=str(path), format="json-ld")
        rows = list(graph.query(query))
        if rows:
            break
    return rows


def test_readme_exposes_exactly_four_sparql_examples():
    """Guard the doc structure the corpus tests index into.

    Cheap (parses README only, no corpus) so it runs in the default tier: if
    the "SPARQL query examples" section gains or loses a block, this fails
    loudly instead of the parametrised corpus test silently mis-indexing.
    """
    blocks = _read_readme_sparql_blocks()
    assert len(blocks) == 4, (
        f"expected 4 ```sparql examples in README.md, found {len(blocks)} "
        "— update QUERY_SPECS to match"
    )
    assert len(QUERY_SPECS) == 4
    assert {spec.index for spec in QUERY_SPECS} == {0, 1, 2, 3}


@pytest.mark.corpus
@pytest.mark.parametrize("spec", QUERY_SPECS, ids=[spec.name for spec in QUERY_SPECS])
def test_readme_sparql_example_returns_rows(spec: _QuerySpec):
    """Each documented README SPARQL example must still match the corpus (#614).

    Asserts > 0 rows so the public examples can't silently rot. A genuine 0
    here is a real #614 regression (the documented query no longer matches the
    shipped data), not something to mask.
    """
    blocks = _read_readme_sparql_blocks()
    assert len(blocks) > spec.index, (
        f"README lost SPARQL example #{spec.index + 1} ({spec.name!r})"
    )
    query = blocks[spec.index]
    rows = _rows_for(spec, query)
    assert len(rows) > 0, (
        f"README SPARQL example #{spec.index + 1} ({spec.name!r}) returned 0 rows "
        f"— the documented query no longer matches the shipped {spec.body_glob} "
        f"corpus (#614). Query:\n{query}"
    )

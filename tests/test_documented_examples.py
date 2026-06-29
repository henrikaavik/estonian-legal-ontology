"""Every documented API_GUIDE / SCHEMA_REFERENCE example must still run green (#506).

``docs/API_GUIDE.md`` and ``docs/SCHEMA_REFERENCE.md`` advertise dozens of
copy-pasteable SPARQL queries and Python loader snippets, but nothing ever
executed them, so they silently rotted: 9 of them returned zero rows or crashed
against the shipped corpus (wrong predicate names, IRIs that were never minted,
lang-tagged literals the data stores plain, and cross-subcorpus joins the prose
never told you to co-load). This module parses every fenced ``sparql`` / ``python``
block straight out of those two docs (so the gate tracks the docs, not a
hard-coded copy) and runs each one, failing on a zero-row SPARQL result or a
Python exception. A genuine 0 here is a real regression — the documented example
no longer matches the shipped data.

Design (mirrors ``tests/test_readme_sparql_examples.py``):

* **No full combined graph.** Loading the ~3 GB ``combined_ontology.jsonld`` with
  RDFS inference OOMs locally, so every SPARQL example is run against the
  *minimal* committed subcorpus it joins onto — the explicit ``files`` a query
  needs, then (as a resilience fallback) the matching ``globs`` one file at a
  time until the first row. Peak graph size stays small.
* **Materialised parent types without the combined surface.** The two
  ``?x a estleg:LegalProvision`` examples need the build-time type rollup that
  ships only inside the combined graph (#519). Instead of loading that graph we
  reuse the builder's single source of truth — ``fix_all_issues._materialize_supertypes``
  — to stamp the entailed supertypes onto a 1-file fixture as it is parsed,
  reproducing exactly what combined would answer at a fraction of the cost.
* **LFS / corpus safety.** Any individual input that is an un-materialised git-LFS
  pointer or absent is skipped via ``validate_all._is_lfs_pointer`` (the guard the
  corpus-invariant suite uses). The corpus-touching tests are ``@pytest.mark.corpus``
  (skipped by default per ``tests/conftest.py``; opt in with ``pytest -m corpus``
  in the LFS-materialised CI tier). The cheap structural guards run by default.
"""

from __future__ import annotations

import json
import os
import re
from contextlib import redirect_stdout
from dataclasses import dataclass
from pathlib import Path

import pytest

import validate_all
from fix_all_issues import _materialize_supertypes

REPO_ROOT = Path(__file__).resolve().parent.parent
KRR = REPO_ROOT / "krr_outputs"
DOCS = REPO_ROOT / "docs"

# Fenced ```lang blocks, in document order. ``\w*`` so an un-tagged ``` fence
# (none of the ones we execute) still parses without swallowing the next block.
_FENCE = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)

# Canonical prefixes prepended to every SPARQL example before execution. A few
# documented blocks (the KOV Layer-1 examples) historically omitted PREFIX lines;
# re-declaring a prefix to the same IRI is a harmless no-op in rdflib, so this is
# safe for blocks that already declare their own.
_PREFIXES = """
PREFIX estleg: <https://data.riik.ee/ontology/estleg#>
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX owl: <http://www.w3.org/2002/07/owl#>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
PREFIX dc: <http://purl.org/dc/elements/1.1/>
PREFIX dcterms: <http://purl.org/dc/terms/>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
"""


@dataclass(frozen=True)
class _Block:
    """One fenced code block extracted from a doc."""

    doc: str
    lang: str
    line: int
    body: str


def _extract_blocks(doc_name: str) -> list[_Block]:
    """Return the fenced blocks of ``doc_name`` in document order.

    Leading ``> `` blockquote markers are stripped so a fenced example nested in
    a blockquote (e.g. the ``estleg:sectionNumber`` query) executes verbatim.
    """
    text = (DOCS / doc_name).read_text(encoding="utf-8")
    blocks: list[_Block] = []
    for match in _FENCE.finditer(text):
        lang = match.group(1) or "none"
        line = text[: match.start()].count("\n") + 1
        body = re.sub(r"(?m)^> ?", "", match.group(2)).strip()
        blocks.append(_Block(doc=doc_name, lang=lang, line=line, body=body))
    return blocks


def _blocks(doc_name: str, lang: str) -> list[_Block]:
    return [b for b in _extract_blocks(doc_name) if b.lang == lang]


@dataclass(frozen=True)
class _SparqlSpec:
    """Binds a documented SPARQL block to the minimal corpus it joins onto.

    ``marker`` is a substring that uniquely identifies the block within its doc
    (robust to reordering). ``files`` are explicit prerequisites loaded first, in
    order, relative to ``krr_outputs/``. ``globs`` (also relative to
    ``krr_outputs/``) are walked one file at a time, re-running the query after
    each, only if ``files`` did not already yield a row — a pure speed/resilience
    knob, never a correctness one. ``rollup`` applies the #519 supertype
    materialisation as the file is parsed (needed for the bare
    ``a estleg:LegalProvision`` queries, whose per-file inputs carry only the leaf
    ``LegalProvision_<law>`` type). ``reverse`` orders the globs newest-first.
    """

    marker: str
    name: str
    files: tuple[str, ...] = ()
    globs: tuple[str, ...] = ()
    rollup: bool = False
    reverse: bool = False


# One spec per documented SPARQL block. None of them touch combined_ontology.jsonld.
SPARQL_SPECS: dict[str, tuple[_SparqlSpec, ...]] = {
    "API_GUIDE.md": (
        _SparqlSpec(
            marker="estleg:requestedCluster estleg:Cluster_PKS_1",
            name="provisions for a topic cluster",
            files=("perekonnaseadus_peep.json",),
            rollup=True,
        ),
        _SparqlSpec(
            marker="?target estleg:referencedBy ?source",
            name="cross-references between laws",
            files=("perekonnaseadus_peep.json",),
            rollup=True,
        ),
        _SparqlSpec(
            marker="?decision a estleg:CourtDecision ;",
            name="court decision to provision links",
            files=("riigikohus/riigikohus_2025_peep.json",),
            globs=("riigikohus/riigikohus_*_peep.json",),
            reverse=True,
        ),
        _SparqlSpec(
            marker="estleg:entryIntoForce ?entry",
            name="government regulations in force",
            files=(
                "regulations/riik/"
                "abielu_solmimise_ja_lahutamise_kinnitamise_padevuse_saamisek_t343620_peep.json",
            ),
            globs=("regulations/riik/*_peep.json",),
        ),
        _SparqlSpec(
            marker='CONTAINS(LCASE(STR(?affected)), "perekonnaseadus")',
            name="drafts impacting a specific law",
            files=("controlled_vocabulary.jsonld",),
            globs=("eelnoud/*_peep.json",),
            reverse=True,
        ),
        _SparqlSpec(
            marker="?directive a estleg:EULegislation .",
            name="EU directives transposed",
            files=(
                "eurlex/eurlex_directives_peep.json",
                "alkoholi_tubaka_kutuse_ja_elektriaktsiisi_seadus_peep.json",
            ),
        ),
        _SparqlSpec(
            marker="estleg:applicableProvision ?provision ;",
            name="sanctions by penalty type",
            files=("sanctions/sanctions_abipolitseiniku_seadus.json",),
            globs=("sanctions/sanctions_*.json",),
        ),
        _SparqlSpec(
            marker="?institution a estleg:Institution .",
            name="institutional competence",
            files=(
                "maakorraldusseadus_peep.json",
                "institutions/institution_maa_ja_ruumiamet.json",
            ),
        ),
        _SparqlSpec(
            marker="estleg:normativeType estleg:NormType_Obligation ;",
            name="deontic classification",
            files=("toolepingu_seadus_peep.json",),
        ),
        _SparqlSpec(
            marker="estleg:amends ?amended ;",
            name="amendment history",
            files=(
                "amendments/"
                "amendments_2024_2025_2025_2026_ja_2026_2027_oppeaasta_koolivaheajad_t1057801.json",
            ),
            globs=("amendments/amendments_*.json",),
        ),
        _SparqlSpec(
            marker="<http://eurovoc.europa.eu/4050>",
            name="EuroVoc subject classification (forward)",
            files=("alaealise_mojutusvahendite_seadus_peep.json",),
        ),
        _SparqlSpec(
            marker='dcterms:title "Töölepingu seadus"',
            name="EuroVoc subject classification (reverse)",
            files=("toolepingu_seadus_peep.json",),
        ),
    ),
    "SCHEMA_REFERENCE.md": (
        _SparqlSpec(
            marker='CONTAINS(LCASE(?lawName), "karistusseadustik")',
            name="drafts amending a specific law",
            files=("controlled_vocabulary.jsonld",),
            globs=("eelnoud/*_peep.json",),
            reverse=True,
        ),
        _SparqlSpec(
            marker="estleg:legislativePhase estleg:Phase_PublicConsultation ;",
            name="drafts in public consultation",
            files=("eelnoud/eelnoud_publicconsultation_peep.json",),
            globs=("eelnoud/*_peep.json",),
        ),
        _SparqlSpec(
            marker="estleg:caseType estleg:CaseType_ConstitutionalReview ;",
            name="court decisions by case type and year",
            files=("riigikohus/riigikohus_2026_peep.json",),
            globs=("riigikohus/riigikohus_*_peep.json",),
            reverse=True,
        ),
        _SparqlSpec(
            marker="?reg a estleg:GovernmentRegulation ;",
            name="regulations issued by Vabariigi Valitsus",
            files=(
                "regulations/riik/"
                "abielu_solmimise_ja_lahutamise_kinnitamise_padevuse_saamisek_t343620_peep.json",
            ),
            globs=("regulations/riik/*_peep.json",),
        ),
        _SparqlSpec(
            marker='"töölepingu seaduse"',
            name="regulations whose preamble cites a law",
            files=(
                "regulations/riik/"
                "haridustootajate_ametikohtade_loetelu_kus_antakse_kuni_56_ka_t336333_peep.json",
            ),
            globs=("regulations/riik/*_peep.json",),
        ),
        _SparqlSpec(
            marker="estleg:euDocumentType estleg:EUDocType_Directive ;",
            name="EU directives in force",
            files=("eurlex/eurlex_directives_peep.json",),
        ),
        _SparqlSpec(
            marker="estleg:euInstitution estleg:EUInst_EuropeanParliament ;",
            name="EU regulations by institution",
            files=("eurlex/eurlex_regulations_peep.json",),
        ),
        _SparqlSpec(
            marker="?decision a estleg:EUCourtDecision ;",
            name="EU court judgments by date",
            files=("curia/curia_judgments_peep.json",),
            globs=("curia/*_peep.json",),
        ),
        _SparqlSpec(
            marker="?s estleg:sectionNumber ?n",
            name="section number predicate targeting",
            files=("volaigusseadus_osa11_peep.json",),
        ),
        _SparqlSpec(
            marker="estleg:versionValidFrom ?from",
            name="provision text as of a date",
            files=(
                "kohaliku_omavalitsuse_volikogu_valimise_seadus_peep.json",
                "provision_versions/kohaliku_omavalitsuse_volikogu_valimise_seadus.jsonld",
            ),
        ),
        _SparqlSpec(
            marker="estleg:annotates       estleg:KOKS_Map_2026",
            name="annotations about a legal entity",
            files=("annotations/oiguskantsler_seisukohad.jsonld",),
        ),
        _SparqlSpec(
            marker="estleg:references estleg:KARIST_2_Osa2_Par_279",
            name="provisions referencing a specific paragraph",
            files=("autoveoseadus_peep.json",),
        ),
        _SparqlSpec(
            marker="estleg:interpretsLaw estleg:KARIST_2_Osa1_Par_12",
            name="court decisions interpreting a provision",
            files=("riigikohus/riigikohus_2025_peep.json",),
            globs=("riigikohus/riigikohus_*_peep.json",),
            reverse=True,
        ),
        _SparqlSpec(
            marker="estleg:transposesDirective estleg:EU_32009L0028",
            name="laws transposing a specific EU directive",
            files=("elektrituruseadus_peep.json",),
        ),
        _SparqlSpec(
            marker='estleg:sourceAct "Töölepingu seadus" ;',
            name="obligations in a specific law",
            files=("toolepingu_seadus_peep.json",),
        ),
        _SparqlSpec(
            marker="dcterms:subject <http://eurovoc.europa.eu/4050> ;",
            name="acts on a specific EuroVoc topic",
            files=("alaealise_mojutusvahendite_seadus_peep.json",),
        ),
        _SparqlSpec(
            marker="?provision estleg:hasSanction ?sanction ;",
            name="sanctions for a type of conduct",
            files=(
                "autoveoseadus_peep.json",
                "sanctions/sanctions_autoveoseadus.json",
            ),
        ),
        _SparqlSpec(
            marker="estleg:Municipality_EHAK_0784",
            name="Tallinn-issued municipal regulations",
            globs=("regulations/kov/tallinna_*/*_peep.json",),
        ),
        _SparqlSpec(
            marker='estleg:titleNormalized "jaatmehoolduseeskiri"',
            name="normalized title across municipalities",
            globs=("regulations/kov/polva_vallavolikogu/*_peep.json",),
        ),
    ),
}


def _is_loadable(path: Path) -> bool:
    """True when ``path`` exists and is not an un-materialised LFS pointer."""
    return path.is_file() and not validate_all._is_lfs_pointer(path)


def _parse(path: Path, graph, rollup: bool) -> None:
    """Parse ``path`` into ``graph``, optionally materialising entailed supertypes.

    With ``rollup`` the JSON-LD is loaded, every node's ``@type`` list is closed
    over the subclass hierarchy via ``fix_all_issues._materialize_supertypes``
    (the builder's own function), then the patched document is parsed — so the
    fixture answers ``?x a estleg:LegalProvision`` exactly as the shipped combined
    graph would, without loading that graph.
    """
    if not rollup:
        graph.parse(source=str(path), format="json-ld")
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    nodes = data.get("@graph") if isinstance(data, dict) else data
    for node in nodes or []:
        if isinstance(node, dict) and isinstance(node.get("@type"), list):
            node["@type"] = _materialize_supertypes(node["@type"])
    graph.parse(data=json.dumps(data), format="json-ld")


def _glob_files(spec: _SparqlSpec) -> list[Path]:
    out: list[Path] = []
    for pattern in spec.globs:
        out.extend(sorted(KRR.glob(pattern), reverse=spec.reverse))
    return out


def _rows_for(spec: _SparqlSpec, query: str) -> list:
    """Build ``spec``'s minimal graph and return ``query``'s result rows.

    Loads the explicit ``files`` first; if they already answer the query we stop
    there. Otherwise the ``globs`` are walked one file at a time until the first
    row appears, so the graph never grows past what the query needs. Skips (rather
    than fails) when a required input is missing or an un-materialised LFS pointer.
    """
    from rdflib import Graph

    base = [KRR / rel for rel in spec.files]
    missing = [p for p in base if not _is_loadable(p)]
    if missing:
        pytest.skip(
            f"{spec.name}: prerequisite missing or an LFS pointer: "
            f"{[p.name for p in missing]}"
        )

    graph = Graph()
    for path in base:
        _parse(path, graph, spec.rollup)
    rows = list(graph.query(query)) if base else []
    if rows:
        return rows

    body = [p for p in _glob_files(spec) if _is_loadable(p)]
    if not base and not body:
        pytest.skip(f"{spec.name}: no materialised inputs for {spec.globs}")
    for path in body:
        _parse(path, graph, spec.rollup)
        rows = list(graph.query(query))
        if rows:
            break
    return rows


def _find_block(doc_name: str, marker: str) -> _Block:
    matches = [b for b in _blocks(doc_name, "sparql") if marker in b.body]
    assert len(matches) == 1, (
        f"marker {marker!r} matched {len(matches)} SPARQL blocks in {doc_name} "
        "(expected exactly 1) — update SPARQL_SPECS"
    )
    return matches[0]


def _sparql_cases() -> list[tuple[str, _SparqlSpec]]:
    return [(doc, spec) for doc, specs in SPARQL_SPECS.items() for spec in specs]


# --------------------------------------------------------------------------- #
# Structural guards (cheap; parse the docs only, run in the default tier).
# --------------------------------------------------------------------------- #


def test_every_sparql_block_has_a_spec():
    """Each documented SPARQL block maps to exactly one spec, and vice versa.

    Cheap (no corpus) so it runs by default: if a doc gains, loses, or rewrites a
    ```sparql example the bijection breaks here loudly, instead of the corpus test
    silently leaving the new example untested.
    """
    for doc_name, specs in SPARQL_SPECS.items():
        blocks = _blocks(doc_name, "sparql")
        # every spec resolves to exactly one block
        matched = [_find_block(doc_name, spec.marker) for spec in specs]
        # every block is claimed by exactly one spec
        assert len(matched) == len(blocks), (
            f"{doc_name}: {len(blocks)} ```sparql blocks but {len(specs)} specs "
            "— add/remove a spec in SPARQL_SPECS"
        )
        assert len({(b.line) for b in matched}) == len(blocks), (
            f"{doc_name}: two specs matched the same SPARQL block"
        )


def test_documented_block_counts():
    """Pin the executable-block inventory so doc edits surface here first."""
    assert len(_blocks("API_GUIDE.md", "sparql")) == 12
    assert len(_blocks("API_GUIDE.md", "python")) == 14
    assert len(_blocks("SCHEMA_REFERENCE.md", "sparql")) == 19
    # SCHEMA_REFERENCE ships data examples as ```json, never executable ```python.
    assert len(_blocks("SCHEMA_REFERENCE.md", "python")) == 0


# --------------------------------------------------------------------------- #
# SPARQL examples must return rows against the shipped corpus.
# --------------------------------------------------------------------------- #


@pytest.mark.corpus
@pytest.mark.parametrize(
    "doc_name,spec",
    _sparql_cases(),
    ids=[f"{doc}:{spec.name}" for doc, spec in _sparql_cases()],
)
def test_documented_sparql_returns_rows(doc_name: str, spec: _SparqlSpec):
    """Every documented SPARQL example must still match the shipped data (#506).

    Asserts > 0 rows so the public examples cannot silently rot. A real 0 means
    the documented query no longer resolves against the corpus it advertises.
    """
    block = _find_block(doc_name, spec.marker)
    query = _PREFIXES + "\n" + block.body
    rows = _rows_for(spec, query)
    assert len(rows) > 0, (
        f"{doc_name} L{block.line} ({spec.name!r}) returned 0 rows — the "
        f"documented query no longer matches the shipped corpus (#506).\n"
        f"Inputs: files={spec.files} globs={spec.globs}\nQuery:\n{block.body}"
    )


# --------------------------------------------------------------------------- #
# Python loader snippets must execute without raising.
# --------------------------------------------------------------------------- #

_PATH_LITERAL = re.compile(r"""["'](krr_outputs/[^"'*]+\.jsonl?d?)["']""")


def _python_cases() -> list[_Block]:
    return _blocks("API_GUIDE.md", "python")


@pytest.mark.corpus
@pytest.mark.parametrize(
    "block",
    _python_cases(),
    ids=[f"API_GUIDE.md:L{b.line}" for b in _python_cases()],
)
def test_documented_python_executes(block: _Block):
    """Every documented Python loader snippet runs without raising (#506).

    The snippets read real ``krr_outputs/`` files with relative paths, so they are
    executed with the repo root as CWD. Any static file literal that is missing or
    an un-materialised LFS pointer skips the case (corpus convention); a genuine
    exception (e.g. the old ``entry.get("file")`` ``AttributeError``) fails.
    """
    for rel in _PATH_LITERAL.findall(block.body):
        path = REPO_ROOT / rel
        if not _is_loadable(path):
            pytest.skip(f"L{block.line}: input missing or LFS pointer: {rel}")

    prev_cwd = Path.cwd()
    os.chdir(REPO_ROOT)
    try:
        with open(os.devnull, "w") as devnull, redirect_stdout(devnull):
            exec(compile(block.body, f"API_GUIDE.md:L{block.line}", "exec"), {})
    finally:
        os.chdir(prev_cwd)

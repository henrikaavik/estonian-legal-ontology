"""#716: estleg_client contract against the committed corpus, docs and packaging.

The count tests pin the client's iterators to the registries the producers
write (EELNOUD_INDEX, RIIGIKOHUS_INDEX, EURLEX_INDEX, REGULATIONS_*_INDEX), so
a loader that silently drops records fails here. The documented ``py`` blocks
in estleg_client/README.md and the API_GUIDE client section are executed.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import re
import tomllib
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import pytest
from rdflib import RDF

import estleg_client
from estleg_client import (
    iter_court_decisions,
    iter_drafts,
    iter_eu_acts,
    iter_regulations,
    load_regulation,
    provision_rows,
    provisions_of,
)

REPO = Path(__file__).resolve().parent.parent
PACKAGING = REPO / "packaging" / "estleg-client"


@pytest.fixture
def root(corpus_krr) -> Path:
    corpus_krr.path("INDEX.json")
    return corpus_krr.root.parent


def test_drafts_iterator_matches_eelnoud_index(corpus_krr, root: Path) -> None:
    index = corpus_krr.read_json("eelnoud/EELNOUD_INDEX.json")
    corpus_krr.path("eelnoud/eelnoud_combined.jsonld")
    rows = list(iter_drafts(root=root))
    assert len(rows) == index["total_drafts"]
    for phase, meta in index["phases"].items():
        assert sum(1 for r in rows if r.phase == phase) == meta["count"], phase
    assert all(r.iri.startswith("https://w3id.org/estleg/Draft_") for r in rows)


def test_decisions_iterator_matches_riigikohus_index(corpus_krr, root: Path) -> None:
    index = corpus_krr.read_json("riigikohus/RIIGIKOHUS_INDEX.json")
    corpus_krr.path("riigikohus/riigikohus_2020_peep.json")
    rows = list(iter_court_decisions(year=2020, root=root))
    assert len(rows) == index["years"]["2020"]
    assert all(r.year == 2020 and r.case_number and r.source_url for r in rows)


def test_eu_iterator_matches_eurlex_lens(corpus_krr, root: Path) -> None:
    index = corpus_krr.read_json("eurlex/EURLEX_INDEX.json")
    corpus_krr.path("eurlex/eurlex_directives_peep.json")
    relevant = list(iter_eu_acts(estonia_relevant=True, root=root))
    assert len(relevant) == index["lens"]["estonia_relevant"]
    directives = sum(1 for _ in iter_eu_acts(doc_type="Directive", root=root))
    assert directives == index["by_type"]["Directive"]["total"]


def test_kov_issuer_iterator_matches_registry(corpus_krr, root: Path) -> None:
    index = corpus_krr.read_json("regulations/kov/REGULATIONS_KOV_INDEX.json")
    issuer = "Tallinna Linnavalitsus"
    rows = list(iter_regulations(kov=True, issuer=issuer, root=root))
    assert len(rows) == index["byIssuer"][issuer]
    assert all(r.is_kov and r.municipality_iri for r in rows)


def test_kov_regulation_before_after(corpus_krr, root: Path) -> None:
    """Pre-#716 the client could not load a KOV regulation at all (0 rows)."""
    corpus_krr.path(
        "regulations/kov/abja_vallavolikogu/"
        "abja_muusikakooli_opetajate_tootasu_alammaara_kinnitamine_t1039736_peep.json"
    )
    graph = load_regulation("Reg_1039736", kov=True, root=root)
    rows = provision_rows(graph)
    assert len(provisions_of(graph)) == len(rows) == 5
    assert all(r.is_kov for r in rows)


def _types(node: dict) -> list[str]:
    value = node.get("@type")
    return [t for t in (value if isinstance(value, list) else [value]) if isinstance(t, str)]


@pytest.mark.slow
def test_exact_matching_vs_substring_on_kov_and_sanctions(corpus_krr) -> None:
    """Measure: exact matching keeps every node the substring test found.

    Every KovProvision node also carries LegalProvision and no SanctionType
    class exists, so on the committed tree both rules select the same nodes;
    exact matching only removes the latent over-/under-match.
    """
    kov_root = corpus_krr.path("regulations/kov")
    sanctions = corpus_krr.path("sanctions")
    exact = substring = kov_only = 0
    for path in [*kov_root.rglob("*_peep.json"), *sanctions.glob("sanctions_*.json")]:
        for node in json.loads(path.read_text(encoding="utf-8")).get("@graph", []):
            types = _types(node)
            hit_exact = bool({"estleg:LegalProvision", "estleg:KovProvision", "estleg:Sanction"}
                             & set(types))
            hit_sub = any("LegalProvision" in t or "Sanction" in t for t in types)
            exact += hit_exact
            substring += hit_sub
            kov_only += "estleg:KovProvision" in types and "estleg:LegalProvision" not in types
    assert exact == substring > 100_000
    assert kov_only == 0


# ── documented examples ──────────────────────────────────────────────────────

_PY_BLOCK = re.compile(r"^```py\n(.*?)^```", re.DOTALL | re.MULTILINE)


def _doc_blocks() -> list[tuple[str, str]]:
    blocks = []
    readme = (REPO / "estleg_client" / "README.md").read_text(encoding="utf-8")
    guide = (REPO / "docs" / "API_GUIDE.md").read_text(encoding="utf-8")
    section = guide.split("## Python Client (`estleg_client`)", 1)[1].split("\n## ", 1)[0]
    for name, text in (("README", readme), ("API_GUIDE", section)):
        for index, match in enumerate(_PY_BLOCK.finditer(text)):
            blocks.append((f"{name}#{index}", match.group(1)))
    return blocks


def test_documented_blocks_exist() -> None:
    names = [name for name, _ in _doc_blocks()]
    assert sum(n.startswith("README") for n in names) >= 4
    assert sum(n.startswith("API_GUIDE") for n in names) >= 1


@pytest.mark.parametrize(("name", "body"), _doc_blocks(), ids=[n for n, _ in _doc_blocks()])
def test_documented_client_examples_execute(name: str, body: str, root: Path,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    if "fetch_corpus(" in body:
        pytest.skip("download example; covered by the fake-release test")
    monkeypatch.setenv(estleg_client.ENV_ROOT, str(root))
    output = StringIO()
    with redirect_stdout(output):
        exec(compile(body, name, "exec"), {})
    assert output.getvalue().strip(), f"{name} printed nothing"


# ── packaging (hermetic) ─────────────────────────────────────────────────────


def test_client_distribution_metadata() -> None:
    meta = tomllib.loads((PACKAGING / "pyproject.toml").read_text(encoding="utf-8"))
    project = meta["project"]
    assert project["name"] == "estleg-client"
    assert project["dependencies"] == ["rdflib>=7.1,<8"]
    assert project["scripts"]["estleg-load"] == "estleg_client.cli:main"
    assert meta["tool"]["setuptools"]["packages"] == ["estleg_client"]
    assert meta["tool"]["setuptools"]["package-data"]["estleg_client"] == ["py.typed"]
    assert meta["tool"]["setuptools"]["dynamic"]["version"]["attr"] == (
        "estleg_client.__version__"
    )
    tree = ast.parse((REPO / "estleg_client" / "__init__.py").read_text(encoding="utf-8"))
    version = next(
        node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "__version__"
                                                 for t in node.targets)
    )
    assert version == estleg_client.__version__
    assert re.fullmatch(r"\d+\.\d+\.\d+", version)


def test_stage_builds_standard_layout(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location("estleg_client_stage", PACKAGING / "stage.py")
    assert spec is not None and spec.loader is not None
    stage = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(stage)
    out = stage.stage(tmp_path / "build")
    for rel in ("pyproject.toml", "README.md", "LICENSE", "estleg_client/__init__.py",
                "estleg_client/py.typed", "estleg_client/cli.py"):
        assert (out / rel).is_file(), rel
    assert not list(out.rglob("__pycache__"))
    with pytest.raises(SystemExit):
        stage.stage(REPO)


def test_publish_workflow_is_pinned_and_trusted() -> None:
    text = (REPO / ".github" / "workflows" / "publish-client.yml").read_text(encoding="utf-8")
    uses = re.findall(r"uses:\s*(\S+)(.*)", text)
    assert uses
    for ref, comment in uses:
        assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", ref), ref
        assert re.search(r"#\s*v\d", comment), ref
    assert "pypa/gh-action-pypi-publish@" in text
    assert "id-token: write" in text
    assert "client-v*" in text
    assert "password" not in text and "PYPI_API_TOKEN" not in text


def test_public_api_is_exported() -> None:
    for name in estleg_client.__all__:
        assert hasattr(estleg_client, name), name
    assert RDF  # rdflib is the only runtime dependency

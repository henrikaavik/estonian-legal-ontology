"""Public adoption surface contract (#715).

Pins the Estonian data statement (``docs/ANDMED.et.md``), the refreshed
ministry overview HTML, the README audience router and the operator runbook
to the committed index files. Every input here is a regular (non-LFS) file,
so the module runs in the default ``unit`` tier.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parent.parent
DOCS = REPO / "docs"
KRR = REPO / "krr_outputs"
ANDMED = DOCS / "ANDMED.et.md"
OVERVIEW = DOCS / "eesti-oigusontoloogia-ulevaade.html"
RUNBOOK = DOCS / "OPERATOR_RUNBOOK.md"
README = REPO / "README.md"
PAGES_WORKFLOW = REPO / ".github" / "workflows" / "pages.yml"
PAGES_URL = (
    "https://henrikaavik.github.io/estonian-legal-ontology/"
    "eesti-oigusontoloogia-ulevaade.html"
)
COUNTS_MARKER = "<!-- estleg:andmed-counts -->"
VERSION_IRI = "https://w3id.org/estleg/1.0.0"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _counts_block() -> dict[str, Any]:
    text = _read(ANDMED)
    assert COUNTS_MARKER in text, "ANDMED.et.md lost its machine-readable counts marker"
    tail = text.split(COUNTS_MARKER, 1)[1]
    match = re.match(r"\s*```json\n(.*?)\n```", tail, re.DOTALL)
    assert match, "the counts marker must be followed by a ```json block"
    return json.loads(match.group(1))


def _dig(doc: Any, dotted: str) -> Any:
    for part in dotted.split("."):
        assert isinstance(doc, dict) and part in doc, f"missing index field {dotted}"
        doc = doc[part]
    return doc


def _load_index(rel: str) -> dict[str, Any]:
    path = REPO / rel
    head = path.read_bytes()[:64]
    assert not head.startswith(b"version https://git-lfs"), f"{rel} is an LFS pointer"
    return json.loads(path.read_text(encoding="utf-8"))


def _et_number(value: int) -> str:
    """Estonian thousands grouping as used in the HTML: ``22 832``."""
    return f"{value:,}".replace(",", " ")


def _normalise_spaces(text: str) -> str:
    return text.replace("&nbsp;", " ").replace(" ", " ").replace(" ", " ")


# --------------------------------------------------------------------------
# ANDMED.et.md
# --------------------------------------------------------------------------


def _corpus_ids() -> list[str]:
    return [entry["key"] for entry in _counts_block()["corpora"]]


@pytest.mark.parametrize("key", _corpus_ids())
def test_andmed_counts_match_indexes(key: str) -> None:
    entry = next(e for e in _counts_block()["corpora"] if e["key"] == key)
    index = _load_index(entry["index"])
    assert _dig(index, entry["countField"]) == entry["count"], (
        f"{key}: ANDMED.et.md says {entry['count']}, "
        f"{entry['index']}:{entry['countField']} says {_dig(index, entry['countField'])}"
    )
    stamp = str(_dig(index, entry["stampField"]))[:10]
    assert stamp == entry["stamp"], f"{key}: snapshot stamp drifted ({stamp})"


def test_andmed_block_covers_every_required_corpus() -> None:
    keys = set(_corpus_ids())
    required = {
        "laws",
        "regulations-riik",
        "regulations-kov",
        "eelnoud",
        "riigikohus",
        "eurlex",
        "curia",
    }
    assert required <= keys, f"missing corpora: {sorted(required - keys)}"


def test_andmed_law_file_count_and_snapshot() -> None:
    block = _counts_block()
    assert block["lawFileCount"] == len(list(KRR.glob("*_peep.json")))
    assert block["snapshotDate"] == _load_index("krr_outputs/INDEX.json")["generated"]
    assert block["versionIRI"] == VERSION_IRI
    meta = json.loads(_read(REPO / "metadata.jsonld"))
    assert block["ontologyVersion"] == meta["owl:versionInfo"]


def test_andmed_prose_table_repeats_block_counts() -> None:
    text = _read(ANDMED).split(COUNTS_MARKER, 1)[0]
    for entry in _counts_block()["corpora"]:
        if entry["key"] in {"riigikohus-full-text", "eurlex-in-force"}:
            continue
        assert _et_number(entry["count"]) in text, (
            f"coverage table lacks {entry['key']}={_et_number(entry['count'])}"
        )


def test_public_file_counts_match_metadata_and_generated_report() -> None:
    metadata = json.loads(_read(REPO / "metadata.jsonld"))
    total = metadata["estleg:statistics"]["estleg:totalFiles"]
    report = _read(DOCS / "VALIDATION_REPORT.md").split(
        "<!-- BEGIN GENERATED: validation-summary -->", 1
    )[1].split("<!-- END GENERATED: validation-summary -->", 1)[0]
    validated = int(re.search(r"\| Files validated \| ([\d,]+) \|", report).group(1).replace(",", ""))
    errors = int(re.search(r"\| Errors \| (\d+) \|", report).group(1))
    warnings = int(re.search(r"\| Warnings \| (\d+) \|", report).group(1))
    for path in (ANDMED, OVERVIEW):
        text = _normalise_spaces(_read(path))
        assert _et_number(total) in text, f"{path.name}: total file count is stale"
        assert _et_number(validated) in text, f"{path.name}: validated file count is stale"
    noun = "warning" if warnings == 1 else "warnings"
    assert f"**{errors} errors / {warnings} {noun}**" in _read(README)
    assert f"<td>JSON/JSON-LD vead</td><td>{errors}</td>" in _read(OVERVIEW)


def test_andmed_has_required_sections_and_contact() -> None:
    text = _read(ANDMED)
    for heading in (
        "## 2. Katvus",
        "## 3. Allikad",
        "## 4. Värskus",
        "## 5. Ametlik tekst ja tuletatud kihid",
        "## 6. Isikuandmed",
        "## 7. Õigused ja litsents",
        "## 8. Viitamine",
        "## 9. Versioon",
        "## 10. Kontakt",
    ):
        assert heading in text, f"ANDMED.et.md is missing {heading!r}"
    for needle in (
        "artikkel 10",
        "iseseisvaks vastutavaks",
        "CURIA",
        "CC BY 4.0",
        VERSION_IRI,
        "../SECURITY.md",
        "/issues",
        "DATA_PROTECTION.md",
        "DATA_RIGHTS.md",
    ):
        assert needle in text, f"ANDMED.et.md is missing {needle!r}"
    assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text), "no personal mailbox"


def test_andmed_freshness_budgets_match_code() -> None:
    # Read the budgets from source: importing the module pulls in the HTTP
    # client stack, which the unit tier's socket guard flags.
    source = _read(REPO / "src" / "estleg" / "check_rt_staleness.py")
    budgets = source.split("CORPUS_BUDGETS:", 1)[1].split("\n)\n", 1)[0]
    days = {int(v) for v in re.findall(r"max_lag_days=(\d+)", budgets)}
    sla = re.search(r"^SLA_MAX_LAG_DAYS = (\d+)$", source, re.MULTILINE)
    assert sla and days, "could not read the freshness budgets"
    days.add(int(sla.group(1)))
    text = _read(ANDMED)
    for value in days:
        assert f"{value} päeva" in text, f"budget {value} d missing from ANDMED.et.md"


# --------------------------------------------------------------------------
# HTML overview
# --------------------------------------------------------------------------


def test_overview_has_new_sections() -> None:
    text = _read(OVERVIEW)
    for anchor in ("litsents", "isikuandmed", "viitamine", "versioon"):
        assert f'id="{anchor}"' in text, f"overview lacks section #{anchor}"
    lower = text.lower()
    for needle in ("cc by 4.0", "gdpr", "riigikohus", "curia", "1.0.0", VERSION_IRI):
        assert needle.lower() in lower, f"overview lacks {needle!r}"
    for doc in ("ANDMED.et.md", "DATA_PROTECTION.md", "DATA_RIGHTS.md"):
        assert doc in text, f"overview does not link {doc}"


def test_overview_carries_measured_counts() -> None:
    text = _normalise_spaces(_read(OVERVIEW))
    for entry in _counts_block()["corpora"]:
        if entry["key"] in {
            "laws",
            "regulations-riik",
            "regulations-kov",
            "eelnoud",
            "riigikohus",
            "eurlex",
            "curia",
        }:
            assert _et_number(entry["count"]) in text, (
                f"overview stats lack {entry['key']}={_et_number(entry['count'])}"
            )
    assert _et_number(_counts_block()["lawFileCount"]) in text
    for stale in ("1 145", "1 190", "23 113", "htmlpreview"):
        assert stale not in text, f"overview still carries stale {stale!r}"


# --------------------------------------------------------------------------
# README router and runbook move
# --------------------------------------------------------------------------


def test_readme_starts_with_audience_router() -> None:
    lines = _read(README).splitlines()
    assert lines[0] == "# Estonian Legal Ontology"
    head = "\n".join(lines[:15])
    first_prose = next(i for i, ln in enumerate(lines) if ln.startswith("A comprehensive"))
    router = "\n".join(lines[:first_prose])
    for link in (
        "docs/ANDMED.et.md",
        PAGES_URL,
        "docs/API_GUIDE.md",
        "mcp_server/README.md",
        "docs/OPERATOR_RUNBOOK.md",
    ):
        assert link in router, f"router lacks {link}"
    assert "**Project policies:**" in head
    for policy in ("GOVERNANCE.md", "SECURITY.md", "docs/RELEASE_NOTES.md"):
        assert policy in head


def test_readme_no_longer_carries_operator_commands() -> None:
    text = _read(README)
    assert "htmlpreview.github.io" not in text
    for command in (
        "generate_all_laws.py --refresh",
        "generate_all_laws.py --from-manifest",
        "generate_regulations.py --refresh",
        "python3 scripts/generate_eu_court_decisions.py",
        "python3 scripts/extract_cross_references.py",
        "python3 scripts/generate_similarity_index.py",
    ):
        assert command not in text, f"README still carries operator command {command!r}"
    assert "## Refreshing Data" in text, "keep the anchor other docs link to"
    assert "pending" in text.lower() and PAGES_URL in text


def test_runbook_names_current_entry_points() -> None:
    text = _read(RUNBOOK)
    for entry in (
        "generate_all_laws.py",
        "generate_regulations.py",
        "generate_draft_legislation.py",
        "generate_court_decisions.py",
        "generate_lower_court_decisions",
        "generate_eu_legislation.py",
        "generate_eu_court_decisions.py",
        "generate_rt_act_kinds.py",
        "screen_court_personal_data.py",
        "run_all_integration.py",
        "build_release_assets.py",
        "validate_all.py",
        "shacl_validate_all.py --all",
        "validate_seadusloome_sync.py",
        "check_rt_staleness.py",
        "/public-api/api/v1/akt/",
        "riigiteataja_common.py",
    ):
        assert entry in text, f"runbook lacks {entry!r}"
    for script in re.findall(r"python3 scripts/([a-z0-9_]+\.py)", text):
        assert (REPO / "scripts" / script).is_file(), f"runbook names missing scripts/{script}"
    for module in re.findall(r"python3 -m estleg\.([a-z0-9_]+)", text):
        assert (REPO / "src" / "estleg" / f"{module}.py").is_file(), module


def test_runbook_flags_exist_in_parsers() -> None:
    """Every ``--flag`` the runbook passes to a generator is a real option."""
    text = _read(RUNBOOK)
    for script, args in re.findall(r"python3 scripts/([a-z0-9_]+)\.py([^\n#]*)", text):
        source = REPO / "src" / "estleg" / f"{script}.py"
        if not source.is_file():
            continue
        code = _read(source)
        for flag in re.findall(r"(--[a-z][a-z0-9-]*)", args):
            assert f'"{flag}"' in code, f"{script}.py has no {flag} option"


# --------------------------------------------------------------------------
# Pages workflow
# --------------------------------------------------------------------------


def test_pages_workflow_is_pinned_and_scoped() -> None:
    text = _read(PAGES_WORKFLOW)
    for action in (
        "actions/configure-pages",
        "actions/upload-pages-artifact",
        "actions/deploy-pages",
    ):
        assert re.search(rf"uses: {action}@[0-9a-f]{{40}} # v\d+\.\d+\.\d+", text), (
            f"{action} must be pinned by full SHA with a version comment"
        )
    for uses in re.findall(r"uses: (\S+)", text):
        assert re.search(r"@[0-9a-f]{40}$", uses), f"unpinned action {uses}"
    assert "pages: write" in text and "id-token: write" in text
    assert "workflow_dispatch" in text and '"docs/**"' in text
    assert "Settings -> Pages" in text, "header must name the maintainer-only setting"

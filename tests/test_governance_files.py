"""#721: governance, licensing and CI-hardening files stay in their contract.

Default tier: reads only small committed files, no corpus and no network.

- ``LICENSE`` is the bare MIT text, so GitHub and SPDX scanners detect it.
- ``REUSE.toml`` parses and every licence it names has a text in ``LICENSES/``.
- No workflow uses a mutable ``@vN`` action tag; actions are pinned by SHA.
- The ``pytest`` job runs the declared Python range.
- The ``Makefile`` defines the shared targets, and CI lint calls ``make lint``.
- ``SECURITY.md`` and ``GOVERNANCE.md`` exist and publish no email address.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO / ".github" / "workflows"

# The MIT text as distributed by choosealicense.com / GitHub, wrapped at 80
# columns. ``{copyright}`` is the one line a project fills in.
MIT_TEMPLATE = """MIT License

{copyright}

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

COPYRIGHT_LINE_RE = re.compile(r"^Copyright \(c\) \d{4}(?:-\d{4})? \S.*$")

# First line of each licence text, as SPDX publishes it.
LICENSE_TITLES = {
    "MIT": "MIT License",
    "CC-BY-4.0": "Creative Commons Attribution 4.0 International",
}

SPDX_ID_RE = re.compile(r"(?:LicenseRef-)?[A-Za-z0-9.+-]+")
SPDX_OPERATORS = {"AND", "OR", "WITH"}

MUTABLE_TAG_RE = re.compile(r"uses:\s*[^\s#]+@v\d+(?:\.\d+)*\s*(?:#.*)?$", re.MULTILINE)
PINNED_USES_RE = re.compile(r"uses:\s*[^\s#]+@([^\s#]+)")
FULL_SHA_RE = re.compile(r"[0-9a-f]{40}")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

MAKE_TARGETS = ("check", "lint", "test", "corpus-gates", "release-assets")
CANONICAL_LINT = "python3 -m ruff check scripts/ src/estleg/ tests/ mcp_server/"


def _workflow_files() -> list[Path]:
    return sorted([*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")])


def _reuse() -> dict:
    with (REPO / "REUSE.toml").open("rb") as fh:
        return tomllib.load(fh)


def _license_ids(expression: str) -> set[str]:
    tokens = SPDX_ID_RE.findall(expression.replace("(", " ").replace(")", " "))
    return {tok for tok in tokens if tok not in SPDX_OPERATORS}


# --- licence ------------------------------------------------------------------


def test_license_is_bare_mit_modulo_copyright_line() -> None:
    text = (REPO / "LICENSE").read_text(encoding="utf-8")
    lines = text.splitlines()
    assert len(lines) > 2, "LICENSE is empty"
    copyright_line = lines[2]
    assert COPYRIGHT_LINE_RE.match(copyright_line), (
        f"LICENSE line 3 is not an MIT copyright line: {copyright_line!r}"
    )
    assert text == MIT_TEMPLATE.format(copyright=copyright_line), (
        "LICENSE must be the bare MIT text so scanners detect it; the scope "
        "note belongs in NOTICE (#721)"
    )


def test_notice_carries_the_licence_scope_note() -> None:
    notice = (REPO / "NOTICE").read_text(encoding="utf-8")
    assert "SCOPE OF THE MIT LICENCE" in notice
    assert "does NOT license the DATA corpus" in notice


def test_reuse_toml_parses_and_names_known_licences() -> None:
    data = _reuse()
    assert data.get("version") == 1
    annotations = data.get("annotations")
    assert annotations, "REUSE.toml has no [[annotations]] tables"
    for table in annotations:
        assert table.get("path"), table
        assert table.get("SPDX-License-Identifier"), table
        holders = table.get("SPDX-FileCopyrightText")
        holders = [holders] if isinstance(holders, str) else holders
        assert holders, table
        for holder in holders:
            assert not EMAIL_RE.search(holder), (
                f"REUSE copyright holder must be the project, not an email: {holder!r}"
            )


def _referenced_licence_ids() -> set[str]:
    ids: set[str] = set()
    for table in _reuse()["annotations"]:
        ids |= _license_ids(table["SPDX-License-Identifier"])
    return ids


@pytest.mark.parametrize("license_id", sorted(_referenced_licence_ids()))
def test_every_reuse_licence_has_a_text(license_id: str) -> None:
    path = REPO / "LICENSES" / f"{license_id}.txt"
    assert path.is_file(), f"REUSE.toml names {license_id} but {path} is missing"
    first_line = path.read_text(encoding="utf-8").splitlines()[0].strip()
    expected = LICENSE_TITLES.get(license_id)
    if expected is not None:
        assert first_line == expected, f"{path} starts with {first_line!r}"
    else:
        assert license_id.startswith("LicenseRef-"), (
            f"{license_id} is neither a known SPDX id nor a LicenseRef"
        )
        assert first_line, f"{path} has no title line"


def test_licences_directory_has_no_unused_texts() -> None:
    """``reuse lint`` fails on licence texts nothing refers to."""
    present = {p.stem for p in (REPO / "LICENSES").glob("*.txt")}
    assert present == _referenced_licence_ids()


def test_pyproject_declares_mit() -> None:
    with (REPO / "pyproject.toml").open("rb") as fh:
        project = tomllib.load(fh)["project"]
    assert project.get("license") == "MIT"
    assert "LICENSE" in project.get("license-files", [])


# --- CI -------------------------------------------------------------------------


def test_workflows_exist() -> None:
    assert _workflow_files(), f"no workflows under {WORKFLOWS}"


@pytest.mark.parametrize("workflow", _workflow_files(), ids=lambda p: p.name)
def test_no_mutable_action_tags(workflow: Path) -> None:
    text = workflow.read_text(encoding="utf-8")
    mutable = MUTABLE_TAG_RE.findall(text)
    assert not mutable, (
        f"{workflow.name} uses mutable action tags; pin the full commit SHA "
        f"with a '# vX.Y.Z' comment (#721): {mutable}"
    )
    for ref in PINNED_USES_RE.findall(text):
        assert FULL_SHA_RE.fullmatch(ref), (
            f"{workflow.name}: action ref {ref!r} is not a 40-character commit SHA"
        )


def _job_block(text: str, job: str) -> str:
    match = re.search(rf"^  {re.escape(job)}:\n(.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)", text, re.M | re.S)
    assert match, f"job {job!r} not found"
    return match.group(1)


def test_pytest_job_runs_the_declared_python_range() -> None:
    block = _job_block((WORKFLOWS / "validate.yml").read_text(encoding="utf-8"), "pytest")
    match = re.search(r"python-version:\s*\[([^\]]*)\]", block)
    assert match, "the pytest job has no python-version matrix"
    versions = {v.strip().strip("'\"") for v in match.group(1).split(",")}
    assert versions == {"3.11", "3.12", "3.13"}
    assert "python-version: ${{ matrix.python-version }}" in block


def test_ci_lint_job_calls_make_lint() -> None:
    block = _job_block((WORKFLOWS / "validate.yml").read_text(encoding="utf-8"), "lint")
    assert re.search(r"^\s*run:\s*make lint\s*$", block, re.M)


# --- Makefile -------------------------------------------------------------------


def test_makefile_defines_the_shared_targets() -> None:
    text = (REPO / "Makefile").read_text(encoding="utf-8")
    for target in MAKE_TARGETS:
        assert re.search(rf"^{re.escape(target)}:", text, re.M), f"no `{target}` target"
    assert "LINT_PATHS := scripts/ src/estleg/ tests/ mcp_server/" in text


@pytest.mark.parametrize(
    "doc",
    ["CONTRIBUTING.md", "AGENTS.md", "CLAUDE.md", ".github/PULL_REQUEST_TEMPLATE.md"],
)
def test_docs_quote_the_canonical_lint_command(doc: str) -> None:
    text = (REPO / doc).read_text(encoding="utf-8")
    assert CANONICAL_LINT in text, f"{doc} does not quote `{CANONICAL_LINT}`"
    for line in text.splitlines():
        if "ruff check" in line:
            assert CANONICAL_LINT in line, f"{doc} has a divergent ruff command: {line!r}"


# --- governance documents -------------------------------------------------------


@pytest.mark.parametrize("name", ["SECURITY.md", "GOVERNANCE.md"])
def test_governance_documents_exist_without_email(name: str) -> None:
    path = REPO / name
    assert path.is_file(), f"{name} is missing (#721)"
    text = path.read_text(encoding="utf-8")
    assert not EMAIL_RE.search(text), (
        f"{name} must not publish an email address; the contact route is "
        "GitHub private vulnerability reporting until a shared alias exists"
    )

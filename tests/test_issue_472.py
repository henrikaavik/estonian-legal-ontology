"""#472: src/estleg package layout + console entry points."""

from __future__ import annotations

import ast
import importlib.metadata
import re
from pathlib import Path

import pytest

from estleg import estleg_common, generate_all_laws, run_all_integration, validate_all
from estleg.estleg_common import discover_repo_root

REPO = Path(__file__).resolve().parent.parent
_PATH_INSERT = re.compile(r"sys\.path\.(insert|append)\(")


def _iter_py(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def test_src_estleg_package_is_importable() -> None:
    assert (REPO / "src" / "estleg" / "__init__.py").is_file()
    assert (REPO / "src" / "estleg" / "estleg_common.py").is_file()
    assert estleg_common.REPO_ROOT == REPO
    assert discover_repo_root() == REPO
    assert generate_all_laws.REPO_ROOT == REPO
    assert validate_all.REPO_ROOT == REPO
    assert run_all_integration.SCRIPTS_DIR == REPO / "scripts"


def test_pyproject_declares_console_scripts() -> None:
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert 'estleg-generate-laws = "estleg.generate_all_laws:main"' in text
    assert 'estleg-run-pipeline = "estleg.run_all_integration:main"' in text
    assert 'estleg-validate = "estleg.validate_all:main"' in text
    assert 'packages = ["estleg", "estleg_client"]' in text
    assert "py-modules = []" not in text
    assert 'packages = ["scripts"]' not in text


def test_installed_entry_points_resolve() -> None:
    entries = importlib.metadata.entry_points()
    scripts = (
        entries.select(group="console_scripts")
        if hasattr(entries, "select")
        else entries.get("console_scripts", [])
    )
    by_name = {ep.name: ep.value for ep in scripts}
    assert by_name["estleg-generate-laws"] == "estleg.generate_all_laws:main"
    assert by_name["estleg-run-pipeline"] == "estleg.run_all_integration:main"
    assert by_name["estleg-validate"] == "estleg.validate_all:main"


@pytest.mark.parametrize(
    "mod",
    ("generate_all_laws", "run_all_integration", "validate_all"),
)
def test_scripts_dir_keeps_thin_shims(mod: str) -> None:
    path = REPO / "scripts" / f"{mod}.py"
    text = path.read_text(encoding="utf-8")
    assert f'runpy.run_module("estleg.{mod}"' in text
    tree = ast.parse(text)
    assert not any(
        isinstance(node, ast.FunctionDef) and node.name == "main" for node in tree.body
    )


def test_no_sys_path_inserts_in_scripts_or_tests() -> None:
    offenders: list[str] = []
    for root in (REPO / "scripts", REPO / "tests"):
        for path in _iter_py(root):
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if _PATH_INSERT.search(line) and not line.lstrip().startswith("#"):
                    offenders.append(f"{path.relative_to(REPO)}:{i}:{line.strip()}")
    assert offenders == [], "sys.path.insert/append leftover:\n" + "\n".join(offenders)

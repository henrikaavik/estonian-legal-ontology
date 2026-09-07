"""No local ``save_json`` residue (issue #376).

Pipeline modules must bind ``estleg_common.save_json`` (tempfile +
``os.replace``). ``classify_eurovoc`` may keep a thin print-then-delegate
wrapper.
"""
from __future__ import annotations

import ast
import importlib
import inspect
import re
from pathlib import Path

from estleg import estleg_common

SCRIPTS = Path(__file__).resolve().parents[1] / "src" / "estleg"
_DEF_SAVE_JSON = re.compile(r"^def save_json\b", re.MULTILINE)

SAVE_JSON_MODULES = (
    "classify_deontic",
    "generate_inverse_references",
    "generate_amendment_history",
    "extract_sanctions",
    "extract_draft_impact",
    "extract_institutional_competence",
    "extract_legal_concepts",
    "extract_temporal_data",
)


def _script_source(modname: str) -> str:
    return (SCRIPTS / f"{modname}.py").read_text(encoding="utf-8")


def _imports_save_json_from_estleg_common(src: str) -> bool:
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return False
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module in {
            "estleg_common",
            "estleg.estleg_common",
        }:
            for alias in node.names:
                if alias.name == "save_json":
                    return True
    return False


def _call_qualname(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _call_qualname(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def test_listed_modules_do_not_define_local_save_json() -> None:
    offenders: list[str] = []
    for name in SAVE_JSON_MODULES:
        if _DEF_SAVE_JSON.search(_script_source(name)):
            offenders.append(name)
    assert offenders == [], (
        "these modules must not define a local save_json (#376); "
        f"still defined in: {', '.join(offenders)}"
    )


def test_listed_modules_bind_estleg_common_save_json() -> None:
    # Prefer source inspection so a mid-migration NameError in a caller
    # module does not hide the #376 contract. When the module imports
    # cleanly, also require the same function object.
    not_imported: list[str] = []
    mismatched: list[str] = []
    for name in SAVE_JSON_MODULES:
        src = _script_source(name)
        if not re.search(r"\bsave_json\b", src):
            not_imported.append(name)
            continue
        if _DEF_SAVE_JSON.search(src):
            continue
        if not _imports_save_json_from_estleg_common(src):
            not_imported.append(name)
            continue
        try:
            mod = importlib.import_module(f"estleg.{name}")
        except Exception:
            continue
        if getattr(mod, "save_json", None) is not estleg_common.save_json:
            mismatched.append(name)
    assert not_imported == [], (
        "save_json must be imported from estleg_common (#376); "
        f"missing/wrong import in: {', '.join(not_imported)}"
    )
    assert mismatched == [], (
        "save_json must be estleg_common.save_json (#376); "
        f"mismatch in: {', '.join(mismatched)}"
    )


def test_classify_eurovoc_save_json_delegates() -> None:
    src = _script_source("classify_eurovoc")
    if not re.search(r"\bsave_json\b", src):
        return

    mod = importlib.import_module("estleg.classify_eurovoc")
    bound = getattr(mod, "save_json", None)
    if bound is None or bound is estleg_common.save_json:
        return

    # Thin wrapper: optional print, then the shared writer. No local dump.
    wrapper_src = inspect.getsource(bound)
    assert "json.dump" not in wrapper_src
    tree = ast.parse(wrapper_src)
    func = next(n for n in tree.body if isinstance(n, ast.FunctionDef))
    allowed = {"print", "_save_json", "save_json", "estleg_common.save_json"}
    for stmt in func.body:
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
            continue
        call = None
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call) or isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Call):
            call = stmt.value
        assert call is not None, (
            "classify_eurovoc.save_json must only print and/or call "
            f"estleg_common.save_json (#376); got {ast.dump(stmt)}"
        )
        assert _call_qualname(call.func) in allowed

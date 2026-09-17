"""Central JSON-LD context and pinned run timestamp (issue #465).

Callers must import ``estleg_common.CONTEXT`` / ``PINNED_RUN_TIMESTAMP``
instead of pasting a private ``@context`` or a 1970 sentinel.
"""
from __future__ import annotations

import re
from pathlib import Path

from estleg import estleg_common

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
OWL_FINGERPRINT = '"owl": "http://www.w3.org/2002/07/owl#"'
_PINNED_ASSIGN = re.compile(r"^PINNED_RUN_TIMESTAMP =", re.MULTILINE)
_ALLOWED_PINNED_ASSIGN = frozenset(
    {
        "estleg_common.py",
        "kov_pipeline_coverage.py",  # may re-export
    }
)
_MUST_NOT_ASSIGN_PINNED = ("generate_annotations.py", "generate_amendment_history.py")


def test_context_contains_required_prefixes() -> None:
    for key in ("estleg", "owl", "eli", "void", "dcat", "prov", "dcterms", "schema"):
        assert key in estleg_common.CONTEXT, key
    assert estleg_common.CONTEXT["schema"] == "https://schema.org/"


def test_pinned_run_timestamp_follows_evaluation_date() -> None:
    assert estleg_common.PINNED_RUN_TIMESTAMP == (
        f"{estleg_common.BUILD_EVALUATION_DATE}T00:00:00+00:00"
    )


def test_no_pasted_owl_context_fingerprint_outside_estleg_common() -> None:
    offenders: list[str] = []
    for path in sorted(SCRIPTS.glob("*.py")):
        if path.name == "estleg_common.py":
            continue
        if OWL_FINGERPRINT in path.read_text(encoding="utf-8"):
            offenders.append(path.name)
    assert offenders == [], (
        "pasted @context fingerprint must live only in estleg_common (#465); "
        f"still present in: {', '.join(offenders)}"
    )


def test_pinned_run_timestamp_not_reassigned_outside_allowlist() -> None:
    offenders: list[str] = []
    for path in sorted(SCRIPTS.glob("*.py")):
        if path.name in _ALLOWED_PINNED_ASSIGN:
            continue
        text = path.read_text(encoding="utf-8")
        if _PINNED_ASSIGN.search(text):
            offenders.append(path.name)
    assert offenders == [], (
        "PINNED_RUN_TIMESTAMP must be assigned only in estleg_common "
        "(kov_pipeline_coverage may re-export) (#465); "
        f"still assigned in: {', '.join(offenders)}"
    )


def test_annotation_and_amendment_generators_do_not_assign_pinned() -> None:
    leftover: list[str] = []
    for name in _MUST_NOT_ASSIGN_PINNED:
        text = (SCRIPTS / name).read_text(encoding="utf-8")
        if _PINNED_ASSIGN.search(text):
            leftover.append(name)
    assert leftover == [], (
        "generate_annotations / generate_amendment_history must import "
        "PINNED_RUN_TIMESTAMP, not assign their own (#465 / #533); "
        f"still assigned in: {', '.join(leftover)}"
    )

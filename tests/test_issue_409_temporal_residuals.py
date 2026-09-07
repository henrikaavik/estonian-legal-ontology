"""#682 — act roots must state a temporalStatus they can actually support.

Supersedes the #409 / #428 residual gate, which required every live INDEX
Act/Law root to be ``inForce`` / ``repealed`` / ``notYetEffective``. Nothing in
the corpus could deliver that: no act root carries an entry-into-force or repeal
date and no version-chain head carries ``versionValidTo``, so the old gate was
satisfied only by ``residual_status`` stamping a blanket ``inForce`` on ~385 acts
and by deprecated roots copying their successor's status. The result was all
1,146 act roots claiming to be in force, the Constitution's retired IRI included.

The invariants kept here are the ones the data supports:

1. Every live INDEX Act/Law root carries an **explicit** ``estleg:temporalStatus``
   from the closed vocabulary — ``unknown`` is a legitimate, honest answer, but a
   missing or off-vocabulary value is still a defect.
2. No ``owl:deprecated true`` Act/Law root anywhere in the root peeps is
   ``inForce``. A retired IRI (#426) is legal succession, not evidence about the
   statute's force. (``repealed`` is equally unsupported; the SHACL shape and
   ``derive_act_temporal_status`` both settle these at ``unknown``.)

Run ``python3 -m estleg.derive_act_temporal_status --all --recompute`` to bring
the corpus into line after changing the derivation.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
KRR = REPO / "krr_outputs"
INDEX = KRR / "INDEX.json"
KNOWN = {"inForce", "repealed", "notYetEffective", "unknown"}


def _types(node: dict) -> list[str]:
    raw = node.get("@type") or []
    return [raw] if isinstance(raw, str) else [t for t in raw if isinstance(t, str)]


def _is_work_act(node: dict) -> bool:
    types = _types(node)
    if "estleg:Part" in types and "estleg:Act" not in types:
        return False
    return "estleg:Act" in types or "estleg:Law" in types


def _index_law_files() -> set[str]:
    index = json.loads(INDEX.read_text(encoding="utf-8"))
    return {fname for law in index["laws"] for fname in (law.get("files") or [])}


def _scan() -> tuple[list[str], list[str]]:
    """One pass over INDEX law files + root peeps. Returns (live_defects, deprecated_in_force)."""
    index_files = _index_law_files()
    live_defects: list[str] = [
        f"missing-file:{fname}" for fname in sorted(index_files) if not (KRR / fname).is_file()
    ]
    deprecated_in_force: list[str] = []
    paths = {KRR / fname for fname in index_files} | set(KRR.glob("*_peep.json"))
    for path in sorted(paths):
        if not path.is_file():
            continue
        doc = json.loads(path.read_text(encoding="utf-8"))
        indexed = path.name in index_files
        for node in doc.get("@graph") or []:
            if not isinstance(node, dict) or not _is_work_act(node):
                continue
            status = node.get("estleg:temporalStatus")
            if node.get("owl:deprecated") is True:
                if status == "inForce":
                    deprecated_in_force.append(f"{path.name}:{node.get('@id')}")
            elif indexed and status not in KNOWN:
                live_defects.append(f"{path.name}:{node.get('@id')}:{status!r}")
    return live_defects, deprecated_in_force


def test_index_live_acts_have_explicit_temporal_status() -> None:
    """#682 (1): every live INDEX Act/Law root states a closed-vocabulary status."""
    live_defects, _ = _scan()
    assert live_defects == [], (
        f"{len(live_defects)} live INDEX Act/Law nodes carry no explicit "
        f"temporalStatus from {sorted(KNOWN)}, e.g. {live_defects[:8]}"
    )


def test_deprecated_act_roots_are_never_in_force() -> None:
    """#682 (2): a retired IRI must not assert that its statute is in force."""
    _, deprecated_in_force = _scan()
    assert deprecated_in_force == [], (
        f"{len(deprecated_in_force)} owl:deprecated Act/Law roots still claim "
        f"inForce; rerun `python3 -m estleg.derive_act_temporal_status --all "
        f"--recompute`. e.g. {deprecated_in_force[:8]}"
    )

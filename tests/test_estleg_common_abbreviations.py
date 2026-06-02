"""Regression tests for KNOWN_ABBREVIATIONS / FULLNAME_GENITIVE and the
pinned corpus-count documentation in estleg_common.

Covers:
* #257 — ``PS`` must resolve to the Constitution's real ``estleg:sourceAct``
  (``"Eesti Vabariigi põhiseadus"``), not the bare ``"Põhiseadus"`` which
  never appears in the corpus, otherwise every ``PS §`` / ``põhiseaduse §``
  citation is silently dropped during court-link resolution.
* #294 — the corpus-count baseline mentioned in the module docstring must
  equal ``metadata.jsonld`` ``estleg:totalFiles`` so a stale literal can't
  trigger a spurious "classifier drift" investigation.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from estleg_common import FULLNAME_GENITIVE, KNOWN_ABBREVIATIONS

REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# #257 — PS / Põhiseadus must match the corpus sourceAct
# ---------------------------------------------------------------------------
def test_ps_maps_to_corpus_source_act() -> None:
    """The value MUST be the canonical estleg:sourceAct of the Constitution.

    ``build_abbreviation_to_prefix`` keys this value into the
    ``{source_act -> prefix}`` map built from the corpus; the Constitution's
    sourceAct is ``"Eesti Vabariigi põhiseadus"`` (verified in
    ``eesti_vabariigi_pohiseadus_peep.json``), so any other value resolves to
    ``None`` and drops all PS citations.
    """
    assert KNOWN_ABBREVIATIONS["PS"] == "Eesti Vabariigi põhiseadus"


def test_pohiseaduse_genitive_still_maps_to_ps() -> None:
    """The genitive text form parsers emit must still resolve to ``PS``."""
    assert FULLNAME_GENITIVE["põhiseaduse"] == "PS"


def test_ps_resolves_through_build_abbreviation_to_prefix_logic() -> None:
    """End-to-end guard mirroring ``build_abbreviation_to_prefix``.

    Replicates the exact resolution step (KNOWN_ABBREVIATIONS value looked up
    in the corpus-derived ``source_act_to_prefix``) so a future name-drift
    regression is caught here, in-module, without importing the consumer
    scripts. With the pre-fix value ``"Põhiseadus"`` this assertion fails.
    """
    # Simulates what build_*_index produces from the Constitution peep file.
    source_act_to_prefix = {"Eesti Vabariigi põhiseadus": "Eesti_Vabariigi_pohiseadus"}

    abbrev_to_prefix = {
        abbrev: source_act_to_prefix[full_name]
        for abbrev, full_name in KNOWN_ABBREVIATIONS.items()
        if full_name in source_act_to_prefix
    }

    assert abbrev_to_prefix.get("PS") == "Eesti_Vabariigi_pohiseadus"


# ---------------------------------------------------------------------------
# #257 hardening — abbreviation table internal consistency
# ---------------------------------------------------------------------------
def test_known_abbreviations_values_are_nonempty_strings() -> None:
    """Every abbreviation must map to a non-empty full-name string; an empty
    or whitespace value can never match a corpus sourceAct."""
    for abbrev, full_name in KNOWN_ABBREVIATIONS.items():
        assert isinstance(full_name, str) and full_name.strip(), abbrev


def test_fullname_genitive_targets_are_known_or_documented() -> None:
    """Every genitive entry resolves to a key in KNOWN_ABBREVIATIONS, except
    the handful of intentionally-documented resolver-fallthrough abbreviations
    (laws with no peep file). This keeps the two tables from silently drifting
    apart while allowing the documented exceptions.
    """
    # Parser-recognised but resolver-fallthrough by design (see the inline
    # comment in FULLNAME_GENITIVE): their target law has no peep file.
    documented_fallthrough = {"KNS", "AluS", "KOFS", "KOVVS"}
    for genitive, abbrev in FULLNAME_GENITIVE.items():
        assert (
            abbrev in KNOWN_ABBREVIATIONS or abbrev in documented_fallthrough
        ), f"{genitive!r} -> {abbrev!r} is neither known nor a documented fallthrough"


# ---------------------------------------------------------------------------
# #294 — docstring corpus-count baseline must match metadata.jsonld
# ---------------------------------------------------------------------------
def test_docstring_corpus_count_matches_metadata_total_files() -> None:
    """The corpus-count literal mentioned in the OPERATIONAL_STATE_FILES
    documentation must equal ``metadata.jsonld`` ``estleg:totalFiles``.

    Reads the *source* of estleg_common.py (not krr_outputs/) and the
    repo-root metadata catalog, so a future corpus-size change that updates
    metadata but not the prose is caught immediately.
    """
    metadata = json.loads(
        (REPO_ROOT / "metadata.jsonld").read_text(encoding="utf-8")
    )

    def _find_total_files(obj: object) -> int | None:
        if isinstance(obj, dict):
            if "estleg:totalFiles" in obj:
                return int(obj["estleg:totalFiles"])
            for value in obj.values():
                found = _find_total_files(value)
                if found is not None:
                    return found
        elif isinstance(obj, list):
            for item in obj:
                found = _find_total_files(item)
                if found is not None:
                    return found
        return None

    total_files = _find_total_files(metadata)
    assert total_files is not None, "metadata.jsonld lacks estleg:totalFiles"

    source = (REPO_ROOT / "scripts" / "estleg_common.py").read_text(
        encoding="utf-8"
    )
    # Every "(currently <N>)" baseline in the file must equal totalFiles.
    cited = [int(m) for m in re.findall(r"currently (\d{4,})", source)]
    assert cited, "expected at least one '(currently <N>)' corpus-count baseline"
    for n in cited:
        assert n == total_files, (
            f"docstring corpus-count {n} != metadata estleg:totalFiles "
            f"{total_files}; update the prose or metadata.jsonld"
        )

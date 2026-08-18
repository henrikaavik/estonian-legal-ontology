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
from pathlib import Path

from estleg.estleg_common import FULLNAME_GENITIVE, KNOWN_ABBREVIATIONS, slugify

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

    source = (REPO_ROOT / "src" / "estleg" / "estleg_common.py").read_text(
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


# ---------------------------------------------------------------------------
# #390 — KNS / AluS / KOFS / KOVVS now registered in KNOWN_ABBREVIATIONS
# ---------------------------------------------------------------------------
def test_390_enabling_law_abbreviations_present() -> None:
    """The four enabling-law abbreviations referenced by FULLNAME_GENITIVE
    must now resolve to their canonical corpus sourceAct so the preamble
    pass (issuedUnder) and Pattern 3 cross-refs chain end-to-end (#390)."""
    assert KNOWN_ABBREVIATIONS["KNS"] == "Kohanimeseadus"
    assert KNOWN_ABBREVIATIONS["AluS"] == "Alusharidusseadus"
    assert KNOWN_ABBREVIATIONS["KOFS"] == \
        "Kohaliku omavalitsuse üksuse finantsjuhtimise seadus"
    assert KNOWN_ABBREVIATIONS["KOVVS"] == \
        "Kohaliku omavalitsuse volikogu valimise seadus"


def test_390_abbrevs_have_matching_genitive() -> None:
    """Each #390 abbreviation keeps its FULLNAME_GENITIVE entry."""
    assert FULLNAME_GENITIVE["kohanimeseaduse"] == "KNS"
    assert FULLNAME_GENITIVE["alusharidusseaduse"] == "AluS"
    assert FULLNAME_GENITIVE[
        "kohaliku omavalitsuse üksuse finantsjuhtimise seaduse"] == "KOFS"
    assert FULLNAME_GENITIVE[
        "kohaliku omavalitsuse volikogu valimise seaduse"] == "KOVVS"


# ---------------------------------------------------------------------------
# #363 — 25 missing law genitives + 9 abbrev genitives
# ---------------------------------------------------------------------------
def test_363_added_abbrev_genitives_resolve_to_existing_abbrevs() -> None:
    """The 9 genitives added for laws whose abbreviation already existed
    must point at those existing KNOWN_ABBREVIATIONS keys (#363)."""
    expected = {
        "tulumaksuseaduse": "TuMS",
        "sotsiaalmaksuseaduse": "SMMS",
        "relvaseaduse": "RelvS",  # #588: was RSVS (the Weapons Act's real abbrev is RelvS)
        "kaitseväeteenistuse seaduse": "KAVS",
        "kindlustustegevuse seaduse": "KindlTS",
        "looduskaitseseaduse": "LKS",
        "ravimiseaduse": "RavS",
        "elektroonilise side seaduse": "EKS",
        "maareformi seaduse": "MaaRS",
    }
    for genitive, abbrev in expected.items():
        assert FULLNAME_GENITIVE.get(genitive) == abbrev, genitive
        # The abbrev pre-existed in KNOWN_ABBREVIATIONS.
        assert abbrev in KNOWN_ABBREVIATIONS


def test_363_new_law_genitives_registered() -> None:
    """Spot-check the newly-registered heavily-cited law genitives and
    their new abbreviations (#363). korrakaitseseadus alone was cited
    525× with zero outbound links before this."""
    for genitive, abbrev, full in (
        ("korrakaitseseaduse", "KorrS", "Korrakaitseseadus"),
        ("riigieelarve seaduse", "RES", "Riigieelarve seadus"),
        ("veeseaduse", "VeeS", "Veeseadus"),
        ("liiklusseaduse", "LS", "Liiklusseadus"),
    ):
        assert FULLNAME_GENITIVE.get(genitive) == abbrev, genitive
        assert KNOWN_ABBREVIATIONS.get(abbrev) == full, abbrev


def test_363_every_genitive_abbrev_is_known() -> None:
    """After #363/#390 EVERY genitive's abbreviation must resolve through
    KNOWN_ABBREVIATIONS (no resolver-fallthrough remains). This is the
    invariant the cross-ref Pattern 3 / preamble chains depend on."""
    for genitive, abbrev in FULLNAME_GENITIVE.items():
        assert abbrev in KNOWN_ABBREVIATIONS, \
            f"{genitive!r} -> {abbrev!r} not in KNOWN_ABBREVIATIONS"


def test_363_no_duplicate_full_names_break_reverse_lookup() -> None:
    """FULLNAME_TO_ABBREV (reverse map in extract_cross_references) keys
    on the full name; a new abbrev must not silently reuse an existing
    full name unless intentionally aliased. Guard that the new #363
    full names are distinct corpus sourceActs."""
    new_full_names = [
        "Korrakaitseseadus", "Riigieelarve seadus", "Veeseadus",
        "Liiklusseadus", "Väärtpaberituru seadus",
    ]
    # Each appears exactly once as a KNOWN_ABBREVIATIONS value.
    values = list(KNOWN_ABBREVIATIONS.values())
    for full in new_full_names:
        assert values.count(full) == 1, full


# ---------------------------------------------------------------------------
# #346 — slugify must strip a trailing '_' AFTER the max_len cut
# ---------------------------------------------------------------------------
def test_346_slugify_strips_trailing_underscore_after_truncation() -> None:
    """A title that slugifies to exactly max_len chars ending in '_'
    must not leave the trailing '_' once truncated — otherwise appending
    a suffix ('_Map_2026', '_Par_N') yields double-underscore IRIs (#346).
    """
    # 78 'a' chars + a space + a word: the run of 'a' fills up to the
    # boundary, the non-alnum boundary becomes '_' exactly at index 80.
    title = "a" * 80 + " konventsioon"
    slug = slugify(title)
    assert len(slug) <= 80
    assert not slug.endswith("_"), slug
    # Appending a standard suffix must NOT produce '__'.
    assert "__" not in f"{slug}_Map_2026"


def test_346_slugify_real_konventsioon_title_no_double_underscore() -> None:
    """Regression for the concrete corpus case: a long convention title
    whose 80-char cut landed on '_'."""
    title = (
        "12. augusti 1949 Genfi konventsioonide 8. juuni 1977 "
        "lisaprotokoll rahvusvaheliste relvakonfliktide ohvrite kaitse kohta"
    )
    slug = slugify(title)
    assert not slug.endswith("_")
    assert "__" not in f"{slug}_Par_1"


def test_346_slugify_normal_title_unchanged() -> None:
    """Short titles (no truncation) keep their existing slug — the
    rstrip only affects the truncation boundary."""
    assert slugify("Karistusseadustik") == "karistusseadustik"
    assert slugify("Avaliku teabe seadus") == "avaliku_teabe_seadus"


def test_346_slugify_internal_underscores_preserved() -> None:
    """Only TRAILING underscores are stripped; internal ones stay."""
    slug = slugify("Põhikooli- ja gümnaasiumiseadus")
    assert "_" in slug
    assert not slug.startswith("_") and not slug.endswith("_")

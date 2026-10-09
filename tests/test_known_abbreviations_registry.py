"""#696 — ``KNOWN_ABBREVIATIONS`` is derived from ``data/law_abbreviations.json``.

Drift tests: the abbreviation table and the registry must agree. The
official Riigi Teataja ``lyhend`` (registry ``source == "rt_api"``) wins
over any hand-maintained alias; aliases are limited to documented
exceptions. Before #696 the hand literal disagreed with the registry on
``TTKS`` / ``KELS`` (and ``AVVKHS`` / ``KOS`` / ``ELTS``), which
``registry_disagreements`` reproduces below.
"""
from __future__ import annotations

from estleg import estleg_common as ec
from estleg.estleg_common import (
    KNOWN_ABBREVIATION_REGISTRY_ABBREVS,
    KNOWN_ABBREVIATIONS,
    build_known_abbreviations,
    load_law_abbreviation_registry,
    registry_cited_abbrev,
    registry_law_title,
)

REGISTRY = load_law_abbreviation_registry()

# Aliases whose law has no entry in the registry (yet): the registry covers
# 601 of the ~1,122 INDEX laws (AGENTS.md). Keep this list honest — the
# test below fails if one of these gains a registry entry.
ALIAS_LAWS_WITHOUT_REGISTRY_ENTRY = frozenset({
    "Võlaõigusseadus", "Äriseadustik", "Pankrotiseadus", "Kõrgharidusseadus",
    "Ehitusseadus", "Meresõiduvahendite seadus",
    "Keskkonnaseadustiku eriosa seadus", "Maareformi seadus",
    "Tööstusomandi kaitse seadus", "Kohanimeseadus", "Alusharidusseadus",
    "Kohaliku omavalitsuse üksuse finantsjuhtimise seadus",
    "Kohaliku omavalitsuse volikogu valimise seadus", "Lastekaitseseadus",
})

# Alias keys that coincide with an AUTO-derived registry acronym of another
# law. An auto acronym is not an official lyhend, so the alias stands.
ALIAS_KEYS_SHADOWING_AUTO_ACRONYM = frozenset({"RLS"})


def _rt_api_lyhend() -> dict[str, str]:
    """Official cited lyhend -> registry title (unsuffixed entry wins)."""
    out: dict[str, str] = {}
    for slug in sorted(REGISTRY):
        entry = REGISTRY[slug]
        if entry.get("source") != "rt_api":
            continue
        cited = registry_cited_abbrev(entry["abbrev"])
        if cited in out and cited != entry["abbrev"]:
            continue
        out[cited] = registry_law_title(slug, entry)
    return out


def registry_disagreements(table: dict[str, str]) -> list[str]:
    """Keys of ``table`` whose law disagrees with the registry's rt_api lyhend."""
    rt = _rt_api_lyhend()
    return sorted(
        k for k, title in table.items()
        if k in rt and rt[k].casefold() != title.casefold()
    )


def test_registry_is_loaded() -> None:
    assert len(REGISTRY) >= 600
    assert len(KNOWN_ABBREVIATIONS) > 200


def test_every_rt_api_lyhend_is_known_with_its_registry_title() -> None:
    for cited, title in _rt_api_lyhend().items():
        assert KNOWN_ABBREVIATIONS.get(cited) == title, cited


def test_known_abbreviations_agree_with_registry() -> None:
    """The drift gate: no key may name a different law than the registry."""
    assert registry_disagreements(KNOWN_ABBREVIATIONS) == []


def test_pre_696_hand_literal_disagreed_on_ttks_and_kels() -> None:
    """The old 95-entry literal fails the drift gate (ticket #696)."""
    old_literal = {
        "TTKS": "Töötervishoiu ja tööohutuse seadus",
        "KELS": "Kõrgharidusseadus",
        "AVVKHS": "Avaliku teenistuse seadus",
        "KOS": "Kinnisasja omandamise kitsendamise seadus",
        "KarS": "Karistusseadustik",
    }
    assert registry_disagreements(old_literal) == ["AVVKHS", "KELS", "KOS", "TTKS"]


def test_collisions_decided_by_registry() -> None:
    assert KNOWN_ABBREVIATIONS["TTKS"] == "Tervishoiuteenuste korraldamise seadus"
    assert KNOWN_ABBREVIATIONS["TTOS"] == "Töötervishoiu ja tööohutuse seadus"
    assert KNOWN_ABBREVIATIONS["KELS"] == "Koolieelse lasteasutuse seadus"
    assert KNOWN_ABBREVIATIONS["KHaS"] == "Kõrgharidusseadus"
    assert KNOWN_ABBREVIATIONS["AVVKHS"].startswith("Riigi poolt isikule alusetult")
    assert KNOWN_ABBREVIATIONS["ATS"] == "Avaliku teenistuse seadus"
    assert KNOWN_ABBREVIATIONS["KOS"] == "Korteriomandiseadus"
    # Registry title is a URL for this rt_api entry; the override fixes it.
    assert KNOWN_ABBREVIATIONS["ELTS"] == "Elektrituruseadus"
    for gone in ("KELS_LASTEAS", "TTKSE"):
        assert gone not in KNOWN_ABBREVIATIONS


def test_compact_prefix_collisions_map_to_the_suffixed_law() -> None:
    """REÕS/REOS and ROS/RÕS are distinct laws (prefixes REOS vs REOS_2,
    ROS vs ROS_2); the cited lyhend picks the right registry entry."""
    assert KNOWN_ABBREVIATION_REGISTRY_ABBREVS["REÕS"] == ("REÕS",)
    assert KNOWN_ABBREVIATION_REGISTRY_ABBREVS["REOS"] == ("REOS_2",)
    assert KNOWN_ABBREVIATION_REGISTRY_ABBREVS["ROS"] == ("ROS",)
    assert KNOWN_ABBREVIATION_REGISTRY_ABBREVS["RÕS"] == ("RÕS_2",)
    assert KNOWN_ABBREVIATIONS["RÕS"] == "Riigi õigusabi seadus"


def test_aliases_never_shadow_an_rt_api_lyhend() -> None:
    rt = _rt_api_lyhend()
    assert sorted(set(ec._CITATION_ALIASES) & set(rt)) == []


def test_alias_titles_are_registry_laws_or_documented() -> None:
    titles = {registry_law_title(s, e).casefold() for s, e in REGISTRY.items()}
    for alias, title in ec._CITATION_ALIASES.items():
        in_registry = title.casefold() in titles
        documented = title in ALIAS_LAWS_WITHOUT_REGISTRY_ENTRY
        assert in_registry != documented, (alias, title)
        assert bool(KNOWN_ABBREVIATION_REGISTRY_ABBREVS[alias]) == in_registry, alias


def test_alias_keys_agree_with_same_named_registry_abbrevs() -> None:
    """An alias equal to a non-rt_api registry abbrev must name that law."""
    by_abbrev = {e["abbrev"]: (s, e) for s, e in REGISTRY.items()}
    for alias, title in ec._CITATION_ALIASES.items():
        if alias not in by_abbrev or alias in ALIAS_KEYS_SHADOWING_AUTO_ACRONYM:
            continue
        slug, entry = by_abbrev[alias]
        assert registry_law_title(slug, entry).casefold() == title.casefold(), alias
    for alias in ALIAS_KEYS_SHADOWING_AUTO_ACRONYM:
        assert by_abbrev[alias][1]["source"] == "auto", alias


def test_fullname_genitive_targets_are_known() -> None:
    for genitive, abbrev in ec.FULLNAME_GENITIVE.items():
        assert abbrev in KNOWN_ABBREVIATIONS, genitive
    assert ec.FULLNAME_GENITIVE["koolieelse lasteasutuse seaduse"] == "KELS"
    assert ec.FULLNAME_GENITIVE["avaliku teenistuse seaduse"] == "ATS"
    assert ec.FULLNAME_GENITIVE["tervishoiuteenuste korraldamise seaduse"] == "TTKS"


def test_build_known_abbreviations_rules() -> None:
    registry = {
        "a_seadus": {"abbrev": "AS", "source": "rt_api", "title": "A seadus (Riigi Teataja)"},
        "b_seadus": {"abbrev": "AS_2", "source": "rt_api", "title": "B seadus"},
        "c_seadus": {"abbrev": "CS_2", "source": "rt_api", "title": "C seadus"},
        "d_seadus": {"abbrev": "DD", "source": "auto", "title": "D seadus"},
    }
    known, regs, titles = build_known_abbreviations(
        registry, aliases={"AS": "X seadus", "DS": "D seadus"}
    )
    assert known["AS"] == "A seadus"  # unsuffixed wins; rt_api beats the alias
    assert known["CS"] == "C seadus" and regs["CS"] == ("CS_2",)
    assert known["DS"] == "D seadus" and regs["DS"] == ("DD",)
    assert "DD" not in known  # auto acronyms are not citation keys
    assert titles["b seadus"] == ("AS_2",)


def test_missing_registry_degrades_to_aliases(tmp_path) -> None:
    assert load_law_abbreviation_registry(tmp_path / "nope.json") == {}
    known, regs, _ = build_known_abbreviations({}, aliases={"KarS": "Karistusseadustik"})
    assert known == {"KarS": "Karistusseadustik"} and regs == {"KarS": ()}

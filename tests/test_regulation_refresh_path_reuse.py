"""A regulation refresh keeps committed paths and provision IRIs (wave 6).

546 committed regulation peeps kept the pre-#346 ``slugify`` spelling
(``…_moodustamine__t1030482_peep.json``). ``make_filename`` now yields
``…_moodustamine_t1030482_peep.json``, so a refresh that trusted it wrote a
second peep for the same act and stranded the old one with identical IRIs.
"""

from __future__ import annotations

from pathlib import Path

from estleg.generate_regulations import (
    ProvisionIriPins,
    existing_regulation_paths,
    existing_regulation_tids,
    make_filename,
    provision_display_key,
)


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    return path


def test_existing_paths_keyed_by_tid_from_filename(tmp_path: Path):
    legacy = _touch(tmp_path / "aasnelgi_moodustamine__t1030482_peep.json")
    kov = _touch(tmp_path / "elva_vallavolikogu" / "kord_t2001_peep.json")
    _touch(tmp_path / "REGULATIONS_RIIK_INDEX.json")

    paths = existing_regulation_paths(tmp_path)

    assert paths == {"1030482": legacy, "2001": kov}
    assert existing_regulation_tids(tmp_path) == {"1030482", "2001"}


def test_legacy_filename_differs_from_current_slug(tmp_path: Path):
    """The committed spelling is NOT what make_filename produces today, which
    is why main() must prefer the committed path for a known tid."""
    title = "Aasnelgi ja kuninga-kuuskjala loo püsielupaiga moodustamine ja kaitse"
    current, _slug = make_filename(title, "1030482", False, None)
    assert "__t1030482" not in current.name


def test_duplicate_tid_resolves_deterministically(tmp_path: Path):
    a = _touch(tmp_path / "a_slug_t7_peep.json")
    _touch(tmp_path / "b_slug_t7_peep.json")
    assert existing_regulation_paths(tmp_path) == {"7": a}


# ── provision IRI pinning (legacy positional suffixes) ─────────────────────

def _prov(suffix: str, display: str) -> dict:
    return {
        "@id": f"estleg:Reg_9_Par_{suffix}",
        "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
        "estleg:paragrahv": display,
    }


def test_display_key_normalises_superscript_spellings():
    assert provision_display_key("§ 10¹.") == "§10^1"
    assert provision_display_key("§ 10<sup>1</sup>.") == "§10^1"
    assert provision_display_key("§ 4′1") == "§4^1"
    assert provision_display_key("§ 4") != provision_display_key("§ 41")


def test_pins_keep_committed_suffix_when_a_paragraph_is_inserted():
    """RT inserted § 9¹: the legacy counter now mints 10_12 for § 10¹ and
    hands 10_11's slot elsewhere. Pins keep 10_11 for § 10¹."""
    committed = {"@graph": [_prov("10", "§ 10."), _prov("10_11", "§ 10¹.")]}
    pins = ProvisionIriPins.from_doc(committed, "Reg_9")
    assert pins.resolve("§ 9<sup>1</sup>.", "9_10") == "9_10"
    assert pins.resolve("§ 10.", "10") == "10"
    assert pins.resolve("§ 10<sup>1</sup>.", "10_12") == "10_11"


def test_new_provision_never_takes_a_committed_suffix():
    committed = {"@graph": [_prov("5_5", "§ 5¹."), _prov("5_6", "§ 5².")]}
    pins = ProvisionIriPins.from_doc(committed, "Reg_9")
    # A new § 5³ whose minted suffix happens to equal § 5²'s committed one.
    assert pins.resolve("§ 5<sup>3</sup>.", "5_6") == "5_6_2"
    assert pins.resolve("§ 5<sup>1</sup>.", "5_8") == "5_5"
    assert pins.resolve("§ 5<sup>2</sup>.", "5_9") == "5_6"


def test_repeated_displays_pin_by_occurrence():
    committed = {"@graph": [_prov("1", "§ 1."), _prov("1_3", "§ 1.")]}
    pins = ProvisionIriPins.from_doc(committed, "Reg_9")
    assert pins.resolve("§ 1.", "1") == "1"
    assert pins.resolve("§ 1.", "1_7") == "1_3"

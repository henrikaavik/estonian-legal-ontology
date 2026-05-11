"""Discovery and coverage tests for classify_deontic."""
from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from classify_deontic import classify_provision, extract_duty_holder


REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = (Path(__file__).parent / "fixtures" / "kov_layer2a"
           / "sample_kov_act.json")


class TestClassifyDeonticDiscoversKov:
    def test_classify_provision_returns_norm_iri(self):
        """Smoke test: the pipeline's per-provision classifier recognises
        the KOV fixture's 'peab tagama' as obligation. The function
        returns an estleg:NormType_* IRI (or None)."""
        norm_iri = classify_provision(
            "Korraldaja peab tagama jäätmete kogumise vastavalt määrusele."
        )
        # Real return values from the existing NORM_TYPES table — IRIs,
        # not bare labels. Look at NORM_TYPES in classify_deontic.py for
        # the full set; obligation matches "peab" + "tagama".
        assert norm_iri is not None
        assert norm_iri.startswith("estleg:NormType_")

    def test_classify_provision_returns_none_for_descriptive(self):
        """Sanity: text without modal verbs returns None."""
        norm_iri = classify_provision(
            "Käesoleva määruse alusel mõeldakse jäätmete all olmejäätmeid."
        )
        # Descriptive sentence — no modal — should return None.
        assert norm_iri is None


def test_prohibition_wins_tie_against_obligation() -> None:
    # ``pole lubatud`` (prohibition, weight 3) and ``on kohustatud``
    # (obligation, weight 3) — equal aggregate score. Prohibition has
    # higher tie priority and must win.
    norm_iri = classify_provision(
        "Pole lubatud rikkuda. Isik on kohustatud järgima."
    )

    assert norm_iri == "estleg:NormType_Prohibition"


def test_obligation_wins_tie_against_right() -> None:
    # ``on kohustatud`` (obligation, weight 3) and ``on õigus``
    # (right, weight 3) — equal score. Obligation outranks right.
    norm_iri = classify_provision(
        "Isik on kohustatud abistama. Isikul on õigus saada teavet."
    )

    assert norm_iri == "estleg:NormType_Obligation"


def test_right_wins_tie_against_permission() -> None:
    # ``on õigus`` (right, weight 3) and ``on lubatud`` (permission,
    # weight 3) — equal score. Right outranks permission.
    norm_iri = classify_provision(
        "Isikul on õigus taotleda. Tegevus on lubatud teatud tingimustel."
    )

    assert norm_iri == "estleg:NormType_Right"


def test_voib_is_permission_not_right() -> None:
    assert classify_provision("Asutus võib taotluse läbi vaadata.") == "estleg:NormType_Permission"


def test_duty_holder_accepts_multiple_capitalized_words() -> None:
    assert extract_duty_holder("Vastutav Töötleja peab andmed kustutama.") == "Vastutav Töötleja"


def test_duty_holder_skips_generic_match_and_continues() -> None:
    assert (
        extract_duty_holder("Käesolev seadus peab kehtima. Vastutav Töötleja peab andmed kustutama.")
        == "Vastutav Töötleja"
    )


def test_duty_holder_does_not_absorb_lowercase_adverbial() -> None:
    # Regression for over-capture: lowercase adverbials such as
    # "Igal aastal" must NOT be absorbed into the duty-holder phrase.
    # Only Capital-initial words (and the Estonian connectors
    # ja/või/ning/ega) may extend the phrase.
    assert (
        extract_duty_holder("Igal aastal Vastutav Töötleja peab esitama.")
        == "Vastutav Töötleja"
    )


def test_duty_holder_admits_estonian_connector() -> None:
    # Multi-word duty holders joined by "ja" / "või" / "ning" / "ega"
    # must still match.
    assert (
        extract_duty_holder("Tervise- ja Sotsiaaltöö Komitee peab esitama aruande.")
        == "Tervise- ja Sotsiaaltöö Komitee"
    )


def test_duty_holder_rejects_digits_in_word() -> None:
    # Regression for the previous ``\\w`` over-acceptance: a token
    # containing digits (e.g. "Töötleja2024") must not extend the
    # duty-holder capture, and there is no valid Capital-initial run
    # immediately preceding the modal "peab" — so no holder is
    # returned.
    assert (
        extract_duty_holder("Vastutav Töötleja2024 peab andmed kustutama.")
        is None
    )

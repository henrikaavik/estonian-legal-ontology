"""Discovery and coverage tests for classify_deontic."""
from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from classify_deontic import classify_provision, extract_duty_holder
from estleg_common import jsonld_text


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


# ---------------------------------------------------------------------------
# Regression for #276: negation handling + peab/tuleb polysemy.
#   * ``peab`` is also 3sg of *pidama* ("keeps"): "peab registrit" is NOT an
#     obligation.
#   * ``tuleb`` is also "comes".
#   * Both only count as obligation with a nearby -ma/-da/-ta infinitive.
#   * A cue negated by ei/pole/ega within a small window is skipped.
# ---------------------------------------------------------------------------

def test_peab_keeps_register_is_not_obligation() -> None:
    # "peab raamatupidamise registrit" = "keeps a register" — no infinitive
    # follows ``peab``, so it must NOT be classified as an obligation.
    assert (
        classify_provision("Ettevõtja peab raamatupidamise registrit.") is None
    )


def test_peab_with_infinitive_is_obligation() -> None:
    # "peab esitama" — ``peab`` + -ma infinitive — IS an obligation.
    assert (
        classify_provision("Isik peab esitama aruande.")
        == "estleg:NormType_Obligation"
    )


def test_tuleb_with_infinitive_is_obligation() -> None:
    # "tuleb tasuda" — ``tuleb`` + -da infinitive — IS an obligation.
    assert (
        classify_provision("Maks tuleb tasuda tähtajaks.")
        == "estleg:NormType_Obligation"
    )


def test_tuleb_comes_without_infinitive_is_not_obligation() -> None:
    # "tuleb" meaning "comes", with no infinitive nearby — not an obligation.
    assert classify_provision("Otsus tuleb järgmisel nädalal.") is None


def test_negated_modal_is_not_obligation() -> None:
    # "ei pea esitama": even though an infinitive follows, the cue is negated
    # (and ``pea`` is not even the obligation literal) — not an obligation.
    assert classify_provision("Isik ei pea esitama aruannet.") is None


def test_negation_window_skips_negated_cue() -> None:
    # A "plain" cue negated within the small preceding window is skipped, so a
    # provision that ONLY carries a negated obligation cue is unclassified.
    assert classify_provision("Isik ei ole kohustatud seda tegema.") is None


def test_prohibition_ei_tohi_still_classifies_under_negation_guard() -> None:
    # Prohibition cues that *encode* negation (e.g. ``ei tohi``) are exempt
    # from the negation guard and must still classify as prohibition.
    assert (
        classify_provision("Sellist tegevust ei tohi teha.")
        == "estleg:NormType_Prohibition"
    )


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


# ---------------------------------------------------------------------------
# Regression for #121: classify_deontic must read estleg:summary through
# jsonld_text so a fresh-generator value-object summary classifies identically
# to a normalized plain-string summary.
# ---------------------------------------------------------------------------

_DEONTIC_SENTENCE = "Korraldaja peab tagama jäätmete kogumise vastavalt määrusele."


def _classify_node_summary(node: dict) -> str | None:
    """Mirror the main()-loop read path: unwrap estleg:summary then classify."""
    summary = jsonld_text(node.get("estleg:summary", ""))
    if not summary:
        return None
    return classify_provision(summary)


def test_value_object_summary_classifies_like_plain_string() -> None:
    plain_node = {"@id": "estleg:X_Par_1", "estleg:summary": _DEONTIC_SENTENCE}
    value_object_node = {
        "@id": "estleg:X_Par_1",
        "estleg:summary": {"@value": _DEONTIC_SENTENCE, "@language": "et"},
    }
    list_node = {
        "@id": "estleg:X_Par_1",
        "estleg:summary": [{"@value": _DEONTIC_SENTENCE, "@language": "et"}],
    }

    plain_result = _classify_node_summary(plain_node)
    assert plain_result is not None
    assert plain_result.startswith("estleg:NormType_")
    assert _classify_node_summary(value_object_node) == plain_result
    assert _classify_node_summary(list_node) == plain_result


def test_value_object_summary_duty_holder_matches_plain_string() -> None:
    sentence = "Vastutav Töötleja peab andmed kustutama."
    plain_node = {"estleg:summary": sentence}
    value_object_node = {"estleg:summary": {"@value": sentence, "@language": "et"}}

    plain_holder = extract_duty_holder(jsonld_text(plain_node["estleg:summary"]))
    assert plain_holder == "Vastutav Töötleja"
    assert (
        extract_duty_holder(jsonld_text(value_object_node["estleg:summary"]))
        == plain_holder
    )


def test_classify_provision_rejects_raw_value_object() -> None:
    # The jsonld_text wrap in classify_deontic is load-bearing: passing the
    # raw {"@value": ...} dict straight to classify_provision blows up, which
    # is exactly what would happen if the wrap were removed.
    import pytest

    with pytest.raises(TypeError):
        classify_provision({"@value": _DEONTIC_SENTENCE, "@language": "et"})

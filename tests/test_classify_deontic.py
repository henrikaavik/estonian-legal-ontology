"""Discovery and coverage tests for classify_deontic."""
from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from classify_deontic import (
    _leading_permission_over_condition,
    classify_provision,
    extract_duty_holder,
)
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
# Regression for #351: ``karistatakse`` (canonical Penal Code passive) is a
# prohibition, and the polysemous judicial-discretion ``võib`` is suppressed in
# penal provisions so they are not mislabelled Permission.
# ---------------------------------------------------------------------------

def test_karistatakse_is_prohibition() -> None:
    # "… eest karistatakse … vangistusega" — the standard penal passive.
    assert (
        classify_provision("Sellise teo eest karistatakse rahatrahviga.")
        == "estleg:NormType_Prohibition"
    )


def test_karistatakse_with_voib_is_prohibition_not_permission() -> None:
    # A penal provision carrying a judicial-discretion ``võib``
    # ("kohus võib määrata") must classify as Prohibition, not Permission:
    # the weight-1 ``võib`` cue is suppressed when ``karistatakse`` is present.
    assert (
        classify_provision(
            "Ruumi kasutada andmise eest karistatakse vangistusega. "
            "Kohus võib määrata lisakaristuse."
        )
        == "estleg:NormType_Prohibition"
    )


def test_voib_alone_still_permission_without_karistatakse() -> None:
    # The ``võib`` suppression is scoped to penal provisions; a plain
    # discretionary ``võib`` with no ``karistatakse`` stays Permission.
    assert (
        classify_provision("Asutus võib taotluse läbi vaadata.")
        == "estleg:NormType_Permission"
    )


# ---------------------------------------------------------------------------
# Regression for #381: predicative mandatory cues with no modal polysemy —
# ``on kohustuslik`` / ``on nõutav`` / ``on nõutud``.
# ---------------------------------------------------------------------------

def test_on_kohustuslik_is_obligation() -> None:
    assert (
        classify_provision("Audiitorkontroll on kohustuslik.")
        == "estleg:NormType_Obligation"
    )


def test_on_noutav_is_obligation() -> None:
    assert (
        classify_provision("Tegevusluba on nõutav kõikidele ettevõtjatele.")
        == "estleg:NormType_Obligation"
    )


def test_on_noutud_is_obligation() -> None:
    assert (
        classify_provision("Kirjalik nõusolek on nõutud enne andmete edastamist.")
        == "estleg:NormType_Obligation"
    )


# ---------------------------------------------------------------------------
# Regression for #329: obligation recognised when the infinitive precedes the
# modal cue (SOV order) and when it is pushed back by intervening citations.
# ---------------------------------------------------------------------------

def test_sov_infinitive_before_peab_is_obligation() -> None:
    # "Tasuda peab töötaja." — infinitive ``Tasuda`` precedes ``peab``.
    assert (
        classify_provision("Tasuda peab töötaja.")
        == "estleg:NormType_Obligation"
    )


def test_sov_infinitive_before_tuleb_is_obligation() -> None:
    # "Esitada tuleb dokument." — infinitive ``Esitada`` precedes ``tuleb``.
    assert (
        classify_provision("Esitada tuleb dokument.")
        == "estleg:NormType_Obligation"
    )


def test_trailing_infinitive_past_citation_is_obligation() -> None:
    # The confirming infinitive ``määrata`` is pushed past the old 6-token
    # window by an intervening "§-s 951 …" citation; the widened window
    # (10) still recognises the obligation.
    assert (
        classify_provision(
            "Toetus tuleb käesoleva seaduse §-s 951 sätestatud korras määrata."
        )
        == "estleg:NormType_Obligation"
    )


def test_preceding_infinitive_beyond_window_is_not_obligation() -> None:
    # The backward scan is bounded by _INFINITIVE_WINDOW (10): an infinitive
    # that sits further back than the window must NOT confirm the modal, so a
    # bare "comes" reading of ``tuleb`` stays unclassified.
    assert (
        classify_provision(
            "Esitada üks kaks kolm neli viis kuus seitse kaheksa "
            "üheksa kümme tuleb otsus."
        )
        is None
    )


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


# ---------------------------------------------------------------------------
# Regression for #584: four systematic deontic mislabel patterns.
#   1. ``ei või`` ("may not") = prohibition — the negated ``võib`` form that
#      \bvõib\b never matched.
#   2. ``peavad`` (plural "must") = obligation — the plural counterpart of the
#      singular ``peab`` that was the only listed modal.
#   3. leading-permission with a trailing ``kui`` condition mislabelled
#      obligation via tie-priority.
#   4. split ``on … keelatud`` ("is … forbidden") that strict adjacency missed.
# ---------------------------------------------------------------------------


def test_ei_voi_is_prohibition() -> None:
    # "Müüja ei või tugineda …" ("the seller may not rely …") — the negated
    # ``võib`` form. Previously fell through to Permission/Obligation because
    # \bvõib\b cannot match the verb form "või".
    assert (
        classify_provision("Müüja ei või tugineda kokkuleppele.")
        == "estleg:NormType_Prohibition"
    )


def test_ei_voi_outranks_competing_permission() -> None:
    # A provision that also contains a bare permissive ``võib`` must still be
    # Prohibition: ``ei või`` (weight 4) outweighs the weight-1 ``võib``.
    assert (
        classify_provision(
            "Isik võib taotleda luba, kuid ei või seda edasi anda."
        )
        == "estleg:NormType_Prohibition"
    )


def test_ei_voi_is_not_cancelled_by_negation_guard() -> None:
    # ``ei või`` is a NEGATIVE cue (it encodes its own negation), so the
    # ei/pole/ega guard must NOT cancel it — the leading "ei" is part of the
    # cue, not a separate negator that suppresses it.
    assert (
        classify_provision("Isik ei või seda teha.")
        == "estleg:NormType_Prohibition"
    )


def test_peavad_plural_with_infinitive_is_obligation() -> None:
    # "Töötajad peavad esitama …" — plural ``peavad`` + -ma infinitive.
    assert (
        classify_provision("Töötajad peavad esitama aruande tähtajaks.")
        == "estleg:NormType_Obligation"
    )


def test_peavad_keeps_without_infinitive_is_not_obligation() -> None:
    # ``peavad`` is polysemous ("they keep …") exactly like ``peab``; with no
    # confirming infinitive it must NOT score as obligation (MODAL guard).
    assert classify_provision("Ettevõtjad peavad raamatupidamise registrit.") is None


def test_peavad_sov_infinitive_before_modal_is_obligation() -> None:
    # SOV order: the infinitive precedes plural ``peavad`` (#329 path applies
    # to the new plural cue too).
    assert (
        classify_provision("Esitada peavad kõik ettevõtjad.")
        == "estleg:NormType_Obligation"
    )


def test_split_on_keelatud_is_prohibition() -> None:
    # "Ruumides on läbiotsimine keelatud." — subject between copula and
    # participle; strict ``on keelatud`` adjacency missed it.
    assert (
        classify_provision("Ruumides on läbiotsimine keelatud.")
        == "estleg:NormType_Prohibition"
    )


def test_split_on_keelatud_with_zone_subject_is_prohibition() -> None:
    assert (
        classify_provision("Tegevus on sihtkaitsevööndis keelatud.")
        == "estleg:NormType_Prohibition"
    )


def test_split_keelatud_does_not_cross_sentence_boundary() -> None:
    # The {1,6}-gap forbids a period, so an ``on`` in one sentence and a
    # ``keelatud`` in the next must NOT be glued into a false prohibition.
    assert (
        classify_provision("Tegevus on lubatud. Hoopis muu asi keelatud sõna.")
        != "estleg:NormType_Prohibition"
    )


def test_strict_on_keelatud_still_prohibition() -> None:
    # Regression guard: the strict-adjacency form is unaffected.
    assert (
        classify_provision("Suitsetamine on keelatud.")
        == "estleg:NormType_Prohibition"
    )


def test_leading_permission_with_kui_condition_is_permission() -> None:
    # "(1) Amet võib otsuse kehtetuks tunnistada, kui isik on esitanud
    # taotluse." — the permission is the operative main-clause norm; the
    # obligation cue ("on esitanud") lives inside the trailing ``kui``
    # condition and must NOT steal the label via tie-priority.
    assert (
        classify_provision(
            "(1) Amet võib otsuse kehtetuks tunnistada, kui isik on "
            "esitanud taotluse."
        )
        == "estleg:NormType_Permission"
    )


def test_leading_permission_helper_detects_main_clause_voib() -> None:
    # Main-clause ``võib`` + a ``kui`` condition is the target shape.
    assert _leading_permission_over_condition("(1) Amet võib otsuse teha, kui …") is True


def test_leading_permission_helper_false_when_voib_only_after_boundary() -> None:
    # A ``võib`` that appears only AFTER the subordinate boundary is inside the
    # condition, not leading, AND the main clause states a genuine obligation —
    # so the boost must not fire.
    assert (
        _leading_permission_over_condition(
            "Isik on kohustatud tegutsema, kui amet võib nõuda."
        )
        is False
    )


def test_leading_permission_helper_false_without_boundary() -> None:
    # Two independent sentences, no ``kui`` / ``:`` / ``;`` condition: the
    # permission is not "leading over a condition", so tie-priority (not the
    # boost) must decide. Regression for the over-fire that flipped
    # Right-vs-Permission.
    assert (
        _leading_permission_over_condition(
            "Isikul on õigus taotleda. Tegevus on lubatud teatud tingimustel."
        )
        is False
    )


def test_leading_permission_boost_does_not_fire_against_prohibition() -> None:
    # The boost is gated on prohibition being absent: a leading permission that
    # competes with a prohibition cue must let the prohibition win on weight.
    assert (
        classify_provision("Amet võib otsuse teha, kuid tegevus on keelatud.")
        == "estleg:NormType_Prohibition"
    )


def test_genuine_obligation_without_leading_permission_unaffected() -> None:
    # No leading permission → no boost → a real obligation classifies normally.
    assert (
        classify_provision("Isik on kohustatud esitama aruande, kui amet seda nõuab.")
        == "estleg:NormType_Obligation"
    )

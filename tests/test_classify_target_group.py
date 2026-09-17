"""Tests for scripts/classify_target_group.py."""

from __future__ import annotations

import json


def test_classify_text_covers_all_target_group_enums():
    from estleg.classify_target_group import classify_text

    assert classify_text("Töötaja peab esitama andmed") == ["citizen"]
    assert classify_text("Tööandja peab pidama arvestust") == ["business"]
    assert classify_text("Keskkonnaamet võib anda loa") == ["public_body"]
    assert classify_text("Ametnik on kohustatud kontrollima") == ["official"]
    assert classify_text("Mittetulundusühing võib taotleda toetust") == ["ngo"]


def test_classify_node_accepts_multi_valued_duty_holder():
    from estleg.classify_target_group import classify_node

    node = {
        "estleg:dutyHolder": "Tööandja ja töötaja",
        "estleg:summary": "Pooltel on õigus kokku leppida.",
    }

    groups, used_duty_holder = classify_node(node)

    assert used_duty_holder is True
    assert groups == ["citizen", "business"]


def test_classify_node_uses_deontic_text_when_duty_holder_missing():
    from estleg.classify_target_group import classify_node

    node = {
        "estleg:summary": "Tarbijal on õigus saada ettevõtjalt teavet.",
    }

    groups, used_duty_holder = classify_node(node)

    assert used_duty_holder is False
    assert groups == ["citizen", "business"]


# ---------------------------------------------------------------------------
# Regression for #277:
#   (a) cues that used to live in BOTH citizen and business no longer force
#       a [citizen, business] result;
#   (b) "juriidiline isik" (a legal entity) is business, not citizen;
#   (c) when a dutyHolder classifies, body cues are not unioned in.
# ---------------------------------------------------------------------------

def test_shared_cue_kasutaja_is_single_valued():
    from estleg.classify_target_group import classify_text

    # ``kasutaja`` ("user") used to be in both lists → always [citizen, business].
    assert classify_text("Kasutaja peab maksma teenuse eest.") == ["citizen"]


def test_shared_cue_volgnik_is_single_valued():
    from estleg.classify_target_group import classify_text

    assert classify_text("Võlgnik peab tasuma võla.") == ["citizen"]


def test_shared_cue_maksumaksja_is_business_only():
    from estleg.classify_target_group import classify_text

    # ``maksumaksja`` / ``maksukohustuslane`` resolve to business only now.
    assert classify_text("Maksumaksja peab esitama deklaratsiooni.") == ["business"]
    assert classify_text("Maksukohustuslane peab tasuma maksu.") == ["business"]


def test_juriidiline_isik_is_business_not_citizen():
    from estleg.classify_target_group import classify_text

    assert classify_text("Juriidiline isik peab esitama aruande.") == ["business"]
    assert classify_text("Juriidilise isiku kohustus on esitada andmed.") == [
        "business"
    ]


def test_fuusiline_and_bare_isik_remain_citizen():
    from estleg.classify_target_group import classify_text

    assert classify_text("Füüsiline isik peab esitama taotluse.") == ["citizen"]
    assert classify_text("Isik peab esitama taotluse.") == ["citizen"]


def test_duty_holder_group_preferred_over_body_cues():
    from estleg.classify_target_group import classify_node

    # The dutyHolder (Tööandja = business) classifies, so the body mention of
    # ``töötaja`` (citizen) must NOT be unioned in — result is business only.
    node = {
        "estleg:dutyHolder": "Tööandja",
        "estleg:summary": "Töötaja peab täitma tööandja korraldusi.",
    }
    groups, used_duty_holder = classify_node(node)
    assert used_duty_holder is True
    assert groups == ["business"]


def test_unmapped_duty_holder_falls_back_to_body_cues():
    from estleg.classify_target_group import classify_node

    # When the dutyHolder literal does not classify, the body cues are used.
    node = {
        "estleg:dutyHolder": "Tundmatu roll",
        "estleg:summary": "Tarbija peab esitama kaebuse.",
    }
    groups, used_duty_holder = classify_node(node)
    assert used_duty_holder is True
    assert groups == ["citizen"]


# ---------------------------------------------------------------------------
# Regression for #330:
#   the ``public_body`` school cue ``kool`` must match real school inflections
#   (kool / kooli / koolid …) but must NOT over-match ``koolitus*``
#   ("training"), which is not a public body.
# ---------------------------------------------------------------------------

def test_koolitus_does_not_match_public_body():
    from estleg.classify_target_group import classify_text

    # Evidence from the issue: training participation, no school entity.
    assert "public_body" not in classify_text("Koolitusel osalemine on kohustuslik.")
    # A bare training sentence classifies to nothing here (no other cue present).
    for sentence in (
        "Koolitus on kohustuslik.",
        "Koolituse läbimine on nõutav.",
        "Koolitusele tuleb registreeruda.",
    ):
        assert "public_body" not in classify_text(sentence)


def test_real_kool_still_matches_public_body():
    from estleg.classify_target_group import classify_text

    # Nominative and the enumerated case inflections must still classify.
    # (Sentences avoid incidental cues from other groups so the result is the
    # single ``public_body`` value.)
    assert classify_text("Kool peab esitama andmed.") == ["public_body"]
    assert classify_text("Kooli kodukord kinnitatakse.") == ["public_body"]
    assert classify_text("Koolis korraldatakse õpet.") == ["public_body"]
    assert classify_text("Koolid esitavad aruande.") == ["public_body"]


def test_classify_files_writes_target_groups_and_report(tmp_path):
    from estleg.classify_target_group import classify_files

    peep = tmp_path / "sample_peep.json"
    report_path = tmp_path / "target_group_report.json"
    peep.write_text(
        json.dumps(
            {
                "@context": {
                    "estleg": "https://w3id.org/estleg/",
                    "owl": "http://www.w3.org/2002/07/owl#",
                },
                "@graph": [
                    {"@id": "estleg:TEST_Map", "@type": ["owl:Ontology", "estleg:Act"]},
                    {
                        "@id": "estleg:TEST_Par_1",
                        "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                        "estleg:paragrahv": "§ 1",
                        "estleg:summary": "Tööandja ja töötaja peavad järgima korda.",
                        "estleg:dutyHolder": "Tööandja ja töötaja",
                    },
                    {
                        "@id": "estleg:TEST_Par_2",
                        "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                        "estleg:paragrahv": "§ 2",
                        "estleg:summary": "Määratlus, sihtgruppi ei tuletata.",
                        "estleg:dutyHolder": "Tundmatu roll",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = classify_files([peep], report_path=report_path)
    doc = json.loads(peep.read_text(encoding="utf-8"))
    by_id = {node["@id"]: node for node in doc["@graph"]}

    assert by_id["estleg:TEST_Par_1"]["estleg:targetGroup"] == [
        {"@id": "estleg:TargetGroup_Citizen"},
        {"@id": "estleg:TargetGroup_Business"},
    ]
    assert "estleg:targetGroup" not in by_id["estleg:TEST_Par_2"]
    assert report["summary"]["provisions_with_dutyHolder"] == 2
    assert report["summary"]["dutyHolder_classified"] == 1
    assert report["summary"]["files_changed"] == 1
    assert report_path.exists()
    assert report["top_unmapped_duty_holders"] == [
        {"value": "Tundmatu roll", "count": 1}
    ]


def test_seltsing_is_not_ngo():
    """#576: ``seltsing`` (a civil-law partnership) must not match the NGO
    ``selts*`` cue; ``selts``/``seltsi`` (society) still does."""
    from estleg.classify_target_group import classify_text

    assert "ngo" not in classify_text("Seltsingu liikmed vastutavad solidaarselt.")
    assert "ngo" in classify_text("Selts korraldab heategevuslikke üritusi.")
    assert "ngo" in classify_text("Seltsi põhikiri kinnitatakse üldkoosolekul.")


def test_bare_juhatus_is_not_business():
    """#576: a bare ``juhatus`` (board) is not a business cue — MTÜs and public
    bodies have one too; only a company's board signals business."""
    from estleg.classify_target_group import classify_text

    assert "business" not in classify_text("Mittetulundusühingu juhatus otsustas.")
    assert "business" not in classify_text("Sihtasutuse juhatuse liige esitab aruande.")
    assert "business" in classify_text("Osaühingu juhatus kinnitas majandusaasta aruande.")
    assert "business" in classify_text("Aktsiaseltsi juhatus kutsub kokku üldkoosoleku.")


def test_body_union_capped_when_no_duty_holder():
    """#576: a body-text fallback union with ≥3 incidental addressees is capped
    to the two highest-priority groups; the dutyHolder path is never capped."""
    from estleg.classify_target_group import classify_node

    # Mentions cues for citizen, business, ngo, official, public_body.
    node = {
        "estleg:summary": (
            "Töötaja ja tööandja ja mittetulundusühing ja ametnik ja amet "
            "ja kohus tegutsevad koos."
        ),
    }
    groups, used_duty = classify_node(node)
    assert used_duty is False
    assert len(groups) == 2
    assert groups == ["citizen", "business"]  # top-2 by TARGET_GROUP_ORDER

    # With a dutyHolder that classifies, the (authoritative) result is NOT capped.
    node2 = {"estleg:dutyHolder": "töötaja ja tööandja ja mittetulundusühing"}
    groups2, used_duty2 = classify_node(node2)
    assert used_duty2 is True
    assert len(groups2) >= 3  # citizen + business + ngo all retained


def test_alcohol_handler_is_not_citizen_only():
    """#460: alkoholikäitleja / ettevõtja duties must not be citizen-only."""
    from estleg.classify_target_group import classify_node

    node = {
        "estleg:summary": (
            "Alkoholikäitleja on isik, kes tegeleb alkoholi käitlemisega. "
            "Alkoholikäitleja ja ettevõtja peavad täitma arvestuskohustust."
        ),
    }
    groups, _ = classify_node(node)
    assert "business" in groups
    assert groups != ["citizen"]
    assert "citizen" not in groups

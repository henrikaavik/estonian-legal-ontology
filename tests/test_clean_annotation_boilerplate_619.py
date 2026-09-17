"""Tests for issue #619 — conservative offline strip of Õiguskantsler
office-letter boilerplate from the stale ``estleg:annotationText`` layer.

The stale build collapsed each letter header onto a single line, so the #324
line-anchored stripper never matched. ``clean_annotation_boilerplate_619`` strips
that collapsed header (recipient block + ``Teie … nr … Meie … nr <ref-id>``
reference block + repeated title + salutation) WITHOUT scraping and WITHOUT ever
removing real body content.

Synthetic fixtures here are modelled on the real corpus shapes observed while
designing the script (see the issue): reference-block-only, ref+salutation,
recipient+ref+repeated-title+salutation, ``Meie``-only ``Ettepanek`` letters,
and clean bodies that must be left untouched. All run in-memory / tmp_path.
"""
from __future__ import annotations

import json

import pytest

from estleg import clean_annotation_boilerplate_619 as clean

# ---------------------------------------------------------------------------
# Real-corpus-shaped fixtures
# ---------------------------------------------------------------------------

# V1: ref block (Teie+Meie) + repeated title + lowercase-role salutation.
V1_TITLE = "Maa hindamise seaduse ja maamaksuseaduse põhiseaduspärasus"
V1_BODY = (
    "Palusite õiguskantsleril hinnata, kas maamaksuseadus ning maa hindamise "
    "seadus on kooskõlas põhiseadusega. Soovisite ennekõike teada, kas 2022."
)
V1 = (
    f"{V1_TITLE}\n\n"
    f"Teie 12.05.2026 nr Meie 25.05.2026 nr 6-1/261230/2604420 {V1_TITLE} "
    f"Lugupeetud avaldaja {V1_BODY}"
)

# V2: recipient letterhead (names + e-mail) BEFORE the ref, repeated title, then a
# Capitalised-NAME salutation recipient (must be left dangling, never eaten).
V2_TITLE = "Erahuvikoolide rahastamine"
V2_BODY_REAL = (
    "Eesti Erahuvikoolide Liidu kohtumisel õiguskantsleri nõunikega palusite "
    "kontrollida hariduskulude jaotust."
)
V2 = (
    f"{V2_TITLE}\n\n"
    "Jaanus Õun juhatuse liige Eesti Erahuvikoolide Liit MTÜ "
    "eehkl@erahuvikoolid.ee Teie nr Meie 20.05.2026 nr 6-1/261206/2604285 "
    f"{V2_TITLE} Lugupeetud Jaanus Õun {V2_BODY_REAL}"
)

# V3: ``Ettepanek`` — recipient block, ``Meie``-only ref (no ``Teie``), FULL repeated
# title (head is an abbreviation), lowercase multi-word role salutation.
V3_HEAD = "Ettepanek viia Tallinna LVK 15.12.2022 määrus nr 24 kooskõlla PS-ga"
V3_BODY_REAL = (
    "Eesti Vabariigi põhiseaduse (PS) § 142 lõike 1 ja õiguskantsleri seaduse "
    "§ 17 alusel teen ettepaneku viia määrus kooskõlla põhiseadusega."
)
V3 = (
    f"{V3_HEAD}\n\n"
    "Mihhail Kõlvart Tallinna Linnavolikogu lvpost@tallinnlv.ee "
    "Meie 11.05.2026 nr 6-4/260933/2603965 Ettepanek viia Tallinna "
    "Linnavolikogu 15.12.2022 määrus nr 24 kooskõlla põhiseadusega "
    f"Austatud volikogu esimees {V3_BODY_REAL}"
)

# V4: reference block but NO salutation; the repeated title is VERBATIM (head).
V4_TITLE = "Ettepanek viia Loksa Linnavalitsuse 25.05.2023 määrus nr 3 kooskõlla põhiseadusega"
V4_BODY_REAL = (
    "Eesti Vabariigi põhiseaduse § 142 lõike 1 alusel teen ettepaneku."
)
V4 = (
    f"{V4_TITLE}\n\n"
    f"Meie 20.05.2026 nr 6-4/260111/2603111 {V4_TITLE} {V4_BODY_REAL}"
)

# Clean body — no boilerplate at all; must be returned untouched.
CLEAN_TEXT = (
    "Mingi pealkiri\n\n"
    "Eesti Vabariigi põhiseaduse § 14 kohaselt on igaühel õigus elule ja "
    "vabadusele ning see on tagatud seadusega."
)


# ---------------------------------------------------------------------------
# Per-variant strip behaviour
# ---------------------------------------------------------------------------

def test_v1_ref_repeated_title_and_lowercase_salutation():
    out, changed = clean.strip_boilerplate(V1)
    assert changed is True
    assert out == f"{V1_TITLE}\n\n{V1_BODY}"
    # residue gone, real body intact and leading.
    assert not clean.has_salutation_residue(out)
    assert not clean.has_reference_residue(out)
    body = out.split("\n\n", 1)[1]
    assert body.startswith("Palusite õiguskantsleril hinnata")


def test_v2_recipient_block_and_capitalised_name_recipient():
    out, changed = clean.strip_boilerplate(V2)
    assert changed is True
    # Salutation keyword + ref + e-mail letterhead removed; real body preserved.
    assert not clean.has_salutation_residue(out)
    assert not clean.has_reference_residue(out)
    assert "eehkl@erahuvikoolid.ee" not in out
    assert "6-1/261206/2604285" not in out
    assert V2_BODY_REAL in out
    # Conservative: the Capitalised name is left dangling (never eats the body).
    body = out.split("\n\n", 1)[1]
    assert body.startswith("Jaanus Õun")


def test_v3_meie_only_ettepanek_multiword_role_recipient():
    out, changed = clean.strip_boilerplate(V3)
    assert changed is True
    assert not clean.has_salutation_residue(out)
    assert "6-4/260933/2603965" not in out
    assert "lvpost@tallinnlv.ee" not in out
    body = out.split("\n\n", 1)[1]
    # "volikogu esimees" (lowercase roles) consumed; real body leads.
    assert body.startswith("Eesti Vabariigi põhiseaduse")
    assert V3_BODY_REAL in out
    # The (abbreviated) head title is kept verbatim, not replaced by the body copy.
    assert out.split("\n\n", 1)[0] == V3_HEAD


def test_v4_reference_block_without_salutation_strips_verbatim_title():
    out, changed = clean.strip_boilerplate(V4)
    assert changed is True
    assert not clean.has_reference_residue(out)
    body = out.split("\n\n", 1)[1]
    assert body.startswith("Eesti Vabariigi põhiseaduse § 142")
    # The repeated title copy is removed; it appears once (the head), not twice.
    assert body.count("Loksa Linnavalitsuse") == 0


def test_salutation_at_body_start_without_reference_block():
    text = "Pealkiri\n\nLugupeetud avaldaja Palusite midagi praegu hinnata seaduses."
    out, changed = clean.strip_boilerplate(text)
    assert changed is True
    assert out == "Pealkiri\n\nPalusite midagi praegu hinnata seaduses."


def test_redaction_recipient_is_consumed():
    text = (
        "Pealkiri\n\nTeie nr Meie 01.01.2026 nr 6-1/100000/2600000 Pealkiri "
        "Lugupeetud [ ] Pöördusite õiguskantsleri poole murega laste pärast."
    )
    out, changed = clean.strip_boilerplate(text)
    assert changed is True
    assert out == "Pealkiri\n\nPöördusite õiguskantsleri poole murega laste pärast."


def test_stacked_salutations_multi_recipient_letter():
    # Real shape: a letter addressed to two officials carries two consecutive
    # salutations; BOTH keywords must go, the real body must lead.
    text = (
        "Orto- ja kaldaerofotode avalikustamine\n\n"
        "Majandusministeerium info@mkm.ee Teie nr Meie 27.11.2025 nr "
        "7-7/252405/2508574 Maa- ja Ruumiamet maaruum@maaruum.ee Orto- ja "
        "kaldaerofotode avalikustamine Lugupeetud minister Lugupeetud Maa- ja "
        "Ruumiameti peadirektor Õiguskantsleri poole on pöördunud mitu inimest."
    )
    out, changed = clean.strip_boilerplate(text)
    assert changed is True
    assert not clean.has_salutation_residue(out)   # neither salutation survives
    body = out.split("\n\n", 1)[1]
    assert body.startswith("Maa- ja Ruumiameti peadirektor Õiguskantsleri poole on pöördunud")


def test_stacked_salutations_with_dangling_name_between():
    # First recipient is a Capitalised name (left dangling), then a 2nd salutation.
    text = (
        "Pealkiri\n\nMeie 10.02.2026 nr 7-5/260065/2601148 Pealkiri "
        "Austatud volikogu esimees Lauri Luik Austatud linnapea Olavi Seisonen "
        "Haapsalu Linnavolikogu kehtestas otsuse number 162 eelmisel aastal."
    )
    out, changed = clean.strip_boilerplate(text)
    assert changed is True
    assert not clean.has_salutation_residue(out)
    assert "Haapsalu Linnavolikogu kehtestas otsuse" in out


def test_space_corrupted_reference_id_is_stripped():
    text = (
        "Erahuvikoolide rahastamine\n\n"
        "Eesti Erahuvikoolide Liit Teie 20.06.2016 nr Meie 29.06.2016 nr "
        "6- 1/161234/2016035 Erahuvikoolide rahastamine Austatud avaldaja "
        "Õiguskantsleri poole pöördus liit hariduskulude küsimuses täna."
    )
    out, changed = clean.strip_boilerplate(text)
    assert changed is True
    assert not clean.has_reference_residue(out)
    assert not clean.has_salutation_residue(out)
    body = out.split("\n\n", 1)[1]
    assert body.startswith("Õiguskantsleri poole pöördus liit")


def test_letterhead_without_teie_meie_cleaned_via_email_signal():
    # Some letterheads open straight with the addressee + e-mail and a bare
    # "<date> nr <id>" (no Teie/Meie). The e-mail is the recipient signal that
    # lets the salutation wipe the whole header.
    text = (
        "Keskkonnahariduslike õppeprogrammide rahastamine\n\n"
        "Hr Andres Sutt Kliimaministeerium info@kliimaministeerium.ee "
        "17.10.2025 nr 7-6/251426/2507526 Keskkonnahariduslike õppeprogrammide "
        "rahastamine Lugupeetud energeetika- ja keskkonnaminister "
        "Õiguskantsleri poole pöördus inimene keskkonnahariduse rahastamise pärast."
    )
    out, changed = clean.strip_boilerplate(text)
    assert changed is True
    assert not clean.has_salutation_residue(out)
    assert "info@kliimaministeerium.ee" not in out
    assert "7-6/251426/2507526" not in out
    body = out.split("\n\n", 1)[1]
    assert body.startswith("Õiguskantsleri poole pöördus inimene")


def test_access_restriction_preamble_letterhead_is_stripped():
    # Confidential letters open with an access-restriction stamp before the ref.
    text = (
        "Õpilase koolist väljaarvamine\n\n"
        "ASUTUSESISESEKS KASUTAMISEKS Alus: ÕKS § 23 lg 8, AvTS § 35 lg 1 p 12 "
        "Juurdepääsupiirang kehtib kuni: 03.08.2096 Teie 25.05.2024 nr 1-2 "
        "Meie 28.06.2024 nr 7-5/240737/2403876 Õpilase koolist väljaarvamine "
        "Lugupeetud direktor Õiguskantsleri poole pöördus avaldaja kooli otsuse pärast."
    )
    out, changed = clean.strip_boilerplate(text)
    assert changed is True
    assert not clean.has_salutation_residue(out)
    assert "ASUTUSESISESEKS" not in out
    assert "2403876" not in out
    body = out.split("\n\n", 1)[1]
    assert body.startswith("Õiguskantsleri poole pöördus avaldaja")


def test_older_oiguskantsler_keyword_reference_is_stripped():
    # 2012–2013 format: outgoing ref is "Õiguskantsler <date> nr <id>", with a
    # "[Seosviit]" placeholder between the Teie and outgoing clauses.
    text = (
        "Seisukoht vastuolu mittetuvastamise kohta\n\n"
        "Teie 2.01.2013 nr [Seosviit] Õiguskantsler 28.02.2013 nr "
        "6-3/130288/1300993 Seisukoht vastuolu mittetuvastamise kohta "
        "Lugupeetud avaldaja Tanan teid avalduse eest ja vastan kusimustele."
    )
    out, changed = clean.strip_boilerplate(text)
    assert changed is True
    assert not clean.has_reference_residue(out)
    assert "6-3/130288/1300993" not in out
    body = out.split("\n\n", 1)[1]
    assert body.startswith("Tanan teid avalduse eest")


def test_body_citation_of_prior_letter_id_is_preserved():
    # A doc-id CITED in prose (no Teie/Meie) must survive — this is the
    # idempotency-critical case that a bare-nr anchor would have eaten.
    text = (
        "Distantsõppe õiguspärasus\n\n"
        "Kirjutasite, et tuginedes õiguskantsleri 15.10.2020 kirjale nr "
        "14-1/201641/2005554 on väidetud, et koolide suunamine distantsõppele "
        "on õiguspärane ja seaduslik."
    )
    out, changed = clean.strip_boilerplate(text)
    assert changed is False
    assert out == text


def test_clean_body_is_left_untouched():
    out, changed = clean.strip_boilerplate(CLEAN_TEXT)
    assert changed is False
    assert out == CLEAN_TEXT


def test_text_without_paragraph_break_is_untouched():
    text = "Teie 01.01.2026 nr Meie 02.01.2026 nr 6-1/1/2 Lugupeetud avaldaja Tekst."
    out, changed = clean.strip_boilerplate(text)
    assert changed is False
    assert out == text


# ---------------------------------------------------------------------------
# Safety — never eat real body content
# ---------------------------------------------------------------------------

def test_lowercase_body_opener_is_not_eaten():
    # "pöördusite" is a lowercase body verb, NOT a role-word: it must survive.
    text = (
        "Pealkiri\n\nMeie 01.01.2026 nr 6-1/100000/2600000 Pealkiri "
        "Lugupeetud pöördusite õiguskantsleri poole murega tervise pärast."
    )
    out, changed = clean.strip_boilerplate(text)
    assert changed is True
    assert not clean.has_salutation_residue(out)
    body = out.split("\n\n", 1)[1]
    assert body == "pöördusite õiguskantsleri poole murega tervise pärast."


def test_court_case_number_is_not_a_reference_id():
    # "nr 5-25-49" is a Riigikohus case number, NOT a Õiguskantsler doc-id; a body
    # citing it must be left intact (and the strip must stay idempotent).
    text = (
        "Pankrotiseaduse põhiseaduspärasus\n\n"
        "Riigikohtu üldkogu esitas põhiseaduslikkuse järelevalve kohtuasjas "
        "nr 5-25-49 täiendavad küsimused kohtumenetluse korra kohta."
    )
    out, changed = clean.strip_boilerplate(text)
    assert changed is False
    assert out == text
    # Even when reached as a dangling tail, a law number "nr 3" must not match.
    text2 = "Pealkiri\n\nVabariigi Valitsuse määrus nr 3 on kooskõlas seadusega."
    assert clean.strip_boilerplate(text2) == (text2, False)


def test_strip_never_empties_the_body():
    # A header-only text (no real body) must be left untouched, not blanked.
    text = "Pealkiri\n\nLugupeetud avaldaja"
    out, changed = clean.strip_boilerplate(text)
    assert changed is False
    assert out == text


def test_body_word_meie_is_not_a_reference_block():
    # "Meie hinnangul" (our view) is prose, not a Õiguskantsler reference block.
    text = (
        "Pealkiri\n\nMeie hinnangul on tegemist olukorraga, kus seadus vajab "
        "täiendamist ja muutmist tulevikus."
    )
    out, changed = clean.strip_boilerplate(text)
    assert changed is False
    assert out == text


# ---------------------------------------------------------------------------
# Idempotency — a second pass is a no-op
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [V1, V2, V3, V4])
def test_strip_is_idempotent(text):
    once, changed1 = clean.strip_boilerplate(text)
    assert changed1 is True
    twice, changed2 = clean.strip_boilerplate(once)
    assert changed2 is False
    assert twice == once


# ---------------------------------------------------------------------------
# ref-id format variants
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "refid",
    [
        "6-1/261230/2604420",   # the dominant shape
        "12-3/100000/2600000",  # two-digit chapter
        "6-10/221608/2206035",  # two-digit sub
        "6-1/2604420",          # single trailing group
    ],
)
def test_reference_id_shapes_are_stripped(refid):
    text = (
        f"Pealkiri\n\nTeie nr Meie 01.01.2026 nr {refid} Pealkiri "
        "Lugupeetud avaldaja Palusite hinnata kõnealust olukorda põhjalikult."
    )
    out, changed = clean.strip_boilerplate(text)
    assert changed is True
    assert refid not in out
    assert out == "Pealkiri\n\nPalusite hinnata kõnealust olukorda põhjalikult."


# ---------------------------------------------------------------------------
# Title derivation
# ---------------------------------------------------------------------------

def test_derive_title_prefers_head():
    assert clean.derive_title("Foo bar", "Õiguskantsleri seisukoht: Foo bar") == "Foo bar"


def test_derive_title_falls_back_to_label_prefix_strip():
    assert clean.derive_title("", "Õiguskantsleri seisukoht: Bar baz") == "Bar baz"
    assert clean.derive_title("   ", "Õiguskantsleri seisukoht:Tihe") == "Tihe"


def test_derive_title_label_without_known_prefix():
    assert clean.derive_title("", "Mingi muu pealkiri") == "Mingi muu pealkiri"


# ---------------------------------------------------------------------------
# Residue detectors (the issue-#619 metric)
# ---------------------------------------------------------------------------

def test_residue_detectors_positive():
    assert clean.has_salutation_residue("... Austatud volikogu esimees ...")
    assert clean.has_salutation_residue("... Lugupeetud avaldaja ...")
    assert clean.has_reference_residue(
        "Teie 01.01.2026 nr Meie 02.01.2026 nr 6-1/1/2 ..."
    )


def test_residue_detectors_negative():
    assert not clean.has_salutation_residue("Eesti Vabariigi põhiseaduse § 14 kohaselt.")
    assert not clean.has_reference_residue("Meie hinnangul on olukord keeruline.")


# ---------------------------------------------------------------------------
# File processing, reporting, and the LFS guard
# ---------------------------------------------------------------------------

def _make_doc(*texts: str) -> dict:
    graph = [{"@type": ["owl:Ontology"], "@id": "estleg:annotations"}]
    for i, text in enumerate(texts):
        graph.append(
            {
                "@id": f"estleg:Annotation_{i}",
                "@type": ["owl:NamedIndividual", "estleg:Annotation"],
                "estleg:annotationText": text,
                "estleg:annotationType": "interpretation",
                "rdfs:label": {"@value": f"Õiguskantsleri seisukoht: T{i}", "@language": "et"},
            }
        )
    return {"@context": {"estleg": "x"}, "@graph": graph}


def test_analyse_doc_counts_and_residue(tmp_path):
    doc = _make_doc(V1, V2, V3, V4, CLEAN_TEXT)
    report = clean.Report()
    clean.analyse_doc(doc, report)
    assert report.scanned == 5
    assert report.changed == 4            # all but CLEAN_TEXT
    assert report.sal_before >= 3
    assert report.sal_after == 0          # near-0 metric: no salutation survives
    assert report.ref_after == 0
    assert report.overstrip_70 == 0       # nothing implausibly truncated


def test_annotation_type_is_never_modified():
    doc = _make_doc(V1, V2)
    clean.analyse_doc(doc, clean.Report())
    for node in clean.iter_annotations(doc):
        assert node["estleg:annotationType"] == "interpretation"


def test_dry_run_does_not_write_but_apply_does(tmp_path):
    path = tmp_path / "ann.jsonld"
    path.write_text(json.dumps(_make_doc(V1, CLEAN_TEXT), ensure_ascii=False), encoding="utf-8")
    before = path.read_text(encoding="utf-8")

    report = clean.process_file(path, dry_run=True)
    assert report.changed == 1
    assert path.read_text(encoding="utf-8") == before  # untouched on dry-run

    report = clean.process_file(path, dry_run=False)
    assert report.changed == 1
    after = json.loads(path.read_text(encoding="utf-8"))
    cleaned = [
        n["estleg:annotationText"]
        for n in clean.iter_annotations(after)
        if n["@id"] == "estleg:Annotation_0"
    ][0]
    assert not clean.has_salutation_residue(cleaned)
    assert cleaned.split("\n\n", 1)[1].startswith("Palusite")


def test_apply_is_idempotent_on_file(tmp_path):
    path = tmp_path / "ann.jsonld"
    path.write_text(json.dumps(_make_doc(V1, V2, V3, V4), ensure_ascii=False), encoding="utf-8")
    clean.process_file(path, dry_run=False)
    first = path.read_text(encoding="utf-8")
    second_report = clean.process_file(path, dry_run=False)
    assert second_report.changed == 0
    assert path.read_text(encoding="utf-8") == first


def test_plain_string_and_value_dict_text_shapes(tmp_path):
    doc = _make_doc(V1)
    # Convert the plain-string text to a language-tagged {@value} dict.
    node = next(clean.iter_annotations(doc))
    node["estleg:annotationText"] = {"@value": V1, "@language": "et"}
    clean.analyse_doc(doc, clean.Report())
    assert isinstance(node["estleg:annotationText"], dict)
    assert not clean.has_salutation_residue(node["estleg:annotationText"]["@value"])


def test_lfs_pointer_guard_raises(tmp_path):
    path = tmp_path / "pointer.jsonld"
    path.write_text(
        "version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 123\n",
        encoding="utf-8",
    )
    with pytest.raises(SystemExit):
        clean.process_file(path, dry_run=True)

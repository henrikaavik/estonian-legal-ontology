"""Tests for fix_eurlex_metadata_582 — the offline surgical repair of wrong
EUR-Lex ``documentDate`` values and Council/Commission ``euInstitution`` swaps
(issue #582).

All tests run against synthetic nodes / tmp_path fixtures (no real corpus). They
cover the title-date parser, the institution-from-title rule, the CELEX
year/corrigendum helpers, the integrated ``fix_node`` decision logic (including
the high-confidence year guard and corrigendum exclusion), idempotency, and the
dry-run-default / ``--apply`` file behaviour.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import fix_eurlex_metadata_582 as fix

COMMISSION = fix.COMMISSION_IRI
COUNCIL = fix.COUNCIL_IRI


# --------------------------------------------------------------------------- #
# parse_title_date
# --------------------------------------------------------------------------- #
def test_parse_title_date_nominative_juuli():
    # The headline example from #582: "26. juuli 2010" -> 2010-07-26.
    assert (
        fix.parse_title_date("Komisjoni otsus, 26. juuli 2010 , täiendavate")
        == "2010-07-26"
    )


def test_parse_title_date_nominative_marts():
    # "märts" is the NOMINATIVE form EUR-Lex uses (not the genitive "märtsi").
    assert (
        fix.parse_title_date(
            "2013/168/EL: Euroopa Keskpanga otsus, 20. märts 2013 , millega"
        )
        == "2013-03-20"
    )


def test_parse_title_date_nominative_detsember():
    assert (
        fix.parse_title_date("Nõukogu otsus, 17. detsember 1999, millega muudetakse")
        == "1999-12-17"
    )


def test_parse_title_date_single_digit_day_is_zero_padded():
    assert (
        fix.parse_title_date("Komisjoni otsus, 9. august 2011 , millega nimetatakse")
        == "2011-08-09"
    )
    assert fix.parse_title_date("Komisjoni otsus, 1. juuli 1999, millega") == "1999-07-01"


def test_parse_title_date_handles_non_breaking_space():
    # EUR-Lex routinely emits U+00A0 around the date: "24.\xa0oktoober\xa01980".
    assert (
        fix.parse_title_date("Nõukogu otsus, 24. oktoober 1980, millega")
        == "1980-10-24"
    )


def test_parse_title_date_accepts_genitive_tail():
    # The shared genitive table is reused as a base, so "detsembri" still parses.
    assert (
        fix.parse_title_date("Komisjoni otsus, 5. detsembri 2006 , riigiabi")
        == "2006-12-05"
    )


def test_parse_title_date_mai_and_juuni():
    assert fix.parse_title_date("Komisjoni otsus, 1. mai 2004") == "2004-05-01"
    assert fix.parse_title_date("Komisjoni otsus, 30. juuni 2009") == "2009-06-30"


def test_parse_title_date_none_when_absent():
    assert fix.parse_title_date("Komisjoni teatis ilma kuupäevata") is None
    assert fix.parse_title_date("") is None
    # A bare number sequence without a month name must not parse.
    assert fix.parse_title_date("2010/415/EL: midagi 12. 2010") is None


# --------------------------------------------------------------------------- #
# institution_from_title
# --------------------------------------------------------------------------- #
def test_institution_council_from_noukogu():
    assert (
        fix.institution_from_title("Nõukogu määrus (EÜ) nr 1256/2008, 16. detsember 2008")
        == COUNCIL
    )


def test_institution_commission_from_komisjoni():
    assert fix.institution_from_title("Komisjoni otsus, 26. juuli 2010") == COMMISSION
    # Bare "Komisjon ..." (no -i) also matches the Komisjon prefix.
    assert fix.institution_from_title("Komisjon teatab liikmesriikidele") == COMMISSION


def test_institution_none_for_co_decision_and_third_bodies():
    # "Euroopa Parlamendi ja nõukogu …" is co-decision — must NOT map to Council
    # despite containing "nõukogu" later in the string.
    assert fix.institution_from_title("Euroopa Parlamendi ja nõukogu määrus") is None
    assert fix.institution_from_title("Euroopa Keskpanga otsus, 20. märts 2013") is None
    # A prefixed decision ("2010/415/: Komisjoni …") falls outside the
    # unambiguous single-issuer scope (raw-label startswith).
    assert fix.institution_from_title("2010/415/: Komisjoni otsus, 26. juuli 2010") is None
    assert fix.institution_from_title("") is None


# --------------------------------------------------------------------------- #
# celex_year
# --------------------------------------------------------------------------- #
def test_celex_year():
    assert fix.celex_year("32010D0415") == "2010"
    assert fix.celex_year("31992L0085") == "1992"
    assert fix.celex_year("31997D0245") == "1997"
    # A decision OJ-sequence in parens does not disturb the leading year.
    assert fix.celex_year("32013D0005(01)") == "2013"


def test_celex_year_none_for_unparseable():
    assert fix.celex_year("") is None
    assert fix.celex_year("C2020...") is None  # non-digit sector


# --------------------------------------------------------------------------- #
# is_corrigendum
# --------------------------------------------------------------------------- #
def test_is_corrigendum_true_for_trailing_marker():
    assert fix.is_corrigendum("32008R1256R(01)") is True
    assert fix.is_corrigendum("31995D0408R(02)") is True
    assert fix.is_corrigendum("32008D0936R(01)") is True
    # Two-digit corrigendum index (R(10)+) — the canonical marker still matches
    # where the issue's literal "@id ending R0+digit" would miss it.
    assert fix.is_corrigendum("32016R0679R(10)") is True


def test_is_corrigendum_false_for_non_corrigenda():
    assert fix.is_corrigendum("32008R1256") is False  # plain regulation
    assert fix.is_corrigendum("32010D0415") is False  # plain decision
    assert fix.is_corrigendum("32013D0005(01)") is False  # decision OJ-sequence
    assert fix.is_corrigendum("") is False


# --------------------------------------------------------------------------- #
# fix_node — integrated decision logic
# --------------------------------------------------------------------------- #
def _node(
    nid: str,
    celex: str,
    label: str,
    *,
    docdate: str | None = None,
    inst: str | None = None,
) -> dict:
    node: dict = {
        "@id": nid,
        "@type": ["owl:NamedIndividual", "estleg:EULegislation"],
        "estleg:celexNumber": celex,
        "rdfs:label": label,
    }
    if docdate is not None:
        node["estleg:documentDate"] = {"@value": docdate, "@type": "xsd:date"}
    if inst is not None:
        node["estleg:euInstitution"] = {"@id": inst}
    return node


def test_fix_node_date_high_confidence_correction():
    # EU_32010D0415: title 2010 == CELEX 2010, but documentDate says 2007.
    node = _node(
        "estleg:EU_32010D0415",
        "32010D0415",
        "2010/415/: Komisjoni otsus, 26. juuli 2010 , täiendavate püügipäevade",
        docdate="2007-07-04",
        inst=COMMISSION,
    )
    date_fixed, inst_fixed = fix.fix_node(node)
    assert date_fixed is True
    assert inst_fixed is False  # prefixed label -> no single-issuer match
    assert node["estleg:documentDate"]["@value"] == "2010-07-26"
    assert node["estleg:documentDate"]["@type"] == "xsd:date"  # shape preserved
    assert node["estleg:euInstitution"]["@id"] == COMMISSION  # untouched


def test_fix_node_date_skipped_when_title_year_disagrees_with_celex():
    # EU_31997D0245: title says 1977 (a title typo); CELEX + documentDate agree
    # on 1997. The CELEX cross-check must leave the documentDate alone.
    node = _node(
        "estleg:EU_31997D0245",
        "31997D0245",
        "Komisjoni otsus, 20. märts 1977, millega nähakse ette kord",
        docdate="1997-03-20",
        inst=COMMISSION,
    )
    date_fixed, inst_fixed = fix.fix_node(node)
    assert date_fixed is False
    assert inst_fixed is False
    assert node["estleg:documentDate"]["@value"] == "1997-03-20"


def test_fix_node_date_skipped_for_corrigendum():
    # A corrigendum's title date describes the parent act — never correct it.
    node = _node(
        "estleg:EU_32010D0415R01",
        "32010D0415R(01)",
        "Komisjoni otsuse, 26. juuli 2010 , parandus",
        docdate="2007-07-04",
        inst=COMMISSION,
    )
    date_fixed, inst_fixed = fix.fix_node(node)
    assert date_fixed is False
    assert inst_fixed is False
    assert node["estleg:documentDate"]["@value"] == "2007-07-04"


def test_fix_node_institution_swap_council():
    # EU_32008R1256: "Nõukogu määrus …" wrongly attributed to the Commission.
    # Same-year title/documentDate -> no date change, only the institution flip.
    node = _node(
        "estleg:EU_32008R1256",
        "32008R1256",
        "Nõukogu määrus (EÜ) nr 1256/2008, 16. detsember 2008 , millega",
        docdate="2008-12-16",
        inst=COMMISSION,
    )
    date_fixed, inst_fixed = fix.fix_node(node)
    assert date_fixed is False
    assert inst_fixed is True
    assert node["estleg:euInstitution"]["@id"] == COUNCIL


def test_fix_node_institution_swap_commission():
    node = _node(
        "estleg:EU_32004D0099",
        "32004D0099",
        "Komisjoni otsus, 2. veebruar 2004 , millega",
        docdate="2004-02-02",
        inst=COUNCIL,
    )
    _, inst_fixed = fix.fix_node(node)
    assert inst_fixed is True
    assert node["estleg:euInstitution"]["@id"] == COMMISSION


def test_fix_node_institution_unchanged_when_already_correct():
    node = _node(
        "estleg:EU_32005D0756",
        "32005D0756",
        "Nõukogu otsus, 17. oktoober 2005, millega",
        docdate="2005-10-17",
        inst=COUNCIL,
    )
    date_fixed, inst_fixed = fix.fix_node(node)
    assert (date_fixed, inst_fixed) == (False, False)
    assert node["estleg:euInstitution"]["@id"] == COUNCIL


def test_fix_node_institution_third_body_left_untouched():
    # "Nõukogu peasekretäri …" is genuinely CONSILSG, not the Council plenary —
    # the restricted Council<->Commission swap must not clobber it.
    node = _node(
        "estleg:EU_32001D0442",
        "32001D0442",
        "Nõukogu peasekretäri otsus, 1. juuli 2001 , dokumentide kohta",
        docdate="2001-07-01",
        inst="estleg:EUInst_CONSILSG",
    )
    date_fixed, inst_fixed = fix.fix_node(node)
    assert (date_fixed, inst_fixed) == (False, False)
    assert node["estleg:euInstitution"]["@id"] == "estleg:EUInst_CONSILSG"


def test_fix_node_both_date_and_institution():
    # EU_32010D0779: "Nõukogu otsus, 14. detsember 2010" typed Commission with a
    # wrong-year documentDate — both repairs fire on the one node.
    node = _node(
        "estleg:EU_32010D0779",
        "32010D0779",
        "Nõukogu otsus, 14. detsember 2010 , mis käsitleb",
        docdate="2009-12-14",
        inst=COMMISSION,
    )
    date_fixed, inst_fixed = fix.fix_node(node)
    assert (date_fixed, inst_fixed) == (True, True)
    assert node["estleg:documentDate"]["@value"] == "2010-12-14"
    assert node["estleg:euInstitution"]["@id"] == COUNCIL


def test_fix_node_is_idempotent():
    node = _node(
        "estleg:EU_32010D0779",
        "32010D0779",
        "Nõukogu otsus, 14. detsember 2010 , mis käsitleb",
        docdate="2009-12-14",
        inst=COMMISSION,
    )
    assert fix.fix_node(node) == (True, True)
    # Second pass: nothing left to change, values stable.
    assert fix.fix_node(node) == (False, False)
    assert node["estleg:documentDate"]["@value"] == "2010-12-14"
    assert node["estleg:euInstitution"]["@id"] == COUNCIL


def test_fix_node_skips_node_without_documentdate():
    # Nothing to "correct" if there is no current documentDate to contradict.
    node = _node(
        "estleg:EU_32010D0415",
        "32010D0415",
        "Komisjoni otsus, 26. juuli 2010 , midagi",
        inst=COMMISSION,
    )
    date_fixed, _ = fix.fix_node(node)
    assert date_fixed is False
    assert "estleg:documentDate" not in node


# --------------------------------------------------------------------------- #
# migrate_doc — graph iteration + node gating
# --------------------------------------------------------------------------- #
def _doc(nodes: list[dict]) -> dict:
    return {
        "@context": {"estleg": "https://data.riik.ee/ontology/estleg#"},
        "@graph": [
            {"@id": "estleg:EurlexOntology", "@type": ["owl:Ontology"]},
            *nodes,
        ],
    }


def test_migrate_doc_counts_and_skips_header_and_corrigenda():
    doc = _doc([
        _node(  # date fix
            "estleg:EU_32010D0779",
            "32010D0779",
            "Nõukogu otsus, 14. detsember 2010 , mis",
            docdate="2009-12-14",
            inst=COMMISSION,
        ),
        _node(  # institution-only fix
            "estleg:EU_32008R1256",
            "32008R1256",
            "Nõukogu määrus (EÜ) nr 1256/2008, 16. detsember 2008",
            docdate="2008-12-16",
            inst=COMMISSION,
        ),
        _node(  # corrigendum -> skipped entirely
            "estleg:EU_32010D0415R01",
            "32010D0415R(01)",
            "Komisjoni otsuse, 26. juuli 2010 , parandus",
            docdate="2007-07-04",
            inst=COMMISSION,
        ),
    ])
    stats = fix.FixStats()
    dates, insts = fix.migrate_doc(doc, stats)
    # The 32010D0779 node fixes both date and institution; 32008R1256 only inst.
    assert dates == 1
    assert insts == 2
    assert stats.nodes_scanned == 3  # the owl:Ontology header is not counted
    assert stats.corrigenda_skipped == 1
    # Header node is untouched.
    header = doc["@graph"][0]
    assert header == {"@id": "estleg:EurlexOntology", "@type": ["owl:Ontology"]}
    # Corrigendum node values are unchanged.
    corr = doc["@graph"][3]
    assert corr["estleg:documentDate"]["@value"] == "2007-07-04"
    assert corr["estleg:euInstitution"]["@id"] == COMMISSION


# --------------------------------------------------------------------------- #
# main — dry-run default vs --apply, file-level behaviour
# --------------------------------------------------------------------------- #
def _write_peep(path: Path, doc: dict) -> None:
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def _fixable_doc() -> dict:
    return _doc([
        _node(
            "estleg:EU_32010D0779",
            "32010D0779",
            "Nõukogu otsus, 14. detsember 2010 , mis",
            docdate="2009-12-14",
            inst=COMMISSION,
        ),
    ])


def test_main_dry_run_is_default_and_writes_nothing(tmp_path, capsys):
    f = tmp_path / "eurlex_decisions_peep.json"
    _write_peep(f, _fixable_doc())
    before = f.read_text(encoding="utf-8")

    rc = fix.main(["--eurlex-dir", str(tmp_path)])  # no --apply => dry-run
    assert rc == 0
    assert f.read_text(encoding="utf-8") == before  # untouched on disk

    out = capsys.readouterr().out
    assert "MODE: dry-run" in out
    assert "documentDate corrected:    1" in out
    assert "euInstitution corrected:   1" in out


def test_main_apply_writes_and_is_idempotent(tmp_path):
    f = tmp_path / "eurlex_decisions_peep.json"
    _write_peep(f, _fixable_doc())

    assert fix.main(["--eurlex-dir", str(tmp_path), "--apply"]) == 0
    doc = json.loads(f.read_text(encoding="utf-8"))
    node = doc["@graph"][1]
    assert node["estleg:documentDate"]["@value"] == "2010-12-14"
    assert node["estleg:euInstitution"]["@id"] == COUNCIL

    after_first = f.read_text(encoding="utf-8")
    # Re-applying over already-fixed data is a byte-stable no-op.
    assert fix.main(["--eurlex-dir", str(tmp_path), "--apply"]) == 0
    assert f.read_text(encoding="utf-8") == after_first


def test_main_skips_lfs_pointer(tmp_path, capsys):
    f = tmp_path / "eurlex_regulations_peep.json"
    f.write_text(
        "version https://git-lfs.github.com/spec/v1\n"
        "oid sha256:deadbeef\nsize 123\n",
        encoding="utf-8",
    )
    rc = fix.main(["--eurlex-dir", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "git-LFS pointer" in out


def test_main_errors_on_missing_directory(tmp_path):
    missing = tmp_path / "does-not-exist"
    assert fix.main(["--eurlex-dir", str(missing)]) == 1

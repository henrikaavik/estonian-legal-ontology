"""Tests for flag_unverified_sanctions_586 — the offline, data-preserving flag
for municipal-regulation sanctions whose source provision text is absent (#586).

Every test runs against synthetic in-memory nodes (no real corpus): the
per-node flagging logic is the unit under test, not corpus counts (which move
as municipal peeps are backfilled).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import flag_unverified_sanctions_586 as flag

NS = "https://data.riik.ee/ontology/estleg#"


def _sanction(target: str, *, label: str = "Fine, max 200 fine units (Reg § 8.)") -> dict:
    return {
        "@id": "estleg:Sanction_X",
        "@type": ["owl:NamedIndividual", "estleg:Sanction"],
        "estleg:sanctionType": "fine",
        "estleg:applicableProvision": {"@id": target},
        "rdfs:label": label,
    }


# ---------------------------------------------------------------------------
# should_flag — the core classification
# ---------------------------------------------------------------------------
def test_textless_reg_target_is_flagged():
    node = _sanction("estleg:Reg_1044072_Par_8")
    assert flag.should_flag(node, text_ids=set()) is True


def test_text_bearing_reg_target_not_flagged():
    node = _sanction("estleg:Reg_1044072_Par_8")
    assert flag.should_flag(node, text_ids={"estleg:Reg_1044072_Par_8"}) is False


def test_non_reg_target_not_flagged_even_when_textless():
    # A state-law provision (no Reg_<id>_Par_ shape) must never be flagged,
    # even though it is absent from the text-bearing set.
    node = _sanction("estleg:AS_Par_53")
    assert flag.should_flag(node, text_ids=set()) is False


def test_non_sanction_node_not_flagged():
    node = {
        "@id": "estleg:Reg_1044072_Par_8",
        "@type": ["estleg:LegalProvision"],
        "estleg:applicableProvision": {"@id": "estleg:Reg_1044072_Par_8"},
    }
    assert flag.should_flag(node, text_ids=set()) is False


def test_subprovision_reg_target_is_flagged():
    node = _sanction("estleg:Reg_999_Par_8_Lg_2")
    assert flag.should_flag(node, text_ids=set()) is True


def test_full_iri_reg_target_is_canonicalised_and_flagged():
    # A target serialised as an expanded IRI must still be recognised.
    node = _sanction(f"{NS}Reg_1044072_Par_8")
    assert flag.should_flag(node, text_ids=set()) is True
    # ...and matched against the canonical id in text_ids.
    assert flag.should_flag(node, text_ids={"estleg:Reg_1044072_Par_8"}) is False


# ---------------------------------------------------------------------------
# apply_flag — annotation + idempotency
# ---------------------------------------------------------------------------
def test_apply_flag_stamps_typed_boolean():
    node = _sanction("estleg:Reg_1044072_Par_8")
    assert flag.apply_flag(node) is True
    assert node["estleg:provenanceUnverified"] == {
        "@value": True,
        "@type": "xsd:boolean",
    }


def test_apply_flag_appends_issuer_to_label():
    node = _sanction("estleg:Reg_1044072_Par_8")
    assert flag.apply_flag(node, issuer="Toila Vallavolikogu") is True
    assert node["rdfs:label"] == (
        "Fine, max 200 fine units (Reg § 8.) [Toila Vallavolikogu]"
    )


def test_apply_flag_without_issuer_leaves_label():
    node = _sanction("estleg:Reg_1044072_Par_8", label="Coercive payment (Reg § 10.)")
    flag.apply_flag(node, issuer=None)
    assert node["rdfs:label"] == "Coercive payment (Reg § 10.)"


def test_apply_flag_is_idempotent():
    node = _sanction("estleg:Reg_1044072_Par_8")
    assert flag.apply_flag(node, issuer="Toila Vallavolikogu") is True
    label_after_first = node["rdfs:label"]
    # Second application is a no-op: returns False, no double-stamp / double-append.
    assert flag.apply_flag(node, issuer="Toila Vallavolikogu") is False
    assert node["rdfs:label"] == label_after_first
    assert node["rdfs:label"].count("[Toila Vallavolikogu]") == 1


def test_apply_flag_does_not_duplicate_issuer_already_in_label():
    node = _sanction(
        "estleg:Reg_1044072_Par_8",
        label="Fine (Reg § 8.) [Toila Vallavolikogu]",
    )
    # Flag still added, but the label is untouched (issuer already present).
    assert flag.apply_flag(node, issuer="Toila Vallavolikogu") is True
    assert node["rdfs:label"].count("[Toila Vallavolikogu]") == 1


# ---------------------------------------------------------------------------
# reg_num_for_sanction — act-node bridge
# ---------------------------------------------------------------------------
def test_reg_num_for_sanction():
    assert (
        flag.reg_num_for_sanction(_sanction("estleg:Reg_1044072_Par_8")) == "1044072"
    )
    assert flag.reg_num_for_sanction(_sanction("estleg:AS_Par_53")) is None


# ---------------------------------------------------------------------------
# has_legal_text — text-presence detection across the corpus's three shapes
# ---------------------------------------------------------------------------
def test_has_legal_text_shapes():
    assert flag.has_legal_text({"estleg:legalText": "Some text"}) is True
    assert flag.has_legal_text({"estleg:legalText": {"@value": "Some text"}}) is True
    assert flag.has_legal_text({"estleg:legalText": ["", "Some text"]}) is True
    assert flag.has_legal_text({"estleg:legalText": ""}) is False
    assert flag.has_legal_text({"estleg:legalText": "   "}) is False
    assert flag.has_legal_text({}) is False


# ---------------------------------------------------------------------------
# process_doc — end-to-end over a synthetic graph + run idempotency
# ---------------------------------------------------------------------------
def _doc() -> dict:
    return {
        "@context": {"estleg": NS},
        "@graph": [
            {"@id": "estleg:Sanctions_Map", "@type": ["owl:Ontology"]},
            # flagged: textless municipal-reg target, issuer resolvable
            {
                "@id": "estleg:Sanction_A",
                "@type": ["owl:NamedIndividual", "estleg:Sanction"],
                "estleg:sanctionType": "fine",
                "estleg:applicableProvision": {"@id": "estleg:Reg_111_Par_8"},
                "rdfs:label": "Fine (Reg § 8.)",
            },
            # not flagged: text-bearing reg target
            {
                "@id": "estleg:Sanction_B",
                "@type": ["owl:NamedIndividual", "estleg:Sanction"],
                "estleg:sanctionType": "fine",
                "estleg:applicableProvision": {"@id": "estleg:Reg_222_Par_1"},
                "rdfs:label": "Fine (Reg § 1.)",
            },
            # not flagged: state-law target
            {
                "@id": "estleg:Sanction_C",
                "@type": ["owl:NamedIndividual", "estleg:Sanction"],
                "estleg:sanctionType": "arrest",
                "estleg:applicableProvision": {"@id": "estleg:AS_Par_53"},
                "rdfs:label": "Arrest (AS § 53.)",
            },
        ],
    }


def test_process_doc_flags_only_textless_reg_targets_and_is_idempotent():
    doc = _doc()
    text_ids = {"estleg:Reg_222_Par_1"}
    reg_issuers = {"111": "Toila Vallavolikogu"}

    stats = flag.FlagStats()
    changed = flag.process_doc(doc, text_ids, reg_issuers, stats)
    assert changed is True
    assert stats.sanctions_scanned == 3
    assert stats.sanctions_flagged == 1
    assert stats.flags_added == 1
    assert stats.labels_enriched == 1

    nodes = {n["@id"]: n for n in doc["@graph"] if "@id" in n}
    assert nodes["estleg:Sanction_A"]["estleg:provenanceUnverified"] == {
        "@value": True,
        "@type": "xsd:boolean",
    }
    assert nodes["estleg:Sanction_A"]["rdfs:label"].endswith("[Toila Vallavolikogu]")
    assert "estleg:provenanceUnverified" not in nodes["estleg:Sanction_B"]
    assert "estleg:provenanceUnverified" not in nodes["estleg:Sanction_C"]

    # Re-run: same population flagged, but nothing newly added/enriched.
    stats2 = flag.FlagStats()
    changed2 = flag.process_doc(doc, text_ids, reg_issuers, stats2)
    assert changed2 is False
    assert stats2.sanctions_flagged == 1
    assert stats2.flags_added == 0
    assert stats2.labels_enriched == 0

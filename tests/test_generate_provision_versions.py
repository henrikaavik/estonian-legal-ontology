"""Tests for scripts/generate_provision_versions.py (issue #198).

Locks down the historical-redaction ProvisionVersion population mechanism:
provision-level diffing across redactions, the ``supersededByVersion`` chain,
validity-interval closing, redaction-list reconstruction from the Riigi Teataja
search API, ``--limit`` / ``--law`` selection, and SHACL conformance of the
emitted ``estleg:ProvisionVersion`` nodes.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

import generate_provision_versions as gpv
from generate_provision_versions import (
    LawTarget,
    Redaction,
    build_law_target,
    build_provision_backlinks,
    extract_provision_texts,
    fetch_current_redaction,
    fetch_redaction_chain,
    missing_law_target_error,
    select_law_slugs,
    synthesise_versions,
    write_sidecar,
)

_SHAPES_PATH = gpv.REPO_ROOT / "shacl" / "estonian_legal_shapes.ttl"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _shacl_conforms(graph_json: dict) -> tuple[bool, str]:
    pyshacl = pytest.importorskip("pyshacl")
    rdflib = pytest.importorskip("rdflib")
    data_graph = rdflib.Graph()
    data_graph.parse(data=json.dumps(graph_json), format="json-ld")
    shapes_graph = rdflib.Graph().parse(str(_SHAPES_PATH), format="turtle")
    conforms, _, msg = pyshacl.validate(
        data_graph, shacl_graph=shapes_graph, inference="rdfs"
    )
    return conforms, str(msg)


def _paragraph_xml(nr: str, *, title: str, body: str) -> str:
    return (
        f'<paragrahv><paragrahvNr>{nr}</paragrahvNr>'
        f'<paragrahvPealkiri>{title}</paragrahvPealkiri>'
        f'<loige><loigeNr>1</loigeNr><lauseOsa>{body}</lauseOsa></loige>'
        f'</paragrahv>'
    )


def _redaction_root(paragraphs: list[str]) -> ET.Element:
    return ET.fromstring("<akt>" + "".join(paragraphs) + "</akt>")


def _make_target(prefix: str = "FIX", par_suffixes: tuple[str, ...] = ("1", "2")) -> LawTarget:
    return LawTarget(
        slug="fixture_law",
        title="Fikseeritud seadus",
        prefix=prefix,
        provisions={s: f"estleg:{prefix}_Par_{s}" for s in par_suffixes},
        peep_files=[],
    )


# Three redactions: § 1 changes at r2, § 2 changes at r3.
_R1 = Redaction(global_id="111", valid_from="2010-01-01", valid_to="2014-12-31", url="/akt/111.xml")
_R2 = Redaction(global_id="222", valid_from="2015-01-01", valid_to="2019-12-31", url="/akt/222.xml")
_R3 = Redaction(global_id="333", valid_from="2020-01-01", valid_to=None, url="/akt/333.xml")


def _three_redaction_chain() -> list[tuple[Redaction, dict[str, str]]]:
    return [
        (_R1, {"1": "Algtekst paragrahv 1.", "2": "Algtekst paragrahv 2."}),
        (_R2, {"1": "MUUDETUD paragrahv 1 redaktsioonis 2.", "2": "Algtekst paragrahv 2."}),
        (_R3, {"1": "MUUDETUD paragrahv 1 redaktsioonis 2.", "2": "MUUDETUD paragrahv 2 redaktsioonis 3."}),
    ]


def _matches_as_of(node: dict, target: str) -> bool:
    """Mirror the canonical as-of-date SPARQL filter against a version node.

    Replicates ``docs/SCHEMA_REFERENCE.md``'s query semantics
    (``?from <= D && (!BOUND(?to) || ?to >= D)``) so the tests assert the exact
    consumer-visible behaviour issue #306 / #393 are about: how many versions a
    given as-of date selects.
    """
    valid_from = node["estleg:versionValidFrom"]["@value"]
    if valid_from > target:
        return False
    valid_to = node.get("estleg:versionValidTo")
    return valid_to is None or valid_to["@value"] >= target


# ---------------------------------------------------------------------------
# Diff + chaining
# ---------------------------------------------------------------------------


class TestSynthesiseVersions:
    """`synthesise_versions` builds one ProvisionVersion per real text change."""

    def test_three_redactions_two_changes_each_provision(self):
        target = _make_target(par_suffixes=("1", "2"))
        nodes = synthesise_versions(target, _three_redaction_chain())
        by_provision: dict[str, list[dict]] = {}
        for n in nodes:
            by_provision.setdefault(n["estleg:versionOf"]["@id"], []).append(n)

        # § 1: versions at r1 and r2 (changed at r2; unchanged at r3 → no v333).
        p1 = by_provision["estleg:FIX_Par_1"]
        assert [n["@id"] for n in p1] == ["estleg:FIX_Par_1_v111", "estleg:FIX_Par_1_v222"]
        # § 2: versions at r1 and r3 (unchanged at r2 → no v222).
        p2 = by_provision["estleg:FIX_Par_2"]
        assert [n["@id"] for n in p2] == ["estleg:FIX_Par_2_v111", "estleg:FIX_Par_2_v333"]

        # supersededByVersion chains each provision's versions; absent on the last.
        assert p1[0]["estleg:supersededByVersion"] == {"@id": "estleg:FIX_Par_1_v222"}
        assert "estleg:supersededByVersion" not in p1[1]
        assert p2[0]["estleg:supersededByVersion"] == {"@id": "estleg:FIX_Par_2_v333"}
        assert "estleg:supersededByVersion" not in p2[1]

        # Issue #306: versionValidTo of version k is the day BEFORE versionValidFrom
        # of version k+1 (exclusive end); absent on the last version.
        assert p1[0]["estleg:versionValidFrom"] == {"@value": "2010-01-01", "@type": "xsd:date"}
        assert p1[0]["estleg:versionValidTo"] == {"@value": "2014-12-31", "@type": "xsd:date"}
        assert "estleg:versionValidTo" not in p1[1]
        assert p2[1]["estleg:versionValidFrom"] == {"@value": "2020-01-01", "@type": "xsd:date"}
        assert "estleg:versionValidTo" not in p2[1]

        # Every node carries versionOf / versionText / versionRedactionId.
        for n in nodes:
            assert n["@type"] == ["owl:NamedIndividual", "estleg:ProvisionVersion"]
            assert n["estleg:versionOf"]["@id"].startswith("estleg:FIX_Par_")
            assert isinstance(n["estleg:versionText"], str) and n["estleg:versionText"]
            assert n["estleg:versionRedactionId"] in {"111", "222", "333"}

    def test_provision_never_changed_gets_one_version_no_successor(self):
        target = _make_target(par_suffixes=("1",))
        chain = [
            (_R1, {"1": "Sama tekst kogu aeg."}),
            (_R2, {"1": "Sama tekst kogu aeg."}),
            (_R3, {"1": "Sama tekst kogu aeg."}),
        ]
        nodes = synthesise_versions(target, chain)
        assert len(nodes) == 1
        n = nodes[0]
        assert n["@id"] == "estleg:FIX_Par_1_v111"
        assert n["estleg:versionValidFrom"] == {"@value": "2010-01-01", "@type": "xsd:date"}
        assert "estleg:supersededByVersion" not in n
        assert "estleg:versionValidTo" not in n

    def test_versionof_targets_resolve_to_law_provision_iris(self):
        # The provision map IS the set of LegalProvision IRIs the peep declares;
        # synthesise_versions must only ever point versionOf at one of those.
        target = _make_target(par_suffixes=("1", "2"))
        nodes = synthesise_versions(target, _three_redaction_chain())
        valid_targets = set(target.provisions.values())
        assert valid_targets == {"estleg:FIX_Par_1", "estleg:FIX_Par_2"}
        for n in nodes:
            assert n["estleg:versionOf"]["@id"] in valid_targets
        # And supersededByVersion only ever points at a node emitted in this graph.
        emitted = {n["@id"] for n in nodes}
        for n in nodes:
            sup = n.get("estleg:supersededByVersion")
            if sup is not None:
                assert sup["@id"] in emitted

    def test_provision_absent_from_a_redaction_does_not_break_chain(self):
        # § 1 missing in r2 (e.g. fetch glitch / mid-amendment), present again r3.
        target = _make_target(par_suffixes=("1",))
        chain = [
            (_R1, {"1": "Esimene tekst."}),
            (_R2, {}),  # § 1 not extracted here
            (_R3, {"1": "Teine tekst."}),
        ]
        nodes = synthesise_versions(target, chain)
        assert [n["@id"] for n in nodes] == ["estleg:FIX_Par_1_v111", "estleg:FIX_Par_1_v333"]
        # Issue #306: exclusive end — day before the successor's 2020-01-01 start.
        assert nodes[0]["estleg:versionValidTo"] == {"@value": "2019-12-31", "@type": "xsd:date"}

    def test_whitespace_only_difference_is_not_a_new_version(self):
        target = _make_target(par_suffixes=("1",))
        chain = [
            (_R1, {"1": "(1) Tekst   ühe   tühikuga."}),
            (_R2, {"1": "(1) Tekst ühe tühikuga.\n"}),
        ]
        nodes = synthesise_versions(target, chain)
        assert len(nodes) == 1

    def test_transition_date_as_of_query_returns_exactly_one_version(self):
        # Issue #306: with an inclusive as-of-date query (the schema's canonical
        # ``?from <= D && (!to || ?to >= D)``), a query ON the transition day must
        # match exactly one version. Version k's exclusive ``versionValidTo`` =
        # day before version k+1's ``versionValidFrom`` makes this hold.
        target = _make_target(par_suffixes=("1",))
        chain = [
            (_R1, {"1": "Algtekst."}),
            (_R2, {"1": "Muudetud tekst."}),  # changes at 2015-01-01
        ]
        nodes = synthesise_versions(target, chain)
        # Version k ends the day BEFORE its successor begins — intervals do not overlap.
        assert nodes[0]["estleg:versionValidTo"] == {"@value": "2014-12-31", "@type": "xsd:date"}
        assert nodes[1]["estleg:versionValidFrom"] == {"@value": "2015-01-01", "@type": "xsd:date"}
        # The canonical inclusive as-of query on the transition day 2015-01-01 selects
        # only the successor (v222), not the superseded v111.
        matches = [n["@id"] for n in nodes if _matches_as_of(n, "2015-01-01")]
        assert matches == ["estleg:FIX_Par_1_v222"]
        # And the day before the transition selects only the predecessor.
        assert [n["@id"] for n in nodes if _matches_as_of(n, "2014-12-31")] == [
            "estleg:FIX_Par_1_v111"
        ]

    def test_same_valid_from_redactions_emit_no_zero_day_version(self):
        # Issue #393: two redactions sharing an entry-into-force date must not yield
        # a zero-duration (versionValidFrom == versionValidTo) node. The earlier
        # same-date redaction is collapsed (last-per-date wins) before chaining.
        target = _make_target(par_suffixes=("1",))
        r_same_a = Redaction(global_id="501", valid_from="2002-06-01", valid_to="2002-06-01", url="/akt/501.xml")
        r_same_b = Redaction(global_id="502", valid_from="2002-06-01", valid_to="2009-12-31", url="/akt/502.xml")
        chain = [
            (r_same_a, {"1": "Esimene samal kuupäeval."}),
            (r_same_b, {"1": "Teine samal kuupäeval."}),
            (_R3, {"1": "Hilisem tekst."}),  # 2020-01-01
        ]
        nodes = synthesise_versions(target, chain)
        # The collapsed earlier 2002-06-01 redaction (g501) is dropped; the surviving
        # 2002-06-01 version is g502, superseded only at the genuine later change.
        assert [n["@id"] for n in nodes] == ["estleg:FIX_Par_1_v502", "estleg:FIX_Par_1_v333"]
        # No node is zero-duration, and the first version's exclusive end is the day
        # before the 2020-01-01 successor (not its own 2002-06-01 start).
        for n in nodes:
            vto = n.get("estleg:versionValidTo")
            if vto is not None:
                assert n["estleg:versionValidFrom"]["@value"] != vto["@value"]
        assert nodes[0]["estleg:versionValidFrom"] == {"@value": "2002-06-01", "@type": "xsd:date"}
        assert nodes[0]["estleg:versionValidTo"] == {"@value": "2019-12-31", "@type": "xsd:date"}
        # The boundary as-of query returns exactly one version on the shared start date.
        assert [n["@id"] for n in nodes if _matches_as_of(n, "2002-06-01")] == [
            "estleg:FIX_Par_1_v502"
        ]

    def test_three_same_valid_from_redactions_keep_only_last(self):
        # More than two redactions on one entry-into-force date: only the last
        # survives (mirrors the by_from most-recently-touched dedup).
        target = _make_target(par_suffixes=("1",))
        chain = [
            (Redaction("601", "2005-01-01", "2005-01-01", "/akt/601.xml"), {"1": "A."}),
            (Redaction("602", "2005-01-01", "2005-01-01", "/akt/602.xml"), {"1": "B."}),
            (Redaction("603", "2005-01-01", None, "/akt/603.xml"), {"1": "C."}),
        ]
        nodes = synthesise_versions(target, chain)
        assert [n["@id"] for n in nodes] == ["estleg:FIX_Par_1_v603"]
        assert nodes[0]["estleg:versionText"] == "C."
        assert "estleg:versionValidTo" not in nodes[0]  # last → still open

    def test_inverted_chain_edge_is_skipped_not_emitted(self):
        # Issue #606 item 1: a residual non-monotonic chain — a later-listed version
        # whose versionValidFrom predates its predecessor (e.g. an out-of-order
        # ``current`` redaction that slipped past the chain sort) — must NOT yield an
        # inverted versionValidTo or a backwards supersededByVersion. The bad edge is
        # skipped and that node's interval is left open; valid edges are unaffected.
        target = _make_target(par_suffixes=("1",))
        chain = [
            (Redaction("111", "2010-01-01", "2014-12-31", "/akt/111.xml"), {"1": "A."}),
            (Redaction("333", "2020-01-01", None, "/akt/333.xml"), {"1": "C."}),  # out of order
            (Redaction("222", "2015-01-01", "2019-12-31", "/akt/222.xml"), {"1": "B."}),
        ]
        nodes = synthesise_versions(target, chain)
        # Distinct text + date for each → three versions, kept in input order.
        assert [n["@id"] for n in nodes] == [
            "estleg:FIX_Par_1_v111", "estleg:FIX_Par_1_v333", "estleg:FIX_Par_1_v222",
        ]
        # Global invariants: no inverted interval, no backwards supersededByVersion.
        from_by_id = {n["@id"]: n["estleg:versionValidFrom"]["@value"] for n in nodes}
        for n in nodes:
            vto = n.get("estleg:versionValidTo")
            if vto is not None:
                assert vto["@value"] >= n["estleg:versionValidFrom"]["@value"]
            sup = n.get("estleg:supersededByVersion")
            if sup is not None:
                assert from_by_id[sup["@id"]] >= n["estleg:versionValidFrom"]["@value"]
        by_id = {n["@id"]: n for n in nodes}
        # The valid edge v111 -> v333 is kept with the #306 exclusive end.
        assert by_id["estleg:FIX_Par_1_v111"]["estleg:supersededByVersion"] == {
            "@id": "estleg:FIX_Par_1_v333"
        }
        assert by_id["estleg:FIX_Par_1_v111"]["estleg:versionValidTo"] == {
            "@value": "2019-12-31", "@type": "xsd:date"
        }
        # ...but the inverted edge v333 -> v222 is dropped: v333 stays open.
        assert "estleg:supersededByVersion" not in by_id["estleg:FIX_Par_1_v333"]
        assert "estleg:versionValidTo" not in by_id["estleg:FIX_Par_1_v333"]


# ---------------------------------------------------------------------------
# ProvisionVersion @id collision / empty-globalID guard (issue #296a)
# ---------------------------------------------------------------------------


class TestVersionIdCollisionGuard:
    """`synthesise_versions` must never emit two ProvisionVersions with one @id."""

    def test_empty_global_id_redaction_emits_no_version(self):
        # A redaction with no globaalID has no stable, non-colliding version @id
        # (and no versionRedactionId), so it is skipped rather than emitted as
        # ``estleg:FIX_Par_1_v`` (which would collide across two such redactions).
        # Two un-emittable redactions in a row therefore yield nothing.
        target = _make_target(par_suffixes=("1",))
        chain = [
            (Redaction("", "2010-01-01", "2014-12-31", "/akt/.xml"), {"1": "Esimene tekst."}),
            (Redaction("", "2015-01-01", None, "/akt/.xml"), {"1": "Teine tekst."}),
        ]
        nodes = synthesise_versions(target, chain)
        assert nodes == []

    def test_empty_global_id_does_not_break_a_later_real_redaction(self):
        # The empty-id redaction cannot be emitted; a later real redaction whose
        # text differs still emits exactly one (well-identified) version.
        target = _make_target(par_suffixes=("1",))
        chain = [
            (Redaction("", "2010-01-01", "2014-12-31", "/akt/.xml"), {"1": "Algtekst."}),
            (_R3, {"1": "Uus tekst."}),
        ]
        nodes = synthesise_versions(target, chain)
        assert [n["@id"] for n in nodes] == ["estleg:FIX_Par_1_v333"]
        assert nodes[0]["estleg:versionValidFrom"] == {"@value": "2020-01-01", "@type": "xsd:date"}

    def test_duplicate_global_id_for_same_provision_is_disambiguated(self):
        # Data quirk: two distinct redactions reporting the same globaalID but
        # different text for one provision. The @id must be disambiguated so the
        # sidecar never carries a duplicate @id (mirrors the amendment generator).
        target = _make_target(par_suffixes=("1",))
        chain = [
            (Redaction("777", "2010-01-01", "2014-12-31", "/akt/777.xml"), {"1": "Alfa."}),
            (Redaction("777", "2015-01-01", None, "/akt/777.xml"), {"1": "Beeta."}),
        ]
        nodes = synthesise_versions(target, chain)
        ids = [n["@id"] for n in nodes]
        assert ids == ["estleg:FIX_Par_1_v777", "estleg:FIX_Par_1_v777_2"]
        assert len(ids) == len(set(ids))
        # The chain still wires up across the disambiguated id.
        assert nodes[0]["estleg:supersededByVersion"] == {"@id": "estleg:FIX_Par_1_v777_2"}


class TestUnemittableRedactionDoesNotSuppressLaterVersion:
    """Regression: a redaction we cannot emit (no globaalID, or no
    entry-into-force date) must NOT advance the tracked text. Otherwise a later
    *valid* redaction carrying the same text looks "unchanged", and the first
    real ProvisionVersion is silently suppressed (``synthesise_versions``
    returns ``[]``)."""

    def test_empty_global_id_then_valid_redaction_with_same_text(self):
        # Malformed redaction (no globaalID) followed by a well-formed one with
        # IDENTICAL text. The valid redaction must still produce a version.
        target = _make_target(par_suffixes=("1",))
        chain = [
            (Redaction("", "2010-01-01", "2014-12-31", "/akt/.xml"), {"1": "Sama tekst."}),
            (_R3, {"1": "Sama tekst."}),
        ]
        nodes = synthesise_versions(target, chain)
        assert [n["@id"] for n in nodes] == ["estleg:FIX_Par_1_v333"]
        assert nodes[0]["estleg:versionText"] == "Sama tekst."
        assert nodes[0]["estleg:versionValidFrom"] == {"@value": "2020-01-01", "@type": "xsd:date"}

    def test_missing_valid_from_then_valid_redaction_with_same_text(self):
        # Same hazard via the other guard: a globaalID but no valid_from cannot
        # be emitted and must not hide a later valid redaction with identical text.
        target = _make_target(par_suffixes=("1",))
        chain = [
            (Redaction("111", "", "2014-12-31", "/akt/111.xml"), {"1": "Sama tekst."}),
            (_R3, {"1": "Sama tekst."}),
        ]
        nodes = synthesise_versions(target, chain)
        assert [n["@id"] for n in nodes] == ["estleg:FIX_Par_1_v333"]
        assert nodes[0]["estleg:versionValidFrom"] == {"@value": "2020-01-01", "@type": "xsd:date"}


# ---------------------------------------------------------------------------
# currentVersion / hasVersion back-links (issue #271)
# ---------------------------------------------------------------------------


class TestProvisionBacklinks:
    """`build_provision_backlinks` materialises the forward-reachable head edge."""

    def test_currentversion_points_at_head_and_haversion_lists_all(self):
        target = _make_target(par_suffixes=("1", "2"))
        nodes = synthesise_versions(target, _three_redaction_chain())
        backlinks = build_provision_backlinks(nodes)

        by_id = {n["@id"]: n for n in backlinks}
        assert set(by_id) == {"estleg:FIX_Par_1", "estleg:FIX_Par_2"}
        # § 1 head is v222 (changed at r2, unchanged at r3); § 2 head is v333.
        assert by_id["estleg:FIX_Par_1"]["estleg:currentVersion"] == {"@id": "estleg:FIX_Par_1_v222"}
        assert by_id["estleg:FIX_Par_2"]["estleg:currentVersion"] == {"@id": "estleg:FIX_Par_2_v333"}
        # hasVersion enumerates every emitted version of the provision, in order.
        assert by_id["estleg:FIX_Par_1"]["estleg:hasVersion"] == [
            {"@id": "estleg:FIX_Par_1_v111"}, {"@id": "estleg:FIX_Par_1_v222"},
        ]
        assert by_id["estleg:FIX_Par_2"]["estleg:hasVersion"] == [
            {"@id": "estleg:FIX_Par_2_v111"}, {"@id": "estleg:FIX_Par_2_v333"},
        ]

    def test_head_version_is_forward_reachable_from_provision(self):
        # The whole point of #271: the head version (no supersededByVersion) had
        # zero inbound edges; currentVersion now makes it forward-reachable, and
        # every back-link target exists among the version nodes (no dangle).
        target = _make_target(par_suffixes=("1", "2"))
        nodes = synthesise_versions(target, _three_redaction_chain())
        emitted_ids = {n["@id"] for n in nodes}
        heads = {n["@id"] for n in nodes if "estleg:supersededByVersion" not in n}

        backlinks = build_provision_backlinks(nodes)
        reached_currents = {bl["estleg:currentVersion"]["@id"] for bl in backlinks}
        # currentVersion reaches exactly the head versions...
        assert reached_currents == heads == {"estleg:FIX_Par_1_v222", "estleg:FIX_Par_2_v333"}
        # ...and no back-link edge dangles outside the version nodes.
        for bl in backlinks:
            assert bl["estleg:currentVersion"]["@id"] in emitted_ids
            for v in bl["estleg:hasVersion"]:
                assert v["@id"] in emitted_ids

    def test_single_unchanging_provision_currentversion_is_the_only_version(self):
        target = _make_target(par_suffixes=("1",))
        chain = [
            (_R1, {"1": "Sama tekst."}),
            (_R2, {"1": "Sama tekst."}),
            (_R3, {"1": "Sama tekst."}),
        ]
        nodes = synthesise_versions(target, chain)
        backlinks = build_provision_backlinks(nodes)
        assert len(backlinks) == 1
        assert backlinks[0]["@id"] == "estleg:FIX_Par_1"
        assert backlinks[0]["estleg:currentVersion"] == {"@id": "estleg:FIX_Par_1_v111"}
        assert backlinks[0]["estleg:hasVersion"] == [{"@id": "estleg:FIX_Par_1_v111"}]

    def test_no_versions_yields_no_backlinks(self):
        assert build_provision_backlinks([]) == []

    def test_backlink_stubs_carry_no_paragrahv_or_type(self):
        target = _make_target(par_suffixes=("1", "2"))
        nodes = synthesise_versions(target, _three_redaction_chain())
        for bl in build_provision_backlinks(nodes):
            assert "@type" not in bl
            assert "estleg:paragrahv" not in bl


# ---------------------------------------------------------------------------
# XML extraction
# ---------------------------------------------------------------------------


class TestExtractProvisionTexts:
    def test_maps_paragrahvnr_to_par_suffix(self):
        root = _redaction_root([
            _paragraph_xml("1", title="Esimene", body="Esimese paragrahvi tekst."),
            _paragraph_xml("22", title="Kahekümne teine", body="22. paragrahvi tekst."),
        ])
        texts = extract_provision_texts(root)
        assert set(texts) == {"1", "22"}
        assert "Esimese paragrahvi tekst." in texts["1"]
        assert "22. paragrahvi tekst." in texts["22"]

    def test_superscript_paragraph_gets_underscore_suffix(self):
        # `§ 22¹` (ylaIndeks="1") must map to suffix "22_1" — same as the peep IRI.
        xml = (
            '<akt><paragrahv><paragrahvNr ylaIndeks="1">22</paragrahvNr>'
            '<paragrahvPealkiri>Lisatud paragrahv</paragrahvPealkiri>'
            '<loige><loigeNr>1</loigeNr><lauseOsa>Lisatud sätte tekst.</lauseOsa></loige>'
            '</paragrahv></akt>'
        )
        texts = extract_provision_texts(ET.fromstring(xml))
        assert set(texts) == {"22_1"}


# ---------------------------------------------------------------------------
# Date/offset normalisation (issue #296b)
# ---------------------------------------------------------------------------


class TestStripOffset:
    """`_strip_offset` must normalise any RT timestamp to a bare YYYY-MM-DD."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("2020-01-01", "2020-01-01"),
            ("  2019-12-31  ", "2019-12-31"),  # surrounding whitespace
            ("2020-01-01T00:00:00", "2020-01-01"),  # T-time, no offset
            ("2020-01-01 00:00:00", "2020-01-01"),  # space-time, no offset
            ("2020-01-01T10:30:00+02:00", "2020-01-01"),  # positive offset
            ("2020-01-01 10:30:00+02:00", "2020-01-01"),  # space + positive offset
            ("2020-01-01T23:59:59Z", "2020-01-01"),  # Zulu
            ("2020-01-01T01:00:00-05:00", "2020-01-01"),  # NEGATIVE offset + T-time
            ("2020-01-01-05:00", "2020-01-01"),  # negative offset, date-only base
            ("", None),
            (None, None),
            ("not-a-date", None),
        ],
    )
    def test_normalises_to_date_only(self, raw, expected):
        assert gpv._strip_offset(raw) == expected


class TestDateBefore:
    @pytest.mark.parametrize(
        ("left", "right", "inclusive", "expected"),
        [
            ("2020-01-01", "2026-05-12", False, True),
            ("2026-05-12", "2026-05-12", True, True),
            ("2026-05-12", "2026-05-12", False, False),
            ("2026-05-13", "2026-05-12", False, False),
            # Unparseable operand ⇒ deterministic lexicographic fallback. Here
            # "garbage" > "2026-05-12" lexicographically, so it is NOT <=.
            ("garbage", "2026-05-12", True, False),
            ("0000", "2026-05-12", True, True),  # "0000" < "2026..." lexicographically
        ],
    )
    def test_date_comparison(self, left, right, inclusive, expected):
        assert gpv._date_before(left, right, inclusive=inclusive) is expected


# ---------------------------------------------------------------------------
# Redaction-list reconstruction (mock the RT search API)
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self) -> None:  # noqa: D401 - mimic requests.Response
        return None

    def json(self) -> dict:
        return self._payload


def test_fetch_redaction_chain_sorted_oldest_to_newest_with_dates(monkeypatch: pytest.MonkeyPatch):
    title = "Näidisseadus"
    today = "2026-05-12"

    # The "full" search (no kehtiv): a mix of superseded redactions (closed
    # interval), an "announcement" entry (lopp == None, old algus — must be
    # ignored), a future-dated edition (algus > today — ignored), and out of
    # order rows. The kehtiv=today search: exactly the current edition.
    full_payload = {
        "aktid": [
            {"globaalID": 30, "pealkiri": title, "kehtivus": {"algus": "2020-01-01", "lopp": "2024-12-31"}, "muudetud": 3, "url": "/akt/30.xml"},
            {"globaalID": 10, "pealkiri": title, "kehtivus": {"algus": "2010-01-01", "lopp": "2014-12-31"}, "muudetud": 1, "url": "/akt/10.xml"},
            {"globaalID": 20, "pealkiri": title, "kehtivus": {"algus": "2015-01-01", "lopp": "2019-12-31"}, "muudetud": 2, "url": "/akt/20.xml"},
            # announcement: lopp None, algus in the past → ignored
            {"globaalID": 99, "pealkiri": title, "kehtivus": {"algus": "2015-01-01", "lopp": None}, "muudetud": 9, "url": "/akt/99.xml"},
            # future-dated → ignored
            {"globaalID": 50, "pealkiri": title, "kehtivus": {"algus": "2030-01-01", "lopp": "2030-12-31"}, "muudetud": 5, "url": "/akt/50.xml"},
            # different title → ignored
            {"globaalID": 60, "pealkiri": "Hoopis teine seadus", "kehtivus": {"algus": "2018-01-01", "lopp": "2018-12-31"}, "muudetud": 6, "url": "/akt/60.xml"},
        ]
    }
    current_payload = {
        "aktid": [
            {"globaalID": 40, "pealkiri": title, "kehtivus": {"algus": "2025-01-01", "lopp": None}, "muudetud": 4, "url": "/akt/40.xml"},
        ]
    }

    def fake_get(url, params=None, timeout=None):  # noqa: ANN001
        assert url == gpv.SEARCH_URL
        if params and params.get("kehtiv"):
            return _FakeResponse(current_payload)
        return _FakeResponse(full_payload)

    monkeypatch.setattr(gpv.requests, "get", fake_get)
    chain = fetch_redaction_chain(title, today=today)
    assert [r.global_id for r in chain] == ["10", "20", "30", "40"]
    assert [r.valid_from for r in chain] == ["2010-01-01", "2015-01-01", "2020-01-01", "2025-01-01"]
    # The last (current) one is open-ended; the rest carry their closing date.
    assert chain[-1].valid_to is None
    assert chain[0].valid_to == "2014-12-31"


def test_fetch_redaction_chain_handles_t_time_and_negative_offsets(
    monkeypatch: pytest.MonkeyPatch,
):
    """Issue #296b: endpoints with T-time / signed offsets normalise + compare as dates.

    Endpoints arrive with mixed shapes (``T``-time, positive AND negative tz
    offsets, ``Z``). They must normalise to ``YYYY-MM-DD`` and be compared as
    calendar dates against ``today`` — including the boundary where an edition's
    ``lopp`` falls on ``today`` (issue #328: retained, not dropped) and where
    ``algus`` equals ``today`` (kept by the ``<=`` start rule).
    """
    title = "Ajatempliseadus"
    today = "2026-05-12"
    full_payload = {
        "aktid": [
            # Superseded, T-time + Zulu — kept (ended 2014-12-31 < today).
            {"globaalID": 10, "pealkiri": title,
             "kehtivus": {"algus": "2010-01-01T00:00:00Z", "lopp": "2014-12-31T23:59:59Z"},
             "muudetud": 1, "url": "/akt/10.xml"},
            # Superseded, space-time + NEGATIVE offset — kept. Previously the raw
            # string "2019-12-31 23:59:59-05:00" mis-compared against today.
            {"globaalID": 20, "pealkiri": title,
             "kehtivus": {"algus": "2015-01-01T00:00:00+02:00", "lopp": "2019-12-31 23:59:59-05:00"},
             "muudetud": 2, "url": "/akt/20.xml"},
            # lopp falls ON today (T-time). Issue #328: an edition ending exactly on
            # the run date is the immediately-preceding edition on a redaction-
            # transition day — it must be RETAINED (inclusive ``lopp <= today``), or
            # its history vanishes (it is not the ``?kehtiv=today`` current edition).
            {"globaalID": 30, "pealkiri": title,
             "kehtivus": {"algus": "2020-01-01T00:00:00+03:00", "lopp": "2026-05-12T08:00:00+03:00"},
             "muudetud": 3, "url": "/akt/30.xml"},
        ]
    }
    # Current edition whose algus == today (boundary: kept), with a T-time offset.
    current_payload = {
        "aktid": [
            {"globaalID": 40, "pealkiri": title,
             "kehtivus": {"algus": "2026-05-12T00:00:00+03:00", "lopp": None},
             "muudetud": 4, "url": "/akt/40.xml"},
        ]
    }

    def fake_get(url, params=None, timeout=None):  # noqa: ANN001
        if params and params.get("kehtiv"):
            return _FakeResponse(current_payload)
        return _FakeResponse(full_payload)

    monkeypatch.setattr(gpv.requests, "get", fake_get)
    chain = fetch_redaction_chain(title, today=today)
    # g30 RETAINED (lopp on today, issue #328); g10/g20 kept and normalised; g40 current.
    assert [r.global_id for r in chain] == ["10", "20", "30", "40"]
    assert [r.valid_from for r in chain] == [
        "2010-01-01", "2015-01-01", "2020-01-01", "2026-05-12",
    ]
    assert [r.valid_to for r in chain] == [
        "2014-12-31", "2019-12-31", "2026-05-12", None,
    ]


def test_fetch_redaction_chain_retains_edition_ending_on_run_date(
    monkeypatch: pytest.MonkeyPatch,
):
    """Issue #328: the edition ending exactly on the run date is kept, not dropped.

    On the day a new redaction enters force, ``today == lopp`` of the previous
    edition. ``fetch_current_redaction`` (``?kehtiv=today``) returns the NEW
    edition, so the previous one is only recoverable from the superseded list — a
    strict ``lopp < today`` test silently dropped it (a history gap). The
    inclusive ``lopp <= today`` test retains it.
    """
    title = "Üleminekuseadus"
    today = "2024-01-01"
    full_payload = {
        "aktid": [
            # Older superseded edition (ended well before today).
            {"globaalID": 10, "pealkiri": title,
             "kehtivus": {"algus": "2018-01-01", "lopp": "2020-12-31"},
             "muudetud": 1, "url": "/akt/10.xml"},
            # Edition ending exactly on the run date — the immediately-preceding one.
            {"globaalID": 20, "pealkiri": title,
             "kehtivus": {"algus": "2021-01-01", "lopp": "2024-01-01"},
             "muudetud": 2, "url": "/akt/20.xml"},
        ]
    }
    # New edition entering force today is what ?kehtiv=today returns.
    current_payload = {
        "aktid": [
            {"globaalID": 30, "pealkiri": title,
             "kehtivus": {"algus": "2024-01-01", "lopp": None},
             "muudetud": 3, "url": "/akt/30.xml"},
        ]
    }

    def fake_get(url, params=None, timeout=None):  # noqa: ANN001
        if params and params.get("kehtiv"):
            return _FakeResponse(current_payload)
        return _FakeResponse(full_payload)

    monkeypatch.setattr(gpv.requests, "get", fake_get)
    chain = fetch_redaction_chain(title, today=today)
    # g20 (lopp == today) retained between the older edition and the new current one.
    assert [r.global_id for r in chain] == ["10", "20", "30"]
    assert [r.valid_to for r in chain] == ["2020-12-31", "2024-01-01", None]


def test_fetch_redaction_chain_reorders_out_of_order_current(
    monkeypatch: pytest.MonkeyPatch,
):
    """Issue #606 item 1: an anomalously early ``current`` redaction (entry-into-force
    before the newest superseded edition) is repositioned so the chain stays
    non-decreasing by ``valid_from`` — never appended out of order at the tail."""
    title = "Korratu seadus"
    today = "2026-01-01"
    superseded = [
        {"globaalID": 10, "pealkiri": title,
         "kehtivus": {"algus": "2010-01-01", "lopp": "2014-12-31"}, "muudetud": 1, "url": "/akt/10.xml"},
        {"globaalID": 20, "pealkiri": title,
         "kehtivus": {"algus": "2015-01-01", "lopp": "2019-12-31"}, "muudetud": 2, "url": "/akt/20.xml"},
        {"globaalID": 30, "pealkiri": title,
         "kehtivus": {"algus": "2020-01-01", "lopp": "2025-12-31"}, "muudetud": 3, "url": "/akt/30.xml"},
    ]
    monkeypatch.setattr(gpv, "_search", lambda *a, **k: list(superseded))
    # Anomalous current: valid_from 2012-01-01 — earlier than the 2020 superseded one.
    monkeypatch.setattr(
        gpv,
        "fetch_current_redaction",
        lambda *a, **k: Redaction("40", "2012-01-01", None, "/akt/40.xml"),
    )
    chain = fetch_redaction_chain(title, today=today)
    froms = [r.valid_from for r in chain]
    # Chain is non-decreasing by valid_from — the out-of-order current was repositioned.
    assert froms == sorted(froms)
    assert [r.global_id for r in chain] == ["10", "40", "20", "30"]


def test_fetch_current_redaction_tiebreak_is_deterministic(monkeypatch: pytest.MonkeyPatch):
    """Issue #606 item 7: two current matches sharing an ``algus`` must resolve to a
    deterministic winner — the lexicographically-greater ``globaalID`` (which becomes
    the version ``@id``) — regardless of the API's row order."""
    title = "Tasaviigi seadus"
    today = "2026-05-12"
    lo = {"globaalID": "122122025001", "pealkiri": title,
          "kehtivus": {"algus": "2025-01-01", "lopp": None}, "url": "/akt/122122025001.xml"}
    hi = {"globaalID": "122122025002", "pealkiri": title,
          "kehtivus": {"algus": "2025-01-01", "lopp": None}, "url": "/akt/122122025002.xml"}

    for ordering in ([lo, hi], [hi, lo]):
        monkeypatch.setattr(gpv, "_search", lambda *a, _rows=ordering, **k: list(_rows))
        red = fetch_current_redaction(title, today=today)
        assert red is not None
        # Identical algus → tie broken on globaalID; the greater id wins either order.
        assert red.global_id == "122122025002"


# ---------------------------------------------------------------------------
# Law selection (--limit / --law) and peep introspection
# ---------------------------------------------------------------------------


def _write_peep(krr: Path, slug: str, prefix: str, par_suffixes: list[str], *, title: str) -> None:
    graph = [
        {"@id": f"estleg:{prefix}_Map_2026", "@type": ["estleg:Act", "estleg:Law", "owl:Ontology"],
         "rdfs:label": f"{title} kaardistus", "dc:source": title},
        {"@id": f"estleg:LegalProvision_{prefix}", "@type": ["owl:Class"]},
    ]
    for s in par_suffixes:
        graph.append({
            "@id": f"estleg:{prefix}_Par_{s}",
            "@type": ["owl:NamedIndividual", f"estleg:LegalProvision_{prefix}"],
            "estleg:paragrahv": f"§ {s}.",
            "estleg:sourceAct": title,
        })
    (krr / f"{slug}_peep.json").write_text(
        json.dumps({"@context": {"estleg": gpv.CONTEXT["estleg"]}, "@graph": graph}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_build_law_target_reads_prefix_and_provisions_from_peep(tmp_path: Path):
    krr = tmp_path / "krr_outputs"
    krr.mkdir()
    _write_peep(krr, "naidis_seadus", "NAIDIS", ["1", "2", "5"], title="Näidisseadus")
    target = build_law_target("naidis_seadus", krr_dir=krr)
    assert target is not None
    assert target.title == "Näidisseadus"
    assert target.prefix == "NAIDIS"
    assert target.provisions == {
        "1": "estleg:NAIDIS_Par_1",
        "2": "estleg:NAIDIS_Par_2",
        "5": "estleg:NAIDIS_Par_5",
    }


def test_existing_peep_without_numeric_provisions_is_not_no_peep(tmp_path: Path):
    """#430: a on-disk peep with no ``_Par_<n>`` IRIs is not ``"no peep"``."""
    krr = tmp_path / "krr_outputs"
    krr.mkdir()
    graph = [
        {
            "@id": "estleg:GENEVA_Map_2026",
            "@type": ["estleg:Act", "estleg:Law", "owl:Ontology"],
            "rdfs:label": "Genfi konventsioonide ratifitseerimise seadus",
            "dc:source": "Genfi konventsioonide ratifitseerimise seadus",
        },
        {
            "@id": "estleg:GENEVA_Art_1",
            "@type": ["owl:NamedIndividual", "estleg:LegalProvision_GENEVA"],
            "estleg:paragrahv": "Artikkel 1.",
        },
    ]
    (krr / "geneva_convention_peep.json").write_text(
        json.dumps({"@context": {"estleg": gpv.CONTEXT["estleg"]}, "@graph": graph},
                   ensure_ascii=False),
        encoding="utf-8",
    )

    assert build_law_target("geneva_convention", krr_dir=krr) is None
    assert missing_law_target_error("geneva_convention", krr_dir=krr) == (
        "no_numeric_provisions"
    )
    assert missing_law_target_error("geneva_convention", krr_dir=krr) != "no peep"
    # A genuinely missing peep still uses the reserved "no peep" code.
    assert missing_law_target_error("puudub_seadus", krr_dir=krr) == "no peep"


def test_main_existing_peep_without_par_nodes_is_not_labeled_no_peep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """#430 end-to-end: report error for a treaty-shell peep is not ``no peep``."""
    krr = tmp_path / "krr_outputs"
    krr.mkdir()
    graph = [
        {
            "@id": "estleg:GENEVA_Map_2026",
            "@type": ["estleg:Act", "estleg:Law", "owl:Ontology"],
            "rdfs:label": "Genfi konventsioonide ratifitseerimise seadus",
            "dc:source": "Genfi konventsioonide ratifitseerimise seadus",
        },
    ]
    (krr / "geneva_convention_peep.json").write_text(
        json.dumps({"@context": {"estleg": gpv.CONTEXT["estleg"]}, "@graph": graph},
                   ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(gpv, "KRR_DIR", krr)
    monkeypatch.setattr(gpv, "VERSIONS_DIR", krr / "provision_versions")
    monkeypatch.setattr(
        gpv,
        "COVERAGE_PATH",
        krr / "reports" / "kov" / "extract_provision_versions_coverage.json",
    )
    monkeypatch.setattr(gpv, "iter_peep_files", lambda *a, **k: list(krr.glob("*_peep.json")))

    def _boom(*a, **k):  # noqa: ANN001
        raise AssertionError("ineligible peep must not hit the network")

    monkeypatch.setattr(gpv.requests, "get", _boom)

    assert gpv.main(["--law", "geneva_convention", "--no-sleep"]) == 0
    law_report = json.loads(
        (krr / "reports" / "provision_versions_report.json").read_text("utf-8")
    )
    assert law_report["rows"][0]["slug"] == "geneva_convention"
    assert law_report["rows"][0]["error"] != "no peep"
    assert law_report["rows"][0]["error"] == "no_numeric_provisions"


def test_reclassify_report_rewrites_stale_no_peep(tmp_path: Path) -> None:
    krr = tmp_path / "krr_outputs"
    krr.mkdir()
    (krr / "geneva_convention_peep.json").write_text(
        json.dumps({
            "@context": {"estleg": gpv.CONTEXT["estleg"]},
            "@graph": [{
                "@id": "estleg:GENEVA_Map_2026",
                "@type": ["estleg:Act", "owl:Ontology"],
            }],
        }),
        encoding="utf-8",
    )
    report = {
        "rows": [
            {"slug": "geneva_convention", "error": "no peep", "warnings": ["no peep"]},
            {"slug": "puudub_seadus", "error": "no peep", "warnings": ["no peep"]},
            {"slug": "ok_law", "error": None},
        ]
    }
    gpv.reclassify_report_missing_peep_rows(report, krr_dir=krr)
    by_slug = {r["slug"]: r for r in report["rows"]}
    assert by_slug["geneva_convention"]["error"] == "no_numeric_provisions"
    assert by_slug["puudub_seadus"]["error"] == "no peep"
    assert by_slug["ok_law"]["error"] is None


def test_committed_version_report_no_peep_rows_have_no_peep_file() -> None:
    """#430: committed report must not label an existing peep as ``no peep``."""
    path = (
        gpv.REPO_ROOT / "krr_outputs" / "reports" / "provision_versions_report.json"
    )
    if not path.is_file():
        pytest.skip("provision_versions_report.json not committed")
    report = json.loads(path.read_text(encoding="utf-8"))
    krr = gpv.REPO_ROOT / "krr_outputs"
    stale = []
    for row in report.get("rows") or []:
        if row.get("error") != "no peep":
            continue
        slug = row.get("slug") or ""
        if gpv._peep_files_for_slug(slug, krr):
            stale.append(slug)
    assert stale == [], (
        f"{len(stale)} report rows still say 'no peep' but a peep exists: "
        f"{stale[:8]}"
    )
    cov = json.loads(
        (krr / "reports" / "kov" / "extract_provision_versions_coverage.json").read_text(
            "utf-8"
        )
    )
    assert cov["files_processed_kov"] == 0


def test_build_law_target_merges_multipart_osa_peeps(tmp_path: Path):
    krr = tmp_path / "krr_outputs"
    krr.mkdir()
    # Two osa files sharing one prefix — like Karistusseadustik.
    for osa, suffixes in (("osa1", ["1", "2"]), ("osa2", ["88", "89"])):
        graph = [
            {"@id": f"estleg:BIG_{osa}", "@type": ["estleg:Act", "estleg:Law", "owl:Ontology"],
             "rdfs:label": "Suur seadus", "dc:source": "Suur seadus"},
        ]
        for s in suffixes:
            graph.append({"@id": f"estleg:BIG_Par_{s}",
                          "@type": ["owl:NamedIndividual", "estleg:LegalProvision_BIG"],
                          "estleg:paragrahv": f"§ {s}."})
        (krr / f"suur_seadus_{osa}_peep.json").write_text(
            json.dumps({"@context": {"estleg": gpv.CONTEXT["estleg"]}, "@graph": graph}, ensure_ascii=False),
            encoding="utf-8",
        )
    target = build_law_target("suur_seadus", krr_dir=krr)
    assert target is not None
    assert target.prefix == "BIG"
    assert set(target.provisions) == {"1", "2", "88", "89"}


def test_select_law_slugs_limit_and_explicit(tmp_path: Path):
    krr = tmp_path / "krr_outputs"
    krr.mkdir()
    for slug in ("alfa_seadus", "beeta_seadus", "gamma_seadus"):
        _write_peep(krr, slug, slug[:4].upper(), ["1"], title=slug)
    # --limit 0 → nothing
    assert select_law_slugs(explicit=None, limit=0, krr_dir=krr) == []
    # --limit 2 → first two (curated defaults absent → alphabetical)
    chosen = select_law_slugs(explicit=None, limit=2, krr_dir=krr)
    assert len(chosen) == 2 and set(chosen) <= {"alfa_seadus", "beeta_seadus", "gamma_seadus"}
    # --all → every on-disk law slug, no curated/default sample truncation.
    assert select_law_slugs(explicit=None, limit=None, all_laws=True, krr_dir=krr) == [
        "alfa_seadus",
        "beeta_seadus",
        "gamma_seadus",
    ]
    # explicit --law wins, unknown slugs dropped
    assert select_law_slugs(explicit=["gamma_seadus", "puudub_seadus"], limit=None, krr_dir=krr) == ["gamma_seadus"]


def test_completed_report_slugs_filters_completed_rows(tmp_path: Path):
    report_path = tmp_path / "reports" / "provision_versions_report.json"
    report_path.parent.mkdir()
    report_path.write_text(
        json.dumps(
            {
                "schemaVersion": gpv.LAW_REPORT_SCHEMA_VERSION,
                "rows": [
                    {
                        "slug": "alfa_seadus",
                        "versions_emitted": 3,
                        "max_redactions": 0,
                        "current_redaction_id": "333",
                        "current_redaction_valid_from": "2020-01-01",
                        "error": None,
                        "warnings": [],
                    },
                    {
                        "slug": "beeta_seadus",
                        "versions_emitted": 0,
                        "max_redactions": 0,
                        "current_redaction_id": "333",
                        "current_redaction_valid_from": "2020-01-01",
                        "error": None,
                        "warnings": [],
                    },
                    {
                        "slug": "gamma_seadus",
                        "versions_emitted": 4,
                        "max_redactions": 0,
                        "current_redaction_id": "333",
                        "current_redaction_valid_from": "2020-01-01",
                        "error": "fetch failed",
                        "warnings": ["fetch failed"],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    report = gpv._load_law_report(report_path)
    assert gpv._completed_report_slugs(report, max_redactions=0) == ["alfa_seadus"]


def test_completed_report_requires_supported_schema(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    report_path = tmp_path / "provision_versions_report.json"
    report_path.write_text(
        json.dumps({
            "rows": [
                {"slug": "alfa_seadus", "versions_emitted": 3, "max_redactions": 0}
            ]
        }),
        encoding="utf-8",
    )

    assert gpv._load_law_report(report_path) is None
    assert "unsupported schemaVersion" in capsys.readouterr().err


def test_completed_report_respects_max_redactions_compatibility(tmp_path: Path):
    report_path = tmp_path / "provision_versions_report.json"
    report_path.write_text(
        json.dumps({
            "schemaVersion": gpv.LAW_REPORT_SCHEMA_VERSION,
            "rows": [
                {
                    "slug": "sample_run",
                    "versions_emitted": 3,
                    "max_redactions": 12,
                    "error": None,
                    "warnings": [],
                },
                {
                    "slug": "full_run",
                    "versions_emitted": 3,
                    "max_redactions": 0,
                    "error": None,
                    "warnings": [],
                },
            ],
        }),
        encoding="utf-8",
    )

    report = gpv._load_law_report(report_path)
    assert gpv._completed_report_slugs(report, max_redactions=0) == ["full_run"]
    assert gpv._completed_report_slugs(report, max_redactions=12) == [
        "sample_run",
        "full_run",
    ]


def test_completed_report_requires_matching_current_redaction(tmp_path: Path):
    report_path = tmp_path / "provision_versions_report.json"
    report_path.write_text(
        json.dumps({
            "schemaVersion": gpv.LAW_REPORT_SCHEMA_VERSION,
            "rows": [
                {
                    "slug": "fresh_law",
                    "versions_emitted": 3,
                    "max_redactions": 0,
                    "current_redaction_id": "333",
                    "current_redaction_valid_from": "2020-01-01",
                    "error": None,
                    "warnings": [],
                },
                {
                    "slug": "stale_law",
                    "versions_emitted": 3,
                    "max_redactions": 0,
                    "current_redaction_id": "333",
                    "current_redaction_valid_from": "2020-01-01",
                    "error": None,
                    "warnings": [],
                },
            ],
        }),
        encoding="utf-8",
    )
    current = {
        "fresh_law": Redaction("333", "2020-01-01", None, "/akt/333.xml"),
        "stale_law": Redaction("444", "2024-01-01", None, "/akt/444.xml"),
    }

    report = gpv._load_law_report(report_path)
    assert gpv._completed_report_slugs(
        report,
        max_redactions=0,
        current_redactions=current,
    ) == ["fresh_law"]


@pytest.mark.parametrize(
    ("prior", "requested", "expected"),
    [
        (None, 0, False),
        (12, 0, False),
        (0, 0, True),
        (0, 12, True),
        (12, 12, True),
        (12, 13, False),
        (-1, 12, False),
        ("not-int", 12, False),
    ],
)
def test_max_redactions_covers_truth_table(prior, requested, expected):
    assert (
        gpv._max_redactions_covers(
            gpv._parse_nonnegative_int(prior), requested
        )
        is expected
    )


def test_load_law_report_handles_malformed_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    report_path = tmp_path / "provision_versions_report.json"
    report_path.write_text("{not json", encoding="utf-8")

    assert gpv._load_law_report(report_path) is None
    assert "could not parse prior ProvisionVersion report" in capsys.readouterr().err


def test_load_law_report_rejects_non_object_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    report_path = tmp_path / "provision_versions_report.json"
    report_path.write_text("[]", encoding="utf-8")

    assert gpv._load_law_report(report_path) is None
    assert "expected object, got list" in capsys.readouterr().err


def test_completed_report_handles_non_list_rows_and_bad_rows(tmp_path: Path):
    assert gpv._completed_report_slugs({"rows": {}}, max_redactions=0) == []
    report = {
        "rows": [
            None,
            {"versions_emitted": 2, "max_redactions": 0},
            {
                "slug": "partial_mismatch",
                "versions_emitted": 2,
                "max_redactions": 0,
                "current_redaction_id": "333",
                "current_redaction_valid_from": "2020-01-01",
                "error": None,
                "warnings": [],
            },
        ]
    }

    assert gpv._completed_report_slugs(
        report,
        max_redactions=0,
        current_redactions={
            "partial_mismatch": Redaction("333", "2021-01-01", None, "/akt/333.xml")
        },
    ) == []


def test_fetch_current_redactions_throttles_and_fails_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    targets = {
        "ok": _make_target(),
        "missing": None,
        "raises": _make_target(),
        "none": _make_target(),
        "ok2": _make_target(),
    }
    calls: list[str] = []
    sleeps: list[float] = []

    def fake_build_law_target(slug: str, **kwargs):
        return targets[slug]

    def fake_fetch_current_redaction(title: str, **kwargs):
        calls.append(title)
        if len(calls) == 2:
            raise RuntimeError("rate limited")
        if len(calls) == 3:
            return None
        return Redaction(str(len(calls)), "2020-01-01", None, f"/akt/{len(calls)}.xml")

    monkeypatch.setattr(gpv, "build_law_target", fake_build_law_target)
    monkeypatch.setattr(gpv, "fetch_current_redaction", fake_fetch_current_redaction)
    monkeypatch.setattr(gpv.time, "sleep", lambda seconds: sleeps.append(seconds))

    result = gpv._fetch_current_redactions(
        ["ok", "missing", "raises", "none", "ok2"],
        krr_dir=tmp_path,
        today="2026-05-25",
        fetch_sleep=0.25,
    )

    assert set(result) == {"ok", "ok2"}
    assert sleeps == [0.25, 0.25, 0.25, 0.25]
    stderr = capsys.readouterr().err
    assert "could not verify current redaction for raises" in stderr
    assert "no current redaction found for none" in stderr


def test_resolve_resume_skips_filters_candidates_and_checks_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    report_path = tmp_path / "provision_versions_report.json"
    report_path.write_text(
        json.dumps({
            "schemaVersion": gpv.LAW_REPORT_SCHEMA_VERSION,
            "rows": [
                {
                    "slug": "fresh_law",
                    "versions_emitted": 2,
                    "max_redactions": 0,
                    "current_redaction_id": "333",
                    "current_redaction_valid_from": "2020-01-01",
                    "error": None,
                    "warnings": [],
                },
                {
                    "slug": "removed_law",
                    "versions_emitted": 2,
                    "max_redactions": 0,
                    "current_redaction_id": "333",
                    "current_redaction_valid_from": "2020-01-01",
                    "error": None,
                    "warnings": [],
                },
            ],
        }),
        encoding="utf-8",
    )
    fetched: list[list[str]] = []

    def fake_fetch_current_redactions(slugs: list[str], **kwargs):
        fetched.append(slugs)
        assert kwargs["fetch_sleep"] == 0.3
        return {"fresh_law": Redaction("333", "2020-01-01", None, "/akt/333.xml")}

    monkeypatch.setattr(gpv, "_fetch_current_redactions", fake_fetch_current_redactions)

    assert gpv.resolve_resume_skips(
        candidates=["fresh_law"],
        law_report_path=report_path,
        max_redactions=0,
        krr_dir=tmp_path,
        today="2026-05-25",
        fetch_sleep=0.3,
    ) == {"fresh_law"}
    assert fetched == [["fresh_law"]]


def test_max_redactions_default_distinguishes_all_mode():
    assert gpv.effective_max_redactions(gpv._parse_args([])) == gpv.DEFAULT_MAX_REDACTIONS
    assert gpv.effective_max_redactions(gpv._parse_args(["--all"])) == 0
    assert gpv.effective_max_redactions(gpv._parse_args(["--max-redactions", "0"])) == 0
    assert (
        gpv.effective_max_redactions(gpv._parse_args(["--all", "--max-redactions", "12"]))
        == 12
    )


def test_negative_max_redactions_rejected(capsys: pytest.CaptureFixture[str]):
    with pytest.raises(SystemExit):
        gpv._parse_args(["--max-redactions", "-1"])
    assert "--max-redactions must be non-negative" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Sidecar writing
# ---------------------------------------------------------------------------


def test_write_sidecar_shape_and_context(tmp_path: Path):
    target = _make_target(par_suffixes=("1", "2"))
    nodes = synthesise_versions(target, _three_redaction_chain())
    out_dir = tmp_path / "provision_versions"
    out_path = write_sidecar(target, nodes, out_dir=out_dir)
    assert out_path == out_dir / "fixture_law.jsonld"
    doc = json.loads(out_path.read_text(encoding="utf-8"))
    assert doc["@context"]["estleg"] == gpv.CONTEXT["estleg"]
    graph = doc["@graph"]
    # First node is the owl:Ontology map header.
    assert graph[0]["@type"] == ["owl:Ontology"]
    assert graph[0]["@id"] == "estleg:ProvisionVersions_fixture_law_Map"
    assert graph[0]["dc:source"] == "Fikseeritud seadus"
    # The map label still counts only the ProvisionVersion nodes (4), not the
    # back-link stubs.
    assert "(4 versiooni)" in graph[0]["rdfs:label"]["@value"]
    # No undefined estleg:total* count predicate leaked onto the map node.
    assert not any(k.startswith("estleg:total") for k in graph[0])

    # The body splits into ProvisionVersion nodes and issue #271 back-link stubs.
    pv_nodes = [n for n in graph[1:] if "estleg:ProvisionVersion" in n.get("@type", [])]
    backlink_nodes = [n for n in graph[1:] if n["@id"] in set(target.provisions.values())]
    assert {n["@id"] for n in pv_nodes} == {
        "estleg:FIX_Par_1_v111", "estleg:FIX_Par_1_v222",
        "estleg:FIX_Par_2_v111", "estleg:FIX_Par_2_v333",
    }
    # Exactly one back-link stub per versioned provision, each pointing
    # currentVersion at its head version and hasVersion at every version.
    assert {n["@id"] for n in backlink_nodes} == {"estleg:FIX_Par_1", "estleg:FIX_Par_2"}
    bl_by_id = {n["@id"]: n for n in backlink_nodes}
    assert bl_by_id["estleg:FIX_Par_1"]["estleg:currentVersion"] == {"@id": "estleg:FIX_Par_1_v222"}
    assert bl_by_id["estleg:FIX_Par_2"]["estleg:currentVersion"] == {"@id": "estleg:FIX_Par_2_v333"}
    assert bl_by_id["estleg:FIX_Par_1"]["estleg:hasVersion"] == [
        {"@id": "estleg:FIX_Par_1_v111"}, {"@id": "estleg:FIX_Par_1_v222"},
    ]
    # Back-link stubs deliberately carry no paragrahv / @type so neither
    # LegalProvisionShape nor ProvisionVersionShape targets them.
    for n in backlink_nodes:
        assert "@type" not in n
        assert "estleg:paragrahv" not in n


# ---------------------------------------------------------------------------
# SHACL conformance
# ---------------------------------------------------------------------------


def test_shacl_accepts_well_formed_provision_versions():
    target = _make_target(par_suffixes=("1", "2"))
    nodes = synthesise_versions(target, _three_redaction_chain())
    graph_json = {"@context": dict(gpv.CONTEXT), "@graph": nodes}
    conforms, msg = _shacl_conforms(graph_json)
    assert conforms, msg


def test_shacl_accepts_sidecar_with_currentversion_backlinks():
    # Issue #271: the full sidecar graph — version nodes PLUS the LegalProvision
    # back-link stubs (estleg:currentVersion / estleg:hasVersion) — must still
    # conform. The stubs carry no estleg:paragrahv, so LegalProvisionShape's
    # required paragrahv/summary do not fire on them.
    target = _make_target(par_suffixes=("1", "2"))
    nodes = synthesise_versions(target, _three_redaction_chain())
    backlinks = build_provision_backlinks(nodes)
    graph_json = {"@context": dict(gpv.CONTEXT), "@graph": [*nodes, *backlinks]}
    conforms, msg = _shacl_conforms(graph_json)
    assert conforms, msg


@pytest.mark.parametrize("drop_prop", ["estleg:versionOf", "estleg:versionValidFrom", "estleg:versionText"])
def test_shacl_rejects_provision_version_missing_required_property(drop_prop: str):
    node = {
        "@id": "estleg:FIX_Par_1_v111",
        "@type": ["owl:NamedIndividual", "estleg:ProvisionVersion"],
        "estleg:versionOf": {"@id": "estleg:FIX_Par_1"},
        "estleg:versionValidFrom": {"@value": "2010-01-01", "@type": "xsd:date"},
        "estleg:versionText": "Mingi sätte tekst.",
        "estleg:versionRedactionId": "111",
    }
    node.pop(drop_prop)
    graph_json = {"@context": dict(gpv.CONTEXT), "@graph": [node]}
    conforms, _ = _shacl_conforms(graph_json)
    assert not conforms


# ---------------------------------------------------------------------------
# End-to-end (mock fetches): process_law writes a sidecar + coverage report
# ---------------------------------------------------------------------------


def test_main_processes_law_with_mocked_fetches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    krr = tmp_path / "krr_outputs"
    krr.mkdir()
    _write_peep(krr, "fixture_law", "FIX", ["1", "2"], title="Fikseeritud seadus")
    monkeypatch.setattr(gpv, "KRR_DIR", krr)
    monkeypatch.setattr(gpv, "VERSIONS_DIR", krr / "provision_versions")
    monkeypatch.setattr(gpv, "COVERAGE_PATH", krr / "reports" / "kov" / "extract_provision_versions_coverage.json")
    monkeypatch.setattr(gpv, "iter_peep_files", lambda *a, **k: list(krr.glob("*_peep.json")))

    chain = [_R1, _R2, _R3]
    texts_by_gid = {
        "111": _redaction_root([_paragraph_xml("1", title="A", body="Algtekst 1."),
                                _paragraph_xml("2", title="B", body="Algtekst 2.")]),
        "222": _redaction_root([_paragraph_xml("1", title="A", body="MUUDETUD 1 r2."),
                                _paragraph_xml("2", title="B", body="Algtekst 2.")]),
        "333": _redaction_root([_paragraph_xml("1", title="A", body="MUUDETUD 1 r2."),
                                _paragraph_xml("2", title="B", body="MUUDETUD 2 r3.")]),
    }
    monkeypatch.setattr(gpv, "fetch_redaction_chain", lambda *a, **k: chain)
    monkeypatch.setattr(gpv, "fetch_redaction_xml", lambda slug, redaction, **k: texts_by_gid[redaction.global_id])

    rc = gpv.main(["--law", "fixture_law", "--max-redactions", "0", "--no-sleep"])
    assert rc == 0

    sidecar = krr / "provision_versions" / "fixture_law.jsonld"
    assert sidecar.exists()
    doc = json.loads(sidecar.read_text(encoding="utf-8"))
    pv_nodes = [n for n in doc["@graph"] if "estleg:ProvisionVersion" in n.get("@type", [])]
    # § 1: v111, v222 ; § 2: v111, v333 → 4 ProvisionVersion nodes.
    assert {n["@id"] for n in pv_nodes} == {
        "estleg:FIX_Par_1_v111", "estleg:FIX_Par_1_v222",
        "estleg:FIX_Par_2_v111", "estleg:FIX_Par_2_v333",
    }
    # versionOf targets all resolve to provision IRIs declared in the peep.
    peep = json.loads((krr / "fixture_law_peep.json").read_text(encoding="utf-8"))
    peep_ids = {n["@id"] for n in peep["@graph"]}
    for n in pv_nodes:
        assert n["estleg:versionOf"]["@id"] in peep_ids

    # Issue #271: the sidecar also carries currentVersion / hasVersion back-link
    # stubs onto the stable provision IRIs, and every target resolves within the
    # sidecar (the head version is now forward-reachable, no dangle).
    pv_ids = {n["@id"] for n in pv_nodes}
    backlink_nodes = [n for n in doc["@graph"] if n["@id"] in peep_ids and "estleg:currentVersion" in n]
    bl_by_id = {n["@id"]: n for n in backlink_nodes}
    assert set(bl_by_id) == {"estleg:FIX_Par_1", "estleg:FIX_Par_2"}
    assert bl_by_id["estleg:FIX_Par_1"]["estleg:currentVersion"] == {"@id": "estleg:FIX_Par_1_v222"}
    assert bl_by_id["estleg:FIX_Par_2"]["estleg:currentVersion"] == {"@id": "estleg:FIX_Par_2_v333"}
    for n in backlink_nodes:
        assert n["estleg:currentVersion"]["@id"] in pv_ids
        for v in n["estleg:hasVersion"]:
            assert v["@id"] in pv_ids

    cov = json.loads((krr / "reports" / "kov" / "extract_provision_versions_coverage.json").read_text("utf-8"))
    assert cov["pipeline"] == "extract_provision_versions"
    assert cov["files_processed"] == 1
    assert cov["files_with_output"] == 1
    assert cov["triples_emitted"] == 4  # ProvisionVersion node count
    law_report = json.loads(
        (krr / "reports" / "provision_versions_report.json").read_text("utf-8")
    )
    assert law_report["schemaVersion"] == gpv.LAW_REPORT_SCHEMA_VERSION
    assert law_report["laws_processed"] == 1
    assert law_report["versions_emitted"] == 4
    assert law_report["rows"][0]["redactions_processed"] == 3
    assert law_report["rows"][0]["redactions_failed"] == 0
    assert law_report["rows"][0]["max_redactions"] == 0
    assert law_report["rows"][0]["current_redaction_id"] == "333"
    assert law_report["rows"][0]["current_redaction_valid_from"] == "2020-01-01"
    assert law_report["rows"][0]["warnings"] == []


def test_main_full_run_reprocesses_prior_sample_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    krr = tmp_path / "krr_outputs"
    krr.mkdir()
    _write_peep(krr, "fixture_law", "FIX", ["1"], title="Fikseeritud seadus")
    report_path = krr / "reports" / "provision_versions_report.json"
    report_path.parent.mkdir(parents=True)
    report_path.write_text(
        json.dumps({
            "schemaVersion": gpv.LAW_REPORT_SCHEMA_VERSION,
            "rows": [
                {
                    "slug": "fixture_law",
                    "versions_emitted": 2,
                    "max_redactions": gpv.DEFAULT_MAX_REDACTIONS,
                    "current_redaction_id": "333",
                    "current_redaction_valid_from": "2020-01-01",
                    "error": None,
                    "warnings": [],
                }
            ],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(gpv, "KRR_DIR", krr)
    monkeypatch.setattr(gpv, "VERSIONS_DIR", krr / "provision_versions")
    monkeypatch.setattr(
        gpv,
        "COVERAGE_PATH",
        krr / "reports" / "kov" / "extract_provision_versions_coverage.json",
    )
    monkeypatch.setattr(gpv, "iter_peep_files", lambda *a, **k: list(krr.glob("*_peep.json")))
    monkeypatch.setattr(
        gpv,
        "_fetch_current_redactions",
        lambda *a, **k: pytest.fail("sample rows cannot satisfy full-history resume"),
    )
    processed: list[tuple[str, int]] = []

    def fake_process_law(target: LawTarget, **kwargs) -> gpv.LawResult:
        processed.append((target.slug, kwargs["max_redactions"]))
        return gpv.LawResult(
            slug=target.slug,
            title=target.title,
            redactions_total=3,
            redactions_fetched=3,
            redactions_failed=0,
            provisions_in_law=len(target.provisions),
            provisions_versioned=1,
            versions_emitted=2,
            sidecar_path="provision_versions/fixture_law.jsonld",
            max_redactions=kwargs["max_redactions"],
            current_redaction_id="333",
            current_redaction_valid_from="2020-01-01",
        )

    monkeypatch.setattr(gpv, "process_law", fake_process_law)

    assert gpv.main(["--all", "--max-redactions", "0", "--no-sleep"]) == 0
    assert processed == [("fixture_law", 0)]


@pytest.mark.parametrize("resume_flag", ["--no-resume", "--force"])
def test_main_resume_escape_hatches_disable_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, resume_flag: str
):
    krr = tmp_path / "krr_outputs"
    krr.mkdir()
    _write_peep(krr, "fixture_law", "FIX", ["1"], title="Fikseeritud seadus")
    monkeypatch.setattr(gpv, "KRR_DIR", krr)
    monkeypatch.setattr(gpv, "VERSIONS_DIR", krr / "provision_versions")
    monkeypatch.setattr(
        gpv,
        "COVERAGE_PATH",
        krr / "reports" / "kov" / "extract_provision_versions_coverage.json",
    )
    monkeypatch.setattr(gpv, "iter_peep_files", lambda *a, **k: list(krr.glob("*_peep.json")))
    monkeypatch.setattr(
        gpv,
        "resolve_resume_skips",
        lambda **kwargs: pytest.fail(f"{resume_flag} must bypass resume checks"),
    )
    processed: list[str] = []

    def fake_process_law(target: LawTarget, **kwargs) -> gpv.LawResult:
        processed.append(target.slug)
        return gpv.LawResult(
            slug=target.slug,
            title=target.title,
            redactions_total=1,
            redactions_fetched=1,
            redactions_failed=0,
            provisions_in_law=len(target.provisions),
            provisions_versioned=1,
            versions_emitted=1,
            sidecar_path="provision_versions/fixture_law.jsonld",
            max_redactions=kwargs["max_redactions"],
            current_redaction_id="333",
            current_redaction_valid_from="2020-01-01",
        )

    monkeypatch.setattr(gpv, "process_law", fake_process_law)

    assert gpv.main(["--all", resume_flag, "--no-sleep"]) == 0
    assert processed == ["fixture_law"]


def test_main_limit_zero_writes_zeroed_coverage_and_no_sidecars(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    krr = tmp_path / "krr_outputs"
    krr.mkdir()
    monkeypatch.setattr(gpv, "KRR_DIR", krr)
    monkeypatch.setattr(gpv, "VERSIONS_DIR", krr / "provision_versions")
    monkeypatch.setattr(gpv, "COVERAGE_PATH", krr / "reports" / "kov" / "extract_provision_versions_coverage.json")
    monkeypatch.setattr(gpv, "iter_peep_files", lambda *a, **k: [])

    def _boom(*a, **k):  # noqa: ANN001
        raise AssertionError("--limit 0 must not hit the network")

    monkeypatch.setattr(gpv.requests, "get", _boom)
    rc = gpv.main(["--limit", "0"])
    assert rc == 0
    assert not (krr / "provision_versions").exists() or not list((krr / "provision_versions").glob("*.jsonld"))
    cov = json.loads((krr / "reports" / "kov" / "extract_provision_versions_coverage.json").read_text("utf-8"))
    assert cov["files_processed"] == 0 and cov["triples_emitted"] == 0
    law_report = json.loads(
        (krr / "reports" / "provision_versions_report.json").read_text("utf-8")
    )
    assert law_report["schemaVersion"] == gpv.LAW_REPORT_SCHEMA_VERSION
    assert law_report["laws_processed"] == 0
    assert law_report["rows"] == []


def test_write_law_report_does_not_clobber_existing_noop(tmp_path: Path):
    report_path = tmp_path / "reports" / "provision_versions_report.json"
    report_path.parent.mkdir()
    original = {"rows": [{"slug": "alfa_seadus", "versions_emitted": 2}]}
    report_path.write_text(json.dumps(original), encoding="utf-8")

    gpv.write_law_report([], report_path)

    assert json.loads(report_path.read_text(encoding="utf-8")) == original

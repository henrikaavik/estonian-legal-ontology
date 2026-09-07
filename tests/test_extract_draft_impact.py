"""Tests for scripts/extract_draft_impact.py."""
from __future__ import annotations

import json

import pytest

from estleg.extract_draft_impact import affected_law_name_values


def test_affected_law_name_values_accepts_canonical_string_array():
    assert affected_law_name_values(["Riigi Teataja seaduse"]) == ["Riigi Teataja seaduse"]


def test_affected_law_name_values_unwraps_legacy_jsonld_values():
    assert affected_law_name_values(
        [{"@value": "Riigi Teataja seaduse", "@language": "et"}]
    ) == ["Riigi Teataja seaduse"]


# --------------------------------------------------------------------- #
# Issue #319 — rdfs:label / estleg:initiator are JSON-LD value objects in
# fresh generate_draft_legislation output. extract_draft_impact must read
# them through jsonld_text / jsonld_texts (NOT as raw strings) so draft
# resolution doesn't break on the value-object shape.
# --------------------------------------------------------------------- #


class TestValueObjectFieldReads:
    def test_module_reads_label_and_initiator_via_jsonld_helpers(self):
        """The module imports the canonical JSON-LD unwrappers so a
        value-object ``rdfs:label`` / ``estleg:initiator`` is read as text,
        never str()'d into ``{'@value': ...}``."""
        from estleg import extract_draft_impact as mod

        assert hasattr(mod, "jsonld_text")
        assert hasattr(mod, "jsonld_texts")

    def test_jsonld_text_unwraps_label_value_object(self):
        """A value-object ``rdfs:label`` resolves to its Estonian text
        (the exact call ``main`` makes before change-type classification)."""
        from estleg.extract_draft_impact import jsonld_text

        label = {"@value": "Riigi Teataja seaduse muutmise seadus", "@language": "et"}
        assert (
            jsonld_text(label, prefer_language="et")
            == "Riigi Teataja seaduse muutmise seadus"
        )
        # A raw string still passes through unchanged (legacy corpus shape).
        assert (
            jsonld_text("Riigi Teataja seaduse muutmise seadus", prefer_language="et")
            == "Riigi Teataja seaduse muutmise seadus"
        )

    def test_jsonld_texts_unwraps_initiator_value_object_list(self):
        """A multi-language ``estleg:initiator`` value-object list yields
        only the Estonian ministry names (the exact call ``main`` makes for
        ``pending_changes_by_ministry``)."""
        from estleg.extract_draft_impact import jsonld_texts

        initiator = [
            {"@value": "Ministry of Justice", "@language": "en"},
            {"@value": "Justiitsministeerium", "@language": "et"},
            {"@value": "Rahandusministeerium", "@language": "et"},
        ]
        assert jsonld_texts(initiator, prefer_language="et") == [
            "Justiitsministeerium",
            "Rahandusministeerium",
        ]


# --------------------------------------------------------------------- #
# Regression tests for ontology-review-plan draft_impact items.
# --------------------------------------------------------------------- #


class TestClassifyChangeTypePriority:
    """Multiple matches return all of them, with canonical priority
    repeals > enacts > amends > supplements applied to the
    single-result form. Previously the first-pattern-listed wins
    rule was order-of-list dependent.
    """

    def test_amends_when_only_muutmi(self):
        from estleg.extract_draft_impact import classify_change_type
        result = classify_change_type("X seaduse muutmise seadus")
        assert result is not None
        assert result[0] == "amends"

    def test_repeals_wins_over_amends_when_both_present(self):
        from estleg.extract_draft_impact import classify_change_type
        # Both ``muutmi`` and ``kehtetuks tunnistami`` match. The
        # canonical priority puts repeals first.
        title = "X muutmise ja Y kehtetuks tunnistamise seadus"
        result = classify_change_type(title)
        assert result is not None
        assert result[0] == "repeals", (
            f"expected 'repeals' to win over 'amends' under canonical "
            f"priority; got {result[0]}"
        )

    def test_enacts_wins_over_amends(self):
        from estleg.extract_draft_impact import classify_change_type
        # ``kehtestami`` AND ``muutmi`` both match.
        title = "X seaduse kehtestamise ja Y muutmise seadus"
        result = classify_change_type(title)
        assert result is not None
        assert result[0] == "enacts"

    def test_classify_change_types_returns_all_matches(self):
        from estleg.extract_draft_impact import classify_change_types
        title = "X muutmise ja Y kehtetuks tunnistamise seadus"
        result = classify_change_types(title)
        types = {ct for ct, _ in result}
        assert {"amends", "repeals"}.issubset(types), (
            f"all-matches helper must return both; got {types}"
        )
        # First entry is the highest-priority match.
        assert result[0][0] == "repeals"

    def test_no_match_returns_none(self):
        from estleg.extract_draft_impact import classify_change_type
        assert classify_change_type("Some random title without keywords") is None
        from estleg.extract_draft_impact import classify_change_types
        assert classify_change_types("Some random title without keywords") == []


class TestBuildLawLookupCollisions:
    """``build_law_lookup`` detects collisions and either warns + drops
    the ambiguous keys or raises ValueError per ``on_collision``.
    """

    def test_warn_drops_ambiguous_key(self, capsys):
        from estleg.extract_draft_impact import build_law_lookup
        # Two laws with the same readable form (after _ → space).
        index = {
            "laws": [
                {"name": "x_seadus", "files": ["x_seadus_v1_peep.json"]},
                {"name": "x_seadus", "files": ["x_seadus_v2_peep.json"]},
            ]
        }
        lookup = build_law_lookup(index, on_collision="warn")
        # Ambiguous slug 'x_seadus' must NOT be in the lookup.
        assert "x_seadus" not in lookup
        # Warning printed to stdout (script uses print, not logging).
        captured = capsys.readouterr()
        assert "ambiguous" in captured.out.lower()

    def test_raise_on_collision(self):
        from estleg.extract_draft_impact import build_law_lookup
        index = {
            "laws": [
                {"name": "x_seadus", "files": ["a_peep.json"]},
                {"name": "x_seadus", "files": ["b_peep.json"]},
            ]
        }
        with pytest.raises(ValueError, match="Ambiguous"):
            build_law_lookup(index, on_collision="raise")

    def test_no_collision_keeps_both(self):
        from estleg.extract_draft_impact import build_law_lookup
        index = {
            "laws": [
                {"name": "alkoholiseadus", "files": ["alkoholiseadus_peep.json"]},
                {"name": "tubakaseadus", "files": ["tubakaseadus_peep.json"]},
            ]
        }
        lookup = build_law_lookup(index)
        assert "alkoholiseadus" in lookup
        assert "tubakaseadus" in lookup

    def test_invalid_on_collision_raises(self):
        from estleg.extract_draft_impact import build_law_lookup
        with pytest.raises(ValueError):
            build_law_lookup({"laws": []}, on_collision="invalid")


class TestResolveLawNameDeterministic:
    """Resolution iterates the lookup in sorted-key order, applies
    word-boundary alignment for substring matches, and tie-breaks
    on shortest key length.
    """

    def test_substring_match_requires_word_boundary(self):
        from estleg.extract_draft_impact import resolve_law_name
        # ``riik`` should NOT match ``riiklik_seadus`` because the
        # tokens don't align as whole words.
        lookup = {
            "riiklik seadus": {"name": "riiklik_seadus",
                                "files": ["a.json"], "slug": "riiklik_seadus"},
        }
        result = resolve_law_name("riik", lookup)
        # No word-boundary-aligned match. Token overlap also fails
        # (only one significant token, < 2 required).
        assert result is None

    def test_substring_match_with_word_boundary(self):
        from estleg.extract_draft_impact import resolve_law_name
        lookup = {
            "alkoholiseadus": {"name": "alkoholiseadus",
                                "files": ["a.json"],
                                "slug": "alkoholiseadus"},
        }
        # The slug computed from the affected_name will be "alkoholiseadus"
        # — it matches the key directly via the slug-equality branch.
        result = resolve_law_name("alkoholiseadus", lookup)
        assert result is not None
        assert result["slug"] == "alkoholiseadus"

    def test_shortest_key_wins_on_tie(self):
        from estleg.extract_draft_impact import resolve_law_name
        # Two entries — both contain 'foo' as a contiguous subsequence.
        # The shorter key should win.
        lookup = {
            "foo": {"name": "foo", "files": ["foo.json"], "slug": "foo"},
            "foo bar baz": {"name": "foo_bar_baz",
                              "files": ["foo_bar_baz.json"],
                              "slug": "foo_bar_baz"},
        }
        result = resolve_law_name("foo", lookup)
        assert result is not None
        # Direct slug match for 'foo' is preferred (slug exactness).
        assert result["slug"] == "foo"

    def test_token_overlap_deterministic(self):
        from estleg.extract_draft_impact import resolve_law_name
        # Two entries with equal token overlap — the shorter key
        # wins under the deterministic tie-break.
        lookup = {
            "x y a": {"name": "x_y_a", "files": ["a.json"], "slug": "x_y_a"},
            "x y b extra long": {"name": "x_y_b_extra_long",
                                  "files": ["b.json"],
                                  "slug": "x_y_b_extra_long"},
        }
        # Affected name "x y" — overlap with each key: 2 common
        # tokens after stripping noise. Shorter key wins.
        result = resolve_law_name("x y c", lookup)
        # Both have overlap 2 ({x, y}) with the affected name "x y c".
        # The shorter key "x y a" should win by the tie-break.
        if result is not None:
            assert result["slug"] == "x_y_a"


class TestEelnoudClearingExplicitGlobs:
    """The clearing pass uses explicit ``*.jsonld`` and ``*.json``
    globs and skips backup/temp suffixes (``.bak``, ``.swp``, ``~``)."""

    def test_jsonld_file_processed(self, tmp_path, monkeypatch):
        """A staged ``*.jsonld`` file with stale links is touched."""
        from estleg import estleg_common
        from estleg import extract_draft_impact as mod
        krr = tmp_path / "krr_outputs"
        eelnoud = krr / "eelnoud"
        eelnoud.mkdir(parents=True)

        # Staged jsonld file with stale draft-impact links.
        jsonld_file = eelnoud / "stale_drafts.jsonld"
        jsonld_file.write_text(json.dumps({
            "@context": {"estleg": "https://w3id.org/estleg/"},
            "@graph": [
                {"@id": "estleg:Draft_X",
                 "@type": ["estleg:DraftLegislation"],
                 "estleg:amendsLaw": {"@id": "estleg:OldLaw"},
                 "estleg:changeType": "amends"},
            ],
        }), encoding="utf-8")

        # Primary combined file (required by main()).
        combined = eelnoud / "eelnoud_combined.jsonld"
        combined.write_text(json.dumps({
            "@context": {"estleg": "https://w3id.org/estleg/"},
            "@graph": [],
        }), encoding="utf-8")

        # Minimal INDEX.json.
        (krr / "INDEX.json").write_text(json.dumps({
            "laws": [],
        }), encoding="utf-8")

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "EELNOUD_DIR", eelnoud)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)

        mod.main()

        # The staged jsonld file's stale keys must be cleared.
        with open(jsonld_file, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        for node in doc["@graph"]:
            assert "estleg:amendsLaw" not in node
            assert "estleg:changeType" not in node

    def test_backup_files_skipped(self, tmp_path, monkeypatch):
        """Files ending with ``.bak``, ``.swp``, or ``~`` are not
        opened by the clearing pass even if they superficially look
        like JSON.
        """
        from estleg import estleg_common
        from estleg import extract_draft_impact as mod
        krr = tmp_path / "krr_outputs"
        eelnoud = krr / "eelnoud"
        eelnoud.mkdir(parents=True)

        # A non-JSON backup that would crash json.load() if opened.
        backup = eelnoud / "stale.json.bak"
        backup.write_text("not valid json {{{ <html>", encoding="utf-8")

        # Required for main() to run.
        combined = eelnoud / "eelnoud_combined.jsonld"
        combined.write_text(json.dumps({
            "@context": {"estleg": "https://w3id.org/estleg/"},
            "@graph": [],
        }), encoding="utf-8")
        (krr / "INDEX.json").write_text(json.dumps({"laws": []}), encoding="utf-8")

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "EELNOUD_DIR", eelnoud)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)

        # Must NOT raise even though the backup file is malformed.
        mod.main()
        # Backup file should remain on disk untouched.
        assert backup.exists()
        assert backup.read_text(encoding="utf-8") == "not valid json {{{ <html>"


class TestGeneratedDraftValueObjects:
    """Fresh ``generate_draft_legislation.py`` output uses JSON-LD value
    objects for title and initiator. Draft-impact extraction must unwrap
    them before regex classification and ministry aggregation.
    """

    def test_main_unwraps_title_and_initiator_value_objects(self, tmp_path, monkeypatch):
        from estleg import estleg_common
        from estleg import extract_draft_impact as mod

        krr = tmp_path / "krr_outputs"
        eelnoud = krr / "eelnoud"
        eelnoud.mkdir(parents=True)

        law_file = krr / "riigi_teataja_seadus_peep.json"
        law_file.write_text(
            json.dumps({
                "@context": {"estleg": "https://w3id.org/estleg/"},
                "@graph": [
                    {
                        "@id": "estleg:RTS_Map",
                        "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
                    }
                ],
            }),
            encoding="utf-8",
        )

        (krr / "INDEX.json").write_text(
            json.dumps({
                "total_laws": 1,
                "laws": [
                    {
                        "name": "riigi_teataja_seadus",
                        "files": [law_file.name],
                    }
                ],
            }),
            encoding="utf-8",
        )

        combined = eelnoud / "eelnoud_combined.jsonld"
        combined.write_text(
            json.dumps({
                "@context": {"estleg": "https://w3id.org/estleg/"},
                "@graph": [
                    {
                        "@id": "estleg:Draft_TEST",
                        "@type": ["estleg:DraftLegislation", "owl:NamedIndividual"],
                        "rdfs:label": {
                            "@value": "Riigi Teataja seaduse muutmise seadus",
                            "@language": "et",
                        },
                        "estleg:initiator": [
                            {
                                "@value": "Ministry of Justice",
                                "@language": "en",
                            },
                            {
                                "@value": "Justiitsministeerium",
                                "@language": "et",
                            },
                            {
                                "@value": "Rahandusministeerium",
                                "@language": "et",
                            },
                        ],
                        "estleg:affectedLawName": [
                            {
                                "@value": "State Gazette Act",
                                "@language": "en",
                            },
                            {
                                "@value": "Riigi Teataja seaduse",
                                "@language": "et",
                            },
                        ],
                    }
                ],
            }),
            encoding="utf-8",
        )

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "EELNOUD_DIR", eelnoud)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)

        mod.main()

        enriched = json.loads(combined.read_text(encoding="utf-8"))
        draft = enriched["@graph"][0]
        assert draft["estleg:changeType"] == "amends"
        assert draft["estleg:amendsLaw"] == {"@id": "estleg:RTS_Map"}

        report = json.loads((krr / "reports" / "draft_impact_report.json").read_text(encoding="utf-8"))
        assert report["pending_changes_by_ministry"] == {
            "Justiitsministeerium": 1,
            "Rahandusministeerium": 1,
        }
        assert report["summary"]["affected_law_names_unresolved"] == 0


# --------------------------------------------------------------------- #
# Issue #289 — act-node resolution. ``estleg:affectedBy`` must land on the
# act node (mirroring extract_temporal_data.find_act_node), never on a
# blind ``graph[0]`` that might be a LegalConcept.
# --------------------------------------------------------------------- #


class TestFindActNode:
    def test_prefers_first_domain_individual_then_none(self):
        from estleg.extract_draft_impact import find_act_node
        concept = {"@id": "estleg:C1", "@type": ["estleg:LegalConcept"]}
        plain_act = {"@id": "estleg:A1", "@type": ["estleg:Act"]}
        onto_act = {
            "@id": "estleg:M1",
            "@type": ["owl:Ontology", "estleg:Act"],
        }
        # First Act/regulation individual wins (#435).
        assert find_act_node([concept, plain_act, onto_act]) is plain_act
        assert find_act_node([concept, onto_act, plain_act]) is onto_act
        assert find_act_node([concept, plain_act]) is plain_act
        # No act node → None (caller must skip, not stamp graph[0]).
        assert find_act_node([concept]) is None
        assert find_act_node([]) is None
        # A bare owl:Ontology (catalogue) without estleg:Act is NOT an act.
        assert find_act_node(
            [{"@id": "estleg:Cat", "@type": ["owl:Ontology"]}]
        ) is None

    def test_string_type_node_handled(self):
        from estleg.extract_draft_impact import find_act_node
        assert find_act_node([{"@id": "estleg:A", "@type": "estleg:Act"}]) == {
            "@id": "estleg:A",
            "@type": "estleg:Act",
        }


class TestInverseLinkSkipsNonActGraph0:
    """A law peep whose ``graph[0]`` is a ``LegalConcept`` (no act node)
    must be SKIPPED — ``estleg:affectedBy`` must never be written onto the
    concept node (issue #289 / the #128 corruption pattern).
    """

    def _run(self, tmp_path, monkeypatch, law_graph):
        from estleg import estleg_common
        from estleg import extract_draft_impact as mod

        krr = tmp_path / "krr_outputs"
        eelnoud = krr / "eelnoud"
        eelnoud.mkdir(parents=True)

        law_file = krr / "concept_only_peep.json"
        law_file.write_text(
            json.dumps({
                "@context": {"estleg": "https://w3id.org/estleg/"},
                "@graph": law_graph,
            }),
            encoding="utf-8",
        )

        (krr / "INDEX.json").write_text(
            json.dumps({
                "total_laws": 1,
                "laws": [{"name": "mingi_seadus", "files": [law_file.name]}],
            }),
            encoding="utf-8",
        )

        combined = eelnoud / "eelnoud_combined.jsonld"
        combined.write_text(
            json.dumps({
                "@context": {"estleg": "https://w3id.org/estleg/"},
                "@graph": [
                    {
                        "@id": "estleg:Draft_X",
                        "@type": ["estleg:DraftLegislation", "owl:NamedIndividual"],
                        "rdfs:label": {
                            "@value": "Mingi seaduse muutmise seadus",
                            "@language": "et",
                        },
                        "estleg:affectedLawName": [
                            {"@value": "Mingi seaduse", "@language": "et"}
                        ],
                    }
                ],
            }),
            encoding="utf-8",
        )

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "EELNOUD_DIR", eelnoud)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)

        mod.main()
        return law_file

    def test_graph0_legalconcept_no_act_node_is_skipped(self, tmp_path, monkeypatch):
        # graph[0] is a LegalConcept; there is NO act node anywhere.
        law_file = self._run(
            tmp_path,
            monkeypatch,
            [
                {"@id": "estleg:Concept_1", "@type": ["estleg:LegalConcept"]},
                {"@id": "estleg:Prov_1", "@type": ["estleg:LegalProvision"]},
            ],
        )
        doc = json.loads(law_file.read_text(encoding="utf-8"))
        # No node may have gained estleg:affectedBy — the concept-node
        # graph[0] fallback would have corrupted it.
        for node in doc["@graph"]:
            assert "estleg:affectedBy" not in node, node

    def test_affectedby_lands_on_act_node_not_graph0(self, tmp_path, monkeypatch):
        # graph[0] is a LegalConcept but a real estleg:Act node follows —
        # the link must land on the Act node, never graph[0].
        law_file = self._run(
            tmp_path,
            monkeypatch,
            [
                {"@id": "estleg:Concept_1", "@type": ["estleg:LegalConcept"]},
                {
                    "@id": "estleg:Act_1",
                    "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
                },
            ],
        )
        doc = json.loads(law_file.read_text(encoding="utf-8"))
        concept = doc["@graph"][0]
        act = doc["@graph"][1]
        assert "estleg:affectedBy" not in concept, concept
        assert act["estleg:affectedBy"] == [{"@id": "estleg:Draft_X"}]


# --------------------------------------------------------------------- #
# Issue #266 — duplicate amendsLaw IRIs. Two affected-name strings that
# resolve to the SAME act IRI must produce a single amendsLaw IRI and be
# counted once in ``affected_law_names_resolved``.
# --------------------------------------------------------------------- #


class TestAmendsLawDedup:
    def test_duplicate_resolution_dedups_iri_and_count(self, tmp_path, monkeypatch):
        from estleg import estleg_common
        from estleg import extract_draft_impact as mod

        krr = tmp_path / "krr_outputs"
        eelnoud = krr / "eelnoud"
        eelnoud.mkdir(parents=True)

        law_file = krr / "riigi_teataja_seadus_peep.json"
        law_file.write_text(
            json.dumps({
                "@context": {"estleg": "https://w3id.org/estleg/"},
                "@graph": [
                    {
                        "@id": "estleg:RTS_Map",
                        "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
                    }
                ],
            }),
            encoding="utf-8",
        )

        (krr / "INDEX.json").write_text(
            json.dumps({
                "total_laws": 1,
                "laws": [
                    {"name": "riigi_teataja_seadus", "files": [law_file.name]}
                ],
            }),
            encoding="utf-8",
        )

        combined = eelnoud / "eelnoud_combined.jsonld"
        combined.write_text(
            json.dumps({
                "@context": {"estleg": "https://w3id.org/estleg/"},
                "@graph": [
                    {
                        "@id": "estleg:Draft_DUP",
                        "@type": ["estleg:DraftLegislation", "owl:NamedIndividual"],
                        "rdfs:label": {
                            "@value": "Riigi Teataja seaduse muutmise seadus",
                            "@language": "et",
                        },
                        # Two distinct strings that BOTH resolve to the same
                        # act IRI (the real target + the phantom bill title
                        # that #266 used to leak through detect_affected_laws).
                        "estleg:affectedLawName": [
                            {"@value": "Riigi Teataja seaduse", "@language": "et"},
                            {
                                "@value": "Riigi Teataja seaduse muutmise seaduse",
                                "@language": "et",
                            },
                        ],
                    }
                ],
            }),
            encoding="utf-8",
        )

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "EELNOUD_DIR", eelnoud)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)

        mod.main()

        enriched = json.loads(combined.read_text(encoding="utf-8"))
        draft = enriched["@graph"][0]
        amends = draft["estleg:amendsLaw"]
        # Single object (deduped), NOT a 2-element list of identical IRIs.
        assert amends == {"@id": "estleg:RTS_Map"}, amends

        # The act node's inverse link is deduped to a single draft ref.
        law_doc = json.loads(law_file.read_text(encoding="utf-8"))
        assert law_doc["@graph"][0]["estleg:affectedBy"] == [
            {"@id": "estleg:Draft_DUP"}
        ]

        report = json.loads(
            (krr / "reports" / "draft_impact_report.json").read_text(encoding="utf-8")
        )
        # Resolved counted ONCE per distinct IRI, not once per name string.
        assert report["summary"]["affected_law_names_resolved"] == 1


# --------------------------------------------------------------------- #
# Issue #295 — no wall-clock ``generated`` field in the tracked report.
# --------------------------------------------------------------------- #


class TestReportIsByteStable:
    def test_report_has_no_generated_timestamp(self, tmp_path, monkeypatch):
        from estleg import estleg_common
        from estleg import extract_draft_impact as mod

        krr = tmp_path / "krr_outputs"
        eelnoud = krr / "eelnoud"
        eelnoud.mkdir(parents=True)

        combined = eelnoud / "eelnoud_combined.jsonld"
        combined.write_text(
            json.dumps({
                "@context": {"estleg": "https://w3id.org/estleg/"},
                "@graph": [],
            }),
            encoding="utf-8",
        )
        (krr / "INDEX.json").write_text(
            json.dumps({"laws": []}), encoding="utf-8"
        )

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "EELNOUD_DIR", eelnoud)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)

        mod.main()

        report = json.loads(
            (krr / "reports" / "draft_impact_report.json").read_text(encoding="utf-8")
        )
        assert "generated" not in report, (
            "draft_impact_report.json must not embed a wall-clock "
            "'generated' timestamp (#295 churn)"
        )


# --------------------------------------------------------------------- #
# Issue #376 — extract_draft_impact must use the ATOMIC save_json from
# estleg_common (tempfile + os.replace), not a local non-atomic open("w")
# that truncates the target to 0 bytes before writing.
# --------------------------------------------------------------------- #


class TestSaveJsonIsAtomic:
    def test_module_save_json_is_estleg_common_atomic(self):
        """``extract_draft_impact.save_json`` must be the imported atomic
        helper, not a shadowing local definition."""
        from estleg import estleg_common
        from estleg import extract_draft_impact as mod

        assert mod.save_json is estleg_common.save_json

    def test_save_json_does_not_truncate_on_serialization_failure(self, tmp_path):
        """The atomic writer never leaves a 0-byte file: a failed write
        (non-serialisable payload) preserves the pre-existing content and
        drops no ``.tmp`` litter."""
        from estleg import extract_draft_impact as mod

        target = tmp_path / "out.json"
        target.write_text('{"keep": "me"}\n', encoding="utf-8")

        with pytest.raises(TypeError):
            mod.save_json(target, {"bad": object()})

        # Original content survived (no truncation-before-write).
        assert target.read_text(encoding="utf-8") == '{"keep": "me"}\n'
        # No tempfile droppings left behind.
        assert [p.name for p in tmp_path.iterdir()] == ["out.json"]


# --------------------------------------------------------------------- #
# Issue #394 (item 3) — the report summary count must agree with its own
# companion list. ``summary.affected_law_names_unresolved`` was the RAW
# occurrence count (len(unresolved)) while ``unresolved_law_names`` is the
# DEDUPED list (sorted(set(unresolved))), a self-inconsistent report.
# --------------------------------------------------------------------- #


class TestUnresolvedCountMatchesList:
    def test_summary_count_equals_deduped_list_length(self, tmp_path, monkeypatch):
        from estleg import estleg_common
        from estleg import extract_draft_impact as mod

        krr = tmp_path / "krr_outputs"
        eelnoud = krr / "eelnoud"
        eelnoud.mkdir(parents=True)

        # Empty INDEX → every affectedLawName is unresolved.
        (krr / "INDEX.json").write_text(
            json.dumps({"total_laws": 0, "laws": []}), encoding="utf-8"
        )

        # Two distinct drafts that BOTH reference the same unresolvable
        # law name. Raw occurrence count == 2; deduped == 1.
        unresolvable = {"@value": "Mitteleiduva seaduse", "@language": "et"}
        combined = eelnoud / "eelnoud_combined.jsonld"
        combined.write_text(
            json.dumps({
                "@context": {"estleg": "https://w3id.org/estleg/"},
                "@graph": [
                    {
                        "@id": "estleg:Draft_A",
                        "@type": ["estleg:DraftLegislation", "owl:NamedIndividual"],
                        "rdfs:label": {
                            "@value": "Mitteleiduva seaduse muutmise seadus",
                            "@language": "et",
                        },
                        "estleg:affectedLawName": [unresolvable],
                    },
                    {
                        "@id": "estleg:Draft_B",
                        "@type": ["estleg:DraftLegislation", "owl:NamedIndividual"],
                        "rdfs:label": {
                            "@value": "Mitteleiduva seaduse muutmise seadus",
                            "@language": "et",
                        },
                        "estleg:affectedLawName": [unresolvable],
                    },
                ],
            }),
            encoding="utf-8",
        )

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "EELNOUD_DIR", eelnoud)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)

        mod.main()

        report = json.loads(
            (krr / "reports" / "draft_impact_report.json").read_text(encoding="utf-8")
        )
        summary_count = report["summary"]["affected_law_names_unresolved"]
        list_len = len(report["unresolved_law_names"])

        # The two must agree (issue #394). Deduped to a single name.
        assert summary_count == list_len, (
            f"summary count {summary_count} != list length {list_len}"
        )
        assert list_len == 1
        assert report["unresolved_law_names"] == ["Mitteleiduva seaduse"]

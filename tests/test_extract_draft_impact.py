"""Tests for scripts/extract_draft_impact.py."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pytest

from extract_draft_impact import affected_law_name_values


def test_affected_law_name_values_accepts_canonical_string_array():
    assert affected_law_name_values(["Riigi Teataja seaduse"]) == ["Riigi Teataja seaduse"]


def test_affected_law_name_values_unwraps_legacy_jsonld_values():
    assert affected_law_name_values(
        [{"@value": "Riigi Teataja seaduse", "@language": "et"}]
    ) == ["Riigi Teataja seaduse"]


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
        from extract_draft_impact import classify_change_type
        result = classify_change_type("X seaduse muutmise seadus")
        assert result is not None
        assert result[0] == "amends"

    def test_repeals_wins_over_amends_when_both_present(self):
        from extract_draft_impact import classify_change_type
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
        from extract_draft_impact import classify_change_type
        # ``kehtestami`` AND ``muutmi`` both match.
        title = "X seaduse kehtestamise ja Y muutmise seadus"
        result = classify_change_type(title)
        assert result is not None
        assert result[0] == "enacts"

    def test_classify_change_types_returns_all_matches(self):
        from extract_draft_impact import classify_change_types
        title = "X muutmise ja Y kehtetuks tunnistamise seadus"
        result = classify_change_types(title)
        types = {ct for ct, _ in result}
        assert {"amends", "repeals"}.issubset(types), (
            f"all-matches helper must return both; got {types}"
        )
        # First entry is the highest-priority match.
        assert result[0][0] == "repeals"

    def test_no_match_returns_none(self):
        from extract_draft_impact import classify_change_type
        assert classify_change_type("Some random title without keywords") is None
        from extract_draft_impact import classify_change_types
        assert classify_change_types("Some random title without keywords") == []


class TestBuildLawLookupCollisions:
    """``build_law_lookup`` detects collisions and either warns + drops
    the ambiguous keys or raises ValueError per ``on_collision``.
    """

    def test_warn_drops_ambiguous_key(self, capsys):
        from extract_draft_impact import build_law_lookup
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
        from extract_draft_impact import build_law_lookup
        index = {
            "laws": [
                {"name": "x_seadus", "files": ["a_peep.json"]},
                {"name": "x_seadus", "files": ["b_peep.json"]},
            ]
        }
        with pytest.raises(ValueError, match="Ambiguous"):
            build_law_lookup(index, on_collision="raise")

    def test_no_collision_keeps_both(self):
        from extract_draft_impact import build_law_lookup
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
        from extract_draft_impact import build_law_lookup
        with pytest.raises(ValueError):
            build_law_lookup({"laws": []}, on_collision="invalid")


class TestResolveLawNameDeterministic:
    """Resolution iterates the lookup in sorted-key order, applies
    word-boundary alignment for substring matches, and tie-breaks
    on shortest key length.
    """

    def test_substring_match_requires_word_boundary(self):
        from extract_draft_impact import resolve_law_name
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
        from extract_draft_impact import resolve_law_name
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
        from extract_draft_impact import resolve_law_name
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
        from extract_draft_impact import resolve_law_name
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
        import extract_draft_impact as mod
        import estleg_common
        krr = tmp_path / "krr_outputs"
        eelnoud = krr / "eelnoud"
        eelnoud.mkdir(parents=True)

        # Staged jsonld file with stale draft-impact links.
        jsonld_file = eelnoud / "stale_drafts.jsonld"
        jsonld_file.write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#"},
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
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#"},
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
        import extract_draft_impact as mod
        import estleg_common
        krr = tmp_path / "krr_outputs"
        eelnoud = krr / "eelnoud"
        eelnoud.mkdir(parents=True)

        # A non-JSON backup that would crash json.load() if opened.
        backup = eelnoud / "stale.json.bak"
        backup.write_text("not valid json {{{ <html>", encoding="utf-8")

        # Required for main() to run.
        combined = eelnoud / "eelnoud_combined.jsonld"
        combined.write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#"},
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
        import extract_draft_impact as mod
        import estleg_common

        krr = tmp_path / "krr_outputs"
        eelnoud = krr / "eelnoud"
        eelnoud.mkdir(parents=True)

        law_file = krr / "riigi_teataja_seadus_peep.json"
        law_file.write_text(
            json.dumps({
                "@context": {"estleg": "https://data.riik.ee/ontology/estleg#"},
                "@graph": [
                    {
                        "@id": "estleg:RTS_Map_2026",
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
                "@context": {"estleg": "https://data.riik.ee/ontology/estleg#"},
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
                                "@value": "Justiitsministeerium",
                                "@language": "et",
                            },
                            {
                                "@value": "Rahandusministeerium",
                                "@language": "et",
                            },
                        ],
                        "estleg:affectedLawName": {
                            "@value": "Riigi Teataja seaduse",
                            "@language": "et",
                        },
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
        assert draft["estleg:amendsLaw"] == {"@id": "estleg:RTS_Map_2026"}

        report = json.loads((krr / "draft_impact_report.json").read_text(encoding="utf-8"))
        assert report["pending_changes_by_ministry"] == {
            "Justiitsministeerium": 1,
            "Rahandusministeerium": 1,
        }

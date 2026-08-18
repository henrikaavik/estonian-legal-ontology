"""Tests for the estleg_common robustness bundle (issue #600).

Four footguns in helpers used across the whole pipeline:
  (a) ``sanitize_id`` en-dash collision (two distinct §-ranges → one IRI);
  (b) ``iter_krr_jsonld_files`` case-sensitive glob (uppercase suffix skipped);
  (c) ``_safe_load`` / ``pair_peep_with_xml`` mislabel OSError as
      ``json_decode_error`` (a transient I/O fault counted as corruption);
  (d) ``jsonld_text(prefer_language=...)`` silent-empty on a scalar-dict
      language mismatch (et-only title under ``prefer_language='en'`` → "").
"""
from __future__ import annotations

import builtins
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from estleg_common import (
    _RunCounters,
    _safe_load,
    iter_krr_jsonld_files,
    jsonld_text,
    pair_peep_with_xml,
    sanitize_id,
)


class TestSanitizeIdDashCollision:
    """#600(a) — an en-dash §-range must not collapse into a bare number that
    collides with a real provision number."""

    def test_endash_range_does_not_collide_with_plain_number(self):
        assert sanitize_id("1–94") != sanitize_id("194")

    def test_endash_range_canonicalises_to_to(self):
        assert sanitize_id("1–94") == "1_to_94"

    def test_typographic_dash_glyphs_canonicalise_to_to(self):
        # figure dash (U+2012), em dash (U+2014), minus sign (U+2212) — the
        # typographic range RT uses in paragraphNr ranges — all map to _to_.
        assert sanitize_id("3‒5") == "3_to_5"
        assert sanitize_id("3—5") == "3_to_5"
        assert sanitize_id("3−5") == "3_to_5"

    def test_ascii_hyphen_numeric_range_canonicalises_to_to(self):
        # #449: an ASCII-hyphen §-range must not collapse to a bare number.
        assert sanitize_id("3-5") == "3_to_5"

    def test_plain_value_is_unchanged(self):
        assert sanitize_id("194") == "194"

    def test_matches_local_generate_all_laws_definition(self):
        # The shared copy must now agree with the (previously divergent)
        # local generate_all_laws.sanitize_id (#600 sync requirement).
        from generate_all_laws import sanitize_id as local_sanitize_id

        for val in ("1–94", "194", "§ 5", "3-5", "Karistus"):
            assert sanitize_id(val) == local_sanitize_id(val), val


class TestIterKrrJsonldFilesCaseInsensitive:
    """#600(b) — an uppercase-suffix corpus file must still be enumerated, so
    it cannot pass CI invisibly by being excluded from every validator."""

    def test_uppercase_suffix_file_is_yielded(self, tmp_path):
        (tmp_path / "lower.json").write_text("{}", encoding="utf-8")
        (tmp_path / "UPPER.JSON").write_text("{}", encoding="utf-8")
        (tmp_path / "mixed.JsonLd").write_text("{}", encoding="utf-8")
        (tmp_path / "ignore.txt").write_text("x", encoding="utf-8")

        names = {p.name for p in iter_krr_jsonld_files(tmp_path)}
        assert names == {"lower.json", "UPPER.JSON", "mixed.JsonLd"}

    def test_operational_state_files_still_excluded(self, tmp_path):
        (tmp_path / "real_peep.json").write_text("{}", encoding="utf-8")
        (tmp_path / ".regen_state.json").write_text("{}", encoding="utf-8")
        names = {p.name for p in iter_krr_jsonld_files(tmp_path)}
        assert names == {"real_peep.json"}

    def test_directories_are_not_yielded(self, tmp_path):
        # A directory whose name ends in .json must not be yielded as a file.
        (tmp_path / "weird.json").mkdir()
        (tmp_path / "file.json").write_text("{}", encoding="utf-8")
        out = list(iter_krr_jsonld_files(tmp_path))
        assert [p.name for p in out] == ["file.json"]


class TestSafeLoadErrorClassification:
    """#600(c) — corruption (ValueError) and I/O fault (OSError) must be
    recorded under DISTINCT reasons; an OSError on a valid file is not
    corruption."""

    def test_corrupt_json_recorded_as_json_decode_error(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{not valid json", encoding="utf-8")
        counters = _RunCounters()
        assert _safe_load(bad, counters) is None
        assert counters.skip_reasons.get("json_decode_error") == 1
        assert "io_error" not in counters.skip_reasons

    def test_oserror_recorded_as_io_error(self, tmp_path, monkeypatch):
        valid = tmp_path / "valid.json"
        valid.write_text("{}", encoding="utf-8")
        counters = _RunCounters()

        real_open = builtins.open

        def _raise_perm(path, *args, **kwargs):
            if Path(path) == valid:
                raise PermissionError(13, "Permission denied")
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", _raise_perm)
        assert _safe_load(valid, counters) is None
        assert counters.skip_reasons.get("io_error") == 1
        assert "json_decode_error" not in counters.skip_reasons

    def test_pair_peep_with_xml_oserror_is_io_error(self, tmp_path, monkeypatch):
        peep = tmp_path / "x_peep.json"
        peep.write_text("{}", encoding="utf-8")
        counters = _RunCounters()

        real_open = builtins.open

        def _raise_io(path, *args, **kwargs):
            if Path(path) == peep:
                raise OSError(5, "I/O error")
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", _raise_io)
        result = pair_peep_with_xml(peep, {}, counters=counters)
        assert result is None
        assert counters.skip_reasons.get("io_error") == 1
        assert "json_decode_error" not in counters.skip_reasons

    def test_pair_peep_with_xml_corrupt_is_json_decode_error(self, tmp_path):
        peep = tmp_path / "x_peep.json"
        peep.write_text("{bad", encoding="utf-8")
        counters = _RunCounters()
        result = pair_peep_with_xml(peep, {}, counters=counters)
        assert result is None
        assert counters.skip_reasons.get("json_decode_error") == 1
        assert "io_error" not in counters.skip_reasons


class TestJsonldTextSilentEmptyFallback:
    """#600(d) — a lone scalar value-object whose @language differs from
    prefer_language must still surface its @value (the docstring promises the
    fallback the list branch has), not return ""."""

    def test_scalar_offlanguage_value_is_not_emptied(self):
        et_title = {"@value": "Pealkiri", "@language": "et"}
        assert jsonld_text(et_title, prefer_language="en") == "Pealkiri"

    def test_list_still_prefers_requested_language(self):
        value = [
            {"@value": "one", "@language": "en"},
            {"@value": "üks", "@language": "et"},
        ]
        assert jsonld_text(value, prefer_language="et") == "üks"

    def test_list_falls_back_when_no_match(self):
        value = [
            {"@value": "one", "@language": "en"},
            {"@value": "üks", "@language": "et"},
        ]
        assert jsonld_text(value, prefer_language="de") == "one üks"

    def test_untagged_scalar_under_preference_kept(self):
        assert jsonld_text({"@value": "üks"}, prefer_language="et") == "üks"

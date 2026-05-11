"""Tests for scripts/build_kov_registry.py CLI behaviour.

Drives the `build()` function directly with a tmp-path repo root,
which exercises the same code path as the CLI without subprocess
overhead or symlink fragility.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


from build_kov_registry import (
    _on_disk_issuers_json,
    build,
    canonical_issuers_json,
    compute_issuers_sha256,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "kov_layer1"
MIN_MUNICIPALITIES = FIXTURE_DIR / "municipalities_min.json"


def _stage_repo(
    tmp_path: Path,
    kov_slugs: list[str],
    curated_csv: str,
) -> Path:
    """Create a fake repo tree under tmp_path so build() can run."""
    (tmp_path / "krr_outputs" / "regulations" / "kov").mkdir(parents=True)
    (tmp_path / "data" / "ehak").mkdir(parents=True)
    for slug in kov_slugs:
        (tmp_path / "krr_outputs" / "regulations" / "kov" / slug).mkdir()
    shutil.copy(
        MIN_MUNICIPALITIES,
        tmp_path / "data" / "ehak" / "municipalities.json",
    )
    (tmp_path / "data" / "ehak" / "issuer_successor_map.csv").write_text(
        curated_csv, encoding="utf-8"
    )
    return tmp_path


HEADER = "issuer_slug,current_municipality_ehak,mapping_source,mapping_evidence\n"


class TestBuildKovRegistry:
    def test_success_writes_issuers_json(self, tmp_path, capsys):
        repo = _stage_repo(
            tmp_path,
            kov_slugs=["tallinna_linnavolikogu", "abja_vallavolikogu"],
            curated_csv=(
                HEADER
                + "abja_vallavolikogu,0480,haldusreform-2017,RT I 21.06.2017 1\n"
            ),
        )
        rc = build(repo)
        assert rc == 0
        out_path = repo / "data" / "ehak" / "issuers.json"
        assert out_path.exists()
        rows = json.load(out_path.open())
        slugs = {r["slug"] for r in rows}
        assert slugs == {"tallinna_linnavolikogu", "abja_vallavolikogu"}
        captured = capsys.readouterr()
        assert "auto-match" in captured.out

    def test_unmapped_issuer_exits_nonzero(self, tmp_path, capsys):
        repo = _stage_repo(
            tmp_path,
            kov_slugs=["obscure_vallavolikogu"],
            curated_csv=HEADER,
        )
        rc = build(repo)
        assert rc != 0
        captured = capsys.readouterr()
        assert "obscure_vallavolikogu" in captured.err

    def test_curated_unknown_ehak_exits_nonzero(self, tmp_path, capsys):
        repo = _stage_repo(
            tmp_path,
            kov_slugs=["abja_vallavolikogu"],
            curated_csv=(
                HEADER
                + "abja_vallavolikogu,9999,haldusreform-2017,evidence\n"
            ),
        )
        rc = build(repo)
        assert rc != 0
        captured = capsys.readouterr()
        assert "9999" in (captured.err + captured.out)

    def test_writes_slug_sorted_array(self, tmp_path):
        # Regression test for Finding #9: the on-disk array MUST be
        # slug-sorted regardless of input ordering. The contract is
        # documented in `build_issuer_registry`'s docstring; this
        # test pins it.
        repo = _stage_repo(
            tmp_path,
            kov_slugs=["zzz_linnavolikogu", "tallinna_linnavolikogu"],
            curated_csv=(
                HEADER
                + "zzz_linnavolikogu,0784,manual-review,evidence-zzz\n"
            ),
        )
        rc = build(repo)
        assert rc == 0
        rows = json.load((repo / "data" / "ehak" / "issuers.json").open())
        assert [r["slug"] for r in rows] == [
            "tallinna_linnavolikogu",
            "zzz_linnavolikogu",
        ]

    def test_emits_canonical_sha256_in_output(self, tmp_path, capsys):
        # The CLI must print the canonical sha256 so audits can diff
        # the digest across runs without parsing the file content.
        repo = _stage_repo(
            tmp_path,
            kov_slugs=["tallinna_linnavolikogu"],
            curated_csv=HEADER,
        )
        rc = build(repo)
        assert rc == 0
        captured = capsys.readouterr()
        assert "sha256:" in captured.out
        # Pull the digest out of the printed line and confirm it
        # matches what compute_issuers_sha256 returns for the rows
        # written.
        rows = json.load((repo / "data" / "ehak" / "issuers.json").open())
        expected = compute_issuers_sha256(rows)
        assert expected in captured.out


class TestCanonicalIssuersJson:
    """Pin the sort contract explicitly: the canonical form is the
    input to sha256 and must not depend on dict-key order or input
    array order.
    """

    def test_top_level_array_sorted_by_slug(self):
        rows = [
            {"slug": "z_x", "displayName": "Z", "bodyType": "volikogu",
             "currentMunicipalityCode": "0001", "mappingSource": "auto-match",
             "mappingEvidence": "", "historicalMunicipalityName": ""},
            {"slug": "a_x", "displayName": "A", "bodyType": "volikogu",
             "currentMunicipalityCode": "0002", "mappingSource": "auto-match",
             "mappingEvidence": "", "historicalMunicipalityName": ""},
        ]
        canonical = canonical_issuers_json(rows)
        # The 'a_x' row appears before 'z_x' regardless of input order.
        assert canonical.index('"slug": "a_x"') < canonical.index(
            '"slug": "z_x"'
        )

    def test_dict_keys_sorted_in_canonical_form(self):
        rows = [{
            "slug": "tallinna_linnavolikogu",
            "displayName": "Tallinna Linnavolikogu",
            "bodyType": "volikogu",
            "currentMunicipalityCode": "0784",
            "mappingSource": "auto-match",
            "mappingEvidence": "",
            "historicalMunicipalityName": "Tallinn",
        }]
        canonical = canonical_issuers_json(rows)
        # bodyType comes before displayName alphabetically; the
        # canonical form sorts dict keys so this ordering is fixed.
        assert canonical.index('"bodyType"') < canonical.index('"displayName"')

    def test_canonical_sha256_stable_across_input_order(self):
        # Same data, different input order → same sha256. This is the
        # whole point of the canonical form: hashes can be diffed
        # across runs without false positives from dict-iteration.
        rows_a = [
            {"slug": "a_x", "displayName": "A"},
            {"slug": "b_x", "displayName": "B"},
        ]
        rows_b = list(reversed(rows_a))
        assert compute_issuers_sha256(rows_a) == compute_issuers_sha256(rows_b)

    def test_on_disk_form_uses_insertion_order(self):
        # The on-disk form preserves insertion order so that the file
        # diff against the historical shape is empty when the registry
        # itself is unchanged.
        rows = [{
            "slug": "tallinna_linnavolikogu",
            "displayName": "Tallinna Linnavolikogu",
            "bodyType": "volikogu",
            "currentMunicipalityCode": "0784",
            "mappingSource": "auto-match",
            "mappingEvidence": "",
            "historicalMunicipalityName": "Tallinn",
        }]
        on_disk = _on_disk_issuers_json(rows)
        # 'slug' is inserted first; in the canonical form 'bodyType'
        # would come first instead. This test pins the on-disk form
        # to insertion order so we don't accidentally swap it.
        assert on_disk.index('"slug"') < on_disk.index('"displayName"')

    def test_sha256_matches_hashlib(self):
        # Belt + braces: the canonical sha256 helper must agree with
        # raw hashlib applied to the canonical bytes. If anyone
        # refactors compute_issuers_sha256 to use a different digest
        # or encoding, this test fails.
        rows = [{
            "slug": "a", "displayName": "A", "bodyType": "v",
            "currentMunicipalityCode": "0001", "mappingSource": "auto-match",
            "mappingEvidence": "", "historicalMunicipalityName": "",
        }]
        expected = hashlib.sha256(
            canonical_issuers_json(rows).encode("utf-8")
        ).hexdigest()
        assert compute_issuers_sha256(rows) == expected

"""Dry-run regulation provision IRI rename map (#722)."""
from __future__ import annotations

import json
from pathlib import Path

from estleg.regulation_iri_rename_map import (
    act_pairs_from_peep,
    compute_rename_map,
    law_suffix_from_display,
    main,
)


def _peep(tid: str, provisions: list[tuple[str, str]], extra: list[dict] | None = None) -> dict:
    graph: list[dict] = [{
        "@id": f"estleg:Reg_{tid}_Map",
        "@type": ["estleg:Act", "estleg:NationalRegulation"],
        "estleg:terviktekstId": tid,
    }]
    for suffix, display in provisions:
        graph.append({
            "@id": f"estleg:Reg_{tid}_Par_{suffix}",
            "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
            "estleg:paragrahv": display,
        })
    graph.extend(extra or [])
    return {"@graph": graph}


# Legacy IRIs exactly as the pre-#722 generator minted them for
# § 1, § 1¹, § 1a, § 2, § 2 (dup), § 7¹ ... § 7³ (positional suffixes).
LEGACY_ACT = [
    ("1", "§ 1."),
    ("1_1", "§ 1¹."),
    ("1a", "§ 1a."),
    ("2", "§ 2."),
    ("2_4", "§ 2."),
    ("7", "§ 7."),
    ("7_6", "§ 7¹."),
    ("7_7", "§ 7²."),
    ("7_8", "§ 7³."),
]


def _write_corpus(root: Path) -> Path:
    regs = root / "regulations"
    (regs / "riik").mkdir(parents=True)
    (regs / "kov" / "vald").mkdir(parents=True)
    (regs / "riik" / "a_t100_peep.json").write_text(json.dumps(_peep("100", LEGACY_ACT)), encoding="utf-8")
    (regs / "kov" / "vald" / "b_t200_peep.json").write_text(
        json.dumps(_peep("200", [("4", "§ 4."), ("41", "§ 4′1"), ("41_2", "§ 41.")])),
        encoding="utf-8",
    )
    (regs / "riik" / "REGULATIONS_RIIK_INDEX.json").write_text("{}", encoding="utf-8")
    return regs


class TestDisplayDerivation:
    def test_structured_superscript(self):
        assert law_suffix_from_display("§ 9¹.") == ("9_1", "9")
        assert law_suffix_from_display("§ 9<sup>2</sup>.") == ("9_2", "9")

    def test_plain_and_lettered(self):
        assert law_suffix_from_display("§ 12.") == ("12", "12")
        assert law_suffix_from_display("§ 1a.") == ("1a", "1a")

    def test_html_prime(self):
        assert law_suffix_from_display("§ 4′1") == ("4_1", "4′1")

    def test_unrecognisable(self):
        assert law_suffix_from_display("§ ?") is None


class TestActPairs:
    def test_pairs_follow_the_generator_law_scheme(self):
        pairs, unresolved = act_pairs_from_peep(_peep("100", LEGACY_ACT))
        mapping = dict(pairs)
        assert unresolved == 0
        assert mapping["estleg:Reg_100_Par_1_1"] == "estleg:Reg_100_Par_1_1"
        assert mapping["estleg:Reg_100_Par_2_4"] == "estleg:Reg_100_Par_2_x2"
        assert mapping["estleg:Reg_100_Par_7_6"] == "estleg:Reg_100_Par_7_1"
        assert mapping["estleg:Reg_100_Par_7_7"] == "estleg:Reg_100_Par_7_2"
        assert mapping["estleg:Reg_100_Par_7_8"] == "estleg:Reg_100_Par_7_3"

    def test_html_prime_collision_is_split(self):
        pairs, _ = act_pairs_from_peep(_peep("200", [("4", "§ 4."), ("41", "§ 4′1"), ("41_2", "§ 41.")]))
        mapping = dict(pairs)
        assert mapping["estleg:Reg_200_Par_41"] == "estleg:Reg_200_Par_4_1"
        assert mapping["estleg:Reg_200_Par_41_2"] == "estleg:Reg_200_Par_41"

    def test_mismatched_display_is_unresolved_and_kept(self):
        pairs, unresolved = act_pairs_from_peep(_peep("300", [("3_13", "§ 14.")]))
        assert unresolved == 1
        assert pairs == [("estleg:Reg_300_Par_3_13", "estleg:Reg_300_Par_3_13")]

    def test_subsections_follow_their_parent(self):
        sub = {"@id": "estleg:Reg_100_Par_7_6_Lg_1", "@type": ["estleg:Subsection"]}
        pairs, _ = act_pairs_from_peep(_peep("100", LEGACY_ACT, [sub]))
        assert dict(pairs)["estleg:Reg_100_Par_7_6_Lg_1"] == "estleg:Reg_100_Par_7_1_Lg_1"


class TestComputeRenameMap:
    def test_totals_and_collisions(self, tmp_path: Path):
        report = compute_rename_map(_write_corpus(tmp_path))
        totals = report["totals"]
        assert totals["acts"] == 2
        assert totals["provisions"] == 12
        # riik: 2_4, 7_6, 7_7, 7_8 change; kov: 41 and 41_2 change.
        assert totals["changedProvisions"] == 6
        assert report["applied"] is False
        assert report["collisions"]["duplicateNewIris"] == 0
        # kov: new Par_41 is the CURRENT IRI of a different node (§ 4′1).
        assert report["collisions"]["chainConflicts"] == 1
        assert report["collisions"]["chainConflictExamples"] == ["estleg:Reg_200_Par_41"]

    def test_deterministic(self, tmp_path: Path):
        regs = _write_corpus(tmp_path)
        first = compute_rename_map(regs, scan_references=True)
        second = compute_rename_map(regs, scan_references=True)
        assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
        assert list(first["map"]) == sorted(first["map"])

    def test_reference_scan_counts_other_files_only(self, tmp_path: Path):
        regs = _write_corpus(tmp_path)
        (regs / "riik" / "c_t400_peep.json").write_text(json.dumps({
            "@graph": [{"@id": "estleg:Reg_400_Map", "estleg:terviktekstId": "400",
                        "rdfs:seeAlso": {"@id": "estleg:Reg_100_Par_7_6"}}],
        }), encoding="utf-8")
        report = compute_rename_map(regs, scan_references=True)
        assert report["externalReferences"]["referencesToChangedIris"] == 1

    def test_cli_writes_only_to_output(self, tmp_path: Path, capsys):
        regs = _write_corpus(tmp_path)
        before = sorted(p.read_bytes() for p in regs.rglob("*.json"))
        out = tmp_path / "map.json"
        assert main(["--regulations-dir", str(regs), "--output", str(out)]) == 0
        assert sorted(p.read_bytes() for p in regs.rglob("*.json")) == before
        written = json.loads(out.read_text(encoding="utf-8"))
        assert written["map"]["estleg:Reg_100_Par_7_6"] == "estleg:Reg_100_Par_7_1"
        assert '"changedProvisions": 6' in capsys.readouterr().out

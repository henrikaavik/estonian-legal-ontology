"""Tests for scripts/fix_all_issues.py builders.

Focus: the canonical combined-ontology builder must NEVER re-ingest the
previous `combined_ontology.jsonld` output, must respect the explicit
JSON-LD allowlist, and must be idempotent across consecutive runs.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import fix_all_issues  # noqa: E402


def write_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def graph_ids(doc: dict) -> set[str]:
    return {n["@id"] for n in doc.get("@graph", []) if isinstance(n, dict) and "@id" in n}


def make_source_doc(ids: list[str]) -> dict:
    return {
        "@graph": [
            {
                "@id": nid,
                "@type": ["estleg:LegalProvision"],
                "estleg:paragrahv": "§ 1.",
                "estleg:summary": "Summary",
                "estleg:requestedCluster": {"@id": f"{nid}_cluster"},
            }
            for nid in ids
        ]
    }


def test_canonical_combined_inputs_excludes_output_and_unallowed_jsonld(tmp_path):
    write_json(tmp_path / "law_a_peep.json", make_source_doc(["estleg:A_1"]))
    write_json(tmp_path / "controlled_vocabulary.jsonld", {"@graph": [{"@id": "estleg:Act"}]})
    # combined output file from a previous build — must be excluded
    write_json(tmp_path / "combined_ontology.jsonld", {"@graph": [{"@id": "estleg:Stale_1"}]})
    # arbitrary aggregate/report — must be excluded
    write_json(tmp_path / "draft_impact_report.json", {"@graph": [{"@id": "estleg:Report"}]})
    # arbitrary jsonld outside the allowlist — must be excluded
    write_json(tmp_path / "random_aggregate.jsonld", {"@graph": [{"@id": "estleg:Random"}]})

    inputs = fix_all_issues.canonical_combined_inputs(tmp_path)
    names = [p.name for p in inputs]

    assert "law_a_peep.json" in names
    assert "controlled_vocabulary.jsonld" in names
    assert "combined_ontology.jsonld" not in names
    assert "draft_impact_report.json" not in names
    assert "random_aggregate.jsonld" not in names


def test_combined_builder_does_not_carry_forward_stale_nodes(tmp_path):
    # Canonical source: one node from the current corpus.
    write_json(tmp_path / "law_a_peep.json", make_source_doc(["estleg:A_1"]))
    # Allowlisted vocabulary: contributes a stable node.
    write_json(
        tmp_path / "controlled_vocabulary.jsonld",
        {"@graph": [{"@id": "estleg:Vocab_X", "@type": ["owl:Class"]}]},
    )
    # Stale previous combined: contains a node that no longer exists in
    # any source. The new builder must not pick this up.
    write_json(
        tmp_path / "combined_ontology.jsonld",
        {
            "@graph": [
                {
                    "@id": "estleg:Stale_Old_1",
                    "@type": ["estleg:LegalProvision"],
                    "estleg:requestedCluster": "estleg:Stale_Cluster",  # the bad string-literal shape
                }
            ]
        },
    )

    fix_all_issues.generate_combined_jsonld(tmp_path)

    rebuilt = read_json(tmp_path / "combined_ontology.jsonld")
    ids = graph_ids(rebuilt)
    assert "estleg:A_1" in ids
    assert "estleg:Vocab_X" in ids
    assert "estleg:Stale_Old_1" not in ids


def test_combined_builder_is_idempotent(tmp_path):
    write_json(tmp_path / "law_a_peep.json", make_source_doc(["estleg:A_1", "estleg:A_2"]))
    write_json(
        tmp_path / "controlled_vocabulary.jsonld",
        {"@graph": [{"@id": "estleg:Vocab_X", "@type": ["owl:Class"]}]},
    )

    fix_all_issues.generate_combined_jsonld(tmp_path)
    first = read_json(tmp_path / "combined_ontology.jsonld")
    fix_all_issues.generate_combined_jsonld(tmp_path)
    second = read_json(tmp_path / "combined_ontology.jsonld")

    assert first == second
    assert graph_ids(second) == {"estleg:A_1", "estleg:A_2", "estleg:Vocab_X"}


def test_combined_builder_skips_unallowed_root_jsonld(tmp_path):
    write_json(tmp_path / "law_a_peep.json", make_source_doc(["estleg:A_1"]))
    write_json(
        tmp_path / "some_other_aggregate.jsonld",
        {"@graph": [{"@id": "estleg:NotASource", "@type": ["owl:Thing"]}]},
    )

    fix_all_issues.generate_combined_jsonld(tmp_path)

    rebuilt = read_json(tmp_path / "combined_ontology.jsonld")
    assert "estleg:NotASource" not in graph_ids(rebuilt)
    assert "estleg:A_1" in graph_ids(rebuilt)

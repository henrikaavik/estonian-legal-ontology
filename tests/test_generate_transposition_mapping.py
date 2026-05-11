from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import generate_transposition_mapping as mod  # noqa: E402


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_missing_directive_celex_is_not_synthesized() -> None:
    assert mod.resolve_directive_iri("32000L0001", {}) is None
    assert mod.resolve_directive_iri(
        "32000L0001", {"32000L0001": "estleg:EU_32000L0001"}
    ) == "estleg:EU_32000L0001"


def test_transposition_schema_is_act_level() -> None:
    schema = mod.generate_schema()
    nodes = {node["@id"]: node for node in schema["@graph"]}

    assert nodes["estleg:transposesDirective"]["rdfs:domain"] == {"@id": "estleg:Act"}
    assert nodes["estleg:transposedBy"]["rdfs:range"] == {"@id": "estleg:Act"}
    assert nodes["estleg:transpositionStatus"]["rdfs:domain"] == {"@id": "estleg:Act"}


def test_law_target_iri_uses_real_ontology_node(tmp_path: Path) -> None:
    law_path = tmp_path / "law_peep.json"
    _write_json(
        law_path,
        {
            "@context": mod.CONTEXT,
            "@graph": [
                {
                    "@id": "estleg:AS_Map_2026",
                    "@type": ["owl:Ontology", "estleg:Act"],
                },
                {
                    "@id": "estleg:AS_Par_1",
                    "@type": ["owl:NamedIndividual"],
                },
            ],
        },
    )

    assert mod.get_law_transposition_target_iri(law_path) == "estleg:AS_Map_2026"


def test_inverse_transposed_by_links_to_real_law_node(
    tmp_path: Path, monkeypatch
) -> None:
    eurlex_dir = tmp_path / "eurlex"
    directives_path = eurlex_dir / "eurlex_directives_peep.json"
    _write_json(
        directives_path,
        {
            "@context": mod.CONTEXT,
            "@graph": [
                {
                    "@id": "estleg:EU_32000L0001",
                    "@type": ["estleg:EULegislation"],
                    "estleg:celexNumber": "32000L0001",
                }
            ],
        },
    )
    monkeypatch.setattr(mod, "EURLEX_DIR", eurlex_dir)

    updated = mod.update_directive_file(
        {"32000L0001": ["estleg:AS_Map_2026"]}
    )

    assert updated == 1
    doc = json.loads(directives_path.read_text(encoding="utf-8"))
    assert doc["@graph"][0]["estleg:transposedBy"] == [
        {"@id": "estleg:AS_Map_2026"}
    ]

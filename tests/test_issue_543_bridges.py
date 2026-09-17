"""#543 — schema.org / Dublin Core T-Box bridges and act-root dual-typing.

T-Box lives in controlled_vocabulary.jsonld. New law generates emit
``schema:Legislation`` on the act root; existing peeps are not rewritten.
Do not load combined_ontology.jsonld.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

from estleg import estleg_common, generate_all_laws

REPO = Path(__file__).resolve().parent.parent
VOCAB = REPO / "krr_outputs" / "controlled_vocabulary.jsonld"

SCHEMA_URI = "https://schema.org/"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _index(doc: dict) -> dict[str, dict]:
    return {
        node["@id"]: node
        for node in doc.get("@graph", [])
        if isinstance(node, dict) and isinstance(node.get("@id"), str)
    }


def _as_ids(value: object) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        return {value}
    if isinstance(value, dict):
        ids: set[str] = set()
        iri = value.get("@id")
        if isinstance(iri, str):
            ids.add(iri)
        for item in value.values():
            ids |= _as_ids(item)
        return ids
    if isinstance(value, list):
        ids: set[str] = set()
        for item in value:
            ids |= _as_ids(item)
        return ids
    return set()


def test_cv_context_declares_official_schema_org_namespace() -> None:
    ctx = _load(VOCAB)["@context"]
    assert ctx["schema"] == SCHEMA_URI


def test_act_subclass_of_eli_legal_resource_and_schema_legislation() -> None:
    node = _index(_load(VOCAB))["estleg:Act"]
    parents = _as_ids(node.get("rdfs:subClassOf"))
    assert "eli:LegalResource" in parents
    assert "schema:Legislation" in parents


def test_legal_text_subproperty_of_schema_text() -> None:
    node = _index(_load(VOCAB))["estleg:legalText"]
    assert "schema:text" in _as_ids(node.get("rdfs:subPropertyOf"))


def test_references_subproperty_of_dcterms_references() -> None:
    node = _index(_load(VOCAB))["estleg:references"]
    assert "dcterms:references" in _as_ids(node.get("rdfs:subPropertyOf"))


def test_entry_into_force_not_also_schema_legislation_date() -> None:
    """#440 ELI bridge stays the only rdfs:subPropertyOf; #543 is comment-only."""
    node = _index(_load(VOCAB))["estleg:entryIntoForce"]
    parents = _as_ids(node.get("rdfs:subPropertyOf"))
    assert "eli:date_entry_in_force" in parents
    assert "schema:legislationDate" not in parents


def test_shared_context_declares_schema_and_strip_keeps_it() -> None:
    assert estleg_common.CONTEXT["schema"] == SCHEMA_URI
    raw = (
        '{\n  "@context": {\n    "owl": "http://www.w3.org/2002/07/owl#",\n'
        f'    "schema": "{SCHEMA_URI}",\n'
        '    "rdfs": "http://www.w3.org/2000/01/rdf-schema#"\n  }\n}\n'
    )
    out = estleg_common.strip_unused_jsonld_context_prefixes(raw, drop_schema=True)
    assert SCHEMA_URI in out
    assert estleg_common.UNUSED_SCHEMA_CONTEXT_LINE in out


def test_generated_act_root_types_include_schema_legislation() -> None:
    xml = """
    <akt>
      <sisu>
        <paragrahv>
          <paragrahvNr>1</paragrahvNr>
          <kuvatavNr>S 1.</kuvatavNr>
          <paragrahvPealkiri>Test</paragrahvPealkiri>
          <loige><loigeNr>1</loigeNr><tavatekst>Tekst.</tavatekst></loige>
        </paragrahv>
      </sisu>
    </akt>
    """
    generate_all_laws._used_prefixes.clear()
    doc = generate_all_laws.generate_law_jsonld(
        "Test seadus",
        "test_schema_act",
        ET.fromstring(xml),
        abbreviation="SCHACT",
        allocator=generate_all_laws.PrefixAllocator(registry={}),
    )
    act = next(
        n for n in doc["@graph"] if "estleg:Act" in (n.get("@type") or [])
    )
    assert "schema:Legislation" in act["@type"]
    assert doc["@context"]["schema"] == SCHEMA_URI

"""#446: NormType → LegalRuleML sameAs; EuroVoc also on eli:is_about."""
from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LRML = "http://docs.oasis-open.org/legalruleml/ns/v1.0/#"


def test_normtype_individuals_map_to_legalruleml() -> None:
    doc = json.loads(
        (REPO / "krr_outputs" / "controlled_vocabulary.jsonld").read_text(
            encoding="utf-8"
        )
    )
    by_id = {
        node["@id"]: node
        for node in doc.get("@graph", [])
        if isinstance(node, dict) and isinstance(node.get("@id"), str)
    }
    expected = {
        "estleg:NormType_Obligation": f"{LRML}Obligation",
        "estleg:NormType_Permission": f"{LRML}Permission",
        "estleg:NormType_Prohibition": f"{LRML}Prohibition",
        "estleg:NormType_Right": f"{LRML}Right",
        "estleg:NormType_Definition": f"{LRML}ConstitutiveStatement",
    }
    for nid, iri in expected.items():
        same = by_id[nid].get("owl:sameAs")
        exact = by_id[nid].get("skos:exactMatch")
        assert isinstance(same, dict), nid
        assert same.get("@id") == iri
        assert isinstance(exact, dict), nid
        assert exact.get("@id") == iri


def test_update_law_file_emits_eli_is_about(tmp_path: Path) -> None:
    from estleg.classify_eurovoc import update_law_file_eurovoc

    peep = tmp_path / "law_peep.json"
    peep.write_text(
        json.dumps(
            {
                "@context": {"estleg": "https://w3id.org/estleg/"},
                "@graph": [
                    {
                        "@id": "estleg:X_Map",
                        "@type": ["owl:Ontology"],
                        "rdfs:label": "X",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    # domain tuple: code, name_en, name_et, label, score, keywords
    domains = [("1234", "test", "test", "Test", 1, ["foo"])]
    assert update_law_file_eurovoc(peep, domains) is True
    node = json.loads(peep.read_text(encoding="utf-8"))["@graph"][0]
    ev = {"@id": "http://eurovoc.europa.eu/1234"}
    assert ev in node["dcterms:subject"]
    assert ev in node["eli:is_about"]
    assert "eli" in json.loads(peep.read_text(encoding="utf-8"))["@context"]


def test_published_peeps_carry_eli_is_about() -> None:
    """#446: EuroVoc subjects on committed law peeps also sit on eli:is_about."""
    krr = REPO / "krr_outputs"
    ev_nodes = 0
    about_nodes = 0
    for path in krr.glob("*_peep.json"):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for node in doc.get("@graph", []):
            if not isinstance(node, dict):
                continue
            subj = node.get("dcterms:subject")
            if isinstance(subj, dict):
                subj = [subj]
            if not isinstance(subj, list):
                continue
            if not any(
                isinstance(ref, dict)
                and str(ref.get("@id", "")).startswith("http://eurovoc.europa.eu/")
                for ref in subj
            ):
                continue
            ev_nodes += 1
            about = node.get("eli:is_about")
            if isinstance(about, dict):
                about = [about]
            if isinstance(about, list) and any(
                isinstance(ref, dict)
                and str(ref.get("@id", "")).startswith("http://eurovoc.europa.eu/")
                for ref in about
            ):
                about_nodes += 1
    assert ev_nodes >= 100, ev_nodes
    assert about_nodes == ev_nodes, (about_nodes, ev_nodes)

"""Tests for court-decision interpretation staleness derivation (#618).

Verifies the in-force-version selection, the outdated/earliest-superseding logic,
idempotent strip-then-recompute, and graceful no-signal cases. Written against a
synthetic version index so it is independent of the live corpus.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import derive_court_interpretation_staleness as D  # noqa: E402

# P1: v1 in force 2010-01-01..2015-12-31, then v2 from 2016-01-01 (open).
# P2: a single open version from 2008.
INDEX = {
    "estleg:P1": [
        ("2010-01-01", "2015-12-31", "estleg:P1_v1"),
        ("2016-01-01", None, "estleg:P1_v2"),
    ],
    "estleg:P2": [("2008-01-01", None, "estleg:P2_v1")],
}


def _decision(dd, *provisions):
    return {
        "@id": "estleg:RK_TEST",
        "@type": ["owl:NamedIndividual", "estleg:CourtDecision"],
        "estleg:decisionDate": {"@value": dd, "@type": "xsd:date"},
        "estleg:interpretsLaw": [{"@id": p} for p in provisions],
    }


def test_outdated_when_interpreted_provision_later_amended():
    node = _decision("2012-06-01", "estleg:P1")
    res = D.derive_node(node, INDEX)
    assert res.version_backed and res.outdated
    assert node["estleg:interpretationOutdated"] == {"@value": True, "@type": "xsd:boolean"}
    # The provision changed on 2016-01-01, after the 2012 ruling.
    assert node["estleg:earliestSupersedingDate"]["@value"] == "2016-01-01"
    # The redaction actually construed in 2012 was v1.
    assert node["estleg:interpretsVersion"] == [{"@id": "estleg:P1_v1"}]


def test_not_outdated_when_decision_after_last_amendment():
    node = _decision("2017-03-01", "estleg:P1")
    res = D.derive_node(node, INDEX)
    assert res.version_backed and not res.outdated
    assert node["estleg:interpretationOutdated"]["@value"] is False
    assert "estleg:earliestSupersedingDate" not in node
    assert node["estleg:interpretsVersion"] == [{"@id": "estleg:P1_v2"}]


def test_earliest_superseding_across_multiple_provisions():
    node = _decision("2012-06-01", "estleg:P1", "estleg:P2")
    D.derive_node(node, INDEX)
    # P1 supersedes in 2016, P2 never -> earliest is 2016.
    assert node["estleg:earliestSupersedingDate"]["@value"] == "2016-01-01"
    # Both in-force redactions linked, sorted for determinism.
    assert node["estleg:interpretsVersion"] == [
        {"@id": "estleg:P1_v1"},
        {"@id": "estleg:P2_v1"},
    ]


def test_provision_without_version_data_yields_no_signal():
    node = _decision("2012-06-01", "estleg:UNVERSIONED")
    res = D.derive_node(node, INDEX)
    assert not res.version_backed
    for prop in D.OUTPUT_PROPS:
        assert prop not in node


def test_missing_decision_date_yields_no_signal():
    node = {
        "@id": "estleg:RK_X",
        "@type": ["estleg:CourtDecision"],
        "estleg:interpretsLaw": [{"@id": "estleg:P1"}],
    }
    res = D.derive_node(node, INDEX)
    assert not res.version_backed
    for prop in D.OUTPUT_PROPS:
        assert prop not in node


def test_single_dict_interprets_law_is_accepted():
    node = _decision("2012-06-01")
    node["estleg:interpretsLaw"] = {"@id": "estleg:P1"}  # dict, not list
    res = D.derive_node(node, INDEX)
    assert res.version_backed and res.outdated


def test_idempotent_recompute_is_a_noop():
    node = _decision("2012-06-01", "estleg:P1")
    D.derive_node(node, INDEX)
    snapshot = {k: node.get(k) for k in D.OUTPUT_PROPS}
    res2 = D.derive_node(node, INDEX)
    assert not res2.changed
    assert {k: node.get(k) for k in D.OUTPUT_PROPS} == snapshot


def test_stale_props_are_cleaned_up_when_no_longer_derivable():
    # A node previously stamped but now interpreting only unversioned law.
    node = _decision("2012-06-01", "estleg:UNVERSIONED")
    node["estleg:interpretationOutdated"] = {"@value": True, "@type": "xsd:boolean"}
    node["estleg:interpretsVersion"] = [{"@id": "estleg:STALE"}]
    res = D.derive_node(node, INDEX)
    assert res.changed and not res.version_backed
    for prop in D.OUTPUT_PROPS:
        assert prop not in node


def test_build_version_index_from_sidecar(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "VERSION_DIR", tmp_path / "provision_versions")
    (tmp_path / "provision_versions").mkdir(parents=True)
    (tmp_path / "provision_versions" / "law.jsonld").write_text(
        json.dumps(
            {
                "@graph": [
                    {
                        "@id": "estleg:P_v2",
                        "@type": ["estleg:ProvisionVersion"],
                        "estleg:versionOf": {"@id": "estleg:P"},
                        "estleg:versionValidFrom": {"@value": "2016-01-01", "@type": "xsd:date"},
                    },
                    {
                        "@id": "estleg:P_v1",
                        "@type": ["estleg:ProvisionVersion"],
                        "estleg:versionOf": {"@id": "estleg:P"},
                        "estleg:versionValidFrom": {"@value": "2010-01-01", "@type": "xsd:date"},
                        "estleg:versionValidTo": {"@value": "2015-12-31", "@type": "xsd:date"},
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    idx = D.build_version_index()
    # Rows are returned sorted by (validFrom, ...).
    assert idx["estleg:P"] == [
        ("2010-01-01", "2015-12-31", "estleg:P_v1"),
        ("2016-01-01", None, "estleg:P_v2"),
    ]

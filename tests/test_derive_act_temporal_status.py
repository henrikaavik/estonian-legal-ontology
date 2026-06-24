"""Tests for the act-level temporalStatus derivation (#617, #128).

Verifies the version-chain aggregation, the #128 act-level placement (only Act
nodes are stamped), and that an already-determined status is never overwritten.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import derive_act_temporal_status as D  # noqa: E402

ASOF = "2026-06-01"


def _v(vid, frm, to=None, superseded_by=None):
    node = {"@id": vid, "estleg:versionValidFrom": {"@value": frm, "@type": "xsd:date"}}
    if to is not None:
        node["estleg:versionValidTo"] = {"@value": to, "@type": "xsd:date"}
    if superseded_by is not None:
        node["estleg:supersededByVersion"] = {"@id": superseded_by}
    return node


def _sidecar(tmp, law_name, version_nodes):
    (tmp / "provision_versions").mkdir(parents=True, exist_ok=True)
    (tmp / "provision_versions" / f"{law_name}.jsonld").write_text(
        json.dumps({"@graph": version_nodes}), encoding="utf-8"
    )


def test_in_force_when_any_provision_current(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "VERSION_DIR", tmp_path / "provision_versions")
    _sidecar(
        tmp_path,
        "law_x",
        [
            {**_v("a", "2020-01-01"), "estleg:versionOf": {"@id": "estleg:X_Par_1"}},
            {**_v("b", "2010-01-01", to="2024-01-01"), "estleg:versionOf": {"@id": "estleg:X_Par_2"}},
        ],
    )
    # Par_1 is open (inForce), Par_2 repealed -> the act is inForce.
    assert D.derive_act_status("law_x", ASOF) == "inForce"


def test_repealed_when_all_provisions_ended(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "VERSION_DIR", tmp_path / "provision_versions")
    _sidecar(
        tmp_path,
        "law_y",
        [
            {**_v("a", "2010-01-01", to="2023-01-01"), "estleg:versionOf": {"@id": "estleg:Y_Par_1"}},
            {**_v("b", "2010-01-01", to="2024-01-01"), "estleg:versionOf": {"@id": "estleg:Y_Par_2"}},
        ],
    )
    assert D.derive_act_status("law_y", ASOF) == "repealed"


def test_not_yet_effective_when_all_future(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "VERSION_DIR", tmp_path / "provision_versions")
    _sidecar(
        tmp_path,
        "law_z",
        [{**_v("a", "2030-01-01"), "estleg:versionOf": {"@id": "estleg:Z_Par_1"}}],
    )
    assert D.derive_act_status("law_z", ASOF) == "notYetEffective"


def test_none_when_no_sidecar(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "VERSION_DIR", tmp_path / "provision_versions")
    (tmp_path / "provision_versions").mkdir()
    assert D.derive_act_status("absent", ASOF) is None


def test_only_fills_unknown_never_overwrites(tmp_path):
    # An Act already marked inForce must be left alone; an unknown one gets filled;
    # a non-Act node is never touched (#128).
    peep = tmp_path / "p_peep.json"
    peep.write_text(
        json.dumps(
            {
                "@graph": [
                    {"@id": "estleg:A_known", "@type": ["estleg:Act"], "estleg:temporalStatus": "repealed"},
                    {"@id": "estleg:A_unknown", "@type": ["estleg:Act"], "estleg:temporalStatus": "unknown"},
                    {"@id": "estleg:A_missing", "@type": ["estleg:Act"]},
                    {"@id": "estleg:P_1", "@type": ["estleg:LegalProvision_x"]},  # not an Act
                ]
            }
        ),
        encoding="utf-8",
    )
    changed, doc = D.apply_to_file(peep, "inForce")
    statuses = {n["@id"]: n.get("estleg:temporalStatus") for n in doc["@graph"]}
    assert changed == 2  # unknown + missing
    assert statuses["estleg:A_known"] == "repealed"  # preserved
    assert statuses["estleg:A_unknown"] == "inForce"
    assert statuses["estleg:A_missing"] == "inForce"
    assert statuses["estleg:P_1"] is None  # #128: provisions never get temporalStatus


def test_apply_is_idempotent(tmp_path):
    peep = tmp_path / "p_peep.json"
    peep.write_text(
        json.dumps({"@graph": [{"@id": "estleg:A", "@type": ["estleg:Act"], "estleg:temporalStatus": "unknown"}]}),
        encoding="utf-8",
    )
    first, doc = D.apply_to_file(peep, "inForce")
    peep.write_text(json.dumps(doc), encoding="utf-8")
    second, _ = D.apply_to_file(peep, "inForce")
    assert first == 1
    assert second == 0

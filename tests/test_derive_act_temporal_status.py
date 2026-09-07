"""Tests for the act-level temporalStatus derivation (#617, #128, #682).

Verifies the version-chain aggregation, the #128 act-level placement (only Act
nodes are stamped), that an already-determined status is never overwritten in
fill-only mode, and the #682 honesty rules: no residual ``inForce``, deprecated
act roots stay ``unknown``, and ``--recompute`` re-derives everything except
date-determined statuses.
"""

import json

from estleg import derive_act_temporal_status as D

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


def _peep(tmp, fname, nodes):
    path = tmp / fname
    path.write_text(json.dumps({"@graph": nodes}), encoding="utf-8")
    return path


def _statuses(path):
    doc = json.loads(path.read_text(encoding="utf-8"))
    return {n["@id"]: n.get("estleg:temporalStatus") for n in doc["@graph"]}


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
    peep = _peep(
        tmp_path,
        "p_peep.json",
        [
            {"@id": "estleg:A_known", "@type": ["estleg:Act"], "estleg:temporalStatus": "repealed"},
            {"@id": "estleg:A_unknown", "@type": ["estleg:Act"], "estleg:temporalStatus": "unknown"},
            {"@id": "estleg:A_missing", "@type": ["estleg:Act"]},
            {"@id": "estleg:P_1", "@type": ["estleg:LegalProvision_x"]},  # not an Act
        ],
    )
    changed, doc = D.apply_to_file(peep, "inForce")
    statuses = {n["@id"]: n.get("estleg:temporalStatus") for n in doc["@graph"]}
    assert changed == 2  # unknown + missing
    assert statuses["estleg:A_known"] == "repealed"  # preserved
    assert statuses["estleg:A_unknown"] == "inForce"
    assert statuses["estleg:A_missing"] == "inForce"
    assert statuses["estleg:P_1"] is None  # #128: provisions never get temporalStatus


def test_residual_status_never_asserts_in_force() -> None:
    """#682: no sidecar evidence means ``unknown``, never a residual ``inForce``."""
    law = {"name": "cotonou", "stubKind": "treaty"}
    node = {"@id": "estleg:X_Map", "@type": ["estleg:Act", "estleg:Law"]}
    assert D.residual_status(law=law, node=node) is None
    deprecated = {**node, "owl:deprecated": True}
    assert D.residual_status(law=law, node=deprecated) is None
    assert D.residual_status(law=None, node=node) is None


def test_act_without_sidecar_settles_at_unknown(tmp_path):
    """A live act with no sidecar status is filled with ``unknown``, not inForce."""
    peep = _peep(
        tmp_path,
        "p_peep.json",
        [{"@id": "estleg:A_Map", "@type": ["estleg:Act", "estleg:Law"]}],
    )
    changed, doc = D.apply_to_file(peep, None, law={"name": "cotonou"})
    assert changed == 1
    assert doc["@graph"][0]["estleg:temporalStatus"] == "unknown"


def test_deprecated_act_stays_unknown(tmp_path) -> None:
    """#682: a retired IRI never copies its successor's status."""
    _peep(
        tmp_path,
        "canon_peep.json",
        [{"@id": "estleg:AS_Map", "@type": ["estleg:Act"], "estleg:temporalStatus": "inForce"}],
    )
    legacy = _peep(
        tmp_path,
        "legacy_peep.json",
        [
            {
                "@id": "estleg:ALKS_Map",
                "@type": ["estleg:Act"],
                "owl:deprecated": True,
                "dcterms:isReplacedBy": {"@id": "estleg:AS_Map"},
                "estleg:temporalStatus": "unknown",
            }
        ],
    )
    result = D.process_deprecated_status(tmp_path, dry_run=False)
    assert result["acts"] == 1
    assert result["acts_changed"] == 0  # already unknown — nothing to write
    assert _statuses(legacy)["estleg:ALKS_Map"] == "unknown"
    # The live successor keeps its own status; the deprecated pass ignores it.
    assert _statuses(tmp_path / "canon_peep.json")["estleg:AS_Map"] == "inForce"


def test_deprecated_act_missing_status_is_filled_unknown(tmp_path) -> None:
    legacy = _peep(
        tmp_path,
        "legacy_peep.json",
        [{"@id": "estleg:ALKS_Map", "@type": ["estleg:Act"], "owl:deprecated": True}],
    )
    result = D.process_deprecated_status(tmp_path, dry_run=False)
    assert (result["files_changed"], result["acts_changed"]) == (1, 1)
    assert _statuses(legacy)["estleg:ALKS_Map"] == "unknown"


def test_deprecated_in_force_needs_recompute(tmp_path) -> None:
    """A pre-#682 deprecated act stamped ``inForce`` is corrected by --recompute."""
    legacy = _peep(
        tmp_path,
        "legacy_peep.json",
        [
            {
                "@id": "estleg:PS_Map",
                "@type": ["estleg:Act"],
                "owl:deprecated": True,
                "dcterms:isReplacedBy": {"@id": "estleg:eesti_vabariigi_pohiseadus_Map"},
                "estleg:temporalStatus": "inForce",
            }
        ],
    )
    fill_only = D.process_deprecated_status(tmp_path, dry_run=False)
    assert fill_only["acts_changed"] == 0  # fill-only never overwrites
    result = D.process_deprecated_status(tmp_path, dry_run=False, recompute=True)
    assert result["acts_changed"] == 1
    assert _statuses(legacy)["estleg:PS_Map"] == "unknown"
    # ...and never ``repealed``: a retired IRI is succession, not repeal.
    assert result["statuses"]["repealed"] == 0


def test_deprecated_dry_run_does_not_write(tmp_path) -> None:
    legacy = _peep(
        tmp_path,
        "legacy_peep.json",
        [
            {
                "@id": "estleg:PS_Map",
                "@type": ["estleg:Act"],
                "owl:deprecated": True,
                "estleg:temporalStatus": "inForce",
            }
        ],
    )
    result = D.process_deprecated_status(tmp_path, dry_run=True, recompute=True)
    assert result["acts_changed"] == 1
    assert _statuses(legacy)["estleg:PS_Map"] == "inForce"  # untouched on disk


def _index_law(name, fname):
    return {"name": name, "files": [fname]}


def test_recompute_clears_residual_in_force(tmp_path, monkeypatch):
    """#682: a residual inForce (no sidecar, no act dates) becomes ``unknown``."""
    monkeypatch.setattr(D, "KRR_DIR", tmp_path)
    monkeypatch.setattr(D, "VERSION_DIR", tmp_path / "provision_versions")
    (tmp_path / "provision_versions").mkdir()
    peep = _peep(
        tmp_path,
        "residual_peep.json",
        [{"@id": "estleg:R_Map", "@type": ["estleg:Act", "estleg:Law"], "estleg:temporalStatus": "inForce"}],
    )
    law = _index_law("residual", "residual_peep.json")

    fill_only = D.process_law(law, ASOF, dry_run=False)
    assert fill_only["acts_changed"] == 0  # fill-only leaves the stale value

    summary = D.process_law(law, ASOF, dry_run=False, recompute=True)
    assert summary["acts_changed"] == 1
    assert summary["source"] == "no-evidence"
    assert summary["statuses"] == {"unknown": 1}
    assert _statuses(peep)["estleg:R_Map"] == "unknown"


def test_recompute_keeps_sidecar_backed_in_force(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "KRR_DIR", tmp_path)
    monkeypatch.setattr(D, "VERSION_DIR", tmp_path / "provision_versions")
    _sidecar(
        tmp_path,
        "backed",
        [{**_v("a", "2020-01-01"), "estleg:versionOf": {"@id": "estleg:B_Par_1"}}],
    )
    peep = _peep(
        tmp_path,
        "backed_peep.json",
        [{"@id": "estleg:B_Map", "@type": ["estleg:Act"], "estleg:temporalStatus": "inForce"}],
    )
    summary = D.process_law(_index_law("backed", "backed_peep.json"), ASOF, dry_run=False, recompute=True)
    assert summary["acts_changed"] == 0
    assert summary["source"] == "sidecar"
    assert summary["statuses"] == {"inForce": 1}
    assert _statuses(peep)["estleg:B_Map"] == "inForce"


def test_recompute_preserves_date_determined_status(tmp_path, monkeypatch):
    """An act carrying its own entryIntoForce keeps the status the dates gave it."""
    monkeypatch.setattr(D, "KRR_DIR", tmp_path)
    monkeypatch.setattr(D, "VERSION_DIR", tmp_path / "provision_versions")
    (tmp_path / "provision_versions").mkdir()
    peep = _peep(
        tmp_path,
        "dated_peep.json",
        [
            {
                "@id": "estleg:D_Map",
                "@type": ["estleg:Act"],
                "estleg:entryIntoForce": {"@value": "2002-07-01", "@type": "xsd:date"},
                "estleg:temporalStatus": "inForce",
            },
            {
                "@id": "estleg:D_Old",
                "@type": ["estleg:Act"],
                "estleg:repealDate": {"@value": "2015-01-01", "@type": "xsd:date"},
                "estleg:temporalStatus": "repealed",
            },
        ],
    )
    summary = D.process_law(_index_law("dated", "dated_peep.json"), ASOF, dry_run=False, recompute=True)
    assert summary["acts_changed"] == 0
    statuses = _statuses(peep)
    assert statuses["estleg:D_Map"] == "inForce"
    assert statuses["estleg:D_Old"] == "repealed"


def test_recompute_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "KRR_DIR", tmp_path)
    monkeypatch.setattr(D, "VERSION_DIR", tmp_path / "provision_versions")
    (tmp_path / "provision_versions").mkdir()
    _peep(
        tmp_path,
        "residual_peep.json",
        [{"@id": "estleg:R_Map", "@type": ["estleg:Act"], "estleg:temporalStatus": "inForce"}],
    )
    law = _index_law("residual", "residual_peep.json")
    first = D.process_law(law, ASOF, dry_run=False, recompute=True)
    second = D.process_law(law, ASOF, dry_run=False, recompute=True)
    assert (first["acts_changed"], second["acts_changed"]) == (1, 0)


def test_apply_is_idempotent(tmp_path):
    peep = _peep(
        tmp_path,
        "p_peep.json",
        [{"@id": "estleg:A", "@type": ["estleg:Act"], "estleg:temporalStatus": "unknown"}],
    )
    first, doc = D.apply_to_file(peep, "inForce")
    peep.write_text(json.dumps(doc), encoding="utf-8")
    second, _ = D.apply_to_file(peep, "inForce")
    assert first == 1
    assert second == 0


def test_format_distribution_lists_all_statuses():
    from collections import Counter

    rendered = D.format_distribution(Counter({"inForce": 3}))
    assert "'inForce': 3" in rendered
    assert "'unknown': 0" in rendered
    assert "'repealed': 0" in rendered
    assert "'notYetEffective': 0" in rendered


def test_recompute_clears_deprecated_status_even_with_act_dates(tmp_path):
    peep = _peep(tmp_path, 'deprecated_dated_peep.json', [{
        '@id': 'estleg:Old_Map', '@type': ['estleg:Act'],
        'owl:deprecated': True, 'estleg:temporalStatus': 'inForce',
        'estleg:entryIntoForce': {'@value': '2002-07-01', '@type': 'xsd:date'},
    }])
    result = D.process_deprecated_status(tmp_path, dry_run=False, recompute=True)
    assert result['acts_changed'] == 1
    assert _statuses(peep)['estleg:Old_Map'] == 'unknown'

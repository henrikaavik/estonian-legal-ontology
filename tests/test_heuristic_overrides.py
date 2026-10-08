"""#700 — durable human overrides for the heuristic classifiers.

Everything runs on tmp_path fixtures; no classifier touches the real corpus.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from estleg import heuristic_overrides as ho
from estleg.heuristic_overrides import (
    HUMAN_ASSERTION_CONFIDENCE,
    OverrideError,
    OverrideStore,
    check_overrides,
    load_overrides,
    parse_overrides_text,
)

CONF_1 = {"@value": HUMAN_ASSERTION_CONFIDENCE, "@type": "xsd:decimal"}
REVIEWER = "Justiitsministeerium"


def _rec(**kw) -> dict:
    base = {
        "node": "estleg:TST_Par_1",
        "predicate": "estleg:normativeType",
        "value": {"@id": "estleg:NormType_Prohibition"},
        "reviewer": REVIEWER,
        "date": "2026-10-01",
        "basis": "RT I, 01.01.2026, 1; § 1",
        "action": "set",
    }
    base.update(kw)
    return {k: v for k, v in base.items() if v is not ...}


def _write_store(path: Path, *records: dict) -> Path:
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
        encoding="utf-8",
    )
    return path


def _store(*records: dict) -> OverrideStore:
    return parse_overrides_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
    )


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _node(doc: dict, node_id: str) -> dict:
    return next(n for n in doc["@graph"] if n.get("@id") == node_id)


# ---------------------------------------------------------------------------
# Loader validation
# ---------------------------------------------------------------------------


def test_missing_store_is_empty(tmp_path: Path) -> None:
    store = load_overrides(tmp_path / "absent.jsonl")
    assert len(store) == 0 and not store


def test_committed_store_validates() -> None:
    store = load_overrides()
    assert store.source == ho.OVERRIDES_PATH
    assert ho.OVERRIDES_PATH.exists()


def test_good_lines_for_every_predicate(tmp_path: Path) -> None:
    path = _write_store(
        tmp_path / "o.jsonl",
        _rec(),
        _rec(predicate="estleg:dutyHolder", value=[{"@id": "estleg:TargetGroup_Business"}]),
        _rec(predicate="estleg:targetGroup", value=[{"@id": "estleg:TargetGroup_Citizen"}]),
        _rec(
            predicate="estleg:competentAuthority",
            value=[{"@id": "estleg:Institution_Politsei_ja_Piirivalveamet"}],
        ),
        _rec(predicate="estleg:competenceType", value="supervision"),
        _rec(node="estleg:TST_Map_2026", predicate="dcterms:subject",
             value=[{"@id": "http://eurovoc.europa.eu/1235"}]),
        _rec(node="estleg:TST_Par_2", action="remove", value=...),
        _rec(node="estleg:TST_Par_3", action="remove", value=None),
    )
    # Blank lines are tolerated.
    path.write_text(path.read_text(encoding="utf-8") + "\n   \n", encoding="utf-8")
    store = load_overrides(path)
    assert len(store) == 8
    assert store.owns("estleg:TST_Par_1", "estleg:competenceType")
    assert store.owned_predicates("estleg:TST_Par_2") == {"estleg:normativeType"}
    assert [r.line for r in store.records()] == list(range(1, 9))
    assert [r.predicate for r in store.records(ho.DEONTIC_PREDICATES)] == [
        "estleg:normativeType",
        "estleg:dutyHolder",
        "estleg:normativeType",
        "estleg:normativeType",
    ]


@pytest.mark.parametrize(
    ("record", "message"),
    [
        (_rec(predicate="estleg:semanticallySimilarTo"), "unknown predicate"),
        (_rec(node="TST_Par_1"), "malformed node IRI"),
        (_rec(node="estleg:TST Par 1"), "malformed node IRI"),
        (_rec(node="https://w3id.org/estleg/TST_Par_1"), "malformed node IRI"),
        (_rec(action="replace"), "action must be"),
        (_rec(value=...), "requires a value"),
        (_rec(action="remove"), "must not carry a value"),
        (_rec(value={"@id": "estleg:NormType_Maybe"}), "NormType"),
        (_rec(value="estleg:NormType_Obligation"), "NormType"),
        (_rec(predicate="estleg:targetGroup", value=[{"@id": "estleg:TargetGroup_Aliens"}]),
         "not a known TargetGroup"),
        (_rec(predicate="estleg:targetGroup", value={"@id": "estleg:TargetGroup_Citizen"}),
         "non-empty list"),
        (_rec(predicate="estleg:targetGroup", value=[]), "non-empty list"),
        (_rec(predicate="estleg:targetGroup",
              value=[{"@id": "estleg:TargetGroup_NGO"}, {"@id": "estleg:TargetGroup_NGO"}]),
         "duplicate"),
        (_rec(predicate="estleg:competenceType", value="oversight"), "must be one of"),
        (_rec(predicate="estleg:competentAuthority", value=[{"@id": "http://x.org/A"}]),
         "not an estleg: IRI"),
        (_rec(predicate="dcterms:subject", value=[{"@id": "http://example.org/1"}]),
         "not a EuroVoc"),
        (_rec(reviewer=""), "reviewer"),
        (_rec(reviewer="jaan.tamm@just.ee"), "not an address"),
        (_rec(date="2026-13-01"), "not a calendar date"),
        (_rec(date="01.10.2026"), "YYYY-MM-DD"),
        (_rec(basis="  "), "basis"),
        (_rec(comment="x"), "unknown key"),
        (_rec(basis=...), "missing key"),
    ],
)
def test_bad_line_is_hard_error_with_line_number(
    tmp_path: Path, record: dict, message: str
) -> None:
    path = _write_store(tmp_path / "o.jsonl", _rec(node="estleg:OK_Par_1"), record)
    with pytest.raises(OverrideError) as err:
        load_overrides(path)
    assert "line 2" in str(err.value)
    assert message in str(err.value)


def test_invalid_json_line(tmp_path: Path) -> None:
    path = tmp_path / "o.jsonl"
    path.write_text(json.dumps(_rec()) + "\n{not json\n", encoding="utf-8")
    with pytest.raises(OverrideError, match="line 2: invalid JSON"):
        load_overrides(path)


def test_duplicate_node_predicate_is_error() -> None:
    with pytest.raises(OverrideError, match=r"line 3: duplicate .* first defined on line 1"):
        _store(
            _rec(),
            _rec(predicate="estleg:dutyHolder", value=[{"@id": "estleg:TargetGroup_Citizen"}]),
            _rec(action="remove", value=...),
        )


# ---------------------------------------------------------------------------
# Node helpers
# ---------------------------------------------------------------------------


def test_confidence_min_semantics_with_owned_layers() -> None:
    store = _store(_rec())
    node = {
        "@id": "estleg:TST_Par_1",
        "estleg:normativeType": {"@id": "estleg:NormType_Prohibition"},
        "estleg:targetGroup": [{"@id": "estleg:TargetGroup_Citizen"}],
    }
    # targetGroup is still heuristic, so the node is only as sure as 0.65.
    assert ho.confidence_for_node(node, store) == "0.65"
    del node["estleg:targetGroup"]
    assert ho.confidence_for_node(node, store) == HUMAN_ASSERTION_CONFIDENCE
    # A removal-only node (no layer left) is fully human-reviewed.
    removal = _store(_rec(action="remove", value=...))
    assert ho.confidence_for_node({"@id": "estleg:TST_Par_1"}, removal) == "1.0"
    # No override → exactly the shared heuristic helper.
    plain = {"@id": "estleg:X", "estleg:normativeType": {"@id": "estleg:NormType_Right"}}
    assert ho.confidence_for_node(plain, store) == ho.heuristic_confidence_for_node(plain)
    assert ho.confidence_for_node({"@id": "estleg:X"}, store) is None


def test_stale_human_stamp_and_attribution_are_retracted() -> None:
    empty = OverrideStore()
    node = {
        "@id": "estleg:TST_Par_1",
        "estleg:normativeType": {"@id": "estleg:NormType_Prohibition"},
        "prov:wasAttributedTo": REVIEWER,
        "estleg:assertionConfidence": dict(CONF_1),
    }
    assert ho.apply_node_overrides(node, empty, ho.DEONTIC_PREDICATES) is True
    assert "prov:wasAttributedTo" not in node
    assert ho.restamp_confidence(node, empty) is True
    assert node["estleg:assertionConfidence"]["@value"] == "0.70"


def test_multiple_reviewers_are_a_sorted_list() -> None:
    store = _store(
        _rec(reviewer="Sotsiaalministeerium"),
        _rec(predicate="estleg:targetGroup", reviewer="Justiitsministeerium",
             value=[{"@id": "estleg:TargetGroup_Citizen"}]),
    )
    node = {"@id": "estleg:TST_Par_1"}
    ho.apply_node_overrides(node, store, ho.DEONTIC_PREDICATES)
    assert node["prov:wasAttributedTo"] == ["Justiitsministeerium", "Sotsiaalministeerium"]


def test_ensure_prov_context() -> None:
    doc = {"@context": {"estleg": "https://w3id.org/estleg/"}}
    assert ho.ensure_prov_context(doc) is True
    assert doc["@context"]["prov"] == "http://www.w3.org/ns/prov#"
    assert ho.ensure_prov_context(doc) is False
    assert ho.ensure_prov_context({"@context": "https://x"}) is False


# ---------------------------------------------------------------------------
# Deontic classifier
# ---------------------------------------------------------------------------

DEONTIC_CTX = {"estleg": "https://w3id.org/estleg/", "rdfs": "http://www.w3.org/2000/01/rdf-schema#"}


def _deontic_peep(tmp_path: Path) -> Path:
    krr = tmp_path / "krr_outputs"
    krr.mkdir(exist_ok=True)
    peep = krr / "tst_peep.json"
    peep.write_text(
        json.dumps(
            {
                "@context": dict(DEONTIC_CTX),
                "@graph": [
                    {"@id": "estleg:TST_Map_2026", "@type": ["estleg:Act"]},
                    {
                        "@id": "estleg:TST_Par_1",
                        "@type": ["estleg:LegalProvision"],
                        "estleg:paragrahv": "§ 1",
                        "estleg:summary": "Tööandja peab esitama aruande.",
                    },
                    {
                        "@id": "estleg:TST_Par_2",
                        "@type": ["estleg:LegalProvision"],
                        "estleg:paragrahv": "§ 2",
                        "estleg:summary": "Tööandja peab tasuma maksu.",
                    },
                    {
                        "@id": "estleg:TST_Par_3",
                        "@type": ["estleg:LegalProvision"],
                        "estleg:paragrahv": "§ 3",
                        "estleg:summary": "Tööandja peab tasuma tasu.",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return peep


def _run_deontic(monkeypatch, tmp_path: Path, peep: Path, store: Path, *extra: str) -> int:
    from estleg import classify_deontic as cd

    monkeypatch.setattr(cd, "KRR_DIR", tmp_path / "krr_outputs")
    monkeypatch.setattr(cd, "iter_peep_files", lambda *a, **k: [peep])
    monkeypatch.setattr(cd, "write_coverage_report", lambda *a, **k: None)
    return cd.main(["--overrides", str(store), *extra])


def test_deontic_honours_set_and_remove_and_survives_rerun(
    tmp_path: Path, monkeypatch
) -> None:
    peep = _deontic_peep(tmp_path)
    store = _write_store(
        tmp_path / "o.jsonl",
        _rec(node="estleg:TST_Par_1"),  # Obligation → Prohibition
        _rec(node="estleg:TST_Par_1", predicate="estleg:dutyHolder", action="remove", value=...),
        _rec(node="estleg:TST_Par_2", action="remove", value=...),
        # Applies even to a node with no classifiable text.
        _rec(node="estleg:TST_Map_2026", value={"@id": "estleg:NormType_Definition"}),
    )
    assert _run_deontic(monkeypatch, tmp_path, peep, store) == 0
    doc = _read(peep)
    first_bytes = peep.read_bytes()

    p1 = _node(doc, "estleg:TST_Par_1")
    assert p1["estleg:normativeType"] == {"@id": "estleg:NormType_Prohibition"}
    assert "estleg:dutyHolder" not in p1  # heuristic would give Business
    assert p1["prov:wasAttributedTo"] == REVIEWER
    assert p1["estleg:assertionConfidence"] == CONF_1

    p2 = _node(doc, "estleg:TST_Par_2")
    assert "estleg:normativeType" not in p2
    assert p2["prov:wasAttributedTo"] == REVIEWER
    assert p2["estleg:assertionConfidence"] == CONF_1

    act = _node(doc, "estleg:TST_Map_2026")
    assert act["estleg:normativeType"] == {"@id": "estleg:NormType_Definition"}
    assert act["estleg:assertionConfidence"] == CONF_1

    # The un-overridden provision keeps the heuristic and its 0.70.
    p3 = _node(doc, "estleg:TST_Par_3")
    assert p3["estleg:normativeType"] == {"@id": "estleg:NormType_Obligation"}
    assert p3["estleg:dutyHolder"] == [{"@id": "estleg:TargetGroup_Business"}]
    assert p3["estleg:assertionConfidence"]["@value"] == "0.70"
    assert "prov:wasAttributedTo" not in p3

    assert doc["@context"]["prov"] == "http://www.w3.org/ns/prov#"

    report = _read(tmp_path / "krr_outputs" / "reports" / "deontic_classification_report.json")
    assert report["summary"]["human_overrides_applied"] == 4

    # A regeneration (clear + rewrite) leaves the reviewed values intact.
    # (Compared parsed: the pre-existing clear pass re-appends the heuristic
    # keys of un-overridden nodes, so key order shifts once on run 2.)
    assert _run_deontic(monkeypatch, tmp_path, peep, store) == 0
    assert _read(peep) == json.loads(first_bytes)
    second = peep.read_bytes()
    assert _run_deontic(monkeypatch, tmp_path, peep, store) == 0
    assert peep.read_bytes() == second


def test_deontic_dropping_an_override_restores_the_heuristic(
    tmp_path: Path, monkeypatch
) -> None:
    peep = _deontic_peep(tmp_path)
    store = _write_store(tmp_path / "o.jsonl", _rec(node="estleg:TST_Par_1"))
    _run_deontic(monkeypatch, tmp_path, peep, store)
    assert _node(_read(peep), "estleg:TST_Par_1")["estleg:normativeType"] == {
        "@id": "estleg:NormType_Prohibition"
    }
    store.write_text("", encoding="utf-8")
    _run_deontic(monkeypatch, tmp_path, peep, store)
    p1 = _node(_read(peep), "estleg:TST_Par_1")
    assert p1["estleg:normativeType"] == {"@id": "estleg:NormType_Obligation"}
    assert "prov:wasAttributedTo" not in p1
    assert p1["estleg:assertionConfidence"]["@value"] == "0.70"


def test_deontic_bad_store_aborts_before_touching_peeps(
    tmp_path: Path, monkeypatch
) -> None:
    peep = _deontic_peep(tmp_path)
    before = peep.read_bytes()
    store = _write_store(tmp_path / "o.jsonl", _rec(predicate="estleg:nope"))
    assert _run_deontic(monkeypatch, tmp_path, peep, store) == 1
    assert peep.read_bytes() == before


def test_deontic_check_overrides_reports_stale_and_writes_nothing(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    peep = _deontic_peep(tmp_path)
    before = peep.read_bytes()
    store = _write_store(
        tmp_path / "o.jsonl",
        _rec(node="estleg:TST_Par_1"),
        _rec(node="estleg:GONE_Par_9"),
        # Another classifier's predicate is not counted for deontic.
        _rec(node="estleg:TST_Par_2", predicate="estleg:targetGroup",
             value=[{"@id": "estleg:TargetGroup_Citizen"}]),
    )
    assert _run_deontic(monkeypatch, tmp_path, peep, store, "--check-overrides") == 0
    out = capsys.readouterr().out
    assert "Overrides for this classifier: 2" in out
    assert "Would apply:                   1" in out
    assert "Stale (node not found):        1" in out
    assert "line 2: estleg:GONE_Par_9 estleg:normativeType" in out
    assert peep.read_bytes() == before
    assert not (tmp_path / "krr_outputs" / "reports").exists()


# ---------------------------------------------------------------------------
# Target-group classifier
# ---------------------------------------------------------------------------


def _tg_peep(tmp_path: Path) -> Path:
    peep = tmp_path / "tg_peep.json"
    peep.write_text(
        json.dumps(
            {
                "@context": {"estleg": "https://w3id.org/estleg/"},
                "@graph": [
                    {
                        "@id": "estleg:TG_Par_1",
                        "estleg:paragrahv": "§ 1",
                        "estleg:summary": "Füüsiline isik peab esitama taotluse.",
                    },
                    {
                        "@id": "estleg:TG_Par_2",
                        "estleg:paragrahv": "§ 2",
                        "estleg:summary": "Füüsiline isik peab esitama avalduse.",
                    },
                    {
                        "@id": "estleg:TG_Par_3",
                        "estleg:paragrahv": "§ 3",
                        "estleg:summary": "Füüsiline isik peab tasuma lõivu.",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return peep


def test_target_group_honours_set_and_remove_and_survives_rerun(tmp_path: Path) -> None:
    from estleg.classify_target_group import classify_files, stamp_confidence_files

    peep = _tg_peep(tmp_path)
    store = _store(
        _rec(node="estleg:TG_Par_1", predicate="estleg:targetGroup",
             value=[{"@id": "estleg:TargetGroup_Business"}, {"@id": "estleg:TargetGroup_NGO"}]),
        _rec(node="estleg:TG_Par_2", predicate="estleg:targetGroup", action="remove", value=...),
    )
    report = classify_files([peep], report_path=tmp_path / "r.json", overrides=store)
    assert report["summary"]["human_overrides_applied"] == 2
    doc = _read(peep)
    first = peep.read_bytes()

    p1 = _node(doc, "estleg:TG_Par_1")
    assert p1["estleg:targetGroup"] == [
        {"@id": "estleg:TargetGroup_Business"},
        {"@id": "estleg:TargetGroup_NGO"},
    ]
    assert p1["prov:wasAttributedTo"] == REVIEWER
    assert p1["estleg:assertionConfidence"] == CONF_1
    p2 = _node(doc, "estleg:TG_Par_2")
    assert "estleg:targetGroup" not in p2
    assert p2["estleg:assertionConfidence"] == CONF_1
    p3 = _node(doc, "estleg:TG_Par_3")
    assert p3["estleg:targetGroup"] == [{"@id": "estleg:TargetGroup_Citizen"}]
    assert p3["estleg:assertionConfidence"]["@value"] == "0.65"
    assert doc["@context"]["prov"] == "http://www.w3.org/ns/prov#"

    classify_files([peep], report_path=tmp_path / "r.json", overrides=store)
    assert peep.read_bytes() == first
    # The --stamp-confidence pass is override-aware too (no 1.0 → 0.65 flip).
    stats = stamp_confidence_files([peep], overrides=store)
    assert stats["nodes_changed"] == 0
    assert peep.read_bytes() == first


def test_target_group_main_check_overrides(tmp_path: Path, monkeypatch, capsys) -> None:
    from estleg import classify_target_group as tg

    peep = _tg_peep(tmp_path)
    before = peep.read_bytes()
    store = _write_store(
        tmp_path / "o.jsonl",
        _rec(node="estleg:TG_Gone", predicate="estleg:targetGroup",
             value=[{"@id": "estleg:TargetGroup_Citizen"}]),
    )
    monkeypatch.setattr(tg, "iter_peep_files", lambda *a, **k: [peep])
    assert tg.main(["--overrides", str(store), "--check-overrides"]) == 0
    out = capsys.readouterr().out
    assert "Stale (node not found):        1" in out
    assert peep.read_bytes() == before


# ---------------------------------------------------------------------------
# Institutional competence
# ---------------------------------------------------------------------------


def _competence_peep(tmp_path: Path) -> Path:
    peep = tmp_path / "comp_peep.json"
    peep.write_text(
        json.dumps(
            {
                "@context": {"estleg": "https://w3id.org/estleg/"},
                "@graph": [
                    {"@id": "estleg:CMP_Map_2026", "@type": ["owl:Ontology", "estleg:Act"]},
                    {
                        "@id": "estleg:CMP_Par_1",
                        "@type": ["estleg:LegalProvision"],
                        "estleg:paragrahv": "§ 1",
                        "estleg:summary": "Keskkonnaamet teostab järelevalvet.",
                    },
                    {
                        "@id": "estleg:CMP_Par_2",
                        "@type": ["estleg:LegalProvision"],
                        "estleg:paragrahv": "§ 2",
                        "estleg:summary": "Keskkonnaamet teostab järelevalvet.",
                    },
                    {
                        "@id": "estleg:CMP_Par_3",
                        "@type": ["estleg:LegalProvision"],
                        "estleg:paragrahv": "§ 3",
                        "estleg:summary": "Keskkonnaamet teostab järelevalvet.",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return peep


def _run_competence(peep: Path, store: OverrideStore):
    from estleg import extract_institutional_competence as mod

    state = mod._PipelineState()
    state.overrides = store
    mod.process_law_file(
        filepath=peep, state=state, issuer_registry={}, canonical_suffixes=set()
    )
    mod.record_override_links(state, set())
    return state


def test_competence_honours_set_and_remove_and_survives_rerun(
    tmp_path: Path, monkeypatch
) -> None:
    from estleg import extract_institutional_competence as mod

    peep = _competence_peep(tmp_path)
    # Sanity: the heuristic detects an authority on the fixture text.
    baseline = _run_competence(peep, OverrideStore())
    heuristic = _node(_read(peep), "estleg:CMP_Par_3")
    assert heuristic.get("estleg:competentAuthority"), "fixture must classify"
    heuristic_iri = heuristic["estleg:competentAuthority"][0]["@id"]
    assert heuristic_iri in baseline.inst_data

    store = _store(
        _rec(node="estleg:CMP_Par_1", predicate="estleg:competentAuthority",
             value=[{"@id": "estleg:Institution_Justiitsministeerium"}]),
        _rec(node="estleg:CMP_Par_1", predicate="estleg:competenceType", value="regulation"),
        _rec(node="estleg:CMP_Par_2", predicate="estleg:competentAuthority",
             action="remove", value=...),
        _rec(node="estleg:CMP_Par_2", predicate="estleg:competenceType",
             action="remove", value=...),
    )
    monkeypatch.setattr(
        mod, "named_institution_by_suffix",
        lambda: {"Justiitsministeerium": ("Justiitsministeerium", "ministry")},
    )
    state = _run_competence(peep, store)
    doc = _read(peep)
    first = peep.read_bytes()

    p1 = _node(doc, "estleg:CMP_Par_1")
    assert p1["estleg:competentAuthority"] == [{"@id": "estleg:Institution_Justiitsministeerium"}]
    assert p1["estleg:competenceType"] == "regulation"
    assert p1["prov:wasAttributedTo"] == REVIEWER
    assert p1["estleg:assertionConfidence"] == CONF_1
    p2 = _node(doc, "estleg:CMP_Par_2")
    assert "estleg:competentAuthority" not in p2
    assert "estleg:competenceType" not in p2
    assert p2["estleg:assertionConfidence"] == CONF_1
    p3 = _node(doc, "estleg:CMP_Par_3")
    assert p3["estleg:competentAuthority"] == [{"@id": heuristic_iri}]
    assert "prov:wasAttributedTo" not in p3
    assert "estleg:assertionConfidence" not in p3
    assert doc["@context"]["prov"] == "http://www.w3.org/ns/prov#"

    assert state.overrides_applied == 4
    # The reviewed authority gets an institution back-link; the heuristic one
    # is only linked from the provision the human did not override.
    links = state.inst_provisions["estleg:Institution_Justiitsministeerium"]
    assert links == [("estleg:CMP_Par_1", "regulation", "estleg:CMP_Map_2026")]
    assert [p for p, _c, _l in state.inst_provisions[heuristic_iri]] == ["estleg:CMP_Par_3"]

    _run_competence(peep, store)
    assert peep.read_bytes() == first


def test_competence_forced_type_without_authority_override(tmp_path: Path) -> None:
    peep = _competence_peep(tmp_path)
    store = _store(
        _rec(node="estleg:CMP_Par_3", predicate="estleg:competenceType", value="advisory"),
    )
    state = _run_competence(peep, store)
    p3 = _node(_read(peep), "estleg:CMP_Par_3")
    assert p3["estleg:competenceType"] == "advisory"
    assert p3["estleg:competentAuthority"]  # heuristic authority kept
    iri = p3["estleg:competentAuthority"][0]["@id"]
    assert ("estleg:CMP_Par_3", "advisory", "estleg:CMP_Map_2026") in state.inst_provisions[iri]


def test_competence_unknown_override_institution_is_skipped(tmp_path: Path) -> None:
    peep = _competence_peep(tmp_path)
    store = _store(
        _rec(node="estleg:CMP_Par_1", predicate="estleg:competentAuthority",
             value=[{"@id": "estleg:Institution_Nonexistent_Body_X"}]),
    )
    state = _run_competence(peep, store)
    assert state.override_links_skipped == 1
    assert "estleg:Institution_Nonexistent_Body_X" not in state.inst_data
    # The provision still carries the reviewed value.
    p1 = _node(_read(peep), "estleg:CMP_Par_1")
    assert p1["estleg:competentAuthority"] == [{"@id": "estleg:Institution_Nonexistent_Body_X"}]


def test_competence_main_check_overrides(tmp_path: Path, monkeypatch, capsys) -> None:
    from estleg import extract_institutional_competence as mod

    peep = _competence_peep(tmp_path)
    before = peep.read_bytes()
    store = _write_store(
        tmp_path / "o.jsonl",
        _rec(node="estleg:CMP_Par_1", predicate="estleg:competenceType", value="general"),
    )
    monkeypatch.setattr(mod, "iter_peep_files", lambda *a, **k: [peep])
    assert mod.main(["--overrides", str(store), "--check-overrides"]) == 0
    out = capsys.readouterr().out
    assert "Would apply:                   1" in out
    assert "Stale (node not found):        0" in out
    assert peep.read_bytes() == before


# ---------------------------------------------------------------------------
# EuroVoc
# ---------------------------------------------------------------------------


def _eurovoc_krr(tmp_path: Path) -> tuple[Path, list[Path]]:
    krr = tmp_path / "krr_outputs"
    krr.mkdir(exist_ok=True)
    peeps = []
    for slug, text in (
        ("AAA", "põhiseadus põhiseaduslik rahvahääletus"),
        ("BBB", "põhiseadus põhiseaduslik rahvahääletus"),
        ("CCC", "põhiseadus põhiseaduslik rahvahääletus"),
    ):
        peep = krr / f"{slug.lower()}_peep.json"
        peep.write_text(
            json.dumps(
                {
                    "@context": {"estleg": "https://w3id.org/estleg/"},
                    "@graph": [
                        {
                            "@id": f"estleg:{slug}_Map_2026",
                            "@type": ["estleg:Act", "estleg:Law"],
                            "rdfs:label": f"{slug} seadus",
                            "dcterms:subject": [{"@id": "estleg:LocalTopic_X"}],
                        },
                        {
                            "@id": f"estleg:{slug}_Par_1",
                            "@type": ["estleg:LegalProvision"],
                            "estleg:summary": text,
                        },
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        peeps.append(peep)
    return krr, peeps


def _run_eurovoc(monkeypatch, krr: Path, peeps: list[Path], store: Path, *extra: str):
    from estleg import classify_eurovoc as ev

    monkeypatch.setattr(ev, "KRR_DIR", krr)
    monkeypatch.setattr(ev, "iter_peep_files", lambda *a, **k: list(peeps))
    monkeypatch.setattr(ev, "write_coverage_report", lambda *a, **k: None)
    monkeypatch.setattr(ev, "write_eurovoc_skos_graph", lambda *a, **k: krr / "skos.jsonld")
    return ev.main(["--overrides", str(store), *extra])


EV_SET = [{"@id": "http://eurovoc.europa.eu/1235"}, {"@id": "http://eurovoc.europa.eu/2826"}]


def test_eurovoc_overlay_honours_set_and_remove_and_survives_rerun(
    tmp_path: Path, monkeypatch
) -> None:
    krr, peeps = _eurovoc_krr(tmp_path)
    store = _write_store(
        tmp_path / "o.jsonl",
        _rec(node="estleg:AAA_Map_2026", predicate="dcterms:subject", value=EV_SET),
        _rec(node="estleg:BBB_Map_2026", predicate="dcterms:subject", action="remove", value=...),
    )
    _run_eurovoc(monkeypatch, krr, peeps, store)
    overlay_path = krr / "eurovoc" / "eurovoc_overlay.jsonld"
    overlay = _read(overlay_path)
    first = overlay_path.read_bytes()
    assert overlay["@context"]["prov"] == "http://www.w3.org/ns/prov#"

    aaa = _node(overlay, "estleg:AAA_Map_2026")
    assert aaa["dcterms:subject"] == EV_SET
    assert aaa["eli:is_about"] == EV_SET
    assert aaa["prov:wasAttributedTo"] == REVIEWER
    assert aaa["estleg:assertionConfidence"] == CONF_1

    bbb = _node(overlay, "estleg:BBB_Map_2026")
    assert "dcterms:subject" not in bbb
    assert "eli:is_about" not in bbb
    assert bbb["prov:wasAttributedTo"] == REVIEWER

    ccc = _node(overlay, "estleg:CCC_Map_2026")
    assert ccc["dcterms:subject"]  # keyword result untouched
    assert "prov:wasAttributedTo" not in ccc

    report = _read(krr / "reports" / "eurovoc_classification.json")
    assert report["human_overrides_applied"] == 2

    _run_eurovoc(monkeypatch, krr, peeps, store)
    assert overlay_path.read_bytes() == first


def test_eurovoc_write_peeps_keeps_reviewed_subjects(tmp_path: Path, monkeypatch) -> None:
    krr, peeps = _eurovoc_krr(tmp_path)
    store = _write_store(
        tmp_path / "o.jsonl",
        _rec(node="estleg:AAA_Map_2026", predicate="dcterms:subject", value=EV_SET),
    )
    _run_eurovoc(monkeypatch, krr, peeps, store, "--write-peeps")
    first = peeps[0].read_bytes()
    act = _node(_read(peeps[0]), "estleg:AAA_Map_2026")
    # Non-EuroVoc subjects survive; EuroVoc part is exactly the reviewed set.
    assert act["dcterms:subject"] == [{"@id": "estleg:LocalTopic_X"}, *EV_SET]
    assert act["eli:is_about"] == EV_SET
    assert act["prov:wasAttributedTo"] == REVIEWER
    assert act["estleg:assertionConfidence"] == CONF_1
    assert _read(peeps[0])["@context"]["prov"] == "http://www.w3.org/ns/prov#"
    # The un-overridden act got the keyword subjects as before.
    other = _node(_read(peeps[2]), "estleg:CCC_Map_2026")
    assert len(other["dcterms:subject"]) > 1
    assert "prov:wasAttributedTo" not in other

    _run_eurovoc(monkeypatch, krr, peeps, store, "--write-peeps")
    assert peeps[0].read_bytes() == first


def test_eurovoc_check_overrides(tmp_path: Path, monkeypatch, capsys) -> None:
    krr, peeps = _eurovoc_krr(tmp_path)
    store = _write_store(
        tmp_path / "o.jsonl",
        _rec(node="estleg:ZZZ_Map_2026", predicate="dcterms:subject", value=EV_SET),
    )
    _run_eurovoc(monkeypatch, krr, peeps, store, "--check-overrides")
    out = capsys.readouterr().out
    assert "line 1: estleg:ZZZ_Map_2026 dcterms:subject" in out
    assert not (krr / "eurovoc").exists()


# ---------------------------------------------------------------------------
# Stale reporting + CLI
# ---------------------------------------------------------------------------


def test_check_overrides_counts(tmp_path: Path) -> None:
    peep = _deontic_peep(tmp_path)
    store = _store(_rec(node="estleg:TST_Par_1"), _rec(node="estleg:Nope_Par_1"))
    report = check_overrides(store, [peep])
    assert report["total"] == 2
    assert report["applicable"] == 1
    assert report["stale"] == [
        {"line": 2, "node": "estleg:Nope_Par_1", "predicate": "estleg:normativeType"}
    ]


def test_cli_validate_list_stale(tmp_path: Path, monkeypatch, capsys) -> None:
    from estleg import estleg_common

    peep = _deontic_peep(tmp_path)
    store = _write_store(
        tmp_path / "o.jsonl",
        _rec(node="estleg:TST_Par_1"),
        _rec(node="estleg:Nope_Par_1", predicate="estleg:targetGroup",
             value=[{"@id": "estleg:TargetGroup_Citizen"}]),
    )
    assert ho.main(["--path", str(store), "validate"]) == 0
    assert "OK: 2 override(s)" in capsys.readouterr().out

    assert ho.main(["--path", str(store), "list", "--classifier", "deontic"]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["line"] == 1

    monkeypatch.setattr(estleg_common, "iter_peep_files", lambda *a, **k: [peep])
    assert ho.main(["--path", str(store), "stale"]) == 1
    assert "estleg:Nope_Par_1" in capsys.readouterr().out
    assert ho.main(["--path", str(store), "stale", "--classifier", "deontic"]) == 0

    bad = _write_store(tmp_path / "bad.jsonl", _rec(date="nope"))
    assert ho.main(["--path", str(bad), "validate"]) == 1
    assert "line 1" in capsys.readouterr().err

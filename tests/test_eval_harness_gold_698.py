"""Legal gold sets, accuracy floors and the fitness-report check (#698).

Default tier: a synthetic corpus with known answers drives the gold-set
builder end to end (schema, deterministic sampling, mechanical rules), the
precision/recall maths, the floor gate and ``--check``. The committed gold
files are schema-validated without touching the corpus. One ``corpus`` test
asserts every committed gold item's node IRI exists in the real corpus.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from estleg import eval_harness, gold_sets

REPO = Path(__file__).resolve().parents[1]
COMMITTED_GOLD = REPO / "eval" / "gold_sets"
COMMIT = "3e4d75f34051a514c95d762354aaf4574c1a7bd7"
CTX = {"estleg": "https://w3id.org/estleg/"}


# ── synthetic corpus ─────────────────────────────────────────────────────────

def _w(path: Path, graph: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"@context": CTX, "@graph": graph}, ensure_ascii=False), encoding="utf-8")


def _prov(iri: str, text: str, **preds) -> dict:
    node = {
        "@id": iri,
        "@type": ["owl:NamedIndividual", "estleg:Subsection" if "_Lg_" in iri else "estleg:LegalProvision"],
        "estleg:legalText": text,
        "estleg:partOfAct": {"@id": "estleg:LA_Map"},
    }
    for key, value in preds.items():
        node[f"estleg:{key}"] = [{"@id": v} for v in value] if isinstance(value, list) else {"@id": value}
    return node


def build_fixture_corpus(root: Path) -> Path:
    krr = root / "krr_outputs"
    law = [
        {
            "@id": "estleg:LA_Map", "@type": ["estleg:Act", "estleg:Law"],
            "dcterms:title": [{"@value": "Proovi seadus", "@language": "et"}],
            "dcterms:source": {"@id": "https://www.riigiteataja.ee/akt/101.xml"},
        },
        _prov("estleg:LA_Par_1", "Taotleja peab esitama andmed käesoleva seaduse § 2 lõike 1 alusel.",
              references=["estleg:LA_Par_2_Lg_1"], normativeType="estleg:NormType_Obligation",
              targetGroup=["estleg:TargetGroup_Citizen"], interpretedBy=["estleg:RK_1"]),
        _prov("estleg:LA_Par_2_Lg_1", "(1) Taotluse lahendab Keskkonnaamet.",
              competentAuthority=["estleg:Institution_keskkonnaamet"],
              normativeType="estleg:NormType_Permission"),
        _prov("estleg:LA_Par_3", "Kehtetu -", targetGroup=["estleg:TargetGroup_Business"]),
        _prov("estleg:LA_Par_4", "Loata tegutsemise eest –karistatakse rahatrahviga kuni kolmsada trahviühikut.",
              hasSanction=["estleg:Sanction_LA_Par_4_fine"]),
        _prov("estleg:LA_Par_5", "Teenuse osutaja tagab teenuse kättesaadavuse."),
    ]
    for n in range(6, 16):
        law.append(_prov(f"estleg:LA_Par_{n}", f"Ühistu liikmete arv on vähemalt {n}."))
    _w(krr / "law_a_peep.json", law)
    _w(krr / "regulations" / "riik" / "r_t1_peep.json", [
        {"@id": "estleg:Reg_1_Map", "@type": ["estleg:Act", "estleg:NationalRegulation"],
         "dc:source": "Proovi määrus", "dcterms:source": {"@id": "https://www.riigiteataja.ee/akt/102.xml"}},
        {"@id": "estleg:Reg_1_Par_1", "@type": ["estleg:LegalProvision"], "estleg:partOfAct": {"@id": "estleg:Reg_1_Map"},
         "estleg:legalText": "Määrus jõustub 1. jaanuaril.", "estleg:normativeType": {"@id": "estleg:NormType_Obligation"}},
    ])
    _w(krr / "regulations" / "kov" / "vald" / "k_t2_peep.json", [
        {"@id": "estleg:Reg_2_Map", "@type": ["estleg:Act", "estleg:MunicipalRegulation"],
         "dc:source": "Valla kord", "dcterms:source": {"@id": "https://www.riigiteataja.ee/akt/403.xml"},
         "estleg:issuer": "Proovi Vallavalitsus", "estleg:enactedBy": {"@id": "estleg:Issuer_proovi_vallavalitsus"}},
        {"@id": "estleg:Reg_2_Par_1", "@type": ["estleg:LegalProvision"], "estleg:partOfAct": {"@id": "estleg:Reg_2_Map"},
         "estleg:legalText": "Korra kinnitab Proovi Vallavalitsus.",
         "estleg:competentAuthority": [{"@id": "estleg:Issuer_proovi_vallavalitsus"}]},
    ])
    _w(krr / "sanctions" / "sanctions_law_a.json", [
        {"@id": "estleg:Sanction_LA_Par_4_fine", "@type": ["owl:NamedIndividual", "estleg:Sanction"],
         "estleg:sanctionType": "fine", "estleg:applicableProvision": {"@id": "estleg:LA_Par_4"},
         "estleg:maxPenaltyAmount": {"@value": "300", "@type": "xsd:decimal"},
         "estleg:maxPenaltyUnit": "fine_units", "rdfs:label": "Fine, max 300 fine units"},
    ])
    _w(krr / "riigikohus" / "riigikohus_2020_peep.json", [
        {"@id": "estleg:RK_1", "@type": ["owl:NamedIndividual", "estleg:CourtDecision"],
         "estleg:caseNumber": "3-20-1", "estleg:decisionDate": {"@value": "2020-05-01", "@type": "xsd:date"},
         "estleg:decisionLink": {"@value": "https://www.riigikohus.ee/et/lahendid/?asjaNr=3-20-1"},
         "estleg:caseType": {"@id": "estleg:CaseType_Administrative"},
         "estleg:interpretsLaw": [{"@id": "estleg:LA_Par_1"}],
         "estleg:legalText": "Kolleegium leiab, et PrS § 1 kohaselt ja PrS § 7 alusel tuleb kaebus rahuldada."},
    ])
    (krr / "reports").mkdir(parents=True, exist_ok=True)
    (krr / "reports" / "court_provision_links_report.json").write_text(json.dumps(
        {"abbreviation_mapping": {"PrS": {"iri_prefixes": ["LA"], "provision_count": 15}}}), encoding="utf-8")
    _w(krr / "eurovoc" / "eurovoc_overlay.jsonld", [
        {"@id": "estleg:EuroVocOverlay", "@type": ["owl:Ontology"]},
        {"@id": "estleg:Reg_2_Map", "dcterms:subject": [{"@id": "http://eurovoc.europa.eu/68"}]},
    ])
    _w(krr / "eurovoc_concept_scheme.jsonld", [
        {"@id": "estleg:EuroVocDomain_68", "skos:prefLabel": [{"@value": "kohalik omavalitsus", "@language": "et"}],
         "skos:exactMatch": [{"@id": "http://eurovoc.europa.eu/68"}]},
    ])
    _w(krr / "institutions" / "institution_keskkonnaamet.json", [
        {"@id": "estleg:Institution_keskkonnaamet", "rdfs:label": "Keskkonnaamet"},
    ])
    concept = ["owl:NamedIndividual", "estleg:Concept", "skos:Concept"]

    def defn(cid: str, label: str, prov: str) -> dict:
        return {"@id": cid, "@type": ["estleg:LegalConcept"], "skos:prefLabel": {"@value": label, "@language": "et"},
                "skos:definition": {"@value": f"{label} määratlus", "@language": "et"},
                "estleg:definedIn": [{"@id": prov}]}

    _w(krr / "concepts" / "concepts_combined.jsonld", [
        {"@id": "estleg:Concept_teenuse_osutaja", "@type": concept,
         "skos:prefLabel": {"@value": "teenuse osutaja", "@language": "et"},
         "skos:altLabel": [{"@value": "teenuseosutaja", "@language": "et"}],
         "estleg:hasDefinitionNode": [{"@id": "estleg:Concept_d1"}, {"@id": "estleg:Concept_d2"}]},
        defn("estleg:Concept_d1", "teenuse osutaja", "estleg:LA_Par_5"),
        defn("estleg:Concept_d2", "teenuseosutaja", "estleg:LA_Par_5"),
        {"@id": "estleg:Concept_ametnik", "@type": concept,
         "skos:prefLabel": {"@value": "ametnik", "@language": "et"},
         "skos:altLabel": [{"@value": "Ametnik", "@language": "et"}],
         "estleg:hasDefinitionNode": [{"@id": "estleg:Concept_d3"}]},
        defn("estleg:Concept_d3", "Ametnik", "estleg:LA_Par_6"),
        {"@id": "estleg:Concept_kaevik", "@type": concept,
         "skos:prefLabel": {"@value": "kaevik", "@language": "et"},
         "estleg:hasDefinitionNode": [{"@id": "estleg:Concept_d4"}]},
        defn("estleg:Concept_d4", "kaevik", "estleg:LA_Par_7"),
        {"@id": "estleg:Concept_kaevur", "@type": concept,
         "skos:prefLabel": {"@value": "kaevur", "@language": "et"},
         "estleg:hasDefinitionNode": [{"@id": "estleg:Concept_d5"}]},
        defn("estleg:Concept_d5", "kaevur", "estleg:LA_Par_8"),
    ])
    (krr / "INDEX.json").write_text(json.dumps(
        {"laws": [{"name": "law_a", "files": ["law_a_peep.json"]}]}), encoding="utf-8")
    (krr / "provision_versions").mkdir(exist_ok=True)
    return krr


@pytest.fixture(scope="module")
def fixture_gold(tmp_path_factory) -> tuple[Path, Path]:
    root = tmp_path_factory.mktemp("gold698")
    krr = build_fixture_corpus(root)
    out = root / "gold"
    out.mkdir()
    gold_sets.build(krr, out, COMMIT, list(gold_sets.LAYERS), gold_sets.DEFAULT_SEED)
    return krr, out


def _items(out: Path, layer: str) -> list[dict]:
    return json.loads((out / f"{layer}.json").read_text(encoding="utf-8"))["items"]


# ── committed gold files ─────────────────────────────────────────────────────

def test_committed_gold_sets_cover_every_layer_and_validate():
    schema = gold_sets.load_schema()
    files = {p.stem for p in COMMITTED_GOLD.glob("*.json") if p.name != "item.schema.json"}
    assert files == set(gold_sets.LAYERS)
    for layer in sorted(files):
        doc = json.loads((COMMITTED_GOLD / f"{layer}.json").read_text(encoding="utf-8"))
        assert gold_sets.validate_gold_document(doc, schema) == [], layer
        items = doc["items"]
        assert len(items) == gold_sets.LAYERS[layer].sample_size, layer
        assert sum(it["negative"] for it in items) / len(items) >= 0.15, layer
        assert doc["sampling"]["seed"] == gold_sets.DEFAULT_SEED
        assert len(doc["sampling"]["corpus_commit"]) == 40
        for it in items:
            assert it["citation"].startswith("https://"), it["id"]
            assert it["evidence"]["text"].strip(), it["id"]
            if it["verdict_source"] == "mechanical":
                assert it["mechanical_rule"] in doc["mechanical_rules"], it["id"]
            # Nothing is adjudicated by a reviewer yet; pre-fills are mechanical only.
            assert it["verdict_source"] in (None, "mechanical", "reviewer")


def test_committed_floors_cover_every_layer():
    floors = json.loads((REPO / "eval" / "accuracy_floors.json").read_text(encoding="utf-8"))
    assert set(floors["layers"]) == set(gold_sets.LAYERS)
    for floor in floors["layers"].values():
        assert set(floor) == {"precision", "recall", "min_adjudicated"}


def test_committed_gate_passes():
    assert eval_harness.main([
        "--gold-set", str(COMMITTED_GOLD), "--floors", str(REPO / "eval" / "accuracy_floors.json"),
        "--gate", "--quiet",
    ]) == 0


# ── builder on the synthetic corpus ──────────────────────────────────────────

def test_builder_writes_valid_files_for_every_layer(fixture_gold):
    _krr, out = fixture_gold
    schema = gold_sets.load_schema()
    for layer in gold_sets.LAYERS:
        doc = json.loads((out / f"{layer}.json").read_text(encoding="utf-8"))
        assert gold_sets.validate_gold_document(doc, schema) == [], layer
        assert doc["sampling"]["corpus_commit"] == COMMIT


def test_builder_is_deterministic(fixture_gold, tmp_path):
    krr, out = fixture_gold
    again = tmp_path / "again"
    again.mkdir()
    gold_sets.build(krr, again, COMMIT, list(gold_sets.LAYERS), gold_sets.DEFAULT_SEED)
    for layer in gold_sets.LAYERS:
        assert (again / f"{layer}.json").read_bytes() == (out / f"{layer}.json").read_bytes(), layer


def test_stratified_sampling_is_seeded_and_stratified():
    cands = [{"stratum": s, "sort_key": (s, i), "i": i} for s in ("a", "b", "c") for i in range(100)]
    cands.append({"stratum": "rare", "sort_key": ("rare", 0), "i": 0})
    one = gold_sets.stratified_sample(cands, 30, random.Random(698))
    two = gold_sets.stratified_sample(list(reversed(cands)), 30, random.Random(698))
    other = gold_sets.stratified_sample(cands, 30, random.Random(1))
    assert [c["sort_key"] for c in one] == [c["sort_key"] for c in two]
    assert [c["sort_key"] for c in one] != [c["sort_key"] for c in other]
    strata = {c["stratum"] for c in one}
    assert strata == {"a", "b", "c", "rare"}  # the rare stratum is not starved
    assert len(one) == 30


def test_allocate_respects_sizes_and_budget():
    alloc = gold_sets.allocate({"big": 1000, "mid": 100, "tiny": 3}, 60, min_per=8)
    assert sum(alloc.values()) == 60
    assert alloc["tiny"] == 3 and alloc["big"] > alloc["mid"] >= 8
    assert gold_sets.allocate({"x": 5, "y": 2}, 60) == {"x": 5, "y": 2}
    many = gold_sets.allocate({f"s{i}": 10 for i in range(50)}, 40)
    assert sum(many.values()) == 40


def test_mechanical_verdicts_on_fixture(fixture_gold):
    _krr, out = fixture_gold
    by_rule = {}
    for layer in gold_sets.LAYERS:
        for it in _items(out, layer):
            by_rule.setdefault((layer, it["mechanical_rule"]), []).append(it)
    xref = by_rule[("crossReferences", "xref-same-act-citation")][0]
    assert xref["node"] == "estleg:LA_Par_1" and xref["system"] == ["estleg:LA_Par_2_Lg_1"]
    assert xref["citation"] == "https://www.riigiteataja.ee/akt/101#para1"
    assert xref["verdict"] == "correct" and xref["gold"] == xref["system"]
    sanction = by_rule[("sanctions", "sanction-amount-in-text")][0]
    assert "kolmsada trahviühikut" in sanction["evidence"]["text"]
    assert by_rule[("targetGroup", "repealed-assertion")][0]["verdict"] == "incorrect"
    assert by_rule[("deontic", "deontic-single-marker")][0]["node"] == "estleg:LA_Par_1"
    competence = {it["node"] for it in by_rule[("competence", "competence-named-actor")]}
    assert competence == {"estleg:LA_Par_2_Lg_1", "estleg:Reg_2_Par_1"}
    court = by_rule[("courtLinks", "court-cites-provision")][0]
    assert court["citation"].startswith("https://www.riigikohus.ee/")
    assert court["related_citation"] == "https://www.riigiteataja.ee/akt/101#para1"
    folds = _items(out, "altLabelFolds")
    case = [it for it in folds if it["mechanical_rule"] == "fold-case-only"]
    sep = [it for it in folds if it["stratum"]["value"] == "separator"]
    assert case and case[0]["verdict"] == "correct"
    assert sep and sep[0]["verdict"] == "pending"  # spacing folds need a reviewer
    near = [it for it in folds if it["negative"]]
    assert near and near[0]["probe"] in {"estleg:Concept_kaevik", "estleg:Concept_kaevur"}


def test_negatives_have_no_system_values(fixture_gold):
    _krr, out = fixture_gold
    for layer in gold_sets.LAYERS:
        for it in _items(out, layer):
            assert bool(it["system"]) != it["negative"], (layer, it["id"])


def test_rebuild_keeps_reviewer_verdicts(fixture_gold, tmp_path):
    krr, out = fixture_gold
    work = tmp_path / "work"
    work.mkdir()
    doc = json.loads((out / "deontic.json").read_text(encoding="utf-8"))
    target = next(it for it in doc["items"] if it["verdict"] == "pending")
    target.update({"verdict": "incorrect", "verdict_source": "reviewer", "gold": ["estleg:NormType_Right"],
                   "reviewer": "Test Reviewer", "adjudicated_on": "2026-10-09", "note": "is a right"})
    (work / "deontic.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    gold_sets.build(krr, work, COMMIT, ["deontic"], gold_sets.DEFAULT_SEED)
    rebuilt = {it["id"]: it for it in _items(work, "deontic")}
    kept = rebuilt[target["id"]]
    assert kept["verdict"] == "incorrect" and kept["reviewer"] == "Test Reviewer"
    assert kept["gold"] == ["estleg:NormType_Right"]


# ── mechanical rules in isolation ────────────────────────────────────────────

def test_sanction_rule_number_words_and_defaults():
    fine = {"estleg:sanctionType": "fine", "estleg:maxPenaltyAmount": {"@value": "300"},
            "estleg:maxPenaltyUnit": "fine_units"}
    assert gold_sets.sanction_rule(fine, "–karistatakse rahatrahviga kuni kolmsada trahviühikut.")[0] == \
        "sanction-amount-in-text"
    assert gold_sets.sanction_rule(fine, "–karistatakse rahatrahviga kuni 300 trahviühikut.")[0] == \
        "sanction-amount-in-text"
    assert gold_sets.sanction_rule(fine, "–karistatakse rahatrahviga kuni 200 trahviühikut.")[0] == \
        "sanction-amount-absent"
    big = {"estleg:sanctionType": "fine", "estleg:maxPenaltyAmount": {"@value": "32000"},
           "estleg:maxPenaltyUnit": "monetary"}
    assert gold_sets.sanction_rule(big, "karistatakse rahatrahviga kuni 32 000 eurot")[0] == "sanction-amount-in-text"
    arrest = {"estleg:sanctionType": "arrest", "estleg:maxPenaltyAmount": {"@value": "30"},
              "estleg:maxPenaltyUnit": "days", "estleg:isStatutoryDefault": True}
    assert gold_sets.sanction_rule(arrest, "karistatakse rahatrahviga või arestiga.")[0] == \
        "sanction-statutory-default"
    prison = {"estleg:sanctionType": "imprisonment", "estleg:maxPenaltyAmount": {"@value": "3"},
              "estleg:maxPenaltyUnit": "years"}
    assert gold_sets.sanction_rule(prison, "karistatakse rahalise karistuse või kuni kolmeaastase vangistusega")[0] \
        == "sanction-amount-in-text"
    noisy = "Veaparandus - parandatud. karistatakse rahatrahviga kuni 300 trahviühikut."
    assert gold_sets.sanction_rule(fine, noisy)[0] is None


def test_deontic_rule():
    obl, perm, pro = ("estleg:NormType_Obligation", "estleg:NormType_Permission", "estleg:NormType_Prohibition")
    assert gold_sets.deontic_rule([obl], "Taotleja peab esitama avalduse.") == ("correct", "deontic-single-marker")
    assert gold_sets.deontic_rule([pro], "Ametnik võib siseneda.") == ("incorrect", "deontic-polarity-conflict")
    assert gold_sets.deontic_rule([perm], "Isik ei pea esitama avaldust.") is None  # negated duty: ambiguous
    assert gold_sets.deontic_rule([pro], "Isik võib, kuid ei tohi.") is None  # two families
    assert gold_sets.deontic_rule([obl], "(2) Kehtetu -") == ("incorrect", "repealed-assertion")
    assert gold_sets.deontic_rule([pro], "Suitsetamine on keelatud.") == ("correct", "deontic-single-marker")


def test_competence_and_eurovoc_rules():
    assert gold_sets.competence_rule("Keskkonnaamet", "Loa annab välja Keskkonnaamet.")[0] == "competence-named-actor"
    # Named, but not as the actor of an authority verb.
    assert gold_sets.competence_rule("Keskkonnaamet", "Isik teavitab Keskkonnaametit.")[0] is None
    assert gold_sets.eurovoc_rule("konkurents", "Konkurentsiseadus") == "eurovoc-label-in-title"
    assert gold_sets.eurovoc_rule("kohalik omavalitsus", "Kohaliku omavalitsuse korralduse seadus")
    assert gold_sets.eurovoc_rule("haridus", "Põhikooli- ja gümnaasiumiseadus") is None


def test_rt_url_and_number_patterns():
    assert gold_sets.rt_url("https://www.riigiteataja.ee/akt/123.xml", "estleg:X_Par_53_1_Lg_4_1") == \
        "https://www.riigiteataja.ee/akt/123#para53b1lg4b1"
    assert gold_sets.parse_provision_iri("estleg:KARIST_2_Osa2_Par_92") == ("KARIST_2_Osa2", "92", None)
    assert gold_sets.is_repealed_text("(3) [Kehtetu - RT I 2010]")
    assert not gold_sets.is_repealed_text("Kehtetuks tunnistatakse määrus nr 5 ja kehtestatakse uus kord, mis …" * 3)
    assert gold_sets.levenshtein_at_most("laev", "laps", 2) == 2
    assert gold_sets.levenshtein_at_most("laev", "kaevur", 1) is None


# ── scoring maths ────────────────────────────────────────────────────────────

def _it(verdict, system, gold=(), negative=False, value="v"):
    return {"verdict": verdict, "system": list(system), "gold": list(gold), "negative": negative,
            "verdict_source": None if verdict == "pending" else "reviewer", "stratum": {"value": value}}


def test_score_items_precision_recall_with_negatives():
    items = [
        _it("correct", ["a"]),                          # TP 1
        _it("correct", ["b", "c"]),                     # TP 2
        _it("incorrect", ["d"]),                        # FP 1, nothing right
        _it("incorrect", ["e"], gold=["f"]),            # FP 1, FN 1
        _it("partial", ["g", "h"], gold=["g", "i"]),    # TP 1, FP 1, FN 1
        _it("correct", [], negative=True),              # TN 1
        _it("incorrect", [], gold=["j", "k"], negative=True),  # FN 2
        _it("incorrect", [], negative=True),            # FN 1 (missing value unnamed)
        _it("pending", ["z"]),                          # excluded
        _it("pending", [], negative=True),              # excluded
    ]
    sc = eval_harness.score_items(items)
    assert (sc["true_positives"], sc["false_positives"], sc["false_negatives"], sc["true_negatives"]) == (4, 3, 5, 1)
    assert sc["precision"] == round(4 / 7, 4)
    assert sc["recall"] == round(4 / 9, 4)
    assert sc["f1"] == pytest.approx(2 * (4 / 7) * (4 / 9) / (4 / 7 + 4 / 9), abs=1e-3)
    assert sc["pending"] == 2 and sc["adjudicated"] == 8 and sc["negatives"] == 4
    assert sc["verdicts"] == {"correct": 3, "incorrect": 4, "partial": 1, "pending": 2}


def test_score_items_all_pending_has_no_metrics():
    sc = eval_harness.score_items([_it("pending", ["a"]), _it("pending", [], negative=True)])
    assert sc["precision"] is None and sc["recall"] is None and sc["f1"] is None
    assert sc["adjudicated"] == 0


def test_wilson_lower_bound():
    assert eval_harness.wilson_lower(0, 0) is None
    assert 0.95 < eval_harness.wilson_lower(169, 169) < 1.0
    assert eval_harness.wilson_lower(50, 100) < 0.5


# ── gate semantics ───────────────────────────────────────────────────────────

def _gold_dir(tmp_path: Path, n_correct: int, n_incorrect: int, n_pending: int = 0) -> Path:
    d = tmp_path / "gold"
    d.mkdir()
    (d / "item.schema.json").write_text((COMMITTED_GOLD / "item.schema.json").read_text(encoding="utf-8"),
                                        encoding="utf-8")
    items = []
    verdicts = ["correct"] * n_correct + ["incorrect"] * n_incorrect + ["pending"] * n_pending
    for i, verdict in enumerate(verdicts):
        items.append({
            "id": f"deontic-{i:012x}", "node": f"estleg:X_Par_{i}", "predicate": "estleg:normativeType",
            "system": ["estleg:NormType_Obligation"], "gold": [], "verdict": verdict,
            "verdict_source": None if verdict == "pending" else "reviewer",
            "mechanical_rule": None, "negative": False,
            "citation": "https://www.riigiteataja.ee/akt/1", "evidence": {"text": "peab", "source": "t"},
            "stratum": {"act_kind": "law", "value": "v"},
            "reviewer": None if verdict == "pending" else "R", "adjudicated_on": None if verdict == "pending"
            else "2026-10-09", "note": None,
        })
    doc = {"schema_version": "1.0", "layer": "deontic", "title": "t", "property": "estleg:normativeType",
           "sampling": {"seed": 1, "corpus_commit": COMMIT, "generated_by": "test", "sample_size": len(items),
                        "negatives": 0}, "mechanical_rules": {}, "items": items}
    (d / "deontic.json").write_text(json.dumps(doc), encoding="utf-8")
    return d


def _floors(tmp_path: Path, precision=0.8, recall=None, min_adjudicated=10) -> Path:
    p = tmp_path / "floors.json"
    p.write_text(json.dumps({"layers": {"deontic": {"precision": precision, "recall": recall,
                                                    "min_adjudicated": min_adjudicated}}}), encoding="utf-8")
    return p


def _gate(gold: Path, floors: Path) -> int:
    return eval_harness.main(["--gold-set", str(gold), "--floors", str(floors), "--gate", "--quiet"])


def test_gate_passes_above_floor(tmp_path):
    assert _gate(_gold_dir(tmp_path, 9, 1), _floors(tmp_path)) == 0


def test_gate_fails_below_floor(tmp_path):
    assert _gate(_gold_dir(tmp_path, 5, 5), _floors(tmp_path)) == 1


def test_gate_does_not_fail_with_too_few_adjudicated(tmp_path):
    gold = _gold_dir(tmp_path, 1, 4, n_pending=50)
    assert _gate(gold, _floors(tmp_path, min_adjudicated=10)) == 0
    acc = eval_harness.evaluate_gold_dir(gold)
    gate = eval_harness.apply_floors(acc, json.loads(_floors(tmp_path, min_adjudicated=10).read_text()))
    assert gate["deontic"]["status"] == "insufficient"
    assert "not enough adjudicated items" in gate["deontic"]["reasons"][0]


def test_gate_fails_on_invalid_gold_file(tmp_path):
    gold = _gold_dir(tmp_path, 9, 1)
    doc = json.loads((gold / "deontic.json").read_text())
    doc["items"][0]["verdict"] = "maybe"
    (gold / "deontic.json").write_text(json.dumps(doc))
    assert _gate(gold, _floors(tmp_path)) == 1


def test_gate_fails_when_floor_has_no_gold_set(tmp_path):
    gold = _gold_dir(tmp_path, 9, 1)
    floors = tmp_path / "f.json"
    floors.write_text(json.dumps({"layers": {"deontic": {"precision": 0.5, "recall": None, "min_adjudicated": 1},
                                             "sanctions": {"precision": 0.5, "recall": None, "min_adjudicated": 1}}}))
    assert _gate(gold, floors) == 1


def test_validation_rejects_inconsistent_items(tmp_path):
    gold = _gold_dir(tmp_path, 2, 0)
    doc = json.loads((gold / "deontic.json").read_text())
    doc["items"][0]["negative"] = True  # negative with system values
    doc["items"][1]["verdict"] = "incorrect"
    doc["items"][1]["gold"] = list(doc["items"][1]["system"])  # "incorrect" but gold == system
    errors = gold_sets.validate_gold_document(doc)
    assert any("negative item must have empty system" in e for e in errors)
    assert any("gold equals the system values" in e for e in errors)
    doc = json.loads((gold / "deontic.json").read_text())
    doc["items"][1]["reviewer"] = None  # reviewer verdict without a reviewer (schema)
    assert any("reviewer" in e for e in gold_sets.validate_gold_document(doc))


# ── report + --check ─────────────────────────────────────────────────────────

def test_report_has_accuracy_block_and_check_detects_staleness(fixture_gold, tmp_path):
    krr, out = fixture_gold
    report = tmp_path / "eval" / "FITNESS_REPORT.md"
    floors = _floors(tmp_path, precision=0.5, min_adjudicated=1)
    args = ["--report", str(report), "--krr-dir", str(krr), "--gold-set", str(out), "--floors", str(floors),
            "--quiet"]
    assert eval_harness.main(args) == 0
    text = report.read_text(encoding="utf-8")
    assert "## Accuracy (legal gold sets, #698)" in text
    assert "| deontic |" in text and "ontology 1.0.0" in text
    assert (report.parent / "fitness_report.json").exists()
    data = json.loads((report.parent / "fitness_report.json").read_text(encoding="utf-8"))
    assert data["accuracy"]["corpus_commits"] == [COMMIT]
    assert eval_harness.main(args + ["--check"]) == 0
    report.write_text(text.replace("| deontic |", "| deontic (edited) |"), encoding="utf-8")
    assert eval_harness.main(args + ["--check"]) == 1


def test_legacy_single_file_scoring_finds_regulation_peeps(tmp_path):
    krr = tmp_path / "krr_outputs"
    _w(krr / "regulations" / "riik" / "r_peep.json", [
        {"@id": "estleg:Reg_9_Par_1", "estleg:targetGroup": ["citizen"]},
    ])
    gold = tmp_path / "g.json"
    gold.write_text(json.dumps({"property": "estleg:targetGroup",
                                "items": [{"node": "estleg:Reg_9_Par_1", "gold": ["citizen"]}]}))
    acc = eval_harness.evaluate_gold_set(gold, krr)
    assert acc["true_positives"] == 1  # rglob: regulation peeps are found


# ── corpus gate ──────────────────────────────────────────────────────────────

@pytest.mark.corpus
def test_every_gold_node_exists_in_corpus(corpus_krr):
    """Every committed gold item's node IRI is defined in the shipped corpus."""
    wanted: dict[str, list[str]] = {}
    for path in sorted(COMMITTED_GOLD.glob("*.json")):
        if path.name == "item.schema.json":
            continue
        for it in json.loads(path.read_text(encoding="utf-8"))["items"]:
            wanted.setdefault(it["node"], []).append(f"{path.stem}:{it['id']}")
    krr = corpus_krr.path("INDEX.json").parent
    files = sorted(krr.glob("*_peep.json")) + sorted((krr / "regulations").rglob("*_peep.json"))
    files.append(corpus_krr.path("concepts/concepts_combined.jsonld"))
    found: set[str] = set()
    for path in files:
        doc = json.loads(path.read_text(encoding="utf-8"))
        for node in doc.get("@graph", []) if isinstance(doc, dict) else []:
            if isinstance(node, dict) and node.get("@id") in wanted:
                found.add(node["@id"])
    missing = sorted(set(wanted) - found)
    assert not missing, f"{len(missing)} gold node IRIs not in corpus, e.g. {missing[:10]}"

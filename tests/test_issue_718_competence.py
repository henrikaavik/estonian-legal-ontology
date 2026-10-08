"""#718 — clause-level competence binding and institution identity.

Covers the binding rule (subject / inverted subject / adessive holder /
passive-delegation agent vs consultation, descriptive genitive, addressee,
law title), Estonian inflection handling, per-binding competence typing,
the provision-level outputs (competentAuthority / competenceType /
mentionsInstitution, predecessor mentions), the uncapped appliesToProvision
list, the dry-run contract, the Wikidata QID invariants and the identity
fields (registrikood, X-tee member code, validity, succession).
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

import pytest

from estleg import extract_institutional_competence as mod
from estleg.extract_institutional_competence import (
    SAMEAS_ALIASES,
    apply_institution_identity,
    bind_institutions,
    detect_clause_competence_type,
    load_institution_identity,
    mention_case,
    split_competence_clauses,
)

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data"
INST_DIR = REPO / "krr_outputs" / "institutions"


def _verdict(text: str) -> dict[str, mod.InstitutionBinding]:
    return {b.suffix: b for b in bind_institutions(text)}


# ---------------------------------------------------------------------------
# Binding rule
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,slug,ctype,reason",
    [
        # Nominative subject before the verb.
        ("Keskkonnaamet annab loa.", "keskkonnaamet", "licensing", "subject"),
        ("Maksu- ja Tolliamet teostab järelevalvet.", "maksu_ja_tolliamet", "supervision", "subject"),
        ("Linnavolikogu kehtestab maksu määra.", "linnavolikogu", "regulation", "subject"),
        ("Riigikohus otsustab vaidluse.", "riigikohus", "general", "subject"),
        ("Politsei- ja Piirivalveamet võib loa kehtetuks tunnistada.",
         "politsei_ja_piirivalveamet", "general", "subject"),
        ("Maa- ja Ruumiamet ei anna luba muutmiseks.", "maa_ja_ruumiamet", "licensing", "subject"),
        # Coordinated subjects share the verb.
        ("Keskkonnaamet ja Terviseamet kontrollivad nõuete täitmist.",
         "terviseamet", "enforcement", "subject"),
        # Inverted (V2) order.
        ("Riiklikku järelevalvet teostab Keskkonnaamet.", "keskkonnaamet", "supervision",
         "inverted_subject"),
        ("Toimkonna nimetab Vabariigi Valitsus.", "vabariigivalitsus", "general",
         "inverted_subject"),
        # Adessive holder of a power.
        ("Sotsiaalministril on õigus kehtestada nõuded.", "sotsiaalminister", "regulation",
         "adessive_power"),
        ("Finantsinspektsioonil on õigus teha ettekirjutus.", "finantsinspektsioon",
         "enforcement", "adessive_power"),
        # Passive regulation-making delegation, both word orders.
        ("Nõuded kehtestatakse sotsiaalministri määrusega.", "sotsiaalminister", "regulation",
         "passive_agent"),
        ("Sotsiaalministri määrusega kehtestatakse nõuded.", "sotsiaalminister", "regulation",
         "passive_agent"),
        ("Korra kehtestatakse Vabariigi Valitsuse määrusega.", "vabariigivalitsus", "regulation",
         "passive_agent"),
        # KOV head noun carries the case.
        ("Kohaliku omavalitsuse üksus kehtestab korra.", "kohalik_omavalitsus", "regulation",
         "subject"),
    ],
)
def test_competent_bindings(text, slug, ctype, reason):
    verdict = _verdict(text)[slug]
    assert verdict.competent, (text, verdict)
    assert verdict.competence_type == ctype
    assert reason in verdict.reasons


@pytest.mark.parametrize(
    "text,slug,reason",
    [
        # Consultation blocklist.
        ("Määruse kehtestab minister kaitseministriga kooskõlastatult.", "kaitseminister",
         "consultation"),
        ("Toimkonna nimetab Vabariigi Valitsus sotsiaalministri ettepanekul.", "sotsiaalminister",
         "consultation"),
        ("Riigikogu nõusolekul nimetab Vabariigi President ametisse.", "riigikogu", "consultation"),
        ("Luba antakse Keskkonnaameti arvamuse alusel.", "keskkonnaamet", "consultation"),
        ("Keskkonnaamet annab arvamuse.", "keskkonnaamet", "consultation"),
        # Descriptive genitive / scope clause (the two shipped false positives).
        ("Projekteerimistingimused on vajalikud riigi või kohaliku omavalitsuse eriplaneeringu "
         "alusel rajatavate ehitiste ehitusprojekti koostamiseks.", "kohalik_omavalitsus",
         "genitive"),
        ("Käesolev seadus sätestab Rahvusarhiivi ja kohaliku omavalitsuse arhiivi tegevuse "
         "alused.", "kohalik_omavalitsus", "genitive"),
        # Vowel-final name + genitive head noun.
        ("Riigikogu liige võib esitada küsimuse.", "riigikogu", "genitive"),
        # Addressee / object.
        ("Taotlus esitatakse Keskkonnaametile.", "keskkonnaamet", "allative"),
        ("Isik teavitab Terviseametit.", "terviseamet", "partitive"),
        # Citing an act is not a delegation; a participle after "poolt" is
        # descriptive.
        ("Seisukoht lisatakse Vabariigi Valitsuse määrusele.", "vabariigivalitsus", "genitive"),
        ("Määrusega sätestatakse Vabariigi Valitsuse poolt nimetatud asutuse ülesanded.",
         "vabariigivalitsus", "genitive"),
        # A nominative mention with no competence verb in its clause.
        ("Kaitseala valitseja on Keskkonnaamet.", "keskkonnaamet", "no_competence_verb"),
    ],
)
def test_non_competent_mentions(text, slug, reason):
    verdict = _verdict(text)[slug]
    assert verdict.competent is False, (text, verdict)
    assert reason in verdict.reasons


def test_law_title_is_not_a_mention():
    text = "Vabariigi Valitsuse seaduse § 105 alusel asendatud sõna."
    assert "vabariigivalitsus" not in _verdict(text)


def test_law_title_check_does_not_swallow_a_verb_phrase():
    verdict = _verdict("Riigikogu võtab vastu seaduse.")["riigikogu"]
    assert verdict.competent


def test_types_are_per_binding_not_per_provision():
    """The old extractor stamped ONE type on every institution of a
    provision; each binding is now typed from its own verb phrase."""
    text = (
        "Keskkonnaamet annab loa. Maksu- ja Tolliamet teostab järelevalvet. "
        "Riigikohus otsustab vaidluse."
    )
    verdict = _verdict(text)
    assert verdict["keskkonnaamet"].competence_type == "licensing"
    assert verdict["maksu_ja_tolliamet"].competence_type == "supervision"
    assert verdict["riigikohus"].competence_type == "general"
    assert mod.detect_competence_type(text) == "licensing"  # the old whole-text type


def test_institution_name_does_not_type_its_own_binding():
    verdict = _verdict("Tarbijakaitse ja Tehnilise Järelevalve Amet kooskõlastab teabelehe.")
    assert verdict["tarbijakaitse_ja_tehnilise_jarelevalve_amet"].competence_type == "general"


def test_enumeration_lead_in_binds_each_point():
    text = "Keskkonnaamet: 1) annab loa; 2) teostab järelevalvet."
    assert split_competence_clauses(text) == [
        "Keskkonnaamet: annab loa", "Keskkonnaamet: teostab järelevalvet",
    ]
    verdict = _verdict(text)["keskkonnaamet"]
    assert verdict.competent and verdict.competence_type == "licensing"
    assert verdict.reasons == ("subject", "subject")


def test_coordinated_genitive_agents_both_bind():
    verdict = _verdict(
        "Isik peetakse Politsei- ja Piirivalveameti või Kaitsepolitseiameti poolt kinni."
    )
    assert verdict["politsei_ja_piirivalveamet"].competent
    assert verdict["kaitsepolitseiamet"].competent


def test_clause_split_keeps_subject_and_verb_apart_across_sentences():
    verdict = _verdict("Keskkonnaamet on valitsusasutus. Luba annab ministeerium.")
    assert verdict["keskkonnaamet"].competent is False


@pytest.mark.parametrize(
    "surface,nominative,case",
    [
        ("Keskkonnaamet", "Keskkonnaamet", "nom"),
        ("Keskkonnaameti", "Keskkonnaamet", "genitive"),
        ("Keskkonnaametile", "Keskkonnaamet", "allative"),
        ("Keskkonnaametil", "Keskkonnaamet", "adessive"),
        ("Keskkonnaametilt", "Keskkonnaamet", "ablative"),
        ("Keskkonnaametiga", "Keskkonnaamet", "comitative"),
        ("Keskkonnaametit", "Keskkonnaamet", "partitive"),
        ("sotsiaalministri", "sotsiaalminister", "genitive"),
        ("sotsiaalministril", "sotsiaalminister", "adessive"),
        ("kaitseministriga", "kaitseminister", "comitative"),
        ("Vabariigi Valitsus", "Vabariigi Valitsus", "nom"),
        ("Vabariigi Valitsuse", "Vabariigi Valitsus", "genitive"),
        ("Vabariigi Valitsusel", "Vabariigi Valitsus", "adessive"),
        ("Vabariigi Valitsust", "Vabariigi Valitsus", "partitive"),
        ("Riigikohtu", "Riigikohus", "genitive"),
        ("Riigikohtule", "Riigikohus", "allative"),
        ("Riigikogu", "Riigikogu", "nom_gen"),
        ("Riigikogule", "Riigikogu", "allative"),
        ("Politsei- ja Piirivalveametile", "Politsei- ja Piirivalveamet", "allative"),
        ("kohaliku omavalitsuse", "kohalik omavalitsus", "genitive"),
        ("vallavalitsusel", "vallavalitsus", "adessive"),
    ],
)
def test_mention_case(surface, nominative, case):
    assert mention_case(surface, nominative) == case


@pytest.mark.parametrize(
    "text,slug",
    [
        ("Vabariigi Valitsuse määrusega", "vabariigivalitsus"),
        ("Riigikohtule", "riigikohus"),
        ("Vabariigi Presidendi ettepanekul", "vabariigipresident"),
        ("Andmekaitse Inspektsioonile", "andmekaitseinspektsioon"),
    ],
)
def test_named_institutions_match_oblique_forms(text, slug):
    assert slug in {s for _, s, _ in mod.detect_institutions(text)}


def test_specific_court_in_genitive_still_suppresses_generic_kohus():
    slugs = {s for _, s, _ in mod.detect_institutions("Riigikohtu otsusega kohus nõustus")}
    assert "kohus" not in slugs and "riigikohus" in slugs


def test_clause_type_extended_forms():
    assert detect_clause_competence_type("annavad tegevusloa") == "licensing"
    assert detect_clause_competence_type("teostada järelevalvet") == "supervision"
    assert detect_clause_competence_type("kehtestatakse määrusega") == "regulation"


# ---------------------------------------------------------------------------
# Pipeline integration
# ---------------------------------------------------------------------------


def _peep(path: Path, provisions: dict[str, str]) -> Path:
    graph: list[dict] = [{"@id": "estleg:T718_Map_2026", "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"]}]
    for pid, text in provisions.items():
        graph.append({
            "@id": pid,
            "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
            "estleg:paragrahv": "§ 1",
            "estleg:summary": text,
        })
    path.write_text(json.dumps({"@context": {"estleg": "https://w3id.org/estleg/"}, "@graph": graph},
                               ensure_ascii=False), encoding="utf-8")
    return path


def _node(path: Path, pid: str) -> dict:
    doc = json.loads(path.read_text(encoding="utf-8"))
    return next(n for n in doc["@graph"] if n.get("@id") == pid)


def _run(path: Path, *, dry_run: bool = False) -> mod._PipelineState:
    state = mod._PipelineState()
    if dry_run:
        state.dry_run = True
        state.measurement = mod.CompetenceMeasurement()
    mod.process_law_file(filepath=path, state=state, issuer_registry={}, canonical_suffixes=set())
    return state


def test_provision_outputs_competent_mentions_and_type(tmp_path):
    peep = _peep(tmp_path / "t_peep.json", {
        "estleg:T718_Par_1": (
            "Keskkonnaamet annab loa kaitseministriga kooskõlastatult. "
            "Taotlus esitatakse Terviseametile. Järelevalvet teostab Maksu- ja Tolliamet."
        ),
    })
    state = _run(peep)
    node = _node(peep, "estleg:T718_Par_1")
    assert node["estleg:competentAuthority"] == [
        {"@id": "estleg:Institution_keskkonnaamet"},
        {"@id": "estleg:Institution_maksu_ja_tolliamet"},
    ]
    assert node["estleg:competenceType"] == "licensing"  # most specific binding
    assert node["estleg:mentionsInstitution"] == [
        {"@id": "estleg:Institution_kaitseminister"},
        {"@id": "estleg:Institution_terviseamet"},
    ]
    # Per-institution types on the sidecar records.
    types = {iri: {c for _p, c, _l in provs} for iri, provs in state.inst_provisions.items()}
    assert types["estleg:Institution_keskkonnaamet"] == {"licensing"}
    assert types["estleg:Institution_maksu_ja_tolliamet"] == {"supervision"}
    # Mention-only institutions still get a node (their IRIs must resolve).
    assert "estleg:Institution_terviseamet" in state.inst_data
    assert not state.inst_provisions.get("estleg:Institution_terviseamet")


def test_mention_only_provision_has_no_competence_type(tmp_path):
    peep = _peep(tmp_path / "t_peep.json", {
        "estleg:T718_Par_2": "Taotlus esitatakse kohaliku omavalitsuse üksusele.",
    })
    _run(peep)
    node = _node(peep, "estleg:T718_Par_2")
    assert "estleg:competentAuthority" not in node
    assert "estleg:competenceType" not in node
    assert node["estleg:mentionsInstitution"] == [{"@id": "estleg:Institution_kohalik_omavalitsus"}]


def test_predecessor_mention_kept_alongside_successor_competence(tmp_path):
    peep = _peep(tmp_path / "t_peep.json", {
        "estleg:T718_Par_3": "Maanteeamet annab loa.",
        "estleg:T718_Par_4": "Taotlus esitatakse Maanteeametile.",
    })
    _run(peep)
    bound = _node(peep, "estleg:T718_Par_3")
    assert bound["estleg:competentAuthority"] == [{"@id": "estleg:Institution_transpordiamet"}]
    assert bound["estleg:mentionsInstitution"] == [{"@id": "estleg:Institution_maanteeamet"}]
    mentioned = _node(peep, "estleg:T718_Par_4")
    assert "estleg:competentAuthority" not in mentioned
    assert mentioned["estleg:mentionsInstitution"] == [{"@id": "estleg:Institution_maanteeamet"}]


def test_rerun_is_idempotent_and_clears_stale_mentions(tmp_path):
    peep = _peep(tmp_path / "t_peep.json", {"estleg:T718_Par_5": "Keskkonnaamet annab loa."})
    doc = json.loads(peep.read_text(encoding="utf-8"))
    doc["@graph"][1]["estleg:mentionsInstitution"] = [{"@id": "estleg:Institution_stale"}]
    peep.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    _run(peep)
    first = peep.read_bytes()
    assert "estleg:mentionsInstitution" not in _node(peep, "estleg:T718_Par_5")
    _run(peep)
    assert peep.read_bytes() == first


def test_dry_run_writes_nothing_and_measures(tmp_path):
    peep = _peep(tmp_path / "t_peep.json", {
        "estleg:T718_Par_6": "Isik teavitab Keskkonnaametit kaitseministriga kooskõlastatult.",
    })
    doc = json.loads(peep.read_text(encoding="utf-8"))
    doc["@graph"][1]["estleg:competentAuthority"] = [
        {"@id": "estleg:Institution_keskkonnaamet"}, {"@id": "estleg:Institution_kaitseminister"},
    ]
    doc["@graph"][1]["estleg:competenceType"] = "general"
    peep.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    before = peep.read_bytes()
    state = _run(peep, dry_run=True)
    assert peep.read_bytes() == before
    result = state.measurement.as_dict(state)
    assert result["bindings_before_by_type"] == {"general": 2}
    assert result["bindings_after_total"] == 0
    assert result["before_type_to_after_state"] == {"general": {"mentionsInstitution": 2}}


def test_main_dry_run_cli_writes_only_report(tmp_path, monkeypatch):
    peep = _peep(tmp_path / "t_peep.json", {"estleg:T718_Par_7": "Keskkonnaamet annab loa."})
    inst = tmp_path / "institutions"
    inst.mkdir()
    monkeypatch.setattr(mod, "iter_peep_files", lambda *a, **k: [peep])
    monkeypatch.setattr(mod, "INSTIT_DIR", inst)
    monkeypatch.setattr(mod, "KRR_DIR", tmp_path)
    before = peep.read_bytes()
    report = tmp_path / "out" / "dry.json"
    assert mod.main(["--dry-run", "--dry-run-report", str(report)]) == 0
    assert peep.read_bytes() == before
    assert list(inst.iterdir()) == []
    assert json.loads(report.read_text(encoding="utf-8"))["bindings_after_by_type"] == {"licensing": 1}


def test_applies_to_provision_is_uncapped(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "INSTIT_DIR", tmp_path)
    state = mod._PipelineState()
    for i in range(120):
        mod._record_provision_for_institution(
            state=state, inst_iri="estleg:Institution_riigikohus", canon_name="Riigikohus",
            iri_suffix="riigikohus", itype="court", provision_iri=f"estleg:X_Par_{i}",
            competence_type="general", law_name="estleg:X_Map",
        )
    mod.write_institution_files(state)
    doc = json.loads((tmp_path / "institution_riigikohus.json").read_text(encoding="utf-8"))
    comp = next(n for n in doc["@graph"] if "estleg:Competence" in n["@type"])
    assert len(comp["estleg:appliesToProvision"]) == 120
    assert comp["estleg:appliesToProvisionCount"]["@value"] == "120"
    assert mod._APPLIES_TO_PROVISION_CAP is None


def test_mention_only_institution_file_and_predecessor_nodes(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "INSTIT_DIR", tmp_path)
    state = mod._PipelineState()
    mod._record_mention_for_institution(state, "estleg:Institution_terviseamet", "terviseamet", None)
    written = mod.write_institution_files(state)
    assert "terviseamet" in written
    node = json.loads((tmp_path / "institution_terviseamet.json").read_text(encoding="utf-8"))["@graph"][0]
    assert "estleg:hasCompetence" not in node
    for alias_key, record in mod._ALIAS_RECORDS.items():
        pred = json.loads((tmp_path / f"institution_{alias_key}.json").read_text(encoding="utf-8"))["@graph"][0]
        assert pred["dcterms:isReplacedBy"] == {"@id": f"estleg:Institution_{record['canonical']}"}
        assert pred["rdfs:label"] == record["label"]


# ---------------------------------------------------------------------------
# Wikidata QID invariants
# ---------------------------------------------------------------------------


def _wikidata_map() -> dict[str, dict]:
    raw = json.loads((DATA / "wikidata_institutions.json").read_text(encoding="utf-8"))
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def test_identity_qids_unique_except_abbreviation_aliases():
    by_qid: dict[str, set[str]] = defaultdict(set)
    for slug, entry in _wikidata_map().items():
        if entry.get("qid"):
            by_qid[entry["qid"]].add(slug)
    offenders = {}
    for qid, slugs in by_qid.items():
        non_alias = {s for s in slugs if s not in SAMEAS_ALIASES}
        if len(non_alias) > 1:
            offenders[qid] = sorted(slugs)
        for alias in slugs & set(SAMEAS_ALIASES):
            assert SAMEAS_ALIASES[alias] in slugs, (alias, qid)
    assert offenders == {}, offenders


@pytest.mark.parametrize("pred,succ", [
    ("keskkonnaministeerium", "kliimaministeerium"),
    ("haridusministeerium", "haridus_ja_teadusministeerium"),
    ("justiitsministeerium", "justiits_ja_digiministeerium"),
    ("keeleinspektsioon", "keeleamet"),
    ("maaeluministeerium", "regionaal_ja_pollumajandusministeerium"),
    ("pollumajandusministeerium", "regionaal_ja_pollumajandusministeerium"),
    ("teabeamet", "valisluureamet"),
])
def test_rename_predecessor_has_no_identity_qid(pred, succ):
    wd = _wikidata_map()
    assert wd[pred].get("qid") is None
    assert wd[pred]["seeAlsoQid"] == wd[succ]["qid"]
    assert mod.wikidata_iri_for_slug(pred) is None
    assert mod.wikidata_see_also_iri_for_slug(pred) == mod.wikidata_iri_for_slug(succ)


@pytest.mark.parametrize("concept_qid", ["Q515", "Q41487", "Q14212", "Q28122896"])
def test_concept_level_qids_are_not_identity(concept_qid):
    assert concept_qid not in {e.get("qid") for e in _wikidata_map().values()}


def test_peaminister_is_the_estonian_office():
    assert _wikidata_map()["peaminister"]["qid"] == "Q737115"


def test_every_wikidata_entry_is_in_the_lookup_cache():
    cache = json.loads((DATA / "wikidata_institution_cache.json").read_text(encoding="utf-8"))
    for slug, entry in _wikidata_map().items():
        for key in ("qid", "seeAlsoQid"):
            if entry.get(key):
                assert entry[key] in cache["entities"], (slug, entry[key])


# ---------------------------------------------------------------------------
# Identity data
# ---------------------------------------------------------------------------


def test_identity_records_are_consistent():
    identity = load_institution_identity()
    assert identity, "data/institution_identity.json is empty"
    cache = json.loads((DATA / "wikidata_institution_cache.json").read_text(encoding="utf-8"))["entities"]
    xtee = json.loads((DATA / "xtee_members_EE.json").read_text(encoding="utf-8"))
    gov = {m["memberCode"] for m in xtee["members"] if m["memberClass"] == "GOV"}
    wd = _wikidata_map()
    current_codes: Counter[str] = Counter()
    for slug, rec in identity.items():
        assert slug not in SAMEAS_ALIASES, slug
        code = rec.get("registrikood")
        if code:
            assert re.fullmatch(r"\d{8}", code), (slug, code)
            qid = wd[slug].get("qid") or wd[slug].get("seeAlsoQid")
            assert code in cache[qid]["P6518"], (slug, code)
            if "validTo" not in rec:
                current_codes[code] += 1
        if "xteeMemberCode" in rec:
            assert "validTo" not in rec
            assert rec["xteeMemberCode"] == f"EE/GOV/{code}"
            assert code in gov
        if rec.get("validFrom") and rec.get("validTo"):
            assert rec["validFrom"] <= rec["validTo"], slug
        for key in ("validFrom", "validTo"):
            if key in rec:
                date.fromisoformat(rec[key])
    assert [c for c, n in current_codes.items() if n > 1] == []


def test_succession_is_symmetric_and_contiguous():
    identity = load_institution_identity()
    for slug, rec in identity.items():
        for succ in rec.get("successorInstitution", []):
            assert slug in identity[succ].get("predecessorInstitution", []), (slug, succ)
            end, start = rec.get("validTo"), identity[succ].get("validFrom")
            if end and start and slug in {"keskkonnaministeerium", "haridusministeerium",
                                          "justiitsministeerium", "keeleinspektsioon",
                                          "maaeluministeerium", "pollumajandusministeerium",
                                          "teabeamet"}:
                assert date.fromisoformat(end) + timedelta(days=1) == date.fromisoformat(start)
        for pred in rec.get("predecessorInstitution", []):
            assert slug in identity[pred].get("successorInstitution", []), (pred, slug)


def test_identity_slugs_resolve_to_institution_files():
    present = {p.stem.removeprefix("institution_") for p in INST_DIR.glob("institution_*.json")}
    identity = load_institution_identity()
    for slug, rec in identity.items():
        assert slug in present, slug
        for key in ("predecessorInstitution", "successorInstitution"):
            for other in rec.get(key, []):
                assert other in present, (slug, key, other)


def test_identity_fields_round_trip_through_rdf():
    rdflib = pytest.importorskip("rdflib")
    node = {
        "@id": "estleg:Institution_kliimaministeerium",
        "@type": ["owl:NamedIndividual", "estleg:Institution"],
        "rdfs:label": "Kliimaministeerium",
        "owl:sameAs": [{"@id": "http://www.wikidata.org/entity/Q1"},
                       {"@id": "estleg:Institution_other"}],
    }
    apply_institution_identity(node, "kliimaministeerium")
    snapshot = json.dumps(node, sort_keys=True)
    apply_institution_identity(node, "kliimaministeerium")
    assert json.dumps(node, sort_keys=True) == snapshot  # idempotent
    doc = {"@context": mod.CONTEXT, "@graph": [node]}
    g = rdflib.Graph().parse(data=json.dumps(doc), format="json-ld")
    est = rdflib.Namespace("https://w3id.org/estleg/")
    subj = est["Institution_kliimaministeerium"]
    assert str(g.value(subj, est.registrikood)) == "70001231"
    assert str(g.value(subj, est.xteeMemberCode)) == "EE/GOV/70001231"
    valid_from = g.value(subj, est.validFrom)
    assert valid_from.datatype == rdflib.XSD.date and str(valid_from) == "2023-07-01"
    assert (subj, est.predecessorInstitution, est["Institution_keskkonnaministeerium"]) in g
    same = set(g.objects(subj, rdflib.OWL.sameAs))
    assert rdflib.URIRef("http://www.wikidata.org/entity/Q16408655") in same
    assert rdflib.URIRef("http://www.wikidata.org/entity/Q1") not in same  # stale QID removed
    assert est["Institution_other"] in same


def test_predecessor_node_round_trip():
    node = {"@id": "estleg:Institution_keskkonnaministeerium",
            "owl:sameAs": {"@id": "http://www.wikidata.org/entity/Q16408655"}}
    apply_institution_identity(node, "keskkonnaministeerium")
    assert "owl:sameAs" not in node
    assert node["rdfs:seeAlso"] == {"@id": "http://www.wikidata.org/entity/Q16408655"}
    assert node["estleg:validTo"] == {"@value": "2023-06-30", "@type": "xsd:date"}
    assert node["estleg:successorInstitution"] == [{"@id": "estleg:Institution_kliimaministeerium"}]
    assert "estleg:xteeMemberCode" not in node


def test_shipped_institution_files_carry_identity():
    kliima = json.loads((INST_DIR / "institution_kliimaministeerium.json").read_text(encoding="utf-8"))
    root = next(n for n in kliima["@graph"] if n["@id"] == "estleg:Institution_kliimaministeerium")
    assert root["estleg:registrikood"] == "70001231"
    assert root["estleg:validFrom"] == {"@value": "2023-07-01", "@type": "xsd:date"}
    kesk = json.loads((INST_DIR / "institution_keskkonnaministeerium.json").read_text(encoding="utf-8"))
    root = next(n for n in kesk["@graph"] if n["@id"] == "estleg:Institution_keskkonnaministeerium")
    assert "owl:sameAs" not in root
    assert root["estleg:successorInstitution"] == [{"@id": "estleg:Institution_kliimaministeerium"}]
    for alias_key in mod._ALIAS_RECORDS:
        assert (INST_DIR / f"institution_{alias_key}.json").is_file(), alias_key


def test_institution_label_is_independent_of_corpus_order():
    """A sentence-initial surface form must not relabel an institution: the
    curated map label wins, then the named catalogue, then the label on disk."""
    assert mod.preferred_institution_label("keskkonnaminister", "Keskkonnaminister") == "keskkonnaminister"
    assert mod.preferred_institution_label("rahandusminister", "rahandusministrile") == "Rahandusminister"
    assert mod.preferred_institution_label("riigikohus", "riigikohtule") == "Riigikohus"
    assert mod.preferred_institution_label("x_amet", "X ametile", {}, "x amet") == "x amet"
    assert mod.preferred_institution_label("x_amet", "Xametile", {}, None) == "Xamet"


# ---------------------------------------------------------------------------
# Act-root competentAuthority roll-up (#508 contract, restored in #718)
# ---------------------------------------------------------------------------


def _rollup_peep(path: Path, *, stale: list[str] | None = None) -> Path:
    act = {"@id": "estleg:R718_Map", "@type": ["estleg:Act", "estleg:Law"]}
    if stale:
        act["estleg:competentAuthority"] = [{"@id": i} for i in stale]
    part = {"@id": "estleg:R718_Map"}
    graph = [
        act,
        {"@id": "estleg:R718_Par_1", "@type": ["estleg:LegalProvision"], "estleg:partOfAct": part,
         "estleg:summary": "Keskkonnaamet annab loa. Taotlus esitatakse Terviseametile."},
        {"@id": "estleg:R718_Par_2", "@type": ["estleg:LegalProvision"], "estleg:partOfAct": part,
         "estleg:summary": "Riigikohus otsustab vaidluse. Keskkonnaamet teostab järelevalvet."},
        # No partOfAct: not rolled up (same as #508).
        {"@id": "estleg:R718_Par_3", "@type": ["estleg:LegalProvision"],
         "estleg:summary": "Maksu- ja Tolliamet kontrollib."},
    ]
    path.write_text(json.dumps({"@context": {"estleg": "https://w3id.org/estleg/"}, "@graph": graph},
                               ensure_ascii=False), encoding="utf-8")
    return path


def test_competence_pass_restamps_act_root_union(tmp_path):
    peep = _rollup_peep(tmp_path / "r_peep.json", stale=["estleg:Institution_stale"])
    _run(peep)
    root = _node(peep, "estleg:R718_Map")
    # Graph-order union of provision authorities; mention-only Terviseamet and
    # the provision without partOfAct do not roll up; the stale id is gone.
    assert root["estleg:competentAuthority"] == [
        {"@id": "estleg:Institution_keskkonnaamet"},
        {"@id": "estleg:Institution_riigikohus"},
    ]
    assert "estleg:mentionsInstitution" not in root


def test_rollup_includes_kov_issuers_and_respects_overrides():
    doc = {"@graph": [
        {"@id": "estleg:K_Map", "@type": ["estleg:Act", "estleg:MunicipalRegulation"]},
        {"@id": "estleg:K_Par_1", "estleg:partOfAct": {"@id": "estleg:K_Map"},
         "estleg:competentAuthority": [{"@id": "estleg:Issuer_tartu_linnavalitsus"}]},
    ]}
    assert mod.rollup_act_authorities(doc) == (1, 1)
    assert doc["@graph"][0]["estleg:competentAuthority"] == [{"@id": "estleg:Issuer_tartu_linnavalitsus"}]
    assert mod.rollup_act_authorities(doc) == (0, 1)  # idempotent

    class _Owner:
        def owns(self, node_id, predicate):
            return node_id == "estleg:K_Map"

    doc["@graph"][0]["estleg:competentAuthority"] = [{"@id": "estleg:Institution_reviewed"}]
    assert mod.rollup_act_authorities(doc, _Owner()) == (0, 1)
    assert doc["@graph"][0]["estleg:competentAuthority"] == [{"@id": "estleg:Institution_reviewed"}]


def test_rollup_only_cli_uses_edges_on_disk(tmp_path, monkeypatch):
    peep = _rollup_peep(tmp_path / "r_peep.json")
    doc = json.loads(peep.read_text(encoding="utf-8"))
    doc["@graph"][1]["estleg:competentAuthority"] = [{"@id": "estleg:Institution_a"}]
    doc["@graph"][2]["estleg:competentAuthority"] = [
        {"@id": "estleg:Institution_b"}, {"@id": "estleg:Institution_a"}]
    peep.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(mod, "iter_peep_files", lambda *a, **k: [peep])
    totals = mod.run_rollup_only()
    assert totals["acts_with_authority_before"] == 0
    assert totals["acts_with_authority_after"] == 1
    assert totals["files_written"] == 1
    root = _node(peep, "estleg:R718_Map")
    assert root["estleg:competentAuthority"] == [
        {"@id": "estleg:Institution_a"}, {"@id": "estleg:Institution_b"}]
    # Provisions are untouched by --rollup-only.
    assert _node(peep, "estleg:R718_Par_1")["estleg:competentAuthority"] == [{"@id": "estleg:Institution_a"}]

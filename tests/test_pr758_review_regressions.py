"""Regression cases found while reviewing the wave-six refresh."""

import hashlib
import json
from datetime import date
from pathlib import Path

import yaml
from xml.etree import ElementTree as ET

from estleg import eval_harness, generate_court_decisions as courts
from estleg.estleg_common import iter_krr_jsonld_files, pair_peep_with_xml
from estleg import riigiteataja_common as rt


def test_court_refresh_reserves_older_year_iris_before_processing_new_year(tmp_path, monkeypatch):
    case = "3-25-123/1"
    short = courts.rk_short_iri(case)
    old = {
        "@id": short, "@type": ["estleg:CourtDecision"],
        "estleg:caseNumber": case, "estleg:rikObjectId": "500",
        "estleg:legalText": "Published decision text",
    }
    (tmp_path / "riigikohus_2025_peep.json").write_text(json.dumps({"@graph": [old]}))
    monkeypatch.setattr(courts, "RK_DIR", tmp_path)
    monkeypatch.setattr(courts, "RK_IRI_COLLISIONS_PATH", tmp_path / "collisions.json")
    monkeypatch.setattr(courts, "START_YEAR", 2025)
    monkeypatch.setattr(courts.time, "sleep", lambda _: None)
    monkeypatch.setattr(courts, "default_end_year", lambda _: 2026)
    monkeypatch.setattr(courts, "fetch_year", lambda year: [{
        "case_nr": case, "object_id": "400" if year == 2026 else "500",
        "date": f"01.01.{year}", "decision_type": "Kohtuotsus", "summary": "", "link": "",
    }])
    courts.main(["--evaluation-date", date(2026, 10, 9).isoformat()])
    docs = {y: json.loads((tmp_path / f"riigikohus_{y}_peep.json").read_text()) for y in (2025, 2026)}
    nodes = {y: next(n for n in doc["@graph"] if "estleg:rikObjectId" in n) for y, doc in docs.items()}
    assert nodes[2025]["@id"] == short
    assert nodes[2025]["estleg:legalText"] == old["estleg:legalText"]
    assert nodes[2026]["@id"] == courts.rk_collision_iri(case, "400")


def test_collision_registry_reserves_short_holder_even_when_not_in_current_feed(tmp_path):
    path = tmp_path / "collisions.json"
    case = "3-25-123/1"
    path.write_text(json.dumps({"collisions": [{
        "caseNumber": case, "rikObjectId": "600", "shortFormHolder": "500",
        "iri": courts.rk_collision_iri(case, "600"),
    }]}))
    scheme = courts.RkIriScheme.frozen(path)
    scheme.plan([{"case_nr": case, "object_id": "400"}])
    assert scheme.mint(case, "400") == courts.rk_collision_iri(case, "400")
    assert scheme.mint(case, "500") == courts.rk_short_iri(case)


def test_attested_xml_is_rejected_after_cache_file_is_overwritten(tmp_path):
    krr = tmp_path / "krr_outputs"
    rt = tmp_path / "data" / "riigiteataja"
    krr.mkdir()
    rt.mkdir(parents=True)
    peep = krr / "law_osa1_peep.json"
    peep.write_text(json.dumps({"@graph": [{"@id": "estleg:X_Osa1", "@type": ["estleg:Part"],
                                           "estleg:globalId": "111"}]}))
    old_xml = b"<akt><metaandmed><globaalID>111</globaalID></metaandmed></akt>"
    xml = rt / "law__tid1.xml"
    xml.write_bytes(old_xml)
    (krr / "fetch_content_hashes.json").write_text(json.dumps({"law": {
        "globalId": "111", "cacheFile": "data/riigiteataja/law__tid1.xml",
        "sha256": hashlib.sha256(old_xml).hexdigest(),
    }}))
    assert pair_peep_with_xml(peep, {}, data_dir=rt) == xml
    xml.write_bytes(old_xml.replace(b"111", b"222"))
    assert pair_peep_with_xml(peep, {}, data_dir=rt) is None
    # Even the right redaction id cannot override a failed byte attestation.
    xml.write_bytes(old_xml.replace(b"</akt>", b"<sisu>changed</sisu></akt>"))
    assert pair_peep_with_xml(peep, {"111": xml}, data_dir=rt) is None
    (rt / "law.xml").write_bytes(old_xml.replace(b"111", b"222"))
    assert pair_peep_with_xml(peep, {}, data_dir=rt) is None


def test_floor_cannot_pass_an_unmeasurable_required_metric():
    accuracy = {"layers": {"test": {"adjudicated": 20, "precision": None, "recall": None}}}
    floors = {"layers": {"test": {"min_adjudicated": 10, "precision": 0.9}}}
    assert eval_harness.apply_floors(accuracy, floors)["test"]["status"] == "fail"


def test_gold_edits_trigger_ci_and_score_current_corpus():
    path = Path(__file__).resolve().parents[1] / ".github/workflows/validate.yml"
    workflow = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
    for event in ("push", "pull_request"):
        assert "eval/**" in workflow["on"][event]["paths"]
    commands = [step.get("run", "") for job in workflow["jobs"].values() for step in job["steps"]]
    gates = [command for command in commands if "eval_harness.py" in command and "--gate" in command]
    assert gates and all("--current-corpus" in command for command in gates)


def test_local_reports_cannot_change_catalogue_file_counts(tmp_path):
    krr = tmp_path / "krr_outputs"
    for rel in ("concepts/concept_crossref_report.json", "similarity/kov_similarity_index.json", "law_peep.json"):
        path = krr / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}")
    assert list(iter_krr_jsonld_files(krr)) == [krr / "law_peep.json"]


def test_regulation_text_preserves_real_and_escaped_superscripts():
    root = ET.fromstring("""<paragrahv>
      <paragrahvPealkiri><![CDATA[Nõuded § 2<sup>1</sup> alusel]]></paragrahvPealkiri>
      <loige><sisuTekst><tavatekst>Viide § 21<sup>2</sup> ning
        &lt;sup&gt;3&lt;/sup&gt; astmele.</tavatekst></sisuTekst></loige>
    </paragrahv>""")
    assert rt.ct(root, "paragrahvPealkiri") == "Nõuded § 2¹ alusel"
    assert rt.collect_full_text(root) == "Viide § 21² ning ³ astmele."
    assert rt.collect_text(root) == rt.collect_full_text(root)
    assert rt.strip_html_tags("<p>§ 2<sup>1</sup> ja &lt;sup&gt;3&lt;/sup&gt;</p>") == "§ 2¹ ja ³"

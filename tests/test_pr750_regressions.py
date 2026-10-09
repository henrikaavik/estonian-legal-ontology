"""Retrieval exports must preserve trustworthy identities and source metadata."""
import json

import pytest

from estleg import generate_retrieval_projection as grp
from tests.test_generate_retrieval_projection import EVAL_DATE, _build_corpus


@pytest.mark.parametrize("broken", ["INDEX.json", "testlaw_peep.json", "provision_versions/testlaw.jsonld"])
def test_invalid_input_preserves_previous_projection(tmp_path, broken):
    krr, out = tmp_path / "krr", tmp_path / "out"
    _build_corpus(krr)
    out.mkdir()
    previous = out / "chunks.jsonl"
    previous.write_text("previous export")
    (krr / broken).write_text("invalid JSON")
    with pytest.raises(ValueError):
        grp.generate(krr_dir=krr, out_dir=out, eval_date=EVAL_DATE, chunks_only=True)
    assert previous.read_text() == "previous export"


def test_duplicate_chunk_ids_are_rejected(tmp_path):
    krr = tmp_path / "krr"
    _build_corpus(krr)
    path = krr / "provision_versions/testlaw.jsonld"
    doc = json.loads(path.read_text())
    versions = [n for n in doc["@graph"] if "estleg:versionRedactionId" in n]
    versions[1]["estleg:versionRedactionId"] = versions[0]["estleg:versionRedactionId"]
    path.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="duplicate chunk"):
        grp.generate(krr_dir=krr, out_dir=tmp_path / "out", eval_date=EVAL_DATE)


def test_consolidated_snapshot_is_not_in_force_before_its_start(tmp_path):
    krr = tmp_path / "krr"
    _build_corpus(krr)
    out = tmp_path / "out"
    grp.generate(krr_dir=krr, out_dir=out, eval_date="2020-01-01")
    records = [json.loads(line) for line in (out / "chunks.jsonl").read_text().splitlines()]
    assert not next(c for c in records if c["redaction_id"] is None)["in_force"]


def test_rt_links_validate_host_and_strip_xml_before_anchor():
    act = "https://www.riigiteataja.ee/akt/123"
    assert grp.provision_rt_url("https://example.com/other", act, "§ 1") == act + "#para1"
    assert grp.provision_rt_url(act + ".xml#old", None, "§ 1") == act + "#para1"
    assert grp.provision_rt_url(None, "https://riigiteataja.ee.evil.test/", "§ 1") is None


def test_invalid_evaluation_date_is_rejected_before_writing(tmp_path):
    out = tmp_path / "out"
    with pytest.raises(ValueError, match="evaluation-date"):
        grp.generate(krr_dir=tmp_path, out_dir=out, eval_date="2026-99-99")
    assert not out.exists()

"""#692 — act-level provenance: content hash, consolidation ids, staleness, XML asset."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import tarfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from estleg import build_release_assets as bra
from estleg import generate_all_laws as gal
from estleg.backfill_rt_eli import fetch_hash_row
from estleg.estleg_common import record_fetch_hashes

REPO = Path(__file__).resolve().parent.parent
PAD = "<!-- " + "x" * 400 + " -->"


def _xml(gid: str, text: str = "Esimene.") -> str:
    return f"""<?xml version='1.0' encoding='utf-8'?>
{PAD}
<oigusakt><metaandmed><skeemiNimi>tyviseadus_1_10.02.2010.xsd</skeemiNimi>
<globaalID>{gid}</globaalID><terviktekstiGrupiID>500</terviktekstiGrupiID></metaandmed>
<sisu><paragrahv><paragrahvNr>1</paragrahvNr><kuvatavNr>§ 1.</kuvatavNr>
<loige><loigeNr>1</loigeNr><sisuTekst><tavatekst>{text}</tavatekst></sisuTekst></loige>
</paragrahv></sisu></oigusakt>"""


def _root_doc(*, tid: str | None = "500", gid: str | None = "2", kehtiv: str = "2026-10-09") -> dict:
    node: dict = {"@id": "estleg:X_Map", "@type": ["estleg:Act", "estleg:Law"],
                  "estleg:kehtiv": {"@value": kehtiv, "@type": "xsd:date"}}
    if tid is not None:
        node["estleg:terviktekstId"] = tid
    if gid is not None:
        node["estleg:globalId"] = gid
    return {"@graph": [node]}


# --- staleness ---------------------------------------------------------------


def test_missing_stored_tid_is_stale() -> None:
    # The ticket's executed case: kehtiv matches, no terviktekstId stored.
    assert gal.existing_law_is_stale(_root_doc(tid=None, gid=None), "2026-10-09", "100") is True


def test_new_redaction_under_the_same_kehtiv_is_stale() -> None:
    # The terviktekst id is the consolidation GROUP: identical across
    # redactions. Only the globaalID tells a new redaction apart.
    doc = _root_doc(tid="500", gid="1")
    assert gal.existing_law_is_stale(doc, "2026-10-09", "500", "2") is True
    assert gal.existing_law_is_stale(_root_doc(gid=None), "2026-10-09", "500", "2") is True
    assert gal.existing_law_is_stale(_root_doc(), "2026-10-09", "500", "2") is False


# --- cache: a cached older redaction is not trusted -------------------------


def test_cached_file_of_another_redaction_is_refetched(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(gal, "DATA_DIR", tmp_path)
    (tmp_path / "x__tid500.xml").write_text(_xml("1", "Vana."), encoding="utf-8")
    calls: list[str] = []

    def fake_common(url, cache_name, **kwargs):
        calls.append(cache_name)
        payload = _xml("2", "Uus.")
        (tmp_path / f"{cache_name}.xml").write_text(payload, encoding="utf-8")
        kwargs["on_bytes"](payload.encode("utf-8"))
        return ET.fromstring(payload.split("-->", 1)[1])

    monkeypatch.setattr(gal, "common_fetch_xml", fake_common)
    root = gal.fetch_xml("/akt/2.xml", "x", tid="500", gid="2")
    assert calls == ["x__tid500"]
    assert gal.xml_root_global_id(root) == "2"
    cached = (tmp_path / "x__tid500.xml").read_bytes()
    assert gal._CONTENT_HASHES["x"] == hashlib.sha256(cached).hexdigest()
    assert gal._FETCH_RECORDS["x"]["bytes"] == len(cached)


def test_matching_cache_is_used_and_attested_without_network(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(gal, "DATA_DIR", tmp_path)
    payload = _xml("2")
    (tmp_path / "x__tid500.xml").write_text(payload, encoding="utf-8")
    monkeypatch.setattr(gal, "common_fetch_xml", lambda *a, **k: pytest.fail("network"))
    root = gal.fetch_xml("/akt/2.xml", "x", tid="500", gid="2")
    assert root is not None
    record = gal._FETCH_RECORDS["x"]
    assert record["sha256"] == hashlib.sha256(payload.encode("utf-8")).hexdigest()
    assert record["cacheFile"].endswith("x__tid500.xml")
    assert record["fetchedFrom"].endswith("/public-api/api/v1/akt/2/xml")

    doc = gal.generate_law_jsonld(
        "X", "x", root, "XX", "/akt/2.xml", kehtiv="2026-10-09",
        allocator=gal.PrefixAllocator(registry={}),
    )
    head = doc["@graph"][0]
    assert head["estleg:contentHash"] == record["sha256"]
    assert head["estleg:terviktekstId"] == "500"
    assert head["estleg:skeemiNimi"] == "tyviseadus_1_10.02.2010.xsd"


def test_download_validator_rejects_a_different_redaction(tmp_path, monkeypatch):
    monkeypatch.setattr(gal, "DATA_DIR", tmp_path)
    monkeypatch.setattr(gal, "CACHE_ONLY", False)

    def fetch(_url, _name, **kwargs):
        assert kwargs["validate_root"](ET.fromstring(_xml("2")))
        assert not kwargs["validate_root"](ET.fromstring(_xml("1")))
        return None

    monkeypatch.setattr(gal, "common_fetch_xml", fetch)
    assert gal.fetch_xml("/akt/2.xml", "x", tid="500", gid="2") is None


# --- fetch_content_hashes.json ----------------------------------------------


def test_hash_row_attests_the_cited_redaction() -> None:
    record = {"sha256": "ab" * 32, "bytes": 10, "cacheFile": "data/riigiteataja/x__tid500.xml"}
    row = fetch_hash_row(
        record, {"gid": "1", "xmlGlobalId": "2", "tid": "500", "kehtivusAlgus": "2026-10-01"},
        kehtiv="2026-10-09",
    )
    assert row["globalId"] == "2"  # the XML's own id wins over the search row
    assert row["source"] == "https://www.riigiteataja.ee/public-api/api/v1/akt/2/xml"
    assert row["page"] == "https://www.riigiteataja.ee/akt/2"
    assert row["kehtivuseAlgus"] == "2026-10-01"
    assert fetch_hash_row(None, {"gid": "2"}, kehtiv=None) is None


def test_record_fetch_hashes_replaces_rows_and_sorts(tmp_path) -> None:
    dest = tmp_path / "fetch_content_hashes.json"
    dest.write_text(json.dumps({
        "z": {"sha256": "old", "source": "data/riigiteataja/z.xml"},
        "keep": {"sha256": "k"},
    }), encoding="utf-8")
    record_fetch_hashes({"z": {"sha256": "new"}, "a": {"sha256": "a"}}, path=dest)
    data = json.loads(dest.read_text(encoding="utf-8"))
    assert list(data) == ["a", "keep", "z"]
    assert data["z"] == {"sha256": "new"}  # no stale field survives


# --- committed manifest, ignored XML ----------------------------------------


def test_manifest_is_committed_and_xml_stays_ignored() -> None:
    lines = {
        line.strip()
        for line in (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    assert "krr_outputs/generation_manifest_laws.json" not in lines
    assert "data/riigiteataja/**/*.xml" in lines


# --- release asset ------------------------------------------------------------


def _xml_tree(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    krr = repo / "krr_outputs"
    xml_dir = repo / "data" / "riigiteataja"
    (xml_dir / "maarus").mkdir(parents=True)
    krr.mkdir(parents=True)
    (xml_dir / "x__tid500.xml").write_text(_xml("2"), encoding="utf-8")
    (xml_dir / "maarus" / "reg_9.xml").write_text(_xml("9"), encoding="utf-8")
    digest = hashlib.sha256((xml_dir / "x__tid500.xml").read_bytes()).hexdigest()
    (krr / "fetch_content_hashes.json").write_text(json.dumps({
        "x": {"sha256": digest, "cacheFile": "data/riigiteataja/x__tid500.xml", "kehtiv": "2026-10-09"},
    }), encoding="utf-8")
    (krr / "generation_manifest_laws.json").write_text(
        json.dumps({"source": {"kehtiv": "2026-10-09"}}), encoding="utf-8"
    )
    return repo, krr


def test_rt_xml_archive_is_dated_deterministic_and_complete(tmp_path) -> None:
    repo, krr = _xml_tree(tmp_path)
    out1, out2 = tmp_path / "r1", tmp_path / "r2"
    out1.mkdir()
    out2.mkdir()
    name, stats = bra.build_rt_xml_archive(krr, repo, out1)
    bra.build_rt_xml_archive(krr, repo, out2)
    assert name == "rt_xml_2026-10-09.tar.gz"
    assert stats == {"kehtiv": "2026-10-09", "files": 2,
                     "uncompressedBytes": stats["uncompressedBytes"], "attested": 1}
    assert (out1 / name).read_bytes() == (out2 / name).read_bytes()
    with tarfile.open(fileobj=io.BytesIO(gzip.decompress((out1 / name).read_bytes()))) as tar:
        members = tar.getnames()
        assert {m.mtime for m in tar.getmembers()} == {0}
    assert members == [
        "krr_outputs/fetch_content_hashes.json",
        "data/riigiteataja/maarus/reg_9.xml",
        "data/riigiteataja/x__tid500.xml",
    ]


def test_rt_xml_archive_refuses_a_broken_attestation(tmp_path) -> None:
    repo, krr = _xml_tree(tmp_path)
    (repo / "data" / "riigiteataja" / "x__tid500.xml").write_text(_xml("3"), encoding="utf-8")
    with pytest.raises(bra.ReleaseAssetError, match="do not match"):
        bra.build_rt_xml_archive(krr, repo, tmp_path)


def test_rt_xml_archive_is_skipped_without_cached_xml(tmp_path) -> None:
    repo = tmp_path / "repo"
    (repo / "krr_outputs").mkdir(parents=True)
    assert bra.build_rt_xml_archive(repo / "krr_outputs", repo, tmp_path) is None


# --- legacy RT II texts carry a document UUID as globaalID ------------------

LEGACY_UUID = "0e73a759-a614-450b-a625-7a963d785e78"


def test_legacy_uuid_globaalid_is_served_from_cache(tmp_path, monkeypatch) -> None:
    # Five 2010 treaty acts: the search row says 204112010005, the XML a UUID.
    monkeypatch.setattr(gal, "DATA_DIR", tmp_path)
    (tmp_path / "x__tid500.xml").write_text(_xml(LEGACY_UUID), encoding="utf-8")
    monkeypatch.setattr(gal, "common_fetch_xml", lambda *a, **k: pytest.fail("network"))
    root = gal.fetch_xml("/akt/204112010005.xml", "x", tid="500", gid="204112010005")
    assert root is not None
    assert gal.xml_root_global_id(root) is None


def test_legacy_uuid_globaalid_is_not_stamped_as_the_act_id(tmp_path, monkeypatch) -> None:
    root = ET.fromstring(_xml(LEGACY_UUID).split("-->", 1)[1])
    doc = gal.generate_law_jsonld(
        "X", "x", root, "XX", "/akt/204112010005.xml", kehtiv="2026-10-09",
        global_id="204112010005", allocator=gal.PrefixAllocator(registry={}),
    )
    act = doc["@graph"][0]
    assert act["estleg:globalId"] == "204112010005"
    assert act["eli:id_local"] == "204112010005"
    assert act["dcterms:source"] == {"@id": "https://www.riigiteataja.ee/akt/204112010005"}


def test_cache_only_never_fetches(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(gal, "DATA_DIR", tmp_path)
    monkeypatch.setattr(gal, "CACHE_ONLY", True)
    monkeypatch.setattr(gal, "common_fetch_xml", lambda *a, **k: pytest.fail("network"))
    (tmp_path / "x__tid500.xml").write_text(_xml("1"), encoding="utf-8")
    assert gal.fetch_xml("/akt/2.xml", "x", tid="500", gid="2") is None
    assert gal.fetch_xml("/akt/1.xml", "x", tid="500", gid="1") is not None

"""Contract tests for the auditable retrieval chunk envelope (#723).

A chunk copied into a RAG store must be auditable months later from the record
alone: which build (``ontology_version``), which as-of date
(``evaluation_date``), which act snapshot (``act_iri`` + ``kehtiv``), which RT
redaction and § (``rt_url``), in what language, under a stable ``chunk_id``.
These tests pin that contract, the optional ``--max-chars`` splitting, the
provision-level ``rt_url`` fallback chain, the manifest, ``llms.txt`` (no links
to unpublished artifacts), the freshness of the committed sample, and that the
#705 release step still ships ``chunks.jsonl.gz`` through this generator.
"""

from __future__ import annotations

import gzip
import json
import re
from pathlib import Path

import pytest

from estleg import build_release_assets as bra
from estleg import generate_retrieval_projection as grp
from estleg.estleg_common import ONTOLOGY_VERSION
from tests.test_generate_retrieval_projection import (
    EVAL_DATE,
    REQUIRED_CHUNK_KEYS,
    RT_BASE,
    _build_corpus,
    _read_chunks,
    _write_json,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
COMMITTED_DIR = REPO_ROOT / "krr_outputs" / "retrieval"

ORDERED_KEYS = [
    "chunk_id",
    "provision_iri",
    "act_iri",
    "redaction_id",
    "paragraph",
    "act_title",
    "abbrev",
    "rt_url",
    "valid_from",
    "valid_to",
    "in_force",
    "kehtiv",
    "evaluation_date",
    "ontology_version",
    "language",
    "part_index",
    "part_count",
    "text",
]

REDACTION_URL = "https://www.riigiteataja.ee/akt/2000"


def _corpus_with_redaction_url(krr: Path, *, long_text: str | None = None) -> None:
    """The #523 fixture, plus ``estleg:rtUrl`` on the current TL_Par_1 version
    (as the real sidecars carry it) and optionally a long version text."""
    _build_corpus(krr)
    path = krr / "provision_versions" / "testlaw.jsonld"
    doc = json.loads(path.read_text(encoding="utf-8"))
    for node in doc["@graph"]:
        if node["@id"] == "estleg:TL_Par_1_v2000":
            node["estleg:rtUrl"] = REDACTION_URL
            if long_text is not None:
                node["estleg:versionText"] = long_text
    _write_json(path, doc)


def _run(tmp_path: Path, **kwargs) -> tuple[Path, list[dict], dict]:
    krr = tmp_path / "krr_outputs"
    if not (krr / "INDEX.json").exists():
        _corpus_with_redaction_url(krr)
    out = tmp_path / "retrieval"
    stats = grp.generate(krr_dir=krr, out_dir=out, eval_date=EVAL_DATE, **kwargs)
    return out, _read_chunks(out), stats


# ---------------------------------------------------------------------------
# Record contract
# ---------------------------------------------------------------------------
def test_chunk_keys_contract_is_pinned_in_order():
    assert list(grp.CHUNK_KEYS) == ORDERED_KEYS
    assert set(grp.CHUNK_KEYS) == REQUIRED_CHUNK_KEYS
    assert set(grp.CHUNK_FIELD_DOCS) == set(grp.CHUNK_KEYS)
    assert re.fullmatch(r"\d+\.\d+\.\d+", grp.CHUNK_SCHEMA_VERSION)


def test_every_record_carries_the_envelope(tmp_path):
    out, chunks, _ = _run(tmp_path)
    assert chunks
    lines = (out / "chunks.jsonl").read_text(encoding="utf-8").splitlines()
    for line, chunk in zip(lines, chunks, strict=True):
        # Key ORDER is part of the byte-stable contract.
        assert list(json.loads(line)) == ORDERED_KEYS
        assert chunk["ontology_version"] == ONTOLOGY_VERSION
        assert chunk["evaluation_date"] == EVAL_DATE
        assert chunk["act_iri"] == "estleg:TL_Map"
        assert chunk["kehtiv"] == "2026-05-24"
        assert chunk["language"] == "et"
        assert (chunk["part_index"], chunk["part_count"]) == (1, 1)
        assert chunk["chunk_id"].startswith(chunk["provision_iri"] + "#")


def test_chunk_ids_are_stable_and_unique(tmp_path):
    _, chunks, _ = _run(tmp_path)
    ids = {c["chunk_id"] for c in chunks}
    assert ids == {
        "estleg:TL_Par_1#1000",
        "estleg:TL_Par_1#2000",
        "estleg:TL_Par_2#consolidated",
    }
    assert len(ids) == len(chunks)
    # Independent of the record's position: adding a law that sorts first
    # must not change any existing id.
    krr = tmp_path / "krr_outputs"
    index = json.loads((krr / "INDEX.json").read_text(encoding="utf-8"))
    index["laws"].insert(0, {"name": "aaa_law", "files": ["aaa_law_peep.json"]})
    _write_json(krr / "INDEX.json", index)
    _write_json(
        krr / "aaa_law_peep.json",
        {
            "@graph": [
                {"@id": "estleg:AAA_Map", "@type": ["owl:Ontology", "estleg:Act"],
                 "dcterms:title": "Aaa seadus"},
                {"@id": "estleg:AAA_Par_1", "@type": ["estleg:LegalProvision_aaa"],
                 "estleg:paragrahv": "§ 1.", "estleg:legalText": "Tekst."},
            ]
        },
    )
    _, chunks2, _ = _run(tmp_path)
    assert ids <= {c["chunk_id"] for c in chunks2}


def test_make_chunk_id_shapes():
    assert grp.make_chunk_id("estleg:X_Par_1", "123") == "estleg:X_Par_1#123"
    assert grp.make_chunk_id("estleg:X_Par_1", None) == "estleg:X_Par_1#consolidated"
    assert grp.make_chunk_id("estleg:X_Par_1", "123", 2, 3) == "estleg:X_Par_1#123#part_2"
    # A single part never carries a suffix.
    assert grp.make_chunk_id("estleg:X_Par_1", "123", 1, 1) == "estleg:X_Par_1#123"


def test_bilingual_act_title_resolves_to_estonian():
    root = {
        "dcterms:title": [
            {"@value": "Alkoholiseadus", "@language": "et"},
            {"@value": "Alcohol Act", "@language": "en"},
        ]
    }
    assert grp.act_title(root) == "Alkoholiseadus"
    # No Estonian value: fall back to what exists rather than an empty title.
    assert grp.act_title({"dcterms:title": {"@value": "Alcohol Act", "@language": "en"}}) == "Alcohol Act"


# ---------------------------------------------------------------------------
# Provision-level rt_url
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("label", "anchor"),
    [
        ("§ 5.", "para5"),
        ("§ 13¹.", "para13b1"),
        ("§ 76².", "para76b2"),
        ("§ 1¹⁰.", "para1b10"),
        ("§ 12^3", "para12b3"),
        ("§ 120. Mõisted", "para120"),
        ("", None),
        ("Lisa 1", None),
        (None, None),
    ],
)
def test_paragraph_anchor(label, anchor):
    assert grp.paragraph_anchor(label) == anchor


def test_provision_rt_url_fallback_chain():
    act = f"{RT_BASE}123456"
    # Redaction URL wins and gets the § anchor.
    assert grp.provision_rt_url(REDACTION_URL, act, "§ 3¹.") == f"{REDACTION_URL}#para3b1"
    # No (or a non-http) redaction URL: act browse URL + anchor.
    assert grp.provision_rt_url(None, act, "§ 3.") == f"{act}#para3"
    assert grp.provision_rt_url("606759", act, "§ 3.") == f"{act}#para3"
    # Unparseable § label: bare URL, never a bogus fragment.
    assert grp.provision_rt_url(REDACTION_URL, act, "Lisa") == REDACTION_URL
    # A pre-existing fragment is replaced, not stacked.
    assert grp.provision_rt_url(f"{REDACTION_URL}#x", act, "§ 1.") == f"{REDACTION_URL}#para1"
    assert grp.provision_rt_url(None, None, "§ 1.") is None


def test_rt_url_is_provision_level_in_the_projection(tmp_path):
    _, chunks, _ = _run(tmp_path)
    by_id = {c["chunk_id"]: c for c in chunks}
    # Version with estleg:rtUrl -> the redaction page, § anchored.
    assert by_id["estleg:TL_Par_1#2000"]["rt_url"] == f"{REDACTION_URL}#para1"
    # Version without estleg:rtUrl -> the act page (.xml stripped), § anchored.
    assert by_id["estleg:TL_Par_1#1000"]["rt_url"] == f"{RT_BASE}123456#para1"
    # Consolidated provision -> act page, § anchored.
    assert by_id["estleg:TL_Par_2#consolidated"]["rt_url"] == f"{RT_BASE}123456#para2"


def test_version_record_reads_rt_url_in_both_jsonld_shapes():
    base = {
        "@type": ["estleg:ProvisionVersion"],
        "estleg:versionOf": {"@id": "estleg:P"},
    }
    _, plain = grp._version_record({**base, "estleg:rtUrl": f"{REDACTION_URL}.xml"})
    _, ref = grp._version_record({**base, "estleg:rtUrl": {"@id": REDACTION_URL}})
    _, none = grp._version_record(base)
    assert plain["rt_url"] == REDACTION_URL
    assert ref["rt_url"] == REDACTION_URL
    assert none["rt_url"] is None


# ---------------------------------------------------------------------------
# Splitting
# ---------------------------------------------------------------------------
def _norm(text: str) -> str:
    return " ".join(text.split())


def test_split_text_no_op_cases():
    assert grp.split_text("Lühike.", None) == ["Lühike."]
    assert grp.split_text("Lühike.", 500) == ["Lühike."]
    text = "  ruum   jääb  "
    assert grp.split_text(text, 500) == [text]  # untouched, not stripped


def test_split_text_on_sentence_boundaries():
    sentence = "Isik peab esitama taotluse nr. 5 vastavalt § 5. lõikele 2 ettenähtud korras. "
    text = (sentence * 12).strip()
    parts = grp.split_text(text, 250)
    assert len(parts) > 1
    assert all(len(p) <= 250 for p in parts)
    assert _norm(" ".join(parts)) == _norm(text)
    # Never cut after the ordinal/abbreviation full stops, only after "korras."
    assert all(p.endswith("korras.") for p in parts)


def test_split_text_cuts_before_subsection_marker():
    text = "(1) " + "a " * 120 + "(2) " + "b " * 120
    parts = grp.split_text(text.strip(), 300)
    assert parts[0].startswith("(1)")
    assert parts[1].startswith("(2)")


def test_split_text_hard_splits_one_overlong_sentence():
    text = " ".join(["sõna"] * 400)  # ~2,000 chars, no punctuation
    parts = grp.split_text(text, 300)
    assert all(0 < len(p) <= 300 for p in parts)
    assert _norm(" ".join(parts)) == _norm(text)


def test_generate_splits_long_text_with_part_fields(tmp_path):
    long_text = " ".join(f"Lause number {i} kehtestab nõude." for i in range(200))
    _corpus_with_redaction_url(tmp_path / "krr_outputs", long_text=long_text)
    _, unsplit, unsplit_stats = _run(tmp_path)
    out, chunks, stats = _run(tmp_path, max_chars=500)

    parts = [c for c in chunks if c["redaction_id"] == "2000"]
    assert len(parts) > 1
    count = len(parts)
    assert [c["part_index"] for c in parts] == list(range(1, count + 1))
    assert {c["part_count"] for c in parts} == {count}
    assert [c["chunk_id"] for c in parts] == [
        f"estleg:TL_Par_1#2000#part_{k}" for k in range(1, count + 1)
    ]
    assert all(len(c["text"]) <= 500 for c in parts)
    assert _norm(" ".join(c["text"] for c in parts)) == _norm(long_text)
    # Envelope is copied to every part.
    assert {c["rt_url"] for c in parts} == {f"{REDACTION_URL}#para1"}
    # Short records are untouched.
    assert [c for c in chunks if c["redaction_id"] != "2000"] == [
        c for c in unsplit if c["redaction_id"] != "2000"
    ]
    # Unit counts are split-invariant; total_chunks counts records.
    assert stats["version_records"] == unsplit_stats["version_records"]
    assert stats["split_provision_versions"] == 1
    assert stats["total_chunks"] == unsplit_stats["total_chunks"] + count - 1
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["max_chars"] == 500


def test_max_chars_below_minimum_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="--max-chars"):
        _run(tmp_path, max_chars=grp.MIN_MAX_CHARS - 1)


def test_split_projection_is_byte_identical_across_runs(tmp_path):
    long_text = " ".join(f"Lause {i} on siin." for i in range(300))
    _corpus_with_redaction_url(tmp_path / "krr_outputs", long_text=long_text)
    out, _, _ = _run(tmp_path, max_chars=400)
    first = {p.name: p.read_bytes() for p in out.iterdir() if p.is_file()}
    out, _, _ = _run(tmp_path, max_chars=400)
    second = {p.name: p.read_bytes() for p in out.iterdir() if p.is_file()}
    assert first == second


# ---------------------------------------------------------------------------
# Manifest + llms.txt
# ---------------------------------------------------------------------------
def test_manifest_matches_code(tmp_path, monkeypatch):
    monkeypatch.setenv("ESTLEG_GENERATOR_COMMIT", "deadbeef")
    out, chunks, _ = _run(tmp_path)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["ontology_version"] == ONTOLOGY_VERSION
    assert manifest["evaluation_date"] == EVAL_DATE
    assert manifest["chunk_schema_version"] == grp.CHUNK_SCHEMA_VERSION
    assert manifest["chunk_keys"] == ORDERED_KEYS
    assert manifest["language"] == "et"
    assert manifest["generator_commit"] == "deadbeef"
    assert manifest["max_chars"] is None
    assert manifest["chunks_asset"] == bra.CHUNKS_ASSET
    assert manifest["chunks_asset_url"].endswith(
        f"/releases/download/v{ONTOLOGY_VERSION}/{bra.CHUNKS_ASSET}"
    )
    assert manifest["total_chunks"] == len(chunks)
    assert manifest["total_chunks"] == (
        manifest["version_records"] + manifest["consolidated_records"]
    )


def test_generator_commit_falls_back_to_git(monkeypatch):
    monkeypatch.delenv("ESTLEG_GENERATOR_COMMIT", raising=False)
    sha = grp.generator_commit()
    assert sha is None or re.fullmatch(r"[0-9a-f]{40}", sha)


_LINK_RE = re.compile(r"\]\(([^)]+)\)")
_UNPUBLISHED = ("chunks.jsonl", "outlines/", "context_packs/")


def _assert_llms_links_published(text: str, base: Path) -> None:
    for target in _LINK_RE.findall(text):
        if target.startswith("http"):
            continue
        assert target not in _UNPUBLISHED, f"llms.txt links unpublished {target}"
        assert (base / target).exists(), f"llms.txt relative link 404s: {target}"


def test_rendered_llms_txt_links_only_published_artifacts(tmp_path):
    out, _, _ = _run(tmp_path)
    text = (out / "llms.txt").read_text(encoding="utf-8")
    assert grp.chunks_asset_url() in text
    assert "chunk_id" in text and "evaluation_date" in text
    for target in _LINK_RE.findall(text):
        assert target not in _UNPUBLISHED


# ---------------------------------------------------------------------------
# Committed stubs are current (freshness gate)
# ---------------------------------------------------------------------------
def _committed_manifest() -> dict:
    return json.loads((COMMITTED_DIR / "manifest.json").read_text(encoding="utf-8"))


def test_committed_manifest_matches_code():
    manifest = _committed_manifest()
    assert manifest["ontology_version"] == ONTOLOGY_VERSION
    assert manifest["chunk_schema_version"] == grp.CHUNK_SCHEMA_VERSION
    assert manifest["chunk_keys"] == ORDERED_KEYS
    assert manifest["chunks_asset_url"] == grp.chunks_asset_url()
    assert manifest["total_chunks"] >= (
        manifest["version_records"] + manifest["consolidated_records"]
    )


def test_committed_sample_carries_the_current_envelope():
    manifest = _committed_manifest()
    rows = [
        json.loads(line)
        for line in (COMMITTED_DIR / "chunks.sample.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line
    ]
    assert len(rows) == grp.SAMPLE_CHUNKS_DEFAULT
    assert len({r["chunk_id"] for r in rows}) == len(rows)
    for row in rows:
        assert list(row) == ORDERED_KEYS
        assert row["ontology_version"] == ONTOLOGY_VERSION
        assert row["evaluation_date"] == manifest["evaluation_date"]
        assert row["language"] == "et"
        assert row["act_iri"] and row["act_iri"].startswith("estleg:")
        if row["rt_url"] is not None:
            assert row["rt_url"].startswith("https://www.riigiteataja.ee/akt/")


def test_committed_llms_txt_and_readme_are_current():
    llms = (COMMITTED_DIR / "llms.txt").read_text(encoding="utf-8")
    _assert_llms_links_published(llms, COMMITTED_DIR)
    assert grp.chunks_asset_url() in llms
    assert f"ontology {ONTOLOGY_VERSION}" in llms
    readme = (COMMITTED_DIR / "README.md").read_text(encoding="utf-8")
    assert f"schema {grp.CHUNK_SCHEMA_VERSION}" in readme
    assert f"ontology {ONTOLOGY_VERSION}" in readme


# ---------------------------------------------------------------------------
# #705 release step still ships chunks.jsonl.gz through this generator
# ---------------------------------------------------------------------------
def test_release_asset_name_agrees():
    assert bra.CHUNKS_ASSET == grp.CHUNKS_ASSET_NAME


def test_release_build_chunks_ships_the_envelope(tmp_path):
    krr = tmp_path / "krr_outputs"
    _corpus_with_redaction_url(krr)
    dest = tmp_path / "release" / bra.CHUNKS_ASSET
    dest.parent.mkdir(parents=True)
    stats = bra.build_chunks(krr, tmp_path / "release" / "retrieval", dest)
    rows = [
        json.loads(line)
        for line in gzip.decompress(dest.read_bytes()).decode("utf-8").splitlines()
    ]
    assert len(rows) == stats["total_chunks"] == 3
    for row in rows:
        assert list(row) == ORDERED_KEYS
        assert row["ontology_version"] == ONTOLOGY_VERSION

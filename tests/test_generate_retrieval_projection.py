"""Tests for the retrieval-projection generator (ticket #523).

Two tiers:

* **Synthetic** (default): pure-helper unit tests plus an end-to-end run over a
  tiny in-memory corpus in ``tmp_path`` — exercises the 3-node assembly, the
  in_force/valid_from selection, the <=120-char outline summary, the
  context-pack bundling, and byte-for-byte determinism across two runs. No real
  corpus or LFS materialisation required.
* **Corpus** (``-m corpus``): a smoke run over the real ``krr_outputs/`` for a
  known law, guarded against un-materialised LFS pointers and a missing corpus.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import generate_retrieval_projection as grp  # noqa: E402

EVAL_DATE = "2026-06-01"
RT_BASE = "https://www.riigiteataja.ee/akt/"

REQUIRED_CHUNK_KEYS = {
    "provision_iri",
    "redaction_id",
    "paragraph",
    "act_title",
    "abbrev",
    "rt_url",
    "valid_from",
    "valid_to",
    "in_force",
    "text",
}


# ---------------------------------------------------------------------------
# Pure-helper unit tests
# ---------------------------------------------------------------------------
def test_strip_xml_normalises_rt_url():
    assert grp._strip_xml(f"{RT_BASE}123.xml") == f"{RT_BASE}123"
    assert grp._strip_xml(f"{RT_BASE}123") == f"{RT_BASE}123"
    assert grp._strip_xml(None) is None


def test_derive_abbrev_from_act_id():
    assert grp.derive_abbrev("estleg:AS_Map_2026") == "AS"
    assert grp.derive_abbrev("estleg:AÕS_Osa1_1_94") == "AÕS"
    assert grp.derive_abbrev("estleg:KrMS_ProcedureMap_2026") == "KrMS"
    assert grp.derive_abbrev(None) is None


def test_act_rt_url_prefers_dcterms_source():
    root = {
        "dcterms:source": {"@id": f"{RT_BASE}109012025020.xml"},
        "owl:sameAs": {"@id": f"{RT_BASE}999.xml"},
    }
    assert grp.act_rt_url(root) == f"{RT_BASE}109012025020"


def test_act_title_fallback_chain():
    assert grp.act_title({"dcterms:title": {"@value": "Alkoholiseadus"}}) == (
        "Alkoholiseadus"
    )
    assert grp.act_title({"dc:source": "Foo"}) == "Foo"
    assert grp.act_title({"rdfs:label": "Bar"}) == "Bar"
    assert grp.act_title(None) == ""


def test_compute_in_force_window():
    # current (open-ended) version is in force
    assert grp.compute_in_force("2011-01-01", None, EVAL_DATE) is True
    # superseded version is not
    assert grp.compute_in_force("2002-01-01", "2010-12-31", EVAL_DATE) is False
    # not-yet-effective future version is not
    assert grp.compute_in_force("2030-01-01", None, EVAL_DATE) is False
    # window straddling the eval date is in force
    assert grp.compute_in_force("2020-01-01", "2030-01-01", EVAL_DATE) is True


def test_truncate_summary_never_exceeds_120():
    long = "x" * 400
    out = grp.truncate_summary(long)
    assert len(out) <= grp.SUMMARY_MAX
    assert out.endswith("...")
    short = "a short summary"
    assert grp.truncate_summary(short) == short
    # whitespace is collapsed to a single line
    assert grp.truncate_summary("a\n  b   c") == "a b c"


def test_is_provision_excludes_subsections():
    assert grp.is_provision(
        {"@type": ["owl:NamedIndividual", "estleg:LegalProvision_x"]}
    )
    assert not grp.is_provision(
        {"@type": ["owl:NamedIndividual", "estleg:Subsection"]}
    )
    assert not grp.is_provision({"@type": ["estleg:Chapter"]})


# ---------------------------------------------------------------------------
# Synthetic corpus fixture
# ---------------------------------------------------------------------------
def _write_json(path: Path, doc) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def _build_corpus(krr: Path) -> None:
    """Stage a tiny but representative corpus under ``krr``.

    One law (``testlaw``) with: an act root, a chapter, a versioned provision
    (TL_Par_1: a superseded + a current version), and a version-less provision
    (TL_Par_2) carrying references/referencedBy/interpretedBy. Plus an
    amendments overlay, a sanctions overlay, and a court decision the
    interpretedBy edge points at.
    """
    _write_json(
        krr / "INDEX.json",
        {"laws": [{"name": "testlaw", "files": ["testlaw_peep.json"]}]},
    )
    long_summary = "Reguleerimisala. " + ("paragrahvi tekst " * 40)
    _write_json(
        krr / "testlaw_peep.json",
        {
            "@context": {"estleg": grp.NS},
            "@graph": [
                {
                    "@id": "estleg:TL_Map_2026",
                    "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
                    "dcterms:title": {"@value": "Testseadus", "@language": "et"},
                    "dc:source": "Testseadus",
                    "dcterms:source": {"@id": f"{RT_BASE}123456.xml"},
                    "owl:sameAs": {"@id": f"{RT_BASE}123456.xml"},
                    "estleg:kehtiv": {"@value": "2026-05-24", "@type": "xsd:date"},
                    "estleg:temporalStatus": "inForce",
                },
                {
                    "@id": "estleg:Chapter_TL_1",
                    "@type": ["owl:NamedIndividual", "estleg:Chapter"],
                    "rdfs:label": "1. peatükk – ÜLDSÄTTED",
                },
                {
                    "@id": "estleg:TL_Par_1",
                    "@type": [
                        "owl:NamedIndividual",
                        "estleg:LegalProvision_testlaw",
                    ],
                    "estleg:paragrahv": "§ 1.",
                    "rdfs:label": "§ 1. Seaduse reguleerimisala",
                    "estleg:summary": long_summary,
                    "estleg:legalText": "Konsolideeritud tekst paragrahv 1.",
                    "estleg:isPartOf": {"@id": "estleg:Chapter_TL_1"},
                    "estleg:hasVersion": [
                        {"@id": "estleg:TL_Par_1_v1000"},
                        {"@id": "estleg:TL_Par_1_v2000"},
                    ],
                },
                {
                    "@id": "estleg:TL_Par_2",
                    "@type": [
                        "owl:NamedIndividual",
                        "estleg:LegalProvision_testlaw",
                    ],
                    "estleg:paragrahv": "§ 2.",
                    "rdfs:label": "§ 2. Mõisted",
                    "estleg:summary": "Lühike kokkuvõte.",
                    "estleg:legalText": "Paragrahv 2 konsolideeritud tekst.",
                    "estleg:isPartOf": {"@id": "estleg:Chapter_TL_1"},
                    "estleg:references": [{"@id": "estleg:TL_Par_1"}],
                    "estleg:referencedBy": [{"@id": "estleg:TL_Par_1"}],
                    "estleg:interpretedBy": [{"@id": "estleg:RK_1_20_1_5"}],
                },
                {
                    "@id": "estleg:TL_Par_2_Lg_1",
                    "@type": ["owl:NamedIndividual", "estleg:Subsection"],
                    "estleg:legalText": "Subsection text — must not become a chunk.",
                },
            ],
        },
    )
    _write_json(
        krr / "provision_versions" / "testlaw.jsonld",
        {
            "@context": {"estleg": grp.NS},
            "@graph": [
                {"@id": "estleg:ProvisionVersions_testlaw_Map", "@type": ["owl:Ontology"]},
                {
                    "@id": "estleg:TL_Par_1_v1000",
                    "@type": ["owl:NamedIndividual", "estleg:ProvisionVersion"],
                    "estleg:versionOf": {"@id": "estleg:TL_Par_1"},
                    "estleg:versionRedactionId": "1000",
                    "estleg:versionValidFrom": {"@value": "2002-01-01", "@type": "xsd:date"},
                    "estleg:versionValidTo": {"@value": "2010-12-31", "@type": "xsd:date"},
                    "estleg:versionText": "Vana redaktsioon paragrahv 1.",
                },
                {
                    "@id": "estleg:TL_Par_1_v2000",
                    "@type": ["owl:NamedIndividual", "estleg:ProvisionVersion"],
                    "estleg:versionOf": {"@id": "estleg:TL_Par_1"},
                    "estleg:versionRedactionId": "2000",
                    "estleg:versionValidFrom": {"@value": "2011-01-01", "@type": "xsd:date"},
                    "estleg:versionText": "Kehtiv redaktsioon paragrahv 1.",
                },
            ],
        },
    )
    _write_json(
        krr / "amendments" / "amendments_testlaw.json",
        {
            "@graph": [
                {"@id": "estleg:AmendmentChain_TL", "@type": ["owl:Ontology"]},
                {
                    "@id": "estleg:Amendment_testlaw_t1_1",
                    "@type": ["owl:NamedIndividual", "estleg:ProposedAmendment"],
                    "rdfs:label": "Muudatus 1",
                },
            ]
        },
    )
    _write_json(
        krr / "sanctions" / "sanctions_testlaw.json",
        {
            "@graph": [
                {"@id": "estleg:Sanctions_testlaw_Map", "@type": ["owl:Ontology"]},
                {
                    "@id": "estleg:Sanction_testlaw_1",
                    "@type": ["owl:NamedIndividual", "estleg:Sanction"],
                    "rdfs:label": "Rahatrahv",
                },
            ]
        },
    )
    _write_json(
        krr / "riigikohus" / "riigikohus_2020_peep.json",
        {
            "@graph": [
                {"@id": "estleg:Riigikohus_2020_Map", "@type": ["owl:Ontology"]},
                {
                    "@id": "estleg:RK_1_20_1_5",
                    "@type": ["owl:NamedIndividual", "estleg:CourtDecision"],
                    "estleg:caseNumber": "1-20-1/5",
                    "estleg:decisionLink": {
                        "@value": "https://www.riigikohus.ee/et/lahendid/?asjaNr=1-20-1/5",
                        "@type": "xsd:anyURI",
                    },
                    "estleg:decisionDate": {"@value": "2020-03-01", "@type": "xsd:date"},
                },
            ]
        },
    )


def _read_chunks(out_dir: Path) -> list[dict]:
    text = (out_dir / "chunks.jsonl").read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line]


def _act_root(map_id: str, title: str, rt_id: str) -> dict:
    return {
        "@id": f"estleg:{map_id}",
        "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
        "dcterms:title": {"@value": title, "@language": "et"},
        "dcterms:source": {"@id": f"{RT_BASE}{rt_id}.xml"},
        "estleg:kehtiv": {"@value": "2026-05-24", "@type": "xsd:date"},
        "estleg:temporalStatus": "inForce",
    }


def _provision(iri: str, par: str) -> dict:
    return {
        "@id": f"estleg:{iri}",
        "@type": ["owl:NamedIndividual", "estleg:LegalProvision_multilaw"],
        "estleg:paragrahv": par,
        "rdfs:label": f"{par} pealkiri",
        "estleg:legalText": f"{iri} tekst.",
    }


def _build_multipart_corpus(krr: Path) -> None:
    """Stage an osa-split code whose overlays are keyed by FILE STEM.

    ``multilaw`` has two peep files; its sanctions live in
    ``sanctions_multilaw_osa1.json`` (1) + ``sanctions_multilaw_osa2.json`` (2)
    and amendments in the matching per-stem files — there is deliberately NO
    ``sanctions_multilaw.json``, so a law-name-only lookup returns 0 and a
    first-resolving lookup returns only osa1.
    """
    _write_json(
        krr / "INDEX.json",
        {
            "laws": [
                {
                    "name": "multilaw",
                    "files": ["multilaw_osa1_peep.json", "multilaw_osa2_peep.json"],
                }
            ]
        },
    )
    _write_json(
        krr / "multilaw_osa1_peep.json",
        {
            "@context": {"estleg": grp.NS},
            "@graph": [
                _act_root("ML_Map_2026", "Multiseadus", "111"),
                _provision("ML_Par_1", "§ 1."),
            ],
        },
    )
    _write_json(
        krr / "multilaw_osa2_peep.json",
        {
            "@context": {"estleg": grp.NS},
            "@graph": [
                _act_root("ML_Map_2026", "Multiseadus", "111"),
                _provision("ML_Par_2", "§ 2."),
            ],
        },
    )
    for osa, ids in (("osa1", ["S1"]), ("osa2", ["S2", "S3"])):
        _write_json(
            krr / "sanctions" / f"sanctions_multilaw_{osa}.json",
            {
                "@graph": [
                    {
                        "@id": f"estleg:Sanctions_multilaw_{osa}_Map",
                        "@type": ["owl:Ontology"],
                    },
                    *[
                        {
                            "@id": f"estleg:Sanction_multilaw_{sid}",
                            "@type": ["owl:NamedIndividual", "estleg:Sanction"],
                            "rdfs:label": f"Sanktsioon {sid}",
                        }
                        for sid in ids
                    ],
                ]
            },
        )
    for osa, aid in (("osa1", "A1"), ("osa2", "A2")):
        _write_json(
            krr / "amendments" / f"amendments_multilaw_{osa}.json",
            {
                "@graph": [
                    {
                        "@id": f"estleg:AmendmentChain_ML_{osa}",
                        "@type": ["owl:Ontology"],
                    },
                    {
                        "@id": f"estleg:Amendment_multilaw_{aid}",
                        "@type": ["owl:NamedIndividual", "estleg:ProposedAmendment"],
                        "rdfs:label": f"Muudatus {aid}",
                    },
                ]
            },
        )


# ---------------------------------------------------------------------------
# End-to-end synthetic tests
# ---------------------------------------------------------------------------
def test_chunk_record_assembly_and_in_force(tmp_path):
    krr = tmp_path / "krr_outputs"
    _build_corpus(krr)
    out = krr / "retrieval"
    grp.generate(krr_dir=krr, out_dir=out, eval_date=EVAL_DATE)

    chunks = _read_chunks(out)
    by_key = {(c["provision_iri"], c["redaction_id"]): c for c in chunks}

    # Subsection node must NOT produce a chunk.
    assert all(c["provision_iri"] != "estleg:TL_Par_2_Lg_1" for c in chunks)
    # One record per version for the versioned provision + one consolidated
    # record for the version-less provision.
    assert ("estleg:TL_Par_1", "1000") in by_key
    assert ("estleg:TL_Par_1", "2000") in by_key
    assert ("estleg:TL_Par_2", None) in by_key

    for chunk in chunks:
        assert set(chunk) == REQUIRED_CHUNK_KEYS
        assert chunk["rt_url"] == f"{RT_BASE}123456"
        assert chunk["rt_url"].startswith(RT_BASE)
        assert chunk["act_title"] == "Testseadus"
        assert chunk["abbrev"] == "TL"
        assert chunk["paragraph"].startswith("§")

    # in_force / valid_from selection.
    superseded = by_key[("estleg:TL_Par_1", "1000")]
    current = by_key[("estleg:TL_Par_1", "2000")]
    consolidated = by_key[("estleg:TL_Par_2", None)]
    assert superseded["in_force"] is False
    assert superseded["valid_to"] == "2010-12-31"
    assert current["in_force"] is True
    assert current["valid_from"] == "2011-01-01"
    assert current["valid_to"] is None
    assert current["text"] == "Kehtiv redaktsioon paragrahv 1."
    # version-less provision falls back to the act consolidation date.
    assert consolidated["valid_from"] == "2026-05-24"
    assert consolidated["redaction_id"] is None
    assert consolidated["in_force"] is True
    assert consolidated["text"] == "Paragrahv 2 konsolideeritud tekst."


def test_outline_summary_and_chapter(tmp_path):
    krr = tmp_path / "krr_outputs"
    _build_corpus(krr)
    out = krr / "retrieval"
    grp.generate(krr_dir=krr, out_dir=out, eval_date=EVAL_DATE)

    outline = json.loads((out / "outlines" / "testlaw.json").read_text("utf-8"))
    assert outline["abbrev"] == "TL"
    assert outline["provision_count"] == 2
    entries = {e["paragraph"]: e for e in outline["provisions"]}
    par1 = entries["§ 1."]
    assert len(par1["summary"]) <= grp.SUMMARY_MAX
    assert par1["summary"].endswith("...")
    assert par1["chapter"] == "1. peatükk – ÜLDSÄTTED"
    assert par1["has_versions"] is True
    assert entries["§ 2."]["has_versions"] is False


def test_context_pack_bundles_all_sections(tmp_path):
    krr = tmp_path / "krr_outputs"
    _build_corpus(krr)
    out = krr / "retrieval"
    grp.generate(krr_dir=krr, out_dir=out, eval_date=EVAL_DATE)

    pack = json.loads((out / "context_packs" / "testlaw.json").read_text("utf-8"))
    for section in ("provisions", "versions", "amendments", "sanctions", "cases"):
        assert section in pack
    assert pack["counts"]["provisions"] == 2
    assert pack["counts"]["versions"] == 2
    assert pack["counts"]["amendments"] == 1
    assert pack["counts"]["sanctions"] == 1
    assert pack["counts"]["cases"] == 1

    # references / referencedBy / interpretedBy survive on the provision entry.
    par2 = next(p for p in pack["provisions"] if p["iri"] == "estleg:TL_Par_2")
    assert par2["references"] == ["estleg:TL_Par_1"]
    assert par2["referenced_by"] == ["estleg:TL_Par_1"]
    assert par2["interpreted_by"] == ["estleg:RK_1_20_1_5"]

    # The case IRI is resolved to a citation via the court-decision index.
    case = pack["cases"][0]
    assert case["iri"] == "estleg:RK_1_20_1_5"
    assert case["case_number"] == "1-20-1/5"
    assert case["decision_link"].startswith("https://www.riigikohus.ee/")


def test_projection_is_byte_identical_across_runs(tmp_path):
    krr = tmp_path / "krr_outputs"
    _build_corpus(krr)
    out_a = tmp_path / "out_a"
    out_b = tmp_path / "out_b"
    grp.generate(krr_dir=krr, out_dir=out_a, eval_date=EVAL_DATE)
    grp.generate(krr_dir=krr, out_dir=out_b, eval_date=EVAL_DATE)

    for rel in (
        "chunks.jsonl",
        "llms.txt",
        "README.md",
        "manifest.json",
        "chunks.sample.jsonl",
        "sample_outline.json",
        "sample_context_pack.json",
        "outlines/testlaw.json",
        "context_packs/testlaw.json",
    ):
        assert (out_a / rel).read_bytes() == (out_b / rel).read_bytes(), (
            f"{rel} changed across identical reruns -> nondeterminism reintroduced"
        )


def test_rerun_clears_stale_per_law_files(tmp_path):
    """A later (e.g. --limit) run must not leave a prior run's files on disk.

    The per-law trees are swapped in atomically, so the on-disk file set always
    equals the manifest's reported counts -- no stale outlines/context packs.
    """
    krr = tmp_path / "krr_outputs"
    _build_corpus(krr)
    out = krr / "retrieval"
    grp.generate(krr_dir=krr, out_dir=out, eval_date=EVAL_DATE)

    # Simulate leftovers from an earlier, larger run.
    ghost_outline = out / "outlines" / "ghost_law.json"
    ghost_context = out / "context_packs" / "ghost_law.json"
    ghost_outline.write_text("{}", encoding="utf-8")
    ghost_context.write_text("{}", encoding="utf-8")

    stats = grp.generate(krr_dir=krr, out_dir=out, eval_date=EVAL_DATE)

    assert not ghost_outline.exists(), "stale outline survived a rerun"
    assert not ghost_context.exists(), "stale context pack survived a rerun"
    on_disk = sorted(p.name for p in (out / "outlines").glob("*.json"))
    assert on_disk == ["testlaw.json"]
    # On-disk count matches what the manifest advertises.
    assert len(on_disk) == stats["outlines"]
    assert len(list((out / "context_packs").glob("*.json"))) == stats["context_packs"]
    # The atomic-swap temp dirs are consumed, not left behind.
    assert not (out / "outlines.tmp").exists()
    assert not (out / "context_packs.tmp").exists()


def test_deprecated_law_is_skipped(tmp_path):
    krr = tmp_path / "krr_outputs"
    _build_corpus(krr)
    # Mark the act root deprecated; the law must drop out of every projection.
    peep = json.loads((krr / "testlaw_peep.json").read_text("utf-8"))
    for node in peep["@graph"]:
        if "owl:Ontology" in node.get("@type", []):
            node["owl:deprecated"] = True
    (krr / "testlaw_peep.json").write_text(
        json.dumps(peep, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    out = krr / "retrieval"
    stats = grp.generate(krr_dir=krr, out_dir=out, eval_date=EVAL_DATE)
    assert stats["laws"] == 0
    assert stats["laws_skipped_deprecated"] == 1
    assert _read_chunks(out) == []


def test_retrieval_subtree_excluded_from_corpus_counter():
    """The derived retrieval artifacts must not inflate the corpus file count."""
    import estleg_common

    krr = Path("krr_outputs")
    for rel in (
        "retrieval/manifest.json",
        "retrieval/outlines/testlaw.json",
        "retrieval/context_packs/testlaw.json",
    ):
        assert estleg_common.is_operational_state_file(krr / rel), rel
    # A normal law peep is still counted.
    assert not estleg_common.is_operational_state_file(krr / "alkoholiseadus_peep.json")


# ---------------------------------------------------------------------------
# Corpus smoke test (opt-in: -m corpus)
# ---------------------------------------------------------------------------
@pytest.mark.corpus
def test_real_corpus_smoke(tmp_path):
    import validate_all

    krr = Path(__file__).resolve().parent.parent / "krr_outputs"
    index = krr / "INDEX.json"
    if not index.exists() or validate_all._is_lfs_pointer(index):
        pytest.skip("krr_outputs/INDEX.json missing or an un-materialised LFS pointer")

    out = tmp_path / "retrieval"
    stats = grp.generate(
        krr_dir=krr, out_dir=out, eval_date=EVAL_DATE, limit=25, with_cases=False
    )
    assert stats["laws"] > 0
    assert stats["total_chunks"] > 0

    chunks = _read_chunks(out)
    assert chunks, "expected at least one chunk from the real corpus"
    for chunk in chunks[:200]:
        assert set(chunk) == REQUIRED_CHUNK_KEYS
        assert chunk["provision_iri"].startswith("estleg:")
        if chunk["rt_url"] is not None:
            assert chunk["rt_url"].startswith("https://www.riigiteataja.ee/")
    assert any(c["in_force"] for c in chunks), "expected some in-force chunk"


# ---------------------------------------------------------------------------
# Review-round-2 regressions (#523)
# ---------------------------------------------------------------------------
def test_context_pack_unions_multipart_overlays(tmp_path):
    """A multi-part code's overlays (keyed by file stem) must be UNIONed."""
    krr = tmp_path / "krr_outputs"
    _build_multipart_corpus(krr)
    out = krr / "retrieval"
    grp.generate(krr_dir=krr, out_dir=out, eval_date=EVAL_DATE)

    pack = json.loads((out / "context_packs" / "multilaw.json").read_text("utf-8"))
    # osa1 (1) + osa2 (2) sanctions are aggregated, not just the first file.
    assert pack["counts"]["sanctions"] == 3
    sanction_ids = {n["@id"] for n in pack["sanctions"]}
    assert sanction_ids == {
        "estleg:Sanction_multilaw_S1",
        "estleg:Sanction_multilaw_S2",
        "estleg:Sanction_multilaw_S3",
    }
    # amendments from both stems too.
    assert pack["counts"]["amendments"] == 2
    assert {n["@id"] for n in pack["amendments"]} == {
        "estleg:Amendment_multilaw_A1",
        "estleg:Amendment_multilaw_A2",
    }


def test_owl_module_entry_is_skipped(tmp_path):
    """An INDEX entry whose only file is a *_owl.jsonld TBox module is skipped."""
    krr = tmp_path / "krr_outputs"
    _build_corpus(krr)
    # Append a non-enacted OWL/TBox entry (no peep file) to the manifest.
    index = json.loads((krr / "INDEX.json").read_text("utf-8"))
    index["laws"].append(
        {"name": "testlaw_eriosa_owl", "files": ["testlaw_eriosa_owl.jsonld"]}
    )
    (krr / "INDEX.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _write_json(
        krr / "testlaw_eriosa_owl.jsonld",
        {"@graph": [{"@id": "estleg:TL_TBox", "@type": ["owl:Ontology"]}]},
    )

    out = krr / "retrieval"
    stats = grp.generate(krr_dir=krr, out_dir=out, eval_date=EVAL_DATE)
    # Only the real enacted law is counted; the OWL entry is skipped.
    assert stats["laws"] == 1
    assert stats["laws_skipped_non_peep"] == 1
    assert not (out / "outlines" / "testlaw_eriosa_owl.json").exists()
    assert not (out / "context_packs" / "testlaw_eriosa_owl.json").exists()
    assert (out / "outlines" / "testlaw.json").exists()


def test_chunks_only_removes_stale_artifacts(tmp_path):
    """In a SCRATCH dir, --chunks-only cleans a prior full run's non-chunk
    artifacts (the committed-dir guard below does not apply to a tmp path)."""
    krr = tmp_path / "krr_outputs"
    _build_corpus(krr)
    out = krr / "retrieval"

    grp.generate(krr_dir=krr, out_dir=out, eval_date=EVAL_DATE)
    # Full run produced the heavy trees + the committed entry points.
    assert (out / "outlines").is_dir()
    assert (out / "context_packs").is_dir()
    assert (out / "llms.txt").exists()

    stats = grp.generate(
        krr_dir=krr, out_dir=out, eval_date=EVAL_DATE, chunks_only=True
    )
    assert stats["outlines"] == 0
    assert stats["context_packs"] == 0
    # Exactly {chunks.jsonl, manifest.json} remain — no stale artifacts.
    assert {p.name for p in out.iterdir()} == {"chunks.jsonl", "manifest.json"}
    for gone in (
        "outlines",
        "context_packs",
        "llms.txt",
        "README.md",
        "sample_outline.json",
        "sample_context_pack.json",
        "chunks.sample.jsonl",
    ):
        assert not (out / gone).exists(), f"{gone} should have been removed"


def test_chunks_only_refuses_to_clean_committed_dir():
    """--chunks-only must refuse to run against the committed projection dir,
    so it can never delete the tracked README/llms.txt/samples there."""
    committed = grp.KRR_DIR / grp.DEFAULT_OUT_DIRNAME
    with pytest.raises(ValueError, match="committed projection"):
        # Raises at the guard before any filesystem work, so the real
        # committed directory is never touched.
        grp.generate(
            krr_dir=grp.KRR_DIR,
            out_dir=committed,
            eval_date=EVAL_DATE,
            chunks_only=True,
        )

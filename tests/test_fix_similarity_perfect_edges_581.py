"""Tests for scripts/fix_similarity_perfect_edges_581.py (#581).

The fix drops perfect-cosine-1.0 intra-bucket KOV similarity edges between two
acts that BOTH lack provision body text (their TF-IDF document is only shared
enabling-law boilerplate). It must:

  * remove such a pair from BOTH directions in the index,
  * KEEP a 1.0 pair where at least one endpoint has provision text,
  * KEEP a sub-1.0 pair even when both endpoints are empty-bodied,
  * decrement ``totalPairs`` by exactly the directed entries removed,
  * leave ``regulationCount`` untouched,
  * be idempotent (a second run is a no-op),
  * never write the KOV peep files (classification is read-only).
"""

import json
from pathlib import Path

import pytest

from estleg import fix_similarity_perfect_edges_581 as fix

# ---------------------------------------------------------------------------
# Fixture builders — a tiny fake corpus mirroring the real index/peep shapes.
# ---------------------------------------------------------------------------

# Empty-body acts (no _Par_ summary/legalText): these score a boilerplate 1.0.
_EMPTY_A = "estleg:Reg_1001_Map"
_EMPTY_B = "estleg:Reg_1002_Map"
# Text-bearing acts: a 1.0 between them is a legitimate edge (real shared text).
_TEXT_A = "estleg:Reg_2001_Map"
_TEXT_B = "estleg:Reg_2002_Map"
# Empty-body acts that only score 0.5 — kept (not a perfect dup).
_SUB_A = "estleg:Reg_3001_Map"
_SUB_B = "estleg:Reg_3002_Map"


def _bare(iri: str) -> str:
    return iri[len("estleg:"):]


def _write_empty_body_peep(root: Path, slug: str, act_iri: str) -> Path:
    """A KOV peep whose provisions carry NO summary/legalText (boilerplate-only)."""
    reg_id = _bare(act_iri).split("_")[1]
    doc = {
        "@graph": [
            {
                "@id": act_iri,
                "@type": ["owl:Ontology", "estleg:Act", "estleg:MunicipalRegulation"],
                "estleg:titleNormalized": "kooli arengukava",
                "estleg:preambleText": "kohaliku omavalitsuse korralduse seaduse alusel",
                "estleg:hasAnnex": [{"@id": f"estleg:Reg_{reg_id}_Annex_1"}],
            },
            # Provision node with no body text -> empty-body act.
            {
                "@id": f"estleg:Reg_{reg_id}_Par_1",
                "@type": ["owl:NamedIndividual", f"estleg:Regulation_{reg_id}"],
            },
            # Annex node carries only the ordinal label (no body) — like the
            # real corpus, contributes no discriminative substance.
            {
                "@id": f"estleg:Reg_{reg_id}_Annex_1",
                "@type": ["owl:NamedIndividual", "estleg:Annex"],
                "rdfs:label": "Lisa 1",
                "estleg:annexNumber": "1",
            },
        ]
    }
    path = root / slug / f"{slug}_t{reg_id}_peep.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def _write_text_body_peep(root: Path, slug: str, act_iri: str) -> Path:
    """A KOV peep whose provision carries real summary/legalText body."""
    reg_id = _bare(act_iri).split("_")[1]
    doc = {
        "@graph": [
            {
                "@id": act_iri,
                "@type": ["owl:Ontology", "estleg:Act", "estleg:MunicipalRegulation"],
                "estleg:titleNormalized": "jaatmehoolduseeskiri",
            },
            {
                "@id": f"estleg:Reg_{reg_id}_Par_1",
                "@type": ["owl:NamedIndividual", f"estleg:Regulation_{reg_id}"],
                "estleg:summary": "jaatmevedu konteiner pakend sortimine kogumine",
                "estleg:legalText": "jaatmevedu konteiner pakend sortimine kogumine",
            },
        ]
    }
    path = root / slug / f"{slug}_t{reg_id}_peep.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def _undirected_pair(index: dict, a: str, b: str) -> bool:
    """True iff the index lists a<->b in BOTH directions inside any bucket."""
    a_to_b = False
    b_to_a = False
    for bucket in index["buckets"].values():
        for pair in bucket["pairs"]:
            if pair["source"] == a:
                a_to_b = a_to_b or any(p["target"] == b for p in pair["peers"])
            if pair["source"] == b:
                b_to_a = b_to_a or any(p["target"] == a for p in pair["peers"])
    return a_to_b and b_to_a


def _build_fake_index() -> dict:
    """An index with one boilerplate-1.0 pair, one real-1.0 pair, one sub-1.0 pair."""
    return {
        "scoreModel": "tfidf-cosine",
        "totalActs": 6,
        # Three undirected pairs => 6 directed peer entries.
        "totalPairs": 6,
        "buckets": {
            "kooli arengukava": {
                "regulationCount": 2,
                "pairs": [
                    {"source": _EMPTY_A, "peers": [{"target": _EMPTY_B, "score": 1.0}]},
                    {"source": _EMPTY_B, "peers": [{"target": _EMPTY_A, "score": 1.0}]},
                ],
            },
            "jaatmehoolduseeskiri": {
                "regulationCount": 2,
                "pairs": [
                    {"source": _TEXT_A, "peers": [{"target": _TEXT_B, "score": 1.0}]},
                    {"source": _TEXT_B, "peers": [{"target": _TEXT_A, "score": 1.0}]},
                ],
            },
            "tee nimi": {
                "regulationCount": 2,
                "pairs": [
                    {"source": _SUB_A, "peers": [{"target": _SUB_B, "score": 0.5}]},
                    {"source": _SUB_B, "peers": [{"target": _SUB_A, "score": 0.5}]},
                ],
            },
        },
    }


@pytest.fixture
def staged(tmp_path: Path):
    """Stage a fake index + peep tree; return (index_path, peep_root)."""
    krr = tmp_path / "krr_outputs"
    index_path = krr / "similarity" / "kov_similarity_index.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(
        json.dumps(_build_fake_index(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    peep_root = krr / "regulations" / "kov"
    _write_empty_body_peep(peep_root, "alpha_vv", _EMPTY_A)
    _write_empty_body_peep(peep_root, "beta_vv", _EMPTY_B)
    _write_text_body_peep(peep_root, "gamma_vv", _TEXT_A)
    _write_text_body_peep(peep_root, "delta_vv", _TEXT_B)
    _write_empty_body_peep(peep_root, "epsilon_vv", _SUB_A)
    _write_empty_body_peep(peep_root, "zeta_vv", _SUB_B)
    return index_path, peep_root


def _run(index_path: Path, peep_root: Path, *extra: str) -> int:
    return fix.main(
        [
            "--index-path",
            str(index_path),
            "--peep-root",
            str(peep_root),
            *extra,
        ]
    )


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def test_empty_body_set_classifies_only_textless_acts(staged):
    _, peep_root = staged
    empty = fix.build_empty_body_act_set(peep_root)
    # The two boilerplate acts + the two sub-1.0 acts have no provision text.
    assert _EMPTY_A in empty
    assert _EMPTY_B in empty
    assert _SUB_A in empty
    assert _SUB_B in empty
    # The text-bearing acts are NOT classified empty.
    assert _TEXT_A not in empty
    assert _TEXT_B not in empty


# ---------------------------------------------------------------------------
# Core behaviour
# ---------------------------------------------------------------------------

def test_boilerplate_perfect_pair_removed_both_directions(staged):
    index_path, peep_root = staged
    rc = _run(index_path, peep_root)
    assert rc == 0
    index = json.loads(index_path.read_text(encoding="utf-8"))
    # The boilerplate 1.0 pair is gone in BOTH directions.
    assert not _undirected_pair(index, _EMPTY_A, _EMPTY_B)
    # Its (now-empty) source rows were pruned from the bucket entirely.
    assert index["buckets"]["kooli arengukava"]["pairs"] == []


def test_real_perfect_pair_survives(staged):
    index_path, peep_root = staged
    _run(index_path, peep_root)
    index = json.loads(index_path.read_text(encoding="utf-8"))
    # A 1.0 pair with text-bearing endpoints is NOT boilerplate -> kept.
    assert _undirected_pair(index, _TEXT_A, _TEXT_B)


def test_sub_perfect_empty_pair_survives(staged):
    index_path, peep_root = staged
    _run(index_path, peep_root)
    index = json.loads(index_path.read_text(encoding="utf-8"))
    # Empty-bodied but score < 1.0 -> only perfect dups are dropped -> kept.
    assert _undirected_pair(index, _SUB_A, _SUB_B)


def test_total_pairs_decremented_by_directed_entries_removed(staged):
    index_path, peep_root = staged
    _run(index_path, peep_root)
    index = json.loads(index_path.read_text(encoding="utf-8"))
    # Started at 6 directed entries; removed the 2 directed entries of the one
    # boilerplate undirected pair.
    assert index["totalPairs"] == 4
    # Sanity: the surviving directed entries actually number 4.
    surviving = sum(
        len(p["peers"]) for b in index["buckets"].values() for p in b["pairs"]
    )
    assert surviving == 4


def test_regulation_count_untouched(staged):
    index_path, peep_root = staged
    _run(index_path, peep_root)
    index = json.loads(index_path.read_text(encoding="utf-8"))
    for bucket in index["buckets"].values():
        assert bucket["regulationCount"] == 2


def test_idempotent_second_run_is_noop(staged):
    index_path, peep_root = staged
    _run(index_path, peep_root)
    after_first = index_path.read_text(encoding="utf-8")
    rc = _run(index_path, peep_root)
    assert rc == 0
    after_second = index_path.read_text(encoding="utf-8")
    # Byte-identical: nothing left to remove.
    assert after_first == after_second


def test_dry_run_does_not_write(staged):
    index_path, peep_root = staged
    before = index_path.read_text(encoding="utf-8")
    rc = _run(index_path, peep_root, "--dry-run")
    assert rc == 0
    assert index_path.read_text(encoding="utf-8") == before


def test_peep_files_not_modified(staged):
    index_path, peep_root = staged
    peep_paths = sorted(peep_root.glob("**/*_peep.json"))
    before = {p: p.read_text(encoding="utf-8") for p in peep_paths}
    _run(index_path, peep_root)
    for path, content in before.items():
        assert path.read_text(encoding="utf-8") == content, (
            f"post-process modified a KOV peep file: {path}"
        )


def test_prune_stats_report_counts(staged):
    _, peep_root = staged
    empty = fix.build_empty_body_act_set(peep_root)
    index = _build_fake_index()
    stats = fix.prune_perfect_boilerplate_edges(index, empty)
    assert stats["directed_peer_entries_scanned"] == 6
    assert stats["perfect_pairs_both_empty"] == 1
    assert stats["directed_entries_removed"] == 2
    assert stats["buckets_touched"] == 1


# ---------------------------------------------------------------------------
# LFS-pointer guard
# ---------------------------------------------------------------------------

def test_lfs_pointer_index_is_skipped(tmp_path):
    pointer = tmp_path / "kov_similarity_index.json"
    pointer.write_text(
        "version https://git-lfs.github.com/spec/v1\n"
        "oid sha256:deadbeef\nsize 123\n",
        encoding="utf-8",
    )
    peep_root = tmp_path / "kov"
    peep_root.mkdir()
    rc = fix.main(["--index-path", str(pointer), "--peep-root", str(peep_root)])
    assert rc == 1
    # Untouched: still the pointer stub.
    assert pointer.read_text(encoding="utf-8").startswith(fix.LFS_POINTER_PREFIX)


# ---------------------------------------------------------------------------
# Real-corpus assertion (opt-in)
# ---------------------------------------------------------------------------

@pytest.mark.corpus
def test_real_corpus_has_no_boilerplate_perfect_pairs_after_fix():
    """The live index carries NO perfect-1.0 edge between two empty-body acts.

    Post-application invariant (#581): once the fix has been applied to
    ``kov_similarity_index.json`` it is idempotent — re-running the pruner
    finds 0 boilerplate-perfect pairs to remove, and no surviving directed 1.0
    edge has BOTH endpoints provision-text-empty. (The one-time historical
    count was 396 undirected / 792 directed entries.)
    """
    if not fix.KOV_INDEX_PATH.is_file():
        pytest.skip("kov_similarity_index.json not present")
    index = json.loads(fix.KOV_INDEX_PATH.read_text(encoding="utf-8"))
    empty = fix.build_empty_body_act_set(fix.KOV_PEEP_GLOB_ROOT)

    # No surviving directed 1.0 edge between two empty-body acts.
    surviving_both_empty = 0
    for bucket in index["buckets"].values():
        for pair in bucket["pairs"]:
            source = pair["source"]
            for peer in pair["peers"]:
                target = peer["target"]
                if (
                    fix._is_perfect(peer["score"])
                    and source in empty
                    and target in empty
                ):
                    surviving_both_empty += 1
    assert surviving_both_empty == 0

    # Idempotent: the pruner removes nothing further.
    stats = fix.prune_perfect_boilerplate_edges(index, empty)
    assert stats["perfect_pairs_both_empty"] == 0
    assert stats["directed_entries_removed"] == 0

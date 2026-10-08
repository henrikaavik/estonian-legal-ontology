"""Source-XML -> peep golden pairs (#706).

Each document in ``tests/fixtures/golden/manifest.json`` pins one Riigi
Teataja act: its source XML (stored gzipped next to the manifest), the
generator that owns it (laws, multipart laws, state / KOV regulations) and
every run input the generator reads (title, slug, abbreviation registry
entry, snapshot ``kehtiv``, evaluation date). The test regenerates the peep
offline from the stored XML and diffs it against the committed golden peep,
so a change in parsing, IRI minting or text extraction shows up as a
reviewable JSON diff instead of slipping into the corpus unnoticed.

Normalisation (``normalise_peep``) drops only:

* ``VOLATILE_KEYS`` - values stamped from the run environment rather than
  from the XML;
* ``ENRICHMENT_ONLY_PREDICATES`` - predicates written by downstream
  enrichers. Generators must never emit them
  (``test_generators_emit_no_enrichment_only_predicates``); they are listed
  so the same normaliser can be pointed at an enriched corpus peep.

Graph nodes are sorted by ``@id`` and keys are sorted, so a pure reordering
is not a regression.

Regenerate after an intentional generator change (offline, deterministic)::

    .venv/bin/python -m tests.test_golden_peeps regen

Re-download the source XML through ``riigiteataja_common.fetch_xml``
(network; the RT redactions are addressed by globaalID, so this should be a
no-op unless RT re-published a redaction)::

    .venv/bin/python -m tests.test_golden_peeps refetch

The ``live`` test below re-fetches every URL-sourced document and checks the
generator output from the live XML still equals the golden peep::

    ESTLEG_LIVE_CANARY=1 .venv/bin/python -m pytest -q -m live tests/test_golden_peeps.py
"""

from __future__ import annotations

import argparse
import copy
import difflib
import gzip
import hashlib
import json
import os
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from pathlib import Path

import pytest

from estleg import generate_all_laws, generate_regulations
from estleg.estleg_common import parse_xml

REPO_ROOT = Path(__file__).resolve().parent.parent
GOLDEN_DIR = Path(__file__).resolve().parent / "fixtures" / "golden"
MANIFEST_PATH = GOLDEN_DIR / "manifest.json"

# Values stamped from the run environment rather than from the source XML.
# ``estleg:contentHash`` is NOT here: the harness stamps it from the stored
# XML bytes exactly as ``generate_all_laws.fetch_xml`` does, so it is
# deterministic and pinned.
VOLATILE_KEYS: frozenset[str] = frozenset(
    {
        "generated",
        "generatedAt",
        "dcterms:modified",
        "prov:generatedAtTime",
    }
)

# Predicates added after generation by the enrichment pipeline
# (run_all_integration stages). They are never part of a generator golden.
ENRICHMENT_ONLY_PREDICATES: frozenset[str] = frozenset(
    {
        "eli:is_about",
        "estleg:affectedBy",
        "estleg:amendedBy",
        "estleg:assertionConfidence",
        "estleg:competentAuthority",
        "estleg:harmonisedWith",
        "estleg:hasProposedAmendment",
        "estleg:implementedBy",
        "estleg:implementedByCount",
        "estleg:interpretedBy",
        "estleg:isRatificationShell",
        "estleg:officialEnglishText",
        "estleg:referencedBy",
        "estleg:references",
        "estleg:temporalStatus",
        "estleg:transposesDirective",
        "estleg:transpositionStatus",
    }
)

MAX_DIFF_LINES = 80
REGEN_HINT = "If the change is intended: .venv/bin/python -m tests.test_golden_peeps regen"


# ── manifest / storage ──────────────────────────────────────────────────────


def load_manifest() -> list[dict]:
    with open(MANIFEST_PATH, encoding="utf-8") as fh:
        return json.load(fh)["documents"]


def xml_path(doc: dict) -> Path:
    return GOLDEN_DIR / f"{doc['id']}.xml.gz"


def golden_path(doc: dict) -> Path:
    return GOLDEN_DIR / f"{doc['id']}.peep.json"


def read_source_xml(doc: dict) -> bytes:
    with gzip.open(xml_path(doc), "rb") as fh:
        return fh.read()


def write_source_xml(doc: dict, payload: bytes) -> None:
    # mtime=0 + fixed filename keep the .gz byte-identical across rewrites.
    with open(xml_path(doc), "wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=9) as gz:
            gz.write(payload)


def dump_json(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


# ── generation ──────────────────────────────────────────────────────────────


def _law_allocator(doc: dict) -> generate_all_laws.PrefixAllocator:
    # An explicit registry pins the act prefix to the manifest instead of
    # the live data/law_abbreviations.json, keeping the golden hermetic.
    return generate_all_laws.PrefixAllocator(registry=copy.deepcopy(doc.get("registry", {})))


def generate_peep(doc: dict, payload: bytes) -> dict:
    """Run the owning generator on ``payload`` with the manifest's inputs."""
    root: ET.Element = parse_xml(payload)
    kind = doc["generator"]
    if kind in ("law", "law_multipart"):
        hashes = generate_all_laws._CONTENT_HASHES
        previous = hashes.get(doc["slug"])
        hashes[doc["slug"]] = hashlib.sha256(payload).hexdigest()
        try:
            if kind == "law":
                return generate_all_laws.generate_law_jsonld(
                    doc["title"],
                    doc["slug"],
                    root,
                    doc.get("abbreviation", ""),
                    rt_url=doc.get("rt_url", ""),
                    kehtiv=doc.get("kehtiv"),
                    terviktekst_id=doc.get("tid"),
                    allocator=_law_allocator(doc),
                )
            parts = dict(
                generate_all_laws.generate_multipart_law(
                    doc["title"],
                    doc["slug"],
                    root,
                    doc.get("abbreviation", ""),
                    rt_url=doc.get("rt_url", ""),
                    kehtiv=doc.get("kehtiv"),
                    terviktekst_id=doc.get("tid"),
                    allocator=_law_allocator(doc),
                )
            )
            wanted = doc["part_filename"]
            if wanted not in parts:
                raise AssertionError(
                    f"{doc['id']}: multipart generator emitted {sorted(parts)}, "
                    f"not {wanted}"
                )
            return parts[wanted]
        finally:
            if previous is None:
                hashes.pop(doc["slug"], None)
            else:
                hashes[doc["slug"]] = previous
    if kind == "regulation":
        peep, _stats = generate_regulations.build_regulation_jsonld(
            doc["title"],
            copy.deepcopy(doc["info"]),
            root,
            is_kov=bool(doc["is_kov"]),
            kehtiv=doc.get("kehtiv"),
            evaluation_date=doc["evaluation_date"],
        )
        return peep
    raise ValueError(f"{doc['id']}: unknown generator {kind!r}")


# ── normalisation / diff ────────────────────────────────────────────────────


def _strip(value: object) -> object:
    if isinstance(value, dict):
        return {
            k: _strip(v)
            for k, v in value.items()
            if k not in VOLATILE_KEYS and k not in ENRICHMENT_ONLY_PREDICATES
        }
    if isinstance(value, list):
        return [_strip(v) for v in value]
    return value


def normalise_peep(peep: dict) -> dict:
    """Drop volatile / enrichment-only keys and order the graph by ``@id``."""
    out = _strip(copy.deepcopy(peep))
    graph = out.get("@graph")
    if isinstance(graph, list):
        out["@graph"] = sorted(
            graph, key=lambda n: (str(n.get("@id", "")), json.dumps(n, sort_keys=True))
        )
    return out


def _iter_keys(value: object) -> Iterator[str]:
    if isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from _iter_keys(v)
    elif isinstance(value, list):
        for v in value:
            yield from _iter_keys(v)


def render_diff(expected: str, actual: str, label: str) -> str:
    lines = list(
        difflib.unified_diff(
            expected.splitlines(),
            actual.splitlines(),
            fromfile=f"golden/{label}",
            tofile=f"generated/{label}",
            lineterm="",
            n=2,
        )
    )
    shown = lines[:MAX_DIFF_LINES]
    if len(lines) > MAX_DIFF_LINES:
        shown.append(f"... ({len(lines) - MAX_DIFF_LINES} more diff lines)")
    return "\n".join(shown)


# ── tests ───────────────────────────────────────────────────────────────────

DOCUMENTS = load_manifest()
DOC_IDS = [d["id"] for d in DOCUMENTS]


def test_manifest_covers_every_generator_kind():
    kinds = {d["generator"] for d in DOCUMENTS}
    assert kinds == {"law", "law_multipart", "regulation"}
    assert 8 <= len(DOCUMENTS) <= 10
    assert any(d["generator"] == "regulation" and d["is_kov"] for d in DOCUMENTS)
    assert any(d["generator"] == "regulation" and not d["is_kov"] for d in DOCUMENTS)
    assert len(set(DOC_IDS)) == len(DOC_IDS)


def test_golden_fixture_budget():
    """Keep the committed goldens small enough to live in Git (not LFS)."""
    total = sum(p.stat().st_size for p in GOLDEN_DIR.iterdir() if p.is_file())
    assert total < 3 * 1024 * 1024, f"golden fixtures are {total} bytes (> 3 MiB)"


@pytest.mark.parametrize("doc", DOCUMENTS, ids=DOC_IDS)
def test_generated_peep_matches_golden(doc: dict, tmp_path: Path):
    payload = read_source_xml(doc)
    generated = normalise_peep(generate_peep(doc, payload))
    # Round-trip through a real file in tmp_path so the comparison sees
    # exactly what a writer would persist (str keys, JSON-native values).
    out = tmp_path / golden_path(doc).name
    out.write_text(dump_json(generated), encoding="utf-8")
    actual = out.read_text(encoding="utf-8")
    expected = golden_path(doc).read_text(encoding="utf-8")
    if actual != expected:
        pytest.fail(
            f"{doc['id']}: generated peep drifted from the golden.\n"
            f"{render_diff(expected, actual, golden_path(doc).name)}\n{REGEN_HINT}",
            pytrace=False,
        )


@pytest.mark.parametrize("doc", DOCUMENTS, ids=DOC_IDS)
def test_generators_emit_no_enrichment_only_predicates(doc: dict):
    raw = generate_peep(doc, read_source_xml(doc))
    leaked = sorted(set(_iter_keys(raw)) & ENRICHMENT_ONLY_PREDICATES)
    assert not leaked, (
        f"{doc['id']}: generator emitted enrichment-only predicates {leaked}; "
        "either the predicate moved into the generator (remove it from "
        "ENRICHMENT_ONLY_PREDICATES and regen) or an enricher leaked in."
    )


@pytest.mark.parametrize("doc", DOCUMENTS, ids=DOC_IDS)
def test_golden_peep_is_structured(doc: dict):
    """Guard against committing a degenerate golden (stub / empty parse)."""
    golden = json.loads(golden_path(doc).read_text(encoding="utf-8"))
    graph = golden["@graph"]
    assert len(graph) > 3, f"{doc['id']}: golden has only {len(graph)} nodes"
    provisions = [
        n for n in graph if "estleg:LegalProvision" in (n.get("@type") or [])
    ]
    assert provisions, f"{doc['id']}: golden has no LegalProvision nodes"


def test_normalise_drops_volatile_and_enrichment_keys_only():
    peep = {
        "@graph": [
            {"@id": "b", "rdfs:label": "x", "generatedAt": "now", "eli:is_about": []},
            {"@id": "a", "estleg:contentHash": "h", "nested": [{"estleg:referencedBy": 1, "k": 2}]},
        ]
    }
    assert normalise_peep(peep) == {
        "@graph": [
            {"@id": "a", "estleg:contentHash": "h", "nested": [{"k": 2}]},
            {"@id": "b", "rdfs:label": "x"},
        ]
    }


_LIVE_DOCS = [d for d in DOCUMENTS if "url" in d["source"]]


@pytest.mark.live
@pytest.mark.parametrize("doc", _LIVE_DOCS, ids=[d["id"] for d in _LIVE_DOCS])
def test_live_rt_xml_still_generates_golden(doc: dict, tmp_path: Path):
    """Re-fetch through ``riigiteataja_common.fetch_xml``; opt-in, network."""
    payload = fetch_source_xml(doc, tmp_path)
    actual = dump_json(normalise_peep(generate_peep(doc, payload)))
    expected = golden_path(doc).read_text(encoding="utf-8")
    assert actual == expected, render_diff(expected, actual, golden_path(doc).name)


# ── regeneration CLI ────────────────────────────────────────────────────────


def fetch_source_xml(doc: dict, cache_dir: Path) -> bytes:
    """Return the source XML bytes for ``doc`` (committed file or RT GET)."""
    source = doc["source"]
    if "committed_xml" in source:
        return (REPO_ROOT / source["committed_xml"]).read_bytes()
    from estleg import riigiteataja_common as rtc

    captured: list[bytes] = []
    root = rtc.fetch_xml(
        source["url"],
        doc["id"],
        cache_dir=cache_dir,
        refresh=True,
        on_bytes=captured.append,
        strict=True,
    )
    if root is None or not captured:
        raise RuntimeError(f"{doc['id']}: fetch_xml returned no XML for {source['url']}")
    return captured[-1]


def _cmd_refetch(selected: list[dict]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        for doc in selected:
            payload = fetch_source_xml(doc, Path(tmp))
            write_source_xml(doc, payload)
            print(f"  fetched {doc['id']}: {len(payload)} bytes -> {xml_path(doc).name}")


def _cmd_regen(selected: list[dict]) -> None:
    for doc in selected:
        peep = normalise_peep(generate_peep(doc, read_source_xml(doc)))
        golden_path(doc).write_text(dump_json(peep), encoding="utf-8")
        print(f"  regenerated {golden_path(doc).name}: {len(peep['@graph'])} nodes")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Maintain the #706 XML->peep goldens.")
    parser.add_argument("command", choices=("regen", "refetch"))
    parser.add_argument("--only", nargs="*", default=None, help="Document ids to process.")
    args = parser.parse_args(argv)
    selected = [d for d in DOCUMENTS if args.only is None or d["id"] in args.only]
    if args.command == "refetch":
        _cmd_refetch(selected)
    _cmd_regen(selected)
    return 0


if __name__ == "__main__":
    os.chdir(REPO_ROOT)
    sys.exit(main())

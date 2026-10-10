"""Raw ingest vs enrichment overlay: regeneration fixtures (#697).

Each fixture under ``tests/fixtures/ingest_697/`` holds the upstream rows one
committed peep was built from:

* ``riigikohus_2026_rows.json``          - rikos search-table rows (six fields
  per decision) for ``krr_outputs/riigikohus/riigikohus_2026_peep.json``;
* ``eurlex_directives_rows.json.gz``     - EUR-Lex SPARQL rows for
  ``krr_outputs/eurlex/eurlex_directives_peep.json``;
* ``curia_court_opinions_rows.json``     - CELLAR case-law rows for
  ``krr_outputs/curia/curia_court_opinions_peep.json``.

DERIVED FIXTURES: no raw search-table HTML or SPARQL response cache exists in
the tree (``data/curia/`` and ``data/eurovoc/`` cache only the interprets /
EuroVoc side queries), and tests may not use the network. The rows are
therefore reconstructed from the committed peeps' RAW fields only (the keys
the ingest layer owns); overlay keys are never read into a row. Regenerate
after an intentional raw refresh::

    .venv/bin/python -m tests.test_ingest_regeneration_697 regen

The regeneration tests run each generator's REAL write path into tmp_path on
top of a copy of the committed peep and assert the result is byte-identical
to the committed file (so the raw layer reproduces exactly and the overlay
survives untouched), apart from an explicit, reviewed ``knownDrift`` list in
the fixture: values where the current generator deliberately differs from
the published corpus (e.g. a trailing space the title cleaner now strips).
"""

from __future__ import annotations

import argparse
import copy
import gzip
import io
import json
import logging
import shutil
from contextlib import redirect_stderr
from pathlib import Path

import pytest

from estleg import generate_court_decisions as gcd
from estleg import generate_eu_court_decisions as gcu
from estleg import generate_eu_legislation as gel
from estleg.ingest_overlay import _type_list, merge_overlays

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "ingest_697"
RK_FIXTURE = FIXTURE_DIR / "riigikohus_2026_rows.json"
EURLEX_FIXTURE = FIXTURE_DIR / "eurlex_directives_rows.json.gz"
CURIA_FIXTURE = FIXTURE_DIR / "curia_court_opinions_rows.json"

RK_PEEP = "riigikohus/riigikohus_2026_peep.json"
EURLEX_PEEP = "eurlex/eurlex_directives_peep.json"
CURIA_PEEP = "curia/curia_court_opinions_peep.json"

RK_YEAR = 2026
CURIA_CATEGORY = "court_opinions"


# ── fixture derivation (raw fields only) ─────────────────────────────────────


def _lit(value: object) -> object:
    return value.get("@value") if isinstance(value, dict) else value


def _types(node: dict) -> list[str]:
    return _type_list(node.get("@type"))


_RK_DECISION_TYPE_LABEL = {
    f"estleg:DecisionType_{info[0]}": label for label, info in gcd.DECISION_TYPES.items()
}


def rk_rows_from_peep(doc: dict) -> list[dict]:
    """rikos search-table rows (``_extract_row_fields`` shape) of a year peep."""
    rows = []
    for node in doc["@graph"]:
        if "estleg:CourtDecision" not in _types(node):
            continue
        iso = gcd._unwrap_literal(node.get("estleg:decisionDate"))
        rows.append(
            {
                "case_nr": node["estleg:caseNumber"],
                "date": f"{iso[8:10]}.{iso[5:7]}.{iso[0:4]}" if iso else "",
                "decision_type": _RK_DECISION_TYPE_LABEL.get(
                    (node.get("estleg:decisionType") or {}).get("@id"), ""
                ),
                "summary": gcd._unwrap_literal(node.get("estleg:summary")),
                # The row's href only gates decisionLink; rikosUrl is its value.
                "link": gcd._unwrap_literal(node.get("estleg:rikosUrl"))
                if "estleg:decisionLink" in node
                else "",
                "object_id": node["estleg:rikObjectId"],
            }
        )
    return rows


_EU_INSTITUTION_CODE = {f"estleg:EUInst_{v[0]}": code for code, v in gel.EU_INSTITUTIONS.items()}


def eurlex_rows_from_peep(doc: dict) -> list[dict]:
    """EUR-Lex SPARQL item dicts (``fetch_legislation_type`` shape)."""
    rows = []
    for node in doc["@graph"]:
        if "estleg:EULegislation" not in _types(node):
            continue
        title = node.get("dcterms:title")
        title_en = None
        if isinstance(title, list):
            title_en = next((t["@value"] for t in title if t.get("@language") == "en"), None)
        row: dict = {"celex": node["estleg:celexNumber"], "title": _lit(node["rdfs:label"])}
        if title_en:
            row["title_en"] = title_en
        for key, field in (
            ("eli", "estleg:eliIdentifier"),
            ("date", "estleg:documentDate"),
            ("transposition_deadline", "estleg:transpositionDeadline"),
            ("in_force", "estleg:inForce"),
        ):
            if field in node:
                row[key] = _lit(node[field])
        inst = node.get("estleg:euInstitution")
        if inst:
            refs = inst if isinstance(inst, list) else [inst]
            row["authors"] = [
                _EU_INSTITUTION_CODE.get(r["@id"], r["@id"].split("EUInst_", 1)[1]) for r in refs
            ]
        if node.get("estleg:subjectSource") == "cellar":
            row["eurovoc_ids"] = [r["@id"].rsplit("/", 1)[1] for r in node["dcterms:subject"]]
        rows.append(row)
    return rows


def curia_rows_from_peep(doc: dict) -> list[dict]:
    """CELLAR case-law item dicts (``fetch_all_case_law`` shape)."""
    rows = []
    for node in doc["@graph"]:
        if "estleg:EUCourtDecision" not in _types(node):
            continue
        row: dict = {"celex": node["estleg:celexNumber"], "title": _lit(node["dcterms:title"])}
        if "estleg:ecliIdentifier" in node:
            row["ecli"] = node["estleg:ecliIdentifier"]
        if "estleg:documentDate" in node:
            row["date"] = _lit(node["estleg:documentDate"])
        rows.append(row)
    return rows


# ── build + write through the real writers ───────────────────────────────────


def _quiet():
    return redirect_stderr(io.StringIO())


def build_rk(rows: list[dict], existing: dict | None) -> dict:
    scheme = gcd.RkIriScheme.frozen()
    if existing is not None:
        scheme.seed_from_graph(existing["@graph"])
    doc, _emitted, _skipped = gcd.build_year_doc(RK_YEAR, rows, scheme)
    assert not scheme.new_collisions, scheme.new_collisions
    return doc


def build_eurlex(rows: list[dict], _existing: dict | None = None) -> dict:
    with _quiet():
        return gel.build_type_doc(gel.EU_DOC_TYPES["directive"], rows)


def build_curia(rows: list[dict], _existing: dict | None = None) -> dict:
    with _quiet():
        return gcu.build_category_doc(CURIA_CATEGORY, rows)


def write_rk(path: Path, doc: dict, *, replace_overlays: bool = False) -> None:
    gcd.write_year_peep(path, doc, replace_overlays=replace_overlays)


def write_eurlex(path: Path, doc: dict, *, replace_overlays: bool = False) -> None:
    gel.write_type_peep(path, doc, replace_overlays=replace_overlays)


def write_curia(path: Path, doc: dict, *, replace_overlays: bool = False) -> None:
    gcu.write_category_peep(path, doc, replace_overlays=replace_overlays)


CASES = {
    "riigikohus": (RK_FIXTURE, RK_PEEP, rk_rows_from_peep, build_rk, write_rk, gcd.RK_INGEST_LAYER),
    "eurlex": (
        EURLEX_FIXTURE,
        EURLEX_PEEP,
        eurlex_rows_from_peep,
        build_eurlex,
        write_eurlex,
        gel.EURLEX_INGEST_LAYER,
    ),
    "curia": (
        CURIA_FIXTURE,
        CURIA_PEEP,
        curia_rows_from_peep,
        build_curia,
        write_curia,
        gcu.CURIA_INGEST_LAYER,
    ),
}

# Overlay predicates whose edges each case must carry through a re-ingest.
OVERLAY_EDGES = {
    "riigikohus": (
        "estleg:legalText",
        "estleg:chamber",
        "estleg:judge",
        "estleg:interpretsLaw",
        "estleg:interpretsVersion",
        "estleg:interpretationOutdated",
    ),
    "eurlex": ("estleg:transposedBy", "estleg:transpositionStatus", "estleg:estoniaRelevant"),
    "curia": ("estleg:interpretsEULaw",),
}


def load_fixture(path: Path) -> dict:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return json.load(fh)
    return json.loads(path.read_text(encoding="utf-8"))


def dump(doc: dict) -> str:
    return json.dumps(doc, ensure_ascii=False, indent=2) + "\n"


def apply_known_drift(doc: dict, drift: list[dict]) -> dict:
    out = copy.deepcopy(doc)
    by_id = {n.get("@id"): n for n in out["@graph"]}
    for entry in drift:
        node = by_id[entry["@id"]]
        if entry["regenerated"] is None:
            node.pop(entry["key"], None)
        else:
            node[entry["key"]] = entry["regenerated"]
    return out


def edge_count(doc: dict, predicates: tuple[str, ...]) -> dict[str, int]:
    counts = dict.fromkeys(predicates, 0)
    for node in doc["@graph"]:
        for pred in predicates:
            value = node.get(pred)
            if value is not None:
                counts[pred] += len(value) if isinstance(value, list) else 1
    return counts


# ── tests ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("case", sorted(CASES))
def test_regeneration_is_byte_identical_to_committed_peep(case, corpus_krr, tmp_path):
    fixture_path, rel, _derive, build, write, _layer = CASES[case]
    committed_path = corpus_krr.path(rel)
    committed_text = committed_path.read_text(encoding="utf-8")
    committed = json.loads(committed_text)
    fixture = load_fixture(fixture_path)

    out = tmp_path / committed_path.name
    shutil.copyfile(committed_path, out)
    write(out, build(fixture["rows"], committed))

    expected = committed_text
    if fixture["knownDrift"]:
        expected = dump(apply_known_drift(committed, fixture["knownDrift"]))
    assert out.read_text(encoding="utf-8") == expected, (
        f"{case}: re-ingest of {rel} is not byte-identical. If the raw source "
        "changed intentionally: .venv/bin/python -m tests.test_ingest_regeneration_697 regen"
    )


@pytest.mark.parametrize("case", sorted(CASES))
def test_overlay_edges_survive_and_replace_overlays_drops_them(case, corpus_krr, tmp_path):
    fixture_path, rel, _derive, build, write, _layer = CASES[case]
    committed_path = corpus_krr.path(rel)
    committed = json.loads(committed_path.read_text(encoding="utf-8"))
    fixture = load_fixture(fixture_path)
    predicates = OVERLAY_EDGES[case]
    before = edge_count(committed, predicates)
    assert all(before.values()), f"{case}: fixture peep carries no overlay {before}"

    merged_path = tmp_path / "merged.json"
    shutil.copyfile(committed_path, merged_path)
    write(merged_path, build(fixture["rows"], committed))
    after = edge_count(json.loads(merged_path.read_text(encoding="utf-8")), predicates)
    assert after == before

    raw = build(fixture["rows"], committed)
    replaced_path = tmp_path / "replaced.json"
    shutil.copyfile(committed_path, replaced_path)
    write(replaced_path, raw, replace_overlays=True)
    replaced = json.loads(replaced_path.read_text(encoding="utf-8"))
    assert edge_count(replaced, predicates) == dict.fromkeys(predicates, 0)
    assert replaced == json.loads(dump(raw))


@pytest.mark.parametrize("case", sorted(CASES))
def test_fixture_rows_carry_raw_fields_only(case, corpus_krr):
    """The derived rows equal a fresh derivation and hold no overlay values."""
    fixture_path, rel, derive, _build, _write, _layer = CASES[case]
    fixture = load_fixture(fixture_path)
    assert fixture["derivedFrom"] == f"krr_outputs/{rel}"
    rows = fixture["rows"]
    assert rows == derive(corpus_krr.read_json(rel))
    overlay_text = json.dumps(rows, ensure_ascii=False)
    for pred in OVERLAY_EDGES[case]:
        assert pred not in overlay_text


def test_replace_overlays_is_logged(corpus_krr, tmp_path, caplog):
    committed_path = corpus_krr.path(RK_PEEP)
    out = tmp_path / committed_path.name
    shutil.copyfile(committed_path, out)
    raw = build_rk(load_fixture(RK_FIXTURE)["rows"], None)
    with caplog.at_level(logging.WARNING):
        write_rk(out, raw, replace_overlays=True)
    assert any("--replace-overlays" in r.message and out.name in r.message for r in caplog.records)


def test_regulation_refresh_keeps_overlay_on_golden_regulation(tmp_path):
    """Regulation writer: overlay added to a golden peep survives a refresh."""
    from estleg import generate_regulations as gr  # noqa: PLC0415
    from tests import test_golden_peeps as golden  # noqa: PLC0415 - shared golden harness

    doc = next(d for d in golden.DOCUMENTS if d["id"] == "riik_kaitsevae_salajane_koostoo")
    fresh = golden.generate_peep(doc, golden.read_source_xml(doc))
    enriched = copy.deepcopy(fresh)
    act = gr._ontology_node(enriched)
    act["dcterms:subject"] = [{"@id": "http://eurovoc.europa.eu/1"}]
    act["estleg:issuedUnder"] = [{"@id": "estleg:Some_Law_Par_1"}]
    provisions = [n for n in enriched["@graph"] if "estleg:LegalProvision" in _types(n)]
    for node in provisions:
        node["@type"] = [*node["@type"], "estleg:KovProvision"]
        node["estleg:targetGroup"] = [{"@id": "estleg:TargetGroup_Citizen"}]
    enriched["@graph"].append(
        {"@id": "estleg:Sim_1", "@type": ["estleg:Similarity"], "estleg:similarityScore": 0.9}
    )
    out = tmp_path / "reg_peep.json"
    out.write_text(dump(enriched), encoding="utf-8")

    status = gr.write_regulation_output(out, copy.deepcopy(fresh), mode="force")
    assert status == "forceRewritten"
    assert out.read_text(encoding="utf-8") == dump(enriched)

    gr.write_regulation_output(out, copy.deepcopy(fresh), mode="force", replace_overlays=True)
    assert json.loads(out.read_text(encoding="utf-8")) == json.loads(dump(fresh))


# ── frozen Riigikohus IRI scheme ─────────────────────────────────────────────


def test_rk_collision_allowlist_is_well_formed():
    data = json.loads(gcd.RK_IRI_COLLISIONS_PATH.read_text(encoding="utf-8"))
    entries = data["collisions"]
    assert len(entries) == 121
    keys = [(e["caseNumber"], e["rikObjectId"]) for e in entries]
    assert len(set(keys)) == len(keys)
    for entry in entries:
        assert entry["iri"] == gcd.rk_collision_iri(entry["caseNumber"], entry["rikObjectId"])
        assert entry["shortFormHolder"] != entry["rikObjectId"]
    assert keys == sorted(keys, key=lambda k: (k[0], gcd._oid_sort_key(k[1])))


def test_rk_scheme_reproduces_allowlisted_twin_in_any_row_order():
    entry = json.loads(gcd.RK_IRI_COLLISIONS_PATH.read_text(encoding="utf-8"))["collisions"][0]
    case_nr, twin, holder = entry["caseNumber"], entry["rikObjectId"], entry["shortFormHolder"]
    base = {"summary": "", "decision_type": "", "link": ""}
    dates = {twin: "01.01.1996", holder: "02.01.1996"}  # distinct documents
    for order in ((twin, holder), (holder, twin)):
        scheme = gcd.RkIriScheme.frozen()
        rows = [{**base, "case_nr": case_nr, "object_id": oid, "date": dates[oid]} for oid in order]
        doc, _, _ = gcd.build_year_doc(1996, rows, scheme)
        ids = {n["estleg:rikObjectId"]: n["@id"] for n in doc["@graph"][1:]}
        assert ids == {holder: gcd.rk_short_iri(case_nr), twin: entry["iri"]}
        assert scheme.new_collisions == []


def test_new_rk_collision_is_appended_to_allowlist(tmp_path, caplog):
    path = tmp_path / "rk_iri_collisions.json"
    shutil.copyfile(gcd.RK_IRI_COLLISIONS_PATH, path)
    scheme = gcd.RkIriScheme.frozen(path)
    existing = {"@graph": [{"@id": "estleg:RK_9_99_1_1", "estleg:caseNumber": "9-99-1/1", "estleg:rikObjectId": "500"}]}
    scheme.seed_from_graph(existing["@graph"])
    base = {"summary": "", "decision_type": "", "link": ""}
    rows = [
        {**base, "case_nr": "9-99-1/1", "object_id": oid, "date": date}
        for oid, date in (("400", "01.01.2099"), ("500", "02.01.2099"))
    ]
    with caplog.at_level(logging.WARNING):
        doc, _, _ = gcd.build_year_doc(2099, rows, scheme)
    ids = {n["estleg:rikObjectId"]: n["@id"] for n in doc["@graph"][1:]}
    # The on-disk holder (500) keeps the short IRI even though 400 < 500.
    assert ids == {"500": "estleg:RK_9_99_1_1", "400": "estleg:RK_9_99_1_1_400"}
    assert any("RK IRI collision" in r.message for r in caplog.records)
    gcd.write_rk_iri_collisions(scheme.collisions, scheme.holders, path=path)
    entries = json.loads(path.read_text(encoding="utf-8"))["collisions"]
    assert len(entries) == 122
    assert {"caseNumber": "9-99-1/1", "rikObjectId": "400", "iri": "estleg:RK_9_99_1_1_400", "shortFormHolder": "500"} in entries


def test_true_duplicate_rows_keep_the_short_iri_holder():
    """#392 dedup never leaves the surviving decision on its collision IRI."""
    base = {"summary": "", "decision_type": "Kohtuotsus", "link": "", "date": "01.01.2099"}
    rows = [{**base, "case_nr": "9-99-2/2", "object_id": oid} for oid in ("900", "800")]
    scheme = gcd.RkIriScheme()
    scheme.seed_from_graph([{"@id": "estleg:RK_9_99_2_2", "estleg:caseNumber": "9-99-2/2", "estleg:rikObjectId": "900"}])
    doc, emitted, skipped = gcd.build_year_doc(2099, rows, scheme)
    assert [n["@id"] for n in doc["@graph"][1:]] == ["estleg:RK_9_99_2_2"]
    assert emitted[0]["object_id"] == "900" and skipped == 1
    assert scheme.new_collisions == []


def test_choose_canonical_decision_prefers_frozen_short_iri():
    nodes = [
        {"@id": "estleg:RK_3_1_1_1_77", "estleg:caseNumber": "3-1-1/1"},
        {"@id": "estleg:RK_3_1_1_1", "estleg:caseNumber": "3-1-1/1"},
    ]
    assert gcd.choose_canonical_decision(nodes)["@id"] == "estleg:RK_3_1_1_1"


@pytest.mark.corpus
def test_every_committed_court_decision_iri_matches_frozen_scheme(corpus_krr):
    """Corpus gate: each CourtDecision @id is the frozen formula of its own fields."""
    collisions = gcd.load_rk_iri_collisions()
    rk_dir = corpus_krr.path("riigikohus")
    seen_twins = set()
    mismatches = []
    total = 0
    for path in sorted(rk_dir.glob("riigikohus_*_peep.json")):
        graph = json.loads(path.read_text(encoding="utf-8"))["@graph"]
        ids = {n.get("@id") for n in graph}
        for node in graph:
            if "estleg:CourtDecision" not in _types(node):
                continue
            total += 1
            case_nr = node["estleg:caseNumber"]
            oid = gcd._unwrap_literal(node["estleg:rikObjectId"])
            expected = gcd.frozen_rk_iri(case_nr, oid, collisions)
            if node["@id"] != expected:
                mismatches.append((path.name, node["@id"], expected))
            if (case_nr, oid) in collisions:
                seen_twins.add((case_nr, oid))
                assert gcd.rk_short_iri(case_nr) in ids, f"{node['@id']}: twin without short holder"
    assert not mismatches, mismatches[:10]
    assert seen_twins == set(collisions), "allowlist entries missing from the corpus"
    assert total >= 12_000


# ── fixture regeneration CLI ─────────────────────────────────────────────────


def _known_drift(case: str, rows: list[dict], committed: dict) -> list[dict]:
    _fixture, _rel, _derive, build, _write, layer = CASES[case]
    merged, _report = merge_overlays(build(rows, committed), committed, layer)
    old = {n.get("@id"): n for n in committed["@graph"]}
    drift = []
    for node in merged["@graph"]:
        prior = old.get(node.get("@id"))
        if prior is None:
            raise SystemExit(f"{case}: regeneration minted a new node {node.get('@id')}")
        for key in sorted(set(prior) | set(node)):
            if prior.get(key) != node.get(key):
                drift.append(
                    {"@id": node["@id"], "key": key, "committed": prior.get(key), "regenerated": node.get(key)}
                )
    if len(merged["@graph"]) != len(committed["@graph"]):
        raise SystemExit(f"{case}: regeneration changed the node count")
    return drift


def regen() -> None:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parent.parent / "krr_outputs"
    for case, (fixture_path, rel, derive, *_rest) in sorted(CASES.items()):
        committed = json.loads((root / rel).read_text(encoding="utf-8"))
        rows = derive(committed)
        payload = {
            "description": (
                "DERIVED fixture (#697): upstream rows reconstructed from the RAW "
                "fields of the committed peep (no raw cache exists in the tree). "
                "knownDrift lists reviewed values where the current generator "
                "differs from the published peep."
            ),
            "derivedFrom": f"krr_outputs/{rel}",
            "knownDrift": _known_drift(case, rows, committed),
            "rows": rows,
        }
        text = json.dumps(payload, ensure_ascii=False, indent=1) + "\n"
        if fixture_path.suffix == ".gz":
            with open(fixture_path, "wb") as raw:
                with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=9) as gz:
                    gz.write(text.encode("utf-8"))
        else:
            fixture_path.write_text(text, encoding="utf-8")
        print(f"{fixture_path.name}: {len(rows)} rows, {len(payload['knownDrift'])} known drift")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["regen"])
    parser.parse_args(argv)
    logging.disable(logging.WARNING)
    regen()


if __name__ == "__main__":
    main()

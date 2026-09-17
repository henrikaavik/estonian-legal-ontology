"""#457 — institution overlay: nominative labels, minister type, no dangling aliases."""

from __future__ import annotations

import json
from pathlib import Path

from estleg.extract_institutional_competence import (
    GENERIC_INSTITUTION_SLUGS,
    KESKKONNAAMET_SLUG,
    KESKKONNAINSPEKTSIOON_SLUG,
    SAMEAS_ALIASES,
    canonicalize_institution_label,
    detect_institutions,
    load_wikidata_institutions,
    named_institution_by_suffix,
    preferred_institution_type,
    wikidata_iri_for_slug,
)
REPO = Path(__file__).resolve().parent.parent
INST_DIR = REPO / "krr_outputs" / "institutions"


def _types(node: dict) -> list[str]:
    raw = node.get("@type") or []
    return [raw] if isinstance(raw, str) else [item for item in raw if isinstance(item, str)]


def _root(doc: dict, slug: str) -> dict:
    nid = f"estleg:Institution_{slug}"
    return next(n for n in doc["@graph"] if isinstance(n, dict) and n.get("@id") == nid)


def _iter_institution_files() -> list[tuple[str, dict]]:
    out = []
    for path in sorted(INST_DIR.glob("institution_*.json")):
        slug = path.stem.removeprefix("institution_")
        out.append((slug, json.loads(path.read_text(encoding="utf-8"))))
    return out


def test_competence_labels_are_nominative() -> None:
    leftover: list[str] = []
    for slug, doc in _iter_institution_files():
        for node in doc.get("@graph", []):
            if "estleg:Competence" not in _types(node):
                continue
            label = node.get("rdfs:label")
            if not isinstance(label, str) or " – " not in label:
                continue
            head = label.rsplit(" – ", 1)[0]
            assert canonicalize_institution_label(head) == head, (slug, label)
            leftover.append(head)
    assert leftover
    assert not any(head.endswith(("lt", "le", "st", "ga")) for head in leftover)


def test_ministers_are_not_ministries() -> None:
    assert preferred_institution_type("haridusminister", "ministry") == "minister"
    found = detect_institutions("haridusministril on õigus kehtestada määrus")
    assert any(suffix == "haridusminister" and itype == "minister" for _, suffix, itype in found)
    for slug, doc in _iter_institution_files():
        if not slug.endswith("minister") or slug.endswith("ministeerium"):
            continue
        assert _root(doc, slug)["estleg:institutionType"] == "minister"


def test_keskkonnaamet_not_labelled_inspektsioon() -> None:
    amet = json.loads((INST_DIR / f"institution_{KESKKONNAAMET_SLUG}.json").read_text(encoding="utf-8"))
    pred = json.loads((INST_DIR / f"institution_{KESKKONNAINSPEKTSIOON_SLUG}.json").read_text(encoding="utf-8"))
    assert _root(amet, KESKKONNAAMET_SLUG)["rdfs:label"] == "Keskkonnaamet"
    pred_node = _root(pred, KESKKONNAINSPEKTSIOON_SLUG)
    assert pred_node["rdfs:label"] == "Keskkonnainspektsioon"
    replaced = pred_node.get("dcterms:isReplacedBy")
    assert isinstance(replaced, dict)
    assert replaced["@id"] == f"estleg:Institution_{KESKKONNAAMET_SLUG}"


def test_abbreviation_alias_nodes_exist() -> None:
    for alias, canonical in SAMEAS_ALIASES.items():
        path = INST_DIR / f"institution_{alias}.json"
        assert path.is_file(), alias
        node = _root(json.loads(path.read_text(encoding="utf-8")), alias)
        same = node.get("owl:sameAs")
        ids = [same["@id"]] if isinstance(same, dict) else [item["@id"] for item in same]
        assert f"estleg:Institution_{canonical}" in ids


def test_real_institutions_mostly_have_wikidata() -> None:
    wd = load_wikidata_institutions()
    named = named_institution_by_suffix()
    real = 0
    with_wd = 0
    for slug, doc in _iter_institution_files():
        node = _root(doc, slug)
        itype = node.get("estleg:institutionType")
        is_real = slug not in GENERIC_INSTITUTION_SLUGS and (
            itype in {"ministry", "minister", "government", "parliament", "head_of_state"}
            or (itype == "court" and slug != "kohus")
            or slug in wd
            or slug in named
        )
        if not is_real:
            continue
        real += 1
        if wikidata_iri_for_slug(slug, wd):
            with_wd += 1
    assert real >= 50
    assert with_wd / real >= 0.80


def test_competence_edge_counts_unchanged_on_cleanup(tmp_path: Path) -> None:
    """Surgical remint must not drop appliesToProvision edges."""
    sample = INST_DIR / "institution_finantsinspektsioon.json"
    before = json.loads(sample.read_text(encoding="utf-8"))
    count = 0
    for node in before["@graph"]:
        refs = node.get("estleg:appliesToProvision") or []
        if isinstance(refs, list):
            count += len(refs)
    assert count > 10
    # Idempotent cleanup on a copy.
    dest = tmp_path / "institutions"
    dest.mkdir()
    dest.joinpath(sample.name).write_text(sample.read_text(encoding="utf-8"), encoding="utf-8")
    from estleg.extract_institutional_competence import cleanup_institution_overlay

    cleanup_institution_overlay(dest)
    after = json.loads((dest / sample.name).read_text(encoding="utf-8"))
    after_count = 0
    for node in after["@graph"]:
        refs = node.get("estleg:appliesToProvision") or []
        if isinstance(refs, list):
            after_count += len(refs)
    assert after_count == count

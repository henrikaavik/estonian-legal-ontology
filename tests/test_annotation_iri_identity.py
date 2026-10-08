"""Õiguskantsler annotation IRI identity: distinct opinions never share an ``@id``.

Regression for the duplicate ``estleg:Annotation_OK_…valjakutse_teenindamine`` node: two
different opinions were emitted under one IRI, so their ``annotationSourceUrl`` /
``annotationDate`` merged and tripped ``sh:maxCount``. Collision detection now runs on the
final emitted id, the #459 remint re-separates ids its common-prefix collapse merges, and
``--dedupe-sidecar-ids`` repairs a committed sidecar with the generator's own rule.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from estleg import generate_annotations as ga
from estleg.generate_annotations import Opinion

SLUG = "6iguskantsleri_seisukoht_oigusrikkumise_puudumise_kohta_valjakutse_teenindamine"
URL_A = f"https://www.oiguskantsler.ee/sites/default/files/2024-11/{SLUG}_politsei_poolt.pdf"
URL_B = f"https://www.oiguskantsler.ee/sites/default/files/2024-11/{SLUG}.pdf"


def _node(node_id: str, *, url: str, date: str, title: str, targets: list[str]) -> dict:
    node = {
        "@id": node_id,
        "@type": ["owl:NamedIndividual", "estleg:Annotation"],
        "estleg:annotates": [{"@id": t} for t in targets] if len(targets) > 1 else {"@id": targets[0]},
        "estleg:annotationText": f"{title}\n\nSisu.",
        "estleg:annotationType": "interpretation",
        "estleg:annotationSource": ga.ANNOTATION_SOURCE,
        "estleg:annotationSourceUrl": {"@value": url, "@type": "xsd:anyURI"},
        "estleg:annotationDate": {"@value": date, "@type": "xsd:date"},
        "rdfs:label": {"@value": f"{ga.ANNOTATION_LABEL_PREFIX}{title}", "@language": "et"},
    }
    return node


def _ids(path: Path) -> list[str]:
    return [n["@id"] for n in json.loads(path.read_text(encoding="utf-8"))["@graph"]]


def test_ids_that_collide_only_after_sanitize_get_distinct_iris() -> None:
    # Raw ids differ, but sanitize_id drops "." / "-" (non-range) -> same emitted local id.
    a = Opinion("arvamus.seadus", "Arvamus A", "https://www.oiguskantsler.ee/a.pdf", "2024-01-01", (), "")
    b = Opinion("arvamus-seadus", "Arvamus B", "https://www.oiguskantsler.ee/b.pdf", "2024-02-01", (), "")
    assert a.opinion_id != b.opinion_id
    assert ga.annotation_iri(a.opinion_id) == ga.annotation_iri(b.opinion_id)  # the trap

    out = ga.disambiguate_duplicate_opinion_ids([a, b])
    iris = [ga.annotation_iri(op.opinion_id) for op in out]
    assert len(set(iris)) == 2
    assert all(iri.startswith(f"{ga.ANNOTATION_ID_PREFIX}arvamusseadus_") for iri in iris)
    # Order independence carries over to the final-form detection.
    rev = ga.disambiguate_duplicate_opinion_ids([b, a])
    assert {ga.annotation_iri(op.opinion_id) for op in rev} == set(iris)


def test_non_colliding_opinion_ids_are_left_raw() -> None:
    ops = [Opinion("Unikaalne-id", "T", "https://www.oiguskantsler.ee/u.pdf", None, (), "")]
    assert ga.disambiguate_duplicate_opinion_ids(ops) == ops


def _write_fixture(path: Path) -> None:
    ga.write_sidecar(
        [
            _node(f"{ga.ANNOTATION_ID_PREFIX}aaa_first", url="https://www.oiguskantsler.ee/x.pdf",
                  date="2020-01-01", title="Esimene", targets=["estleg:OIGUSK_Map"]),
            _node(f"{ga.ANNOTATION_ID_PREFIX}{SLUG}", url=URL_A, date="2015-08-11",
                  title="Valjakutse politsei poolt", targets=["estleg:KORS_Map", "estleg:OIGUSK_Map"]),
            _node(f"{ga.ANNOTATION_ID_PREFIX}{SLUG}", url=URL_B, date="2015-04-24",
                  title="Valjakutse", targets=["estleg:OIGUSK_Map"]),
            _node(f"{ga.ANNOTATION_ID_PREFIX}zzz_last", url="https://www.oiguskantsler.ee/z.pdf",
                  date="2021-01-01", title="Viimane", targets=["estleg:OIGUSK_Map"]),
        ],
        out_path=path,
    )


def test_dedupe_sidecar_ids_repairs_only_colliders_and_is_idempotent(tmp_path: Path) -> None:
    sidecar = tmp_path / "oiguskantsler_seisukohad.jsonld"
    _write_fixture(sidecar)
    before = _ids(sidecar)

    stats = ga.dedupe_sidecar_ids(in_path=sidecar)
    after = _ids(sidecar)
    assert stats["renamed"] == 2
    assert len(after) == len(before) and len(set(after)) == len(after)
    # Order preserved; only the two colliding positions changed.
    assert [i for i, (x, y) in enumerate(zip(before, after)) if x != y] == [2, 3]

    # The repaired ids are exactly what the generator would now mint for these opinions.
    regenerated = ga.disambiguate_duplicate_opinion_ids(
        [
            Opinion(SLUG, "Valjakutse politsei poolt", URL_A, "2015-08-11", (), ""),
            Opinion(SLUG, "Valjakutse", URL_B, "2015-04-24", (), ""),
        ]
    )
    assert after[2:4] == [ga.annotation_iri(op.opinion_id) for op in regenerated]

    snapshot = sidecar.read_bytes()
    again = ga.dedupe_sidecar_ids(in_path=sidecar)
    assert again["renamed"] == 0
    assert sidecar.read_bytes() == snapshot


def test_dedupe_sidecar_ids_cli_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sidecar = tmp_path / "oiguskantsler_seisukohad.jsonld"
    _write_fixture(sidecar)
    original = ga.dedupe_sidecar_ids
    monkeypatch.setattr(ga, "dedupe_sidecar_ids", lambda: original(in_path=sidecar))
    assert ga.main(["--dedupe-sidecar-ids"]) == 0
    ids = _ids(sidecar)
    assert len(set(ids)) == len(ids) == 5


def test_remint_does_not_merge_into_another_documents_id(tmp_path: Path) -> None:
    # Pre-#459 per-act nodes "<slug>_KORS" + "<slug>_OIGUSK" (one document) collapse to the
    # common prefix "<slug>", which is the bare id of a DIFFERENT document.
    sidecar = tmp_path / "oiguskantsler_seisukohad.jsonld"
    ga.write_sidecar(
        [
            _node(f"{ga.ANNOTATION_ID_PREFIX}{SLUG}", url=URL_B, date="2015-04-24",
                  title="Valjakutse", targets=["estleg:OIGUSK_Map"]),
            _node(f"{ga.ANNOTATION_ID_PREFIX}{SLUG}_KORS", url=URL_A, date="2015-08-11",
                  title="Valjakutse politsei poolt", targets=["estleg:KORS_Map"]),
            _node(f"{ga.ANNOTATION_ID_PREFIX}{SLUG}_OIGUSK", url=URL_A, date="2015-08-11",
                  title="Valjakutse politsei poolt", targets=["estleg:OIGUSK_Map"]),
        ],
        out_path=sidecar,
    )
    ga.remint_annotation_sidecar(in_path=sidecar, provision_ids=set())
    ids = _ids(sidecar)[1:]
    assert len(ids) == 2 and len(set(ids)) == 2


# LFS artifact; runs in the json-validation `-m corpus` job.
@pytest.mark.corpus
def test_committed_annotation_sidecar_has_no_duplicate_ids() -> None:
    graph = json.loads(ga.SIDECAR_PATH.read_text(encoding="utf-8"))["@graph"]
    dupes = [i for i, n in Counter(node["@id"] for node in graph).items() if n > 1]
    assert dupes == []


def test_attribution_repair_never_changes_ids_or_order(tmp_path: Path) -> None:
    # #719: --repair-attribution rewrites targets and attribution fields only; the #743
    # identity (every @id, and node order) is untouched.
    sidecar = tmp_path / "oiguskantsler_seisukohad.jsonld"
    ga.write_sidecar(
        [
            _node(f"{ga.ANNOTATION_ID_PREFIX}{SLUG}", url=URL_B, date="2015-04-24",
                  title="Valjakutse", targets=["estleg:OIGUSK_Map"]),
            _node(f"{ga.ANNOTATION_ID_PREFIX}{SLUG}_2", url=URL_A, date="2015-08-11",
                  title="Valjakutse politsei poolt", targets=["estleg:KORS_Map", "estleg:OIGUSK_Map"]),
        ],
        out_path=sidecar,
    )
    before = _ids(sidecar)
    ga.repair_attribution(in_path=sidecar, provision_ids=set(), law_index=None, seed_path=None)
    assert _ids(sidecar) == before

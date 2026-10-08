"""#719 — no Õiguskantsler annotation misattribution.

Three contracts:

* a cited § is paired with ITS OWN act (the nearest preceding act mention in the same
  sentence), never crossed onto every act the opinion names (the old cartesian product put
  "HKMS § 112" onto PS/VangS/RLS too);
* project-authored prose (the seed paraphrase, the "Õiguskantsleri seisukoht. Teemad: …"
  template) is quarantined into ``estleg:editorialNote`` + ``estleg:editorialSource``;
  ``estleg:annotationText`` is verbatim source text only, flagged with ``estleg:isExcerpt``
  and, when the body is known, ``estleg:sourceTextLength``;
* ``--repair-attribution`` applies both to the committed sidecar offline and idempotently.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from estleg import generate_annotations as ga
from estleg.generate_annotations import (
    EDITORIAL_SOURCE,
    TOPIC_TEMPLATE_PREFIX,
    Opinion,
    _LawIndex,
    act_iri_prefix,
    annotates_iris,
    build_annotations_for_opinion,
    pair_cited_sections,
    provision_iris_for_acts,
)

HKMS = "estleg:HKMS_Map"
PS = "estleg:PS_Map"
VANGS = "estleg:VANGIS_Map"
RLS = "estleg:RLS_Map"
KARS = "estleg:KarS_Map"
VOS = "estleg:VOS_Map"

_TITLES = {
    "Halduskohtumenetluse seadustik": HKMS,
    "Eesti Vabariigi põhiseadus": PS,
    "Vangistusseadus": VANGS,
    "Riigilõivuseadus": RLS,
    "Karistusseadustik": KARS,
    "Võlaõigusseadus": VOS,
}

TICKET_TEXT = "HKMS § 112, § 15 ja § 24 ning põhiseaduse § 15"


def _index() -> _LawIndex:
    """A small in-memory law index: nominative titles + their inflected forms."""
    by_name: dict[str, str] = {}
    for title, iri in _TITLES.items():
        norm = ga._norm_name(title)
        by_name[norm] = iri
        for form in ga._genitive_variants(norm):
            by_name.setdefault(form, iri)
    return _LawIndex(by_name=by_name)


def _all_provisions(sections: list[str]) -> set[str]:
    """Every listed § exists in every act — the worst case for a cartesian product."""
    return {f"{act_iri_prefix(iri)}_Par_{s}" for iri in _TITLES.values() for s in sections}


# ---------------------------------------------------------------------------
# Proximity pairing
# ---------------------------------------------------------------------------


def test_single_act_title() -> None:
    assert pair_cited_sections("Karistusseadustiku § 381¹ põhiseaduspärasus", _index()) == [(KARS, "381_1")]


def test_ticket_example_pairs_each_section_with_its_own_act() -> None:
    assert pair_cited_sections(TICKET_TEXT, _index()) == [
        (HKMS, "112"),
        (HKMS, "15"),
        (HKMS, "24"),
        (PS, "15"),
    ]


def test_multiple_named_acts_in_one_sentence() -> None:
    text = "Põhiseadusega on vastuolus vangistusseaduse § 114 lg 1 ja riigilõivuseaduse § 57 lõige 3."
    assert pair_cited_sections(text, _index()) == [(VANGS, "114"), (RLS, "57")]


def test_section_before_any_act_mention_is_not_paired() -> None:
    assert pair_cited_sections("§ 12 ja karistusseadustiku § 5", _index()) == [(KARS, "5")]


def test_ning_pohiseaduse_alias_resolves_to_the_constitution() -> None:
    pairs = pair_cited_sections("karistusseadustiku § 5 ning põhiseaduse § 15", _index())
    assert pairs == [(KARS, "5"), (PS, "15")]


def test_sentence_boundary_stops_pairing() -> None:
    assert pair_cited_sections("Karistusseadustik on oluline. § 7 kehtib kõigile.", _index()) == []


def test_abbreviation_period_is_not_a_sentence_boundary() -> None:
    text = "Karistusseadustiku § 7 lg 2. punkt ja § 9 on selged."
    assert pair_cited_sections(text, _index()) == [(KARS, "7"), (KARS, "9")]


def test_unindexed_act_claims_its_section() -> None:
    # relvaseadus is not in the index: its § must not fall to the earlier karistusseadustik.
    text = "karistusseadustiku § 5 ja relvaseaduse § 3"
    assert pair_cited_sections(text, _index()) == [(KARS, "5")]


def test_parenthesised_section_of_an_unindexed_act_is_claimed() -> None:
    # Real sidecar text: § 43 is Tallinna põhimäärus's, not the KOKS-like act before it.
    text = "Karistusseadustiku (§ 27 ja § 45 lg 3) ja Tallinna põhimääruse (§ 43 lg 3) järgi"
    assert pair_cited_sections(text, _index()) == [(KARS, "27"), (KARS, "45")]


@pytest.mark.parametrize(
    "text",
    [
        "Karistusseadustiku § 5 ja Narva Linnavolikogu 20.02.2014 määruse nr 6 „Koduteenuste kord“ § 3 ja § 7",
        "Karistusseadustiku § 5 ning justiitsministri määruse nr 72 „Karistusseadustiku rakendamise kord“ § 3",
        "Karistusseadustiku § 5 kui ka Kiili Vallavolikogu 17.03.2016 määruse nr 9 § 3 lg 7",
    ],
)
def test_regulation_cited_by_number_or_quoted_title_claims_its_sections(text: str) -> None:
    assert pair_cited_sections(text, _index()) == [(KARS, "5")]


def test_glued_footnote_marker_still_names_the_act() -> None:
    text = "Analüüsisin põhiseaduse1 § 139 lõike 1 ning karistusseadustiku2 § 1 lõike 1"
    assert pair_cited_sections(text, _index()) == [(PS, "139"), (KARS, "1")]


def test_quoted_title_with_parenthetical_alias_claims_its_section() -> None:
    text = "Karistusseadustiku § 5 ja kaitseministri määruse nr 16 „Ehitiste kord“ (määrus) § 61 lõike 2"
    assert pair_cited_sections(text, _index()) == [(KARS, "5")]


def test_unresolvable_abbreviation_claims_its_section() -> None:
    # "XyzS" is abbreviation-shaped but unknown: it blocks, it does not hand § 9 to PS.
    assert pair_cited_sections("PS § 1 ja XyzS § 9", _index()) == [(PS, "1")]


def test_anaphora_refers_to_the_last_named_act() -> None:
    text = "Vangistusseaduse § 15 p 2 (koostoimes sama seaduse § 95 lõigetega 1 ja 2)"
    assert pair_cited_sections(text, _index()) == [(VANGS, "15"), (VANGS, "95")]


def test_inflected_abbreviation_is_a_mention() -> None:
    assert pair_cited_sections("Vt KarS-i § 5.", _index()) == [(KARS, "5")]


# ---------------------------------------------------------------------------
# No cartesian product
# ---------------------------------------------------------------------------


def test_no_cartesian_product_for_the_ticket_example() -> None:
    acts = [HKMS, PS, VANGS, RLS]
    provisions = _all_provisions(["112", "15", "24"])
    targets = provision_iris_for_acts(acts, TICKET_TEXT, provisions, _index())
    assert targets == [
        "estleg:HKMS_Par_112",
        "estleg:HKMS_Par_15",
        "estleg:HKMS_Par_24",
        "estleg:PS_Par_15",
        VANGS,  # named act with no § of its own -> act-level fallback
        RLS,
    ]
    assert "estleg:PS_Par_112" not in targets
    assert "estleg:VANGIS_Par_112" not in targets


@pytest.mark.parametrize(
    "text",
    [
        TICKET_TEXT,
        "Karistusseadustiku § 5 ja võlaõigusseaduse § 40. Põhiseaduse § 15 kohaselt § 7.",
        "§ 3 ja § 4. Riigilõivuseaduse § 57 ning vangistusseaduse § 114 ja sama seaduse § 15.",
    ],
)
def test_every_section_target_comes_from_a_pair(text: str) -> None:
    acts = list(_TITLES.values())
    provisions = _all_provisions(["3", "4", "5", "7", "15", "24", "40", "57", "112", "114"])
    targets = provision_iris_for_acts(acts, text, provisions, _index())
    pairs = {(act_iri_prefix(iri), section) for iri, section in pair_cited_sections(text, _index())}
    par_targets = [t for t in targets if "_Par_" in t]
    assert par_targets, "fixture should produce § targets"
    for target in par_targets:
        prefix, section = target.split("_Par_", 1)
        assert (prefix, section) in pairs, target
    # Every § target count is bounded by the citations, never citations x acts.
    assert len(par_targets) == len(pairs)


# ---------------------------------------------------------------------------
# Quarantine of project prose
# ---------------------------------------------------------------------------


def test_seed_paraphrase_is_an_editorial_note_not_annotation_text() -> None:
    op = Opinion(
        "seed-1",
        "Karistusseadustiku § 381¹ põhiseaduspärasus",
        "https://www.oiguskantsler.ee/x/seed-1",
        "2026-05-06",
        ("Karistusseadustik",),
        "",
        editorial_note="Õiguskantsler leidis, et säte ei ole vastuolus põhiseadusega.",
    )
    node = build_annotations_for_opinion(op, _index()).annotations[0]
    assert node["estleg:annotationText"] == op.title
    assert node["estleg:editorialNote"] == op.editorial_note
    assert node["estleg:editorialSource"] == EDITORIAL_SOURCE
    assert node["estleg:isExcerpt"] is True
    assert "estleg:sourceTextLength" not in node  # body unknown: never invented
    assert node["estleg:annotationSource"] == "Õiguskantsler"
    assert node["estleg:annotationSourceUrl"]["@value"] == op.url


def test_topic_template_is_an_editorial_note() -> None:
    op = Opinion("tags", "Võlaõigusseaduse tõlgendamine", "https://x/tags.pdf", None, (), "", tags=("Raha ja vara",))
    node = build_annotations_for_opinion(op, _index()).annotations[0]
    assert node["estleg:annotationText"] == op.title
    assert node["estleg:editorialNote"] == f"{TOPIC_TEMPLATE_PREFIX}Raha ja vara."
    assert TOPIC_TEMPLATE_PREFIX not in node["estleg:annotationText"]


def test_verbatim_body_is_annotation_text_with_length() -> None:
    body = "Võlaõigusseaduse § 40 kohaldamisel tuleb arvestada tarbija huve."
    op = Opinion("body", "Üldine seisukoht", "https://x/body.pdf", None, (), body)
    node = build_annotations_for_opinion(op, _index()).annotations[0]
    assert node["estleg:annotationText"] == f"{op.title}\n\n{body}"
    assert node["estleg:isExcerpt"] is False
    assert node["estleg:sourceTextLength"] == len(body)
    assert "estleg:editorialNote" not in node


def test_truncated_body_is_an_excerpt_with_full_length() -> None:
    body = "Võlaõigusseaduse kohaldamine. " + "Lause on pikk ja sisukas. " * 200
    op = Opinion("long", "Pikk seisukoht", "https://x/long.pdf", None, (), body.strip())
    node = build_annotations_for_opinion(op, _index()).annotations[0]
    assert len(node["estleg:annotationText"]) <= ga.ANNOTATION_TEXT_MAX_CHARS
    assert node["estleg:isExcerpt"] is True
    assert node["estleg:sourceTextLength"] == len(body.strip())


def test_no_url_means_no_oiguskantsler_attribution() -> None:
    op = Opinion("nourl", "Võlaõigusseaduse tõlgendamine", "", None, (), "Võlaõigusseadus kehtib.")
    node = build_annotations_for_opinion(op, _index()).annotations[0]
    assert "estleg:annotationSource" not in node
    assert "estleg:annotationSourceUrl" not in node


def test_repo_seed_prose_never_reaches_annotation_text() -> None:
    for op in ga.load_seed_opinions(ga.SEED_PATH):
        assert op.editorial_note
        assert ga._annotation_text(op) == op.title
        assert ga._editorial_note(op) == op.editorial_note


# ---------------------------------------------------------------------------
# run() keeps § links on the default path
# ---------------------------------------------------------------------------


def _write_peep(krr: Path, slug: str, act_iri: str, title: str, sections: list[str]) -> None:
    prefix = act_iri_prefix(act_iri)
    graph = [{"@id": act_iri, "@type": ["estleg:Act", "estleg:Law", "owl:Ontology"], "dc:source": title}]
    graph += [{"@id": f"{prefix}_Par_{s}", "@type": ["owl:NamedIndividual"]} for s in sections]
    (krr / f"{slug}_peep.json").write_text(
        json.dumps({"@context": {"estleg": ga.CONTEXT["estleg"]}, "@graph": graph}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_default_run_keeps_provision_links(tmp_path: Path) -> None:
    krr = tmp_path / "krr_outputs"
    krr.mkdir()
    _write_peep(krr, "volaoigusseadus", VOS, "Võlaõigusseadus", ["40"])
    _write_peep(krr, "karistusseadustik", KARS, "Karistusseadustik", ["40", "5"])
    seed = tmp_path / "seed.json"
    seed.write_text(
        json.dumps(
            {
                "opinions": [
                    {
                        "id": "vos-kars",
                        "title": "Võlaõigusseaduse § 40 ja karistusseadustiku § 5 koostoime",
                        "date": "2024-03-15",
                        "url": "https://www.oiguskantsler.ee/x/vos-kars",
                        "laws": ["Võlaõigusseadus", "Karistusseadustik"],
                        "editorial_note": "Projekti kokkuvõte.",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    out = krr / "annotations" / "sidecar.jsonld"
    rc = ga.run(
        scrape=False, limit=5, seed_path=seed, krr_dir=krr, out_path=out,
        coverage_path=krr / "coverage.json", cache_dir=None,
    )
    assert rc == 0
    node = next(n for n in json.loads(out.read_text(encoding="utf-8"))["@graph"] if ga._is_annotation_node(n))
    # § 40 belongs to VÕS only and § 5 to KarS only — no KarS § 40 although it exists.
    assert annotates_iris(node) == ["estleg:VOS_Par_40", "estleg:KarS_Par_5"]
    assert node["estleg:editorialNote"] == "Projekti kokkuvõte."


# ---------------------------------------------------------------------------
# --repair-attribution on a tmp sidecar
# ---------------------------------------------------------------------------


def _ann(node_id: str, title: str, body: str, targets: list[str], *, url: str | None = "https://x/y") -> dict:
    node = {
        "@id": f"estleg:Annotation_OK_{node_id}",
        "@type": ["owl:NamedIndividual", "estleg:Annotation"],
        "estleg:annotates": [{"@id": t} for t in targets],
        "estleg:annotationText": f"{title}\n\n{body}" if body else title,
        "estleg:annotationType": "interpretation",
        "estleg:annotationSource": "Õiguskantsler",
        "rdfs:label": {"@value": f"{ga.ANNOTATION_LABEL_PREFIX}{title}", "@language": "et"},
    }
    if url:
        node["estleg:annotationSourceUrl"] = {"@value": f"{url}/{node_id}", "@type": "xsd:anyURI"}
    return node


def _sidecar(tmp_path: Path, nodes: list[dict]) -> Path:
    path = tmp_path / "sidecar.jsonld"
    header = {"@id": "estleg:Annotations_Oiguskantsler_Map", "@type": ["owl:Ontology"]}
    ga.save_json(path, {"@context": dict(ga.CONTEXT), "@graph": [header, *nodes]})
    return path


def _repair(path: Path, provisions: set[str], seed: Path | None = None) -> dict:
    return ga.repair_attribution(in_path=path, provision_ids=provisions, law_index=_index(), seed_path=seed)


def test_repair_removes_cross_product_and_is_idempotent(tmp_path: Path) -> None:
    provisions = _all_provisions(["112", "15", "24"])
    cartesian = sorted(f"{act_iri_prefix(a)}_Par_{s}" for a in (HKMS, PS, VANGS, RLS) for s in ("112", "15", "24"))
    path = _sidecar(tmp_path, [_ann("ticket", "Arvamus", TICKET_TEXT, cartesian)])

    stats = _repair(path, provisions)
    node = json.loads(path.read_text(encoding="utf-8"))["@graph"][1]
    assert set(annotates_iris(node)) == {
        "estleg:HKMS_Par_112", "estleg:HKMS_Par_15", "estleg:HKMS_Par_24", "estleg:PS_Par_15", VANGS, RLS,
    }
    assert stats["before"]["targets"] == 12
    assert stats["after"]["targets"] == 6
    assert stats["removed_par_targets"] == 8
    assert stats["added_act_targets"] == 2
    assert node["estleg:isExcerpt"] is False  # short text: provably the whole body
    assert node["estleg:sourceTextLength"] == len(TICKET_TEXT)

    first = path.read_bytes()
    again = _repair(path, provisions)
    assert again["changed"] is False
    assert path.read_bytes() == first


def test_repair_quarantines_template_and_seed_paraphrase(tmp_path: Path) -> None:
    seed = tmp_path / "seed.json"
    seed.write_text(
        json.dumps({"opinions": [{"title": "S", "laws": [], "editorial_note": "Projekti   ümberjutustus."}]}),
        encoding="utf-8",
    )
    nodes = [
        _ann("tmpl", "Pealkiri", f"{TOPIC_TEMPLATE_PREFIX}Raha ja vara.", [VOS]),
        _ann("para", "Teine", "Projekti ümberjutustus.", [VOS]),
        _ann("nourl", "Kolmas", "Võlaõigusseadus kehtib.", [VOS], url=None),
    ]
    path = _sidecar(tmp_path, nodes)
    stats = _repair(path, set(), seed=seed)
    tmpl, para, nourl = json.loads(path.read_text(encoding="utf-8"))["@graph"][1:]
    assert stats["quarantined_bodies"] == 2
    for node, title in ((tmpl, "Pealkiri"), (para, "Teine")):
        assert node["estleg:annotationText"] == title
        assert node["estleg:editorialSource"] == EDITORIAL_SOURCE
        assert node["estleg:isExcerpt"] is True
        assert "estleg:sourceTextLength" not in node
    assert tmpl["estleg:editorialNote"].startswith(TOPIC_TEMPLATE_PREFIX)
    # New keys sit right after annotationText.
    keys = list(tmpl)
    assert keys[keys.index("estleg:annotationText") + 1] == "estleg:editorialNote"
    assert "estleg:annotationSource" not in nourl
    assert stats["unattributed_no_url"] == 1
    assert _repair(path, set(), seed=seed)["changed"] is False


def test_repair_marks_window_length_text_as_excerpt_and_keeps_existing_flags(tmp_path: Path) -> None:
    long_body = "Lause. " * 300  # > the truncation window: completeness cannot be shown
    known = _ann("known", "Teadaolev", "Lühike.", [VOS])
    known["estleg:isExcerpt"] = True  # set by a generator run that knew more: keep it
    path = _sidecar(tmp_path, [_ann("long", "Pikk", long_body.strip()[:1900], [VOS]), known])
    _repair(path, set())
    long_node, known_node = json.loads(path.read_text(encoding="utf-8"))["@graph"][1:]
    assert long_node["estleg:isExcerpt"] is True
    assert "estleg:sourceTextLength" not in long_node
    assert known_node["estleg:isExcerpt"] is True
    assert "estleg:sourceTextLength" not in known_node


def test_remint_uses_the_same_pairing_rule(tmp_path: Path) -> None:
    provisions = _all_provisions(["112", "15", "24"])
    cartesian = [f"{act_iri_prefix(a)}_Par_{s}" for a in (HKMS, PS) for s in ("112", "15", "24")]
    path = _sidecar(tmp_path, [_ann("ticket", "Arvamus", TICKET_TEXT, cartesian)])
    ga.remint_annotation_sidecar(in_path=path, provision_ids=provisions, law_index=_index())
    node = next(n for n in json.loads(path.read_text(encoding="utf-8"))["@graph"] if ga._is_annotation_node(n))
    assert annotates_iris(node) == [
        "estleg:HKMS_Par_112", "estleg:HKMS_Par_15", "estleg:HKMS_Par_24", "estleg:PS_Par_15",
    ]


# ---------------------------------------------------------------------------
# Corpus gate (LFS sidecar + root peeps)
# ---------------------------------------------------------------------------


@pytest.mark.corpus
def test_committed_sidecar_has_no_misattribution(tmp_path: Path) -> None:
    doc = json.loads(ga.SIDECAR_PATH.read_text(encoding="utf-8"))
    nodes = [n for n in doc["@graph"] if ga._is_annotation_node(n)]
    assert len(nodes) >= 3000
    index = ga.build_law_index()
    seed_notes = {op.editorial_note for op in ga.load_seed_opinions(ga.SEED_PATH)}
    offenders: list[str] = []
    for node in nodes:
        text = node["estleg:annotationText"]
        pairs = {(act_iri_prefix(iri), section) for iri, section in pair_cited_sections(text, index)}
        for target in annotates_iris(node):
            if "_Par_" in target:
                prefix, section = target.split("_Par_", 1)
                if (prefix, section) not in pairs:
                    offenders.append(f"{node['@id']} -> {target}")
        assert isinstance(node.get("estleg:isExcerpt"), bool), node["@id"]
        if node.get("estleg:annotationSource") == "Õiguskantsler":
            assert ga._literal_value(node.get("estleg:annotationSourceUrl")), node["@id"]
        assert TOPIC_TEMPLATE_PREFIX not in text, node["@id"]
        assert not any(note and note in text for note in seed_notes), node["@id"]
    assert not offenders, f"{len(offenders)} § targets of an act the text does not cite them for: {offenders[:10]}"

    # The committed sidecar is already repaired: the repair is a no-op on it.
    stats = ga.repair_attribution(
        out_path=tmp_path / "repaired.jsonld", provision_ids=ga.collect_provision_ids(), law_index=index
    )
    assert stats["changed"] is False

"""Real-corpus invariant tests (#591).

The rest of the suite is *fixtures-only*: every test that touches
``combined_ontology.jsonld`` writes a synthetic one-node file under
``tmp_path``, and the only real-data reads are ``controlled_vocabulary``
+ ``harmonisation_report``. So ``pytest`` green has never said anything
about the *shipped* graph — which is exactly how #561/#571/#574 all
shipped green, and how two tests came to *certify* the #571/#574 bugs as
expected output.

This module is the structural fix: it loads the REAL committed
``krr_outputs/`` artifacts and asserts golden facts + cross-artifact
invariants, so a regression in the *data* (not just the generator code)
fails ``pytest``.

Two tiers, matched to what each CI job has on disk:

* **Default (unmarked).** Load the non-LFS committed artifacts — the
  1,190 ``*_peep.json`` files, ``INDEX.json``,
  ``transposition_mapping.json``, and the harmonisation report + the law
  peeps PR #633 migrated — and assert real values. These need no git-LFS
  and run in the lightweight ``pytest`` CI job: the first real-artifact
  assertions the default suite has ever carried.
* **``@pytest.mark.corpus`` (opt-in).** The checks that need the git-LFS
  ``combined_ontology.jsonld`` materialised (the standalone zero-dangling
  closure gate) or that sweep the whole version layer (#574 point-in-time
  non-overlap). ``tests/conftest.py`` auto-skips the ``corpus`` marker, so
  the default job skips these; the ``json-validation`` CI job — which
  pulls the LFS combined graph — opts in with ``pytest -m corpus``.
"""

import json
from pathlib import Path

import pytest

KRR = Path(__file__).resolve().parent.parent / "krr_outputs"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _load(rel: str) -> dict:
    with open(KRR / rel, encoding="utf-8") as f:
        return json.load(f)


def _ref_ids(value) -> set[str]:
    """Collect the ``@id`` strings from an estleg object-ref value.

    The value of an object property is either a single ``{"@id": ...}``
    mapping or a list of them; normalise both (and a bare ``None``) to a
    set of id strings.
    """
    if value is None:
        return set()
    if isinstance(value, dict):
        value = [value]
    ids: set[str] = set()
    for item in value:
        if isinstance(item, dict) and isinstance(item.get("@id"), str):
            ids.add(item["@id"])
    return ids


def _peep_directive_fields(rel: str) -> tuple[set[str], set[str], bool]:
    """Aggregate (transposesDirective, harmonisedWith, deprecated) for a peep.

    Aggregating across every node in the peep — rather than assuming the
    act root is ``@graph[0]`` — keeps the assertion robust to node ordering
    and to the fields landing on a Part node.
    """
    doc = _load(rel)
    transposes: set[str] = set()
    harmonised: set[str] = set()
    deprecated = False
    for node in doc.get("@graph", []):
        if not isinstance(node, dict):
            continue
        transposes |= _ref_ids(node.get("estleg:transposesDirective"))
        harmonised |= _ref_ids(node.get("estleg:harmonisedWith"))
        if node.get("owl:deprecated") is True:
            deprecated = True
    return transposes, harmonised, deprecated


def _date(value) -> str | None:
    """Extract the ISO date string from an ``xsd:date`` typed literal.

    ISO ``YYYY-MM-DD`` strings sort lexicographically == chronologically,
    so callers can compare the returned strings directly.
    """
    if isinstance(value, dict):
        v = value.get("@value")
        return v if isinstance(v, str) else None
    return value if isinstance(value, str) else None


# --------------------------------------------------------------------------- #
# Default tier — non-LFS committed artifacts (run in the lightweight CI job)
# --------------------------------------------------------------------------- #
def test_index_is_internally_consistent():
    """INDEX.json must agree with its own body — the manifest a consumer
    trusts to enumerate the corpus."""
    idx = _load("INDEX.json")
    laws = idx["laws"]
    assert len(laws) == idx["total_laws"], (
        f"INDEX total_laws={idx['total_laws']} but body lists {len(laws)} laws"
    )
    assert sum(len(law["files"]) for law in laws) == idx["total_files"], (
        "INDEX total_files disagrees with the per-law file lists"
    )
    names = [law["name"] for law in laws]
    assert len(set(names)) == len(names), "duplicate law names in INDEX.json"
    assert all(law.get("files") for law in laws), "INDEX law with empty files list"
    # The peep files the manifest points at must actually be on disk.
    missing = [
        f
        for law in laws
        for f in law["files"]
        if not (KRR / f).exists()
    ]
    assert not missing, f"{len(missing)} INDEX-listed files absent, e.g. {missing[:5]}"


def test_all_peeps_are_structurally_wellformed():
    """Every committed ``*_peep.json`` loads and is shaped like JSON-LD —
    a real-data sweep the synthetic-fixture suite never performed."""
    peeps = sorted(KRR.glob("*_peep.json"))
    assert len(peeps) > 1000, f"only {len(peeps)} peep files — corpus not populated?"
    malformed: list[str] = []
    for path in peeps:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        graph = doc.get("@graph")
        if "@context" not in doc or not isinstance(graph, list) or not graph:
            malformed.append(path.name)
            continue
        if any(not isinstance(n, dict) or not isinstance(n.get("@id"), str) for n in graph):
            malformed.append(path.name)
    assert not malformed, f"{len(malformed)} malformed peeps, e.g. {malformed[:5]}"


def test_transposition_mapping_header_matches_body():
    """The exact invariant a manual edit silently broke across PR #633's
    review rounds: the header count must equal the body length, and no
    ``matched_law_name`` may carry a ``_peep`` filename-stem suffix."""
    tm = _load("transposition_mapping.json")
    mappings = tm["mappings"]
    assert tm["total_matched"] == len(mappings), (
        f"total_matched={tm['total_matched']} but {len(mappings)} mappings"
    )
    suffixed = [m["matched_law_name"] for m in mappings if m["matched_law_name"].endswith("_peep")]
    assert not suffixed, f"matched_law_name carries a _peep stem: {suffixed[:5]}"


def test_harmonisation_report_directive_count_matches_entries():
    """``directives_with_parallels`` must equal the entry count — invariant
    (c) of ``validate_harmonisation_symmetry``, asserted here at pytest
    level so a stale report fails the default suite."""
    report = _load("harmonisation/harmonisation_report.json")
    entries = report["harmonisation_entries"]
    assert report["directives_with_parallels"] == len(entries)
    assert all(e.get("estonian_laws") for e in entries), (
        "harmonisation entry with empty estonian_laws"
    )


def test_directive_links_live_on_canonical_acts_not_deprecated():
    """Golden facts locking in PR #633 / #631: the directive links sit on
    the canonical acts, and the deprecated VÕS/TLS families carry none.

    These are the exact facts that regressed across four review rounds —
    the Parental Leave directive (31996L0034) was orphaned in R1 when only
    ``harmonisedWith`` moved and its sole (deprecated TLS) mapping row was
    dropped; it is restored onto the canonical ``toolepingu_seadus``.
    """
    # Canonical acts carry the migrated directive links.
    tls_transposes, _, tls_deprecated = _peep_directive_fields("toolepingu_seadus_peep.json")
    assert not tls_deprecated
    assert "estleg:EU_31996L0034" in tls_transposes, (
        "toolepingu_seadus lost the restored Parental Leave directive (31996L0034)"
    )

    ari_transposes, _, ari_deprecated = _peep_directive_fields("ariseadustik_peep.json")
    assert not ari_deprecated
    assert {"estleg:EU_32007L0036", "estleg:EU_32009L0109"} <= ari_transposes, (
        "ariseadustik lost the directives it should have gained"
    )

    # Deprecated families carry NEITHER directive field and are flagged.
    for stem in ("volaigusseadus_osa1", "toolepinguseadus", "ariseadustik1"):
        transposes, harmonised, deprecated = _peep_directive_fields(f"{stem}_peep.json")
        assert deprecated is True, f"{stem} should be owl:deprecated"
        assert not transposes, f"{stem} (deprecated) still carries transposesDirective"
        assert not harmonised, f"{stem} (deprecated) still carries harmonisedWith"


# --------------------------------------------------------------------------- #
# Opt-in tier — needs the git-LFS combined graph / sweeps the version layer.
# The json-validation CI job pulls combined and runs `pytest -m corpus`.
# --------------------------------------------------------------------------- #
@pytest.mark.corpus
def test_combined_graph_is_closed():
    """The shipped ``combined_ontology.jsonld`` loads standalone with zero
    dangling ``estleg:`` object refs — the real-data check the suite has
    only ever run against synthetic one-node fixtures."""
    import validate_all

    combined = KRR / "combined_ontology.jsonld"
    if not combined.exists() or validate_all._is_lfs_pointer(combined):
        pytest.skip("combined_ontology.jsonld is an un-materialised LFS pointer")

    validate_all.errors.clear()
    validate_all.warnings.clear()
    validate_all.validate_combined_graph_closure(KRR)
    assert validate_all.errors == [], validate_all.errors


@pytest.mark.corpus
def test_provision_version_intervals_do_not_overlap():
    """Real point-in-time invariant (#574): walking ``supersededByVersion``,
    each version must end strictly before its successor begins. The unit
    test in ``test_generate_provision_versions`` asserts this on a synthetic
    chain; this sweeps the shipped sidecars, where it was 100% violated
    before the #574 regeneration."""
    sidecars = sorted((KRR / "provision_versions").glob("*.jsonld"))
    assert len(sidecars) > 100, f"only {len(sidecars)} version sidecars — layer not populated?"
    overlaps: list[tuple[str, str, str, str]] = []
    for path in sidecars:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        nodes = {
            n["@id"]: n
            for n in doc.get("@graph", [])
            if isinstance(n, dict) and isinstance(n.get("@id"), str)
        }
        for node in nodes.values():
            succ_ref = node.get("estleg:supersededByVersion")
            succ_id = succ_ref.get("@id") if isinstance(succ_ref, dict) else succ_ref
            successor = nodes.get(succ_id) if isinstance(succ_id, str) else None
            if successor is None:
                continue
            valid_to = _date(node.get("estleg:versionValidTo"))
            next_from = _date(successor.get("estleg:versionValidFrom"))
            if valid_to and next_from and valid_to >= next_from:
                overlaps.append((path.name, node["@id"], valid_to, next_from))
    assert not overlaps, (
        f"{len(overlaps)} overlapping version interval(s) (#574), e.g. {overlaps[:3]}"
    )

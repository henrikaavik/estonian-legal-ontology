#!/usr/bin/env python3
"""Emit the machine-readable inter-release change record (#549, #713).

Default mode (#713) — **provision-level release delta**, uncapped: every law
peep listed in ``krr_outputs/INDEX.json`` at a git ref (default: the latest
``v*`` tag, ``git tag --sort=-v:refname``) is compared with the same law in
the working tree (or ``--to-ref``). Old files are read with
``git cat-file --batch`` (``<ref>:<path>``); Git LFS aggregates are never
needed. Provision nodes (``estleg:LegalProvision`` / ``estleg:Subsection``)
are keyed on ``@id``:

* **added** / **removed** — the IRI exists on one side only;
* **changed** — ``estleg:legalText``, ``estleg:summary`` or a temporal field
  (:data:`TEMPORAL_FIELDS`) differs.

A removed and an added IRI that are the same provision re-minted under a new
prefix are linked with ``replacedBy`` / ``replaces`` (:func:`pair_reminted_iris`).

Laws are classified as added / removed / deprecated (``deprecated_laws`` in
INDEX). Added and changed provisions carry ``versionValidFrom`` (the latest
``estleg:versionValidFrom`` of the provision) when the working-tree
``provision_versions/<law>.jsonld`` sidecar has one.

Outputs:

* ``krr_outputs/changes-<version>.jsonld`` — ``dcat:Dataset`` /
  ``estleg:ReleaseDelta`` with complete counts. The added / removed / changed
  IRI lists are inline when their total is at most :data:`JSONL_THRESHOLD`;
  above it they move to the JSONL sibling (``estleg:listedInline false``).
* ``krr_outputs/changes-<version>.jsonl`` — one JSON object per law-level and
  provision-level change (written only above the threshold).
* ``krr_outputs/reports/release_changes_report.json`` — counts, refs
  compared, field-level change counts, top laws.

``--mode index-deprecated`` keeps the original #549 snapshot (INDEX
``deprecated_laws`` vs live ``laws``, capped at ``--cap``) and ``--old`` /
``--new`` the plain IRI-set diff of two JSON-LD files.

    python3 scripts/emit_release_changes.py                    # v<latest> → working tree
    python3 scripts/emit_release_changes.py --from-ref v1.0.0 --to-ref HEAD
    python3 scripts/emit_release_changes.py --mode index-deprecated
    python3 scripts/emit_release_changes.py --old old.jsonld --new new.jsonld
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from estleg.estleg_common import (
    BUILD_EVALUATION_DATE,
    CONTEXT,
    KRR_DIR,
    NS,
    ONTOLOGY_VERSION,
    REPO_ROOT,
    act_root_node,
    save_json,
)

LISTED_IRI_CAP = 50
DEFAULT_OUTPUT = KRR_DIR / f"changes-{ONTOLOGY_VERSION}.jsonld"
INDEX_PATH = KRR_DIR / "INDEX.json"
REPORT_PATH = KRR_DIR / "reports" / "release_changes_report.json"

OLD_LABEL_DEFAULT = "INDEX deprecated_laws (removed/replaced)"
NEW_LABEL_DEFAULT = f"INDEX laws {ONTOLOGY_VERSION}"


def law_name_to_iri(name: str) -> str:
    """Map an INDEX law slug to a stable estleg IRI."""
    slug = name.strip()
    if slug.startswith(("http://", "https://", "estleg:")):
        return slug
    return f"{NS}{slug}"


def _collect_at_ids(value: Any, out: set[str]) -> None:
    if isinstance(value, dict):
        node_id = value.get("@id")
        if isinstance(node_id, str) and node_id:
            out.add(node_id)
        for child in value.values():
            _collect_at_ids(child, out)
    elif isinstance(value, list):
        for child in value:
            _collect_at_ids(child, out)


def index_live_law_names(doc: dict) -> list[str]:
    """Return live INDEX ``laws[].name`` slugs in document order."""
    laws = doc.get("laws")
    if not isinstance(laws, list):
        return []
    names: list[str] = []
    for entry in laws:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if isinstance(name, str) and name:
            names.append(name)
    return names


def index_deprecated_law_names(doc: dict) -> list[str]:
    """Return INDEX ``deprecated_laws.entries[].name`` slugs in document order."""
    block = doc.get("deprecated_laws")
    if not isinstance(block, dict):
        return []
    entries = block.get("entries")
    if not isinstance(entries, list):
        return []
    names: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if isinstance(name, str) and name:
            names.append(name)
    return names


def collect_iris_from_jsonld(path: Path | str) -> set[str]:
    """Collect ``@id`` values from JSON-LD; INDEX law names are a fallback.

    Walking ``@id`` covers combined graphs and ordinary peeps. When *path*
    is ``INDEX.json`` (no ``@id``s), live + deprecated law slugs are
    converted to IRIs so tests and the first published delta do not need
    ``combined_ontology.jsonld``.
    """
    source = Path(path)
    with source.open(encoding="utf-8") as handle:
        doc = json.load(handle)
    iris: set[str] = set()
    _collect_at_ids(doc, iris)
    if isinstance(doc, dict):
        for name in index_live_law_names(doc):
            iris.add(law_name_to_iri(name))
        for name in index_deprecated_law_names(doc):
            iris.add(law_name_to_iri(name))
    return iris


def snapshot_iris_from_index(
    doc: dict,
    *,
    deprecated: bool,
) -> set[str]:
    """IRIs for the committed 0.11.0 snapshot (deprecated vs live INDEX)."""
    names = index_deprecated_law_names(doc) if deprecated else index_live_law_names(doc)
    return {law_name_to_iri(name) for name in names}


def diff_iris(old: set[str], new: set[str]) -> dict[str, Any]:
    """Return added/removed IRI lists (sorted) plus full counts."""
    added = sorted(new - old)
    removed = sorted(old - new)
    return {
        "added": added,
        "removed": removed,
        "addedCount": len(added),
        "removedCount": len(removed),
    }


def build_change_record(
    old_label: str,
    new_label: str,
    diff: dict[str, Any],
    extra_counts: dict[str, Any] | None = None,
    *,
    listed_cap: int = LISTED_IRI_CAP,
    version: str = ONTOLOGY_VERSION,
) -> dict[str, Any]:
    """Build a ``dcat:Dataset`` / ``estleg:ReleaseDelta`` JSON-LD document."""
    added = list(diff.get("added") or [])
    removed = list(diff.get("removed") or [])
    added_count = int(diff.get("addedCount", len(added)))
    removed_count = int(diff.get("removedCount", len(removed)))
    record: dict[str, Any] = {
        "@context": dict(CONTEXT),
        "@id": f"{NS}dataset/changes-{version}",
        "@type": ["dcat:Dataset", "estleg:ReleaseDelta"],
        "dcterms:title": f"Estonian Legal Ontology change record {version}",
        "dcterms:description": (
            f"Machine-readable IRI delta ({old_label} → {new_label}). "
            f"Listed IRIs are capped at {listed_cap}; counts are complete."
        ),
        "owl:versionInfo": version,
        "estleg:comparedFrom": old_label,
        "estleg:comparedTo": new_label,
        "estleg:addedCount": added_count,
        "estleg:removedCount": removed_count,
        "estleg:listedIriCap": listed_cap,
        "estleg:added": added[:listed_cap],
        "estleg:removed": removed[:listed_cap],
    }
    if extra_counts:
        record.update(extra_counts)
    return record


def build_index_deprecated_vs_live_record(
    index_path: Path = INDEX_PATH,
    *,
    listed_cap: int = LISTED_IRI_CAP,
    version: str = ONTOLOGY_VERSION,
) -> dict[str, Any]:
    """Honest first published delta: deprecated INDEX slugs vs live laws."""
    with Path(index_path).open(encoding="utf-8") as handle:
        index = json.load(handle)
    if not isinstance(index, dict):
        raise ValueError(f"{index_path} is not a JSON object")
    old = snapshot_iris_from_index(index, deprecated=True)
    new = snapshot_iris_from_index(index, deprecated=False)
    return build_change_record(
        OLD_LABEL_DEFAULT,
        NEW_LABEL_DEFAULT,
        diff_iris(old, new),
        listed_cap=listed_cap,
        version=version,
    )


# ---------------------------------------------------------------------------
# Provision-level release delta (#713)
# ---------------------------------------------------------------------------

#: Total listed provision IRIs (added + removed + changed) above which the
#: lists move from the JSON-LD record to the ``changes-<version>.jsonl``
#: sibling. Keeps the DCAT-linked JSON-LD small enough to load in one parse.
JSONL_THRESHOLD = 10_000
WORKTREE = "WORKTREE"
INDEX_REL = "krr_outputs/INDEX.json"
KRR_REL = "krr_outputs"
PROVISION_TYPES = frozenset({"estleg:LegalProvision", "estleg:Subsection"})
TEXT_FIELDS = ("estleg:legalText", "estleg:summary")
#: Temporal fields compared on provision nodes (any that is present).
TEMPORAL_FIELDS = (
    "estleg:entryIntoForce",
    "estleg:repealDate",
    "estleg:temporalStatus",
    "estleg:validFrom",
    "estleg:validUntil",
    "owl:deprecated",
)
TRACKED_FIELDS = TEXT_FIELDS + TEMPORAL_FIELDS


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def latest_release_tag(repo: Path = REPO_ROOT) -> str | None:
    """Newest ``v*`` tag by version order (``git tag --sort=-v:refname``)."""
    out = _git(repo, "tag", "--list", "v*", "--sort=-v:refname")
    tags = [line.strip() for line in out.splitlines() if line.strip()]
    return tags[0] if tags else None


def resolve_commit(repo: Path, ref: str) -> str:
    """Full commit SHA of ``ref`` (``HEAD`` for the working tree)."""
    return _git(repo, "rev-parse", f"{'HEAD' if ref == WORKTREE else ref}^{{commit}}")


class GitBlobReader:
    """Read ``<ref>:<path>`` blobs through one ``git cat-file --batch`` process."""

    def __init__(self, repo: Path, ref: str) -> None:
        self.ref = ref
        self._proc = subprocess.Popen(
            ["git", "-C", str(repo), "cat-file", "--batch"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
        )

    def read(self, rel_path: str) -> bytes | None:
        assert self._proc.stdin is not None and self._proc.stdout is not None
        self._proc.stdin.write(f"{self.ref}:{rel_path}\n".encode())
        self._proc.stdin.flush()
        header = self._proc.stdout.readline().decode().split()
        if len(header) != 3 or header[1] != "blob":
            return None  # "<object> missing" (or not a blob)
        data = self._proc.stdout.read(int(header[2]))
        self._proc.stdout.read(1)  # trailing LF
        return data

    def close(self) -> None:
        if self._proc.stdin is not None:
            self._proc.stdin.close()
        self._proc.wait()


class Snapshot:
    """One side of the comparison: a git ref or the working tree."""

    def __init__(self, repo: Path, ref: str) -> None:
        self.repo = repo
        self.ref = ref
        self._git = None if ref == WORKTREE else GitBlobReader(repo, ref)

    def read_json(self, rel_path: str) -> Any | None:
        if self._git is None:
            path = self.repo / rel_path
            if not path.is_file():
                return None
            raw = path.read_bytes()
        else:
            raw = self._git.read(rel_path)
            if raw is None:
                return None
        if raw.startswith(b"version https://git-lfs.github.com/spec/"):
            raise ValueError(f"{self.ref}:{rel_path} is a Git LFS pointer")
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{self.ref}:{rel_path} is not JSON: {exc}") from exc

    def close(self) -> None:
        if self._git is not None:
            self._git.close()


@dataclass
class LawEntry:
    """One INDEX law (live or deprecated) on one side of the comparison."""

    name: str
    files: list[str]
    deprecated: bool


def index_law_entries(index: dict) -> dict[str, LawEntry]:
    """INDEX ``laws`` + ``deprecated_laws.entries`` by law name."""
    out: dict[str, LawEntry] = {}
    blocks = [(index.get("laws"), False)]
    deprecated = index.get("deprecated_laws")
    if isinstance(deprecated, dict):
        blocks.append((deprecated.get("entries"), True))
    for entries, is_deprecated in blocks:
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            files = entry.get("files")
            if isinstance(name, str) and name:
                out[name] = LawEntry(
                    name,
                    [f for f in files if isinstance(f, str)] if isinstance(files, list) else [],
                    is_deprecated,
                )
    return out


def expand_iri(iri: str) -> str:
    """``estleg:X`` → ``https://w3id.org/estleg/X``; absolute IRIs unchanged."""
    return NS + iri.removeprefix("estleg:") if iri.startswith("estleg:") else iri


def _types(node: dict) -> list:
    value = node.get("@type") or []
    return [value] if isinstance(value, str) else value


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def provision_fingerprints(docs: list[dict]) -> dict[str, dict[str, str]]:
    """Provision ``@id`` → ``{tracked field: canonical JSON}`` over a law's files."""
    out: dict[str, dict[str, str]] = {}
    for doc in docs:
        for node in doc.get("@graph") or []:
            if not isinstance(node, dict) or not PROVISION_TYPES.intersection(_types(node)):
                continue
            iri = node.get("@id")
            if not isinstance(iri, str):
                continue
            out[iri] = {f: _canonical(node[f]) for f in TRACKED_FIELDS if f in node}
    return out


def latest_valid_from(sidecar: dict | None) -> dict[str, str]:
    """Provision ``@id`` → latest ``versionValidFrom`` in a version sidecar."""
    out: dict[str, str] = {}
    for node in (sidecar or {}).get("@graph") or []:
        if not isinstance(node, dict) or "estleg:ProvisionVersion" not in _types(node):
            continue
        of = node.get("estleg:versionOf")
        of = of.get("@id") if isinstance(of, dict) else of
        valid = node.get("estleg:versionValidFrom")
        valid = valid.get("@value") if isinstance(valid, dict) else valid
        if isinstance(of, str) and isinstance(valid, str) and valid[:10] > out.get(of, ""):
            out[of] = valid[:10]
    return out


@dataclass
class ReleaseDelta:
    """Result of :func:`compute_release_delta`."""

    from_ref: str
    to_ref: str
    from_commit: str
    to_commit: str
    laws_compared: int = 0
    added_laws: list[dict] = field(default_factory=list)
    removed_laws: list[dict] = field(default_factory=list)
    deprecated_laws: list[dict] = field(default_factory=list)
    #: one row per provision change: change / iri / law / fields / versionValidFrom
    provisions: list[dict] = field(default_factory=list)
    missing_files: list[str] = field(default_factory=list)

    def iris(self, change: str) -> list[str]:
        return [row["iri"] for row in self.provisions if row["change"] == change]

    def counts(self) -> dict[str, int]:
        c = Counter(row["change"] for row in self.provisions)
        return {k: c.get(k, 0) for k in ("added", "removed", "changed")}

    def field_counts(self) -> dict[str, int]:
        c: Counter[str] = Counter()
        for row in self.provisions:
            c.update(row.get("fields") or [])
        return dict(sorted(c.items()))


def _law_ref(entry: LawEntry, docs: list[dict]) -> dict:
    root = next((act_root_node(d) for d in docs if act_root_node(d)), None)
    iri = root.get("@id") if isinstance(root, dict) else None
    return {"law": entry.name, "iri": expand_iri(iri) if isinstance(iri, str) else None}


def _load_docs(snap: Snapshot, entry: LawEntry | None, missing: list[str]) -> list[dict]:
    docs: list[dict] = []
    for name in entry.files if entry else []:
        rel = f"{KRR_REL}/{name}"
        doc = snap.read_json(rel)
        if isinstance(doc, dict):
            docs.append(doc)
        else:
            missing.append(f"{snap.ref}:{rel}")
    return docs


def _structural_tail(iri: str) -> str | None:
    """``_Par_…`` tail of a provision IRI (the part a prefix re-mint keeps)."""
    head, sep, tail = iri.rpartition("_Par_")
    return f"_Par_{tail}" if sep and head else None


def pair_reminted_iris(
    rows: list[dict], old_fp: dict[str, dict[str, str]], new_fp: dict[str, dict[str, str]]
) -> int:
    """Annotate removed/added pairs that are one provision under a new IRI.

    A pair matches when, inside one law, the removed and the added IRI share
    the ``_Par_…`` tail and an identical tracked-field fingerprint, and that
    match is unique on both sides (e.g. the Tier-1 collision re-prefix
    ``ROS_Par_1`` → ``ROS_2_Par_1``). Rows keep their ``added`` / ``removed``
    change (the IRI did change); ``replacedBy`` / ``replaces`` link them.
    Returns the number of pairs.
    """
    def key(compact: str, fp: dict[str, dict[str, str]]) -> tuple | None:
        tail = _structural_tail(compact)
        return (tail, _canonical(fp[compact])) if tail and fp.get(compact) else None

    def compact(row: dict) -> str:
        return "estleg:" + row["iri"].removeprefix(NS)

    added: dict[tuple, list[dict]] = {}
    removed: dict[tuple, list[dict]] = {}
    for row in rows:
        if row["change"] == "added" and (k := key(compact(row), new_fp)):
            added.setdefault(k, []).append(row)
        elif row["change"] == "removed" and (k := key(compact(row), old_fp)):
            removed.setdefault(k, []).append(row)
    pairs = 0
    for k, gone in removed.items():
        new_rows = added.get(k) or []
        if len(gone) == 1 and len(new_rows) == 1:
            gone[0]["replacedBy"] = new_rows[0]["iri"]
            new_rows[0]["replaces"] = gone[0]["iri"]
            pairs += 1
    return pairs


def compute_release_delta(
    repo: Path = REPO_ROOT,
    from_ref: str | None = None,
    to_ref: str = WORKTREE,
) -> ReleaseDelta:
    """Provision-level delta between ``from_ref`` and ``to_ref`` (see module doc)."""
    from_ref = from_ref or latest_release_tag(repo)
    if not from_ref:
        raise ValueError("no v* tag found; pass --from-ref")
    old, new = Snapshot(repo, from_ref), Snapshot(repo, to_ref)
    try:
        old_index, new_index = old.read_json(INDEX_REL), new.read_json(INDEX_REL)
        if not isinstance(old_index, dict) or not isinstance(new_index, dict):
            raise ValueError(f"{INDEX_REL} missing at {from_ref} or {to_ref}")
        old_laws, new_laws = index_law_entries(old_index), index_law_entries(new_index)
        delta = ReleaseDelta(
            from_ref, to_ref, resolve_commit(repo, from_ref), resolve_commit(repo, to_ref)
        )
        for name in sorted(old_laws.keys() | new_laws.keys()):
            before, after = old_laws.get(name), new_laws.get(name)
            old_docs = _load_docs(old, before, delta.missing_files)
            new_docs = _load_docs(new, after, delta.missing_files)
            delta.laws_compared += 1
            if before is None:
                delta.added_laws.append(_law_ref(after, new_docs))
            elif after is None:
                delta.removed_laws.append(_law_ref(before, old_docs))
            elif after.deprecated and not before.deprecated:
                delta.deprecated_laws.append(_law_ref(after, new_docs))
            old_fp, new_fp = provision_fingerprints(old_docs), provision_fingerprints(new_docs)
            valid_from = (
                latest_valid_from(new.read_json(f"{KRR_REL}/provision_versions/{name}.jsonld"))
                if new_fp != old_fp
                else {}
            )
            law_rows: list[dict] = []
            for iri in sorted(old_fp.keys() | new_fp.keys()):
                a, b = old_fp.get(iri), new_fp.get(iri)
                if a == b:
                    continue
                row: dict[str, Any] = {"law": name, "iri": expand_iri(iri)}
                if a is None:
                    row["change"] = "added"
                elif b is None:
                    row["change"] = "removed"
                else:
                    row["change"] = "changed"
                    row["fields"] = sorted(f for f in a.keys() | b.keys() if a.get(f) != b.get(f))
                if row["change"] != "removed" and iri in valid_from:
                    row["versionValidFrom"] = valid_from[iri]
                law_rows.append(row)
            pair_reminted_iris(law_rows, old_fp, new_fp)
            delta.provisions.extend(law_rows)
        return delta
    finally:
        old.close()
        new.close()


def _ref_label(ref: str, commit: str) -> str:
    if ref == WORKTREE:
        return f"working tree (HEAD {commit[:10]})"
    return f"git {ref} ({commit[:10]})"


def build_release_delta_record(
    delta: ReleaseDelta,
    *,
    version: str = ONTOLOGY_VERSION,
    threshold: int = JSONL_THRESHOLD,
    jsonl_name: str | None = None,
) -> dict[str, Any]:
    """``dcat:Dataset`` / ``estleg:ReleaseDelta`` JSON-LD for a provision delta.

    Same record shape as the #549 file (``comparedFrom`` / ``comparedTo`` /
    ``addedCount`` / ``removedCount`` / ``added`` / ``removed``) at provision
    level, uncapped: lists are inline up to ``threshold`` listed IRIs,
    otherwise ``estleg:listedInline`` is false and ``dcat:distribution``
    points at the JSONL sibling that holds every row.
    """
    counts = delta.counts()
    listed = sum(counts.values())
    inline = listed <= threshold
    jsonl_name = jsonl_name or f"changes-{version}.jsonl"
    record: dict[str, Any] = {
        "@context": dict(CONTEXT),
        "@id": f"{NS}dataset/changes-{version}",
        "@type": ["dcat:Dataset", "estleg:ReleaseDelta"],
        "dcterms:title": f"Estonian Legal Ontology change record {version}",
        "dcterms:description": (
            f"Provision-level delta ({_ref_label(delta.from_ref, delta.from_commit)} → "
            f"{_ref_label(delta.to_ref, delta.to_commit)}): estleg:LegalProvision and "
            "estleg:Subsection IRIs added, removed, or changed (legalText, summary or "
            "temporal fields). Counts and lists are complete (no cap)"
            + ("." if inline else f"; the lists are in {jsonl_name}.")
        ),
        "owl:versionInfo": version,
        "estleg:comparedFrom": _ref_label(delta.from_ref, delta.from_commit),
        "estleg:comparedTo": _ref_label(delta.to_ref, delta.to_commit),
        "estleg:addedCount": counts["added"],
        "estleg:removedCount": counts["removed"],
        "estleg:changedCount": counts["changed"],
        "estleg:addedLawCount": len(delta.added_laws),
        "estleg:removedLawCount": len(delta.removed_laws),
        "estleg:deprecatedLawCount": len(delta.deprecated_laws),
        "estleg:addedLaw": [row["iri"] or row["law"] for row in delta.added_laws],
        "estleg:removedLaw": [row["iri"] or row["law"] for row in delta.removed_laws],
        "estleg:deprecatedLaw": [row["iri"] or row["law"] for row in delta.deprecated_laws],
        "estleg:listedInline": inline,
    }
    if inline:
        record["estleg:added"] = delta.iris("added")
        record["estleg:removed"] = delta.iris("removed")
        record["estleg:changed"] = delta.iris("changed")
    else:
        record["dcat:distribution"] = {
            "@type": "dcat:Distribution",
            "dcterms:title": f"Change rows {version} (JSON Lines)",
            "dcat:downloadURL": {"@id": jsonl_name},
            "dcat:mediaType": "application/x-ndjson",
        }
    return record


def jsonl_rows(delta: ReleaseDelta) -> list[dict]:
    """Every change as one row: law-level first, then provisions by law / change / IRI."""
    rows: list[dict] = []
    for change, items in (
        ("law_added", delta.added_laws),
        ("law_removed", delta.removed_laws),
        ("law_deprecated", delta.deprecated_laws),
    ):
        rows.extend({"change": change, **item} for item in items)
    order = {"added": 0, "removed": 1, "changed": 2}
    rows.extend(sorted(delta.provisions, key=lambda r: (r["law"], order[r["change"]], r["iri"])))
    return rows


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    tmp.replace(path)


def build_release_report(
    delta: ReleaseDelta,
    *,
    version: str,
    output: Path,
    jsonl_path: Path | None,
    threshold: int,
    top: int = 20,
) -> dict[str, Any]:
    """Small JSON summary for ``reports/release_changes_report.json``."""
    per_law: Counter[str] = Counter(row["law"] for row in delta.provisions)
    by_law_change: dict[str, Counter[str]] = {}
    for row in delta.provisions:
        by_law_change.setdefault(row["law"], Counter())[row["change"]] += 1

    def _rel(path: Path | None) -> str | None:
        if path is None:
            return None
        try:
            return str(path.resolve().relative_to(REPO_ROOT.resolve()))
        except ValueError:
            return str(path)

    return {
        "generated": BUILD_EVALUATION_DATE,  # #295: pinned, no wall-clock churn
        "ontologyVersion": version,
        "comparedFrom": {"ref": delta.from_ref, "commit": delta.from_commit},
        "comparedTo": {"ref": delta.to_ref, "commit": delta.to_commit},
        "laws": {
            "compared": delta.laws_compared,
            "added": len(delta.added_laws),
            "removed": len(delta.removed_laws),
            "deprecated": len(delta.deprecated_laws),
            "withProvisionChanges": len(per_law),
        },
        "provisions": {
            **delta.counts(),
            "changedByField": delta.field_counts(),
            "withVersionValidFrom": sum(1 for r in delta.provisions if "versionValidFrom" in r),
            "reMintedPairs": sum(1 for r in delta.provisions if "replacedBy" in r),
        },
        "missingFiles": sorted(delta.missing_files),
        "outputs": {
            "jsonld": _rel(output),
            "jsonl": _rel(jsonl_path),
            "listedInline": jsonl_path is None,
            "jsonlThreshold": threshold,
        },
        "topLaws": [
            {"law": law, "total": total, **dict(sorted(by_law_change[law].items()))}
            for law, total in sorted(per_law.items(), key=lambda kv: (-kv[1], kv[0]))[:top]
        ],
    }


def emit_release_delta(
    repo: Path = REPO_ROOT,
    *,
    from_ref: str | None = None,
    to_ref: str = WORKTREE,
    output: Path | None = None,
    report_path: Path | None = None,
    version: str = ONTOLOGY_VERSION,
    threshold: int = JSONL_THRESHOLD,
) -> tuple[ReleaseDelta, dict, dict]:
    """Compute the delta and write the JSON-LD, the JSONL (if needed), the report."""
    krr = repo / KRR_REL
    output = output or krr / f"changes-{version}.jsonld"
    report_path = report_path or krr / "reports" / "release_changes_report.json"
    delta = compute_release_delta(repo, from_ref, to_ref)
    jsonl_path = output.with_suffix(".jsonl")
    record = build_release_delta_record(
        delta, version=version, threshold=threshold, jsonl_name=jsonl_path.name
    )
    save_json(output, record)
    if record["estleg:listedInline"]:
        jsonl_path.unlink(missing_ok=True)  # never leave a stale sibling
        written_jsonl = None
    else:
        write_jsonl(jsonl_path, jsonl_rows(delta))
        written_jsonl = jsonl_path
    report = build_release_report(
        delta, version=version, output=output, jsonl_path=written_jsonl, threshold=threshold
    )
    save_json(report_path, report)
    return delta, record, report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n", 1)[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--mode",
        choices=("release-delta", "index-deprecated"),
        default="release-delta",
        help="release-delta (default, #713) or the #549 INDEX deprecated-vs-live snapshot.",
    )
    parser.add_argument(
        "--from-ref",
        help="Git ref of the previous release (default: latest v* tag).",
    )
    parser.add_argument(
        "--to-ref",
        default=WORKTREE,
        help=f"Git ref to compare against (default: {WORKTREE}, the working tree).",
    )
    parser.add_argument("--repo", type=Path, default=REPO_ROOT, help=argparse.SUPPRESS)
    parser.add_argument(
        "--version",
        default=ONTOLOGY_VERSION,
        help=f"Version stamped on the record and its filename (default: {ONTOLOGY_VERSION}).",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=JSONL_THRESHOLD,
        help=f"Inline-list limit before the JSONL sibling is used (default: {JSONL_THRESHOLD}).",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Report path (default: krr_outputs/reports/release_changes_report.json).",
    )
    parser.add_argument(
        "--old",
        type=Path,
        help="IRI-set mode: previous JSON-LD (or INDEX.json); pass with --new.",
    )
    parser.add_argument(
        "--new",
        type=Path,
        help="IRI-set mode: current JSON-LD (or INDEX.json); pass with --old.",
    )
    parser.add_argument(
        "--index",
        type=Path,
        default=INDEX_PATH,
        help="index-deprecated mode: INDEX.json to read.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Destination JSON-LD (default: krr_outputs/changes-<version>.jsonld).",
    )
    parser.add_argument(
        "--cap",
        type=int,
        default=LISTED_IRI_CAP,
        help=f"index-deprecated / IRI-set modes: listed IRI cap (default: {LISTED_IRI_CAP}).",
    )
    parser.add_argument(
        "--old-label",
        default=OLD_LABEL_DEFAULT,
        help="IRI-set / index-deprecated modes: estleg:comparedFrom label.",
    )
    parser.add_argument(
        "--new-label",
        default=NEW_LABEL_DEFAULT,
        help="IRI-set / index-deprecated modes: estleg:comparedTo label.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if (args.old is None) ^ (args.new is None):
        print("error: --old and --new must be passed together", file=sys.stderr)
        return 2
    if args.old is not None or args.mode == "index-deprecated":
        output = args.output or KRR_DIR / f"changes-{args.version}.jsonld"
        if args.old is not None:
            record = build_change_record(
                args.old_label,
                args.new_label,
                diff_iris(collect_iris_from_jsonld(args.old), collect_iris_from_jsonld(args.new)),
                listed_cap=args.cap,
                version=args.version,
            )
        else:
            record = build_index_deprecated_vs_live_record(
                args.index, listed_cap=args.cap, version=args.version
            )
        save_json(output, record)
        print(
            f"Wrote {output} "
            f"(added={record['estleg:addedCount']}, "
            f"removed={record['estleg:removedCount']}, "
            f"listed_cap={record['estleg:listedIriCap']})"
        )
        return 0

    started = time.perf_counter()
    try:
        delta, record, report = emit_release_delta(
            args.repo,
            from_ref=args.from_ref,
            to_ref=args.to_ref,
            output=args.output,
            report_path=args.report,
            version=args.version,
            threshold=args.threshold,
        )
    except (ValueError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    counts = delta.counts()
    print(
        f"Release delta {record['estleg:comparedFrom']} → {record['estleg:comparedTo']}: "
        f"provisions added={counts['added']} removed={counts['removed']} "
        f"changed={counts['changed']}; laws added={len(delta.added_laws)} "
        f"removed={len(delta.removed_laws)} deprecated={len(delta.deprecated_laws)}; "
        f"inline={record['estleg:listedInline']} "
        f"({time.perf_counter() - started:.1f}s)"
    )
    if delta.missing_files:
        print(f"warning: {len(delta.missing_files)} INDEX-listed file(s) missing", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

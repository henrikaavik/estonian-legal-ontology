"""Keep committed law-node IRIs across a regeneration (STABILITY.md `@id` policy).

The law generator mints structural IRIs from the RT XML of the redaction it
parses. The committed corpus carries IRIs that later repair passes fixed
(content-hash dedupe suffixes such as ``Chapter_ABIPOL_6_807c6001``, osa
renames, collision prefixes), and a plain regeneration does not reproduce
them: ``Chapter_ABIPOL_6_807c6001`` comes back as ``Chapter_ABIPOL_6_1``.
"Law/provision local names are frozen for MINOR/PATCH. A rename is MAJOR."

:func:`pin_to_committed` reconciles a freshly generated document with the
document already on disk. Every structural node gets an identity key that
does not depend on its IRI:

* Chapter / Division / Subdivision / Part concept: node kind + normalised
  Estonian label + occurrence index among nodes with that same key;
* TopicCluster (the ``skos:Concept`` twin of a chapter): the key of the
  chapter whose ``dcterms:subject`` names it; a cluster no chapter names is
  keyed by its label without the ``§min–max`` range (the range moves when a
  paragraph is added);
* LegalProvision (§): the normalised ``estleg:paragrahv`` + occurrence index
  (each multipart osa is its own document, so the part is implicit);
* Subsection (lõige): its parent provision's key + normalised
  ``estleg:subsectionNumber`` + occurrence index under that parent.

A regenerated node whose key the committed document has takes the committed
``@id``, and every ``{"@id": …}`` reference in the document is rewritten
through the same table. A node without a committed counterpart keeps the
generator's IRI unless that IRI is already a committed IRI of another
identity; then it gets a deterministic ``_<sha1(key)[:8]>`` suffix, because a
published IRI must never be re-used for a different provision. An unmatched
lõige under a provision whose IRI was pinned is rebased onto the pinned
parent first, so it keeps the ``<parent>_Lg_<n>`` shape. Committed nodes with
no counterpart are genuine removals (repealed or renumbered in the new
redaction) and are reported, not resurrected.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field

# Node kinds that carry a structural identity, in key-computation order:
# clusters are keyed through their chapter, subsections through their parent.
KIND_CHAPTER = "Chapter"
KIND_DIVISION = "Division"
KIND_SUBDIVISION = "Subdivision"
KIND_PART_CONCEPT = "PartConcept"
KIND_CLUSTER = "TopicCluster"
KIND_PROVISION = "LegalProvision"
KIND_SUBSECTION = "Subsection"
PINNED_KINDS: tuple[str, ...] = (
    KIND_CHAPTER,
    KIND_DIVISION,
    KIND_SUBDIVISION,
    KIND_PART_CONCEPT,
    KIND_CLUSTER,
    KIND_PROVISION,
    KIND_SUBSECTION,
)

_SUP_DIGITS = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")
_SUP_RUN_RE = re.compile(r"[⁰¹²³⁴⁵⁶⁷⁸⁹]+")
_SUP_TAG_RE = re.compile(r"<sup>\s*([^<]*?)\s*</sup>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_DASH_RE = re.compile(r"[‐-―−]")
_WS_RE = re.compile(r"\s+")
_PAR_RANGE_RE = re.compile(r"^§\s*\d+\s*[–\-]\s*\d+\s*")
_LG_LABEL_RE = re.compile(r"\blg\s+(\S+)\s*$")
_LG_IRI_RE = re.compile(r"_Lg_(\d+(?:_\d+)?)(?:_x\d+)?$")


def _types(node: dict) -> list[str]:
    raw = node.get("@type") or []
    return [raw] if isinstance(raw, str) else [t for t in raw if isinstance(t, str)]


def node_kind(node: dict) -> str | None:
    """The pinned kind of a graph node, or None for roots and other nodes."""
    types = _types(node)
    if "estleg:Subsection" in types:
        return KIND_SUBSECTION
    if "estleg:LegalProvision" in types:
        return KIND_PROVISION
    if "estleg:Chapter" in types:
        return KIND_CHAPTER
    if "estleg:Division" in types:
        return KIND_DIVISION
    if "estleg:Subdivision" in types:
        return KIND_SUBDIVISION
    if "estleg:TopicCluster" in types:
        return KIND_CLUSTER
    if "skos:Concept" in types and not {"estleg:Act", "estleg:Part"} & set(types):
        return KIND_PART_CONCEPT
    return None


def _text(value: object) -> str:
    """The Estonian (or only) lexical form of a JSON-LD literal."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        raw = value.get("@value")
        return raw if isinstance(raw, str) else ""
    if isinstance(value, list):
        texts = [v for v in value if isinstance(v, (str, dict))]
        for item in texts:
            if isinstance(item, dict) and item.get("@language") == "et":
                return _text(item)
        return _text(texts[0]) if texts else ""
    return ""


def normalise_text(value: object) -> str:
    """Label text folded so the committed and regenerated forms compare equal.

    ``<sup>1</sup>`` and ``¹`` both become ``^1`` (never a bare ``1``: § 26¹ is
    not § 261), other markup is dropped, dashes are unified, NFKC applied,
    whitespace collapsed and case folded.
    """
    text = _text(value)
    text = _SUP_TAG_RE.sub(lambda m: "^" + m.group(1).translate(_SUP_DIGITS), text)
    text = _SUP_RUN_RE.sub(lambda m: "^" + m.group(0).translate(_SUP_DIGITS), text)
    text = _TAG_RE.sub("", text)
    text = unicodedata.normalize("NFKC", text)
    text = _DASH_RE.sub("-", text)
    return _WS_RE.sub(" ", text).strip().casefold()


def normalise_number(value: object) -> str:
    """A § or lõige number: ``§ 26¹.`` / ``26<sup>1</sup>`` / ``26 1`` -> ``26^1``."""
    text = normalise_text(value)
    if "§" in text:
        text = text.rsplit("§", 1)[1]
    text = text.strip().strip(".").strip()
    text = re.sub(r"^\((.*)\)$", r"\1", text).strip()
    return re.sub(r"\s*\^\s*", "^", text)


def _ref(value: object) -> str | None:
    if isinstance(value, dict) and isinstance(value.get("@id"), str):
        return value["@id"]
    if isinstance(value, list):
        for item in value:
            ref = _ref(item)
            if ref:
                return ref
    return None


def _refs(value: object) -> list[str]:
    if isinstance(value, dict):
        return [value["@id"]] if isinstance(value.get("@id"), str) else []
    if isinstance(value, list):
        return [ref for item in value for ref in _refs(item)]
    return []


def _subsection_number(node: dict) -> str:
    number = node.get("estleg:subsectionNumber")
    if number not in (None, ""):
        return normalise_number(number)
    match = _LG_LABEL_RE.search(normalise_text(node.get("rdfs:label")))
    if match:
        return normalise_number(match.group(1))
    # Committed lõiked minted before #514 carry neither number nor label;
    # their IRI tail is the only record of the position they were given.
    match = _LG_IRI_RE.search(str(node.get("@id") or ""))
    if match:
        base, _, sup = match.group(1).partition("_")
        return f"{base}^{sup}" if sup else base
    return ""


def identity_keys(doc: dict) -> dict[str, tuple]:
    """``{@id: identity key}`` for every pinnable node of a law document."""
    graph = [n for n in doc.get("@graph", []) if isinstance(n, dict) and isinstance(n.get("@id"), str)]
    by_id = {n["@id"]: n for n in graph}
    kinds = {n["@id"]: node_kind(n) for n in graph}
    keys: dict[str, tuple] = {}
    occurrences: Counter[tuple] = Counter()

    def _assign(iri: str, base: tuple) -> None:
        occurrences[base] += 1
        keys[iri] = (*base, occurrences[base])

    for node in graph:
        kind = kinds[node["@id"]]
        if kind in (KIND_CHAPTER, KIND_DIVISION, KIND_SUBDIVISION, KIND_PART_CONCEPT):
            _assign(node["@id"], (kind, normalise_text(node.get("rdfs:label"))))

    twin_of: dict[str, str] = {}
    for node in graph:
        if kinds[node["@id"]] != KIND_CHAPTER:
            continue
        for subject in _refs(node.get("dcterms:subject")):
            if kinds.get(subject) == KIND_CLUSTER and subject not in twin_of:
                twin_of[subject] = node["@id"]
                break
    for node in graph:
        if kinds[node["@id"]] != KIND_CLUSTER:
            continue
        chapter = twin_of.get(node["@id"])
        if chapter is not None:
            _assign(node["@id"], (KIND_CLUSTER, "chapter", keys[chapter]))
        else:
            label = _PAR_RANGE_RE.sub("", normalise_text(node.get("rdfs:label") or node.get("skos:prefLabel")))
            _assign(node["@id"], (KIND_CLUSTER, "label", label))

    for node in graph:
        if kinds[node["@id"]] == KIND_PROVISION:
            number = normalise_number(node.get("estleg:paragrahv") or node.get("rdfs:label"))
            _assign(node["@id"], (KIND_PROVISION, number))

    for node in graph:
        if kinds[node["@id"]] != KIND_SUBSECTION:
            continue
        parent = _ref(node.get("estleg:parentProvision"))
        parent_key = keys.get(parent) if parent in by_id else ("orphan", parent)
        _assign(node["@id"], (KIND_SUBSECTION, parent_key, _subsection_number(node)))
    return keys


@dataclass
class PinStats:
    """What :func:`pin_to_committed` did, per node kind."""

    matched: Counter = field(default_factory=Counter)
    renamed: Counter = field(default_factory=Counter)
    unmatched: Counter = field(default_factory=Counter)
    removed: Counter = field(default_factory=Counter)
    renames: dict[str, str] = field(default_factory=dict)
    removed_iris: list[str] = field(default_factory=list)
    disambiguated: dict[str, str] = field(default_factory=dict)
    disambiguated_kinds: Counter = field(default_factory=Counter)

    def as_dict(self) -> dict:
        return {
            "matched": dict(self.matched),
            "renamed": dict(self.renamed),
            "unmatched": dict(self.unmatched),
            "removed": dict(self.removed),
            "removedIris": list(self.removed_iris),
            "disambiguated": dict(self.disambiguated),
        }


def _key_digest(key: tuple) -> str:
    return hashlib.sha1(json.dumps(key, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()[:8]


def rewrite_ids(value: object, remap: dict[str, str]) -> None:
    """Map every ``{"@id": iri}`` (node ids included) through ``remap`` in place."""
    if isinstance(value, dict):
        iri = value.get("@id")
        if isinstance(iri, str) and iri in remap:
            value["@id"] = remap[iri]
        for item in value.values():
            if isinstance(item, (dict, list)):
                rewrite_ids(item, remap)
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, (dict, list)):
                rewrite_ids(item, remap)


def pin_to_committed(new_doc: dict, committed_doc: dict | None) -> tuple[dict, PinStats]:
    """Give ``new_doc``'s structural nodes the IRIs ``committed_doc`` published.

    Returns a pinned deep copy of ``new_doc`` and the per-kind statistics.
    ``committed_doc`` is not modified. Pinning is idempotent:
    ``pin(pin(d, c), c) == pin(d, c)``.
    """
    stats = PinStats()
    doc = copy.deepcopy(new_doc)
    if not isinstance(committed_doc, dict) or not isinstance(doc.get("@graph"), list):
        return doc, stats

    committed_keys = identity_keys(committed_doc)
    committed_by_key = {key: iri for iri, key in committed_keys.items()}
    committed_ids = {
        n["@id"]
        for n in committed_doc.get("@graph", [])
        if isinstance(n, dict) and isinstance(n.get("@id"), str)
    }
    new_keys = identity_keys(doc)
    graph_ids = [n["@id"] for n in doc["@graph"] if isinstance(n, dict) and isinstance(n.get("@id"), str)]
    nodes = {n["@id"]: n for n in doc["@graph"] if isinstance(n, dict) and isinstance(n.get("@id"), str)}

    remap: dict[str, str] = {}
    matched_committed: set[str] = set()
    for iri in graph_ids:
        key = new_keys.get(iri)
        if key is None or key not in committed_by_key:
            continue
        target = committed_by_key[key]
        remap[iri] = target
        matched_committed.add(target)
        stats.matched[key[0]] += 1
        if target != iri:
            stats.renamed[key[0]] += 1
            stats.renames[iri] = target

    # IRIs no unmatched node may take: every committed IRI (a removed node's
    # IRI must not be re-used for another identity) and every IRI already
    # assigned in this document.
    taken = set(committed_ids) | set(remap.values())
    for iri in graph_ids:
        key = new_keys.get(iri)
        if key is None or iri in remap:
            continue
        stats.unmatched[key[0]] += 1
        candidate = iri
        if key[0] == KIND_SUBSECTION:
            parent = _ref(nodes[iri].get("estleg:parentProvision"))
            pinned_parent = remap.get(parent)
            if parent and pinned_parent and pinned_parent != parent and iri.startswith(parent + "_"):
                candidate = pinned_parent + iri[len(parent):]
        if candidate in taken:
            base, digest = candidate, _key_digest(key)
            candidate = f"{base}_{digest}"
            extra = 2
            while candidate in taken:
                candidate = f"{base}_{digest}_{extra}"
                extra += 1
            stats.disambiguated[iri] = candidate
            stats.disambiguated_kinds[key[0]] += 1
        taken.add(candidate)
        if candidate != iri:
            remap[iri] = candidate

    for iri, key in committed_keys.items():
        if iri not in matched_committed:
            stats.removed[key[0]] += 1
            stats.removed_iris.append(iri)

    if any(src != dst for src, dst in remap.items()):
        rewrite_ids(doc, {src: dst for src, dst in remap.items() if src != dst})
    return doc, stats


class PinTotals:
    """Run-level totals over many :func:`pin_to_committed` calls, with logging."""

    BUCKETS = ("matched", "renamed", "unmatched", "removed", "disambiguated")

    def __init__(self) -> None:
        self.counts: dict[str, Counter] = {bucket: Counter() for bucket in self.BUCKETS}

    def clear(self) -> None:
        for bucket in self.counts.values():
            bucket.clear()

    def record(self, name: str, stats: PinStats, log=print) -> None:
        """Fold one file's result in; log its renames, removals and collisions."""
        for bucket in ("matched", "renamed", "unmatched", "removed"):
            self.counts[bucket].update(getattr(stats, bucket))
        self.counts["disambiguated"].update(stats.disambiguated_kinds)
        renamed, removed = sum(stats.renamed.values()), sum(stats.removed.values())
        if renamed or removed or stats.disambiguated:
            log(
                f"    IRI pin {name}: kept {renamed} committed IRI(s) the generator "
                f"re-minted; {removed} committed node(s) have no counterpart"
            )
        for src, dst in sorted(stats.disambiguated.items()):
            log(f"    WARN: IRI pin {name}: {src} is a committed IRI of another element; the new element gets {dst}")

    def as_dict(self) -> dict[str, dict[str, int]]:
        return {bucket: dict(sorted(c.items())) for bucket, c in self.counts.items()}

    def summary(self) -> str:
        return (
            f"{sum(self.counts['renamed'].values())} committed IRI(s) kept over a re-mint, "
            f"{sum(self.counts['removed'].values())} committed node(s) removed, "
            f"{sum(self.counts['disambiguated'].values())} disambiguated"
        )

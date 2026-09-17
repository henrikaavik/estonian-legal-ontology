#!/usr/bin/env python3
"""Find nodes an rdfs:domain / rdfs:range axiom types into a SHACL-shaped class (#709).

The SHACL buckets validate with ``inference="rdfs"``. A property whose domain
or range is a shaped class therefore types every subject or object it touches
as that class, and the class's shape fires on nodes that were never meant to
satisfy it: a draft carrying ``estleg:changeType`` became a ProposedAmendment,
a court decision's ``estleg:interpretsVersion`` target -- declared in full on
another load surface -- became a bare ProvisionVersion. Five buckets failed on
~390,000 such violations before #709.

This reads the JSON-LD directly, so it answers in about half a minute what the
pyshacl run needs half an hour for, and it names the axiom rather than the
symptom. It does not replace SHACL: it says nothing about nodes that carry a
type honestly and still break its shape.

    python3 scripts/check_phantom_typing.py --all
    python3 scripts/check_phantom_typing.py --bucket riigikohus
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from itertools import chain
from pathlib import Path

from estleg.estleg_common import canonical_estleg_ref
from estleg.shacl_validate_all import BUCKETS, KRR, SHAPES

VOCAB_NAME = "controlled_vocabulary.jsonld"
SH_TARGET_CLASS = "http://www.w3.org/ns/shacl#targetClass"
AXIOM_KEYS = ("rdfs:domain", "rdfs:range", "rdfs:subClassOf", "rdfs:subPropertyOf")
RDFS_NS = "http://www.w3.org/2000/01/rdf-schema#"
AXIOM_NAMES = (*AXIOM_KEYS, *(RDFS_NS + key.split(":")[1] for key in AXIOM_KEYS))

# A multipart act is split into one file per osa, each rooted in an estleg:Part
# node (#566) that points at the act with estleg:isPartOf and repeats the act's
# metadata. The Act-domain properties therefore entail ``a estleg:Act`` on 34
# part roots. Tolerated, not right: every one passes the Act shapes, and the
# repair is the Part / LegalPart modelling that #709 still has open, not an
# axiom. Keyed on the node so that any other Part picking up an Act-domain
# property is still reported.
PART_CLASS = "estleg:Part"
PART_OF_PROP = "estleg:isPartOf"
TOLERATED_ON_PART_ROOTS = "estleg:Act"


@dataclass(frozen=True)
class PhantomTyping:
    bucket: str
    axis: str  # "domain" | "range"
    prop: str
    cls: str
    nodes: tuple[str, ...]

    def format(self) -> str:
        return (
            f"{self.bucket}: rdfs:{self.axis} of {self.prop} types {len(self.nodes)} "
            f"node(s) as {self.cls} that no file in the bucket declares as one "
            f"(e.g. {self.nodes[0]})"
        )


def _as_list(value: object) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _ref(value: object) -> str | None:
    """Id of a node or reference, in compact form when it is an ``estleg:`` one."""
    if isinstance(value, dict):
        value = value.get("@id")
    return (canonical_estleg_ref(value) or value) if isinstance(value, str) else None


def _axiom_values(node: dict, key: str) -> list:
    """Read both legal JSON-LD spellings, including when both are present."""
    return _as_list(node.get(key)) + _as_list(node.get(RDFS_NS + key.split(":")[1]))


def shaped_classes(shapes_path: Path = SHAPES) -> frozenset[str]:
    import rdflib

    try:
        shapes = rdflib.Graph().parse(str(shapes_path), format="turtle")
    except Exception as exc:
        raise CannotScan(f"{shapes_path}: cannot read SHACL shapes ({exc})") from exc
    targets = shapes.objects(None, rdflib.URIRef(SH_TARGET_CLASS))
    return frozenset(filter(None, (canonical_estleg_ref(str(t)) for t in targets)))


class TBox:
    """The vocabulary's subclass closure and its shaped domain / range axioms."""

    def __init__(self, vocab_nodes: Iterable[dict], shaped: frozenset[str]) -> None:
        self._parents: dict[str, set[str]] = defaultdict(set)
        nodes = [n for n in vocab_nodes if isinstance(n, dict)]
        for node in nodes:
            child = _ref(node)
            for parent in map(_ref, _axiom_values(node, "rdfs:subClassOf")):
                if child and parent:
                    self._parents[child].add(parent)
        # RDFS applies every domain / range a property declares, in any file,
        # and hands each one down to its sub-properties.
        declared: dict[str, dict[str, set[str]]] = {
            "rdfs:domain": defaultdict(set),
            "rdfs:range": defaultdict(set),
        }
        supers: dict[str, set[str]] = defaultdict(set)
        for node in nodes:
            prop = _ref(node)
            if prop is None:
                continue
            supers[prop].update(filter(None, map(_ref, _axiom_values(node, "rdfs:subPropertyOf"))))
            for key, table in declared.items():
                # A union domain is a blank class; RDFS entails no named type from it.
                for cls in filter(None, map(_ref, _axiom_values(node, key))):
                    if self.closure([cls]) & shaped:
                        table[prop].add(cls)
        self.domain = self._inherited(declared["rdfs:domain"], supers)
        self.range = self._inherited(declared["rdfs:range"], supers)

    @staticmethod
    def _inherited(table: dict[str, set[str]], supers: dict[str, set[str]]) -> dict[str, set[str]]:
        out: dict[str, set[str]] = {}
        for prop in set(table) | set(supers):
            seen: set[str] = set()
            stack = [prop]
            while stack:
                current = stack.pop()
                if current not in seen:
                    seen.add(current)
                    stack.extend(supers.get(current, ()))
            classes = set().union(*(table.get(p, set()) for p in seen))
            if classes:
                out[prop] = classes
        return out

    def closure(self, types: Iterable[str]) -> set[str]:
        """``types`` plus every superclass the vocabulary declares."""
        seen: set[str] = set()
        stack = list(types)
        while stack:
            cls = stack.pop()
            if cls not in seen:
                seen.add(cls)
                stack.extend(self._parents.get(cls, ()))
        return seen


def iter_nodes(value: object) -> Iterator[dict]:
    """Every dict carrying an ``@id``, at any depth (some modules nest nodes)."""
    if isinstance(value, dict):
        if "@id" in value:
            yield value
        for child in value.values():
            yield from iter_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_nodes(child)


def scan_documents(bucket: str, docs: Iterable[object], tbox: TBox) -> list[PhantomTyping]:
    """Report each axiom that types a node no document in ``docs`` declares as such."""
    asserted: dict[str, set[str]] = defaultdict(set)
    entailed: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    part_links: set[str] = set()
    for doc in docs:
        for node in iter_nodes(doc):
            nid = _ref(node)
            if nid is None:
                continue
            types = [t for t in _as_list(node.get("@type")) if isinstance(t, str)]
            asserted[nid] |= tbox.closure(canonical_estleg_ref(t) or t for t in types)
            for raw_key, value in node.items():
                key = canonical_estleg_ref(raw_key) or raw_key
                if key == PART_OF_PROP:
                    part_links.add(nid)
                for cls in tbox.domain.get(key, ()):
                    entailed["domain", key, cls].add(nid)
                if key in tbox.range:
                    targets = set(filter(None, map(_ref, _as_list(value))))
                    for cls in tbox.range[key]:
                        entailed["range", key, cls] |= targets

    # A node's type and its parent edge can be declared in different files.
    # Decide the exception after collecting all assertions, just as RDF does.
    part_roots = {nid for nid in part_links if PART_CLASS in asserted[nid]}
    findings = []
    for (axis, prop, cls), nodes in sorted(entailed.items()):
        tolerated = part_roots if cls == TOLERATED_ON_PART_ROOTS else set()
        phantom = sorted(n for n in nodes if cls not in asserted[n] and n not in tolerated)
        if phantom:
            findings.append(PhantomTyping(bucket, axis, prop, cls, tuple(phantom)))
    return findings


class CannotScan(Exception):
    """Answering "clean" would describe input that was never read."""


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise CannotScan(f"{path}: {exc}") from exc


def _parse(path: Path, text: str) -> object:
    try:
        return json.loads(text)
    except ValueError as exc:
        raise CannotScan(f"{path}: not JSON ({exc}); unpulled Git LFS pointer?") from exc


def scan_bucket(bucket: str, *, krr: Path = KRR, shapes: Path = SHAPES) -> list[PhantomTyping]:
    shaped = shaped_classes(shapes)
    if not shaped:
        raise CannotScan(f"{shapes}: no sh:targetClass found, so no class would count as shaped")
    # Same guard as shacl_validate_all (#338 / #590): the vocabulary rides along
    # in every bucket, so a vanished corpus subdirectory still yields one file.
    try:
        corpus = [path for path in BUCKETS[bucket](krr) if path.name != VOCAB_NAME]
    except (OSError, ValueError) as exc:
        raise CannotScan(f"{bucket}: cannot collect corpus files ({exc})") from exc
    if not corpus:
        raise CannotScan(f"no corpus files collected for the {bucket!r} bucket (vanished/renamed?)")

    vocab_path = krr / VOCAB_NAME
    vocab = _parse(vocab_path, _read(vocab_path))
    # pyshacl loads the whole bucket into one graph, so an axiom declared in a
    # corpus file (legacy OWL modules carry their own) types nodes just as one
    # in the vocabulary does.
    axioms = list(iter_nodes(vocab))
    for path in corpus:
        text = _read(path)
        if any(f'"{key}"' in text for key in AXIOM_NAMES):
            nodes = iter_nodes(_parse(path, text))
            axioms.extend(node for node in nodes if any(key in node for key in AXIOM_NAMES))

    # The vocabulary is scanned too: the enum individuals it declares
    # (NormType_*, CaseType_*, ...) count as declared in every bucket.
    docs = (_parse(path, _read(path)) for path in corpus)
    return scan_documents(bucket, chain([vocab], docs), TBox(axioms, shaped))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--bucket", choices=sorted(BUCKETS), help="Scan one SHACL bucket.")
    group.add_argument("--all", action="store_true", help="Scan every SHACL bucket in turn.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    findings: list[PhantomTyping] = []
    for bucket in sorted(BUCKETS) if args.all else [args.bucket]:
        try:
            found = scan_bucket(bucket)
        except CannotScan as exc:
            print(f"ERROR: {exc}")
            return 2
        print(f"{bucket}: {len(found)} phantom-typing axiom(s)")
        findings.extend(found)
    for finding in findings:
        print("  " + finding.format())
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())

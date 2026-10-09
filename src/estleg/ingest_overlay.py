"""Raw ingest layer vs enrichment overlay layer: merge-on-write (#697).

An ingest generator (a scraper / API client that turns an upstream source
into a peep) legitimately owns only the facts it reads from that source: the
RAW layer. Everything else on the same nodes was layered on later by
enrichment passes (court→law links, EuroVoc, transposition edges, full-text
passes, similarity, deontic tagging …): the OVERLAY layer. Before #697 every
ingest generator except the law generator ``save_json``-ed a freshly built
document over the peep, so a routine re-scrape deleted the overlay.

This module is the one shared rule every ingest writer routes through:

* Each generator declares an :class:`IngestLayer`: the RAW predicate keys it
  owns, the node ``@type`` values whose node set it owns, and (rarely)
  overlay ``@type`` values and keys that both layers write.
* On rewrite, :func:`merge_overlays` keeps every key of an existing node that
  the generator did not emit and does not own (overlay preserved), lets every
  key the generator emitted win (raw re-read), and drops a RAW key the
  generator stopped emitting (the source no longer says it).
* Nodes the ingest does not own (e.g. ``estleg:Citation`` or
  ``estleg:Similarity`` nodes added by enrichers) survive at their position;
  nodes of a raw-owned type that the new ingest no longer emits are dropped.
* Key order of surviving nodes and graph order follow the existing file, so
  an unchanged re-ingest is byte-identical to the committed peep.

``--replace-overlays`` (:func:`add_replace_overlays_argument`) is the explicit
opt-out: the freshly built raw document is written as-is and the overlay
loss is logged at WARNING with per-key counts.
"""

from __future__ import annotations

import argparse
import json
import logging
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

REPLACE_OVERLAYS_HELP = (
    "Write the freshly ingested raw documents as-is, DROPPING every "
    "enrichment overlay (keys and nodes not owned by this ingest) on the "
    "existing peeps. Default: overlays are preserved (merge-on-write, #697). "
    "The dropped overlay is logged per file."
)

Combiner = Callable[[Any, Any], Any]


def _type_list(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [t for t in value if isinstance(t, str)]
    return []


def _as_list(value: object) -> list:
    return list(value) if isinstance(value, list) else [value]


def _canon(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _same_value_set(existing: Any, new: Any) -> bool:
    """True when two different serialisations carry the same JSON-LD value set.

    JSON-LD arrays (without ``@list``) are unordered and a scalar equals its
    one-element array, so ``["b", "a"]`` ~ ``["a", "b"]`` and ``"a"`` ~ ``["a"]``.
    """
    if existing == new or not (isinstance(existing, list) or isinstance(new, list)):
        return False
    return sorted(map(_canon, _as_list(existing))) == sorted(map(_canon, _as_list(new)))


def union_values(existing: Any, new: Any) -> Any:
    """Union of two JSON-LD values: existing items first, then new additions.

    Used for list-valued keys that both layers write (e.g.
    ``estleg:derivationMethod``: the ingest stamps its own method, enrichers
    append theirs). Equal inputs return ``new`` unchanged.
    """
    if existing == new:
        return new
    merged = _as_list(existing)
    seen = {_canon(v) for v in merged}
    for item in _as_list(new):
        key = _canon(item)
        if key not in seen:
            seen.add(key)
            merged.append(item)
    if len(merged) == 1 and not isinstance(new, list) and not isinstance(existing, list):
        return merged[0]
    return merged


def keep_refined(placeholder: Any) -> Combiner:
    """Combiner: an enricher's refinement of an ingest ``placeholder`` survives.

    When the new build yields ``placeholder`` (e.g. ``CaseType_Other``) and the
    existing node carries a different value, the existing value is kept.
    """

    def _combine(existing: Any, new: Any) -> Any:
        if new == placeholder and existing != placeholder:
            return existing
        return new

    return _combine


def max_int(existing: Any, new: Any) -> Any:
    """Keep the larger of two integer counters (accumulating raw+overlay counts)."""
    if isinstance(existing, int) and isinstance(new, int):
        if isinstance(existing, bool) or isinstance(new, bool):
            return new
        return max(existing, new)
    return new


@dataclass(frozen=True)
class IngestLayer:
    """What one ingest generator owns (the RAW layer) on the peeps it writes.

    ``raw_keys``
        Predicate keys whose value is read from the upstream source. When the
        new build omits one that the existing node carries, it is dropped.
        Keys the new build emits always win, whether declared or not. Keys
        that enrichers ALSO write are deliberately left out of this set, so
        an enricher's value survives a rebuild that does not emit the key.
    ``raw_node_types``
        Node ``@type`` values whose node set the ingest owns: an existing node
        of such a type that the new build no longer emits is dropped. Every
        other existing node is overlay and preserved.
    ``overlay_types``
        ``@type`` values enrichers add to raw nodes (kept on merge).
    ``union_keys``
        Keys both layers append to; merged as a value union.
    ``seed_keys``
        Keys the ingest derives only to seed a NEW node; once a node exists an
        enricher owns them (e.g. court ``estleg:referencedLaw``, normalised
        post-hoc by #596). On merge the existing value, or its absence, wins.
    ``combiners``
        Per-key ``(existing, new) -> merged`` for keys needing special care.
    """

    name: str
    raw_keys: frozenset[str]
    raw_node_types: frozenset[str]
    overlay_types: frozenset[str] = frozenset()
    union_keys: frozenset[str] = frozenset()
    seed_keys: frozenset[str] = frozenset()
    combiners: Mapping[str, Combiner] = field(default_factory=dict)

    def owns_node(self, node: Mapping[str, Any]) -> bool:
        return bool(set(_type_list(node.get("@type"))) & self.raw_node_types)


@dataclass
class MergeReport:
    """What :func:`merge_overlays` kept and dropped (per document)."""

    nodes_merged: int = 0
    overlay_keys_kept: Counter = field(default_factory=Counter)
    overlay_types_kept: Counter = field(default_factory=Counter)
    overlay_nodes_kept: Counter = field(default_factory=Counter)
    raw_keys_dropped: Counter = field(default_factory=Counter)
    raw_nodes_dropped: int = 0

    @property
    def overlay_total(self) -> int:
        return (
            sum(self.overlay_keys_kept.values())
            + sum(self.overlay_types_kept.values())
            + sum(self.overlay_nodes_kept.values())
        )

    def update(self, other: "MergeReport") -> None:
        self.nodes_merged += other.nodes_merged
        self.overlay_keys_kept.update(other.overlay_keys_kept)
        self.overlay_types_kept.update(other.overlay_types_kept)
        self.overlay_nodes_kept.update(other.overlay_nodes_kept)
        self.raw_keys_dropped.update(other.raw_keys_dropped)
        self.raw_nodes_dropped += other.raw_nodes_dropped

    def summary(self) -> str:
        keys = ", ".join(f"{k}={v}" for k, v in sorted(self.overlay_keys_kept.items()))
        nodes = ", ".join(f"{k}={v}" for k, v in sorted(self.overlay_nodes_kept.items()))
        return (
            f"merged {self.nodes_merged} nodes; overlay keys [{keys or '-'}]; "
            f"overlay nodes [{nodes or '-'}]; raw keys dropped "
            f"{sum(self.raw_keys_dropped.values())}; raw nodes dropped "
            f"{self.raw_nodes_dropped}"
        )


def _merge_types(existing: object, new: object, layer: IngestLayer, report: MergeReport) -> object:
    old_types = _type_list(existing)
    new_types = _type_list(new)
    if old_types == new_types:
        return new
    merged = [t for t in old_types if t in new_types or t in layer.overlay_types]
    for t in old_types:
        if t not in new_types and t in layer.overlay_types:
            report.overlay_types_kept[t] += 1
    merged.extend(t for t in new_types if t not in merged)
    if isinstance(new, str) and len(merged) == 1:
        return merged[0]
    return merged


def merge_node(
    new: Mapping[str, Any],
    existing: Mapping[str, Any],
    layer: IngestLayer,
    report: MergeReport | None = None,
) -> dict:
    """Merge one freshly built node onto its existing counterpart.

    Existing key order is kept; keys new to this build are appended.
    """
    report = report if report is not None else MergeReport()
    out: dict = {}
    for key, old_value in existing.items():
        if key in layer.seed_keys:
            out[key] = old_value
            report.overlay_keys_kept[key] += 1
        elif key in new:
            if key == "@type":
                out[key] = _merge_types(old_value, new[key], layer, report)
            elif key in layer.combiners:
                out[key] = layer.combiners[key](old_value, new[key])
            elif key in layer.union_keys:
                out[key] = union_values(old_value, new[key])
            elif _same_value_set(old_value, new[key]):
                # Same JSON-LD values in another order / array shape: keep
                # the published serialisation.
                out[key] = old_value
            else:
                out[key] = new[key]
        elif key.startswith("@") or key in layer.raw_keys:
            report.raw_keys_dropped[key] += 1
        else:
            out[key] = old_value
            report.overlay_keys_kept[key] += 1
    for key, value in new.items():
        if key not in out and key not in existing and key not in layer.seed_keys:
            out[key] = value
    report.nodes_merged += 1
    return out


def _merge_graph(
    new_graph: list,
    old_graph: list,
    layer: IngestLayer,
    report: MergeReport,
) -> list:
    old_by_id: dict[str, dict] = {}
    for node in old_graph:
        if isinstance(node, dict) and isinstance(node.get("@id"), str):
            old_by_id.setdefault(node["@id"], node)
    new_ids = {
        n.get("@id") for n in new_graph if isinstance(n, dict) and isinstance(n.get("@id"), str)
    }

    # Overlay nodes keep their position: each is anchored after the closest
    # preceding existing node that the new build still emits.
    head: list = []
    after: dict[str, list] = {}
    anchor: str | None = None
    for node in old_graph:
        nid = node.get("@id") if isinstance(node, dict) else None
        if isinstance(nid, str) and nid in new_ids:
            anchor = nid
            continue
        if not isinstance(node, dict) or layer.owns_node(node):
            report.raw_nodes_dropped += 1
            continue
        specific = [t for t in _type_list(node.get("@type")) if t != "owl:NamedIndividual"]
        report.overlay_nodes_kept[(specific or ["(untyped)"])[0]] += 1
        (after.setdefault(anchor, []) if anchor is not None else head).append(node)

    out: list = list(head)
    emitted: set[str] = set()
    for node in new_graph:
        nid = node.get("@id") if isinstance(node, dict) else None
        if isinstance(nid, str) and nid in old_by_id and nid not in emitted:
            out.append(merge_node(node, old_by_id[nid], layer, report))
        else:
            out.append(node)
        if isinstance(nid, str):
            emitted.add(nid)
            out.extend(after.pop(nid, []))
    return out


def _used_prefixes(value: object, candidates: frozenset[str], out: set[str]) -> None:
    """Collect which ``candidates`` prefixes ``value`` uses (keys, @id/@type values)."""

    def _note(token: str) -> None:
        head, sep, _ = token.partition(":")
        if sep and head in candidates:
            out.add(head)

    if isinstance(value, dict):
        for key, item in value.items():
            _note(key)
            if key in ("@id", "@type"):
                for token in item if isinstance(item, list) else [item]:
                    if isinstance(token, str):
                        _note(token)
            else:
                _used_prefixes(item, candidates, out)
    elif isinstance(value, list):
        for item in value:
            _used_prefixes(item, candidates, out)


def _choose_context(new_ctx: object, old_ctx: object, merged_graph: object) -> object:
    """Keep the published ``@context`` when it still covers the document.

    A shared CONTEXT that grew new prefixes after a peep was published would
    otherwise churn every file on re-ingest without changing a triple. The
    existing context wins when every prefix the merged graph uses is defined
    there with the same IRI; otherwise the new context is used.
    """
    if not isinstance(new_ctx, dict) or not isinstance(old_ctx, dict) or new_ctx == old_ctx:
        return new_ctx
    candidates = frozenset(k for k in (*new_ctx, *old_ctx) if isinstance(k, str))
    used: set[str] = set()
    _used_prefixes(merged_graph, candidates, used)
    for prefix in used:
        if prefix not in old_ctx or (prefix in new_ctx and new_ctx[prefix] != old_ctx[prefix]):
            # New raw terms may require the new context, while retained
            # overlay predicates still require prefixes only the old one had.
            return {**old_ctx, **new_ctx}
    return old_ctx


def merge_overlays(
    new_doc: Mapping[str, Any],
    existing_doc: Mapping[str, Any] | None,
    layer: IngestLayer,
) -> tuple[dict, MergeReport]:
    """Return ``(merged_doc, report)``: ``new_doc`` with ``existing_doc``'s overlay.

    ``new_doc`` is not mutated. With no (or an unreadable) existing document
    the result equals ``new_doc``.
    """
    report = MergeReport()
    if not isinstance(existing_doc, Mapping):
        return dict(new_doc), report
    out: dict = {}
    for key, old_value in existing_doc.items():
        if key == "@graph":
            continue
        if key in new_doc:
            out[key] = new_doc[key]
        elif not key.startswith("@") and key not in layer.raw_keys:
            out[key] = old_value
    for key, value in new_doc.items():
        if key == "@graph":
            continue
        out.setdefault(key, value)
    new_graph = new_doc.get("@graph")
    old_graph = existing_doc.get("@graph")
    if isinstance(new_graph, list):
        merged_graph = (
            _merge_graph(new_graph, old_graph, layer, report)
            if isinstance(old_graph, list)
            else list(new_graph)
        )
        if "@context" in out:
            out["@context"] = _choose_context(
                new_doc.get("@context"), existing_doc.get("@context"), merged_graph
            )
        # Re-insert @graph where the new document had it.
        ordered: dict = {}
        for key in new_doc:
            ordered[key] = merged_graph if key == "@graph" else out.pop(key)
        ordered.update(out)
        out = ordered
    return out, report


def load_existing_doc(path: Path) -> dict | None:
    """Existing peep at ``path`` or ``None`` when absent / unreadable."""
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    return doc if isinstance(doc, dict) else None


def prepare_write(
    path: Path,
    new_doc: dict,
    layer: IngestLayer,
    *,
    replace_overlays: bool = False,
    existing_doc: Mapping[str, Any] | None = None,
    log: logging.Logger | None = None,
) -> tuple[dict, MergeReport]:
    """Return the document to persist at ``path`` under the overlay rule.

    ``existing_doc`` may be passed when the caller already loaded it.
    With ``replace_overlays`` the raw ``new_doc`` is returned and the overlay
    that is being discarded is logged at WARNING.
    """
    log = log or logger
    existing = existing_doc if existing_doc is not None else load_existing_doc(path)
    merged, report = merge_overlays(new_doc, existing, layer)
    if not replace_overlays:
        return merged, report
    if report.overlay_total:
        log.warning(
            "--replace-overlays: %s drops the %s overlay on %s (%s)",
            layer.name,
            report.overlay_total,
            path.name,
            report.summary(),
        )
    return new_doc, report


def add_replace_overlays_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--replace-overlays",
        action="store_true",
        default=False,
        help=REPLACE_OVERLAYS_HELP,
    )


def log_replace_overlays_mode(layer: IngestLayer, enabled: bool, log: logging.Logger | None = None) -> None:
    """Announce the explicit opt-out once per run."""
    if enabled:
        (log or logger).warning(
            "%s: --replace-overlays set — enrichment overlays on existing peeps "
            "will be DISCARDED (#697 opt-out).",
            layer.name,
        )

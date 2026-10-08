#!/usr/bin/env python3
"""Build the Estonian legal-reasoning benchmark (issue #727).

Three task families are derived deterministically from the committed corpus
(no network, no LFS inputs):

``point_in_time``
    "What did <law> § <n> say on <date>?" from the provision-version sidecars
    under ``krr_outputs/provision_versions/``. The answer is the redaction text
    in force on that date; the other redactions of the same § are distractors.
``court_interpretation``
    "Which provisions does Riigikohus decision <case no.> interpret?" from
    ``estleg:interpretsLaw`` on the Riigikohus decision nodes. Only the case
    number, ECLI, decision date and chamber are read — never party names,
    judges, summaries or decision text (docs/DATA_PROTECTION.md).
``cross_reference``
    "Which provision does the citation '<text>' in <law> § <n> refer to?"
    from the ``estleg:references`` edges on law provisions. The citation
    string is re-extracted from the provision's own ``estleg:legalText`` with
    the cross-reference extractor and matched to exactly one existing edge.

Splits are assigned by hashing the *group* (source law, or decision) so no law
and no decision appears in more than one split. Sampling is a fixed-salt
SHA-256 ranking, so the same corpus always yields the same items.

Usage::

    python3 scripts/build_legal_benchmark.py                  # full build
    python3 scripts/build_legal_benchmark.py --sample-per-task 200 \
        --out eval/benchmark/sample                           # committed sample

See docs/BENCHMARK.md for the task definitions and evaluation protocol.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Iterable, Iterator, Mapping

from estleg.estleg_common import (
    BUILD_EVALUATION_DATE,
    KRR_DIR,
    REPO_ROOT,
    act_root_node,
    jsonld_id_values,
    jsonld_text,
    node_type_list,
)
from estleg.extract_cross_references import extract_citations_from_text

BENCHMARK_NAME = "estleg-legal-reasoning"
BENCHMARK_VERSION = "0.1.0"
#: Fixed salt for every hash-based decision (sampling, splits, choice order).
SEED = "estleg-bench-727-v1"

TASK_POINT_IN_TIME = "point_in_time"
TASK_COURT = "court_interpretation"
TASK_CROSS_REF = "cross_reference"
TASKS: tuple[str, ...] = (TASK_POINT_IN_TIME, TASK_COURT, TASK_CROSS_REF)

SPLITS: tuple[str, ...] = ("train", "dev", "test")
#: Cumulative percentage thresholds: bucket < 80 → train, < 90 → dev, else test.
SPLIT_THRESHOLDS: tuple[tuple[str, int], ...] = (("train", 80), ("dev", 90), ("test", 100))

DEFAULT_CAP_PER_TASK = 3000
#: At most this many items from one law (tasks a, c) so the big codes do not
#: dominate. Decisions are their own group and yield one item each.
DEFAULT_PER_GROUP_CAP = 30
#: Point-in-time texts longer than this are skipped (answer) or not used
#: (distractor); long multi-lõige sections make poor multiple-choice items.
DEFAULT_MAX_TEXT_CHARS = 1500
MAX_DISTRACTORS = 3
#: Decisions interpreting more than this many provisions are skipped.
MAX_COURT_TARGETS = 25
#: Case types excluded by default (Art. 10 GDPR: offence data linked to a case).
CRIMINAL_CASE_TYPES = frozenset({"estleg:CaseType_Criminal", "estleg:CaseType_Misdemeanor"})

ESTLEG_NS = "https://w3id.org/estleg/"

LICENCE_LAW_TEXT = {
    "source_text": (
        "Estonian statutory text from Riigi Teataja (https://www.riigiteataja.ee). "
        "Legislation is not an object of copyright (Autoriõiguse seadus § 5)."
    ),
    "source_terms": (
        "VERIFY: Riigi Teataja reuse/database terms for the consolidated product "
        "are not yet confirmed (NOTICE; docs/DATA_RIGHTS.md)."
    ),
    "compilation": (
        "Item construction, IRIs and links: CC BY 4.0, Estonian Legal Ontology "
        "project (draft election; CC0 1.0 is the alternative pending sign-off)."
    ),
    "status": "DRAFT — pending legal / data-owner sign-off",
}
LICENCE_COURT = {
    "source_text": (
        "Riigikohus decision metadata (case number, ECLI, date, chamber) from "
        "RIK / Riigikohus. Judgments are not objects of copyright "
        "(Autoriõiguse seadus § 5). No decision text, summary, judge or party "
        "name is included."
    ),
    "source_terms": (
        "VERIFY: personal-data position is a DRAFT pending DPO confirmation "
        "(docs/DATA_PROTECTION.md, #720); a republisher is an independent "
        "GDPR controller."
    ),
    "compilation": LICENCE_LAW_TEXT["compilation"],
    "status": "DRAFT — pending DPO / legal / data-owner sign-off",
}

_WS_RE = re.compile(r"\s+")
_PAR_IN_REF_RE = re.compile(r"§\s*[\w¹²³⁴⁵⁶⁷⁸⁹⁰]+")
_TARGET_IRI_RE = re.compile(r"^(?P<prefix>.+?)_Par_(?P<par>\d+(?:_\d+)?)(?:_Lg_(?P<lg>\d+(?:_\d+)?))?$")


# ── small deterministic helpers ──────────────────────────────────────────────


def stable_hash(*parts: str) -> str:
    """SHA-256 hex digest of ``SEED`` and ``parts`` joined by ``|``."""
    return hashlib.sha256("|".join((SEED, *parts)).encode("utf-8")).hexdigest()


def assign_split(group: str) -> str:
    """Deterministic train/dev/test split for a group id (law or decision)."""
    bucket = int(stable_hash("split", group)[:8], 16) % 100
    for name, threshold in SPLIT_THRESHOLDS:
        if bucket < threshold:
            return name
    return SPLITS[-1]


def normalize_text(text: str) -> str:
    """Whitespace-insensitive, case-folded comparison key for answer texts."""
    return _WS_RE.sub("", text).casefold()


def normalize_iri(value: str) -> str:
    """Compact an estleg IRI to ``estleg:<local>`` for comparison."""
    value = value.strip()
    if value.startswith(ESTLEG_NS):
        return "estleg:" + value[len(ESTLEG_NS):]
    return value


def _local(iri: str) -> str:
    return iri.split(":", 1)[1] if iri.startswith("estleg:") else iri


def _date_value(value: object) -> date | None:
    text = jsonld_text(value)
    try:
        return date.fromisoformat(text[:10]) if text else None
    except ValueError:
        return None


def _iri_value(value: object) -> str | None:
    ids = jsonld_id_values(value)
    return ids[0] if ids else None


def _load_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _graph(doc: object) -> list[dict]:
    if isinstance(doc, dict):
        return [n for n in doc.get("@graph") or [] if isinstance(n, dict)]
    if isinstance(doc, list):
        return [n for n in doc if isinstance(n, dict)]
    return []


def _is_provision(node: dict) -> bool:
    node_id = node.get("@id")
    if not isinstance(node_id, str) or "_Par_" not in node_id:
        return False
    types = node_type_list(node)
    return "estleg:Citation" not in types and "estleg:ProvisionVersion" not in types


def provision_display(node: dict) -> str:
    """Human label of a provision: ``§ 12`` or ``§ 2 lg 1²``."""
    label = jsonld_text(node.get("rdfs:label"))
    if "estleg:Subsection" in node_type_list(node) and label:
        return label.strip()
    par = jsonld_text(node.get("estleg:paragrahv")) or label
    match = _PAR_IN_REF_RE.search(par)
    return match.group(0) if match else (label.strip() or _local(str(node.get("@id"))))


def _date_et(value: date) -> str:
    return f"{value.day:02d}.{value.month:02d}.{value.year}"


# ── law index (one pass over the root law peeps) ─────────────────────────────


@dataclass
class LawInfo:
    """One INDEX.json law: title, RT citation, files and provision prefixes."""

    name: str
    title: str
    act_iri: str | None
    rt_citation: str | None
    kehtiv: str | None
    files: list[str]
    prefixes: set[str] = field(default_factory=set)


@dataclass
class LawIndex:
    laws: dict[str, LawInfo]
    provision_law: dict[str, str]
    provision_label: dict[str, str]

    def target_label(self, iri: str) -> str:
        law_name = self.provision_law.get(iri)
        label = self.provision_label.get(iri)
        if law_name and label:
            return f"{self.laws[law_name].title} {label}"
        return iri


def _first_et_text(value: object) -> str:
    """First Estonian (or untagged) string of a JSON-LD literal / list."""
    values = value if isinstance(value, list) else [value]
    for item in values:
        if isinstance(item, str) and item.strip():
            return item.strip()
        if isinstance(item, dict) and item.get("@language") in (None, "et"):
            text = jsonld_text(item).strip()
            if text:
                return text
    return ""


def _law_root_fields(graphs: Iterable[tuple[dict, list[dict]]]) -> tuple[str | None, str, str | None, str | None]:
    """(act_iri, title, rt_citation, kehtiv) from the best root node of a law.

    An ``estleg:Act`` / ``estleg:Law`` root beats an ``estleg:Part`` root; the
    first root carrying ``dcterms:source`` wins within a rank.
    """
    best: tuple[int, dict] | None = None
    for doc, _graph_nodes in graphs:
        root = act_root_node(doc)
        if root is None:
            continue
        types = node_type_list(root)
        rank = 0 if {"estleg:Act", "estleg:Law"} & set(types) else 1
        if root.get("dcterms:source"):
            rank -= 10
        if best is None or rank < best[0]:
            best = (rank, root)
    if best is None:
        return None, "", None, None
    root = best[1]
    title = _first_et_text(root.get("dcterms:title")) or jsonld_text(root.get("dc:source"))
    return (
        root.get("@id"),
        title,
        _iri_value(root.get("dcterms:source")),
        jsonld_text(root.get("estleg:kehtiv")) or None,
    )


def iter_law_documents(krr_dir: Path) -> Iterator[tuple[str, list[str], list[tuple[dict, list[dict]]]]]:
    """Yield ``(law name, files, [(doc, graph)])`` for every INDEX.json law."""
    index = _load_json(krr_dir / "INDEX.json")
    for entry in index.get("laws") or []:
        name = entry.get("name")
        files = [f for f in entry.get("files") or [] if (krr_dir / f).is_file()]
        if not name or not files:
            continue
        docs = []
        for rel in files:
            doc = _load_json(krr_dir / rel)
            docs.append((doc, _graph(doc)))
        yield name, files, docs


def load_law_index(krr_dir: Path) -> LawIndex:
    """Index every law: root metadata, provision → law, provision labels."""
    laws: dict[str, LawInfo] = {}
    provision_law: dict[str, str] = {}
    provision_label: dict[str, str] = {}
    for name, files, docs in iter_law_documents(krr_dir):
        act_iri, title, rt_citation, kehtiv = _law_root_fields(docs)
        info = LawInfo(name, title or name, act_iri, rt_citation, kehtiv, files)
        for _doc, nodes in docs:
            for node in nodes:
                if not _is_provision(node):
                    continue
                iri = node["@id"]
                info.prefixes.add(_local(iri).split("_Par_", 1)[0])
                provision_law.setdefault(iri, name)
                provision_label.setdefault(iri, provision_display(node))
        laws[name] = info
    return LawIndex(laws, provision_law, provision_label)


# ── item assembly and sampling ───────────────────────────────────────────────


def _ordered_choices(item_id: str, answer: str, distractors: list[str]) -> tuple[list[str], int]:
    choices = sorted([answer, *distractors], key=lambda c: stable_hash("choice", item_id, c))
    return choices, choices.index(answer)


def sample_items(
    candidates: list[dict], *, cap: int, per_group_cap: int | None
) -> list[dict]:
    """Rank candidates by a salted hash of their id; apply group then task caps."""
    ranked = sorted(candidates, key=lambda it: stable_hash("rank", it["id"]))
    if per_group_cap is not None:
        seen: Counter[str] = Counter()
        kept = []
        for item in ranked:
            if seen[item["group"]] < per_group_cap:
                seen[item["group"]] += 1
                kept.append(item)
        ranked = kept
    return ranked[:cap]


# ── task (a): point-in-time provision text ───────────────────────────────────


@dataclass
class _Version:
    iri: str
    text: str
    valid_from: date
    valid_to: date | None
    redaction_id: str
    rt_url: str | None
    provision_ref: str


def _parse_version(node: dict) -> _Version | None:
    text = jsonld_text(node.get("estleg:versionText")).strip()
    valid_from = _date_value(node.get("estleg:versionValidFrom"))
    if not text or valid_from is None:
        return None
    valid_to = _date_value(node.get("estleg:versionValidTo"))
    if valid_to is not None and valid_to < valid_from:
        return None
    return _Version(
        iri=node["@id"],
        text=text,
        valid_from=valid_from,
        valid_to=valid_to,
        redaction_id=jsonld_text(node.get("estleg:versionRedactionId")),
        rt_url=jsonld_text(node.get("estleg:rtUrl")) or None,
        provision_ref=jsonld_text(node.get("estleg:provisionRef")),
    )


def _query_date(version: _Version) -> date:
    if version.valid_to is None:
        return version.valid_from
    return date.fromordinal((version.valid_from.toordinal() + version.valid_to.toordinal()) // 2)


def _covers(version: _Version, day: date) -> bool:
    return version.valid_from <= day and (version.valid_to is None or day <= version.valid_to)


def build_point_in_time_candidates(
    krr_dir: Path, index: LawIndex, *, max_text_chars: int, stats: Counter
) -> list[dict]:
    """One candidate item per law provision with ≥ 2 distinct redaction texts."""
    candidates: list[dict] = []
    for path in sorted((krr_dir / "provision_versions").glob("*.jsonld")):
        by_provision: dict[str, list[_Version]] = defaultdict(list)
        for node in _graph(_load_json(path)):
            if "estleg:ProvisionVersion" not in node_type_list(node):
                continue
            provision = _iri_value(node.get("estleg:versionOf"))
            version = _parse_version(node)
            if provision and version:
                by_provision[provision].append(version)
        file_has_history = False
        for provision, versions in sorted(by_provision.items()):
            if len({normalize_text(v.text) for v in versions}) < 2:
                continue
            stats["provisions_with_distinct_history"] += 1
            file_has_history = True
            law_name = index.provision_law.get(provision)
            if law_name is None:
                stats["skip_not_a_law_provision"] += 1
                continue
            item = _point_in_time_item(provision, versions, index, law_name, max_text_chars, stats)
            if item is not None:
                candidates.append(item)
        if file_has_history:
            stats["sidecars_with_distinct_history"] += 1
    return candidates


def _point_in_time_item(
    provision: str,
    versions: list[_Version],
    index: LawIndex,
    law_name: str,
    max_text_chars: int,
    stats: Counter,
) -> dict | None:
    law = index.laws[law_name]
    versions = sorted(versions, key=lambda v: (v.valid_from, v.iri))
    eligible = []
    for version in versions:
        if len(version.text) > max_text_chars:
            continue
        day = _query_date(version)
        if sum(_covers(v, day) for v in versions) != 1:
            continue
        key = normalize_text(version.text)
        others = [
            v for v in versions
            if normalize_text(v.text) != key and len(v.text) <= max_text_chars
        ]
        if others:
            eligible.append((version, day, others))
    if not eligible:
        stats["skip_no_unambiguous_version"] += 1
        return None
    version, day, others = min(eligible, key=lambda e: stable_hash("version", e[0].iri))
    # Hardest distractors first: the redactions closest in time, one per text.
    others.sort(key=lambda v: (abs(v.valid_from.toordinal() - version.valid_from.toordinal()), v.iri))
    distractors: list[str] = []
    seen = {normalize_text(version.text)}
    for other in others:
        key = normalize_text(other.text)
        if key not in seen:
            seen.add(key)
            distractors.append(other.text)
        if len(distractors) == MAX_DISTRACTORS:
            break
    rt_citation = law.rt_citation or version.rt_url
    if not rt_citation:
        stats["skip_no_rt_citation"] += 1
        return None
    par_label = index.provision_label.get(provision) or (
        (_PAR_IN_REF_RE.search(version.provision_ref) or [version.provision_ref])[0]
    )
    item_id = f"pit-{_local(provision)}-{day.isoformat()}"
    choices, answer_index = _ordered_choices(item_id, version.text, distractors)
    return {
        "id": item_id,
        "task": TASK_POINT_IN_TIME,
        "group": law_name,
        "prompt": (
            f"Mis oli „{law.title}“ {par_label} tekst seisuga {_date_et(day)}? "
            "Vali vastusevariantide hulgast sel kuupäeval kehtinud redaktsioon."
        ),
        "answer_type": "choice",
        "answers": [version.text],
        "distractors": distractors,
        "choices": choices,
        "answer_index": answer_index,
        "source_iris": [provision, version.iri],
        "rt_citation": rt_citation,
        "source_url": version.rt_url,
        "snapshot": {
            "query_date": day.isoformat(),
            "version_iri": version.iri,
            "redaction_id": version.redaction_id,
            "valid_from": version.valid_from.isoformat(),
            "valid_to": version.valid_to.isoformat() if version.valid_to else None,
            "law_kehtiv": law.kehtiv,
            "evaluation_date": BUILD_EVALUATION_DATE,
        },
        "licence": LICENCE_LAW_TEXT,
    }


# ── task (b): court → provision interpretation ───────────────────────────────


def build_court_candidates(
    krr_dir: Path, index: LawIndex, *, include_criminal: bool, stats: Counter
) -> list[dict]:
    """One candidate per Riigikohus decision with 1..MAX_COURT_TARGETS targets.

    PERSONAL DATA: reads only ``@id``, caseNumber, ecliIdentifier,
    decisionDate, chamber, caseType (filter only) and interpretsLaw.
    """
    candidates: list[dict] = []
    for path in sorted((krr_dir / "riigikohus").glob("riigikohus_*_peep.json")):
        for node in _graph(_load_json(path)):
            if "estleg:CourtDecision" not in node_type_list(node):
                continue
            stats["decisions_scanned"] += 1
            targets = sorted(set(jsonld_id_values(node.get("estleg:interpretsLaw"))))
            if not targets:
                continue
            stats["decisions_with_targets"] += 1
            stats["resolved_citations"] += len(targets)
            case_type = _iri_value(node.get("estleg:caseType"))
            if case_type in CRIMINAL_CASE_TYPES and not include_criminal:
                stats["skip_criminal_or_misdemeanour"] += 1
                continue
            if len(targets) > MAX_COURT_TARGETS:
                stats["skip_too_many_targets"] += 1
                continue
            item = _court_item(node, targets, index)
            if item is None:
                stats["skip_missing_case_metadata"] += 1
                continue
            candidates.append(item)
    return candidates


def _court_item(node: dict, targets: list[str], index: LawIndex) -> dict | None:
    decision_iri = node.get("@id")
    case_number = jsonld_text(node.get("estleg:caseNumber")).strip()
    decided = _date_value(node.get("estleg:decisionDate"))
    if not isinstance(decision_iri, str) or not case_number or decided is None:
        return None
    chamber = jsonld_text(node.get("estleg:chamber")).strip() or None
    ecli = jsonld_text(node.get("estleg:ecliIdentifier")).strip() or None
    qualifiers = ", ".join(x for x in (chamber, ecli and f"ECLI {ecli}") if x)
    prompt = (
        f"Milliseid õigusakti sätteid tõlgendab Riigikohtu {_date_et(decided)} lahend "
        f"kohtuasjas nr {case_number}{f' ({qualifiers})' if qualifiers else ''}? "
        "Loetle kõik tõlgendatud sätted."
    )
    return {
        "id": f"court-{_local(decision_iri)}",
        "task": TASK_COURT,
        "group": decision_iri,
        "prompt": prompt,
        "answer_type": "iri_set",
        "answers": targets,
        "answer_labels": [index.target_label(t) for t in targets],
        "distractors": [],
        "source_iris": [decision_iri, *targets],
        "rt_citation": None,
        "source_url": f"https://www.riigikohus.ee/et/lahendid/?asjaNr={case_number}",
        "snapshot": {
            "decision_date": decided.isoformat(),
            "case_number": case_number,
            "ecli": ecli,
            "chamber": chamber,
            "evaluation_date": BUILD_EVALUATION_DATE,
        },
        "licence": LICENCE_COURT,
    }


# ── task (c): cross-reference resolution ─────────────────────────────────────


def _match_reference(citation: dict, refs: list[str], own_prefixes: set[str]) -> str | None:
    """The single reference in ``refs`` that ``citation`` resolves to, else None.

    Matching is structural: same § (and lõige when cited), a target inside the
    citing law for ``käesoleva seaduse`` citations and outside it otherwise.
    Ambiguity (0 or > 1 candidates) yields None.
    """
    if len(citation.get("paragraphs") or []) != 1:
        return None
    par = citation["paragraphs"][0]
    lg = (citation.get("lg") or "").strip() or None
    exact, fallback = [], []
    for ref in refs:
        match = _TARGET_IRI_RE.match(_local(ref))
        if not match or match.group("par") != par:
            continue
        inside = match.group("prefix") in own_prefixes
        if inside != bool(citation.get("is_self_ref")):
            continue
        ref_lg = match.group("lg")
        if ref_lg == lg:
            exact.append(ref)
        elif lg and ref_lg is None:
            fallback.append(ref)
    pool = exact or fallback
    return pool[0] if len(pool) == 1 else None


def build_cross_reference_candidates(
    krr_dir: Path, index: LawIndex, *, stats: Counter
) -> list[dict]:
    """One candidate per (provision, citation string) resolved to one edge."""
    candidates: list[dict] = []
    for name, _files, docs in iter_law_documents(krr_dir):
        law = index.laws.get(name)
        if law is None:
            continue
        for _doc, nodes in docs:
            for node in nodes:
                if not _is_provision(node):
                    continue
                refs = jsonld_id_values(node.get("estleg:references"))
                text = jsonld_text(node.get("estleg:legalText"))
                if not refs or not text:
                    continue
                stats["provisions_with_references"] += 1
                stats["reference_edges"] += len(refs)
                seen_texts: set[str] = set()
                for citation in extract_citations_from_text(text):
                    cited = citation["citationText"]
                    if cited in seen_texts:
                        continue
                    seen_texts.add(cited)
                    stats["citations_extracted"] += 1
                    target = _match_reference(citation, refs, law.prefixes)
                    if target is None:
                        stats["skip_ambiguous_or_unmatched"] += 1
                        continue
                    if not law.rt_citation:
                        stats["skip_no_rt_citation"] += 1
                        continue
                    candidates.append(_cross_ref_item(node, cited, target, refs, law, index))
    return candidates


def _neighbour_iris(target: str) -> list[str]:
    """Adjacent lõiked / §§ of ``target`` (existence is checked by the caller)."""
    match = _TARGET_IRI_RE.match(_local(target))
    if not match:
        return []
    prefix, par, lg = match.group("prefix"), match.group("par"), match.group("lg")
    out: list[str] = []
    if lg and lg.isdigit():
        out += [f"estleg:{prefix}_Par_{par}_Lg_{n}" for n in (int(lg) + 1, int(lg) - 1) if n > 0]
        out.append(f"estleg:{prefix}_Par_{par}")
    if par.isdigit():
        out += [f"estleg:{prefix}_Par_{n}" for n in (int(par) + 1, int(par) - 1) if n > 0]
    return out


def _cross_ref_distractors(target: str, refs: list[str], index: LawIndex) -> list[str]:
    """Other edges of the citing provision first, then the target's neighbours."""
    pool = sorted(r for r in set(refs) if r != target)
    pool += [n for n in _neighbour_iris(target) if n in index.provision_law]
    distractors: list[str] = []
    for iri in pool:
        if iri != target and iri not in distractors:
            distractors.append(iri)
    return distractors[:MAX_DISTRACTORS]


def _cross_ref_item(
    node: dict, cited: str, target: str, refs: list[str], law: LawInfo, index: LawIndex
) -> dict:
    source = node["@id"]
    label = index.provision_label.get(source) or provision_display(node)
    distractors = _cross_ref_distractors(target, refs, index)
    item_id = f"xref-{_local(source)}-{stable_hash('cite', cited)[:10]}"
    choices, answer_index = _ordered_choices(item_id, target, distractors)
    return {
        "id": item_id,
        "task": TASK_CROSS_REF,
        "group": law.name,
        "prompt": (
            f"Millisele sättele viitab viide „{cited}“, mis sisaldub "
            f"„{law.title}“ sättes {label}? Vasta sätte identifikaatoriga."
        ),
        "answer_type": "iri",
        "answers": [target],
        "answer_labels": [index.target_label(target)],
        "distractors": distractors,
        "choices": choices,
        "answer_index": answer_index,
        "source_iris": [source, target],
        "rt_citation": law.rt_citation,
        "source_url": None,
        "snapshot": {
            "citation_text": cited,
            "law_kehtiv": law.kehtiv,
            "evaluation_date": BUILD_EVALUATION_DATE,
        },
        "licence": LICENCE_LAW_TEXT,
    }


# ── validation (dependency-free mirror of eval/benchmark/item.schema.json) ───

_REQUIRED_KEYS: dict[str, type | tuple[type, ...]] = {
    "id": str,
    "task": str,
    "split": str,
    "group": str,
    "prompt": str,
    "answer_type": str,
    "answers": list,
    "distractors": list,
    "source_iris": list,
    "rt_citation": (str, type(None)),
    "source_url": (str, type(None)),
    "snapshot": dict,
    "licence": dict,
}
_FORBIDDEN_COURT_KEYS = ("summary", "legalText", "judge", "rdfs:label")


def validate_item(item: Mapping) -> list[str]:
    """Return schema problems for one item (empty list when valid)."""
    problems = [
        f"{key}: expected {kind}" for key, kind in _REQUIRED_KEYS.items()
        if not isinstance(item.get(key), kind)
    ]
    if problems:
        return problems
    if item["task"] not in TASKS:
        problems.append(f"task: unknown {item['task']!r}")
    if item["split"] not in SPLITS:
        problems.append(f"split: unknown {item['split']!r}")
    if not item["answers"]:
        problems.append("answers: empty")
    if item["task"] != TASK_COURT and not item["rt_citation"]:
        problems.append("rt_citation: required for law-text tasks")
    if "choices" in item and item["choices"][item["answer_index"]] != item["answers"][0]:
        problems.append("answer_index: does not point at the answer")
    if item["task"] == TASK_COURT:
        blob = json.dumps(item, ensure_ascii=False)
        problems.extend(f"court item carries {k}" for k in _FORBIDDEN_COURT_KEYS if f'"{k}"' in blob)
    return problems


# ── orchestration ────────────────────────────────────────────────────────────


@dataclass
class BuildConfig:
    cap_per_task: int = DEFAULT_CAP_PER_TASK
    per_group_cap: int = DEFAULT_PER_GROUP_CAP
    max_text_chars: int = DEFAULT_MAX_TEXT_CHARS
    include_criminal: bool = False
    tasks: tuple[str, ...] = TASKS


def build_benchmark(krr_dir: Path, config: BuildConfig) -> tuple[list[dict], dict]:
    """Build every requested task; return ``(items, manifest)``."""
    index = load_law_index(krr_dir)
    stats: dict[str, Counter] = {task: Counter() for task in config.tasks}
    selected: dict[str, list[dict]] = {}
    candidate_counts: dict[str, int] = {}
    for task in config.tasks:
        if task == TASK_POINT_IN_TIME:
            cands = build_point_in_time_candidates(
                krr_dir, index, max_text_chars=config.max_text_chars, stats=stats[task]
            )
            group_cap: int | None = config.per_group_cap
        elif task == TASK_COURT:
            cands = build_court_candidates(
                krr_dir, index, include_criminal=config.include_criminal, stats=stats[task]
            )
            group_cap = None
        else:
            cands = build_cross_reference_candidates(krr_dir, index, stats=stats[task])
            group_cap = config.per_group_cap
        candidate_counts[task] = len(cands)
        selected[task] = sample_items(cands, cap=config.cap_per_task, per_group_cap=group_cap)

    items: list[dict] = []
    for task in config.tasks:
        for item in selected[task]:
            item["split"] = assign_split(item["group"])
            problems = validate_item(item)
            if problems:
                raise ValueError(f"invalid benchmark item {item['id']}: {problems}")
            items.append(item)
    items.sort(key=lambda it: (it["task"], it["id"]))
    manifest = build_manifest(items, config, candidate_counts, stats, len(index.laws))
    return items, manifest


def count_by_split(
    items: Iterable[Mapping], tasks: Iterable[str]
) -> tuple[dict[str, dict[str, int]], dict[str, dict[str, int]]]:
    """(items per task/split, distinct groups per task/split)."""
    counts = {task: {split: 0 for split in SPLITS} for task in tasks}
    groups: dict[str, dict[str, set[str]]] = {task: {split: set() for split in SPLITS} for task in counts}
    for item in items:
        counts[item["task"]][item["split"]] += 1
        groups[item["task"]][item["split"]].add(item["group"])
    return counts, {t: {s: len(g) for s, g in by.items()} for t, by in groups.items()}


def build_manifest(
    items: list[dict],
    config: BuildConfig,
    candidate_counts: Mapping[str, int],
    stats: Mapping[str, Counter],
    law_count: int,
) -> dict:
    counts, groups = count_by_split(items, config.tasks)
    return {
        "name": BENCHMARK_NAME,
        "version": BENCHMARK_VERSION,
        "seed": SEED,
        "evaluation_date": BUILD_EVALUATION_DATE,
        "split_thresholds_percent": dict(SPLIT_THRESHOLDS),
        "split_key": {
            TASK_POINT_IN_TIME: "source law (INDEX.json name)",
            TASK_COURT: "decision IRI",
            TASK_CROSS_REF: "citing law (INDEX.json name)",
        },
        "caps": {
            "per_task": config.cap_per_task,
            "per_law_group": config.per_group_cap,
            "max_text_chars": config.max_text_chars,
            "max_distractors": MAX_DISTRACTORS,
            "max_court_targets": MAX_COURT_TARGETS,
            "include_criminal": config.include_criminal,
        },
        "laws_indexed": law_count,
        "candidates": dict(candidate_counts),
        "items": counts,
        "groups": groups,
        "total_items": len(items),
        "source_stats": {task: dict(sorted(c.items())) for task, c in stats.items()},
    }


def take_sample(items: list[dict], per_task: int) -> list[dict]:
    """First ``per_task`` items of each task in rank order (a strict subset)."""
    by_task: dict[str, list[dict]] = defaultdict(list)
    for item in sorted(items, key=lambda it: stable_hash("rank", it["id"])):
        if len(by_task[item["task"]]) < per_task:
            by_task[item["task"]].append(item)
    return sorted((it for group in by_task.values() for it in group), key=lambda it: (it["task"], it["id"]))


def write_outputs(out_dir: Path, items: list[dict], manifest: dict) -> dict[str, Path]:
    """Write ``<split>.jsonl`` files plus ``manifest.json``; return the paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for split in SPLITS:
        path = out_dir / f"{split}.jsonl"
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            for item in items:
                if item["split"] == split:
                    fh.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
        paths[split] = path
    manifest_path = out_dir / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    paths["manifest"] = manifest_path
    return paths


def read_items(paths: Iterable[Path]) -> list[dict]:
    """Load benchmark items from JSONL files."""
    items = []
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            items.extend(json.loads(line) for line in fh if line.strip())
    return items


# ── reference scorer ─────────────────────────────────────────────────────────


def _prediction_set(item: Mapping, prediction: object) -> set[str]:
    """Normalise one prediction to a comparable set of answer keys."""
    if prediction is None:
        return set()
    if isinstance(prediction, bool):
        return set()
    if isinstance(prediction, int):
        choices = item.get("choices") or []
        prediction = choices[prediction] if 0 <= prediction < len(choices) else None
        if prediction is None:
            return set()
    values = prediction if isinstance(prediction, (list, tuple, set)) else [prediction]
    if item["answer_type"] == "choice":
        return {normalize_text(str(v)) for v in values}
    return {normalize_iri(str(v)) for v in values}


def _gold_set(item: Mapping) -> set[str]:
    if item["answer_type"] == "choice":
        return {normalize_text(a) for a in item["answers"]}
    return {normalize_iri(a) for a in item["answers"]}


def score_predictions(items: Iterable[Mapping], predictions: Mapping[str, object]) -> dict:
    """Score predictions keyed by item id.

    A prediction is a choice index (int), an answer string / IRI, or a list
    of IRIs for ``court_interpretation``. Missing predictions score 0.
    Per task: ``exact_match`` (prediction set equals gold set) and
    ``set_f1`` (mean per-item F1; equals exact match for single-answer tasks).
    """
    per_task: dict[str, dict[str, float]] = {}
    totals: dict[str, list[float]] = defaultdict(lambda: [0, 0, 0.0, 0.0])
    for item in items:
        gold = _gold_set(item)
        predicted = _prediction_set(item, predictions.get(item["id"]))
        hits = len(gold & predicted)
        precision = hits / len(predicted) if predicted else 0.0
        recall = hits / len(gold) if gold else 0.0
        f1 = 2 * precision * recall / (precision + recall) if hits else 0.0
        bucket = totals[item["task"]]
        bucket[0] += 1
        bucket[1] += item["id"] in predictions
        bucket[2] += float(predicted == gold)
        bucket[3] += f1
    for task, (n, answered, em, f1) in sorted(totals.items()):
        per_task[task] = {
            "n": n,
            "answered": answered,
            "exact_match": round(em / n, 4) if n else 0.0,
            "set_f1": round(f1 / n, 4) if n else 0.0,
        }
    n_all = sum(t["n"] for t in per_task.values())
    overall = {
        "n": n_all,
        "macro_exact_match": round(sum(t["exact_match"] for t in per_task.values()) / len(per_task), 4)
        if per_task else 0.0,
        "macro_set_f1": round(sum(t["set_f1"] for t in per_task.values()) / len(per_task), 4)
        if per_task else 0.0,
    }
    return {"per_task": per_task, "overall": overall}


# ── CLI ──────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--krr", type=Path, default=KRR_DIR, help="corpus root (krr_outputs)")
    parser.add_argument(
        "--out", type=Path, default=REPO_ROOT / "eval" / "benchmark" / "full",
        help="output directory for <split>.jsonl and manifest.json",
    )
    parser.add_argument("--cap", type=int, default=DEFAULT_CAP_PER_TASK, help="max items per task")
    parser.add_argument("--per-group-cap", type=int, default=DEFAULT_PER_GROUP_CAP,
                        help="max items per law for point_in_time / cross_reference")
    parser.add_argument("--max-text-chars", type=int, default=DEFAULT_MAX_TEXT_CHARS)
    parser.add_argument("--include-criminal", action="store_true",
                        help="keep criminal / misdemeanour decisions (needs DPO sign-off, Art. 10 GDPR)")
    parser.add_argument("--tasks", nargs="+", choices=TASKS, default=list(TASKS))
    parser.add_argument("--sample-per-task", type=int, default=None,
                        help="write only the first N ranked items of each task")
    parser.add_argument("--score", type=Path, default=None,
                        help="score a predictions JSON ({id: prediction}) against --out and exit")
    parser.add_argument("--split", choices=SPLITS, default="test", help="split scored by --score")
    args = parser.parse_args(argv)

    if args.score is not None:
        items = read_items([args.out / f"{args.split}.jsonl"])
        predictions = _load_json(args.score)
        print(json.dumps(score_predictions(items, predictions), indent=2, ensure_ascii=False))
        return 0

    config = BuildConfig(
        cap_per_task=args.cap,
        per_group_cap=args.per_group_cap,
        max_text_chars=args.max_text_chars,
        include_criminal=args.include_criminal,
        tasks=tuple(args.tasks),
    )
    items, manifest = build_benchmark(args.krr, config)
    if args.sample_per_task is not None:
        manifest["full_build"] = {
            "items": manifest["items"],
            "groups": manifest["groups"],
            "total_items": manifest["total_items"],
        }
        items = take_sample(items, args.sample_per_task)
        manifest["sample_per_task"] = args.sample_per_task
        manifest["items"], manifest["groups"] = count_by_split(items, config.tasks)
        manifest["total_items"] = len(items)
    paths = write_outputs(args.out, items, manifest)
    for name, path in paths.items():
        print(f"{name}: {path}")
    print(json.dumps(manifest["items"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

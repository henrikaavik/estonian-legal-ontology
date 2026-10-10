#!/usr/bin/env python3
"""Legal gold sets for the heuristic layers (#698).

Builds ``eval/gold_sets/<layer>.json``: a deterministic, stratified sample of
what the system asserts (and deliberately, what it does not assert) for eight
heuristic layers, laid out so a legal reviewer can adjudicate each item from
the quoted evidence span without opening the source. Every file validates
against the shared ``eval/gold_sets/item.schema.json``.

Layers: cross-references, sanctions, EuroVoc, deontic (normative type), target
group, competence, court links, and the #699 ``skos:altLabel`` orthographic
folds (which replaced the former Levenshtein ``skos:closeMatch`` pairs).

Verdicts are ``pending`` unless a **mechanical rule** decides the item from the
evidence alone (``verdict_source: "mechanical"``, rule id in
``mechanical_rule``; every rule is described in the file's
``mechanical_rules`` block). Nothing else is pre-judged. Re-running the
builder keeps every reviewer adjudication (``verdict_source: "reviewer"``) for
items whose id is still sampled.

Usage::

    python3 scripts/build_gold_sets.py --krr-dir <corpus snapshot> \\
        --corpus-commit <sha> [--out eval/gold_sets] [--layers crossReferences ...]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import subprocess
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

REPO_ROOT = Path(__file__).resolve().parents[2]
KRR_DIR = REPO_ROOT / "krr_outputs"
GOLD_DIR = REPO_ROOT / "eval" / "gold_sets"
SCHEMA_PATH = GOLD_DIR / "item.schema.json"

SCHEMA_VERSION = "1.0"
DEFAULT_SEED = 698
DEFAULT_SAMPLE_SIZE = 300
NEGATIVE_SHARE = 0.20
MIN_PER_STRATUM = 8
EVIDENCE_WINDOW = 260
EVIDENCE_MAX = 720

VERDICTS = ("pending", "correct", "incorrect", "partial")
VERDICT_SOURCES = ("mechanical", "reviewer")
ACT_KINDS = ("law", "state_regulation", "kov_regulation", "court_decision", "concept")

RT_AKT = "https://www.riigiteataja.ee/akt/"


@dataclass(frozen=True)
class LayerSpec:
    layer: str
    predicate: str
    title: str
    sample_size: int
    description: str


LAYERS: dict[str, LayerSpec] = {
    spec.layer: spec
    for spec in (
        LayerSpec(
            "crossReferences", "estleg:references", "Cross-references", DEFAULT_SAMPLE_SIZE,
            "One item per provision -> provision citation edge (estleg:references). "
            "Negatives: provisions with no outgoing reference edge.",
        ),
        LayerSpec(
            "sanctions", "estleg:hasSanction", "Sanctions", DEFAULT_SAMPLE_SIZE,
            "One item per parsed estleg:Sanction (type, max/min penalty) on its "
            "applicable provision. Negatives: sections with no sanction.",
        ),
        LayerSpec(
            "eurovoc", "dcterms:subject", "EuroVoc subjects (Estonian acts)", DEFAULT_SAMPLE_SIZE,
            "One item per act -> EuroVoc domain assignment from the keyword "
            "classifier overlay (EU acts carry official CELLAR EuroVoc and are "
            "out of scope). Negatives: acts the classifier left unclassified.",
        ),
        LayerSpec(
            "deontic", "estleg:normativeType", "Deontic modality (normative type)",
            DEFAULT_SAMPLE_SIZE,
            "One item per provision and its estleg:normativeType. Negatives: "
            "provisions with no normative type.",
        ),
        LayerSpec(
            "targetGroup", "estleg:targetGroup", "Target group", DEFAULT_SAMPLE_SIZE,
            "One item per provision -> target-group assertion. Negatives: "
            "provisions with no target group.",
        ),
        LayerSpec(
            "competence", "estleg:competentAuthority", "Competent authority",
            DEFAULT_SAMPLE_SIZE,
            "One item per provision -> competent-authority assertion. Negatives: "
            "provisions with no competent authority.",
        ),
        LayerSpec(
            "courtLinks", "estleg:interpretedBy", "Court links (Riigikohus -> provision)",
            DEFAULT_SAMPLE_SIZE,
            "One item per provision -> Riigikohus decision edge (estleg:interpretedBy). "
            "Negatives: a section of an act the decision interprets "
            "(estleg:interpretsLaw) with no edge to that decision (at section or "
            "subsection level); the decision is the item's probe. Half are "
            "sections the decision cites by abbreviation (cited_unlinked: likely "
            "recall misses), half sections it does not cite.",
        ),
        LayerSpec(
            "altLabelFolds", "skos:altLabel", "Orthographic altLabel folds (#699)", 50,
            "One item per #699 orthographic fold (a spelling folded into a "
            "canonical Concept as skos:altLabel; replaces the removed Levenshtein "
            "skos:closeMatch pairs). Negatives: Concept pairs within edit distance "
            "2 that were NOT folded (the probe is the other Concept's label).",
        ),
    )
}

# ---------------------------------------------------------------------------
# Mechanical rules — the only verdicts this builder pre-fills.
# ---------------------------------------------------------------------------

MECHANICAL_RULES: dict[str, dict[str, str]] = {
    "crossReferences": {
        "xref-same-act-citation": (
            "correct: the source text cites 'käesoleva seaduse/seadustiku/määruse "
            "§ N' (with 'lõige M' when the target is a subsection, and no lõige "
            "when the target is the section) and the target is § N [lg M] of the "
            "same act."
        ),
        "xref-own-section-subsection": (
            "correct: the source text cites 'käesoleva paragrahvi lõige M' and the "
            "target is lõige M of the source's own section."
        ),
        "xref-named-act-citation": (
            "correct: the source text names the target act by its title in the "
            "genitive ('<title>e/-u § N', with lõige M for a subsection target) "
            "and the target is that § [lg]."
        ),
        "repealed-negative": (
            "correct (negative): the provision text is only 'Kehtetu' (repealed); "
            "there is nothing to cite."
        ),
        "xref-no-citation-negative": (
            "correct (negative): the provision text contains no citation marker "
            "at all (no '§', 'paragrahv', 'lõige/lõike', 'punkt', 'seadus', "
            "'määrus', 'artikkel', 'direktiiv')."
        ),
    },
    "sanctions": {
        "sanction-amount-in-text": (
            "correct: the provision text contains the sanction's penal phrase for "
            "its type (rahatrahv / vangistus / arest / rahaline karistus / sunniraha) "
            "AND the parsed amount followed by its unit (trahviühikut / eurot / "
            "aasta / päeva / päevamäära), as digits or as Estonian number words "
            "('kolmsada trahviühikut', 'kolmeaastase vangistusega'); a type with no "
            "amount needs only the phrase. Texts carrying RT editorial notes "
            "('Veaparandus') get no mechanical verdict in any layer."
        ),
        "sanction-statutory-default": (
            "correct: the sanction is flagged estleg:isStatutoryDefault (its maximum "
            "is the Penal Code general-part default, not written in the provision) "
            "and the provision text contains the penal phrase for its type."
        ),
        "sanction-amount-absent": (
            "incorrect: the sanction is not a statutory default and carries a "
            "parsed amount that occurs nowhere in the provision text (neither as "
            "digits nor as an Estonian number word)."
        ),
        "repealed-negative": (
            "correct (negative): the section text is only 'Kehtetu' (repealed)."
        ),
        "sanction-no-penal-marker-negative": (
            "correct (negative): the section text contains no penal marker "
            "('karista', 'trahv', 'arest', 'vangistus', 'sunniraha', "
            "'konfiskeeri', 'sundlõpeta')."
        ),
    },
    "eurovoc": {
        "eurovoc-label-in-title": (
            "correct: every word of the EuroVoc domain's Estonian label (minus a "
            "final vowel) begins a word of the act title, e.g. 'konkurents' in "
            "'Konkurentsiseadus', 'kohalik omavalitsus' in 'Kohaliku omavalitsuse "
            "korralduse seadus'."
        ),
    },
    "deontic": {
        "deontic-single-marker": (
            "correct: the text carries modal markers of exactly one family and the "
            "system type is that family. Families: Prohibition ('on keelatud', 'ei "
            "tohi', 'ei või', 'keelatakse', 'ei ole lubatud'), Obligation ('on "
            "kohustatud', 'peab/peavad', 'kohustub', 'tuleb'), Permission "
            "('võib/võivad' not after 'ei', 'on lubatud'), Right ('on õigus', "
            "'õigus on'). Negated obligation or right ('ei pea', 'ei ole "
            "kohustatud', 'ei ole õigust') makes the text ambiguous: no verdict."
        ),
        "deontic-polarity-conflict": (
            "incorrect: the text carries markers of exactly one family and the "
            "system type has the opposite polarity (Obligation/Prohibition vs "
            "Permission/Right)."
        ),
        "repealed-assertion": (
            "incorrect: the system asserts a value on a provision whose text is "
            "only 'Kehtetu' (repealed)."
        ),
        "repealed-negative": (
            "correct (negative): the provision text is only 'Kehtetu' (repealed)."
        ),
    },
    "targetGroup": {
        "repealed-assertion": (
            "incorrect: the system asserts a target group on a provision whose "
            "text is only 'Kehtetu' (repealed)."
        ),
        "repealed-negative": (
            "correct (negative): the provision text is only 'Kehtetu' (repealed)."
        ),
    },
    "competence": {
        "competence-named-actor": (
            "correct: a sentence of the text contains the authority's name in the "
            "nominative AND an authority verb ('lahendab', 'kehtestab', 'väljastab', "
            "'otsustab', 'menetleb', 'teostab', 'korraldab', 'kinnitab', "
            "'registreerib', 'määrab', 'kontrollib', 'algatab', 'kooskõlastab', "
            "'on pädev', 'teeb ettekirjutuse', 'annab välja/loa/nõusoleku', 'peab "
            "registrit', 'tunnistab')."
        ),
        "repealed-assertion": (
            "incorrect: the system asserts an authority on a provision whose text "
            "is only 'Kehtetu' (repealed)."
        ),
        "repealed-negative": (
            "correct (negative): the provision text is only 'Kehtetu' (repealed)."
        ),
    },
    "courtLinks": {
        "court-cites-provision": (
            "correct: the decision text cites the provision as '<act "
            "abbreviation> § N' (and 'lg M' / 'lõige M' when the provision is a "
            "subsection), using an abbreviation the court-link resolver maps to "
            "the provision's act, in a decision dated 2013-01-01 or later (older "
            "decisions may cite a predecessor act under the same abbreviation, "
            "e.g. HKMS 1999 vs 2011, TLS 1992 vs TLS 2009, and stay pending)."
        ),
        "court-section-absent-negative": (
            "correct (negative): the probe decision's text never mentions '§ N' "
            "for the provision's section number, so it cannot cite the provision."
        ),
    },
    "altLabelFolds": {
        "fold-case-only": (
            "correct: the altLabel differs from the prefLabel only in letter case "
            "(identical after Unicode NFC + casefold)."
        ),
    },
}

REPEALED_RE = re.compile(r"^\W*(\(\d+[¹²³⁴⁵⁶⁷⁸⁹⁰]*\)\s*)?\[?\s*kehtetu\b", re.I)


def is_repealed_text(text: str) -> bool:
    text = (text or "").strip()
    return bool(text) and len(text) < 200 and bool(REPEALED_RE.match(text))


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------

def norm(text: str) -> str:
    return unicodedata.normalize("NFC", text or "").casefold()


def as_list(value: object) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def ref_ids(value: object) -> list[str]:
    out = []
    for item in as_list(value):
        if isinstance(item, dict) and isinstance(item.get("@id"), str):
            out.append(item["@id"])
        elif isinstance(item, str):
            out.append(item)
    return out


def literal(value: object) -> str | None:
    for item in as_list(value):
        if isinstance(item, dict):
            if "@value" in item:
                return str(item["@value"])
        elif isinstance(item, str):
            return item
    return None


def et_literal(value: object) -> str | None:
    items = as_list(value)
    for item in items:
        if isinstance(item, dict) and item.get("@language") == "et":
            return str(item.get("@value"))
    return literal(value)


def item_id(layer: str, node: str, predicate: str, system: list[str], probe: str | None) -> str:
    key = "|".join([layer, node, predicate, ",".join(sorted(system)), probe or ""])
    return f"{layer}-{hashlib.sha1(key.encode('utf-8')).hexdigest()[:12]}"


def evidence_window(text: str, start: int | None = None, end: int | None = None) -> str:
    """A quoted span of ``text`` around ``[start, end)`` (or its head)."""
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return ""
    if start is None:
        span = text[:EVIDENCE_MAX]
        return span + ("…" if len(text) > EVIDENCE_MAX else "")
    lo = max(0, start - EVIDENCE_WINDOW)
    hi = min(len(text), (end or start) + EVIDENCE_WINDOW)
    if lo > 0:
        sp = text.find(" ", lo)
        lo = sp + 1 if 0 <= sp < start else lo
    if hi < len(text):
        sp = text.rfind(" ", (end or start), hi)
        hi = sp if sp > 0 else hi
    span = text[lo:hi]
    return ("…" if lo > 0 else "") + span + ("…" if hi < len(text) else "")


def window_for(text: str, pattern: re.Pattern | None) -> str:
    """Evidence span: around the first ``pattern`` match in ``text`` if any."""
    flat = unicodedata.normalize("NFC", re.sub(r"\s+", " ", text or "").strip())
    if pattern is not None:
        m = re.compile(pattern.pattern, pattern.flags | re.I).search(flat)
        if m:
            return evidence_window(flat, m.start(), m.end())
    return evidence_window(flat)


def allocate(strata: dict[str, int], n: int, min_per: int = MIN_PER_STRATUM) -> dict[str, int]:
    """Stratified allocation of ``n`` draws over strata of the given sizes.

    Every stratum first gets ``min(size, min_per)``; the remainder is shared
    in proportion to size (largest remainder), never exceeding a stratum's
    size. Deterministic: ties break on the stratum key.
    """
    keys = sorted(k for k, size in strata.items() if size > 0)
    total = sum(strata[k] for k in keys)
    if total <= n:
        return {k: strata[k] for k in keys}
    if len(keys) > n:
        # More strata than budget: one per stratum, largest strata first.
        alloc = dict.fromkeys(keys, 0)
        for k in sorted(keys, key=lambda k: (-strata[k], k))[:n]:
            alloc[k] = 1
        return alloc
    floor = min(min_per, n // len(keys))
    alloc = {k: min(strata[k], floor) for k in keys}
    remaining = n - sum(alloc.values())
    while remaining > 0:
        spare = {k: strata[k] - alloc[k] for k in keys if strata[k] > alloc[k]}
        if not spare:
            break
        spare_total = sum(spare.values())
        quotas = {k: remaining * spare[k] / spare_total for k in spare}
        gave = 0
        for k in spare:
            add = min(int(quotas[k]), spare[k])
            alloc[k] += add
            gave += add
        left = remaining - gave
        for k in sorted(spare, key=lambda k: (-(quotas[k] - int(quotas[k])), k)):
            if left <= 0:
                break
            if alloc[k] < strata[k]:
                alloc[k] += 1
                left -= 1
                gave += 1
        remaining -= gave
        if gave == 0:
            break
    return alloc


def stratified_sample(
    candidates: list[dict], n: int, rng: random.Random, min_per: int = MIN_PER_STRATUM
) -> list[dict]:
    """Deterministic stratified sample of candidate dicts (key ``stratum``)."""
    by_stratum: dict[str, list[dict]] = defaultdict(list)
    for cand in candidates:
        by_stratum[cand["stratum"]].append(cand)
    alloc = allocate({k: len(v) for k, v in by_stratum.items()}, n, min_per)
    picked: list[dict] = []
    for key in sorted(alloc):
        pool = sorted(by_stratum[key], key=lambda c: c["sort_key"])
        picked.extend(rng.sample(pool, alloc[key]))
    return picked


def sup_digits(num: str) -> str:
    table = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")
    return num.translate(table)


def number_pattern(num: str) -> str:
    """Regex for a § / lõige number from an IRI segment (``46_2`` = 46²)."""
    base, _, sup = num.partition("_")
    if sup:
        return rf"{re.escape(base)}(?:{re.escape(sup_digits(sup))}|\^{re.escape(sup)})(?![\d⁰¹²³⁴⁵⁶⁷⁸⁹])"
    return rf"{re.escape(base)}(?![\d⁰¹²³⁴⁵⁶⁷⁸⁹^])"


PROVISION_IRI_RE = re.compile(
    r"^estleg:(?P<prefix>.+?)_Par_(?P<par>\d+(?:_\d+)?)(?:_Lg_(?P<lg>\d+(?:_\d+)?))?$"
)


def parse_provision_iri(iri: str) -> tuple[str, str, str | None] | None:
    m = PROVISION_IRI_RE.match(iri)
    if not m:
        return None
    return m.group("prefix"), m.group("par"), m.group("lg")


def rt_anchor(par: str, lg: str | None) -> str:
    def seg(num: str) -> str:
        base, _, sup = num.partition("_")
        return f"{base}b{sup}" if sup else base

    anchor = f"para{seg(par)}"
    if lg:
        anchor += f"lg{seg(lg)}"
    return anchor


def rt_url(source: str | None, provision_iri: str | None = None) -> str | None:
    """Human RT URL for an act (``…/akt/<id>``) with a § anchor when known."""
    if not source:
        return None
    m = re.search(r"/akt/([0-9]+|[A-Za-z%0-9]+)(?:\.xml)?$", source.split("#")[0])
    if not m:
        return None
    url = f"{RT_AKT}{m.group(1)}"
    parsed = parse_provision_iri(provision_iri) if provision_iri else None
    if parsed:
        url += "#" + rt_anchor(parsed[1], parsed[2])
    return url


def levenshtein_at_most(a: str, b: str, limit: int) -> int | None:
    """Edit distance if ``<= limit`` else ``None`` (banded DP)."""
    if abs(len(a) - len(b)) > limit:
        return None
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        best = cur[0]
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
            best = min(best, cur[j])
        if best > limit:
            return None
        prev = cur
    return prev[-1] if prev[-1] <= limit else None


# ---------------------------------------------------------------------------
# Corpus model
# ---------------------------------------------------------------------------

@dataclass
class Provision:
    iri: str
    kind: str
    text: str
    act: str | None
    is_section: bool
    preds: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class Act:
    iri: str
    kind: str
    title: str
    source: str | None
    file: str


LAYER_PREDICATES = (
    "estleg:references",
    "estleg:normativeType",
    "estleg:targetGroup",
    "estleg:competentAuthority",
    "estleg:interpretedBy",
    "estleg:hasSanction",
)


class Corpus:
    """In-memory view of the law + regulation peeps the layers need."""

    def __init__(self, krr_dir: Path):
        self.krr = Path(krr_dir)
        self.provisions: dict[str, Provision] = {}
        self.acts: dict[str, Act] = {}
        self.prefix_act: dict[str, str] = {}
        self.labels: dict[str, str] = {}
        self.uncitable = 0
        self._load_peeps()
        self._load_labels()
        self._drop_uncitable()

    def _peep_files(self) -> list[tuple[str, Path]]:
        out: list[tuple[str, Path]] = []
        for p in sorted(self.krr.glob("*_peep.json")):
            out.append(("law", p))
        for p in sorted((self.krr / "regulations" / "riik").glob("*_peep.json")):
            out.append(("state_regulation", p))
        for p in sorted((self.krr / "regulations" / "kov").rglob("*_peep.json")):
            out.append(("kov_regulation", p))
        return out

    def _load_peeps(self) -> None:
        for kind, path in self._peep_files():
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            graph = doc.get("@graph", []) if isinstance(doc, dict) else []
            file_act: str | None = None
            local: list[Provision] = []
            for node in graph:
                if not isinstance(node, dict) or not isinstance(node.get("@id"), str):
                    continue
                types = as_list(node.get("@type"))
                nid = node["@id"]
                if "estleg:Act" in types:
                    title = et_literal(node.get("dcterms:title")) or literal(node.get("dc:source")) or ""
                    source = ref_ids(node.get("dcterms:source"))
                    act = Act(nid, kind, title, source[0] if source else None,
                              str(path.relative_to(self.krr)))
                    self.acts[nid] = act
                    if file_act is None or (source and not self.acts[file_act].source):
                        file_act = nid
                    if nid.endswith("_Map"):
                        self.prefix_act.setdefault(nid[len("estleg:"):-len("_Map")], nid)
                    issuer = node.get("estleg:issuer")
                    for body in ref_ids(node.get("estleg:enactedBy")):
                        if isinstance(issuer, str) and issuer.strip():
                            self.labels.setdefault(body, issuer.strip())
                    continue
                text = node.get("estleg:legalText")
                if not isinstance(text, str):
                    continue
                preds = {p: ref_ids(node.get(p)) for p in LAYER_PREDICATES if node.get(p)}
                act_ref = ref_ids(node.get("estleg:partOfAct"))
                local.append(Provision(
                    nid, kind, text, act_ref[0] if act_ref else None,
                    "estleg:Subsection" not in types and "_Lg_" not in nid, preds,
                ))
            for prov in local:
                if prov.act is None or prov.act not in self.acts:
                    prov.act = file_act
                self.provisions[prov.iri] = prov

    def _load_labels(self) -> None:
        for path in sorted((self.krr / "institutions").glob("*.json")):
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            for node in doc.get("@graph", []):
                if isinstance(node, dict) and node.get("@id") and node.get("rdfs:label"):
                    self.labels[node["@id"]] = literal(node["rdfs:label"]) or ""
        scheme = self.krr / "eurovoc_concept_scheme.jsonld"
        if scheme.exists():
            for node in json.loads(scheme.read_text(encoding="utf-8")).get("@graph", []):
                for exact in ref_ids(node.get("skos:exactMatch")):
                    self.labels[exact] = et_literal(node.get("skos:prefLabel")) or ""
        cv = self.krr / "controlled_vocabulary.jsonld"
        if cv.exists():
            for node in json.loads(cv.read_text(encoding="utf-8")).get("@graph", []):
                if isinstance(node, dict) and str(node.get("@id", "")).startswith(
                    ("estleg:TargetGroup_", "estleg:NormType_")
                ):
                    self.labels[node["@id"]] = et_literal(node.get("rdfs:label")) or et_literal(
                        node.get("skos:prefLabel")
                    ) or node["@id"]

    def _drop_uncitable(self) -> None:
        """Provisions with no RT URL cannot be gold items (no citation)."""
        try:
            from estleg.estleg_common import KNOWN_ABBREVIATIONS
        except ImportError:  # pragma: no cover - estleg_common always ships
            KNOWN_ABBREVIATIONS = {}
        self.title_lyhend = {}
        for abbr, title in sorted(KNOWN_ABBREVIATIONS.items()):
            self.title_lyhend.setdefault(norm(title), abbr)
        keep = {}
        for iri, prov in self.provisions.items():
            if self.citation(prov):
                keep[iri] = prov
            else:
                self.uncitable += 1
        self.provisions = keep

    def act_of(self, prov: Provision) -> Act | None:
        return self.acts.get(prov.act) if prov.act else None

    def act_citation(self, act: Act | None, provision_iri: str | None = None) -> str | None:
        """RT URL: the act's ``/akt/<id>``, else RT's ``/akt/<lühend>`` alias."""
        if act is None:
            return None
        url = rt_url(act.source, provision_iri)
        if url is None and act.kind == "law":
            abbr = getattr(self, "title_lyhend", {}).get(norm(act.title))
            if abbr:
                url = rt_url(f"{RT_AKT}{quote(abbr)}", provision_iri)
        return url

    def citation(self, prov: Provision) -> str | None:
        return self.act_citation(self.act_of(prov), prov.iri)

    def label(self, iri: str) -> str:
        if iri in self.labels:
            return self.labels[iri]
        if iri.startswith("estleg:Issuer_"):
            return iri[len("estleg:Issuer_"):].replace("_", " ")
        return iri.rsplit("_", 1)[-1] if iri.startswith("estleg:TargetGroup_") else iri


# ---------------------------------------------------------------------------
# Item construction
# ---------------------------------------------------------------------------

def make_item(
    layer: str,
    *,
    node: str,
    system: list[str],
    system_labels: list[str] | None,
    citation: str | None,
    evidence: str,
    evidence_source: str,
    stratum: dict[str, str],
    negative: bool = False,
    probe: str | None = None,
    probe_label: str | None = None,
    related_citation: str | None = None,
    verdict: tuple[str, str] | None = None,
    context: dict | None = None,
) -> dict:
    predicate = LAYERS[layer].predicate
    item: dict = {
        "id": item_id(layer, node, predicate, system, probe),
        "node": node,
        "predicate": predicate,
        "system": sorted(system),
        "gold": [],
        "verdict": "pending",
        "verdict_source": None,
        "mechanical_rule": None,
        "negative": negative,
        "citation": citation or "",
        "evidence": {"text": evidence, "source": evidence_source},
        "stratum": stratum,
        "reviewer": None,
        "adjudicated_on": None,
        "note": None,
    }
    if system_labels:
        order = sorted(range(len(system)), key=lambda i: system[i])
        item["system_labels"] = [system_labels[i] for i in order]
    if probe:
        item["probe"] = probe
        if probe_label:
            item["probe_label"] = probe_label
    if related_citation:
        item["related_citation"] = related_citation
    if context:
        item["context"] = context
    if verdict:
        item["verdict"], item["mechanical_rule"] = verdict
        item["verdict_source"] = "mechanical"
        if item["verdict"] == "correct":
            item["gold"] = sorted(system)
    return item


def _sample_layer(
    positives: list[dict], negatives: list[dict], n: int, seed: int,
    neg_min_per: int = MIN_PER_STRATUM,
) -> tuple[list[dict], list[dict]]:
    n_neg = min(len(negatives), max(1, round(n * NEGATIVE_SHARE)))
    n_pos = min(len(positives), n - n_neg)
    if n_pos < n - n_neg:
        n_neg = min(len(negatives), n - n_pos)
    rng = random.Random(seed)
    pos = stratified_sample(positives, n_pos, rng)
    neg = stratified_sample(negatives, n_neg, rng, neg_min_per)
    return pos, neg


# --- cross references -------------------------------------------------------

XREF_MARKER_RE = re.compile(
    r"§|paragrahv|\blõi(ke|kes|ge|gete|getes)\b|\bpunkt|seadus|määrus|artik|direktiiv",
    re.I,
)
LG_FORMS = r"(?:lõi(?:ke|kes|ge|kega|kele|kest)|lg)"
# "§ 8", "§-s 631", "§-st 12" (case endings attach to the sign).
SECTION_SIGN = r"§(?:-[a-zõäöü]+)?\s*"


def _genitive_title(title: str) -> str | None:
    t = norm(title).strip()
    if t.endswith("seadustik"):
        return t + "u"
    if t.endswith("seadus"):
        return t + "e"
    return None


def xref_rule(src: Provision, target: str, corpus: Corpus) -> tuple[str | None, re.Pattern | None]:
    parsed = parse_provision_iri(target)
    text = norm(re.sub(r"\s+", " ", src.text))
    if not parsed or _editorial_noise(text):
        return None, None
    prefix, par, lg = parsed
    src_parsed = parse_provision_iri(src.iri)
    lg_tail = rf"\s*{LG_FORMS}\s*{number_pattern(lg)}" if lg else r"(?!\s*(?:lõi\w*|lg)\b|\s*\d)"
    if src_parsed and src_parsed[0] == prefix:
        pat = re.compile(
            rf"käesoleva (?:seaduse|seadustiku|määruse)\s+{SECTION_SIGN}{number_pattern(par)}{lg_tail}"
        )
        if pat.search(text):
            return "xref-same-act-citation", pat
        if lg and src_parsed[1] == par:
            own = re.compile(rf"käesoleva paragrahvi\s+{LG_FORMS}\s*{number_pattern(lg)}")
            if own.search(text):
                return "xref-own-section-subsection", own
        return None, re.compile(rf"§\s*{number_pattern(par)}")
    act_iri = corpus.prefix_act.get(prefix)
    act = corpus.acts.get(act_iri) if act_iri else None
    gen = _genitive_title(act.title) if act else None
    if gen:
        pat = re.compile(rf"{re.escape(gen)}\s+{SECTION_SIGN}{number_pattern(par)}{lg_tail}")
        if pat.search(text):
            return "xref-named-act-citation", pat
    return None, re.compile(rf"§\s*{number_pattern(par)}")


def build_cross_references(corpus: Corpus, seed: int, n: int) -> list[dict]:
    layer = "crossReferences"
    positives, negatives = [], []
    for prov in corpus.provisions.values():
        targets = prov.preds.get("estleg:references", [])
        if targets:
            for tgt in targets:
                same = (parse_provision_iri(prov.iri) or ("",))[0] == (parse_provision_iri(tgt) or ("?",))[0]
                positives.append({
                    "stratum": f"{prov.kind}|{'same_act' if same else 'other_act'}",
                    "sort_key": (prov.iri, tgt), "prov": prov, "target": tgt,
                    "same": same,
                })
        else:
            marked = bool(XREF_MARKER_RE.search(prov.text))
            negatives.append({
                "stratum": f"{prov.kind}|{'has_citation_marker' if marked else 'no_marker'}",
                "sort_key": (prov.iri,), "prov": prov,
            })
    pos, neg = _sample_layer(positives, negatives, n, seed)
    items = []
    for c in pos:
        prov, tgt = c["prov"], c["target"]
        rule, pat = xref_rule(prov, tgt, corpus)
        tprov = corpus.provisions.get(tgt)
        items.append(make_item(
            layer, node=prov.iri, system=[tgt], system_labels=None,
            citation=corpus.citation(prov),
            evidence=window_for(prov.text, pat),
            evidence_source=f"estleg:legalText of {prov.iri}",
            stratum={"act_kind": prov.kind, "value": "same_act" if c["same"] else "other_act"},
            related_citation=corpus.citation(tprov) if tprov else None,
            verdict=("correct", rule) if rule else None,
        ))
    for c in neg:
        prov = c["prov"]
        verdict = None
        if is_repealed_text(prov.text):
            verdict = ("correct", "repealed-negative")
        elif not XREF_MARKER_RE.search(prov.text):
            verdict = ("correct", "xref-no-citation-negative")
        items.append(make_item(
            layer, node=prov.iri, system=[], system_labels=None,
            citation=corpus.citation(prov),
            evidence=window_for(prov.text, re.compile(r"§|paragrahv", re.I)),
            evidence_source=f"estleg:legalText of {prov.iri}",
            stratum={"act_kind": prov.kind, "value": c["stratum"].split("|", 1)[1]},
            negative=True, verdict=verdict,
        ))
    return items


# --- sanctions --------------------------------------------------------------

SANCTION_PHRASES = {
    "fine": r"rahatrahv",
    "imprisonment": r"vangistus",
    "arrest": r"arest",
    "pecuniary_punishment": r"rahali\w* karistus",
    "coercive_payment": r"sunnira",
    "confiscation": r"konfiskeeri",
    "compulsory_dissolution": r"sundlõpeta",
}
SANCTION_UNITS = {
    "fine_units": r"trahviühiku",
    "monetary": r"euro",
    "years": r"aasta",
    "days": r"päeva",
    "daily_rates": r"päevamäära",
    "percent_of_turnover": r"(?:%|protsen)",
}
PENAL_MARKER_RE = re.compile(r"karista|trahv|arest|vangistus|sunniraha|konfiskeeri|sundlõpeta", re.I)


def _amount_pattern(amount: str) -> str:
    """Digits of ``amount`` allowing thousands spaces (``32 000``)."""
    digits = amount.split(".")[0] if amount.endswith(".0") or re.fullmatch(r"\d+\.0+", amount) else amount
    parts = []
    for i, ch in enumerate(digits):
        parts.append(re.escape(ch))
        left = len(digits) - i - 1
        if left and left % 3 == 0:
            parts.append(r"[\s  ]?")
    return r"(?<![\d.,])" + "".join(parts) + r"(?![\d])"


_ONES = {1: "üks", 2: "kaks", 3: "kolm", 4: "neli", 5: "viis", 6: "kuus", 7: "seitse",
         8: "kaheksa", 9: "üheksa"}


def estonian_number_words(n: int) -> set[str]:
    """Nominative Estonian spellings of ``n`` (1..9999), joined and spaced."""
    def below_100(k: int) -> list[str]:
        if k == 0:
            return [""]
        if k < 10:
            return [_ONES[k]]
        if k == 10:
            return ["kümme"]
        if k < 20:
            return [_ONES[k - 10] + "teist"]
        tens, ones = divmod(k, 10)
        head = _ONES[tens] + "kümmend"
        return [head] if not ones else [f"{head} {_ONES[ones]}", f"{head}{_ONES[ones]}"]

    def below_1000(k: int) -> list[str]:
        hundreds, rest = divmod(k, 100)
        if not hundreds:
            return below_100(rest)
        head = "sada" if hundreds == 1 else _ONES[hundreds] + "sada"
        return [head if not r else f"{head}{sep}{r}" for r in below_100(rest) for sep in (" ", "")]

    if n <= 0 or n >= 10000:
        return set()
    thousands, rest = divmod(n, 1000)
    if not thousands:
        return set(below_1000(rest))
    head = "tuhat" if thousands == 1 else _ONES[thousands] + " tuhat"
    return {head if not r else f"{head} {r}" for r in below_1000(rest)}


def _editorial_noise(text: str) -> bool:
    """RT editorial notes ('Veaparandus …') leaked into the provision text."""
    return "veaparandus" in norm(text)


# Imprisonment terms are usually written as words ("kuni kolmeaastase vangistusega").
YEAR_WORDS = {
    "1": "üheaastase", "2": "kaheaastase", "3": "kolmeaastase", "4": "neljaaastase",
    "5": "viieaastase", "6": "kuueaastase", "7": "seitsmeaastase", "8": "kaheksaaastase",
    "9": "üheksaaastase", "10": "kümneaastase", "12": "kaheteistaastase",
    "15": "viieteistaastase", "20": "kahekümneaastase",
}


def sanction_rule(sanction: dict, text: str) -> tuple[str | None, re.Pattern | None]:
    flat = norm(re.sub(r"\s+", " ", text))
    stype = sanction.get("estleg:sanctionType")
    phrase = SANCTION_PHRASES.get(stype)
    amount = literal(sanction.get("estleg:maxPenaltyAmount"))
    unit = sanction.get("estleg:maxPenaltyUnit")
    if not phrase:
        return None, None
    phrase_re = re.compile(phrase)
    if _editorial_noise(flat):
        return None, phrase_re
    if sanction.get("estleg:isStatutoryDefault") is True:
        # The maximum is the Penal Code general-part default (arrest 30 days,
        # pecuniary punishment 500 daily rates, fine 300 units), not written
        # in the provision: only the penal phrase can be checked.
        return ("sanction-statutory-default", phrase_re) if phrase_re.search(flat) else (None, phrase_re)
    if amount and unit in SANCTION_UNITS:
        amount = re.sub(r"\.0+$", "", amount)
        amt = _amount_pattern(amount)
        words = sorted(estonian_number_words(int(amount)), key=len, reverse=True) if amount.isdigit() else []
        alts = [amt] + [rf"(?<!\w){re.escape(w)}" for w in words]
        pat = re.compile(rf"(?:{'|'.join(alts)})\s*{SANCTION_UNITS[unit]}")
        if unit == "years" and amount in YEAR_WORDS:
            pat = re.compile(rf"{pat.pattern}|{amt}[\s-]*aastase|{YEAR_WORDS[amount]}")
        if phrase_re.search(flat) and pat.search(flat):
            return "sanction-amount-in-text", pat
        written = re.search(amt, flat) or any(re.search(rf"(?<!\w){re.escape(w)}(?!\w)", flat) for w in words)
        if not written and not (unit == "years" and amount in YEAR_WORDS and YEAR_WORDS[amount] in flat):
            return "sanction-amount-absent", phrase_re
        return None, pat
    if not amount and phrase_re.search(flat):
        return "sanction-amount-in-text", phrase_re
    return None, phrase_re


def build_sanctions(corpus: Corpus, seed: int, n: int) -> list[dict]:
    layer = "sanctions"
    positives = []
    sanctioned: set[str] = set()
    for path in sorted((corpus.krr / "sanctions").glob("*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for node in doc.get("@graph", []):
            if not isinstance(node, dict) or "estleg:Sanction" not in as_list(node.get("@type")):
                continue
            prov_ids = ref_ids(node.get("estleg:applicableProvision"))
            if not prov_ids:
                continue
            sanctioned.add(prov_ids[0])
            prov = corpus.provisions.get(prov_ids[0])
            if prov is None:
                continue
            positives.append({
                "stratum": f"{prov.kind}|{node.get('estleg:sanctionType')}",
                "sort_key": (node["@id"],), "prov": prov, "sanction": node,
            })
    for prov in corpus.provisions.values():
        sanctioned.update(prov.iri for _ in prov.preds.get("estleg:hasSanction", [])[:1])
    negatives = []
    for prov in corpus.provisions.values():
        if not prov.is_section or prov.iri in sanctioned:
            continue
        marked = bool(PENAL_MARKER_RE.search(prov.text))
        # Over-weight penal-marker sections: those are where recall is decided.
        negatives.append({
            "stratum": f"{prov.kind}|{'penal_marker' if marked else 'no_penal_marker'}",
            "sort_key": (prov.iri,), "prov": prov,
        })
    pos, neg = _sample_layer(positives, negatives, n, seed)
    items = []
    for c in pos:
        prov, s = c["prov"], c["sanction"]
        rule, pat = sanction_rule(s, prov.text)
        verdict = None
        if rule in ("sanction-amount-in-text", "sanction-statutory-default"):
            verdict = ("correct", rule)
        elif rule == "sanction-amount-absent":
            verdict = ("incorrect", rule)
        items.append(make_item(
            layer, node=prov.iri, system=[s["@id"]],
            system_labels=[literal(s.get("rdfs:label")) or s["@id"]],
            citation=corpus.citation(prov),
            evidence=window_for(prov.text, pat or PENAL_MARKER_RE),
            evidence_source=f"estleg:legalText of {prov.iri}",
            stratum={"act_kind": prov.kind, "value": str(s.get("estleg:sanctionType"))},
            context={
                "sanctionType": s.get("estleg:sanctionType"),
                "maxPenaltyAmount": literal(s.get("estleg:maxPenaltyAmount")),
                "maxPenaltyUnit": s.get("estleg:maxPenaltyUnit"),
                "minPenaltyAmount": literal(s.get("estleg:minPenaltyAmount")),
            },
            verdict=verdict,
        ))
    for c in neg:
        prov = c["prov"]
        verdict = None
        if is_repealed_text(prov.text):
            verdict = ("correct", "repealed-negative")
        elif not PENAL_MARKER_RE.search(prov.text):
            verdict = ("correct", "sanction-no-penal-marker-negative")
        items.append(make_item(
            layer, node=prov.iri, system=[], system_labels=None,
            citation=corpus.citation(prov),
            evidence=window_for(prov.text, PENAL_MARKER_RE),
            evidence_source=f"estleg:legalText of {prov.iri}",
            stratum={"act_kind": prov.kind, "value": c["stratum"].split("|", 1)[1]},
            negative=True, verdict=verdict,
        ))
    return items


# --- EuroVoc ----------------------------------------------------------------

def _label_stems(label: str) -> list[str]:
    stems = []
    for word in re.findall(r"\w+", norm(label)):
        if word in {"ja", "ning", "või"}:
            continue
        stems.append(word[:-1] if len(word) > 4 and word[-1] in "aeiouõäöü" else word)
    return stems


def eurovoc_rule(label: str, title: str) -> str | None:
    stems = _label_stems(label)
    words = re.findall(r"\w+", norm(title))
    if not stems or not words:
        return None
    if all(any(w.startswith(s) for w in words) for s in stems):
        return "eurovoc-label-in-title"
    return None


def build_eurovoc(corpus: Corpus, seed: int, n: int) -> list[dict]:
    layer = "eurovoc"
    overlay = json.loads((corpus.krr / "eurovoc" / "eurovoc_overlay.jsonld").read_text(encoding="utf-8"))
    subjects: dict[str, list[str]] = {}
    for node in overlay.get("@graph", []):
        if isinstance(node, dict) and node.get("dcterms:subject"):
            subjects[node["@id"]] = ref_ids(node["dcterms:subject"])
    positives, negatives = [], []
    for act in corpus.acts.values():
        if not act.iri.endswith("_Map") or not corpus.act_citation(act):
            continue
        subs = subjects.get(act.iri)
        if subs:
            for s in subs:
                positives.append({
                    "stratum": f"{act.kind}|{s.rsplit('/', 1)[-1]}",
                    "sort_key": (act.iri, s), "act": act, "subject": s,
                })
        else:
            negatives.append({"stratum": f"{act.kind}|unclassified", "sort_key": (act.iri,), "act": act})
    pos, neg = _sample_layer(positives, negatives, n, seed)
    # One pass for the act text heads of the sampled acts.
    wanted = {c["act"].iri for c in pos + neg}
    heads: dict[str, list[str]] = defaultdict(list)
    for p in corpus.provisions.values():
        if p.act in wanted and p.is_section and len(heads[p.act]) < 2:
            heads[p.act].append(p.text)
    items = []
    for c in pos:
        act, s = c["act"], c["subject"]
        label = corpus.labels.get(s, s)
        rule = eurovoc_rule(label, act.title)
        items.append(make_item(
            layer, node=act.iri, system=[s], system_labels=[label],
            citation=corpus.act_citation(act),
            evidence=f"Title: {act.title}. " + evidence_window(" ".join(heads.get(act.iri, []))),
            evidence_source=f"dcterms:title + first sections of {act.iri}",
            stratum={"act_kind": act.kind, "value": s.rsplit("/", 1)[-1]},
            verdict=("correct", rule) if rule else None,
        ))
    for c in neg:
        act = c["act"]
        items.append(make_item(
            layer, node=act.iri, system=[], system_labels=None,
            citation=corpus.act_citation(act),
            evidence=f"Title: {act.title}. " + evidence_window(" ".join(heads.get(act.iri, []))),
            evidence_source=f"dcterms:title + first sections of {act.iri}",
            stratum={"act_kind": act.kind, "value": "unclassified"},
            negative=True,
        ))
    return items


# --- deontic ----------------------------------------------------------------

DEONTIC_FAMILIES = {
    "estleg:NormType_Prohibition": re.compile(
        r"\bon keelatud\b|\bei tohi\b|\bei või\b|\bkeelatakse\b|\bei ole lubatud\b"
    ),
    "estleg:NormType_Obligation": re.compile(
        r"\bon kohustatud\b|(?<!ei )\bpea(?:b|vad)\b|\bkohustub\b|\btuleb\b"
    ),
    "estleg:NormType_Permission": re.compile(r"(?<!ei )\bvõi(?:b|vad)\b|(?<!ei )\bon lubatud\b"),
    "estleg:NormType_Right": re.compile(r"(?<!ei )\bon õigus\b|\bõigus on\b"),
}
DEONTIC_AMBIGUOUS = re.compile(r"\bei pea\b|\bei ole kohustatud\b|\bei ole õigust\b")
DUTY = {"estleg:NormType_Prohibition", "estleg:NormType_Obligation"}
LIBERTY = {"estleg:NormType_Permission", "estleg:NormType_Right"}


def deontic_families(text: str) -> set[str] | None:
    flat = norm(re.sub(r"\s+", " ", text))
    if DEONTIC_AMBIGUOUS.search(flat):
        return None
    return {fam for fam, pat in DEONTIC_FAMILIES.items() if pat.search(flat)}


def deontic_rule(values: list[str], text: str) -> tuple[str, str] | None:
    if is_repealed_text(text):
        return ("incorrect", "repealed-assertion")
    fams = deontic_families(text)
    if not fams or len(fams) != 1 or len(values) != 1 or _editorial_noise(text):
        return None
    fam, val = next(iter(fams)), values[0]
    if val == fam:
        return ("correct", "deontic-single-marker")
    if (fam in DUTY and val in LIBERTY) or (fam in LIBERTY and val in DUTY):
        return ("incorrect", "deontic-polarity-conflict")
    return None


DEONTIC_ANY = re.compile("|".join(p.pattern for p in DEONTIC_FAMILIES.values()))


def build_deontic(corpus: Corpus, seed: int, n: int) -> list[dict]:
    layer = "deontic"
    positives, negatives = [], []
    for prov in corpus.provisions.values():
        vals = prov.preds.get("estleg:normativeType", [])
        if vals:
            positives.append({"stratum": f"{prov.kind}|{vals[0]}", "sort_key": (prov.iri,), "prov": prov})
        else:
            negatives.append({"stratum": f"{prov.kind}|none", "sort_key": (prov.iri,), "prov": prov})
    pos, neg = _sample_layer(positives, negatives, n, seed)
    items = []
    for c in pos:
        prov = c["prov"]
        vals = prov.preds["estleg:normativeType"]
        items.append(make_item(
            layer, node=prov.iri, system=vals, system_labels=[corpus.label(v) for v in vals],
            citation=corpus.citation(prov),
            evidence=window_for(prov.text, DEONTIC_ANY),
            evidence_source=f"estleg:legalText of {prov.iri}",
            stratum={"act_kind": prov.kind, "value": vals[0]},
            verdict=deontic_rule(vals, prov.text),
        ))
    for c in neg:
        prov = c["prov"]
        items.append(make_item(
            layer, node=prov.iri, system=[], system_labels=None,
            citation=corpus.citation(prov),
            evidence=window_for(prov.text, DEONTIC_ANY),
            evidence_source=f"estleg:legalText of {prov.iri}",
            stratum={"act_kind": prov.kind, "value": "none"},
            negative=True,
            verdict=("correct", "repealed-negative") if is_repealed_text(prov.text) else None,
        ))
    return items


# --- target group -----------------------------------------------------------

def build_target_group(corpus: Corpus, seed: int, n: int) -> list[dict]:
    layer = "targetGroup"
    positives, negatives = [], []
    for prov in corpus.provisions.values():
        vals = prov.preds.get("estleg:targetGroup", [])
        for v in vals:
            positives.append({"stratum": f"{prov.kind}|{v}", "sort_key": (prov.iri, v), "prov": prov, "value": v})
        if not vals:
            negatives.append({"stratum": f"{prov.kind}|none", "sort_key": (prov.iri,), "prov": prov})
    pos, neg = _sample_layer(positives, negatives, n, seed)
    items = []
    for c in pos:
        prov, v = c["prov"], c["value"]
        items.append(make_item(
            layer, node=prov.iri, system=[v], system_labels=[corpus.label(v)],
            citation=corpus.citation(prov),
            evidence=window_for(prov.text, None),
            evidence_source=f"estleg:legalText of {prov.iri}",
            stratum={"act_kind": prov.kind, "value": v},
            context={"all_system_values": sorted(prov.preds["estleg:targetGroup"])},
            verdict=("incorrect", "repealed-assertion") if is_repealed_text(prov.text) else None,
        ))
    for c in neg:
        prov = c["prov"]
        items.append(make_item(
            layer, node=prov.iri, system=[], system_labels=None,
            citation=corpus.citation(prov),
            evidence=window_for(prov.text, None),
            evidence_source=f"estleg:legalText of {prov.iri}",
            stratum={"act_kind": prov.kind, "value": "none"},
            negative=True,
            verdict=("correct", "repealed-negative") if is_repealed_text(prov.text) else None,
        ))
    return items


# --- competence -------------------------------------------------------------

AUTHORITY_VERB_RE = re.compile(
    r"\b(lahendab|kehtestab|väljastab|otsustab|menetleb|teostab|korraldab|kinnitab|"
    r"registreerib|määrab|kontrollib|algatab|kooskõlastab|on pädev|teeb ettekirjutuse|"
    r"annab välja|annab loa|annab nõusoleku|peab registrit|tunnistab)\b"
)
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.;:])\s+")


def competence_rule(label: str, text: str) -> tuple[str | None, re.Pattern | None]:
    name = norm(label).strip()
    if len(name) < 4:
        return None, None
    name_re = re.compile(rf"(?<!\w){re.escape(name)}(?!\w)")
    flat = norm(re.sub(r"\s+", " ", text))
    if _editorial_noise(flat):
        return None, name_re
    for sentence in SENTENCE_SPLIT_RE.split(flat):
        if name_re.search(sentence) and AUTHORITY_VERB_RE.search(sentence):
            return "competence-named-actor", name_re
    return None, name_re


def build_competence(corpus: Corpus, seed: int, n: int) -> list[dict]:
    layer = "competence"
    positives, negatives = [], []
    for prov in corpus.provisions.values():
        vals = prov.preds.get("estleg:competentAuthority", [])
        for v in vals:
            fam = "issuer" if v.startswith("estleg:Issuer_") else "institution"
            positives.append({"stratum": f"{prov.kind}|{fam}", "sort_key": (prov.iri, v), "prov": prov, "value": v})
        if not vals:
            negatives.append({"stratum": f"{prov.kind}|none", "sort_key": (prov.iri,), "prov": prov})
    pos, neg = _sample_layer(positives, negatives, n, seed)
    items = []
    for c in pos:
        prov, v = c["prov"], c["value"]
        label = corpus.label(v)
        if is_repealed_text(prov.text):
            verdict, pat = ("incorrect", "repealed-assertion"), None
        else:
            rule, pat = competence_rule(label, prov.text)
            verdict = ("correct", rule) if rule else None
        items.append(make_item(
            layer, node=prov.iri, system=[v], system_labels=[label],
            citation=corpus.citation(prov),
            evidence=window_for(prov.text, pat or AUTHORITY_VERB_RE),
            evidence_source=f"estleg:legalText of {prov.iri}",
            stratum={"act_kind": prov.kind, "value": c["stratum"].split("|", 1)[1]},
            verdict=verdict,
        ))
    for c in neg:
        prov = c["prov"]
        items.append(make_item(
            layer, node=prov.iri, system=[], system_labels=None,
            citation=corpus.citation(prov),
            evidence=window_for(prov.text, AUTHORITY_VERB_RE),
            evidence_source=f"estleg:legalText of {prov.iri}",
            stratum={"act_kind": prov.kind, "value": "none"},
            negative=True,
            verdict=("correct", "repealed-negative") if is_repealed_text(prov.text) else None,
        ))
    return items


# --- court links ------------------------------------------------------------

def _court_abbreviations(krr: Path) -> dict[str, list[str]]:
    """Court-text abbreviation -> IRI prefixes (the court-link resolver's map)."""
    report = krr / "reports" / "court_provision_links_report.json"
    out: dict[str, list[str]] = {}
    if report.exists():
        mapping = json.loads(report.read_text(encoding="utf-8")).get("abbreviation_mapping", {})
        for abbr, row in sorted(mapping.items()):
            out[abbr] = sorted(row.get("iri_prefixes", []))
    return out


def section_iri(iri: str) -> str:
    return iri.split("_Lg_", 1)[0]


COURT_MECHANICAL_FROM = "2013-01-01"
COURT_MENTION_RE = re.compile(r"(?<!\w)([A-ZÕÄÖÜŠŽ][\wõäöüšž]{0,11})\s*§\s*(\d+)(?![\d⁰¹²³⁴⁵⁶⁷⁸⁹])")


def court_rule(prov_iri: str, decision_text: str, abbrevs: list[str]) -> tuple[str | None, re.Pattern | None]:
    parsed = parse_provision_iri(prov_iri)
    if not parsed:
        return None, None
    _prefix, par, lg = parsed
    flat = re.sub(r"\s+", " ", decision_text)
    sec = number_pattern(par)
    lg_part = rf"\s*(?:lg|lõi(?:ge|ke|kes))\.?\s*{number_pattern(lg)}" if lg else ""
    for abbr in sorted(abbrevs):
        pat = re.compile(rf"(?<!\w){re.escape(abbr)}\s*§\s*{sec}{lg_part}")
        if pat.search(flat):
            return "court-cites-provision", pat
    return None, re.compile(rf"§\s*{sec}")


def build_court_links(corpus: Corpus, seed: int, n: int) -> list[dict]:
    layer = "courtLinks"
    abbr_prefixes = _court_abbreviations(corpus.krr)
    prefix_abbrs: dict[str, list[str]] = defaultdict(list)
    for abbr, prefixes in abbr_prefixes.items():
        for prefix in prefixes:
            prefix_abbrs[prefix].append(abbr)
    rk_files = sorted((corpus.krr / "riigikohus").glob("riigikohus_*_peep.json"))
    decisions_meta: dict[str, dict] = {}
    for path in rk_files:
        doc = json.loads(path.read_text(encoding="utf-8"))
        for node in doc.get("@graph", []):
            if not (isinstance(node, dict) and "estleg:CourtDecision" in as_list(node.get("@type"))):
                continue
            text = re.sub(r"\s+", " ", literal(node.get("estleg:legalText")) or "")
            cited = set()
            for abbr, num in COURT_MENTION_RE.findall(text):
                for prefix in abbr_prefixes.get(abbr, []):
                    cited.add(f"estleg:{prefix}_Par_{num}")
            decisions_meta[node["@id"]] = {
                # interpretsLaw points at provisions; the negatives pool is
                # the acts those provisions belong to.
                "laws": sorted({
                    corpus.provisions[p].act for p in ref_ids(node.get("estleg:interpretsLaw"))
                    if p in corpus.provisions and corpus.provisions[p].act
                }),
                "cited_sections": cited,
                "has_text": bool(text),
                "case": literal(node.get("estleg:caseNumber")) or node["@id"],
                "date": literal(node.get("estleg:decisionDate")) or "",
                "link": literal(node.get("estleg:decisionLink")),
                "caseType": (ref_ids(node.get("estleg:caseType")) or ["unknown"])[0],
            }
    positives = []
    linked_sections: set[tuple[str, str]] = set()
    by_act: dict[str, list[Provision]] = defaultdict(list)
    for prov in corpus.provisions.values():
        if prov.kind == "law" and prov.act and prov.is_section:
            by_act[prov.act].append(prov)
        for d in prov.preds.get("estleg:interpretedBy", []):
            linked_sections.add((section_iri(prov.iri), d))
            if d in decisions_meta:
                positives.append({
                    "stratum": f"{prov.kind}|{decisions_meta[d]['caseType']}",
                    "sort_key": (prov.iri, d), "prov": prov, "decision": d,
                })
    negatives = []
    for d, meta in sorted(decisions_meta.items()):
        if not meta["has_text"]:
            continue
        for law in meta["laws"]:
            unlinked = sorted(
                p.iri for p in by_act.get(law, []) if (p.iri, d) not in linked_sections
            )
            if not unlinked:
                continue
            # Two candidates per (decision, act): a section the decision cites
            # by abbreviation but that carries no edge (a recall miss if the
            # citation holds), and a section it does not cite.
            cited = [iri for iri in unlinked if iri in meta["cited_sections"]]
            uncited = [iri for iri in unlinked if iri not in meta["cited_sections"]]
            for kind, pool in (("cited_unlinked", cited), ("uncited_section", uncited)):
                if pool:
                    pick = pool[int(hashlib.sha1(f"{d}|{law}|{kind}".encode()).hexdigest(), 16) % len(pool)]
                    negatives.append({
                        "stratum": f"law|{kind}", "sort_key": (d, law, kind),
                        "prov": corpus.provisions[pick], "decision": d, "kind": kind,
                    })
    n_neg = max(1, round(n * NEGATIVE_SHARE))
    pos, neg = _sample_layer(positives, negatives, n, seed, neg_min_per=n_neg // 2)
    wanted = {c["decision"] for c in pos + neg}
    texts: dict[str, str] = {}
    for path in rk_files:
        doc = json.loads(path.read_text(encoding="utf-8"))
        for node in doc.get("@graph", []):
            if isinstance(node, dict) and node.get("@id") in wanted:
                texts[node["@id"]] = literal(node.get("estleg:legalText")) or literal(
                    node.get("estleg:summary")
                ) or ""
    items = []
    for c in pos:
        prov, d = c["prov"], c["decision"]
        prefix = (parse_provision_iri(prov.iri) or ("",))[0]
        rule, pat = court_rule(prov.iri, texts.get(d, ""), prefix_abbrs.get(prefix, []))
        meta = decisions_meta[d]
        if meta["date"] < COURT_MECHANICAL_FROM:
            rule = None  # may cite a predecessor act under the same abbreviation
        items.append(make_item(
            layer, node=prov.iri, system=[d], system_labels=[f"RK {meta['case']}"],
            citation=meta["link"], related_citation=corpus.citation(prov),
            evidence=window_for(texts.get(d, ""), pat),
            evidence_source=f"estleg:legalText of {d}",
            stratum={"act_kind": prov.kind, "value": meta["caseType"]},
            context={"decisionDate": meta["date"]},
            verdict=("correct", rule) if rule else None,
        ))
    for c in neg:
        prov, d = c["prov"], c["decision"]
        meta = decisions_meta[d]
        parsed = parse_provision_iri(prov.iri)
        prefix = parsed[0] if parsed else ""
        verdict, pat = None, None
        rule, cite_pat = court_rule(prov.iri, texts.get(d, ""), prefix_abbrs.get(prefix, []))
        if rule:
            # Cited but unlinked: a likely recall miss, but the abbreviation may
            # name a predecessor act, so the reviewer decides.
            pat = cite_pat
        elif parsed:
            pat = re.compile(rf"§\s*{number_pattern(parsed[1])}")
            if texts.get(d) and not pat.search(re.sub(r"\s+", " ", texts[d])):
                verdict = ("correct", "court-section-absent-negative")
        items.append(make_item(
            layer, node=prov.iri, system=[], system_labels=None,
            citation=meta["link"], related_citation=corpus.citation(prov),
            evidence=window_for(texts.get(d, ""), pat),
            evidence_source=f"estleg:legalText of {d}",
            stratum={"act_kind": "law", "value": c["kind"]},
            negative=True, probe=d, probe_label=f"RK {meta['case']}", verdict=verdict,
            context={"decisionDate": meta["date"]},
        ))
    return items


# --- #699 altLabel folds ----------------------------------------------------

def _orthographic_key(label: str) -> str:
    from estleg.extract_legal_concepts import orthographic_key

    return orthographic_key(label)


def build_altlabel_folds(corpus: Corpus, seed: int, n: int) -> list[dict]:
    layer = "altLabelFolds"
    graph = json.loads((corpus.krr / "concepts" / "concepts_combined.jsonld").read_text(encoding="utf-8"))["@graph"]
    by_id = {node["@id"]: node for node in graph if isinstance(node, dict) and "@id" in node}
    canon = [n_ for n_ in graph if isinstance(n_, dict) and "estleg:Concept" in as_list(n_.get("@type"))]

    def pref(node: dict) -> str:
        return et_literal(node.get("skos:prefLabel")) or ""

    def def_source(node: dict, surface: str) -> tuple[str | None, str, str | None]:
        """(RT citation, quoted definition, provision IRI) for a spelling."""
        best = None
        for d in ref_ids(node.get("estleg:hasDefinitionNode")):
            dn = by_id.get(d)
            if not dn:
                continue
            if best is None or norm(pref(dn)) == norm(surface) and pref(dn) == surface:
                best = dn
                if pref(dn) == surface:
                    break
        if best is None:
            return None, "", None
        prov_iri = (ref_ids(best.get("estleg:definedIn")) or [None])[0]
        prov = corpus.provisions.get(prov_iri) if prov_iri else None
        cite = corpus.citation(prov) if prov else None
        quote = f"{pref(best)} — {et_literal(best.get('skos:definition')) or ''}"
        return cite, quote, prov_iri

    folds_sep, folds_case = [], []
    for node in sorted(canon, key=lambda x: x["@id"]):
        p = pref(node)
        for alt in sorted({lit for lit in (et_literal(a) if isinstance(a, dict) else a
                                           for a in as_list(node.get("skos:altLabel"))) if lit}):
            if alt == p or _orthographic_key(alt) != _orthographic_key(p):
                continue
            if not def_source(node, alt)[0]:
                continue
            cand = {"node": node, "alt": alt, "sort_key": (node["@id"], alt)}
            if norm(alt) == norm(p):
                folds_case.append({**cand, "stratum": "case_only"})
            else:
                folds_sep.append({**cand, "stratum": "separator"})
    # Negatives: unfolded Concept pairs within edit distance 2 (the old
    # closeMatch population), deterministic order.
    labels = sorted(((norm(pref(x)), x["@id"]) for x in canon if pref(x)), key=lambda t: (len(t[0]), t))
    near = []
    for i, (la, ia) in enumerate(labels):
        for lb, ib in labels[i + 1:]:
            if len(lb) - len(la) > 2:
                break
            if len(la) < 4 or _orthographic_key(la) == _orthographic_key(lb):
                continue
            if levenshtein_at_most(la, lb, 2) is not None and def_source(by_id[ia], pref(by_id[ia]))[0]:
                near.append({"stratum": "edit_distance_le_2", "sort_key": (ia, ib), "a": ia, "b": ib})
    n_neg = max(1, round(n * 0.24))
    n_sep = min(len(folds_sep), n - n_neg)
    n_case = min(len(folds_case), n - n_neg - n_sep)
    rng = random.Random(seed)
    pos = sorted(folds_sep, key=lambda c: c["sort_key"])[:n_sep]
    pos += rng.sample(sorted(folds_case, key=lambda c: c["sort_key"]), n_case)
    neg = rng.sample(sorted(near, key=lambda c: c["sort_key"]), min(len(near), n - len(pos)))
    items = []
    for c in pos:
        node, alt = c["node"], c["alt"]
        cite, quote_alt, _ = def_source(node, alt)
        _, quote_pref, _ = def_source(node, pref(node))
        verdict = ("correct", "fold-case-only") if c["stratum"] == "case_only" else None
        items.append(make_item(
            layer, node=node["@id"], system=[alt], system_labels=None,
            citation=cite,
            evidence=f"prefLabel '{pref(node)}': {quote_pref} || altLabel '{alt}': {quote_alt}"[:EVIDENCE_MAX],
            evidence_source=f"skos:definition of the definition nodes of {node['@id']}",
            stratum={"act_kind": "concept", "value": c["stratum"]},
            context={"prefLabel": pref(node)},
            verdict=verdict,
        ))
    for c in neg:
        a, b = by_id[c["a"]], by_id[c["b"]]
        cite, quote_a, _ = def_source(a, pref(a))
        _, quote_b, _ = def_source(b, pref(b))
        items.append(make_item(
            layer, node=a["@id"], system=[], system_labels=None,
            citation=cite,
            evidence=f"'{pref(a)}': {quote_a} || '{pref(b)}': {quote_b}"[:EVIDENCE_MAX],
            evidence_source=f"skos:definition of {a['@id']} and {b['@id']}",
            stratum={"act_kind": "concept", "value": "edit_distance_le_2"},
            negative=True, probe=b["@id"], probe_label=pref(b),
            context={"prefLabel": pref(a)},
        ))
    return items


BUILDERS = {
    "crossReferences": build_cross_references,
    "sanctions": build_sanctions,
    "eurovoc": build_eurovoc,
    "deontic": build_deontic,
    "targetGroup": build_target_group,
    "competence": build_competence,
    "courtLinks": build_court_links,
    "altLabelFolds": build_altlabel_folds,
}


# ---------------------------------------------------------------------------
# Gold-set documents
# ---------------------------------------------------------------------------

REVIEWER_FIELDS = ("gold", "verdict", "verdict_source", "mechanical_rule", "reviewer", "adjudicated_on", "note")


def merge_reviewer_verdicts(new_items: list[dict], old_doc: dict | None) -> int:
    """Carry reviewer adjudications from ``old_doc`` onto re-sampled items."""
    if not old_doc:
        return 0
    old = {it["id"]: it for it in old_doc.get("items", []) if it.get("verdict_source") == "reviewer"}
    kept = 0
    for item in new_items:
        prior = old.get(item["id"])
        # IDs do not include the legal evidence or sanction amounts. A stable
        # assertion IRI can survive an amended provision or corrected amount;
        # a verdict on the previous evidence must not adjudicate the new one.
        if prior and all(prior.get(key) == item.get(key) for key in
                         ("evidence", "context", "citation", "related_citation")):
            for key in REVIEWER_FIELDS:
                item[key] = prior.get(key)
            kept += 1
    return kept


def gold_document(
    layer: str, items: list[dict], seed: int, corpus_commit: str, population: dict,
    excluded_without_citation: int = 0,
) -> dict:
    spec = LAYERS[layer]
    items = sorted(items, key=lambda it: (it["negative"], it["stratum"]["act_kind"], it["stratum"]["value"], it["id"]))
    return {
        "$schema": "./item.schema.json",
        "schema_version": SCHEMA_VERSION,
        "layer": layer,
        "title": spec.title,
        "property": spec.predicate,
        "description": spec.description,
        "sampling": {
            "seed": seed,
            "corpus_commit": corpus_commit,
            "generated_by": "scripts/build_gold_sets.py",
            "sample_size": len(items),
            "negatives": sum(1 for it in items if it["negative"]),
            "negative_share_target": NEGATIVE_SHARE if layer != "altLabelFolds" else 0.24,
            "min_per_stratum": MIN_PER_STRATUM,
            "population": population,
            "excluded_without_citation": excluded_without_citation,
        },
        "mechanical_rules": MECHANICAL_RULES.get(layer, {}),
        "items": items,
    }


def _population(items: list[dict]) -> dict:
    out: dict[str, int] = defaultdict(int)
    for it in items:
        out[f"{it['stratum']['act_kind']}|{it['stratum']['value']}"] += 1
    return dict(sorted(out.items()))


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def load_schema(path: Path = SCHEMA_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_gold_document(doc: dict, schema: dict | None = None) -> list[str]:
    """Schema + semantic problems of one gold-set document (empty = valid)."""
    import jsonschema

    schema = schema if schema is not None else load_schema()
    errors = [
        f"{'/'.join(str(p) for p in err.absolute_path) or '<root>'}: {err.message}"
        for err in sorted(jsonschema.Draft202012Validator(schema).iter_errors(doc), key=str)
    ]
    if errors:
        return errors
    seen: set[str] = set()
    rules = set(doc.get("mechanical_rules", {}))
    for idx, item in enumerate(doc["items"]):
        where = f"items/{idx} ({item['id']})"
        if item["id"] in seen:
            errors.append(f"{where}: duplicate item id")
        seen.add(item["id"])
        if item["negative"] and item["system"]:
            errors.append(f"{where}: negative item must have empty system values")
        if not item["negative"] and not item["system"]:
            errors.append(f"{where}: non-negative item needs system values")
        if item["verdict"] == "pending":
            if item["verdict_source"] is not None:
                errors.append(f"{where}: pending item must not carry a verdict_source")
        elif item["verdict_source"] is None:
            errors.append(f"{where}: adjudicated item needs verdict_source")
        if item["verdict_source"] == "mechanical" and item.get("mechanical_rule") not in rules:
            errors.append(f"{where}: unknown mechanical_rule {item.get('mechanical_rule')!r}")
        if item["verdict_source"] == "reviewer" and not item.get("reviewer"):
            errors.append(f"{where}: reviewer verdict must name the reviewer")
        if item["verdict"] == "partial" and not item["gold"]:
            errors.append(f"{where}: a partial verdict must record the gold values")
        gold, system = set(item["gold"]), set(item["system"])
        if item["negative"] and item["verdict"] == "correct" and gold:
            errors.append(f"{where}: a confirmed negative must have empty gold")
        if not item["negative"] and item["verdict"] == "correct" and gold and gold != system:
            errors.append(f"{where}: 'correct' gold differs from system (use 'partial'/'incorrect')")
        if item["verdict"] in ("incorrect", "partial") and gold == system and gold:
            errors.append(f"{where}: '{item['verdict']}' gold equals the system values")
    return errors


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _git_head(repo: Path) -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def build(krr_dir: Path, out_dir: Path, corpus_commit: str, layers: list[str], seed: int) -> dict[str, dict]:
    corpus = Corpus(krr_dir)
    schema = load_schema(out_dir / "item.schema.json") if (out_dir / "item.schema.json").exists() else load_schema()
    summary = {}
    for layer in layers:
        spec = LAYERS[layer]
        items = BUILDERS[layer](corpus, seed, spec.sample_size)
        path = out_dir / f"{layer}.json"
        old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
        kept = merge_reviewer_verdicts(items, old)
        doc = gold_document(layer, items, seed, corpus_commit, _population(items),
                            0 if layer == "altLabelFolds" else corpus.uncitable)
        problems = validate_gold_document(doc, schema)
        if problems:
            raise SystemExit(f"{layer}: invalid gold set:\n  " + "\n  ".join(problems[:20]))
        path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        summary[layer] = {
            "items": len(items),
            "negatives": sum(1 for it in items if it["negative"]),
            "prefilled": sum(1 for it in items if it["verdict_source"] == "mechanical"),
            "reviewer_kept": kept,
        }
        print(f"{layer}: {summary[layer]}")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the #698 legal gold sets")
    parser.add_argument("--krr-dir", type=Path, default=KRR_DIR,
                        help="Corpus root to sample from (a krr_outputs/ tree or snapshot).")
    parser.add_argument("--out", type=Path, default=GOLD_DIR)
    parser.add_argument("--corpus-commit", default=None,
                        help="Commit the corpus snapshot was taken at (default: git HEAD).")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--layers", nargs="*", default=list(LAYERS), choices=list(LAYERS))
    args = parser.parse_args(argv)
    commit = args.corpus_commit or _git_head(REPO_ROOT)
    build(args.krr_dir, args.out, commit, args.layers, args.seed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Re-identification check for personal names in court decisions (#720).

The GDPR record (``docs/DATA_PROTECTION.md``) has to answer one question
before the names policy can be decided: do the person names stored in
``krr_outputs/riigikohus/`` match what the official RIK feed publishes, or
does the corpus identify people the court's own anonymisation hides? This
module has two halves.

``--offline`` (safe, no network)
    Scans every ``estleg:CourtDecision`` in the Riigikohus peeps, finds
    person-name-like token runs in the fields that carry names, separates
    them from organisation / institution names heuristically, and attributes
    each to a role (judge, professional representative or official, or
    unattributed). The unattributed bucket is the one that may hold parties,
    victims or witnesses. It writes
    ``krr_outputs/reports/reidentification_check_report.json`` holding ONLY
    aggregate counts, year histograms, field names and SHA-256 fingerprints of
    the sorted candidate sets. No name is ever written or logged.

``--live`` (maintainer action; refused unless ``ESTLEG_LIVE_CANARY=1``)
    Draws a deterministic, year-stratified sample of decisions that hold at
    least one unattributed candidate, re-fetches each decision's CURRENT text
    from the official RIK detail page (cache bypassed), and checks every
    stored candidate against it. A decision counts as *re-identified* when
    the stored text names a person the official text does not show in that
    form. Output: ``krr_outputs/reports/reidentification_live_report.json``,
    again aggregates only (plus the case numbers of re-identified decisions,
    which are court document identifiers, so the erasure SOP can act on them).

The candidate detector is a recall-oriented heuristic, not named-entity
recognition: its precision has not been measured, and the report says so.
Treat the counts as an upper bound on name-bearing decisions.

Usage::

    python3 -m estleg.check_reidentification --offline
    ESTLEG_LIVE_CANARY=1 python3 -m estleg.check_reidentification --live --sample 100
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from estleg.estleg_common import (
    BUILD_EVALUATION_DATE,
    KRR_DIR,
    REPO_ROOT,
    assert_allowed_http_url,
    report_file,
    save_json,
    sha256_hex,
)

HEURISTIC_VERSION = "2"
COURT_DECISION_TYPE = "estleg:CourtDecision"
OFFLINE_REPORT_NAME = "reidentification_check_report.json"
LIVE_REPORT_NAME = "reidentification_live_report.json"
LIVE_ENV = "ESTLEG_LIVE_CANARY"

# Fields of a Riigikohus decision node that can carry a person's name.
# ``rdfs:label`` is "RK <case number>" in this corpus; it is scanned anyway so
# the report proves it rather than assumes it.
TEXT_FIELDS: tuple[str, ...] = ("rdfs:label", "estleg:summary", "estleg:legalText")
JUDGE_FIELD = "estleg:judge"
SCANNED_FIELDS: tuple[str, ...] = (*TEXT_FIELDS, JUDGE_FIELD)

# The official RIK detail page (same endpoint and extractor the corpus was
# scraped with — see ``generate_court_decisions._detail_url``).
LIVE_DETAIL_URL = "https://rikos.rik.ee/?asjaNr="
LIVE_MIN_INTERVAL_SECONDS = 2.0
# The detail page is the source of ``estleg:legalText`` only; ``estleg:summary``
# comes from the search-table abstract, which the detail page does not repeat,
# so comparing summary candidates against it would report false "absent".
LIVE_FIELD = "estleg:legalText"
LIVE_DEFAULT_SAMPLE = 100

_UPPER = "A-ZÕÄÖÜŠŽ"
_LOWER = "a-zõäöüšžéèáàíóúýçñ'’"
_NAME_WORD = rf"[{_UPPER}][{_LOWER}]+(?:-[{_UPPER}][{_LOWER}]+)?"
_NAME_WORD_RE = re.compile(rf"(?<![\w{_UPPER}{_LOWER}]){_NAME_WORD}(?![\w{_LOWER}])")
# Court anonymisation writes parties as initials ("A. B.", "A.B.", "A-B. C.").
INITIALS_RE = re.compile(rf"(?<!\w)[{_UPPER}]\.\s?(?:-?[{_UPPER}]\.\s?)?[{_UPPER}]\.(?!\w)")
# Uppercase legal-form markers next to a run make it an organisation.
_LEGAL_FORM_RE = re.compile(r"(?<!\w)(?:OÜ|AS|MTÜ|SA|TÜ|UÜ|KÜ|FIE|OY|AB|GmbH|Ltd|LLC|SIA|UAB)(?!\w)")

# Organisation / place / institution stems (substring match on a casefolded
# token). Kept short and generic on purpose; institution labels from
# ``krr_outputs/institutions/`` and ``data/institution_aliases.json`` extend it.
ORG_STEMS: tuple[str, ...] = (
    "kohus", "kohtu", "prokuratuur", "prokuratuuri", "amet", "inspektsioon",
    "ministeerium", "vabariik", "vabariigi", "valitsus", "volikogu", "riigikogu",
    "kantselei", "kolleegium", "komisjon", "nõukogu", "teenistus", "agentuur",
    "keskus", "ülikool", "kool", "haigla", "pank", "panga", "liit", "koda",
    "büroo", "fond", "selts", "ühistu", "register", "registri", "vangla",
    "linn", "vald", "valla", "maavalitsus", "osakond", "politsei", "prefektuur",
    "kassa", "ühing", "partei", "erakond", "kirik", "kogudus", "sihtasutus",
    "aktsiaselts", "osaühing", "firma", "grupp", "group", "holding", "invest",
    "kinnisvara", "ehitus", "kaubandus", "seadus", "seadustik", "määrus",
    "konventsioon", "direktiiv", "euroopa", "eesti", "tallinn", "tartu",
    "pärnu", "narva", "harju", "viru", "saare", "lääne", "pärnumaa", "tartumaa",
    "venemaa", "läti", "leedu", "soome", "rootsi", "saksamaa", "riigi",
    "õiguskantsler", "notar", "advokatuur", "omavalitsus", "kriminaal",
    "tsiviil", "haldus", "põhiseadus", "maksu", "tolli",
    # company / trade-name / place / vehicle tokens seen in the corpus
    "bank", "corporation", "enterprise", "company", "trading", "service",
    "ltd", "oy", "inc", "farma", "pharma", "post", "maja", "küla", "gümnaasium",
    "beheer", "vara", "ühendriik", "piirkon", "nimekiri", "spisok", "roche",
    "toyota", "mercedes", "ford", "lamborghini", "volkswagen", "audi", "opel",
    "volvo", "nissan", "honda", "mazda", "škoda", "skoda", "renault", "peugeot",
    "cruiser", "katla", "sadam", "jaam", "tänav", "puiestee", "maantee",
)

# Capitalised words that open sentences or label header slots in RIK
# decisions. A run whose FIRST token is one of these is not a name.
STOP_FIRST_TOKENS: frozenset[str] = frozenset(
    {
        "kohtuasi", "kohtukoosseis", "eesistuja", "liikmed", "vaidlustatud",
        "kaebuse", "teised", "asja", "menetluse", "kohtuasja", "määruse",
        "otsuse", "resolutsioon", "asjaolud", "kohtumäärus", "kohtuotsus",
        "riigikohus", "riigikohtu", "kassaator", "kaebaja", "vastustaja",
        "hageja", "kostja", "süüdistatav", "kannatanu", "taotleja", "esindaja",
        "kaitsja", "prokurör", "abiprokurör", "kohtunik", "kohtunikud",
        "menetlusosalised", "menetlusosaline", "tsiviilasi", "kriminaalasi",
        "haldusasi", "väärteoasi", "kolleegium", "üldkogu", "erikogu",
        "saalis", "jaanuar", "veebruar", "märts", "aprill", "mai", "juuni",
        "juuli", "august", "september", "oktoober", "november", "detsember",
        "see", "selle", "seega", "samuti", "kuna", "kuigi", "sest", "kui",
        "ning", "või", "ja", "et", "mis", "kes", "kus", "nii", "siis",
        "seetõttu", "lisaks", "näiteks", "vastavalt", "arvestades", "eeltoodust",
        "eeltoodud", "eelnevalt", "tulenevalt", "kohtul", "kohus", "kolleegiumi",
        "euroopa", "eesti", "rahvusvahelise", "põhiseaduse", "seaduse",
    }
)

# Role words in the ~60 characters before a run. A run preceded by one is a
# professional / official appearing in that capacity, not a private party.
JUDGE_ROLE_RE = re.compile(r"(?:eesistuja|liikmed|liige|kohtunik\w*|kohtukoosseis)", re.IGNORECASE)
PROFESSIONAL_ROLE_RE = re.compile(
    r"(?:vandeadvokaa\w*|advokaa\w*|kaitsja\w*|prokurör\w*|abiprokurör\w*|"
    r"ringkonnaprokurör\w*|riigiprokurör\w*|notar\w*|kohtutäitur\w*|"
    r"pankrotihaldur\w*|õigusnõunik\w*|jurist\w*|esindaja\w*|õiguskantsler\w*|"
    r"menetleja\w*|uurija\w*|ametnik\w*|tõlk\w*|ekspert\w*|kohtuistungi\s+sekretär\w*)",
    re.IGNORECASE,
)
ROLE_CONTEXT_CHARS = 60

ROLE_JUDGE = "judge"
ROLE_PROFESSIONAL = "professional_or_official"
ROLE_UNATTRIBUTED = "unattributed"
ROLES: tuple[str, ...] = (ROLE_JUDGE, ROLE_PROFESSIONAL, ROLE_UNATTRIBUTED)

# Minimum lowercase occurrences across the corpus for a word to count as a
# common noun / verb rather than a given name (first-token filter).
COMMON_WORD_MIN_COUNT = 3


class LiveCheckRefused(RuntimeError):
    """``--live`` was requested without the canary opt-in or allow-listed host."""


# ---------------------------------------------------------------------------
# Corpus iteration
# ---------------------------------------------------------------------------


def _types(node: dict) -> list[str]:
    raw = node.get("@type") or []
    return [raw] if isinstance(raw, str) else [t for t in raw if isinstance(t, str)]


def _literal_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        inner = value.get("@value")
        return inner if isinstance(inner, str) else ""
    if isinstance(value, list):
        return " ".join(_literal_text(item) for item in value)
    return ""


def _judge_names(node: dict) -> list[str]:
    raw = node.get(JUDGE_FIELD)
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    return [_literal_text(item).strip() for item in raw if _literal_text(item).strip()]


def iter_decisions(rk_dir: Path) -> Iterator[dict]:
    """Yield every ``estleg:CourtDecision`` node from ``rk_dir``'s peeps."""
    for path in sorted(rk_dir.glob("*_peep.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        for node in doc.get("@graph") or []:
            if isinstance(node, dict) and COURT_DECISION_TYPE in _types(node):
                yield node


def decision_year(node: dict) -> str:
    text = _literal_text(node.get("estleg:decisionDate"))
    return text[:4] if len(text) >= 4 and text[:4].isdigit() else "unknown"


# ---------------------------------------------------------------------------
# Candidate detection
# ---------------------------------------------------------------------------


def load_institution_stems(repo_root: Path = REPO_ROOT) -> frozenset[str]:
    """Casefolded word stems from institution labels and alias slugs."""
    stems: set[str] = set()
    aliases = repo_root / "data" / "institution_aliases.json"
    if aliases.is_file():
        doc = json.loads(aliases.read_text(encoding="utf-8"))
        for slug, entry in (doc.get("aliases") or {}).items():
            stems.update(part for part in slug.split("_") if len(part) >= 5)
            if isinstance(entry, dict) and isinstance(entry.get("canonical"), str):
                stems.update(p for p in entry["canonical"].split("_") if len(p) >= 5)
    inst_dir = repo_root / "krr_outputs" / "institutions"
    if inst_dir.is_dir():
        for path in sorted(inst_dir.glob("institution_*.json")):
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            for node in doc.get("@graph") or []:
                label = _literal_text(node.get("rdfs:label")) if isinstance(node, dict) else ""
                for word in re.findall(rf"[{_UPPER}{_LOWER}]+", label):
                    if len(word) >= 5:
                        stems.add(word.casefold()[:-1])
    return frozenset(stems)


def build_common_words(texts: Iterable[str]) -> frozenset[str]:
    """Words seen lowercase at least ``COMMON_WORD_MIN_COUNT`` times."""
    counts: Counter[str] = Counter(re.findall(rf"(?<!\w)[{_LOWER}]{{3,}}(?!\w)", " ".join(texts)))
    return frozenset(word for word, n in counts.items() if n >= COMMON_WORD_MIN_COUNT)


@dataclass(frozen=True)
class Candidate:
    text: str
    role: str


@dataclass
class Detector:
    institution_stems: frozenset[str] = frozenset()
    common_words: frozenset[str] = frozenset()
    # Stemmed full names of every judge listed on any decision's panel, so a
    # judge named in a dissent or a cross-reference is not "unattributed".
    known_judges: frozenset[str] = frozenset()

    def _is_org_token(self, token: str) -> bool:
        folded = token.casefold()
        if any(stem in folded for stem in ORG_STEMS):
            return True
        return any(folded.startswith(stem) for stem in self.institution_stems)

    def _name_runs(self, text: str) -> Iterator[list[re.Match[str]]]:
        """Yield runs (>= 2) of capitalised words joined by exactly one space."""
        run: list[re.Match[str]] = []
        for match in _NAME_WORD_RE.finditer(text):
            if run and text[run[-1].end():match.start()] != " ":
                if len(run) >= 2:
                    yield run
                run = []
            run.append(match)
        if len(run) >= 2:
            yield run

    def _is_filler(self, token: str) -> bool:
        folded = token.casefold()
        return folded in STOP_FIRST_TOKENS or folded in self.common_words

    def _segments(self, run: list[re.Match[str]]) -> Iterator[list[re.Match[str]]]:
        """Split a run at organisation and header/common words.

        RIK headers run slots together without punctuation ("Eesistuja <name>
        Kohtuasi <name> ... <name> Harju Maakohus"), so a separator token ends
        one segment and starts the next instead of disqualifying the run.
        """
        segment: list[re.Match[str]] = []
        for match in run:
            token = match.group(0)
            if self._is_filler(token) or self._is_org_token(token):
                if segment:
                    yield segment
                segment = []
                continue
            segment.append(match)
        if segment:
            yield segment

    def is_person_like(self, text: str, start: int, end: int) -> bool:
        """False when a legal-form marker (OÜ, AS, ...) sits next to the segment."""
        window = text[max(0, start - 6):start] + text[end:end + 6]
        return not _LEGAL_FORM_RE.search(window)

    def candidates(self, text: str, judges: Iterable[str] = ()) -> list[Candidate]:
        judge_stems = {_stem(word) for name in judges for word in name.split()}
        found: list[Candidate] = []
        for run in (seg for raw in self._name_runs(text) for seg in self._segments(raw)):
            if not 2 <= len(run) <= 3:
                continue
            start, end = run[0].start(), run[-1].end()
            tokens = [m.group(0) for m in run]
            if not self.is_person_like(text, start, end):
                continue
            context = text[max(0, start - ROLE_CONTEXT_CHARS):start]
            key = " ".join(_stem(t) for t in tokens)
            if judge_stems and all(_stem(t) in judge_stems for t in tokens):
                role = ROLE_JUDGE
            elif key in self.known_judges:
                role = ROLE_JUDGE
            elif JUDGE_ROLE_RE.search(context[-25:]):
                role = ROLE_JUDGE
            elif PROFESSIONAL_ROLE_RE.search(context):
                role = ROLE_PROFESSIONAL
            else:
                role = ROLE_UNATTRIBUTED
            found.append(Candidate(" ".join(tokens), role))
        return found


def _stem(word: str) -> str:
    folded = word.casefold()
    return folded[:-2] if len(folded) > 5 else folded


# ---------------------------------------------------------------------------
# Offline scan
# ---------------------------------------------------------------------------


@dataclass
class DecisionScan:
    year: str
    case_number: str
    by_field: dict[str, list[Candidate]] = field(default_factory=dict)
    judge_count: int = 0
    initials: int = 0

    def all_candidates(self) -> list[Candidate]:
        return [c for cands in self.by_field.values() for c in cands]

    def unattributed(self, field_name: str | None = None) -> list[Candidate]:
        pool = self.all_candidates() if field_name is None else self.by_field.get(field_name, [])
        return [c for c in pool if c.role == ROLE_UNATTRIBUTED]


def scan_decision(node: dict, detector: Detector) -> DecisionScan:
    judges = _judge_names(node)
    scan = DecisionScan(
        year=decision_year(node),
        case_number=_literal_text(node.get("estleg:caseNumber")).strip(),
        judge_count=len(judges),
    )
    for name in TEXT_FIELDS:
        text = _literal_text(node.get(name))
        if not text:
            continue
        scan.by_field[name] = detector.candidates(text, judges)
        scan.initials += len(INITIALS_RE.findall(text))
    return scan


def _distribution(values: list[int]) -> dict[str, float | int]:
    if not values:
        return {"n": 0, "median": 0, "p90": 0, "max": 0}
    ordered = sorted(values)
    return {
        "n": len(ordered),
        "median": statistics.median(ordered),
        "p90": ordered[min(len(ordered) - 1, int(len(ordered) * 0.9))],
        "max": ordered[-1],
    }


def _fingerprint(names: Iterable[str]) -> str:
    """SHA-256 of the sorted, casefolded, de-duplicated candidate set."""
    return sha256_hex("\n".join(sorted({n.casefold() for n in names})))


def build_offline_report(scans: list[DecisionScan]) -> dict:
    """Aggregate-only report. Never include a candidate string here."""
    per_field: dict[str, dict] = {}
    for name in TEXT_FIELDS:
        role_mentions: Counter[str] = Counter()
        decisions_with: Counter[str] = Counter()
        present = 0
        for scan in scans:
            cands = scan.by_field.get(name)
            if cands is None:
                continue
            present += 1
            roles = Counter(c.role for c in cands)
            role_mentions.update(roles)
            for role in roles:
                decisions_with[role] += 1
        per_field[name] = {
            "decisions_with_field": present,
            "candidate_mentions_by_role": {r: role_mentions.get(r, 0) for r in ROLES},
            "decisions_with_candidate_by_role": {r: decisions_with.get(r, 0) for r in ROLES},
        }
    per_field[JUDGE_FIELD] = {
        "decisions_with_field": sum(1 for s in scans if s.judge_count),
        "values": sum(s.judge_count for s in scans),
        "note": "Panel members extracted from the legalText header; judges act in an official capacity.",
    }

    any_person = [s for s in scans if s.all_candidates()]
    unattributed = [s for s in scans if s.unattributed()]
    years = sorted({s.year for s in scans})
    histogram = {
        year: {
            "decisions": sum(1 for s in scans if s.year == year),
            "with_any_person_candidate": sum(1 for s in any_person if s.year == year),
            "with_unattributed_candidate": sum(1 for s in unattributed if s.year == year),
            "with_initials_marker": sum(1 for s in scans if s.year == year and s.initials),
        }
        for year in years
    }
    all_names = [c.text for s in scans for c in s.all_candidates()]
    unattributed_names = [c.text for s in scans for c in s.unattributed()]
    return {
        "generated": BUILD_EVALUATION_DATE,
        "issue": "#720",
        "corpus": "krr_outputs/riigikohus/*_peep.json",
        "heuristic_version": HEURISTIC_VERSION,
        "method": (
            "Runs of 2-3 capitalised words joined by single spaces in the scanned "
            "text fields; dropped when the first word is a header/stop word or occurs "
            "lowercase in the corpus, when any word matches an organisation/place/"
            "institution stem, or when a legal-form marker (OÜ, AS, MTÜ, ...) is "
            "adjacent. Survivors are attributed to a role from the preceding context "
            "(judge panel, professional representative/official, else unattributed). "
            "Recall-oriented heuristic; precision has not been measured, so counts "
            "are an upper bound. Inflected forms of one name count separately."
        ),
        "fields_scanned": list(SCANNED_FIELDS),
        "contains_names": False,
        "decisions_scanned": len(scans),
        "decisions_with_any_person_candidate": len(any_person),
        "decisions_with_unattributed_candidate": len(unattributed),
        "decisions_with_initials_marker": sum(1 for s in scans if s.initials),
        "initials_markers_total": sum(s.initials for s in scans),
        "candidates_per_decision": {
            "all_roles_over_decisions_with_candidates": _distribution(
                [len(s.all_candidates()) for s in any_person]
            ),
            "unattributed_over_decisions_with_unattributed": _distribution(
                [len(s.unattributed()) for s in unattributed]
            ),
        },
        "unique_candidates": {
            "all_roles": len({n.casefold() for n in all_names}),
            "unattributed": len({n.casefold() for n in unattributed_names}),
        },
        "fields": per_field,
        "year_histogram": histogram,
        "fingerprints": {
            "algorithm": "sha256 over newline-joined sorted casefolded unique candidates",
            "all_roles": _fingerprint(all_names),
            "unattributed": _fingerprint(unattributed_names),
        },
        "live_check": {
            "status": "not_run",
            "owner": "maintainer",
            "how": "ESTLEG_LIVE_CANARY=1 python3 -m estleg.check_reidentification --live",
            "report": f"krr_outputs/reports/{LIVE_REPORT_NAME}",
        },
    }


def scan_corpus(rk_dir: Path, *, repo_root: Path = REPO_ROOT) -> list[DecisionScan]:
    nodes = list(iter_decisions(rk_dir))
    common = build_common_words(
        _literal_text(n.get(f)) for n in nodes for f in TEXT_FIELDS
    )
    known_judges = frozenset(
        " ".join(_stem(word) for word in name.split()) for n in nodes for name in _judge_names(n)
    )
    detector = Detector(load_institution_stems(repo_root), common, known_judges)
    return [scan_decision(node, detector) for node in nodes]


def run_offline(rk_dir: Path, out_path: Path, *, repo_root: Path = REPO_ROOT) -> dict:
    scans = scan_corpus(rk_dir, repo_root=repo_root)
    report = build_offline_report(scans)
    save_json(out_path, report)
    return report


# ---------------------------------------------------------------------------
# Live check (maintainer action — never run in CI or tests)
# ---------------------------------------------------------------------------


def assert_live_allowed(env: dict[str, str] | None = None) -> None:
    """Refuse unless the operator opted in AND the RIK host is allow-listed."""
    env = os.environ if env is None else env
    if env.get(LIVE_ENV) != "1":
        raise LiveCheckRefused(
            f"--live talks to the official RIK court-information system; set "
            f"{LIVE_ENV}=1 to confirm this is an intentional maintainer run"
        )
    try:
        assert_allowed_http_url(LIVE_DETAIL_URL)
    except ValueError as exc:
        raise LiveCheckRefused(
            f"{exc}; add the host to ALLOWED_HTTP_HOSTS in estleg_common before a live run"
        ) from exc


def stratified_sample(scans: list[DecisionScan], size: int) -> list[DecisionScan]:
    """Deterministic year-stratified sample of decisions with unattributed legalText candidates.

    Quotas are proportional to each year's share (at least one per year that
    has eligible decisions); within a year, decisions are ordered by the
    SHA-256 of their case number, so the sample is reproducible without an RNG.
    """
    eligible = [s for s in scans if s.unattributed(LIVE_FIELD) and s.case_number]
    if not eligible or size <= 0:
        return []
    by_year: dict[str, list[DecisionScan]] = {}
    for scan in eligible:
        by_year.setdefault(scan.year, []).append(scan)
    total = len(eligible)
    quotas = {y: max(1, round(size * len(v) / total)) for y, v in by_year.items()}
    while sum(quotas.values()) > size and any(q > 1 for q in quotas.values()):
        largest = max((y for y in quotas if quotas[y] > 1), key=lambda y: (quotas[y], y))
        quotas[largest] -= 1
    sample: list[DecisionScan] = []
    for year in sorted(by_year):
        ordered = sorted(by_year[year], key=lambda s: sha256_hex(s.case_number))
        sample.extend(ordered[: quotas[year]])
    return sample


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def _initials_of(name: str) -> list[str]:
    letters = [word[0] for word in name.split()]
    return [". ".join(letters) + ".", ".".join(letters) + "."]


def classify_candidate(name: str, official_text: str) -> str:
    """``present`` / ``initialised`` / ``absent`` against the official text."""
    official = _normalise(official_text)
    if _normalise(name) in official:
        return "present"
    if any(form.casefold() in official for form in _initials_of(name)):
        return "initialised"
    return "absent"


def live_verdict(scan: DecisionScan, official_text: str | None) -> tuple[str, Counter[str]]:
    statuses: Counter[str] = Counter()
    if not official_text:
        return "fetch_failed", statuses
    for cand in {c.text for c in scan.unattributed(LIVE_FIELD)}:
        statuses[classify_candidate(cand, official_text)] += 1
    verdict = "consistent" if set(statuses) <= {"present"} else "re_identified"
    return verdict, statuses


def build_live_report(results: list[tuple[DecisionScan, str, Counter[str]]], requested: int) -> dict:
    verdicts: Counter[str] = Counter(v for _s, v, _c in results)
    statuses: Counter[str] = Counter()
    by_year: dict[str, Counter[str]] = {}
    for scan, verdict, counts in results:
        statuses.update(counts)
        by_year.setdefault(scan.year, Counter())[verdict] += 1
    return {
        "generated": BUILD_EVALUATION_DATE,
        "issue": "#720",
        "endpoint": LIVE_DETAIL_URL + "<case_number>",
        "heuristic_version": HEURISTIC_VERSION,
        "definition": (
            "A sampled decision is re_identified when at least one unattributed "
            "person candidate in the stored estleg:legalText is not present verbatim in the "
            "current official RIK text (status initialised = the official text "
            "shows only initials; absent = neither form). consistent = every "
            "candidate present verbatim. fetch_failed = no official text."
        ),
        "contains_names": False,
        "sample_requested": requested,
        "sample_checked": len(results),
        "sampled_case_numbers_sha256": sha256_hex(
            "\n".join(sorted(s.case_number for s, _v, _c in results))
        ),
        "verdicts": {k: verdicts.get(k, 0) for k in ("consistent", "re_identified", "fetch_failed")},
        "candidate_status": {k: statuses.get(k, 0) for k in ("present", "initialised", "absent")},
        "by_year": {y: dict(sorted(c.items())) for y, c in sorted(by_year.items())},
        "re_identified_case_numbers": sorted(
            s.case_number for s, v, _c in results if v == "re_identified"
        ),
    }


def _default_fetch(case_number: str) -> str | None:
    from estleg.generate_court_decisions import fetch_decision_text

    return fetch_decision_text(case_number, use_cache=False)


def run_live(
    rk_dir: Path,
    out_path: Path,
    *,
    sample_size: int = LIVE_DEFAULT_SAMPLE,
    fetch: Callable[[str], str | None] = _default_fetch,
    env: dict[str, str] | None = None,
    min_interval: float = LIVE_MIN_INTERVAL_SECONDS,
    repo_root: Path = REPO_ROOT,
) -> dict:
    assert_live_allowed(env)
    scans = scan_corpus(rk_dir, repo_root=repo_root)
    sample = stratified_sample(scans, sample_size)
    results: list[tuple[DecisionScan, str, Counter[str]]] = []
    last = 0.0
    for scan in sample:
        wait = min_interval - (time.monotonic() - last)
        if wait > 0:
            time.sleep(wait)
        last = time.monotonic()
        verdict, counts = live_verdict(scan, fetch(scan.case_number))
        results.append((scan, verdict, counts))
    report = build_live_report(results, sample_size)
    save_json(out_path, report)
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--offline", action="store_true", help="Aggregate scan of the committed corpus.")
    mode.add_argument("--live", action="store_true", help="Compare a sample against the official RIK feed.")
    parser.add_argument("--rk-dir", type=Path, default=KRR_DIR / "riigikohus")
    parser.add_argument("--out", type=Path, default=None, help="Report path (default: krr_outputs/reports/...).")
    parser.add_argument("--sample", type=int, default=LIVE_DEFAULT_SAMPLE, help="Live sample size.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.live:
        out = args.out or report_file(KRR_DIR, LIVE_REPORT_NAME)
        try:
            report = run_live(args.rk_dir, out, sample_size=args.sample)
        except LiveCheckRefused as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 2
        print(f"live: checked={report['sample_checked']} verdicts={report['verdicts']}")
        print(f"wrote {out}")
        return 0
    out = args.out or report_file(KRR_DIR, OFFLINE_REPORT_NAME)
    report = run_offline(args.rk_dir, out)
    dist = report["candidates_per_decision"]
    print(
        f"decisions_scanned={report['decisions_scanned']} "
        f"with_any_person_candidate={report['decisions_with_any_person_candidate']} "
        f"with_unattributed_candidate={report['decisions_with_unattributed_candidate']} "
        f"median_all={dist['all_roles_over_decisions_with_candidates']['median']} "
        f"median_unattributed={dist['unattributed_over_decisions_with_unattributed']['median']}"
    )
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

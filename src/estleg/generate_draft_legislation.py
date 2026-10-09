#!/usr/bin/env python3
"""
Fetch draft legislation (eelnõud) from Estonia's EIS (Eelnõude infosüsteem)
and generate JSON-LD ontology files.

Data sources:
  - Public consultation RSS: eelnoud.valitsus.ee/main/mount/rss/home/publicConsult.rss
  - Review/coordination RSS: eelnoud.valitsus.ee/main/mount/rss/home/review.rss
  - Submission RSS: eelnoud.valitsus.ee/main/mount/rss/home/submission.rss

Generates:
  - krr_outputs/eelnoud/eelnoud_<feed>_peep.json  (drafts, grouped by the EIS
    feed they were FIRST observed in, plus their eli-dl:ProcessStep nodes)
  - krr_outputs/eelnoud/EELNOUD_INDEX.json  (registry of all drafts)

Lifecycle (#717): every feed observation is an ``eli-dl:ProcessStep``
(``estleg:Draft_<key>_Step_<n>``) attached with ``estleg:hasProcessStep``;
``estleg:legislativePhase`` is DERIVED from the latest step (Riigikogu steps
are appended by ``generate_riigikogu_proceedings.py``). A live run merges new
observations into the steps already committed, so the history accumulates
across runs. ``--lifecycle-from-peeps`` re-derives the lifecycle offline from
the committed peeps (the EIS feeds answer HTTP 403 since the Sätla
switch-over on 2026-10-01). See docs/DRAFT_LIFECYCLE.md.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from functools import partial
from pathlib import Path

from estleg.estleg_common import (
    CONTEXT,
    allowed_get,
    jsonld_text,
    mint_act_iri,
    parse_xml,
    save_json,
)
from estleg.estleg_common import (
    sanitize_id as _shared_sanitize_id,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
KRR_DIR = REPO_ROOT / "krr_outputs"
EELNOUD_DIR = KRR_DIR / "eelnoud"
EELNOUD_DIR.mkdir(parents=True, exist_ok=True)

NS = "https://w3id.org/estleg/"
# ELI-DL v3 namespace. Never merged into the SHARED CONTEXT (that would
# unused-prefix every other peep, #443); the drafts peeps add it themselves
# because their eli-dl:ProcessStep nodes use it (#717).
ELI_DL_NS = "http://data.europa.eu/eli/eli-draft-legislation-ontology#"
ELI_DL_SCHEMA_CONTEXT = {**CONTEXT, "eli-dl": ELI_DL_NS}
DRAFTS_CONTEXT = ELI_DL_SCHEMA_CONTEXT

# Shared title-prefix length for title-only draft @id generation
# (generate_draft_node fallback) and main() dedup keys. Using different
# slices (40 vs 60) made two title-only drafts collide in one path and
# not the other (issue #296).
TITLE_KEY_LEN = 60

# EIS RSS feed URLs
RSS_FEEDS = {
    "publicConsultation": {
        "url": "https://eelnoud.valitsus.ee/main/mount/rss/home/publicConsult.rss",
        "phase": "PublicConsultation",
        "label_et": "Avalik konsultatsioon",
        "label_en": "Public Consultation",
    },
    "review": {
        "url": "https://eelnoud.valitsus.ee/main/mount/rss/home/review.rss",
        "phase": "Review",
        "label_et": "Kooskõlastamine",
        "label_en": "Inter-ministerial Review",
    },
    "submission": {
        "url": "https://eelnoud.valitsus.ee/main/mount/rss/home/submission.rss",
        "phase": "Submission",
        "label_et": "Esitatud Vabariigi Valitsusele",
        "label_en": "Submitted to Government",
    },
}

# Ministry code mapping (EIS number prefix -> initiator name literal).
# EIS files a draft under the ministry that OWNS it at snapshot time, so a
# 2012 KLIM draft is a Keskkonnaministeerium draft (same legal person,
# renamed 2023-07-01). REM is the Regionaal- ja Põllumajandusministeerium
# (not the "Regionaalminister" office, #717); JUM is the pre-2025
# Justiitsministeerium code.
MINISTRY_CODES = {
    # Literal kept as before (#382 contract); the dated IRI is initiatedBy.
    "JDM": "Justiitsministeerium",
    "JUM": "Justiitsministeerium",
    "HTM": "Haridus- ja Teadusministeerium",
    "SIM": "Siseministeerium",
    "VÄM": "Välisministeerium",
    "REM": "Regionaal- ja Põllumajandusministeerium",
    "MEM": "Maaeluministeerium",
    "PÕM": "Põllumajandusministeerium",
    "RAM": "Rahandusministeerium",
    "SOM": "Sotsiaalministeerium",
    "MKM": "Majandus- ja Kommunikatsiooniministeerium",
    "KLIM": "Kliimaministeerium",
    "KKM": "Keskkonnaministeerium",
    "KAM": "Kaitseministeerium",
    "KUM": "Kultuuriministeerium",
    "RK": "Riigikantselei",
    "RIIGIKOGU": "Riigikogu",
}

# EIS code -> institution slug (krr_outputs/institutions/institution_<slug>.json)
# of the CURRENT owner. ``initiated_by_slug`` walks the same-legal-person
# rename chain in data/institution_identity.json back to the name valid on
# the draft date (KLIM 2012 -> keskkonnaministeerium). RK (Riigikantselei)
# has no institution node yet, so it gets no estleg:initiatedBy.
MINISTRY_INSTITUTION_SLUGS = {
    "JDM": "justiits_ja_digiministeerium",
    "JUM": "justiitsministeerium",
    "HTM": "haridus_ja_teadusministeerium",
    "SIM": "siseministeerium",
    "VÄM": "valisministeerium",
    "REM": "regionaal_ja_pollumajandusministeerium",
    "MEM": "maaeluministeerium",
    "PÕM": "pollumajandusministeerium",
    "RAM": "rahandusministeerium",
    "SOM": "sotsiaalministeerium",
    "MKM": "majandus_ja_kommunikatsiooniministeerium",
    "KLIM": "kliimaministeerium",
    "KKM": "keskkonnaministeerium",
    "KAM": "kaitseministeerium",
    "KUM": "kultuuriministeerium",
    "RIIGIKOGU": "riigikogu",
}
INSTITUTION_IDENTITY_PATH = REPO_ROOT / "data" / "institution_identity.json"
INSTITUTIONS_DIR = KRR_DIR / "institutions"

sanitize_id = partial(_shared_sanitize_id, max_len=80, replace_dash=True)


def parse_eis_number(title: str) -> tuple[str, str, str]:
    """
    Parse EIS number and date from RSS title.
    Format: "Title text - MINISTRY/YY-NNNN (DD.MM.YYYY)"
    Returns: (eis_number, ministry_code, date_str)
    """
    match = re.search(r"-\s*([A-ZÄÖÜÕa-z]+/\d{2}-\d{4})\s*\((\d{2}\.\d{2}\.\d{4})\)\s*$", title)
    if match:
        eis_number = match.group(1)
        date_str = match.group(2)
        ministry_code = eis_number.split("/")[0]
        return eis_number, ministry_code, date_str
    return "", "", ""


def parse_draft_title(title: str) -> str:
    """Extract the actual title without EIS number and date suffix."""
    cleaned = re.sub(r"\s*-\s*[A-ZÄÖÜÕa-z]+/\d{2}-\d{4}\s*\(\d{2}\.\d{2}\.\d{4}\)\s*$", "", title)
    return cleaned.strip()


def extract_uuid(link: str) -> str:
    """Extract UUID from EIS link."""
    match = re.search(r"/docList/([0-9a-f-]{36})", link)
    return match.group(1) if match else ""


def classify_draft_type(title: str) -> tuple[str, str]:
    """
    Classify the draft type from its title.
    Returns: (type_id, type_label)
    """
    title_lower = title.lower()

    # A väljatöötamiskavatsus (pre-draft intention) is checked FIRST: its
    # title routinely embeds a law name, e.g.
    # "Kliimaseaduse eelnõu väljatöötamise kavatsus", which would otherwise
    # match the broader ``seadus`` arm below and be mis-typed as a Bill
    # (issue #304). Keep this guard above the ``seadus`` check.
    if "kavatsus" in title_lower or "väljatöötamis" in title_lower:
        return "DraftIntent", "Väljatöötamiskavatsus"
    # A regulation (määrus) / order (korraldus) draft is checked BEFORE the
    # broad ``seadus`` substring (issue #599). A ministerial/government
    # regulation title routinely embeds its enabling law name, e.g.
    # "Sotsiaalministri määrus X seaduse alusel" or
    # "Vabariigi Valitsuse määruse ja Y seaduse muutmine", which would
    # otherwise match the ``seadus`` arm and be mis-typed as a Bill /
    # AmendmentBill. A regulation is legally not a bill, and mis-typing also
    # routes it past bill-only impact analysis. #304 fixed only the
    # kavatsus-above-seadus ordering; this seadus-above-määrus ordering is
    # separate.
    elif "määrus" in title_lower:
        if "vabariigi valitsuse" in title_lower:
            return "GovernmentRegulation", "VV määruse eelnõu"
        elif "ministri" in title_lower:
            return "MinisterialRegulation", "Ministri määruse eelnõu"
        return "Regulation", "Määruse eelnõu"
    elif "korraldus" in title_lower:
        return "GovernmentOrder", "Korralduse eelnõu"
    elif "seaduse eelnõu" in title_lower or "seadus" in title_lower:
        if "muutmi" in title_lower:
            return "AmendmentBill", "Seaduse muutmise eelnõu"
        return "Bill", "Seaduseelnõu"
    elif "seisukoht" in title_lower or "euroopa liidu" in title_lower:
        return "EUPosition", "EL seisukoha eelnõu"
    elif "ülevaade" in title_lower or "analüüs" in title_lower or "uuring" in title_lower:
        return "Report", "Ülevaade"
    elif "kodakondsus" in title_lower:
        return "CitizenshipDecision", "Kodakondsuse otsus"
    elif "tegevuskava" in title_lower or "strateegia" in title_lower:
        return "ActionPlan", "Tegevuskava"
    else:
        return "Other", "Muu eelnõu"


# Change-verb stems that mark a candidate as (part of) the *bill's own*
# title rather than the existing law it targets. A draft titled
# "X seaduse muutmise seadus" must yield only "X seaduse" — the trailing
# "...muutmise seadus" matches the bare ``...seadus`` pattern and would
# otherwise be returned as a phantom affected law that resolves to the
# very same IRI (issue #266: duplicate ``amendsLaw`` IRIs downstream).
_CHANGE_VERB_STEMS = ("muutmi", "täiendami", "kehtetuks", "kehtestami")


# F1 (#724 study §8): a §-in-title bill ("Avaliku teabe seaduse § 32 1
# muutmise seadus", "Erakooliseaduse § 22`2 täiendamise seadus") put the §
# clause between the law name and the change verb, so no pattern matched and
# 98 such bills got no amendsLaw. The § / lõige / punkt clause is dropped
# before matching; provision-level targets are #724's business, not this one.
_SUP = "`'^¹²³⁴⁵⁶⁷⁸⁹⁰"
_SECTION_NUMBER = rf"\d+(?:[{_SUP}]+\d*|\s\d{{1,2}}(?=\s))?"
_SECTION_CLAUSE_RE = re.compile(
    rf"\s*§+\s*{_SECTION_NUMBER}"
    rf"(?:\s*(?:,|ja|ning|–|-)\s*(?:§+\s*)?{_SECTION_NUMBER})*"
    r"(?:\s+(?:lõike|lõiget|lõigete|lõikes|lõigetes|lg)\.?\s*\d+"
    r"(?:\s*(?:,|ja|ning)\s*\d+)*)?"
    r"(?:\s+(?:punkti|punkte|punktide|p)\.?\s*\d+(?:\s*(?:,|ja|ning)\s*\d+)*)?",
    re.IGNORECASE,
)


def strip_section_clauses(title: str) -> str:
    """Drop ``§ N [lõike M] [punkti K]`` clauses from a draft title (F1)."""
    return re.sub(r"\s+", " ", _SECTION_CLAUSE_RE.sub(" ", title)).strip()


# F2 (#724 study §8): a coordinated phrase "Karistusseadustiku ja
# tervishoiuteenuste korraldamise seaduse" is ONE regex capture; fuzzy
# resolution then landed on its last member (all 4 KarS/TsÜS links were
# wrong). Split it into one name per law head before resolving. A member
# without its own law head ("Õppetoetuste ja õppelaenu seaduse") stays glued
# to the following member, and a trailing "teiste seaduste" is dropped.
_COORD_SPLIT_RE = re.compile(r"(\s*,\s*|\s+ja\s+|\s+ning\s+)", re.IGNORECASE)
_LAW_HEAD_RE = re.compile(r"(?:seaduse|seadustiku|seadus|seadustik)$", re.IGNORECASE)


def split_coordinated_law_names(name: str) -> list[str]:
    """Split "A seaduse, B seadustiku ja C seaduse" into its member laws."""
    pieces = _COORD_SPLIT_RE.split(name.strip())
    if len(pieces) < 3:
        return [name.strip()]
    members: list[str] = []
    buf = ""
    for index, piece in enumerate(pieces):
        if index % 2:  # a separator
            if buf:
                buf += piece
            continue
        buf += piece
        if _LAW_HEAD_RE.search(piece.strip()):
            members.append(buf.strip(" ,"))
            buf = ""
    return members or [name.strip()]


def detect_affected_laws(title: str) -> list[str]:
    """
    Try to detect which existing laws this draft would amend.
    Returns list of law names mentioned in the title.
    """
    affected = []
    title = strip_section_clauses(title)
    # Common patterns: "X seaduse muutmine", "X seadustiku muutmine".
    # The leading token is [\w.]+ (not \w+) so a year prefix like "2016."
    # is captured as part of the law name (issue #380). Without it the
    # match starts at "aasta", dropping the year, and the year-less
    # "aasta riigieelarve seaduse" mis-resolves to the only budget law in
    # the corpus (the 2026 one).
    patterns = [
        r"([\w.]+(?:,?\s+\w+)*?\s+seaduse)\s+(?:muutmi|täiendami)",
        r"([\w.]+(?:,?\s+\w+)*?\s+seadustiku)\s+(?:muutmi|täiendami)",
        r"(\w+seaduse)\s+(?:muutmi|täiendami)",
        r"(\w+seadustiku)\s+(?:muutmi|täiendami)",
        r"([\w.]+(?:\s+\w+)*?\s+seadus)\b",
        r"([\w.]+(?:\s+\w+)*?\s+seadustik)\b",
    ]
    for pattern in patterns:
        matches = re.findall(pattern, title, re.IGNORECASE)
        for m in matches:
            for cleaned in split_coordinated_law_names(m.strip()):
                cleaned_lower = cleaned.lower()
                # Skip the draft itself references.
                if "eelnõu" in cleaned_lower or len(cleaned) <= 5:
                    continue
                # Skip candidates that still carry a change-verb stem: those
                # are the bill's own title (e.g. "X seaduse muutmise seadus"),
                # not the existing law being amended (issue #266).
                if any(stem in cleaned_lower for stem in _CHANGE_VERB_STEMS):
                    continue
                affected.append(cleaned)
    return list(dict.fromkeys(affected))  # deduplicate preserving order


# ---------------------------------------------------------------------------
# Lifecycle: eli-dl:ProcessStep nodes and the derived current phase (#717)
# ---------------------------------------------------------------------------

# Last live EIS refresh (EELNOUD_INDEX "generated", commit 576079199b). The
# feeds have answered HTTP 403 since the Sätla switch-over (2026-10-01), so
# the offline lifecycle pass reasons relative to this snapshot.
EIS_SNAPSHOT_DATE = "2026-03-07"
# A draft still at Phase_PublicConsultation whose latest evidence is older
# than this before the snapshot is flagged estleg:lifecycleStale (its outcome
# is unknown to every source we read; nothing is invented).
STALE_AFTER_DAYS = 365

PROCESS_STEP_TYPES = ["owl:NamedIndividual", "estleg:ProcessStep", "eli-dl:ProcessStep"]
STEP_INFIX = "_Step_"

# Closed value set of estleg:derivationMethod on draft lifecycle steps.
DERIVATION_EIS_FEED = "eis-feed"

# Phase individuals. Order is the tie-break for steps on the same day and
# the estleg:phaseOrder of the individual.
PHASES: dict[str, dict] = {
    "PublicConsultation": {"order": 1, "et": "Avalik konsultatsioon", "en": "Public Consultation"},
    "Review": {"order": 2, "et": "Kooskõlastamine", "en": "Inter-ministerial Review"},
    "Submission": {"order": 3, "et": "Esitatud Vabariigi Valitsusele", "en": "Submitted to Government"},
    "Enacted": {"order": 4, "et": "Vastu võetud", "en": "Enacted"},
    "Withdrawn": {"order": 5, "et": "Tagasi võetud", "en": "Withdrawn"},
    "Rejected": {"order": 6, "et": "Tagasi lükatud", "en": "Rejected"},
    "RiigikoguProceeding": {"order": 7, "et": "Riigikogu menetluses", "en": "In Riigikogu proceedings"},
    "FirstReading": {"order": 8, "et": "Esimene lugemine", "en": "First reading"},
    "SecondReading": {"order": 9, "et": "Teine lugemine", "en": "Second reading"},
    "ThirdReading": {"order": 10, "et": "Kolmas lugemine", "en": "Third reading"},
    "Reconsideration": {"order": 11, "et": "Uuesti arutamine", "en": "Reconsideration"},
    "Lapsed": {"order": 12, "et": "Menetlusest välja langenud", "en": "Lapsed"},
}
# Same-day ordering: a terminal outcome sorts after the stage it ends.
_SAME_DAY_RANK = {
    "PublicConsultation": 1, "Review": 2, "Submission": 3,
    "RiigikoguProceeding": 4, "FirstReading": 5, "SecondReading": 6,
    "ThirdReading": 7, "Reconsideration": 8,
    "Enacted": 9, "Rejected": 9, "Withdrawn": 9, "Lapsed": 9,
}
TERMINAL_PHASES = frozenset({"Enacted", "Rejected", "Withdrawn", "Lapsed"})
FEED_FILE_PHASES = {feed["phase"]: key for key, feed in RSS_FEEDS.items()}


def phase_iri(phase: str) -> str:
    return f"estleg:Phase_{phase}"


def phase_of_iri(iri: str) -> str:
    return iri.removeprefix("estleg:Phase_") if isinstance(iri, str) else ""


def _ref(value: object) -> str | None:
    if isinstance(value, dict):
        ref = value.get("@id")
        return ref if isinstance(ref, str) else None
    return value if isinstance(value, str) else None


def _refs(value: object) -> list[str]:
    items = value if isinstance(value, list) else ([value] if value else [])
    return [r for r in (_ref(item) for item in items) if r]


def _types(node: dict) -> list[str]:
    raw = node.get("@type", [])
    return [raw] if isinstance(raw, str) else [t for t in raw if isinstance(t, str)]


def is_draft(node: object) -> bool:
    return isinstance(node, dict) and "estleg:DraftLegislation" in _types(node)


def is_process_step(node: object) -> bool:
    return isinstance(node, dict) and "estleg:ProcessStep" in _types(node)


def _literal(value: object) -> str:
    if isinstance(value, dict):
        raw = value.get("@value")
        return str(raw) if raw is not None else ""
    return str(value) if value is not None else ""


def step_date(step: dict) -> str:
    return _literal(step.get("dcterms:date"))


def step_phase(step: dict) -> str:
    return phase_of_iri(_ref(step.get("estleg:processStage")) or "")


def step_order(step: dict) -> int:
    try:
        return int(_literal(step.get("estleg:stepOrder")) or 0)
    except ValueError:
        return 0


def method_family(method: str) -> str:
    """``riigikogu-mark`` / ``-eis-number`` / ``-title-date`` -> ``riigikogu``.

    A step's identity must survive a change of the JOIN evidence (a later
    run may confirm a title-date join by mark), so the Riigikogu methods
    share one family in :func:`step_key`.
    """
    return "riigikogu" if method.startswith("riigikogu-") else method


def step_key(step: dict) -> tuple[str, str, str, str]:
    """Identity of a step across runs: (method family, stage, day, raw status)."""
    return (
        method_family(_literal(step.get("estleg:derivationMethod"))),
        step_phase(step),
        step_date(step),
        _literal(step.get("estleg:riigikoguStatus")),
    )


def make_step(
    draft_id: str,
    ordinal: int,
    *,
    phase: str,
    day: str,
    method: str,
    source: str | None,
    label: str,
    extra: dict | None = None,
) -> dict:
    """One ``eli-dl:ProcessStep`` node (``<draft>_Step_<ordinal>``)."""
    if phase not in PHASES:
        raise ValueError(f"unknown lifecycle phase {phase!r}")
    node: dict = {
        "@id": f"{draft_id}{STEP_INFIX}{ordinal}",
        "@type": list(PROCESS_STEP_TYPES),
        "rdfs:label": label,
        "estleg:processStepOf": {"@id": draft_id},
        "estleg:processStage": {"@id": phase_iri(phase)},
        "estleg:stepOrder": {"@value": str(ordinal), "@type": "xsd:integer"},
        "estleg:derivationMethod": method,
    }
    if day:
        node["dcterms:date"] = {"@value": day, "@type": "xsd:date"}
    if source:
        node["dcterms:source"] = {"@id": source}
    if extra:
        node.update(extra)
    return node


def merge_steps(
    draft_id: str,
    existing: list[dict],
    specs: list[dict],
    *,
    replace_methods: frozenset[str] = frozenset(),
) -> list[dict]:
    """Merge step ``specs`` into the ``existing`` steps of one draft.

    A spec is ``make_step``'s keyword arguments without the ids. Steps keep
    their ``@id`` across runs (keyed on :func:`step_key`); a new step gets
    the next free ordinal, so ids only ever append. Existing steps whose
    method is in ``replace_methods`` and that no spec reproduces are dropped
    (a source that is re-derived wholesale, e.g. the Riigikogu cache).
    """
    kept: list[dict] = []
    by_key: dict[tuple, dict] = {}
    for step in existing:
        key = step_key(step)
        if key in by_key:
            continue
        by_key[key] = step
        kept.append(step)
    wanted_keys: set[tuple] = set()
    next_ordinal = max([step_order(s) for s in kept] + [0]) + 1
    ordered_specs = sorted(
        specs,
        key=lambda spec: (
            spec.get("day") or "",
            _SAME_DAY_RANK.get(spec["phase"], 0),
            (spec.get("extra") or {}).get("estleg:riigikoguStatus", ""),
        ),
    )
    for spec in ordered_specs:
        probe = {
            "estleg:derivationMethod": spec["method"],
            "estleg:processStage": {"@id": phase_iri(spec["phase"])},
            "dcterms:date": {"@value": spec.get("day") or ""},
            "estleg:riigikoguStatus": (spec.get("extra") or {}).get("estleg:riigikoguStatus", ""),
        }
        key = step_key(probe)
        wanted_keys.add(key)
        if key in by_key:
            # Refresh mutable payload (label, source, licence) in place.
            fresh = make_step(draft_id, step_order(by_key[key]), **spec)
            by_key[key].clear()
            by_key[key].update(fresh)
            continue
        step = make_step(draft_id, next_ordinal, **spec)
        next_ordinal += 1
        by_key[key] = step
        kept.append(step)
    result = [
        step
        for step in kept
        if _literal(step.get("estleg:derivationMethod")) not in replace_methods
        or step_key(step) in wanted_keys
    ]
    return sorted(result, key=step_order)


def latest_step(steps: list[dict]) -> dict | None:
    """The chronologically last step (undated steps sort first)."""
    if not steps:
        return None
    return max(
        steps,
        key=lambda s: (step_date(s), _SAME_DAY_RANK.get(step_phase(s), 0), step_order(s)),
    )


def derive_phase(steps: list[dict], *, enacted: bool = False) -> str | None:
    """Current phase: the latest step's stage; a resolved ``enactedAs``
    (``enacted=True``) means Enacted unless a later terminal step says
    otherwise."""
    last = latest_step(steps)
    phase = step_phase(last) if last else None
    if enacted and phase not in TERMINAL_PHASES:
        return "Enacted"
    return phase


def is_stale(steps: list[dict], phase: str | None, snapshot: str = EIS_SNAPSHOT_DATE) -> bool:
    """PublicConsultation with no evidence newer than STALE_AFTER_DAYS."""
    if phase != "PublicConsultation":
        return False
    last = latest_step(steps)
    day = step_date(last) if last else ""
    if not day:
        return True
    cutoff = date.fromisoformat(snapshot) - timedelta(days=STALE_AFTER_DAYS)
    return date.fromisoformat(day) < cutoff


def eis_step_spec(feed_key: str, day: str) -> dict:
    feed = RSS_FEEDS[feed_key]
    label = f"EIS: {feed['label_et']}" + (f" ({day})" if day else "")
    return {
        "phase": feed["phase"],
        "day": day,
        "method": DERIVATION_EIS_FEED,
        "source": feed["url"],
        "label": label,
    }


# ---------------------------------------------------------------------------
# initiatedBy: EIS ministry code -> dated institution IRI
# ---------------------------------------------------------------------------


def load_institution_identity(path: Path | None = None) -> dict[str, dict]:
    path = path or INSTITUTION_IDENTITY_PATH
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return doc.get("institutions", {}) if isinstance(doc, dict) else {}


def initiated_by_slug(
    ministry_code: str, day: str, identity: dict[str, dict]
) -> str | None:
    """Institution slug for an EIS code on ``day``.

    Starts from the current owner and walks ``predecessorInstitution`` back
    while the predecessor is the SAME legal person (same registrikood) and
    ``day`` falls before the current name's ``validFrom``. Mergers (a
    predecessor with another registrikood) are not followed: the owner of
    the merged ministry is not knowable from the EIS code alone.
    """
    slug = MINISTRY_INSTITUTION_SLUGS.get(ministry_code)
    if not slug:
        return None
    seen = {slug}
    while day:
        record = identity.get(slug) or {}
        valid_from = record.get("validFrom") or ""
        if not valid_from or day >= valid_from:
            break
        same_person = [
            pred
            for pred in record.get("predecessorInstitution") or []
            if (identity.get(pred) or {}).get("registrikood")
            and (identity.get(pred) or {}).get("registrikood") == record.get("registrikood")
            and pred not in seen
        ]
        if len(same_person) != 1:
            break
        slug = same_person[0]
        seen.add(slug)
    return slug


def institution_iri(slug: str, institutions_dir: Path | None = None) -> str | None:
    base = institutions_dir or INSTITUTIONS_DIR
    if not (base / f"institution_{slug}.json").exists():
        return None
    return f"estleg:Institution_{slug}"


def ministry_code_of(node: dict) -> str:
    eis = _literal(node.get("estleg:eisNumber"))
    return eis.split("/", 1)[0] if "/" in eis else ""


def apply_initiator(
    node: dict,
    identity: dict[str, dict],
    institutions_dir: Path | None = None,
) -> None:
    """Set the ``initiator`` literal and the dated ``initiatedBy`` IRI."""
    code = ministry_code_of(node)
    if code in MINISTRY_CODES:
        node["estleg:initiator"] = MINISTRY_CODES[code]
    day = _literal(node.get("estleg:publicationDate"))
    slug = initiated_by_slug(code, day, identity) if code else None
    iri = institution_iri(slug, institutions_dir) if slug else None
    if iri:
        node["estleg:initiatedBy"] = {"@id": iri}
    else:
        node.pop("estleg:initiatedBy", None)


# ---------------------------------------------------------------------------
# Lifecycle over a set of peep documents
# ---------------------------------------------------------------------------


def peep_path(phase: str, eelnoud_dir: Path | None = None) -> Path:
    return (eelnoud_dir or EELNOUD_DIR) / f"eelnoud_{phase.lower()}_peep.json"


def load_phase_peeps(eelnoud_dir: Path | None = None) -> dict[str, dict]:
    """``{feed_key: doc}`` for the committed per-feed peeps that exist."""
    docs: dict[str, dict] = {}
    for key, feed in RSS_FEEDS.items():
        path = peep_path(feed["phase"], eelnoud_dir)
        if path.exists():
            docs[key] = json.loads(path.read_text(encoding="utf-8"))
    return docs


def drafts_with_steps(doc: dict) -> list[tuple[dict, list[dict]]]:
    """Pair every draft in ``doc`` with its step nodes (via hasProcessStep)."""
    graph = doc.get("@graph") or []
    steps_by_id = {n["@id"]: n for n in graph if is_process_step(n) and isinstance(n.get("@id"), str)}
    out = []
    for node in graph:
        if is_draft(node):
            steps = [steps_by_id[r] for r in _refs(node.get("estleg:hasProcessStep")) if r in steps_by_id]
            out.append((node, steps))
    return out


def replace_steps(doc: dict, steps_by_draft: dict[str, list[dict]]) -> None:
    """Replace the step nodes of every draft in ``steps_by_draft`` at once.

    Drops the draft's previous steps (by ``hasProcessStep`` and by
    ``processStepOf``), appends the new ones and rewrites
    ``hasProcessStep``. One pass over the graph, so a 13k-draft peep stays
    linear.
    """
    graph = doc.get("@graph") or []
    drafts = {n["@id"]: n for n in graph if is_draft(n) and n.get("@id") in steps_by_draft}
    stale: set[str] = set()
    for draft_id, draft in drafts.items():
        stale.update(_refs(draft.get("estleg:hasProcessStep")))
    kept = [
        n
        for n in graph
        if not (
            is_process_step(n)
            and (
                n.get("@id") in stale
                or _ref(n.get("estleg:processStepOf")) in steps_by_draft
            )
        )
    ]
    for draft_id, steps in steps_by_draft.items():
        kept.extend(steps)
        draft = drafts.get(draft_id)
        if draft is None:
            continue
        if steps:
            draft["estleg:hasProcessStep"] = [{"@id": s["@id"]} for s in steps]
        else:
            draft.pop("estleg:hasProcessStep", None)
    doc["@graph"] = kept


def set_draft_steps(doc: dict, draft: dict, steps: list[dict]) -> None:
    """Replace ``draft``'s step nodes in ``doc`` with ``steps``."""
    replace_steps(doc, {draft["@id"]: steps})


def order_graph(graph: list[dict]) -> list[dict]:
    """Header, drafts (existing order kept), then steps grouped per draft.

    Drafts keep their committed order (a live run writes them sorted by
    @id, #341) so the lifecycle pass adds steps without reshuffling the
    file, and drafts stay a contiguous prefix for consumers that slice the
    first N nodes. Steps follow in draft order, then by ``stepOrder``.
    """
    headers = [n for n in graph if "owl:Ontology" in _types(n)]
    steps = [n for n in graph if is_process_step(n)]
    others = [n for n in graph if "owl:Ontology" not in _types(n) and not is_process_step(n)]
    rank = {n.get("@id"): i for i, n in enumerate(others)}
    steps.sort(
        key=lambda st: (
            rank.get(_ref(st.get("estleg:processStepOf")), len(rank)),
            step_order(st),
            str(st.get("@id", "")),
        )
    )
    return headers + others + steps


def finalize_draft(
    draft: dict,
    steps: list[dict],
    snapshot: str = EIS_SNAPSHOT_DATE,
) -> str | None:
    """Derive phase + stale flag on ``draft`` from its ``steps``."""
    enacted = bool(_refs(draft.get("estleg:enactedAs")))
    phase = derive_phase(steps, enacted=enacted)
    if phase:
        draft["estleg:legislativePhase"] = {"@id": phase_iri(phase)}
    if is_stale(steps, phase, snapshot):
        draft["estleg:lifecycleStale"] = {"@value": "true", "@type": "xsd:boolean"}
    else:
        draft.pop("estleg:lifecycleStale", None)
    return phase


def finalize_doc(doc: dict) -> None:
    ctx = doc.get("@context")
    if isinstance(ctx, dict) and "eli-dl" not in ctx:
        doc["@context"] = {**ctx, "eli-dl": ELI_DL_NS}
    # The file groups drafts by the feed they were FIRST seen in; the current
    # phase lives on each draft (#717), so the old "hetkel etapis" (currently
    # at) header wording is corrected in place.
    for node in doc.get("@graph") or []:
        if "owl:Ontology" not in _types(node):
            continue
        desc = node.get("dc:description")
        text = _literal(desc)
        if "hetkel etapis" in text:
            fixed = text.replace("mis on hetkel etapis", "mida EIS näitas esmakordselt etapis")
            if isinstance(desc, dict):
                desc["@value"] = fixed
            else:
                node["dc:description"] = fixed
    doc["@graph"] = order_graph(doc.get("@graph") or [])


def apply_eis_lifecycle(
    docs: dict[str, dict],
    *,
    snapshot: str = EIS_SNAPSHOT_DATE,
    identity: dict[str, dict] | None = None,
    institutions_dir: Path | None = None,
) -> Counter:
    """Offline pass: give every draft its EIS step(s), phase and initiatedBy.

    A draft without any EIS step gets one reconstructed from the committed
    snapshot: the feed of the file it lives in, dated with its
    ``publicationDate`` (the date EIS printed in the feed item title).
    """
    identity = load_institution_identity() if identity is None else identity
    stats: Counter = Counter()
    for feed_key, doc in docs.items():
        updates: dict[str, list[dict]] = {}
        for draft, steps in drafts_with_steps(doc):
            draft_id = draft["@id"]
            if not any(_literal(s.get("estleg:derivationMethod")) == DERIVATION_EIS_FEED for s in steps):
                day = _literal(draft.get("estleg:publicationDate"))
                steps = merge_steps(draft_id, steps, [eis_step_spec(feed_key, day)])
                stats["eis_steps_reconstructed"] += 1
            updates[draft_id] = steps
            apply_initiator(draft, identity, institutions_dir)
            finalize_draft(draft, steps, snapshot)
        replace_steps(doc, updates)
        finalize_doc(doc)
    return stats


def lifecycle_stats(docs: dict[str, dict]) -> dict:
    phases: Counter = Counter()
    steps = 0
    stale = 0
    initiated = 0
    drafts = 0
    methods: Counter = Counter()
    for doc in docs.values():
        for node in doc.get("@graph") or []:
            if is_draft(node):
                drafts += 1
                phases[phase_of_iri(_ref(node.get("estleg:legislativePhase")) or "")] += 1
                stale += "estleg:lifecycleStale" in node
                initiated += "estleg:initiatedBy" in node
            elif is_process_step(node):
                steps += 1
                methods[_literal(node.get("estleg:derivationMethod"))] += 1
    return {
        "drafts": drafts,
        "phases": dict(sorted(phases.items())),
        "process_steps": steps,
        "steps_by_method": dict(sorted(methods.items())),
        "stale": stale,
        "initiated_by": initiated,
    }


def latest_eis_date(docs: dict[str, dict]) -> str:
    """Latest EIS step date in ``docs`` (a data date, never the wall clock)."""
    days = [
        step_date(node)
        for doc in docs.values()
        for node in doc.get("@graph") or []
        if is_process_step(node)
        and _literal(node.get("estleg:derivationMethod")) == DERIVATION_EIS_FEED
    ]
    return max((d for d in days if d), default="")


def write_index(
    docs: dict[str, dict],
    eelnoud_dir: Path | None = None,
    *,
    generated: str | None = None,
) -> dict:
    """Write EELNOUD_INDEX.json from the peeps (phase = DERIVED phase).

    ``phases`` counts drafts by their current (derived) phase, so it agrees
    with ``estleg:legislativePhase``; ``files`` counts drafts per peep (the
    feed of first observation).
    """
    target = eelnoud_dir or EELNOUD_DIR
    if generated is None:
        # Keep the committed data date (the EIS snapshot) unless the caller
        # fetched new observations; fall back to the latest EIS step date.
        try:
            prior = json.loads((target / "EELNOUD_INDEX.json").read_text(encoding="utf-8"))
            generated = str(prior.get("generated") or "")
        except (OSError, ValueError):
            generated = ""
        generated = generated or latest_eis_date(docs)
    rows: list[dict] = []
    files: dict[str, dict] = {}
    for feed_key, feed in RSS_FEEDS.items():
        doc = docs.get(feed_key)
        if doc is None:
            continue
        drafts = [n for n in doc.get("@graph") or [] if is_draft(n)]
        files[feed_key] = {
            "file": peep_path(feed["phase"], target).name,
            "first_observed_in": feed["phase"],
            "count": len(drafts),
        }
        for node in drafts:
            link = node.get("estleg:eisLink")
            rows.append(
                {
                    "id": node["@id"],
                    "title": jsonld_text(node.get("rdfs:label", ""), prefer_language="et"),
                    "eis_number": _literal(node.get("estleg:eisNumber")),
                    "phase": phase_of_iri(_ref(node.get("estleg:legislativePhase")) or ""),
                    "link": _literal(link),
                }
            )
    rows.sort(key=lambda r: r["id"])
    counts = Counter(r["phase"] for r in rows)
    phases = {
        phase: {
            "label_et": meta["et"],
            "label_en": meta["en"],
            "count": counts[phase],
            **({"file": peep_path(phase, target).name} if phase in FEED_FILE_PHASES else {}),
        }
        for phase, meta in PHASES.items()
        if counts.get(phase)
    }
    index = {
        "total_drafts": len(rows),
        # Data date, not a wall clock (#295): the EIS snapshot (offline) or
        # the latest observed EIS date (live). The #531 stamp reads it.
        "generated": generated,
        "source": "https://eelnoud.valitsus.ee",
        "phases": phases,
        "files": files,
        "drafts": [{k: v for k, v in r.items() if k != "id"} for r in rows],
    }
    save_json(target / "EELNOUD_INDEX.json", index)
    return index


def save_phase_peeps(docs: dict[str, dict], eelnoud_dir: Path | None = None) -> None:
    for feed_key, doc in docs.items():
        save_json(peep_path(RSS_FEEDS[feed_key]["phase"], eelnoud_dir), doc)


def run_lifecycle_from_peeps(eelnoud_dir: Path | None = None) -> dict:
    docs = load_phase_peeps(eelnoud_dir)
    stats = apply_eis_lifecycle(docs)
    save_phase_peeps(docs, eelnoud_dir)
    write_index(docs, eelnoud_dir, generated=EIS_SNAPSHOT_DATE)
    return {**lifecycle_stats(docs), **stats}


def fetch_rss(url: str) -> list[dict]:
    """Fetch RSS items, failing before output writes if the feed is unavailable."""
    print(f"  Fetching {url}...")
    try:
        resp = allowed_get(url, timeout=60)
        resp.raise_for_status()
        resp.encoding = "utf-8"
    except Exception as e:
        raise RuntimeError(f"RSS fetch failed for {url}: {e}") from e

    root = parse_xml(resp.text)
    if root.tag != "rss" or root.find("channel") is None:
        raise RuntimeError(f"RSS feed {url} has no rss/channel structure")
    items = []

    for item in root.iter("item"):
        title_el = item.find("title")
        link_el = item.find("link")
        pub_date_el = item.find("pubDate")

        if title_el is None or title_el.text is None:
            continue

        raw_title = title_el.text.strip()
        # Normalize whitespace (some titles have embedded newlines)
        raw_title = re.sub(r"\s+", " ", raw_title)

        items.append({
            "raw_title": raw_title,
            "title": parse_draft_title(raw_title),
            "link": link_el.text.strip() if link_el is not None and link_el.text else "",
            "pub_date": pub_date_el.text.strip() if pub_date_el is not None and pub_date_el.text else "",
        })

    print(f"  Found {len(items)} items")
    return items


_PHASE_COMMENTS_ET = {
    "PublicConsultation": "Eelnõu on avalikul konsultatsioonil – üldsus saab arvamust avaldada.",
    "Review": "Eelnõu on ministeeriumidevahelisel kooskõlastamisel.",
    "Submission": "Eelnõu on esitatud Vabariigi Valitsusele otsustamiseks.",
    "Enacted": "Eelnõu on vastu võetud ja jõustunud seadusena.",
    "Withdrawn": "Algataja võttis eelnõu menetlusest tagasi.",
    "Rejected": "Eelnõu lükati menetluses tagasi.",
    "RiigikoguProceeding": "Eelnõu on Riigikogus algatatud või menetlusse võetud.",
    "FirstReading": "Eelnõu on Riigikogus esimesel lugemisel.",
    "SecondReading": "Eelnõu on Riigikogus teisel lugemisel.",
    "ThirdReading": "Eelnõu on Riigikogus kolmandal lugemisel.",
    "Reconsideration": "Vabariigi President jättis seaduse välja kuulutamata; Riigikogu arutab uuesti.",
    "Lapsed": "Eelnõu langes menetlusest välja (koosseisu lõppemine, ühendamine, tagastamine vms).",
}


def phase_individual_nodes() -> list[dict]:
    return [
        {
            "@id": phase_iri(phase),
            "@type": ["owl:NamedIndividual", "estleg:LegislativePhase", "eli-dl:ProcessStage"],
            "rdfs:label": {"@value": meta["et"], "@language": "et"},
            "skos:prefLabel": {"@value": meta["en"], "@language": "en"},
            "rdfs:comment": {"@value": _PHASE_COMMENTS_ET[phase], "@language": "et"},
            "estleg:phaseOrder": {"@value": str(meta["order"]), "@type": "xsd:integer"},
        }
        for phase, meta in PHASES.items()
    ]


def lifecycle_schema_nodes() -> list[dict]:
    """T-Box terms the #717 lifecycle layer emits (mirrored in the CV)."""
    def prop(iri, kind, label, comment, domain=None, rng=None):
        node = {
            "@id": iri,
            "@type": [kind],
            "rdfs:label": {"@value": label, "@language": "en"},
            "rdfs:comment": {"@value": comment, "@language": "en"},
        }
        if domain:
            node["rdfs:domain"] = {"@id": domain}
        if rng:
            node["rdfs:range"] = {"@id": rng}
        return node

    return [
        {
            "@id": "estleg:ProcessStep",
            "@type": ["owl:Class"],
            "rdfs:subClassOf": {"@id": "eli-dl:ProcessStep"},
            "rdfs:label": {"@value": "Menetlussamm (Process Step)", "@language": "et"},
            "rdfs:comment": {
                "@value": "One dated observation of a draft at a legislative stage (EIS feed or Riigikogu proceeding event), #717.",
                "@language": "en",
            },
        },
        prop("estleg:hasProcessStep", "owl:ObjectProperty", "has process step",
             "A dated lifecycle step of the draft (#717).", "estleg:DraftLegislation", "estleg:ProcessStep"),
        prop("estleg:processStepOf", "owl:ObjectProperty", "process step of",
             "Inverse of estleg:hasProcessStep (#717).", "estleg:ProcessStep", "estleg:DraftLegislation"),
        prop("estleg:processStage", "owl:ObjectProperty", "process stage",
             "The estleg:LegislativePhase (eli-dl:ProcessStage) the step observed (#717).",
             "estleg:ProcessStep", "estleg:LegislativePhase"),
        prop("estleg:stepOrder", "owl:DatatypeProperty", "step order",
             "Stable ordinal of the step within its draft; ids only append (#717).",
             "estleg:ProcessStep", "xsd:integer"),
        prop("estleg:derivationMethod", "owl:DatatypeProperty", "derivation method",
             "How a value was derived (closed vocabulary, docs/DRAFT_LIFECYCLE.md): eis-feed, "
             "riigikogu-eis-number, riigikogu-mark, riigikogu-title-date, minted-ecli, "
             "rederived-case-type, title-regex, cellar-interprets (#717).",
             None, "xsd:string"),
        prop("estleg:initiatedBy", "owl:ObjectProperty", "initiated by",
             "The estleg:Institution that owns the draft in EIS, dated along the same-legal-person "
             "rename chain. No rdfs:range so bare institution IRIs are not phantom-typed (#717).",
             "estleg:DraftLegislation"),
        prop("estleg:lifecycleStale", "owl:DatatypeProperty", "lifecycle stale",
             "True when the draft is still at public consultation with no evidence newer than a year "
             "before the EIS snapshot; its outcome is unknown, not invented (#717).",
             "estleg:DraftLegislation", "xsd:boolean"),
        prop("estleg:riigikoguMark", "owl:DatatypeProperty", "Riigikogu mark",
             "Riigikogu registration mark with draft type code, e.g. '897 SE' (#717).",
             "estleg:DraftLegislation", "xsd:string"),
        prop("estleg:riigikoguUuid", "owl:DatatypeProperty", "Riigikogu UUID",
             "UUID of the Riigikogu draft volume (api.riigikogu.ee /api/volumes/drafts/{uuid}), #717.",
             "estleg:DraftLegislation", "xsd:string"),
        prop("estleg:riigikoguMembership", "owl:DatatypeProperty", "Riigikogu membership",
             "Riigikogu membership (koosseis) number the draft was proceeded in (#717).",
             "estleg:DraftLegislation", "xsd:integer"),
        prop("estleg:riigikoguStatus", "owl:DatatypeProperty", "Riigikogu status",
             "Raw Riigikogu proceeding status code of the event the step records (#717).",
             "estleg:ProcessStep", "xsd:string"),
    ]


def generate_schema_nodes() -> list[dict]:
    """Generate the ontology schema nodes for DraftLegislation."""
    return [
        # DraftLegislation class
        {
            "@id": "estleg:DraftLegislation",
            "@type": ["owl:Class"],
            "rdfs:subClassOf": {"@id": "eli-dl:DraftLegislationWork"},
            "rdfs:label": {"@value": "Eelnõu (Draft Legislation)", "@language": "et"},
            "rdfs:comment": {
                "@value": (
                    "Õigusakt, mis ei ole veel jõustunud, kuid on seadusandlikus "
                    "menetluses. ELI-DL v3: rdfs:subClassOf eli-dl:DraftLegislationWork (#443)."
                ),
                "@language": "et",
            },
            "dc:description": {"@value": "A legislative draft that has not yet been enacted into law but is in the legislative process.", "@language": "en"},
        },
        # LegislativePhase class
        {
            "@id": "estleg:LegislativePhase",
            "@type": ["owl:Class"],
            "rdfs:label": {"@value": "Seadusandlik etapp (Legislative Phase)", "@language": "et"},
            "rdfs:comment": {"@value": "Eelnõu menetlusetapp EIS süsteemis.", "@language": "et"},
        },
        # DraftType class
        {
            "@id": "estleg:DraftType",
            "@type": ["owl:Class"],
            "rdfs:label": {"@value": "Eelnõu liik (Draft Type)", "@language": "et"},
            "rdfs:comment": {"@value": "Eelnõu tüüp: seaduseelnõu, määruse eelnõu, korralduse eelnõu jne.", "@language": "et"},
        },
        # Phase individuals (EIS feeds 1-3, outcomes 4-6, Riigikogu 7-12; #717)
        *phase_individual_nodes(),
        # Draft type individuals
        {
            "@id": "estleg:DraftType_Bill",
            "@type": ["owl:NamedIndividual", "estleg:DraftType"],
            "rdfs:label": {"@value": "Seaduseelnõu", "@language": "et"},
            "skos:prefLabel": {"@value": "Bill", "@language": "en"},
        },
        {
            "@id": "estleg:DraftType_AmendmentBill",
            "@type": ["owl:NamedIndividual", "estleg:DraftType"],
            "rdfs:label": {"@value": "Seaduse muutmise eelnõu", "@language": "et"},
            "skos:prefLabel": {"@value": "Amendment Bill", "@language": "en"},
        },
        {
            "@id": "estleg:DraftType_GovernmentRegulation",
            "@type": ["owl:NamedIndividual", "estleg:DraftType"],
            "rdfs:label": {"@value": "VV määruse eelnõu", "@language": "et"},
            "skos:prefLabel": {"@value": "Government Regulation Draft", "@language": "en"},
        },
        {
            "@id": "estleg:DraftType_MinisterialRegulation",
            "@type": ["owl:NamedIndividual", "estleg:DraftType"],
            "rdfs:label": {"@value": "Ministri määruse eelnõu", "@language": "et"},
            "skos:prefLabel": {"@value": "Ministerial Regulation Draft", "@language": "en"},
        },
        {
            "@id": "estleg:DraftType_GovernmentOrder",
            "@type": ["owl:NamedIndividual", "estleg:DraftType"],
            "rdfs:label": {"@value": "Korralduse eelnõu", "@language": "et"},
            "skos:prefLabel": {"@value": "Government Order Draft", "@language": "en"},
        },
        {
            "@id": "estleg:DraftType_EUPosition",
            "@type": ["owl:NamedIndividual", "estleg:DraftType"],
            "rdfs:label": {"@value": "EL seisukoha eelnõu", "@language": "et"},
            "skos:prefLabel": {"@value": "EU Position Draft", "@language": "en"},
        },
        {
            "@id": "estleg:DraftType_DraftIntent",
            "@type": ["owl:NamedIndividual", "estleg:DraftType"],
            "rdfs:label": {"@value": "Väljatöötamiskavatsus", "@language": "et"},
            "skos:prefLabel": {"@value": "Draft Intent / Pre-draft", "@language": "en"},
        },
        {
            "@id": "estleg:DraftType_Regulation",
            "@type": ["owl:NamedIndividual", "estleg:DraftType"],
            "rdfs:label": {"@value": "Määruse eelnõu", "@language": "et"},
            "skos:prefLabel": {"@value": "Regulation Draft", "@language": "en"},
        },
        {
            "@id": "estleg:DraftType_ActionPlan",
            "@type": ["owl:NamedIndividual", "estleg:DraftType"],
            "rdfs:label": {"@value": "Tegevuskava", "@language": "et"},
            "skos:prefLabel": {"@value": "Action Plan", "@language": "en"},
        },
        {
            "@id": "estleg:DraftType_Report",
            "@type": ["owl:NamedIndividual", "estleg:DraftType"],
            "rdfs:label": {"@value": "Ülevaade", "@language": "et"},
            "skos:prefLabel": {"@value": "Report", "@language": "en"},
        },
        {
            "@id": "estleg:DraftType_CitizenshipDecision",
            "@type": ["owl:NamedIndividual", "estleg:DraftType"],
            "rdfs:label": {"@value": "Kodakondsuse otsus", "@language": "et"},
            "skos:prefLabel": {"@value": "Citizenship Decision", "@language": "en"},
        },
        {
            "@id": "estleg:DraftType_Other",
            "@type": ["owl:NamedIndividual", "estleg:DraftType"],
            "rdfs:label": {"@value": "Muu eelnõu", "@language": "et"},
            "skos:prefLabel": {"@value": "Other Draft", "@language": "en"},
        },
        # Object properties
        {
            "@id": "estleg:legislativePhase",
            "@type": ["owl:ObjectProperty"],
            "rdfs:label": {"@value": "seadusandlik etapp", "@language": "et"},
            "rdfs:domain": {"@id": "estleg:DraftLegislation"},
            "rdfs:range": {"@id": "estleg:LegislativePhase"},
            "rdfs:comment": {"@value": "The current legislative phase of the draft.", "@language": "en"},
        },
        {
            "@id": "estleg:draftType",
            "@type": ["owl:ObjectProperty"],
            "rdfs:label": {"@value": "eelnõu liik", "@language": "et"},
            "rdfs:domain": {"@id": "estleg:DraftLegislation"},
            "rdfs:range": {"@id": "estleg:DraftType"},
            "rdfs:comment": {"@value": "The type/category of the draft.", "@language": "en"},
        },
        {
            "@id": "estleg:amendsLaw",
            "@type": ["owl:ObjectProperty"],
            "rdfs:label": {"@value": "muudab seadust", "@language": "et"},
            "rdfs:domain": {"@id": "estleg:DraftLegislation"},
            "rdfs:range": {"@id": "estleg:LegalProvision"},
            "rdfs:comment": {"@value": "Links a draft to the existing law it proposes to amend.", "@language": "en"},
        },
        {
            "@id": "estleg:enactedAs",
            "@type": ["owl:ObjectProperty"],
            "rdfs:label": {"@value": "jõustus seadusena", "@language": "et"},
            "rdfs:domain": {"@id": "estleg:DraftLegislation"},
            "rdfs:comment": {
                "@value": (
                    "Links a draft with changeType=enacts to the enacted act "
                    "IRI when the title resolves against INDEX. No rdfs:range "
                    "— objects are often bare act stubs in the drafts bucket."
                ),
                "@language": "en",
            },
        },
        # Datatype properties
        {
            "@id": "estleg:eisNumber",
            "@type": ["owl:DatatypeProperty"],
            "rdfs:label": "EIS number",
            "rdfs:domain": {"@id": "estleg:DraftLegislation"},
            "rdfs:range": {"@id": "xsd:string"},
            "rdfs:comment": {"@value": "The EIS (Eelnõude infosüsteem) reference number.", "@language": "en"},
        },
        {
            "@id": "estleg:eisLink",
            "@type": ["owl:DatatypeProperty"],
            "rdfs:label": "EIS link",
            "rdfs:domain": {"@id": "estleg:DraftLegislation"},
            "rdfs:range": {"@id": "xsd:anyURI"},
            "rdfs:comment": {"@value": "Direct link to the draft in EIS.", "@language": "en"},
        },
        {
            "@id": "estleg:initiator",
            "@type": ["owl:DatatypeProperty"],
            "rdfs:label": {"@value": "algataja", "@language": "et"},
            "rdfs:domain": {"@id": "estleg:DraftLegislation"},
            "rdfs:range": {"@id": "xsd:string"},
            "rdfs:comment": {"@value": "The ministry or institution that initiated the draft.", "@language": "en"},
        },
        {
            "@id": "estleg:publicationDate",
            "@type": ["owl:DatatypeProperty"],
            "rdfs:label": {"@value": "avaldamiskuupäev", "@language": "et"},
            "rdfs:domain": {"@id": "estleg:DraftLegislation"},
            "rdfs:range": {"@id": "xsd:date"},
            "rdfs:comment": {"@value": "Date the draft was published/registered in EIS.", "@language": "en"},
        },
        {
            "@id": "estleg:affectedLawName",
            "@type": ["owl:DatatypeProperty"],
            "rdfs:label": {"@value": "mõjutatud seadus", "@language": "et"},
            "rdfs:domain": {"@id": "estleg:DraftLegislation"},
            "rdfs:range": {"@id": "xsd:string"},
            "rdfs:comment": {"@value": "Name of existing law this draft proposes to amend.", "@language": "en"},
        },
        *lifecycle_schema_nodes(),
    ]


def generate_draft_node(
    item: dict,
    phase_id: str,
    eis_number: str,
    ministry_code: str,
    date_str: str,
) -> dict:
    """Generate a JSON-LD node for a single draft."""
    uuid = extract_uuid(item["link"])
    draft_type_id, draft_type_label = classify_draft_type(item["title"])
    affected_laws = detect_affected_laws(item["title"])

    # Create stable ID from EIS number or UUID
    if eis_number:
        safe_id = sanitize_id(eis_number)
    elif uuid:
        safe_id = uuid.replace("-", "")[:16]
    else:
        safe_id = sanitize_id(item["title"][:TITLE_KEY_LEN])

    node: dict = {
        "@id": f"estleg:Draft_{safe_id}",
        "@type": ["owl:NamedIndividual", "estleg:DraftLegislation"],
        "rdfs:label": {"@value": item["title"], "@language": "et"},
        "estleg:legislativePhase": {"@id": f"estleg:Phase_{phase_id}"},
        "estleg:draftType": {"@id": f"estleg:DraftType_{draft_type_id}"},
    }

    if eis_number:
        node["estleg:eisNumber"] = eis_number

    if item["link"]:
        node["estleg:eisLink"] = {"@value": item["link"], "@type": "xsd:anyURI"}
        node["dcterms:source"] = {"@id": item["link"]}

    # Issue #606 (item 9): only emit estleg:initiator for a KNOWN ministry
    # code. After the 2023 ministry reorg an unmapped code (e.g. a newly
    # created ministry) must not be passed through verbatim as the initiator
    # literal — that pollutes the data with a raw code instead of a name.
    # Surface the raw code under a marker property plus a stderr warning.
    if ministry_code in MINISTRY_CODES:
        # Plain xsd:string (issue #382): estleg:initiator has rdfs:range
        # xsd:string and the SHACL DraftLegislationShape constrains it to
        # sh:datatype xsd:string. A {@value,@language} value-object is an
        # rdf:langString and would fail SHACL conformance.
        node["estleg:initiator"] = MINISTRY_CODES[ministry_code]
    elif ministry_code:
        print(
            f"  WARNING: unmapped EIS ministry code {ministry_code!r}; "
            "emitting estleg:initiatorCodeUnmapped instead of estleg:initiator",
            file=sys.stderr,
        )
        # Plain xsd:string too, mirroring the estleg:initiator shape note.
        node["estleg:initiatorCodeUnmapped"] = ministry_code

    if date_str:
        try:
            parsed = datetime.strptime(date_str, "%d.%m.%Y")
            # Issue #606 (item 13b): strptime validates FORMAT, not RANGE, so
            # a spurious "01.01.1900"/"31.12.2099" would otherwise be accepted.
            # EIS (eelnoud.valitsus.ee) drafts are recent (the system dates to
            # the early 2010s); bound the year to a sane EIS-era window and
            # only emit publicationDate when in range. The upper bound is
            # now()+1 (not a hardcoded future year) to tolerate near-future
            # dates without admitting far-future garbage.
            if 2000 <= parsed.year <= datetime.now().year + 1:
                node["estleg:publicationDate"] = {
                    "@value": parsed.strftime("%Y-%m-%d"),
                    "@type": "xsd:date",
                }
        except ValueError:
            pass

    if affected_laws:
        node["estleg:affectedLawName"] = affected_laws

    return node


def rebuild_eelnoud_combined_from_peeps(eelnoud_dir: Path | None = None) -> dict:
    """Rebuild ``eelnoud_combined.jsonld`` offline from schema + phase peeps.

    Thin wrapper over
    :func:`estleg.rebuild_subcorpus_combined.rebuild_subcorpus_combined`, the
    single offline producer whose source list is the parity gate's own.
    """
    from estleg.rebuild_subcorpus_combined import rebuild_subcorpus_combined

    target = eelnoud_dir if eelnoud_dir is not None else EELNOUD_DIR
    return rebuild_subcorpus_combined(
        "eelnoud", target.parent, subcorpus_dir=target
    ).as_dict()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--lifecycle-from-peeps",
        action="store_true",
        help=(
            "Skip the EIS fetch; re-derive the eli-dl:ProcessStep lifecycle, "
            "phase and initiatedBy offline from the committed peeps (#717)."
        ),
    )
    parser.add_argument(
        "--rebuild-combined-from-peeps",
        action="store_true",
        help=(
            "Skip the EIS fetch; rebuild eelnoud_combined.jsonld offline from "
            "eelnoud_schema.json + the phase peeps."
        ),
    )
    return parser.parse_args(argv)


# Draft fields owned by later passes, carried over a live re-fetch (the
# passes re-derive them anyway; carrying keeps a fetch-only run lossless).
LIVE_PRESERVED_KEYS = (
    "estleg:amendsLaw",
    "estleg:changeType",
    "estleg:enactedAs",
    "estleg:riigikoguMark",
    "estleg:riigikoguUuid",
    "estleg:riigikoguMembership",
    "dcterms:subject",
    "estleg:subjectSource",
)


def main(argv: list[str] | None = None) -> int:
    # ``argv=None`` means "no CLI flags" (not sys.argv) so in-process callers
    # and tests that call ``main()`` keep the plain live-fetch behaviour.
    args = parse_args([] if argv is None else argv)
    if args.lifecycle_from_peeps:
        stats = run_lifecycle_from_peeps()
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        return 0
    if args.rebuild_combined_from_peeps:
        stats = rebuild_eelnoud_combined_from_peeps()
        print(
            f"Rebuilt {stats['path']} from peeps: "
            f"{stats['nodes']} nodes, {stats['files']} source files"
        )
        return 0

    print("=" * 60)
    print("Fetching draft legislation from EIS")
    print("=" * 60)

    # Prior history: drafts, steps and the file each draft lives in. A live
    # run MERGES into it (feeds only show the present; the lifecycle is the
    # accumulated observations), and never drops a draft a feed stopped
    # listing.
    prior_docs = load_phase_peeps()
    prior: dict[str, tuple[str, dict, list[dict]]] = {}
    for feed_key, doc in prior_docs.items():
        for node, steps in drafts_with_steps(doc):
            prior[node["@id"]] = (feed_key, node, steps)

    entries: dict[str, dict] = {}
    seen_ids: dict[str, str] = {}

    # Fetch all RSS feeds; EVERY observation becomes a step.
    for feed_key, feed_info in RSS_FEEDS.items():
        print(f"\n--- {feed_info['label_et']} ({feed_info['label_en']}) ---")
        items = fetch_rss(feed_info["url"])

        for item in items:
            eis_number, ministry_code, date_str = parse_eis_number(item["raw_title"])
            uuid = extract_uuid(item["link"])

            # Deduplicate by EIS number or UUID
            dedup_key = eis_number or uuid or item["title"][:TITLE_KEY_LEN]
            draft_node = generate_draft_node(
                item,
                phase_id=feed_info["phase"],
                eis_number=eis_number,
                ministry_code=ministry_code,
                date_str=date_str,
            )
            draft_id = seen_ids.setdefault(dedup_key, draft_node["@id"])
            entry = entries.get(draft_id)
            if entry is None:
                entry = entries[draft_id] = {"node": draft_node, "feed": feed_key, "specs": []}
            day = _literal(draft_node.get("estleg:publicationDate"))
            entry["specs"].append(eis_step_spec(feed_key, day))

    print(f"\n--- Unique drafts observed: {len(entries)} ---")

    identity = load_institution_identity()
    docs: dict[str, dict] = {
        key: {
            "@context": DRAFTS_CONTEXT,
            "@graph": [
                {
                    "@id": mint_act_iri(f"Eelnoud_{feed['phase']}"),
                    "@type": ["owl:Ontology"],
                    "rdfs:label": {"@value": f"EIS eelnõud – {feed['label_et']}", "@language": "et"},
                    "dc:description": {
                        "@value": f"Eelnõud, mida EIS näitas esmakordselt etapis: {feed['label_et']}",
                        "@language": "et",
                    },
                    "dc:source": "Eelnõude infosüsteem (EIS) – eelnoud.valitsus.ee",
                }
            ],
        }
        for key, feed in RSS_FEEDS.items()
    }
    updates: dict[str, dict[str, list[dict]]] = {}
    for draft_id in sorted(set(entries) | set(prior)):
        entry = entries.get(draft_id)
        prior_feed, prior_node, prior_steps = prior.get(draft_id, (None, None, []))
        feed_key = prior_feed or entry["feed"]
        if entry is not None:
            node = entry["node"]
            if prior_node is not None:
                for key in LIVE_PRESERVED_KEYS:
                    if key in prior_node:
                        node[key] = prior_node[key]
            specs = entry["specs"]
        else:
            node, specs = prior_node, []
        steps = merge_steps(draft_id, prior_steps, specs)
        docs[feed_key]["@graph"].append(node)
        updates.setdefault(feed_key, {})[draft_id] = steps
        apply_initiator(node, identity)
        finalize_draft(node, steps, date.today().isoformat())
    for key, doc in docs.items():
        replace_steps(doc, updates.get(key, {}))
        finalize_doc(doc)
    docs = {k: d for k, d in docs.items() if any(is_draft(n) for n in d["@graph"])}

    # The T-Box file is a CV projection (generate_schemas_from_cv.py, #433);
    # only bootstrap it when absent.
    schema_path = EELNOUD_DIR / "eelnoud_schema.json"
    if not schema_path.exists():
        save_json(schema_path, {"@context": ELI_DL_SCHEMA_CONTEXT, "@graph": generate_schema_nodes()})
        print(f"  Saved: {schema_path.name}")

    save_phase_peeps(docs)
    for key, doc in docs.items():
        print(f"  Saved: {peep_path(RSS_FEEDS[key]['phase']).name} ({len(doc['@graph'])} nodes)")

    # Combined file: written by the offline rebuild (schema + phase peeps,
    # the parity gate's own source list) so live and offline cannot drift.
    print("\n--- Generating combined drafts file ---")
    stats = rebuild_eelnoud_combined_from_peeps(EELNOUD_DIR)
    print(f"  Saved: {stats['path'].name} ({stats['nodes']} nodes)")

    # NOTE (issue #295): ``generated`` is the latest observed EIS date (a data
    # date, byte-stable for the same feed content), never the wall clock.
    index = write_index(docs, generated=latest_eis_date(docs))
    print("\n" + "=" * 60)
    print(f"Done! {index['total_drafts']} drafts in {EELNOUD_DIR.relative_to(REPO_ROOT)}")
    for phase, meta in index["phases"].items():
        print(f"  {meta['label_et']}: {meta['count']} drafts")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

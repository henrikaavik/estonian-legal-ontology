#!/usr/bin/env python3
"""Classify LegalProvision target groups for administrative-burden analysis.

The classifier is deliberately deterministic. It first maps explicit
``estleg:dutyHolder`` values (TargetGroup IRIs, or leftover phrases) then
falls back to deontic text cues in the provision summary/full text.
Unmapped duty-holder phrases are dropped from the graph (#460) rather than
kept as free literals.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from estleg.estleg_common import (
    BUILD_EVALUATION_DATE,
    KRR_DIR,
    heuristic_confidence_for_node,
    iter_peep_files,
    jsonld_text,
    save_json,
    stamp_assertion_confidence,
)

TARGET_GROUP_ORDER: tuple[str, ...] = (
    "citizen",
    "business",
    "public_body",
    "official",
    "ngo",
)

# Closed enum → compact IRI. CV individuals already exist (#609);
# #460 stores these IRIs on estleg:targetGroup itself.
TARGET_GROUP_IRI: dict[str, str] = {
    "citizen": "estleg:TargetGroup_Citizen",
    "business": "estleg:TargetGroup_Business",
    "public_body": "estleg:TargetGroup_PublicBody",
    "official": "estleg:TargetGroup_Official",
    "ngo": "estleg:TargetGroup_NGO",
}
_IRI_TO_TOKEN: dict[str, str] = {iri: token for token, iri in TARGET_GROUP_IRI.items()}
_FULL_IRI_PREFIX = "https://w3id.org/estleg/"
_LFS_POINTER_PREFIX = "version https://git-lfs.github.com/spec/v1"

TARGET_GROUP_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "business": tuple(
        re.compile(p, re.IGNORECASE | re.UNICODE)
        for p in (
            r"\btööandja\w*\b",
            r"\bettevõtja\w*\b",
            r"\bettevõt(?:e|ja)\w*\b",
            r"\b\w+ettevõtja\w*\b",
            r"\bäriühing\w*\b",
            r"\bosaühing\w*\b",
            r"\baktsiaselts\w*\b",
            r"\bjuhatuse\s+liik\w*\b",
            r"\baktsionär\w*\b",
            r"\bosanik\w*\b",
            r"\bvedaja\w*\b",
            r"\bjäätmevedaja\w*\b",
            r"\bsoojusettevõtja\w*\b",
            r"\bkrediidiasutus\w*\b",
            r"\btootja\w*\b",
            r"\bteenuse\s+osutaja\w*\b",
            r"\b(?:avaliku\s+ürituse\s+)?korraldaja\w*\b",
            r"\btehnovõrgu\s+omanik\w*\b",
            r"\bmüügikoha\s+omanik\w*\b",
            r"\bkrediidiandja\w*\b",
            r"\bkindlustusandja\w*\b",
            r"\b(?:vastutav|volitatud)?\s*töötleja\w*\b",
            r"\bkäitaja\w*\b",
            # Compound handlers (alkoholikäitleja, toidukäitleja, …) must
            # count as business; a bare ``\bkäitleja`` misses them (#460).
            r"\b\w*käitleja\w*\b",
            # Tax-obligated entities. In administrative-burden terms a
            # generic taxpayer/tax-liable subject is treated as a business
            # (consistent with raamatupidamiskohustuslane below); the
            # citizen list no longer carries this cue (#277).
            r"\bmaksu(?:maksja|kohustuslane)\w*\b",
            # A legal entity is, by definition, a business. The negative
            # lookbehind on the citizen ``isik`` cue keeps "juriidiline isik"
            # out of citizen; this routes it to business (#277).
            r"\bjuriidili\w*\s+isik\w*\b",
            r"\bandmeandja\w*\b",
            r"\bsideettevõtja\w*\b",
            r"\braamatupidamiskohustuslane\w*\b",
            r"\bhoonestaja\w*\b",
            r"\binvesteerimisühing\w*\b",
            # #576: a bare ``juhatus`` (board) is NOT a business cue on its own —
            # MTÜs, sihtasutused and public bodies all have one (130 false
            # business tags). Require a commercial-entity context (genitive
            # ``<entity>+i/u/e juhatus``) so only a *company's* board signals
            # business.
            r"\b(?:aktsiaselts|osaühing|äriühing|täisühing|usaldusühing"
            r"|tulundusühistu|ettevõt\w+|äri\w*)\w*\s+juhatus\w*\b",
            r"\breeder\w*\b",
            r"\bkaevetöö\s+tegija\w*\b",
            # NOTE: bare ``kasutaja`` ("user") and ``võlgnik``/``võlausaldaja``
            # ("debtor"/"creditor") were ALSO in this list, so any provision
            # mentioning them was always tagged [citizen, business] (#277).
            # They are generic natural-person roles, so they now live only in
            # the citizen list. Specific business users keep their own
            # qualified cues elsewhere.
            r"\bemitent\w*\b",
            r"\bvõrguettevõtja\w*\b",
            r"\bpakkuja\w*\b",
            r"\breklaami\s+avalikustaja\w*\b",
            r"\btegevusloa\s+omaja\w*\b",
            r"\bpostiteenuse\s+osutaja\w*\b",
            r"\bpensionifondivalitseja\w*\b",
        )
    ),
    "citizen": tuple(
        re.compile(p, re.IGNORECASE | re.UNICODE)
        for p in (
            r"\btöötaja\w*\b",
            r"\bfüüsiline\s+isik\w*\b",
            # Bare ``isik`` ("person") is a natural person — EXCEPT in
            # "juriidiline isik" (a legal entity = business). The fixed-width
            # negative lookbehinds drop the nominative/genitive of that phrase
            # so it does not score as citizen; ``juriidili\w* isik`` in the
            # business list then routes it correctly (#277).
            r"(?<!juriidiline\s)(?<!juriidilise\s)"
            r"\bisik(?:u|ul|ule|uga|ust|uks|ut|uid|ud|ute)?\b",
            r"\bkodanik\w*\b",
            r"\belanik\w*\b",
            r"\btarbija\w*\b",
            r"\bandmesubjekt\w*\b",
            r"\bpatsient\w*\b",
            r"\btaotleja\w*\b",
            r"\bõpilane\w*\b",
            r"\b(?:lapse)?vanem\w*\b",
            r"\bhooldaja\w*\b",
            r"\beestkostja\w*\b",
            r"\b(?:toetuse|stipendiumi)\s+saaja\w*\b",
            r"\bhauaplatsi\s+kasutaja\w*\b",
            r"\bloomapidaja\w*\b",
            r"\b(?:kinnistu|korteri|eluruumi|hauaplatsi)?\s*omanik\w*\b",
            # ``maksu(maksja|kohustuslane)`` moved to business-only (#277); the
            # debtor/creditor and user cues below are citizen-only now.
            r"\b(?:võlgnik|võlausaldaja)\w*\b",
            r"\bsõitja\w*\b",
            r"\blaevapere\s+liige\w*\b",
            r"\bnoortevolikogu\s+kandidaa\w*\b",
            r"\bkinnipeetav\w*\b",
            r"\bvälismaalane\w*\b",
            r"\bväikelaevajuht\w*\b",
            r"\bvaatleja\w*\b",
            r"\bjahipiirkonna\s+kasutaja\w*\b",
            r"\bkasutaja\w*\b",
            r"\bvaldaja\w*\b",
            r"\büürnik\w*\b",
            r"\blugeja\w*\b",
        )
    ),
    "public_body": tuple(
        re.compile(p, re.IGNORECASE | re.UNICODE)
        for p in (
            r"\bministeerium\w*\b",
            r"\b(?:maksu- ja tolliamet|politsei- ja piirivalveamet|"
            r"keskkonnaamet|terviseamet|transpordiamet|maanteeamet|"
            r"põllumajandus- ja toiduamet|haridus- ja noorteamet)\w*\b",
            r"\b(?:amet|ameti|ametil|ametile|ametiga|ametist)\b",
            r"\binspektsioon\w*\b",
            r"\b(?:kohus|kohtu(?:le|s|st|ga|lt|l|te|id)?|kohtud)\b",
            r"\bvalitsus\w*\b",
            r"\bvolikogu\w*\b",
            r"\bomavalitsus\w*\b",
            r"\briigikogu\w*\b",
            r"\bregistripidaja\w*\b",
            r"\bavalik-?õiguslik\w*\b",
            # School (kool) and its case inflections, enumerated so that this
            # cue does NOT swallow ``koolitus*`` ("training") — a non-public-body
            # obligation context that the bare ``kool\w*`` over-matched (#330).
            r"\bkool(?:i(?:s|le|lt|st|ks|ga|d|de)?)?\b",
            r"\brakendusüksus\w*\b",
            r"\b(?:valla|linna)valitsus\w*\b",
            r"\bkalmistu\s+haldaja\w*\b",
            r"\bhoolekogu\w*\b",
            r"\bametiasutus\w*\b",
            r"\b(?:vahendus)?asutus\w*\b",
            r"\bvalimiskomisjon\w*\b",
        )
    ),
    "official": tuple(
        re.compile(p, re.IGNORECASE | re.UNICODE)
        for p in (
            r"\bminister\w*\b",
            r"\bametiisik\w*\b",
            r"\bametnik\w*\b",
            r"\bprokurör\w*\b",
            r"\bkohtutäitur\w*\b",
            r"\bkohtunik\w*\b",
            r"\bnotar\w*\b",
            r"\bdirektor\w*\b",
            r"\besimees\w*\b",
            r"\bjuhataja\w*\b",
            r"\bjärelevalveametnik\w*\b",
            r"\bteenistuja\w*\b",
            r"\bülem\w*\b",
            r"\b(?:kaadri)?kaitseväelane\w*\b",
            r"\bkomisjoni\s+lii\w*\b",
            r"\bosakonnajuhataja\w*\b",
            r"\bvahitüürimees\w*\b",
        )
    ),
    "ngo": tuple(
        re.compile(p, re.IGNORECASE | re.UNICODE)
        for p in (
            r"\bmittetulundusühing\w*\b",
            r"\bmtü\b",
            r"\bsihtasutus\w*\b",
            r"\bfond\w*\b",
            r"\bühing\w*\b",
            # #576: ``selts*`` must NOT swallow ``seltsing`` — a civil-law
            # partnership (VÕS §580 jj), not an NGO. Negative lookahead excludes
            # the ``seltsing`` inflections while keeping ``selts``/``seltsi``
            # (society/association) and ``seltsid``.
            r"\bselts(?!ing)\w*\b",
        )
    ),
}

# Weak generic citizen cues. These fire on almost any provision that
# mentions a person, including handler/business duties ("X on isik, kes…").
# #460: if citizen evidence is only these AND a business/public_body cue
# also matched, drop citizen. Do not assign citizen by absence-of-others.
_CITIZEN_WEAK_SOURCES: tuple[str, ...] = (
    r"(?<!juriidiline\s)(?<!juriidilise\s)"
    r"\bisik(?:u|ul|ule|uga|ust|uks|ut|uid|ud|ute)?\b",
    r"\bkasutaja\w*\b",
    r"\bvaldaja\w*\b",
    r"\b(?:kinnistu|korteri|eluruumi|hauaplatsi)?\s*omanik\w*\b",
)
_CITIZEN_WEAK_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE | re.UNICODE) for p in _CITIZEN_WEAK_SOURCES
)
_CITIZEN_WEAK_PATTERN_TEXTS = {p.pattern for p in _CITIZEN_WEAK_PATTERNS}
_CITIZEN_STRONG_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    p
    for p in TARGET_GROUP_PATTERNS["citizen"]
    if p.pattern not in _CITIZEN_WEAK_PATTERN_TEXTS
)


def target_group_iri(token: str) -> str:
    """Map a legacy enum token (or already-compact IRI) to ``estleg:TargetGroup_*``."""
    raw = (token or "").strip()
    if raw.startswith(_FULL_IRI_PREFIX):
        raw = "estleg:" + raw[len(_FULL_IRI_PREFIX) :]
    if raw in TARGET_GROUP_IRI:
        return TARGET_GROUP_IRI[raw]
    if raw in _IRI_TO_TOKEN:
        return raw
    raise ValueError(f"unknown target-group token: {token!r}")


def normalize_target_group_value(value: object) -> list[str]:
    """Upgrade legacy string tokens to compact IRIs; idempotent if already IRIs.

    Accepts a single string, a JSON-LD ``{"@id": …}`` object, or a list of
    either. Unknown values are skipped. Result order follows
    ``TARGET_GROUP_ORDER``.
    """
    seen: set[str] = set()
    for item in _as_group_list(value):
        try:
            seen.add(target_group_iri(item))
        except ValueError:
            continue
    return [
        TARGET_GROUP_IRI[token]
        for token in TARGET_GROUP_ORDER
        if TARGET_GROUP_IRI[token] in seen
    ]


def emit_target_group(groups: list[str]) -> list[dict[str, str]]:
    """JSON-LD object-ref list written onto ``estleg:targetGroup``."""
    return [{"@id": iri} for iri in normalize_target_group_value(groups)]


def _load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        print(f"  WARN: cannot load {path}: {exc}")
        return None


def _as_group_list(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        iri = value.get("@id")
        if isinstance(iri, str) and iri:
            return [iri]
        text = jsonld_text(value)
        return [text] if text else []
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(_as_group_list(item))
        return out
    return []


def _has_strong_citizen_cue(text: str) -> bool:
    return any(pattern.search(text) for pattern in _CITIZEN_STRONG_PATTERNS)


def classify_text(text: str) -> list[str]:
    """Return target-group enum tokens detected in text, in stable order."""
    if not text:
        return []
    found = {
        group
        for group, patterns in TARGET_GROUP_PATTERNS.items()
        if any(pattern.search(text) for pattern in patterns)
    }
    # #460: do not keep citizen when the only citizen hit is a weak generic
    # (bare isik / kasutaja / valdaja / omanik) and a business or public_body
    # cue is also present. Absence of other groups is not itself a citizen hit.
    if (
        "citizen" in found
        and ("business" in found or "public_body" in found)
        and not _has_strong_citizen_cue(text)
    ):
        found.discard("citizen")
    return [group for group in TARGET_GROUP_ORDER if group in found]


def duty_holder_tokens(value: object) -> tuple[list[str], bool]:
    """Map a dutyHolder value to target-group tokens.

    Returns ``(tokens, present)``. ``present`` is true when the node carried
    any dutyHolder value (IRI, phrase, or list). Tokens come from compact
    TargetGroup IRIs when those are already stored, otherwise from
    ``classify_text`` over leftover phrases. Unmapped phrases yield
    ``([], True)`` so callers can drop them (#460).
    """
    if value in (None, "", [], {}):
        return [], False
    iris = normalize_target_group_value(value)
    if iris:
        return [_IRI_TO_TOKEN[iri] for iri in iris], True
    phrases: list[str] = []
    if isinstance(value, list):
        phrases = [jsonld_text(item) for item in value]
    else:
        phrases = [jsonld_text(value)]
    phrases = [phrase for phrase in phrases if phrase]
    if not phrases:
        return [], True
    found: set[str] = set()
    for phrase in phrases:
        found.update(classify_text(phrase))
    return [group for group in TARGET_GROUP_ORDER if group in found], True


def upgrade_node_duty_holder(node: dict) -> bool:
    """Rewrite ``estleg:dutyHolder`` to TargetGroup IRIs, or drop junk.

    Returns True when the node changed. Already-correct IRI lists are
    left untouched (idempotent).
    """
    if "estleg:dutyHolder" not in node:
        return False
    tokens, present = duty_holder_tokens(node.get("estleg:dutyHolder"))
    if not present:
        return False
    if not tokens:
        del node["estleg:dutyHolder"]
        return True
    new = emit_target_group(tokens)
    if node.get("estleg:dutyHolder") == new:
        return False
    node["estleg:dutyHolder"] = new
    return True


def classify_node(node: dict) -> tuple[list[str], bool]:
    """Classify one provision node.

    Returns ``(groups, used_duty_holder)`` where ``used_duty_holder`` is
    true when a dutyHolder value was present (IRI or phrase), whether or
    not it classified.
    """
    duty_tokens, duty_present = duty_holder_tokens(node.get("estleg:dutyHolder"))
    summary = jsonld_text(node.get("estleg:summary", ""))
    legal_text = jsonld_text(node.get("estleg:legalText", ""))
    label = jsonld_text(node.get("rdfs:label", ""))

    # Prefer the dutyHolder-derived group when the dutyHolder value itself
    # classifies: the duty holder is the authoritative subject of the
    # provision, so scanning (and unioning) the body text would only
    # over-broaden the result with incidental mentions (#277). Fall back to the
    # body cues only when there is no dutyHolder, or it does not classify.
    groups = set(duty_tokens)
    if not groups:
        body_text = " ".join(part for part in (label, summary, legal_text) if part)
        if body_text:
            groups.update(classify_text(body_text))
            # #576: a body-text union has no addressee anchoring, so incidental
            # mentions pile up (79 provisions carried ALL 5 groups, 874 carried
            # ≥4). When the fallback fires and yields an implausibly broad set,
            # keep only the two highest-priority addressees rather than asserting
            # the provision targets everyone. The dutyHolder path (the
            # authoritative subject) is never capped.
            if len(groups) >= 3:
                groups = set(
                    [g for g in TARGET_GROUP_ORDER if g in groups][:2]
                )

    return [group for group in TARGET_GROUP_ORDER if group in groups], duty_present


def classify_files(
    files: list[Path],
    *,
    report_path: Path,
    write: bool = True,
) -> dict:
    counts = Counter()
    group_counts = Counter()
    unmapped_duty_holders = Counter()
    changed_files = 0

    for path in files:
        counts["files_scanned"] += 1
        doc = _load_json(path)
        if not isinstance(doc, dict) or not isinstance(doc.get("@graph"), list):
            counts["files_skipped"] += 1
            continue

        changed = False
        for node in doc["@graph"]:
            if not isinstance(node, dict):
                continue
            types = node.get("@type") or []
            if isinstance(types, str):
                types = [types]
            if "estleg:paragrahv" not in node and "estleg:Subsection" not in types:
                continue
            counts["provisions_scanned"] += 1
            old_iris = normalize_target_group_value(node.get("estleg:targetGroup"))
            groups, had_duty_holder = classify_node(node)
            new_iris = [target_group_iri(group) for group in groups]

            if had_duty_holder:
                counts["provisions_with_dutyHolder"] += 1
                if groups:
                    counts["dutyHolder_classified"] += 1
                else:
                    duty = jsonld_text(node.get("estleg:dutyHolder", "")).strip()
                    if not duty:
                        raw = node.get("estleg:dutyHolder")
                        if isinstance(raw, dict):
                            duty = str(raw.get("@id") or "")
                        elif isinstance(raw, str):
                            duty = raw
                    if duty:
                        unmapped_duty_holders[duty] += 1

            if groups:
                counts["provisions_classified"] += 1
                if len(groups) > 1:
                    counts["multi_valued_provisions"] += 1
                group_counts.update(groups)
                if old_iris != new_iris:
                    node["estleg:targetGroup"] = emit_target_group(groups)
                    changed = True
            elif "estleg:targetGroup" in node:
                node.pop("estleg:targetGroup", None)
                changed = True
            confidence = heuristic_confidence_for_node(node)
            if confidence and stamp_assertion_confidence(node, confidence):
                changed = True

        if changed:
            changed_files += 1
            if write:
                save_json(path, doc)

    duty_total = counts["provisions_with_dutyHolder"]
    report = {
        "generated": f"{BUILD_EVALUATION_DATE}T00:00:00+00:00",  # #295: pinned deterministic stamp (no wall-clock churn in tracked artifact)
        "summary": {
            **dict(counts),
            "files_changed": changed_files,
            "dutyHolder_coverage": (
                round(counts["dutyHolder_classified"] / duty_total, 4)
                if duty_total
                else None
            ),
        },
        "by_target_group": dict(group_counts),
        "top_unmapped_duty_holders": [
            {"value": value, "count": count}
            for value, count in unmapped_duty_holders.most_common(50)
        ],
    }
    if write:
        save_json(report_path, report)
    return report


def _is_lfs_pointer(path: Path) -> bool:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return fh.read(len(_LFS_POINTER_PREFIX)).startswith(_LFS_POINTER_PREFIX)
    except OSError:
        return False


def upgrade_node_target_group_iris(node: dict) -> bool:
    """Replace legacy string tokens on one node with IRI object refs.

    Does not reclassify. Returns True iff the node was mutated.
    """
    if "estleg:targetGroup" not in node:
        return False
    raw = node["estleg:targetGroup"]
    iris = normalize_target_group_value(raw)
    if not iris:
        return False
    emitted = [{"@id": iri} for iri in iris]
    if raw == emitted:
        return False
    node["estleg:targetGroup"] = emitted
    return True


_LEGACY_JSON_TOKENS: tuple[str, ...] = (
    '"citizen"',
    '"business"',
    '"public_body"',
    '"official"',
    '"ngo"',
)
_TARGET_GROUP_TOKEN_LINE_RE = re.compile(
    r'^(\s*)"(citizen|business|public_body|official|ngo)"(,?)\s*$'
)


def upgrade_target_group_iris_files(
    files: list[Path],
    *,
    write: bool = True,
) -> dict[str, int]:
    """Mechanical string→IRI remint of ``estleg:targetGroup`` on peeps (#460)."""
    stats = {
        "files_scanned": 0,
        "files_skipped": 0,
        "files_changed": 0,
        "nodes_changed": 0,
    }
    for path in files:
        if _is_lfs_pointer(path):
            stats["files_skipped"] += 1
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            stats["files_skipped"] += 1
            continue
        if not any(tok in text for tok in _LEGACY_JSON_TOKENS):
            stats["files_skipped"] += 1
            continue
        try:
            doc = json.loads(text)
        except ValueError:
            stats["files_skipped"] += 1
            continue
        if not isinstance(doc, dict) or not isinstance(doc.get("@graph"), list):
            stats["files_skipped"] += 1
            continue
        stats["files_scanned"] += 1
        file_changed = False
        for node in doc["@graph"]:
            if not isinstance(node, dict):
                continue
            if upgrade_node_target_group_iris(node):
                stats["nodes_changed"] += 1
                file_changed = True
        if file_changed:
            stats["files_changed"] += 1
            if write:
                save_json(path, doc)
    return stats


def upgrade_jsonld_target_group_stream(
    src: Path,
    dest: Path | None = None,
) -> dict[str, int]:
    """Line-stream remint of pretty-printed ``estleg:targetGroup`` arrays.

    Avoids ``json.loads`` on the 265 MB combined graph. Idempotent when the
    array already holds ``{"@id": "estleg:TargetGroup_*"}`` objects.
    """
    dest = dest or src
    tmp = dest.with_name(dest.name + ".tg-iri.tmp")
    stats = {"blocks": 0, "tokens_replaced": 0, "lines": 0}
    in_target_group = False
    try:
        with src.open(encoding="utf-8") as handle, tmp.open(
            "w", encoding="utf-8", newline="\n"
        ) as out:
            for line in handle:
                stats["lines"] += 1
                stripped = line.lstrip()
                if not in_target_group:
                    if stripped.startswith('"estleg:targetGroup"'):
                        in_target_group = True
                        stats["blocks"] += 1
                    out.write(line)
                    continue
                match = _TARGET_GROUP_TOKEN_LINE_RE.match(line)
                if match:
                    indent, token, comma = match.groups()
                    iri = TARGET_GROUP_IRI[token]
                    out.write(f"{indent}{{\n")
                    out.write(f'{indent}  "@id": "{iri}"\n')
                    out.write(f"{indent}}}{comma}\n")
                    stats["tokens_replaced"] += 1
                    continue
                if stripped.startswith("]"):
                    in_target_group = False
                out.write(line)
        tmp.replace(dest)
    except Exception:
        if tmp.exists():
            tmp.unlink()
        raise
    return stats


_DUTY_HOLDER_STRING_LINE_RE = re.compile(
    r'^(\s*)"estleg:dutyHolder":\s*"(.*)"(\s*,?)\s*$'
)


def stamp_confidence_files(files: list[Path], *, write: bool = True) -> dict[str, int]:
    """Stamp ``estleg:assertionConfidence`` on existing classifier outputs."""
    stats = Counter()
    markers = (
        '"estleg:normativeType"',
        '"estleg:targetGroup"',
        '"dcterms:subject"',
    )
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            stats["files_skipped"] += 1
            continue
        if not any(marker in text for marker in markers):
            stats["files_skipped"] += 1
            continue
        try:
            doc = json.loads(text)
        except ValueError:
            stats["files_skipped"] += 1
            continue
        if not isinstance(doc, dict) or not isinstance(doc.get("@graph"), list):
            stats["files_skipped"] += 1
            continue
        stats["files_scanned"] += 1
        file_changed = False
        for node in doc["@graph"]:
            if not isinstance(node, dict):
                continue
            value = heuristic_confidence_for_node(node)
            if value and stamp_assertion_confidence(node, value):
                stats["nodes_changed"] += 1
                file_changed = True
        if file_changed:
            stats["files_changed"] += 1
            if write:
                save_json(path, doc)
    return stats


def upgrade_duty_holder_files(files: list[Path], *, write: bool = True) -> dict[str, int]:
    """Remint or drop free-string ``estleg:dutyHolder`` values on peeps."""
    stats = Counter()
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            stats["files_skipped"] += 1
            continue
        if '"estleg:dutyHolder"' not in text:
            stats["files_skipped"] += 1
            continue
        try:
            doc = json.loads(text)
        except ValueError:
            stats["files_skipped"] += 1
            continue
        if not isinstance(doc, dict) or not isinstance(doc.get("@graph"), list):
            stats["files_skipped"] += 1
            continue
        stats["files_scanned"] += 1
        file_changed = False
        for node in doc["@graph"]:
            if not isinstance(node, dict):
                continue
            if upgrade_node_duty_holder(node):
                stats["nodes_changed"] += 1
                file_changed = True
        if file_changed:
            stats["files_changed"] += 1
            if write:
                save_json(path, doc)
    return stats


def upgrade_jsonld_duty_holder_stream(
    src: Path,
    dest: Path | None = None,
) -> dict[str, int]:
    """Line-stream remint of pretty-printed ``estleg:dutyHolder`` strings."""
    dest = dest or src
    tmp = dest.with_name(dest.name + ".dh-iri.tmp")
    stats = {"lines": 0, "rewritten": 0, "dropped": 0}
    pending: str | None = None

    def flush(line: str | None) -> None:
        nonlocal pending
        if pending is not None:
            out.write(pending)
        pending = line

    try:
        with src.open(encoding="utf-8") as handle, tmp.open(
            "w", encoding="utf-8", newline="\n"
        ) as out:
            for line in handle:
                stats["lines"] += 1
                match = _DUTY_HOLDER_STRING_LINE_RE.match(line)
                if not match:
                    flush(line)
                    continue
                indent, raw, comma = match.groups()
                tokens, _present = duty_holder_tokens(raw)
                if not tokens:
                    stats["dropped"] += 1
                    if comma.strip() != ",":
                        # Last property: strip the previous line's trailing comma.
                        if pending is not None and pending.rstrip().endswith(","):
                            pending = pending.rstrip()[:-1] + pending[len(pending.rstrip()) :]
                    continue
                iris = [target_group_iri(token) for token in tokens]
                chunk = [f'{indent}"estleg:dutyHolder": [\n']
                for index, iri in enumerate(iris):
                    tail = "," if index < len(iris) - 1 else ""
                    chunk.append(f"{indent}  {{\n")
                    chunk.append(f'{indent}    "@id": "{iri}"\n')
                    chunk.append(f"{indent}  }}{tail}\n")
                chunk.append(f"{indent}]{comma}\n")
                flush("".join(chunk))
                stats["rewritten"] += 1
            flush(None)
        tmp.replace(dest)
    except Exception:
        if tmp.exists():
            tmp.unlink()
        raise
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="classify and report without rewriting corpus files",
    )
    parser.add_argument(
        "--exclude-kov",
        action="store_true",
        help="skip KOV regulation peeps",
    )
    parser.add_argument(
        "--upgrade-iris",
        action="store_true",
        help="replace legacy string tokens with TargetGroup IRIs without reclassifying",
    )
    parser.add_argument(
        "--upgrade-duty-holders",
        action="store_true",
        help="map dutyHolder phrases to TargetGroup IRIs or drop unmapped junk (#460)",
    )
    parser.add_argument(
        "--stamp-confidence",
        action="store_true",
        help="write estleg:assertionConfidence on classifier outputs (#456)",
    )
    args = parser.parse_args(argv)

    files = iter_peep_files(include_kov=not args.exclude_kov)
    if args.stamp_confidence:
        stats = stamp_confidence_files(files, write=not args.dry_run)
        print("Estonian Legal Ontology - assertionConfidence stamp (#456)")
        print(f"  Files scanned: {stats['files_scanned']}")
        print(f"  Files changed: {stats['files_changed']}")
        print(f"  Nodes changed: {stats['nodes_changed']}")
        return 0
    if args.upgrade_duty_holders:
        stats = upgrade_duty_holder_files(files, write=not args.dry_run)
        print("Estonian Legal Ontology - dutyHolder IRI remint (#460)")
        print(f"  Files scanned: {stats['files_scanned']}")
        print(f"  Files changed: {stats['files_changed']}")
        print(f"  Nodes changed: {stats['nodes_changed']}")
        return 0
    if args.upgrade_iris:
        stats = upgrade_target_group_iris_files(files, write=not args.dry_run)
        print("Estonian Legal Ontology - Target Group IRI remint (#460)")
        print(f"  Files scanned: {stats['files_scanned']}")
        print(f"  Files changed: {stats['files_changed']}")
        print(f"  Nodes changed: {stats['nodes_changed']}")
        return 0
    report = classify_files(
        files,
        report_path=KRR_DIR / "reports" / "target_group_report.json",
        write=not args.dry_run,
    )
    summary = report["summary"]
    print("Estonian Legal Ontology - Target Group Classification")
    print(f"  Files scanned: {summary.get('files_scanned', 0)}")
    print(f"  Files changed: {summary.get('files_changed', 0)}")
    print(f"  Provisions classified: {summary.get('provisions_classified', 0)}")
    print(f"  dutyHolder coverage: {summary.get('dutyHolder_coverage')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

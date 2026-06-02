#!/usr/bin/env python3
"""Classify LegalProvision target groups for administrative-burden analysis.

The classifier is deliberately deterministic. It first maps explicit
``estleg:dutyHolder`` literals, then falls back to deontic text cues in the
provision summary/full text. Unmapped duty holders are surfaced in the report
for manual review instead of hidden behind an external LLM dependency.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from estleg_common import (
    BUILD_EVALUATION_DATE,
    KRR_DIR,
    iter_peep_files,
    jsonld_text,
)

TARGET_GROUP_ORDER: tuple[str, ...] = (
    "citizen",
    "business",
    "public_body",
    "official",
    "ngo",
)

TARGET_GROUP_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "business": tuple(
        re.compile(p, re.IGNORECASE | re.UNICODE)
        for p in (
            r"\btööandja\w*\b",
            r"\bettevõtja\w*\b",
            r"\bettevõt(?:e|ja)\w*\b",
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
            r"\bkäitleja\w*\b",
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
            r"\bjuhatus\w*\b",
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
            r"\bkool\w*\b",
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
            r"\bselts\w*\b",
        )
    ),
}


def _load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        print(f"  WARN: cannot load {path}: {exc}")
        return None


def _save_json(path: Path, doc: dict) -> None:
    path.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _as_group_list(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        text = jsonld_text(value)
        return [text] if text else []
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(_as_group_list(item))
        return out
    return []


def classify_text(text: str) -> list[str]:
    """Return target-group enum values detected in text, in stable order."""
    if not text:
        return []
    found = {
        group
        for group, patterns in TARGET_GROUP_PATTERNS.items()
        if any(pattern.search(text) for pattern in patterns)
    }
    return [group for group in TARGET_GROUP_ORDER if group in found]


def classify_node(node: dict) -> tuple[list[str], bool]:
    """Classify one provision node.

    Returns ``(groups, used_duty_holder)`` where ``used_duty_holder`` only
    records whether the provision had a dutyHolder literal available for the
    first-pass dictionary mapping.
    """
    duty_text = jsonld_text(node.get("estleg:dutyHolder", ""))
    summary = jsonld_text(node.get("estleg:summary", ""))
    legal_text = jsonld_text(node.get("estleg:legalText", ""))
    label = jsonld_text(node.get("rdfs:label", ""))

    # Prefer the dutyHolder-derived group when the dutyHolder literal itself
    # classifies: the duty holder is the authoritative subject of the
    # provision, so scanning (and unioning) the body text would only
    # over-broaden the result with incidental mentions (#277). Fall back to the
    # body cues only when there is no dutyHolder, or it does not classify.
    groups = set(classify_text(duty_text))
    if not groups:
        body_text = " ".join(part for part in (label, summary, legal_text) if part)
        if body_text:
            groups.update(classify_text(body_text))

    return [group for group in TARGET_GROUP_ORDER if group in groups], bool(duty_text)


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
            if not isinstance(node, dict) or "estleg:paragrahv" not in node:
                continue
            counts["provisions_scanned"] += 1
            old_groups = _as_group_list(node.get("estleg:targetGroup"))
            groups, had_duty_holder = classify_node(node)

            if had_duty_holder:
                counts["provisions_with_dutyHolder"] += 1
                if groups:
                    counts["dutyHolder_classified"] += 1
                else:
                    duty = jsonld_text(node.get("estleg:dutyHolder", "")).strip()
                    if duty:
                        unmapped_duty_holders[duty] += 1

            if groups:
                counts["provisions_classified"] += 1
                if len(groups) > 1:
                    counts["multi_valued_provisions"] += 1
                group_counts.update(groups)
                if old_groups != groups:
                    node["estleg:targetGroup"] = groups
                    changed = True
            elif "estleg:targetGroup" in node:
                node.pop("estleg:targetGroup", None)
                changed = True

        if changed:
            changed_files += 1
            if write:
                _save_json(path, doc)

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
        _save_json(report_path, report)
    return report


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
    args = parser.parse_args(argv)

    files = iter_peep_files(include_kov=not args.exclude_kov)
    report = classify_files(
        files,
        report_path=KRR_DIR / "target_group_report.json",
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

#!/usr/bin/env python3
"""Retarget VÕS harmonisation off the torts Part onto subject-correct Parts (#578b/#631).

`generate_harmonisation_links` resolved a multi-Part act's target from
`law_files[0]`; because `transposition_mapping.json` string-sorts `law_files`,
`osa10` sorts first, so **every** Võlaõigusseadus consumer/contract directive
landed its `harmonises` edge on `volaoigusseadus_Osa10_1005_1067` —
*LEPINGUVÄLISED KOHUSTUSED* (non-contractual obligations / torts), which none of
them transpose into. A second mechanism *smeared* the forward
`harmonisedWith` / `transposesDirective` onto all 9 VÕS Parts.

This surgical, offline, deterministic post-process retargets each affected
directive to its subject-correct Part (or drops the VÕS edge where VÕS is not the
transposing vehicle and other acts already carry it), across all four artifacts
the `validate_harmonisation_symmetry` gate keeps consistent:

  1. law `volaoigusseadus_osa*_peep.json` — `harmonisedWith` + `transposesDirective`
  2. `harmonisation/harmonisation_by_directive/harm_<celex>.json` — `harmonises`
  3. `harmonisation/harmonisation_report.json` — `harmonisation_entries[].estonian_laws`
  4. `transposition_mapping.json` — the VÕS row's `law_files` (narrowed to the
     correct Part so a regen reproduces the single-Part link, not the smear)

The Part assignments are the maintainer's subject-matter determination (a
transposition fact); the unambiguous core is that the **torts Part is wrong for
every one of these directives**. High-confidence special-Part assignments are used
where the matter is clearly located (consumer sales → sales Part; timeshare → use
Part; package travel → services Part); the general Part (ÜLDOSA, §1–207) is used
as a safe anchor where the precise special Part is less certain; the VÕS edge is
dropped where the directive is plainly not a VÕS-obligations matter.

Idempotent; `--dry-run` reports without writing; atomic writes via `save_json`.
"""

from __future__ import annotations

import argparse
import json

from estleg.estleg_common import KRR_DIR, save_json

# CELEX -> the single subject-correct VÕS Part IRI to carry the link, or None to
# drop the VÕS edge entirely (other mapped acts remain the transposing vehicle).
TARGET: dict[str, str | None] = {
    "31990L0314": "estleg:volaoigusseadus_Osa8_619_916",   # Package Travel -> services (reisileping)
    "31993L0013": "estleg:volaoigusseadus_Osa1_1_207",     # Unfair Terms -> general (tüüptingimused)
    "31997L0007": "estleg:volaoigusseadus_Osa1_1_207",     # Distance Contracts -> general (withdrawal)
    "31999L0044": "estleg:volaoigusseadus_Osa2_208_270",   # Consumer Sales -> sales (müügileping)
    "32002L0065": "estleg:volaoigusseadus_Osa1_1_207",     # Distance Fin. Marketing -> general
    "32008L0048": "estleg:volaoigusseadus_Osa1_1_207",     # Consumer Credit -> general (+ TarbKS/VOSRS _Map)
    "32008L0122": "estleg:volaoigusseadus_Osa3_271_421",   # Timeshare -> use contracts (kasutusleping)
    "32009L0044": None,                                     # Financial Collateral -> drop VÕS (AÕS + _Map)
    "32009L0052": None,                                     # Employer Sanctions -> drop VÕS (criminal/labour)
}

# The torts Part every affected directive wrongly pointed at.
WRONG_PART = "estleg:volaoigusseadus_Osa10_1005_1067"

# Every VÕS Part IRI -> its peep filename (for the mapping law_files narrowing).
_PART_PEEP = {
    "estleg:volaoigusseadus_Osa1_1_207": "volaoigusseadus_osa1_peep.json",
    "estleg:volaoigusseadus_Osa2_208_270": "volaoigusseadus_osa2_peep.json",
    "estleg:volaoigusseadus_Osa3_271_421": "volaoigusseadus_osa3_peep.json",
    "estleg:volaoigusseadus_Osa8_619_916": "volaoigusseadus_osa8_peep.json",
}

HARM_DIR = KRR_DIR / "harmonisation" / "harmonisation_by_directive"
REPORT = KRR_DIR / "harmonisation" / "harmonisation_report.json"
MAPPING = KRR_DIR / "reports" / "transposition_mapping.json"


def _is_vos_part(iri: str) -> bool:
    return iri.startswith("estleg:volaoigusseadus_Osa")


def _as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def retarget_harm_file(celex: str, dry: bool) -> bool:
    """harm_<celex>.json: replace every VÕS-Part `harmonises` target with the one
    correct Part (or drop them), leaving non-VÕS targets untouched."""
    path = HARM_DIR / f"harm_{celex}.json"
    if not path.exists():
        return False
    doc = json.loads(path.read_text(encoding="utf-8"))
    target = TARGET[celex]
    changed = False
    for node in doc.get("@graph", []):
        if not isinstance(node, dict) or not node.get("@id", "").startswith("estleg:Harmonisation_"):
            continue
        ids = [x["@id"] for x in _as_list(node.get("estleg:harmonises")) if isinstance(x, dict) and x.get("@id")]
        kept = [i for i in ids if not _is_vos_part(i)]
        if target is not None:
            kept.append(target)
        # de-dup, preserve order
        seen: set[str] = set()
        new = [i for i in kept if not (i in seen or seen.add(i))]
        if new != ids:
            node["estleg:harmonises"] = [{"@id": i} for i in new]
            changed = True
    if changed and not dry:
        save_json(path, doc)
    return changed


def retarget_report(dry: bool) -> int:
    """harmonisation_report.json: set each affected entry's estonian_laws IRI set
    equal to its (post-retarget) harm_<celex>.json harmonises set."""
    if not REPORT.exists():
        return 0
    rep = json.loads(REPORT.read_text(encoding="utf-8"))
    changed = 0
    for entry in rep.get("harmonisation_entries", []):
        cel = entry.get("directive_celex")
        if cel not in TARGET:
            continue
        hf = HARM_DIR / f"harm_{cel}.json"
        if not hf.exists():
            continue
        hdoc = json.loads(hf.read_text(encoding="utf-8"))
        hiris: list[str] = []
        for n in hdoc.get("@graph", []):
            if isinstance(n, dict) and n.get("@id", "").startswith("estleg:Harmonisation_"):
                hiris = [x["@id"] for x in _as_list(n.get("estleg:harmonises")) if isinstance(x, dict) and x.get("@id")]
        existing = [m.get("iri") for m in entry.get("estonian_laws", []) if isinstance(m, dict)]
        if set(existing) != set(hiris):
            # Rebuild estonian_laws preserving prior member metadata where the IRI
            # survives; drop removed IRIs; add the new VÕS Part with a record that
            # carries the law name, matching the other members' shape.
            by_iri = {m.get("iri"): m for m in entry.get("estonian_laws", []) if isinstance(m, dict)}

            def _record(iri: str) -> dict:
                if iri in by_iri:
                    return by_iri[iri]
                rec = {"iri": iri}
                if iri.startswith("estleg:volaoigusseadus_Osa"):
                    rec["name"] = "volaoigusseadus"
                return rec

            entry["estonian_laws"] = [_record(i) for i in hiris]
            changed += 1
    if changed and not dry:
        save_json(REPORT, rep)
    return changed


def retarget_peeps(dry: bool) -> int:
    """volaoigusseadus_osa*_peep.json: keep each affected directive's forward
    edges (harmonisedWith / transposesDirective) ONLY on the correct Part; strip
    the smear from every other Part."""
    changed_files = 0
    for path in sorted(KRR_DIR.glob("volaoigusseadus_osa*_peep.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        file_changed = False
        for node in doc.get("@graph", []):
            if not isinstance(node, dict) or "estleg:Act" not in (node.get("@type") or []):
                continue
            part_iri = node.get("@id")
            for prop, prefix in (("estleg:harmonisedWith", "estleg:Harmonisation_"),
                                 ("estleg:transposesDirective", "estleg:EU_")):
                vals = _as_list(node.get(prop))
                new = []
                for x in vals:
                    if not (isinstance(x, dict) and x.get("@id")):
                        continue
                    cel = x["@id"].replace(prefix, "")
                    if cel in TARGET and TARGET[cel] != part_iri:
                        # this directive does not belong on this Part -> drop
                        continue
                    new.append(x)
                if len(new) != len(vals):
                    if new:
                        node[prop] = new
                    else:
                        node.pop(prop, None)
                    file_changed = True
        if file_changed:
            changed_files += 1
            if not dry:
                save_json(path, doc)
    return changed_files


def narrow_mapping(dry: bool) -> int:
    """transposition_mapping.json: narrow each affected directive's VÕS row
    `law_files` to the single correct Part peep (or empty it when the VÕS edge is
    dropped) so a regen reproduces the corrected link instead of the 9-Part smear."""
    if not MAPPING.exists():
        return 0
    tm = json.loads(MAPPING.read_text(encoding="utf-8"))
    changed = 0
    for m in tm.get("mappings", []):
        cel = m.get("directive_celex")
        if cel not in TARGET:
            continue
        files = m.get("law_files", [])
        # only the VÕS rows (those whose law_files are the volaoigusseadus parts)
        vos_files = [f for f in files if "volaoigusseadus_osa" in f]
        if not vos_files:
            continue
        non_vos = [f for f in files if "volaoigusseadus_osa" not in f]
        target = TARGET[cel]
        new_vos = [_PART_PEEP[target]] if target is not None else []
        new_files = non_vos + new_vos
        if new_files != files:
            m["law_files"] = new_files
            changed += 1
    if changed and not dry:
        save_json(MAPPING, tm)
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Report without writing.")
    args = parser.parse_args(argv)

    print("=" * 70)
    print(f"Retarget VÕS harmonisation off the torts Part (#578b){'  [DRY RUN]' if args.dry_run else ''}")
    print(f"  Wrong Part: {WRONG_PART}")
    print("=" * 70)

    harm_changed = sum(retarget_harm_file(c, args.dry_run) for c in TARGET)
    report_changed = retarget_report(args.dry_run)
    peeps_changed = retarget_peeps(args.dry_run)
    mapping_changed = narrow_mapping(args.dry_run)

    for cel, tgt in TARGET.items():
        print(f"  {cel}: -> {tgt.replace('estleg:volaoigusseadus_', 'VÕS ') if tgt else 'DROP VÕS edge'}")
    print(f"\n  harm files changed:    {harm_changed}")
    print(f"  report entries changed: {report_changed}")
    print(f"  VÕS peep files changed: {peeps_changed}")
    print(f"  mapping rows narrowed:  {mapping_changed}")
    if not args.dry_run and (harm_changed or peeps_changed):
        print("\n  NOTE: rebuild combined + run validate_all (harmonisation symmetry + "
              "transposition gates) and shacl.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

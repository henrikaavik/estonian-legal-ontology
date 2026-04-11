# Compact URI Naming Migration Design

**Issue:** #83 — Adopt compact URI naming scheme for node IDs
**Date:** 2026-04-11
**Status:** Approved

## Problem

57% of law IRIs use long slugified titles (up to 102 chars, truncated mid-word) while 43% already use clean short abbreviations. 153 IRIs hit a hard 102-char ceiling and are truncated. Additional inconsistencies exist: `_Par_` vs `Par`, `Concept_` vs `LegalConcept_`.

**Scope of change:** ~28,590 IRI definitions, ~129,000 references across ~1,300 files.

## Approach: Hybrid (Option C)

Keep the existing `estleg:{Prefix}_{NodeType}_{Id}` pattern. Build a proper abbreviation registry so ALL laws get short prefixes. Fix truncation and normalize inconsistencies. No structural `/` separator changes.

## Abbreviation Registry

A new file `data/law_abbreviations.json` is the single source of truth:

```json
{
  "perekonnaseadus": {
    "abbrev": "PKS",
    "source": "rt_api",
    "title": "Perekonnaseadus"
  },
  "1974_aasta_rahvusvahelise_konventsiooni_...": {
    "abbrev": "RKIM1974",
    "source": "auto",
    "title": "1974. aasta rahvusvahelise konventsiooni..."
  }
}
```

**Key:** filename slug (the `*_peep.json` filename minus `_peep.json`).

**Resolution order:**
1. RT API `lyhend` field (official) — ~270 laws
2. Existing short prefix from current `_Map_2026` IRI — catches already-working 272
3. Auto-derive from title initials + year/disambiguator — remaining ~360
4. Manual override (edit the JSON)

**Auto-derivation algorithm:**
- First letter of each significant word, uppercased
- Skip stop words: "seadus", "seaduse", "seadustik", "aasta", "ja", "ning", "kohta", "vahel", "vahelise", "seaduse", "rakendamise"
- Detect treaties by filename containing "konventsiooni", "lepingu", "protokolli" — append 4-digit year if found
- If result < 3 chars, use first 6 chars of slug instead
- Suffix `_2`, `_3` etc. on collision with existing abbreviation
- Cap at 12 characters
- Transliterate Estonian diacritics before deriving (o->o, a->a, u->u, o->o)

## Canonical IRI Patterns

All IRIs follow: `estleg:{Abbrev}_{NodeType}_{Id}`

| Node Type | Pattern | Example |
|-----------|---------|---------|
| Ontology root | `{Abbrev}_Map_2026` | `estleg:PKS_Map_2026` |
| Provision class | `LegalProvision_{Abbrev}` | `estleg:LegalProvision_PKS` |
| Paragraph | `{Abbrev}_Par_{N}` | `estleg:PKS_Par_12` |
| Topic cluster | `Cluster_{Abbrev}_{Label}` | `estleg:Cluster_PKS_Abielu` |
| Concept scheme | `{Abbrev}_TopicScheme` | `estleg:PKS_TopicScheme` |
| Chapter | `{Abbrev}_Chapter_{N}` | `estleg:PKS_Chapter_1` |
| Division | `{Abbrev}_Division_{N}_{M}` | `estleg:PKS_Division_1_1` |
| Legal concept | `Concept_{Label}` | `estleg:Concept_Abielu` |
| Part cluster | `Cluster_{Abbrev}_{N}_Part` | `estleg:Cluster_AOS_1_Part` |

**What changes:**
- ~360 long-slug prefixes → short abbreviations
- 153 truncated IRIs → proper short prefixes
- ~120 VOS `Par164` → `Par_164` (add underscore)
- ~30 `LegalConcept_*` → `Concept_*`

**What does NOT change:**
- `estleg:` namespace
- `_Par_`, `_Map_`, `Cluster_`, `_Chapter_`, `_Division_` markers
- Any IRI already using a short abbreviation correctly

Roughly 35-40% of IRIs change, not 100%.

## Migration Script

Single script `scripts/migrate_uris.py` with three modes:

```
python3 scripts/migrate_uris.py build-registry   # Phase 1
python3 scripts/migrate_uris.py dry-run           # Phase 2
python3 scripts/migrate_uris.py apply             # Phase 3
```

### Phase 1: `build-registry`
- Reads all `*_peep.json` to extract current ontology IRI prefixes
- Queries cached RT XML / INDEX.json for official `lyhend`
- Auto-derives for laws without official abbreviations
- Detects and resolves collisions
- Writes `data/law_abbreviations.json`
- Output: registry + summary

### Phase 2: `dry-run`
- Loads registry, scans ALL files, builds complete rename map `old_iri → new_iri`
- Also maps inconsistency fixes (`VOS_Par164 → VOS_Par_164`, `LegalConcept_ → Concept_`)
- Reports: total renames, per-file counts, dangling refs, collision check
- Scans `.py` files for hardcoded `estleg:` literals
- Writes `data/uri_migration_report.json`
- **Zero files modified**

### Phase 3: `apply`
- Requires `data/uri_migration_report.json` to exist (forces dry-run first)
- Refuses to run if dry-run detected collisions
- Loads rename map, walks all files, substitutes IRIs in `@id` values and `{"@id": "..."}` references
- Handles string-valued IRI mentions in report JSONs
- Post-migration verification: re-scans all files, confirms zero old IRIs remain, zero new collisions
- Output: summary + verification pass/fail

## File Scope

| Step | Files | Changes |
|------|-------|---------|
| 1 | `data/law_abbreviations.json` | Created |
| 2 | `krr_outputs/*_peep.json` (635) | `@id` definitions + internal refs |
| 3 | `krr_outputs/amendments/*.json` (~376) | IRI references |
| 4 | `krr_outputs/sanctions/*.json` (~162) | IRI references |
| 5 | `krr_outputs/institutions/*.json` (~85) | IRI references |
| 6 | `krr_outputs/concepts/*.jsonld` | Concept unification + refs |
| 7 | `krr_outputs/*.json` (reports) | String IRI mentions |
| 8 | `krr_outputs/*.jsonld` (combined) | All IRI references |
| 9 | `krr_outputs/riigikohus/*.json` | Court decision refs |
| 10 | `krr_outputs/eurlex/*.json` | EU legislation refs |
| 11 | `krr_outputs/eelnoud/*.json` | Draft legislation refs |
| 12 | `scripts/generate_all_laws.py` | Use registry for `_unique_prefix()` |
| 13 | `scripts/fix_duplicate_ids.py` | Update hardcoded patterns |
| 14 | `shacl/estonian_legal_shapes.ttl` | Update IRI references |
| 15 | `docs/SCHEMA_REFERENCE.md`, `docs/API_GUIDE.md` | Update examples |

**Post-migration generator update:** `generate_all_laws.py` reads registry for future runs, falling back to `_unique_prefix()` for new laws.

## Risk Mitigation

**Pre-migration:** clean git tree, `validate_all.py` passes, registry reviewed, dry-run report reviewed.

**During migration:** single atomic operation, post-verification scan built in.

**Rollback:** `git checkout .` — instant revert. No external state.

| Risk | Mitigation |
|------|-----------|
| Abbreviation collision | Dry-run detects, refuses to proceed |
| Missed reference substitution | Post-scan checks every `estleg:` string against rename map |
| Scripts hardcode old IRIs | Phase 2 scans all `.py` files for `estleg:` literals |
| SHACL references stale IRIs | Included in file scope |
| `combined_ontology.jsonld` too large | Stream-process with string replacement |
| Future runs produce old IRIs | Generator updated to use registry |

## Commit Strategy

- **Commit 1:** `"Add law abbreviation registry for URI migration"` (Phase 1 output)
- **Commit 2:** `"Migrate IRIs to compact abbreviation-based naming (#83)"` (Phase 3 output + generator update)

Two commits total. Clean bisect history.

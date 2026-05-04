# KOV Integration Layer 2b — Cross-references + Inverse Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add preamble citation extraction (`issuedUnder`, `implementsCitation` reified `Citation` instances) and a `implementedBy` / `implementedByCount` inverse projection to the KOV act corpus, plus KOV-aware body-text scoping rules. This is the second of three sub-PRs in the Layer 2 split.

**Architecture:** A new act-level pass scans `estleg:preambleText` on every `MunicipalRegulation`/`NationalRegulation`/`Law` peep file and parses citations using a corpus-aware regex bank that handles real Estonian variation (`§` and `paragrahv`, `lg`/`lõike`/`lõikest`/`lõigete`, `p`/`punkti`/`punkt`, multiword law names like `kohaliku omavalitsuse korralduse seaduse`, both DD.MM.YYYY and `19. detsembri 2019. a` date forms, `määruse nr` and `määrusega nr`). Citations resolve via canonical lookups built once at startup: `genitive_law_name → act_iri`, `genitive_law_name → {par_nr → provision_iri}`, and `(issuer_label, adoption_date, act_number) → regulation_act_iri` keyed by `vastuvoetud/aktikuupaev` from the source XML (the corpus has zero `estleg:adoptionDate` fields — they live only in XML). `issuedUnder` always points to act-level IRIs (e.g. `estleg:AS_Map_2026`, never bare prefix `estleg:AS`); paragraph-level provision detail rides on `Citation.citationTarget`. Body-text references in KOV files use `enactedByMunicipality` first, then issuer body type, then act number — global fallback only on a unique high-confidence match. The inverse generator gains `implementedBy` (filtered projection from preamble enabling-act links — semantically distinct from the existing `referencedBy` body-text inverse) and `implementedByCount` aggregate.

**Tech Stack:** Python 3.11+, pytest, JSON-LD, SHACL (pyshacl), rdflib. Builds on Layer 2a's `kov_pipeline_coverage`, `build_globalid_xml_lookup`, `pair_peep_with_xml`, and Citation-class declarations.

**Spec reference:** `docs/superpowers/specs/2026-05-02-kov-integration-design.md` (Layer 2 — `extract_cross_references` and `generate_inverse_references` sections).
**Layer 2a reference:** `docs/superpowers/plans/2026-05-03-kov-integration-layer2a.md`.

---

## Crisp 2b invariant

After 2b lands:

1. **Preamble citations emit triples.** Every KOV act preamble citation that resolves to a known act produces an `estleg:issuedUnder` triple from the act node to the **enabling act-level IRI** (e.g. `estleg:AS_Map_2026`, never bare prefix). Citations carrying paragraph and/or `lg`/`p` detail also produce one `estleg:implementsCitation` reified `estleg:Citation` node per citation, with `citationTarget` (provision IRI when § is present, else act IRI), `citationDetail` (lg/p literal), and `citationText` (original substring) populated.
2. **KOV body-text scoping is municipality-first.** When a KOV act's body text references `Tallinna Linnavolikogu määruse nr 15 § 4` or `vallavolikogu määrus nr 12`, the resolver scopes the search to the same `enactedByMunicipality` first, narrows by issuer body type, then act number. Cross-municipality false matches are skipped.
3. **Inverse generator emits new properties.** `generate_inverse_references` produces `estleg:implementedBy` (filtered projection — only from `issuedUnder` + `Citation.citationTarget`, NOT from body-text `references`) and `estleg:implementedByCount` aggregate on each enabling act and provision. The existing `estleg:referencedBy` continues unchanged.
4. **Both pipelines run KOV by default.** `extract_cross_references` and `generate_inverse_references` are unpinned; their three `# DEFERRED to Layer 2b` comments are removed.
5. **Coverage gates pass.** `extract_cross_references_coverage.json` and `generate_inverse_references_coverage.json` show `files_with_output_kov > 0` and `triples_emitted_kov > 0`. Targeted SHACL has zero new Layer-2 / Layer-1 / uncategorized regressions.

**Not in 2b:** sanctions `enforcedAtLevel`, competence Issuer-binding, court-provision-links KOV resolver — those are Layer 2c.

**Gate B reaches umbrella complete when 2a + 2b + 2c land.**

---

## File Structure

### New files

| Path | Responsibility |
| --- | --- |
| `tests/test_extract_cross_references.py` | Tests for preamble parser, canonical lookups, body-text scoping, main()-integration. |
| `tests/test_generate_inverse_references.py` | Tests for `implementedBy` filtered projection, `implementedByCount`, and the body-text-exclusion E2E invariant. |
| `tests/fixtures/kov_layer2b/sample_kov_preamble_*.txt` | 8 corpus-derived preamble strings exercising real Estonian variation. |
| `tests/fixtures/kov_layer2b/sample_kov_act_with_preamble.json` | KOV act fixture using corpus-shaped IRIs (`estleg:Reg_<id>_Map_2026`). |
| `tests/fixtures/kov_layer2b/sample_law_with_provisions.json` | Law fixture using corpus-shaped IRI (`estleg:KOKS_Map_2026`, not bare `estleg:KOKS`). |
| `tests/fixtures/kov_layer2b/sample_state_reg_with_xml.json` + `.xml` | State reg + matching XML so the `vastuvoetud/aktikuupaev`-based adoption-date lookup builder is exercised. |
| `tests/fixtures/kov_layer2b/sample_kov_with_internal_ref.json` | KOV act whose body text references a same-municipality act. |
| `krr_outputs/reports/kov/extract_cross_references_coverage.json` | Generated. |
| `krr_outputs/reports/kov/generate_inverse_references_coverage.json` | Generated. |

### Modified files

| Path | Change |
| --- | --- |
| `scripts/extract_cross_references.py` | Extend `build_provision_index` to also return `prefix_to_act_iri`. Add `extract_preamble_citations` (corpus-aware regex). Add canonical lookup builders (`build_genitive_to_act_iri`, `build_state_regulation_lookup`, `build_kov_act_lookup`). Add resolvers (`resolve_preamble_citation`, `resolve_kov_internal_act_ref`). Add `extract_kov_act_refs_from_text` for body-text KOV references. Wire preamble pass + body-text scope rule into `main()`. Coverage instrumentation. Unpin the 3 `include_kov=False` call sites. |
| `scripts/generate_inverse_references.py` | Add `collect_preamble_back_references`, `apply_implemented_by`, `compute_implemented_by_count`. Wire into `main()`. Coverage instrumentation. Unpin the 3 `include_kov=False` call sites. |
| `scripts/extract_legal_concepts.py` | (M2 from 2a review) `_triples_kov` accounting now uses `is_kov` alone. |
| `scripts/generate_amendment_history.py` | (M3 from 2a review) Move module-level `mkdir` calls inside `main()`. |
| `scripts/estleg_common.py` | (M4 from 2a review) Add `AGGREGATE_REGISTRY_PREFIXES` constant. |
| `scripts/classify_eurovoc.py` | (M4 cont.) Replace local `REGISTRY_ID_PREFIXES` with import. |
| `tests/test_generate_amendment_history.py` | (M5 from 2a review) Replace trivial `callable()` check with parameterized integration test. |
| `README.md` | (M6 from 2a review) Reword deferred-pipelines line to call out KOV-not-applicable separately. |
| `docs/SCHEMA_REFERENCE.md` | Document Layer 2b's emitted triples (now-populated `issuedUnder`, `implementsCitation`, `implementedBy`, `implementedByCount`). |
| `docs/REGULATIONS_INTEGRATION_PLAN.md` | Mark Layer 2b complete; restate Gate B umbrella status. |
| `CHANGELOG.md` | Entry: Layer 2b deliverables. |

---

## Schema reference (already declared in 2a; this PR populates instances)

| Property | Domain | Range | Cardinality |
| --- | --- | --- | --- |
| `estleg:issuedUnder` | `Act` | `Act` (act-level only — never a provision IRI) | many-per-act |
| `estleg:implementsCitation` | `Act` | `Citation` | many-per-act |
| `estleg:citationTarget` | `Citation` | `LegalProvision` (or `Act` when no § in citation) | exactly one |
| `estleg:citationDetail` | `Citation` | `xsd:string` (e.g. `"lg 1 p 3"`) | optional |
| `estleg:citationText` | `Citation` | `xsd:string` (original substring) | optional but recommended |
| `estleg:implementedBy` | `Act` ∪ `LegalProvision` | `Act` | many-per-target |
| `estleg:implementedByCount` | `Act` ∪ `LegalProvision` | `xsd:integer` | exactly one when present |

**IRI scheme for Citation instances:** `estleg:Citation_<source-act-shortid>_<seq>`, where `<source-act-shortid>` is the source act's `@id` minus `estleg:` (e.g. `Reg_1014955_Map_2026`) and `<seq>` is a 1-based per-act counter.

---

## Task 1: Layer 2a review carryover (M2–M6) + FULLNAME_GENITIVE expansion (M7)

**Files:**
- Modify: `scripts/extract_legal_concepts.py` (M2)
- Modify: `scripts/generate_amendment_history.py` (M3)
- Modify: `scripts/estleg_common.py` (M4 + M7)
- Modify: `scripts/classify_eurovoc.py` (M4)
- Modify: `tests/test_generate_amendment_history.py` (M5)
- Modify: `README.md` (M6)

The Layer 2a final review surfaced 5 minor findings explicitly noted as "addressable in Layer 2b prep". Apply them as a single foundation commit. M7 expands `FULLNAME_GENITIVE` to cover the most common KOV enabling laws — without these, the parser will produce hits but the resolver (Task 5) will return `None` for the bulk of real preambles.

- [ ] **Step 1: M2 — Align `_triples_kov` accounting in `extract_legal_concepts.py`**

```bash
grep -n "_triples_kov\|bridged" scripts/extract_legal_concepts.py | head -10
```

Find the `_triples_kov += 2` line. Replace `if bridged and is_kov:` with `if is_kov:`. The bridge-failure signal stays in `_unresolved`. KOV attribution is "is this peep under regulations/kov/" — same as the other 4 pipelines.

- [ ] **Step 2: M3 — Move `mkdir` calls inside `main()` in `generate_amendment_history.py`**

At module scope, find:

```python
AMENDMENTS_DIR = KRR_DIR / "amendments"
AMENDMENTS_DIR.mkdir(parents=True, exist_ok=True)
EELNOUD_DIR = KRR_DIR / "eelnoud"
```

Keep the `_DIR` definitions at module scope. Move the `.mkdir(...)` calls inside `main()`, near the top:

```python
def main() -> int:
    AMENDMENTS_DIR.mkdir(parents=True, exist_ok=True)
    EELNOUD_DIR.mkdir(parents=True, exist_ok=True)
    # ... rest of main ...
```

- [ ] **Step 3: M4 — Move `REGISTRY_ID_PREFIXES` to `estleg_common.py`**

Add to `scripts/estleg_common.py` (with other module-level constants):

```python
AGGREGATE_REGISTRY_PREFIXES: tuple[str, ...] = (
    "estleg:Municipalities_",
    "estleg:Issuers_",
    "estleg:LegalConcepts_",
    "estleg:Citations_",
    "estleg:Similarity_",
)
```

In `scripts/classify_eurovoc.py`, delete the local `REGISTRY_ID_PREFIXES`. Import from `estleg_common`. Update use sites.

- [ ] **Step 4: M5 — Replace trivial `callable()` test with parameterized integration test**

In `tests/test_generate_amendment_history.py::TestAmendmentHistoryDiscovery::test_imports_globalid_helper_from_temporal`, replace the body with a parameterized integration test that runs `pair_peep_with_xml` from each accessible import path against a small fixture and asserts identical results. The full code is in the plan body — the goal is to prove the DRY rule semantically, not just syntactically.

- [ ] **Step 5: M6 — Reword README**

In `README.md`, find the line near the Layer 2a section that says approximately "The remaining 5 KOV-relevant pipelines ... are deferred to Layers 2b and 2c." Replace with:

```
The remaining 5 KOV-relevant pipelines (cross-references, inverse, sanctions, competence, court-provision-links) are deferred to Layers 2b and 2c, plus 4 KOV-not-applicable pipelines (similarity, draft-impact, harmonisation-links, transposition-mapping) stay pinned indefinitely.
```

- [ ] **Step 6: M7 — Expand `FULLNAME_GENITIVE` and `KNOWN_ABBREVIATIONS` for KOV enabling laws**

Open `scripts/estleg_common.py`. Find the `KNOWN_ABBREVIATIONS` dict (around line 19) and add the following entries. These are the abbreviations for the 20 most-cited KOV enabling laws (sampled from `krr_outputs/regulations/kov/**/preambleText`); without them the resolver chain `genitive → abbrev → full_name → prefix → act_iri` breaks at the second hop.

```python
KNOWN_ABBREVIATIONS: dict[str, str] = {
    # ... existing entries ...
    # M7 additions — KOV enabling laws (preamble corpus, top-20 by frequency)
    "KNS": "Kohanimeseadus",
    "PGS": "Põhikooli- ja gümnaasiumiseadus",
    "KELS_LASTEAS": "Koolieelse lasteasutuse seadus",
    "AluS": "Alusharidusseadus",
    "HuviKS": "Huvikooli seadus",
    "RuumS": "Ruumiandmete seadus",
    "KOFS": "Kohaliku omavalitsuse üksuse finantsjuhtimise seadus",
    "ÜVVKS": "Ühisveevärgi ja -kanalisatsiooni seadus",
    "KaugKS": "Kaugkütteseadus",
    "ElamuS": "Elamuseadus",
    "RaamatS": "Rahvaraamatukogu seadus",
    "RHS_HÄDA": "Hädaolukorra seadus",
    "EhSE": "Ehitusseadustik",
    "JäätS": "Jäätmeseadus",
    "TeeS": "Teeseadus",
    "RahvaS": "Rahvahääletuse seadus",
    "RKVS": "Riigikogu valimise seadus",
    "KOVVS": "Kohaliku omavalitsuse volikogu valimise seadus",
    "RaamatPS": "Raamatupidamise seadus",
    "ÜTS": "Ühistranspordiseadus",
}
```

Then find `FULLNAME_GENITIVE` (around line 71) and add the corresponding genitive forms. Keys are lowercased for case-insensitive matching by Task 5's resolver.

```python
FULLNAME_GENITIVE: dict[str, str] = {
    # ... existing entries ...
    # M7 additions — match against parser's lowercased law_ref output
    "kohanimeseaduse": "KNS",
    "põhikooli- ja gümnaasiumiseaduse": "PGS",
    "koolieelse lasteasutuse seaduse": "KELS_LASTEAS",
    "alusharidusseaduse": "AluS",
    "huvikooli seaduse": "HuviKS",
    "ruumiandmete seaduse": "RuumS",
    "kohaliku omavalitsuse üksuse finantsjuhtimise seaduse": "KOFS",
    "ühisveevärgi ja -kanalisatsiooni seaduse": "ÜVVKS",
    "kaugkütteseaduse": "KaugKS",
    "elamuseaduse": "ElamuS",
    "rahvaraamatukogu seaduse": "RaamatS",
    "hädaolukorra seaduse": "RHS_HÄDA",
    "ehitusseadustiku": "EhSE",
    "jäätmeseaduse": "JäätS",
    "teeseaduse": "TeeS",
    "rahvahääletuse seaduse": "RahvaS",
    "riigikogu valimise seaduse": "RKVS",
    "kohaliku omavalitsuse volikogu valimise seaduse": "KOVVS",
    "raamatupidamise seaduse": "RaamatPS",
    "ühistranspordiseaduse": "ÜTS",
    "sotsiaalhoolekande seaduse": "SHS",   # SHS already in KNOWN_ABBREVIATIONS
    "avaliku teabe seaduse": "AVTS",
    "avaliku teenistuse seaduse": "AVVKHS",
    "planeerimisseaduse": "PPVS",
}
```

**Important:** if a `dc:source` value in the existing law corpus does not exactly match the new full-name string (case-sensitive), the chain still breaks at `source_act_to_prefix.get(full_name)` in Task 5. After editing, verify with:

```bash
python3 - <<'PY'
import json, glob
from pathlib import Path
ROOT = Path('krr_outputs')
sources = set()
for f in ROOT.glob('*_peep.json'):
    try:
        d = json.load(open(f))
        for n in d.get('@graph', []):
            t = n.get('@type') or []
            if isinstance(t, str): t = [t]
            if 'owl:Ontology' in t:
                src = n.get('dc:source')
                if isinstance(src, str): sources.add(src)
                elif isinstance(src, list):
                    for s in src:
                        if isinstance(s, str): sources.add(s)
    except Exception: pass
new_full_names = ['Kohanimeseadus', 'Põhikooli- ja gümnaasiumiseadus',
                  'Koolieelse lasteasutuse seadus', 'Alusharidusseadus',
                  'Huvikooli seadus', 'Ruumiandmete seadus',
                  'Kohaliku omavalitsuse üksuse finantsjuhtimise seadus',
                  'Ühisveevärgi ja -kanalisatsiooni seadus', 'Kaugkütteseadus',
                  'Elamuseadus', 'Rahvaraamatukogu seadus', 'Hädaolukorra seadus',
                  'Ehitusseadustik', 'Jäätmeseadus', 'Teeseadus',
                  'Rahvahääletuse seadus', 'Riigikogu valimise seadus',
                  'Kohaliku omavalitsuse volikogu valimise seadus',
                  'Raamatupidamise seadus', 'Ühistranspordiseadus',
                  'Sotsiaalhoolekande seadus', 'Avaliku teabe seadus',
                  'Avaliku teenistuse seadus', 'Planeerimisseadus']
missing = [n for n in new_full_names if n not in sources]
if missing:
    print('NOT FOUND in any dc:source — chain will break for these:')
    for m in missing: print(' ', m)
else:
    print('All M7 full-names match a corpus dc:source')
PY
```

If the script reports any name as missing, search for the actual `dc:source` of the corresponding peep file and update either `KNOWN_ABBREVIATIONS` value or the abbreviation chain in Task 5 (`build_genitive_to_act_iri`) to use a fallback `dc:source` lookup. For cleanliness, prefer to fix `KNOWN_ABBREVIATIONS` to use the corpus-attested form.

- [ ] **Step 7: Verify**

```
pytest -q
python3 -c "import ast; ast.parse(open('scripts/estleg_common.py').read())" && echo OK
python3 -c "import ast; ast.parse(open('scripts/classify_eurovoc.py').read())" && echo OK
python3 -c "import ast; ast.parse(open('scripts/extract_legal_concepts.py').read())" && echo OK
python3 -c "import ast; ast.parse(open('scripts/generate_amendment_history.py').read())" && echo OK
```

Expected: 135 passed (unchanged from 2a end-state, modulo the M5 test count shift).

- [ ] **Step 7: Commit**

```bash
git add scripts/extract_legal_concepts.py scripts/generate_amendment_history.py scripts/estleg_common.py scripts/classify_eurovoc.py tests/test_generate_amendment_history.py README.md
git commit -m "Address Layer 2a review nits (M2-M7) before 2b work

M2-M6 are the carryover nits. M7 expands FULLNAME_GENITIVE +
KNOWN_ABBREVIATIONS to cover the ~20 most-cited KOV enabling laws
(kohanimeseaduse, põhikooli- ja gümnaasiumiseaduse, etc.); without it
Layer 2b's preamble resolver returns None for the bulk of real KOV
preambles even when the parser succeeds."
```

---

## Task 2: Corpus-aware preamble citation parser

**Files:**
- Modify: `scripts/extract_cross_references.py` (add `extract_preamble_citations`)
- Create: `tests/test_extract_cross_references.py`
- Create: `tests/fixtures/kov_layer2b/` directory + 8 preamble fixtures

The plan's first round had a too-narrow regex that missed common real preamble forms like `kohaliku omavalitsuse korralduse seaduse § 22 lg 1 p 34`, numeric dates `20.12.2007`, instrumental `määrusega nr`, and quoted forms `"Kohanimeseaduse"`. This task uses a corpus-derived test suite to drive a richer parser.

- [ ] **Step 1: Create the 8 corpus-derived preamble fixtures**

Create `tests/fixtures/kov_layer2b/preamble_samples.json`:

```json
{
  "samples": [
    {
      "id": "long-law-name-with-lg-p",
      "text": "Määrus kehtestatakse Kohaliku omavalitsuse korralduse seaduse § 22 lõike 1 punkti 34 alusel.",
      "expected_count": 1,
      "expected_form": "law-genitive",
      "expected_law_genitive": "kohaliku omavalitsuse korralduse seaduse",
      "expected_paragraph": "22",
      "expected_detail": "lg 1 p 34"
    },
    {
      "id": "abbrev-lg-p",
      "text": "Määrus kehtestatakse kohaliku omavalitsuse korralduse seaduse § 6 lg 3 p 2 ja Teeseaduse § 2 alusel.",
      "expected_count": 2,
      "expected_first_form": "law-genitive",
      "expected_first_detail": "lg 3 p 2"
    },
    {
      "id": "compound-law-name",
      "text": "Määrus kehtestatakse põhikooli- ja gümnaasiumiseaduse § 66 lõike 2 alusel.",
      "expected_count": 1,
      "expected_law_genitive": "põhikooli- ja gümnaasiumiseaduse",
      "expected_paragraph": "66",
      "expected_detail": "lg 2"
    },
    {
      "id": "paragrahv-word-form",
      "text": "Määrus kehtestatakse kohaliku omavalitsuse korralduse seaduse paragrahv 22 lõike 1 punkt 34 alusel.",
      "expected_count": 1,
      "expected_paragraph": "22",
      "expected_detail": "lg 1 p 34"
    },
    {
      "id": "state-reg-numeric-date-instrumental",
      "text": "Määrus kehtestatakse Vabariigi Valitsuse 20.12.2007 määrusega nr 251 alusel.",
      "expected_count": 1,
      "expected_form": "state-regulation-date-number",
      "expected_issuer": "Vabariigi Valitsus",
      "expected_adoption_date": "2007-12-20",
      "expected_act_number": "251"
    },
    {
      "id": "kov-reg-named-month-instrumental",
      "text": "Määrus kehtestatakse Tallinna Linnavolikogu 2. detsembri 2010 määruse nr 60 § 1 alusel.",
      "expected_count": 1,
      "expected_form": "kov-regulation-date-number",
      "expected_issuer": "Tallinna Linnavolikogu",
      "expected_adoption_date": "2010-12-02",
      "expected_act_number": "60"
    },
    {
      "id": "kov-reg-numeric-date-aastane",
      "text": "Määrus kehtestatakse Häädemeeste Vallavolikogu 18.juuni 2003.a määrusega nr 15 alusel.",
      "expected_count": 1,
      "expected_form": "kov-regulation-date-number",
      "expected_issuer": "Häädemeeste Vallavolikogu",
      "expected_adoption_date": "2003-06-18",
      "expected_act_number": "15"
    },
    {
      "id": "quoted-law-name",
      "text": "Määrus kehtestatakse \"Kohanimeseaduse\" § 5 lõike 4 alusel.",
      "expected_count": 1,
      "expected_form": "law-genitive",
      "expected_law_genitive": "kohanimeseaduse",
      "expected_paragraph": "5",
      "expected_detail": "lg 4"
    },
    {
      "id": "minister-numeric-date-instrumental",
      "text": "Määrus kehtestatakse rahandusministri 11.12.2003 määrusega nr 105 \"Avaliku sektori finantsarvestuse ja -aruandluse juhend\" alusel.",
      "expected_count": 1,
      "expected_form": "state-regulation-date-number",
      "expected_issuer": "rahandusminister",
      "expected_adoption_date": "2003-12-11",
      "expected_act_number": "105"
    },
    {
      "id": "minister-multiword-named-month",
      "text": "Määrus kehtestatakse majandus- ja kommunikatsiooniministri 17. detsembri 2002. a määruse nr 45 § 3 alusel.",
      "expected_count": 1,
      "expected_form": "state-regulation-date-number",
      "expected_issuer": "majandus- ja kommunikatsiooniminister",
      "expected_adoption_date": "2002-12-17",
      "expected_act_number": "45"
    },
    {
      "id": "minister-locative-form",
      "text": "Lähtudes rahandusministri 11. detsembri 2003 määruses nr 105 sätestatust, kehtestatakse järgmine kord.",
      "expected_count": 1,
      "expected_form": "state-regulation-date-number",
      "expected_issuer": "rahandusminister",
      "expected_adoption_date": "2003-12-11",
      "expected_act_number": "105"
    }
  ]
}
```

**Why these specific samples:** all three minister-form samples are paraphrased from real KOV preambles in the corpus (`polva_valla_raamatupidamise_sise_eeskiri_t1060057_peep.json`, `kagu_eesti_spetsialistide_eluasemete_toetuse_andmise_kord_t1049666_peep.json`, `sotsiaaltransporditeenuse_osutamise_juhend_t1017737_peep.json`). The corpus uses lowercase `rahandusministri`, multiword `majandus- ja kommunikatsiooniministri` with hyphens, and locative `määruses` alongside the more common genitive `määruse` and instrumental `määrusega`. A regex that misses any of these will fail on the bulk of state-regulation citations in real KOV text.

- [ ] **Step 2: Write the failing test**

Create `tests/test_extract_cross_references.py`:

```python
"""Tests for scripts/extract_cross_references.py — Layer 2b additions."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "kov_layer2b"


def load_preamble_samples():
    with open(FIXTURE_DIR / "preamble_samples.json", "r", encoding="utf-8") as fh:
        return json.load(fh)["samples"]


class TestExtractPreambleCitations:
    """Drive the parser with corpus-derived strings, not synthetic ones.
    Each sample asserts what real KOV / state regulation preamble text
    looks like in the wild — see fixtures/kov_layer2b/preamble_samples.json."""

    @pytest.mark.parametrize("sample", load_preamble_samples(),
                              ids=lambda s: s["id"])
    def test_sample(self, sample):
        from extract_cross_references import extract_preamble_citations

        cits = extract_preamble_citations(sample["text"])
        assert len(cits) == sample["expected_count"], (
            f"expected {sample['expected_count']} citations from "
            f"{sample['id']!r}, got {len(cits)}: {cits}"
        )

        if "expected_form" in sample:
            assert cits[0]["form"] == sample["expected_form"]
        if "expected_first_form" in sample:
            assert cits[0]["form"] == sample["expected_first_form"]
        if "expected_first_detail" in sample:
            assert cits[0]["citationDetail"] == sample["expected_first_detail"]
        if "expected_law_genitive" in sample:
            # Comparison is case-insensitive — preambles vary capitalization
            assert cits[0]["law_ref"].lower() == sample["expected_law_genitive"].lower()
        if "expected_paragraph" in sample:
            assert cits[0]["paragraphs"] == [sample["expected_paragraph"]]
        if "expected_detail" in sample:
            assert cits[0]["citationDetail"] == sample["expected_detail"]
        if "expected_issuer" in sample:
            assert cits[0]["issuer"] == sample["expected_issuer"]
        if "expected_adoption_date" in sample:
            assert cits[0]["adoption_date"] == sample["expected_adoption_date"]
        if "expected_act_number" in sample:
            assert cits[0]["act_number"] == sample["expected_act_number"]

    def test_empty_text_returns_empty(self):
        from extract_cross_references import extract_preamble_citations
        assert extract_preamble_citations("") == []
        assert extract_preamble_citations(None) == []

    def test_no_match_returns_empty(self):
        from extract_cross_references import extract_preamble_citations
        assert extract_preamble_citations("Plain text with no citations.") == []
```

- [ ] **Step 3: Run the test (expect ImportError)**

```
pytest tests/test_extract_cross_references.py::TestExtractPreambleCitations -v
```

Expected: FAIL with `ImportError: cannot import name 'extract_preamble_citations'`.

- [ ] **Step 4: Implement `extract_preamble_citations` with corpus-aware regex**

Add to `scripts/extract_cross_references.py` (place after `extract_citations_from_text`, before `_expand_par_range`):

```python
# ----------------------------------------------------------------------
# Preamble citation extraction (Layer 2b)
# ----------------------------------------------------------------------

# Estonian month name (genitive) → 2-digit month number.
# All forms come from the spec text "<day>. <month-genitive> <year>".
_ET_MONTHS = {
    "jaanuari": "01", "veebruari": "02", "märtsi": "03", "aprilli": "04",
    "mai": "05", "juuni": "06", "juuli": "07", "augusti": "08",
    "septembri": "09", "oktoobri": "10", "novembri": "11", "detsembri": "12",
}

# Quote characters seen in the corpus around law names. The parser
# strips these before matching the genitive form.
_QUOTES = '"\'„""«»«»“”„’‘'

# Section symbol or its word form. KOV preambles freely mix § and
# the spelled-out "paragrahv" / "paragrahvi".
_PARA_TOKEN = r"(?:§|paragrahv(?:i)?|paragrahvid?)"

# Lõige (subsection) — multiple case forms in the corpus:
#   lõike (singular genitive) — most common
#   lõikest (singular elative)
#   lõigete (plural genitive) — for "lõigete 1 ja 2"
#   lg (abbreviation) — common in shorter preambles
_LG_TOKEN = r"(?:lõike(?:st|s)?|lõigete|lg)"

# Punkt — multiple forms:
#   punkti (genitive) — "punkti 34"
#   punkt (nominative)
#   p (abbreviation)
_P_TOKEN = r"(?:punkti|punkt|p)"

# Pattern A — law in genitive form + § N (lõike M (punkti K)?)?
# Matches one paragraph reference. The law genitive name can be
# multiword and contain hyphens / non-Latin diacritics.
# Example matches:
#   "kohaliku omavalitsuse korralduse seaduse § 22 lõike 1 punkti 34"
#   "põhikooli- ja gümnaasiumiseaduse § 66 lõike 2"
#   "Kohanimeseaduse § 5 lõike 4"
#   "kohaliku omavalitsuse korralduse seaduse paragrahv 6 lõike 1"
_PAT_LAW_PARA = re.compile(
    r"(?P<law>(?:[a-zõäöüšžA-ZÕÄÖÜŠŽ][a-zõäöüšžA-ZÕÄÖÜŠŽ\-]*\s+){0,5}"
    r"[a-zõäöüšžA-ZÕÄÖÜŠŽ][a-zõäöüšžA-ZÕÄÖÜŠŽ\-]*seaduse(?:tiku)?)\s*"
    rf"{_PARA_TOKEN}\s*(?P<par>\d+)"
    rf"(?:\s+{_LG_TOKEN}\s+(?P<lg>\d+))?"
    rf"(?:\s+{_P_TOKEN}\s+(?P<p>\d+))?",
    re.UNICODE | re.IGNORECASE,
)

# Pattern B — state regulation: "Vabariigi Valitsuse <date> määruse(ga) nr N"
# Date can be:
#   - "19. detsembri 2019. a" (named-month genitive form)
#   - "20.12.2007" (numeric DD.MM.YYYY)
# määruse / määrusega — case form varies by sentence position.
_DATE_NAMED = (
    r"(?P<day_n>\d{1,2})\.\s*"
    r"(?P<month>jaanuari|veebruari|märtsi|aprilli|mai|juuni|juuli|"
    r"augusti|septembri|oktoobri|novembri|detsembri)\s+"
    r"(?P<year_n>\d{4})\.?\s*a?\.?"
)
_DATE_NUMERIC = (
    r"(?P<day_d>\d{1,2})\.\s*(?P<month_d>\d{1,2})\.\s*(?P<year_d>\d{4})\.?\s*a?\.?"
)
_DATE_EITHER = rf"(?:{_DATE_NAMED}|{_DATE_NUMERIC})"

_PAT_STATE_REG = re.compile(
    # Issuer alternatives:
    #   1. "Vabariigi Valitsuse" (genitive of Government)
    #   2. multiword/lowercase minister names ending in "ministri":
    #         "rahandusministri" (compound)
    #         "majandus- ja kommunikatsiooniministri" (multiword + hyphen)
    #         "riigihalduse ministri" (separate word)
    #   The lookahead prevents the issuer match from running into the date.
    r"(?P<issuer>Vabariigi\s+Valitsuse|"
    r"(?:[A-ZÕÄÖÜŠŽa-zõäöüšž][\w\-õäöüšžÕÄÖÜŠŽ]*(?:[-\s]+(?:ja\s+)?[a-zõäöüšž][\w\-õäöüšžÕÄÖÜŠŽ]*){0,4}\s*)?"
    r"[a-zõäöüšž]+ministri)\s+"
    rf"{_DATE_EITHER}\s+määrus(?:ega|es|e|est)\s+nr\s*\.?\s*(?P<num>\d+)",
    re.UNICODE | re.IGNORECASE,
)

# Pattern C — KOV regulation: "<Issuer Display Name> <date> määruse(ga) nr N"
# Issuer ends in volikogu / valitsus(e) — the genitive form is
# "Linnavolikogu" / "Linnavalitsuse" / "Vallavolikogu" / "Vallavalitsuse".
# Issuer name may be compound (Häädemeeste, Lääne-Nigula).
_PAT_KOV_REG = re.compile(
    r"(?P<issuer>[A-ZÕÄÖÜŠŽ][a-zõäöüšž\-]+(?:\s+[A-ZÕÄÖÜŠŽ][a-zõäöüšž\-]+)*\s+"
    r"(?:Linnavolikogu|Linnavalitsuse|Linnavalitsus|"
    r"Vallavolikogu|Vallavalitsuse|Vallavalitsus|"
    r"Alevivolikogu))\s+"
    rf"{_DATE_EITHER}\s+määrus(?:ega|e|est)\s+nr\s*(?P<num>\d+)",
    re.UNICODE,
)


def _build_citation_detail(lg: str | None, p: str | None) -> str | None:
    """Combine lõige (lg) and punkt (p) into 'lg 1' / 'lg 1 p 3'."""
    parts = []
    if lg:
        parts.append(f"lg {lg}")
    if p:
        parts.append(f"p {p}")
    return " ".join(parts) if parts else None


def _normalize_state_issuer(issuer_text: str) -> str:
    """Strip Estonian genitive ending ("...ministri" → "...minister",
    "Vabariigi Valitsuse" → "Vabariigi Valitsus") so the resolver's
    state-regulation lookup key matches the corpus issuer label.

    The corpus stamps state-regulation issuers in nominative case on
    the act node's ``estleg:issuer`` field (e.g. "rahandusminister",
    "Vabariigi Valitsus"). Without normalisation, the parser-extracted
    "rahandusministri" would never match any lookup key.
    """
    # Collapse internal whitespace (multiword minister regex can pick
    # up stray spaces around hyphens or 'ja').
    s = re.sub(r"\s+", " ", issuer_text.strip())
    if s.endswith("Valitsuse"):
        return s[:-1]  # Valitsuse → Valitsus
    # Both "rahandusministri" (compound, one word) and "riigihalduse
    # ministri" (separate word) collapse to "...minister".
    if s.endswith("ministri"):
        return s[:-1]
    return s


def _strip_quotes(text: str) -> str:
    """Strip surrounding quote characters around a single token. The
    parser pre-processes preamble text to remove these so the law-name
    regex can match `Kohanimeseaduse` whether or not it's quoted."""
    return text.strip(_QUOTES + " ")


def _normalize_date(
    day_n: str | None, month_named: str | None, year_n: str | None,
    day_d: str | None, month_d: str | None, year_d: str | None,
) -> str | None:
    """Build ISO-8601 date from either named-month or numeric-date
    capture groups. Returns None if neither pair is fully populated."""
    if day_n and month_named and year_n:
        m = _ET_MONTHS.get(month_named.lower())
        if m is None:
            return None
        return f"{year_n}-{m}-{int(day_n):02d}"
    if day_d and month_d and year_d:
        return f"{year_d}-{int(month_d):02d}-{int(day_d):02d}"
    return None


def extract_preamble_citations(text: str) -> list[dict]:
    """Parse a preamble for Estonian legal citation forms.

    Returns a list of dicts in textual order. Each dict has:
        form           : "law-genitive" | "state-regulation-date-number"
                         | "kov-regulation-date-number"
        citationText   : original matched substring
        citationDetail : "lg 1" / "lg 1 p 3" / None

    Form-specific keys:
        law-genitive:
            law_ref      : the full genitive law name
            paragraphs   : ["N"] (always one entry — no ranges in
                           preambles in the current corpus)

        state-regulation-date-number / kov-regulation-date-number:
            issuer        : nominative issuer string
            adoption_date : ISO-8601 date string
            act_number    : the digit string after "määruse(ga) nr"
    """
    if not text:
        return []

    # Substitute quote characters with spaces in a SCRATCH copy used
    # only for matching. The original `text` is preserved so that
    # citationText (intended for traceability and SHACL display) is
    # the literal input substring with quotes intact.
    cleaned = re.sub(rf"[{re.escape(_QUOTES)}]", " ", text)

    def _orig_substring(start_in_cleaned: int, end_in_cleaned: int) -> str:
        # Quote substitution is one-char-for-one-char, so offsets in
        # `cleaned` are valid in `text`. Use the original text for
        # citationText to preserve quotes / punctuation.
        return text[start_in_cleaned:end_in_cleaned].strip()

    citations: list[tuple[int, dict]] = []

    for m in _PAT_LAW_PARA.finditer(cleaned):
        cit = {
            "form": "law-genitive",
            "law_ref": _strip_quotes(m.group("law")).strip(),
            "paragraphs": [m.group("par")],
            "citationDetail": _build_citation_detail(m.group("lg"), m.group("p")),
            "citationText": _orig_substring(m.start(), m.end()),
        }
        citations.append((m.start(), cit))

    for m in _PAT_STATE_REG.finditer(cleaned):
        date = _normalize_date(
            m.group("day_n"), m.group("month"), m.group("year_n"),
            m.group("day_d"), m.group("month_d"), m.group("year_d"),
        )
        if date is None:
            continue
        cit = {
            "form": "state-regulation-date-number",
            "issuer": _normalize_state_issuer(m.group("issuer")),
            "adoption_date": date,
            "act_number": m.group("num"),
            "citationDetail": None,
            "citationText": _orig_substring(m.start(), m.end()),
        }
        citations.append((m.start(), cit))

    for m in _PAT_KOV_REG.finditer(cleaned):
        date = _normalize_date(
            m.group("day_n"), m.group("month"), m.group("year_n"),
            m.group("day_d"), m.group("month_d"), m.group("year_d"),
        )
        if date is None:
            continue
        cit = {
            "form": "kov-regulation-date-number",
            "issuer": re.sub(r"\s+", " ", m.group("issuer").strip()),
            "adoption_date": date,
            "act_number": m.group("num"),
            "citationDetail": None,
            "citationText": _orig_substring(m.start(), m.end()),
        }
        citations.append((m.start(), cit))

    citations.sort(key=lambda pair: pair[0])
    return [c for _, c in citations]
```

- [ ] **Step 5: Run the test**

```
pytest tests/test_extract_cross_references.py::TestExtractPreambleCitations -v
```

Expected: 11 parametrized samples + `test_empty_text_returns_empty` + `test_no_match_returns_empty` = 13 passed.

If a sample fails, the regex doesn't yet handle that case. **Iterate on the regex until all 11 samples pass — do not relax the test fixtures.** The samples are corpus-derived; they represent real text the pipeline will hit.

- [ ] **Step 6: Run the full suite**

```
pytest -q
```

Expected: prior count + 13 new tests, all passing.

- [ ] **Step 7: Commit**

```bash
git add scripts/extract_cross_references.py tests/test_extract_cross_references.py tests/fixtures/kov_layer2b/preamble_samples.json
git commit -m "Add corpus-aware preamble citation parser

Drives the parser with 11 corpus-derived preamble fixtures covering
real Estonian variation: multiword law names, paragraph word/symbol
forms, lõige genitive/elative/plural cases, named-month and numeric
date formats, instrumental määrusega and locative määruses,
multi-word lowercase ministers (majandus- ja kommunikatsiooniministri,
riigihalduse ministri), quoted law names. The regex bank handles
all 11 samples; iterate on the bank if any fail rather than
weakening the fixtures. citationText is preserved as the original
input substring (quotes intact)."
```

---

## Task 3: Canonical lookups (act IRIs + genitive-to-act + regulation by XML adoption-date)

**Files:**
- Modify: `scripts/extract_cross_references.py` (extend `build_provision_index`; add 3 new lookup builders)
- Modify: `tests/test_extract_cross_references.py` (add `TestCanonicalLookups`)

The reviewer's blockers B1, B2, and B3 share a root cause: there's no canonical mapping from the parser's output (a genitive law name like `kohaliku omavalitsuse korralduse seaduse`) to a real act @id in the corpus (like `estleg:KOKS_Map_2026`, NOT bare prefix `estleg:KOKS`). State and KOV regulations have a similar problem: they're keyed by `(issuer, adoption_date, act_number)`, but no act node carries `estleg:adoptionDate` — adoption dates live only inside source XML at `<vastuvoetud><aktikuupaev>`.

This task builds five canonical lookups exposed as functions:

1. **`prefix_to_act_iri`** AND **`act_iri_to_prefix`** — extends `build_provision_index` to also return BOTH directions: `{prefix: act_iri}` and `{act_iri: prefix}`. Read from each scanned peep file's `owl:Ontology` node. The reverse map is essential because the resolver (Task 5) needs to derive a *prefix* from an *act IRI* to look up provisions, and `act_iri.split("_")[0]` breaks for 34 acts in the corpus where the prefix itself contains underscores (e.g. `KARIST_2_Map_2026` — split-on-`_` returns `KARIST`, but provisions live under prefix `KARIST_2`).
2. **`build_genitive_to_act_iri(...)`** — chains `FULLNAME_GENITIVE` → abbrev → `source_act_to_prefix` → `prefix_to_act_iri`. Returns `{genitive_law_name (lowercased): act_iri}`.
3. **`build_state_regulation_lookup(riik_root, data_dir)`** — walks state regulation peep files; for each, pairs to its XML via `pair_peep_with_xml(...)` (Layer 2a helper); reads `<vastuvoetud><aktikuupaev>` from the XML; keys the lookup by `(issuer_label_normalized, adoption_date, act_number)`.
4. **`build_kov_act_lookup(kov_root, data_dir)`** — same shape as state-reg lookup, but for KOV peep files. Issuer label is normalised via `_normalize_issuer_label` (transliterates Estonian diacritics) so the registry-side `"Polva Vallavalitsus"` matches act-side `"Põlva Vallavalitsus"` despite the õ-vs-o mismatch (Layer 1 issuers were derived from URL slugs).

For body-text references that don't have date context, this task also builds a parallel non-date lookup `build_kov_act_lookup_by_number(kov_root)` keyed by `(issuer_label_normalized, act_number)` (no date) whose **value is a list of candidate act IRIs**, not a single value. Body-text references typically lack adoption date and the same issuer can have several acts under the same `actNumber` across historical revisions; the resolver returns a target only when the candidate list narrows to exactly one entry. Preamble resolution uses the date-keyed version, which is unique by construction.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_extract_cross_references.py`:

```python
class TestCanonicalLookups:
    def test_extends_provision_index_with_prefix_to_act_iri_and_reverse(
        self, tmp_path, monkeypatch
    ):
        """build_provision_index now returns 5 things; the two new ones
        are prefix→act_iri AND act_iri→prefix. The reverse map is
        essential for Task 5's resolver, which needs to derive a prefix
        from an act IRI without splitting on '_' (34 corpus acts have
        prefixes containing underscores, e.g. 'KARIST_2_Map_2026')."""
        from extract_cross_references import build_provision_index
        import estleg_common

        krr = tmp_path / "krr_outputs"
        krr.mkdir()
        # Stage two law peeps: one straight prefix, one underscore-containing prefix
        (krr / "alkoholiseadus_peep.json").write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:AS_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
                 "rdfs:label": "Alkoholiseadus",
                 "dc:source": "Alkoholiseadus"},
                {"@id": "estleg:AS_Par_42",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "AS § 42"}
            ],
        }), encoding="utf-8")
        (krr / "karistusseadustik_osa1_peep.json").write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:KARIST_2_Osa1_1_87",
                 "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
                 "rdfs:label": "Karistusseadustik (osa 1)",
                 "dc:source": "Karistusseadustik"},
                {"@id": "estleg:KARIST_2_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "KarS § 1"}
            ],
        }), encoding="utf-8")
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)

        result = build_provision_index()
        # Old return — 3 values; new return — 5 values (both maps appended)
        assert len(result) == 5
        (prefix_to_provisions, source_act_to_prefix, iri_to_file,
         prefix_to_act_iri, act_iri_to_prefix) = result

        # Prefix → act IRI
        assert prefix_to_act_iri.get("AS") == "estleg:AS_Map_2026"
        assert prefix_to_act_iri.get("KARIST_2") == "estleg:KARIST_2_Osa1_1_87"

        # Reverse direction — exact mirror of prefix_to_act_iri
        assert act_iri_to_prefix.get("estleg:AS_Map_2026") == "AS"
        # The reverse lookup MUST resolve to the multi-segment prefix,
        # not 'KARIST', because provisions are keyed under 'KARIST_2'.
        assert act_iri_to_prefix.get("estleg:KARIST_2_Osa1_1_87") == "KARIST_2"

    def test_genitive_to_act_iri_chains_through_abbrev(self):
        """Build the genitive_law_name → act_iri lookup by chaining
        FULLNAME_GENITIVE → abbrev → source_act_to_prefix → prefix_to_act_iri."""
        from extract_cross_references import build_genitive_to_act_iri

        # Mock the pieces — we test the chaining logic, not the
        # underlying registries (those are tested in build_provision_index).
        source_act_to_prefix = {
            "Alkoholiseadus": "AS",
            "Karistusseadustik": "KARIST",
        }
        prefix_to_act_iri = {
            "AS": "estleg:AS_Map_2026",
            "KARIST": "estleg:KARIST_2_Map_2026",
        }
        # FULLNAME_GENITIVE has e.g. "karistusseadustiku" → "KarS"
        # KNOWN_ABBREVIATIONS would map "KarS" → "Karistusseadustik"
        # The function we build wraps that chain.

        result = build_genitive_to_act_iri(
            source_act_to_prefix=source_act_to_prefix,
            prefix_to_act_iri=prefix_to_act_iri,
        )
        # The lookup contains lowercased keys so it can be queried
        # case-insensitively against parser output.
        assert "karistusseadustiku" in result
        assert result["karistusseadustiku"] == "estleg:KARIST_2_Map_2026"

    def test_state_regulation_lookup_reads_adoption_from_xml(self, tmp_path):
        """The corpus has zero estleg:adoptionDate — adoption_date is
        only in XML at <vastuvoetud><aktikuupaev>. The lookup builder
        pairs each peep to its XML via pair_peep_with_xml and reads
        the date from there."""
        from extract_cross_references import build_state_regulation_lookup

        # Stage a state regulation peep with globalId
        riik = tmp_path / "krr_outputs" / "regulations" / "riik"
        riik.mkdir(parents=True)
        (riik / "vv_112_peep.json").write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Reg_VV112_Map_2019",
                 "@type": ["owl:Ontology", "estleg:Act",
                           "estleg:GovernmentRegulation"],
                 "estleg:issuer": "Vabariigi Valitsus",
                 "estleg:globalId": "112112",
                 "estleg:actNumber": "112"}
                 # NOTE: NO estleg:adoptionDate — that's the corpus reality
            ],
        }), encoding="utf-8")
        # Stage matching XML with the vastuvoetud block
        rt = tmp_path / "data" / "riigiteataja" / "maarus"
        rt.mkdir(parents=True)
        (rt / "reg_112112.xml").write_text(
            '<akt globaalID="112112"><metaandmed>'
            '<vastuvoetud><aktikuupaev>2019-12-19</aktikuupaev></vastuvoetud>'
            '</metaandmed></akt>',
            encoding="utf-8",
        )

        lookup = build_state_regulation_lookup(
            riik_root=riik, data_dir=tmp_path / "data" / "riigiteataja",
        )
        assert lookup[("Vabariigi Valitsus", "2019-12-19", "112")] == \
            "estleg:Reg_VV112_Map_2019"

    def test_kov_act_lookup_uses_registry_label(self, tmp_path):
        """Issuer label comes from the registry's rdfs:label, not from
        slug→title-case (which would produce 'Noo' instead of 'Nõo')."""
        from extract_cross_references import build_kov_act_lookup

        # Stage a KOV peep + matching XML, plus an issuers registry
        # whose rdfs:label has a diacritic.
        krr = tmp_path / "krr_outputs"
        kov = krr / "regulations" / "kov" / "noo_vallavolikogu"
        kov.mkdir(parents=True)
        (kov / "act_peep.json").write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Reg_NOO_5_Map_2020",
                 "@type": ["owl:Ontology", "estleg:Act",
                           "estleg:MunicipalRegulation"],
                 "estleg:issuer": "Nõo Vallavolikogu",  # canonical form
                 "estleg:globalId": "555000",
                 "estleg:actNumber": "5",
                 "estleg:enactedBy": {"@id": "estleg:Issuer_noo_vallavolikogu"}}
            ],
        }), encoding="utf-8")
        # Issuers registry — uses rdfs:label that the lookup should respect
        (krr / "issuers_kov_peep.json").write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Issuer_noo_vallavolikogu",
                 "@type": ["owl:NamedIndividual", "estleg:Issuer"],
                 # Note: real registry has 'Noo Vallavolikogu' (ASCII
                 # because Layer 1 derived from slug). Test verifies
                 # the lookup respects whatever the registry says.
                 "rdfs:label": "Noo Vallavolikogu"}
            ],
        }), encoding="utf-8")
        # Matching XML
        rt = tmp_path / "data" / "riigiteataja" / "maarus_kov"
        rt.mkdir(parents=True)
        (rt / "reg_555000.xml").write_text(
            '<akt globaalID="555000"><metaandmed>'
            '<vastuvoetud><aktikuupaev>2020-05-15</aktikuupaev></vastuvoetud>'
            '</metaandmed></akt>',
            encoding="utf-8",
        )

        lookup = build_kov_act_lookup(
            kov_root=kov.parent,
            data_dir=tmp_path / "data" / "riigiteataja",
            issuers_path=krr / "issuers_kov_peep.json",
        )
        # Use whatever the registry says — for this test, "Noo Vallavolikogu"
        assert lookup[("Noo Vallavolikogu", "2020-05-15", "5")] == \
            "estleg:Reg_NOO_5_Map_2020"

    def test_kov_act_lookup_by_number_no_date(self, tmp_path):
        """Body-text references typically don't carry adoption_date.
        A separate lookup is keyed by (issuer_label_normalized,
        act_number) and stores a LIST of candidate IRIs — the same
        issuer can have several acts under the same actNumber across
        historical revisions, and the resolver must see all candidates
        to decide whether the match is unique."""
        from extract_cross_references import build_kov_act_lookup_by_number

        krr = tmp_path / "krr_outputs"
        kov = krr / "regulations" / "kov" / "tallinna_linnavolikogu"
        kov.mkdir(parents=True)
        (kov / "act_peep.json").write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Reg_TLN_15_Map_2020",
                 "@type": ["owl:Ontology", "estleg:MunicipalRegulation"],
                 "estleg:issuer": "Tallinna Linnavolikogu",
                 "estleg:actNumber": "15"}
            ],
        }), encoding="utf-8")

        lookup = build_kov_act_lookup_by_number(kov_root=kov.parent)
        # The lookup key is (issuer_label_normalized, act_number) and
        # the value is a LIST of candidate IRIs.
        assert lookup[("tallinna linnavolikogu", "15")] == [
            "estleg:Reg_TLN_15_Map_2020"
        ]

    def test_kov_act_lookup_by_number_records_all_candidates(self, tmp_path):
        """When an issuer has reused the same act_number across
        historical revisions, the lookup MUST keep all candidates so
        the resolver can detect ambiguity and abstain."""
        from extract_cross_references import build_kov_act_lookup_by_number

        krr = tmp_path / "krr_outputs"
        kov = krr / "regulations" / "kov" / "tallinna_linnavolikogu"
        kov.mkdir(parents=True)
        # Two acts, same issuer + same act number, different years
        (kov / "old_peep.json").write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Reg_TLN_15_Map_2010",
                 "@type": ["owl:Ontology", "estleg:MunicipalRegulation"],
                 "estleg:issuer": "Tallinna Linnavolikogu",
                 "estleg:actNumber": "15"}
            ],
        }), encoding="utf-8")
        (kov / "new_peep.json").write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Reg_TLN_15_Map_2020",
                 "@type": ["owl:Ontology", "estleg:MunicipalRegulation"],
                 "estleg:issuer": "Tallinna Linnavolikogu",
                 "estleg:actNumber": "15"}
            ],
        }), encoding="utf-8")

        lookup = build_kov_act_lookup_by_number(kov_root=kov.parent)
        candidates = lookup[("tallinna linnavolikogu", "15")]
        assert isinstance(candidates, list)
        assert sorted(candidates) == sorted([
            "estleg:Reg_TLN_15_Map_2010",
            "estleg:Reg_TLN_15_Map_2020",
        ])

    def test_kov_act_lookup_normalizes_diacritics(self, tmp_path):
        """Issuer labels in Layer 1's registry are slug-derived (ASCII),
        but acts carry the canonical form with diacritics. The lookup
        must normalise both sides so 'Põlva Vallavalitsus' on the act
        side matches a body-text reference written 'Polva Vallavalitsus'
        (and vice-versa)."""
        from extract_cross_references import build_kov_act_lookup_by_number

        krr = tmp_path / "krr_outputs"
        kov = krr / "regulations" / "kov" / "polva_vallavalitsus"
        kov.mkdir(parents=True)
        (kov / "act_peep.json").write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Reg_POLVA_5_Map_2020",
                 "@type": ["owl:Ontology", "estleg:MunicipalRegulation"],
                 "estleg:issuer": "Põlva Vallavalitsus",   # canonical, with õ
                 "estleg:actNumber": "5"}
            ],
        }), encoding="utf-8")

        lookup = build_kov_act_lookup_by_number(kov_root=kov.parent)
        # Both diacritic and ASCII forms must hit the same candidate list.
        assert lookup[("polva vallavalitsus", "5")] == [
            "estleg:Reg_POLVA_5_Map_2020"
        ]
```

- [ ] **Step 2: Run (expect ImportError / signature mismatch)**

```
pytest tests/test_extract_cross_references.py::TestCanonicalLookups -v
```

Expected: FAIL.

- [ ] **Step 3: Extend `build_provision_index` to return both prefix maps**

In `scripts/extract_cross_references.py`, find `build_provision_index` (around line 54). It currently returns 3 values: `(prefix_to_provisions, source_act_to_prefix, iri_to_file)`. Add two more: `prefix_to_act_iri` and `act_iri_to_prefix`.

The prefix derivation MUST handle 3 act-IRI shapes seen in the corpus:

| Shape | Example | Prefix |
| --- | --- | --- |
| `<prefix>_Map_<year>` | `estleg:AS_Map_2026` | `AS` |
| `<prefix>_Osa<N>[_...]` | `estleg:KARIST_2_Osa1_1_87` | `KARIST_2` |
| `<prefix>` (no suffix; rare legacy) | `estleg:AÕSRS` | `AÕSRS` |

A naive `shortid.split("_")[0]` is **wrong** — it returns `KARIST` for the second row (whereas the corpus's provisions live at `estleg:KARIST_2_Par_<n>`). Use the canonical helper below.

```python
import re as _re_local

_ACT_SUFFIX_PATTERNS = [
    _re_local.compile(r"_Map_\d{4}$"),                # _Map_2026
    _re_local.compile(r"_Osa\d+(?:_.+)?$"),           # _Osa1, _Osa5_AsjaoigusteKaitse
    _re_local.compile(r"_(?:Procedure|Substantive)Map_\d{4}$"),  # KrMS variants
]


def _prefix_from_act_iri(act_iri: str) -> str | None:
    """Derive the provision-keying prefix from a full act @id.

    The prefix is the leading segment that all of the act's provisions
    share (e.g. 'KOKS' for 'estleg:KOKS_Map_2026' whose provisions look
    like 'estleg:KOKS_Par_22'; 'KARIST_2' for 'estleg:KARIST_2_Osa1_1_87'
    whose provisions look like 'estleg:KARIST_2_Par_1').
    """
    if not act_iri.startswith("estleg:"):
        return None
    short = act_iri[len("estleg:"):]
    for pat in _ACT_SUFFIX_PATTERNS:
        m = pat.search(short)
        if m:
            return short[: m.start()]
    return short  # legacy: no suffix


def build_provision_index() -> tuple[
    dict[str, dict[str, str]],
    dict[str, str],
    dict[str, Path],
    dict[str, str],   # prefix → act @id
    dict[str, str],   # act @id → prefix (reverse, for resolver use)
]:
    prefix_to_provisions: dict[str, dict[str, str]] = {}
    source_act_to_prefix: dict[str, str] = {}
    iri_to_file: dict[str, Path] = {}
    prefix_to_act_iri: dict[str, str] = {}
    act_iri_to_prefix: dict[str, str] = {}

    for peep in iter_peep_files():
        try:
            with open(peep, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue

        # ... existing scan logic that populates prefix_to_provisions,
        #     source_act_to_prefix, iri_to_file ...

        # Layer 2b: capture the act node and bind both prefix maps.
        for node in doc.get("@graph", []):
            types = node.get("@type") or []
            if isinstance(types, str):
                types = [types]
            if "owl:Ontology" not in types:
                continue
            act_iri = node.get("@id")
            if not act_iri:
                continue
            prefix = _prefix_from_act_iri(act_iri)
            if not prefix:
                continue
            # Multi-part laws (e.g. KARIST_2_Osa1, KARIST_2_Osa2) share
            # the same prefix; record the FIRST act_iri under the prefix
            # and let the others be addressable via the reverse map only.
            prefix_to_act_iri.setdefault(prefix, act_iri)
            act_iri_to_prefix[act_iri] = prefix
            break

    return (prefix_to_provisions, source_act_to_prefix, iri_to_file,
            prefix_to_act_iri, act_iri_to_prefix)
```

Update every caller of `build_provision_index()` in the same module to unpack the 5-tuple. There's one caller in `main()` (around line 405):

```python
# Was:
prefix_to_provisions, source_act_to_prefix, iri_to_file = build_provision_index()
# Now:
(prefix_to_provisions, source_act_to_prefix, iri_to_file,
 prefix_to_act_iri, act_iri_to_prefix) = build_provision_index()
```

- [ ] **Step 4: Implement `build_genitive_to_act_iri`**

Add to `scripts/extract_cross_references.py`:

```python
def build_genitive_to_act_iri(
    source_act_to_prefix: dict[str, str],
    prefix_to_act_iri: dict[str, str],
) -> dict[str, str]:
    """Build a {genitive_law_name (lowercased) → act @id} lookup by
    chaining FULLNAME_GENITIVE → abbrev → source_act_to_prefix →
    prefix_to_act_iri.

    The returned keys are lowercased so the parser's case-insensitive
    output ('Kohaliku omavalitsuse korralduse seaduse' or 'kohaliku
    omavalitsuse korralduse seaduse') resolves uniformly via .lower().
    """
    # FULLNAME_GENITIVE is imported from estleg_common at the top
    # of this module. Each entry is genitive_lower → abbrev.
    out: dict[str, str] = {}
    for genitive, abbrev in FULLNAME_GENITIVE.items():
        # Walk the abbrev → full_name → prefix chain.
        # KNOWN_ABBREVIATIONS maps abbrev → full_name.
        full_name = KNOWN_ABBREVIATIONS.get(abbrev)
        if full_name is None:
            continue
        prefix = source_act_to_prefix.get(full_name)
        if prefix is None:
            continue
        act_iri = prefix_to_act_iri.get(prefix)
        if act_iri is None:
            continue
        out[genitive.lower()] = act_iri
    return out
```

- [ ] **Step 5: Implement `build_state_regulation_lookup` (XML-based adoption date)**

```python
import xml.etree.ElementTree as _ET_LOCAL


def _read_adoption_date_from_xml(xml_path: Path) -> str | None:
    """Read <vastuvoetud><aktikuupaev> from a Riigi Teataja XML.
    Returns the ISO-8601 date string or None when absent."""
    try:
        tree = _ET_LOCAL.parse(str(xml_path))
    except _ET_LOCAL.ParseError:
        return None
    for node in tree.iter():
        # Tag local-name match (ns-aware)
        if node.tag.endswith("vastuvoetud"):
            for child in node:
                if child.tag.endswith("aktikuupaev"):
                    if child.text:
                        return child.text.strip()
    return None


def build_state_regulation_lookup(
    riik_root: Path,
    data_dir: Path,
) -> dict[tuple[str, str, str], str]:
    """Build (issuer, adoption_date, act_number) → act @id for state
    regulations under riik_root. adoption_date is read from each
    paired XML at <vastuvoetud><aktikuupaev>; the corpus has zero
    estleg:adoptionDate fields."""
    from estleg_common import build_globalid_xml_lookup, pair_peep_with_xml

    lookup: dict[tuple[str, str, str], str] = {}
    if not riik_root.is_dir():
        return lookup

    xml_lookup = build_globalid_xml_lookup(data_dir)

    for peep in sorted(riik_root.rglob("*_peep.json")):
        try:
            with open(peep, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue

        # Find the act node (excluded MunicipalRegulation)
        act_node = None
        for node in doc.get("@graph", []):
            types = node.get("@type") or []
            if isinstance(types, str):
                types = [types]
            if any(t in {"estleg:NationalRegulation",
                          "estleg:GovernmentRegulation",
                          "estleg:MinisterialRegulation"} for t in types):
                act_node = node
                break
        if act_node is None:
            continue

        issuer = act_node.get("estleg:issuer")
        act_num = act_node.get("estleg:actNumber")
        if not (issuer and act_num):
            continue

        xml_path = pair_peep_with_xml(peep, xml_lookup, data_dir=data_dir)
        if xml_path is None:
            continue
        adoption_date = _read_adoption_date_from_xml(xml_path)
        if adoption_date is None:
            continue

        lookup[(issuer, adoption_date, str(act_num))] = act_node["@id"]
    return lookup


def build_kov_act_lookup(
    kov_root: Path,
    data_dir: Path,
    issuers_path: Path,
) -> dict[tuple[str, str, str], str]:
    """Build (issuer_label, adoption_date, act_number) → act @id for
    KOV regulations.

    The issuer key is taken from estleg:issuer on the act node
    (which the corpus generator stamps), with the registry's
    rdfs:label as a sanity reference. adoption_date is read from
    paired XML."""
    from estleg_common import build_globalid_xml_lookup, pair_peep_with_xml

    lookup: dict[tuple[str, str, str], str] = {}
    if not kov_root.is_dir():
        return lookup

    xml_lookup = build_globalid_xml_lookup(data_dir)

    # Load issuer registry to validate the issuer label appears there
    valid_issuer_labels: set[str] = set()
    if issuers_path.exists():
        try:
            with open(issuers_path, "r", encoding="utf-8") as fh:
                idoc = json.load(fh)
            for n in idoc.get("@graph", []):
                if "estleg:Issuer" in (n.get("@type") or []):
                    label = n.get("rdfs:label")
                    if label:
                        valid_issuer_labels.add(label)
        except (json.JSONDecodeError, OSError):
            pass

    for peep in sorted(kov_root.rglob("*_peep.json")):
        if peep.name.startswith("REGULATIONS_KOV_INDEX"):
            continue
        try:
            with open(peep, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue

        act_node = None
        for node in doc.get("@graph", []):
            types = node.get("@type") or []
            if isinstance(types, str):
                types = [types]
            if "estleg:MunicipalRegulation" in types:
                act_node = node
                break
        if act_node is None:
            continue

        issuer = act_node.get("estleg:issuer")
        act_num = act_node.get("estleg:actNumber")
        if not (issuer and act_num):
            continue

        # Note: when valid_issuer_labels is non-empty, prefer matching
        # against it; otherwise accept whatever the act node says.
        if valid_issuer_labels and issuer not in valid_issuer_labels:
            # Tolerate this — the registry build may have used
            # transliterated labels while the act has the canonical
            # form. Don't reject.
            pass

        xml_path = pair_peep_with_xml(peep, xml_lookup, data_dir=data_dir)
        if xml_path is None:
            continue
        adoption_date = _read_adoption_date_from_xml(xml_path)
        if adoption_date is None:
            continue

        lookup[(issuer, adoption_date, str(act_num))] = act_node["@id"]
    return lookup


_ESTONIAN_TO_ASCII = str.maketrans({
    "õ": "o", "ä": "a", "ö": "o", "ü": "u",
    "Õ": "O", "Ä": "A", "Ö": "O", "Ü": "U",
    "š": "s", "ž": "z", "Š": "S", "Ž": "Z",
})


def _normalize_issuer_label(label: str) -> str:
    """Normalise a KOV issuer label so registry-side ASCII forms
    ('Polva Vallavalitsus') and act-side canonical forms ('Põlva
    Vallavalitsus') hash to the same key.

    Applied symmetrically: both lookup-construction and lookup-query
    sides must call this function before keying the dict.
    """
    if not label:
        return ""
    s = label.strip().translate(_ESTONIAN_TO_ASCII)
    s = re.sub(r"\s+", " ", s)
    return s.lower()


def build_kov_act_lookup_by_number(
    kov_root: Path,
) -> dict[tuple[str, str], list[str]]:
    """Build (normalized_issuer, act_number) → [act @id, ...] for KOV
    regulations.

    Used for body-text references that don't carry adoption_date.
    Multiple acts under the same issuer can share an act_number across
    historical revisions; the lookup STORES ALL CANDIDATES so the
    resolver (Task 6) can decide whether the match is unique. The
    resolver returns a target only when the candidate list contains
    exactly one entry (after scoping by enactedByMunicipality).
    """
    lookup: dict[tuple[str, str], list[str]] = {}
    if not kov_root.is_dir():
        return lookup
    for peep in sorted(kov_root.rglob("*_peep.json")):
        if peep.name.startswith("REGULATIONS_KOV_INDEX"):
            continue
        try:
            with open(peep, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue
        for node in doc.get("@graph", []):
            types = node.get("@type") or []
            if isinstance(types, str):
                types = [types]
            if "estleg:MunicipalRegulation" not in types:
                continue
            issuer = node.get("estleg:issuer")
            act_num = node.get("estleg:actNumber")
            aid = node.get("@id")
            if issuer and act_num and aid:
                key = (_normalize_issuer_label(issuer), str(act_num))
                lookup.setdefault(key, []).append(aid)
            break
    return lookup
```

The `build_kov_act_lookup` (date-keyed) and `build_state_regulation_lookup` builders also call `_normalize_issuer_label` symmetrically when constructing the key, so the resolver's query side can pass an already-normalised value without worrying about diacritic alignment. Update both functions:

```python
# In build_state_regulation_lookup, replace the final tuple-key line:
lookup[(_normalize_issuer_label(issuer), adoption_date, str(act_num))] = act_node["@id"]

# In build_kov_act_lookup, replace the final tuple-key line:
lookup[(_normalize_issuer_label(issuer), adoption_date, str(act_num))] = act_node["@id"]
```

Update each test that asserts against these lookups to use the normalised issuer key. For `test_state_regulation_lookup_reads_adoption_from_xml`:

```python
assert lookup[("vabariigi valitsus", "2019-12-19", "112")] == \
    "estleg:Reg_VV112_Map_2019"
```

For `test_kov_act_lookup_uses_registry_label`, verify against the normalised form:

```python
# The act stamps "Nõo Vallavolikogu"; the lookup key is the normalised version.
assert lookup[("noo vallavolikogu", "2020-05-15", "5")] == \
    "estleg:Reg_NOO_5_Map_2020"
```

- [ ] **Step 6: Run tests**

```
pytest tests/test_extract_cross_references.py::TestCanonicalLookups -v
```

Expected: 7 passed (the original 5 + the two new candidate-list / diacritic-normalization tests).

- [ ] **Step 7: Commit**

```bash
git add scripts/extract_cross_references.py tests/test_extract_cross_references.py
git commit -m "Add canonical act/provision/regulation lookups for Layer 2b

Extends build_provision_index to also return prefix_to_act_iri AND
act_iri_to_prefix (the reverse). The reverse map is essential
because the Task 5 resolver needs to derive a prefix from an act
IRI, and naive split-on-underscore breaks for the 34 corpus acts
whose prefix itself contains an underscore (e.g. KARIST_2_Map_2026
→ provisions live at KARIST_2_Par_<n>, not KARIST_Par_<n>).

Adds build_genitive_to_act_iri chaining FULLNAME_GENITIVE through
the abbreviation table to the new act-IRI map.

Adds build_state_regulation_lookup and build_kov_act_lookup, both
keyed by (normalized_issuer, adoption_date, act_number). adoption_date
comes from <vastuvoetud><aktikuupaev> in source XML — the corpus
has zero estleg:adoptionDate fields. Issuer labels are normalised
via _normalize_issuer_label (transliterates Estonian diacritics) so
that registry-side 'Polva Vallavalitsus' and act-side 'Põlva
Vallavalitsus' hash to the same key.

Adds build_kov_act_lookup_by_number for body-text references that
lack date context. Returns LIST-VALUED candidates per (issuer,
act_number) key — the same issuer can have several acts under the
same number across revisions, and the resolver must see all
candidates to decide whether the match is unique."
```

---

## Task 4: Citation IRI builder + reified Citation node

**Files:**
- Modify: `scripts/extract_cross_references.py`
- Modify: `tests/test_extract_cross_references.py`

Build a SHACL-valid Citation IRI from the source act @id and a sequence number, plus a node-builder that emits a reified `estleg:Citation` ready to insert into the source peep file's `@graph`. Citation IRI pattern matches the SHACL declaration from Layer 2a: `^estleg:Citation_[A-Za-z0-9_]+_[0-9]+$`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_extract_cross_references.py`:

```python
class TestCitationNodeBuilder:
    def test_iri_pattern_matches_shacl(self):
        from extract_cross_references import build_citation_iri
        # Use a corpus-shaped IRI as input — Reg_<id>_Map_<year>
        iri = build_citation_iri("estleg:Reg_1014955_Map_2026", seq=1)
        assert iri == "estleg:Citation_Reg_1014955_Map_2026_1"
        import re
        assert re.match(r"^estleg:Citation_[A-Za-z0-9_]+_[0-9]+$", iri)

    def test_citation_node_with_full_detail(self):
        from extract_cross_references import build_citation_node
        node = build_citation_node(
            iri="estleg:Citation_Reg_1014955_Map_2026_1",
            # Corpus shape for provisions is <prefix>_Par_<n> (NOT
            # <prefix>_Map_<year>_Par_<n>). The act IRI carries the
            # _Map_<year> suffix; provisions reuse the bare prefix.
            target_iri="estleg:KOKS_Par_22",
            citation_detail="lg 1 p 34",
            citation_text="kohaliku omavalitsuse korralduse seaduse § 22 lõike 1 punkti 34",
        )
        assert node["@id"] == "estleg:Citation_Reg_1014955_Map_2026_1"
        assert "estleg:Citation" in node["@type"]
        assert "owl:NamedIndividual" in node["@type"]
        assert node["estleg:citationTarget"] == {"@id": "estleg:KOKS_Par_22"}
        assert node["estleg:citationDetail"] == "lg 1 p 34"
        assert node["estleg:citationText"].startswith("kohaliku omavalitsuse")

    def test_citation_node_omits_optional_when_none(self):
        from extract_cross_references import build_citation_node
        node = build_citation_node(
            iri="estleg:Citation_Reg_X_Map_2026_1",
            target_iri="estleg:Reg_Y_Map_2020",  # act-level when no §
            citation_detail=None,
            citation_text=None,
        )
        assert "estleg:citationTarget" in node
        assert "estleg:citationDetail" not in node
        assert "estleg:citationText" not in node
```

- [ ] **Step 2: Run (expect ImportError)**

```
pytest tests/test_extract_cross_references.py::TestCitationNodeBuilder -v
```

Expected: FAIL.

- [ ] **Step 3: Implement the helpers**

Add to `scripts/extract_cross_references.py`:

```python
def build_citation_iri(source_act_iri: str, seq: int) -> str:
    """Build a SHACL-valid Citation IRI from the source act @id and a
    1-based sequence number. The 'estleg:' prefix is stripped from the
    source IRI before embedding."""
    shortid = source_act_iri.removeprefix("estleg:")
    return f"estleg:Citation_{shortid}_{seq}"


def build_citation_node(
    iri: str,
    target_iri: str,
    citation_detail: str | None,
    citation_text: str | None,
) -> dict:
    """Build a reified Citation node ready to insert into a peep file's
    @graph. Optional fields are omitted when None."""
    node: dict = {
        "@id": iri,
        "@type": ["owl:NamedIndividual", "estleg:Citation"],
        "estleg:citationTarget": {"@id": target_iri},
    }
    if citation_detail:
        node["estleg:citationDetail"] = citation_detail
    if citation_text:
        node["estleg:citationText"] = citation_text
    return node
```

- [ ] **Step 4: Run + commit**

```
pytest tests/test_extract_cross_references.py::TestCitationNodeBuilder -v
```

Expected: 3 passed.

```bash
git add scripts/extract_cross_references.py tests/test_extract_cross_references.py
git commit -m "Add Citation IRI builder + reified Citation node builder"
```

---

## Task 5: Preamble citation resolver — act-level `issuedUnder` only

**Files:**
- Modify: `scripts/extract_cross_references.py`
- Modify: `tests/test_extract_cross_references.py`

The reviewer (recommendation 4) flagged that `issuedUnder` should always point to act-level IRIs — provision detail belongs in `Citation.citationTarget`, not in `issuedUnder`. The resolver returns a tuple `(citation_target_iri, enabling_act_iri)`. Both can be different: when a preamble cites `KOKS § 22`, `citation_target_iri` is the provision (`estleg:KOKS_Map_2026_Par_22`) but `enabling_act_iri` is the act (`estleg:KOKS_Map_2026`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_extract_cross_references.py`:

```python
class TestResolvePreambleCitation:
    @pytest.fixture
    def lookups(self):
        # Fixtures use corpus-shaped IRIs throughout:
        #   act IRIs: <prefix>_Map_<year> or <prefix>_Osa<n>_...
        #   provision IRIs: <prefix>_Par_<n>  (NO _Map_<year> in the middle)
        return {
            "genitive_to_act_iri": {
                "kohaliku omavalitsuse korralduse seaduse": "estleg:KOKS_Map_2026",
                "alkoholiseaduse": "estleg:AS_Map_2026",
                "karistusseadustiku": "estleg:KARIST_2_Osa1_1_87",
            },
            "prefix_to_provisions": {
                "KOKS": {"22": "estleg:KOKS_Par_22",
                          "6": "estleg:KOKS_Par_6"},
                "AS":   {"42": "estleg:AS_Par_42"},
                "KARIST_2": {"1": "estleg:KARIST_2_Par_1"},
            },
            "act_iri_to_prefix": {
                "estleg:KOKS_Map_2026": "KOKS",
                "estleg:AS_Map_2026": "AS",
                # Multi-segment prefix — the case the v2 plan got wrong.
                "estleg:KARIST_2_Osa1_1_87": "KARIST_2",
            },
            "state_reg_lookup": {
                ("vabariigi valitsus", "2007-12-20", "251"):
                    "estleg:Reg_VV251_Map_2007",
            },
            "kov_act_lookup": {
                ("tallinna linnavolikogu", "2020-12-02", "60"):
                    "estleg:Reg_TLN60_Map_2020",
            },
        }

    def test_law_with_paragraph_returns_act_level_issuedUnder(self, lookups):
        """issuedUnder must be act-level, citation_target is the provision."""
        from extract_cross_references import resolve_preamble_citation
        cit = {
            "form": "law-genitive",
            "law_ref": "kohaliku omavalitsuse korralduse seaduse",
            "paragraphs": ["22"],
            "citationDetail": "lg 1 p 34",
            "citationText": "kohaliku omavalitsuse korralduse seaduse § 22 lõike 1 punkti 34",
        }
        result = resolve_preamble_citation(cit, **lookups)
        assert result is not None
        target_iri, enabling_act_iri = result
        # Provision-level for citation target — corpus shape is <prefix>_Par_<n>
        assert target_iri == "estleg:KOKS_Par_22"
        # Act-level for issuedUnder — this is the key Layer 2b semantic
        assert enabling_act_iri == "estleg:KOKS_Map_2026"

    def test_law_without_paragraph(self, lookups):
        """When citation has no §, both target and enabling_act are act-level."""
        from extract_cross_references import resolve_preamble_citation
        cit = {
            "form": "law-genitive",
            "law_ref": "alkoholiseaduse",
            "paragraphs": [],
            "citationDetail": None,
            "citationText": "alkoholiseaduse",
        }
        result = resolve_preamble_citation(cit, **lookups)
        assert result == ("estleg:AS_Map_2026", "estleg:AS_Map_2026")

    def test_law_with_multipart_prefix_resolves_via_reverse_map(self, lookups):
        """Reverse map (act_iri_to_prefix) MUST be consulted for the
        prefix — naive split on '_' would yield 'KARIST' for
        'estleg:KARIST_2_Osa1_1_87' and miss the real prefix
        'KARIST_2'. This is the regression that motivated the
        round-3 review (B1)."""
        from extract_cross_references import resolve_preamble_citation
        cit = {
            "form": "law-genitive",
            "law_ref": "karistusseadustiku",
            "paragraphs": ["1"],
            "citationDetail": None,
            "citationText": "karistusseadustiku § 1",
        }
        result = resolve_preamble_citation(cit, **lookups)
        assert result == (
            "estleg:KARIST_2_Par_1",       # provision under the multi-segment prefix
            "estleg:KARIST_2_Osa1_1_87",   # act IRI with the same multi-segment prefix
        )

    def test_state_regulation(self, lookups):
        from extract_cross_references import resolve_preamble_citation
        cit = {
            "form": "state-regulation-date-number",
            "issuer": "Vabariigi Valitsus",
            "adoption_date": "2007-12-20",
            "act_number": "251",
            "citationDetail": None,
            "citationText": "Vabariigi Valitsuse 20.12.2007 määrusega nr 251",
        }
        result = resolve_preamble_citation(cit, **lookups)
        # Date+number form has no provision granularity — both equal
        assert result == ("estleg:Reg_VV251_Map_2007",
                          "estleg:Reg_VV251_Map_2007")

    def test_kov_regulation(self, lookups):
        from extract_cross_references import resolve_preamble_citation
        cit = {
            "form": "kov-regulation-date-number",
            "issuer": "Tallinna Linnavolikogu",
            "adoption_date": "2020-12-02",
            "act_number": "60",
            "citationDetail": None,
            "citationText": "Tallinna Linnavolikogu 2. detsembri 2010 määruse nr 60",
        }
        result = resolve_preamble_citation(cit, **lookups)
        assert result == ("estleg:Reg_TLN60_Map_2020",
                          "estleg:Reg_TLN60_Map_2020")

    def test_unresolved_returns_none(self, lookups):
        from extract_cross_references import resolve_preamble_citation
        cit = {
            "form": "law-genitive",
            "law_ref": "ettetuntmatu_seaduse",
            "paragraphs": ["1"],
            "citationDetail": None,
            "citationText": "ettetuntmatu seaduse § 1",
        }
        result = resolve_preamble_citation(cit, **lookups)
        assert result is None
```

- [ ] **Step 2: Run + implement**

```python
def resolve_preamble_citation(
    cit: dict,
    *,
    genitive_to_act_iri: dict[str, str],
    prefix_to_provisions: dict[str, dict[str, str]],
    act_iri_to_prefix: dict[str, str],
    state_reg_lookup: dict[tuple[str, str, str], str],
    kov_act_lookup: dict[tuple[str, str, str], str],
) -> tuple[str, str] | None:
    """Resolve a preamble citation to (citation_target_iri, enabling_act_iri).

    citation_target_iri:
        - paragraph-level provision IRI for law citations with §
        - act-level IRI otherwise

    enabling_act_iri:
        - ALWAYS act-level. Layer 2b's issuedUnder semantic is "act
          enacted under THIS act" — the range is Act, not LegalProvision.

    Returns None when the citation can't be resolved against any lookup.
    """
    form = cit.get("form")

    if form == "law-genitive":
        genitive = cit.get("law_ref", "").lower()
        act_iri = genitive_to_act_iri.get(genitive)
        if act_iri is None:
            return None
        # If no paragraph, both target and enabling_act are act-level.
        if not cit.get("paragraphs"):
            return (act_iri, act_iri)
        # Resolve paragraph via prefix_to_provisions. The prefix MUST
        # come from act_iri_to_prefix — naive split on '_' breaks for
        # 34 corpus acts whose prefix itself contains underscores
        # (e.g. estleg:KARIST_2_Osa1_1_87 → prefix is 'KARIST_2',
        # NOT 'KARIST').
        prefix = act_iri_to_prefix.get(act_iri)
        if prefix is None:
            # Act IRI not in the prefix index — fall back to act-level.
            return (act_iri, act_iri)
        par = cit["paragraphs"][0]
        target = prefix_to_provisions.get(prefix, {}).get(par)
        if target is None:
            # Paragraph not in provision index — fall back to act-level
            # for citation_target while keeping enabling_act_iri at act.
            return (act_iri, act_iri)
        return (target, act_iri)

    if form == "state-regulation-date-number":
        # Issuer is normalised by the parser side; lookup keys are
        # also normalised at construction time (Task 3).
        from estleg_common import sanitize_id  # already imported in module
        # Reuse _normalize_issuer_label so query-side and build-side
        # hash to the same key.
        key = (
            _normalize_issuer_label(cit["issuer"]),
            cit["adoption_date"],
            cit["act_number"],
        )
        act_iri = state_reg_lookup.get(key)
        if act_iri is None:
            return None
        return (act_iri, act_iri)

    if form == "kov-regulation-date-number":
        key = (
            _normalize_issuer_label(cit["issuer"]),
            cit["adoption_date"],
            cit["act_number"],
        )
        act_iri = kov_act_lookup.get(key)
        if act_iri is None:
            return None
        return (act_iri, act_iri)

    return None
```

```
pytest tests/test_extract_cross_references.py::TestResolvePreambleCitation -v
```

Expected: 6 passed (the original 5 + the new `test_law_with_multipart_prefix_resolves_via_reverse_map`).

- [ ] **Step 3: Commit**

```bash
git add scripts/extract_cross_references.py tests/test_extract_cross_references.py
git commit -m "Resolve preamble citations to act-level issuedUnder + provision-level citationTarget

issuedUnder always points to the enabling act IRI (act-level only;
range is Act). Provision granularity rides on Citation.citationTarget."
```

---

## Task 6: KOV body-text reference parser + scope rules

**Files:**
- Modify: `scripts/extract_cross_references.py`
- Modify: `tests/test_extract_cross_references.py`
- Create: `tests/fixtures/kov_layer2b/sample_kov_with_internal_ref.json`

The reviewer's blocker B5: the v1 plan created `resolve_kov_internal_act_ref` but never parsed body-text KOV references or wired the resolver into `process_law_file`. This task does both.

- [ ] **Step 1: Add the body-text reference parser**

Add to `scripts/extract_cross_references.py`:

```python
# Body-text KOV reference pattern. KOV body text typically cites by
# issuer + act number (no date in body refs, unlike preambles).
# Example: "Tallinna Linnavolikogu määruse nr 15 § 4"
# Or generic: "linnavolikogu määrus nr 5"
_PAT_KOV_BODY_REF = re.compile(
    r"(?:(?P<issuer>(?:[A-ZÕÄÖÜŠŽ][a-zõäöüšž\-]+\s+)*)\s*)?"
    r"(?P<body>(?:[Ll]innavolikogu|[Ll]innavalitsuse?|"
    r"[Vv]allavolikogu|[Vv]allavalitsuse?|"
    r"[Aa]levivolikogu))\s+"
    r"määrus(?:e(?:ga)?)?\s+nr\s*(?P<num>\d+)",
    re.UNICODE,
)


def extract_kov_act_refs_from_text(text: str) -> list[dict]:
    """Parse body text for KOV-act references.

    Returns a list of dicts with:
        issuer    : full issuer label normalised via _normalize_issuer_label,
                    or None (when only generic body word like 'linnavolikogu'
                    is used without a place name)
        body_type : 'volikogu' | 'valitsus' | None
        act_number: digit string
        text      : matched substring (preserved verbatim from input)
    """
    if not text:
        return []
    out: list[dict] = []
    for m in _PAT_KOV_BODY_REF.finditer(text):
        body = m.group("body").lower()
        body_type = "volikogu" if "volikogu" in body else "valitsus"
        issuer_full = m.group("issuer")
        if issuer_full and issuer_full.strip():
            # Issuer prefix present — combine into a single label and
            # normalise diacritics + case for the lookup key. The
            # registry side and act side both store normalised labels
            # so this can be a direct dict lookup downstream.
            raw_label = (issuer_full.strip() + " " + m.group("body").strip())
            issuer_label = _normalize_issuer_label(raw_label)
        else:
            issuer_label = None
        out.append({
            "issuer": issuer_label,
            "body_type": body_type,
            "act_number": m.group("num"),
            "text": m.group(0).strip(),
        })
    return out
```

- [ ] **Step 2: Add the scoped resolver**

Add `resolve_kov_internal_act_ref` — municipality-first, with explicit-issuer fast path:

```python
def resolve_kov_internal_act_ref(
    *,
    source_municipality: str | None,
    explicit_issuer: str | None,
    body_type: str | None,
    act_number: str,
    kov_act_lookup_by_number: dict[tuple[str, str], list[str]],
    issuer_registry: dict[str, tuple[str, str, str]],
) -> str | None:
    """Resolve a KOV-internal act reference by act_number, scoped to the
    source act's municipality.

    Args:
        source_municipality: the @id of the source act's
            ``enactedByMunicipality``. None disables scoping.
        explicit_issuer: a pre-normalised issuer label parsed from the
            body-text (e.g. 'tallinna linnavolikogu'). When present,
            the resolver narrows directly to that issuer instead of
            enumerating every issuer in the source municipality. When
            None, the resolver falls back to body-type-scoped enumeration.
        body_type: 'volikogu' | 'valitsus' | None — secondary scope.
        act_number: digit string from the body-text reference.
        kov_act_lookup_by_number: (issuer_label_normalized, act_number)
            → list of candidate act @ids (Task 3 stores all candidates).
        issuer_registry: issuer @id → (label_normalized, municipality @id,
            body_type). The label is the rdfs:label from
            issuers_kov_peep.json passed through _normalize_issuer_label.

    Returns the resolved target @id only when the candidate list
    narrows to exactly one entry. Otherwise None.
    """
    if source_municipality is None:
        return None

    # Path A: explicit issuer string from the body text — the most
    # reliable disambiguator. Verify it belongs to the source
    # municipality (cross-municipality citations should be skipped).
    if explicit_issuer is not None:
        municipality_for_issuer = None
        for _iri, (label, mun_iri, _btype) in issuer_registry.items():
            if label == explicit_issuer:
                municipality_for_issuer = mun_iri
                break
        if municipality_for_issuer != source_municipality:
            return None
        candidates = kov_act_lookup_by_number.get(
            (explicit_issuer, act_number), []
        )
        return candidates[0] if len(candidates) == 1 else None

    # Path B: no explicit issuer — enumerate issuers in the source
    # municipality, narrow by body_type when supplied.
    candidate_labels: list[str] = []
    for _iri, (label, mun_iri, btype) in issuer_registry.items():
        if mun_iri != source_municipality:
            continue
        if body_type is not None and btype != body_type:
            continue
        candidate_labels.append(label)

    matches: list[str] = []
    for label in candidate_labels:
        matches.extend(
            kov_act_lookup_by_number.get((label, act_number), [])
        )
    # Dedupe + uniqueness check — body text isn't reliable enough to
    # pick a winner among ambiguous matches.
    unique = sorted(set(matches))
    return unique[0] if len(unique) == 1 else None


def build_issuer_registry(issuers_path: Path) -> dict[str, tuple[str, str, str]]:
    """Load issuers_kov_peep.json and return
    {issuer @id → (label_normalized, currentMunicipality @id, bodyType)}.

    The label is rdfs:label passed through _normalize_issuer_label so
    it hashes the same way as labels emitted by extract_kov_act_refs_from_text
    and stored as keys in build_kov_act_lookup_by_number. Layer 1's
    slug→display derivation produces 'Noo Vallavolikogu' for slug 'noo'
    instead of the canonical 'Nõo Vallavolikogu'; normalisation absorbs
    that mismatch.
    """
    out: dict[str, tuple[str, str, str]] = {}
    if not issuers_path.exists():
        return out
    try:
        with open(issuers_path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return out
    for node in doc.get("@graph", []):
        types = node.get("@type") or []
        if isinstance(types, str):
            types = [types]
        if "estleg:Issuer" not in types:
            continue
        iri = node.get("@id")
        label = node.get("rdfs:label")
        body_type = node.get("estleg:bodyType")
        municipality = node.get("estleg:currentMunicipality")
        if isinstance(municipality, dict):
            municipality = municipality.get("@id")
        if iri and label and body_type and municipality:
            out[iri] = (_normalize_issuer_label(label), municipality, body_type)
    return out
```

- [ ] **Step 3: Write the tests**

Append to `tests/test_extract_cross_references.py`:

```python
class TestExtractKovActRefsFromText:
    def test_parses_full_issuer_label(self):
        from extract_cross_references import extract_kov_act_refs_from_text
        refs = extract_kov_act_refs_from_text(
            "Vastavalt Tallinna Linnavolikogu määruse nr 15 § 4 lõikele 1 ..."
        )
        assert len(refs) == 1
        assert refs[0]["issuer"] == "tallinna linnavolikogu"  # normalised
        assert refs[0]["body_type"] == "volikogu"
        assert refs[0]["act_number"] == "15"

    def test_parses_generic_body_word_no_place_name(self):
        from extract_cross_references import extract_kov_act_refs_from_text
        refs = extract_kov_act_refs_from_text(
            "linnavolikogu määrus nr 5 sätestab täpsemad tingimused."
        )
        assert len(refs) == 1
        assert refs[0]["issuer"] is None         # no place name → ambiguous
        assert refs[0]["body_type"] == "volikogu"
        assert refs[0]["act_number"] == "5"

    def test_parses_diacritic_issuer(self):
        from extract_cross_references import extract_kov_act_refs_from_text
        refs = extract_kov_act_refs_from_text(
            "Põlva Vallavalitsuse määrusega nr 3 kehtestatud korras."
        )
        assert len(refs) == 1
        assert refs[0]["issuer"] == "polva vallavalitsus"  # normalised + transliterated
        assert refs[0]["body_type"] == "valitsus"
        assert refs[0]["act_number"] == "3"

    def test_no_match_returns_empty(self):
        from extract_cross_references import extract_kov_act_refs_from_text
        assert extract_kov_act_refs_from_text("Plain text.") == []
        assert extract_kov_act_refs_from_text("") == []


class TestKovBodyTextScope:
    @pytest.fixture
    def fixture_data(self):
        # Two acts under different municipalities. Both have the same
        # actNumber to expose any cross-municipality leakage.
        kov_act_lookup_by_number = {
            ("tallinna linnavolikogu", "15"): ["estleg:Reg_TLN15_Map_2020"],
            ("tartu linnavolikogu", "15"): ["estleg:Reg_TRT15_Map_2018"],
            # Ambiguous case — same issuer reused number 7 across revisions
            ("polva vallavolikogu", "7"): [
                "estleg:Reg_POLVA7_Map_2010",
                "estleg:Reg_POLVA7_Map_2018",
            ],
        }
        issuer_registry = {
            "estleg:Issuer_tallinna_linnavolikogu": (
                "tallinna linnavolikogu",
                "estleg:Municipality_tallinn", "volikogu",
            ),
            "estleg:Issuer_tartu_linnavolikogu": (
                "tartu linnavolikogu",
                "estleg:Municipality_tartu", "volikogu",
            ),
            "estleg:Issuer_polva_vallavolikogu": (
                "polva vallavolikogu",
                "estleg:Municipality_polva_vald", "volikogu",
            ),
        }
        return kov_act_lookup_by_number, issuer_registry

    def test_explicit_issuer_in_same_municipality_resolves(self, fixture_data):
        from extract_cross_references import resolve_kov_internal_act_ref
        lookup, registry = fixture_data
        target = resolve_kov_internal_act_ref(
            source_municipality="estleg:Municipality_tallinn",
            explicit_issuer="tallinna linnavolikogu",
            body_type="volikogu",
            act_number="15",
            kov_act_lookup_by_number=lookup,
            issuer_registry=registry,
        )
        assert target == "estleg:Reg_TLN15_Map_2020"

    def test_explicit_issuer_in_different_municipality_returns_none(
        self, fixture_data
    ):
        """Tallinn act citing 'Tartu Linnavolikogu' explicitly is
        cross-municipality — skip rather than resolve."""
        from extract_cross_references import resolve_kov_internal_act_ref
        lookup, registry = fixture_data
        target = resolve_kov_internal_act_ref(
            source_municipality="estleg:Municipality_tallinn",
            explicit_issuer="tartu linnavolikogu",
            body_type="volikogu",
            act_number="15",
            kov_act_lookup_by_number=lookup,
            issuer_registry=registry,
        )
        assert target is None

    def test_generic_body_word_resolves_via_municipality_scope(
        self, fixture_data
    ):
        """No explicit issuer → enumerate volikogus in source
        municipality. Tallinn has only one volikogu, so this resolves
        uniquely."""
        from extract_cross_references import resolve_kov_internal_act_ref
        lookup, registry = fixture_data
        target = resolve_kov_internal_act_ref(
            source_municipality="estleg:Municipality_tallinn",
            explicit_issuer=None,
            body_type="volikogu",
            act_number="15",
            kov_act_lookup_by_number=lookup,
            issuer_registry=registry,
        )
        assert target == "estleg:Reg_TLN15_Map_2020"

    def test_ambiguous_candidates_returns_none(self, fixture_data):
        """When the issuer has reused the act_number across revisions,
        the resolver MUST abstain rather than pick a 'latest by file
        order' winner."""
        from extract_cross_references import resolve_kov_internal_act_ref
        lookup, registry = fixture_data
        target = resolve_kov_internal_act_ref(
            source_municipality="estleg:Municipality_polva_vald",
            explicit_issuer="polva vallavolikogu",
            body_type="volikogu",
            act_number="7",
            kov_act_lookup_by_number=lookup,
            issuer_registry=registry,
        )
        assert target is None
```

- [ ] **Step 4: Verify + commit**

```
pytest tests/test_extract_cross_references.py::TestExtractKovActRefsFromText -v
pytest tests/test_extract_cross_references.py::TestKovBodyTextScope -v
```

Expected: 4 + 4 = 8 passed.

```bash
git add scripts/extract_cross_references.py tests/test_extract_cross_references.py
git commit -m "Add KOV body-text reference parser + municipality-scoped resolver

extract_kov_act_refs_from_text parses body text for citations to
'<Issuer> määruse nr N' or generic '<body> määrus nr N'.
resolve_kov_internal_act_ref scopes resolution to the source act's
municipality first; cross-municipality matches are skipped to avoid
Tallinn->Tartu false positives."
```

---

## Task 7: Wire preamble pass + body-text scope into `extract_cross_references.main()`

**Files:**
- Modify: `scripts/extract_cross_references.py`
- Modify: `tests/test_extract_cross_references.py`

Two passes get added: (a) preamble scanning + Citation node emission, (b) body-text KOV scope rules invoked from `process_law_file`.

- [ ] **Step 1: Build all the lookups at startup**

Inside `main()`, after the existing `build_provision_index()` call (around line 405) — which now returns 5 values:

```python
    (prefix_to_provisions, source_act_to_prefix, iri_to_file,
     prefix_to_act_iri, act_iri_to_prefix) = build_provision_index()

    # Layer 2b: canonical lookups for the preamble + body-text passes
    genitive_to_act_iri = build_genitive_to_act_iri(
        source_act_to_prefix=source_act_to_prefix,
        prefix_to_act_iri=prefix_to_act_iri,
    )

    riik_root = KRR_DIR / "regulations" / "riik"
    kov_root = KRR_DIR / "regulations" / "kov"
    issuers_path = KRR_DIR / "issuers_kov_peep.json"
    state_reg_lookup = build_state_regulation_lookup(riik_root, DATA_DIR)
    kov_act_lookup = build_kov_act_lookup(kov_root, DATA_DIR, issuers_path)
    kov_act_lookup_by_number = build_kov_act_lookup_by_number(kov_root)
    issuer_registry = build_issuer_registry(issuers_path)
    print(f"  state-reg lookup: {len(state_reg_lookup)} entries")
    print(f"  KOV act lookup (date-keyed): {len(kov_act_lookup)} entries")
    print(f"  KOV act lookup (number-only): {len(kov_act_lookup_by_number)} keys")
```

(`DATA_DIR` is the existing `data/riigiteataja/` constant; it should already be defined in the script.)

- [ ] **Step 2: Add the preamble pass to `main()`**

Insert after the existing body-text references logic but before the final report-write. The pass iterates every peep file once, parses its `estleg:preambleText`, resolves each citation to `(target, enabling_act)`, and emits `estleg:issuedUnder` + `estleg:implementsCitation` + reified `estleg:Citation` nodes onto the act node. Idempotency: prior pass output is cleared first.

**Important:** because we are extending `extract_cross_references.py` itself, **DO NOT** add `from extract_cross_references import …` anywhere in this file. The helpers (`extract_preamble_citations`, `resolve_preamble_citation`, `build_citation_iri`, `build_citation_node`) are already in module scope from the earlier tasks.

```python
    # Layer 2b: preamble pass — issuedUnder + implementsCitation + Citation
    print("\nLayer 2b: scanning preambles for issuedUnder citations...")
    preamble_files_processed: set[Path] = set()
    preamble_files_with_output: set[Path] = set()
    preamble_files_kov_processed: set[Path] = set()
    preamble_files_kov_with_output: set[Path] = set()
    preamble_triples_emitted = 0
    preamble_triples_emitted_kov = 0
    preamble_citations_resolved = 0
    preamble_citations_unresolved = 0

    for peep in iter_peep_files():
        is_kov = "regulations/kov/" in str(peep)
        try:
            with open(peep, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue
        graph = doc.get("@graph", [])
        if not graph:
            continue

        # Find the act node (owl:Ontology-typed). Skip the file if absent.
        act_node = None
        for n in graph:
            types = n.get("@type") or []
            if isinstance(types, str):
                types = [types]
            if "owl:Ontology" in types and n.get("@id"):
                act_node = n
                break
        if act_node is None:
            continue
        source_act_iri = act_node["@id"]

        preamble_text = act_node.get("estleg:preambleText")
        if not preamble_text:
            continue

        preamble_files_processed.add(peep)
        if is_kov:
            preamble_files_kov_processed.add(peep)

        # Idempotency: clear prior pass output before regenerating.
        act_node.pop("estleg:issuedUnder", None)
        act_node.pop("estleg:implementsCitation", None)
        graph[:] = [n for n in graph
                    if not (isinstance(n.get("@id"), str)
                            and n["@id"].startswith(
                                f"estleg:Citation_{source_act_iri.removeprefix('estleg:')}_"
                            ))]

        # Re-find act_node after the comprehension — safe because we
        # only filtered Citation_* nodes.
        for n in graph:
            if n.get("@id") == source_act_iri:
                act_node = n
                break

        citations = extract_preamble_citations(preamble_text)
        if not citations:
            continue

        issued_under_set: set[str] = set()
        implements_citation_refs: list[dict] = []
        new_citation_nodes: list[dict] = []

        for seq, cit in enumerate(citations, start=1):
            resolved = resolve_preamble_citation(
                cit,
                genitive_to_act_iri=genitive_to_act_iri,
                prefix_to_provisions=prefix_to_provisions,
                act_iri_to_prefix=act_iri_to_prefix,
                state_reg_lookup=state_reg_lookup,
                kov_act_lookup=kov_act_lookup,
            )
            if resolved is None:
                preamble_citations_unresolved += 1
                continue
            preamble_citations_resolved += 1
            target_iri, enabling_act_iri = resolved
            issued_under_set.add(enabling_act_iri)

            # Emit a reified Citation node only when the citation has
            # paragraph or detail granularity.
            if cit.get("paragraphs") or cit.get("citationDetail"):
                citation_iri = build_citation_iri(source_act_iri, seq)
                citation_node = build_citation_node(
                    iri=citation_iri,
                    target_iri=target_iri,
                    citation_detail=cit.get("citationDetail"),
                    citation_text=cit.get("citationText"),
                )
                new_citation_nodes.append(citation_node)
                implements_citation_refs.append({"@id": citation_iri})

        if issued_under_set:
            act_node["estleg:issuedUnder"] = [
                {"@id": iri} for iri in sorted(issued_under_set)
            ]
            preamble_triples_emitted += len(issued_under_set)
            if is_kov:
                preamble_triples_emitted_kov += len(issued_under_set)

        if implements_citation_refs:
            act_node["estleg:implementsCitation"] = implements_citation_refs
            graph.extend(new_citation_nodes)
            preamble_triples_emitted += len(implements_citation_refs)
            if is_kov:
                preamble_triples_emitted_kov += len(implements_citation_refs)

        if issued_under_set or implements_citation_refs:
            preamble_files_with_output.add(peep)
            if is_kov:
                preamble_files_kov_with_output.add(peep)
            save_json(peep, doc)

    print(f"  preamble files processed: {len(preamble_files_processed)} "
          f"(KOV: {len(preamble_files_kov_processed)})")
    print(f"  preamble files with output: {len(preamble_files_with_output)} "
          f"(KOV: {len(preamble_files_kov_with_output)})")
    print(f"  preamble triples emitted: {preamble_triples_emitted} "
          f"(KOV: {preamble_triples_emitted_kov})")
    print(f"  preamble citations resolved/unresolved: "
          f"{preamble_citations_resolved}/{preamble_citations_unresolved}")
```

- [ ] **Step 3: Wire body-text KOV scope into `process_law_file` (with stat updates)**

`process_law_file` already iterates provisions and parses citations from `legalText`/`summary`. Extend it to ALSO parse KOV body-text refs and resolve them via the scoped resolver. **Critical:** the new branch MUST set `modified = True` when it adds refs, and bump `stats["citations_found"]`, `stats["citations_resolved"]`, `stats["provisions_with_refs"]` so the file save trigger fires AND the file shows up in the coverage report.

In `process_law_file` (around line 299):

```python
def process_law_file(
    json_file: Path,
    abbrev_to_prefix: dict[str, str],
    prefix_to_provisions: dict[str, dict[str, str]],
    iri_to_file: dict[str, Path],
    *,
    kov_act_lookup_by_number: dict[tuple[str, str], list[str]] | None = None,
    issuer_registry: dict[str, tuple[str, str, str]] | None = None,
) -> dict:
    # ... existing body-text scanning that runs the per-provision
    # extract_citations_from_text + resolve_citation flow ...
    # ... existing logic sets `modified = True` and bumps
    # stats["citations_found"], stats["citations_resolved"],
    # stats["provisions_with_refs"] for in-law citations ...

    # Layer 2b: KOV body-text refs scoped to source municipality.
    # Runs AFTER the existing in-law citation pass on the same graph.
    if kov_act_lookup_by_number is not None:
        # Read the source act's municipality (may be None for laws or
        # for state regulations that aren't municipality-bound).
        source_municipality = None
        for node in graph:
            types = node.get("@type") or []
            if isinstance(types, str):
                types = [types]
            if "estleg:MunicipalRegulation" in types:
                ebm = node.get("estleg:enactedByMunicipality")
                if isinstance(ebm, dict):
                    source_municipality = ebm.get("@id")
                elif isinstance(ebm, str):
                    source_municipality = ebm
                break

        # Per provision, parse body-text KOV refs and resolve.
        for prov_node in graph:
            if "estleg:paragrahv" not in prov_node:
                continue
            text_parts = []
            lt = prov_node.get("estleg:legalText")
            if isinstance(lt, str):
                text_parts.append(lt)
            sm = prov_node.get("estleg:summary")
            if isinstance(sm, str):
                text_parts.append(sm)
            text = " ".join(text_parts)
            kov_refs = extract_kov_act_refs_from_text(text)
            if not kov_refs:
                continue

            # Treat every parsed KOV body-text ref as a "found" citation;
            # whether resolved or not, this is real signal in the coverage
            # report (matches the convention from the in-law branch).
            stats["citations_found"] += len(kov_refs)

            new_targets: list[str] = []
            for ref in kov_refs:
                target = resolve_kov_internal_act_ref(
                    source_municipality=source_municipality,
                    explicit_issuer=ref["issuer"],
                    body_type=ref["body_type"],
                    act_number=ref["act_number"],
                    kov_act_lookup_by_number=kov_act_lookup_by_number,
                    issuer_registry=issuer_registry or {},
                )
                if target is not None:
                    new_targets.append(target)

            if not new_targets:
                continue

            # Append to existing references (preserving the in-law refs)
            # and dedupe.
            refs = prov_node.get("estleg:references")
            if refs is None:
                refs = []
                prov_node["estleg:references"] = refs
            elif isinstance(refs, dict):
                refs = [refs]
                prov_node["estleg:references"] = refs

            had_new = False
            for tgt in new_targets:
                if {"@id": tgt} not in refs:
                    refs.append({"@id": tgt})
                    had_new = True

            if had_new:
                stats["citations_resolved"] += len(new_targets)
                # Only count the provision the FIRST time a Layer 2b ref
                # lands on it (the in-law pass may have already counted it).
                if not prov_node.get("_layer2b_counted"):
                    stats["provisions_with_refs"] += 1
                    prov_node["_layer2b_counted"] = True
                modified = True

    # Strip the temporary marker before returning.
    for prov_node in graph:
        prov_node.pop("_layer2b_counted", None)

    if modified:
        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
            f.write("\n")
    # ... existing return ...
```

(The existing `process_law_file` already does the `if modified: save` step. The patch above shows the new KOV branch threading INTO that flow — the actual file save remains a single block at the function tail, not duplicated. Place the strip-marker loop just before the existing `if modified: save` line.)

Pass the new args at the call site (around line 474):

```python
    stats = process_law_file(
        json_file, abbrev_to_prefix, prefix_to_provisions, iri_to_file,
        kov_act_lookup_by_number=kov_act_lookup_by_number,
        issuer_registry=issuer_registry,
    )
```

- [ ] **Step 4: Add the integration test for KOV-only-body-text save**

Append to `tests/test_extract_cross_references.py`:

```python
class TestKovBodyTextSavesFile:
    """A KOV peep that gains references ONLY from the new Layer 2b
    body-text branch must still be saved and counted. Tests that
    `modified = True` and the three counters fire even when the
    in-law branch found nothing."""

    def test_kov_only_body_text_triggers_save(self, tmp_path):
        from extract_cross_references import process_law_file

        # Stage a KOV peep whose body text references a same-municipality
        # act by issuer + number, with no in-law citations.
        peep = tmp_path / "kov_peep.json"
        peep.write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Reg_TLN_Test_Map_2024",
                 "@type": ["owl:Ontology", "estleg:MunicipalRegulation"],
                 "estleg:enactedByMunicipality": {
                     "@id": "estleg:Municipality_tallinn"},
                 "estleg:issuer": "Tallinna Linnavolikogu",
                 "estleg:actNumber": "999"},
                {"@id": "estleg:Reg_TLN_Test_Map_2024_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 1",
                 "estleg:legalText":
                    "Vastavalt Tallinna Linnavolikogu määruse nr 15 § 4 ..."},
            ],
        }), encoding="utf-8")

        kov_lookup = {
            ("tallinna linnavolikogu", "15"): ["estleg:Reg_TLN15_Map_2020"],
        }
        registry = {
            "estleg:Issuer_tallinna_linnavolikogu": (
                "tallinna linnavolikogu",
                "estleg:Municipality_tallinn", "volikogu",
            ),
        }

        stats = process_law_file(
            peep,
            abbrev_to_prefix={},
            prefix_to_provisions={},
            iri_to_file={},
            kov_act_lookup_by_number=kov_lookup,
            issuer_registry=registry,
        )

        # File must have been saved with the new ref, and stats must
        # reflect that the KOV-only branch fired.
        assert stats["citations_found"] >= 1
        assert stats["citations_resolved"] >= 1
        assert stats["provisions_with_refs"] >= 1

        with open(peep, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        prov = next(n for n in saved["@graph"]
                    if n.get("@id", "").endswith("_Par_1"))
        refs = prov.get("estleg:references", [])
        if isinstance(refs, dict):
            refs = [refs]
        assert {"@id": "estleg:Reg_TLN15_Map_2020"} in refs
        # Temporary marker MUST NOT be persisted.
        assert "_layer2b_counted" not in prov
```

- [ ] **Step 5: Run + commit**

```
pytest tests/test_extract_cross_references.py -v
```

All test classes pass.

```bash
git add scripts/extract_cross_references.py tests/test_extract_cross_references.py
git commit -m "Wire preamble pass + body-text KOV scope into extract_cross_references main()

The preamble pass scans every peep file's preambleText, resolves
each citation to (target_iri, enabling_act_iri), emits issuedUnder
+ implementsCitation + reified Citation nodes, and tracks coverage
counters (overall + KOV-specific).

The body-text branch in process_law_file extends the existing in-law
citation flow with KOV act references scoped by enactedByMunicipality.
Sets modified = True and bumps citations_found/resolved/
provisions_with_refs so files that gain ONLY KOV body-text refs are
still saved and counted in the coverage report (regression flagged
in the round-3 review)."
```

---

## Task 8: Coverage instrumentation (set-based dedup, KOV-by-source-path)

**Files:**
- Modify: `scripts/extract_cross_references.py`

Apply the canonical coverage instrumentation pattern, using SETS for the processed/with-output counters so the body-text and preamble passes don't double-count files. KOV attribution is by source act path (the file we're scanning), NOT the output target's path.

Pattern:
- `_files_processed_kov` is a `set[Path]` of KOV peep paths that the pipeline reached
- `_files_with_output_kov` is a `set[Path]` of KOV peep paths whose pipeline emitted ≥1 triple
- At report-write, convert to `len(...)` for the report fields

This avoids the bug where a KOV file that produces both body-text references AND preamble triples gets counted twice.

- [ ] **Step 1: Add the CoverageReport-style accumulator**

Above `main()`, near the existing report dataclasses:

```python
from dataclasses import dataclass, field


@dataclass
class CoverageReport:
    """Layer 2a/2b canonical coverage instrumentation. KOV-specific
    counters are independent of overall counters so the gate check
    in validate_all can fail when the KOV side produces nothing."""
    pipeline: str
    input_files: int = 0
    input_files_kov: int = 0
    files_processed: set = field(default_factory=set)
    files_with_output: set = field(default_factory=set)
    files_processed_kov: set = field(default_factory=set)
    files_with_output_kov: set = field(default_factory=set)
    triples_emitted: int = 0
    triples_emitted_kov: int = 0
    errors: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "pipeline": self.pipeline,
            "input_files": self.input_files,
            "input_files_kov": self.input_files_kov,
            "files_processed": len(self.files_processed),
            "files_with_output": len(self.files_with_output),
            "files_processed_kov": len(self.files_processed_kov),
            "files_with_output_kov": len(self.files_with_output_kov),
            "triples_emitted": self.triples_emitted,
            "triples_emitted_kov": self.triples_emitted_kov,
            "error_count": len(self.errors),
            "errors": self.errors[:50],  # cap to keep report size sane
        }
```

- [ ] **Step 2: Wire the accumulator through `main()`**

```python
    coverage = CoverageReport(pipeline="extract_cross_references")
    coverage.input_files = sum(1 for _ in iter_peep_files())
    coverage.input_files_kov = sum(
        1 for p in iter_peep_files()
        if "regulations/kov/" in str(p)
    )

    # ... existing in-law citation pass and Layer 2b passes ...
    # Each pass increments coverage.files_processed{,_kov},
    # coverage.files_with_output{,_kov}, coverage.triples_emitted{,_kov}
    # via .add(peep) on the sets and += on the int counters.

    # The body-text branch and preamble branch both call:
    coverage.files_processed.add(peep)
    if is_kov:
        coverage.files_processed_kov.add(peep)

    if produced_output_for_this_file:
        coverage.files_with_output.add(peep)
        if is_kov:
            coverage.files_with_output_kov.add(peep)

    coverage.triples_emitted += n_triples_this_file
    if is_kov:
        coverage.triples_emitted_kov += n_triples_this_file
```

- [ ] **Step 3: Write the coverage report**

```python
    report_dir = KRR_DIR / "reports" / "kov"
    report_dir.mkdir(parents=True, exist_ok=True)
    out_path = report_dir / "extract_cross_references_coverage.json"
    save_json(out_path, coverage.to_dict())
    print(f"\nCoverage report: {out_path}")
    print(f"  input_files: {coverage.input_files} "
          f"(KOV: {coverage.input_files_kov})")
    print(f"  files_processed: {len(coverage.files_processed)} "
          f"(KOV: {len(coverage.files_processed_kov)})")
    print(f"  files_with_output: {len(coverage.files_with_output)} "
          f"(KOV: {len(coverage.files_with_output_kov)})")
    print(f"  triples_emitted: {coverage.triples_emitted} "
          f"(KOV: {coverage.triples_emitted_kov})")

    # Gate check — fails the run when KOV side produced nothing.
    if (coverage.input_files_kov >= 11000
            and len(coverage.files_with_output_kov) == 0):
        print("\nGATE FAIL: KOV files were processed but none produced output.")
        return 1
    if coverage.triples_emitted_kov == 0 and coverage.input_files_kov >= 11000:
        print("\nGATE FAIL: zero KOV triples emitted.")
        return 1
    print("\nGATE OK")
    return 0
```

- [ ] **Step 4: Verify**

```
python3 -c "import ast; ast.parse(open('scripts/extract_cross_references.py').read())" && echo OK
pytest tests/test_extract_cross_references.py -v
```

- [ ] **Step 5: Commit**

```bash
git add scripts/extract_cross_references.py
git commit -m "Add coverage instrumentation to extract_cross_references (set-based dedup)

CoverageReport tracks files_processed/with_output as set[Path] so
the body-text and preamble passes don't double-count files. KOV
attribution is by source act path (the file we're scanning) — the
gate fails when KOV input is non-trivial but output is zero."
```

---

## Task 9: Unpin `extract_cross_references`

```bash
grep -n "include_kov=False" scripts/extract_cross_references.py
```

Remove all 3 pins. Verify syntax:

```bash
python3 -c "import ast; ast.parse(open('scripts/extract_cross_references.py').read())" && echo OK
pytest -q
```

Expected: OK + tests still passing.

```bash
git add scripts/extract_cross_references.py
git commit -m "Unpin extract_cross_references — runs KOV by default"
```

---

## Task 10: Update `generate_inverse_references` for `implementedBy` + body-text-exclusion E2E test + act-level iri_to_file index

**Files:**
- Modify: `scripts/generate_inverse_references.py`
- Create: `tests/test_generate_inverse_references.py`

`implementedBy` is a filtered projection from `issuedUnder` and `Citation.citationTarget` — explicitly NOT from body-text `references`. Round-3 review item 7 also flagged that `collect_all_references` currently indexes only `_Par_` and `RK_` IRIs in `iri_to_file` (line 152 of `generate_inverse_references.py`), so KOV body-text references that resolve to ACT-LEVEL IRIs like `estleg:Reg_..._Map_2026` produce unresolved warnings and never gain a `referencedBy` triple in the target file. This task fixes both.

- [ ] **Step 1: Index act-level nodes in `iri_to_file`**

Find `collect_all_references` in `scripts/generate_inverse_references.py` (around line 116). The current logic:

```python
            # Register this IRI's file location
            if "_Par_" in node_id or "RK_" in node_id:
                iri_to_file[node_id] = json_file
```

is too narrow. Replace with:

```python
            # Register this IRI's file location. Three node-id shapes
            # carry references in the corpus:
            #   1. <prefix>_Par_<n>      provisions in laws / regulations
            #   2. RK_*                  riigikohus decisions
            #   3. owl:Ontology-typed    act-level IRIs (laws, state regs,
            #                            KOV regs). Layer 2b body-text refs
            #                            resolve to these directly, so they
            #                            must be indexed too — otherwise
            #                            referencedBy on KOV act targets
            #                            silently drops.
            types = node.get("@type") or []
            if isinstance(types, str):
                types = [types]
            is_act_level = any(t in {"owl:Ontology", "estleg:Act",
                                       "estleg:Law",
                                       "estleg:NationalRegulation",
                                       "estleg:GovernmentRegulation",
                                       "estleg:MinisterialRegulation",
                                       "estleg:MunicipalRegulation"}
                                for t in types)
            if "_Par_" in node_id or "RK_" in node_id or is_act_level:
                iri_to_file[node_id] = json_file
```

- [ ] **Step 2: Add a regression test for the act-level index**

```python
class TestActLevelIndex:
    def test_act_level_iri_resolves_in_iri_to_file(self, tmp_path, monkeypatch):
        """A KOV body-text ref that resolves to estleg:Reg_X_Map_2026
        (an act-level IRI) must be writable as referencedBy on that
        target file. collect_all_references must index act-level
        nodes for this to work."""
        import generate_inverse_references as mod
        import estleg_common

        krr = tmp_path / "krr_outputs"
        krr.mkdir()

        # Source act: KOV reg with body-text ref to a SISTER KOV act
        kov = krr / "regulations" / "kov" / "tallinna_linnavolikogu"
        kov.mkdir(parents=True)
        (kov / "src_peep.json").write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Reg_TLN_Src_Map_2024",
                 "@type": ["owl:Ontology", "estleg:MunicipalRegulation"]},
                {"@id": "estleg:Reg_TLN_Src_Par_1",
                 "@type": ["owl:NamedIndividual"],
                 "estleg:references": [
                     {"@id": "estleg:Reg_TLN_Tgt_Map_2020"},   # ACT-LEVEL
                 ]}
            ],
        }), encoding="utf-8")

        # Target KOV act
        (kov / "tgt_peep.json").write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Reg_TLN_Tgt_Map_2020",
                 "@type": ["owl:Ontology", "estleg:MunicipalRegulation"]},
            ],
        }), encoding="utf-8")

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)

        rc = mod.main()
        assert rc in (None, 0)

        # Target should now have referencedBy from the source act
        with open(kov / "tgt_peep.json", "r", encoding="utf-8") as fh:
            tgt = json.load(fh)
        act = tgt["@graph"][0]
        assert "estleg:referencedBy" in act
        rb = act["estleg:referencedBy"]
        if isinstance(rb, dict):
            rb = [rb]
        assert any(r["@id"] == "estleg:Reg_TLN_Src_Par_1" for r in rb)
```

- [ ] **Step 3: Implement the `implementedBy` helpers**

Same shape as v1's Task 10:

```python
def collect_preamble_back_references(json_files: list[Path]) -> dict[str, list[str]]:
    """Collect inverse links derived ONLY from preamble citations.

    Two sources:
      1. estleg:issuedUnder  → enabling_act_iri receives the source act
      2. estleg:implementsCitation → reified Citation node's
                                       estleg:citationTarget receives
                                       the source act

    Body-text references (estleg:references) are explicitly NOT
    consulted. They feed estleg:referencedBy via the existing
    inverse pass.
    """
    inverse: dict[str, list[str]] = defaultdict(list)
    for json_file in json_files:
        try:
            with open(json_file, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue
        graph = doc.get("@graph", [])
        # Build a quick map of citation_iri → target_iri inside this file
        citation_target: dict[str, str] = {}
        for n in graph:
            types = n.get("@type") or []
            if isinstance(types, str):
                types = [types]
            if "estleg:Citation" in types:
                cid = n.get("@id")
                tgt = n.get("estleg:citationTarget")
                if isinstance(tgt, dict):
                    tgt = tgt.get("@id")
                if cid and tgt:
                    citation_target[cid] = tgt

        for n in graph:
            types = n.get("@type") or []
            if isinstance(types, str):
                types = [types]
            if not any(t in {"owl:Ontology", "estleg:Act"} for t in types):
                continue
            source_act_iri = n.get("@id")
            if not source_act_iri:
                continue

            # issuedUnder targets
            iu = n.get("estleg:issuedUnder", [])
            if isinstance(iu, dict):
                iu = [iu]
            for ref in iu:
                if isinstance(ref, dict) and "@id" in ref:
                    inverse[ref["@id"]].append(source_act_iri)

            # implementsCitation → resolve to citationTarget
            ic = n.get("estleg:implementsCitation", [])
            if isinstance(ic, dict):
                ic = [ic]
            for ref in ic:
                if isinstance(ref, dict) and "@id" in ref:
                    target = citation_target.get(ref["@id"])
                    if target:
                        inverse[target].append(source_act_iri)
    # Dedupe per target
    return {k: sorted(set(v)) for k, v in inverse.items()}


def apply_implemented_by(
    inverse: dict[str, list[str]],
    iri_to_file: dict[str, Path],
) -> dict[str, int]:
    """Write estleg:implementedBy onto target nodes. Returns
    {filepath_name: count_of_nodes_updated}."""
    file_updates: dict[Path, dict[str, list[str]]] = defaultdict(dict)
    for target_iri, sources in inverse.items():
        target_file = iri_to_file.get(target_iri)
        if target_file is None:
            continue
        file_updates[target_file][target_iri] = sources

    counts: dict[str, int] = {}
    for target_file, by_iri in sorted(file_updates.items()):
        try:
            with open(target_file, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue
        nodes_updated = 0
        for node in doc.get("@graph", []):
            nid = node.get("@id")
            if not nid or nid not in by_iri:
                continue
            sources = by_iri[nid]
            node["estleg:implementedBy"] = [{"@id": s} for s in sources]
            node["estleg:implementedByCount"] = len(sources)
            nodes_updated += 1
        if nodes_updated:
            with open(target_file, "w", encoding="utf-8") as fh:
                json.dump(doc, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
            counts[target_file.name] = nodes_updated
    return counts


def compute_implemented_by_count(
    inverse: dict[str, list[str]],
) -> dict[str, int]:
    """Aggregate implementedByCount per target IRI."""
    return {target: len(set(sources)) for target, sources in inverse.items()}
```

Wire the helpers into `main()`:

```python
    print("\nLayer 2b: collecting preamble back-references...")
    files = list(iter_peep_files())
    preamble_inverse = collect_preamble_back_references(files)
    impl_counts = apply_implemented_by(preamble_inverse, iri_to_file)
    print(f"  implementedBy emitted to {sum(impl_counts.values())} nodes "
          f"across {len(impl_counts)} files")
```

- [ ] **Step 2: Write the body-text-exclusion E2E test**

```python
class TestImplementedByBodyTextExclusion:
    def test_body_text_references_do_not_feed_implementedBy(
        self, tmp_path, monkeypatch
    ):
        """Run main() over a graph where source act A has both body-text
        references to target X and issuedUnder to target Y. Verify X
        does NOT gain implementedBy; Y does."""
        import generate_inverse_references as mod
        import estleg_common

        krr = tmp_path / "krr_outputs"
        krr.mkdir()

        # Source KOV act with both kinds of links
        kov = krr / "regulations" / "kov" / "tallinna_linnavolikogu"
        kov.mkdir(parents=True)
        (kov / "source_peep.json").write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Reg_A_Map",
                 "@type": ["owl:Ontology", "estleg:MunicipalRegulation"],
                 "estleg:issuedUnder": [{"@id": "estleg:Y_Map_2026"}]},
                {"@id": "estleg:Reg_A_Par_1",
                 "@type": ["owl:NamedIndividual"],
                 "estleg:paragrahv": "§ 1",
                 # Body-text references to X — must NOT contribute
                 # to implementedBy on X.
                 "estleg:references": [{"@id": "estleg:X_Map_2026_Par_5"}]}
            ],
        }), encoding="utf-8")

        # Target X (a law)
        (krr / "x_peep.json").write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:X_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Law"]},
                {"@id": "estleg:X_Map_2026_Par_5",
                 "@type": ["owl:NamedIndividual"],
                 "estleg:paragrahv": "§ 5"}
            ],
        }), encoding="utf-8")

        # Target Y (a law)
        (krr / "y_peep.json").write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Y_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Law"]}
            ],
        }), encoding="utf-8")

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)

        rc = mod.main()
        assert rc in (None, 0)

        # X should have referencedBy from body-text but NOT implementedBy
        with open(krr / "x_peep.json", "r", encoding="utf-8") as fh:
            x_doc = json.load(fh)
        x_par_5 = next(n for n in x_doc["@graph"]
                       if n.get("@id") == "estleg:X_Map_2026_Par_5")
        # Body-text reference produces referencedBy — that's the
        # existing inverse, unchanged
        assert "estleg:referencedBy" in x_par_5
        # But X must NOT have implementedBy from body-text
        assert "estleg:implementedBy" not in x_par_5

        # Y should have implementedBy because A.issuedUnder pointed at it
        with open(krr / "y_peep.json", "r", encoding="utf-8") as fh:
            y_doc = json.load(fh)
        y_act = y_doc["@graph"][0]
        assert "estleg:implementedBy" in y_act
        impls = y_act["estleg:implementedBy"]
        if isinstance(impls, dict):
            impls = [impls]
        assert any(e["@id"] == "estleg:Reg_A_Map" for e in impls)
```

- [ ] **Step 3: Run + commit**

```
pytest tests/test_generate_inverse_references.py -v
pytest -q
```

```bash
git add scripts/generate_inverse_references.py tests/test_generate_inverse_references.py
git commit -m "Add implementedBy filtered projection + body-text-exclusion E2E test"
```

---

## Task 11: Unpin `generate_inverse_references` + coverage instrumentation

**Files:**
- Modify: `scripts/generate_inverse_references.py`

- [ ] **Step 1: Remove the pins**

```bash
grep -n "include_kov=False" scripts/generate_inverse_references.py
```

There are 3 occurrences. Remove the `include_kov=False` argument and the trailing `# DEFERRED to Layer 2b` comment from each call.

- [ ] **Step 2: Add the CoverageReport accumulator**

Add the same `CoverageReport` dataclass as in Task 8 (or import it from a shared module). Use `pipeline="generate_inverse_references"`.

- [ ] **Step 3: Wire the accumulator**

Coverage attribution: `triples_emitted_kov` increments when the SOURCE act path (the act emitting the inverse triple) is under `regulations/kov/`. `files_with_output[_kov]` is a set keyed by TARGET peep path (the file the inverse triple was written into) — the same as the existing `update_counts` keys, just stored as `set[Path]`.

```python
    coverage = CoverageReport(pipeline="generate_inverse_references")

    # ... existing collect_all_references + apply_inverse_references ...
    # The set-of-target-paths comes from update_counts.
    for fname, n in update_counts.items():
        target_path = KRR_DIR / fname  # may need adjustment for nested dirs
        coverage.files_with_output.add(target_path)
        if "regulations/kov/" in str(target_path):
            coverage.files_with_output_kov.add(target_path)
        coverage.triples_emitted += n

    # Source-side attribution: count how many KOV-source acts contributed
    # at least one referencedBy / implementedBy triple.
    for source_iri, target_list in inverse_map.items():
        # Each target_list contains source_iris that produced the triple
        # — we want the SOURCE files. Resolve via iri_to_file.
        for source in target_list:
            source_path = iri_to_file.get(source)
            if source_path is None:
                continue
            coverage.files_processed.add(source_path)
            if "regulations/kov/" in str(source_path):
                coverage.files_processed_kov.add(source_path)
                coverage.triples_emitted_kov += 1

    # Layer 2b implementedBy attribution — same pattern, against
    # preamble_inverse instead of inverse_map.
    for target_iri, sources in preamble_inverse.items():
        for source in sources:
            source_path = iri_to_file.get(source)
            if source_path is None:
                continue
            if "regulations/kov/" in str(source_path):
                coverage.triples_emitted_kov += 1
```

- [ ] **Step 4: Write the coverage report**

```python
    report_dir = KRR_DIR / "reports" / "kov"
    report_dir.mkdir(parents=True, exist_ok=True)
    out_path = report_dir / "generate_inverse_references_coverage.json"
    save_json(out_path, coverage.to_dict())
    print(f"\nCoverage report: {out_path}")

    if (coverage.input_files_kov >= 11000
            and len(coverage.files_with_output_kov) == 0):
        print("\nGATE FAIL: KOV files were processed but none received output.")
        return 1
    print("\nGATE OK")
    return 0
```

- [ ] **Step 5: Verify + commit**

```
python3 -c "import ast; ast.parse(open('scripts/generate_inverse_references.py').read())" && echo OK
pytest tests/test_generate_inverse_references.py -v
```

```bash
git add scripts/generate_inverse_references.py
git commit -m "Unpin generate_inverse_references + add coverage instrumentation

Removes the 3 include_kov=False pins, indexes act-level nodes in
iri_to_file (so KOV body-text refs targeting act IRIs gain
referencedBy on their target file instead of being silently
dropped), and adds CoverageReport with set-based dedup. KOV
attribution counts triples by SOURCE act path (the file emitting
the inverse triple); the gate fails when KOV input is non-trivial
but output is zero."
```

---

## Task 12: Run on full corpus + verify gates

Same pattern as Layer 2a Task 11 + the targeted SHACL bucket check. Run both pipelines, verify each reports GATE OK, run validate_all, run targeted SHACL.

If running in a fresh worktree, first symlink `data/riigiteataja/` from the main checkout (Layer 2a Task 11 documented this gap).

Coverage thresholds (KOV-specific):
- `input_files_kov ≥ 11000`
- `files_with_output_kov > 0`
- `triples_emitted_kov > 0`
- `error_count ≤ max(5, 1% of files_processed)`

```bash
git add krr_outputs/reports/kov/
git add krr_outputs/regulations/kov krr_outputs/regulations/riik
# INDEX-driven law file staging
python3 - <<'PY'
import json, subprocess, os
idx = json.load(open("krr_outputs/INDEX.json"))
paths = [f"krr_outputs/{f}" for entry in idx["laws"]
         for f in entry.get("files", []) if os.path.exists(f"krr_outputs/{f}")]
chunk = 500
for i in range(0, len(paths), chunk):
    subprocess.check_call(["git", "add", "--"] + paths[i:i + chunk])
PY
for path in cross_references_report.json inverse_references_report.json; do
    git add "krr_outputs/$path" 2>/dev/null || true
done
git status --short | head -30
git commit -m "Run Layer 2b pipelines on full corpus"
```

---

## Task 13: Documentation updates

**Files:**
- Modify: `docs/SCHEMA_REFERENCE.md`
- Modify: `docs/REGULATIONS_INTEGRATION_PLAN.md`
- Modify: `CHANGELOG.md`
- Modify: `README.md`

- [ ] **Step 1: Update `docs/SCHEMA_REFERENCE.md`**

Find the table that lists 2a's declared-but-unpopulated properties (`issuedUnder`, `implementsCitation`, `Citation` class, `implementedBy`, `implementedByCount`). Replace the "declared, populated in Layer 2b" notes with the production examples below.

```markdown
### estleg:issuedUnder

**Domain:** estleg:Act (Law, NationalRegulation, GovernmentRegulation,
MinisterialRegulation, MunicipalRegulation)

**Range:** estleg:Act (act-level only — never a LegalProvision)

**Cardinality:** many-per-act

**Semantics:** Identifies the ACT under whose authority this act was
issued. For municipal regulations, the most common targets are KOKS
(`estleg:KOKS_Map_2026`), state-regulation acts, or other KOV acts.
Provision-level granularity (e.g. "issued under § 22 lg 1 p 34") is
captured separately on `Citation.citationTarget` — see
`estleg:implementsCitation` below.

**Example:**
```json
{
  "@id": "estleg:Reg_1014955_Map_2026",
  "@type": ["owl:Ontology", "estleg:Act", "estleg:MunicipalRegulation"],
  "estleg:issuedUnder": [
    {"@id": "estleg:KOKS_Map_2026"},
    {"@id": "estleg:RaamatPS_Map_2026"}
  ]
}
```

### estleg:implementsCitation

**Domain:** estleg:Act

**Range:** estleg:Citation

**Cardinality:** many-per-act

**Semantics:** Reified citation node carrying the original preamble
substring, the paragraph/lõige/punkti detail, and the resolved target
(provision when § is present, act otherwise).

**Example:** see `estleg:Citation` below.

### estleg:Citation (class)

A reified citation node. Always co-occurs with `estleg:implementsCitation`
on the source act.

**Properties:**
- `estleg:citationTarget` — provision OR act IRI (cardinality 1)
- `estleg:citationDetail` — `"lg 1 p 34"` style literal (optional)
- `estleg:citationText` — original input substring (optional, recommended)

**IRI pattern:** `estleg:Citation_<source-act-shortid>_<seq>` where
`<source-act-shortid>` is the source act's @id minus `estleg:` and
`<seq>` is a 1-based per-act counter.

```json
{
  "@id": "estleg:Citation_Reg_1014955_Map_2026_1",
  "@type": ["owl:NamedIndividual", "estleg:Citation"],
  "estleg:citationTarget": {"@id": "estleg:KOKS_Par_22"},
  "estleg:citationDetail": "lg 1 p 34",
  "estleg:citationText": "kohaliku omavalitsuse korralduse seaduse § 22 lõike 1 punkti 34"
}
```

### estleg:implementedBy

**Domain:** estleg:Act ∪ estleg:LegalProvision

**Range:** estleg:Act (the implementing act — typically a regulation)

**Cardinality:** many-per-target

**Semantics:** Filtered inverse projection of `issuedUnder` and
`Citation.citationTarget`. Body-text references (`estleg:references`)
do NOT contribute. The existing `estleg:referencedBy` continues to
serve as the inverse for body-text references — `implementedBy` is
strictly the "what acts cite me as their enabling authority" view.

**Example:**
```json
{
  "@id": "estleg:KOKS_Par_22",
  "estleg:implementedBy": [
    {"@id": "estleg:Reg_1014955_Map_2026"},
    {"@id": "estleg:Reg_2034567_Map_2024"}
  ],
  "estleg:implementedByCount": 2
}
```

### estleg:implementedByCount

**Domain:** estleg:Act ∪ estleg:LegalProvision

**Range:** xsd:integer

**Cardinality:** exactly one (when implementedBy is present)

**Semantics:** Aggregate count of distinct sources, useful for
SPARQL ranking queries.
```

- [ ] **Step 2: Update `docs/REGULATIONS_INTEGRATION_PLAN.md`**

Find the Gate B section. Mark Layer 2b as complete:

```markdown
### Layer 2b — Cross-references + Inverse (COMPLETE)

PR: #99 (squash-merged YYYY-MM-DD)

Deliverables:
- `extract_cross_references.py` emits `issuedUnder`, `implementsCitation`,
  reified `Citation` nodes from preamble citations
- `extract_cross_references.py` resolves KOV body-text act references
  scoped to the source municipality
- `generate_inverse_references.py` emits `implementedBy` /
  `implementedByCount` (filtered projection)
- `generate_inverse_references.py` indexes act-level nodes in
  `iri_to_file` so KOV body-text refs targeting act IRIs gain
  `referencedBy` correctly
- Both pipelines unpinned, run KOV by default
- Coverage: <fill in numbers from Task 12>

Gate B umbrella status: 2/3 sub-PRs merged. Layer 2c (sanctions,
competence, court-provision-links) is the remaining piece.
```

- [ ] **Step 3: Update `CHANGELOG.md`**

```markdown
## YYYY-MM-DD — Layer 2b: KOV cross-references and inverse projection

### Added
- `estleg:issuedUnder`, `estleg:implementsCitation`, reified
  `estleg:Citation` nodes populated for every act with a parseable
  preamble (laws, state regs, KOV acts).
- `estleg:implementedBy` and `estleg:implementedByCount` on every
  act/provision cited by a preamble.
- KOV body-text act references resolved with
  municipality-first scoping; cross-municipality matches skipped.

### Changed
- `extract_cross_references.py` and `generate_inverse_references.py`
  run the full corpus including `regulations/kov/` by default.
- `build_provision_index` returns 5 values (added `prefix_to_act_iri`
  and `act_iri_to_prefix`) — callers must update unpacking.
- `generate_inverse_references.collect_all_references` now indexes
  act-level nodes in `iri_to_file`, fixing silent referencedBy drops
  on KOV body-text refs that target act IRIs.

### Internal
- 11 corpus-derived preamble fixtures drive the parser tests.
- `_normalize_issuer_label` transliterates Estonian diacritics so
  registry-side ASCII labels match act-side canonical forms.
- `FULLNAME_GENITIVE` and `KNOWN_ABBREVIATIONS` extended with the
  20 most-cited KOV enabling laws.
```

- [ ] **Step 4: Update README.md** (post Layer 2b status)

Find the Layer-status block. Replace:

```
- Layer 2b: Cross-references + Inverse — [in progress]
```

with:

```
- Layer 2b: Cross-references + Inverse — COMPLETE (issuedUnder,
  implementsCitation, implementedBy, KOV body-text scope)
```

- [ ] **Step 5: Commit**

```bash
git add docs/SCHEMA_REFERENCE.md docs/REGULATIONS_INTEGRATION_PLAN.md CHANGELOG.md README.md
git commit -m "Document Layer 2b deliverables (issuedUnder, Citation, implementedBy)"
```

---

## Task 14: Final verification + push + PR

**Files:** None (verification + commit + PR creation only).

- [ ] **Step 1: Final test run**

```bash
pytest -q
```

Expected: all tests passing (~165+ depending on counts after Task 1's M5 shift).

- [ ] **Step 2: Run validate_all**

```bash
python3 scripts/validate_all.py
```

Inspect the targeted SHACL output. The 3-bucket categorization should show **zero new Layer-2 / Layer-1 / uncategorized regressions** vs the Layer 2a baseline.

- [ ] **Step 3: Inspect coverage gates**

```bash
python3 - <<'PY'
import json
for name in ("extract_cross_references", "generate_inverse_references"):
    p = f"krr_outputs/reports/kov/{name}_coverage.json"
    with open(p) as fh:
        r = json.load(fh)
    assert r["files_with_output_kov"] > 0, f"{name}: zero KOV output files"
    assert r["triples_emitted_kov"] > 0, f"{name}: zero KOV triples"
    print(f"{name}: GATE OK — "
          f"{r['files_with_output_kov']} KOV files, "
          f"{r['triples_emitted_kov']} KOV triples")
PY
```

- [ ] **Step 4: Generate PR body**

```bash
cat > /tmp/pr-body-2b.md <<'EOF'
## Summary

Layer 2b: Cross-references + Inverse, with KOV-aware preamble parsing
and body-text scoping.

- Preamble citations resolve to act-level `issuedUnder` and reified
  `Citation` nodes via `implementsCitation`. Provision detail rides
  only on `Citation.citationTarget`.
- KOV body-text references are scoped by `enactedByMunicipality` (with
  issuer-label and act-number narrowing) before resolving.
- `generate_inverse_references` emits `implementedBy` /
  `implementedByCount` as a filtered projection (NOT from body-text
  references) and now indexes act-level nodes in `iri_to_file` so
  KOV referencedBy targets resolve correctly.
- Both pipelines run KOV by default; coverage gates enforce non-empty
  KOV output.

## Test plan

- [ ] Full pytest suite passing (165+ tests)
- [ ] validate_all reports zero new Layer-2/Layer-1/uncategorized SHACL regressions
- [ ] extract_cross_references_coverage.json gate OK
- [ ] generate_inverse_references_coverage.json gate OK
- [ ] Spot-check 3 KOV peeps for issuedUnder + Citation triples
- [ ] Spot-check 3 law peeps for implementedBy from KOV preambles
- [ ] No new test fixtures committed under `data/riigiteataja/` (gitignored)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
```

- [ ] **Step 5: Stop short of pushing**

The push + `gh pr create` step is left for the human reviewer. Print the PR body path so the human can review it before invoking `gh pr create --body-file /tmp/pr-body-2b.md ...`.

```bash
echo "PR body ready at /tmp/pr-body-2b.md"
echo "When ready: git push -u origin <branch> && gh pr create --title '...' --body-file /tmp/pr-body-2b.md"
```

---

## Self-Review Checklist

After all 14 tasks complete:

- [ ] **Spec coverage.** Preamble (Task 2 corpus-aware, Task 7 wire); Citation reified node (Task 4, Task 7); body-text KOV scope (Task 6 parser, Task 7 wire into process_law_file); implementedBy filtered projection (Task 10) with body-text-exclusion E2E test; unpinning (Tasks 9, 11); coverage with set-based dedup (Tasks 8, 11); M-fixes including FULLNAME_GENITIVE expansion (Task 1).

- [ ] **Round-2 review blockers (B1-B7) addressed:**
  - B1 (genitive→act lookup): Task 3 builds `genitive_to_act_iri` chain
  - B2 (prefix as act IRI): `prefix_to_act_iri` extends `build_provision_index` (Task 3); fixtures use corpus-shape IRIs throughout
  - B3 (adoptionDate corpus reality): regulation lookups read from XML `<vastuvoetud><aktikuupaev>` (Task 3)
  - B4 (preamble regex too narrow): 11 corpus-derived fixtures + extended regex bank (Task 2)
  - B5 (body-text not wired): Task 6 parser + Task 7 process_law_file integration
  - B6 (issuer label fragile): Task 6 reads from `issuers_kov_peep.json` rdfs:label
  - B7 (self-import): Task 7 explicitly says do not self-import

- [ ] **Round-3 review items (1-8 + 3 nice-to-fixes) addressed:**
  - **#1 prefix derivation:** Task 3 returns `act_iri_to_prefix` reverse map; Task 5 resolver uses it (no `split("_")[0]`). Test `test_law_with_multipart_prefix_resolves_via_reverse_map` proves the multi-segment case.
  - **#2 provision IRI shape:** all fixtures use `estleg:KOKS_Par_22` form (NOT `estleg:KOKS_Map_2026_Par_22`) — Tasks 4, 5 examples corrected.
  - **#3 FULLNAME_GENITIVE coverage:** M7 in Task 1 adds 20+ genitive→abbrev entries for the most-cited KOV enabling laws (kohanimeseaduse, PGS, koolieelse lasteasutuse seaduse, etc.) plus a verification one-liner that the `dc:source` chain holds end-to-end.
  - **#4 minister regex:** Task 2 has 3 new corpus-derived minister fixtures (numeric-date instrumental, multiword named-month, locative `määruses`); regex extended for multiword/lowercase ministers and locative case.
  - **#5 issuer label diacritic alignment:** `_normalize_issuer_label` transliterates diacritics symmetrically on registry, lookup-construction, and resolver-query sides (Task 3, Task 6).
  - **#6 modified flag in body-text wiring:** Task 7 Step 3 explicitly sets `modified = True`, bumps `citations_found` / `citations_resolved` / `provisions_with_refs`; integration test `TestKovBodyTextSavesFile` proves a KOV-only-body-text file is saved AND counted.
  - **#7 act-level iri_to_file index:** Task 10 Step 1 expands `collect_all_references` to index `owl:Ontology`-typed act nodes; regression test `TestActLevelIndex` proves `referencedBy` lands on KOV act targets.
  - **#8 placeholder expansion:** Task 7 preamble pass, Task 8 coverage instrumentation, Task 10 helpers, Task 13 docs all replaced with self-contained code or full prose. No "code omitted for brevity" / "same as v1" instructions remain.
  - **N1 (nice-to-fix) explicit issuer in body-text:** `extract_kov_act_refs_from_text` returns `issuer` (normalised); resolver takes `explicit_issuer` and uses Path A directly when present, Path B (body-type-scoped enumeration) only when missing.
  - **N2 (nice-to-fix) multi-candidate lookup:** `build_kov_act_lookup_by_number` returns `dict[..., list[str]]` and the resolver returns `None` when more than one candidate matches — no "latest by sorted file order" silent winner.
  - **N3 (nice-to-fix) preserve original citationText:** parser uses an offset-aligned scratch copy for matching but reads `citationText` from the original `text` so quotes / punctuation survive.

- [ ] **No actionable gaps.** No "TODO", "TBD", "implement later", "Add appropriate <thing>", "Code block omitted for brevity".

- [ ] **Type consistency.** `extract_preamble_citations` return shape consistent across Tasks 2, 5, 7. Lookup names consistent across Tasks 3, 5, 6, 7. Resolver return tuple `(citation_target_iri, enabling_act_iri)` consistent. `build_kov_act_lookup_by_number` value type is `list[str]` everywhere it appears (Task 3 builder, Task 6 resolver, Task 7 wiring).

- [ ] **issuedUnder semantics.** Range is Act, not LegalProvision. Confirmed in resolver (Task 5) — second tuple element is always act-level. Provision detail rides on Citation.citationTarget only.

- [ ] **Idempotency.** Both passes (preamble and implementedBy) clear before regenerating.

- [ ] **All 14 tasks have clear commit messages.**

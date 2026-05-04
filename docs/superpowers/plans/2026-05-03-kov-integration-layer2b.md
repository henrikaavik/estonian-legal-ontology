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

## Task 1: Layer 2a review carryover (M2–M6)

**Files:**
- Modify: `scripts/extract_legal_concepts.py` (M2)
- Modify: `scripts/generate_amendment_history.py` (M3)
- Modify: `scripts/estleg_common.py` (M4)
- Modify: `scripts/classify_eurovoc.py` (M4)
- Modify: `tests/test_generate_amendment_history.py` (M5)
- Modify: `README.md` (M6)

The Layer 2a final review surfaced 5 minor findings explicitly noted as "addressable in Layer 2b prep". Apply them as a single foundation commit.

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

- [ ] **Step 6: Verify**

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
git commit -m "Address Layer 2a review nits (M2-M6) before 2b work"
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
    }
  ]
}
```

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
    r"(?P<issuer>Vabariigi\s+Valitsuse|"
    r"[A-ZÕÄÖÜŠŽ][a-zõäöüšž]+(?:\s+ministri))\s+"
    rf"{_DATE_EITHER}\s+määrus(?:ega|e|est)\s+nr\s*(?P<num>\d+)",
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
    """Strip Estonian genitive ending from a state issuer string."""
    s = issuer_text.strip()
    if s.endswith("Valitsuse"):
        return s[:-1]  # Valitsuse → Valitsus
    if s.endswith("ministri"):
        return s[:-1]  # ministri → minister (rough)
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

    # Strip quote characters before matching so patterns work on
    # quoted forms like "Kohanimeseaduse" or «Kohaliku omavalitsuse...»
    cleaned = re.sub(rf"[{re.escape(_QUOTES)}]", " ", text)

    citations: list[tuple[int, dict]] = []

    for m in _PAT_LAW_PARA.finditer(cleaned):
        cit = {
            "form": "law-genitive",
            "law_ref": _strip_quotes(m.group("law")).strip(),
            "paragraphs": [m.group("par")],
            "citationDetail": _build_citation_detail(m.group("lg"), m.group("p")),
            "citationText": m.group(0).strip(),
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
            "citationText": m.group(0).strip(),
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
            "issuer": m.group("issuer").strip(),
            "adoption_date": date,
            "act_number": m.group("num"),
            "citationDetail": None,
            "citationText": m.group(0).strip(),
        }
        citations.append((m.start(), cit))

    citations.sort(key=lambda pair: pair[0])
    return [c for _, c in citations]
```

- [ ] **Step 5: Run the test**

```
pytest tests/test_extract_cross_references.py::TestExtractPreambleCitations -v
```

Expected: 8 parametrized samples + `test_empty_text_returns_empty` + `test_no_match_returns_empty` = 10 passed.

If a sample fails, the regex doesn't yet handle that case. **Iterate on the regex until all 8 samples pass — do not relax the test fixtures.** The samples are corpus-derived; they represent real text the pipeline will hit.

- [ ] **Step 6: Run the full suite**

```
pytest -q
```

Expected: prior count + 10 new tests, all passing.

- [ ] **Step 7: Commit**

```bash
git add scripts/extract_cross_references.py tests/test_extract_cross_references.py tests/fixtures/kov_layer2b/preamble_samples.json
git commit -m "Add corpus-aware preamble citation parser

Drives the parser with 8 corpus-derived preamble fixtures covering
real Estonian variation: multiword law names, paragraph word/symbol
forms, lõige genitive/elative/plural cases, named-month and numeric
date formats, instrumental määrusega, quoted law names. The regex
bank handles all 8 samples; iterate on the bank if any fail rather
than weakening the fixtures."
```

---

## Task 3: Canonical lookups (act IRIs + genitive-to-act + regulation by XML adoption-date)

**Files:**
- Modify: `scripts/extract_cross_references.py` (extend `build_provision_index`; add 3 new lookup builders)
- Modify: `tests/test_extract_cross_references.py` (add `TestCanonicalLookups`)

The reviewer's blockers B1, B2, and B3 share a root cause: there's no canonical mapping from the parser's output (a genitive law name like `kohaliku omavalitsuse korralduse seaduse`) to a real act @id in the corpus (like `estleg:KOKS_Map_2026`, NOT bare prefix `estleg:KOKS`). State and KOV regulations have a similar problem: they're keyed by `(issuer, adoption_date, act_number)`, but no act node carries `estleg:adoptionDate` — adoption dates live only inside source XML at `<vastuvoetud><aktikuupaev>`.

This task builds four canonical lookups exposed as functions:

1. **`prefix_to_act_iri`** — extends `build_provision_index` to also return `{prefix: act_iri}`. Read from each scanned peep file's `owl:Ontology` node.
2. **`build_genitive_to_act_iri(provision_index_data)`** — chains `FULLNAME_GENITIVE` → abbrev → `source_act_to_prefix` → `prefix_to_act_iri`. Returns `{genitive_law_name (lowercased): act_iri}`.
3. **`build_state_regulation_lookup(riik_root, data_dir)`** — walks state regulation peep files; for each, pairs to its XML via `pair_peep_with_xml(...)` (Layer 2a helper); reads `<vastuvoetud><aktikuupaev>` from the XML; keys the lookup by `(issuer_label, adoption_date, act_number)`.
4. **`build_kov_act_lookup(kov_root, data_dir)`** — same shape as state-reg lookup, but for KOV peep files. Reads the registry's `rdfs:label` for the issuer string (per blocker B6 — slug→display reconstruction is fragile because of diacritics like `Nõo` vs `Noo`).

For body-text references that don't have date context, this task ALSO builds a parallel non-date lookup `build_kov_act_lookup_by_number(kov_root)` keyed by `(issuer_label, act_number)` (no date). Body-text resolution uses this; preamble resolution uses the date-keyed version.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_extract_cross_references.py`:

```python
class TestCanonicalLookups:
    def test_extends_provision_index_with_prefix_to_act_iri(self, tmp_path,
                                                               monkeypatch):
        """build_provision_index now returns 4 things, the new one being
        prefix → act @id (e.g. 'AS' → 'estleg:AS_Map_2026', NOT bare
        'estleg:AS')."""
        from extract_cross_references import build_provision_index
        import estleg_common

        krr = tmp_path / "krr_outputs"
        krr.mkdir()
        # Stage a law peep with a real-shape act @id
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
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)

        result = build_provision_index()
        # Old return — 3 values; new return — 4 values (act-iri map last)
        assert len(result) == 4
        prefix_to_provisions, source_act_to_prefix, iri_to_file, prefix_to_act_iri = result
        # Prefix derivation depends on existing logic, but we should
        # see 'AS' → 'estleg:AS_Map_2026', not bare 'estleg:AS'
        assert "AS" in prefix_to_act_iri
        assert prefix_to_act_iri["AS"] == "estleg:AS_Map_2026"

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
        A separate lookup is keyed by (issuer_label, act_number) only."""
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
        # The lookup key is (issuer_label, act_number) — no date
        assert lookup[("Tallinna Linnavolikogu", "15")] == \
            "estleg:Reg_TLN_15_Map_2020"
```

- [ ] **Step 2: Run (expect ImportError / signature mismatch)**

```
pytest tests/test_extract_cross_references.py::TestCanonicalLookups -v
```

Expected: FAIL.

- [ ] **Step 3: Extend `build_provision_index` to return `prefix_to_act_iri`**

In `scripts/extract_cross_references.py`, find `build_provision_index` (around line 54). It currently returns 3 values: `(prefix_to_provisions, source_act_to_prefix, iri_to_file)`. Add a 4th: `prefix_to_act_iri`.

```python
def build_provision_index() -> tuple[
    dict[str, dict[str, str]],
    dict[str, str],
    dict[str, Path],
    dict[str, str],   # NEW: prefix → act @id
]:
    prefix_to_provisions: dict[str, dict[str, str]] = {}
    source_act_to_prefix: dict[str, str] = {}
    iri_to_file: dict[str, Path] = {}
    prefix_to_act_iri: dict[str, str] = {}    # NEW

    for peep in iter_peep_files():
        # ... existing scan logic that populates the first three ...

        # NEW: capture the act node's @id (the owl:Ontology-typed node
        # at the top of @graph). The prefix is already derived in
        # the existing logic; this just records the act's full @id.
        for node in doc.get("@graph", []):
            types = node.get("@type") or []
            if isinstance(types, str):
                types = [types]
            if "owl:Ontology" in types and "@id" in node:
                # Use the same prefix derivation that existing code uses.
                # Insert the recording at the right spot in the existing flow.
                if file_prefix and file_prefix not in prefix_to_act_iri:
                    prefix_to_act_iri[file_prefix] = node["@id"]
                break

    return prefix_to_provisions, source_act_to_prefix, iri_to_file, prefix_to_act_iri
```

Update every caller of `build_provision_index()` in the same module to unpack the 4-tuple. There's one caller in `main()` (around line 405):

```python
# Was:
prefix_to_provisions, source_act_to_prefix, iri_to_file = build_provision_index()
# Now:
prefix_to_provisions, source_act_to_prefix, iri_to_file, prefix_to_act_iri = build_provision_index()
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


def build_kov_act_lookup_by_number(
    kov_root: Path,
) -> dict[tuple[str, str], str]:
    """Build (issuer_label, act_number) → act @id for KOV regulations.

    Used for body-text references that don't carry adoption_date.
    Multiple acts under the same issuer can share an act_number across
    historical revisions; the latest @id wins (sorted file order)."""
    lookup: dict[tuple[str, str], str] = {}
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
            if issuer and act_num and node.get("@id"):
                lookup[(issuer, str(act_num))] = node["@id"]
            break
    return lookup
```

- [ ] **Step 6: Run tests**

```
pytest tests/test_extract_cross_references.py::TestCanonicalLookups -v
```

Expected: 5 passed.

- [ ] **Step 7: Commit**

```bash
git add scripts/extract_cross_references.py tests/test_extract_cross_references.py
git commit -m "Add canonical act/provision/regulation lookups for Layer 2b

Extends build_provision_index to also return prefix_to_act_iri
(prefix → real act @id like estleg:AS_Map_2026, not bare prefix).
Adds build_genitive_to_act_iri chaining FULLNAME_GENITIVE through
the abbreviation table to the new act-IRI map.

Adds build_state_regulation_lookup and build_kov_act_lookup, both
keyed by (issuer, adoption_date, act_number). adoption_date comes
from <vastuvoetud><aktikuupaev> in source XML — the corpus has zero
estleg:adoptionDate fields. Pairs each peep to its XML via the
Layer 2a pair_peep_with_xml helper.

Adds build_kov_act_lookup_by_number for body-text references that
lack date context."
```

---

## Task 4: Citation IRI builder + reified Citation node

**Files:**
- Modify: `scripts/extract_cross_references.py`
- Modify: `tests/test_extract_cross_references.py`

Same as v1's Task 3 — straightforward IRI builder + node builder. The Citation IRI shape matches the SHACL pattern from Layer 2a.

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
            target_iri="estleg:KOKS_Map_2026_Par_22",  # corpus-shape: <prefix>_Map_<year>_Par_N
            citation_detail="lg 1 p 34",
            citation_text="kohaliku omavalitsuse korralduse seaduse § 22 lõike 1 punkti 34",
        )
        assert node["@id"] == "estleg:Citation_Reg_1014955_Map_2026_1"
        assert "estleg:Citation" in node["@type"]
        assert "owl:NamedIndividual" in node["@type"]
        assert node["estleg:citationTarget"] == {"@id": "estleg:KOKS_Map_2026_Par_22"}
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
        return {
            "genitive_to_act_iri": {
                "kohaliku omavalitsuse korralduse seaduse": "estleg:KOKS_Map_2026",
                "alkoholiseaduse": "estleg:AS_Map_2026",
            },
            "prefix_to_provisions": {
                "KOKS": {"22": "estleg:KOKS_Map_2026_Par_22",
                          "6": "estleg:KOKS_Map_2026_Par_6"},
                "AS":   {"42": "estleg:AS_Map_2026_Par_42"},
            },
            "source_act_to_prefix": {
                "Kohaliku omavalitsuse korralduse seadus": "KOKS",
                "Alkoholiseadus": "AS",
            },
            "state_reg_lookup": {
                ("Vabariigi Valitsus", "2007-12-20", "251"):
                    "estleg:Reg_VV251_Map_2007",
            },
            "kov_act_lookup": {
                ("Tallinna Linnavolikogu", "2020-12-02", "60"):
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
        # Provision-level for citation target
        assert target_iri == "estleg:KOKS_Map_2026_Par_22"
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
    source_act_to_prefix: dict[str, str],
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
        # Resolve paragraph via prefix_to_provisions.
        # Find the prefix from act_iri — strip 'estleg:' and trailing
        # _Map_<year>. (We chained genitive → prefix → act_iri, but we
        # need the prefix here; chain again or carry it through.)
        # The prefix is the first underscore-delimited segment after
        # 'estleg:', when the IRI follows the corpus convention.
        # E.g. 'estleg:KOKS_Map_2026' → 'KOKS'.
        shortid = act_iri.removeprefix("estleg:")
        prefix = shortid.split("_")[0]
        par = cit["paragraphs"][0]
        target = prefix_to_provisions.get(prefix, {}).get(par)
        if target is None:
            # Paragraph not in provision index — fall back to act-level
            # for citation_target while keeping enabling_act_iri at act.
            return (act_iri, act_iri)
        return (target, act_iri)

    if form == "state-regulation-date-number":
        key = (cit["issuer"], cit["adoption_date"], cit["act_number"])
        act_iri = state_reg_lookup.get(key)
        if act_iri is None:
            return None
        return (act_iri, act_iri)

    if form == "kov-regulation-date-number":
        key = (cit["issuer"], cit["adoption_date"], cit["act_number"])
        act_iri = kov_act_lookup.get(key)
        if act_iri is None:
            return None
        return (act_iri, act_iri)

    return None
```

```
pytest tests/test_extract_cross_references.py::TestResolvePreambleCitation -v
```

Expected: 5 passed.

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
        issuer    : full issuer label or None (when only generic body
                    word like 'linnavolikogu' is used)
        body_type : 'volikogu' | 'valitsus' | None
        act_number: digit string
        text      : matched substring
    """
    if not text:
        return []
    out: list[dict] = []
    for m in _PAT_KOV_BODY_REF.finditer(text):
        body = m.group("body").lower()
        body_type = "volikogu" if "volikogu" in body else "valitsus"
        issuer_full = m.group("issuer")
        if issuer_full and issuer_full.strip():
            # Issuer prefix present — combine into full label
            issuer_label = (issuer_full.strip() + " "
                            + m.group("body").strip())
            # Title-case the body suffix for consistency
            issuer_label = issuer_label.replace(
                "linnavolikogu", "Linnavolikogu"
            ).replace(
                "vallavolikogu", "Vallavolikogu"
            ).replace(
                "linnavalitsus", "Linnavalitsus"
            ).replace(
                "vallavalitsus", "Vallavalitsus"
            )
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

Add `resolve_kov_internal_act_ref` (same shape as v1 — municipality-first):

```python
def resolve_kov_internal_act_ref(
    *,
    source_municipality: str | None,
    body_type: str | None,
    act_number: str,
    kov_act_lookup_by_number: dict[tuple[str, str], str],
    issuer_registry: dict[str, tuple[str, str, str]],
) -> str | None:
    """Resolve a KOV-internal act reference by act_number, scoped to the
    source act's municipality.

    issuer_registry: issuer @id → (rdfs:label, municipality @id, body_type).
        Built from issuers_kov_peep.json — the registry's rdfs:label is
        the canonical issuer string; reconstructing from the slug is
        fragile because of diacritics.

    kov_act_lookup_by_number: (issuer_label, act_number) → act @id.

    Returns the resolved target or None when:
        - source_municipality is None (no scoping context)
        - zero matches
        - multiple matches
    """
    if source_municipality is None:
        return None

    candidate_labels: list[str] = []
    for issuer_iri, (label, mun_iri, btype) in issuer_registry.items():
        if mun_iri != source_municipality:
            continue
        if body_type is not None and btype != body_type:
            continue
        candidate_labels.append(label)

    matches = [
        target_iri
        for (issuer_label, num), target_iri in kov_act_lookup_by_number.items()
        if issuer_label in candidate_labels and num == act_number
    ]
    if len(matches) == 1:
        return matches[0]
    return None


def build_issuer_registry(issuers_path: Path) -> dict[str, tuple[str, str, str]]:
    """Load issuers_kov_peep.json and return
    {issuer @id → (rdfs:label, currentMunicipality @id, bodyType)}.
    The label comes from the registry verbatim — Layer 1's slug→display
    derivation is fragile (e.g. 'Noo Vallavolikogu' for slug 'noo'
    instead of 'Nõo Vallavolikogu')."""
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
            out[iri] = (label, municipality, body_type)
    return out
```

- [ ] **Step 3: Write the tests**

(Follow the same shape as the v1 plan's `TestKovBodyTextScope` but use the new `kov_act_lookup_by_number` keys and the registry-derived issuer labels. Also add `TestExtractKovActRefsFromText` covering the parser with samples like `"Tallinna Linnavolikogu määruse nr 15 § 4"` and `"linnavolikogu määrus nr 5"`.)

- [ ] **Step 4: Verify + commit**

```
pytest tests/test_extract_cross_references.py::TestExtractKovActRefsFromText -v
pytest tests/test_extract_cross_references.py::TestKovBodyTextScope -v
```

Both must pass.

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

Inside `main()`, after the existing `build_provision_index()` call (around line 405) — which now returns 4 values:

```python
    prefix_to_provisions, source_act_to_prefix, iri_to_file, prefix_to_act_iri = \
        build_provision_index()

    # Layer 2b: canonical lookups for the preamble + body-text passes
    genitive_to_act_iri = build_genitive_to_act_iri(
        source_act_to_prefix, prefix_to_act_iri
    )
    abbrev_to_prefix = build_abbreviation_to_prefix(source_act_to_prefix)

    riik_root = KRR_DIR / "regulations" / "riik"
    kov_root = KRR_DIR / "regulations" / "kov"
    issuers_path = KRR_DIR / "issuers_kov_peep.json"
    state_reg_lookup = build_state_regulation_lookup(riik_root, DATA_DIR)
    kov_act_lookup = build_kov_act_lookup(kov_root, DATA_DIR, issuers_path)
    kov_act_lookup_by_number = build_kov_act_lookup_by_number(kov_root)
    issuer_registry = build_issuer_registry(issuers_path)
    print(f"  state-reg lookup: {len(state_reg_lookup)} entries")
    print(f"  KOV act lookup (date-keyed): {len(kov_act_lookup)} entries")
    print(f"  KOV act lookup (number-only): {len(kov_act_lookup_by_number)} entries")
```

(`DATA_DIR` is the existing `data/riigiteataja/` constant; it should already be defined in the script.)

- [ ] **Step 2: Add the preamble pass to `main()`**

Insert after the existing body-text references logic but before the final report-write. Use the per-peep iteration pattern, calling `extract_preamble_citations` and `resolve_preamble_citation`. The pass:
- Clears prior `issuedUnder` / `implementsCitation` / Citation nodes (idempotent)
- Emits new triples
- Tracks coverage counters

The wiring instructions are nearly identical to v1's Task 7, with these critical differences:

  - **Do NOT add `from extract_cross_references import ...` inside `extract_cross_references.py`** — the functions are in the same module. The v1 plan had this self-import; remove it.
  - Pass `genitive_to_act_iri` (not `abbrev_to_prefix`) and `prefix_to_provisions` to `resolve_preamble_citation`.
  - Use the corpus-shaped IRIs throughout — the resolver returns real act IRIs like `estleg:KOKS_Map_2026`, not bare prefixes.

(Full code block for the preamble pass omitted here for brevity; follow v1 structure with the corrections above.)

- [ ] **Step 3: Wire body-text KOV scope into `process_law_file`**

`process_law_file` already iterates provisions and parses citations from `legalText`/`summary`. Extend it to ALSO parse KOV body-text refs and resolve them via the scoped resolver. The source act's `enactedByMunicipality` must be available in the per-file context.

In `process_law_file` (around line 299):

```python
def process_law_file(
    json_file: Path,
    abbrev_to_prefix: dict[str, str],
    prefix_to_provisions: dict[str, dict[str, str]],
    iri_to_file: dict[str, Path],
    *,
    kov_act_lookup_by_number: dict[tuple[str, str], str] | None = None,
    issuer_registry: dict[str, tuple[str, str, str]] | None = None,
) -> dict:
    # ... existing body-text scanning ...

    # Layer 2b: KOV body-text refs scoped to source municipality
    if kov_act_lookup_by_number is not None:
        # Read the source act's municipality (may be None for laws)
        source_municipality = None
        for node in graph:
            types = node.get("@type") or []
            if isinstance(types, str):
                types = [types]
            if any(t in {"estleg:NationalRegulation",
                          "estleg:GovernmentRegulation",
                          "estleg:MinisterialRegulation",
                          "estleg:MunicipalRegulation"} for t in types):
                ebm = node.get("estleg:enactedByMunicipality")
                if isinstance(ebm, dict):
                    source_municipality = ebm.get("@id")
                break

        # Per provision, parse body-text KOV refs and resolve
        for prov_node in graph:
            if "estleg:paragrahv" not in prov_node:
                continue
            text = (prov_node.get("estleg:legalText", "")
                    + " " + prov_node.get("estleg:summary", ""))
            for ref in extract_kov_act_refs_from_text(text):
                target = resolve_kov_internal_act_ref(
                    source_municipality=source_municipality,
                    body_type=ref["body_type"],
                    act_number=ref["act_number"],
                    kov_act_lookup_by_number=kov_act_lookup_by_number,
                    issuer_registry=issuer_registry or {},
                )
                if target is not None:
                    refs = prov_node.setdefault("estleg:references", [])
                    if isinstance(refs, dict):
                        refs = [refs]
                        prov_node["estleg:references"] = refs
                    if {"@id": target} not in refs:
                        refs.append({"@id": target})
```

Pass the new args at the call site (around line 474):

```python
    stats = process_law_file(
        json_file, abbrev_to_prefix, prefix_to_provisions, iri_to_file,
        kov_act_lookup_by_number=kov_act_lookup_by_number,
        issuer_registry=issuer_registry,
    )
```

- [ ] **Step 4: Run + commit**

```
pytest tests/test_extract_cross_references.py -v
```

All test classes pass. Plus a new integration test (`TestMainEndToEnd`) that uses corpus-shaped IRIs throughout — replace any synthetic `estleg:JS` style IRIs with `estleg:JS_Map_2026`.

```bash
git add scripts/extract_cross_references.py tests/test_extract_cross_references.py
git commit -m "Wire preamble pass + body-text KOV scope into extract_cross_references main()"
```

---

## Task 8: Coverage instrumentation (set-based dedup, KOV-by-source-path)

**Files:**
- Modify: `scripts/extract_cross_references.py`

Apply the canonical coverage instrumentation pattern, using SETS for the processed/with-output counters so the body-text and preamble passes don't double-count files. The reviewer's recommendation: KOV attribution should be by source act path (the file we're scanning), NOT the output target's path.

Pattern:
- `_files_processed_kov` is a `set[Path]` of KOV peep paths that the pipeline reached
- `_files_with_output_kov` is a `set[Path]` of KOV peep paths whose pipeline emitted ≥1 triple
- At report-write, convert to `len(...)` for the report fields

This avoids the bug where a KOV file that produces both body-text references AND preamble triples gets counted twice.

- [ ] **Step 1: Apply the canonical pattern with set-based dedup**

(Code block omitted for brevity — follow Layer 2a's Task 6 instrumentation pattern, but with `set[Path]` for the per-pipeline-counted-files counters.)

- [ ] **Step 2: Commit**

```bash
git add scripts/extract_cross_references.py
git commit -m "Add coverage instrumentation to extract_cross_references (set-based dedup)"
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

## Task 10: Update `generate_inverse_references` for `implementedBy` + body-text-exclusion E2E test

**Files:**
- Modify: `scripts/generate_inverse_references.py`
- Create: `tests/test_generate_inverse_references.py`

`implementedBy` is a filtered projection from `issuedUnder` and `Citation.citationTarget` — explicitly NOT from body-text `references`. The reviewer's recommendation 6: add an end-to-end test that proves this. Run the inverse pipeline over a fixture where the SAME source act has both a body-text `references` to a target AND an `issuedUnder` to a different target. The target with only body-text references should NOT gain `implementedBy`. The target with `issuedUnder` should.

- [ ] **Step 1: Implement the helpers** (same shape as v1's Task 10 — `collect_preamble_back_references`, `apply_implemented_by`, `compute_implemented_by_count`).

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

Same pattern as Task 9. Remove the 3 `include_kov=False` pins, add coverage instrumentation. Coverage attribution: `_triples_kov` increments when the SOURCE act path (the act emitting the inverse triple) is under `regulations/kov/`. `_files_with_output[_kov]` is a set keyed by target peep path.

```bash
git add scripts/generate_inverse_references.py
git commit -m "Unpin generate_inverse_references + add coverage instrumentation"
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

(Same as v1 — SCHEMA_REFERENCE.md, REGULATIONS_INTEGRATION_PLAN.md, CHANGELOG.md, README.md.)

---

## Task 14: Final verification + push + PR

(Same as v1 — pytest, validate_all, gate verification, generate PR body, **stop short of pushing**.)

---

## Self-Review Checklist

After all 14 tasks complete:

- [ ] **Spec coverage.** Preamble (Task 2 corpus-aware, Task 7 wire); Citation reified node (Task 4, Task 7); body-text KOV scope (Task 6 parser, Task 7 wire into process_law_file); implementedBy filtered projection (Task 10) with body-text-exclusion E2E test; unpinning (Tasks 9, 11); coverage with set-based dedup (Tasks 8, 11); M-fixes (Task 1).

- [ ] **Blockers from review v1 addressed:**
  - B1 (genitive→act lookup): Task 3 builds `genitive_to_act_iri` chain
  - B2 (prefix as act IRI): `prefix_to_act_iri` extends `build_provision_index` (Task 3); fixtures use corpus-shape IRIs throughout
  - B3 (adoptionDate corpus reality): regulation lookups read from XML `<vastuvoetud><aktikuupaev>` (Task 3)
  - B4 (preamble regex too narrow): 8 corpus-derived fixtures + extended regex bank (Task 2)
  - B5 (body-text not wired): Task 6 parser + Task 7 process_law_file integration
  - B6 (issuer label fragile): Task 6 reads from `issuers_kov_peep.json` rdfs:label
  - B7 (self-import): Task 7 explicitly says do not self-import

- [ ] **No actionable gaps.** No "TODO", "TBD", "implement later", "Add appropriate <thing>".

- [ ] **Type consistency.** `extract_preamble_citations` return shape consistent across Tasks 2, 5, 7. Lookup names consistent across Tasks 3, 5, 6, 7. Resolver return tuple `(citation_target_iri, enabling_act_iri)` consistent.

- [ ] **issuedUnder semantics.** Range is Act, not LegalProvision. Confirmed in resolver (Task 5) — second tuple element is always act-level. Provision detail rides on Citation.citationTarget only.

- [ ] **Idempotency.** Both passes (preamble and implementedBy) clear before regenerating.

- [ ] **All 14 tasks have clear commit messages.**

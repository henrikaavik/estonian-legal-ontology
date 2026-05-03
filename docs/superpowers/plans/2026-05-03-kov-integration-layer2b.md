# KOV Integration Layer 2b — Cross-references + Inverse Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add preamble citation extraction (`issuedUnder`, `implementsCitation` reified `Citation` instances) and a `implementedBy` / `implementedByCount` inverse projection to the KOV act corpus, plus KOV-aware body-text scoping rules. This is the second of three sub-PRs in the Layer 2 split.

**Architecture:** A new act-level pass scans `estleg:preambleText` on every `MunicipalRegulation` / `NationalRegulation` peep file, parses citations using extended versions of the existing `extract_citations_from_text` regex bank, resolves to enabling acts via the existing law/regulation registries (with new state-regulation and KOV-regulation resolvers for the date+number forms), and emits `issuedUnder` (act → enabling act, paragraph-granularity) plus reified `Citation` nodes for any citations carrying `lg`/`p` sub-paragraph specificity. Body-text scope rules for KOV act references use `enactedByMunicipality` first, narrowing by issuer body type, then act number/title. The inverse generator gains `implementedBy` (filtered projection from preamble enabling-act links — semantically distinct from the existing `referencedBy` body-text inverse) and `implementedByCount` (aggregate). Both extract_cross_references and generate_inverse_references are unpinned — they run with the new `include_kov=True` default.

**Tech Stack:** Python 3.11+, pytest, JSON-LD, SHACL (pyshacl), rdflib. Builds on the Layer 2a `kov_pipeline_coverage`, `build_globalid_xml_lookup`, `pair_peep_with_xml`, and Citation-class declarations.

**Spec reference:** `docs/superpowers/specs/2026-05-02-kov-integration-design.md` (Layer 2 — `extract_cross_references` and `generate_inverse_references` sections).
**Layer 2a reference:** `docs/superpowers/plans/2026-05-03-kov-integration-layer2a.md`.

---

## Crisp 2b invariant

After 2b lands:

1. **Preamble citations emit triples.** Every KOV act preamble citation that resolves to a known act produces an `estleg:issuedUnder` triple from the act node to the enabling act IRI (paragraph-granularity). Citations carrying `lg`/`p` sub-paragraph detail also produce one `estleg:implementsCitation` reified `estleg:Citation` node per citation, with `citationTarget`, `citationDetail`, and `citationText` populated.
2. **KOV body-text scoping is municipality-first.** When a KOV act's body text references `linnavolikogu määrus nr 5` or `vallavalitsuse määrus nr 12`, the resolver scopes the search to the same `enactedByMunicipality` first (catches vallavalitsus → vallavolikogu same-municipality cases), narrows by issuer body type, then act number / title. Global fallback only on a unique high-confidence match.
3. **Inverse generator emits new properties.** `generate_inverse_references` produces `estleg:implementedBy` (filtered projection — only from `issuedUnder` + `Citation.citationTarget` triples, NOT from body-text `references`) and `estleg:implementedByCount` (aggregate count) on each enabling act and provision. The existing `estleg:referencedBy` continues unchanged over the expanded file set.
4. **Both pipelines run KOV by default.** `extract_cross_references` and `generate_inverse_references` are unpinned (the `# DEFERRED to Layer 2b` comments removed). They process the full corpus including KOV with `iter_peep_files()` defaulting `include_kov=True`.
5. **Coverage gates pass.** `extract_cross_references_coverage.json` and `generate_inverse_references_coverage.json` show `files_with_output_kov > 0` and `triples_emitted_kov > 0`. SHACL targeted check shows zero new Layer-2b property violations and zero Layer-1/Layer-2a regressions.

**Not in 2b:** sanctions `enforcedAtLevel`, competence Issuer-binding, court-provision-links KOV resolver — those are Layer 2c.

**Gate B reaches umbrella complete when 2a + 2b + 2c land.**

---

## File Structure

### New files

| Path | Responsibility |
| --- | --- |
| `tests/test_extract_cross_references.py` | Tests for preamble parser, KOV body-text scoping, and main()-integration. |
| `tests/test_generate_inverse_references.py` | Tests for `implementedBy` filtered projection and `implementedByCount` aggregate. |
| `tests/fixtures/kov_layer2b/sample_kov_act_with_preamble.json` | KOV act fixture with a realistic Estonian preamble citation. |
| `tests/fixtures/kov_layer2b/sample_law_with_provisions.json` | Law fixture providing provision targets the preamble can resolve to. |
| `tests/fixtures/kov_layer2b/sample_state_reg.json` | State regulation fixture (testing the date+number resolver). |
| `tests/fixtures/kov_layer2b/sample_kov_with_internal_ref.json` | KOV act with a body-text reference to a same-municipality act (testing the municipality-scoped resolver). |
| `krr_outputs/reports/kov/extract_cross_references_coverage.json` | Generated. |
| `krr_outputs/reports/kov/generate_inverse_references_coverage.json` | Generated. |

### Modified files

| Path | Change |
| --- | --- |
| `scripts/extract_cross_references.py` | Add preamble scanner (`extract_preamble_citations`, `resolve_preamble_citation`, `build_citation_iri`, `build_kov_act_lookup`). Add KOV body-text scope rules (`resolve_kov_internal_act_ref`). Wire both into `main()`. Coverage instrumentation. Unpin the 3 `include_kov=False` call sites. |
| `scripts/generate_inverse_references.py` | Add `collect_preamble_back_references`, `apply_implemented_by`, `compute_implemented_by_count`. Wire into `main()`. Coverage instrumentation. Unpin the 3 `include_kov=False` call sites. |
| `scripts/extract_legal_concepts.py` | (M2 from 2a review) `_triples_kov` accounting now uses `is_kov` alone, not `bridged AND is_kov`. |
| `scripts/generate_amendment_history.py` | (M3 from 2a review) Move module-level `AMENDMENTS_DIR.mkdir(...)` and `EELNOUD_DIR.mkdir(...)` inside `main()`. |
| `scripts/estleg_common.py` | (M4 from 2a review) Add `AGGREGATE_REGISTRY_PREFIXES` constant; existing `read_act_metadata_from_peep` in `classify_eurovoc.py` imports from here. |
| `scripts/classify_eurovoc.py` | (M4 cont.) Replace local `REGISTRY_ID_PREFIXES` with import from `estleg_common`. |
| `tests/test_generate_amendment_history.py` | (M5 from 2a review) Replace the trivial `callable(...)` check in `test_imports_globalid_helper_from_temporal` with a parameterized integration test that runs the same fixture through both consumers (temporal + amendment-history) and asserts identical XML pairing. |
| `README.md` | (M6 from 2a review) Reword the deferred-pipelines line to call out KOV-not-applicable separately. |
| `docs/SCHEMA_REFERENCE.md` | Document Layer 2b's emitted triples (now-populated `issuedUnder`, `implementsCitation`, `implementedBy`, `implementedByCount`). Note the body-text scoping rule. |
| `docs/REGULATIONS_INTEGRATION_PLAN.md` | Mark Layer 2b complete; restate Gate B umbrella status. |
| `CHANGELOG.md` | Entry: Layer 2b deliverables. |

---

## Schema reference (already declared in 2a; this PR populates instances)

| Property | Domain | Range | When emitted | Cardinality |
| --- | --- | --- | --- | --- |
| `estleg:issuedUnder` | `Act` | `Act` | Per resolved preamble citation, on the source act node | many-per-act |
| `estleg:implementsCitation` | `Act` | `Citation` | When citation has lg/p detail, on the source act node | many-per-act |
| `estleg:citationTarget` | `Citation` | `LegalProvision` | Per Citation node | exactly one |
| `estleg:citationDetail` | `Citation` | `xsd:string` | Per Citation node when sub-§ detail exists | optional |
| `estleg:citationText` | `Citation` | `xsd:string` | Per Citation node | optional but recommended |
| `estleg:implementedBy` | `Act` ∪ `LegalProvision` | `Act` | Inverse projection from `issuedUnder` + `Citation.citationTarget` | many-per-target |
| `estleg:implementedByCount` | `Act` ∪ `LegalProvision` | `xsd:integer` | Aggregate count of `implementedBy` | exactly one when present |

**IRI scheme for Citation instances:** `estleg:Citation_<source-act-shortid>_<seq>`. The `<source-act-shortid>` is the source act's `@id` minus the `estleg:` prefix (e.g., `Reg_1014955_Map_2026`). `<seq>` is a 1-based per-act counter. Example: `estleg:Citation_Reg_1014955_Map_2026_1`.

---

## Task 1: Layer 2a review carryover (M2–M6)

**Files:**
- Modify: `scripts/extract_legal_concepts.py` (M2)
- Modify: `scripts/generate_amendment_history.py` (M3)
- Modify: `scripts/estleg_common.py` (M4)
- Modify: `scripts/classify_eurovoc.py` (M4)
- Modify: `tests/test_generate_amendment_history.py` (M5)
- Modify: `README.md` (M6)

The Layer 2a final review surfaced 5 minor findings, all explicitly noted as "addressable in Layer 2b prep". Apply them as a single foundation commit before any new logic lands.

- [ ] **Step 1: M2 — Align `_triples_kov` accounting in `extract_legal_concepts.py`**

The Layer 2a review noted that `_triples_kov` was incremented only when `bridged AND is_kov`, where `bridged` means the par_to_iri lookup found the provision IRI. The corpus run shows `unresolved_references=0`, so this isn't a current bug, but it disagrees with the simpler `is_kov`-only attribution used by deontic/eurovoc/temporal/amendment-history.

Find the `_triples_kov += 2` line in `scripts/extract_legal_concepts.py` (search for "bridged"):

```bash
grep -n "_triples_kov\|bridged" scripts/extract_legal_concepts.py | head -10
```

Replace the conditional. The before form looks roughly like:

```python
if bridged and is_kov:
    _triples_kov += 2
```

Change to:

```python
if is_kov:
    _triples_kov += 2
```

The bridge-failure signal stays in `_unresolved` (already tracked separately). KOV attribution is "is this peep under regulations/kov/" — same as the other 4 pipelines.

- [ ] **Step 2: M3 — Move `mkdir` calls inside `main()` in `generate_amendment_history.py`**

At module scope, find:

```python
AMENDMENTS_DIR = KRR_DIR / "amendments"
AMENDMENTS_DIR.mkdir(parents=True, exist_ok=True)
EELNOUD_DIR = KRR_DIR / "eelnoud"
```

Keep the `_DIR` definitions at module scope but move the `.mkdir(...)` calls inside `main()`, near the top:

```python
def main() -> int:
    AMENDMENTS_DIR.mkdir(parents=True, exist_ok=True)
    EELNOUD_DIR.mkdir(parents=True, exist_ok=True)
    # ... rest of main ...
```

Importing the module no longer creates directories as a side effect.

- [ ] **Step 3: M4 — Move `REGISTRY_ID_PREFIXES` to `estleg_common.py`**

Add to `scripts/estleg_common.py` (place near the top, with other module-level constants):

```python
AGGREGATE_REGISTRY_PREFIXES: tuple[str, ...] = (
    "estleg:Municipalities_",
    "estleg:Issuers_",
    "estleg:LegalConcepts_",
    "estleg:Citations_",
    "estleg:Similarity_",
)
```

In `scripts/classify_eurovoc.py`, find the local `REGISTRY_ID_PREFIXES` definition. Delete it. Add an import at the top of the file:

```python
from estleg_common import AGGREGATE_REGISTRY_PREFIXES
```

In `read_act_metadata_from_peep`, replace `REGISTRY_ID_PREFIXES` with `AGGREGATE_REGISTRY_PREFIXES` at the use site.

- [ ] **Step 4: M5 — Replace trivial `callable(...)` test with a parameterized integration test**

Find `tests/test_generate_amendment_history.py::TestAmendmentHistoryDiscovery::test_imports_globalid_helper_from_temporal`. Replace its body with:

```python
    @pytest.mark.parametrize("source_module", [
        "estleg_common",          # canonical home of the helper
        "extract_temporal_data",  # re-export path
        "generate_amendment_history",  # also re-exports for backwards-compat? (no — uses, not re-exports)
    ])
    def test_helpers_have_consistent_pairing(self, tmp_path, source_module):
        """Both consumer pipelines must produce identical XML pairing
        when given the same lookup + peep file. Pin this DRY-rule by
        running pair_peep_with_xml from each accessible import path
        against a small fixture and asserting they agree."""
        if source_module == "generate_amendment_history":
            pytest.skip("amendment-history imports the helper, doesn't re-export it")
        mod = __import__(source_module, fromlist=["pair_peep_with_xml"])
        if not hasattr(mod, "pair_peep_with_xml"):
            pytest.skip(f"{source_module} does not expose pair_peep_with_xml")

        # Stage a peep with globalId 555 and a matching XML
        rt = tmp_path / "data" / "riigiteataja" / "maarus_kov"
        rt.mkdir(parents=True)
        (rt / "reg_555.xml").write_text(
            '<akt globaalID="555"><metaandmed></metaandmed></akt>',
            encoding="utf-8",
        )
        peep = tmp_path / "act.json"
        peep.write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Reg_X",
                 "@type": ["owl:Ontology", "estleg:MunicipalRegulation"],
                 "estleg:globalId": "555"}
            ],
        }), encoding="utf-8")

        from estleg_common import build_globalid_xml_lookup
        lookup = build_globalid_xml_lookup(rt.parent)
        result = mod.pair_peep_with_xml(peep, lookup)
        assert result is not None
        assert result.name == "reg_555.xml"
```

This pins behavior across both import paths rather than just asserting `callable(...)`.

- [ ] **Step 5: M6 — Reword README's deferred-pipelines line**

In `README.md`, find the line near the Layer 2a section that says approximately:

```
The remaining 5 KOV-relevant pipelines (cross-references, inverse, sanctions, competence, court-provision-links) are deferred to Layers 2b and 2c.
```

Replace with:

```
The remaining 5 KOV-relevant pipelines (cross-references, inverse, sanctions, competence, court-provision-links) are deferred to Layers 2b and 2c, plus 4 KOV-not-applicable pipelines (similarity, draft-impact, harmonisation-links, transposition-mapping) stay pinned indefinitely.
```

- [ ] **Step 6: Verify**

Run the full test suite to confirm no regressions:

```
pytest -q
```

Expected: 135 passed (unchanged from 2a end-state — these are refactors, not new tests). The M5 change replaces one test method with one parametrized method (3 cases, 1 skipped) so the count may shift by ±1. Record the actual count.

```
python3 -c "import ast; ast.parse(open('scripts/estleg_common.py').read())" && echo OK
python3 -c "import ast; ast.parse(open('scripts/classify_eurovoc.py').read())" && echo OK
python3 -c "import ast; ast.parse(open('scripts/extract_legal_concepts.py').read())" && echo OK
python3 -c "import ast; ast.parse(open('scripts/generate_amendment_history.py').read())" && echo OK
```

Expected: all 4 print OK.

- [ ] **Step 7: Commit**

```bash
git add scripts/extract_legal_concepts.py scripts/generate_amendment_history.py scripts/estleg_common.py scripts/classify_eurovoc.py tests/test_generate_amendment_history.py README.md
git commit -m "Address Layer 2a review nits (M2-M6) before 2b work

* M2: extract_legal_concepts._triples_kov uses is_kov alone (matches
  the other 4 pipelines' KOV attribution semantics)
* M3: generate_amendment_history mkdir() moved inside main() so
  importing the module has no side effects
* M4: AGGREGATE_REGISTRY_PREFIXES moved to estleg_common as a
  shared constant; classify_eurovoc imports it
* M5: test_imports_globalid_helper_from_temporal upgraded from
  callable() smoke check to a parameterized integration test that
  exercises pair_peep_with_xml from every accessible import path
* M6: README clarifies that 4 pipelines are KOV-not-applicable
  (separate from the 5 deferred ones)"
```

---

## Task 2: Add preamble citation parser

**Files:**
- Modify: `scripts/extract_cross_references.py` (add `extract_preamble_citations`)
- Create: `tests/test_extract_cross_references.py`
- Create: `tests/fixtures/kov_layer2b/sample_kov_act_with_preamble.json`

The existing `extract_citations_from_text` handles three patterns: known abbreviations, "käesoleva seaduse" self-references, and full-name genitive forms. KOV preambles add three more patterns:

1. **State regulation by date+number:** `Vabariigi Valitsuse 19. detsembri 2019. a määruse nr 112 alusel`
2. **KOV regulation by date+number:** `Tallinna Linnavolikogu 18. juuni 2020 määruse nr 15 alusel`
3. **Bare law name (no §):** `alkoholiseaduse alusel` — resolves to the law itself, no provision

The new function `extract_preamble_citations(text)` returns a richer dict shape than the body-text version because preamble citations need to track the citation form (for resolver dispatch) and the original text (for the Citation node's `citationText`).

- [ ] **Step 1: Create the fixture**

Create `tests/fixtures/kov_layer2b/sample_kov_act_with_preamble.json`:

```json
{
  "@context": {
    "estleg": "https://data.riik.ee/ontology/estleg#",
    "owl": "http://www.w3.org/2002/07/owl#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/"
  },
  "@graph": [
    {
      "@id": "estleg:Reg_1014955_Map_2026",
      "@type": ["owl:Ontology", "estleg:Act", "estleg:MunicipalRegulation"],
      "rdfs:label": "Tallinna jäätmehoolduseeskiri",
      "dc:source": "Tallinna jäätmehoolduseeskiri",
      "estleg:globalId": "999999999",
      "estleg:terviktekstId": "1014955",
      "estleg:enactedBy": {"@id": "estleg:Issuer_tallinna_linnavolikogu"},
      "estleg:enactedByMunicipality": {"@id": "estleg:Municipality_EHAK_0784"},
      "estleg:titleNormalized": "jaatmehoolduseeskiri",
      "estleg:preambleText": "Määrus kehtestatakse jäätmeseaduse § 71 lõike 1 ja Vabariigi Valitsuse 19. detsembri 2019. a määruse nr 112 alusel."
    },
    {
      "@id": "estleg:Reg_1014955_Par_1",
      "@type": ["owl:NamedIndividual", "estleg:KovProvision"],
      "estleg:paragrahv": "§ 1.",
      "rdfs:label": "§ 1. Reguleerimisala",
      "estleg:summary": "stub",
      "estleg:enactedBy": {"@id": "estleg:Issuer_tallinna_linnavolikogu"},
      "estleg:enactedByMunicipality": {"@id": "estleg:Municipality_EHAK_0784"},
      "estleg:partOfAct": {"@id": "estleg:Reg_1014955_Map_2026"}
    }
  ]
}
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_extract_cross_references.py`:

```python
"""Tests for scripts/extract_cross_references.py — preamble + KOV scoping."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "kov_layer2b"


class TestExtractPreambleCitations:
    def test_law_with_paragraph_and_lg(self):
        """jäätmeseaduse § 71 lõike 1 — common law-citation form."""
        from extract_cross_references import extract_preamble_citations

        text = "Määrus kehtestatakse jäätmeseaduse § 71 lõike 1 alusel."
        cits = extract_preamble_citations(text)

        assert len(cits) == 1
        assert cits[0]["form"] == "law-genitive"
        assert cits[0]["law_ref"].lower() == "jäätmeseaduse"
        assert cits[0]["paragraphs"] == ["71"]
        assert cits[0]["citationDetail"] == "lg 1"
        assert cits[0]["citationText"].startswith("jäätmeseaduse")

    def test_law_with_paragraph_lg_and_punkt(self):
        """alkoholiseaduse § 42 lõike 1 punkti 3 — full lg/p detail."""
        from extract_cross_references import extract_preamble_citations

        text = "Määrus kehtestatakse alkoholiseaduse § 42 lõike 1 punkti 3 alusel."
        cits = extract_preamble_citations(text)

        assert len(cits) == 1
        assert cits[0]["paragraphs"] == ["42"]
        assert cits[0]["citationDetail"] == "lg 1 p 3"

    def test_state_regulation_by_date_number(self):
        """Vabariigi Valitsuse 19. detsembri 2019. a määruse nr 112."""
        from extract_cross_references import extract_preamble_citations

        text = (
            "Määrus kehtestatakse Vabariigi Valitsuse "
            "19. detsembri 2019. a määruse nr 112 alusel."
        )
        cits = extract_preamble_citations(text)

        assert len(cits) == 1
        assert cits[0]["form"] == "state-regulation-date-number"
        assert cits[0]["issuer"] == "Vabariigi Valitsus"
        assert cits[0]["act_number"] == "112"
        assert cits[0]["adoption_date"] == "2019-12-19"
        assert cits[0]["citationText"].startswith("Vabariigi Valitsuse")

    def test_kov_regulation_by_date_number(self):
        """Tallinna Linnavolikogu 18. juuni 2020 määruse nr 15."""
        from extract_cross_references import extract_preamble_citations

        text = (
            "Määrus kehtestatakse Tallinna Linnavolikogu "
            "18. juuni 2020 määruse nr 15 alusel."
        )
        cits = extract_preamble_citations(text)

        assert len(cits) == 1
        assert cits[0]["form"] == "kov-regulation-date-number"
        assert cits[0]["issuer"] == "Tallinna Linnavolikogu"
        assert cits[0]["act_number"] == "15"
        assert cits[0]["adoption_date"] == "2020-06-18"

    def test_two_citations_in_one_preamble(self):
        """Real KOV preambles often chain two citations with `ja`."""
        from extract_cross_references import extract_preamble_citations

        text = (
            "Määrus kehtestatakse jäätmeseaduse § 71 lõike 1 ja "
            "Vabariigi Valitsuse 19. detsembri 2019. a määruse nr 112 alusel."
        )
        cits = extract_preamble_citations(text)

        # Order matters — preserves the textual order
        assert len(cits) == 2
        assert cits[0]["form"] == "law-genitive"
        assert cits[1]["form"] == "state-regulation-date-number"

    def test_empty_text_returns_empty(self):
        from extract_cross_references import extract_preamble_citations
        assert extract_preamble_citations("") == []
        assert extract_preamble_citations(None) == []

    def test_no_match_returns_empty(self):
        """Text without citation patterns returns empty, doesn't crash."""
        from extract_cross_references import extract_preamble_citations
        assert extract_preamble_citations("This is not a preamble.") == []
```

- [ ] **Step 3: Run test (expect failure)**

```
pytest tests/test_extract_cross_references.py::TestExtractPreambleCitations -v
```

Expected: FAIL with `ImportError: cannot import name 'extract_preamble_citations'`.

- [ ] **Step 4: Implement `extract_preamble_citations`**

Add to `scripts/extract_cross_references.py` (place after `extract_citations_from_text`, before `_expand_par_range`):

```python
# ----------------------------------------------------------------------
# Preamble citation extraction (Layer 2b)
# ----------------------------------------------------------------------

# Estonian month name → 2-digit month number
_ET_MONTHS = {
    "jaanuari": "01", "veebruari": "02", "märtsi": "03", "aprilli": "04",
    "mai": "05", "juuni": "06", "juuli": "07", "augusti": "08",
    "septembri": "09", "oktoobri": "10", "novembri": "11", "detsembri": "12",
}

# Pattern A — law in genitive form + § N (lõike M (punkti K)?)?
# Captures: 1=law genitive name, 2=paragraph nr, 3=lg or None, 4=p or None
_PAT_LAW_PARA = re.compile(
    r"(?P<law>[a-zõäöüšž]+seaduse)\s*"
    r"§\s*(?P<par>\d+)"
    r"(?:\s+lõike\s+(?P<lg>\d+))?"
    r"(?:\s+punkti\s+(?P<p>\d+))?",
    re.UNICODE | re.IGNORECASE,
)

# Pattern B — state regulation: "Vabariigi Valitsuse <date> määruse nr N"
_PAT_STATE_REG = re.compile(
    r"(?P<issuer>Vabariigi\s+Valitsuse|"
    r"[A-ZÕÄÖÜŠŽ][a-zõäöüšž]+(?:\s+ministri))\s+"
    r"(?P<day>\d{1,2})\.\s*"
    r"(?P<month>jaanuari|veebruari|märtsi|aprilli|mai|juuni|juuli|augusti|septembri|oktoobri|novembri|detsembri)\s+"
    r"(?P<year>\d{4})\.?\s*a\.?\s+määruse\s+nr\s*(?P<num>\d+)",
    re.UNICODE | re.IGNORECASE,
)

# Pattern C — KOV regulation: "<Issuer Display Name> <date> määruse nr N"
# (issuer ends in volikogu / valitsus rather than ministri)
_PAT_KOV_REG = re.compile(
    r"(?P<issuer>[A-ZÕÄÖÜŠŽ][a-zõäöüšž\-]+(?:\s+[A-ZÕÄÖÜŠŽ][a-zõäöüšž\-]+)*\s+(?:Linnavolikogu|Linnavalitsuse|Vallavolikogu|Vallavalitsuse))\s+"
    r"(?P<day>\d{1,2})\.\s*"
    r"(?P<month>jaanuari|veebruari|märtsi|aprilli|mai|juuni|juuli|augusti|septembri|oktoobri|novembri|detsembri)\s+"
    r"(?P<year>\d{4})\.?\s*a?\.?\s+määruse\s+nr\s*(?P<num>\d+)",
    re.UNICODE,
)


def _build_citation_detail(lg: str | None, p: str | None) -> str | None:
    """Combine lõike (lg) and punkti (p) numbers into the
    citationDetail literal, e.g. 'lg 1' or 'lg 1 p 3'. Returns None
    if both are absent."""
    parts = []
    if lg:
        parts.append(f"lg {lg}")
    if p:
        parts.append(f"p {p}")
    return " ".join(parts) if parts else None


def _normalize_issuer(issuer_text: str) -> str:
    """Strip Estonian genitive ending from a state-issuer string.
    'Vabariigi Valitsuse' → 'Vabariigi Valitsus'.
    'Tallinna Linnavolikogu' stays as-is (already nominative for issuer
    body words that don't decline the same way in this position)."""
    if issuer_text.endswith("Valitsuse"):
        return issuer_text[:-1]  # Valitsuse → Valitsus
    if issuer_text.endswith("ministri"):
        return issuer_text[:-1]  # ministri → minister (rough; the
                                  # downstream resolver doesn't depend
                                  # on it)
    return issuer_text


def extract_preamble_citations(text: str) -> list[dict]:
    """Parse a preamble text for Estonian legal citation forms.

    Returns a list of dicts in textual order. Each dict has:
        form           : "law-genitive" | "state-regulation-date-number"
                         | "kov-regulation-date-number"
        citationText   : original matched substring (for the Citation
                         node's estleg:citationText)
        citationDetail : sub-§ string ("lg 1 p 3") or None

    Form-specific keys:
        law-genitive:
            law_ref      : the genitive law name (e.g. "alkoholiseaduse")
            paragraphs   : list of "N" strings (always one entry — no
                           ranges in preambles)

        state-regulation-date-number / kov-regulation-date-number:
            issuer        : nominative issuer string
            adoption_date : ISO-8601 date string ("2019-12-19")
            act_number    : the nr after "määruse nr"
    """
    if not text:
        return []

    citations: list[tuple[int, dict]] = []  # (start_offset, dict)

    for m in _PAT_LAW_PARA.finditer(text):
        cit = {
            "form": "law-genitive",
            "law_ref": m.group("law"),
            "paragraphs": [m.group("par")],
            "citationDetail": _build_citation_detail(m.group("lg"), m.group("p")),
            "citationText": m.group(0),
        }
        citations.append((m.start(), cit))

    for m in _PAT_STATE_REG.finditer(text):
        cit = {
            "form": "state-regulation-date-number",
            "issuer": _normalize_issuer(m.group("issuer")),
            "adoption_date": (
                f"{m.group('year')}-"
                f"{_ET_MONTHS[m.group('month').lower()]}-"
                f"{int(m.group('day')):02d}"
            ),
            "act_number": m.group("num"),
            "citationDetail": None,
            "citationText": m.group(0),
        }
        citations.append((m.start(), cit))

    for m in _PAT_KOV_REG.finditer(text):
        cit = {
            "form": "kov-regulation-date-number",
            "issuer": m.group("issuer"),
            "adoption_date": (
                f"{m.group('year')}-"
                f"{_ET_MONTHS[m.group('month').lower()]}-"
                f"{int(m.group('day')):02d}"
            ),
            "act_number": m.group("num"),
            "citationDetail": None,
            "citationText": m.group(0),
        }
        citations.append((m.start(), cit))

    citations.sort(key=lambda pair: pair[0])
    return [c for _, c in citations]
```

- [ ] **Step 5: Run test to verify it passes**

```
pytest tests/test_extract_cross_references.py::TestExtractPreambleCitations -v
```

Expected: 7 passed.

- [ ] **Step 6: Run the full suite to confirm no regressions**

```
pytest -q
```

Expected: prior count + 7 new tests, all passing.

- [ ] **Step 7: Commit**

```bash
git add scripts/extract_cross_references.py tests/test_extract_cross_references.py tests/fixtures/kov_layer2b/sample_kov_act_with_preamble.json
git commit -m "Add preamble citation parser (extract_preamble_citations)"
```

---

## Task 3: Citation IRI builder + reified Citation node

**Files:**
- Modify: `scripts/extract_cross_references.py` (add `build_citation_iri`, `build_citation_node`)
- Modify: `tests/test_extract_cross_references.py` (add `TestCitationNodeBuilder`)

The Layer 2a SHACL CitationShape requires the IRI pattern `^.../estleg#Citation_[A-Za-z0-9_]+_[0-9]+$` and `citationTarget` as the only required property. Build helpers that produce SHACL-valid Citation nodes from a resolved citation.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_extract_cross_references.py`:

```python
class TestCitationNodeBuilder:
    def test_iri_pattern_matches_shacl(self):
        from extract_cross_references import build_citation_iri
        iri = build_citation_iri("estleg:Reg_1014955_Map_2026", seq=1)
        assert iri == "estleg:Citation_Reg_1014955_Map_2026_1"
        # Match the SHACL pattern from Layer 2a
        import re
        pattern = r"^estleg:Citation_[A-Za-z0-9_]+_[0-9]+$"
        assert re.match(pattern, iri)

    def test_citation_node_with_full_detail(self):
        from extract_cross_references import build_citation_node
        node = build_citation_node(
            iri="estleg:Citation_Reg_1014955_Map_2026_1",
            target_iri="estleg:JS_Par_71",
            citation_detail="lg 1",
            citation_text="jäätmeseaduse § 71 lõike 1",
        )
        assert node["@id"] == "estleg:Citation_Reg_1014955_Map_2026_1"
        assert "estleg:Citation" in node["@type"]
        assert "owl:NamedIndividual" in node["@type"]
        assert node["estleg:citationTarget"] == {"@id": "estleg:JS_Par_71"}
        assert node["estleg:citationDetail"] == "lg 1"
        assert node["estleg:citationText"] == "jäätmeseaduse § 71 lõike 1"

    def test_citation_node_omits_optional_when_none(self):
        from extract_cross_references import build_citation_node
        node = build_citation_node(
            iri="estleg:Citation_X_1",
            target_iri="estleg:Foo_Par_1",
            citation_detail=None,
            citation_text=None,
        )
        # citationTarget is required; the two optionals are omitted entirely
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
    """Build a SHACL-valid Citation IRI from the source act's @id and a
    1-based sequence number.

    'estleg:Reg_1014955_Map_2026', seq=1 → 'estleg:Citation_Reg_1014955_Map_2026_1'.

    The 'estleg:' prefix is stripped from the source IRI before
    embedding (otherwise the SHACL pattern would see two colons).
    """
    shortid = source_act_iri.removeprefix("estleg:")
    return f"estleg:Citation_{shortid}_{seq}"


def build_citation_node(
    iri: str,
    target_iri: str,
    citation_detail: str | None,
    citation_text: str | None,
) -> dict:
    """Build a Citation reified node ready to insert into a peep file's
    @graph. Optional fields are omitted when None — the SHACL shape
    declares them with maxCount 1 but no minCount, so absence is fine.
    """
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

- [ ] **Step 4: Run test to verify it passes**

```
pytest tests/test_extract_cross_references.py::TestCitationNodeBuilder -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/extract_cross_references.py tests/test_extract_cross_references.py
git commit -m "Add Citation IRI builder + reified Citation node builder"
```

---

## Task 4: Preamble citation resolver

**Files:**
- Modify: `scripts/extract_cross_references.py` (add `resolve_preamble_citation`, `build_state_regulation_lookup`, `build_kov_act_lookup`)
- Modify: `tests/test_extract_cross_references.py` (add `TestResolvePreambleCitation`)

The parser produced citation dicts with `form` discriminator. The resolver dispatches on form:
- `law-genitive`: re-use the existing law abbreviation/genitive-name registries (already built by `build_provision_index`); resolve to `<law-IRI>_Par_<N>` when paragraph available, `<law-IRI>` (the act-level node) when not.
- `state-regulation-date-number`: look up by `(issuer, adoption_date, act_number)` in a new pre-built state-regulation lookup.
- `kov-regulation-date-number`: similar, but in a KOV act lookup keyed by `(issuer_display_name, adoption_date, act_number)`.

Returns a tuple `(target_iri, target_act_iri)`. The first is the citation target (paragraph-level or act-level); the second is the enabling act's act-level @id (for `issuedUnder`).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_extract_cross_references.py`:

```python
class TestResolvePreambleCitation:
    @pytest.fixture
    def state_reg_lookup(self):
        # Pre-built lookup: (issuer, adoption_date, act_number) → act @id
        return {
            ("Vabariigi Valitsus", "2019-12-19", "112"):
                "estleg:Reg_1234_Map_2019",
        }

    @pytest.fixture
    def kov_act_lookup(self):
        return {
            ("Tallinna Linnavolikogu", "2020-06-18", "15"):
                "estleg:Reg_5678_Map_2020",
        }

    @pytest.fixture
    def law_lookup(self):
        # abbrev_to_prefix style: maps "alkoholiseaduse" (genitive) to
        # the law's IRI prefix. The existing FULLNAME_GENITIVE +
        # KNOWN_ABBREVIATIONS tables already cover this; the test
        # provides a minimal stub.
        return {
            "alkoholiseaduse": "estleg:AlkS",
            "jäätmeseaduse": "estleg:JS",
        }

    @pytest.fixture
    def provision_index(self):
        # Maps law-prefix → {par_nr: provision_iri}
        return {
            "estleg:AlkS": {"42": "estleg:AlkS_Par_42"},
            "estleg:JS": {"71": "estleg:JS_Par_71"},
        }

    def test_law_with_paragraph(self, state_reg_lookup, kov_act_lookup,
                                  law_lookup, provision_index):
        from extract_cross_references import resolve_preamble_citation
        cit = {
            "form": "law-genitive",
            "law_ref": "jäätmeseaduse",
            "paragraphs": ["71"],
            "citationDetail": "lg 1",
            "citationText": "jäätmeseaduse § 71 lõike 1",
        }
        result = resolve_preamble_citation(
            cit, law_lookup=law_lookup, provision_index=provision_index,
            state_reg_lookup=state_reg_lookup, kov_act_lookup=kov_act_lookup,
        )
        # Returns (target_iri, enabling_act_iri)
        assert result == ("estleg:JS_Par_71", "estleg:JS")

    def test_law_without_paragraph(self, law_lookup, provision_index,
                                     state_reg_lookup, kov_act_lookup):
        """When citation form is law-genitive but paragraphs is empty,
        target is the law's act-level @id."""
        from extract_cross_references import resolve_preamble_citation
        cit = {
            "form": "law-genitive",
            "law_ref": "alkoholiseaduse",
            "paragraphs": [],
            "citationDetail": None,
            "citationText": "alkoholiseaduse",
        }
        result = resolve_preamble_citation(
            cit, law_lookup=law_lookup, provision_index=provision_index,
            state_reg_lookup=state_reg_lookup, kov_act_lookup=kov_act_lookup,
        )
        assert result == ("estleg:AlkS", "estleg:AlkS")

    def test_state_regulation(self, law_lookup, provision_index,
                                state_reg_lookup, kov_act_lookup):
        from extract_cross_references import resolve_preamble_citation
        cit = {
            "form": "state-regulation-date-number",
            "issuer": "Vabariigi Valitsus",
            "adoption_date": "2019-12-19",
            "act_number": "112",
            "citationDetail": None,
            "citationText": "Vabariigi Valitsuse 19. detsembri 2019. a määruse nr 112",
        }
        result = resolve_preamble_citation(
            cit, law_lookup=law_lookup, provision_index=provision_index,
            state_reg_lookup=state_reg_lookup, kov_act_lookup=kov_act_lookup,
        )
        # State regulations resolve to the act-level IRI on both sides
        # (no provision granularity in the date-number form).
        assert result == ("estleg:Reg_1234_Map_2019",
                          "estleg:Reg_1234_Map_2019")

    def test_kov_regulation(self, law_lookup, provision_index,
                              state_reg_lookup, kov_act_lookup):
        from extract_cross_references import resolve_preamble_citation
        cit = {
            "form": "kov-regulation-date-number",
            "issuer": "Tallinna Linnavolikogu",
            "adoption_date": "2020-06-18",
            "act_number": "15",
            "citationDetail": None,
            "citationText": "Tallinna Linnavolikogu 18. juuni 2020 määruse nr 15",
        }
        result = resolve_preamble_citation(
            cit, law_lookup=law_lookup, provision_index=provision_index,
            state_reg_lookup=state_reg_lookup, kov_act_lookup=kov_act_lookup,
        )
        assert result == ("estleg:Reg_5678_Map_2020",
                          "estleg:Reg_5678_Map_2020")

    def test_unresolved_returns_none(self, law_lookup, provision_index,
                                       state_reg_lookup, kov_act_lookup):
        """Unknown law name returns None, doesn't crash."""
        from extract_cross_references import resolve_preamble_citation
        cit = {
            "form": "law-genitive",
            "law_ref": "ettetuntmatu_seaduse",
            "paragraphs": ["1"],
            "citationDetail": None,
            "citationText": "ettetuntmatu seaduse § 1",
        }
        result = resolve_preamble_citation(
            cit, law_lookup=law_lookup, provision_index=provision_index,
            state_reg_lookup=state_reg_lookup, kov_act_lookup=kov_act_lookup,
        )
        assert result is None
```

- [ ] **Step 2: Run (expect ImportError)**

```
pytest tests/test_extract_cross_references.py::TestResolvePreambleCitation -v
```

Expected: FAIL.

- [ ] **Step 3: Implement `resolve_preamble_citation`**

Add to `scripts/extract_cross_references.py`:

```python
def resolve_preamble_citation(
    cit: dict,
    *,
    law_lookup: dict[str, str],
    provision_index: dict[str, dict[str, str]],
    state_reg_lookup: dict[tuple[str, str, str], str],
    kov_act_lookup: dict[tuple[str, str, str], str],
) -> tuple[str, str] | None:
    """Resolve a preamble citation dict to (target_iri, enabling_act_iri).

    target_iri is what citationTarget should point at:
        - paragraph-level for law citations with §
        - act-level otherwise (and for state/kov regulation citations,
          since the date-number form has no provision granularity)

    enabling_act_iri is the act @id for issuedUnder. Always act-level.

    Returns None if the citation can't be resolved against any lookup.
    """
    form = cit.get("form")

    if form == "law-genitive":
        prefix = law_lookup.get(cit["law_ref"].lower())
        if prefix is None:
            return None
        # Act-level IRI is just the prefix; paragraph IRIs are
        # provision_index[prefix][par_nr]. The act node's @id in real
        # peep files is typically `<prefix>_Map_<year>` but the
        # provision_index also lets the resolver tolerate either form
        # for issuedUnder. For Layer 2b we use the prefix directly as
        # the enabling-act @id; the inverse generator will dereference
        # via provision_index when computing implementedBy.
        if not cit.get("paragraphs"):
            return (prefix, prefix)
        par = cit["paragraphs"][0]
        target = provision_index.get(prefix, {}).get(par)
        if target is None:
            # Paragraph not in our provision index — fall back to act-level
            return (prefix, prefix)
        return (target, prefix)

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

- [ ] **Step 4: Run test**

```
pytest tests/test_extract_cross_references.py::TestResolvePreambleCitation -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/extract_cross_references.py tests/test_extract_cross_references.py
git commit -m "Add preamble citation resolver dispatching on form"
```

---

## Task 5: State-regulation and KOV act lookups

**Files:**
- Modify: `scripts/extract_cross_references.py` (add `build_state_regulation_lookup`, `build_kov_act_lookup`)
- Modify: `tests/test_extract_cross_references.py` (add `TestActLookups`)

The resolver expects pre-built lookups keyed by `(issuer, adoption_date, act_number)`. Build them by walking the corpus once at startup. The data needed is on each act node:

- `estleg:issuer` (string, e.g. "Vabariigi Valitsus" or "Tallinna Linnavolikogu")
- `estleg:entryIntoForce` or `estleg:adoptionDate` (depends on what the generator stamped — adoption is preferred but entryIntoForce is acceptable as fallback)
- `estleg:actNumber` (string)

Some KOV acts may have these only partially populated. Skip an act when `act_number` is missing — it can't be resolved by the date-number form anyway.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_extract_cross_references.py`:

```python
class TestActLookups:
    def test_build_state_regulation_lookup(self, tmp_path):
        from extract_cross_references import build_state_regulation_lookup

        # Stage two state regulation peep files
        riik = tmp_path / "regulations" / "riik"
        riik.mkdir(parents=True)
        (riik / "reg_a_peep.json").write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Reg_1234_Map_2019",
                 "@type": ["owl:Ontology", "estleg:Act",
                           "estleg:GovernmentRegulation"],
                 "estleg:issuer": "Vabariigi Valitsus",
                 "estleg:adoptionDate": "2019-12-19",
                 "estleg:actNumber": "112"}
            ],
        }), encoding="utf-8")
        (riik / "reg_b_peep.json").write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Reg_9999_Map_2020",
                 "@type": ["owl:Ontology", "estleg:Act",
                           "estleg:MinisterialRegulation"],
                 "estleg:issuer": "Sotsiaalminister",
                 "estleg:adoptionDate": "2020-03-15",
                 "estleg:actNumber": "7"}
            ],
        }), encoding="utf-8")

        lookup = build_state_regulation_lookup(riik)
        assert lookup[("Vabariigi Valitsus", "2019-12-19", "112")] == \
            "estleg:Reg_1234_Map_2019"
        assert lookup[("Sotsiaalminister", "2020-03-15", "7")] == \
            "estleg:Reg_9999_Map_2020"

    def test_build_kov_act_lookup(self, tmp_path):
        from extract_cross_references import build_kov_act_lookup

        kov = tmp_path / "regulations" / "kov" / "tallinna_linnavolikogu"
        kov.mkdir(parents=True)
        (kov / "act_peep.json").write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Reg_5678_Map_2020",
                 "@type": ["owl:Ontology", "estleg:Act",
                           "estleg:MunicipalRegulation"],
                 "estleg:issuer": "Tallinna Linnavolikogu",
                 "estleg:adoptionDate": "2020-06-18",
                 "estleg:actNumber": "15"}
            ],
        }), encoding="utf-8")

        lookup = build_kov_act_lookup(kov.parent)
        assert lookup[("Tallinna Linnavolikogu", "2020-06-18", "15")] == \
            "estleg:Reg_5678_Map_2020"

    def test_skip_acts_without_act_number(self, tmp_path):
        """Not every act has actNumber populated — those can't be
        resolved by the date-number form; skip silently."""
        from extract_cross_references import build_state_regulation_lookup

        riik = tmp_path / "regulations" / "riik"
        riik.mkdir(parents=True)
        (riik / "reg_no_num_peep.json").write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Reg_X",
                 "@type": ["owl:Ontology", "estleg:NationalRegulation"],
                 "estleg:issuer": "Vabariigi Valitsus",
                 "estleg:adoptionDate": "2020-01-01"}
                 # NO estleg:actNumber
            ],
        }), encoding="utf-8")

        lookup = build_state_regulation_lookup(riik)
        assert lookup == {}
```

- [ ] **Step 2: Run (expect ImportError)**

```
pytest tests/test_extract_cross_references.py::TestActLookups -v
```

Expected: FAIL.

- [ ] **Step 3: Implement the lookup builders**

Add to `scripts/extract_cross_references.py`:

```python
def _act_metadata_for_lookup(node: dict) -> tuple[str, str, str] | None:
    """Extract (issuer, adoption_date, act_number) tuple from an act
    node. Returns None if any field is missing — the act can't be
    resolved by the date-number form without all three.

    Falls back to entryIntoForce when adoptionDate is absent (some
    older generators stamped only the entry-into-force date).
    """
    issuer = node.get("estleg:issuer")
    adoption = node.get("estleg:adoptionDate") or node.get("estleg:entryIntoForce")
    if isinstance(adoption, dict):
        adoption = adoption.get("@value")
    act_num = node.get("estleg:actNumber")
    if not (issuer and adoption and act_num):
        return None
    return (issuer, adoption, str(act_num))


def _walk_act_nodes(root: Path) -> Iterable[tuple[dict, Path]]:
    """Yield (act_node, peep_path) for every peep file under root.
    Recursive; walks all subdirectories. Skips files that fail to
    parse or have no recognized act node."""
    for peep in sorted(root.rglob("*_peep.json")):
        try:
            with open(peep, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue
        for node in doc.get("@graph", []):
            types = node.get("@type") or []
            if isinstance(types, str):
                types = [types]
            if any(t in {"estleg:NationalRegulation",
                          "estleg:GovernmentRegulation",
                          "estleg:MinisterialRegulation",
                          "estleg:MunicipalRegulation"} for t in types):
                if "@id" in node:
                    yield node, peep


def build_state_regulation_lookup(riik_root: Path) -> dict[tuple[str, str, str], str]:
    """Build (issuer, adoption_date, act_number) → act @id lookup
    from state regulation peep files under riik_root."""
    lookup: dict[tuple[str, str, str], str] = {}
    if not riik_root.is_dir():
        return lookup
    for node, _path in _walk_act_nodes(riik_root):
        types = node.get("@type") or []
        if isinstance(types, str):
            types = [types]
        # State regs only — exclude MunicipalRegulation
        if "estleg:MunicipalRegulation" in types:
            continue
        key = _act_metadata_for_lookup(node)
        if key is not None:
            lookup[key] = node["@id"]
    return lookup


def build_kov_act_lookup(kov_root: Path) -> dict[tuple[str, str, str], str]:
    """Build (issuer, adoption_date, act_number) → act @id lookup from
    KOV peep files under kov_root."""
    lookup: dict[tuple[str, str, str], str] = {}
    if not kov_root.is_dir():
        return lookup
    for node, _path in _walk_act_nodes(kov_root):
        types = node.get("@type") or []
        if isinstance(types, str):
            types = [types]
        if "estleg:MunicipalRegulation" not in types:
            continue
        key = _act_metadata_for_lookup(node)
        if key is not None:
            lookup[key] = node["@id"]
    return lookup
```

Add `from typing import Iterable` if not already imported.

- [ ] **Step 4: Run test**

```
pytest tests/test_extract_cross_references.py::TestActLookups -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/extract_cross_references.py tests/test_extract_cross_references.py
git commit -m "Add state-regulation and KOV act lookups for preamble resolver"
```

---

## Task 6: KOV body-text scope rules

**Files:**
- Modify: `scripts/extract_cross_references.py` (add `resolve_kov_internal_act_ref`)
- Modify: `tests/test_extract_cross_references.py` (add `TestKovBodyTextScope`)
- Create: `tests/fixtures/kov_layer2b/sample_kov_with_internal_ref.json`

Body-text references like `linnavolikogu määrus nr 5` need municipality-aware scoping. Resolution order from the spec:

1. Same `enactedByMunicipality` — scope to all issuers (volikogu and valitsus) of the same municipality. Catches the common vallavalitsus → vallavolikogu case.
2. Within that scope, narrow by issuer body type if the citation specifies one ("linnavolikogu määrus" → volikogu; "linnavalitsuse määrus" → valitsus).
3. Then narrow by act number / title.
4. Global fallback only on a unique, high-confidence match.

The resolver function takes the source act's `enactedByMunicipality`, the body-text reference (parsed into act_number + body_type if available), the kov_act_lookup, and an issuer registry. Returns the target IRI or None.

This is more complex than preamble resolution because body text doesn't always include date — only act number and body type. Layer 2b implements a basic municipality-first match by act_number; richer disambiguation lands in 2c if needed.

- [ ] **Step 1: Create the fixture**

Create `tests/fixtures/kov_layer2b/sample_kov_with_internal_ref.json`:

```json
{
  "@context": {
    "estleg": "https://data.riik.ee/ontology/estleg#",
    "owl": "http://www.w3.org/2002/07/owl#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "dc": "http://purl.org/dc/elements/1.1/"
  },
  "@graph": [
    {
      "@id": "estleg:Reg_2002_Map_2026",
      "@type": ["owl:Ontology", "estleg:Act", "estleg:MunicipalRegulation"],
      "rdfs:label": "Tallinna linnavalitsuse korraldus",
      "dc:source": "Tallinna linnavalitsuse korraldus",
      "estleg:globalId": "888888888",
      "estleg:terviktekstId": "2002",
      "estleg:enactedBy": {"@id": "estleg:Issuer_tallinna_linnavalitsus"},
      "estleg:enactedByMunicipality": {"@id": "estleg:Municipality_EHAK_0784"},
      "estleg:titleNormalized": "korraldus"
    },
    {
      "@id": "estleg:Reg_2002_Par_1",
      "@type": ["owl:NamedIndividual", "estleg:KovProvision"],
      "estleg:paragrahv": "§ 1.",
      "estleg:summary": "stub",
      "estleg:legalText": "Käesolev korraldus on antud Tallinna Linnavolikogu määruse nr 15 § 4 alusel.",
      "estleg:enactedBy": {"@id": "estleg:Issuer_tallinna_linnavalitsus"},
      "estleg:enactedByMunicipality": {"@id": "estleg:Municipality_EHAK_0784"},
      "estleg:partOfAct": {"@id": "estleg:Reg_2002_Map_2026"}
    }
  ]
}
```

- [ ] **Step 2: Write the failing test**

Append to `tests/test_extract_cross_references.py`:

```python
class TestKovBodyTextScope:
    @pytest.fixture
    def kov_act_lookup(self):
        return {
            ("Tallinna Linnavolikogu", None, "15"): "estleg:Reg_5678_Map_2020",
            ("Tartu Linnavolikogu", None, "15"): "estleg:Reg_TARTU_15",
        }

    @pytest.fixture
    def issuer_registry(self):
        # Maps issuer @id → (municipality @id, body_type)
        return {
            "estleg:Issuer_tallinna_linnavolikogu":
                ("estleg:Municipality_EHAK_0784", "volikogu"),
            "estleg:Issuer_tallinna_linnavalitsus":
                ("estleg:Municipality_EHAK_0784", "valitsus"),
            "estleg:Issuer_tartu_linnavolikogu":
                ("estleg:Municipality_EHAK_0793", "volikogu"),
        }

    def test_same_municipality_volikogu_match(self, kov_act_lookup,
                                                issuer_registry):
        """Tallinn vallavalitsus citing 'linnavolikogu määrus nr 15'
        resolves to the Tallinn linnavolikogu act."""
        from extract_cross_references import resolve_kov_internal_act_ref

        result = resolve_kov_internal_act_ref(
            source_municipality="estleg:Municipality_EHAK_0784",
            body_type="volikogu",
            act_number="15",
            kov_act_lookup=kov_act_lookup,
            issuer_registry=issuer_registry,
        )
        assert result == "estleg:Reg_5678_Map_2020"

    def test_no_municipality_scope_returns_none(self, kov_act_lookup,
                                                  issuer_registry):
        """Without source_municipality, can't disambiguate Tallinn vs
        Tartu both having a 'linnavolikogu määrus nr 15'. Returns None
        (no global fallback when ambiguous)."""
        from extract_cross_references import resolve_kov_internal_act_ref

        result = resolve_kov_internal_act_ref(
            source_municipality=None,
            body_type="volikogu",
            act_number="15",
            kov_act_lookup=kov_act_lookup,
            issuer_registry=issuer_registry,
        )
        assert result is None

    def test_different_municipality_returns_none(self, kov_act_lookup,
                                                   issuer_registry):
        """Tartu source citing 'linnavolikogu määrus nr 15' must NOT
        resolve to Tallinn's act (cross-municipality match is unsafe)."""
        from extract_cross_references import resolve_kov_internal_act_ref

        result = resolve_kov_internal_act_ref(
            source_municipality="estleg:Municipality_EHAK_0793",
            body_type="volikogu",
            act_number="15",
            kov_act_lookup=kov_act_lookup,
            issuer_registry=issuer_registry,
        )
        assert result == "estleg:Reg_TARTU_15"

    def test_body_type_disambiguates_within_municipality(self, kov_act_lookup,
                                                           issuer_registry):
        """If the same municipality has both volikogu and valitsus acts
        numbered 15, body_type narrows the search."""
        from extract_cross_references import resolve_kov_internal_act_ref

        # Add a Tallinn linnavalitsus act with the same number 15
        extended_lookup = {
            **kov_act_lookup,
            ("Tallinna Linnavalitsus", None, "15"): "estleg:Reg_TLV_15",
        }
        result = resolve_kov_internal_act_ref(
            source_municipality="estleg:Municipality_EHAK_0784",
            body_type="valitsus",
            act_number="15",
            kov_act_lookup=extended_lookup,
            issuer_registry=issuer_registry,
        )
        assert result == "estleg:Reg_TLV_15"
```

- [ ] **Step 3: Run (expect ImportError)**

```
pytest tests/test_extract_cross_references.py::TestKovBodyTextScope -v
```

Expected: FAIL.

- [ ] **Step 4: Implement `resolve_kov_internal_act_ref`**

Add to `scripts/extract_cross_references.py`:

```python
def resolve_kov_internal_act_ref(
    *,
    source_municipality: str | None,
    body_type: str | None,  # "volikogu" | "valitsus" | None
    act_number: str,
    kov_act_lookup: dict[tuple[str, str | None, str], str],
    issuer_registry: dict[str, tuple[str, str]],
) -> str | None:
    """Resolve a KOV-internal act reference by act number, scoped to the
    source act's municipality.

    Args:
        source_municipality: the @id of the citing act's
            estleg:enactedByMunicipality. None disables municipality
            scoping (returns None — no global fallback).
        body_type: 'volikogu' or 'valitsus' if the citation specifies one,
            else None.
        act_number: the parsed act number from the citation.
        kov_act_lookup: (issuer_display_name, adoption_date, act_number)
            → act @id. adoption_date may be None for body-text refs.
        issuer_registry: issuer @id → (municipality @id, body_type).

    Returns:
        The resolved target act @id, or None when:
        - source_municipality is None (no scoping context)
        - no candidate matches the (municipality, body_type, act_number) filter
        - multiple candidates match and we can't disambiguate
    """
    if source_municipality is None:
        return None

    # Find issuer @ids in the same municipality, optionally filtering
    # by body_type. The issuer display names of these matches form the
    # search scope.
    candidate_issuer_displays: list[str] = []
    for issuer_iri, (mun_iri, btype) in issuer_registry.items():
        if mun_iri != source_municipality:
            continue
        if body_type is not None and btype != body_type:
            continue
        # Extract the display name from the issuer @id.
        # estleg:Issuer_tallinna_linnavolikogu → "Tallinna Linnavolikogu"
        slug = issuer_iri.removeprefix("estleg:Issuer_")
        display = " ".join(part.capitalize() for part in slug.split("_"))
        candidate_issuer_displays.append(display)

    # Filter the kov_act_lookup to entries whose issuer is in the
    # candidate set AND whose act_number matches.
    matches = [
        target_iri
        for (issuer, _adoption_date, num), target_iri in kov_act_lookup.items()
        if issuer in candidate_issuer_displays and num == act_number
    ]
    # Exactly-one match resolves; zero or multiple → None.
    if len(matches) == 1:
        return matches[0]
    return None
```

- [ ] **Step 5: Run tests**

```
pytest tests/test_extract_cross_references.py::TestKovBodyTextScope -v
```

Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add scripts/extract_cross_references.py tests/test_extract_cross_references.py tests/fixtures/kov_layer2b/sample_kov_with_internal_ref.json
git commit -m "Add KOV body-text scope rules (municipality-first resolver)"
```

---

## Task 7: Wire preamble pass into `extract_cross_references.main()`

**Files:**
- Modify: `scripts/extract_cross_references.py` (extend `main()` with preamble pass + Citation node emission)
- Modify: `tests/test_extract_cross_references.py` (add `TestMainPreamblePass`)

The existing main() walks all peep files and processes body-text references via `process_law_file`. Add a parallel act-level pass over the same files that:
1. For each peep file with an `estleg:preambleText` on the act node, run `extract_preamble_citations`.
2. Resolve each citation via `resolve_preamble_citation` against the pre-built lookups.
3. For each resolved citation: append `estleg:issuedUnder = {"@id": enabling_act_iri}` to the act node's existing `issuedUnder` list. If the citation has `citationDetail`, also emit a `Citation` node and an `estleg:implementsCitation` link.

The Citation nodes go into the same peep file's `@graph` (alongside the act and its provisions). Sequence numbers reset per peep file.

- [ ] **Step 1: Write the failing integration test**

Append to `tests/test_extract_cross_references.py`:

```python
class TestMainPreamblePass:
    def test_preamble_pass_emits_issued_under_and_citations(
        self, tmp_path, monkeypatch
    ):
        """End-to-end: stage a KOV act with a preamble and a target law,
        run main(), assert issuedUnder + Citation node land on the act."""
        import extract_cross_references as mod
        import estleg_common

        # Build a tiny corpus
        krr = tmp_path / "krr_outputs"
        kov = krr / "regulations" / "kov" / "tallinna_linnavolikogu"
        kov.mkdir(parents=True)
        # The KOV act with preamble
        peep = kov / "act_peep.json"
        import shutil
        shutil.copy(
            FIXTURE_DIR / "sample_kov_act_with_preamble.json",
            peep,
        )
        # The law the preamble cites — must be present so the resolver
        # can find it. Use a minimal law fixture.
        (krr / "jaatmeseadus_peep.json").write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:JS",
                 "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
                 "rdfs:label": "Jäätmeseadus",
                 "dc:source": "Jäätmeseadus"},
                {"@id": "estleg:JS_Par_71",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 71",
                 "estleg:summary": "stub"}
            ],
        }), encoding="utf-8")
        # The state regulation the preamble cites — use a minimal fixture
        riik = krr / "regulations" / "riik"
        riik.mkdir()
        (riik / "vv_112_peep.json").write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Reg_VV112_Map_2019",
                 "@type": ["owl:Ontology", "estleg:Act",
                           "estleg:GovernmentRegulation"],
                 "estleg:issuer": "Vabariigi Valitsus",
                 "estleg:adoptionDate": "2019-12-19",
                 "estleg:actNumber": "112"}
            ],
        }), encoding="utf-8")

        # INDEX.json so the laws get discovered
        (krr / "INDEX.json").write_text(json.dumps({
            "laws": [{"name": "jaatmeseadus",
                      "files": ["jaatmeseadus_peep.json"]}],
        }), encoding="utf-8")

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)

        rc = mod.main()
        assert rc in (None, 0)

        # Re-read the KOV peep and verify Layer-2b triples
        with open(peep, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        act = next(n for n in doc["@graph"]
                   if "estleg:MunicipalRegulation" in (n.get("@type") or []))

        # issuedUnder should have 2 entries (the law + the state reg)
        issued = act.get("estleg:issuedUnder")
        if isinstance(issued, dict):
            issued = [issued]
        issued_iris = {x["@id"] for x in issued}
        assert "estleg:JS" in issued_iris
        assert "estleg:Reg_VV112_Map_2019" in issued_iris

        # implementsCitation should have at least 1 entry (the lg-level
        # citation on jäätmeseaduse § 71 lõike 1)
        cites = act.get("estleg:implementsCitation")
        if isinstance(cites, dict):
            cites = [cites]
        assert len(cites) >= 1

        # The Citation node itself should be in @graph
        cit_nodes = [n for n in doc["@graph"]
                     if "estleg:Citation" in (n.get("@type") or [])]
        assert len(cit_nodes) >= 1
        cit = cit_nodes[0]
        assert cit["@id"].startswith("estleg:Citation_Reg_1014955_Map_2026_")
        assert cit["estleg:citationTarget"]["@id"] == "estleg:JS_Par_71"
        assert cit["estleg:citationDetail"] == "lg 1"
```

- [ ] **Step 2: Run test (expect failure — preamble pass doesn't exist yet)**

```
pytest tests/test_extract_cross_references.py::TestMainPreamblePass -v
```

Expected: FAIL — current main() only does body-text references; the preamble pass and Citation node emission are missing.

- [ ] **Step 3: Add the preamble pass to `main()`**

In `scripts/extract_cross_references.py::main()`, after the existing body-text-references logic but before the final report-write, add a new section:

```python
    # =====================================================================
    # Layer 2b: preamble citation pass
    # =====================================================================
    print("\n--- Preamble citation pass ---")

    riik_root = KRR_DIR / "regulations" / "riik"
    kov_root = KRR_DIR / "regulations" / "kov"

    state_reg_lookup = build_state_regulation_lookup(riik_root)
    kov_act_lookup = build_kov_act_lookup(kov_root)
    print(f"  state-reg lookup: {len(state_reg_lookup)} entries")
    print(f"  KOV act lookup:   {len(kov_act_lookup)} entries")

    preamble_files_processed = 0
    preamble_files_processed_kov = 0
    preamble_files_with_output = 0
    preamble_files_with_output_kov = 0
    preamble_triples = 0
    preamble_triples_kov = 0
    preamble_unresolved = 0

    for peep_path in iter_peep_files():
        is_kov = "regulations/kov" in str(peep_path)
        try:
            with open(peep_path, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue

        # Find the act node
        act_node = None
        for node in doc.get("@graph", []):
            types = node.get("@type") or []
            if isinstance(types, str):
                types = [types]
            if any(t in {"estleg:NationalRegulation",
                          "estleg:GovernmentRegulation",
                          "estleg:MinisterialRegulation",
                          "estleg:MunicipalRegulation"} for t in types):
                act_node = node
                break
        if act_node is None:
            continue
        preamble_text = act_node.get("estleg:preambleText")
        if not preamble_text:
            continue

        cits = extract_preamble_citations(preamble_text)
        if not cits:
            continue

        preamble_files_processed += 1
        if is_kov:
            preamble_files_processed_kov += 1

        # Clear any prior preamble-derived triples (idempotent rerun)
        act_node.pop("estleg:issuedUnder", None)
        act_node.pop("estleg:implementsCitation", None)
        # Remove any existing Citation nodes from @graph that belong to
        # this source act
        source_shortid = act_node["@id"].removeprefix("estleg:")
        prefix = f"estleg:Citation_{source_shortid}_"
        doc["@graph"] = [
            n for n in doc["@graph"]
            if not (n.get("@id", "").startswith(prefix))
        ]

        issued_under: list[dict] = []
        implements: list[dict] = []
        seq = 0
        had_output = False
        for cit in cits:
            resolved = resolve_preamble_citation(
                cit,
                law_lookup=abbrev_to_prefix,  # built earlier in main()
                provision_index=prefix_to_provisions,
                state_reg_lookup=state_reg_lookup,
                kov_act_lookup=kov_act_lookup,
            )
            if resolved is None:
                preamble_unresolved += 1
                continue
            target_iri, enabling_iri = resolved
            issued_under.append({"@id": enabling_iri})
            preamble_triples += 1
            if is_kov:
                preamble_triples_kov += 1

            if cit.get("citationDetail"):
                seq += 1
                cit_iri = build_citation_iri(act_node["@id"], seq)
                cit_node = build_citation_node(
                    iri=cit_iri,
                    target_iri=target_iri,
                    citation_detail=cit["citationDetail"],
                    citation_text=cit["citationText"],
                )
                doc["@graph"].append(cit_node)
                implements.append({"@id": cit_iri})
                preamble_triples += 1
                if is_kov:
                    preamble_triples_kov += 1
            had_output = True

        if issued_under:
            act_node["estleg:issuedUnder"] = issued_under
        if implements:
            act_node["estleg:implementsCitation"] = implements

        if had_output:
            preamble_files_with_output += 1
            if is_kov:
                preamble_files_with_output_kov += 1
            with open(peep_path, "w", encoding="utf-8") as fh:
                json.dump(doc, fh, ensure_ascii=False, indent=2)
                fh.write("\n")

    print(f"  Preamble citations: {preamble_triples} triples emitted "
          f"(KOV: {preamble_triples_kov}, unresolved: {preamble_unresolved})")
```

The variables `abbrev_to_prefix` and `prefix_to_provisions` are already populated earlier in main() (around line 405). The new section reuses them.

- [ ] **Step 4: Add the imports at the top of the script**

If not already imported:

```python
from extract_cross_references import (
    extract_preamble_citations,
    resolve_preamble_citation,
    build_citation_iri,
    build_citation_node,
    build_state_regulation_lookup,
    build_kov_act_lookup,
)
```

(The script imports from itself only at test time; the actual functions are already in the same module.)

- [ ] **Step 5: Run the integration test**

```
pytest tests/test_extract_cross_references.py::TestMainPreamblePass -v
```

Expected: 1 passed.

```
pytest -q
```

Expected: prior count + the integration test, all passing.

- [ ] **Step 6: Commit**

```bash
git add scripts/extract_cross_references.py tests/test_extract_cross_references.py
git commit -m "Wire preamble pass into extract_cross_references main()

Each KOV act's preambleText is parsed and resolved; resolved citations
emit issuedUnder triples on the act node, and citations carrying lg/p
detail also produce reified Citation nodes with citationTarget/
citationDetail/citationText. Idempotent: clears prior preamble-derived
triples and Citation nodes before regenerating."
```

---

## Task 8: Add coverage instrumentation to `extract_cross_references`

**Files:**
- Modify: `scripts/extract_cross_references.py`

Apply the canonical coverage instrumentation pattern from Layer 2a (Task 6 of layer2a plan, lines ~1100-1180). Pipeline-specific notes:

- `_files_processed` = peep files reached the body-text + preamble passes
- `_triples` = body-text references emitted PLUS preamble issuedUnder/implementsCitation/Citation triples
- `_triples_kov` = same restricted to `is_kov` files
- `_files_with_output` = files where at least one body-text or preamble triple was emitted
- `_fallback_hits` = preamble citations that fell back to act-level when paragraph index was missing
- `_unresolved` = preamble citations that didn't resolve
- Coverage report path: `krr_outputs/reports/kov/extract_cross_references_coverage.json`

- [ ] **Step 1: Add the imports**

At the top of `scripts/extract_cross_references.py`, alongside existing imports:

```python
import time
from datetime import datetime, timezone
from kov_pipeline_coverage import (
    CoverageReport,
    measure_runtime,
    resolve_pipeline_version,
    write_coverage_report,
)
```

- [ ] **Step 2: Wrap `main()` with instrumentation**

Add at the top of main():

```python
def main() -> None:
    _start = time.perf_counter()
    _files = list(iter_peep_files())
    _kov_files = [f for f in _files if "regulations/kov" in str(f)]
    _files_processed = 0
    _files_processed_kov = 0
    _files_with_output = 0
    _files_with_output_kov = 0
    _files_skipped = 0
    _skip_reasons: dict[str, int] = {}
    _triples = 0
    _triples_kov = 0
    _fallback_hits = 0
    _unresolved = 0
    _failures: list[str] = []
    # ... existing main body ...
```

In the body-text references loop, increment `_triples` for each `references` entry written, and `_triples_kov` if the source peep is under regulations/kov. Track `_files_with_output[_kov]` for files that wrote ≥1 reference.

In the preamble-pass loop (Task 7), the variables `preamble_files_processed_kov`, `preamble_triples_kov`, etc. that were declared local to the pass should be merged into the outer counters at the end of the pass:

```python
    _files_processed += preamble_files_processed
    _files_processed_kov += preamble_files_processed_kov
    _files_with_output += preamble_files_with_output
    _files_with_output_kov += preamble_files_with_output_kov
    _triples += preamble_triples
    _triples_kov += preamble_triples_kov
    _unresolved += preamble_unresolved
```

At the end of main(), write the coverage report:

```python
    _wall, _rate, _peak_mb = measure_runtime(_start, _files_processed)
    write_coverage_report(
        CoverageReport(
            pipeline="extract_cross_references",
            run_timestamp=datetime.now(timezone.utc).isoformat(),
            pipeline_version=resolve_pipeline_version(),
            input_files_total=len(_files),
            input_files_kov=len(_kov_files),
            files_processed=_files_processed,
            files_processed_kov=_files_processed_kov,
            files_with_output=_files_with_output,
            files_with_output_kov=_files_with_output_kov,
            files_skipped=_files_skipped,
            skip_reasons=_skip_reasons,
            triples_emitted=_triples,
            triples_emitted_kov=_triples_kov,
            fallback_hits=_fallback_hits,
            unresolved_references=_unresolved,
            wall_time_seconds=round(_wall, 2),
            items_per_second=round(_rate, 2),
            peak_memory_mb=round(_peak_mb, 1),
            error_count=len(_failures),
            failure_samples=_failures,
        ),
        REPO_ROOT / "krr_outputs" / "reports" / "kov"
        / "extract_cross_references_coverage.json",
    )
```

- [ ] **Step 3: Run the full suite**

```
pytest -q
```

Expected: still passing.

- [ ] **Step 4: Commit**

```bash
git add scripts/extract_cross_references.py
git commit -m "Add coverage instrumentation to extract_cross_references"
```

---

## Task 9: Unpin `extract_cross_references` (remove DEFERRED comments)

**Files:**
- Modify: `scripts/extract_cross_references.py`

Remove the 3 `include_kov=False  # DEFERRED to Layer 2b` pins added in Layer 2a Task 3. Each call site now runs with the new `include_kov=True` default.

- [ ] **Step 1: Locate the pins**

```bash
grep -n "include_kov=False" scripts/extract_cross_references.py
```

Expected: 3 lines, each with a `# DEFERRED to Layer 2b` comment.

- [ ] **Step 2: Replace each pin**

For Pattern A (`for x in iter_peep_files(include_kov=False):  # DEFERRED to Layer 2b`):

Change to:
```python
    for x in iter_peep_files():
```

For Pattern B (`xs = iter_peep_files(include_kov=False)  # DEFERRED to Layer 2b`):

Change to:
```python
    xs = iter_peep_files()
```

The default flip means `iter_peep_files()` already returns KOV files.

- [ ] **Step 3: Verify**

```bash
grep -n "include_kov=False" scripts/extract_cross_references.py
```

Expected: empty.

```bash
python3 -c "import ast; ast.parse(open('scripts/extract_cross_references.py').read())" && echo OK
```

Expected: OK.

```
pytest -q
```

Expected: still passing.

- [ ] **Step 4: Commit**

```bash
git add scripts/extract_cross_references.py
git commit -m "Unpin extract_cross_references — runs KOV by default"
```

---

## Task 10: Update `generate_inverse_references` for `implementedBy` projection

**Files:**
- Modify: `scripts/generate_inverse_references.py`
- Create: `tests/test_generate_inverse_references.py`

The existing script emits `referencedBy` from `references`. Layer 2b adds two new properties:

- `estleg:implementedBy` — filtered projection from `issuedUnder` and `Citation.citationTarget` only (not body-text `references`).
- `estleg:implementedByCount` — aggregate count of `implementedBy` entries.

Both are written to the **enabling act** (or its provisions when the citation is paragraph-level). Idempotent — clear before regenerate.

- [ ] **Step 1: Write the failing test**

Create `tests/test_generate_inverse_references.py`:

```python
"""Tests for scripts/generate_inverse_references.py — implementedBy + count."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent


class TestCollectPreambleBackReferences:
    def test_collects_issued_under(self, tmp_path):
        """Each estleg:issuedUnder triple becomes an inverse entry."""
        from generate_inverse_references import collect_preamble_back_references

        kov = tmp_path / "regulations" / "kov" / "tallinna_linnavolikogu"
        kov.mkdir(parents=True)
        (kov / "act_peep.json").write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Reg_X_Map",
                 "@type": ["owl:Ontology", "estleg:Act",
                           "estleg:MunicipalRegulation"],
                 "estleg:issuedUnder": [
                     {"@id": "estleg:JS"},
                     {"@id": "estleg:Reg_VV112_Map_2019"}
                 ]}
            ],
        }), encoding="utf-8")

        result = collect_preamble_back_references(tmp_path)
        assert "estleg:JS" in result
        assert "estleg:Reg_X_Map" in result["estleg:JS"]
        assert "estleg:Reg_VV112_Map_2019" in result
        assert "estleg:Reg_X_Map" in result["estleg:Reg_VV112_Map_2019"]

    def test_collects_citation_target(self, tmp_path):
        """Reified Citation nodes contribute via citationTarget."""
        from generate_inverse_references import collect_preamble_back_references

        kov = tmp_path / "regulations" / "kov" / "tallinna_linnavolikogu"
        kov.mkdir(parents=True)
        (kov / "act_peep.json").write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Reg_X_Map",
                 "@type": ["owl:Ontology", "estleg:MunicipalRegulation"],
                 "estleg:implementsCitation": [
                     {"@id": "estleg:Citation_X_1"}
                 ]},
                {"@id": "estleg:Citation_X_1",
                 "@type": ["owl:NamedIndividual", "estleg:Citation"],
                 "estleg:citationTarget": {"@id": "estleg:JS_Par_71"}}
            ],
        }), encoding="utf-8")

        result = collect_preamble_back_references(tmp_path)
        assert "estleg:JS_Par_71" in result
        assert "estleg:Reg_X_Map" in result["estleg:JS_Par_71"]

    def test_ignores_body_text_references(self, tmp_path):
        """Plain references (body-text) must NOT appear in implementedBy
        projection — those are referencedBy."""
        from generate_inverse_references import collect_preamble_back_references

        kov = tmp_path / "regulations" / "kov" / "tallinna_linnavolikogu"
        kov.mkdir(parents=True)
        (kov / "act_peep.json").write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Reg_X_Par_5",
                 "@type": ["owl:NamedIndividual"],
                 "estleg:references": [
                     {"@id": "estleg:JS_Par_71"}
                 ]}
            ],
        }), encoding="utf-8")

        result = collect_preamble_back_references(tmp_path)
        # Body-text references must not contribute to implementedBy
        assert result == {}


class TestComputeImplementedByCount:
    def test_count_matches_list_length(self):
        from generate_inverse_references import compute_implemented_by_count

        graph = [
            {"@id": "estleg:JS",
             "estleg:implementedBy": [
                 {"@id": "estleg:KOV_1"},
                 {"@id": "estleg:KOV_2"},
                 {"@id": "estleg:KOV_3"},
             ]},
        ]
        compute_implemented_by_count(graph)
        node = graph[0]
        assert node["estleg:implementedByCount"] == {
            "@type": "xsd:integer", "@value": "3"
        }

    def test_count_omitted_when_no_implementedBy(self):
        from generate_inverse_references import compute_implemented_by_count

        graph = [{"@id": "estleg:UnusedAct"}]
        compute_implemented_by_count(graph)
        assert "estleg:implementedByCount" not in graph[0]
```

- [ ] **Step 2: Run test (expect ImportError)**

```
pytest tests/test_generate_inverse_references.py -v
```

Expected: FAIL.

- [ ] **Step 3: Implement the helpers**

Add to `scripts/generate_inverse_references.py`:

```python
def collect_preamble_back_references(
    krr_root: Path,
) -> dict[str, list[str]]:
    """Walk all peep files under krr_root and build the inverse mapping
    target_iri → [source_act_iris] for the Layer 2b implementedBy
    projection.

    The projection is filtered: only triples coming from
    estleg:issuedUnder (act-level enabling-act link) and
    estleg:Citation.citationTarget (paragraph-level enabling
    provision) contribute. Body-text estleg:references are excluded —
    those are the existing referencedBy projection.

    Idempotent: running this function twice with the same input
    produces the same output. The caller is responsible for clearing
    any prior estleg:implementedBy before applying.
    """
    inverse: dict[str, list[str]] = {}

    for peep in sorted(krr_root.rglob("*_peep.json")):
        try:
            with open(peep, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue

        graph = doc.get("@graph", [])

        # First pass: collect Citation @id → citationTarget mapping in
        # this file (so the second pass can dereference
        # implementsCitation links).
        citation_targets: dict[str, str] = {}
        for node in graph:
            types = node.get("@type") or []
            if isinstance(types, str):
                types = [types]
            if "estleg:Citation" in types:
                target = node.get("estleg:citationTarget")
                if isinstance(target, dict):
                    cit_id = node.get("@id")
                    target_id = target.get("@id")
                    if cit_id and target_id:
                        citation_targets[cit_id] = target_id

        # Second pass: walk act nodes; for each issuedUnder entry add
        # an inverse from target → source. For each implementsCitation
        # entry, look up the Citation's citationTarget and add an
        # inverse from THAT to the source act.
        for node in graph:
            source_iri = node.get("@id")
            if not source_iri:
                continue

            issued = node.get("estleg:issuedUnder")
            if isinstance(issued, dict):
                issued = [issued]
            if isinstance(issued, list):
                for entry in issued:
                    if isinstance(entry, dict) and "@id" in entry:
                        inverse.setdefault(entry["@id"], []).append(source_iri)

            cites = node.get("estleg:implementsCitation")
            if isinstance(cites, dict):
                cites = [cites]
            if isinstance(cites, list):
                for entry in cites:
                    if isinstance(entry, dict) and "@id" in entry:
                        target = citation_targets.get(entry["@id"])
                        if target:
                            inverse.setdefault(target, []).append(source_iri)

    # Deduplicate per target
    for k in inverse:
        inverse[k] = sorted(set(inverse[k]))
    return inverse


def apply_implemented_by(
    inverse_map: dict[str, list[str]],
    krr_root: Path,
) -> int:
    """Walk all peep files; for each node whose @id is in inverse_map,
    write estleg:implementedBy and (if non-empty) estleg:implementedByCount.

    Returns the number of nodes updated. Idempotent — clears any
    existing estleg:implementedBy / estleg:implementedByCount before
    writing fresh values.
    """
    updates = 0
    files_updated = 0
    for peep in sorted(krr_root.rglob("*_peep.json")):
        try:
            with open(peep, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue
        changed = False
        for node in doc.get("@graph", []):
            node_id = node.get("@id")
            # Always clear (idempotency)
            had_old = "estleg:implementedBy" in node
            node.pop("estleg:implementedBy", None)
            node.pop("estleg:implementedByCount", None)
            if node_id and node_id in inverse_map:
                refs = inverse_map[node_id]
                node["estleg:implementedBy"] = [
                    {"@id": r} for r in refs
                ]
                node["estleg:implementedByCount"] = {
                    "@type": "xsd:integer", "@value": str(len(refs))
                }
                updates += 1
                changed = True
            elif had_old:
                changed = True
        if changed:
            with open(peep, "w", encoding="utf-8") as fh:
                json.dump(doc, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
            files_updated += 1
    return updates


def compute_implemented_by_count(graph: list[dict]) -> None:
    """In-place: for each node in `graph` that has estleg:implementedBy,
    set estleg:implementedByCount to its length. Removes any prior
    count when implementedBy is absent.

    This is exposed as a separate helper for testing — apply_implemented_by
    already does this inline during writes.
    """
    for node in graph:
        impls = node.get("estleg:implementedBy")
        if isinstance(impls, dict):
            impls = [impls]
        if isinstance(impls, list) and impls:
            node["estleg:implementedByCount"] = {
                "@type": "xsd:integer", "@value": str(len(impls))
            }
        else:
            node.pop("estleg:implementedByCount", None)
```

- [ ] **Step 4: Run tests**

```
pytest tests/test_generate_inverse_references.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Wire into `main()`**

Find `generate_inverse_references.py::main()`. After the existing `referencedBy` logic, add the implementedBy pass:

```python
    print("\n--- Generating implementedBy (Layer 2b) ---")
    inverse_map = collect_preamble_back_references(KRR_DIR)
    print(f"  Found preamble back-references for "
          f"{len(inverse_map)} target IRIs")
    updates = apply_implemented_by(inverse_map, KRR_DIR)
    print(f"  Wrote implementedBy to {updates} nodes")
```

Add coverage instrumentation following the canonical pattern. Counter notes:
- `_triples` = total `implementedBy` IRIs written + `referencedBy` triples (sum of both inverse passes)
- `_triples_kov` = subset attributable to KOV sources (count entries in inverse_map values that are under regulations/kov/)
- `_files_with_output[_kov]` = files that gained ≥1 inverse triple

- [ ] **Step 6: Run the full suite**

```
pytest -q
```

Expected: prior count + 4 new tests, all passing.

- [ ] **Step 7: Commit**

```bash
git add scripts/generate_inverse_references.py tests/test_generate_inverse_references.py
git commit -m "Add implementedBy filtered projection + implementedByCount aggregate

generate_inverse_references gains a Layer 2b pass that builds an
inverse map from estleg:issuedUnder + Citation.citationTarget triples
only (NOT from body-text estleg:references — those produce the
existing referencedBy). Each target gains estleg:implementedBy with
the list of source acts plus estleg:implementedByCount as an aggregate.
Idempotent: prior implementedBy/Count are cleared before regenerating."
```

---

## Task 11: Unpin `generate_inverse_references` + add coverage instrumentation

**Files:**
- Modify: `scripts/generate_inverse_references.py`

Same pattern as Task 9 — remove the 3 `include_kov=False  # DEFERRED to Layer 2b` pins, then add coverage instrumentation following the canonical pattern.

- [ ] **Step 1: Unpin**

```bash
grep -n "include_kov=False" scripts/generate_inverse_references.py
```

Expected: 3 lines.

For each, remove the explicit `include_kov=False` argument and the trailing `# DEFERRED to Layer 2b` comment.

```bash
grep -n "include_kov=False" scripts/generate_inverse_references.py
```

Expected: empty.

- [ ] **Step 2: Add coverage instrumentation**

Apply the canonical pattern (same shape as Task 8). Coverage report path: `krr_outputs/reports/kov/generate_inverse_references_coverage.json`. Counter notes:
- `_triples` = total inverse triples written across both `referencedBy` and `implementedBy`
- `_triples_kov` = subset where the source act (the value in inverse_map) is under `regulations/kov/`. Note: KOV "output" here means inverse triples generated FROM a KOV source act, written to a non-KOV target (typically a state law).
- `_files_with_output[_kov]` = peep files that gained ≥1 inverse triple

- [ ] **Step 3: Verify**

```bash
python3 -c "import ast; ast.parse(open('scripts/generate_inverse_references.py').read())" && echo OK
pytest -q
```

Expected: OK + tests still passing.

- [ ] **Step 4: Commit**

```bash
git add scripts/generate_inverse_references.py
git commit -m "Unpin generate_inverse_references + add coverage instrumentation"
```

---

## Task 12: Run on full corpus + verify gates

**Files:**
- Generated: `krr_outputs/reports/kov/extract_cross_references_coverage.json`
- Generated: `krr_outputs/reports/kov/generate_inverse_references_coverage.json`
- Generated: in-place updates to peep files (issuedUnder, implementsCitation, Citation nodes, implementedBy, implementedByCount)

This is the integration step. No new code; runs the two pipelines on the real corpus and validates the gates.

**Prerequisite:** `data/riigiteataja/` is gitignored. If running in a fresh worktree, symlink from the main checkout (same workaround documented in Layer 2a Task 11).

- [ ] **Step 1: Run both pipelines with proper exit-code propagation**

```bash
set -o pipefail

for pipeline in extract_cross_references generate_inverse_references; do
    echo "=== running ${pipeline} ==="
    python "scripts/${pipeline}.py" > "/tmp/${pipeline}.log" 2>&1
    rc=$?
    if [ $rc -ne 0 ]; then
        echo "FAIL: ${pipeline} exited ${rc}"
        tail -50 "/tmp/${pipeline}.log"
        exit 1
    fi
    tail -5 "/tmp/${pipeline}.log"
done
```

Expected: both exit 0, each writes a coverage JSON.

- [ ] **Step 2: Verify coverage gates**

For each pipeline, run the canonical gate check:

```bash
set -e
for p in extract_cross_references generate_inverse_references; do
  echo "=== $p ==="
  python3 -c "
import json, sys
d = json.load(open('krr_outputs/reports/kov/${p}_coverage.json'))
print(f\"  input total/kov:        {d['input_files_total']} / {d['input_files_kov']}\")
print(f\"  files processed (kov):  {d['files_processed']} ({d['files_processed_kov']})\")
print(f\"  files w/ output (kov):  {d['files_with_output']} ({d['files_with_output_kov']})\")
print(f\"  triples emitted (kov):  {d['triples_emitted']} ({d['triples_emitted_kov']})\")
print(f\"  unresolved refs:        {d['unresolved_references']}\")
print(f\"  errors:                 {d['error_count']}\")

fail = []
if d['input_files_kov'] < 11000:
    fail.append(f'input_files_kov={d[\"input_files_kov\"]} < 11000')
if d['files_with_output_kov'] == 0:
    fail.append('files_with_output_kov is 0')
if d['triples_emitted_kov'] == 0:
    fail.append('triples_emitted_kov is 0')
err_threshold = max(5, int(0.01 * d['files_processed']))
if d['error_count'] > err_threshold:
    fail.append(f'error_count={d[\"error_count\"]} > {err_threshold}')
if fail:
    print('  GATE FAIL:'); [print(f'    {f}') for f in fail]; sys.exit(1)
print('  GATE OK')
"
done
```

Each must report GATE OK.

- [ ] **Step 3: Run validate_all**

```bash
set -o pipefail
python scripts/validate_all.py > /tmp/validate_all_layer2b.log 2>&1 || \
    { tail -50 /tmp/validate_all_layer2b.log; exit 1; }
tail -5 /tmp/validate_all_layer2b.log
```

Expected: 0 errors. (Pre-existing SHACL violations on `requestedCluster` / missing `summary` are NOT validate_all errors.)

- [ ] **Step 4: Targeted SHACL check**

Run the same 3-bucket check from Layer 2a Task 11 Step 3b, with the exception that `LAYER2A_PATHS` is no longer "must be 0" — we now expect `citationTarget` and related properties to fire IF the data is malformed. The check needs a small adjustment:

```bash
python3 - <<'PY'
import sys, glob, json
sys.path.insert(0, "scripts")
import rdflib, pyshacl

g = rdflib.Graph()
g.parse("krr_outputs/municipalities_peep.json", format="json-ld")
g.parse("krr_outputs/issuers_kov_peep.json", format="json-ld")
import os
tallinn_dir = "krr_outputs/regulations/kov/tallinna_linnavolikogu"
sample_kov = sorted(os.listdir(tallinn_dir))[0]
g.parse(f"{tallinn_dir}/{sample_kov}", format="json-ld")
g.parse("krr_outputs/alkoholiseadus_peep.json", format="json-ld")
sample_riik = sorted(glob.glob("krr_outputs/regulations/riik/*_peep.json"))[0]
g.parse(sample_riik, format="json-ld")

shapes = rdflib.Graph().parse("shacl/estonian_legal_shapes.ttl", format="turtle")
ok, _, msg = pyshacl.validate(g, shacl_graph=shapes, inference="rdfs")

PREEXISTING_PATHS = {"requestedCluster", "summary"}
LAYER2_PATHS = {"citationTarget", "citationDetail", "citationText",
                "issuedUnder", "implementsCitation",
                "implementedBy", "implementedByCount", "enforcedAtLevel"}
LAYER1_PATHS = {"enactedBy", "enactedByMunicipality", "partOfAct",
                "titleNormalized", "ehakCode", "county", "bodyType",
                "currentMunicipality", "mappingSource"}

def categorize(line):
    if any(p in line for p in PREEXISTING_PATHS):
        return "preexisting"
    if any(p in line for p in LAYER2_PATHS):
        return "layer2"
    if any(p in line for p in LAYER1_PATHS):
        return "layer1"
    return "other"

lines = (msg or "").splitlines()
buckets = {"preexisting": [], "layer2": [], "layer1": [], "other": []}
for line in lines:
    if "Result Path" in line or "ResultPath" in line:
        buckets[categorize(line)].append(line)

print(f"SHACL_TARGETED {'PASS' if ok else 'FAIL'}")
for cat, items in buckets.items():
    print(f"  {cat}: {len(items)}")
    for v in items[:3]:
        print(f"    {v.strip()}")

# Layer 2 violations now mean Layer 2b emitted invalid Citation /
# issuedUnder / implementedBy data — must be 0 (instances exist now).
hard_fail = (
    bool(buckets["layer2"])
    or bool(buckets["layer1"])
    or bool(buckets["other"])
)
sys.exit(1 if hard_fail else 0)
PY
```

Expected exit 0 — pre-existing-only violations remain.

- [ ] **Step 5: Stage and commit**

Same chunked-staging pattern as Layer 2a Task 11 Step 4. Stage:
- `krr_outputs/reports/kov/` (the 2 new reports)
- `krr_outputs/regulations/kov` (in-place issuedUnder / implementsCitation / Citation nodes / implementedBy / implementedByCount)
- `krr_outputs/regulations/riik` (state regs may gain implementedBy back-references)
- Indexed law files via INDEX.json (laws will gain implementedBy back-references when KOV/state acts cite them)

```bash
# Coverage reports
git add krr_outputs/reports/kov/

# In-place updates
git add krr_outputs/regulations/kov
git add krr_outputs/regulations/riik

# Indexed law files (chunked to avoid command-line limits)
python3 - <<'PY'
import json, subprocess, os
idx = json.load(open("krr_outputs/INDEX.json"))
paths = [
    f"krr_outputs/{f}"
    for entry in idx["laws"] for f in entry.get("files", [])
    if os.path.exists(f"krr_outputs/{f}")
]
chunk = 500
for i in range(0, len(paths), chunk):
    subprocess.check_call(["git", "add", "--"] + paths[i:i + chunk])
print(f"staged {len(paths)} indexed law files")
PY

# Any new aggregate outputs (cross_references_report etc.)
for path in \
    krr_outputs/cross_references_report.json \
    krr_outputs/inverse_references_report.json
do
    git add "$path" 2>/dev/null || true
done

git status --short | head -30
git commit -m "Run Layer 2b pipelines on full corpus

Generates initial KOV coverage reports for the 2 unpinned pipelines
(extract_cross_references, generate_inverse_references) plus in-place
updates: issuedUnder + implementsCitation triples on KOV act nodes,
reified Citation nodes added to KOV peep files, implementedBy +
implementedByCount projections written to enabling acts and provisions.
Idempotent: re-running clears prior preamble-derived triples before
regenerating."
```

---

## Task 13: Documentation updates

**Files:**
- Modify: `docs/SCHEMA_REFERENCE.md`
- Modify: `docs/REGULATIONS_INTEGRATION_PLAN.md`
- Modify: `CHANGELOG.md`
- Modify: `README.md`

- [ ] **Step 1: Schema reference**

In `docs/SCHEMA_REFERENCE.md`, find the existing "KOV Layer 2a — Schema Foundations" section. Append after it:

```markdown
## KOV Layer 2b — Cross-references + Inverse

Layer 2b populates the schema declared in 2a:

### `estleg:issuedUnder`
Emitted on each act node from preamble citations that resolve to a known law, state regulation, or KOV regulation. The range is `estleg:Act`.

### `estleg:implementsCitation` + `estleg:Citation`
Emitted when a preamble citation carries `lg`/`p` sub-paragraph detail. Each Citation node is reified (carries `citationTarget`, optional `citationDetail`, optional `citationText`) so target+detail stay attached safely under JSON-LD round-trips.

### `estleg:implementedBy` + `estleg:implementedByCount`
Inverse projection of `issuedUnder` and `Citation.citationTarget`. Written to the enabling act (or its provisions for paragraph-level Citation targets). The `implementedByCount` aggregate is on every node that has `implementedBy`.

### KOV body-text scoping
Body-text references like `linnavolikogu määrus nr 5` are scoped to the source act's `enactedByMunicipality` first, then narrowed by issuer body type, then act number. Cross-municipality matches are skipped to avoid Tallinn→Tartu false positives.

### Pipeline coverage reports
- `krr_outputs/reports/kov/extract_cross_references_coverage.json`
- `krr_outputs/reports/kov/generate_inverse_references_coverage.json`
```

- [ ] **Step 2: Status note**

In `docs/REGULATIONS_INTEGRATION_PLAN.md`, add another blockquote after the existing Layer 2a status:

```markdown
> **Status (2026-05-03):** KOV integration Layer 2b (cross-references +
> inverse) implemented. Preamble citations now emit `issuedUnder` triples
> and reified Citation nodes; the inverse generator produces
> `implementedBy` + `implementedByCount` projections. Both pipelines run
> KOV by default. Layer 2c (sanctions / competence / court-links) is the
> last umbrella sub-PR before Gate B is reached.
```

- [ ] **Step 3: CHANGELOG**

Append to the `[Unreleased]` section:

```markdown
### Added (Layer 2b)
- Preamble citation extraction in `extract_cross_references`: every
  resolved preamble citation emits `estleg:issuedUnder` plus (when
  the citation carries `lg`/`p` detail) one `estleg:implementsCitation`
  reified `estleg:Citation` node per citation.
- KOV body-text scope rules: `linnavolikogu määrus nr X` references are
  scoped to the same `enactedByMunicipality` first.
- Inverse generator gains `estleg:implementedBy` (filtered projection
  from preamble enabling-act links only) and `estleg:implementedByCount`
  (aggregate count) on enabling acts and provisions.
- `extract_cross_references` and `generate_inverse_references` are
  unpinned — they run with the new `include_kov=True` default.
- Per-pipeline coverage reports under `krr_outputs/reports/kov/` for
  the 2 unpinned pipelines.
```

- [ ] **Step 4: README**

Add a sub-section after the existing Layer 2a section:

```markdown
#### Layer 2b — Cross-references + Inverse

Two pipelines now process KOV preambles + body-text citations:

- `extract_cross_references` — emits `issuedUnder` and reified Citation
  nodes for every resolved KOV act preamble; KOV body-text references
  use municipality-first scoping.
- `generate_inverse_references` — adds `implementedBy` + `implementedByCount`
  on enabling acts and provisions.

Coverage reports: `krr_outputs/reports/kov/extract_cross_references_coverage.json`,
`krr_outputs/reports/kov/generate_inverse_references_coverage.json`.

The remaining 3 KOV-relevant pipelines (sanctions, competence,
court-provision-links) are deferred to Layer 2c.
```

- [ ] **Step 5: Commit**

```bash
git add docs/SCHEMA_REFERENCE.md docs/REGULATIONS_INTEGRATION_PLAN.md CHANGELOG.md README.md
git commit -m "Document KOV integration Layer 2b"
```

---

## Task 14: Final verification + push + PR

**Files:**
- (no source changes; this task validates and ships)

- [ ] **Step 1: Run the full pytest suite**

```
pytest -v 2>&1 | tail -40
```

Record actual pass count (Layer 2a baseline 135 + Layer 2b new tests).

- [ ] **Step 2: Run validate_all (with proper exit-code propagation)**

```bash
set -o pipefail
python scripts/validate_all.py > /tmp/validate_all_final.log 2>&1 || \
    { tail -50 /tmp/validate_all_final.log; exit 1; }
tail -5 /tmp/validate_all_final.log
```

Expected: 0 errors.

- [ ] **Step 3: Verify all 7 coverage reports exist**

```bash
ls -la krr_outputs/reports/kov/*.json
```

Expected: 7 reports (5 from Layer 2a + 2 from Layer 2b). Re-run the coverage gate loop on all 7 — each must report GATE OK.

- [ ] **Step 4: Verify the 7 still-pinned pipelines still skip KOV**

```bash
python3 -c "
import sys; sys.path.insert(0, 'scripts')
from estleg_common import iter_peep_files
files_no_kov = iter_peep_files(include_kov=False)
files_default = iter_peep_files()
print(f'  default: {len(files_default)} files')
print(f'  include_kov=False: {len(files_no_kov)} files')
print(f'  KOV-only delta: {len(files_default) - len(files_no_kov)} files')
"
```

Expected: KOV-only delta around 11,059.

```bash
python3 - <<'PY'
import re
from pathlib import Path
KOV_READY = {
    "classify_deontic.py", "classify_eurovoc.py", "extract_temporal_data.py",
    "extract_legal_concepts.py", "generate_amendment_history.py",
    "extract_cross_references.py", "generate_inverse_references.py",  # Layer 2b additions
}
defaulted = []
for path in sorted(Path("scripts").glob("*.py")):
    if path.name == "estleg_common.py":
        continue
    text = path.read_text()
    for m in re.finditer(r"iter_peep_files\(([^)]*)\)", text):
        args = m.group(1)
        if "include_kov" not in args:
            defaulted.append((path.name, args.strip() or "<no args>"))
unexpected = [(n, a) for n, a in defaulted if n not in KOV_READY]
print(f"unexpected default calls: {len(unexpected)}")
for n, a in unexpected:
    print(f"  {n}: iter_peep_files({a})")
PY
```

Expected: 0 unexpected calls.

- [ ] **Step 5: DO NOT PUSH OR OPEN PR**

Stop here. Generate the PR-body draft to `/tmp/pr_body_layer2b.md` and report back to the user. The user will explicitly authorize the push.

PR body should include:
- Summary of what Layer 2b ships
- Explicit list of what's NOT in this PR (deferred to 2c, plus Layer 3)
- Test plan (KOV-specific gates per pipeline)
- Reviewer guide for the coverage reports
- Known issues (carryover from earlier layers — pre-existing SHACL hygiene)
- Any noteworthy regressions surfaced and fixed in the branch (if any)

```bash
git status --short
git log --oneline origin/main..HEAD | head -20
```

Confirm the branch is NOT pushed.

## Report

- Status (DONE | DONE_WITH_CONCERNS | BLOCKED | NEEDS_CONTEXT)
- pytest pass count
- validate_all result
- Per-pipeline gate summary (7 lines, one per pipeline)
- Pinned-pipelines verification
- Path to /tmp/pr_body_layer2b.md
- `git log --oneline origin/main..HEAD` output
- Confirmation that branch was NOT pushed and no PR was opened
- Any concerns

---

## Self-Review Checklist

After all 14 tasks complete, verify:

- [ ] **Spec coverage.** Every Layer 2b requirement in the spec maps to a task:
  - Preamble scanning: Tasks 2, 4, 7
  - Citation reified node + IRI scheme: Tasks 3, 7
  - Body-text KOV scope rules: Task 6
  - implementedBy filtered projection: Task 10
  - implementedByCount aggregate: Task 10
  - Unpinning: Tasks 9, 11
  - Coverage instrumentation: Tasks 8, 11
  - Layer 2a M-fixes: Task 1

- [ ] **No actionable gaps.** No "TODO", "TBD", "implement later", "Add appropriate <thing>" in the plan.

- [ ] **Type consistency.** `extract_preamble_citations` returns `list[dict]` with consistent keys across Tasks 2, 4, 7. `Citation` IRI shape matches the SHACL pattern in Layer 2a. `_lookup_paths` set pattern reused from Layer 2a Task 8 fix.

- [ ] **KOV attribution discipline.** Coverage counters (`_files_with_output_kov`, `_triples_kov`) use `is_kov` alone (matches the Layer 2a M2 alignment) — not "is_kov AND bridged" or any conditional.

- [ ] **Idempotency.** Both extract_cross_references' preamble pass and generate_inverse_references' implementedBy pass clear prior triples before regenerating — verified by the test suite.

- [ ] **All 14 tasks have clear commit messages with the documented prefix.**

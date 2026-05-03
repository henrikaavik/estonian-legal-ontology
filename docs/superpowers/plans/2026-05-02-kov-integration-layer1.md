# KOV Integration — Layer 1 (Municipality + Issuer Entity Model) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Municipality + Issuer entity layer over the existing 11,059 KOV regulation files, plus the supporting class hierarchy (`Act`, `Law`), so every KOV act and provision is queryable by Municipality and Issuer. This is the standalone first PR of the three-layer KOV integration described in `docs/superpowers/specs/2026-05-02-kov-integration-design.md`.

**Architecture:** A new EHAK-coded `Municipality` registry plus a 357-row `Issuer` registry (volikogu and valitsus bodies). Body-type-aware auto-match handles the unambiguous mappings; a curated CSV with mandatory evidence covers the rest. A single orchestrator script does in-place enrichment of all KOV act + provision JSON-LD nodes, plus an additive type-stamp on existing law nodes. SHACL shapes catch any future drift.

**Tech Stack:** Python 3.11+, pytest, JSON-LD, SHACL (pyshacl), rdflib. Existing `scripts/estleg_common.py` for shared helpers.

**Spec reference:** `docs/superpowers/specs/2026-05-02-kov-integration-design.md`

**Out of scope for this layer:** all pipeline integrators (Layer 2), similarity (Layer 3), historical Municipality entities for pre-2017 defunct units.

---

## File Structure

### New files

| Path | Responsibility |
| --- | --- |
| `data/ehak/municipalities.json` | Static EHAK municipality registry (79 entries, hand-curated from Statistics Estonia). |
| `data/ehak/issuer_successor_map.csv` | Curated rows for issuers that auto-match cannot resolve. Each row: issuer slug, current EHAK code, mapping source, mapping evidence. |
| `data/ehak/issuers.json` | Generated, committed. Combined output of auto-match + curated CSV. The single source of truth for issuer→municipality mapping at runtime. |
| `scripts/kov_registry.py` | Pure-function module: slug parser, body-type-aware auto-match, title normalization, curated CSV loader. No I/O at module scope. |
| `scripts/build_kov_registry.py` | CLI that generates `data/ehak/issuers.json` from auto-match + curated CSV, failing loudly on unmapped issuers. |
| `scripts/enrich_kov_layer1.py` | CLI orchestrator that (a) generates `krr_outputs/municipalities_peep.json` and `krr_outputs/issuers_kov_peep.json`, (b) enriches all KOV act files in place with act-level + provision-level Layer 1 properties, (c) stamps `estleg:Law` rdf:type onto existing law act nodes. |
| `tests/test_kov_registry.py` | Tests for `kov_registry.py` pure functions. |
| `tests/test_build_kov_registry.py` | Tests for `build_kov_registry.py` CLI behavior including the unmapped-issuer failure path. |
| `tests/test_enrich_kov_layer1.py` | End-to-end enrichment tests on a fixture KOV act + provision tree. |
| `tests/fixtures/kov_layer1/` | Fixture set: minimal `municipalities.json`, `issuer_successor_map.csv`, two KOV act peep files (one pre-reform, one current), one law peep file, expected outputs. |
| `krr_outputs/municipalities_peep.json` | Generated. 79 Municipality JSON-LD nodes. |
| `krr_outputs/issuers_kov_peep.json` | Generated. 357 Issuer JSON-LD nodes. |

### Modified files

| Path | Change |
| --- | --- |
| `metadata.jsonld` | Add new classes: `Act`, `Law`, `Municipality`, `Issuer`, `KovProvision`, `Citation`, `Similarity`. Declare existing `*Regulation` classes as `rdfs:subClassOf` `Act`. Add new properties (see schema additions). |
| `shacl/estonian_legal_shapes.ttl` | Append new node shapes: `MunicipalRegulationShape` (Layer-1 fields), `KovProvisionShape`, `IssuerShape`, `MunicipalityShape`, `LawShape`. |
| `scripts/validate_all.py` | Add the new generated output files (`municipalities_peep.json`, `issuers_kov_peep.json`) to the file scan list so its existing duplicate-`@id` and `@type`-as-array checks cover them. |
| All 11,059 KOV act peep files under `krr_outputs/regulations/kov/<issuer>/*_peep.json` | Each act node gains `estleg:Act` and `enactedBy`, `enactedByMunicipality`, `titleNormalized`. Each provision node gains `KovProvision` rdf:type, `enactedBy`, `enactedByMunicipality`, `partOfAct`. |
| 615 law peep files (paths from `krr_outputs/INDEX.json` `laws[].files`) | Each act node gains both `estleg:Law` and `estleg:Act` rdf:type entries. |
| All 3,813 state regulation peep files under `krr_outputs/regulations/riik/*_peep.json` | Each act node gains `estleg:Act` rdf:type. The specific subtype (`NationalRegulation` / `GovernmentRegulation` / `MinisterialRegulation`) is already present from the Phase 1 generator. |
| `docs/SCHEMA_REFERENCE.md` | New section "KOV entity model (Layer 1)" enumerating all new classes, properties, IRI patterns, and SHACL shapes. |
| `README.md` | KOV section updated to describe the entity layer; example query snippets. |
| `CHANGELOG.md` | Entry: "Add KOV integration Layer 1 (Municipality + Issuer entity model)". |

---

### Schema additions reference (used across multiple tasks)

The full set of schema additions for Layer 1, gathered here so individual tasks can reference them:

**New classes:**
- `estleg:Act` — top-level "any enacted Estonian legal act"
- `estleg:Law` — `rdfs:subClassOf estleg:Act`
- `estleg:Municipality` — territorial KOV unit (top-level)
- `estleg:Issuer` — `rdfs:subClassOf estleg:Institution`
- `estleg:KovProvision` — `rdfs:subClassOf estleg:LegalProvision`
- `estleg:Citation` — placeholder class for Layer 2 (declared in Layer 1 so the SHACL shape file can be unified, but Citation instances are produced in Layer 2)
- `estleg:Similarity` — placeholder class for Layer 3 (same reason)

**Subclass triples added on existing classes:**
- `estleg:NationalRegulation rdfs:subClassOf estleg:Act .`
- `estleg:GovernmentRegulation rdfs:subClassOf estleg:NationalRegulation .`
- `estleg:MinisterialRegulation rdfs:subClassOf estleg:NationalRegulation .`
- `estleg:MunicipalRegulation rdfs:subClassOf estleg:Act .`

**New properties (introduced in Layer 1):**
- `estleg:enactedBy` — domain: `MunicipalRegulation` ∪ `KovProvision`; range: `Issuer`
- `estleg:enactedByMunicipality` — domain: `MunicipalRegulation` ∪ `KovProvision`; range: `Municipality`
- `estleg:titleNormalized` — domain: `MunicipalRegulation`; range: `xsd:string`
- `estleg:partOfAct` — domain: `KovProvision`; range: `Act`
- `estleg:ehakCode` — domain: `Municipality`; range: `xsd:string`
- `estleg:county` — domain: `Municipality`; range: `xsd:string`
- `estleg:bodyType` — domain: `Issuer`; range: `xsd:string` ("volikogu" | "valitsus")
- `estleg:currentMunicipality` — domain: `Issuer`; range: `Municipality`
- `estleg:historicalMunicipalityName` — domain: `Issuer`; range: `xsd:string`
- `estleg:mappingSource` — domain: `Issuer`; range: `xsd:string`
- `estleg:mappingEvidence` — domain: `Issuer`; range: `xsd:string`

**IRI patterns:**
- `estleg:Municipality_EHAK_<4-digit>` — Municipality
- `estleg:Issuer_<slug>` — Issuer (where `<slug>` is the existing KOV directory name)

**Direct-typing decision (important for SHACL).** The plan stamps
`estleg:Act` rdf:type **directly** on every act node (KOV
MunicipalRegulation, Law, NationalRegulation/Government/Ministerial)
during Layer 1 enrichment, in addition to the act's specific subtype.
This avoids requiring SHACL validators to do RDFS subclass inference
to satisfy `sh:class estleg:Act` constraints (e.g. on `partOfAct`'s
range). Same pattern as `KovProvision` getting stamped directly on
provision nodes. The schema declarations in `metadata.jsonld` still
record the subclass relationships for documentation and reasoner
use, but the data is self-describing without inference. Without this
step, `partOfAct` SHACL constraints would fail under common pyshacl
configurations because act nodes are typed only as
`MunicipalRegulation` (not directly as `Act`).

---

## Task 1: Set up EHAK municipality data file

**Files:**
- Create: `data/ehak/municipalities.json`
- Create: `tests/fixtures/kov_layer1/municipalities_min.json`
- Create: `tests/test_kov_registry.py`

The full 79-entry `municipalities.json` is hand-curated from Statistics Estonia's EHAK list at <https://www.stat.ee/sites/default/files/2020-03/ehak.csv>. This task creates the file structure plus a five-row fixture for tests.

- [ ] **Step 1: Write the failing test** (test the file structure, not its contents)

Create `tests/test_kov_registry.py`:

```python
"""Tests for scripts/kov_registry.py — issuer/municipality registry helpers."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pytest

from kov_registry import load_municipalities


REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "kov_layer1"
MIN_MUNICIPALITIES = FIXTURE_DIR / "municipalities_min.json"


class TestLoadMunicipalities:
    def test_returns_dict_keyed_by_ehak(self):
        muns = load_municipalities(MIN_MUNICIPALITIES)
        assert isinstance(muns, dict)
        assert "0784" in muns  # Tallinn

    def test_each_entry_has_required_fields(self):
        muns = load_municipalities(MIN_MUNICIPALITIES)
        for code, entry in muns.items():
            assert entry["ehakCode"] == code
            assert entry["name"]
            assert entry["county"]
            assert entry["type"] in {"linn", "vald"}

    def test_real_file_has_79_entries(self):
        path = REPO_ROOT / "data" / "ehak" / "municipalities.json"
        if not path.exists():
            pytest.skip("real registry not yet generated")
        muns = load_municipalities(path)
        assert len(muns) == 79
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_kov_registry.py::TestLoadMunicipalities -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'kov_registry'`

- [ ] **Step 3: Create the fixture file**

Create `tests/fixtures/kov_layer1/municipalities_min.json`:

```json
[
  {"ehakCode": "0784", "name": "Tallinn", "county": "Harju maakond", "type": "linn"},
  {"ehakCode": "0793", "name": "Tartu linn", "county": "Tartu maakond", "type": "linn"},
  {"ehakCode": "0796", "name": "Tartu vald", "county": "Tartu maakond", "type": "vald"},
  {"ehakCode": "0855", "name": "Mulgi vald", "county": "Viljandi maakond", "type": "vald"},
  {"ehakCode": "0625", "name": "Saaremaa vald", "county": "Saare maakond", "type": "vald"}
]
```

- [ ] **Step 4: Implement `load_municipalities` in `scripts/kov_registry.py`**

Create `scripts/kov_registry.py`:

```python
"""Helpers for the KOV (municipal) issuer/municipality registry.

Pure functions — all I/O takes explicit Path arguments. Tested in
tests/test_kov_registry.py.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import TypedDict


class Municipality(TypedDict):
    ehakCode: str
    name: str
    county: str
    type: str  # "linn" | "vald"


def load_municipalities(path: Path) -> dict[str, Municipality]:
    """Load the municipalities registry, keyed by EHAK code."""
    with open(path, "r", encoding="utf-8") as fh:
        rows = json.load(fh)
    out: dict[str, Municipality] = {}
    for row in rows:
        code = row["ehakCode"]
        if code in out:
            raise ValueError(f"Duplicate EHAK code: {code}")
        if row["type"] not in {"linn", "vald"}:
            raise ValueError(f"Invalid type for {code}: {row['type']!r}")
        out[code] = row
    return out
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_kov_registry.py::TestLoadMunicipalities::test_returns_dict_keyed_by_ehak tests/test_kov_registry.py::TestLoadMunicipalities::test_each_entry_has_required_fields -v`
Expected: 2 passed. The third test stays SKIPPED until the real registry exists.

- [ ] **Step 6: Hand-curate the real `data/ehak/municipalities.json`**

Open <https://www.stat.ee/sites/default/files/2020-03/ehak.csv> (or the latest equivalent) and select the 79 currently active municipalities. Build `data/ehak/municipalities.json` using the exact same row schema as the fixture. Each `name` must include the ` linn` / ` vald` suffix where applicable so the auto-match disambiguator can use it.

A few must-have rows for the auto-match disambiguation tests (Task 3) to pass:
- Tartu linn `0793`, Tartu vald `0796`
- Rakvere linn `0663`, Rakvere vald `0661`
- Viljandi linn `0897`, Viljandi vald `0899`
- Võru linn `0919`, Võru vald `0922`
- Tallinn `0784`, Mulgi vald `0855`, Saaremaa vald `0625`

- [ ] **Step 7: Run the full test class**

Run: `pytest tests/test_kov_registry.py::TestLoadMunicipalities -v`
Expected: 3 passed. The `test_real_file_has_79_entries` no longer skips.

- [ ] **Step 8: Commit**

```bash
git add data/ehak/municipalities.json tests/fixtures/kov_layer1/municipalities_min.json tests/test_kov_registry.py scripts/kov_registry.py
git commit -m "Add EHAK municipality registry and loader"
```

---

## Task 2: Issuer slug parser

**Files:**
- Modify: `scripts/kov_registry.py` (add `parse_issuer_slug`)
- Modify: `tests/test_kov_registry.py` (add test class)

The 357 KOV directory names under `krr_outputs/regulations/kov/` have the shape `<root>_<body>` where `<body>` is one of `linnavolikogu`, `linnavalitsus`, `vallavolikogu`, `vallavalitsus`. The parser splits a slug into `(root, body, body_type, municipality_type)`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_kov_registry.py`:

```python
from kov_registry import parse_issuer_slug


class TestParseIssuerSlug:
    def test_linnavolikogu(self):
        parts = parse_issuer_slug("tallinna_linnavolikogu")
        assert parts == {
            "slug": "tallinna_linnavolikogu",
            "root": "tallinna",
            "body": "linnavolikogu",
            "bodyType": "volikogu",
            "municipalityType": "linn",
        }

    def test_vallavalitsus(self):
        parts = parse_issuer_slug("mulgi_vallavalitsus")
        assert parts["bodyType"] == "valitsus"
        assert parts["municipalityType"] == "vald"

    def test_compound_root(self):
        parts = parse_issuer_slug("kohtla_jarve_linnavolikogu")
        assert parts["root"] == "kohtla_jarve"
        assert parts["body"] == "linnavolikogu"

    def test_unknown_body_raises(self):
        with pytest.raises(ValueError, match="unknown body suffix"):
            parse_issuer_slug("foo_riigikogu")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_kov_registry.py::TestParseIssuerSlug -v`
Expected: FAIL with `ImportError: cannot import name 'parse_issuer_slug'`

- [ ] **Step 3: Implement `parse_issuer_slug`**

Add to `scripts/kov_registry.py`:

```python
_BODY_SUFFIXES = {
    "linnavolikogu": ("volikogu", "linn"),
    "linnavalitsus": ("valitsus", "linn"),
    "vallavolikogu": ("volikogu", "vald"),
    "vallavalitsus": ("valitsus", "vald"),
}


class IssuerSlugParts(TypedDict):
    slug: str
    root: str
    body: str
    bodyType: str          # "volikogu" | "valitsus"
    municipalityType: str  # "linn" | "vald"


def parse_issuer_slug(slug: str) -> IssuerSlugParts:
    """Split an issuer directory slug into its components.

    Slugs look like ``tallinna_linnavolikogu`` or
    ``kohtla_jarve_linnavolikogu`` (compound root). The body suffix is
    one of ``linnavolikogu``, ``linnavalitsus``, ``vallavolikogu``,
    ``vallavalitsus``.
    """
    for body, (body_type, mun_type) in _BODY_SUFFIXES.items():
        suffix = f"_{body}"
        if slug.endswith(suffix):
            root = slug[: -len(suffix)]
            return {
                "slug": slug,
                "root": root,
                "body": body,
                "bodyType": body_type,
                "municipalityType": mun_type,
            }
    raise ValueError(f"unknown body suffix in slug: {slug!r}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_kov_registry.py::TestParseIssuerSlug -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/kov_registry.py tests/test_kov_registry.py
git commit -m "Add issuer slug parser to kov_registry"
```

---

## Task 3: Body-type-aware auto-match

**Files:**
- Modify: `scripts/kov_registry.py` (add `auto_match_municipality`)
- Modify: `tests/test_kov_registry.py` (add test class)

The auto-matcher takes parsed slug parts plus the municipality registry and returns the EHAK code if the (root, municipality_type) pair uniquely identifies a current municipality. It MUST fail (return `None`) if multiple municipalities match, so the curated CSV picks them up.

The transliteration table from `estleg_common._ESTONIAN_TRANSLITERATION` maps `ä→a`, `ö→o`, etc. — needed because slugs are ASCII but municipality names contain diacritics.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_kov_registry.py`:

```python
from kov_registry import auto_match_municipality


class TestAutoMatchMunicipality:
    @pytest.fixture
    def muns(self):
        return load_municipalities(MIN_MUNICIPALITIES)

    def test_unique_linn_match(self, muns):
        parts = {"slug": "tallinna_linnavolikogu", "root": "tallinna",
                 "body": "linnavolikogu", "bodyType": "volikogu",
                 "municipalityType": "linn"}
        assert auto_match_municipality(parts, muns) == "0784"

    def test_unique_vald_match(self, muns):
        parts = {"slug": "mulgi_vallavolikogu", "root": "mulgi",
                 "body": "vallavolikogu", "bodyType": "volikogu",
                 "municipalityType": "vald"}
        assert auto_match_municipality(parts, muns) == "0855"

    def test_disambiguates_linn_from_vald(self, muns):
        # Tartu exists as both linn (0793) and vald (0796)
        # tartu_linnavolikogu must match only the linn
        linn_parts = {"slug": "tartu_linnavolikogu", "root": "tartu",
                      "body": "linnavolikogu", "bodyType": "volikogu",
                      "municipalityType": "linn"}
        vald_parts = {"slug": "tartu_vallavolikogu", "root": "tartu",
                      "body": "vallavolikogu", "bodyType": "volikogu",
                      "municipalityType": "vald"}
        assert auto_match_municipality(linn_parts, muns) == "0793"
        assert auto_match_municipality(vald_parts, muns) == "0796"

    def test_no_match_returns_none(self, muns):
        # 'abja' is a defunct pre-2017 vald, not in the current 79
        parts = {"slug": "abja_vallavolikogu", "root": "abja",
                 "body": "vallavolikogu", "bodyType": "volikogu",
                 "municipalityType": "vald"}
        assert auto_match_municipality(parts, muns) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_kov_registry.py::TestAutoMatchMunicipality -v`
Expected: FAIL with `ImportError: cannot import name 'auto_match_municipality'`

- [ ] **Step 3: Implement `auto_match_municipality`**

Add to `scripts/kov_registry.py`:

```python
_TRANSLIT = str.maketrans({
    "ö": "o", "ä": "a", "ü": "u", "õ": "o",
    "Ö": "O", "Ä": "A", "Ü": "U", "Õ": "O",
    "š": "s", "ž": "z", "Š": "S", "Ž": "Z",
})


def _normalize_name_for_matching(name: str) -> str:
    """Lowercase, transliterate, strip type suffix for slug-matching."""
    base = name.translate(_TRANSLIT).lower().strip()
    for suffix in (" linn", " vald"):
        if base.endswith(suffix):
            base = base[: -len(suffix)].rstrip()
            break
    # Compound names like "Kohtla-Järve" use a hyphen; slugs use underscore.
    return base.replace("-", "_").replace(" ", "_")


def auto_match_municipality(
    parts: IssuerSlugParts,
    municipalities: dict[str, Municipality],
) -> str | None:
    """Return EHAK code if (root, municipalityType) uniquely identifies a
    municipality. Returns None if there is no match or more than one.

    Returning None is the "fail loudly to the curated CSV" path — auto-match
    never guesses.
    """
    target_root = parts["root"]
    target_type = parts["municipalityType"]
    matches: list[str] = []
    for code, mun in municipalities.items():
        if mun["type"] != target_type:
            continue
        if _normalize_name_for_matching(mun["name"]) == target_root:
            matches.append(code)
    if len(matches) == 1:
        return matches[0]
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_kov_registry.py::TestAutoMatchMunicipality -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/kov_registry.py tests/test_kov_registry.py
git commit -m "Add body-type-aware auto-match for issuer to municipality"
```

---

## Task 4: Curated successor CSV loader

**Files:**
- Modify: `scripts/kov_registry.py` (add `load_curated_map`)
- Create: `tests/fixtures/kov_layer1/issuer_successor_map_min.csv`
- Modify: `tests/test_kov_registry.py` (add test class)

The CSV columns: `issuer_slug, current_municipality_ehak, mapping_source, mapping_evidence`. The loader requires every row to have a non-empty `mapping_evidence` — that's the auditable trail the spec mandates.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_kov_registry.py`:

```python
from kov_registry import load_curated_map


class TestLoadCuratedMap:
    @pytest.fixture
    def csv_path(self, tmp_path):
        p = tmp_path / "map.csv"
        p.write_text(
            "issuer_slug,current_municipality_ehak,mapping_source,mapping_evidence\n"
            "abja_vallavolikogu,0855,haldusreform-2017,RT I 21.06.2017 1\n"
            "aegviidu_vallavolikogu,0150,haldusreform-2017,RT I 21.06.2017 1\n",
            encoding="utf-8",
        )
        return p

    def test_loads_rows_keyed_by_slug(self, csv_path):
        rows = load_curated_map(csv_path)
        assert set(rows) == {"abja_vallavolikogu", "aegviidu_vallavolikogu"}
        abja = rows["abja_vallavolikogu"]
        assert abja["currentMunicipalityCode"] == "0855"
        assert abja["mappingSource"] == "haldusreform-2017"
        assert abja["mappingEvidence"] == "RT I 21.06.2017 1"

    def test_empty_evidence_rejected(self, tmp_path):
        p = tmp_path / "bad.csv"
        p.write_text(
            "issuer_slug,current_municipality_ehak,mapping_source,mapping_evidence\n"
            "x_vallavolikogu,0855,haldusreform-2017,\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="mapping_evidence"):
            load_curated_map(p)

    def test_duplicate_slug_rejected(self, tmp_path):
        p = tmp_path / "dup.csv"
        p.write_text(
            "issuer_slug,current_municipality_ehak,mapping_source,mapping_evidence\n"
            "x_vallavolikogu,0855,haldusreform-2017,evidence-a\n"
            "x_vallavolikogu,0784,haldusreform-2017,evidence-b\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="duplicate"):
            load_curated_map(p)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_kov_registry.py::TestLoadCuratedMap -v`
Expected: FAIL with `ImportError: cannot import name 'load_curated_map'`

- [ ] **Step 3: Implement `load_curated_map`**

Add to `scripts/kov_registry.py`:

```python
import csv


class CuratedRow(TypedDict):
    currentMunicipalityCode: str
    mappingSource: str
    mappingEvidence: str


def load_curated_map(path: Path) -> dict[str, CuratedRow]:
    """Load the curated issuer→municipality map.

    Required columns: issuer_slug, current_municipality_ehak,
    mapping_source, mapping_evidence. Every row MUST have non-empty
    mapping_evidence — this is the auditable trail.
    """
    out: dict[str, CuratedRow] = {}
    with open(path, "r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            slug = row["issuer_slug"].strip()
            evidence = (row.get("mapping_evidence") or "").strip()
            if not evidence:
                raise ValueError(
                    f"Row for {slug!r} has empty mapping_evidence; "
                    "every curated row must cite its source."
                )
            if slug in out:
                raise ValueError(f"duplicate issuer_slug: {slug}")
            out[slug] = {
                "currentMunicipalityCode": row["current_municipality_ehak"].strip(),
                "mappingSource": row["mapping_source"].strip(),
                "mappingEvidence": evidence,
            }
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_kov_registry.py::TestLoadCuratedMap -v`
Expected: 3 passed.

- [ ] **Step 5: Create the seed curated file**

Create `data/ehak/issuer_successor_map.csv`:

```csv
issuer_slug,current_municipality_ehak,mapping_source,mapping_evidence
```

(Header row only. Real rows are added in Task 5 once we know which issuers fail auto-match.)

- [ ] **Step 6: Commit**

```bash
git add scripts/kov_registry.py tests/test_kov_registry.py data/ehak/issuer_successor_map.csv
git commit -m "Add curated successor map loader with mandatory evidence"
```

---

## Task 5: Build the combined issuer registry

**Files:**
- Create: `scripts/build_kov_registry.py`
- Modify: `scripts/kov_registry.py` (add `discover_issuer_slugs`, `build_issuer_registry`)
- Create: `tests/test_build_kov_registry.py`
- Create: `tests/fixtures/kov_layer1/kov_dirs/` (skeleton directory tree)
- Modify: `data/ehak/issuer_successor_map.csv` (fill in failing rows)
- Create: `data/ehak/issuers.json` (generated, committed)

The CLI walks `krr_outputs/regulations/kov/`, gets all 357 directory names, runs each through auto-match, and falls back to the curated CSV. Any unmapped issuer is a hard error.

- [ ] **Step 1: Add `discover_issuer_slugs` test**

Append to `tests/test_kov_registry.py`:

```python
from kov_registry import discover_issuer_slugs


class TestDiscoverIssuerSlugs:
    def test_finds_directory_names(self, tmp_path):
        kov_root = tmp_path / "regulations" / "kov"
        for name in ("tallinna_linnavolikogu", "mulgi_vallavolikogu"):
            (kov_root / name).mkdir(parents=True)
        # Also create the index file that should be ignored
        (kov_root / "REGULATIONS_KOV_INDEX.json").write_text("{}")

        slugs = discover_issuer_slugs(kov_root)
        assert slugs == ["mulgi_vallavolikogu", "tallinna_linnavolikogu"]

    def test_missing_root_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            discover_issuer_slugs(tmp_path / "does_not_exist")
```

- [ ] **Step 2: Implement `discover_issuer_slugs`**

Add to `scripts/kov_registry.py`:

```python
def discover_issuer_slugs(kov_root: Path) -> list[str]:
    """Return sorted list of issuer-directory slugs under regulations/kov/."""
    if not kov_root.is_dir():
        raise FileNotFoundError(f"KOV root not found: {kov_root}")
    return sorted(p.name for p in kov_root.iterdir() if p.is_dir())
```

- [ ] **Step 3: Run the new test**

Run: `pytest tests/test_kov_registry.py::TestDiscoverIssuerSlugs -v`
Expected: 2 passed.

- [ ] **Step 4: Add `build_issuer_registry` test**

Append to `tests/test_kov_registry.py`:

```python
from kov_registry import build_issuer_registry


class TestBuildIssuerRegistry:
    def test_combines_auto_and_curated(self, tmp_path):
        # Two issuer dirs: one auto-matchable (tallinna), one needing
        # the curated CSV (abja → Mulgi)
        kov_root = tmp_path / "regulations" / "kov"
        (kov_root / "tallinna_linnavolikogu").mkdir(parents=True)
        (kov_root / "abja_vallavolikogu").mkdir(parents=True)

        csv_path = tmp_path / "map.csv"
        csv_path.write_text(
            "issuer_slug,current_municipality_ehak,mapping_source,mapping_evidence\n"
            "abja_vallavolikogu,0855,haldusreform-2017,RT I 21.06.2017 1\n",
            encoding="utf-8",
        )

        muns = load_municipalities(MIN_MUNICIPALITIES)
        curated = load_curated_map(csv_path)
        slugs = discover_issuer_slugs(kov_root)
        issuers = build_issuer_registry(slugs, muns, curated)

        tallinn = issuers["tallinna_linnavolikogu"]
        assert tallinn["currentMunicipalityCode"] == "0784"
        assert tallinn["mappingSource"] == "auto-match"
        assert tallinn["mappingEvidence"] == ""
        assert tallinn["bodyType"] == "volikogu"

        abja = issuers["abja_vallavolikogu"]
        assert abja["currentMunicipalityCode"] == "0855"
        assert abja["mappingSource"] == "haldusreform-2017"
        assert abja["mappingEvidence"] == "RT I 21.06.2017 1"

    def test_unmapped_issuer_raises(self, tmp_path):
        kov_root = tmp_path / "regulations" / "kov"
        (kov_root / "obscure_vallavolikogu").mkdir(parents=True)

        muns = load_municipalities(MIN_MUNICIPALITIES)
        slugs = discover_issuer_slugs(kov_root)

        with pytest.raises(ValueError, match="obscure_vallavolikogu"):
            build_issuer_registry(slugs, muns, curated={})

    def test_curated_with_unknown_ehak_raises(self, tmp_path):
        # Curated row points at a non-existent EHAK code — must fail at
        # registry build time, not later at SHACL.
        kov_root = tmp_path / "regulations" / "kov"
        (kov_root / "abja_vallavolikogu").mkdir(parents=True)

        muns = load_municipalities(MIN_MUNICIPALITIES)
        slugs = discover_issuer_slugs(kov_root)
        curated = {
            "abja_vallavolikogu": {
                "currentMunicipalityCode": "9999",  # not in registry
                "mappingSource": "haldusreform-2017",
                "mappingEvidence": "RT I 21.06.2017 1",
            }
        }
        with pytest.raises(ValueError, match="9999"):
            build_issuer_registry(slugs, muns, curated)

    def test_curated_with_invalid_source_raises(self, tmp_path):
        kov_root = tmp_path / "regulations" / "kov"
        (kov_root / "abja_vallavolikogu").mkdir(parents=True)

        muns = load_municipalities(MIN_MUNICIPALITIES)
        slugs = discover_issuer_slugs(kov_root)
        curated = {
            "abja_vallavolikogu": {
                "currentMunicipalityCode": "0855",
                "mappingSource": "guess",  # not in allowed set
                "mappingEvidence": "anything",
            }
        }
        with pytest.raises(ValueError, match="mapping_source"):
            build_issuer_registry(slugs, muns, curated)
```

- [ ] **Step 5: Run the test (expect failure)**

Run: `pytest tests/test_kov_registry.py::TestBuildIssuerRegistry -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 6: Implement `build_issuer_registry`**

Add to `scripts/kov_registry.py`:

```python
class IssuerEntry(TypedDict):
    slug: str
    displayName: str
    bodyType: str                    # "volikogu" | "valitsus"
    currentMunicipalityCode: str
    mappingSource: str               # "auto-match" | "haldusreform-2017"
                                     # | "manual-review" | "url:..."
    mappingEvidence: str             # may be empty for auto-match rows
    historicalMunicipalityName: str  # the issuing-time KOV unit, free text


def _slug_to_display_name(slug: str) -> str:
    """Convert ``tallinna_linnavolikogu`` → ``Tallinna Linnavolikogu``."""
    return " ".join(part.capitalize() for part in slug.split("_"))


_ALLOWED_MAPPING_SOURCES = {
    "auto-match",
    "haldusreform-2017",
    "manual-review",
}


def _validate_mapping_source(source: str) -> None:
    if source.startswith("url:"):
        return
    if source not in _ALLOWED_MAPPING_SOURCES:
        raise ValueError(
            f"invalid mapping_source: {source!r} "
            f"(allowed: {sorted(_ALLOWED_MAPPING_SOURCES)} or 'url:<...>')"
        )


def build_issuer_registry(
    slugs: list[str],
    municipalities: dict[str, Municipality],
    curated: dict[str, CuratedRow],
) -> dict[str, IssuerEntry]:
    """Build the combined issuer registry.

    Auto-match wins where (root, municipalityType) is unique. Curated
    CSV covers the rest. Unmapped issuers raise ValueError listing every
    failure. Curated rows are also validated against the municipalities
    registry (EHAK code must exist) and the allowed mapping_source set,
    so typos surface here rather than at SHACL time.
    """
    out: dict[str, IssuerEntry] = {}
    unmapped: list[str] = []
    for slug in slugs:
        parts = parse_issuer_slug(slug)
        ehak = auto_match_municipality(parts, municipalities)
        if ehak is not None:
            mapping_source = "auto-match"
            mapping_evidence = ""
        elif slug in curated:
            row = curated[slug]
            ehak = row["currentMunicipalityCode"]
            mapping_source = row["mappingSource"]
            mapping_evidence = row["mappingEvidence"]
            _validate_mapping_source(mapping_source)
            if ehak not in municipalities:
                raise ValueError(
                    f"curated row for {slug!r} references unknown EHAK "
                    f"code {ehak!r}; not present in municipalities.json"
                )
        else:
            unmapped.append(slug)
            continue

        out[slug] = {
            "slug": slug,
            "displayName": _slug_to_display_name(slug),
            "bodyType": parts["bodyType"],
            "currentMunicipalityCode": ehak,
            "mappingSource": mapping_source,
            "mappingEvidence": mapping_evidence,
            "historicalMunicipalityName": parts["root"].replace("_", " ").title(),
        }

    if unmapped:
        raise ValueError(
            "Unmapped issuers (add curated rows for each):\n  "
            + "\n  ".join(unmapped)
        )
    return out
```

- [ ] **Step 7: Run the test class**

Run: `pytest tests/test_kov_registry.py::TestBuildIssuerRegistry -v`
Expected: 2 passed.

- [ ] **Step 8: Implement the CLI**

Create `scripts/build_kov_registry.py`:

```python
"""CLI: build data/ehak/issuers.json from auto-match + curated CSV.

Run: python scripts/build_kov_registry.py
Exits non-zero with a list of unmapped issuers if any are unresolvable.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kov_registry import (
    build_issuer_registry,
    discover_issuer_slugs,
    load_curated_map,
    load_municipalities,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
EHAK_DIR = REPO_ROOT / "data" / "ehak"
KOV_ROOT = REPO_ROOT / "krr_outputs" / "regulations" / "kov"

MUNICIPALITIES = EHAK_DIR / "municipalities.json"
CURATED_CSV = EHAK_DIR / "issuer_successor_map.csv"
ISSUERS_OUT = EHAK_DIR / "issuers.json"


def main() -> int:
    municipalities = load_municipalities(MUNICIPALITIES)
    curated = load_curated_map(CURATED_CSV)
    slugs = discover_issuer_slugs(KOV_ROOT)
    print(f"Found {len(slugs)} issuer directories")
    print(f"Curated CSV has {len(curated)} explicit mappings")

    try:
        registry = build_issuer_registry(slugs, municipalities, curated)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    rows = [registry[slug] for slug in sorted(registry)]
    with open(ISSUERS_OUT, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    by_source: dict[str, int] = {}
    for r in rows:
        by_source[r["mappingSource"]] = by_source.get(r["mappingSource"], 0) + 1
    print(f"Wrote {len(rows)} issuers to {ISSUERS_OUT}")
    print("By mapping source:", by_source)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 9: Run the CLI to surface unmapped issuers**

Run: `python scripts/build_kov_registry.py`
Expected on first run: non-zero exit with a list of perhaps 100–150 slugs that auto-match cannot resolve.

- [ ] **Step 10: Fill in the curated CSV from the unmapped list**

For each slug in the failure list, look up the haldusreform 2017 successor in the official Riigikogu act. Add a row to `data/ehak/issuer_successor_map.csv` with:
- `current_municipality_ehak` — EHAK code of today's successor
- `mapping_source` — `haldusreform-2017` (most common), `manual-review` (when the case required judgement)
- `mapping_evidence` — citation string. Mandatory.

The 2017 reform act is at <https://www.riigiteataja.ee/akt/121062017001>; the per-municipality successor lists live in the annexes.

- [ ] **Step 11: Re-run the CLI until it succeeds**

Run: `python scripts/build_kov_registry.py`
Expected: zero exit, message like `Wrote 357 issuers to data/ehak/issuers.json`. Counts by source roughly: ~200 auto-match, ~150 haldusreform-2017, handful of manual-review.

- [ ] **Step 12: Add CLI tests for `build_kov_registry.py`**

The CLI's exit-code contract is part of the deliverable: it must exit
non-zero when issuers are unmapped, so CI can fail loudly. Add a
subprocess-driven test that exercises both the success and the
unmapped-failure paths.

Create `tests/test_build_kov_registry.py`:

```python
"""Subprocess tests for scripts/build_kov_registry.py CLI behaviour.

Drives the CLI as a child process so the test exercises the real
argument parsing, exit code, and stderr handling — not just the
underlying library functions.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "build_kov_registry.py"
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "kov_layer1"
MIN_MUNICIPALITIES = FIXTURE_DIR / "municipalities_min.json"


def _stage_repo(tmp_path: Path, kov_slugs: list[str], curated_csv: str):
    """Create a fake repo tree under tmp_path so the CLI can run."""
    (tmp_path / "krr_outputs" / "regulations" / "kov").mkdir(parents=True)
    (tmp_path / "data" / "ehak").mkdir(parents=True)
    for slug in kov_slugs:
        (tmp_path / "krr_outputs" / "regulations" / "kov" / slug).mkdir()
    shutil.copy(MIN_MUNICIPALITIES,
                tmp_path / "data" / "ehak" / "municipalities.json")
    (tmp_path / "data" / "ehak" / "issuer_successor_map.csv").write_text(
        curated_csv, encoding="utf-8"
    )
    # The script imports kov_registry; symlink scripts/ so imports resolve.
    (tmp_path / "scripts").symlink_to(REPO_ROOT / "scripts")
    return tmp_path


def _run_cli(repo_root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
    )


HEADER = "issuer_slug,current_municipality_ehak,mapping_source,mapping_evidence\n"


class TestBuildKovRegistryCLI:
    def test_success_writes_issuers_json(self, tmp_path):
        repo = _stage_repo(
            tmp_path,
            kov_slugs=["tallinna_linnavolikogu", "abja_vallavolikogu"],
            curated_csv=(
                HEADER
                + "abja_vallavolikogu,0855,haldusreform-2017,RT I 21.06.2017 1\n"
            ),
        )
        result = _run_cli(repo)
        assert result.returncode == 0, result.stderr
        out_path = repo / "data" / "ehak" / "issuers.json"
        assert out_path.exists()
        rows = json.load(out_path.open())
        slugs = {r["slug"] for r in rows}
        assert slugs == {"tallinna_linnavolikogu", "abja_vallavolikogu"}
        # Stdout should mention the source breakdown
        assert "auto-match" in result.stdout

    def test_unmapped_issuer_exits_nonzero(self, tmp_path):
        repo = _stage_repo(
            tmp_path,
            kov_slugs=["obscure_vallavolikogu"],
            curated_csv=HEADER,  # empty — no curated mapping for obscure
        )
        result = _run_cli(repo)
        assert result.returncode != 0
        assert "obscure_vallavolikogu" in result.stderr

    def test_curated_unknown_ehak_exits_nonzero(self, tmp_path):
        # A curated row pointing at a nonexistent EHAK code surfaces here,
        # not at SHACL time.
        repo = _stage_repo(
            tmp_path,
            kov_slugs=["abja_vallavolikogu"],
            curated_csv=(
                HEADER
                + "abja_vallavolikogu,9999,haldusreform-2017,evidence\n"
            ),
        )
        result = _run_cli(repo)
        assert result.returncode != 0
        assert "9999" in (result.stderr + result.stdout)
```

Run: `pytest tests/test_build_kov_registry.py -v`
Expected: 3 passed.

- [ ] **Step 13: Commit**

```bash
git add scripts/build_kov_registry.py scripts/kov_registry.py tests/test_kov_registry.py tests/test_build_kov_registry.py data/ehak/issuer_successor_map.csv data/ehak/issuers.json
git commit -m "Build combined KOV issuer registry with curated successor map"
```

---

## Task 6: Title normalization

**Files:**
- Modify: `scripts/kov_registry.py` (add `normalize_title`)
- Modify: `tests/test_kov_registry.py` (add test class)

The bucket detector in Layer 3 reads `estleg:titleNormalized` literals. Layer 1 enrichment writes them. The normalizer strips the municipality-name prefix when present, lowercases, transliterates diacritics.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_kov_registry.py`:

```python
from kov_registry import normalize_title


class TestNormalizeTitle:
    def test_strips_municipality_prefix(self):
        assert normalize_title(
            "Tallinna jäätmehoolduseeskiri", "Tallinna Linnavolikogu"
        ) == "jaatmehoolduseeskiri"

    def test_strips_genitive_form(self):
        # "Mulgi valla" is the genitive of "Mulgi vald"
        assert normalize_title(
            "Mulgi valla hankekord", "Mulgi Vallavolikogu"
        ) == "hankekord"

    def test_no_prefix_match_keeps_full_title(self):
        assert normalize_title(
            "Üldhariduskooli põhimäärus", "Tartu Linnavolikogu"
        ) == "uldhariduskooli pohimaarus"

    def test_lowercases_and_transliterates(self):
        assert normalize_title(
            "Põhimäärus", "Põlva Vallavolikogu"
        ) == "pohimaarus"
```

- [ ] **Step 2: Run test (expect failure)**

Run: `pytest tests/test_kov_registry.py::TestNormalizeTitle -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement `normalize_title`**

Add to `scripts/kov_registry.py`:

```python
import re


def normalize_title(title: str, issuer_display_name: str) -> str:
    """Lowercase + transliterate the title; strip the issuer's municipality
    name prefix when present at the start.

    The issuer display name is e.g. ``Tallinna Linnavolikogu``. Its first
    token (``Tallinna``) is the genitive form of the municipality name and
    is the most common prefix on KOV act titles. The function also handles
    the form ``<root> valla`` / ``<root> linna`` (e.g.
    ``Mulgi valla hankekord``).
    """
    base = title.translate(_TRANSLIT).lower().strip()
    base = re.sub(r"\s+", " ", base)

    # Issuer's first token is the municipality name in genitive form
    issuer_first = issuer_display_name.split()[0]
    prefix_token = issuer_first.translate(_TRANSLIT).lower()

    # Try several prefix shapes, longest first
    for prefix in (
        f"{prefix_token} valla ",
        f"{prefix_token} linna ",
        f"{prefix_token} ",
    ):
        if base.startswith(prefix):
            base = base[len(prefix):]
            break
    return base.strip()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_kov_registry.py::TestNormalizeTitle -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/kov_registry.py tests/test_kov_registry.py
git commit -m "Add title normalization helper for KOV layer 1"
```

---

## Task 7: Schema additions in metadata.jsonld

**Files:**
- Modify: `metadata.jsonld`
- Modify: `tests/test_kov_registry.py` (add a smoke test that the JSON-LD parses)

`metadata.jsonld` is the human-readable schema description and is loaded by `validate_all.py`. Add the new classes, subclass triples, and property declarations.

- [ ] **Step 1: Inspect the existing structure**

Run: `python -c "import json; d=json.load(open('metadata.jsonld'))['@graph']; print(d[0])"`
Expected: prints the first node so you can see the existing shape (typically `{"@id": "estleg:LegalProvision", "@type": "owl:Class", ...}` or similar).

- [ ] **Step 2: Write a smoke test**

Append to `tests/test_kov_registry.py`:

```python
class TestMetadataJsonLd:
    def test_new_classes_present(self):
        path = REPO_ROOT / "metadata.jsonld"
        if not path.exists():
            pytest.skip("metadata.jsonld missing")
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        ids = {n.get("@id") for n in doc.get("@graph", [])}
        for required in (
            "estleg:Act", "estleg:Law",
            "estleg:Municipality", "estleg:Issuer", "estleg:KovProvision",
            "estleg:Citation", "estleg:Similarity",
            "estleg:enactedBy", "estleg:enactedByMunicipality",
            "estleg:titleNormalized", "estleg:partOfAct",
            "estleg:ehakCode", "estleg:county", "estleg:bodyType",
            "estleg:currentMunicipality", "estleg:historicalMunicipalityName",
            "estleg:mappingSource", "estleg:mappingEvidence",
        ):
            assert required in ids, f"missing in metadata.jsonld: {required}"

    def test_subclass_triples_present(self):
        path = REPO_ROOT / "metadata.jsonld"
        if not path.exists():
            pytest.skip("metadata.jsonld missing")
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)

        def parents_of(class_id):
            for n in doc["@graph"]:
                if n.get("@id") == class_id:
                    raw = n.get("rdfs:subClassOf", [])
                    if isinstance(raw, dict):
                        raw = [raw]
                    return {p.get("@id") for p in raw}
            return set()

        assert "estleg:Act" in parents_of("estleg:Law")
        assert "estleg:Act" in parents_of("estleg:NationalRegulation")
        assert "estleg:Act" in parents_of("estleg:MunicipalRegulation")
        assert "estleg:Institution" in parents_of("estleg:Issuer")
        assert "estleg:LegalProvision" in parents_of("estleg:KovProvision")
```

- [ ] **Step 3: Run the smoke test (expect failure)**

Run: `pytest tests/test_kov_registry.py::TestMetadataJsonLd -v`
Expected: FAIL on the first missing class.

- [ ] **Step 4: Add the class declarations to `metadata.jsonld`**

Append to the `@graph` array in `metadata.jsonld`. Match the existing JSON-LD style used in the file:

```json
{
  "@id": "estleg:Act",
  "@type": "owl:Class",
  "rdfs:label": "Act",
  "rdfs:comment": "An enacted Estonian legal act of any level (law, state regulation, municipal regulation)."
},
{
  "@id": "estleg:Law",
  "@type": "owl:Class",
  "rdfs:label": "Law",
  "rdfs:subClassOf": {"@id": "estleg:Act"},
  "rdfs:comment": "A statute (seadus). Stamped onto every existing law act node during KOV layer 1 enrichment."
},
{
  "@id": "estleg:Municipality",
  "@type": "owl:Class",
  "rdfs:label": "Municipality",
  "rdfs:comment": "Current Estonian municipality (KOV unit). Territorial entity, identified by EHAK code."
},
{
  "@id": "estleg:Issuer",
  "@type": "owl:Class",
  "rdfs:label": "Issuer",
  "rdfs:subClassOf": {"@id": "estleg:Institution"},
  "rdfs:comment": "An act-issuing body (volikogu or valitsus). Subclass of Institution so competence-extractor links target Issuers via existing Institution-range properties."
},
{
  "@id": "estleg:KovProvision",
  "@type": "owl:Class",
  "rdfs:label": "KovProvision",
  "rdfs:subClassOf": {"@id": "estleg:LegalProvision"},
  "rdfs:comment": "A provision belonging to a MunicipalRegulation. Stamped during KOV layer 1 enrichment so SHACL can target provision-level constraints without OWL inference."
},
{
  "@id": "estleg:Citation",
  "@type": "owl:Class",
  "rdfs:label": "Citation",
  "rdfs:comment": "Reified preamble citation (used by KOV layer 2). Carries citationTarget, citationDetail, citationText."
},
{
  "@id": "estleg:Similarity",
  "@type": "owl:Class",
  "rdfs:label": "Similarity",
  "rdfs:comment": "Reified similarity link between two acts (used by KOV layer 3). Carries similarTarget, similarityScore, similarityModel."
}
```

Then add subclass triples for the existing regulation classes. If the existing nodes are present in `metadata.jsonld`, edit them in place to add `rdfs:subClassOf`. Otherwise append:

```json
{
  "@id": "estleg:NationalRegulation",
  "rdfs:subClassOf": {"@id": "estleg:Act"}
},
{
  "@id": "estleg:MunicipalRegulation",
  "rdfs:subClassOf": {"@id": "estleg:Act"}
},
{
  "@id": "estleg:GovernmentRegulation",
  "rdfs:subClassOf": {"@id": "estleg:NationalRegulation"}
},
{
  "@id": "estleg:MinisterialRegulation",
  "rdfs:subClassOf": {"@id": "estleg:NationalRegulation"}
}
```

- [ ] **Step 5: Add the property declarations**

For each of the 11 new properties in the schema-additions reference at the top of this plan, append a node like:

```json
{
  "@id": "estleg:enactedBy",
  "@type": "owl:ObjectProperty",
  "rdfs:label": "enactedBy",
  "rdfs:comment": "The Issuer that enacted this MunicipalRegulation or KovProvision.",
  "rdfs:range": {"@id": "estleg:Issuer"}
},
{
  "@id": "estleg:enactedByMunicipality",
  "@type": "owl:ObjectProperty",
  "rdfs:label": "enactedByMunicipality",
  "rdfs:comment": "The current Municipality whose Issuer enacted this MunicipalRegulation or KovProvision.",
  "rdfs:range": {"@id": "estleg:Municipality"}
},
{
  "@id": "estleg:titleNormalized",
  "@type": "owl:DatatypeProperty",
  "rdfs:label": "titleNormalized",
  "rdfs:comment": "Lowercased, transliterated title with the municipality-name prefix stripped. Used by Layer 3 bucket detection.",
  "rdfs:range": {"@id": "xsd:string"}
},
{
  "@id": "estleg:partOfAct",
  "@type": "owl:ObjectProperty",
  "rdfs:label": "partOfAct",
  "rdfs:comment": "IRI link from a provision to its parent act node. The existing sourceAct is a literal title; partOfAct is the structural join path.",
  "rdfs:range": {"@id": "estleg:Act"}
},
{
  "@id": "estleg:ehakCode",
  "@type": "owl:DatatypeProperty",
  "rdfs:label": "ehakCode",
  "rdfs:comment": "Four-digit EHAK code from Statistics Estonia.",
  "rdfs:range": {"@id": "xsd:string"}
},
{
  "@id": "estleg:county",
  "@type": "owl:DatatypeProperty",
  "rdfs:label": "county",
  "rdfs:comment": "Estonian county (maakond) the Municipality belongs to.",
  "rdfs:range": {"@id": "xsd:string"}
},
{
  "@id": "estleg:bodyType",
  "@type": "owl:DatatypeProperty",
  "rdfs:label": "bodyType",
  "rdfs:comment": "Issuer body type: 'volikogu' (legislative council) or 'valitsus' (executive).",
  "rdfs:range": {"@id": "xsd:string"}
},
{
  "@id": "estleg:currentMunicipality",
  "@type": "owl:ObjectProperty",
  "rdfs:label": "currentMunicipality",
  "rdfs:comment": "The current Municipality that the Issuer maps to (post-2017 successor for legacy Issuers).",
  "rdfs:range": {"@id": "estleg:Municipality"}
},
{
  "@id": "estleg:historicalMunicipalityName",
  "@type": "owl:DatatypeProperty",
  "rdfs:label": "historicalMunicipalityName",
  "rdfs:comment": "Free-text issuing-time municipality name. Preserves history without modeling defunct entities.",
  "rdfs:range": {"@id": "xsd:string"}
},
{
  "@id": "estleg:mappingSource",
  "@type": "owl:DatatypeProperty",
  "rdfs:label": "mappingSource",
  "rdfs:comment": "How the Issuer→Municipality mapping was derived: 'auto-match' | 'haldusreform-2017' | 'manual-review' | 'url:<citation>'.",
  "rdfs:range": {"@id": "xsd:string"}
},
{
  "@id": "estleg:mappingEvidence",
  "@type": "owl:DatatypeProperty",
  "rdfs:label": "mappingEvidence",
  "rdfs:comment": "Source citation for a curated Issuer mapping. Empty for auto-match rows; required for every curated row.",
  "rdfs:range": {"@id": "xsd:string"}
}
```

- [ ] **Step 6: Run the smoke test**

Run: `pytest tests/test_kov_registry.py::TestMetadataJsonLd -v`
Expected: 2 passed.

- [ ] **Step 7: Commit**

```bash
git add metadata.jsonld tests/test_kov_registry.py
git commit -m "Add KOV layer 1 schema (Act, Law, Municipality, Issuer, KovProvision)"
```

---

## Task 8: SHACL shapes

**Files:**
- Modify: `shacl/estonian_legal_shapes.ttl`
- Create: `tests/test_shacl_kov_layer1.py`

Shapes guard the schema invariants: every Municipality has `ehakCode` + `county`; every Issuer has `currentMunicipality` + `bodyType` + `mappingSource`; every `MunicipalRegulation` has `enactedBy` + `enactedByMunicipality` + `titleNormalized`; every `KovProvision` has `enactedBy` + `enactedByMunicipality` + `partOfAct`; every Law node has both `owl:Ontology` and `estleg:Law` types; IRI patterns are enforced for Municipality and Issuer.

- [ ] **Step 1: Write the failing test**

Create `tests/test_shacl_kov_layer1.py`:

```python
"""Smoke-test the new SHACL shapes against synthetic graphs.

Uses pyshacl. Build a tiny in-memory JSON-LD graph that should pass and
confirm the validator agrees, plus a graph missing a required field that
should fail.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SHAPES = REPO_ROOT / "shacl" / "estonian_legal_shapes.ttl"


def _validate(graph_json: dict) -> tuple[bool, str]:
    pyshacl = pytest.importorskip("pyshacl")
    rdflib = pytest.importorskip("rdflib")
    g = rdflib.Graph()
    g.parse(data=json.dumps(graph_json), format="json-ld")
    shapes = rdflib.Graph().parse(str(SHAPES), format="turtle")
    conforms, _, msg = pyshacl.validate(g, shacl_graph=shapes, inference="rdfs")
    return conforms, str(msg)


CONTEXT = {
    "estleg": "https://data.riik.ee/ontology/estleg#",
    "owl": "http://www.w3.org/2002/07/owl#",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
}


class TestMunicipalityShape:
    def test_valid_passes(self):
        ok, msg = _validate({
            "@context": CONTEXT,
            "@id": "estleg:Municipality_EHAK_0784",
            "@type": "estleg:Municipality",
            "rdfs:label": "Tallinn",
            "estleg:ehakCode": "0784",
            "estleg:county": "Harju maakond",
        })
        assert ok, msg

    def test_missing_county_fails(self):
        ok, _ = _validate({
            "@context": CONTEXT,
            "@id": "estleg:Municipality_EHAK_0784",
            "@type": "estleg:Municipality",
            "estleg:ehakCode": "0784",
        })
        assert not ok

    def test_bad_iri_pattern_fails(self):
        ok, _ = _validate({
            "@context": CONTEXT,
            "@id": "estleg:Municipality_FOOBAR",  # not EHAK-coded
            "@type": "estleg:Municipality",
            "estleg:ehakCode": "0784",
            "estleg:county": "Harju maakond",
        })
        assert not ok


class TestIssuerShape:
    def test_valid_passes(self):
        ok, msg = _validate({
            "@context": CONTEXT,
            "@graph": [
                {
                    "@id": "estleg:Municipality_EHAK_0784",
                    "@type": "estleg:Municipality",
                    "rdfs:label": "Tallinn",
                    "estleg:ehakCode": "0784",
                    "estleg:county": "Harju maakond",
                },
                {
                    "@id": "estleg:Issuer_tallinna_linnavolikogu",
                    "@type": "estleg:Issuer",
                    "rdfs:label": "Tallinna Linnavolikogu",
                    "estleg:bodyType": "volikogu",
                    "estleg:currentMunicipality": {"@id": "estleg:Municipality_EHAK_0784"},
                    "estleg:mappingSource": "auto-match",
                },
            ],
        })
        assert ok, msg

    def test_missing_body_type_fails(self):
        ok, _ = _validate({
            "@context": CONTEXT,
            "@graph": [
                {
                    "@id": "estleg:Municipality_EHAK_0784",
                    "@type": "estleg:Municipality",
                    "rdfs:label": "Tallinn",
                    "estleg:ehakCode": "0784",
                    "estleg:county": "Harju maakond",
                },
                {
                    "@id": "estleg:Issuer_tallinna_linnavolikogu",
                    "@type": "estleg:Issuer",
                    "rdfs:label": "Tallinna Linnavolikogu",
                    "estleg:currentMunicipality": {"@id": "estleg:Municipality_EHAK_0784"},
                    "estleg:mappingSource": "auto-match",
                },
            ],
        })
        assert not ok


class TestKovProvisionShape:
    def test_missing_part_of_act_fails(self):
        ok, _ = _validate({
            "@context": CONTEXT,
            "@id": "estleg:Reg_1014955_Par_1",
            "@type": ["owl:NamedIndividual", "estleg:KovProvision"],
            "estleg:paragrahv": "§ 1.",
            "estleg:summary": "stub",
            "estleg:enactedBy": {"@id": "estleg:Issuer_tallinna_linnavolikogu"},
            "estleg:enactedByMunicipality": {"@id": "estleg:Municipality_EHAK_0784"},
        })
        assert not ok


class TestEndToEndEnrichedKovGraph:
    """Validate a complete enriched KOV graph: Municipality + Issuer +
    MunicipalRegulation (typed Act) + KovProvision joined by partOfAct.

    Catches sh:class join failures that the negative-only tests miss —
    e.g. if `partOfAct` requires `sh:class estleg:Act` but the act node
    is only typed as `MunicipalRegulation`, this graph fails.
    """

    def test_full_enriched_graph_passes(self):
        ok, msg = _validate({
            "@context": CONTEXT,
            "@graph": [
                # Municipality
                {
                    "@id": "estleg:Municipality_EHAK_0784",
                    "@type": ["owl:NamedIndividual", "estleg:Municipality"],
                    "rdfs:label": "Tallinn",
                    "estleg:ehakCode": "0784",
                    "estleg:county": "Harju maakond",
                },
                # Issuer
                {
                    "@id": "estleg:Issuer_tallinna_linnavolikogu",
                    "@type": ["owl:NamedIndividual", "estleg:Issuer"],
                    "rdfs:label": "Tallinna Linnavolikogu",
                    "estleg:bodyType": "volikogu",
                    "estleg:currentMunicipality": {
                        "@id": "estleg:Municipality_EHAK_0784"
                    },
                    "estleg:mappingSource": "auto-match",
                },
                # MunicipalRegulation (act node, directly typed as Act)
                {
                    "@id": "estleg:Reg_1014955_Map_2026",
                    "@type": [
                        "owl:Ontology",
                        "estleg:Act",
                        "estleg:MunicipalRegulation",
                    ],
                    "rdfs:label": "Tallinna jäätmehoolduseeskiri",
                    "estleg:enactedBy": {"@id": "estleg:Issuer_tallinna_linnavolikogu"},
                    "estleg:enactedByMunicipality": {
                        "@id": "estleg:Municipality_EHAK_0784"
                    },
                    "estleg:titleNormalized": "jaatmehoolduseeskiri",
                },
                # KovProvision joined to act via partOfAct
                {
                    "@id": "estleg:Reg_1014955_Par_1",
                    "@type": [
                        "owl:NamedIndividual",
                        "estleg:KovProvision",
                    ],
                    "estleg:paragrahv": "§ 1.",
                    "estleg:summary": "stub",
                    "estleg:enactedBy": {"@id": "estleg:Issuer_tallinna_linnavolikogu"},
                    "estleg:enactedByMunicipality": {
                        "@id": "estleg:Municipality_EHAK_0784"
                    },
                    "estleg:partOfAct": {"@id": "estleg:Reg_1014955_Map_2026"},
                },
            ],
        })
        assert ok, f"enriched graph failed SHACL: {msg}"
```

- [ ] **Step 2: Run the test (expect failure)**

Run: `pytest tests/test_shacl_kov_layer1.py -v`
Expected: shapes don't exist yet, so the "valid passes" tests fail and "missing fails" tests still fail (since there's no shape to enforce them).

- [ ] **Step 3: Append the new shapes to `shacl/estonian_legal_shapes.ttl`**

Add at the end of the file:

```turtle
# ---------------------------------------------------------------------------
# KOV integration Layer 1 shapes
# ---------------------------------------------------------------------------

estleg:MunicipalityShape
    a sh:NodeShape ;
    sh:targetClass estleg:Municipality ;
    sh:nodeKind sh:IRI ;
    sh:pattern "^https://data\\.riik\\.ee/ontology/estleg#Municipality_EHAK_[0-9]{4}$" ;
    sh:property [
        sh:path estleg:ehakCode ;
        sh:minCount 1 ;
        sh:maxCount 1 ;
        sh:datatype xsd:string ;
        sh:pattern "^[0-9]{4}$" ;
        sh:name "ehakCode" ;
    ] ;
    sh:property [
        sh:path estleg:county ;
        sh:minCount 1 ;
        sh:maxCount 1 ;
        sh:datatype xsd:string ;
        sh:name "county" ;
    ] ;
    sh:property [
        sh:path rdfs:label ;
        sh:minCount 1 ;
        sh:datatype xsd:string ;
    ] .

estleg:IssuerShape
    a sh:NodeShape ;
    sh:targetClass estleg:Issuer ;
    sh:nodeKind sh:IRI ;
    sh:pattern "^https://data\\.riik\\.ee/ontology/estleg#Issuer_[a-z0-9_]+$" ;
    sh:property [
        sh:path estleg:bodyType ;
        sh:minCount 1 ;
        sh:maxCount 1 ;
        sh:in ("volikogu" "valitsus") ;
        sh:name "bodyType" ;
    ] ;
    sh:property [
        sh:path estleg:currentMunicipality ;
        sh:minCount 1 ;
        sh:maxCount 1 ;
        sh:class estleg:Municipality ;
        sh:name "currentMunicipality" ;
    ] ;
    sh:property [
        sh:path estleg:mappingSource ;
        sh:minCount 1 ;
        sh:maxCount 1 ;
        sh:datatype xsd:string ;
        sh:name "mappingSource" ;
    ] ;
    sh:property [
        sh:path rdfs:label ;
        sh:minCount 1 ;
        sh:datatype xsd:string ;
    ] .

estleg:MunicipalRegulationLayer1Shape
    a sh:NodeShape ;
    sh:targetClass estleg:MunicipalRegulation ;
    sh:property [
        sh:path estleg:enactedBy ;
        sh:minCount 1 ;
        sh:maxCount 1 ;
        sh:class estleg:Issuer ;
        sh:name "enactedBy" ;
    ] ;
    sh:property [
        sh:path estleg:enactedByMunicipality ;
        sh:minCount 1 ;
        sh:maxCount 1 ;
        sh:class estleg:Municipality ;
        sh:name "enactedByMunicipality" ;
    ] ;
    sh:property [
        sh:path estleg:titleNormalized ;
        sh:minCount 1 ;
        sh:maxCount 1 ;
        sh:datatype xsd:string ;
        sh:name "titleNormalized" ;
    ] .

estleg:KovProvisionShape
    a sh:NodeShape ;
    sh:targetClass estleg:KovProvision ;
    sh:property [
        sh:path estleg:enactedBy ;
        sh:minCount 1 ;
        sh:maxCount 1 ;
        sh:class estleg:Issuer ;
        sh:name "enactedBy" ;
    ] ;
    sh:property [
        sh:path estleg:enactedByMunicipality ;
        sh:minCount 1 ;
        sh:maxCount 1 ;
        sh:class estleg:Municipality ;
        sh:name "enactedByMunicipality" ;
    ] ;
    sh:property [
        sh:path estleg:partOfAct ;
        sh:minCount 1 ;
        sh:maxCount 1 ;
        sh:class estleg:Act ;
        sh:name "partOfAct" ;
    ] .
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_shacl_kov_layer1.py -v`
Expected: all 7 tests pass (3 Municipality + 2 Issuer + 1 KovProvision negative + 1 end-to-end positive).

- [ ] **Step 5: Commit**

```bash
git add shacl/estonian_legal_shapes.ttl tests/test_shacl_kov_layer1.py
git commit -m "Add SHACL shapes for KOV layer 1 entity model"
```

---

## Task 9: Generate the Municipality JSON-LD output

**Files:**
- Create: `scripts/enrich_kov_layer1.py` (start the orchestrator; this task fills in the Municipality output step only)
- Create: `tests/test_enrich_kov_layer1.py`

The output file is `krr_outputs/municipalities_peep.json`. Each Municipality is one JSON-LD node with `@id`, `@type`, `rdfs:label`, `estleg:ehakCode`, `estleg:county`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_enrich_kov_layer1.py`:

```python
"""End-to-end tests for KOV layer 1 enrichment."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pytest

from enrich_kov_layer1 import build_municipality_doc

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "kov_layer1"
MIN_MUNICIPALITIES = FIXTURE_DIR / "municipalities_min.json"


class TestBuildMunicipalityDoc:
    def test_builds_jsonld_doc(self):
        from kov_registry import load_municipalities
        muns = load_municipalities(MIN_MUNICIPALITIES)
        doc = build_municipality_doc(muns)

        assert "@context" in doc
        assert doc["@context"]["estleg"] == "https://data.riik.ee/ontology/estleg#"

        graph = doc["@graph"]
        ids = {n["@id"] for n in graph}
        assert "estleg:Municipality_EHAK_0784" in ids

        tallinn = next(n for n in graph if n["@id"] == "estleg:Municipality_EHAK_0784")
        assert "estleg:Municipality" in tallinn["@type"]
        assert tallinn["rdfs:label"] == "Tallinn"
        assert tallinn["estleg:ehakCode"] == "0784"
        assert tallinn["estleg:county"] == "Harju maakond"
```

- [ ] **Step 2: Run the test (expect failure)**

Run: `pytest tests/test_enrich_kov_layer1.py::TestBuildMunicipalityDoc -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'enrich_kov_layer1'`.

- [ ] **Step 3: Create `scripts/enrich_kov_layer1.py` with `build_municipality_doc`**

Create `scripts/enrich_kov_layer1.py`:

```python
"""KOV integration Layer 1 enrichment orchestrator.

Reads:
  - data/ehak/municipalities.json
  - data/ehak/issuers.json
  - krr_outputs/regulations/kov/<issuer>/*_peep.json
  - krr_outputs/*_peep.json (laws)

Writes:
  - krr_outputs/municipalities_peep.json (new)
  - krr_outputs/issuers_kov_peep.json (new)
  - in-place updates to KOV act + provision files
  - in-place stamp of estleg:Law on existing law nodes

Idempotent: safe to re-run.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kov_registry import (
    IssuerEntry,
    Municipality,
    load_municipalities,
    normalize_title,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
KRR_DIR = REPO_ROOT / "krr_outputs"
EHAK_DIR = REPO_ROOT / "data" / "ehak"
KOV_DIR = KRR_DIR / "regulations" / "kov"

MUNICIPALITIES_OUT = KRR_DIR / "municipalities_peep.json"
ISSUERS_OUT = KRR_DIR / "issuers_kov_peep.json"

CONTEXT = {
    "estleg": "https://data.riik.ee/ontology/estleg#",
    "owl": "http://www.w3.org/2002/07/owl#",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
    "skos": "http://www.w3.org/2004/02/skos/core#",
}


def municipality_iri(ehak_code: str) -> str:
    return f"estleg:Municipality_EHAK_{ehak_code}"


def issuer_iri(slug: str) -> str:
    return f"estleg:Issuer_{slug}"


def build_municipality_doc(municipalities: dict[str, Municipality]) -> dict:
    """Build the JSON-LD document containing all Municipality nodes."""
    nodes = [
        {
            "@id": "estleg:Municipalities_Map_2026",
            "@type": ["owl:Ontology"],
            "rdfs:label": "Estonian Municipalities (current EHAK)",
            "dcterms:source": "https://www.stat.ee/sites/default/files/2020-03/ehak.csv",
        }
    ]
    for code in sorted(municipalities):
        mun = municipalities[code]
        nodes.append({
            "@id": municipality_iri(code),
            "@type": ["owl:NamedIndividual", "estleg:Municipality"],
            "rdfs:label": mun["name"],
            "estleg:ehakCode": code,
            "estleg:county": mun["county"],
        })
    return {"@context": CONTEXT, "@graph": nodes}


def main() -> int:
    print("KOV layer 1 enrichment — TODO: full pipeline (see later tasks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_enrich_kov_layer1.py::TestBuildMunicipalityDoc -v`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/enrich_kov_layer1.py tests/test_enrich_kov_layer1.py
git commit -m "Add Municipality JSON-LD doc builder"
```

---

## Task 10: Generate the Issuer JSON-LD output

**Files:**
- Modify: `scripts/enrich_kov_layer1.py` (add `build_issuer_doc`)
- Modify: `tests/test_enrich_kov_layer1.py` (add test class)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_enrich_kov_layer1.py`:

```python
class TestBuildIssuerDoc:
    @pytest.fixture
    def issuers(self):
        return {
            "tallinna_linnavolikogu": {
                "slug": "tallinna_linnavolikogu",
                "displayName": "Tallinna Linnavolikogu",
                "bodyType": "volikogu",
                "currentMunicipalityCode": "0784",
                "mappingSource": "auto-match",
                "mappingEvidence": "",
                "historicalMunicipalityName": "Tallinn",
            },
            "abja_vallavolikogu": {
                "slug": "abja_vallavolikogu",
                "displayName": "Abja Vallavolikogu",
                "bodyType": "volikogu",
                "currentMunicipalityCode": "0855",
                "mappingSource": "haldusreform-2017",
                "mappingEvidence": "RT I 21.06.2017 1",
                "historicalMunicipalityName": "Abja",
            },
        }

    def test_builds_jsonld_doc(self, issuers):
        from enrich_kov_layer1 import build_issuer_doc
        doc = build_issuer_doc(issuers)
        graph = doc["@graph"]

        ids = {n["@id"] for n in graph}
        assert "estleg:Issuer_tallinna_linnavolikogu" in ids
        assert "estleg:Issuer_abja_vallavolikogu" in ids

        tallinn = next(n for n in graph
                       if n["@id"] == "estleg:Issuer_tallinna_linnavolikogu")
        assert "estleg:Issuer" in tallinn["@type"]
        assert tallinn["rdfs:label"] == "Tallinna Linnavolikogu"
        assert tallinn["estleg:bodyType"] == "volikogu"
        assert tallinn["estleg:currentMunicipality"] == {
            "@id": "estleg:Municipality_EHAK_0784"
        }
        assert tallinn["estleg:mappingSource"] == "auto-match"
        # Empty evidence is omitted entirely
        assert "estleg:mappingEvidence" not in tallinn

        abja = next(n for n in graph
                    if n["@id"] == "estleg:Issuer_abja_vallavolikogu")
        assert abja["estleg:mappingEvidence"] == "RT I 21.06.2017 1"
        assert abja["estleg:historicalMunicipalityName"] == "Abja"
```

- [ ] **Step 2: Run test (expect failure)**

Run: `pytest tests/test_enrich_kov_layer1.py::TestBuildIssuerDoc -v`
Expected: FAIL with `ImportError: cannot import name 'build_issuer_doc'`.

- [ ] **Step 3: Implement `build_issuer_doc`**

Add to `scripts/enrich_kov_layer1.py`:

```python
def build_issuer_doc(issuers: dict[str, IssuerEntry]) -> dict:
    """Build the JSON-LD document containing all Issuer nodes."""
    nodes: list[dict] = [
        {
            "@id": "estleg:Issuers_Kov_Map_2026",
            "@type": ["owl:Ontology"],
            "rdfs:label": "KOV Issuers (volikogu and valitsus bodies)",
        }
    ]
    for slug in sorted(issuers):
        entry = issuers[slug]
        node: dict = {
            "@id": issuer_iri(slug),
            "@type": ["owl:NamedIndividual", "estleg:Issuer"],
            "rdfs:label": entry["displayName"],
            "estleg:bodyType": entry["bodyType"],
            "estleg:currentMunicipality": {
                "@id": municipality_iri(entry["currentMunicipalityCode"])
            },
            "estleg:mappingSource": entry["mappingSource"],
        }
        if entry["mappingEvidence"]:
            node["estleg:mappingEvidence"] = entry["mappingEvidence"]
        if entry["historicalMunicipalityName"]:
            node["estleg:historicalMunicipalityName"] = entry["historicalMunicipalityName"]
        nodes.append(node)
    return {"@context": CONTEXT, "@graph": nodes}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_enrich_kov_layer1.py::TestBuildIssuerDoc -v`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/enrich_kov_layer1.py tests/test_enrich_kov_layer1.py
git commit -m "Add Issuer JSON-LD doc builder"
```

---

## Task 11: KOV act + provision in-place enrichment

**Files:**
- Modify: `scripts/enrich_kov_layer1.py` (add `enrich_kov_act_file`)
- Create: `tests/fixtures/kov_layer1/sample_kov_act.json` (one realistic KOV act peep file)
- Modify: `tests/test_enrich_kov_layer1.py` (add test class)

The enrichment touches the act node (gains `enactedBy`, `enactedByMunicipality`, `titleNormalized`) and every provision node in the same `@graph` (gains `KovProvision` rdf:type, `enactedBy`, `enactedByMunicipality`, `partOfAct`). Idempotent: re-running on an already-enriched file produces no changes.

- [ ] **Step 1: Create the fixture**

Create `tests/fixtures/kov_layer1/sample_kov_act.json` (a small valid KOV peep file shape, modelled on the actual files):

```json
{
  "@context": {
    "estleg": "https://data.riik.ee/ontology/estleg#",
    "owl": "http://www.w3.org/2002/07/owl#",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
    "skos": "http://www.w3.org/2004/02/skos/core#"
  },
  "@graph": [
    {
      "@id": "estleg:Reg_1014955_Map_2026",
      "@type": ["owl:Ontology", "estleg:MunicipalRegulation"],
      "rdfs:label": "Tallinna jäätmehoolduseeskiri (määrus)",
      "dc:source": "Tallinna jäätmehoolduseeskiri",
      "estleg:documentType": "määrus",
      "estleg:isKov": {"@value": "true", "@type": "xsd:boolean"},
      "estleg:issuer": "Tallinna Linnavolikogu",
      "estleg:terviktekstId": "1014955"
    },
    {
      "@id": "estleg:Regulation_1014955",
      "@type": ["owl:Class"],
      "rdfs:subClassOf": {"@id": "estleg:LegalProvision"}
    },
    {
      "@id": "estleg:Reg_1014955_Par_1",
      "@type": ["owl:NamedIndividual", "estleg:Regulation_1014955"],
      "estleg:paragrahv": "§ 1.",
      "rdfs:label": "§ 1. Title",
      "estleg:sourceAct": "Tallinna jäätmehoolduseeskiri",
      "estleg:summary": "Sample provision",
      "estleg:legalText": "Sample legal text"
    },
    {
      "@id": "estleg:Reg_1014955_Par_2",
      "@type": ["owl:NamedIndividual", "estleg:Regulation_1014955"],
      "estleg:paragrahv": "§ 2.",
      "rdfs:label": "§ 2. Other",
      "estleg:sourceAct": "Tallinna jäätmehoolduseeskiri",
      "estleg:summary": "Another provision"
    }
  ]
}
```

- [ ] **Step 2: Write the failing test**

Append to `tests/test_enrich_kov_layer1.py`:

```python
import shutil


SAMPLE_KOV_ACT = FIXTURE_DIR / "sample_kov_act.json"


class TestEnrichKovActFile:
    @pytest.fixture
    def issuer(self):
        return {
            "slug": "tallinna_linnavolikogu",
            "displayName": "Tallinna Linnavolikogu",
            "bodyType": "volikogu",
            "currentMunicipalityCode": "0784",
            "mappingSource": "auto-match",
            "mappingEvidence": "",
            "historicalMunicipalityName": "Tallinn",
        }

    @pytest.fixture
    def temp_act(self, tmp_path):
        dest = tmp_path / "act.json"
        shutil.copy(SAMPLE_KOV_ACT, dest)
        return dest

    def test_act_node_enriched(self, temp_act, issuer):
        from enrich_kov_layer1 import enrich_kov_act_file
        enrich_kov_act_file(temp_act, issuer)
        with open(temp_act, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        act = next(n for n in doc["@graph"]
                   if n["@id"] == "estleg:Reg_1014955_Map_2026")
        assert act["estleg:enactedBy"] == {"@id": "estleg:Issuer_tallinna_linnavolikogu"}
        assert act["estleg:enactedByMunicipality"] == {"@id": "estleg:Municipality_EHAK_0784"}
        assert act["estleg:titleNormalized"] == "jaatmehoolduseeskiri"
        # Direct Act type stamped (so partOfAct SHACL doesn't need inference)
        assert "estleg:Act" in act["@type"]
        # MunicipalRegulation preserved
        assert "estleg:MunicipalRegulation" in act["@type"]

    def test_provisions_enriched(self, temp_act, issuer):
        from enrich_kov_layer1 import enrich_kov_act_file
        enrich_kov_act_file(temp_act, issuer)
        with open(temp_act, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        provisions = [n for n in doc["@graph"] if "estleg:paragrahv" in n]
        assert len(provisions) == 2
        for p in provisions:
            assert "estleg:KovProvision" in p["@type"]
            assert p["estleg:enactedBy"] == {"@id": "estleg:Issuer_tallinna_linnavolikogu"}
            assert p["estleg:enactedByMunicipality"] == {"@id": "estleg:Municipality_EHAK_0784"}
            assert p["estleg:partOfAct"] == {"@id": "estleg:Reg_1014955_Map_2026"}

    def test_idempotent(self, temp_act, issuer):
        from enrich_kov_layer1 import enrich_kov_act_file
        enrich_kov_act_file(temp_act, issuer)
        once = json.load(open(temp_act, encoding="utf-8"))
        enrich_kov_act_file(temp_act, issuer)
        twice = json.load(open(temp_act, encoding="utf-8"))
        assert once == twice  # second run is a no-op

    def test_missing_act_node_raises(self, tmp_path, issuer):
        # A KOV file shape that lacks any estleg:MunicipalRegulation node
        # must fail loudly — not silently skip while the orchestrator
        # increments its enriched-count.
        from enrich_kov_layer1 import enrich_kov_act_file
        bad = tmp_path / "broken.json"
        bad.write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:NotAnAct_Map_2026",
                 "@type": ["owl:Ontology"],
                 "rdfs:label": "garbage"}
            ],
        }), encoding="utf-8")
        with pytest.raises(ValueError, match="no estleg:MunicipalRegulation"):
            enrich_kov_act_file(bad, issuer)

    def test_multiple_act_nodes_raise(self, tmp_path, issuer):
        # Two MunicipalRegulation nodes in one file must also fail —
        # otherwise provisions would all link to whichever comes first
        # and partial enrichment would corrupt the file silently.
        from enrich_kov_layer1 import enrich_kov_act_file
        bad = tmp_path / "two_acts.json"
        bad.write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Reg_1_Map_2026",
                 "@type": ["owl:Ontology", "estleg:MunicipalRegulation"]},
                {"@id": "estleg:Reg_2_Map_2026",
                 "@type": ["owl:Ontology", "estleg:MunicipalRegulation"]},
            ],
        }), encoding="utf-8")
        with pytest.raises(ValueError, match="found 2"):
            enrich_kov_act_file(bad, issuer)
```

- [ ] **Step 3: Run test (expect failure)**

Run: `pytest tests/test_enrich_kov_layer1.py::TestEnrichKovActFile -v`
Expected: FAIL with `ImportError: cannot import name 'enrich_kov_act_file'`.

- [ ] **Step 4: Implement `enrich_kov_act_file`**

Add to `scripts/enrich_kov_layer1.py`:

```python
def _add_type(node: dict, type_iri: str) -> bool:
    """Add type_iri to node["@type"] if missing. Returns True if changed."""
    types = node.get("@type")
    if isinstance(types, str):
        types = [types]
    elif types is None:
        types = []
    if type_iri in types:
        return False
    types.append(type_iri)
    node["@type"] = types
    return True


def enrich_kov_act_file(path: Path, issuer: IssuerEntry) -> None:
    """Add Layer 1 properties to a single KOV act peep file in place.

    Raises ValueError if the file does not contain exactly one node
    typed as estleg:MunicipalRegulation. A KOV file under the corpus
    must have that node — silently skipping malformed files would let
    Gate A pass while leaving acts undecorated.

    Idempotent — running on an already-enriched file leaves it
    byte-identical.
    """
    with open(path, "r", encoding="utf-8") as fh:
        doc = json.load(fh)

    issuer_ref = {"@id": issuer_iri(issuer["slug"])}
    municipality_ref = {"@id": municipality_iri(issuer["currentMunicipalityCode"])}

    # Pass 1: collect every MunicipalRegulation node, enforce exactly one,
    # then enrich it.
    act_nodes: list[dict] = []
    for node in doc.get("@graph", []):
        types = node.get("@type", [])
        if isinstance(types, str):
            types = [types]
        if "estleg:MunicipalRegulation" in types:
            act_nodes.append(node)

    if len(act_nodes) == 0:
        raise ValueError(
            f"{path}: no estleg:MunicipalRegulation node in @graph; "
            "expected a KOV act file"
        )
    if len(act_nodes) > 1:
        raise ValueError(
            f"{path}: found {len(act_nodes)} estleg:MunicipalRegulation "
            "nodes; KOV files must contain exactly one"
        )

    act_node = act_nodes[0]
    act_iri = act_node.get("@id")
    title = act_node.get("rdfs:label") or act_node.get("dc:source") or ""
    # Stamp estleg:Act directly so SHACL constraints with
    # sh:class estleg:Act on partOfAct don't require RDFS inference
    # at validation time.
    _add_type(act_node, "estleg:Act")
    act_node["estleg:enactedBy"] = issuer_ref
    act_node["estleg:enactedByMunicipality"] = municipality_ref
    act_node["estleg:titleNormalized"] = normalize_title(
        title, issuer["displayName"]
    )

    # Pass 2: enrich provisions.
    for node in doc.get("@graph", []):
        if "estleg:paragrahv" not in node:
            continue
        _add_type(node, "estleg:KovProvision")
        node["estleg:enactedBy"] = issuer_ref
        node["estleg:enactedByMunicipality"] = municipality_ref
        node["estleg:partOfAct"] = {"@id": act_iri}

    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_enrich_kov_layer1.py::TestEnrichKovActFile -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add scripts/enrich_kov_layer1.py tests/test_enrich_kov_layer1.py tests/fixtures/kov_layer1/sample_kov_act.json
git commit -m "Add KOV act/provision in-place enrichment"
```

---

## Task 12: Law type stamping

**Files:**
- Modify: `scripts/enrich_kov_layer1.py` (add `stamp_law_type`)
- Create: `tests/fixtures/kov_layer1/sample_law.json`
- Modify: `tests/test_enrich_kov_layer1.py` (add test class)

Existing law peep files have act nodes typed only as `owl:Ontology`. After Layer 1, they also carry `estleg:Law`. Idempotent.

- [ ] **Step 1: Create the fixture**

Create `tests/fixtures/kov_layer1/sample_law.json`:

```json
{
  "@context": {
    "estleg": "https://data.riik.ee/ontology/estleg#",
    "owl": "http://www.w3.org/2002/07/owl#",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "dc": "http://purl.org/dc/elements/1.1/"
  },
  "@graph": [
    {
      "@id": "estleg:AlkS_Map_2026",
      "@type": ["owl:Ontology"],
      "rdfs:label": "Alkoholiseadus teemakaardistus",
      "dc:source": "Alkoholiseadus"
    }
  ]
}
```

- [ ] **Step 2: Write the failing test**

Append to `tests/test_enrich_kov_layer1.py`:

```python
SAMPLE_LAW = FIXTURE_DIR / "sample_law.json"


class TestStampLawType:
    @pytest.fixture
    def temp_law(self, tmp_path):
        dest = tmp_path / "law.json"
        shutil.copy(SAMPLE_LAW, dest)
        return dest

    def test_stamps_law_type(self, temp_law):
        from enrich_kov_layer1 import stamp_law_type
        stamp_law_type(temp_law)
        with open(temp_law, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        law_node = doc["@graph"][0]
        assert "owl:Ontology" in law_node["@type"]
        assert "estleg:Law" in law_node["@type"]
        # estleg:Act is stamped alongside Law so SHACL constraints with
        # sh:class estleg:Act don't require RDFS inference.
        assert "estleg:Act" in law_node["@type"]

    def test_idempotent(self, temp_law):
        from enrich_kov_layer1 import stamp_law_type
        stamp_law_type(temp_law)
        once = json.load(open(temp_law, encoding="utf-8"))
        stamp_law_type(temp_law)
        twice = json.load(open(temp_law, encoding="utf-8"))
        assert once == twice

    def test_skips_non_root_types(self, temp_law):
        # If the act node is already a different specific type (e.g.
        # NationalRegulation), don't add Law — only top-level
        # owl:Ontology act nodes should be stamped with Law. (Act is
        # stamped on regulation acts via stamp_act_type instead.)
        with open(temp_law, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        doc["@graph"][0]["@type"] = ["owl:Ontology", "estleg:NationalRegulation"]
        with open(temp_law, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)

        from enrich_kov_layer1 import stamp_law_type
        stamp_law_type(temp_law)
        with open(temp_law, "r", encoding="utf-8") as fh:
            doc2 = json.load(fh)
        assert "estleg:Law" not in doc2["@graph"][0]["@type"]


class TestStampActType:
    """Stamps estleg:Act on state regulation acts (regulations/riik/)
    so that partOfAct and issuedUnder SHACL constraints don't need
    inference."""

    def test_stamps_act_on_national_regulation(self, tmp_path):
        from enrich_kov_layer1 import stamp_act_type
        f = tmp_path / "reg.json"
        f.write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Reg_1009410_Map_2026",
                 "@type": ["owl:Ontology", "estleg:NationalRegulation"],
                 "rdfs:label": "test"}
            ],
        }), encoding="utf-8")
        stamp_act_type(f)
        doc = json.load(open(f, encoding="utf-8"))
        types = doc["@graph"][0]["@type"]
        assert "estleg:Act" in types
        assert "estleg:NationalRegulation" in types  # preserved

    def test_idempotent(self, tmp_path):
        from enrich_kov_layer1 import stamp_act_type
        f = tmp_path / "reg.json"
        f.write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Reg_1_Map_2026",
                 "@type": ["owl:Ontology", "estleg:GovernmentRegulation"]}
            ],
        }), encoding="utf-8")
        stamp_act_type(f)
        once = json.load(open(f, encoding="utf-8"))
        stamp_act_type(f)
        twice = json.load(open(f, encoding="utf-8"))
        assert once == twice
```

- [ ] **Step 3: Run test (expect failure)**

Run: `pytest tests/test_enrich_kov_layer1.py::TestStampLawType -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 4: Implement `stamp_law_type`**

Add to `scripts/enrich_kov_layer1.py`:

```python
_REGULATION_TYPES = {
    "estleg:NationalRegulation",
    "estleg:GovernmentRegulation",
    "estleg:MinisterialRegulation",
    "estleg:MunicipalRegulation",
}


def stamp_law_type(path: Path) -> None:
    """Add `estleg:Law` and `estleg:Act` rdf:type to the act node of a
    law peep file.

    Skips files whose act node already has a regulation-specific type
    (NationalRegulation, MunicipalRegulation, etc.) — those go through
    `stamp_act_type` instead.

    Idempotent.
    """
    with open(path, "r", encoding="utf-8") as fh:
        doc = json.load(fh)

    changed = False
    for node in doc.get("@graph", []):
        types = node.get("@type", [])
        if isinstance(types, str):
            types = [types]
        if "owl:Ontology" not in types:
            continue
        if _REGULATION_TYPES.intersection(types):
            continue
        if _add_type(node, "estleg:Law"):
            changed = True
        if _add_type(node, "estleg:Act"):
            changed = True
        node["@type"] = sorted(set(node["@type"]))  # stable order

    if changed:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False, indent=2)
            fh.write("\n")


def stamp_act_type(path: Path) -> None:
    """Add `estleg:Act` to act nodes already typed as a regulation
    subclass (NationalRegulation / GovernmentRegulation /
    MinisterialRegulation). For state regulation files under
    `regulations/riik/`. Idempotent.

    KOV act nodes are handled by `enrich_kov_act_file`; this function is
    for the state regulation files that Layer 1 otherwise leaves alone.
    """
    with open(path, "r", encoding="utf-8") as fh:
        doc = json.load(fh)

    changed = False
    for node in doc.get("@graph", []):
        types = node.get("@type", [])
        if isinstance(types, str):
            types = [types]
        if "owl:Ontology" not in types:
            continue
        # Only stamp Act on regulation acts; KOV acts go through
        # enrich_kov_act_file.
        if "estleg:MunicipalRegulation" in types:
            continue
        if not _REGULATION_TYPES.intersection(types):
            continue
        if _add_type(node, "estleg:Act"):
            changed = True
            node["@type"] = sorted(set(node["@type"]))

    if changed:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_enrich_kov_layer1.py::TestStampLawType tests/test_enrich_kov_layer1.py::TestStampActType -v`
Expected: 5 passed (3 in StampLawType, 2 in StampActType).

- [ ] **Step 6: Commit**

```bash
git add scripts/enrich_kov_layer1.py tests/test_enrich_kov_layer1.py tests/fixtures/kov_layer1/sample_law.json
git commit -m "Add Law + Act type stamping for existing acts"
```

---

## Task 13: Wire up the orchestrator `main()`

**Files:**
- Modify: `scripts/enrich_kov_layer1.py` (replace the placeholder `main()` with the full orchestrator)
- Modify: `tests/test_enrich_kov_layer1.py` (add an end-to-end orchestrator test)

The full pipeline:
1. Load `data/ehak/municipalities.json` and `data/ehak/issuers.json`.
2. Write `krr_outputs/municipalities_peep.json` and `krr_outputs/issuers_kov_peep.json`.
3. For each KOV act file under `krr_outputs/regulations/kov/<slug>/*_peep.json`, look up the issuer and call `enrich_kov_act_file`.
4. For each law peep file at `krr_outputs/*_peep.json` (root level only), call `stamp_law_type`.
5. Print a summary of counts.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_enrich_kov_layer1.py`:

```python
class TestOrchestratorEndToEnd:
    def test_run_against_temp_tree(self, tmp_path, monkeypatch):
        """End-to-end: stage a small KOV tree + a law file in tmp, run main(),
        assert outputs."""
        # Arrange a faux KRR tree
        krr = tmp_path / "krr_outputs"
        ehak = tmp_path / "data" / "ehak"
        krr.mkdir()
        ehak.mkdir(parents=True)

        shutil.copy(MIN_MUNICIPALITIES, ehak / "municipalities.json")
        (ehak / "issuers.json").write_text(json.dumps([
            {
                "slug": "tallinna_linnavolikogu",
                "displayName": "Tallinna Linnavolikogu",
                "bodyType": "volikogu",
                "currentMunicipalityCode": "0784",
                "mappingSource": "auto-match",
                "mappingEvidence": "",
                "historicalMunicipalityName": "Tallinn",
            }
        ]), encoding="utf-8")

        # KOV act
        kov_dir = krr / "regulations" / "kov" / "tallinna_linnavolikogu"
        kov_dir.mkdir(parents=True)
        shutil.copy(SAMPLE_KOV_ACT, kov_dir / "act_peep.json")

        # Law + INDEX.json (source of canonical law file list)
        shutil.copy(SAMPLE_LAW, krr / "alkoholiseadus_peep.json")
        (krr / "INDEX.json").write_text(json.dumps({
            "generated": "2026-05-02",
            "total_files": 1,
            "total_laws": 1,
            "laws": [{"name": "alkoholiseadus",
                      "files": ["alkoholiseadus_peep.json"]}],
        }), encoding="utf-8")

        # State regulation under regulations/riik/
        riik = krr / "regulations" / "riik"
        riik.mkdir(parents=True)
        (riik / "valitsuse_maarus_peep.json").write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Reg_1009410_Map_2026",
                 "@type": ["owl:Ontology", "estleg:GovernmentRegulation"],
                 "rdfs:label": "Test government regulation"}
            ],
        }), encoding="utf-8")

        # Patch module-level paths
        import enrich_kov_layer1 as mod
        monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "EHAK_DIR", ehak)
        monkeypatch.setattr(mod, "KOV_DIR", krr / "regulations" / "kov")
        monkeypatch.setattr(mod, "MUNICIPALITIES_OUT", krr / "municipalities_peep.json")
        monkeypatch.setattr(mod, "ISSUERS_OUT", krr / "issuers_kov_peep.json")

        rc = mod.main()
        assert rc == 0

        # Outputs
        assert (krr / "municipalities_peep.json").exists()
        assert (krr / "issuers_kov_peep.json").exists()

        # KOV act enriched
        kov_doc = json.load(open(kov_dir / "act_peep.json", encoding="utf-8"))
        act = next(n for n in kov_doc["@graph"]
                   if n["@id"] == "estleg:Reg_1014955_Map_2026")
        assert "estleg:enactedBy" in act
        assert act["estleg:titleNormalized"] == "jaatmehoolduseeskiri"

        # Law type stamped (Law + Act both)
        law_doc = json.load(open(krr / "alkoholiseadus_peep.json", encoding="utf-8"))
        assert "estleg:Law" in law_doc["@graph"][0]["@type"]
        assert "estleg:Act" in law_doc["@graph"][0]["@type"]

        # State regulation stamped with Act, GovernmentRegulation preserved
        reg_doc = json.load(open(riik / "valitsuse_maarus_peep.json", encoding="utf-8"))
        types = reg_doc["@graph"][0]["@type"]
        assert "estleg:Act" in types
        assert "estleg:GovernmentRegulation" in types
```

- [ ] **Step 2: Run the test (expect failure)**

Run: `pytest tests/test_enrich_kov_layer1.py::TestOrchestratorEndToEnd -v`
Expected: FAIL — current `main()` is a stub.

- [ ] **Step 3: Replace `main()` in `scripts/enrich_kov_layer1.py`**

Replace the existing `main()`:

```python
def _load_issuers(path: Path) -> dict[str, IssuerEntry]:
    with open(path, "r", encoding="utf-8") as fh:
        rows = json.load(fh)
    return {row["slug"]: row for row in rows}


def _save_jsonld(path: Path, doc: dict) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def _load_law_paths(index_path: Path) -> tuple[list[Path], list[str]]:
    """Resolve law file paths from krr_outputs/INDEX.json.

    INDEX.json's `laws` is a list of {name, files: [...]}. A multi-part
    law (e.g. asjaoigusseadus_osa1, _osa2) has multiple files in one
    entry. Flatten and resolve to absolute paths under KRR_DIR.

    INDEX.json is the canonical source — using KRR_DIR.glob('*_peep.json')
    instead would conflate the 615 laws with other root-level files
    (combined ontology output, generated registry files, etc.) and would
    drift over time.

    Policy on missing entries: INDEX.json is known to drift (typos like
    'ametiuhigute' for 'ametiuhingute', renames not propagated, etc.).
    Rather than abort the whole Layer 1 run on stale INDEX entries —
    which is an unrelated hygiene issue — we log them and continue.
    The orchestrator surfaces the count in its summary so the drift
    is visible. INDEX hygiene is fixed in a separate PR.

    Mixed extensions: INDEX entries end in either `.json` or `.jsonld`.
    Both are accepted as-is; we do NOT silently substitute the
    alternate extension because that could mask real renames.

    Returns: (resolved_paths, missing_filenames)
    """
    with open(index_path, "r", encoding="utf-8") as fh:
        idx = json.load(fh)
    paths: list[Path] = []
    missing: list[str] = []
    for entry in idx.get("laws", []):
        for fname in entry.get("files", []):
            p = KRR_DIR / fname
            if p.exists():
                paths.append(p)
            else:
                missing.append(fname)
    return paths, missing


def main() -> int:
    print("=" * 70)
    print("KOV Integration Layer 1 — Enrichment")
    print("=" * 70)

    municipalities = load_municipalities(EHAK_DIR / "municipalities.json")
    issuers = _load_issuers(EHAK_DIR / "issuers.json")
    print(f"Loaded {len(municipalities)} municipalities, {len(issuers)} issuers")

    # 1. Write Municipality JSON-LD
    _save_jsonld(MUNICIPALITIES_OUT, build_municipality_doc(municipalities))
    print(f"Wrote {MUNICIPALITIES_OUT.name}")

    # 2. Write Issuer JSON-LD
    _save_jsonld(ISSUERS_OUT, build_issuer_doc(issuers))
    print(f"Wrote {ISSUERS_OUT.name}")

    # 3. Enrich KOV act files
    kov_files = sorted(KOV_DIR.glob("**/*_peep.json"))
    # The KOV index file lives at the top of KOV_DIR — exclude it.
    kov_files = [f for f in kov_files if not f.name.startswith("REGULATIONS_KOV_INDEX")]
    print(f"\nEnriching {len(kov_files)} KOV act files...")
    enriched = 0
    missing_issuer = 0
    for f in kov_files:
        slug = f.parent.name
        issuer = issuers.get(slug)
        if issuer is None:
            missing_issuer += 1
            print(f"  ERROR: no issuer entry for {slug}; skipping {f.name}")
            continue
        # Will raise on malformed files (no MunicipalRegulation node).
        enrich_kov_act_file(f, issuer)
        enriched += 1
    print(f"Enriched {enriched} KOV act files (missing-issuer: {missing_issuer})")
    if missing_issuer:
        # Hard-fail rather than let Gate A pass with skipped files.
        print(
            f"FAIL: {missing_issuer} KOV files had no matching issuer entry. "
            "Re-run scripts/build_kov_registry.py to regenerate issuers.json.",
            file=sys.stderr,
        )
        return 2

    # 4. Stamp estleg:Law + estleg:Act on the 615 laws (paths from INDEX.json)
    law_paths, missing_law_files = _load_law_paths(KRR_DIR / "INDEX.json")
    law_count = len({p.stem.replace("_peep", "").rsplit("_osa", 1)[0]
                     for p in law_paths})
    print(f"\nStamping estleg:Law on {len(law_paths)} law files "
          f"({law_count} unique laws)...")
    if missing_law_files:
        print(f"  WARN: INDEX.json references {len(missing_law_files)} "
              "missing law files (stale index — fix in a separate PR):")
        for f in missing_law_files[:10]:
            print(f"    {f}")
        if len(missing_law_files) > 10:
            print(f"    ... and {len(missing_law_files) - 10} more")
    for f in law_paths:
        stamp_law_type(f)

    # 5. Stamp estleg:Act on state regulation acts under regulations/riik/
    riik_dir = KRR_DIR / "regulations" / "riik"
    riik_files = sorted(riik_dir.glob("*_peep.json")) if riik_dir.exists() else []
    print(f"\nStamping estleg:Act on {len(riik_files)} state regulation files...")
    for f in riik_files:
        stamp_act_type(f)

    print("\nDone.")
    return 0
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_enrich_kov_layer1.py::TestOrchestratorEndToEnd -v`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/enrich_kov_layer1.py tests/test_enrich_kov_layer1.py
git commit -m "Wire up KOV layer 1 enrichment orchestrator"
```

---

## Task 14: Run the orchestrator against the real corpus

**Files:**
- Generated: `krr_outputs/municipalities_peep.json`, `krr_outputs/issuers_kov_peep.json`
- Generated diff: 11,059 KOV act files + 615 law files (in-place updates)

This is a real-data run, not a code change. It produces the artefacts that Gate A validates.

- [ ] **Step 1: Run the orchestrator**

Run: `python scripts/enrich_kov_layer1.py`
Expected output (exact counts depend on INDEX state at run time):

```
Loaded 79 municipalities, 357 issuers
Wrote municipalities_peep.json
Wrote issuers_kov_peep.json

Enriching 11059 KOV act files...
Enriched 11059 KOV act files (missing-issuer: 0)

Stamping estleg:Law on 637 law files (607 unique laws)...
  WARN: INDEX.json references 15 missing law files (stale index — fix in a separate PR):
    ametiuhigute_seadus_peep.json
    mittetulundusuhingu_seadus_peep.json
    riigivastutuse_peep.json
    taitemenetluse_peep.json
    tervishoiuteenuste_peep.json
    volaoigusseadus_osa1_peep.json
    volaoigusseadus_osa10_peep.json
    volaoigusseadus_osa2_peep.json
    ... and 7 more

Stamping estleg:Act on 3813 state regulation files...
Done.
```

Notes on counts:

- **Law files vs unique laws.** INDEX.json's `laws` field has 615
  entries, but the file count is higher because multi-part laws
  (e.g. *asjaõigusseadus_osa1, _osa2, …*) appear as multiple files
  under one `laws[i].files` array.
- **Missing-INDEX warning is expected.** The current INDEX.json
  references 15 files that no longer exist in the repo (typo entries
  like `ametiuhigute_seadus`, ortographically-deprecated entries like
  `volaoigusseadus_osa*`). The loader skips them and the warning
  summarises which. After skipping, the present count is **637 law
  files / 607 unique laws** at the time of writing — these numbers
  drift as INDEX is fixed in a separate PR. Whatever the orchestrator
  prints is the source of truth for that run; pin the verification
  numbers in Step 4 to this output, not to the static numbers above.

- [ ] **Step 2: Verify outputs exist**

Run:
```bash
ls -la krr_outputs/municipalities_peep.json krr_outputs/issuers_kov_peep.json
python -c "import json; d=json.load(open('krr_outputs/municipalities_peep.json')); print(len([n for n in d['@graph'] if 'estleg:Municipality' in (n.get('@type') or [])]))"
python -c "import json; d=json.load(open('krr_outputs/issuers_kov_peep.json')); print(len([n for n in d['@graph'] if 'estleg:Issuer' in (n.get('@type') or [])]))"
```
Expected output: `79`, `357`.

- [ ] **Step 3: Spot-check a KOV file**

Run:
```bash
python -c "import json; d=json.load(open('krr_outputs/regulations/kov/tallinna_linnavolikogu/$(ls krr_outputs/regulations/kov/tallinna_linnavolikogu | head -1)')); act=[n for n in d['@graph'] if 'estleg:MunicipalRegulation' in (n.get('@type') or [])][0]; print(act.get('estleg:enactedBy'), act.get('estleg:enactedByMunicipality'), act.get('estleg:titleNormalized'))"
```
Expected: prints the issuer IRI, municipality IRI, and a normalized title string.

- [ ] **Step 4: Run validate_all.py**

Run: `python scripts/validate_all.py`
Expected: zero exit. The existing checks (duplicate `@id`, `@type` arrays, etc.) cover the new files.

- [ ] **Step 5: Run idempotency check**

A naive `git status --short` after the second run will *also* show
the (uncommitted) diffs from the first run, so it can't distinguish
"new diffs from second run" from "first-run output not yet committed".
Two options — pick whichever fits your workflow:

**Option A — snapshot-based diff (no commit needed yet):**

The hash list must cover every file the orchestrator can touch,
including indexed law files ending in `.jsonld`. Build it from the
same resolved INDEX path list used for staging:

```bash
python3 - <<'PY' > /tmp/layer1_outputs.list
import json, os
idx = json.load(open("krr_outputs/INDEX.json"))
files = []
# Indexed law files (.json + .jsonld both)
for entry in idx["laws"]:
    for f in entry.get("files", []):
        p = f"krr_outputs/{f}"
        if os.path.exists(p):
            files.append(p)
# All KOV peep files
import glob
files += glob.glob("krr_outputs/regulations/kov/**/*_peep.json", recursive=True)
files = [f for f in files if not f.endswith("REGULATIONS_KOV_INDEX.json")]
# State regulations
files += glob.glob("krr_outputs/regulations/riik/*_peep.json")
# Generated registry outputs
files += [
    "krr_outputs/municipalities_peep.json",
    "krr_outputs/issuers_kov_peep.json",
]
print("\n".join(sorted(set(files))))
PY

xargs -a /tmp/layer1_outputs.list sha256sum > /tmp/before.sha256

python scripts/enrich_kov_layer1.py

xargs -a /tmp/layer1_outputs.list sha256sum > /tmp/after.sha256
diff /tmp/before.sha256 /tmp/after.sha256
```

Expected: `diff` is empty (no output, exit 0). Any line of diff
output means the second run mutated a file → enrichment is not
idempotent.

**Option B — commit baseline first, then re-run:**

If you've already done Step 7 (committed the generated outputs), just
run the orchestrator again and check that the working tree stays
clean:

```bash
python scripts/enrich_kov_layer1.py
git status --short
```

Expected: no `M` entries. If anything is modified, that's a second-run
mutation = idempotency bug.

- [ ] **Step 6: Run full SHACL validation against the integrated graph**

Run:
```bash
python -c "
import json, glob, rdflib, pyshacl
g = rdflib.Graph()
for f in glob.glob('krr_outputs/municipalities_peep.json') + glob.glob('krr_outputs/issuers_kov_peep.json'):
    g.parse(f, format='json-ld')
shapes = rdflib.Graph().parse('shacl/estonian_legal_shapes.ttl', format='turtle')
ok, _, msg = pyshacl.validate(g, shacl_graph=shapes, inference='rdfs')
print('PASS' if ok else 'FAIL'); print(msg if not ok else '')
"
```
Expected: `PASS`.

- [ ] **Step 7: Commit the generated outputs**

Drive the staging from the resolved INDEX path list so that law files
ending in `.jsonld` (which exist in INDEX) are not missed, and stage
the state regulation directory explicitly so the new `estleg:Act`
stamps actually ship:

```bash
# 1. KOV outputs + new registry files
git add krr_outputs/municipalities_peep.json krr_outputs/issuers_kov_peep.json
git add krr_outputs/regulations/kov

# 2. State regulations (all 3,813 files gained estleg:Act)
git add krr_outputs/regulations/riik

# 3. Law files — driven by INDEX so .jsonld and .json are both included
python3 - <<'PY'
import json, subprocess, os
idx = json.load(open("krr_outputs/INDEX.json"))
paths = [
    f"krr_outputs/{f}"
    for entry in idx["laws"] for f in entry.get("files", [])
    if os.path.exists(f"krr_outputs/{f}")
]
# git add in chunks so the command line stays well under typical limits
chunk = 500
for i in range(0, len(paths), chunk):
    subprocess.check_call(["git", "add", "--"] + paths[i:i + chunk])
print(f"staged {len(paths)} indexed law files")
PY

git status --short | head -20
git commit -m "Generate KOV layer 1 enrichment outputs"
```

This commit will be large (~15k files). That's expected: 11,059 KOV
acts + 3,813 state regs + ~652 indexed law files + 2 new registry
outputs.

---

## Task 15: Documentation updates

**Files:**
- Modify: `docs/SCHEMA_REFERENCE.md`
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/REGULATIONS_INTEGRATION_PLAN.md`

- [ ] **Step 1: Add KOV entity model section to `docs/SCHEMA_REFERENCE.md`**

Append a new section at the end of the file:

````markdown
## KOV Entity Model (Layer 1)

The KOV (kohaliku omavalitsuse) layer adds a Municipality + Issuer entity
model so every municipal regulation and provision is queryable by
territorial unit and issuing body.

### Class hierarchy

- `estleg:Act` — top-level "any enacted Estonian legal act"
  - `estleg:Law` — statute (seadus); stamped onto every existing law node
  - `estleg:NationalRegulation`
    - `estleg:GovernmentRegulation`
    - `estleg:MinisterialRegulation`
  - `estleg:MunicipalRegulation`
- `estleg:Municipality` — territorial KOV unit (top-level)
- `estleg:Issuer` — `rdfs:subClassOf estleg:Institution`
- `estleg:KovProvision` — `rdfs:subClassOf estleg:LegalProvision`
- `estleg:Citation` — reified preamble citation (Layer 2)
- `estleg:Similarity` — reified act-level similarity link (Layer 3)

### Properties

| Property | Domain | Range | Purpose |
| --- | --- | --- | --- |
| `estleg:enactedBy` | `MunicipalRegulation`, `KovProvision` | `Issuer` | Which body enacted this |
| `estleg:enactedByMunicipality` | `MunicipalRegulation`, `KovProvision` | `Municipality` | Which KOV unit |
| `estleg:titleNormalized` | `MunicipalRegulation` | `xsd:string` | Lowercased, transliterated, prefix-stripped title |
| `estleg:partOfAct` | `KovProvision` | `Act` | IRI link from provision to parent act |
| `estleg:ehakCode` | `Municipality` | `xsd:string` | Four-digit EHAK code |
| `estleg:county` | `Municipality` | `xsd:string` | Maakond |
| `estleg:bodyType` | `Issuer` | `xsd:string` | `"volikogu"` \| `"valitsus"` |
| `estleg:currentMunicipality` | `Issuer` | `Municipality` | Today's successor for legacy issuers |
| `estleg:historicalMunicipalityName` | `Issuer` | `xsd:string` | Issuing-time municipality name |
| `estleg:mappingSource` | `Issuer` | `xsd:string` | How the mapping was derived |
| `estleg:mappingEvidence` | `Issuer` | `xsd:string` | Source citation for curated mappings |

### IRI patterns

- `estleg:Municipality_EHAK_<4-digit>` — Municipality
- `estleg:Issuer_<slug>` — Issuer; slug matches the directory name under `krr_outputs/regulations/kov/`

### Generated files

- `krr_outputs/municipalities_peep.json` — 79 Municipality nodes
- `krr_outputs/issuers_kov_peep.json` — 357 Issuer nodes

### Direct-typing in enriched data

Every act node carries `estleg:Act` rdf:type **directly**, in
addition to its specific subtype (`Law`, `MunicipalRegulation`,
`NationalRegulation`, etc.). Same pattern as `KovProvision` on
provisions. This keeps the data self-describing and means SHACL
constraints on `estleg:Act` (e.g. `partOfAct`'s range) work without
requiring RDFS subclass inference at validation time.

### SHACL shapes

`shacl/estonian_legal_shapes.ttl` enforces required fields and IRI
patterns on `Municipality`, `Issuer`, `MunicipalRegulation` (Layer-1
fields), and `KovProvision`. Layer 2 will add a `Citation` shape;
Layer 3 will add a `Similarity` shape.

### Example queries

Find all Tallinn-issued regulations:

```sparql
SELECT ?reg ?title WHERE {
  ?reg a estleg:MunicipalRegulation ;
       estleg:enactedByMunicipality estleg:Municipality_EHAK_0784 ;
       rdfs:label ?title .
}
```

Compare a normalized title across municipalities:

```sparql
SELECT ?mun ?reg WHERE {
  ?reg a estleg:MunicipalRegulation ;
       estleg:titleNormalized "jaatmehoolduseeskiri" ;
       estleg:enactedByMunicipality ?mun .
}
```
````

- [ ] **Step 2: Update `README.md`**

Find the section that lists the KOV layer (search for "KOV" or "11,059"). Update the description to reflect Layer 1's "first-class entity model" status. Add a brief code example pointing to `docs/SCHEMA_REFERENCE.md#kov-entity-model-layer-1`.

- [ ] **Step 3: Update `CHANGELOG.md`**

Add an entry at the top under an appropriate version heading:

```markdown
## [Unreleased]

### Added
- KOV integration Layer 1: Municipality + Issuer entity model
  (`estleg:Municipality`, `estleg:Issuer`, `estleg:KovProvision`,
  `estleg:Act`, `estleg:Law` classes; 79 Municipality nodes; 357
  Issuer nodes; per-act `enactedBy`/`enactedByMunicipality`/
  `titleNormalized` enrichment; per-provision
  `KovProvision`/`enactedBy`/`enactedByMunicipality`/`partOfAct`
  enrichment; `estleg:Law` rdf:type stamped onto all existing law
  nodes). See `docs/superpowers/specs/2026-05-02-kov-integration-design.md`.
```

- [ ] **Step 4: Update `docs/REGULATIONS_INTEGRATION_PLAN.md`**

Add a status note near the top:

```markdown
> **Status (2026-05-02):** KOV integration Phase 3 (Layer 1 — Municipality
> + Issuer entity model) implemented. Layers 2 (pipelines) and 3
> (similarity) follow as separate PRs. See
> `docs/superpowers/specs/2026-05-02-kov-integration-design.md` and
> `docs/superpowers/plans/2026-05-02-kov-integration-layer1.md`.
```

- [ ] **Step 5: Commit**

```bash
git add docs/SCHEMA_REFERENCE.md README.md CHANGELOG.md docs/REGULATIONS_INTEGRATION_PLAN.md
git commit -m "Document KOV integration layer 1"
```

---

## Task 16: Final verification + branch + PR

**Files:**
- (no source changes; this task validates the cumulative work)

- [ ] **Step 1: Run the full pytest suite**

Run: `pytest -v`
Expected: all existing tests still pass plus the new tests added in Tasks 1–13. The total count should be the previous count + ~25 new tests.

- [ ] **Step 2: Run validate_all.py**

Run: `python scripts/validate_all.py`
Expected: zero exit.

- [ ] **Step 3: Run full SHACL validation across the complete file set**

Save this script to `scripts/shacl_validate_all.py` and run it. It
loads the same complete file set as the staging command (every KOV
act, every state regulation, every indexed law file `.json` /
`.jsonld`, plus the two new registry outputs) and runs pyshacl over
the union graph. Expect a multi-minute run.

```python
"""Full SHACL validation across the Layer 1 output corpus."""
from __future__ import annotations

import glob
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
KRR = REPO / "krr_outputs"
SHAPES = REPO / "shacl" / "estonian_legal_shapes.ttl"


def collect_files() -> list[Path]:
    out: list[Path] = [
        KRR / "municipalities_peep.json",
        KRR / "issuers_kov_peep.json",
    ]
    # Indexed law files (covers .json and .jsonld)
    idx = json.load(open(KRR / "INDEX.json"))
    for entry in idx["laws"]:
        for f in entry.get("files", []):
            p = KRR / f
            if p.exists():
                out.append(p)
    # All KOV acts (recursive)
    out += [
        Path(p)
        for p in glob.glob(str(KRR / "regulations/kov/**/*_peep.json"),
                            recursive=True)
        if not p.endswith("REGULATIONS_KOV_INDEX.json")
    ]
    # State regulations
    out += [Path(p) for p in glob.glob(str(KRR / "regulations/riik/*_peep.json"))]
    return sorted(set(out))


def main() -> int:
    import rdflib
    import pyshacl

    files = collect_files()
    print(f"Loading {len(files)} files into a single graph...")
    g = rdflib.Graph()
    parse_failures: list[tuple[str, str]] = []
    for i, f in enumerate(files):
        if i % 1000 == 0:
            print(f"  {i}/{len(files)}")
        try:
            g.parse(str(f), format="json-ld")
        except Exception as exc:
            parse_failures.append((str(f), str(exc)))

    if parse_failures:
        print(f"\nPARSE FAILURES: {len(parse_failures)}")
        for f, exc in parse_failures[:10]:
            print(f"  {f}: {exc}")
        return 2

    print(f"\nLoaded {len(g)} triples. Running pyshacl...")
    shapes = rdflib.Graph().parse(str(SHAPES), format="turtle")
    ok, _, msg = pyshacl.validate(g, shacl_graph=shapes, inference="rdfs")
    print("SHACL", "PASS" if ok else "FAIL")
    if not ok:
        print(msg)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
```

Then run:

```bash
python scripts/shacl_validate_all.py
```

Expected: `SHACL PASS`. Wall time: typically 5–15 minutes depending
on hardware.

If RAM is constrained, the alternative is to validate per-file
in a loop with the shapes graph reused — slower wall time but
constant memory:

```python
ok_count = fail_count = 0
for f in collect_files():
    g = rdflib.Graph()
    g.parse(str(f), format="json-ld")
    ok, _, msg = pyshacl.validate(g, shacl_graph=shapes, inference="rdfs")
    if ok:
        ok_count += 1
    else:
        fail_count += 1
        print(f"FAIL {f}: {msg}")
print(f"\n{ok_count} pass, {fail_count} fail")
```

Expected: `0 fail`.

- [ ] **Step 4: Verify Gate A acceptance criteria**

Save this verification script to `scripts/verify_layer1.py` and run it.
Tick the boxes below only after the script reports each as `OK`. Do
**not** pre-tick.

```python
"""Layer 1 acceptance verification. Exit 0 = all checks pass."""
from __future__ import annotations

import glob
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
KRR = REPO / "krr_outputs"


def _load(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _act_node(doc, type_iri):
    for n in doc.get("@graph", []):
        types = n.get("@type") or []
        if isinstance(types, str):
            types = [types]
        if type_iri in types:
            return n
    return None


def check_issuer_count():
    issuers = _load(REPO / "data" / "ehak" / "issuers.json")
    expected = 357
    return len(issuers) == expected, f"{len(issuers)}/{expected}"


def check_kov_acts():
    fields = ("estleg:enactedBy", "estleg:enactedByMunicipality",
              "estleg:titleNormalized")
    types_required = ("estleg:Act", "estleg:MunicipalRegulation")
    files = [
        f for f in glob.glob(str(KRR / "regulations/kov/**/*_peep.json"),
                              recursive=True)
        if not f.endswith("REGULATIONS_KOV_INDEX.json")
    ]
    ok = 0
    for f in files:
        doc = _load(f)
        act = _act_node(doc, "estleg:MunicipalRegulation")
        if act is None:
            continue
        types = act.get("@type") or []
        if all(k in act for k in fields) and all(t in types for t in types_required):
            ok += 1
    return ok == len(files), f"{ok}/{len(files)}"


def check_kov_provisions():
    """Verify each KOV provision is fully enriched AND its partOfAct
    points at the same file's MunicipalRegulation node — not just any
    IRI. A bug that wrote a stale or unrelated act IRI would be caught
    here, not just at SHACL.
    """
    fields = ("estleg:enactedBy", "estleg:enactedByMunicipality",
              "estleg:partOfAct")
    files = [
        f for f in glob.glob(str(KRR / "regulations/kov/**/*_peep.json"),
                              recursive=True)
        if not f.endswith("REGULATIONS_KOV_INDEX.json")
    ]
    ok = 0
    total = 0
    for f in files:
        doc = _load(f)
        # Locate the file's exactly-one MunicipalRegulation @id
        act_iri = None
        for n in doc.get("@graph", []):
            types = n.get("@type") or []
            if isinstance(types, str):
                types = [types]
            if "estleg:MunicipalRegulation" in types:
                if act_iri is not None:
                    act_iri = "<MULTIPLE>"  # invalidate
                    break
                act_iri = n.get("@id")
        for n in doc.get("@graph", []):
            if "estleg:paragrahv" not in n:
                continue
            total += 1
            types = n.get("@type") or []
            if "estleg:KovProvision" not in types:
                continue
            if not all(k in n for k in fields):
                continue
            link = n.get("estleg:partOfAct") or {}
            if link.get("@id") != act_iri:
                continue
            ok += 1
    return ok == total, f"{ok}/{total}"


def check_laws():
    idx = _load(KRR / "INDEX.json")
    paths = [
        KRR / f
        for entry in idx["laws"] for f in entry.get("files", [])
        if os.path.exists(KRR / f)
    ]
    ok = 0
    for p in paths:
        doc = _load(p)
        n = _act_node(doc, "owl:Ontology")
        if n is None:
            continue
        types = n.get("@type") or []
        if "estleg:Law" in types and "estleg:Act" in types:
            ok += 1
    return ok == len(paths), f"{ok}/{len(paths)}"


def check_state_regulations():
    files = sorted(glob.glob(str(KRR / "regulations/riik/*_peep.json")))
    ok = 0
    for f in files:
        doc = _load(f)
        for n in doc.get("@graph", []):
            types = n.get("@type") or []
            if isinstance(types, str):
                types = [types]
            if "owl:Ontology" not in types:
                continue
            if any(t.endswith("Regulation") and t.startswith("estleg:")
                   for t in types):
                if "estleg:Act" in types:
                    ok += 1
                break
    return ok == len(files), f"{ok}/{len(files)}"


CHECKS = [
    ("issuer count = 357", check_issuer_count),
    ("KOV acts enriched", check_kov_acts),
    ("KOV provisions enriched", check_kov_provisions),
    ("indexed laws stamped (Law + Act)", check_laws),
    ("state regulations stamped (Act)", check_state_regulations),
]


def main():
    fail = 0
    for label, fn in CHECKS:
        ok, detail = fn()
        status = "OK  " if ok else "FAIL"
        print(f"  [{status}] {label}: {detail}")
        if not ok:
            fail += 1
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
```

Then run:

```bash
python scripts/verify_layer1.py
```

- [ ] `python scripts/validate_all.py` exits 0
- [ ] SHACL passes (Step 6 below)
- [ ] `verify_layer1.py` reports `[OK]` for issuer count = 357
- [ ] `verify_layer1.py` reports `[OK]` for KOV acts enriched (`11059/11059`)
- [ ] `verify_layer1.py` reports `[OK]` for KOV provisions enriched (`116708/116708`)
- [ ] `verify_layer1.py` reports `[OK]` for indexed laws stamped — count matches the orchestrator's "Stamping estleg:Law on N law files" output (the missing-INDEX entries are excluded automatically)
- [ ] `verify_layer1.py` reports `[OK]` for state regulations stamped (`3813/3813` or whatever count the orchestrator printed)

If any check fails, fix the underlying issue before opening the PR. Do
not commit pre-ticked checkboxes.

- [ ] **Step 5: Push and open the PR**

```bash
git push -u origin <branch-name>
gh pr create --title "KOV integration Layer 1 — Municipality + Issuer entity model" --body "$(cat <<'EOF'
## Summary
- Builds the Municipality + Issuer entity layer on top of the existing 11,059 KOV regulation files.
- Adds `estleg:Act` / `estleg:Law` parent class hierarchy; stamps `estleg:Law` onto every existing law act node.
- Every KOV act and provision now carries Issuer + Municipality links, plus `titleNormalized` for Layer 3 bucketing.
- 79 Municipality nodes + 357 Issuer nodes generated under `krr_outputs/`.

Layer 1 of the three-layer plan in `docs/superpowers/specs/2026-05-02-kov-integration-design.md`.
Implementation plan: `docs/superpowers/plans/2026-05-02-kov-integration-layer1.md`.

## Test plan
- [ ] `pytest -v` passes
- [ ] `python scripts/validate_all.py` exits 0
- [ ] SHACL passes for all new shapes
- [ ] `python scripts/enrich_kov_layer1.py` is idempotent on a clean tree
- [ ] Spot-check a Tallinn KOV file: `enactedBy` resolves to `Issuer_tallinna_linnavolikogu`, `enactedByMunicipality` resolves to `Municipality_EHAK_0784`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-Review Checklist (run at the end)

After all tasks complete, verify:

- [ ] **Spec coverage.** Every Layer 1 requirement in `docs/superpowers/specs/2026-05-02-kov-integration-design.md` (sections "Layer 1 — Municipality + Issuer entity model" and "Layer 1 tests" and the Gate A criteria) maps to a task above.
- [ ] **No placeholders.** Search this plan for "TODO", "TBD", "implement later" — none should remain.
- [ ] **Type consistency.** `IssuerEntry` fields used in `enrich_kov_act_file` (Task 11) match the keys produced by `build_issuer_registry` (Task 5). `Municipality` typed-dict shape stays consistent across Tasks 1, 3, 9.
- [ ] **All 25 tests added.** Tasks 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13 each add a test class. Total new tests should be ≥25 (Layer 1 spec target was ~14, but cross-task work pushed it higher).

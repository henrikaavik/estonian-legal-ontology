"""Tests for scripts/extract_institutional_competence.py — Layer 2c PR #2."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pytest


def _iter_kov_inclusive(*args, **kwargs):
    """Wrapper that forces include_kov=True regardless of caller args.

    Tasks 2-5 are scheduled BEFORE the unpin in Task 6, so production
    main() still calls iter_peep_files(include_kov=False) at this
    point. Tests that stage KOV fixtures must monkeypatch
    `mod.iter_peep_files` with this wrapper so KOV-staged peeps are
    visible regardless of whether the production unpin has landed yet.
    """
    from estleg_common import iter_peep_files as _real_iter
    kwargs.pop("include_kov", None)
    return _real_iter(include_kov=True, **kwargs)


class TestKovBodyWordDetection:
    """The new GENERIC_PATTERNS entries detect KOV body words and
    _canonical_body_slug normalises them across Estonian case
    inflections. These tests exercise the regex + canonicalizer as
    a unit (the regex match feeds directly into _canonical_body_slug
    in the production wiring)."""

    @pytest.mark.parametrize("text,expected", [
        ("linnavolikogu", "linnavolikogu"),
        ("linnavolikogus", "linnavolikogu"),
        ("linnavolikogule", "linnavolikogu"),
        ("linnavolikogusse", "linnavolikogu"),
    ])
    def test_linnavolikogu_matches_basic_form(self, text, expected):
        from extract_institutional_competence import (
            _canonical_body_slug,
            GENERIC_PATTERNS,
        )
        match = None
        for pat, _label, _itype in GENERIC_PATTERNS:
            m = pat.search(f"see {text} kehtestab korra")
            if m and m.group(0).lower() == text.lower():
                match = m
                break
        assert match is not None, f"no GENERIC_PATTERNS regex matched {text!r}"
        slug = _canonical_body_slug(match.group(0))
        assert slug == expected

    @pytest.mark.parametrize("text,expected", [
        ("vallavalitsus", "vallavalitsus"),
        ("vallavalitsuse", "vallavalitsus"),
        ("vallavalitsust", "vallavalitsus"),
        ("vallavalitsusele", "vallavalitsus"),
        ("vallavalitsuses", "vallavalitsus"),
        ("vallavalitsusesse", "vallavalitsus"),
        ("vallavalitsusse", "vallavalitsus"),
        ("linnavalitsus", "linnavalitsus"),
        ("linnavalitsuse", "linnavalitsus"),
        ("linnavalitsust", "linnavalitsus"),
        ("linnavalitsusele", "linnavalitsus"),
        ("linnavalitsuses", "linnavalitsus"),
        ("linnavalitsusesse", "linnavalitsus"),
        ("linnavalitsusse", "linnavalitsus"),
    ])
    def test_vallavalitsus_inflections(self, text, expected):
        from extract_institutional_competence import (
            _canonical_body_slug,
            GENERIC_PATTERNS,
        )
        match = None
        for pat, _label, _itype in GENERIC_PATTERNS:
            m = pat.search(f"see {text} otsustab")
            if m and m.group(0).lower() == text.lower():
                match = m
                break
        assert match is not None, f"no regex matched {text!r}"
        slug = _canonical_body_slug(match.group(0))
        assert slug == expected

    @pytest.mark.parametrize("text,expected", [
        # Adessive (-l): "X-l on õigus" pattern
        ("vallavalitsusel", "vallavalitsus"),
        ("linnavalitsusel", "linnavalitsus"),
        ("vallavolikogul", "vallavolikogu"),
        ("linnavolikogul", "linnavolikogu"),
        ("alevivolikogul", "alevivolikogu"),
        # Ablative (-lt): "X-lt küsib"
        ("vallavalitsuselt", "vallavalitsus"),
        ("linnavolikogult", "linnavolikogu"),
        # Elative (-st): "X-st kostis"
        ("vallavalitsusest", "vallavalitsus"),
        ("linnavolikogust", "linnavolikogu"),
        # Comitative (-ga): "X-ga koos"
        ("vallavalitsusega", "vallavalitsus"),
        ("linnavolikoguga", "linnavolikogu"),
    ])
    def test_extended_case_forms(self, text, expected):
        """Adessive/ablative/elative/comitative forms — common in
        legal text ("vallavalitsusel on õigus...", "linnavolikogult
        küsib", etc.). Layer 2c PR #2 review surfaced 29 corpus
        cases of "vallavalitsusel on õigus" alone."""
        from extract_institutional_competence import (
            _canonical_body_slug,
            GENERIC_PATTERNS,
        )
        match = None
        for pat, _label, _itype in GENERIC_PATTERNS:
            m = pat.search(f"see {text} on õigus midagi teha")
            if m and m.group(0).lower() == text.lower():
                match = m
                break
        assert match is not None, f"no GENERIC_PATTERNS regex matched {text!r}"
        slug = _canonical_body_slug(match.group(0))
        assert slug == expected

    def test_named_institutions_still_win(self):
        """Adding the new patterns must not override existing
        named-institution detection. detect_institutions returns
        Riigikohus first when both 'Riigikohus' and 'linnavolikogu'
        appear in the same provision text."""
        from extract_institutional_competence import detect_institutions
        results = detect_institutions(
            "Riigikohus otsustab; linnavolikogu kehtestab korra"
        )
        slugs = {iri_suffix for _name, iri_suffix, _t in results}
        assert "riigikohus" in slugs


class TestResolveKovAuthority:
    """Unit tests for the 3-priority resolver and the
    _is_path3_case predicate."""

    @pytest.fixture
    def reg(self):
        """Synthetic issuer registry: place name → (label, mun, body_type)."""
        return {
            "estleg:Issuer_abja_vallavolikogu": (
                "abja vallavolikogu",
                "estleg:Municipality_mulgi", "volikogu",
            ),
            "estleg:Issuer_abja_vallavalitsus": (
                "abja vallavalitsus",
                "estleg:Municipality_mulgi", "valitsus",
            ),
            "estleg:Issuer_xyz_vallavolikogu": (
                "xyz vallavolikogu",
                "estleg:Municipality_xyz", "volikogu",
            ),
            "estleg:Issuer_other_vallavalitsus": (
                "other vallavalitsus",
                "estleg:Municipality_xyz", "valitsus",
            ),
            "estleg:Issuer_elva_linnavalitsus": (
                "elva linnavalitsus",
                "estleg:Municipality_elva", "valitsus",
            ),
            "estleg:Issuer_elva_vallavolikogu": (
                "elva vallavolikogu",
                "estleg:Municipality_elva", "volikogu",
            ),
            "estleg:Issuer_singleton_vallavolikogu": (
                "singleton vallavolikogu",
                "estleg:Municipality_singleton", "volikogu",
            ),
            "estleg:Issuer_dup1_vallavolikogu": (
                "dup1 vallavolikogu",
                "estleg:Municipality_dup", "volikogu",
            ),
            "estleg:Issuer_dup2_vallavolikogu": (
                "dup2 vallavolikogu",
                "estleg:Municipality_dup", "volikogu",
            ),
        }

    def test_path1_source_issuer_same_body_resolves_directly(self, reg):
        from extract_institutional_competence import _resolve_kov_authority
        result = _resolve_kov_authority(
            body_slug="vallavolikogu",
            source_municipality="estleg:Municipality_mulgi",
            source_issuer="estleg:Issuer_abja_vallavolikogu",
            issuer_registry=reg,
        )
        assert result == "estleg:Issuer_abja_vallavolikogu"

    def test_path2_paired_body_slug_swap(self, reg):
        from extract_institutional_competence import _resolve_kov_authority
        result = _resolve_kov_authority(
            body_slug="vallavalitsus",
            source_municipality="estleg:Municipality_mulgi",
            source_issuer="estleg:Issuer_abja_vallavolikogu",
            issuer_registry=reg,
        )
        assert result == "estleg:Issuer_abja_vallavalitsus"

    def test_path1_suffix_mismatch_abstains(self, reg):
        """Source act enacted by a vallavolikogu; detected
        linnavolikogu — same body type but slug suffix mismatch.
        Resolver abstains."""
        from extract_institutional_competence import _resolve_kov_authority
        result = _resolve_kov_authority(
            body_slug="linnavolikogu",
            source_municipality="estleg:Municipality_mulgi",
            source_issuer="estleg:Issuer_abja_vallavolikogu",
            issuer_registry=reg,
        )
        assert result is None

    def test_path2_paired_body_missing_abstains_without_path3(self, reg):
        """Source act enacted by Issuer_xyz_vallavolikogu; detected
        body word vallavalitsus (SAME municipal family). Registry
        has NO Issuer_xyz_vallavalitsus but does have
        Issuer_other_vallavalitsus in the same modern municipality.
        Resolver MUST abstain — Path 3 must NOT bind to a conflated
        historical issuer."""
        from extract_institutional_competence import _resolve_kov_authority
        result = _resolve_kov_authority(
            body_slug="vallavalitsus",
            source_municipality="estleg:Municipality_xyz",
            source_issuer="estleg:Issuer_xyz_vallavolikogu",
            issuer_registry=reg,
        )
        assert result is None

    def test_path2_cross_family_linna_to_valla_abstains(self, reg):
        """Source act enacted by Issuer_elva_linnavalitsus (urban
        executive); detected vallavolikogu (rural council). Even
        though Issuer_elva_vallavolikogu IS in the registry, the
        cross-family swap (linna* → valla*) must be rejected."""
        from extract_institutional_competence import _resolve_kov_authority
        result = _resolve_kov_authority(
            body_slug="vallavolikogu",
            source_municipality="estleg:Municipality_elva",
            source_issuer="estleg:Issuer_elva_linnavalitsus",
            issuer_registry=reg,
        )
        assert result is None

    def test_path2_cross_family_valla_to_linna_abstains(self, reg):
        """Mirror of the prior test in the other direction."""
        from extract_institutional_competence import _resolve_kov_authority
        result = _resolve_kov_authority(
            body_slug="linnavolikogu",
            source_municipality="estleg:Municipality_mulgi",
            source_issuer="estleg:Issuer_abja_vallavalitsus",
            issuer_registry=reg,
        )
        assert result is None

    def test_municipality_mismatch_falls_through_to_path3(self, reg):
        """Layer 1 inconsistency: source act says
        Municipality_singleton but source_issuer's registry entry
        says Municipality_mulgi. Path 1+2 abstain; Path 3 then runs
        against the act's stated municipality."""
        from extract_institutional_competence import _resolve_kov_authority
        reg_with_bad = dict(reg)
        reg_with_bad["estleg:Issuer_consistent_act_issuer"] = (
            "consistent act issuer",
            "estleg:Municipality_mulgi", "volikogu",
        )
        result = _resolve_kov_authority(
            body_slug="vallavolikogu",
            source_municipality="estleg:Municipality_singleton",
            source_issuer="estleg:Issuer_consistent_act_issuer",
            issuer_registry=reg_with_bad,
        )
        assert result == "estleg:Issuer_singleton_vallavolikogu"

    def test_municipality_mismatch_abstains_when_path3_also_empty(self, reg):
        """Same Layer 1 inconsistency, but the act's stated
        municipality has zero suffix-compatible matches. Resolver
        abstains entirely."""
        from extract_institutional_competence import _resolve_kov_authority
        reg_with_bad = dict(reg)
        reg_with_bad["estleg:Issuer_consistent_act_issuer"] = (
            "consistent act issuer",
            "estleg:Municipality_mulgi", "volikogu",
        )
        result = _resolve_kov_authority(
            body_slug="alevivolikogu",
            source_municipality="estleg:Municipality_singleton",
            source_issuer="estleg:Issuer_consistent_act_issuer",
            issuer_registry=reg_with_bad,
        )
        assert result is None

    def test_path3_unique_fallback_resolves(self, reg):
        """source_issuer is None; Path 3 finds the unique
        suffix-compatible issuer in the source municipality."""
        from extract_institutional_competence import _resolve_kov_authority
        result = _resolve_kov_authority(
            body_slug="vallavolikogu",
            source_municipality="estleg:Municipality_singleton",
            source_issuer=None,
            issuer_registry=reg,
        )
        assert result == "estleg:Issuer_singleton_vallavolikogu"

    def test_path3_ambiguous_returns_none(self, reg):
        """Path 3 finds two suffix-compatible issuers. Abstain."""
        from extract_institutional_competence import _resolve_kov_authority
        result = _resolve_kov_authority(
            body_slug="vallavolikogu",
            source_municipality="estleg:Municipality_dup",
            source_issuer=None,
            issuer_registry=reg,
        )
        assert result is None

    def test_no_municipality_returns_none(self, reg):
        """source_municipality=None — every path requires it; abstain."""
        from extract_institutional_competence import _resolve_kov_authority
        result = _resolve_kov_authority(
            body_slug="vallavolikogu",
            source_municipality=None,
            source_issuer="estleg:Issuer_abja_vallavolikogu",
            issuer_registry=reg,
        )
        assert result is None

    @pytest.mark.parametrize("source_issuer,source_municipality,expected", [
        (None, "estleg:Municipality_singleton", True),
        ("estleg:Issuer_unknown_to_registry",
         "estleg:Municipality_singleton", True),
        ("estleg:Issuer_abja_vallavolikogu",
         "estleg:Municipality_mulgi", False),
        ("estleg:Issuer_abja_vallavolikogu",
         "estleg:Municipality_singleton", True),
        (None, None, False),
    ])
    def test_is_path3_case_predicate(
        self, reg, source_issuer, source_municipality, expected
    ):
        from extract_institutional_competence import _is_path3_case
        assert _is_path3_case(
            source_issuer=source_issuer,
            source_municipality=source_municipality,
            issuer_registry=reg,
        ) is expected

    def test_is_path3_case_stays_in_sync_with_resolver(self, reg):
        """Parity test: when _is_path3_case returns False AND the
        resolver returns a non-None IRI, the IRI must come from
        Path 1 or Path 2 (source_issuer or its slug-swap output);
        when _is_path3_case returns True AND the resolver returns
        a non-None IRI, the IRI must come from Path 3 (registry
        scan in source_municipality)."""
        from extract_institutional_competence import (
            _resolve_kov_authority,
            _is_path3_case,
            _swap_issuer_body_suffix,
        )
        cases = [
            ("estleg:Issuer_abja_vallavolikogu",
             "estleg:Municipality_mulgi", "vallavolikogu", reg),
            ("estleg:Issuer_abja_vallavolikogu",
             "estleg:Municipality_mulgi", "vallavalitsus", reg),
            (None, "estleg:Municipality_singleton", "vallavolikogu", reg),
            ("estleg:Issuer_unknown",
             "estleg:Municipality_singleton", "vallavolikogu", reg),
        ]
        for source_issuer, source_mun, body_slug, registry in cases:
            result = _resolve_kov_authority(
                body_slug=body_slug,
                source_municipality=source_mun,
                source_issuer=source_issuer,
                issuer_registry=registry,
            )
            is_p3 = _is_path3_case(
                source_issuer=source_issuer,
                source_municipality=source_mun,
                issuer_registry=registry,
            )
            if result is None:
                continue
            if is_p3:
                assert result != source_issuer
                if source_issuer is not None:
                    swapped = _swap_issuer_body_suffix(source_issuer, body_slug)
                    assert result != swapped
            else:
                if result == source_issuer:
                    continue
                swapped = _swap_issuer_body_suffix(source_issuer or "", body_slug)
                assert result == swapped, (
                    f"non-Path3 result {result!r} doesn't match Path 1 or 2"
                )


class TestExtractCompetenceWithIssuerBinding:
    """End-to-end via main(). Same _iter_kov_inclusive monkeypatch
    pattern as Layer 2c PR #1's tests."""

    @pytest.fixture
    def issuers_registry_file(self, tmp_path):
        """Stage a minimal issuers_kov_peep.json in tmp_path/krr_outputs/
        so build_issuer_registry returns a usable mapping."""
        krr = tmp_path / "krr_outputs"
        krr.mkdir(exist_ok=True)
        path = krr / "issuers_kov_peep.json"
        path.write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
                "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
            },
            "@graph": [
                {"@id": "estleg:Issuer_tallinna_linnavolikogu",
                 "@type": ["owl:NamedIndividual", "estleg:Issuer"],
                 "rdfs:label": "Tallinna Linnavolikogu",
                 "estleg:bodyType": "volikogu",
                 "estleg:currentMunicipality": {
                     "@id": "estleg:Municipality_tallinn"}},
                {"@id": "estleg:Issuer_tallinna_linnavalitsus",
                 "@type": ["owl:NamedIndividual", "estleg:Issuer"],
                 "rdfs:label": "Tallinna Linnavalitsus",
                 "estleg:bodyType": "valitsus",
                 "estleg:currentMunicipality": {
                     "@id": "estleg:Municipality_tallinn"}},
            ],
        }), encoding="utf-8")
        return krr

    def test_kov_act_competence_binds_to_issuer(
        self, issuers_registry_file, monkeypatch
    ):
        import extract_institutional_competence as mod
        import estleg_common
        krr = issuers_registry_file
        institutions_dir = krr / "institutions"
        institutions_dir.mkdir(parents=True, exist_ok=True)

        # Stage a Tallinn KOV act with a provision citing linnavolikogu
        kov = krr / "regulations" / "kov" / "tallinna_linnavolikogu"
        kov.mkdir(parents=True)
        peep = kov / "act_peep.json"
        peep.write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:Reg_TLN_X_Map_2024",
                 "@type": ["owl:Ontology", "estleg:Act",
                           "estleg:MunicipalRegulation"],
                 "estleg:enactedBy": {"@id": "estleg:Issuer_tallinna_linnavolikogu"},
                 "estleg:enactedByMunicipality": {
                     "@id": "estleg:Municipality_tallinn"}},
                {"@id": "estleg:Reg_TLN_X_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 1",
                 "estleg:summary":
                     "Linnavolikogu kehtestab maksu määra."},
            ],
        }), encoding="utf-8")

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "INSTIT_DIR", institutions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "iter_peep_files", _iter_kov_inclusive)

        rc = mod.main()
        assert rc in (0, None)

        with open(peep, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        prov = next(n for n in saved["@graph"]
                    if n.get("@id") == "estleg:Reg_TLN_X_Par_1")
        refs = prov.get("estleg:competentAuthority", [])
        if isinstance(refs, dict):
            refs = [refs]
        ids = [r.get("@id") for r in refs]
        assert "estleg:Issuer_tallinna_linnavolikogu" in ids
        assert not any(i and i.endswith("Institution_linnavolikogu") for i in ids)
        assert not (institutions_dir / "institution_linnavolikogu.json").exists()

    def test_state_law_competence_with_kov_body_word_abstains(
        self, issuers_registry_file, monkeypatch
    ):
        """Law-typed peep (no enactedByMunicipality). Detected
        linnavolikogu must NOT bind."""
        import extract_institutional_competence as mod
        import estleg_common
        krr = issuers_registry_file
        institutions_dir = krr / "institutions"
        institutions_dir.mkdir(parents=True, exist_ok=True)
        peep = krr / "kov_seadus_peep.json"
        peep.write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:KOKS_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"]},
                {"@id": "estleg:KOKS_Par_22",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 22",
                 "estleg:summary":
                     "Linnavolikogu kehtestab kohaliku elu küsimuste "
                     "lahendamise korra."},
            ],
        }), encoding="utf-8")

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "INSTIT_DIR", institutions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "iter_peep_files", _iter_kov_inclusive)

        rc = mod.main()
        assert rc in (0, None)

        with open(peep, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        prov = next(n for n in saved["@graph"]
                    if n.get("@id") == "estleg:KOKS_Par_22")
        # After Fix 1: no detections produced authoritative bindings,
        # so neither competentAuthority nor competenceType should be
        # written to the provision.
        assert "estleg:competentAuthority" not in prov
        assert "estleg:competenceType" not in prov
        refs = prov.get("estleg:competentAuthority", [])
        if isinstance(refs, dict):
            refs = [refs]
        ids = [r.get("@id") for r in refs]
        assert not any(i and "linnavolikogu" in (i or "") for i in ids)

    def test_kov_act_with_named_authority_still_emits_institution(
        self, issuers_registry_file, monkeypatch
    ):
        """Named institutions (Riigikohus etc.) follow the existing
        Institution_* path even in KOV peeps."""
        import extract_institutional_competence as mod
        import estleg_common
        krr = issuers_registry_file
        institutions_dir = krr / "institutions"
        institutions_dir.mkdir(parents=True, exist_ok=True)
        kov = krr / "regulations" / "kov" / "tallinna_linnavolikogu"
        kov.mkdir(parents=True)
        peep = kov / "act_peep.json"
        peep.write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:Reg_TLN_Y_Map_2024",
                 "@type": ["owl:Ontology", "estleg:Act",
                           "estleg:MunicipalRegulation"],
                 "estleg:enactedBy": {"@id": "estleg:Issuer_tallinna_linnavolikogu"},
                 "estleg:enactedByMunicipality": {
                     "@id": "estleg:Municipality_tallinn"}},
                {"@id": "estleg:Reg_TLN_Y_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 1",
                 "estleg:summary":
                     "Riigikohus otsustab vaidlused selle määruse alusel."},
            ],
        }), encoding="utf-8")

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "INSTIT_DIR", institutions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "iter_peep_files", _iter_kov_inclusive)

        rc = mod.main()
        assert rc in (0, None)

        with open(peep, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        prov = next(n for n in saved["@graph"]
                    if n.get("@id") == "estleg:Reg_TLN_Y_Par_1")
        refs = prov.get("estleg:competentAuthority", [])
        if isinstance(refs, dict):
            refs = [refs]
        ids = [r.get("@id") for r in refs]
        assert "estleg:Institution_riigikohus" in ids
        assert (institutions_dir / "institution_riigikohus.json").exists()


class TestAuthorityRefDeduplication:
    """A summary with multiple inflected mentions of the same body
    must produce ONE competentAuthority ref, not N."""

    @pytest.fixture
    def issuers_registry_file(self, tmp_path):
        krr = tmp_path / "krr_outputs"
        krr.mkdir()
        path = krr / "issuers_kov_peep.json"
        path.write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
                "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
            },
            "@graph": [
                {"@id": "estleg:Issuer_polva_vallavalitsus",
                 "@type": ["owl:NamedIndividual", "estleg:Issuer"],
                 "rdfs:label": "Põlva Vallavalitsus",
                 "estleg:bodyType": "valitsus",
                 "estleg:currentMunicipality": {
                     "@id": "estleg:Municipality_polva"}},
            ],
        }), encoding="utf-8")
        return krr

    def test_multiple_inflected_mentions_dedupe_to_one(
        self, issuers_registry_file, monkeypatch
    ):
        """Reviewer-cited regression: polva hankekord §1 had three
        identical Issuer_polva_vallavalitsus refs because the summary
        mentioned vallavalitsus three times in different cases."""
        import extract_institutional_competence as mod
        import estleg_common
        krr = issuers_registry_file
        institutions_dir = krr / "institutions"
        institutions_dir.mkdir(parents=True, exist_ok=True)
        kov = krr / "regulations" / "kov" / "polva_vallavalitsus"
        kov.mkdir(parents=True)
        peep = kov / "hankekord_peep.json"
        peep.write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:Reg_Polva_Map_2024",
                 "@type": ["owl:Ontology", "estleg:Act",
                           "estleg:MunicipalRegulation"],
                 "estleg:enactedBy": {"@id": "estleg:Issuer_polva_vallavalitsus"},
                 "estleg:enactedByMunicipality": {
                     "@id": "estleg:Municipality_polva"}},
                {"@id": "estleg:Reg_Polva_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 1",
                 "estleg:summary":
                     "Vallavalitsus kehtestab korra. Vallavalitsusel on "
                     "õigus küsida lisateavet. Vallavalitsuse otsus on "
                     "lõplik."},
            ],
        }), encoding="utf-8")

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "INSTIT_DIR", institutions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "iter_peep_files", _iter_kov_inclusive)

        rc = mod.main()
        assert rc in (0, None)

        with open(peep, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        prov = next(n for n in saved["@graph"]
                    if n.get("@id") == "estleg:Reg_Polva_Par_1")
        refs = prov.get("estleg:competentAuthority", [])
        if isinstance(refs, dict):
            refs = [refs]
        ids = [r.get("@id") for r in refs]
        # Three inflected mentions of the same body → exactly ONE ref.
        assert ids == ["estleg:Issuer_polva_vallavalitsus"], (
            f"expected 1 deduped ref, got {ids}"
        )

    def test_institution_path_dedupes_inst_provisions(
        self, issuers_registry_file, monkeypatch
    ):
        """Same pattern but for state-side Institution_* path: the
        institutions/institution_<slug>.json provision list must not
        contain duplicate (provision, type, law) tuples when the
        same named institution is detected multiple times in one
        summary."""
        import extract_institutional_competence as mod
        import estleg_common
        krr = issuers_registry_file
        institutions_dir = krr / "institutions"
        institutions_dir.mkdir(parents=True, exist_ok=True)
        peep = krr / "state_law_peep.json"
        peep.write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:State_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"]},
                {"@id": "estleg:State_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 1",
                 "estleg:summary":
                     "Riigikohus otsustab vaidlused. Riigikohtu otsus "
                     "on lõplik. Riigikohus võib kaaluda."},
            ],
        }), encoding="utf-8")

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "INSTIT_DIR", institutions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "iter_peep_files", _iter_kov_inclusive)

        rc = mod.main()
        assert rc in (0, None)

        # Provision side: exactly one Institution_riigikohus ref.
        with open(peep, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        prov = next(n for n in saved["@graph"]
                    if n.get("@id") == "estleg:State_Par_1")
        refs = prov.get("estleg:competentAuthority", [])
        if isinstance(refs, dict):
            refs = [refs]
        ids = [r.get("@id") for r in refs]
        assert ids == ["estleg:Institution_riigikohus"], (
            f"expected 1 deduped Institution ref, got {ids}"
        )
        # Institution-side: the provision tuple appears exactly once.
        inst_file = institutions_dir / "institution_riigikohus.json"
        assert inst_file.exists()
        with open(inst_file, "r", encoding="utf-8") as fh:
            inst_doc = json.load(fh)
        # The institution writer groups provisions under
        # estleg:Competence nodes (one per competence type), each
        # with estleg:appliesToProvision. Find State_Par_1 refs
        # across ALL Competence nodes — there should be exactly one.
        state_par_1_count = 0
        for node in inst_doc.get("@graph", []):
            applies = node.get("estleg:appliesToProvision", [])
            if isinstance(applies, dict):
                applies = [applies]
            for p in applies:
                if isinstance(p, dict) and p.get("@id") == "estleg:State_Par_1":
                    state_par_1_count += 1
        assert state_par_1_count <= 1, (
            f"institution file lists provision State_Par_1 "
            f"{state_par_1_count} times (expected 0 or 1)"
        )


class TestCompetenceIdempotency:
    """Idempotency at the peep level: clear → extract → save iff
    either fresh output OR prior output existed."""

    @pytest.fixture
    def issuers_registry_file(self, tmp_path):
        krr = tmp_path / "krr_outputs"
        krr.mkdir()
        path = krr / "issuers_kov_peep.json"
        path.write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
                "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
            },
            "@graph": [
                {"@id": "estleg:Issuer_tallinna_linnavolikogu",
                 "@type": ["owl:NamedIndividual", "estleg:Issuer"],
                 "rdfs:label": "Tallinna Linnavolikogu",
                 "estleg:bodyType": "volikogu",
                 "estleg:currentMunicipality": {
                     "@id": "estleg:Municipality_tallinn"}},
            ],
        }), encoding="utf-8")
        return krr

    def test_stale_competentauthority_cleared_when_text_changes(
        self, issuers_registry_file, monkeypatch
    ):
        import extract_institutional_competence as mod
        import estleg_common
        krr = issuers_registry_file
        institutions_dir = krr / "institutions"
        institutions_dir.mkdir(parents=True, exist_ok=True)

        peep = krr / "stale_peep.json"
        peep.write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:Stale_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"]},
                {"@id": "estleg:Stale_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 1",
                 "estleg:summary": "Käesolev säte kirjeldab üldist eesmärki.",
                 "estleg:competentAuthority": [
                     {"@id": "estleg:Institution_riigikohus"}],
                 "estleg:competenceType": "supervision"},
            ],
        }), encoding="utf-8")
        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "INSTIT_DIR", institutions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "iter_peep_files", _iter_kov_inclusive)

        rc = mod.main()
        assert rc in (0, None)

        with open(peep, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        prov = next(n for n in saved["@graph"]
                    if n.get("@id") == "estleg:Stale_Par_1")
        assert "estleg:competentAuthority" not in prov
        assert "estleg:competenceType" not in prov

    def test_classification_recomputed_from_act_type(
        self, issuers_registry_file, monkeypatch
    ):
        """A peep's enactedByMunicipality flips between runs;
        recomputed resolution reflects the new value, not the
        stale prior emission."""
        import extract_institutional_competence as mod
        import estleg_common
        krr = issuers_registry_file
        institutions_dir = krr / "institutions"
        institutions_dir.mkdir(parents=True, exist_ok=True)
        kov = krr / "regulations" / "kov" / "tallinna_linnavolikogu"
        kov.mkdir(parents=True)
        peep = kov / "act_peep.json"
        peep.write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:Reg_TLN_Z_Map_2024",
                 "@type": ["owl:Ontology", "estleg:Act",
                           "estleg:MunicipalRegulation"],
                 "estleg:enactedBy": {"@id": "estleg:Issuer_tallinna_linnavolikogu"},
                 "estleg:enactedByMunicipality": {
                     "@id": "estleg:Municipality_tallinn"}},
                {"@id": "estleg:Reg_TLN_Z_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 1",
                 "estleg:summary": "Linnavolikogu kehtestab maksu.",
                 "estleg:competentAuthority": [
                     {"@id": "estleg:Institution_some_stale_value"}]},
            ],
        }), encoding="utf-8")
        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "INSTIT_DIR", institutions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "iter_peep_files", _iter_kov_inclusive)

        rc = mod.main()
        assert rc in (0, None)

        with open(peep, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        prov = next(n for n in saved["@graph"]
                    if n.get("@id") == "estleg:Reg_TLN_Z_Par_1")
        refs = prov.get("estleg:competentAuthority", [])
        if isinstance(refs, dict):
            refs = [refs]
        ids = [r.get("@id") for r in refs]
        assert "estleg:Issuer_tallinna_linnavolikogu" in ids
        assert "estleg:Institution_some_stale_value" not in ids

    def test_no_op_when_no_existing_no_fresh(
        self, issuers_registry_file, monkeypatch
    ):
        """Peep with no detections AND no prior triples — mtime
        preserved (no spurious write)."""
        import extract_institutional_competence as mod
        import estleg_common
        krr = issuers_registry_file
        institutions_dir = krr / "institutions"
        institutions_dir.mkdir(parents=True, exist_ok=True)
        peep = krr / "boring_peep.json"
        peep.write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:Boring_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"]},
                {"@id": "estleg:Boring_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 1",
                 "estleg:summary":
                     "Käesolev seadus reguleerib üldisi suhteid."},
            ],
        }), encoding="utf-8")
        before = peep.stat().st_mtime
        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "INSTIT_DIR", institutions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "iter_peep_files", _iter_kov_inclusive)

        rc = mod.main()
        assert rc in (0, None)

        after = peep.stat().st_mtime
        assert before == after, "no-op file must not be re-saved"


class TestStaleInstitutionFileDeletion:
    """End-of-run institution-file pruning. Stale files are unlinked
    only when no per-peep errors occurred."""

    @pytest.fixture
    def issuers_registry_file(self, tmp_path):
        krr = tmp_path / "krr_outputs"
        krr.mkdir()
        path = krr / "issuers_kov_peep.json"
        path.write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
                "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
            },
            "@graph": [
                {"@id": "estleg:Issuer_dummy",
                 "@type": ["owl:NamedIndividual", "estleg:Issuer"],
                 "rdfs:label": "Dummy",
                 "estleg:bodyType": "volikogu",
                 "estleg:currentMunicipality": {
                     "@id": "estleg:Municipality_x"}},
            ],
        }), encoding="utf-8")
        return krr

    def test_stale_institutions_json_deleted_when_no_provisions_cite_it(
        self, issuers_registry_file, monkeypatch
    ):
        import extract_institutional_competence as mod
        import estleg_common
        krr = issuers_registry_file
        institutions_dir = krr / "institutions"
        institutions_dir.mkdir(parents=True, exist_ok=True)

        stale = institutions_dir / "institution_xyz.json"
        stale.write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#"},
            "@graph": [{"@id": "estleg:Institution_xyz",
                        "@type": ["estleg:Institution"]}],
        }), encoding="utf-8")
        peep = krr / "boring_peep.json"
        peep.write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:Boring_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"]},
                {"@id": "estleg:Boring_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 1",
                 "estleg:summary": "Käesolev seadus on üldine."},
            ],
        }), encoding="utf-8")

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "INSTIT_DIR", institutions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "iter_peep_files", _iter_kov_inclusive)

        rc = mod.main()
        assert rc in (0, None)

        assert not stale.exists(), "stale institution file must be unlinked"

    def test_living_institution_files_preserved(
        self, issuers_registry_file, monkeypatch
    ):
        """A pre-existing institutions/institution_riigikohus.json
        AND a peep that triggers Riigikohus detection — the file
        MUST be preserved (NOT deleted via incorrect stem-prefix
        comparison)."""
        import extract_institutional_competence as mod
        import estleg_common
        krr = issuers_registry_file
        institutions_dir = krr / "institutions"
        institutions_dir.mkdir(parents=True, exist_ok=True)

        living = institutions_dir / "institution_riigikohus.json"
        living.write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#"},
            "@graph": [{"@id": "estleg:Institution_riigikohus",
                        "@type": ["estleg:Institution"],
                        "rdfs:label": "old label"}],
        }), encoding="utf-8")
        peep = krr / "active_peep.json"
        peep.write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:Active_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"]},
                {"@id": "estleg:Active_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 1",
                 "estleg:summary": "Riigikohus otsustab vaidlused."},
            ],
        }), encoding="utf-8")

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "INSTIT_DIR", institutions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "iter_peep_files", _iter_kov_inclusive)

        rc = mod.main()
        assert rc in (0, None)

        assert living.exists()

    def test_deletion_skipped_when_per_peep_errors_present(
        self, issuers_registry_file, monkeypatch
    ):
        """When at least one peep returns load_json None or no @graph,
        stale-file deletion is skipped (preserves files whose owning
        peeps failed to load)."""
        import extract_institutional_competence as mod
        import estleg_common
        krr = issuers_registry_file
        institutions_dir = krr / "institutions"
        institutions_dir.mkdir(parents=True, exist_ok=True)

        stale = institutions_dir / "institution_unused.json"
        stale.write_text("{}", encoding="utf-8")
        broken = krr / "broken_peep.json"
        broken.write_text("not json", encoding="utf-8")
        peep = krr / "ok_peep.json"
        peep.write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:OK_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"]},
                {"@id": "estleg:OK_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 1",
                 "estleg:summary": "Üldnorm."},
            ],
        }), encoding="utf-8")

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "INSTIT_DIR", institutions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "iter_peep_files", _iter_kov_inclusive)

        rc = mod.main()
        assert rc in (0, None)

        assert stale.exists()


class TestCompetenceCoverageReport:
    """Coverage report shape + gate behavior."""

    @pytest.fixture
    def issuers_registry_file(self, tmp_path):
        krr = tmp_path / "krr_outputs"
        krr.mkdir()
        path = krr / "issuers_kov_peep.json"
        path.write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
                "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
            },
            "@graph": [
                {"@id": "estleg:Issuer_tallinna_linnavolikogu",
                 "@type": ["owl:NamedIndividual", "estleg:Issuer"],
                 "rdfs:label": "Tallinna Linnavolikogu",
                 "estleg:bodyType": "volikogu",
                 "estleg:currentMunicipality": {
                     "@id": "estleg:Municipality_tallinn"}},
            ],
        }), encoding="utf-8")
        return krr

    def test_coverage_report_written_with_kov_split(
        self, issuers_registry_file, monkeypatch
    ):
        import extract_institutional_competence as mod
        import estleg_common
        krr = issuers_registry_file
        institutions_dir = krr / "institutions"
        reports_dir = krr / "reports" / "kov"
        institutions_dir.mkdir(parents=True)
        reports_dir.mkdir(parents=True)

        kov = krr / "regulations" / "kov" / "tallinna_linnavolikogu"
        kov.mkdir(parents=True)
        (kov / "act_peep.json").write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:Reg_TLN_K_Map_2024",
                 "@type": ["owl:Ontology", "estleg:Act",
                           "estleg:MunicipalRegulation"],
                 "estleg:enactedBy": {"@id": "estleg:Issuer_tallinna_linnavolikogu"},
                 "estleg:enactedByMunicipality": {
                     "@id": "estleg:Municipality_tallinn"}},
                {"@id": "estleg:Reg_TLN_K_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 1",
                 "estleg:summary": "Linnavolikogu kehtestab maksu."},
            ],
        }), encoding="utf-8")
        (krr / "state_peep.json").write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:State_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"]},
                {"@id": "estleg:State_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 1",
                 "estleg:summary": "Riigikohus otsustab vaidlused."},
            ],
        }), encoding="utf-8")

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "INSTIT_DIR", institutions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "iter_peep_files", _iter_kov_inclusive)

        rc = mod.main()
        assert rc == 0

        cov_path = reports_dir / "extract_institutional_competence_coverage.json"
        assert cov_path.exists()
        with open(cov_path, "r", encoding="utf-8") as fh:
            cov = json.load(fh)
        assert cov["pipeline"] == "extract_institutional_competence"
        assert cov["files_processed_kov"] >= 1
        assert cov["files_with_output_kov"] >= 1
        assert cov["triples_emitted_kov"] >= 1
        assert cov["files_with_output"] >= 2

    def test_gate_fails_when_kov_input_nontrivial_but_no_output(
        self, issuers_registry_file, monkeypatch
    ):
        """Triple-monkeypatch trick (iter_peep_files + load_json +
        save_json) — 11,001 synthetic KOV paths with no detection."""
        import extract_institutional_competence as mod
        import estleg_common
        krr = issuers_registry_file
        institutions_dir = krr / "institutions"
        reports_dir = krr / "reports" / "kov"
        institutions_dir.mkdir(parents=True)
        reports_dir.mkdir(parents=True)

        kov_marker = krr / "regulations" / "kov" / "no_match"
        synthetic_kov_paths = [
            kov_marker / f"act_{i}_peep.json" for i in range(11001)
        ]

        canned_doc = {
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:Reg_NoMatch_Map_2024",
                 "@type": ["owl:Ontology", "estleg:Act",
                           "estleg:MunicipalRegulation"],
                 "estleg:enactedByMunicipality": {
                     "@id": "estleg:Municipality_tallinn"}},
                {"@id": "estleg:Reg_NoMatch_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 1",
                 "estleg:summary": "Käesolev määrus ei viita ühelegi institutsioonile."},
            ],
        }

        def fake_iter(*args, **kwargs):
            return synthetic_kov_paths

        def fake_load(filepath):
            import copy
            return copy.deepcopy(canned_doc)

        def fake_save(filepath, doc):
            return None

        monkeypatch.setattr(mod, "iter_peep_files", fake_iter)
        monkeypatch.setattr(mod, "load_json", fake_load)
        monkeypatch.setattr(mod, "save_json", fake_save)
        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "INSTIT_DIR", institutions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)

        rc = mod.main()
        assert rc == 1, f"gate must fail when KOV >=11k but no output; got rc={rc}"

    def test_unresolved_count_includes_state_side_kov_body_words(
        self, issuers_registry_file, monkeypatch
    ):
        """State law with KOV body word counts toward
        unresolved_references."""
        import extract_institutional_competence as mod
        import estleg_common
        krr = issuers_registry_file
        institutions_dir = krr / "institutions"
        reports_dir = krr / "reports" / "kov"
        institutions_dir.mkdir(parents=True)
        reports_dir.mkdir(parents=True)

        (krr / "kov_seadus_peep.json").write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:KOKS_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"]},
                {"@id": "estleg:KOKS_Par_22",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 22",
                 "estleg:summary": "Linnavolikogu kehtestab korra."},
            ],
        }), encoding="utf-8")

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "INSTIT_DIR", institutions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "iter_peep_files", _iter_kov_inclusive)

        rc = mod.main()
        assert rc in (0, None)

        cov_path = reports_dir / "extract_institutional_competence_coverage.json"
        with open(cov_path, "r", encoding="utf-8") as fh:
            cov = json.load(fh)
        assert cov["unresolved_references"] >= 1

    def test_fallback_hits_counts_path3_resolutions(
        self, tmp_path, monkeypatch
    ):
        """KOV act whose enactedBy is unknown to the registry; act's
        enactedByMunicipality has exactly one suffix-compatible
        Issuer match. Resolver uses Path 3 -> fallback_hits >= 1."""
        import extract_institutional_competence as mod
        import estleg_common
        krr = tmp_path / "krr_outputs"
        institutions_dir = krr / "institutions"
        reports_dir = krr / "reports" / "kov"
        institutions_dir.mkdir(parents=True)
        reports_dir.mkdir(parents=True)

        (krr / "issuers_kov_peep.json").write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
                "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
            },
            "@graph": [
                {"@id": "estleg:Issuer_tartu_linnavolikogu",
                 "@type": ["owl:NamedIndividual", "estleg:Issuer"],
                 "rdfs:label": "Tartu Linnavolikogu",
                 "estleg:bodyType": "volikogu",
                 "estleg:currentMunicipality": {
                     "@id": "estleg:Municipality_tartu"}},
            ],
        }), encoding="utf-8")

        kov = krr / "regulations" / "kov" / "tartu"
        kov.mkdir(parents=True)
        (kov / "act_peep.json").write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:Reg_Tartu_X_Map_2024",
                 "@type": ["owl:Ontology", "estleg:Act",
                           "estleg:MunicipalRegulation"],
                 "estleg:enactedBy": {"@id": "estleg:Issuer_unknown"},
                 "estleg:enactedByMunicipality": {
                     "@id": "estleg:Municipality_tartu"}},
                {"@id": "estleg:Reg_Tartu_X_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 1",
                 "estleg:summary": "Linnavolikogu kehtestab maksu."},
            ],
        }), encoding="utf-8")

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "INSTIT_DIR", institutions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "iter_peep_files", _iter_kov_inclusive)

        rc = mod.main()
        assert rc in (0, None)

        cov_path = reports_dir / "extract_institutional_competence_coverage.json"
        with open(cov_path, "r", encoding="utf-8") as fh:
            cov = json.load(fh)
        assert cov["fallback_hits"] >= 1


class TestCorpusInvariant:
    """Corpus-wide invariant: every KOV provision carrying
    competentAuthority targeting a body word (volikogu/valitsus
    body type) MUST resolve to an Issuer_* IRI, never an
    Institution_<body_slug> fallback."""

    @pytest.mark.slow
    def test_kov_competentauthority_is_issuer_iri(self):
        from estleg_common import iter_peep_files, KRR_DIR
        if not KRR_DIR.exists() or not list(KRR_DIR.glob("*_peep.json")):
            pytest.fail("krr_outputs/ empty; corpus invariant was not checked")

        body_slug_institutions = {
            "Institution_linnavolikogu", "Institution_vallavolikogu",
            "Institution_alevivolikogu", "Institution_linnavalitsus",
            "Institution_vallavalitsus",
        }

        violations = []
        for peep in iter_peep_files():
            if "regulations/kov/" not in str(peep):
                continue
            try:
                with open(peep, "r", encoding="utf-8") as fh:
                    doc = json.load(fh)
            except (json.JSONDecodeError, OSError):
                continue
            for node in doc.get("@graph", []):
                refs = node.get("estleg:competentAuthority")
                if not refs:
                    continue
                if isinstance(refs, dict):
                    refs = [refs]
                for ref in refs:
                    iri = ref.get("@id") if isinstance(ref, dict) else None
                    if iri is None:
                        continue
                    suffix = iri.removeprefix("estleg:")
                    if suffix in body_slug_institutions:
                        violations.append(
                            f"{peep.name}: {node.get('@id')} has body-slug "
                            f"Institution_* fallback: {iri}"
                        )
        assert not violations, (
            f"Corpus invariant violated by {len(violations)} provisions:\n  "
            + "\n  ".join(violations[:20])
            + (f"\n  ... and {len(violations) - 20} more"
               if len(violations) > 20 else "")
        )


# ---------------------------------------------------------------------------
# Issue #170 regression tests — substring leaks, inflection, validation, etc.
# ---------------------------------------------------------------------------


class TestIssue170SubstringLeak:
    """Finding 1 (#170): the old `name.lower() in text.lower()` matcher
    leaked false positives — abbreviations and short institution names
    matched inside unrelated words. The new \\b...\\b regex matcher must
    reject these."""

    def test_mta_substring_in_unrelated_word_is_rejected(self):
        from extract_institutional_competence import detect_institutions
        # 'uMTAga' contains MTA as a substring, but \bMTA\b won't match.
        results = detect_institutions("kasutati uMTAga maagilist sõna")
        assert all(s != "maksu_ja_tolliamet" for _, s, _ in results)

    def test_ppa_substring_in_unrelated_word_is_rejected(self):
        from extract_institutional_competence import detect_institutions
        # PPA inside hyperPPAclause should not match.
        results = detect_institutions("toimetab edasi hyperPPAclause vorm")
        assert all(s != "politsei_ja_piirivalveamet" for _, s, _ in results)

    def test_mta_word_boundary_match_is_kept(self):
        from extract_institutional_competence import detect_institutions
        results = detect_institutions("MTA juhend on kohustuslik")
        slugs = {s for _, s, _ in results}
        assert "maksu_ja_tolliamet" in slugs

    def test_riigikogu_inside_unrelated_word_is_rejected(self):
        from extract_institutional_competence import detect_institutions
        # 'Riigikogu' as a substring of 'rahvusVabariigikogu' (synthetic) shouldn't fire.
        results = detect_institutions(
            "X rahvusVabariigikogu Y", )
        slugs = {s for _, s, _ in results}
        assert "riigikogu" not in slugs


class TestIssue170AbbreviationFullnamePrecedence:
    """Finding 2 (#170): when both the abbreviation and the canonical
    full name appear in the same provision, only register the canonical
    institution. The abbreviation entry must be suppressed."""

    def test_mta_and_full_name_collapse_to_one_institution(self):
        from extract_institutional_competence import detect_institutions
        results = detect_institutions(
            "Maksu- ja Tolliamet vastutab MTA poolt välja antud reeglite eest"
        )
        slugs = [s for _, s, _ in results]
        # Single canonical entry, no extra MTA-only entry.
        assert slugs.count("maksu_ja_tolliamet") == 1
        assert "mta" not in slugs

    def test_ppa_and_full_name_collapse(self):
        from extract_institutional_competence import detect_institutions
        results = detect_institutions(
            "Politsei- ja Piirivalveamet ehk PPA tegutseb"
        )
        slugs = [s for _, s, _ in results]
        assert slugs.count("politsei_ja_piirivalveamet") == 1
        assert "ppa" not in slugs

    def test_lone_mta_still_registers_when_full_name_absent(self):
        from extract_institutional_competence import detect_institutions
        results = detect_institutions("MTA poolt määratud sanktsioon")
        slugs = {s for _, s, _ in results}
        assert "maksu_ja_tolliamet" in slugs


class TestIssue170MinistryCaseInsensitive:
    """Finding 3 (#170): ministry/agency/inspektsioon patterns now run
    with re.IGNORECASE, so sentence-internal forms like 'siseministeeriumi'
    and 'rahandusministeeriumile' match."""

    def test_lowercase_ministeerium_matches(self):
        from extract_institutional_competence import detect_institutions
        results = detect_institutions(
            "siseministeeriumi pädevuses on järgmine"
        )
        slugs = {s for _, s, _ in results}
        assert "siseministeerium" in slugs

    def test_inflected_ministeerium_collapses_to_nominative(self):
        from extract_institutional_competence import detect_institutions
        results = detect_institutions(
            "rahandusministeeriumile esitatakse aruanne"
        )
        slugs = {s for _, s, _ in results}
        assert "rahandusministeerium" in slugs

    def test_lowercase_inspektsioon_matches(self):
        from extract_institutional_competence import detect_institutions
        results = detect_institutions("andmekaitseinspektsiooni nõuandel")
        slugs = {s for _, s, _ in results}
        # via the explicit inflection-strip path
        assert "andmekaitseinspektsioon" in slugs


class TestIssue170KohusSuppression:
    """Finding 4 (#170): generic 'kohus' is suppressed whenever any of
    riigikohus/ringkonnakohus/halduskohus/maakohus appears in the text —
    independent of `found` accumulator state ordering."""

    def test_generic_kohus_kept_when_alone(self):
        from extract_institutional_competence import detect_institutions
        results = detect_institutions("kohus otsustab vaidluse")
        slugs = {s for _, s, _ in results}
        assert "kohus" in slugs

    def test_generic_kohus_suppressed_with_riigikohus(self):
        from extract_institutional_competence import detect_institutions
        results = detect_institutions(
            "Riigikohus võib edastada kohusele uue otsuse"
        )
        slugs = {s for _, s, _ in results}
        assert "riigikohus" in slugs
        assert "kohus" not in slugs

    def test_generic_kohus_suppressed_with_halduskohus(self):
        from extract_institutional_competence import detect_institutions
        results = detect_institutions("halduskohus tuvastas kohuse rikkumise")
        slugs = {s for _, s, _ in results}
        assert "halduskohus" in slugs
        assert "kohus" not in slugs


class TestIssue170InflectionCollapse:
    """Finding 5 (#170): Estonian noun-stem normaliser must collapse all
    case forms of *amet/*ministeerium/*inspektsioon/*minister to their
    nominative slug.

    NOTE: agencies covered by the issue-#118 alias table (e.g. Maksuamet)
    are exercised in ``TestIssue118AliasCollapse`` instead — there the
    de-inflected stem is further collapsed onto its successor slug, so
    the expected output differs. The cases here use agencies/ministries
    that are *not* alias keys, so de-inflection is the only step."""

    @pytest.mark.parametrize("form,expected", [
        ("Statistikaametile", "statistikaamet"),
        ("Statistikaameti",   "statistikaamet"),
        ("statistikaametil",  "statistikaamet"),
        ("statistikaametilt", "statistikaamet"),
        ("statistikaametist", "statistikaamet"),
        ("statistikaametisse", "statistikaamet"),
        ("statistikaametiga", "statistikaamet"),
        ("statistikaametiks", "statistikaamet"),
        ("Tooinspektsioonile", "tooinspektsioon"),
        ("tooinspektsiooni", "tooinspektsioon"),
        ("Siseministeeriumile", "siseministeerium"),
        ("siseministeeriumi", "siseministeerium"),
        ("rahandusministeerium", "rahandusministeerium"),
        ("kaitseministeeriumiga", "kaitseministeerium"),
    ])
    def test_normalize_iri_suffix_collapses_inflection(self, form, expected):
        from extract_institutional_competence import normalize_iri_suffix
        assert normalize_iri_suffix(form) == expected

    def test_inflected_form_does_not_create_sibling_iri(self):
        """The whole point of Finding 5: 'Statistikaametile' and
        'Statistikaameti' must share a single IRI suffix, not produce
        Institution_statistikaametile + Institution_statistikaameti
        siblings."""
        from extract_institutional_competence import detect_institutions
        # Run two detections separately and verify they collapse.
        results_a = detect_institutions("Statistikaametile esitati taotlus")
        results_b = detect_institutions("Statistikaameti otsus on lõplik")
        slugs_a = {s for _, s, _ in results_a}
        slugs_b = {s for _, s, _ in results_b}
        assert "statistikaamet" in slugs_a
        assert "statistikaamet" in slugs_b


class TestIssue118ExtendedCaseSuffixes:
    """Issue #118: the noun-stem case-suffix list was widened to cover the
    full set of Estonian grammatical cases (translative -ks, terminative
    -ni, essive -na, abessive -ta and the plural-stem composites) so a
    re-run of the extractor collapses every inflected agency reference to
    its nominative slug — the earlier re-run left e.g.
    ``Institution_finantsinspektsioonilt`` (ablative) as its own node.

    Agencies that are *not* alias keys are used here so de-inflection is
    the only transformation; alias-key cases live in
    ``TestIssue118AliasCollapse`` and ``TestIssue118AliasCollapseExtended``."""

    @pytest.mark.parametrize("form,expected", [
        # The exact form named in the issue.
        ("Finantsinspektsioonilt", "finantsinspektsioon"),
        ("finantsinspektsiooni", "finantsinspektsioon"),
        ("Finantsinspektsioonile", "finantsinspektsioon"),
        ("finantsinspektsioonist", "finantsinspektsioon"),
        ("finantsinspektsioonis", "finantsinspektsioon"),
        ("finantsinspektsioonina", "finantsinspektsioon"),
        ("finantsinspektsioonini", "finantsinspektsioon"),
        # *amet across the full case paradigm.
        ("Statistikaametini", "statistikaamet"),
        ("Statistikaametina", "statistikaamet"),
        ("Statistikaametita", "statistikaamet"),
        ("Statistikaametitele", "statistikaamet"),     # plural allative
        ("Statistikaametitega", "statistikaamet"),     # plural comitative
        ("Statistikaametitest", "statistikaamet"),     # plural elative
        # *ministeerium.
        ("Siseministeeriumini", "siseministeerium"),
        ("Siseministeeriumita", "siseministeerium"),
        # Already nominative — must pass through untouched.
        ("Finantsinspektsioon", "finantsinspektsioon"),
        ("Statistikaamet", "statistikaamet"),
        ("Rahandusministeerium", "rahandusministeerium"),
        # *kogu / *valitsus roots (Issue #118): de-inflected forms must
        # land on the nominative even though _canonical_body_slug also
        # canonicalises KOV body words downstream.
        ("riigikogule", "riigikogu"),
        ("Vabariigivalitsusele", "vabariigivalitsus"),
    ])
    def test_extended_case_suffix_collapses_to_nominative(self, form, expected):
        from extract_institutional_competence import normalize_iri_suffix
        assert normalize_iri_suffix(form) == expected

    @pytest.mark.parametrize("text,expected", [
        ("vallavolikoguni", "vallavolikogu"),     # terminative
        ("linnavolikoguna", "linnavolikogu"),     # essive
        ("vallavalitsuseks", "vallavalitsus"),    # translative
        ("linnavalitsuseni", "linnavalitsus"),    # terminative
    ])
    def test_canonical_body_slug_widened_cases(self, text, expected):
        from extract_institutional_competence import _canonical_body_slug
        assert _canonical_body_slug(text) == expected

    def test_no_inflected_institution_slugs_on_disk(self):
        """After the re-run every krr_outputs/institutions/institution_*.json
        filename stem must already be its own canonical slug — i.e.
        normalize_iri_suffix() is a fixed point on it."""
        from extract_institutional_competence import INST_DIR, normalize_iri_suffix
        offenders: list[tuple[str, str]] = []
        for path in sorted(INST_DIR.glob("institution_*.json")):
            slug = path.stem.removeprefix("institution_")
            norm = normalize_iri_suffix(slug)
            if norm != slug:
                offenders.append((slug, norm))
        assert offenders == [], offenders


class TestIssue118AliasCollapse:
    """Issue #118: the curated historical-merge alias table collapses a
    predecessor institution slug onto its canonical successor — applied
    AFTER the noun-stem normaliser, so inflected predecessor names also
    collapse correctly.

    Conservative scope: only well-documented agency mergers/renames are
    in data/institution_aliases.json (see that file for the evidence)."""

    @pytest.mark.parametrize("form,expected", [
        # Maksuamet + Tolliamet -> Maksu- ja Tolliamet (2004).
        ("Maksuamet", "maksu_ja_tolliamet"),
        ("Maksuametile", "maksu_ja_tolliamet"),
        ("maksuametiga", "maksu_ja_tolliamet"),
        ("Tolliamet", "maksu_ja_tolliamet"),
        ("Tolliametile", "maksu_ja_tolliamet"),
        # Politseiamet/Piirivalveamet/KMA -> Politsei- ja Piirivalveamet (2010).
        ("Politseiamet", "politsei_ja_piirivalveamet"),
        ("Piirivalveametile", "politsei_ja_piirivalveamet"),
        # Tervishoiuamet / Tervisekaitseinspektsioon -> Terviseamet (2010).
        ("Tervishoiuamet", "terviseamet"),
        ("Tervishoiuametile", "terviseamet"),
        ("Tervisekaitseinspektsioon", "terviseamet"),
        # Tarbijakaitseamet -> Tarbijakaitse ja Tehnilise Järelevalve Amet (2019).
        ("Tarbijakaitseamet", "tarbijakaitse_ja_tehnilise_jarelevalve_amet"),
        # Maanteeamet/Lennuamet -> Transpordiamet (2021).
        ("Maanteeamet", "transpordiamet"),
        ("Lennuametile", "transpordiamet"),
        # Keskkonnainspektsioon -> Keskkonnaamet (2021).
        ("Keskkonnainspektsioon", "keskkonnaamet"),
        # Not an alias key — must pass through unchanged (it's a target).
        ("Keskkonnaamet", "keskkonnaamet"),
        ("Riigikohus", "riigikohus"),
    ])
    def test_alias_collapses_predecessor_to_canonical(self, form, expected):
        from extract_institutional_competence import normalize_iri_suffix
        assert normalize_iri_suffix(form) == expected

    def test_abbreviation_then_alias_chain(self):
        """An abbreviation that maps to a predecessor full-name slug must
        still land on the canonical successor — the alias is applied
        after the abbreviation expansion."""
        from extract_institutional_competence import normalize_iri_suffix
        # 'mta' -> 'maksu_ja_tolliamet' (the abbrev map already points at
        # the merged name); the alias step is a no-op here but must not
        # break the lookup.
        assert normalize_iri_suffix("mta") == "maksu_ja_tolliamet"

    def test_detect_predecessor_resolves_to_canonical_node(self):
        """Detecting a pre-merger reference in provision text must yield
        the canonical successor's Institution slug, so a single node
        absorbs both 'Maksuamet' and 'Maksu- ja Tolliamet' mentions."""
        from extract_institutional_competence import detect_institutions
        pre = detect_institutions("Maksuamet teostab järelevalvet maksukohustuse üle.")
        post = detect_institutions("Maksu- ja Tolliamet teostab järelevalvet.")
        pre_slugs = {s for _, s, _ in pre}
        post_slugs = {s for _, s, _ in post}
        assert "maksu_ja_tolliamet" in pre_slugs
        assert "maksu_ja_tolliamet" in post_slugs
        # The bare predecessor slug must NOT survive as a separate node.
        assert "maksuamet" not in pre_slugs

    def test_alias_loader_skips_self_referential_and_malformed(self, tmp_path, monkeypatch):
        """_load_institution_aliases must drop entries that are missing a
        canonical, map a slug to itself, or aren't dicts/strings."""
        import extract_institutional_competence as mod
        bad_table = {
            "version": 1,
            "aliases": {
                "selfref": {"canonical": "selfref"},          # self-loop -> dropped
                "nocanon": {"evidence": "no canonical key"},   # missing -> dropped
                "goodkey": {"canonical": "goodvalue", "evidence": "ok"},
                "stringform": "anothertarget",                 # string value form -> kept
            },
        }
        path = tmp_path / "aliases.json"
        path.write_text(json.dumps(bad_table), encoding="utf-8")
        loaded = mod._load_institution_aliases(path)
        assert loaded == {"goodkey": "goodvalue", "stringform": "anothertarget"}

    def test_missing_alias_file_returns_empty(self, tmp_path):
        import extract_institutional_competence as mod
        assert mod._load_institution_aliases(tmp_path / "does_not_exist.json") == {}


class TestIssue118AliasCollapseExtended:
    """Issue #118 re-run reconciliation: with the widened case-suffix list,
    *inflected* predecessor-agency references also collapse onto the
    canonical successor slug — and the curated-canonical seeding makes the
    extractor accept those successor slugs even on the re-run that prunes
    the stale double-underscore file for one of them."""

    @pytest.mark.parametrize("form,expected", [
        # Maksu- ja Tolliamet (2004) — inflected predecessor forms.
        ("Maksuametile", "maksu_ja_tolliamet"),
        ("Maksuametilt", "maksu_ja_tolliamet"),
        ("Maksuametisse", "maksu_ja_tolliamet"),
        ("Maksuametini", "maksu_ja_tolliamet"),
        ("Tolliametisse", "maksu_ja_tolliamet"),
        ("Tolliametita", "maksu_ja_tolliamet"),
        # Terviseamet (2010).
        ("Terviseametisse", "terviseamet"),
        ("Tervishoiuametile", "terviseamet"),
        ("Tervisekaitseinspektsioonilt", "terviseamet"),
        ("Tervisekaitseametisse", "terviseamet"),
        # Transpordiamet (2021).
        ("Maanteeametile", "transpordiamet"),
        ("Maanteeametilt", "transpordiamet"),
        ("Lennuametisse", "transpordiamet"),
        # Põllumajandus- ja Toiduamet (2021) — the second dangling target.
        ("Pollumajandusametile", "pollumajandus_ja_toiduamet"),
        ("Veterinaar- ja Toiduametile", "pollumajandus_ja_toiduamet"),
        # Tarbijakaitse ja Tehnilise Järelevalve Amet (2019).
        ("Tarbijakaitseametisse", "tarbijakaitse_ja_tehnilise_jarelevalve_amet"),
        # Keskkonnaamet (2021).
        ("Keskkonnainspektsioonilt", "keskkonnaamet"),
        # Politsei- ja Piirivalveamet (2010).
        ("Politseiametile", "politsei_ja_piirivalveamet"),
        ("Piirivalveametisse", "politsei_ja_piirivalveamet"),
        ("Kodakondsus- ja Migratsiooniametile", "politsei_ja_piirivalveamet"),
    ])
    def test_inflected_predecessor_collapses_to_successor(self, form, expected):
        from extract_institutional_competence import normalize_iri_suffix, sanitize_id
        # detect_institutions runs sanitize_id() on generic matches before
        # normalize_iri_suffix(); mirror that so multi-word predecessor
        # names are exercised the same way the pipeline does.
        assert normalize_iri_suffix(sanitize_id(form)) == expected

    def test_curated_canonical_suffixes_include_alias_targets(self):
        """_curated_canonical_suffixes() seeds the registry with every
        named-institution suffix plus every alias `canonical` target — so
        Maksu- ja Tolliamet and Põllumajandus- ja Toiduamet are accepted
        even when their on-disk file is briefly absent."""
        from extract_institutional_competence import _curated_canonical_suffixes
        seeded = _curated_canonical_suffixes()
        assert "maksu_ja_tolliamet" in seeded
        assert "pollumajandus_ja_toiduamet" in seeded
        assert "politsei_ja_piirivalveamet" in seeded
        assert "terviseamet" in seeded

    def test_load_canonical_institutions_collapses_double_underscore(self, tmp_path):
        """A stale ``institution_maksu__ja_tolliamet.json`` on disk must be
        registered under the collapsed slug the normaliser emits, so
        predecessor references aren't dropped as unknown_institution."""
        import extract_institutional_competence as mod
        inst = tmp_path / "institutions"
        inst.mkdir(parents=True)
        (inst / "institution_maksu__ja_tolliamet.json").write_text(
            json.dumps({"@graph": []}), encoding="utf-8"
        )
        loaded = mod._load_canonical_institutions(inst)
        assert "maksu_ja_tolliamet" in loaded
        assert "maksu__ja_tolliamet" not in loaded

    def test_load_canonical_institutions_empty_dir_stays_empty(self, tmp_path):
        """Bootstrap mode: an empty institutions dir yields an empty set
        (no curated seeding) so a freshly-cloned tree still populates
        everything from scratch."""
        import extract_institutional_competence as mod
        inst = tmp_path / "institutions"
        inst.mkdir(parents=True)
        assert mod._load_canonical_institutions(inst) == set()

    def test_real_corpus_has_canonical_alias_target_files(self):
        """End-state: the regenerated corpus has an institution file for
        every alias `canonical` target (the re-run reconciliation)."""
        import extract_institutional_competence as mod
        present = {
            p.stem.removeprefix("institution_")
            for p in mod.INST_DIR.glob("institution_*.json")
        }
        for canonical in set(mod._INSTITUTION_ALIASES.values()):
            assert canonical in present, (
                f"alias target {canonical!r} has no institution file"
            )


class TestIssue170CrossProvisionDedup:
    """Finding 6 (#170): the per-institution provision list must be
    deduped at append-time across ALL provisions (not just within one
    provision); same `(provision, ctype, law)` tuple shouldn't appear
    twice."""

    def test_record_provision_does_not_duplicate(self):
        from extract_institutional_competence import (
            _PipelineState,
            _record_provision_for_institution,
        )
        state = _PipelineState()
        for _ in range(3):
            _record_provision_for_institution(
                state=state,
                inst_iri="estleg:Institution_test",
                canon_name="Test",
                iri_suffix="test",
                itype="agency",
                provision_iri="estleg:Test_Par_1",
                competence_type="general",
                law_name="test_law",
            )
        provisions = state.inst_provisions["estleg:Institution_test"]
        assert provisions == [("estleg:Test_Par_1", "general", "test_law")], (
            f"expected exactly one tuple, got {provisions}"
        )


class TestIssue170TruncationAware:
    """Finding 7 (#170): when an institution would have its
    appliesToProvision list truncated past `_APPLIES_TO_PROVISION_CAP`,
    the institution file MUST surface
    estleg:appliesToProvisionCount with the full count, even when the
    appliesToProvision list itself is capped."""

    def test_truncated_count_surfaced(self, tmp_path, monkeypatch):
        import extract_institutional_competence as mod
        krr = tmp_path / "krr_outputs"
        institutions_dir = krr / "institutions"
        krr.mkdir()
        institutions_dir.mkdir()

        # Stage one peep with 60 provisions all citing Riigikohus —
        # that's well above the 50-cap, so truncation fires.
        peep = krr / "big_peep.json"
        graph = [
            {"@id": "estleg:Big_Map_2026",
             "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"]},
        ]
        for i in range(60):
            graph.append({
                "@id": f"estleg:Big_Par_{i}",
                "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                "estleg:paragrahv": f"§ {i}",
                "estleg:summary": "Riigikohus otsustab vaidluse.",
            })
        peep.write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": graph,
        }), encoding="utf-8")
        # Empty issuers registry — Riigikohus is named, so this works.
        (krr / "issuers_kov_peep.json").write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#"},
            "@graph": [],
        }), encoding="utf-8")

        import estleg_common
        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "INSTIT_DIR", institutions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "iter_peep_files", _iter_kov_inclusive)

        rc = mod.main()
        assert rc in (0, None)

        inst_path = institutions_dir / "institution_riigikohus.json"
        assert inst_path.exists()
        with open(inst_path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)

        # Find the Competence node for Riigikohus.
        competences = [n for n in doc["@graph"]
                       if "estleg:Competence" in n.get("@type", [])]
        assert competences, "expected at least one Competence node"
        for comp in competences:
            count = comp.get("estleg:appliesToProvisionCount")
            assert count is not None, (
                f"missing estleg:appliesToProvisionCount on {comp.get('@id')}"
            )
            applies = comp.get("estleg:appliesToProvision", [])
            if isinstance(applies, dict):
                applies = [applies]
            # appliesToProvision is capped...
            assert len(applies) <= mod._APPLIES_TO_PROVISION_CAP
            # ...but appliesToProvisionCount records the full count.
            count_value = int(count.get("@value")) if isinstance(count, dict) else int(count)
            assert count_value == 60, (
                f"appliesToProvisionCount should reflect all 60 provisions, got {count_value}"
            )


class TestIssue215CompetenceBackfill:
    """Issue #215: Competence nodes expose grantedBy and competenceArea."""

    def test_select_granted_by_majority_tie_and_broad_spread(self):
        from extract_institutional_competence import _select_granted_by

        # Strict plurality (2 vs 1) → return the plurality act.
        assert _select_granted_by([
            "estleg:LAW_A_Map_2026",
            "estleg:LAW_A_Map_2026",
            "estleg:LAW_B_Map_2026",
        ]) == "estleg:LAW_A_Map_2026"
        # Issue #274: a 1-1 tie has no majority → abstain (was: arbitrary
        # sort-order winner "estleg:LAW_A_Map_2026").
        assert _select_granted_by([
            "estleg:LAW_B_Map_2026",
            "estleg:LAW_A_Map_2026",
        ]) is None
        # All-singleton broad spread → abstain.
        assert _select_granted_by([
            "estleg:LAW_A_Map_2026",
            "estleg:LAW_B_Map_2026",
            "estleg:LAW_C_Map_2026",
            "estleg:LAW_D_Map_2026",
        ]) is None

    def test_write_institution_files_emits_granted_by_and_competence_area(
        self, tmp_path, monkeypatch
    ):
        import extract_institutional_competence as mod

        institutions_dir = tmp_path / "institutions"
        institutions_dir.mkdir()
        monkeypatch.setattr(mod, "INSTIT_DIR", institutions_dir)

        state = mod._PipelineState()
        for idx in range(3):
            mod._record_provision_for_institution(
                state=state,
                inst_iri="estleg:Institution_andmekaitseinspektsioon",
                canon_name="Andmekaitse Inspektsioon",
                iri_suffix="andmekaitseinspektsioon",
                itype="agency",
                provision_iri=f"estleg:IKS_Par_{idx}",
                competence_type="supervision",
                law_name="estleg:IKS_Map_2026",
            )
        mod._record_provision_for_institution(
            state=state,
            inst_iri="estleg:Institution_andmekaitseinspektsioon",
            canon_name="Andmekaitse Inspektsioon",
            iri_suffix="andmekaitseinspektsioon",
            itype="agency",
            provision_iri="estleg:OTHER_Par_1",
            competence_type="supervision",
            law_name="estleg:OTHER_Map_2026",
        )

        mod.write_institution_files(state)

        doc = json.loads(
            (institutions_dir / "institution_andmekaitseinspektsioon.json")
            .read_text(encoding="utf-8")
        )
        competence = next(
            node for node in doc["@graph"]
            if "estleg:Competence" in node.get("@type", [])
        )
        assert competence["estleg:grantedBy"] == {"@id": "estleg:IKS_Map_2026"}
        assert competence["estleg:competenceArea"] == "data_protection"


class TestIssue170CanonicalValidation:
    """Finding 8 (#170): institutions whose normalized iri_suffix isn't
    in the canonical 126-list registry must be dropped, with a counter
    surfaced in the coverage report."""

    def test_unknown_institution_dropped(self, tmp_path, monkeypatch):
        import extract_institutional_competence as mod
        import estleg_common
        krr = tmp_path / "krr_outputs"
        institutions_dir = krr / "institutions"
        reports_dir = krr / "reports" / "kov"
        krr.mkdir()
        institutions_dir.mkdir()
        reports_dir.mkdir(parents=True)

        # Pre-stage the canonical registry with ONLY 'riigikohus' so any
        # other detection (e.g. 'mitteamet' from a generic *amet pattern)
        # is rejected.
        (institutions_dir / "institution_riigikohus.json").write_text(
            "{}", encoding="utf-8"
        )

        # Empty issuers registry.
        (krr / "issuers_kov_peep.json").write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#"},
            "@graph": [],
        }), encoding="utf-8")

        # Peep with text triggering BOTH a known canonical institution
        # (Riigikohus) and an unknown one matched via generic *amet
        # ('hüpoteetilineamet').
        peep = krr / "weird_peep.json"
        peep.write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:Weird_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"]},
                {"@id": "estleg:Weird_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 1",
                 "estleg:summary":
                     "Riigikohus võib küsida hüpoteetilineamet käest "
                     "lisateavet."},
            ],
        }), encoding="utf-8")

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "INSTIT_DIR", institutions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "iter_peep_files", _iter_kov_inclusive)

        rc = mod.main()
        assert rc in (0, None)

        # Riigikohus is canonical — file must be (re)written.
        assert (institutions_dir / "institution_riigikohus.json").exists()
        # 'hüpoteetilineamet' is unknown — must NOT be emitted.
        assert not (institutions_dir / "institution_hupoteetilineamet.json").exists()

        # Coverage report records the unknown count.
        cov_path = reports_dir / "extract_institutional_competence_coverage.json"
        with open(cov_path, "r", encoding="utf-8") as fh:
            cov = json.load(fh)
        assert cov["skip_reasons"].get("unknown_institution", 0) >= 1

    def test_bootstrap_mode_when_registry_empty(self, tmp_path, monkeypatch):
        """When the canonical registry is empty (fresh-clone bootstrap),
        validation is disabled — all detections still emit."""
        import extract_institutional_competence as mod
        import estleg_common
        krr = tmp_path / "krr_outputs"
        institutions_dir = krr / "institutions"
        reports_dir = krr / "reports" / "kov"
        krr.mkdir()
        institutions_dir.mkdir()
        reports_dir.mkdir(parents=True)

        (krr / "issuers_kov_peep.json").write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#"},
            "@graph": [],
        }), encoding="utf-8")

        peep = krr / "fresh_peep.json"
        peep.write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:Fresh_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"]},
                {"@id": "estleg:Fresh_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 1",
                 "estleg:summary": "Riigikohus otsustab vaidlused."},
            ],
        }), encoding="utf-8")

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "INSTIT_DIR", institutions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "iter_peep_files", _iter_kov_inclusive)

        rc = mod.main()
        assert rc in (0, None)
        # Bootstrap mode emits the institution despite the empty registry.
        assert (institutions_dir / "institution_riigikohus.json").exists()


class TestIssue170NoImportSideEffect:
    """Finding 9 (#170): importing the module must not create the
    institutions directory on disk."""

    def test_import_does_not_mkdir(self, tmp_path, monkeypatch):
        import importlib
        import extract_institutional_competence as mod
        # Point at a location that doesn't exist; reload the module and
        # confirm nothing was created.
        nonexistent = tmp_path / "shouldnt_exist"
        monkeypatch.setattr(mod, "INSTIT_DIR", nonexistent)
        importlib.reload(mod)
        # After reload, the (newly-imported) module's INSTIT_DIR points
        # back at the production path, so check the tmp path stayed
        # untouched.
        assert not nonexistent.exists()


class TestIssue170MainRefactor:
    """Finding 11 (#170): main() now delegates to process_law_file,
    write_institution_files, write_report, write_coverage helpers — pin
    their public surface so future refactors don't accidentally remove
    the helpers."""

    def test_helpers_are_exported(self):
        from extract_institutional_competence import (
            process_law_file,
            write_institution_files,
            write_report,
            write_coverage,
            _PipelineState,
            _ensure_dirs,
            _record_provision_for_institution,
        )
        assert callable(process_law_file)
        assert callable(write_institution_files)
        assert callable(write_report)
        assert callable(write_coverage)
        assert callable(_ensure_dirs)
        assert callable(_record_provision_for_institution)
        state = _PipelineState()
        assert state.inst_data == {}
        assert state.unknown_institution_count == 0


class TestIssue170DeadInflectionMapDeleted:
    """Finding 10 (#170): triple/double-underscore variants in the
    INFLECTION_MAP are unreachable because normalize_iri_suffix collapses
    runs of underscores BEFORE the lookup. They've been deleted; verify
    by importing the (now-private) map."""

    def test_inflection_map_has_no_double_underscore_keys(self):
        from extract_institutional_competence import _INFLECTION_MAP
        bad = [k for k in _INFLECTION_MAP if "__" in k]
        assert bad == [], (
            f"unreachable double-underscore keys still in _INFLECTION_MAP: {bad}"
        )


class TestIssue258CompetenceTypeOrdering:
    """Issue #258: the bare-noun 'järelevalve' pattern used to outrank the
    real action verbs (annab loa / kehtestab / kontrollib), so any provision
    that merely *mentioned* supervision collapsed to `supervision`.
    detect_competence_type returns the FIRST match, so the action verbs must
    now precede the bare noun while verb-adjacent supervision phrases stay
    on top."""

    def test_kehtestab_jarelevalve_is_regulation_not_supervision(self):
        """The exact case named in the issue: a regulation verb that happens
        to mention the supervision noun must classify as `regulation`."""
        from extract_institutional_competence import detect_competence_type
        assert detect_competence_type(
            "Minister kehtestab järelevalve korra"
        ) == "regulation"

    def test_annab_loa_with_jarelevalve_is_licensing(self):
        from extract_institutional_competence import detect_competence_type
        assert detect_competence_type(
            "Amet annab loa ja teostab järelevalve raames kontrolli"
        ) == "licensing"

    def test_kontrollib_with_jarelevalve_is_enforcement(self):
        from extract_institutional_competence import detect_competence_type
        assert detect_competence_type(
            "Asutus kontrollib järelevalve käigus nõuete täitmist"
        ) == "enforcement"

    @pytest.mark.parametrize("text", [
        "Amet järelevalvet teostab maksukohustuse üle",
        "Amet teostab järelevalvet maksukohustuse üle",
        "Amet teeb järelevalvet maksukohustuse üle",
    ])
    def test_verb_adjacent_supervision_phrases_stay_supervision(self, text):
        """The genuine supervision phrases (verb adjacent to the noun) must
        still classify as `supervision` even though the bare noun is now
        demoted below the action verbs."""
        from extract_institutional_competence import detect_competence_type
        assert detect_competence_type(text) == "supervision"

    def test_bare_jarelevalve_noun_still_falls_back_to_supervision(self):
        """When no operative verb is present, the bare 'järelevalve' noun is
        still a (weak) supervision signal — the demoted pattern is reached
        as the last resort."""
        from extract_institutional_competence import detect_competence_type
        assert detect_competence_type(
            "Järelevalve toimub seaduses sätestatud korras"
        ) == "supervision"


class TestIssue259GenericAgencyOverMatch:
    """Issue #259: the generic *amet / *inspektsioon patterns used a trailing
    `\\w*` that swallowed derivational endings, so a person (politseiametnik),
    an adverb (mitteametlikult) or a collective (ametkond) were detected as
    agencies. The trailing token is now a controlled Estonian case-suffix
    alternation, plus a stoplist for slugs like `mitteamet`."""

    @pytest.mark.parametrize("text", [
        "politseiametnik teostab järelevalvet",
        "järelevalveametnik koostab akti",
        "mitteametlikult teatatakse otsusest",
        "mitteametlik kokkulepe sõlmiti",
        "ametkond koguneb istungile",
    ])
    def test_derivational_forms_are_not_institutions(self, text):
        from extract_institutional_competence import detect_institutions
        results = detect_institutions(text)
        assert results == [], (
            f"derivational form should not be detected as an institution: "
            f"{text!r} -> {results!r}"
        )

    def test_politseiametnik_slug_absent(self):
        from extract_institutional_competence import detect_institutions
        slugs = {s for _, s, _ in
                 detect_institutions("politseiametnik teostab järelevalvet")}
        assert "politseiametnik" not in slugs

    @pytest.mark.parametrize("text,expected_slug", [
        ("Terviseamet teostab järelevalvet", "terviseamet"),
        ("Keskkonnaamet andis loa", "keskkonnaamet"),
        # A non-curated *amet name reached via the generic pattern, across
        # several case forms — all collapse to one nominative slug.
        ("Statistikaamet avaldas andmed", "statistikaamet"),
        ("Statistikaametile esitati taotlus", "statistikaamet"),
        ("Statistikaameti otsus on lõplik", "statistikaamet"),
        ("Statistikaametiks nimetati asutus", "statistikaamet"),
        # *inspektsioon case forms still match.
        ("Tooinspektsioonile esitatakse kaebus", "tooinspektsioon"),
    ])
    def test_real_agency_names_still_detected(self, text, expected_slug):
        from extract_institutional_competence import detect_institutions
        slugs = {s for _, s, _ in detect_institutions(text)}
        assert expected_slug in slugs, (
            f"{text!r} -> {slugs!r}, expected {expected_slug!r}"
        )

    def test_mitteamet_stoplisted_even_in_genitive(self):
        """`mitteameti` / `mitteametile` de-inflect to the slug `mitteamet`,
        which is a valid *amet case form but never a real institution — the
        stoplist drops it (this is what produced the committed
        institution_mitteamet.json)."""
        from extract_institutional_competence import detect_institutions
        assert detect_institutions("mitteameti otsus") == []
        assert detect_institutions("edastati mitteametile") == []

    def test_mitteamet_in_stoplist(self):
        from extract_institutional_competence import _INSTITUTION_STOPLIST
        assert "mitteamet" in _INSTITUTION_STOPLIST

    def test_agency_pattern_excludes_nik_lik_kond_endings(self):
        """Direct regex-level assertion: the *amet pattern must not match
        the derivational endings -nik / -lik / -kond / -likult."""
        from extract_institutional_competence import GENERIC_PATTERNS
        amet_pat = next(
            pat for pat, label, _itype in GENERIC_PATTERNS
            if label == "agency" and "amet" in pat.pattern
        )
        for bad in ("politseiametnik", "mitteametlik", "ametlikult",
                    "ametkond", "järelevalveametnik"):
            assert amet_pat.search(bad) is None, (
                f"*amet pattern wrongly matched derivational form {bad!r}"
            )
        for good in ("terviseamet", "statistikaametile", "maksuametit"):
            assert amet_pat.search(good) is not None, (
                f"*amet pattern failed to match valid form {good!r}"
            )


class TestIssue274GrantedByTies:
    """Issue #274: _select_granted_by is documented as returning "the
    majority source act". It must abstain on any tie for the top spot and on
    all-singleton spreads regardless of how many acts there are — the old
    guards only abstained when len(counts) > 3, leaking an arbitrary
    sort-order winner for 1-1, 2-2 and 3-way singleton cases."""

    def test_two_two_tie_returns_none(self):
        from extract_institutional_competence import _select_granted_by
        # Sort-order would have picked "estleg:AA"; the contract says abstain.
        assert _select_granted_by([
            "estleg:ZZ", "estleg:ZZ", "estleg:AA", "estleg:AA",
        ]) is None

    def test_three_way_singleton_tie_returns_none(self):
        from extract_institutional_competence import _select_granted_by
        assert _select_granted_by([
            "estleg:A", "estleg:B", "estleg:C",
        ]) is None

    def test_all_singleton_two_acts_returns_none(self):
        from extract_institutional_competence import _select_granted_by
        assert _select_granted_by(["estleg:A", "estleg:B"]) is None

    def test_strict_plurality_returned(self):
        from extract_institutional_competence import _select_granted_by
        # 2 vs 1 — a strict plurality, so the top act IS returned.
        assert _select_granted_by([
            "estleg:A", "estleg:A", "estleg:B",
        ]) == "estleg:A"
        # 3 vs 2 vs 1 — strict plurality even with >3 distinct counts seen.
        assert _select_granted_by([
            "estleg:A", "estleg:A", "estleg:A",
            "estleg:B", "estleg:B", "estleg:C",
        ]) == "estleg:A"

    def test_unanimous_and_sole_singleton_returned(self):
        from extract_institutional_competence import _select_granted_by
        assert _select_granted_by([
            "estleg:A", "estleg:A", "estleg:A",
        ]) == "estleg:A"
        assert _select_granted_by(["estleg:A"]) == "estleg:A"

    def test_non_estleg_refs_ignored(self):
        from extract_institutional_competence import _select_granted_by
        # Only estleg: refs count; with the noise stripped this is a sole
        # singleton.
        assert _select_granted_by([
            "http://example/x", "estleg:A", None,  # type: ignore[list-item]
        ]) == "estleg:A"
        # ... and with the noise stripped this is a 1-1 tie → abstain.
        assert _select_granted_by([
            "http://example/x", "estleg:A", "estleg:B",
        ]) is None


class TestIssue321MinisterInflectedForms:
    """Issue #321: the generic ``*minister`` pattern required the literal
    ``minister``, so Estonian oblique forms — built on the stem-changing
    genitive ``ministri`` (``ministril``/``ministrile``/``ministrit``/...),
    the MOST common case in competence clauses — never matched. The regex
    now also accepts the ``ministri`` genitive stem, and the de-inflection
    (``_collapse_minister_stem``) collapses every form to the nominative
    ``*minister`` slug so no inflated siblings are spawned."""

    def test_evidence_case_from_ticket(self):
        """The exact regression named in the issue: a genitive-stem oblique
        form previously yielded ``[]``."""
        from extract_institutional_competence import detect_institutions
        slugs = {s for _, s, _ in
                 detect_institutions("sotsiaalministril on õigus kehtestada")}
        assert "sotsiaalminister" in slugs

    @pytest.mark.parametrize("text,expected", [
        # Nominative still fires (no regression).
        ("sotsiaalminister kehtestab korra", "sotsiaalminister"),
        # Genitive stem + every common oblique case ending.
        ("haridusministri käskkiri jõustub", "haridusminister"),
        ("sotsiaalministril on õigus kehtestada", "sotsiaalminister"),
        ("haridusministrile esitatakse taotlus", "haridusminister"),
        ("sotsiaalministrilt küsitakse arvamust", "sotsiaalminister"),
        # NB: the generic ministry pattern requires a portfolio prefix
        # (``[a-zäöüõšž]+`` before ``minister``/``ministri``), so a BARE
        # standalone ``minister``/``ministri`` with no portfolio is not
        # detected — same as the pre-existing nominative behaviour. The
        # bare-stem de-inflection itself is covered separately via
        # ``normalize_iri_suffix`` below.
        ("sotsiaalministrit teavitatakse otsusest", "sotsiaalminister"),
        ("kaitseministriga kooskõlastatult", "kaitseminister"),
        ("rahandusministrisse puutuvad küsimused", "rahandusminister"),
        ("siseministriks nimetati isik", "siseminister"),
    ])
    def test_inflected_minister_detected_and_collapsed(self, text, expected):
        from extract_institutional_competence import detect_institutions
        slugs = {s for _, s, _ in detect_institutions(text)}
        assert expected in slugs, f"{text!r} -> {slugs!r}, expected {expected!r}"

    def test_all_minister_forms_collapse_to_one_slug(self):
        """Nominative + several oblique forms of the SAME ministry must
        normalize to a single slug (no Institution_sotsiaalministril
        sibling alongside Institution_sotsiaalminister)."""
        from extract_institutional_competence import normalize_iri_suffix
        forms = [
            "sotsiaalminister", "sotsiaalministri", "sotsiaalministril",
            "sotsiaalministrile", "sotsiaalministrilt", "sotsiaalministrit",
            "sotsiaalministriga", "sotsiaalministrisse", "sotsiaalministriks",
        ]
        slugs = {normalize_iri_suffix(f) for f in forms}
        assert slugs == {"sotsiaalminister"}, slugs

    @pytest.mark.parametrize("form,expected", [
        ("sotsiaalministril", "sotsiaalminister"),
        ("haridusministri", "haridusminister"),
        ("ministrit", "minister"),
    ])
    def test_normalize_iri_suffix_collapses_minister_inflection(
        self, form, expected
    ):
        from extract_institutional_competence import normalize_iri_suffix
        assert normalize_iri_suffix(form) == expected

    def test_ministeerium_forms_unaffected(self):
        """The ``minister`` genitive-stem handling must NOT swallow
        ``ministeerium`` forms (different lexeme; its genitive is the
        regular ``ministeeriumi``, de-inflected by the existing rule)."""
        from extract_institutional_competence import normalize_iri_suffix
        assert normalize_iri_suffix("siseministeerium") == "siseministeerium"
        assert normalize_iri_suffix("siseministeeriumi") == "siseministeerium"
        assert normalize_iri_suffix(
            "siseministeeriumile") == "siseministeerium"
        assert normalize_iri_suffix(
            "rahandusministeeriumiga") == "rahandusministeerium"

    def test_generic_minister_pattern_matches_genitive_stem(self):
        """Direct regex-level assertion: the generic ministry pattern must
        match the ``ministri`` genitive stem, not just literal ``minister``."""
        from extract_institutional_competence import GENERIC_PATTERNS
        minister_pat = next(
            pat for pat, label, _itype in GENERIC_PATTERNS
            if label == "ministry" and "minister" in pat.pattern
            and "ministeerium" not in pat.pattern
        )
        for form in ("sotsiaalminister", "sotsiaalministril",
                     "haridusministri", "sotsiaalministrit"):
            assert minister_pat.search(form) is not None, (
                f"generic minister pattern failed to match {form!r}"
            )


class TestIssue322CompetenceTypeHighestSpecificity:
    """Issue #322: detect_competence_type returned the FIRST list match, so
    a provision granting BOTH licensing and supervision was classified
    supervision-only (the supervision phrase sat above the licensing verb).
    It now collects every match and returns the highest-specificity type
    (licensing > supervision-phrase > enforcement > regulation >
    supervision-noun > general)."""

    def test_evidence_case_licensing_wins_over_supervision_phrase(self):
        """The exact regression named in the issue: a provision that BOTH
        grants a licence AND mentions a verb-adjacent supervision phrase
        previously collapsed to ``supervision``; it must now be
        ``licensing``."""
        from extract_institutional_competence import detect_competence_type
        assert detect_competence_type(
            "Amet annab loa ja teostab järelevalvet maksukohustuse üle"
        ) == "licensing"

    def test_licensing_wins_over_bare_jarelevalve_and_teostab(self):
        from extract_institutional_competence import detect_competence_type
        assert detect_competence_type(
            "Amet väljastab luba ja teostab järelevalve raames kontrolli"
        ) == "licensing"

    @pytest.mark.parametrize("text,expected", [
        # Verb-adjacent supervision phrase (also contains bare "teostab"
        # enforcement) must stay supervision when no licensing verb present.
        ("Amet teostab järelevalvet maksukohustuse üle", "supervision"),
        ("Amet järelevalvet teostab maksukohustuse üle", "supervision"),
        ("Amet teeb järelevalvet maksukohustuse üle", "supervision"),
        # Enforcement beats regulation per the ticket's specificity ladder.
        ("Asutus kehtestab korra ja kontrollib selle täitmist",
         "enforcement"),
        # Regulation beats the bare supervision noun.
        ("Minister kehtestab järelevalve korra", "regulation"),
        # Bare supervision noun is the weakest decisive signal.
        ("Järelevalve toimub seaduses sätestatud korras", "supervision"),
        # No operative verb at all → general.
        ("Käesolev säte kirjeldab üldist eesmärki", "general"),
    ])
    def test_specificity_ladder(self, text, expected):
        from extract_institutional_competence import detect_competence_type
        assert detect_competence_type(text) == expected, (
            f"{text!r} classified wrong"
        )

    def test_competence_patterns_are_ordered_by_descending_specificity(self):
        """detect_competence_type's early-skip relies on COMPETENCE_PATTERNS
        being sorted highest-specificity-first; guard that invariant."""
        from extract_institutional_competence import COMPETENCE_PATTERNS
        specificities = [spec for _pat, _ctype, spec in COMPETENCE_PATTERNS]
        assert specificities == sorted(specificities, reverse=True), (
            f"COMPETENCE_PATTERNS not in descending specificity order: "
            f"{specificities}"
        )


class TestIssue373LicensingTegevusluba:
    """Issue #373: COMPETENCE_PATTERNS had ``\\bannab\\s+loa\\b`` and
    ``\\bväljastab\\s+luba\\b`` but the ``\\bloa\\b`` / ``\\bluba\\b`` word
    boundary cannot match inside the compound ``tegevusloa`` /
    ``tegevusluba``, so 6 licensing-grant provisions fell through to the
    default ``general``. The compound forms are now explicit licensing
    patterns."""

    @pytest.mark.parametrize("text", [
        "Inspektsioon annab tegevusloa, kui nõuded on täidetud",
        "Amet väljastab tegevusloa taotlejale",
        # The grant verb may be separated from the compound by other words
        # but the immediate "annab tegevusloa" collocation is what fires.
        "Finantsinspektsioon annab tegevusloa krediidiasutusele",
    ])
    def test_tegevusluba_grant_is_licensing(self, text):
        from extract_institutional_competence import detect_competence_type
        assert detect_competence_type(text) == "licensing", (
            f"{text!r} should be licensing"
        )

    def test_tegevusluba_no_longer_falls_through_to_general(self):
        """Regression guard for the exact bug: without the new patterns this
        text classified ``general``."""
        from extract_institutional_competence import detect_competence_type
        assert detect_competence_type(
            "Inspektsioon annab tegevusloa, kui nõuded on täidetud"
        ) != "general"

    def test_plain_loa_still_licensing(self):
        """The original ``annab loa`` / ``väljastab luba`` forms must still
        classify as licensing (no regression)."""
        from extract_institutional_competence import detect_competence_type
        assert detect_competence_type("Amet annab loa taotlejale") == "licensing"
        assert detect_competence_type(
            "Amet väljastab luba taotlejale") == "licensing"

    def test_tegevusluba_patterns_present(self):
        """The compound-licensing patterns exist in COMPETENCE_PATTERNS."""
        from extract_institutional_competence import COMPETENCE_PATTERNS
        joined = " ".join(pat.pattern for pat, _ctype, _spec in COMPETENCE_PATTERNS)
        assert "tegevusloa" in joined, (
            "no 'tegevusloa' licensing pattern found in COMPETENCE_PATTERNS"
        )
        for pat, ctype, _spec in COMPETENCE_PATTERNS:
            if "tegevusloa" in pat.pattern:
                assert ctype == "licensing", (
                    f"tegevusloa pattern mapped to {ctype}, expected licensing"
                )


class TestIssue376AtomicSaveJson:
    """Issue #376: the module-local non-atomic ``save_json`` (which truncated
    the target to 0 bytes via ``open(filepath, 'w')`` before writing — a
    crash mid-write left a zero-byte/partial JSON that ``load_json`` silently
    swallowed) is replaced by the atomic ``estleg_common.save_json``
    (tempfile + ``os.replace``)."""

    def test_save_json_is_the_atomic_estleg_common_implementation(self):
        import extract_institutional_competence as mod
        import estleg_common
        assert mod.save_json is estleg_common.save_json

    def test_save_json_uses_atomic_replace_not_truncating_open(self, tmp_path):
        """A crash between truncate and write must not destroy existing
        data. The non-atomic ``open(path, 'w')`` zeroes the file before the
        new bytes land; the atomic version writes to a tempfile and
        ``os.replace``s, so a failed serialization leaves the ORIGINAL file
        intact."""
        import extract_institutional_competence as mod

        target = tmp_path / "inst.json"
        mod.save_json(target, {"@graph": [{"@id": "estleg:Inst_a"}]})
        original_bytes = target.read_bytes()

        class _Unserializable:
            pass

        # A non-serialisable payload raises mid-write. With a truncating
        # open() the file would already be 0 bytes; the atomic version must
        # leave the prior content untouched.
        with pytest.raises(TypeError):
            mod.save_json(target, {"bad": _Unserializable()})

        assert target.read_bytes() == original_bytes, (
            "atomic save_json must not corrupt the existing file on a "
            "failed write"
        )
        # And no stray tempfile droppings are left behind.
        leftovers = [p for p in tmp_path.iterdir() if p.name != "inst.json"]
        assert leftovers == [], f"tempfile droppings left behind: {leftovers}"

    def test_save_json_roundtrip(self, tmp_path):
        import json as _json
        import extract_institutional_competence as mod
        target = tmp_path / "out.json"
        doc = {"@context": {"estleg": "x"}, "@graph": [{"@id": "estleg:A"}]}
        mod.save_json(target, doc)
        assert _json.loads(target.read_text(encoding="utf-8")) == doc
        # Trailing newline, matching the prior local implementation's format.
        assert target.read_text(encoding="utf-8").endswith("\n")

    def test_save_json_monkeypatchable_on_module(self, tmp_path):
        """The gate test (test_gate_fails_when_kov_input_nontrivial_but_no_output)
        monkeypatches ``mod.save_json``; the production call sites must pick
        up the override. Verify the name is a rebindable module attribute and
        that bare-name calls resolve through it."""
        import extract_institutional_competence as mod
        calls = []
        original = mod.save_json
        try:
            mod.save_json = lambda fp, doc: calls.append((fp, doc))
            mod.save_json(tmp_path / "x.json", {"k": "v"})
            assert calls == [(tmp_path / "x.json", {"k": "v"})]
            assert not (tmp_path / "x.json").exists()
        finally:
            mod.save_json = original

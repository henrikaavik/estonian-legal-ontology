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

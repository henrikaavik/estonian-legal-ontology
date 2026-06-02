import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from generate_draft_legislation import detect_affected_laws, generate_draft_node


def test_generate_draft_node_emits_affected_law_names_as_strings():
    node = generate_draft_node(
        {
            "title": "Riigi Teataja seaduse muutmise seadus",
            "link": "https://eelnoud.valitsus.ee/main/mount/docList/12345678-1234-1234-1234-123456789abc?activity=2",
        },
        phase_id="Review",
        eis_number="JDM/26-0214",
        ministry_code="JDM",
        date_str="17.02.2026",
    )

    assert node["estleg:affectedLawName"]
    assert all(isinstance(value, str) for value in node["estleg:affectedLawName"])


# --------------------------------------------------------------------- #
# Regression tests for issue #266: detect_affected_laws must NOT emit the
# bill's own full title (".. muutmise seadus") as an affected law, because
# it resolves to the same IRI as the real target and yields duplicate
# ``amendsLaw`` IRIs downstream.
# --------------------------------------------------------------------- #


class TestDetectAffectedLawsDropsBillTitle:
    def test_amendment_title_yields_only_real_target(self):
        # The bare ``...seadus`` pattern would also match the whole bill
        # title "Riigi Teataja seaduse muutmise seadus". That candidate
        # carries the change-verb stem "muutmi" and must be dropped.
        result = detect_affected_laws("Riigi Teataja seaduse muutmise seadus")
        assert result == ["Riigi Teataja seaduse"], result
        # The bill title itself must not appear.
        assert "Riigi Teataja seaduse muutmise seadus" not in result
        # No candidate may still carry a change-verb stem.
        for name in result:
            assert "muutmi" not in name.lower()

    def test_supplements_title_yields_only_real_target(self):
        result = detect_affected_laws("Maksukorralduse seaduse täiendamise seadus")
        assert result == ["Maksukorralduse seaduse"], result
        for name in result:
            assert "täiendami" not in name.lower()

    def test_repeal_title_drops_kehtetuks_candidate(self):
        # The only candidate the bare-seadus pattern yields for this title is
        # the whole bill title, which carries the "kehtetuks" stem and must be
        # dropped — leaving no phantom. (These patterns capture a genitive
        # real-target only for muutmi/täiendami, so the result is empty here,
        # which is correct: no bill-title leak.)
        result = detect_affected_laws(
            "Mingi seaduse kehtetuks tunnistamise seadus"
        )
        assert "Mingi seaduse kehtetuks tunnistamise seadus" not in result
        for name in result:
            assert "kehtetuks" not in name.lower()

    def test_enacts_title_drops_kehtestami_candidate(self):
        result = detect_affected_laws("Mingi seaduse kehtestamise seadus")
        assert "Mingi seaduse kehtestamise seadus" not in result
        for name in result:
            assert "kehtestami" not in name.lower()

    def test_plain_law_title_still_detected(self):
        # A non-amendment title (no change verb) with a space before
        # "seadus" still resolves normally and is not dropped.
        result = detect_affected_laws("Alkoholi seadus")
        assert "Alkoholi seadus" in result

    def test_node_amends_law_has_no_duplicate_iri_inputs(self):
        # End-to-end on the node: affectedLawName must not contain both the
        # real target and the bill title (which would resolve to one IRI and
        # be appended twice in extract_draft_impact).
        node = generate_draft_node(
            {
                "title": "Riigi Teataja seaduse muutmise seadus",
                "link": "https://eelnoud.valitsus.ee/main/mount/docList/"
                "12345678-1234-1234-1234-123456789abc?activity=2",
            },
            phase_id="Review",
            eis_number="JDM/26-0214",
            ministry_code="JDM",
            date_str="17.02.2026",
        )
        names = node["estleg:affectedLawName"]
        # Exactly one distinct target; no duplicate that resolves to same IRI.
        assert len(names) == len(set(names))
        assert names == ["Riigi Teataja seaduse"], names


# --------------------------------------------------------------------- #
# Issue #295 — EELNOUD_INDEX.json must not embed a wall-clock ``generated``
# field, so reruns of the same inputs are byte-stable.
# --------------------------------------------------------------------- #


class TestIndexIsByteStable:
    _SYNTHETIC_ITEM = {
        "raw_title": "Alkoholiseaduse muutmise seadus - JDM/26-0001 (01.02.2026)",
        "title": "Alkoholiseaduse muutmise seadus",
        "link": "https://eelnoud.valitsus.ee/main/mount/docList/"
        "11111111-2222-3333-4444-555555555555?activity=1",
        "pub_date": "Mon, 02 Feb 2026 00:00:00 +0200",
    }

    def _run(self, tmp_path, monkeypatch):
        import generate_draft_legislation as mod

        eelnoud = tmp_path / "eelnoud"
        eelnoud.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(mod, "EELNOUD_DIR", eelnoud)
        # main()'s closing summary prints EELNOUD_DIR.relative_to(REPO_ROOT);
        # point REPO_ROOT at tmp_path so that stays valid under the tmp dir.
        monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
        # Only the first feed yields the synthetic item; the rest are empty
        # so the dedup key is hit once.
        calls = {"n": 0}

        def fake_fetch(_url):
            calls["n"] += 1
            return [dict(self._SYNTHETIC_ITEM)] if calls["n"] == 1 else []

        monkeypatch.setattr(mod, "fetch_rss", fake_fetch)
        mod.main()
        return eelnoud / "EELNOUD_INDEX.json"

    def test_index_has_no_generated_timestamp(self, tmp_path, monkeypatch):
        index_path = self._run(tmp_path, monkeypatch)
        index = json.loads(index_path.read_text(encoding="utf-8"))
        assert "generated" not in index, (
            "EELNOUD_INDEX.json must not embed a wall-clock 'generated' "
            "timestamp (#295 churn)"
        )
        # Sanity: the synthetic draft was still indexed.
        assert index["total_drafts"] == 1

    def test_two_runs_are_byte_identical(self, tmp_path, monkeypatch):
        first = self._run(tmp_path, monkeypatch).read_text(encoding="utf-8")
        second = self._run(tmp_path, monkeypatch).read_text(encoding="utf-8")
        assert first == second

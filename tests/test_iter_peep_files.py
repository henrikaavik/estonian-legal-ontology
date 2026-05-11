"""Behavioural tests for ``estleg_common.iter_peep_files``.

The legacy version of this file asserted hard counts against the real
``krr_outputs/`` corpus (``len(kov_files) > 1000``), which fails on a
clean clone, on CI without git-lfs, or any time the corpus drifts.
Issue #150 / Issue #142 calls for behavioural tests against an
isolated ``tmp_path`` so test outcomes track code behaviour, not
corpus shape.

The corpus invariant — "the live corpus has ≥1000 KOV peeps" — is
preserved as a marked ``@pytest.mark.corpus`` test that is SKIPPED by
default. Opt in with ``pytest -m corpus`` once a populated corpus is
available locally.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


import estleg_common


REPO_ROOT = Path(__file__).resolve().parent.parent
REAL_KRR_DIR = REPO_ROOT / "krr_outputs"


@pytest.fixture
def fake_krr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Stage a deterministic fake ``krr_outputs/`` for ``iter_peep_files``.

    Layout::

        tmp_krr/
          law_a_peep.json
          law_b_peep.json
          regulations/riik/state_a_peep.json
          regulations/riik/state_b_peep.json
          regulations/kov/abja_vald/kov_a_peep.json
          regulations/kov/elva_linn/kov_b_peep.json

    So we have exactly 2 KOV peeps, 2 state-regulation peeps, and
    2 top-level law peeps. The exact counts are tested below; this
    is intentional — the assertion checks that the ``include_kov``
    flag controls inclusion, not counts.
    """
    tmp_krr = tmp_path / "krr_outputs"
    riik = tmp_krr / "regulations" / "riik"
    kov = tmp_krr / "regulations" / "kov"
    riik.mkdir(parents=True, exist_ok=True)
    (kov / "abja_vald").mkdir(parents=True, exist_ok=True)
    (kov / "elva_linn").mkdir(parents=True, exist_ok=True)

    (tmp_krr / "law_a_peep.json").write_text("{}", encoding="utf-8")
    (tmp_krr / "law_b_peep.json").write_text("{}", encoding="utf-8")
    (riik / "state_a_peep.json").write_text("{}", encoding="utf-8")
    (riik / "state_b_peep.json").write_text("{}", encoding="utf-8")
    (kov / "abja_vald" / "kov_a_peep.json").write_text("{}", encoding="utf-8")
    (kov / "elva_linn" / "kov_b_peep.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(estleg_common, "KRR_DIR", tmp_krr)
    return tmp_krr


class TestIterPeepFilesDefault:
    def test_default_includes_kov(self, fake_krr: Path) -> None:
        """``iter_peep_files()`` (no args) MUST include KOV peeps after the
        Layer 2a default flip."""
        files = estleg_common.iter_peep_files()
        kov_files = [f for f in files if "regulations/kov" in f.as_posix()]
        assert len(kov_files) == 2, (
            f"expected exactly 2 KOV peeps under fake_krr; got {len(kov_files)}"
        )

    def test_explicit_include_kov_false_excludes(
        self, fake_krr: Path
    ) -> None:
        """Pinned call sites that pass ``include_kov=False`` MUST get a
        KOV-free file set."""
        files = estleg_common.iter_peep_files(include_kov=False)
        kov_files = [f for f in files if "regulations/kov" in f.as_posix()]
        assert kov_files == []

    def test_default_includes_state_regulations(
        self, fake_krr: Path
    ) -> None:
        """``include_regulations=True`` is the default; state regs
        under ``regulations/riik/`` MUST appear."""
        files = estleg_common.iter_peep_files()
        riik_files = [
            f for f in files if "regulations/riik" in f.as_posix()
        ]
        assert len(riik_files) == 2

    def test_default_includes_top_level_laws(self, fake_krr: Path) -> None:
        """Top-level ``*_peep.json`` files at KRR_DIR root represent enacted
        laws; they MUST appear when ``include_laws=True`` (the default)."""
        files = estleg_common.iter_peep_files()
        law_files = [
            f
            for f in files
            if f.parent == fake_krr and f.name.endswith("_peep.json")
        ]
        assert len(law_files) == 2

    def test_include_laws_false_excludes_top_level(
        self, fake_krr: Path
    ) -> None:
        files = estleg_common.iter_peep_files(include_laws=False)
        law_files = [
            f
            for f in files
            if f.parent == fake_krr and f.name.endswith("_peep.json")
        ]
        assert law_files == []

    def test_include_regulations_false_excludes_riik(
        self, fake_krr: Path
    ) -> None:
        files = estleg_common.iter_peep_files(include_regulations=False)
        riik_files = [
            f for f in files if "regulations/riik" in f.as_posix()
        ]
        kov_files = [f for f in files if "regulations/kov" in f.as_posix()]
        assert riik_files == []
        assert kov_files == [], (
            "include_regulations=False MUST also disable KOV; otherwise "
            "callers that turn off regulations would still see KOV peeps."
        )

    def test_results_are_sorted_deterministically(
        self, fake_krr: Path
    ) -> None:
        """The function MUST return paths in a deterministic order so
        downstream walks don't drift between runs."""
        files = estleg_common.iter_peep_files()
        sorted_files = sorted(files, key=lambda p: p.as_posix().casefold())
        assert files == sorted_files


@pytest.mark.corpus
class TestIterPeepFilesCorpus:
    """Hard-count invariants for the live ``krr_outputs/`` corpus.

    SKIPPED by default. Opt in with ``pytest -m corpus`` after
    confirming ``krr_outputs/regulations/kov/`` is populated.
    """

    def test_live_corpus_has_many_kov_peeps(self) -> None:
        if not REAL_KRR_DIR.exists():
            pytest.skip("krr_outputs/ not present in this checkout")
        # Use the real KRR_DIR via a fresh import path; do not
        # monkeypatch so we hit production data.
        files = estleg_common.iter_peep_files()
        kov_files = [
            f for f in files if "regulations/kov" in f.as_posix()
        ]
        assert len(kov_files) > 1000, (
            f"expected >1000 KOV peeps in live corpus; got {len(kov_files)}"
        )

    def test_live_corpus_has_many_riik_regulations(self) -> None:
        if not REAL_KRR_DIR.exists():
            pytest.skip("krr_outputs/ not present in this checkout")
        files = estleg_common.iter_peep_files()
        riik_files = [
            f for f in files if "regulations/riik" in f.as_posix()
        ]
        assert len(riik_files) > 100

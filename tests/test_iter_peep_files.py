import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pytest

from estleg_common import iter_peep_files


REPO_ROOT = Path(__file__).resolve().parent.parent
KRR_DIR = REPO_ROOT / "krr_outputs"


class TestIterPeepFilesDefault:
    def test_default_includes_kov(self):
        """After Layer 2a flips the default, calling iter_peep_files() with
        no arguments must include KOV files."""
        files = iter_peep_files()
        kov_files = [f for f in files if "regulations/kov" in str(f)]
        assert len(kov_files) > 1000, (
            f"expected KOV files in default iter; got {len(kov_files)}"
        )

    def test_explicit_include_kov_false_excludes(self):
        """Pinned call sites (Layer 2b/2c/3 deferrals) must still get a
        KOV-free file set when they pass include_kov=False."""
        files = iter_peep_files(include_kov=False)
        kov_files = [f for f in files if "regulations/kov" in str(f)]
        assert kov_files == []

    def test_default_includes_state_regulations(self):
        """include_regulations=True is still the default; state regs
        under regulations/riik/ should appear."""
        files = iter_peep_files()
        riik_files = [f for f in files if "regulations/riik" in str(f)]
        assert len(riik_files) > 100

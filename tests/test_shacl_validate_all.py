import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import shacl_validate_all


def touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")


def test_collect_files_splits_laws_and_state_regulations(tmp_path):
    krr = tmp_path / "krr_outputs"
    touch(krr / "law_peep.json")
    touch(krr / "regulations" / "riik" / "state_peep.json")
    touch(krr / "regulations" / "kov" / "issuer" / "kov_peep.json")
    (krr / "INDEX.json").write_text(
        json.dumps({"laws": [{"name": "law", "files": ["law_peep.json"]}]}),
        encoding="utf-8",
    )

    files = shacl_validate_all.collect_files("laws", krr=krr)

    assert files == [
        krr / "law_peep.json",
        krr / "regulations" / "riik" / "state_peep.json",
    ]


def test_kov_bucket_registered_and_resolves_kov_peep_files(tmp_path):
    # Guards the CI ``semantic-validation`` matrix entry: the ``kov``
    # bucket must exist and must collect municipal regulation peep
    # files from ``krr_outputs/regulations/kov/**`` (issue #105).
    assert "kov" in shacl_validate_all.BUCKETS

    krr = tmp_path / "krr_outputs"
    touch(krr / "regulations" / "kov" / "tallinn" / "rule_peep.json")
    touch(krr / "regulations" / "kov" / "tartu" / "nested" / "rule2_peep.json")

    files = shacl_validate_all.collect_files("kov", krr=krr)

    assert krr / "regulations" / "kov" / "tallinn" / "rule_peep.json" in files
    assert krr / "regulations" / "kov" / "tartu" / "nested" / "rule2_peep.json" in files
    assert all(p.name.endswith("_peep.json") for p in files)


def test_collect_files_splits_kov_with_registries(tmp_path):
    krr = tmp_path / "krr_outputs"
    touch(krr / "municipalities_peep.json")
    touch(krr / "issuers_kov_peep.json")
    touch(krr / "regulations" / "kov" / "issuer" / "kov_peep.json")
    touch(krr / "regulations" / "kov" / "REGULATIONS_KOV_INDEX.json")

    files = shacl_validate_all.collect_files("kov", krr=krr)

    assert files == [
        krr / "issuers_kov_peep.json",
        krr / "municipalities_peep.json",
        krr / "regulations" / "kov" / "issuer" / "kov_peep.json",
    ]


def test_collect_files_covers_semantic_corpus_buckets(tmp_path):
    krr = tmp_path / "krr_outputs"
    touch(krr / "law_peep.json")
    touch(krr / "regulations" / "riik" / "state_peep.json")
    touch(krr / "regulations" / "kov" / "issuer" / "kov_peep.json")
    touch(krr / "riigikohus" / "riigikohus_2026_peep.json")
    touch(krr / "eelnoud" / "eelnoud_review_peep.json")
    touch(krr / "eurlex" / "eurlex_regulations_peep.json")
    touch(krr / "curia" / "curia_judgments_peep.json")
    (krr / "INDEX.json").write_text(
        json.dumps({"laws": [{"name": "law", "files": ["law_peep.json"]}]}),
        encoding="utf-8",
    )

    files = shacl_validate_all.collect_files(all_buckets=True, krr=krr)

    assert files == sorted(
        {
            krr / "law_peep.json",
            krr / "regulations" / "riik" / "state_peep.json",
            krr / "regulations" / "kov" / "issuer" / "kov_peep.json",
            krr / "riigikohus" / "riigikohus_2026_peep.json",
            krr / "eelnoud" / "eelnoud_review_peep.json",
            krr / "eurlex" / "eurlex_regulations_peep.json",
            krr / "curia" / "curia_judgments_peep.json",
        }
    )

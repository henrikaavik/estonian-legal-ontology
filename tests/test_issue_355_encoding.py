"""#355 — U+FFFD in ProvisionVersion texts; decode RT XML without forcing UTF-8.

The old fetch did ``resp.encoding = "utf-8"`` and then ``resp.text``. On
windows-1257 / iso-8859-13 pre-2010 RT XML that replaces each Baltic high
byte with U+FFFD (``või`` → ``v\\ufffd\\ufffdi``). These tests call the
shipped ``decode_rt_xml_bytes`` / ``strip_or_repair_fffd_in_version_text``
helpers directly — they must not be mocked.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import generate_provision_versions as gpv
import validate_all
from generate_provision_versions import (
    decode_rt_xml_bytes,
    strip_or_repair_fffd_in_version_text,
)

FFFD = "\ufffd"
REPO_ROOT = Path(__file__).resolve().parent.parent


def test_forced_utf8_on_windows_1257_yields_fffd() -> None:
    """The #355 bug: forcing UTF-8 on Baltic bytes produces U+FFFD."""
    data = "käesolev või määrus".encode("windows-1257")
    forced = data.decode("utf-8", errors="replace")
    assert FFFD in forced
    assert "või" not in forced


def test_decode_rt_xml_bytes_recovers_windows_1257() -> None:
    payload = "käesolev või määrus"
    data = payload.encode("windows-1257")
    assert FFFD in data.decode("utf-8", errors="replace")
    assert decode_rt_xml_bytes(data) == payload
    assert FFFD not in decode_rt_xml_bytes(data)


def test_decode_rt_xml_bytes_recovers_iso_8859_13() -> None:
    payload = "üksnes pärast sätestatud"
    data = payload.encode("iso-8859-13")
    assert FFFD in data.decode("utf-8", errors="replace")
    assert decode_rt_xml_bytes(data) == payload


def test_decode_rt_xml_bytes_keeps_valid_utf8() -> None:
    payload = '<?xml version="1.0" encoding="UTF-8"?><akt>või Šveitsi</akt>'
    data = payload.encode("utf-8")
    assert decode_rt_xml_bytes(data) == payload


def test_decode_rt_xml_bytes_uses_declared_then_fallbacks() -> None:
    """A wrong declared charset that yields FFFD loses to windows-1257."""
    payload = "või"
    data = payload.encode("windows-1257")
    out = decode_rt_xml_bytes(
        data,
        declared_encoding="utf-8",
        apparent_encoding="utf-8",
    )
    assert out == payload
    assert FFFD not in out


def test_decode_rt_xml_bytes_already_corrupted_utf8_keeps_fffd() -> None:
    """Source that already contains U+FFFD as UTF-8 stays UTF-8 (ï¿½ loses)."""
    data = "või".replace("õ", FFFD * 2).encode("utf-8")
    assert b"\xef\xbf\xbd" in data
    out = decode_rt_xml_bytes(data)
    assert FFFD in out
    assert "ï¿½" not in out


def test_strip_or_repair_fffd_pair_voi() -> None:
    assert strip_or_repair_fffd_in_version_text(f"hoida v{FFFD}{FFFD}i ladustada") == (
        "hoida või ladustada"
    )


def test_strip_or_repair_fffd_kaesolev() -> None:
    assert (
        strip_or_repair_fffd_in_version_text(f"K{FFFD}{FFFD}esoleva seaduse")
        == "Käesoleva seaduse"
    )


def test_strip_or_repair_fffd_section_sign() -> None:
    assert strip_or_repair_fffd_in_version_text(f"seaduse {FFFD}{FFFD}38 lõikes") == (
        "seaduse §38 lõikes"
    )
    assert strip_or_repair_fffd_in_version_text(f"{FFFD}{FFFD}-le") == "§-le"


def test_strip_or_repair_fffd_endash_between_digits() -> None:
    assert strip_or_repair_fffd_in_version_text(f"RT I 1995, 26{FFFD}{FFFD}28, 355") == (
        "RT I 1995, 26–28, 355"
    )


def test_strip_or_repair_fffd_endash_between_tokens() -> None:
    assert strip_or_repair_fffd_in_version_text(f"mõju {FFFD}{FFFD}{FFFD} valitsev") == (
        "mõju – valitsev"
    )


def test_strip_or_repair_fffd_s_hacek() -> None:
    assert strip_or_repair_fffd_in_version_text(f"või {FFFD}veitsi Konföderatsioon") == (
        "või Šveitsi Konföderatsioon"
    )
    assert strip_or_repair_fffd_in_version_text(f"t{FFFD}eki või võlakirja") == (
        "tšeki või võlakirja"
    )
    assert strip_or_repair_fffd_in_version_text(f"du{FFFD}i kasutamine") == (
        "duši kasutamine"
    )


def test_strip_or_repair_fffd_mid_sentence_stays_lowercase() -> None:
    """Word-initial õ/ü/ä must not be title-cased mid-sentence."""
    assert (
        strip_or_repair_fffd_in_version_text(f"selle isiku {FFFD}{FFFD}iguste valdajale")
        == "selle isiku õiguste valdajale"
    )
    assert (
        strip_or_repair_fffd_in_version_text(f"ruumides ning {FFFD}ahtisuudmele lähemal")
        == "ruumides ning šahtisuudmele lähemal"
    )


def test_strip_or_repair_fffd_idempotent_on_clean_text() -> None:
    clean = "Käesoleva seaduse § 1 lõikes sätestatud või."
    assert strip_or_repair_fffd_in_version_text(clean) == clean


def test_cleanup_committed_rewrites_version_text(tmp_path: Path) -> None:
    versions = tmp_path / "provision_versions"
    versions.mkdir()
    doc = {
        "@graph": [
            {
                "@id": "estleg:X_Par_1_v1",
                "@type": ["estleg:ProvisionVersion"],
                "estleg:versionText": f"v{FFFD}{FFFD}i sätestatud",
                "rdfs:label": {"@value": f"X {FFFD}{FFFD}igus", "@language": "et"},
            }
        ]
    }
    path = versions / "x.jsonld"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    stats = gpv.cleanup_committed_provision_version_fffd(versions)
    assert stats["files"] == 1
    rewritten = json.loads(path.read_text(encoding="utf-8"))
    node = rewritten["@graph"][0]
    assert FFFD not in node["estleg:versionText"]
    assert node["estleg:versionText"] == "või sätestatud"
    assert FFFD not in node["rdfs:label"]["@value"]
    assert node["rdfs:label"]["@value"] == "X õigus"


def test_validate_provision_version_encoding_errors(tmp_path: Path) -> None:
    krr = tmp_path / "krr_outputs"
    (krr / "provision_versions").mkdir(parents=True)
    (krr / "provision_versions" / "x.jsonld").write_text(
        json.dumps(
            {
                "@graph": [
                    {
                        "@id": "estleg:X_Par_1_v1",
                        "@type": ["estleg:ProvisionVersion"],
                        "estleg:versionText": f"v{FFFD}{FFFD}i",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    validate_all.reset()
    validate_all.validate_provision_version_encoding(krr)
    assert any("#355" in e and "U+FFFD" in e for e in validate_all.errors)


def test_committed_provision_versions_have_no_fffd() -> None:
    """Corpus gate: the 428-node U+FFFD residue is gone."""
    versions = REPO_ROOT / "krr_outputs" / "provision_versions"
    leftover = []
    for path in versions.glob("*.jsonld"):
        text = path.read_text(encoding="utf-8")
        if FFFD in text:
            leftover.append((path.name, text.count(FFFD)))
    assert leftover == [], leftover[:8]


def test_committed_curia_labels_have_no_nbsp() -> None:
    curia = REPO_ROOT / "krr_outputs" / "curia"
    leftover = 0
    for path in curia.glob("*.jsonld"):
        leftover += path.read_text(encoding="utf-8").count("\u00a0")
    assert leftover == 0


def test_validate_provision_version_encoding_passes(tmp_path: Path) -> None:
    krr = tmp_path / "krr_outputs"
    (krr / "provision_versions").mkdir(parents=True)
    (krr / "provision_versions" / "x.jsonld").write_text(
        json.dumps(
            {
                "@graph": [
                    {
                        "@id": "estleg:X_Par_1_v1",
                        "@type": ["estleg:ProvisionVersion"],
                        "estleg:versionText": "või",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    validate_all.reset()
    validate_all.validate_provision_version_encoding(krr)
    assert validate_all.errors == []

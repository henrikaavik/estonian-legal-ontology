"""A multipart law's whole-act ``<base>_map`` peep joins its parts' chain group.

Since the 2026-10-09 Riigi Teataja refresh the map root carries the same
identity block (``estleg:globalId`` …) as any act root, so it pairs to the
act's XML on its own. Before the fix it formed a separate ``base_slug_of``
group and wrote ``amendments_<base>_map.json`` with the SAME node ids as
``amendments_<base>.json`` (the ids derive from the shared act prefix), which
``validate_all.py`` reported as cross-file @id collisions.
"""

from __future__ import annotations

import json
from pathlib import Path

from estleg import generate_amendment_history as mod


def _setup(tmp_path: Path, monkeypatch):
    from estleg import estleg_common

    krr = tmp_path / "krr_outputs"
    rt = tmp_path / "data" / "riigiteataja"
    krr.mkdir(parents=True)
    rt.mkdir(parents=True)
    (krr / "amendments").mkdir()
    (krr / "eelnoud").mkdir()
    (krr / "regulations" / "riik").mkdir(parents=True)
    (krr / "regulations" / "kov").mkdir(parents=True)

    monkeypatch.setattr(mod, "KRR_DIR", krr)
    monkeypatch.setattr(mod, "DATA_DIR", rt)
    monkeypatch.setattr(mod, "AMENDMENTS_DIR", krr / "amendments")
    monkeypatch.setattr(mod, "EELNOUD_DIR", krr / "eelnoud")
    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
    return krr, rt


def _write_peep(krr: Path, slug: str, act_id: str, gid: str | None) -> Path:
    node = {
        "@id": act_id,
        "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
        "rdfs:label": "Iksseadus",
        "dc:source": "Iksseadus",
    }
    if gid:
        node["estleg:globalId"] = gid
    path = krr / f"{slug}_peep.json"
    path.write_text(
        json.dumps({
            "@context": {"estleg": "https://w3id.org/estleg/",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [node],
        }),
        encoding="utf-8",
    )
    return path


def _write_xml(rt: Path, gid: str) -> None:
    (rt / f"reg_{gid}.xml").write_text(
        f'<akt globaalID="{gid}"><metaandmed>'
        "<muutmismarge><aktikuupaev>2020-01-01</aktikuupaev>"
        "<joustumine>2020-06-01</joustumine><avaldamismarge>"
        "<RTosa>I</RTosa><RTaasta>2020</RTaasta><RTnr>7</RTnr>"
        "</avaldamismarge></muutmismarge>"
        "</metaandmed></akt>",
        encoding="utf-8",
    )


class TestMapSlugGrouping:
    def test_map_and_parts_share_one_base_slug(self):
        slugs = ["x_osa1", "x_osa2", "x_map"]
        assert {mod.base_slug_of(s) for s in slugs} == {"x"}
        # Only a trailing ``_map`` is a multipart suffix.
        assert mod.base_slug_of("x_mapping") == "x_mapping"
        assert mod.base_slug_of("x_map_seadus") == "x_map_seadus"
        assert mod._amendment_chain_path("x").name == "amendments_x.json"

    def test_map_sorts_after_every_part(self):
        members = [(s, {}) for s in ("x_map", "x_osa10", "x_osa2", "x_osa1")]
        ordered = sorted(members, key=lambda m: mod._osa_order_key(m[0]))
        assert [s for s, _ in ordered] == ["x_osa1", "x_osa2", "x_osa10", "x_map"]

    def test_chain_targets_are_the_parts(self):
        members = [("x_osa1", {}), ("x_osa2", {}), ("x_map", {})]
        assert [s for s, _ in mod.chain_target_members(members)] == [
            "x_osa1", "x_osa2",
        ]
        # A map with no parts on disk is still a chain target.
        assert [s for s, _ in mod.chain_target_members([("y_map", {})])] == [
            "y_map",
        ]

    def test_obsolete_sweep_removes_stale_map_chain(self, tmp_path, monkeypatch):
        amendments = tmp_path / "amendments"
        amendments.mkdir()
        monkeypatch.setattr(mod, "AMENDMENTS_DIR", amendments)
        (amendments / "amendments_x.json").write_text("{}", encoding="utf-8")
        (amendments / "amendments_x_map.json").write_text("{}", encoding="utf-8")

        current = {mod.base_slug_of(s) for s in ("x_osa1", "x_osa2", "x_map")}
        removed = mod._remove_obsolete_amendment_chain_files(current)

        assert removed == 1
        assert sorted(p.name for p in amendments.iterdir()) == ["amendments_x.json"]


class TestMainWritesOneChainForMapAndParts:
    def test_main_writes_base_chain_and_removes_map_chain(
        self, tmp_path, monkeypatch
    ):
        krr, rt = _setup(tmp_path, monkeypatch)
        # Parts carry no globalId and have no legacy ``x.xml``, so (as for
        # Võlaõigusseadus after the refresh) only the map pairs to the XML.
        _write_peep(krr, "x_osa1", "estleg:X_Osa1", None)
        _write_peep(krr, "x_osa2", "estleg:X_Osa2", None)
        _write_peep(krr, "x_map", "estleg:X_Map", "4242")
        _write_xml(rt, "4242")
        stale = krr / "amendments" / "amendments_x_map.json"
        stale.write_text("{}", encoding="utf-8")

        assert mod.main() in (None, 0)

        chain_files = sorted(p.name for p in (krr / "amendments").glob("*.json"))
        assert chain_files == ["amendments_x.json"]

        chain = json.loads(
            (krr / "amendments" / "amendments_x.json").read_text(encoding="utf-8")
        )
        events = [
            n for n in chain["@graph"]
            if "estleg:AmendmentEvent" in (n.get("@type") or [])
        ]
        assert events, "the map's XML amendments must land in the base chain"
        for event in events:
            assert event["estleg:amends"] == [
                {"@id": "estleg:X_Osa1"}, {"@id": "estleg:X_Osa2"},
            ]

        def act_root(slug: str) -> dict:
            doc = json.loads((krr / f"{slug}_peep.json").read_text(encoding="utf-8"))
            return doc["@graph"][0]

        event_refs = [{"@id": e["@id"]} for e in events]
        assert act_root("x_osa1")["estleg:amendedBy"] == event_refs
        assert act_root("x_osa2")["estleg:amendedBy"] == event_refs
        assert "estleg:amendedBy" not in act_root("x_map")

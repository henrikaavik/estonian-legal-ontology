"""Tests for scripts/backfill_kov_regulation_typing.py (#424 KOV typing backfill).

The backfill strips the stray state-regulation ``@type`` (``estleg:NationalRegulation``
and, defensively, the two state subclasses) from KOV regulation act roots so they
are typed exactly as the post-#267 generator emits them: ``estleg:MunicipalRegulation``
+ ``estleg:Act`` (+ ``owl:Ontology``). It must be deterministic and idempotent.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import backfill_kov_regulation_typing as bkt


def _root(extra_types: list[str]) -> dict:
    """A KOV act root node with the given extra @type entries."""
    return {
        "@id": "estleg:Reg_999_Map_2026",
        "@type": ["owl:Ontology", *extra_types, "estleg:MunicipalRegulation", "estleg:Act"],
        "rdfs:label": "Test KOV reg (määrus)",
        "estleg:documentType": "määrus",
        "estleg:terviktekstId": "999",
        "estleg:isKov": {"@value": "true", "@type": "xsd:boolean"},
    }


class TestIsKovRoot:
    def test_ontology_plus_municipal_is_root(self):
        assert bkt.is_kov_root(_root(["estleg:NationalRegulation"])) is True

    def test_municipal_without_ontology_is_not_root(self):
        # A bare municipal node (no owl:Ontology) is not the act root.
        assert bkt.is_kov_root(
            {"@id": "estleg:X", "@type": ["estleg:MunicipalRegulation"]}
        ) is False

    def test_state_regulation_root_is_not_kov_root(self):
        # A state regulation root (no MunicipalRegulation) must be left alone.
        assert bkt.is_kov_root(
            {"@id": "estleg:X", "@type": ["owl:Ontology", "estleg:NationalRegulation"]}
        ) is False

    def test_provision_is_not_root(self):
        assert bkt.is_kov_root(
            {"@id": "estleg:Reg_999_Par_1",
             "@type": ["owl:NamedIndividual", "estleg:KovProvision"]}
        ) is False


class TestBackfillNode:
    def test_removes_national_regulation_and_sorts(self):
        node = _root(["estleg:NationalRegulation"])
        assert bkt.backfill_node(node) is True
        assert node["@type"] == ["estleg:Act", "estleg:MunicipalRegulation", "owl:Ontology"]
        assert "estleg:NationalRegulation" not in node["@type"]

    def test_removes_state_subclasses_defensively(self):
        # If a state-only subclass ever leaked onto a KOV root, scrub it too.
        node = _root(["estleg:NationalRegulation", "estleg:GovernmentRegulation",
                      "estleg:MinisterialRegulation"])
        assert bkt.backfill_node(node) is True
        assert node["@type"] == ["estleg:Act", "estleg:MunicipalRegulation", "owl:Ontology"]

    def test_preserves_municipal_and_act(self):
        node = _root(["estleg:NationalRegulation"])
        bkt.backfill_node(node)
        assert "estleg:MunicipalRegulation" in node["@type"]
        assert "estleg:Act" in node["@type"]

    def test_clean_root_is_noop(self):
        # Already municipal-only -> no change (idempotency at node level).
        node = _root([])
        assert bkt.backfill_node(node) is False

    def test_does_not_touch_non_root(self):
        prov = {"@id": "estleg:Reg_999_Par_1",
                "@type": ["owl:NamedIndividual", "estleg:KovProvision"]}
        assert bkt.backfill_node(prov) is False
        assert prov["@type"] == ["owl:NamedIndividual", "estleg:KovProvision"]

    def test_does_not_touch_state_regulation_root(self):
        # A genuine state regulation root keeps its NationalRegulation type.
        state = {"@id": "estleg:Reg_1_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Act", "estleg:NationalRegulation",
                           "estleg:GovernmentRegulation"]}
        assert bkt.backfill_node(state) is False
        assert "estleg:NationalRegulation" in state["@type"]

    def test_string_type_is_handled(self):
        # A node whose @type is a bare string (not the root) is a no-op.
        node = {"@id": "estleg:X", "@type": "estleg:MunicipalRegulation"}
        assert bkt.backfill_node(node) is False


class TestProcessFile:
    def _write(self, path: Path, graph: list) -> None:
        path.write_text(
            json.dumps({"@context": {"estleg": "https://w3id.org/estleg/"},
                        "@graph": graph}, ensure_ascii=False),
            encoding="utf-8",
        )

    def test_scrubs_root_and_leaves_provisions(self, tmp_path):
        f = tmp_path / "x_peep.json"
        self._write(f, [
            _root(["estleg:NationalRegulation"]),
            {"@id": "estleg:Reg_999_Par_1",
             "@type": ["owl:NamedIndividual", "estleg:KovProvision"],
             "estleg:paragrahv": "§ 1"},
        ])
        changed, total = bkt.process_file(f, dry_run=False)
        assert (changed, total) == (1, 1)
        doc = json.loads(f.read_text(encoding="utf-8"))
        root = doc["@graph"][0]
        prov = doc["@graph"][1]
        assert "estleg:NationalRegulation" not in root["@type"]
        assert root["@type"] == ["estleg:Act", "estleg:MunicipalRegulation", "owl:Ontology"]
        # Provision untouched.
        assert prov["@type"] == ["owl:NamedIndividual", "estleg:KovProvision"]

    def test_dry_run_writes_nothing(self, tmp_path):
        f = tmp_path / "x_peep.json"
        self._write(f, [_root(["estleg:NationalRegulation"])])
        before = f.read_text(encoding="utf-8")
        changed, total = bkt.process_file(f, dry_run=True)
        assert changed == 1
        assert f.read_text(encoding="utf-8") == before  # unchanged on disk

    def test_idempotent_second_run_is_byte_stable(self, tmp_path):
        f = tmp_path / "x_peep.json"
        self._write(f, [_root(["estleg:NationalRegulation"])])
        # First real run mutates.
        assert bkt.process_file(f, dry_run=False) == (1, 1)
        after_first = f.read_text(encoding="utf-8")
        # Second run is a no-op and does not rewrite the file.
        assert bkt.process_file(f, dry_run=False) == (0, 1)
        assert f.read_text(encoding="utf-8") == after_first

    def test_non_dict_doc_is_skipped(self, tmp_path):
        # An accidental list-shaped JSON-LD doc must not crash.
        f = tmp_path / "weird_peep.json"
        f.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        assert bkt.process_file(f, dry_run=False) == (0, 0)


class TestCollectAndMain:
    def _make_kov_tree(self, tmp_path: Path) -> Path:
        kov = tmp_path / "regulations" / "kov"
        (kov / "abja_vallavolikogu").mkdir(parents=True)
        (kov / "aegviidu_vallavolikogu").mkdir(parents=True)
        for sub, name in (("abja_vallavolikogu", "a_peep.json"),
                          ("aegviidu_vallavolikogu", "b_peep.json")):
            (kov / sub / name).write_text(
                json.dumps({"@context": {"estleg": "https://w3id.org/estleg/"},
                            "@graph": [_root(["estleg:NationalRegulation"])]},
                           ensure_ascii=False),
                encoding="utf-8",
            )
        # An index file the glob/filter must ignore.
        (kov / "REGULATIONS_KOV_INDEX.json").write_text('{"index": true}', encoding="utf-8")
        return kov

    def test_collect_excludes_index_file(self, tmp_path):
        kov = self._make_kov_tree(tmp_path)
        files = bkt.collect_kov_peep_files(kov)
        assert len(files) == 2
        assert all(p.name.endswith("_peep.json") for p in files)
        assert all(p.name != "REGULATIONS_KOV_INDEX.json" for p in files)

    def test_main_scrubs_whole_tree(self, tmp_path, capsys):
        kov = self._make_kov_tree(tmp_path)
        rc = bkt.main(["--kov-dir", str(kov)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "scrubbed stray state-regulation @type from 2 root(s)" in out
        for p in bkt.collect_kov_peep_files(kov):
            doc = json.loads(p.read_text(encoding="utf-8"))
            assert "estleg:NationalRegulation" not in doc["@graph"][0]["@type"]

    def test_main_dry_run_reports_but_does_not_write(self, tmp_path, capsys):
        kov = self._make_kov_tree(tmp_path)
        before = {p: p.read_text(encoding="utf-8") for p in bkt.collect_kov_peep_files(kov)}
        rc = bkt.main(["--kov-dir", str(kov), "--dry-run"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "would scrub stray state-regulation @type from 2 root(s)" in out
        for p, text in before.items():
            assert p.read_text(encoding="utf-8") == text

    def test_main_empty_tree_is_clean_exit(self, tmp_path, capsys):
        empty = tmp_path / "regulations" / "kov"
        empty.mkdir(parents=True)
        rc = bkt.main(["--kov-dir", str(empty)])
        assert rc == 0
        assert "No KOV peep files found" in capsys.readouterr().out

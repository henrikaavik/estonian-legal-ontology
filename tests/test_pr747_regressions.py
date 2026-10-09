"""Release failures found in PR #747 review."""

import gzip
import json

import pytest
from rdflib import Graph
from rdflib.compare import isomorphic

from estleg import build_release_assets as bra
from estleg import link_amendment_versions as lav
from estleg.serialize_corpus import graph_from_jsonld, serialize_jsonld_streaming
from estleg.serialize_named_graphs import write_nquads_gz


def test_streamed_turtle_preserves_shared_blank_node(tmp_path):
    doc = {"@graph": [
        {"@id": "https://example.org/a", "https://example.org/p": {"@id": "_:shared"}},
        {"@id": "_:shared", "https://example.org/q": "value"},
    ]}
    source, target = tmp_path / "source.jsonld", tmp_path / "out.ttl"
    source.write_text(json.dumps(doc))
    serialize_jsonld_streaming(source, {"ttl": target}, batch_size=1)
    assert isomorphic(Graph().parse(target, format="turtle"), graph_from_jsonld(doc))


def test_named_graph_gzip_has_no_filename_or_timestamp(tmp_path):
    line = '<https://example.org/a> <https://example.org/p> "x" <https://example.org/g> .\n'
    first, second = tmp_path / "first.gz", tmp_path / "second.gz"
    write_nquads_gz(first, [line])
    write_nquads_gz(second, [line])
    assert first.read_bytes() == second.read_bytes()
    assert first.read_bytes()[4:8] == b"\0" * 4
    assert gzip.decompress(first.read_bytes()).decode() == line


@pytest.mark.parametrize("value", ["not json", "[]", "{}"])
def test_present_invalid_amendment_input_fails(tmp_path, value):
    path = tmp_path / "law.jsonld"
    path.write_text(value)
    with pytest.raises(ValueError, match="law.jsonld"):
        lav._load(path)


def test_incomplete_sums_cannot_pass_release_gate(tmp_path):
    (tmp_path / bra.SUMS_NAME).write_text("")
    checked = bra.verify_sums(tmp_path, require_complete=True)
    assert checked["missing"] or checked["mismatched"]


@pytest.mark.parametrize("name", ["../LICENSE", "/tmp/LICENSE", "", "subdir/LICENSE"])
def test_checksum_names_must_be_top_level_assets(tmp_path, name):
    sums = tmp_path / bra.SUMS_NAME
    sums.write_text("0" * 64 + "  " + name + "\n")
    with pytest.raises(ValueError):
        bra.parse_sums(sums)


def test_cleanup_preserves_unrelated_files(tmp_path):
    notes = tmp_path / "notes.txt"
    notes.write_text("user notes")
    (tmp_path / bra.CHUNKS_ASSET).write_text("old asset")
    bra.clean_release_dir(tmp_path)
    assert notes.read_text() == "user notes"
    assert not (tmp_path / bra.CHUNKS_ASSET).exists()

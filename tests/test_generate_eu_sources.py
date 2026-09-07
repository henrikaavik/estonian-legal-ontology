from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from estleg import eurlex_common
from estleg import generate_eu_court_decisions as curia
from estleg import generate_eu_legislation as eurlex
from estleg import generate_harmonisation_links as harmonisation


def test_eurlex_pagination_query_is_ordered(monkeypatch):
    queries: list[str] = []

    def fake_query(query: str) -> list[dict]:
        queries.append(query)
        return []

    monkeypatch.setattr(eurlex, "sparql_query", fake_query)

    items, partial = eurlex.fetch_legislation_type("cdm:directive")
    assert items == []
    assert partial is False
    assert "ORDER BY ?work" in queries[0]


def test_eurlex_in_force_accepts_boolean_literals():
    assert eurlex.is_in_force_value("1")
    assert eurlex.is_in_force_value("true")
    assert eurlex.is_in_force_value("True")
    assert not eurlex.is_in_force_value("0")


def test_curia_pagination_query_is_ordered(monkeypatch):
    queries: list[str] = []

    def fake_query(query: str) -> list[dict]:
        queries.append(query)
        return []

    monkeypatch.setattr(curia, "sparql_query", fake_query)

    items, partial = curia.fetch_all_case_law()
    assert items == []
    assert partial is False
    assert "ORDER BY ?work" in queries[0]


def test_curia_classifies_efta_and_newer_order_codes():
    # Sector E (EFTA Court) must NOT be lumped into the CJEU bucket — the
    # EFTA Court is a separate institution and this test guards the fix
    # for the original mis-classification.
    assert curia.classify_from_celex("E2024CB0001") == (
        "Order",
        "Kohtumäärus",
        "EFTACourt",
        "orders",
    )
    assert "EFTACourt" in curia.EU_COURTS, "EFTA Court must be a known court"
    assert curia.classify_from_celex("62024CD0001")[0] == "Order"
    # Sector 6 should still resolve to a CJEU formation, not EFTA.
    assert curia.classify_from_celex("62024CD0001")[2] != "EFTACourt"


def test_curia_preserves_full_title_separately_from_label():
    long_title = "Kohtuasi C-1/24#" + ("Väga pikk pealkiri " * 40)
    node = curia.decision_to_node({"celex": "62024CJ0001", "title": long_title})

    assert len(node["rdfs:label"]["@value"]) <= 500
    assert node["dcterms:title"]["@value"] == curia.clean_title(long_title)


def test_curia_extract_case_number_parses_efta_prefix():
    """#296: EFTA case numbers (CELEX sector ``E`` → EFTACourt) start with
    ``E-``; the prefix class must include ``E`` or ``euCaseNumber`` is empty
    even though the decision classifies as an EFTA Court decision."""
    assert curia.extract_case_number("Kohtuasi E-1/20") == "E-1/20"
    # Bare (no "Kohtuasi") and joined-case forms also parse.
    assert curia.extract_case_number("E-9/14") == "E-9/14"
    assert (
        curia.extract_case_number("Liidetud kohtuasjad E-3/13 ja E-20/13")
        == "E-3/13 ja E-20/13"
    )
    # Existing CJEU prefixes still parse (no regression).
    assert curia.extract_case_number("Kohtuasi C-438/14") == "C-438/14"


def test_curia_emits_valid_document_date():
    """A well-formed YYYY-MM-DD date is emitted as an xsd:date literal."""
    node = curia.decision_to_node(
        {"celex": "62024CJ0001", "title": "Kohtuasi C-1/24", "date": "2024-03-15"}
    )
    assert node["estleg:documentDate"] == {"@value": "2024-03-15", "@type": "xsd:date"}


@pytest.mark.parametrize(
    "bad_date",
    ["15.03.2024", "2024-13-01", "2024-03", "not-a-date", "2024/03/15"],
)
def test_curia_skips_malformed_document_date(bad_date, capsys):
    """#296: a non-YYYY-MM-DD CELLAR date must be skipped (never emitted into
    an xsd:date literal, which would break SHACL) and logged — mirroring the
    RK ``decisionDate`` guard. The rest of the node is still produced."""
    node = curia.decision_to_node(
        {"celex": "62024CJ0001", "title": "Kohtuasi C-1/24", "date": bad_date}
    )
    assert "estleg:documentDate" not in node
    # Node is otherwise intact.
    assert node["estleg:celexNumber"] == "62024CJ0001"
    # The skip is surfaced on stderr.
    captured = capsys.readouterr()
    assert "documentDate" in captured.err
    assert bad_date in captured.err


def test_harmonisation_query_is_ordered(monkeypatch, tmp_path):
    queries: list[str] = []

    def fake_query(query: str) -> list[dict]:
        queries.append(query)
        return []

    monkeypatch.setattr(harmonisation, "sparql_query", fake_query)
    # Hermeticity: redirect the on-disk cache to a fresh tmpdir so a
    # cached response from another test (or a real run) cannot mask
    # the SPARQL call this test is verifying.
    monkeypatch.setattr(harmonisation, "CACHE_DIR", tmp_path / "cache")

    assert harmonisation.fetch_other_transpositions("32000L0001") == []
    assert "ORDER BY ?country ?celex_nat" in queries[0]


def test_harmonisation_query_uses_nim_vocab(monkeypatch, tmp_path):
    """#197: the query must use the real CDM National-Implementing-Measure
    vocabulary, not the non-existent ``cdm:resource_legal_measures_transposition_*``
    properties that returned zero rows for every directive."""
    queries: list[str] = []

    def fake_query(query: str) -> list[dict]:
        queries.append(query)
        return []

    monkeypatch.setattr(harmonisation, "sparql_query", fake_query)
    monkeypatch.setattr(harmonisation, "CACHE_DIR", tmp_path / "cache")

    harmonisation.fetch_other_transpositions("32016L0680")
    q = queries[0]
    assert "cdm:measure_national_implementing_implements_directive" in q
    assert "cdm:measure_national_implementing_implemented_by_country" in q
    assert "cdm:resource_legal_measures_transposition_for" not in q
    assert "cdm:resource_legal_measures_transposition_by" not in q


def test_harmonisation_parses_nim_rows(monkeypatch, tmp_path):
    """#197: NIM bindings (country / national CELEX / title / date) are parsed
    into the expected per-measure record shape, and rows for countries outside
    the target set are dropped."""
    def fake_query(_query: str) -> list[dict]:
        def b(country, celex, title=None, date=None, nim=None):
            row = {
                "country": {"value": country},
                "celex_nat": {"value": celex},
            }
            if title:
                row["title"] = {"value": title}
            if date:
                row["date"] = {"value": date}
            if nim:
                row["nim"] = {"value": nim}
            return row

        return [
            b("LVA", "72016L0680LV01", title="Latvian transposition", date="2018-05-04"),
            b("FIN", "72016L0680FI01", title="Suomen täytäntöönpano"),
            b("DEU", "72016L0680DE01", title="should be dropped — not a target country"),
            b("LVA", "72016L0680LV01"),  # duplicate (country, celex) — deduped
        ]

    monkeypatch.setattr(harmonisation, "sparql_query", fake_query)
    monkeypatch.setattr(harmonisation, "CACHE_DIR", tmp_path / "cache")

    rows = harmonisation.fetch_other_transpositions("32016L0680")
    assert [(r["country_code"], r["celex_nat"]) for r in rows] == [
        ("LVA", "72016L0680LV01"),
        ("FIN", "72016L0680FI01"),
    ]
    lv = rows[0]
    assert lv["country_en"] == "Latvia" and lv["country_et"] == "Läti"
    assert lv["title"] == "Latvian transposition" and lv["date"] == "2018-05-04"
    fi = rows[1]
    assert fi["country_en"] == "Finland" and fi["title"] == "Suomen täytäntöönpano"
    assert "date" not in fi  # OPTIONAL ?date was unbound


def test_harmonisation_report_is_non_empty():
    """#197: the committed harmonisation report must actually contain parallel
    measures (the whole point — it was {0 parallels} before the NIM-vocab fix)."""
    report_path = (
        Path(__file__).resolve().parent.parent
        / "krr_outputs" / "harmonisation" / "harmonisation_report.json"
    )
    assert report_path.exists(), "harmonisation report not generated"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["total_parallel_measures"] > 0
    assert report["directives_with_parallels"] > 0


def test_harmonisation_resolves_real_law_iri(tmp_path, monkeypatch):
    krr = tmp_path / "krr_outputs"
    law = krr / "law_peep.json"
    law.parent.mkdir(parents=True, exist_ok=True)
    law.write_text(
        '{"@graph": [{"@id": "estleg:AS_Map", "@type": ["owl:Ontology"]}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(harmonisation, "KRR_DIR", krr)

    assert harmonisation.get_law_harmonisation_target_iri("law_peep.json") == "estleg:AS_Map"


# ---------------------------------------------------------------------------
# SPARQL retry/terminal-failure behaviour (Findings 1, 8)
# ---------------------------------------------------------------------------


def _eventual_success_query(failures: int, payload: list[dict]):
    """Build a fake ``sparql_query`` that raises ``failures`` times then returns ``payload``."""
    state = {"calls": 0}

    def fake(query: str) -> list[dict]:
        state["calls"] += 1
        if state["calls"] <= failures:
            raise RuntimeError(f"transient 503 (call {state['calls']})")
        return payload

    return fake, state


def test_sparql_retry_recovers_after_transient_failures(monkeypatch):
    payload = [
        {"celex": {"value": "32024L0001"}, "work": {"value": "x"}, "title": {"value": "T"}}
    ]
    fake, state = _eventual_success_query(failures=2, payload=payload)
    monkeypatch.setattr(eurlex, "sparql_query", fake)
    # No real backoff sleep during tests.
    monkeypatch.setattr(eurlex.time, "sleep", lambda _: None)

    items, partial = eurlex.fetch_legislation_type("cdm:directive")
    assert partial is False
    assert state["calls"] >= 3, "expected retry to consume two failures plus a success"
    assert any(item["celex"] == "32024L0001" for item in items)


def test_sparql_terminal_failure_propagates_without_allow_partial(monkeypatch):
    def always_fail(query: str) -> list[dict]:
        raise RuntimeError("EUR-Lex 5xx storm")

    monkeypatch.setattr(eurlex, "sparql_query", always_fail)
    monkeypatch.setattr(eurlex.time, "sleep", lambda _: None)

    with pytest.raises(RuntimeError, match="sparql_query failed after"):
        eurlex.fetch_legislation_type("cdm:directive")


def test_sparql_terminal_failure_partial_run_when_allowed(monkeypatch):
    def always_fail(query: str) -> list[dict]:
        raise RuntimeError("EUR-Lex 5xx storm")

    monkeypatch.setattr(eurlex, "sparql_query", always_fail)
    monkeypatch.setattr(eurlex.time, "sleep", lambda _: None)

    items, partial = eurlex.fetch_legislation_type(
        "cdm:directive", allow_partial=True
    )
    assert items == []
    assert partial is True


def test_curia_sparql_terminal_failure_propagates(monkeypatch):
    def always_fail(query: str) -> list[dict]:
        raise RuntimeError("EUR-Lex 503")

    monkeypatch.setattr(curia, "sparql_query", always_fail)
    monkeypatch.setattr(curia.time, "sleep", lambda _: None)

    with pytest.raises(RuntimeError, match="sparql_query failed after"):
        curia.fetch_all_case_law()


def test_harmonisation_sparql_terminal_failure_propagates(monkeypatch, tmp_path):
    def always_fail(query: str) -> list[dict]:
        raise RuntimeError("EUR-Lex 5xx")

    monkeypatch.setattr(harmonisation, "sparql_query", always_fail)
    monkeypatch.setattr(harmonisation.time, "sleep", lambda _: None)
    monkeypatch.setattr(harmonisation, "CACHE_DIR", tmp_path / "cache")

    with pytest.raises(RuntimeError, match="sparql_query failed after"):
        harmonisation.fetch_other_transpositions("32000L0001", use_cache=False)


# ---------------------------------------------------------------------------
# is_in_force defensive coercion (Finding 5)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value, expected",
    [
        (None, False),
        (True, True),
        (False, False),
        ("TRUE", True),
        ("yes", True),
        ("y", True),
        ("1", True),
        ("0", False),
        ("", False),
        ("false", False),
        ("  True  ", True),
    ],
)
def test_eurlex_in_force_defensive_coercion(value, expected):
    assert eurlex.is_in_force_value(value) is expected


# ---------------------------------------------------------------------------
# Harmonisation freshness gate (Finding 6)
# ---------------------------------------------------------------------------


def _build_date():
    # #592: freshness is measured pin-to-pin against the CURRENT
    # BUILD_EVALUATION_DATE, not wall-clock now(). Derive test dates from it so
    # the tests are deterministic and don't drift with the calendar.
    return datetime.fromisoformat(harmonisation.BUILD_EVALUATION_DATE).date()


def test_harmonisation_freshness_passes_when_fresh(monkeypatch, capsys):
    fresh = _build_date().isoformat()
    # Should NOT call sys.exit; capture-only behaviour.
    harmonisation._check_mapping_freshness(
        {"generated": fresh}, threshold_days=30, allow_stale=False
    )
    captured = capsys.readouterr()
    assert fresh in captured.out


def test_harmonisation_freshness_pinned_generated_is_not_a_time_bomb(capsys):
    # #592 regression: the committed transposition_mapping.json carries
    # generated == BUILD_EVALUATION_DATE. Under the old now()-based comparison
    # this gate hard-failed CI ~threshold days after the pin with zero changes.
    # Pin-to-pin must report 0 days behind and never exit, independent of today.
    harmonisation._check_mapping_freshness(
        {"generated": harmonisation.BUILD_EVALUATION_DATE},
        threshold_days=30,
        allow_stale=False,
    )
    out = capsys.readouterr().out
    assert "0d behind build snapshot" in out


def test_harmonisation_freshness_fails_when_stale(monkeypatch):
    stale = (_build_date() - timedelta(days=120)).isoformat()
    with pytest.raises(SystemExit) as exc_info:
        harmonisation._check_mapping_freshness(
            {"generated": stale}, threshold_days=30, allow_stale=False
        )
    assert exc_info.value.code == 1


def test_harmonisation_freshness_warns_with_allow_stale(monkeypatch, capsys):
    stale = (_build_date() - timedelta(days=120)).isoformat()
    # Should NOT raise SystemExit when allow_stale=True; should print WARNING.
    harmonisation._check_mapping_freshness(
        {"generated": stale}, threshold_days=30, allow_stale=True
    )
    captured = capsys.readouterr()
    assert "WARNING" in captured.out


def test_harmonisation_freshness_handles_missing_timestamp():
    # No ``generated`` key at all should fail without --allow-stale-mapping.
    with pytest.raises(SystemExit):
        harmonisation._check_mapping_freshness(
            {}, threshold_days=30, allow_stale=False
        )


# ---------------------------------------------------------------------------
# Harmonisation cache (Finding 7)
# ---------------------------------------------------------------------------


def test_harmonisation_cache_hit_skips_sparql(monkeypatch, tmp_path):
    """Second call within TTL must read the on-disk cache and skip SPARQL."""
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(harmonisation, "CACHE_DIR", cache_dir)

    call_count = {"n": 0}

    def fake_query(query: str) -> list[dict]:
        call_count["n"] += 1
        return [
            {
                "country": {"value": "LVA"},
                "celex_nat": {"value": "72024A0042"},
            }
        ]

    monkeypatch.setattr(harmonisation, "sparql_query", fake_query)

    # First call: SPARQL is hit and result is cached.
    first = harmonisation.fetch_other_transpositions("32000L0001")
    assert call_count["n"] == 1
    assert first and first[0]["celex_nat"] == "72024A0042"

    # The cache file should now exist on disk. The on-disk path is whatever
    # ``_cache_path_for`` computes — an injective function of the CELEX dir +
    # the active TARGET_COUNTRIES set (#606), not the bare ``<celex>.json``.
    cached_file = harmonisation._cache_path_for("32000L0001")
    assert cached_file.exists()

    # Second call should NOT increment call_count — pure cache hit.
    second = harmonisation.fetch_other_transpositions("32000L0001")
    assert call_count["n"] == 1, "expected zero additional SPARQL calls on cache hit"
    assert second == first


def test_harmonisation_cache_bypassed_with_use_cache_false(monkeypatch, tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    monkeypatch.setattr(harmonisation, "CACHE_DIR", cache_dir)
    # Pre-seed the cache at the REAL lookup path (so use_cache=True would read
    # it) with a sentinel value; ``use_cache=False`` must still bypass it (#606
    # changed the path scheme, so derive it via ``_cache_path_for``).
    harmonisation._cache_path_for("32000L0001").write_text(
        json.dumps([{"country_code": "LTU", "celex_nat": "STALE"}]), encoding="utf-8"
    )

    fresh_payload = [
        {
            "country": {"value": "LVA"},
            "celex_nat": {"value": "FRESH"},
        }
    ]

    def fake_query(query: str) -> list[dict]:
        return fresh_payload

    monkeypatch.setattr(harmonisation, "sparql_query", fake_query)

    result = harmonisation.fetch_other_transpositions(
        "32000L0001", use_cache=False
    )
    assert result and result[0]["celex_nat"] == "FRESH"


# ---------------------------------------------------------------------------
# Harmonisation graph[0] guard (Finding 9)
# ---------------------------------------------------------------------------


def test_harmonisation_graph0_guard_rejects_provision_only_law(tmp_path, monkeypatch):
    """Files whose graph contains only provision-level nodes must return None."""
    krr = tmp_path / "krr_outputs"
    law = krr / "provision_only_peep.json"
    law.parent.mkdir(parents=True, exist_ok=True)
    # The graph[0] node is a provision (NOT an act-level type) — under the
    # tightened guard, the resolver must refuse to fall back to it.
    law.write_text(
        json.dumps(
            {
                "@graph": [
                    {
                        "@id": "estleg:Provision_AS_p1",
                        "@type": ["estleg:Provision"],
                        "rdfs:label": "Section 1",
                    },
                    {
                        "@id": "estleg:Provision_AS_p2",
                        "@type": ["estleg:Provision"],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(harmonisation, "KRR_DIR", krr)

    assert harmonisation.get_law_harmonisation_target_iri("provision_only_peep.json") is None


def test_harmonisation_graph0_guard_accepts_act_level_first_node(tmp_path, monkeypatch):
    """If graph[0] *is* an act-level node (no owl:Ontology), the fallback accepts it."""
    krr = tmp_path / "krr_outputs"
    law = krr / "act_level_first_peep.json"
    law.parent.mkdir(parents=True, exist_ok=True)
    law.write_text(
        json.dumps(
            {
                "@graph": [
                    {
                        "@id": "estleg:AS_Act",
                        "@type": ["estleg:Act"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(harmonisation, "KRR_DIR", krr)

    assert (
        harmonisation.get_law_harmonisation_target_iri("act_level_first_peep.json")
        == "estleg:AS_Act"
    )


def test_harmonisation_graph0_guard_handles_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(harmonisation, "KRR_DIR", tmp_path / "nonexistent")
    assert harmonisation.get_law_harmonisation_target_iri("missing.json") is None


# ---------------------------------------------------------------------------
# GET → POST: the Publications Office Virtuoso endpoint 202s on GET (#129/#96).
# ---------------------------------------------------------------------------


class _FakeResp:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = (
            json_data
            if json_data is not None
            else {"results": {"bindings": [{"x": {"value": "ok"}}]}}
        )

    def raise_for_status(self):
        return None

    def json(self):
        return self._json


@pytest.mark.parametrize("module", [eurlex, curia, harmonisation])
def test_eu_sparql_query_posts_not_gets(module, monkeypatch):
    """Each EU generator's ``sparql_query`` must POST the query, never GET it.

    The Publications Office Virtuoso endpoint answers GET for non-trivial
    queries with HTTP 202 + an empty body; ``raise_for_status()`` does not
    flag a 2xx, so a GET-based helper silently returns ``[]`` (#129/#96).
    """
    posted = {}

    def fake_post(url, data=None, headers=None, timeout=None):
        posted["url"] = url
        posted["data"] = data
        posted["headers"] = headers
        return _FakeResp()

    def fail_get(*_a, **_k):  # pragma: no cover - must not be reached
        raise AssertionError(f"{module.__name__}.sparql_query must not use requests.get")

    monkeypatch.setattr(eurlex_common.requests, "post", fake_post)
    monkeypatch.setattr(eurlex_common.requests, "get", fail_get)

    assert module.sparql_query("ASK {}") == [{"x": {"value": "ok"}}]
    assert posted["url"] == module.SPARQL_ENDPOINT
    assert posted["data"] == {"query": "ASK {}"}
    assert posted["headers"]["Accept"] == "application/sparql-results+json"
    assert "x-www-form-urlencoded" in posted["headers"]["Content-Type"]


@pytest.mark.parametrize("module", [eurlex, curia, harmonisation])
def test_eu_sparql_query_202_raises(module, monkeypatch):
    """A bare HTTP 202 (empty body) must raise so the retry layer reacts."""
    def fake_post(url, data=None, headers=None, timeout=None):
        return _FakeResp(status_code=202, json_data={})

    monkeypatch.setattr(eurlex_common.requests, "post", fake_post)
    with pytest.raises(RuntimeError, match="202"):
        module.sparql_query("ASK {}")


# ---------------------------------------------------------------------------
# #96 — transposition deadline on EU directive nodes built by generate_eu_legislation.
# ---------------------------------------------------------------------------


def test_eurlex_directive_query_projects_deadline(monkeypatch):
    """The legislation query carries an OPTIONAL ?deadline projection (#96)."""
    queries: list[str] = []

    def fake_query(query: str) -> list[dict]:
        queries.append(query)
        return []

    monkeypatch.setattr(eurlex, "sparql_query", fake_query)
    eurlex.fetch_legislation_type("cdm:directive")
    assert "?deadline" in queries[0]
    assert "cdm:directive_date_transposition" in queries[0]


def test_legislation_to_node_emits_deadline_when_present():
    node = eurlex.legislation_to_node(
        {
            "celex": "32011L0083",
            "title": "Consumer Rights Directive",
            "date": "2011-10-25",
            "in_force": "true",
            "eli": "",
            "authors": [],
            "transposition_deadline": "2013-12-13",
        },
        "Directive",
    )
    assert node["estleg:transpositionDeadline"] == {"@value": "2013-12-13", "@type": "xsd:date"}


def test_legislation_to_node_omits_deadline_when_absent():
    node = eurlex.legislation_to_node(
        {
            "celex": "32016R0679",
            "title": "GDPR",
            "date": "2016-04-27",
            "in_force": "true",
            "eli": "",
            "authors": [],
            "transposition_deadline": "",
        },
        "Regulation",
    )
    assert "estleg:transpositionDeadline" not in node


def test_eurlex_schema_does_not_redeclare_transposition_deadline():
    """estleg:transpositionDeadline (#96) is declared once, in
    controlled_vocabulary.jsonld — re-declaring it in the eurlex subcorpus
    schema would trip validate_all's @id-uniqueness gate."""
    ids = {n["@id"] for n in eurlex.generate_schema_nodes()}
    assert "estleg:transpositionDeadline" not in ids
    # The other EU-legislation datatype props are still declared here.
    assert "estleg:documentDate" in ids
    assert "estleg:celexNumber" in ids


def test_eurlex_dedup_keeps_earliest_deadline(monkeypatch):
    """When a directive appears across rows with several deadlines, the
    earliest is kept (corrigenda may declare later dates)."""
    payload = [
        {"celex": {"value": "32007L0073"}, "work": {"value": "w"}, "title": {"value": "T"},
         "deadline": {"value": "2008-09-14"}},
        {"celex": {"value": "32007L0073"}, "work": {"value": "w"}, "title": {"value": "T"},
         "deadline": {"value": "2007-12-18"}},
    ]
    state = {"called": False}

    def fake_query(_q):
        if state["called"]:
            return []
        state["called"] = True
        return payload

    monkeypatch.setattr(eurlex, "sparql_query", fake_query)
    monkeypatch.setattr(eurlex.time, "sleep", lambda _: None)

    items, partial = eurlex.fetch_legislation_type("cdm:directive")
    assert partial is False
    assert len(items) == 1
    assert items[0]["transposition_deadline"] == "2007-12-18"

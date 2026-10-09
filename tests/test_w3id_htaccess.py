"""The staged w3id rules (``w3id/estleg/.htaccess``) route each IRI class (#728).

The file is the staging copy of what perma-id/w3id.org serves at
``https://w3id.org/estleg/``. This test parses it with a minimal mod_rewrite
model -- ``SetEnvIf`` variables, ``RewriteCond %{HTTP_ACCEPT}`` (``[NC]``,
``[OR]``), ``RewriteRule`` with ``R=<status>`` and ``L`` -- and asserts the
redirect each IRI class gets for each kind of client. Apache's own regex
dialect (PCRE) agrees with Python's ``re`` on every construct the file uses;
the test fails loudly on a directive it does not model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pytest

HTACCESS = Path(__file__).resolve().parents[1] / "w3id" / "estleg" / ".htaccess"
HOST = "https://estleg.sixtyfour.ee"
REPO = "https://github.com/henrikaavik/estonian-legal-ontology"
RAW = "https://raw.githubusercontent.com/henrikaavik/estonian-legal-ontology"

BROWSER = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
TURTLE = "text/turtle"
JSONLD = "application/ld+json"
CURL = "*/*"


@dataclass
class Cond:
    variable: str
    pattern: re.Pattern[str]
    is_or: bool


@dataclass
class Rule:
    pattern: re.Pattern[str]
    target: str
    status: int | None
    last: bool
    conds: list[Cond] = field(default_factory=list)


def _flags(text: str) -> set[str]:
    return {f.strip() for f in text.strip("[]").split(",") if f.strip()} if text else set()


def parse(path: Path = HTACCESS) -> tuple[dict[str, str], list[Rule], list[str]]:
    env: dict[str, str] = {}
    rules: list[Rule] = []
    headers: list[str] = []
    pending: list[Cond] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        directive = parts[0]
        if directive in ("Options", "RewriteEngine"):
            continue
        if directive == "SetEnvIf":
            name, _, value = parts[3].partition("=")
            env[name] = value
        elif directive == "Header":
            headers.append(line)
        elif directive == "RewriteCond":
            flags = _flags(parts[3] if len(parts) > 3 else "")
            pending.append(Cond(parts[1], re.compile(parts[2], re.I if "NC" in flags else 0),
                                "OR" in flags))
        elif directive == "RewriteRule":
            flags = _flags(parts[3] if len(parts) > 3 else "")
            status = next((int(f[2:]) for f in flags if f.startswith("R=")), None)
            rules.append(Rule(re.compile(parts[1], re.I if "NC" in flags else 0),
                              parts[2], status, "L" in flags, pending))
            pending = []
        else:  # pragma: no cover - a new directive must be modelled first
            raise AssertionError(f"unmodelled directive: {line}")
    assert not pending, "RewriteCond without a RewriteRule"
    return env, rules, headers


def _conds_hold(conds: list[Cond], accept: str) -> bool:
    if not conds:
        return True
    result: bool | None = None
    joiner_or = False
    for cond in conds:
        assert cond.variable == "%{HTTP_ACCEPT}", f"unmodelled condition {cond.variable}"
        hit = bool(cond.pattern.search(accept))
        result = hit if result is None else (result or hit) if joiner_or else (result and hit)
        joiner_or = cond.is_or
    return bool(result)


def resolve(local: str, accept: str = CURL) -> tuple[int, str]:
    """``(status, Location)`` for ``https://w3id.org/estleg/<local>``."""
    env, rules, _ = parse()
    for rule in rules:
        m = rule.pattern.search(local)
        if not m or not _conds_hold(rule.conds, accept):
            continue
        target = re.sub(r"%\{ENV:(\w+)\}", lambda e: env[e.group(1)], rule.target)
        target = re.sub(r"\$(\d)", lambda g: m.group(int(g.group(1))) or "", target)
        assert rule.status is not None and rule.last, f"rule {rule.pattern.pattern} must be [R,L]"
        return rule.status, target
    raise AssertionError(f"no rule matched {local!r}")


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------
def test_resolver_host_is_one_variable() -> None:
    env, rules, _ = parse()
    assert env["RESOLVER"] == HOST
    text = HTACCESS.read_text(encoding="utf-8")
    # The host appears exactly once outside comments: in the SetEnvIf line.
    code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    assert code.count("estleg.sixtyfour.ee") == 1
    assert any("%{ENV:RESOLVER}" in r.target for r in rules)


def test_vary_accept_and_no_commented_out_rules() -> None:
    _, _, headers = parse()
    assert 'Header always set Vary "Accept"' in headers
    for line in HTACCESS.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("#"):
            body = line.lstrip().lstrip("#").strip()
            assert not body.startswith(("RewriteRule", "RewriteCond")), (
                f"commented-out rule left in the staging file: {line}"
            )


def test_every_rule_is_a_terminal_redirect() -> None:
    _, rules, _ = parse()
    assert rules
    for rule in rules:
        assert rule.status in (302, 303) and rule.last


# ---------------------------------------------------------------------------
# Rule classes
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("accept", [CURL, BROWSER, TURTLE])
def test_namespace_root_302_to_repository(accept: str) -> None:
    assert resolve("", accept) == (302, REPO)


@pytest.mark.parametrize("accept", [TURTLE, JSONLD, "application/n-triples"])
def test_version_iri_rdf_gets_tbox_at_tag(accept: str) -> None:
    assert resolve("1.0.0", accept) == (
        303, f"{RAW}/v1.0.0/krr_outputs/controlled_vocabulary.jsonld")


@pytest.mark.parametrize("accept", [CURL, BROWSER])
def test_version_iri_otherwise_302_to_release(accept: str) -> None:
    assert resolve("1.0.0", accept) == (302, f"{REPO}/releases/tag/v1.0.0")
    assert resolve("12.3.45/", accept) == (302, f"{REPO}/releases/tag/v12.3.45")


def test_void_iris() -> None:
    assert resolve("dataset/estonian-legal-ontology", TURTLE) == (
        303, f"{RAW}/main/krr_outputs/void.ttl")
    assert resolve("graph/laws", BROWSER) == (303, f"{REPO}/blob/main/krr_outputs/void.ttl")


@pytest.mark.parametrize("accept", [CURL, BROWSER, TURTLE, JSONLD])
def test_vocabulary_303_to_hosted_vocabulary(accept: str) -> None:
    assert resolve("vocabulary", accept) == (303, f"{HOST}/vocabulary")


@pytest.mark.parametrize(
    "local",
    ["KARIST_2_Osa2_Par_141", "KARIST_2_Map", "Reg_1057801_Map", "RK_3_21_2176_52",
     "Institution_abiminister", "Act", "CaseType_Civil", "KarS_Par_141"],
)
@pytest.mark.parametrize(
    "accept",
    [TURTLE, JSONLD, BROWSER, "text/html", "application/rdf+xml",
     "application/n-triples", "TEXT/TURTLE", "application/ld+json;q=0.9, */*;q=0.1"],
)
def test_term_iris_303_to_resolver(local: str, accept: str) -> None:
    assert resolve(local, accept) == (303, f"{HOST}/id/{local}")


@pytest.mark.parametrize("accept", [CURL, "image/png", ""])
def test_term_iri_without_rdf_or_html_accept_keeps_interim_302(accept: str) -> None:
    assert resolve("KARIST_2_Osa2_Par_141", accept) == (302, REPO)


def test_multi_segment_paths_never_reach_the_resolver() -> None:
    assert resolve("a/b", TURTLE) == (302, REPO)
    assert resolve("1.0.0/extra", TURTLE) == (302, REPO)

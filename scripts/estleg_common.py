"""
Shared constants, regex patterns, and helpers for Estonian Legal Ontology scripts.

This module centralises abbreviation mappings, JSON-LD context, and utility
functions used by extract_cross_references.py and
extract_court_provision_links.py so they stay in sync.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Well-known abbreviation -> full law name mappings (union of both scripts)
# ---------------------------------------------------------------------------
KNOWN_ABBREVIATIONS: dict[str, str] = {
    "KarS": "Karistusseadustik",
    "VÕS": "Võlaõigusseadus",
    "TsÜS": "Tsiviilseadustiku üldosa seadus",
    "AÕS": "Asjaõigusseadus",
    "PKS": "Perekonnaseadus",
    "ÄS": "Äriseadustik",
    "HMS": "Haldusmenetluse seadus",
    "TsMS": "Tsiviilkohtumenetluse seadustik",
    "KrMS": "Kriminaalmenetluse seadustik",
    "TMS": "Täitemenetluse seadustik",
    "KOKS": "Kohaliku omavalitsuse korralduse seadus",
    "PS": "Põhiseadus",
    "PankrS": "Pankrotiseadus",
    "MKS": "Maksukorralduse seadus",
    "TLS": "Töölepingu seadus",
    "RLS": "Riigilõivuseadus",
    "TTKS": "Töötervishoiu ja tööohutuse seadus",
    "TKS": "Tolliseadus",
    "TPTS": "Tööturuteenuste ja -toetuste seadus",
    "IKS": "Isikuandmete kaitse seadus",
    "RahaPTS": "Rahapesu ja terrorismi rahastamise tõkestamise seadus",
    "KELS": "Kõrgharidusseadus",
    "PPVS": "Planeerimisseadus",
    "AVVKHS": "Avaliku teenistuse seadus",
    "RHS": "Riigihangete seadus",
    "SHS": "Sotsiaalhoolekande seadus",
    "ELTS": "Elektrituruseadus",
    "EhS": "Ehitusseadus",
    "KVS": "Korruptsioonivastane seadus",
    "MSVS": "Meresõiduvahendite seadus",
    "RSVS": "Relvaseadus",
    "EKS": "Elektroonilise side seadus",
    "KES": "Keskkonnaseadustiku eriosa seadus",
    "LKS": "Looduskaitseseadus",
    "KeÜS": "Keskkonnaseadustiku üldosa seadus",
    "MaaRS": "Maareformi seadus",
    "KOS": "Kinnisasja omandamise kitsendamise seadus",
    "RavS": "Ravimiseadus",
    "AVTS": "Avaliku teabe seadus",
    "KLS": "Kalapüügiseadus",
    "KAVS": "Kaitseväeteenistuse seadus",
    "TTÜKS": "Tööstusomandi kaitse seadus",
    "KMS": "Käibemaksuseadus",
    "TuMS": "Tulumaksuseadus",
    "SMMS": "Sotsiaalmaksuseadus",
    "KindlTS": "Kindlustustegevuse seadus",
    # M7 additions — KOV enabling laws that have peep files in the corpus.
    # Values MUST match the canonical estleg:sourceAct on the act's
    # provisions. Verified by the validation one-liner in Task 1 Step 7
    # of the Layer 2b plan.
    # Note on underscore-suffixed keys: `RHS_HÄDA` and `KELS_LASTEAS`
    # disambiguate from already-registered abbreviations (RHS for
    # Riigihangete seadus, KELS for Kõrgharidusseadus). Future
    # collisions should follow the same `_<HINT>` pattern.
    "PGS": "Põhikooli- ja gümnaasiumiseadus",
    "KELS_LASTEAS": "Koolieelse lasteasutuse seadus",
    "HuviKS": "Huvikooli seadus",
    "RuumS": "Ruumiandmete seadus",
    "ÜVVKS": "Ühisveevärgi ja -kanalisatsiooni seadus",
    "KaugKS": "Kaugkütteseadus",
    "ElamuS": "Elamuseadus",
    "RaamatS": "Rahvaraamatukogu seadus",
    "RHS_HÄDA": "Hädaolukorra seadus",
    "EhSE": "Ehitusseadustik",
    "JäätS": "Jäätmeseadus",
    "TeeS": "Teeseadus",
    "RahvaS": "Rahvahääletuse seadus",
    "RKVS": "Riigikogu valimise seadus",
    "RaamatPS": "Raamatupidamise seadus",
    "ÜTS": "Ühistranspordiseadus",
}

# ---------------------------------------------------------------------------
# Genitive / partitive full-name forms -> abbreviation
# ---------------------------------------------------------------------------
FULLNAME_GENITIVE: dict[str, str] = {
    "karistusseadustiku": "KarS",
    "võlaõigusseaduse": "VÕS",
    "tsiviilseadustiku üldosa seaduse": "TsÜS",
    "asjaõigusseaduse": "AÕS",
    "perekonnaseaduse": "PKS",
    "äriseadustiku": "ÄS",
    "haldusmenetluse seaduse": "HMS",
    "tsiviilkohtumenetluse seadustiku": "TsMS",
    "kriminaalmenetluse seadustiku": "KrMS",
    "täitemenetluse seadustiku": "TMS",
    "kohaliku omavalitsuse korralduse seaduse": "KOKS",
    "põhiseaduse": "PS",
    "pankrotiseaduse": "PankrS",
    "maksukorralduse seaduse": "MKS",
    "töölepingu seaduse": "TLS",
    "riigilõivuseaduse": "RLS",
    # M7 — match against parser's lowercased law_ref output. Entries
    # whose abbrev resolves through KNOWN_ABBREVIATIONS to a corpus
    # peep file produce real triples; entries whose target law has
    # no peep file (KNS, AluS, KOFS, KOVVS) silently fall through —
    # this is intentional and correct (parser-recognised,
    # resolver-fallthrough).
    # Four entries (sotsiaalhoolekande seaduse → SHS, avaliku teabe
    # seaduse → AVTS, avaliku teenistuse seaduse → AVVKHS,
    # planeerimisseaduse → PPVS) reuse abbreviations already in
    # KNOWN_ABBREVIATIONS from Layer 2a — only the genitive form is new.
    "kohanimeseaduse": "KNS",
    "põhikooli- ja gümnaasiumiseaduse": "PGS",
    "koolieelse lasteasutuse seaduse": "KELS_LASTEAS",
    "alusharidusseaduse": "AluS",
    "huvikooli seaduse": "HuviKS",
    "ruumiandmete seaduse": "RuumS",
    "kohaliku omavalitsuse üksuse finantsjuhtimise seaduse": "KOFS",
    "ühisveevärgi ja -kanalisatsiooni seaduse": "ÜVVKS",
    "kaugkütteseaduse": "KaugKS",
    "elamuseaduse": "ElamuS",
    "rahvaraamatukogu seaduse": "RaamatS",
    "hädaolukorra seaduse": "RHS_HÄDA",
    "ehitusseadustiku": "EhSE",
    "jäätmeseaduse": "JäätS",
    "teeseaduse": "TeeS",
    "rahvahääletuse seaduse": "RahvaS",
    "riigikogu valimise seaduse": "RKVS",
    "kohaliku omavalitsuse volikogu valimise seaduse": "KOVVS",
    "raamatupidamise seaduse": "RaamatPS",
    "ühistranspordiseaduse": "ÜTS",
    "sotsiaalhoolekande seaduse": "SHS",
    "avaliku teabe seaduse": "AVTS",
    "avaliku teenistuse seaduse": "AVVKHS",
    "planeerimisseaduse": "PPVS",
}

# ---------------------------------------------------------------------------
# Aggregate registry IRI prefixes — files whose @id values use these
# prefixes are registry/aggregate maps (not classifiable as a single
# act). Centralised here so EuroVoc classification and any future
# pipeline that needs to skip these files share one source of truth.
# ---------------------------------------------------------------------------
AGGREGATE_REGISTRY_PREFIXES: tuple[str, ...] = (
    "estleg:Municipalities_",
    "estleg:Issuers_",
    "estleg:LegalConcepts_",
    "estleg:Citations_",
    "estleg:Similarity_",
)

# ---------------------------------------------------------------------------
# JSON-LD shared context
# ---------------------------------------------------------------------------
NS = "https://data.riik.ee/ontology/estleg#"

CONTEXT: dict[str, str] = {
    "estleg": NS,
    "owl": "http://www.w3.org/2002/07/owl#",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "dc": "http://purl.org/dc/elements/1.1/",
    "skos": "http://www.w3.org/2004/02/skos/core#",
    "dcterms": "http://purl.org/dc/terms/",
}

# ---------------------------------------------------------------------------
# KOV body-word canonicalization and issuer-name normalization (Layer 2c PR #3)
# ---------------------------------------------------------------------------
# Court summaries cite KOV bodies using genitive forms (Linnavalitsuse,
# Vallavalitsuse); KOV peep estleg:issuer fields use nominative (Linnavalitsus,
# Vallavalitsus). The volikogu forms are identical in both cases. BODY_CANON
# normalizes either side's body word to the nominative form so issuer-string
# compare works after normalize_issuer_name().
BODY_CANON: dict[str, str] = {
    "linnavalitsuse": "linnavalitsus",
    "vallavalitsuse": "vallavalitsus",
    "linnavalitsus":  "linnavalitsus",
    "vallavalitsus":  "vallavalitsus",
    "linnavolikogu":  "linnavolikogu",
    "vallavolikogu":  "vallavolikogu",
}


def normalize_issuer_name(s: str) -> str:
    """Lowercase + Estonian diacritic transliteration + whitespace collapse.

    Same approach as Layer 1 ``titleNormalized``. Used at both index-build
    time (over KOV peep ``estleg:issuer`` strings) and citation-resolve
    time (over reconstructed display name from a regex match).
    """
    s = s.lower()
    for src, dst in (
        ("õ", "o"), ("ä", "a"), ("ö", "o"),
        ("ü", "u"), ("š", "s"), ("ž", "z"),
    ):
        s = s.replace(src, dst)
    return " ".join(s.split())


def jsonld_text(value: object, default: str = "") -> str:
    """Return a plain string from common JSON-LD text shapes.

    Generated corpus fields may be plain strings, value objects such as
    ``{"@value": "...", "@language": "et"}``, or lists of those objects.
    Semantic extractors should call this before regex/token processing so
    fresh generator output and enriched normalized output use one contract.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        text = value.get("@value")
        return text if isinstance(text, str) else default
    if isinstance(value, list):
        parts = [text for item in value if (text := jsonld_text(item))]
        return " ".join(parts) if parts else default
    return default


# ---------------------------------------------------------------------------
# Run-counters helper (Layer 2c PR #3)
# ---------------------------------------------------------------------------
# Pipelines that read many JSON files and need to record malformed inputs
# under coverage-report fields share this accumulator so failure
# bookkeeping (files_skipped + skip_reasons + error_count + failure_samples)
# stays atomic. _safe_load wraps json.load with the four bumps; callers
# treat None as "skip this file."

# In-memory cap on failures samples. The downstream coverage-report writer
# (kov_pipeline_coverage.write_coverage_report) enforces a final cap of 20
# samples in the JSON output. We over-provision 10× that cap here so a
# single run can collect a diverse pool across reasons before the writer
# truncates — otherwise a single high-frequency failure (e.g. a corrupt
# fixture) could fill the 20-slot budget and crowd out other failure modes.
_FAILURE_SAMPLES_INMEMORY_CAP = 200


@dataclass
class _RunCounters:
    """Mutable accumulator threaded through every read site of a pipeline.

    De-dup set ``seen_error_paths`` ensures the same malformed file
    encountered by multiple walks (cleanup, index-build, processing)
    counts once toward ``file_skips`` rather than once per walk.
    """

    file_skips: int = 0
    error_count: int = 0
    skip_reasons: dict[str, int] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    seen_error_paths: set[Path] = field(default_factory=set)

    def bump_skip(self, reason: str, sample: str) -> None:
        self.file_skips += 1
        self.error_count += 1
        self.skip_reasons[reason] = self.skip_reasons.get(reason, 0) + 1
        if len(self.failures) < _FAILURE_SAMPLES_INMEMORY_CAP:
            self.failures.append(sample)

    def bump_citation_skip(self, reason: str, sample: str) -> None:
        """Citation-level skip: bumps skip_reasons + failures only.

        Use this for per-citation routing (ambiguous_key, unknown_issuer,
        etc.) where the underlying file is fine and only one citation
        within it is skipped. Use bump_skip() instead when the entire
        file is unreadable (file_skips/error_count get bumped there).
        """
        self.skip_reasons[reason] = self.skip_reasons.get(reason, 0) + 1
        if len(self.failures) < _FAILURE_SAMPLES_INMEMORY_CAP:
            self.failures.append(sample)

    def bump_citation_count(self, reason: str) -> None:
        """Counter-only bump (no failure sample) — for known-deferred buckets."""
        self.skip_reasons[reason] = self.skip_reasons.get(reason, 0) + 1


def _safe_load(path: Path, counters: _RunCounters) -> dict | None:
    """Load JSON from ``path``; return ``None`` and bump counters on failure.

    Same path counted only once across the whole run via
    ``counters.seen_error_paths``.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (ValueError, OSError) as exc:
        if path not in counters.seen_error_paths:
            counters.seen_error_paths.add(path)
            counters.bump_skip(
                "json_decode_error",
                f"json_decode_error | {path.name} | "
                f"{type(exc).__name__}: {str(exc)[:80]}",
            )
        return None


# ---------------------------------------------------------------------------
# Paragraph citation regex fragment
#
# Handles all Estonian grammatical cases for the § symbol:
#   §, §§, §-de, §-des, §-d, §-s, §-st, §-le, §-i, §-ga
# Also accepts non-breaking hyphen (\u2011).
# ---------------------------------------------------------------------------
PAR_SUFFIX = r"§§?(?:[\-\u2011](?:de(?:s)?|d|s|st|le|i|ga))?"


# ---------------------------------------------------------------------------
# Output paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]
KRR_DIR = REPO_ROOT / "krr_outputs"


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def save_json(filepath: Path, doc: dict | list) -> None:
    """Write JSON-LD document to file with consistent formatting.

    Uses a tempfile + atomic ``os.replace`` so partial writes never appear
    at ``filepath``. If serialization fails (non-serialisable payload, OS
    error mid-write), the tempfile is unlinked best-effort before the
    original exception propagates so failed runs don't litter ``.tmp``
    droppings in the target directory.
    """
    filepath.parent.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=filepath.parent,
        prefix=f".{filepath.name}.",
        suffix=".tmp",
        delete=False,
    )
    tmp_name = tmp.name
    try:
        with tmp as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp_name, filepath)
    except BaseException:
        # Best-effort cleanup of the tempfile so failed writes never
        # leave ``.<name>.<rand>.tmp`` droppings in the target directory.
        # Suppress FileNotFoundError so we don't mask the original error
        # if the tempfile was already removed (e.g. by os.replace).
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def iter_peep_files(
    *,
    include_laws: bool = True,
    include_regulations: bool = True,
    include_kov: bool = True,
) -> list[Path]:
    """Return every ``*_peep.json`` ontology file managed by the pipeline.

    Pipeline-wide file iterator that includes top-level laws, state-level
    regulations under ``regulations/riik/``, and KOV regulations under
    ``regulations/kov/``. The Layer 2a default flip (2026-05-03) made KOV
    inclusion the new baseline; pipelines that aren't yet KOV-ready opt
    out explicitly via ``include_kov=False`` (Layer 2b/2c/3 deferrals,
    or KOV-irrelevant pipelines like harmonisation links).
    """
    files: list[Path] = []
    if include_laws:
        files.extend(KRR_DIR.glob("*_peep.json"))
    if include_regulations:
        riik_dir = KRR_DIR / "regulations" / "riik"
        if riik_dir.exists():
            files.extend(riik_dir.glob("*_peep.json"))
        if include_kov:
            kov_dir = KRR_DIR / "regulations" / "kov"
            if kov_dir.exists():
                files.extend(kov_dir.glob("**/*_peep.json"))
    # Sort by absolute posix path (casefold) — using relative_to(KRR_DIR)
    # would raise ValueError for any path outside KRR_DIR, which is a
    # footgun for future callers, symlinks, or test scaffolding.
    return sorted(files, key=lambda p: p.as_posix().casefold())


def build_globalid_xml_lookup(rt_root: Path) -> dict[str, Path]:
    """Recursively scan rt_root for *.xml files and build a
    globalId → Path lookup keyed by the root element's globaalID
    attribute. Files without a recognisable globaalID are skipped.

    Walks rt_root and all subdirectories so KOV XML under
    ``maarus_kov/`` and state regulation XML under ``maarus/`` are
    paired alongside law XML at the root.
    """
    lookup: dict[str, Path] = {}
    if not rt_root.is_dir():
        return lookup
    for xml_path in sorted(rt_root.rglob("*.xml"), key=lambda p: str(p.relative_to(rt_root)).casefold()):
        try:
            tree = ET.parse(str(xml_path))
        except ET.ParseError:
            continue
        root = tree.getroot()
        # Primary: root element's globaalID attribute (most RT XML).
        gid = root.get("globaalID")
        if gid is None:
            # Fallback: scan top-level <metaandmed> children for a
            # globaalID leaf element.
            #
            # NOTE: ElementTree's truthiness on Element is False when
            # the element has no children, so `child.find(a) or
            # child.find(b)` would silently discard a leaf hit. Use
            # explicit `is None` checks throughout.
            for child in root:
                if not child.tag.endswith("metaandmed"):
                    continue
                gid_el = child.find("globaalID")
                if gid_el is None:
                    gid_el = child.find("{*}globaalID")
                if gid_el is not None and gid_el.text:
                    gid = gid_el.text.strip()
                    break
        if gid:
            gid = str(gid)
            if gid in lookup:
                print(
                    "WARN: duplicate globalId "
                    f"{gid} in {lookup[gid].relative_to(rt_root)} and {xml_path.relative_to(rt_root)}; "
                    "keeping first"
                )
                continue
            lookup[gid] = xml_path
    return lookup


def pair_peep_with_xml(
    peep_path: Path,
    lookup: dict[str, Path],
    *,
    data_dir: Path | None = None,
    counters: _RunCounters | None = None,
) -> Path | None:
    """Pair a peep file to its XML.

    Two paths in priority order:

    1. **globalId-based** (the new path, KOV + state regs): the
       generators stamp ``estleg:globalId`` on every act node, and KOV
       XML filenames embed the same id (``reg_<globalId>.xml``). This
       is the canonical pairing for everything generated post-Phase-1.

    2. **Slug-based fallback** (legacy laws): the 615 indexed laws
       were generated before the ``estleg:globalId`` field existed,
       so their act nodes carry ``estleg:Law``/``owl:Ontology`` types
       but no globalId. For these, fall back to matching the peep
       file's stem (sans ``_peep``) against
       ``<data_dir>/<stem>.xml``. ``data_dir`` is required for the
       fallback; if not supplied, only the globalId path runs.

    Returns the XML path or None if neither path resolves.
    """
    try:
        with open(peep_path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except (ValueError, OSError) as exc:
        if counters is not None:
            if peep_path not in counters.seen_error_paths:
                counters.seen_error_paths.add(peep_path)
                counters.bump_skip(
                    "json_decode_error",
                    f"json_decode_error | {peep_path.name} | "
                    f"{type(exc).__name__}: {str(exc)[:80]}",
                )
        else:
            # No counters provided — still surface the corruption signal so
            # callers that haven't been wired into the run-counter framework
            # don't silently conflate "corrupt peep" with "no XML found".
            print(
                f"pair_peep_with_xml: corrupt peep {peep_path}: "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
        return None
    gid = None
    for node in doc.get("@graph", []):
        types = node.get("@type") or []
        if isinstance(types, str):
            types = [types]
        if not any(t.endswith("Regulation") or t == "estleg:Law"
                   or t == "owl:Ontology" for t in types):
            continue
        gid = node.get("estleg:globalId")
        if gid:
            break
    # Path 1: globalId
    if gid:
        xml = lookup.get(str(gid))
        if xml is not None:
            return xml
    # Path 2: slug fallback (laws without globalId)
    if data_dir is not None:
        slug = peep_path.stem.replace("_peep", "")
        xml = data_dir / f"{slug}.xml"
        if xml.exists():
            return xml
        # Stripped-_osa fallback for multi-part laws
        base_slug = re.sub(r"_osa\d+$", "", slug)
        xml = data_dir / f"{base_slug}.xml"
        if xml.exists():
            return xml
    return None


_ESTONIAN_TRANSLITERATION: dict[str, str] = {
    "ö": "o", "ä": "a", "ü": "u", "õ": "o",
    "Ö": "O", "Ä": "A", "Ü": "U", "Õ": "O",
    "š": "s", "ž": "z", "Š": "S", "Ž": "Z",
}
_TRANSLIT_TABLE = str.maketrans(_ESTONIAN_TRANSLITERATION)


def sanitize_id(value: str) -> str:
    """Create a safe ID component from a string."""
    s = value.replace(" ", "_")
    # Transliterate Estonian diacritics before stripping non-ASCII
    s = s.translate(_TRANSLIT_TABLE)
    s = re.sub(r"[^0-9A-Za-z_]", "", s)
    return s or "Unknown"


def slugify(text: str, max_len: int = 80) -> str:
    """Convert Estonian text to a filename-safe slug.

    Lowercases, transliterates Estonian diacritics, replaces non-alnum
    runs with underscores, and truncates to ``max_len`` characters.
    Centralised here so callers (laws, regulations, downstream scripts)
    share one definition.
    """
    text = text.translate(_TRANSLIT_TABLE)
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")[:max_len]

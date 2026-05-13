#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent
KRR_DIR = PROJECT_ROOT / "krr_outputs"
DEFAULT_REPORT_PATH = KRR_DIR / "reports" / "multipart_iri_migration_report.json"

# ── Known multipart laws ──────────────────────────────────────────────────────
# The five laws known to use the multipart Osa<N> generator. We still
# auto-detect the actual files via the ``<law>_osa<N>_peep.json`` glob so a
# new multipart law (or a removed one) is picked up automatically — this
# constant is just the canonical default for ``--law``.
MULTIPART_LAWS: tuple[str, ...] = (
    "karistusseadustik",
    "tsiviilseadustik",
    "tsiviilseadustiku_uldosa_seadus",
    "volaigusseadus",
    "asjaoigusseadus",
)

# ── IRI patterns ──────────────────────────────────────────────────────────────
# OLD provision @id: estleg:<PREFIX>_Par_<n>(_<sub>)? where _Osa is NOT
# anywhere in the local name. PREFIX is everything between ``estleg:`` and
# the trailing ``_Par_<n>``. We accept ASCII letters, digits and
# underscores in the prefix; Estonian unicode prefixes (TsÜS) are matched
# explicitly so VõS-style and TsÜS-style abbreviations both round-trip.
_PREFIX_CHARS = r"A-Za-z0-9_ÄÖÕÜäöõüŠŽšž"
OLD_PAR_RE = re.compile(
    rf"^estleg:(?P<prefix>[{_PREFIX_CHARS}]+?)_Par_(?P<par>\d+(?:_\d+)?)$"
)

# Osa-number extractor from a peep filename. We deliberately match ONLY
# a single integer — range-form osas like ``osa6-13`` are not migratable
# because the destination ``Osa<N>`` is ambiguous, so they are skipped
# and surfaced in the report.
OSA_FILENAME_RE = re.compile(
    r"^(?P<law>.+)_osa(?P<osa>\d+)_peep$"
)


@dataclass(frozen=True)
class ProvisionRename:
    """One per-osa rename: ``old_iri`` (full ``estleg:`` form) → ``new_iri``."""

    old_iri: str
    new_iri: str
    law: str
    osa: int
    prefix: str
    par: str


@dataclass
class LawScope:
    """Per-law state collected while building the rename map."""

    name: str
    osa_files: dict[int, Path] = field(default_factory=dict)
    skipped_osa_files: list[Path] = field(default_factory=list)
    renames: list[ProvisionRename] = field(default_factory=list)
    # par_suffix -> list of (osa, ProvisionRename) — used to detect
    # cross-osa par_suffix collisions (the "VõS edge case").
    par_index: dict[str, list[ProvisionRename]] = field(default_factory=dict)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _read_json(path: Path) -> dict | None:
    """Return parsed JSON at ``path`` or ``None`` on read/parse failure."""
    try:
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def _write_json(path: Path, data: object) -> None:
    """Write ``data`` to ``path`` as UTF-8 JSON with 2-space indent."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def discover_multipart_laws(krr_dir: Path) -> list[str]:
    """Return the sorted list of laws that have at least one osa peep file.

    A multipart law is any base name ``<law>`` for which at least one file
    ``<law>_osa<N>_peep.json`` exists in ``krr_dir``. ``<N>`` must begin
    with a digit so that legitimate names embedding ``_osa`` as a word
    fragment (e.g. ``..._osale_peep.json``, ``..._osalemise_seadus_peep.json``)
    are NOT mistaken for multipart laws. Range-form osa identifiers
    (``osa6-13``) ARE accepted so the report can flag them as skipped.
    """
    laws: set[str] = set()
    # ``_osa\d`` ensures the suffix is followed by at least one digit —
    # ``osa6-13`` matches (digit-led range), but ``osale`` / ``osalemise`` /
    # ``osalisriikide`` do not.
    multipart_re = re.compile(r"^(.+)_osa\d[^_]*_peep$")
    for path in sorted(krr_dir.glob("*_osa*_peep.json")):
        m = multipart_re.match(path.stem)
        if m:
            laws.add(m.group(1))
    return sorted(laws)


def build_law_scope(law: str, krr_dir: Path) -> LawScope:
    """Collect the osa peep files for ``law`` and build its rename plan.

    Only files matching ``<law>_osa<digits>_peep.json`` contribute renames.
    Range-form osa files (``osa6-13``) are recorded as ``skipped_osa_files``
    so the report can flag them — their provisions are left untouched.
    """
    scope = LawScope(name=law)
    candidates = sorted(krr_dir.glob(f"{law}_osa*_peep.json"))
    for path in candidates:
        m = OSA_FILENAME_RE.match(path.stem)
        if not m or m.group("law") != law:
            scope.skipped_osa_files.append(path)
            continue
        osa = int(m.group("osa"))
        scope.osa_files[osa] = path

    par_index: dict[str, list[ProvisionRename]] = defaultdict(list)
    for osa in sorted(scope.osa_files):
        path = scope.osa_files[osa]
        data = _read_json(path)
        if not isinstance(data, dict):
            continue
        graph = data.get("@graph")
        if not isinstance(graph, list):
            continue
        for node in graph:
            if not isinstance(node, dict):
                continue
            iri = node.get("@id")
            if not isinstance(iri, str):
                continue
            if "_Osa" in iri:
                continue
            m = OLD_PAR_RE.match(iri)
            if not m:
                continue
            prefix = m.group("prefix")
            par = m.group("par")
            new_iri = f"estleg:{prefix}_Osa{osa}_Par_{par}"
            rename = ProvisionRename(
                old_iri=iri,
                new_iri=new_iri,
                law=law,
                osa=osa,
                prefix=prefix,
                par=par,
            )
            scope.renames.append(rename)
            par_index[par].append(rename)

    scope.par_index = dict(par_index)
    return scope


def _per_file_rename_map(scope: LawScope) -> dict[int, dict[str, str]]:
    """Return ``{osa_number: {old_iri: new_iri}}`` for ``scope``.

    Each osa peep file owns its own provisions, so per-file maps are
    unambiguous even when sibling osa files of the same law share a
    ``_Par_<n>`` suffix (the VõS case where ``VOS_Par_76`` exists in
    osa1 but ``VOS3_Par_76`` / ``VOS_O4_Par_76`` live in their own
    osas).
    """
    per_file: dict[int, dict[str, str]] = defaultdict(dict)
    for r in scope.renames:
        per_file[r.osa][r.old_iri] = r.new_iri
    return dict(per_file)


def _canonical_rename_map(
    scopes: list[LawScope],
) -> tuple[dict[str, str], list[dict]]:
    """Return the cross-reference rename map plus the ambiguity report.

    A given OLD IRI (e.g. ``estleg:VOS_Par_76``) is **ambiguous** when it
    appears as the @id of a ``<PREFIX>_Par_<n>`` provision (with NO _Osa
    in the IRI) in more than one osa peep file of the same law. When that
    happens, cross-referencing files outside the osa peep family cannot
    tell which provision the OLD IRI was meant to denote, so we have to
    choose one canonical destination.

    **Decision**: pick the EARLIEST osa (lowest N). Rationale:

    * the earliest osa is by far the most stable historical anchor — when
      a multipart law was originally minted with one prefix and later
      partial osas were split off with prefix variants, the un-suffixed
      OLD form most frequently came from the earliest osa;
    * deterministic and ordering-independent across runs.

    Every ambiguous resolution is recorded in the returned ``ambiguities``
    list so the report can surface them for human review.
    """
    canonical: dict[str, str] = {}
    ambiguities: list[dict] = []
    for scope in scopes:
        # Group renames by their OLD IRI string. If a single OLD IRI maps
        # to multiple NEW IRIs (because it appeared in multiple osa
        # files), pick the earliest osa.
        by_old: dict[str, list[ProvisionRename]] = defaultdict(list)
        for r in scope.renames:
            by_old[r.old_iri].append(r)
        for old_iri, rens in sorted(by_old.items()):
            if len(rens) == 1:
                canonical[old_iri] = rens[0].new_iri
                continue
            # Multiple osa files defined the same OLD IRI as a @graph
            # node — strictly ambiguous. Pick lowest osa.
            sorted_rens = sorted(rens, key=lambda r: r.osa)
            choice = sorted_rens[0]
            canonical[old_iri] = choice.new_iri
            ambiguities.append(
                {
                    "old_iri": old_iri,
                    "canonical_new_iri": choice.new_iri,
                    "also_could_be": [
                        r.new_iri for r in sorted_rens[1:]
                    ],
                    "law": scope.name,
                }
            )
    return canonical, ambiguities


def _build_substitution_pattern(old_iris: list[str]) -> re.Pattern[str] | None:
    """Compile a regex that matches any of ``old_iris`` as a quoted IRI.

    The pattern matches a JSON string literal whose body is exactly an
    ``estleg:`` IRI from ``old_iris`` — i.e. ``"<old_iri>"``. This is
    deliberately stricter than a bare substring search:

    * ``"estleg:KARIST_2_Par_1"`` matches the literal ``KARIST_2_Par_1``
      but NOT ``KARIST_2_Par_10`` (which would be ``"estleg:KARIST_2_Par_10"``
      — a different complete string).
    * It avoids accidentally rewriting inside compound IRIs like
      ``estleg:VOS_Par_76_Subsec_1`` because the trailing ``"`` boundary
      is required.

    Returns ``None`` if ``old_iris`` is empty.
    """
    if not old_iris:
        return None
    # Sort longest-first defensively; with the strict ``"..."`` boundary
    # alternation order does not affect correctness, but longest-first
    # eliminates any ambiguity at the regex engine level.
    alts = sorted(set(old_iris), key=len, reverse=True)
    pattern = "|".join(re.escape(iri) for iri in alts)
    return re.compile(rf'"({pattern})"')


def _apply_substitution(
    text: str, rename_map: dict[str, str]
) -> tuple[str, int]:
    """Rewrite quoted OLD IRIs in ``text`` using ``rename_map``.

    Returns ``(new_text, replacement_count)``. The substitution is
    substring-safe: it only matches complete quoted IRIs (``"estleg:..."``),
    never a prefix of another IRI.
    """
    if not rename_map:
        return text, 0
    pattern = _build_substitution_pattern(list(rename_map.keys()))
    if pattern is None:
        return text, 0

    def _replace(match: re.Match[str]) -> str:
        old = match.group(1)
        new = rename_map.get(old, old)
        return f'"{new}"'

    new_text, count = pattern.subn(_replace, text)
    return new_text, count


def _all_corpus_files(krr_dir: Path) -> list[Path]:
    """Return every ``.json`` / ``.jsonld`` under ``krr_dir`` in sorted order."""
    files: list[Path] = []
    files.extend(krr_dir.rglob("*.json"))
    files.extend(krr_dir.rglob("*.jsonld"))
    return sorted(set(files))


def _is_law_file(path: Path, law_files: dict[str, dict[int, Path]]) -> tuple[str, int] | None:
    """If ``path`` is one of the law-osa peep files we know about, return
    ``(law, osa)``; otherwise ``None``."""
    for law, osa_map in law_files.items():
        for osa, p in osa_map.items():
            if p == path:
                return law, osa
    return None


@dataclass
class MigrationPlan:
    """Aggregate plan produced by :func:`build_plan`."""

    scopes: list[LawScope]
    canonical_map: dict[str, str]
    ambiguities: list[dict]
    per_file_maps: dict[Path, dict[str, str]]  # specific peep file overrides
    rename_map_size: int

    def total_renames(self) -> int:
        return sum(len(s.renames) for s in self.scopes)


def build_plan(
    krr_dir: Path, laws: list[str] | None = None
) -> MigrationPlan:
    """Inspect the corpus and assemble the migration plan.

    ``laws`` filters to a subset of multipart laws (defaults to every
    auto-detected multipart law). The returned plan separates the
    canonical (cross-reference) rename map from per-file overrides used
    for the osa peep files themselves.
    """
    discovered = discover_multipart_laws(krr_dir)
    if laws is None:
        laws_to_use = discovered
    else:
        laws_to_use = [law for law in laws if law in discovered]

    scopes: list[LawScope] = []
    per_file_maps: dict[Path, dict[str, str]] = {}
    for law in laws_to_use:
        scope = build_law_scope(law, krr_dir)
        scopes.append(scope)
        for osa, mapping in _per_file_rename_map(scope).items():
            per_file_maps[scope.osa_files[osa]] = mapping

    canonical_map, ambiguities = _canonical_rename_map(scopes)
    return MigrationPlan(
        scopes=scopes,
        canonical_map=canonical_map,
        ambiguities=ambiguities,
        per_file_maps=per_file_maps,
        rename_map_size=sum(len(s.renames) for s in scopes),
    )


def execute_plan(
    plan: MigrationPlan, krr_dir: Path, *, apply: bool
) -> tuple[int, int]:
    """Walk every corpus file and (optionally) apply the rename.

    Returns ``(files_inspected, files_modified)``. When ``apply`` is
    ``False`` we still count files that *would* have changed but do not
    rewrite them.
    """
    files = _all_corpus_files(krr_dir)
    files_inspected = 0
    files_modified = 0
    for path in files:
        files_inspected += 1
        # The per-osa-peep map is only an OVERRIDE — it disambiguates the
        # rare same-OLD-IRI-in-multiple-osas case. For non-ambiguous IRIs,
        # we always fall back to the canonical map so cross-osa references
        # (e.g. KARIST osa1 referencing a paragraph in osa2) are also
        # rewritten. Without this fallback every cross-osa reference
        # silently dangles in the OLD scheme.
        per_file = plan.per_file_maps.get(path, {})
        rename_map = {**plan.canonical_map, **per_file}
        if not rename_map:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        new_text, count = _apply_substitution(text, rename_map)
        if count == 0 or new_text == text:
            continue
        files_modified += 1
        if apply:
            path.write_text(new_text, encoding="utf-8")
    return files_inspected, files_modified


def build_report(
    plan: MigrationPlan,
    *,
    mode: str,
    files_inspected: int,
    files_modified: int,
) -> dict:
    """Assemble the JSON report describing the plan and its outcome."""
    laws_report: list[dict] = []
    for scope in plan.scopes:
        # par_suffixes flagged as cross-osa-collisions ARE the ambiguous
        # ones for this law — we surface them per law as well so a
        # consumer can scope the warning by act.
        amb_suffixes = sorted(
            par for par, rens in scope.par_index.items()
            if len({r.osa for r in rens}) > 1
        )
        laws_report.append(
            {
                "name": scope.name,
                "osa_count": len(scope.osa_files),
                "old_provisions_renamed": len(scope.renames),
                "ambiguous_par_suffixes": amb_suffixes,
                "skipped_osa_files": [
                    str(p.relative_to(PROJECT_ROOT))
                    for p in scope.skipped_osa_files
                ],
            }
        )

    return {
        "pipeline": "migrate_multipart_iri_scheme",
        "mode": mode,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "laws": laws_report,
        "rename_map_size": plan.rename_map_size,
        "ambiguous_canonical_choices": plan.ambiguities,
        "files_inspected": files_inspected,
        "files_modified": files_modified,
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Migrate multipart-law provision IRIs from the legacy "
            "estleg:<PREFIX>_Par_<n> scheme to the disambiguated "
            "estleg:<PREFIX>_Osa<N>_Par_<n> scheme."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Actually rewrite files. Without this flag the script runs "
            "in dry-run mode and only writes the report."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Default mode: build the plan, write the report, modify "
            "nothing. Mutually exclusive with --apply."
        ),
    )
    parser.add_argument(
        "--law",
        action="append",
        dest="laws",
        default=None,
        help=(
            "Restrict the migration to one law (repeat to allow several). "
            "Defaults to every auto-detected multipart law."
        ),
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=DEFAULT_REPORT_PATH,
        help=(
            "Where to write the migration plan report (JSON). "
            f"Defaults to {DEFAULT_REPORT_PATH.relative_to(PROJECT_ROOT)}."
        ),
    )
    parser.add_argument(
        "--krr-dir",
        type=Path,
        default=KRR_DIR,
        help="Override the krr_outputs/ root (used by the tests).",
    )
    args = parser.parse_args(argv)

    if args.apply and args.dry_run:
        parser.error("--apply and --dry-run are mutually exclusive")
    apply_changes = bool(args.apply)
    mode = "apply" if apply_changes else "dry-run"

    krr_dir = args.krr_dir.resolve()
    if not krr_dir.is_dir():
        parser.error(f"krr-dir does not exist or is not a directory: {krr_dir}")

    plan = build_plan(krr_dir, laws=args.laws)
    files_inspected, files_modified = execute_plan(
        plan, krr_dir, apply=apply_changes
    )

    report = build_report(
        plan,
        mode=mode,
        files_inspected=files_inspected,
        files_modified=files_modified,
    )
    _write_json(args.report, report)

    print(f"[{mode}] discovered laws: {len(plan.scopes)}")
    print(f"[{mode}] rename map size: {plan.rename_map_size}")
    print(f"[{mode}] ambiguous canonical choices: {len(plan.ambiguities)}")
    print(f"[{mode}] files inspected: {files_inspected}")
    if apply_changes:
        print(f"[{mode}] files modified: {files_modified}")
    else:
        print(f"[{mode}] files that would change: {files_modified}")
    print(f"[{mode}] report: {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

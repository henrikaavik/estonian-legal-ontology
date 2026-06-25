"""Strip spliced RT-citation boilerplate from concept ``skos:definition`` (#580).

A subset of ``estleg:LegalConcept`` / ``estleg:Concept`` ``skos:definition``
values are contaminated by an inline Riigi Teataja *amendment-citation*
boilerplate block that the extractor's lazy ``DEFINITION_PATTERN`` swept up from
the source XML's ``<avaldamismarge>`` (publication-mark) nodes::

    "hauaplatsi … ühendatud rajatis; RT IV 2020-10-01 2 401102020002 2020-10-04"
                                    └──────────── spliced citation ───────────┘
    "teenistuja palga … eest; RT IV 2019-03-07 13 407032019013 2019-03-10 9) töötasu …"
                            └──────── citation ───────┘└── NEXT item bled in ──┘

The boilerplate always begins with an ``RT`` register marker — ``RT I``,
``RT IV``, ``RT Õ`` (optionally with the ``V`` variant) — immediately followed
by an ISO ``YYYY-MM-DD`` date, then the issue / global-id / publication-date run
of the citation, and (sometimes) text that has bled in from the *next* list item.

Everything from that ``RT … YYYY-MM-DD`` marker onward is contamination: the
real legal definition always precedes it, and what follows is either the
citation tail or foreign next-item text. So the conservative, deterministic fix
is to **cut the value at the first ``RT [IÕ]+V?\\s+YYYY-MM-DD`` marker** and tidy
the trailing separator left behind (``"… rajatis;"`` -> ``"… rajatis"``).

What this is careful NOT to do:
  * It never fires unless the ``RT … <date>`` register marker is present, so a
    definition that merely *mentions* a four-digit year is untouched.
  * It only removes the citation TAIL; the legitimate text before the marker is
    preserved byte-for-byte.
  * A value that becomes empty / placeholder-only after stripping (the
    citation-ONLY definitions, e.g. ``"RT IV 2019-05-02 4 …"``) is reported but
    left as an empty string rather than deleted — removing the
    ``skos:definition`` key entirely is a structural change the canonical
    re-extraction (which drops noise definitions via ``is_noise_definition``)
    owns, not this surgical post-process. An empty definition is still cleaner
    than a citation masquerading as a definition.

Scope / safety:
  * Operates on ``krr_outputs/concepts/concepts_combined.jsonld`` by default;
    git-LFS pointer files are skipped before ``json.loads``.
  * Handles both the single-object (``LegalConcept``) and list-valued
    (``Concept``) ``skos:definition`` shapes, language-tagged or bare.
  * Idempotent: a re-run finds no ``RT … <date>`` marker and reports 0 changes.
  * Atomic writes via ``estleg_common.save_json``.
  * ``--dry-run`` reports counts without writing.

Run (offline, idempotent)::

    python3 scripts/fix_concept_definition_splices.py
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from estleg_common import REPO_ROOT, save_json

# The RT amendment-citation marker: register sigil (``I`` / ``IV`` / ``Õ`` /
# ``ÕV`` …) followed by an ISO date. ``[IÕ]+V?`` covers ``I``, ``IV``, ``Õ``,
# ``ÕV``. The leading ``\s*`` lets us also swallow a separating space so the cut
# point sits right before the citation.  Anchored on the date so a stray bare
# "RT" inside running prose (without a following date) can never trigger a cut.
RT_CITATION_MARKER = re.compile(r"\s*RT\s+[IÕ]+V?\s+\d{4}-\d{2}-\d{2}.*$", re.DOTALL)

# Separators / whitespace the real text might end on once the citation tail is
# removed (``"… rajatis;"`` / ``"… tähenduses."`` keep the period; a dangling
# ``;`` / ``,`` / dash is trimmed).
_TRAILING_SEP = " \t\r\n;,–—-"

DEFAULT_CONCEPTS_FILE = (
    REPO_ROOT / "krr_outputs" / "concepts" / "concepts_combined.jsonld"
)


def strip_citation_tail(definition: str) -> str:
    """Remove a spliced ``RT … YYYY-MM-DD …`` citation tail from ``definition``.

    Returns the cleaned text. If no RT-citation marker is present the input is
    returned unchanged (so the function is a safe no-op on clean definitions).
    The dangling separator left where the citation was cut is trimmed; internal
    text is never modified.
    """
    if not RT_CITATION_MARKER.search(definition):
        return definition
    cleaned = RT_CITATION_MARKER.sub("", definition)
    return cleaned.rstrip(_TRAILING_SEP)


def _clean_value_obj(value):
    """Clean one ``skos:definition`` entry (a ``{"@value": …}`` obj or a str).

    Returns ``(new_value, changed)``.
    """
    if isinstance(value, dict):
        text = value.get("@value")
        if not isinstance(text, str):
            return value, False
        cleaned = strip_citation_tail(text)
        if cleaned == text:
            return value, False
        new_obj = dict(value)
        new_obj["@value"] = cleaned
        return new_obj, True
    if isinstance(value, str):
        cleaned = strip_citation_tail(value)
        return (cleaned, cleaned != value)
    return value, False


def clean_definition_field(field):
    """Clean a ``skos:definition`` field (single value or a list of values).

    Returns ``(new_field, n_values_changed)``.
    """
    if isinstance(field, list):
        out = []
        changed = 0
        for item in field:
            new_item, did = _clean_value_obj(item)
            out.append(new_item)
            changed += 1 if did else 0
        return out, changed
    new_field, did = _clean_value_obj(field)
    return new_field, (1 if did else 0)


def migrate_doc(doc: dict) -> tuple[int, int]:
    """Strip citation tails on every ``skos:definition`` in ``doc`` in place.

    Returns ``(nodes_changed, values_changed)``.
    """
    graph = doc.get("@graph")
    if not isinstance(graph, list):
        return 0, 0
    nodes_changed = 0
    values_changed = 0
    for node in graph:
        if not isinstance(node, dict):
            continue
        field = node.get("skos:definition")
        if field is None:
            continue
        new_field, n_changed = clean_definition_field(field)
        if n_changed:
            node["skos:definition"] = new_field
            nodes_changed += 1
            values_changed += n_changed
    return nodes_changed, values_changed


def _is_lfs_pointer(path: Path) -> bool:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return fh.read(60).startswith("version https://git-lfs")
    except OSError:
        return True


def process_file(path: Path, dry_run: bool) -> tuple[int, int]:
    """Migrate one concepts file. Returns ``(nodes_changed, values_changed)``."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    nodes, values = migrate_doc(doc)
    if values and not dry_run:
        save_json(path, doc)
    return nodes, values


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--concepts-file",
        type=Path,
        default=DEFAULT_CONCEPTS_FILE,
        help="concepts_combined.jsonld to clean (default: krr_outputs/concepts/…).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing.",
    )
    args = parser.parse_args(argv)

    path: Path = args.concepts_file
    if not path.is_file():
        print(f"ERROR: not a file: {path}")
        return 1
    if _is_lfs_pointer(path):
        print(f"ERROR: {path} is a git-LFS pointer; run `git lfs pull` first.")
        return 1

    print("=" * 70)
    print("Strip spliced RT-citation tails from skos:definition (#580)")
    print(f"  File: {path}")
    print("=" * 70)

    nodes, values = process_file(path, args.dry_run)
    verb = "would clean" if args.dry_run else "cleaned"
    print(f"\n  Nodes {verb}:  {nodes}")
    print(f"  Values {verb}: {values}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

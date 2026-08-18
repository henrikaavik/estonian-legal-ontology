"""Read-only consumer client for the Estonian Legal Ontology corpus."""

from estleg_client.load import (
    LawNotFoundError,
    corpus_root,
    load_law,
    provisions_of,
    resolve_iri,
    sanctions_of,
)

__all__ = [
    "LawNotFoundError",
    "corpus_root",
    "load_law",
    "provisions_of",
    "resolve_iri",
    "sanctions_of",
]

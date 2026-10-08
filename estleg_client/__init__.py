"""Read-only consumer client for the Estonian Legal Ontology corpus.

Five corpora, one loader and one row iterator each::

    from estleg_client import load_law, load_regulation, load_court_decision
    from estleg_client import load_draft, load_eu_act, iter_rows

    graph = load_law("ABIPOL")                    # rdflib.Graph
    for row in iter_rows(graph, "sanctions"):     # SanctionRow dataclasses
        print(row.iri, row.amount_eur, row.subject)

Everything listed in ``__all__`` is the public API and follows semantic
versioning (see ``estleg_client/README.md``). Modules and names starting
with ``_`` are private.
"""

from estleg_client._corpus import (
    ENV_CACHE,
    ENV_ROOT,
    CorpusNotFoundError,
    CorpusUnavailableError,
    corpus_root,
    default_cache_dir,
)
from estleg_client._jsonld import has_type
from estleg_client.corpora import (
    iter_court_decisions,
    iter_drafts,
    iter_eu_acts,
    iter_regulations,
    load_court_decision,
    load_court_decisions,
    load_draft,
    load_eu_act,
    load_regulation,
)
from estleg_client.download import DownloadError, fetch_corpus
from estleg_client.load import (
    LawNotFoundError,
    NotFoundError,
    load_law,
    provisions_of,
    resolve_iri,
    sanctions_of,
    typed_iris,
)
from estleg_client.rows import (
    CitationRow,
    DecisionRow,
    DraftRow,
    EuActRow,
    ProvisionRow,
    RegulationRow,
    Row,
    SanctionRow,
    citation_rows,
    decision_rows,
    draft_rows,
    eu_act_rows,
    iter_rows,
    provision_rows,
    regulation_rows,
    sanction_rows,
)

__version__ = "1.0.0"

__all__ = [
    "ENV_CACHE",
    "ENV_ROOT",
    "CitationRow",
    "CorpusNotFoundError",
    "CorpusUnavailableError",
    "DecisionRow",
    "DownloadError",
    "DraftRow",
    "EuActRow",
    "LawNotFoundError",
    "NotFoundError",
    "ProvisionRow",
    "RegulationRow",
    "Row",
    "SanctionRow",
    "__version__",
    "citation_rows",
    "corpus_root",
    "decision_rows",
    "default_cache_dir",
    "draft_rows",
    "eu_act_rows",
    "fetch_corpus",
    "has_type",
    "iter_court_decisions",
    "iter_drafts",
    "iter_eu_acts",
    "iter_regulations",
    "iter_rows",
    "load_court_decision",
    "load_court_decisions",
    "load_draft",
    "load_eu_act",
    "load_law",
    "load_regulation",
    "provision_rows",
    "provisions_of",
    "regulation_rows",
    "resolve_iri",
    "sanction_rows",
    "sanctions_of",
    "typed_iris",
]

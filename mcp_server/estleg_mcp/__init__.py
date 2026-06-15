"""estleg-mcp: a stdio MCP server over the Estonian Legal Ontology corpus.

The package is split into two layers:

* :mod:`estleg_mcp.data` -- a pure data-access layer (no MCP imports) that
  resolves laws, reads provisions, and derives riigiteataja.ee citations from
  the JSON-LD corpus under ``krr_outputs/``.
* :mod:`estleg_mcp.server` -- a thin FastMCP wrapper that exposes the data
  layer as natural-language tools for a lawmaker to query.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"

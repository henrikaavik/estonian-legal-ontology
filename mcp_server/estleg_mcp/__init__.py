"""estleg-mcp: an MCP server (stdio + streamable HTTP) over the Estonian Legal Ontology.

The package is split into layers:

* :mod:`estleg_mcp.data` -- a pure data-access layer (no MCP imports) that
  resolves laws, reads provisions, and derives riigiteataja.ee citations from
  the JSON-LD corpus under ``krr_outputs/``.
* :mod:`estleg_mcp.server` -- a thin FastMCP wrapper that exposes the data
  layer as natural-language tools for a lawmaker to query.
* :mod:`estleg_mcp.i18n`, :mod:`estleg_mcp.provenance`,
  :mod:`estleg_mcp.audit`, :mod:`estleg_mcp.security` -- the #714 hardening:
  Estonian-first wording, the snapshot envelope, the per-call audit line, and
  HTTP access control (per-consumer tokens, fail closed, rate limit).
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.2.0"

"""Temporary disk storage with RDFLib's existing SPARQL evaluator.

JSON-LD parsing and SHACL evaluation still run in RDFLib and pySHACL. Encode
literals reversibly because Oxigraph otherwise canonicalises numeric values
and merges plain strings with xsd:string, changing RDFLib's SHACL comparisons.
The temporary database is an implementation detail, not an RDF export.
"""
import json

from oxrdflib.store import (
    OxigraphStore, from_ox, from_ox_graph_name, to_ox, to_ox_quad_pattern,
)
from pyoxigraph import Literal as OxLiteral, NamedNode, Quad
from rdflib import Literal, URIRef
from rdflib.store import Store

_LITERAL_ENCODING = NamedNode("urn:estleg:temporary-store:literal")


def _to_storage(term):
    if isinstance(term, Literal):
        return OxLiteral(json.dumps([
            str(term), term.language.lower() if term.language else None,
            str(term.datatype) if term.datatype else None,
        ], ensure_ascii=False, separators=(",", ":")), datatype=_LITERAL_ENCODING)
    return to_ox(term)


def _pattern(triple, context=None):
    # Reuse the adapter's graph/default-union handling, with lossless terms.
    graph_name = to_ox_quad_pattern((None, None, None), context)[3]
    return (*(_to_storage(term) for term in triple), graph_name)


def _from_storage(term):
    if isinstance(term, OxLiteral):
        value, language, datatype = json.loads(term.value)
        return Literal(
            value, lang=language, datatype=URIRef(datatype) if datatype else None,
            normalize=False,
        )
    return from_ox(term)


class DiskStore(OxigraphStore):
    """Use Oxigraph's disk indexes but keep RDFLib query semantics."""

    def query(self, *args, **kwargs):
        # Graph.query explicitly falls back to RDFLib on NotImplementedError.
        raise NotImplementedError

    def update(self, *args, **kwargs):
        raise NotImplementedError

    def add(self, triple, context, quoted=False):
        if quoted:
            raise ValueError("DiskStore is not formula aware")
        self._inner.add(Quad(*(_to_storage(t) for t in triple), to_ox(context)))
        Store.add(self, triple, context, quoted)

    def addN(self, quads):  # noqa: N802
        for s, p, o, context in quads:
            self.add((s, p, o), context)

    def remove(self, triple, context=None):
        for quad in self._inner.quads_for_pattern(*_pattern(triple, context)):
            self._inner.remove(quad)
        Store.remove(self, triple, context)

    def contexts(self, triple=None):
        if triple is None:
            yield from super().contexts()
        else:
            for quad in self._inner.quads_for_pattern(*_pattern(triple)):
                yield from_ox_graph_name(quad.graph_name, self)

    def triples(self, triple_pattern, context=None):
        try:
            quads = self._inner.quads_for_pattern(
                *_pattern(triple_pattern, context)
            )
        except (TypeError, ValueError):
            return
        for quad in quads:
            yield (
                tuple(_from_storage(t) for t in (quad.subject, quad.predicate, quad.object)),
                iter((from_ox_graph_name(quad.graph_name, self),)),
            )

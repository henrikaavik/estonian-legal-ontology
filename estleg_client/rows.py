"""Typed, flat row views over corpus nodes (for CSV writers, pandas, BI tools).

Every row is a frozen dataclass with an ``iri`` field (the full
``https://w3id.org/estleg/...`` IRI as ``str``), plain ``str`` / ``int`` /
``bool`` / ``Decimal`` / ``tuple[str, ...]`` fields, ``None`` where the corpus
has no value, and ``as_dict()`` returning CSV/JSON-ready primitives. Rows are
built either from an ``rdflib.Graph`` (``provision_rows(graph)`` ...,
``iter_rows(graph, kind)``) or straight from the JSON-LD files by the corpus
iterators in :mod:`estleg_client.corpora`; both paths share one builder per
row type.

Sanction normalisation (``SanctionRow``)
---------------------------------------
* ``amount_eur`` / ``min_amount_eur`` / ``max_amount_eur`` are filled only for
  money-denominated units: ``monetary`` in ``EUR`` (taken as is), ``monetary``
  in ``EEK`` (divided by the fixed conversion rate 15.6466) and
  ``fine_units`` (trahviühik; multiplied by 4 EUR, KarS § 47 lg 1).
  ``amount_eur`` is the upper bound (``max_amount_eur``), the figure a CSV
  column of "fine up to" needs.
* ``daily_rates`` (päevamäär, income-dependent), ``percent_of_turnover``,
  ``years`` and ``days`` are not money amounts; their EUR fields are ``None``.
* ``subject`` is ``natural_person`` / ``legal_person`` / ``None``. The corpus
  has no explicit subject property today, so it is *inferred* from the
  penalty kind (``subject_source="inferred"``): ``fine_units``,
  ``daily_rates``, imprisonment and arrest apply to natural persons (KarS
  §§ 44, 45, 47 lg 1, 48); a fine or pecuniary punishment in euros or as a
  share of turnover, and compulsory dissolution, apply to legal persons (KarS
  §§ 44 lg 8, 46, 47 lg 2). Coercive payments and confiscation stay ``None``.
  An explicit ``estleg:sanctionSubject`` value, if a future release adds one,
  wins (``subject_source="explicit"``).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, ClassVar, Literal, Protocol

from rdflib import RDF, Graph, Literal as RDFLiteral, URIRef

from estleg_client._jsonld import as_list, compact, expand, node_types, prefixes_of
from estleg_client.vocab import (
    CITATION_TYPES,
    DECISION_TYPES,
    DRAFT_TYPES,
    EU_ACT_TYPES,
    KOV_PROVISION,
    MUNICIPAL_REGULATION,
    PROVISION_TYPES,
    REGULATION_TYPES,
    SANCTION_TYPES,
    SUBSECTION,
    SUBSECTION_TYPES,
)

__all__ = [
    "CitationRow",
    "DecisionRow",
    "DraftRow",
    "EEK_PER_EUR",
    "EUR_PER_FINE_UNIT",
    "EuActRow",
    "ProvisionRow",
    "RegulationRow",
    "Row",
    "RowKind",
    "SanctionRow",
    "citation_rows",
    "decision_rows",
    "draft_rows",
    "eu_act_rows",
    "iter_rows",
    "provision_rows",
    "regulation_rows",
    "sanction_rows",
]

#: Euros per trahviühik (fine unit), KarS § 47 lg 1.
EUR_PER_FINE_UNIT = Decimal(4)
#: Fixed EEK/EUR conversion rate (Council Regulation (EU) No 671/2010).
EEK_PER_EUR = Decimal("15.6466")

RowKind = Literal[
    "provisions",
    "subsections",
    "sanctions",
    "citations",
    "regulations",
    "decisions",
    "drafts",
    "eu_acts",
]


# ── node access ──────────────────────────────────────────────────────────────


class NodeView(Protocol):
    """Read access to one node, independent of rdflib vs. raw JSON-LD."""

    iri: str

    def types(self) -> frozenset[str]: ...

    def iris(self, pred: str) -> list[str]: ...

    def literals(self, pred: str) -> list[tuple[str, str | None]]: ...


class NodeStore(Protocol):
    """Lookup of nodes by IRI and by exact type."""

    def get(self, iri: str) -> NodeView | None: ...

    def typed(self, *types: str, exclude: tuple[str, ...] = ()) -> Iterator[NodeView]: ...


class GraphNode:
    """``NodeView`` over an ``rdflib.Graph`` subject."""

    __slots__ = ("_graph", "_subject", "iri")

    def __init__(self, graph: Graph, subject: URIRef) -> None:
        self._graph = graph
        self._subject = subject
        self.iri = str(subject)

    def types(self) -> frozenset[str]:
        return frozenset(str(o) for o in self._graph.objects(self._subject, RDF.type))

    def iris(self, pred: str) -> list[str]:
        return sorted(
            str(o)
            for o in self._graph.objects(self._subject, URIRef(expand(pred)))
            if isinstance(o, URIRef)
        )

    def literals(self, pred: str) -> list[tuple[str, str | None]]:
        out: list[tuple[str, str | None]] = []
        for obj in self._graph.objects(self._subject, URIRef(expand(pred))):
            if isinstance(obj, RDFLiteral):
                out.append((str(obj), obj.language))
        return sorted(out, key=lambda item: (item[1] or "", item[0]))


class GraphStore:
    """``NodeStore`` over an ``rdflib.Graph``."""

    def __init__(self, graph: Graph) -> None:
        self._graph = graph

    def get(self, iri: str) -> NodeView | None:
        subject = URIRef(iri)
        if (subject, None, None) in self._graph:
            return GraphNode(self._graph, subject)
        return None

    def typed(self, *types: str, exclude: tuple[str, ...] = ()) -> Iterator[NodeView]:
        seen: set[URIRef] = set()
        excluded = [URIRef(expand(t)) for t in exclude]
        for type_iri in types:
            for subject in self._graph.subjects(RDF.type, URIRef(expand(type_iri))):
                if not isinstance(subject, URIRef) or subject in seen:
                    continue
                if any((subject, RDF.type, ex) in self._graph for ex in excluded):
                    continue
                seen.add(subject)
        for subject in sorted(seen, key=str):
            yield GraphNode(self._graph, subject)


class DictNode:
    """``NodeView`` over a compact JSON-LD node dict."""

    __slots__ = ("_node", "_prefixes", "_keys", "iri")

    def __init__(self, node: Mapping[str, Any], prefixes: Mapping[str, str]) -> None:
        self._node = node
        self._prefixes = prefixes
        self.iri = expand(str(node.get("@id", "")), prefixes)
        self._keys = {expand(key, prefixes): key for key in node if not key.startswith("@")}

    def types(self) -> frozenset[str]:
        return node_types(self._node, self._prefixes)

    def _values(self, pred: str) -> list[Any]:
        key = self._keys.get(expand(pred))
        return as_list(self._node.get(key)) if key is not None else []

    def iris(self, pred: str) -> list[str]:
        out = []
        for value in self._values(pred):
            if isinstance(value, Mapping) and isinstance(value.get("@id"), str):
                out.append(expand(value["@id"], self._prefixes))
        return sorted(out)

    def literals(self, pred: str) -> list[tuple[str, str | None]]:
        out: list[tuple[str, str | None]] = []
        for value in self._values(pred):
            if isinstance(value, Mapping):
                if "@value" not in value:
                    continue
                raw, lang = value["@value"], value.get("@language")
            else:
                raw, lang = value, None
            if isinstance(raw, bool):
                text = "true" if raw else "false"
            elif isinstance(raw, (str, int, float)):
                text = str(raw)
            else:
                continue
            out.append((text, lang if isinstance(lang, str) else None))
        return out


class DictStore:
    """``NodeStore`` over JSON-LD node dicts (no rdflib parse)."""

    def __init__(self, nodes: Iterable[Mapping[str, Any]], context: Any = None) -> None:
        self._prefixes = prefixes_of(context or {})
        self._by_iri: dict[str, DictNode] = {}
        for node in nodes:
            if isinstance(node, Mapping) and isinstance(node.get("@id"), str):
                view = DictNode(node, self._prefixes)
                self._by_iri.setdefault(view.iri, view)

    def get(self, iri: str) -> NodeView | None:
        return self._by_iri.get(iri)

    def typed(self, *types: str, exclude: tuple[str, ...] = ()) -> Iterator[NodeView]:
        wanted = {expand(t) for t in types}
        excluded = {expand(t) for t in exclude}
        for view in self._by_iri.values():
            node_types = view.types()
            if not wanted.isdisjoint(node_types) and excluded.isdisjoint(node_types):
                yield view


# ── value helpers ────────────────────────────────────────────────────────────


def _text(view: NodeView | None, *preds: str, lang: str = "et") -> str | None:
    if view is None:
        return None
    for pred in preds:
        values = view.literals(pred)
        if not values:
            continue
        for wanted in (lang, None):
            for text, value_lang in values:
                if value_lang == wanted and text:
                    return text
        for text, _ in values:
            if text:
                return text
    return None


def _texts(view: NodeView, pred: str) -> tuple[str, ...]:
    return tuple(sorted({text for text, _ in view.literals(pred) if text}))


def _iri(view: NodeView | None, pred: str) -> str | None:
    if view is None:
        return None
    values = view.iris(pred)
    return values[0] if values else None


def _url(view: NodeView | None, *preds: str) -> str | None:
    if view is None:
        return None
    for pred in preds:
        iri = _iri(view, pred)
        if iri:
            return iri
        text = _text(view, pred)
        if text and text.startswith(("http://", "https://")):
            return text
    return None


def _bool(view: NodeView, pred: str) -> bool | None:
    text = _text(view, pred)
    if text is None:
        return None
    lowered = text.strip().lower()
    if lowered in {"true", "1"}:
        return True
    if lowered in {"false", "0"}:
        return False
    return None


def _decimal(view: NodeView, pred: str) -> Decimal | None:
    text = _text(view, pred)
    if text is None:
        return None
    try:
        return Decimal(text.strip())
    except InvalidOperation:
        return None


def _local_after(iri: str | None, marker: str) -> str | None:
    """``estleg:CaseType_Administrative`` with marker ``CaseType_`` -> ``Administrative``."""
    if not iri:
        return None
    local = compact(iri)
    return local.split(marker, 1)[1] if marker in local else local


def act_prefix_of(iri: str | None) -> str | None:
    """Registry prefix of an act or one of its nodes (``ABIPOL``, ``Reg_1057801``)."""
    if not iri:
        return None
    local = compact(iri)
    for marker in ("_Par_", "_Map", "_Lg_", "_Annex"):
        if marker in local:
            return local.split(marker, 1)[0]
    for head in ("Sanction_", "Citation_", "Chapter_", "Division_"):
        if local.startswith(head):
            return act_prefix_of(local[len(head) :])
    return local


# ── row types ────────────────────────────────────────────────────────────────


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, tuple):
        return ";".join(str(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class Row:
    """Base class: every row has an ``iri``."""

    iri: str

    kind: ClassVar[str] = "row"

    def as_dict(self) -> dict[str, Any]:
        """Field -> value with ``Decimal`` as int/float and tuples joined by ``;``."""
        return {f.name: _plain(getattr(self, f.name)) for f in dataclasses.fields(self)}

    @classmethod
    def columns(cls) -> list[str]:
        """Field names in declaration order (CSV header)."""
        return [f.name for f in dataclasses.fields(cls)]


@dataclass(frozen=True, slots=True)
class ProvisionRow(Row):
    """A paragraph (``§``, ``level="provision"``) or a lõige (``level="subsection"``)."""

    level: str
    act_iri: str | None
    act_prefix: str | None
    paragraph: str | None
    subsection: str | None
    parent_iri: str | None
    label: str | None
    summary: str | None
    legal_text: str | None
    is_kov: bool
    temporal_status: str | None
    valid_from: str | None
    valid_to: str | None
    source_url: str | None

    kind: ClassVar[str] = "provisions"


@dataclass(frozen=True, slots=True)
class SanctionRow(Row):
    """An ``estleg:Sanction`` with EUR-normalised amounts (see module docs)."""

    provision_iri: str | None
    act_prefix: str | None
    sanction_type: str | None
    min_amount: Decimal | None
    max_amount: Decimal | None
    unit: str | None
    currency: str | None
    min_amount_eur: Decimal | None
    max_amount_eur: Decimal | None
    amount_eur: Decimal | None
    subject: str | None
    subject_source: str | None
    statutory_default: bool | None
    label: str | None
    source_url: str | None

    kind: ClassVar[str] = "sanctions"


@dataclass(frozen=True, slots=True)
class CitationRow(Row):
    source_iri: str | None
    target_iri: str | None
    act_prefix: str | None
    text: str | None
    detail: str | None

    kind: ClassVar[str] = "citations"


@dataclass(frozen=True, slots=True)
class RegulationRow(Row):
    """A state (``NationalRegulation``) or municipal (``MunicipalRegulation``) act."""

    act_prefix: str | None
    rt_id: str | None
    global_id: str | None
    title: str | None
    issuer: str | None
    is_kov: bool
    municipality_iri: str | None
    document_type: str | None
    act_number: str | None
    temporal_status: str | None
    valid_from: str | None
    valid_to: str | None
    last_amended: str | None
    publication_date: str | None
    provision_count: int
    source_url: str | None

    kind: ClassVar[str] = "regulations"


@dataclass(frozen=True, slots=True)
class DecisionRow(Row):
    """A Riigikohus decision."""

    case_number: str | None
    ecli: str | None
    decision_date: str | None
    year: int | None
    case_type: str | None
    decision_type: str | None
    chamber: str | None
    judges: tuple[str, ...]
    label: str | None
    summary: str | None
    legal_text: str | None
    interprets: tuple[str, ...]
    interpretation_outdated: bool | None
    source_url: str | None
    rikos_url: str | None

    kind: ClassVar[str] = "decisions"


@dataclass(frozen=True, slots=True)
class DraftRow(Row):
    """An EIS draft (eelnõu)."""

    eis_number: str | None
    title: str | None
    phase: str | None
    draft_type: str | None
    initiator: str | None
    publication_date: str | None
    change_type: str | None
    affected_laws: tuple[str, ...]
    amends_law_iris: tuple[str, ...]
    enacted_as: tuple[str, ...]
    source_url: str | None

    kind: ClassVar[str] = "drafts"


@dataclass(frozen=True, slots=True)
class EuActRow(Row):
    """An EUR-Lex act (regulation, directive, decision, ...)."""

    celex: str | None
    title: str | None
    doc_type: str | None
    document_date: str | None
    in_force: bool | None
    eli: str | None
    institution: str | None
    transposition_deadline: str | None
    transposed_by: tuple[str, ...]
    estonia_relevant: bool | None
    source_url: str | None

    kind: ClassVar[str] = "eu_acts"


# ── builders ─────────────────────────────────────────────────────────────────


def _act_of(view: NodeView, store: NodeStore) -> NodeView | None:
    act = _iri(view, "estleg:partOfAct")
    if act is None:
        parent = _iri(view, "estleg:parentProvision")
        parent_view = store.get(parent) if parent else None
        act = _iri(parent_view, "estleg:partOfAct") if parent_view else None
    return store.get(act) if act else None


def build_provision_row(view: NodeView, store: NodeStore) -> ProvisionRow:
    types = view.types()
    is_subsection = SUBSECTION in types
    act = _act_of(view, store)
    act_iri = act.iri if act else _iri(view, "estleg:partOfAct")
    parent = _iri(view, "estleg:parentProvision")
    parent_view = store.get(parent) if parent else None
    paragraph = _text(view, "estleg:paragrahv") or _text(parent_view, "estleg:paragrahv")
    is_kov = KOV_PROVISION in types or (
        act is not None and MUNICIPAL_REGULATION in act.types()
    )
    return ProvisionRow(
        iri=view.iri,
        level="subsection" if is_subsection else "provision",
        act_iri=act_iri,
        act_prefix=act_prefix_of(act_iri or view.iri),
        paragraph=paragraph,
        subsection=_text(view, "estleg:subsectionNumber") if is_subsection else None,
        parent_iri=parent,
        label=_text(view, "rdfs:label"),
        summary=_text(view, "estleg:summary"),
        legal_text=_text(view, "estleg:legalText"),
        is_kov=is_kov,
        temporal_status=_text(act, "estleg:temporalStatus"),
        valid_from=_text(act, "estleg:entryIntoForce"),
        valid_to=_text(act, "estleg:repealDate"),
        source_url=_url(act, "dcterms:source"),
    )


def _to_eur(amount: Decimal | None, unit: str | None, currency: str | None) -> Decimal | None:
    if amount is None or unit is None:
        return None
    if unit == "fine_units":
        return amount * EUR_PER_FINE_UNIT
    if unit == "monetary":
        code = (currency or "EUR").upper()
        if code == "EUR":
            return amount
        if code == "EEK":
            return (amount / EEK_PER_EUR).quantize(Decimal("0.01"))
    return None


_NATURAL_UNITS = {"fine_units", "daily_rates"}
_NATURAL_TYPES = {"imprisonment", "arrest"}
_LEGAL_TYPES = {"compulsory_dissolution"}
_LEGAL_MONEY_TYPES = {"fine", "pecuniary_punishment"}


def _subject(
    view: NodeView, sanction_type: str | None, unit: str | None
) -> tuple[str | None, str | None]:
    explicit = _text(view, "estleg:sanctionSubject")
    if explicit:
        return explicit, "explicit"
    inferred: str | None = None
    if unit in _NATURAL_UNITS or sanction_type in _NATURAL_TYPES:
        inferred = "natural_person"
    elif sanction_type in _LEGAL_TYPES:
        inferred = "legal_person"
    elif sanction_type in _LEGAL_MONEY_TYPES and unit in {"monetary", "percent_of_turnover"}:
        inferred = "legal_person"
    return inferred, ("inferred" if inferred else None)


def build_sanction_row(view: NodeView, store: NodeStore) -> SanctionRow:
    provision = _iri(view, "estleg:applicableProvision")
    provision_view = store.get(provision) if provision else None
    act = _act_of(provision_view, store) if provision_view else None
    sanction_type = _text(view, "estleg:sanctionType")
    max_amount = _decimal(view, "estleg:maxPenaltyAmount")
    min_amount = _decimal(view, "estleg:minPenaltyAmount")
    max_unit = _text(view, "estleg:maxPenaltyUnit")
    min_unit = _text(view, "estleg:minPenaltyUnit")
    unit = max_unit or min_unit
    currency = _text(view, "estleg:maxPenaltyCurrency") or _text(
        view, "estleg:minPenaltyCurrency"
    )
    max_eur = _to_eur(max_amount, max_unit, currency)
    min_eur = _to_eur(min_amount, min_unit or max_unit, currency)
    subject, subject_source = _subject(view, sanction_type, unit)
    statutory = _bool(view, "estleg:isStatutoryDefault")
    return SanctionRow(
        iri=view.iri,
        provision_iri=provision,
        act_prefix=act_prefix_of(provision or view.iri),
        sanction_type=sanction_type,
        min_amount=min_amount,
        max_amount=max_amount,
        unit=unit,
        currency=currency,
        min_amount_eur=min_eur,
        max_amount_eur=max_eur,
        amount_eur=max_eur,
        subject=subject,
        subject_source=subject_source,
        statutory_default=statutory,
        label=_text(view, "rdfs:label"),
        source_url=_url(act, "dcterms:source"),
    )


def build_citation_row(view: NodeView, store: NodeStore) -> CitationRow:
    source = _iri(view, "estleg:citationSource")
    return CitationRow(
        iri=view.iri,
        source_iri=source,
        target_iri=_iri(view, "estleg:citationTarget"),
        act_prefix=act_prefix_of(source or view.iri),
        text=_text(view, "estleg:citationText"),
        detail=_text(view, "estleg:citationDetail"),
    )


def build_regulation_row(view: NodeView, store: NodeStore) -> RegulationRow:
    provisions = sum(
        1
        for node in store.typed(*PROVISION_TYPES, exclude=SUBSECTION_TYPES)
        if _iri(node, "estleg:partOfAct") == view.iri
    )
    kov_flag = _bool(view, "estleg:isKov")
    return RegulationRow(
        iri=view.iri,
        act_prefix=act_prefix_of(view.iri),
        rt_id=_text(view, "estleg:terviktekstId"),
        global_id=_text(view, "estleg:globalId"),
        title=_text(view, "dc:source") or _text(view, "rdfs:label"),
        issuer=_text(view, "estleg:issuer"),
        is_kov=kov_flag if kov_flag is not None else MUNICIPAL_REGULATION in view.types(),
        municipality_iri=_iri(view, "estleg:enactedByMunicipality"),
        document_type=_text(view, "estleg:documentType"),
        act_number=_text(view, "estleg:actNumber"),
        temporal_status=_text(view, "estleg:temporalStatus"),
        valid_from=_text(view, "estleg:entryIntoForce"),
        valid_to=_text(view, "estleg:repealDate"),
        last_amended=_text(view, "estleg:lastAmendmentDate"),
        publication_date=_text(view, "estleg:publicationDate"),
        provision_count=provisions,
        source_url=_url(view, "dcterms:source"),
    )


def build_decision_row(view: NodeView, store: NodeStore) -> DecisionRow:
    date = _text(view, "estleg:decisionDate")
    year = int(date[:4]) if date and date[:4].isdigit() else None
    return DecisionRow(
        iri=view.iri,
        case_number=_text(view, "estleg:caseNumber"),
        ecli=_text(view, "estleg:ecliIdentifier"),
        decision_date=date,
        year=year,
        case_type=_local_after(_iri(view, "estleg:caseType"), "CaseType_"),
        decision_type=_local_after(_iri(view, "estleg:decisionType"), "DecisionType_"),
        chamber=_text(view, "estleg:chamber"),
        judges=_texts(view, "estleg:judge"),
        label=_text(view, "rdfs:label"),
        summary=_text(view, "estleg:summary"),
        legal_text=_text(view, "estleg:legalText"),
        interprets=tuple(view.iris("estleg:interpretsLaw")),
        interpretation_outdated=_bool(view, "estleg:interpretationOutdated"),
        source_url=_url(view, "estleg:decisionLink"),
        rikos_url=_url(view, "estleg:rikosUrl"),
    )


def build_draft_row(view: NodeView, store: NodeStore) -> DraftRow:
    return DraftRow(
        iri=view.iri,
        eis_number=_text(view, "estleg:eisNumber"),
        title=_text(view, "rdfs:label"),
        phase=_local_after(_iri(view, "estleg:legislativePhase"), "Phase_"),
        draft_type=_local_after(_iri(view, "estleg:draftType"), "DraftType_"),
        initiator=_text(view, "estleg:initiator"),
        publication_date=_text(view, "estleg:publicationDate"),
        change_type=_text(view, "estleg:changeType"),
        affected_laws=_texts(view, "estleg:affectedLawName"),
        amends_law_iris=tuple(view.iris("estleg:amendsLaw")),
        enacted_as=tuple(view.iris("estleg:enactedAs")),
        source_url=_url(view, "estleg:eisLink"),
    )


def build_eu_act_row(view: NodeView, store: NodeStore) -> EuActRow:
    return EuActRow(
        iri=view.iri,
        celex=_text(view, "estleg:celexNumber"),
        title=_text(view, "dcterms:title") or _text(view, "rdfs:label"),
        doc_type=_local_after(_iri(view, "estleg:euDocumentType"), "EUDocType_"),
        document_date=_text(view, "estleg:documentDate"),
        in_force=_bool(view, "estleg:inForce"),
        eli=_url(view, "estleg:eliIdentifier"),
        institution=_local_after(_iri(view, "estleg:euInstitution"), "EUInst_"),
        transposition_deadline=_text(view, "estleg:transpositionDeadline"),
        transposed_by=tuple(view.iris("estleg:transposedBy")),
        estonia_relevant=_bool(view, "estleg:estoniaRelevant"),
        source_url=_url(view, "estleg:eurLexLink", "dcterms:source"),
    )


_Builder = Callable[[NodeView, NodeStore], Any]

# kind -> (types, excluded types, builder). Paragraph rows exclude subsections:
# the release aggregate types every lõige ``Subsection`` *and* ``LegalProvision``.
_KINDS: dict[str, tuple[tuple[str, ...], tuple[str, ...], _Builder]] = {
    "provisions": (PROVISION_TYPES, SUBSECTION_TYPES, build_provision_row),
    "subsections": (SUBSECTION_TYPES, (), build_provision_row),
    "sanctions": (SANCTION_TYPES, (), build_sanction_row),
    "citations": (CITATION_TYPES, (), build_citation_row),
    "regulations": (REGULATION_TYPES, (), build_regulation_row),
    "decisions": (DECISION_TYPES, (), build_decision_row),
    "drafts": (DRAFT_TYPES, (), build_draft_row),
    "eu_acts": (EU_ACT_TYPES, (), build_eu_act_row),
}

ROW_KINDS: tuple[str, ...] = tuple(_KINDS)


def iter_store_rows(store: NodeStore, kind: str) -> Iterator[Any]:
    """Rows of ``kind`` from any ``NodeStore`` (graph- or dict-backed)."""
    try:
        types, excluded, builder = _KINDS[kind]
    except KeyError:
        raise ValueError(f"unknown row kind {kind!r}; expected one of {ROW_KINDS}") from None
    for view in store.typed(*types, exclude=excluded):
        yield builder(view, store)


def iter_rows(graph: Graph, kind: RowKind) -> Iterator[Row]:
    """Yield typed rows of ``kind`` from an ``rdflib.Graph`` (exact type match).

    ``kind`` is one of ``provisions`` (paragraphs, incl. ``KovProvision``),
    ``subsections``, ``sanctions``, ``citations``, ``regulations``,
    ``decisions``, ``drafts`` or ``eu_acts``.
    """
    yield from iter_store_rows(GraphStore(graph), kind)


def provision_rows(graph: Graph, *, include_subsections: bool = False) -> list[ProvisionRow]:
    """``ProvisionRow`` per paragraph (and lõige when ``include_subsections``)."""
    rows = list(iter_rows(graph, "provisions"))
    if include_subsections:
        rows.extend(iter_rows(graph, "subsections"))
    return rows  # type: ignore[return-value]


def sanction_rows(graph: Graph) -> list[SanctionRow]:
    return list(iter_rows(graph, "sanctions"))  # type: ignore[arg-type]


def citation_rows(graph: Graph) -> list[CitationRow]:
    return list(iter_rows(graph, "citations"))  # type: ignore[arg-type]


def regulation_rows(graph: Graph) -> list[RegulationRow]:
    return list(iter_rows(graph, "regulations"))  # type: ignore[arg-type]


def decision_rows(graph: Graph) -> list[DecisionRow]:
    return list(iter_rows(graph, "decisions"))  # type: ignore[arg-type]


def draft_rows(graph: Graph) -> list[DraftRow]:
    return list(iter_rows(graph, "drafts"))  # type: ignore[arg-type]


def eu_act_rows(graph: Graph) -> list[EuActRow]:
    return list(iter_rows(graph, "eu_acts"))  # type: ignore[arg-type]

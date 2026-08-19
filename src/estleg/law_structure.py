"""Shared law XML → JSON-LD emission (#466).

Single-file and multipart law builders share this module so chapter /
division / cluster / subsection walks land once.
"""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from collections import Counter

from estleg.estleg_common import sanitize_id
from estleg.riigiteataja_common import ct, ln


def chapter_cluster_subject(cluster_id: str) -> dict:
    """Issue #436: chapters refer to their topic cluster; they are not the same individual."""
    return {"@id": cluster_id}


# RT I, ...)", "terviktekst RT paberkandjal", EU-Council boilerplate) inside
# these subtrees. Their ``tavatekst``/``lause``/``lauseOsa`` descendants must
# never leak into ``estleg:legalText`` / ``estleg:summary``.
_MARKER_TAGS: frozenset[str] = frozenset(
    {"muutmismarge", "avaldamismarge", "joustumismarge"}
)


def _iter_text_nodes(el: ET.Element):
    """Yield descendant elements of ``el`` in document order, skipping any
    amendment/publication/entry-into-force marker subtree.

    A drop-in replacement for ``el.iter()`` in the text extractors that
    prunes whole ``muutmismarge``/``avaldamismarge``/``joustumismarge``
    subtrees (#255) rather than walking into them. ``el`` itself is
    yielded only when it is not a marker root.
    """
    if ln(el.tag) in _MARKER_TAGS:
        return
    yield el
    for child in el:
        yield from _iter_text_nodes(child)


def _marker_pruned_text(el: ET.Element) -> str:
    """Return ``el``'s text content with marker subtrees pruned (#255).

    Mirrors ``"".join(el.itertext())`` but drops any text that lives
    inside a ``muutmismarge``/``avaldamismarge``/``joustumismarge``
    subtree, so a marker nested *inside* a ``loige``/``lause`` does not
    leak into the citable text the way raw ``itertext()`` would.
    """
    out: list[str] = []
    if el.text:
        out.append(el.text)
    for child in el:
        if ln(child.tag) in _MARKER_TAGS:
            if child.tail:
                out.append(child.tail)
            continue
        out.append(_marker_pruned_text(child))
        if child.tail:
            out.append(child.tail)
    return "".join(out)


# Issue #368: how far back from the hard ``max_len`` cap ``collect_text`` may
# scan for a sentence/word boundary before falling back to a raw slice. Keeps
# the cut close to the cap (so the preview stays ~full) while avoiding a cut
# mid-token.
_SUMMARY_BOUNDARY_WINDOW = 60
# Sentence terminators recognised by the boundary-aware truncation.
_SENTENCE_END_CHARS = ".!?…"


def _truncate_at_boundary(
    text: str, max_len: int, *, window: int = _SUMMARY_BOUNDARY_WINDOW
) -> str:
    """Return ``text`` cut to at most ``max_len`` chars on a clean boundary.

    Issue #368: a raw ``text[:max_len]`` slice severs the summary mid-word
    (``…kohustatud arvut``). Instead, when the text is longer than the cap
    we look back up to ``window`` characters for a sentence terminator
    (``.!?…`` followed by whitespace) and, failing that, the last
    whitespace, cutting there. Only if no boundary exists in the window do
    we fall back to the hard slice so the cap is always honoured.
    """
    if len(text) <= max_len:
        return text
    head = text[:max_len]
    lo = max(0, max_len - window)
    # Prefer a sentence boundary: a terminator inside the window whose next
    # char (still within ``head``) is whitespace.
    best_sentence = -1
    for i in range(max_len - 1, lo - 1, -1):
        if head[i] in _SENTENCE_END_CHARS:
            nxt = text[i + 1] if i + 1 < len(text) else " "
            if nxt.isspace():
                best_sentence = i + 1
                break
    if best_sentence != -1:
        return head[:best_sentence].rstrip()
    # Else cut at the last whitespace within the window.
    space = head.rfind(" ", lo)
    if space != -1:
        return head[:space].rstrip()
    # No boundary available: honour the cap with a raw slice.
    return head


def collect_text(el: ET.Element, max_len: int = 500) -> str:
    """Return a boundary-truncated preview of the provision's full text.

    Delegates to :func:`collect_full_text` (the lõige-structured, non-doubling
    text builder) and truncates at a sentence/word boundary (#368). #613: the
    previous independent leaf-walk matched both a ``lause`` container *and* its
    nested ``lauseOsa`` (``_iter_text_nodes`` yields parent then child), so it
    double-counted every multi-lõige body and emitted ``estleg:summary`` as the
    text repeated. Reusing the single source of provision text keeps the summary
    a clean, single preview that always agrees with ``estleg:legalText``.
    (collect_full_text already renders <sup>N</sup> as Unicode superscript, #572.)
    """
    full = collect_full_text(el)
    return _truncate_at_boundary(full, max_len) if full else ""


def provision_summary(text: str, title: str, display: str) -> str:
    """Return the SHACL-required summary for a provision node."""
    summary = text.strip()
    if summary:
        return summary
    if title.strip():
        return f"{display.strip()} {title.strip()}".strip()
    return display.strip() or "Sisukokkuvõte puudub"


# Mapping of Unicode superscript digits to plain digits.
_SUPERSCRIPT_DIGIT_MAP: dict[str, str] = {
    "¹": "1",  # superscript 1
    "²": "2",  # superscript 2
    "³": "3",  # superscript 3
    "⁰": "0",  # superscript 0
    "⁴": "4",  # superscript 4
    "⁵": "5",  # superscript 5
    "⁶": "6",  # superscript 6
    "⁷": "7",  # superscript 7
    "⁸": "8",  # superscript 8
    "⁹": "9",  # superscript 9
}

# Inverse map (plain digit -> Unicode superscript glyph) for rendering a leaked
# literal ``<sup>N</sup>`` as the proper superscript ``¹²³`` in display/body
# strings (#572). RT stores superscripted structural numbers as literal CDATA
# (``§ 1<sup>1</sup>.``); without conversion the markup leaks verbatim into
# rdfs:label / estleg:paragrahv / legalText / subsectionNumber / summary.
_DIGIT_TO_SUPERSCRIPT: dict[str, str] = {v: k for k, v in _SUPERSCRIPT_DIGIT_MAP.items()}
_SUP_TAG_RE = re.compile(r"<sup>\s*(\d+)\s*</sup>")


def _sup_to_unicode(text: str) -> str:
    """Convert literal ``<sup>N</sup>`` markup to Unicode superscript digits (#572).

    Any residual bare ``<sup>``/``</sup>`` tags (non-digit content) are stripped
    so no HTML markup survives into the citable strings.
    """
    if not text or "<sup>" not in text:
        return text
    converted = _SUP_TAG_RE.sub(
        lambda m: "".join(_DIGIT_TO_SUPERSCRIPT.get(d, d) for d in m.group(1)), text
    )
    return converted.replace("<sup>", "").replace("</sup>", "")


def _superscript_index_from(el: ET.Element, number_tag: str) -> str:
    """Extract the superscript index carried by ``el``'s ``number_tag`` child.

    Riigi Teataja records superscripted structural numbers in two forms:
      * ``ylaIndeks="N"`` attribute on the number element (``paragrahvNr``,
        ``peatykkNr``, ``jaguNr``; canonical, machine-friendly);
      * the ``kuvatavNr`` CDATA, either as a Unicode superscript glyph or
        as ``<sup>N</sup>`` markup.

    Returns the index as a plain digit string ("1", "2", ...) or ``""``
    when no superscript is present. Generalises the original
    paragraph-only extractor so chapters/divisions (``6¹``) get the same
    treatment as paragraphs (``§ 22¹``) — #253.
    """
    for c in el:
        if ln(c.tag) == number_tag:
            ya = c.attrib.get("ylaIndeks")
            if ya and ya.strip():
                return ya.strip()
            break

    kuv = None
    for c in el:
        if ln(c.tag) == "kuvatavNr":
            kuv = "".join(c.itertext()) or ""
            break
    if not kuv:
        return ""

    m = re.search(
        r"\d+\s*(?:<sup>\s*(\d+)\s*</sup>|([¹²³⁰⁴-⁹]+))",
        kuv,
    )
    if not m:
        return ""
    if m.group(1):
        return m.group(1).strip()
    if m.group(2):
        return "".join(_SUPERSCRIPT_DIGIT_MAP.get(ch, "") for ch in m.group(2))
    return ""


def _superscript_index(par_el: ET.Element) -> str:
    """Extract the superscript index for a paragrahv element.

    Thin wrapper over :func:`_superscript_index_from` for the
    ``paragrahvNr`` number element. Returns the index as a plain digit
    string ("1", "2", ...) or ``""`` when no superscript is present.
    """
    return _superscript_index_from(par_el, "paragrahvNr")


def _structural_id_suffix(el: ET.Element, number: str, number_tag: str) -> str:
    """Return the IRI suffix for a structural element (chapter/division).

    Combines the structural ``number`` (already extracted via ``ct``) with
    any superscript index carried by ``el``'s ``number_tag`` child so that
    ``6`` and ``6¹`` produce distinct suffixes ``6`` vs ``6_1`` — mirroring
    :func:`_paragraph_id_suffix` for paragraphs (``Par_22`` vs ``Par_22_1``).
    This replaces the old ``sanitize_id(number)`` suffix that collapsed
    superscript chapters onto their base sibling (#253).
    """
    base = sanitize_id(number) if number else "Unknown"
    sup = _superscript_index_from(el, number_tag)
    if sup:
        return f"{base}_{sanitize_id(sup)}"
    return base


def _chapter_id_suffix(ch_el: ET.Element, ch_nr: str, ch_title: str) -> str:
    """Return the IRI suffix for a ``peatykk`` (chapter) element.

    Uses ``peatykkNr`` plus any ``ylaIndeks`` superscript (``6¹`` →
    ``6_1``); falls back to the chapter title (truncated) only when the
    chapter has no number, preserving the previous behaviour for
    title-only chapters (#253).
    """
    if ch_nr:
        return _structural_id_suffix(ch_el, ch_nr, "peatykkNr")
    # Issue #354: the 20-char title truncation can leave a trailing ``_``
    # (``…KASUTAMINE_``) that near-collides with a real sibling suffix.
    return sanitize_id(ch_title[:20]).rstrip("_") or "Unknown"


def _division_id_suffix(jagu_el: ET.Element, j_nr: str, j_title: str) -> str:
    """Return the IRI suffix for a ``jagu`` (division) element.

    Uses ``jaguNr`` plus any ``ylaIndeks`` superscript; falls back to the
    title (truncated) when the division has no number (#253).
    """
    if j_nr:
        return _structural_id_suffix(jagu_el, j_nr, "jaguNr")
    # Issue #354: strip a trailing ``_`` left by the 20-char title cut so a
    # title-only division does not produce ``…KASUTAMINE_`` colliding with a
    # ``…KASUTAMINE`` sibling.
    return sanitize_id(j_title[:20]).rstrip("_") or "Unknown"


def _paragraph_id_suffix(par_el: ET.Element) -> str:
    """Return the canonical IRI suffix for a paragrahv element.

    Combines ``paragrahvNr`` with any superscript index so that
    duplicate paragraph numbers (``§ 22`` and ``§ 22¹``) get distinct,
    order-independent IRIs (``Par_22`` vs ``Par_22_1``). This replaces
    the previous insertion-order disambiguation (#156, #165 fix 2).
    """
    par_nr = ct(par_el, "paragrahvNr") or ""
    sup = _superscript_index(par_el)
    base = sanitize_id(par_nr) if par_nr else "Unknown"
    if sup:
        return f"{base}_{sanitize_id(sup)}"
    return base


def _dedupe_paragraph_suffix(raw_suffix: str, counts: Counter[str]) -> str:
    counts[raw_suffix] += 1
    if counts[raw_suffix] == 1:
        return raw_suffix
    return f"{raw_suffix}_x{counts[raw_suffix]}"


def _dedupe_subsection_suffix(raw_suffix: str, counts: Counter[str]) -> str:
    counts[raw_suffix] += 1
    if raw_suffix == "Unknown":
        return f"Unknown_{counts[raw_suffix]}"
    if counts[raw_suffix] == 1:
        return raw_suffix
    return f"{raw_suffix}_x{counts[raw_suffix]}"


_DUPN_TAIL_RE = re.compile(r"_Dup\d+$")


def content_derived_dup_suffix(label: str) -> str:
    """Stable collision suffix from a heading / §-range (#448).

    Prefer ``§min–max`` → ``min_max``; otherwise an 8-char SHA-1 of the
    label. Never contains the substring ``_Dup``.
    """
    text = (label or "").strip()
    match = re.search(r"§\s*(\d+)\s*[–\-]\s*(\d+)", text)
    if match:
        return f"{match.group(1)}_{match.group(2)}"
    if text:
        return hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]
    return "x"


def remint_dupn_id(old_id: str, label: str, taken: set[str]) -> str:
    """Replace a ``_DupN`` tail with a content-derived suffix (#448)."""
    base = _DUPN_TAIL_RE.sub("", old_id)
    suffix = content_derived_dup_suffix(label)
    candidate = f"{base}_{suffix}"
    n = 2
    while candidate in taken or candidate == old_id:
        candidate = f"{base}_{suffix}_{n}"
        n += 1
    return candidate


def remint_dupn_ids(doc: dict) -> dict[str, str]:
    """Rewrite ``_DupN`` @ids in a JSON-LD doc. Returns old→new remap."""
    from estleg.estleg_common import jsonld_text

    graph = doc.get("@graph", [])
    taken = {
        n.get("@id")
        for n in graph
        if isinstance(n, dict) and isinstance(n.get("@id"), str)
    }
    remap: dict[str, str] = {}
    for node in graph:
        if not isinstance(node, dict):
            continue
        nid = node.get("@id")
        if not isinstance(nid, str) or not _DUPN_TAIL_RE.search(nid):
            continue
        label = jsonld_text(node.get("rdfs:label") or node.get("skos:prefLabel") or "")
        new_id = remint_dupn_id(nid, label, taken)
        remap[nid] = new_id
        taken.add(new_id)
        taken.discard(nid)
    if not remap:
        return {}
    for node in graph:
        if isinstance(node, dict) and node.get("@id") in remap:
            node["@id"] = remap[node["@id"]]
    _rewrite_json_ids(doc, remap)
    return remap


def _rewrite_json_ids(value: object, remap: dict[str, str]) -> None:
    """Replace remapped IRIs in ``@id`` objects and string values."""
    if isinstance(value, dict):
        iri = value.get("@id")
        if isinstance(iri, str) and iri in remap:
            value["@id"] = remap[iri]
        for item in value.values():
            _rewrite_json_ids(item, remap)
    elif isinstance(value, list):
        for item in value:
            _rewrite_json_ids(item, remap)
    elif isinstance(value, str):
        pass


def _dedupe_structural_id(
    base_id: str, counts: Counter[str], *, content: str = ""
) -> str:
    """Return a stable, file-local unique structural node IRI.

    RT occasionally repeats chapter or division numbers inside a law. Keep
    the first IRI unchanged. Later collisions get a content-derived
    suffix (#448) instead of ``_DupN``.
    """
    counts[base_id] += 1
    if counts[base_id] == 1:
        return base_id
    suffix = content_derived_dup_suffix(content or base_id)
    candidate = f"{base_id}_{suffix}"
    extra = 2
    while candidate in counts:
        candidate = f"{base_id}_{suffix}_{extra}"
        extra += 1
    counts[candidate] += 1
    return candidate


def _walk_paragraphs_direct(
    container: ET.Element,
    *,
    block_tags: tuple[str, ...] = ("peatykk", "jagu", "osa"),
) -> list[ET.Element]:
    """Return paragrahv elements that belong DIRECTLY to ``container``.

    Recursively descends through wrapper elements but stops as soon as
    it encounters a deeper structural block — paragraphs inside those
    nested blocks belong to the inner block, not to ``container``.
    Replaces the ``ch.iter()`` walk that mis-attributed paragraphs in
    osa → peatykk → jagu nesting (#166).
    """
    out: list[ET.Element] = []
    for child in container:
        tag = ln(child.tag)
        if tag == "paragrahv":
            out.append(child)
        elif tag in block_tags:
            continue
        else:
            out.extend(_walk_paragraphs_direct(child, block_tags=block_tags))
    return out


def _loige_body_text(loige_el: ET.Element) -> str:
    """Return the joined text of one ``loige`` subtree.

    Concatenates the ``lauseOsa``/``lause``/``tavatekst`` fragments below
    the lõige, normalising whitespace and dropping fragments of length
    <= 3 (the same filter ``collect_full_text`` applies). The lõige's own
    ``(N)`` marker is *not* prepended here — callers add it when they want
    the marker (the provision-level ``estleg:legalText`` does;
    ``estleg:legalText`` on a Subsection node also does, mirroring the
    provision-level form).
    """
    body_parts: list[str] = []
    for grandchild in _iter_text_nodes(loige_el):
        tag = ln(grandchild.tag)
        if tag in ("lauseOsa", "lause", "tavatekst"):
            txt = _marker_pruned_text(grandchild).strip()
            txt = re.sub(r"\s+", " ", txt)
            if txt and len(txt) > 3:
                body_parts.append(txt)
    return _sup_to_unicode(" ".join(body_parts).strip())


def _superscript_from_text(value: str) -> str:
    """Extract a trailing superscript index from a number-bearing string.

    Mirrors the ``kuvatavNr`` branch of ``_superscript_index`` but works
    on a bare string (a ``loige`` ``kuvatavNr`` like ``(2)`` or ``(2¹)``).
    Machine ``loigeNr`` superscripts are carried in the ``ylaIndeks``
    attribute, so this fallback only matters for the display form.
    Returns the index as a plain digit string ("1", "2", ...) or ``""``.
    """
    if not value:
        return ""
    m = re.search(
        r"\d+\s*(?:<sup>\s*(\d+)\s*</sup>|([¹²³⁰⁴-⁹]+))",
        value,
    )
    if not m:
        return ""
    if m.group(1):
        return m.group(1).strip()
    if m.group(2):
        return "".join(_SUPERSCRIPT_DIGIT_MAP.get(ch, "") for ch in m.group(2))
    return ""


def _loige_numbers(loige_el: ET.Element) -> tuple[str, str]:
    """Return ``(display_number, id_suffix)`` for one ``loige`` element.

    The *display number* is the human-facing lõige label — preferring the
    ``kuvatavNr`` CDATA (stripped of its parentheses; e.g. ``(2¹)`` →
    ``2¹``) and falling back to ``loigeNr``. The *id suffix* is the
    canonical, order-independent IRI fragment: the base ``loigeNr`` digits
    plus any superscript index (``ylaIndeks`` on the ``loigeNr`` element,
    or a Unicode/``<sup>`` superscript glyph in ``kuvatavNr``), joined
    with an underscore — ``§ 14 lg 2¹`` → ``2_1``. This mirrors
    ``_paragraph_id_suffix`` for paragrahv elements.
    """
    loige_nr = ""
    ya = ""
    for sub in loige_el:
        if ln(sub.tag) == "loigeNr":
            if sub.text:
                loige_nr = sub.text.strip()
            ya = (sub.attrib.get("ylaIndeks") or "").strip()
            break

    kuv_raw = ""
    for sub in loige_el:
        if ln(sub.tag) == "kuvatavNr":
            kuv_raw = "".join(sub.itertext()).strip()
            break
    kuv_inner = re.sub(r"[()]", "", kuv_raw).strip() if kuv_raw else ""

    # Display: kuvatavNr (without parens) wins, else loigeNr, else "".
    # #572: render leaked literal <sup>N</sup> as Unicode superscript.
    display = _sup_to_unicode(kuv_inner or loige_nr)

    # ID suffix base + superscript.
    base = loige_nr
    if not base:
        # No machine loigeNr — fall back to the digits in the display form.
        m = re.match(r"\s*(\d+)", kuv_inner)
        base = m.group(1) if m else kuv_inner
    sup = ya
    if not sup and kuv_raw:
        sup = _superscript_from_text(kuv_raw)
    # sanitize_id keeps [0-9A-Za-z_] only, so a base like "2" stays "2"
    # and any stray punctuation is dropped before it becomes an IRI fragment.
    base_clean = sanitize_id(base) if base else "Unknown"
    if sup:
        return display, f"{base_clean}_{sanitize_id(sup)}"
    return display, base_clean


def _iter_loiked(el: ET.Element) -> list[ET.Element]:
    """Return the direct ``loige`` children of a paragrahv element, in
    document order."""
    return [child for child in el if ln(child.tag) == "loige"]


def collect_full_text(el: ET.Element) -> str:
    """Return the complete text of a provision, preserving lõige boundaries.

    Each ``loige`` is prefixed with its own marker (``(N) ...``) drawn
    from ``loigeNr`` (or ``kuvatavNr`` when ``loigeNr`` is missing).
    When no lõige structure exists, fall back to concatenating the raw
    text fragments (``lauseOsa``/``lause``/``tavatekst``) directly so
    single-paragraph laws still emit useful text.
    """
    lõige_blocks: list[str] = []
    for child in _iter_loiked(el):
        loige_nr, _suffix = _loige_numbers(child)
        body = _loige_body_text(child)
        if not body:
            continue
        if loige_nr:
            lõige_blocks.append(f"({loige_nr}) {body}")
        else:
            lõige_blocks.append(body)

    if lõige_blocks:
        return _sup_to_unicode(" ".join(lõige_blocks))

    parts: list[str] = []
    for child in _iter_text_nodes(el):
        tag = ln(child.tag)
        if tag in ("lauseOsa", "lause", "tavatekst"):
            txt = _marker_pruned_text(child).strip()
            txt = re.sub(r"\s+", " ", txt)
            if txt and len(txt) > 3:
                parts.append(txt)
    return _sup_to_unicode(" ".join(parts))


def build_subsections(
    par_el: ET.Element,
    provision_id: str,
    *,
    abbrev_prefix: str,
    par_suffix: str,
    paragraph_display: str,
    osa_nr: str | None = None,
    seen_ids: set[str] | None = None,
) -> list[dict]:
    """Build ``estleg:Subsection`` nodes for one paragrahv's ``loige`` children.

    Each lõige becomes one ``estleg:Subsection`` named individual:
      * ``@id``: ``estleg:<PREFIX>[_Osa<N>]_Par_<parSuffix>_Lg_<loigeSuffix>``
        (superscript ``§ 14 lg 2¹`` → ``..._Par_14_Lg_2_1``);
      * ``@type``: ``["estleg:Subsection", "owl:NamedIndividual"]``;
      * ``estleg:subsectionNumber``: the display lõige number as a string;
      * ``estleg:legalText``: the lõige's own text, prefixed with its
        ``(N)`` marker (mirroring the provision-level ``estleg:legalText``);
      * ``estleg:parentProvision``: ``{"@id": provision_id}``;
      * ``rdfs:label``: e.g. ``"§ 14 lg 2"``.

    Subsections whose lõige carries no usable text are skipped (an empty
    lõige is a structural artefact, not a citable unit, and SHACL requires
    ``estleg:legalText``). Returns the nodes in document order. Repeated
    source suffixes are disambiguated with stable ``_DupN`` suffixes;
    ``seen_ids``, when supplied, accumulates the emitted Subsection IRIs
    and still catches impossible post-disambiguation collisions.
    """
    if seen_ids is None:
        seen_ids = set()
    # Paragraph display strings from kuvatavNr carry a trailing period and
    # whitespace ("§ 14. "); strip it so the Subsection label reads
    # "§ 14 lg 2" rather than "§ 14.  lg 2".
    par_label_base = None
    if paragraph_display:
        cleaned = re.sub(r"\s+", " ", paragraph_display).strip().rstrip(".").strip()
        par_label_base = cleaned or None
    subsection_nodes: list[dict] = []
    suffix_counts: Counter[str] = Counter()
    for index, loige_el in enumerate(_iter_loiked(par_el), start=1):
        display_nr, suffix = _loige_numbers(loige_el)
        body = _loige_body_text(loige_el)
        if not body:
            # Empty lõige (e.g. a repealed-marker placeholder) — no
            # citable text, so no Subsection node.
            continue
        # #514: an unnumbered lõige is lõige <sibling-index>, not Lg_Unknown.
        if suffix == "Unknown":
            suffix = str(index)
            display_nr = display_nr or suffix
        suffix = _dedupe_subsection_suffix(suffix, suffix_counts)
        osa_segment = f"_Osa{osa_nr}" if osa_nr else ""
        sub_id = f"estleg:{abbrev_prefix}{osa_segment}_Par_{par_suffix}_Lg_{suffix}"
        if sub_id in seen_ids:
            raise ValueError(
                f"Duplicate subsection IRI {sub_id!r} produced for prefix "
                f"{abbrev_prefix!r} after duplicate suffix disambiguation."
            )
        seen_ids.add(sub_id)

        legal_text = f"({display_nr}) {body}" if display_nr else body

        # estleg:legalText / estleg:subsectionNumber are emitted as plain
        # (xsd:string) literals, matching estleg:SubsectionShape's
        # sh:datatype xsd:string and the existing estleg:legalText data in
        # the corpus (the generate_missing_parts.py outputs).
        node: dict = {
            "@id": sub_id,
            "@type": ["estleg:Subsection", "owl:NamedIndividual"],
            "estleg:subsectionNumber": display_nr or suffix,
            "estleg:legalText": legal_text,
            "estleg:parentProvision": {"@id": provision_id},
        }
        if par_label_base and display_nr:
            node["rdfs:label"] = f"{par_label_base} lg {display_nr}"
        items = _loige_item_numbers(loige_el, body)
        if items:
            node["estleg:itemNumber"] = items if len(items) > 1 else items[0]
        subsection_nodes.append(node)
    return subsection_nodes


_PUNKT_NR_RE = re.compile(
    r"\bp(?:unkt(?:is|i|id)?|)\s+(\d+)\b",
    re.IGNORECASE,
)
_UNKNOWN_LG_IRI_RE = re.compile(r"^(estleg:.+_Lg_)Unknown_(\d+)$")


def _loige_item_numbers(loige_el: ET.Element, body: str) -> list[str]:
    """Punkt numbers from RT ``punktNr`` children, else from the lõige text (#514)."""
    nums: list[str] = []
    for child in loige_el.iter():
        if ln(child.tag) == "punktNr" and child.text and child.text.strip().isdigit():
            nums.append(child.text.strip())
    if not nums:
        nums = _PUNKT_NR_RE.findall(body or "")
    return list(dict.fromkeys(nums))


def rewrite_unknown_lg_iris(graph: list) -> int:
    """Rewrite ``_Lg_Unknown_N`` IRIs to ``_Lg_N`` when the target is free (#514)."""
    existing = {
        node.get("@id")
        for node in graph
        if isinstance(node, dict) and isinstance(node.get("@id"), str)
    }
    mapping: dict[str, str] = {}
    for node in graph:
        if not isinstance(node, dict):
            continue
        nid = node.get("@id")
        if not isinstance(nid, str):
            continue
        match = _UNKNOWN_LG_IRI_RE.match(nid)
        if not match:
            continue
        new_id = f"{match.group(1)}{match.group(2)}"
        if new_id in existing:
            alt = f"{new_id}_x2"
            if alt in existing:
                continue
            new_id = alt
        mapping[nid] = new_id
        existing.add(new_id)
    if not mapping:
        return 0

    def _rewrite(value: object) -> object:
        if isinstance(value, str):
            return mapping.get(value, value)
        if isinstance(value, dict):
            return {key: _rewrite(item) for key, item in value.items()}
        if isinstance(value, list):
            return [_rewrite(item) for item in value]
        return value

    for index, node in enumerate(graph):
        graph[index] = _rewrite(node)
    return len(mapping)


def stamp_item_numbers_from_text(node: dict) -> bool:
    """Add ``estleg:itemNumber`` from lõige ``legalText`` when absent (#514)."""
    types = node.get("@type") or []
    if isinstance(types, str):
        types = [types]
    if "estleg:Subsection" not in types:
        return False
    if node.get("estleg:itemNumber"):
        return False
    text = node.get("estleg:legalText") or ""
    if not isinstance(text, str):
        return False
    items = list(dict.fromkeys(_PUNKT_NR_RE.findall(text)))
    if not items:
        return False
    node["estleg:itemNumber"] = items if len(items) > 1 else items[0]
    return True


def _iter_peatykks(
    parent: ET.Element,
    *,
    descend_osa: bool,
    osa_nr: str | None = None,
) -> list[tuple[ET.Element, str | None]]:
    """Yield (peatykk, enclosing_osa_nr) without double-counting nested chapters."""
    out: list[tuple[ET.Element, str | None]] = []
    for child in parent:
        tag = ln(child.tag)
        if tag == "peatykk":
            out.append((child, osa_nr))
        elif tag == "osa":
            if descend_osa:
                out.extend(
                    _iter_peatykks(
                        child,
                        descend_osa=True,
                        osa_nr=ct(child, "osaNr") or osa_nr,
                    )
                )
        else:
            out.extend(
                _iter_peatykks(child, descend_osa=descend_osa, osa_nr=osa_nr)
            )
    return out


def emit_hierarchy_and_provisions(
    *,
    graph: list[dict],
    chapter_root: ET.Element,
    paragrahvid: list[ET.Element],
    prefix: str,
    ontology_id: str,
    class_id: str = "estleg:LegalProvision",
    title: str,
    slug: str,
    par_min: object,
    par_max: object,
    par_numbers: list[int],
    scheme_id: str,
    descend_osa: bool,
    osa_nr: str | None = None,
    part_concept_id: str | None = None,
    cluster_has_broader: bool = False,
    fallback_label: str,
    provision_iri_prefix: str,
    structural_ns: str,
    part_label: str | None = None,
    subsection_builder=None,
) -> None:
    """Emit chapters, divisions, clusters, provisions, and subsections (#466).

    ``provision_iri_prefix`` is the IRI stem before the paragraph suffix
    (``estleg:ABBR_Par_`` or ``estleg:ABBR_Osa2_Par_``).
    ``structural_ns`` is the chapter/cluster infix (``ABBR`` or ``ABBR_2``).
    """
    par_to_container: dict[int, str] = {}
    par_to_cluster: dict[int, str] = {}
    clusters: list[dict] = []
    structural_id_counts: Counter[str] = Counter()
    chapters_with_divisions: list[tuple[dict, str]] = []

    for ch, ch_osa_nr in _iter_peatykks(
        chapter_root, descend_osa=descend_osa, osa_nr=osa_nr
    ):
        ch_nr = ct(ch, "peatykkNr") or ""
        ch_title = ct(ch, "peatykkPealkiri") or ""
        if not ch_title:
            continue
        ch_pars = _walk_paragraphs_direct(ch)
        ch_par_nrs: list[int] = []
        for paragraph in ch_pars:
            nr = ct(paragraph, "paragrahvNr")
            if nr:
                try:
                    ch_par_nrs.append(int(re.sub(r"[^\d]", "", nr)))
                except ValueError:
                    pass
        osa_segment = ""
        if descend_osa and ch_osa_nr:
            osa_segment = f"Osa{sanitize_id(ch_osa_nr)}_"
        structural_suffix = (
            f"{osa_segment}{_chapter_id_suffix(ch, ch_nr, ch_title)}"
        )
        par_range = (
            f"§{min(ch_par_nrs)}–{max(ch_par_nrs)}" if ch_par_nrs else ""
        )
        cluster_id = _dedupe_structural_id(
            f"estleg:Cluster_{structural_ns}_{structural_suffix}",
            structural_id_counts,
            content=f"{par_range} {ch_title}",
        )
        chapter_id = _dedupe_structural_id(
            f"estleg:Chapter_{structural_ns}_{structural_suffix}",
            structural_id_counts,
            content=f"{par_range} {ch_title}",
        )
        clusters.append(
            {"id": cluster_id, "label": f"{par_range} {ch_title}".strip(), "count": 0}
        )
        cluster_node: dict = {
            "@id": cluster_id,
            "@type": ["owl:NamedIndividual", "estleg:TopicCluster", "skos:Concept"],
            "rdfs:label": f"{par_range} {ch_title}".strip(),
            "skos:prefLabel": f"{par_range} {ch_title}".strip(),
            "skos:inScheme": {"@id": scheme_id},
        }
        if cluster_has_broader and part_concept_id:
            cluster_node["skos:broader"] = {"@id": part_concept_id}
        graph.append(cluster_node)
        chapter_node: dict = {
            "@id": chapter_id,
            "@type": ["owl:NamedIndividual", "estleg:Chapter"],
            "rdfs:label": f"{ch_nr}. peatükk – {ch_title}".strip(" –"),
            "estleg:chapterNumber": ch_nr,
            "estleg:partOfAct": {"@id": ontology_id},
            "dcterms:subject": chapter_cluster_subject(cluster_id),
        }
        for paragraph in ch_pars:
            par_to_cluster[id(paragraph)] = cluster_id
            par_to_container[id(paragraph)] = chapter_id
        division_ids: list[str] = []
        for jagu_el in [child for child in ch if ln(child.tag) == "jagu"]:
            j_nr = ct(jagu_el, "jaguNr") or ""
            j_title = ct(jagu_el, "jaguPealkiri") or ""
            if j_title or j_nr:
                div_id = _dedupe_structural_id(
                    f"estleg:Division_{structural_ns}_{structural_suffix}_"
                    f"{_division_id_suffix(jagu_el, j_nr, j_title)}",
                    structural_id_counts,
                    content=f"{j_nr} {j_title}",
                )
                division_ids.append(div_id)
                graph.append(
                    {
                        "@id": div_id,
                        "@type": ["owl:NamedIndividual", "estleg:Division"],
                        "rdfs:label": f"{j_nr}. jagu – {j_title}".strip(" –"),
                        "estleg:isPartOf": {"@id": chapter_id},
                    }
                )
                for jp in _walk_paragraphs_direct(jagu_el):
                    par_to_cluster[id(jp)] = cluster_id
                    par_to_container[id(jp)] = div_id
            else:
                for jp in _walk_paragraphs_direct(jagu_el):
                    par_to_cluster[id(jp)] = cluster_id
                    par_to_container[id(jp)] = chapter_id
        if division_ids:
            chapter_node["estleg:hasPart"] = [{"@id": d} for d in division_ids]
            chapters_with_divisions.append((chapter_node, chapter_id))
        graph.append(chapter_node)

    if clusters:
        preamble_pars = [p for p in paragrahvid if id(p) not in par_to_container]
        if preamble_pars:
            preamble_label = "Sissejuhatavad sätted"
            preamble_ns = (
                f"{structural_ns}_Preamble"
                if osa_nr is None
                else f"{structural_ns}_Preamble"
            )
            preamble_cluster_id = _dedupe_structural_id(
                f"estleg:Cluster_{preamble_ns}",
                structural_id_counts,
                content=preamble_label,
            )
            preamble_chapter_id = _dedupe_structural_id(
                f"estleg:Chapter_{preamble_ns}",
                structural_id_counts,
                content=preamble_label,
            )
            clusters.append(
                {"id": preamble_cluster_id, "label": preamble_label, "count": 0}
            )
            preamble_cluster: dict = {
                "@id": preamble_cluster_id,
                "@type": ["owl:NamedIndividual", "estleg:TopicCluster", "skos:Concept"],
                "rdfs:label": preamble_label,
                "skos:prefLabel": preamble_label,
                "skos:inScheme": {"@id": scheme_id},
            }
            if cluster_has_broader and part_concept_id:
                preamble_cluster["skos:broader"] = {"@id": part_concept_id}
            graph.append(preamble_cluster)
            graph.append(
                {
                    "@id": preamble_chapter_id,
                    "@type": ["owl:NamedIndividual", "estleg:Chapter"],
                    "rdfs:label": preamble_label,
                    "estleg:partOfAct": {"@id": ontology_id},
                    "dcterms:subject": chapter_cluster_subject(preamble_cluster_id),
                }
            )
            for paragraph in preamble_pars:
                par_to_container[id(paragraph)] = preamble_chapter_id
                par_to_cluster[id(paragraph)] = preamble_cluster_id

    if not clusters and paragrahvid:
        fallback_cluster_id = (
            f"estleg:Cluster_{structural_ns}_default"
        )
        par_range = f"§{par_min}–{par_max}" if par_numbers else ""
        clusters.append(
            {
                "id": fallback_cluster_id,
                "label": f"{par_range} {fallback_label}".strip(),
                "count": 0,
            }
        )
        for paragraph in paragrahvid:
            par_to_cluster[id(paragraph)] = fallback_cluster_id
        graph.append(
            {
                "@id": fallback_cluster_id,
                "@type": ["owl:NamedIndividual", "estleg:TopicCluster", "skos:Concept"],
                "rdfs:label": f"{par_range} {fallback_label}".strip(),
                "skos:prefLabel": f"{par_range} {fallback_label}".strip(),
                "skos:inScheme": {"@id": scheme_id},
            }
        )

    cluster_counts: Counter[str] = Counter(par_to_cluster.values())
    for cluster in clusters:
        count = cluster_counts.get(cluster["id"], 0)
        for node in graph:
            if node.get("@id") == cluster["id"]:
                node["estleg:provisionCount"] = count
                break

    if clusters:
        if cluster_has_broader and part_concept_id:
            part_label = part_label or (f"Osa {osa_nr}" if osa_nr else title)
            has_chapter_clusters = any(
                isinstance(node.get("skos:broader"), dict) for node in graph
            )
            if has_chapter_clusters:
                graph.insert(
                    2,
                    {
                        "@id": part_concept_id,
                        "@type": ["owl:NamedIndividual", "skos:Concept"],
                        "rdfs:label": part_label,
                        "skos:prefLabel": part_label,
                        "skos:inScheme": {"@id": scheme_id},
                        "skos:narrower": [{"@id": cl["id"]} for cl in clusters],
                    },
                )
                top_concepts = [{"@id": part_concept_id}]
            else:
                top_concepts = [{"@id": cl["id"]} for cl in clusters]
            scheme_label = f"{title} {part_label} teemaskeem"
        else:
            top_concepts = [{"@id": cl["id"]} for cl in clusters]
            scheme_label = f"{title} teemaskeem"
        graph.insert(
            2,
            {
                "@id": scheme_id,
                "@type": ["skos:ConceptScheme"],
                "rdfs:label": scheme_label,
                "skos:hasTopConcept": top_concepts,
            },
        )

    seen_ids: set[str] = set()
    paragraph_suffix_counts: Counter[str] = Counter()
    par_iri_by_elem: dict[int, str] = {}
    seen_subsection_ids: set[str] = set()
    for paragraph in paragrahvid:
        p_nr = ct(paragraph, "paragrahvNr") or "?"
        p_title = ct(paragraph, "paragrahvPealkiri") or ""
        p_display = _sup_to_unicode(ct(paragraph, "kuvatavNr")) or f"§ {p_nr}"
        text = collect_text(paragraph)
        full_text = collect_full_text(paragraph)
        raw_par_suffix = _paragraph_id_suffix(paragraph)
        par_suffix = _dedupe_paragraph_suffix(raw_par_suffix, paragraph_suffix_counts)
        p_id = f"{provision_iri_prefix}{par_suffix}"
        if p_id in seen_ids:
            raise ValueError(
                f"Duplicate paragraph IRI {p_id!r} produced for prefix "
                f"{prefix!r} after duplicate suffix disambiguation."
            )
        seen_ids.add(p_id)
        par_iri_by_elem[id(paragraph)] = p_id
        cluster_ref = par_to_cluster.get(id(paragraph))
        if p_title:
            label = f"{p_display} {p_title}"
        elif text:
            excerpt = text[:80].rstrip()
            if len(text) > 80:
                excerpt = excerpt + "..."
            label = f"{p_display} [{excerpt}]"
        else:
            label = p_display
        node = {
            "@id": p_id,
            "@type": ["owl:NamedIndividual", class_id],
            "estleg:paragrahv": p_display,
            "rdfs:label": label,
            "estleg:sourceAct": title,
            "estleg:partOfAct": {"@id": ontology_id},
            "estleg:summary": provision_summary(text, p_title, p_display),
        }
        if full_text:
            node["estleg:legalText"] = full_text
        if cluster_ref:
            node["estleg:requestedCluster"] = {"@id": cluster_ref}
        container_ref = par_to_container.get(id(paragraph))
        if container_ref:
            node["estleg:isPartOf"] = {"@id": container_ref}
        builder = subsection_builder or build_subsections
        subsection_nodes = builder(
            paragraph,
            p_id,
            abbrev_prefix=prefix,
            par_suffix=par_suffix,
            paragraph_display=p_display,
            osa_nr=str(osa_nr) if osa_nr is not None else None,
            seen_ids=seen_subsection_ids,
        )
        if subsection_nodes:
            node["estleg:hasSubsection"] = [
                {"@id": item["@id"]} for item in subsection_nodes
            ]
        graph.append(node)
        graph.extend(subsection_nodes)

    for chapter_node, chapter_id in chapters_with_divisions:
        direct_iris = [
            par_iri_by_elem[id(paragraph)]
            for paragraph in paragrahvid
            if par_to_container.get(id(paragraph)) == chapter_id
            and id(paragraph) in par_iri_by_elem
        ]
        if direct_iris:
            chapter_node.setdefault("estleg:hasPart", [])
            chapter_node["estleg:hasPart"].extend(
                {"@id": iri} for iri in direct_iris
            )




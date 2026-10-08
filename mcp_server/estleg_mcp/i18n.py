"""Estonian-first wording for estleg-mcp (#714).

Two things live here, both keyed by the stable tool names:

* :data:`TOOL_DESCRIPTIONS_ET` -- the Estonian lead paragraph of every tool
  description. :func:`tool_description` puts it first and keeps the English
  docstring (with its field contract) underneath, so a model reading the tool
  list sees Estonian first without losing the precise English contract.
* :data:`MESSAGES` -- the human text a tool returns *inside* a result (the
  ``note`` of a miss, an overflow hint, the ``explanation`` of
  ``explain_provision``), in Estonian (``et``, the default) and English
  (``en``). Every tool takes a ``language`` argument that selects one; the
  server sets it for the duration of the call with :func:`use_language`.

Field names, enum values (``change: "amended"``), IRIs and URLs are never
translated: they are the machine contract.
"""

from __future__ import annotations

import contextvars
import inspect
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

LANGUAGES: tuple[str, ...] = ("et", "en")
DEFAULT_LANGUAGE = "et"

_LANGUAGE: contextvars.ContextVar[str] = contextvars.ContextVar(
    "estleg_language", default=DEFAULT_LANGUAGE
)


class UnsupportedLanguage(ValueError):
    """A ``language`` argument outside :data:`LANGUAGES`."""


def normalize_language(value: str | None) -> str:
    """``"et"`` / ``"en"`` from a user value (case- and region-insensitive).

    ``None`` or "" selects the default (``et``); ``"et-EE"`` / ``"EN_gb"`` are
    accepted. Anything else raises :class:`UnsupportedLanguage` rather than
    silently answering in a language the caller did not ask for.
    """
    if value is None or not str(value).strip():
        return DEFAULT_LANGUAGE
    code = str(value).strip().lower().replace("_", "-").split("-")[0]
    if code not in LANGUAGES:
        raise UnsupportedLanguage(
            f"language must be one of {', '.join(LANGUAGES)}; got {value!r}"
        )
    return code


def current_language() -> str:
    """The language selected for the running tool call (default ``et``)."""
    return _LANGUAGE.get()


@contextmanager
def use_language(language: str | None) -> Iterator[str]:
    """Select ``language`` for the enclosed block (restored afterwards)."""
    code = normalize_language(language)
    token = _LANGUAGE.set(code)
    try:
        yield code
    finally:
        _LANGUAGE.reset(token)


# ---------------------------------------------------------------------------
# Result text
# ---------------------------------------------------------------------------
MESSAGES: dict[str, dict[str, str]] = {
    "law_not_found": {
        "et": "seadust ei leitud: {query}",
        "en": "law not found: {query}",
    },
    "law_not_found_detail": {
        "et": (
            "Seadust {query!r} ei leitud. Proovi pealkirja (nt 'Karistusseadustik'), "
            "ametlikku lühendit (nt 'KarS', 'VÕS') või leia täpne nimi tööriistaga "
            "search_laws."
        ),
        "en": (
            "No law matched {query!r}. Try a title (e.g. 'Karistusseadustik'), "
            "an official abbreviation (e.g. 'KarS', 'VÕS'), or use search_laws "
            "to discover the exact name."
        ),
    },
    "bad_date": {
        "et": "{field} peab olema ISO kuupäev, nt '2015-01-01'; saadi {value!r}.",
        "en": "{field} must be an ISO date like '2015-01-01'; got {value!r}.",
    },
    "bad_window": {
        "et": "Ajavahemik on tühi: since ({since}) on hilisem kui until ({until}).",
        "en": "Empty window: since ({since}) is after until ({until}).",
    },
    "no_law_history": {
        "et": (
            "Seaduse {law} redaktsioonide ajalugu pole korpuses; jäta as_of "
            "ära, et saada kehtiva tervikteksti ülevaade."
        ),
        "en": (
            "No version history is recorded for {law}; omit as_of for the "
            "current consolidated overview."
        ),
    },
    "no_provisions_in_force": {
        "et": (
            "{date} ei kehtinud ükski seaduse {law} paragrahv; salvestatud ajalugu "
            "hõlmab perioodi {earliest}..{latest}."
        ),
        "en": (
            "No provisions of {law} were in force on {date}; recorded history "
            "spans {earliest}..{latest}."
        ),
    },
    "paragraph_not_found": {
        "et": (
            "Seaduses {law} ei leitud paragrahvi {paragraph!r}. Paragrahvide arvu "
            "näitab get_law."
        ),
        "en": (
            "No § matching {paragraph!r} found in {law}. Use get_law to see how "
            "many provisions exist."
        ),
    },
    "no_redaction_on": {
        "et": (
            "{date} ei kehtinud seaduse {law} sätte {paragraph} ühtegi redaktsiooni; "
            "salvestatud ajalugu hõlmab perioodi {earliest}..{latest}."
        ),
        "en": (
            "No redaction of {paragraph} of {law} was in force on {date}; "
            "recorded history spans {earliest}..{latest}."
        ),
    },
    "no_provision_history": {
        "et": (
            "Selle sätte redaktsioonide ajalugu seaduses {law} pole korpuses; jäta "
            "as_of ära, et lugeda kehtivat teksti."
        ),
        "en": (
            "No version history is recorded for that § of {law}; omit as_of for "
            "the current consolidated text."
        ),
    },
    "present": {"et": "praeguseni", "en": "present"},
    "that_section": {"et": "see paragrahv", "en": "that §"},
    "overflow": {
        "et": (
            "näidatakse esimesed {shown} tulemust {total}-st; suurenda `limit` "
            "väärtust või täpsusta päringut."
        ),
        "en": "showing first {shown} of {total} matches; raise `limit` or narrow the query to see more.",
    },
    "draft_not_found": {
        "et": "eelnõud ei leitud eelnõude alamkorpusest",
        "en": "draft not found in eelnoud subcorpus",
    },
    "regulation_not_found": {
        "et": (
            "Määrust {query!r} ei leitud. Proovi täpset pealkirja, korpuse slug'i "
            "või Riigi Teataja id-d või leia määrus tööriistaga regulations_for_law "
            "/ regulations_by_issuer."
        ),
        "en": (
            "No regulation matched {query!r}. Try its exact title, corpus slug, or "
            "riigiteataja id, or use regulations_for_law / regulations_by_issuer "
            "to discover one."
        ),
    },
    "no_celex": {
        "et": "Päringus {query!r} pole CELEX-numbrit.",
        "en": "No CELEX number in {query!r}.",
    },
    "curia_none": {
        "et": (
            "Ükski CURIA lahend ei maini CELEX-numbrit {celex!r}. CURIA failides "
            "pole interprets-seoseid (#418); sobitamine on CELEX-alamstringi otsing "
            "väljadel label / celexNumber / owl:sameAs / dcterms:source."
        ),
        "en": (
            "No CURIA decisions mention CELEX {celex!r}. The committed CURIA peeps "
            "have no interprets edges (#418); matching is a CELEX substring on "
            "label / celexNumber / owl:sameAs / dcterms:source."
        ),
    },
    "harm_none": {
        "et": (
            "CELEX-numbri {celex!r} harmoneerimisfaili pole "
            "(krr_outputs/harmonisation/harmonisation_by_directive/harm_{celex}.json)."
        ),
        "en": (
            "No harmonisation sidecar for CELEX {celex!r} "
            "(krr_outputs/harmonisation/harmonisation_by_directive/harm_{celex}.json)."
        ),
    },
    "directive_not_found": {
        "et": (
            "Direktiivi {celex!r} pole EUR-Lexi direktiivide failis "
            "(krr_outputs/eurlex/eurlex_directives_peep.json)."
        ),
        "en": (
            "Directive {celex!r} is not in the EUR-Lex directives peep "
            "(krr_outputs/eurlex/eurlex_directives_peep.json)."
        ),
    },
    "coverage_caveat": {
        "et": (
            "Katvuse tõdemus, mitte õiguslik järeldus (#701): korpusest ei leitud "
            "ülevõtmisseost, mis ei tähenda, et direktiiv oleks üle võtmata."
        ),
        "en": (
            "Corpus-coverage fact, not a legal finding (#701): no transposition "
            "edge was found in this corpus, which does not mean the directive is "
            "untransposed."
        ),
    },
    "provision_not_found": {
        "et": (
            "Sätet {query!r} ei leitud. Anna sätte IRI (nt "
            "'estleg:KARIST_2_Osa1_Par_13') või viide kujul 'KarS § 13'."
        ),
        "en": (
            "No provision matched {query!r}. Pass a provision IRI (e.g. "
            "'estleg:KARIST_2_Osa1_Par_13') or a reference like 'KarS § 13'."
        ),
    },
    "explain_text": {
        "et": (
            "{paragraph} ({law}). {history} Säte viitab {refs_out} sättele ja "
            "sellele viitab {refs_in} sätet; seda tõlgendab {decisions} "
            "Riigikohtu lahendit; pädevaid asutusi {authorities}; sanktsioone "
            "{sanctions}; KOV määrusi, mis seda sätet tsiteerivad, {kov}."
        ),
        "en": (
            "{paragraph} ({law}). {history} It cites {refs_out} provision(s) and "
            "is cited by {refs_in}; {decisions} Supreme Court decision(s) "
            "interpret it; competent authorities: {authorities}; sanctions: "
            "{sanctions}; municipal regulations citing it: {kov}."
        ),
    },
    "explain_history": {
        "et": (
            "Korpuses on {n} redaktsiooni alates {first}; kehtiv redaktsioon "
            "{current} alates {since}."
        ),
        "en": (
            "The corpus records {n} redaction(s) since {first}; the current "
            "redaction {current} applies from {since}."
        ),
    },
    "explain_history_none": {
        "et": "Redaktsioonide ajalugu korpuses pole.",
        "en": "No redaction history is recorded.",
    },
    "explain_history_ceased": {
        "et": (
            "Korpuses on {n} redaktsiooni alates {first}; viimane kehtis kuni "
            "{until} (säte ei kehti)."
        ),
        "en": (
            "The corpus records {n} redaction(s) since {first}; the last one "
            "applied until {until} (no longer in force)."
        ),
    },
}


def msg(key: str, **values: Any) -> str:
    """The ``key`` message in the current language, formatted with ``values``."""
    entry = MESSAGES[key]
    template = entry.get(current_language()) or entry[DEFAULT_LANGUAGE]
    return template.format(**values)


# ---------------------------------------------------------------------------
# Tool descriptions (Estonian lead paragraph per tool)
# ---------------------------------------------------------------------------
LANGUAGE_PARAM_DESCRIPTION = (
    "Vastuse tekstide keel: 'et' (vaikimisi) või 'en'. / Language of the "
    "human-readable text in the result (notes, explanations): 'et' (default) "
    "or 'en'. Field names, IRIs and URLs are never translated."
)

TOOL_DESCRIPTIONS_ET: dict[str, str] = {
    "search_laws": (
        "Otsi Eesti seadusi pealkirja, ametliku lühendi või slug'i järgi. Kasuta "
        "esimesena, kui seaduse täpne nimi pole teada; otsing ei arvesta "
        "täppe ja laiendab EuroVoci valdkonnanimesid. Iga tulemus sisaldab Riigi "
        "Teataja URL-i (või tühja stringi). Näide: \"Millised seadused käsitlevad "
        "töölepingut?\""
    ),
    "get_law": (
        "Ühe seaduse ülevaade: Riigi Teataja URL, kehtivus, EuroVoci teemad, "
        "paragrahvide ja peatükkide arv; as_of (ISO kuupäev) lisab tolle päeva "
        "seisu. Näide: \"Anna ülevaade karistusseadustikust.\""
    ),
    "get_provision": (
        "Loe seaduse üht paragrahvi -- kehtivat teksti või as_of kuupäeval "
        "kehtinud redaktsiooni. Pikk tekst kärbitakse (truncated / full_length "
        "näitavad seda); full_text=True tagastab kogu teksti. Näide: \"Mida "
        "sätestas KarS § 13 2010-06-15?\""
    ),
    "who_references": (
        "Kes viitab seadusele või paragrahvile (sissetulevad viited = mõju). "
        "Näide: \"Millised sätted viitavad KarS §-le 60?\""
    ),
    "references_of": (
        "Millele seadus või paragrahv viitab (väljaminevad viited). Näide: "
        "\"Millele viitab KarS § 13?\""
    ),
    "drafts_affecting_law": (
        "Menetluses olevad eelnõud, mis muudaksid seadust; iga rida viitab "
        "eelnõude infosüsteemi (EIS). Näide: \"Millised eelnõud puudutavad "
        "tervishoiuteenuste korraldamise seadust?\""
    ),
    "court_decisions_for_law": (
        "Riigikohtu lahendid, mis tõlgendavad seaduse sätteid; iga rida viitab "
        "riigikohus.ee-le. Näide: \"Millised Riigikohtu lahendid tõlgendavad "
        "KarS-i?\""
    ),
    "sanctions_for_law": (
        "Seaduses sätestatud karistused ja sanktsioonid koos neid kehtestava "
        "paragrahviga. Näide: \"Milliseid karistusi näeb ette KarS?\""
    ),
    "competent_authority_for_law": (
        "Millised riigiasutused seadust rakendavad või järelevalvet teevad, ja "
        "mitmes sättes. Näide: \"Kes teeb järelevalvet isikuandmete kaitse "
        "seaduse üle?\""
    ),
    "transposition": (
        "EL direktiiv ↔ Eesti seadus mõlemas suunas: anna CELEX-number või "
        "seaduse nimi. Näide: \"Milline Eesti seadus võtab üle direktiivi "
        "31990L0314?\""
    ),
    "provision_history": (
        "Ühe paragrahvi kõik redaktsioonid ajas, igaüks oma kehtivusaja, teksti "
        "ja Riigi Teataja redaktsiooni URL-iga. full_text=True tagastab tekstid "
        "kärpimata. Näide: \"Kuidas on KarS § 13 aja jooksul muutunud?\""
    ),
    "regulations_for_law": (
        "Seaduse alusel antud või seda rakendavad määrused (riigi ja KOV). "
        "Näide: \"Millised määrused on antud KOKS-i alusel?\""
    ),
    "get_regulation": (
        "Ühe määruse ülevaade: andja, kehtivus, volitusnorm(id) ja Riigi Teataja "
        "URL. Näide: \"Anna ülevaade määrusest t302269.\""
    ),
    "regulations_by_issuer": (
        "Asutuse antud määrused (ministeerium, valitsus, volikogu). Näide: "
        "\"Milliseid määrusi on andnud rahandusminister?\""
    ),
    "define_term": (
        "Õigusmõiste definitsioon mõistete kihist koos selle akti viitega, mis "
        "mõiste defineerib. Näide: \"Mida tähendab 'elatis'?\""
    ),
    "laws_for_subject": (
        "Seadused EuroVoci teema IRI või märksõna järgi, igaüks Riigi Teataja "
        "URL-iga. Näide: \"Millised seadused käsitlevad karistusõigust?\""
    ),
    "amendment_history": (
        "Seaduse jõustunud muudatused (mitte menetluses eelnõud) koos muutva "
        "akti viitega. Näide: \"Milliseid muudatusi on KarS-i tehtud?\""
    ),
    "eu_case_law_for_directive": (
        "CURIA lahendid, mis mainivad direktiivi CELEX-numbrit. Näide: "
        "\"Millised Euroopa Kohtu lahendid käsitlevad direktiivi 32000L0060?\""
    ),
    "harmonisation_for_directive": (
        "Teiste liikmesriikide meetmed, mis võtavad üle sama direktiivi, koos "
        "EUR-Lexi ja (Eesti puhul) Riigi Teataja viitega. Näide: \"Millised "
        "naaberriigid võtsid üle direktiivi 32000L0060?\""
    ),
    "layers_available": (
        "Milliseid korpuse kihte MCP loeb ja milliseid mitte (ning sätete "
        "tuvastamise elav kontroll). Näide: \"Kas MCP loeb harmoneerimiskihti?\""
    ),
    "what_changed": (
        "Mis muutus seaduses või paragrahvis ajavahemikus since..until: "
        "sätete redaktsioonid (muudetud, lisatud, kehtetuks muutunud) ja "
        "jõustunud muudatussündmused, igaüks Riigi Teataja viitega. Näide: "
        "\"Mis muutus KarS-is 2014. aastal?\""
    ),
    "transposition_gaps": (
        "Kehtivad EL direktiivid, mille ülevõtmisseost korpuses pole (#701 "
        "katvuslipp -- katvuse tõdemus, mitte õiguslik järeldus), või ühe "
        "direktiivi ülevõtmise seis. Näide: \"Millistel kehtivatel "
        "direktiividel pole korpuses ülevõtvat seadust?\""
    ),
    "kov_regulations_citing": (
        "KOV määrused, mis tsiteerivad seadust või paragrahvi volitusnormina "
        "(implementsCitation) või on antud selle alusel (issuedUnder). Näide: "
        "\"Millised valla- ja linnavolikogude määrused tuginevad KOKS § 22-le?\""
    ),
    "explain_provision": (
        "Selgita üht sätet ühe vastusega: tekst, redaktsioonide ajalugu, "
        "viited sisse ja välja, kohtulahendid, pädevad asutused, sanktsioonid "
        "ja seda tsiteerivad KOV määrused. Näide: \"Selgita KarS § 424.\""
    ),
}


def tool_description(name: str, english_doc: str | None) -> str:
    """Estonian lead paragraph, then the English docstring (#714).

    Tool names stay stable; only the description text is Estonian-first. A
    tool without an Estonian entry falls back to its English docstring.
    """
    english = inspect.cleandoc(english_doc or "")
    estonian = TOOL_DESCRIPTIONS_ET.get(name, "").strip()
    if not estonian:
        return english
    if not english:
        return estonian
    return f"{estonian}\n\nEnglish: {english}"


__all__ = [
    "DEFAULT_LANGUAGE",
    "LANGUAGES",
    "LANGUAGE_PARAM_DESCRIPTION",
    "MESSAGES",
    "TOOL_DESCRIPTIONS_ET",
    "UnsupportedLanguage",
    "current_language",
    "msg",
    "normalize_language",
    "tool_description",
    "use_language",
]

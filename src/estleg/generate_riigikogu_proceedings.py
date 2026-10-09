#!/usr/bin/env python3
"""Join EIS drafts to their Riigikogu proceedings and record the stages (#717).

Source: the Riigikogu open-data REST API (``https://api.riigikogu.ee``),
licensed **CC BY-SA 3.0** (https://creativecommons.org/licenses/by-sa/3.0/).
The client keeps to **1 request/s** and caches a trimmed projection of every
response under ``data/riigikogu/`` so reruns are fully offline. Pass
``--refresh`` to re-fetch the draft listing (details stay cached unless
``--refresh-details``).

For every EIS draft node in ``krr_outputs/eelnoud/*_peep.json`` this pass:

* finds the Riigikogu draft volume it became, by (in order of preference)
  the Riigikogu mark the EIS title quotes, e.g. ``(835 SE)``, within the
  Riigikogu membership in force on the EIS date (``riigikogu-mark``), the
  EIS number exactly one Riigikogu volume quotes in its "[EIS]" notice files
  (``riigikogu-eis-number``), or an exact normalised title match on a
  Government bill initiated within a year of the EIS date
  (``riigikogu-title-date``);
* records the Riigikogu mark / UUID / membership on the draft;
* appends one ``eli-dl:ProcessStep`` per Riigikogu proceeding event
  (``estleg:riigikoguStatus`` keeps the raw status code) stamped with
  ``dcterms:license`` CC BY-SA 3.0 and the join method as
  ``estleg:derivationMethod``;
* reuses Riigikogu's EuroVoc descriptors as ``dcterms:subject`` (bare
  ``http://eurovoc.europa.eu/<id>`` IRIs) with ``estleg:subjectSource
  "riigikogu"``;
* re-derives ``estleg:legislativePhase`` from the latest step and rewrites
  ``EELNOUD_INDEX.json``.

Nothing is invented: a draft without a confident join keeps its EIS steps
only. Run ``scripts/rebuild_subcorpus_combined.py --subcorpus eelnoud``
afterwards (or pass ``--rebuild-combined``).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from estleg.estleg_common import allowed_get, save_json

REPO_ROOT = Path(__file__).resolve().parents[2]
KRR_DIR = REPO_ROOT / "krr_outputs"
EELNOUD_DIR = KRR_DIR / "eelnoud"
CACHE_DIR = REPO_ROOT / "data" / "riigikogu"

API_BASE = "https://api.riigikogu.ee"
RATE_DELAY = 1.0  # seconds between requests (Riigikogu: 1 request/s)
LIST_PAGE_SIZE = 500
LICENSE_IRI = "https://creativecommons.org/licenses/by-sa/3.0/"
LICENSE_NOTE = (
    "Riigikogu open data (api.riigikogu.ee), CC BY-SA 3.0 "
    "https://creativecommons.org/licenses/by-sa/3.0/ ; trimmed projection "
    "of the API responses cached by estleg.generate_riigikogu_proceedings (#717)."
)
SUBJECT_SOURCE = "riigikogu"
EUROVOC_URI_BASE = "http://eurovoc.europa.eu/"
_EUROVOC_CODE_RE = re.compile(r"^(?:[0-9]+|c_[0-9a-f]+)$")

# Join methods (values of estleg:derivationMethod on Riigikogu steps).
JOIN_EIS_NUMBER = "riigikogu-eis-number"
JOIN_MARK = "riigikogu-mark"
JOIN_TITLE_DATE = "riigikogu-title-date"
JOIN_PRIORITY = (JOIN_MARK, JOIN_EIS_NUMBER, JOIN_TITLE_DATE)

TITLE_DATE_WINDOW_DAYS = 366
GOVERNMENT_INITIATOR = "vabariigi valitsus"


# ---------------------------------------------------------------------------
# Cached, rate-limited client
# ---------------------------------------------------------------------------


@dataclass
class RiigikoguClient:
    """GET JSON from api.riigikogu.ee through a file cache.

    ``offline=True`` never touches the network: a cache miss returns
    ``None``. Every network request is counted in ``requests``.
    """

    cache_dir: Path = CACHE_DIR
    offline: bool = False
    rate_delay: float = RATE_DELAY
    requests: int = 0
    _last: float = field(default=0.0, repr=False)

    def _wait(self) -> None:
        elapsed = time.monotonic() - self._last
        if elapsed < self.rate_delay:
            time.sleep(self.rate_delay - elapsed)

    retries: int = 3

    def fetch(self, path: str, params: dict | None = None) -> dict | list:
        """GET with bounded retry on 5xx / connection errors (never on 4xx)."""
        import requests  # local: only the network path needs it

        if self.offline:
            raise RuntimeError(f"offline: refusing to fetch {path}")
        url = API_BASE + path
        for attempt in range(self.retries + 1):
            self._wait()
            try:
                resp = allowed_get(url, params=params or {}, timeout=90)
            except requests.RequestException:
                if attempt >= self.retries:
                    raise
                time.sleep(2.0 * 2**attempt)
                continue
            finally:
                self._last = time.monotonic()
                self.requests += 1
            if resp.status_code >= 500 and attempt < self.retries:
                time.sleep(2.0 * 2**attempt)
                continue
            resp.raise_for_status()
            return resp.json()
        raise RuntimeError(f"unreachable: {url}")  # pragma: no cover

    def cached(
        self,
        rel: str,
        path: str,
        params: dict | None = None,
        *,
        trim=None,
        refresh: bool = False,
    ):
        """Return the cached projection at ``cache_dir/rel`` or fetch it."""
        target = self.cache_dir / rel
        if target.exists() and not refresh:
            return json.loads(target.read_text(encoding="utf-8"))
        if self.offline:
            return None
        data = self.fetch(path, params)
        if trim is not None:
            data = trim(data)
        save_json(target, data)
        return data


# ---------------------------------------------------------------------------
# Response trimming (only the fields the join and the steps use are cached)
# ---------------------------------------------------------------------------

_LIST_FIELDS = (
    "uuid",
    "title",
    "mark",
    "membership",
    "draftTypeCode",
    "activeDraftStage",
    "activeDraftStatus",
    "proceedingStatus",
    "activeDraftStatusDate",
    "initiated",
)

# "[EIS]_Eelnõu_esitamine__RIIGIKOGU-26-0522_-_…" → RIIGIKOGU/26-0522
_EIS_FILE_NUMBER_RE = re.compile(r"([A-ZÄÖÜÕŠŽ]{2,9})-(\d{2})-(\d{4})")


def trim_list_page(page: dict) -> dict:
    content = ((page or {}).get("_embedded") or {}).get("content") or []
    return {
        "page": (page or {}).get("page") or {},
        "content": [{k: item.get(k) for k in _LIST_FIELDS} for item in content],
    }


def _iter_file_names(value: object) -> Iterable[str]:
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "fileName" and isinstance(item, str):
                yield item
            else:
                yield from _iter_file_names(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_file_names(item)


def eis_numbers_in_detail(detail: dict) -> list[str]:
    """EIS numbers quoted by Riigikogu's "[EIS]" notice file names."""
    found: list[str] = []
    for name in _iter_file_names(detail):
        if "[EIS]" not in name and "EIS" not in name.split("_")[0]:
            continue
        for code, yy, num in _EIS_FILE_NUMBER_RE.findall(name):
            number = f"{code}/{yy}-{num}"
            if number not in found:
                found.append(number)
    return found


def trim_detail(detail: dict) -> dict:
    readings = []
    for reading in detail.get("readings") or []:
        events = []
        for event in reading.get("proceedingEvents") or []:
            status = event.get("status")
            if not status:
                continue
            events.append({"date": event.get("date"), "status": status})
        readings.append({"readingCode": reading.get("readingCode"), "events": events})
    return {
        "uuid": detail.get("uuid"),
        "title": detail.get("title"),
        "mark": detail.get("mark"),
        "membership": detail.get("membership"),
        "draftTypeCode": detail.get("draftTypeCode"),
        "activeDraftStage": detail.get("activeDraftStage"),
        "activeDraftStatus": detail.get("activeDraftStatus"),
        "initiated": detail.get("initiated"),
        "accepted": detail.get("accepted"),
        "initiators": [
            {"name": i.get("name"), "type": i.get("type")}
            for i in detail.get("initiators") or []
        ],
        "descriptors": [
            {"edid": d.get("edid"), "text": d.get("text")}
            for d in detail.get("descriptors") or []
        ],
        "readings": readings,
        "eisNumbers": eis_numbers_in_detail(detail),
    }


def trim_descriptor(data: list | dict) -> dict:
    rows = data if isinstance(data, list) else [data]
    row = rows[0] if rows else {}
    return {"edid": row.get("edid"), "code": row.get("code"), "text": row.get("text")}


def trim_memberships(data: list | dict) -> list[dict]:
    rows = data if isinstance(data, list) else (data.get("_embedded") or {}).get("content", [])
    out = []
    for row in rows or []:
        out.append(
            {
                "membership": row.get("membership") or row.get("number"),
                "startDate": row.get("startDate"),
                "endDate": row.get("endDate"),
            }
        )
    return out


LIST_SPLIT_SIZES = (500, 50, 5, 1)
LIST_SORT = ["mark,asc", "uuid,asc"]


def _list_window(
    client: RiigikoguClient, offset: int, size: int, *, refresh: bool
) -> dict | None:
    import requests  # local: only the network path needs it

    try:
        return client.cached(
            f"drafts_list/size{size:03d}_page{offset // size:05d}.json",
            "/api/volumes/drafts",
            {"page": offset // size, "size": size, "lang": "ET", "sort": LIST_SORT},
            trim=trim_list_page,
            refresh=refresh,
        )
    except requests.HTTPError as exc:
        status = getattr(exc.response, "status_code", None)
        if status != 404:
            raise
        # Record the failure so an offline rerun replays the same split.
        marker = {"error": status}
        save_json(client.cache_dir / f"drafts_list/size{size:03d}_page{offset // size:05d}.json", marker)
        return marker


def load_draft_listing(
    client: RiigikoguClient, *, refresh: bool = False
) -> tuple[list[dict], list[int]]:
    """All Riigikogu draft volumes (listing projection), cached per window.

    The API answers HTTP 404 for a whole listing page when one row on it
    cannot be serialised, so a failing window is split (500 → 50 → 5 → 1)
    and only the unservable single rows are skipped. Returns
    ``(rows, skipped_offsets)``.
    """
    rows: list[dict] = []
    skipped: list[int] = []
    total: list[int] = []  # learned from the first window that answers

    def walk(offset: int, size_index: int) -> bool:
        size = LIST_SPLIT_SIZES[size_index]
        page = _list_window(client, offset, size, refresh=refresh)
        if page is None:
            raise RuntimeError(f"incomplete Riigikogu listing: missing window {offset}/{size}")
        if "error" in page:
            if size_index + 1 >= len(LIST_SPLIT_SIZES):
                skipped.append(offset)
                return True
            child = LIST_SPLIT_SIZES[size_index + 1]
            answered = False
            for sub in range(offset, offset + size, child):
                if total and sub >= total[0]:
                    break
                answered = walk(sub, size_index + 1) or answered
            return answered
        count = (page.get("page") or {}).get("totalElements")
        content = page.get("content")
        if not isinstance(count, int) or count < 0 or not isinstance(content, list):
            raise RuntimeError("incomplete Riigikogu listing: missing page/count/content")
        if total and total[0] != count:
            raise RuntimeError("incomplete Riigikogu listing: total changed during pagination")
        if not total:
            total.append(count)
        if len(content) != min(size, max(0, count - offset)):
            raise RuntimeError(f"incomplete Riigikogu listing: truncated window {offset}/{size}")
        rows.extend(content)
        return True

    step = LIST_SPLIT_SIZES[0]
    offset = 0
    while True:
        walk(offset, 0)
        offset += step
        if not total or offset >= total[0]:
            break
    return rows, skipped


def load_memberships(client: RiigikoguClient, *, refresh: bool = False) -> list[dict]:
    data = client.cached(
        "memberships.json", "/api/memberships", trim=trim_memberships, refresh=refresh
    )
    if not data:
        raise RuntimeError("incomplete Riigikogu cache: missing memberships")
    return sorted(data or [], key=lambda m: m.get("startDate") or "")


def membership_on(day: str, memberships: list[dict]) -> int | None:
    """Riigikogu membership (koosseis) number whose term contains ``day``."""
    current = None
    for m in memberships:
        start = m.get("startDate") or ""
        if start and start <= day:
            current = m.get("membership")
    return current


# ---------------------------------------------------------------------------
# Title normalisation and mark parsing
# ---------------------------------------------------------------------------

DRAFT_TYPE_CODES = ("SE", "OE", "AE", "UA", "PE", "DE", "TK")
_MARK_RE = re.compile(r"(?<![\d/.-])(\d{1,4})\s?(" + "|".join(DRAFT_TYPE_CODES) + r")\b")
_QUOTES_RE = re.compile(r"[\"'„“”«»‘’`]")
_NON_WORD_RE = re.compile(r"[^\w]+")
_DROP_WORDS = frozenset({"eelnõu", "eelnõud", "eelnou"})
# Symmetric genitive → nominative fold: EIS writes "…seaduse eelnõu",
# Riigikogu "…seadus"; folding every occurrence on both sides is enough.
_CASE_FOLD = (("seadustiku", "seadustik"), ("seaduste", "seadus"), ("seaduse", "seadus"))


def _fold_word(word: str) -> str:
    """Fold compound genitives too: "liiklusseaduse" → "liiklusseadus"."""
    for suffix, nominative in _CASE_FOLD:
        if word.endswith(suffix):
            return word[: -len(suffix)] + nominative
    return word


def parse_marks(title: str) -> list[tuple[int, str]]:
    """Riigikogu marks quoted in an EIS title, e.g. ``(835 SE)`` → (835, 'SE')."""
    out: list[tuple[int, str]] = []
    for num, code in _MARK_RE.findall(title or ""):
        key = (int(num), code)
        if key not in out:
            out.append(key)
    return out


def normalize_title(title: str) -> str:
    """Comparable form of an EIS / Riigikogu bill title.

    EIS writes the draft ("X seaduse muutmise seaduse eelnõu (835 SE)"),
    Riigikogu the act ("X seaduse muutmise seadus"): drop the mark, quotes
    and the trailing "eelnõu", then fold "seaduse"/"seadustiku" at the end
    to the nominative.
    """
    text = unicodedata.normalize("NFC", title or "")
    text = _MARK_RE.sub(" ", text).lower()
    text = _QUOTES_RE.sub(" ", text)
    text = _NON_WORD_RE.sub(" ", text)
    words = [
        _fold_word(word)
        for word in text.split()
        if word not in _DROP_WORDS
    ]
    text = " ".join(words)
    return re.sub(r"\s+", " ", text).strip()


def title_tokens(title: str) -> set[str]:
    return {t for t in normalize_title(title).split() if len(t) > 2}


def title_similarity(a: str, b: str) -> float:
    ta, tb = title_tokens(a), title_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


# ---------------------------------------------------------------------------
# Joining EIS drafts to Riigikogu draft volumes
# ---------------------------------------------------------------------------

# An EIS item that quotes a mark but is ABOUT the bill rather than the bill
# itself (an opinion, implementing acts, amendment proposals, a reading
# briefing) must not inherit the bill's stages.
_RELATED_DOC_RE = re.compile(
    r"seisukoh|arvamus|muudatusettepanek|rakendusakt|lugemise|informatsioon|"
    r"ettepanek|märgukiri|vastuskiri|kommentaar",
    re.IGNORECASE,
)
MARK_TITLE_SIMILARITY = 0.5


@dataclass(frozen=True)
class Join:
    draft_id: str
    rk_uuid: str
    method: str


def _label(node: dict) -> str:
    value = node.get("rdfs:label", "")
    if isinstance(value, dict):
        return str(value.get("@value") or "")
    if isinstance(value, list):
        return " ".join(_label({"rdfs:label": v}) for v in value)
    return str(value or "")


def _literal(value: object) -> str:
    if isinstance(value, dict):
        raw = value.get("@value")
        return str(raw) if raw is not None else ""
    return str(value) if value is not None else ""


def _days_between(start: str, end: str) -> int | None:
    try:
        return (date.fromisoformat(end[:10]) - date.fromisoformat(start[:10])).days
    except (TypeError, ValueError):
        return None


def candidate_joins(
    drafts: list[dict],
    listing: list[dict],
    memberships: list[dict],
) -> tuple[list[Join], Counter]:
    """Joins from the listing alone (mark, title-date); one per draft."""
    stats: Counter = Counter()
    rows = {r["uuid"]: r for r in listing if r.get("uuid")}
    by_mark: dict[tuple, list[dict]] = defaultdict(list)
    by_title: dict[str, list[dict]] = defaultdict(list)
    for row in rows.values():
        by_mark[(row.get("membership"), row.get("mark"), row.get("draftTypeCode"))].append(row)
        by_title[normalize_title(row.get("title") or "")].append(row)

    joins: list[Join] = []
    for node in drafts:
        title = _label(node)
        day = _literal(node.get("estleg:publicationDate"))
        if not title or not day:
            continue
        related = bool(_RELATED_DOC_RE.search(title))
        membership = membership_on(day, memberships)
        joined = False
        for mark, code in parse_marks(title):
            cands = by_mark.get((membership, mark, code), [])
            if len(cands) != 1:
                continue
            row = cands[0]
            if related:
                stats["mark_skipped_related_document"] += 1
                break
            if title_similarity(title, row.get("title") or "") < MARK_TITLE_SIMILARITY and not re.search(
                rf"\(\s*{mark}\s?{code}\s*\)\s*$", title
            ):
                stats["mark_skipped_low_similarity"] += 1
                break
            joins.append(Join(node["@id"], row["uuid"], JOIN_MARK))
            stats["mark_candidate"] += 1
            joined = True
            break
        if joined or related:
            continue
        draft_type = ((node.get("estleg:draftType") or {}).get("@id") or "")
        if draft_type not in ("estleg:DraftType_Bill", "estleg:DraftType_AmendmentBill"):
            continue
        cands = [
            row
            for row in by_title.get(normalize_title(title), [])
            if row.get("draftTypeCode") == "SE"
            and (lag := _days_between(day, row.get("initiated") or "")) is not None
            and 0 <= lag <= TITLE_DATE_WINDOW_DAYS
        ]
        if len(cands) == 1:
            joins.append(Join(node["@id"], cands[0]["uuid"], JOIN_TITLE_DATE))
            stats["title_date_candidate"] += 1
        elif cands:
            stats["title_date_ambiguous"] += 1
    return joins, stats


def government_initiated(detail: dict) -> bool:
    return any(
        GOVERNMENT_INITIATOR in (i.get("name") or "").lower()
        for i in detail.get("initiators") or []
    )


def resolve_joins(
    drafts: list[dict],
    candidates: list[Join],
    details: dict[str, dict],
) -> tuple[dict[str, Join], Counter]:
    """Confirm candidates against details and add EIS-number joins.

    * a title-date join needs a Government initiator;
    * an EIS number that exactly one fetched detail quotes in its "[EIS]"
      notice files joins the EIS draft with that number. It overrides a
      title-date join but not a mark join: on the 2026-10 cache 11 notice
      files quoted the EIS number of a neighbouring bill (131 SE's EIS entry
      quoted from 132 SE), while the "(131 SE)" in the EIS title was right.
    """
    stats: Counter = Counter()
    by_eis = {
        _literal(n.get("estleg:eisNumber")): n["@id"]
        for n in drafts
        if _literal(n.get("estleg:eisNumber"))
    }
    final: dict[str, Join] = {}
    for join in candidates:
        detail = details.get(join.rk_uuid)
        if detail is None:
            stats["detail_missing"] += 1
            continue
        if join.method == JOIN_TITLE_DATE and not government_initiated(detail):
            stats["title_date_rejected_not_government"] += 1
            continue
        final[join.draft_id] = join
    quoted_by: dict[str, set[str]] = defaultdict(set)
    for uuid, detail in details.items():
        for number in detail.get("eisNumbers") or []:
            quoted_by[number].add(uuid)
    for number, uuids in sorted(quoted_by.items()):
        draft_id = by_eis.get(number)
        if draft_id is None:
            continue
        if len(uuids) != 1:
            stats["eis_number_quoted_by_several_volumes"] += 1
            continue
        uuid = next(iter(uuids))
        previous = final.get(draft_id)
        if previous and previous.rk_uuid != uuid:
            if previous.method == JOIN_MARK:
                stats["eis_number_conflicts_with_mark_kept_mark"] += 1
                continue
            stats["eis_number_overrode_title_date"] += 1
        if previous and previous.rk_uuid == uuid:
            stats["eis_number_confirms_" + previous.method] += 1
            if previous.method == JOIN_MARK:
                continue
        final[draft_id] = Join(draft_id, uuid, JOIN_EIS_NUMBER)
    stats.update("final_" + j.method for j in final.values())
    return final, stats


# ---------------------------------------------------------------------------
# Riigikogu events -> lifecycle steps
# ---------------------------------------------------------------------------

READING_PHASES = {
    "INITIATION": "RiigikoguProceeding",
    "FIRST_READING": "FirstReading",
    "SECOND_READING": "SecondReading",
    "THIRD_READING": "ThirdReading",
}
STATUS_PHASES = {
    "VASTU_VOETUD": "Enacted",
    "SAADETUD_VABARIIGI_PRESIDENDILE": "Enacted",
    "VALJAKUULUTATUD": "Enacted",
    "SAADETUD_RT": "Enacted",
    "AVALDATUD_RIIGITEATAJAS": "Enacted",
    "TAGASI_LYKATUD": "Rejected",
    "TAGASI_VOETUD": "Withdrawn",
    "TAGASI_VOETUD_TAISKOGUL_MENETLEMATA": "Withdrawn",
    "VALJA_LANGENUD": "Lapsed",
    "VALJA_LANGENUD_KOOSEISU_LOPPEMISEGA": "Lapsed",
    "VALJA_ARVATUD": "Lapsed",
    "YHENDATUD": "Lapsed",
    "YHENDAMISEGA_MENETLUS_LOPETATUD": "Lapsed",
    "TAGASTATUD": "Lapsed",
    "VALJA_KUULUTAMATA_JAETUD": "Reconsideration",
    "UUESTI_ARUTAMINE": "Reconsideration",
    "TEISTKORDNE_MENETLEMINE": "Reconsideration",
}
# activeDraftStage -> phase, for a draft whose detail carries no events.
STAGE_PHASES = {
    "MENETLUSSE_VOETUD": "RiigikoguProceeding",
    "ESIMENE_LUGEMINE": "FirstReading",
    "TEINE_LUGEMINE": "SecondReading",
    "KOLMAS_LUGEMINE": "ThirdReading",
    **STATUS_PHASES,
}


def draft_volume_url(uuid: str) -> str:
    return f"{API_BASE}/api/volumes/drafts/{uuid}"


def event_phase(reading_code: str, status: str) -> str:
    return STATUS_PHASES.get(status) or READING_PHASES.get(reading_code) or "RiigikoguProceeding"


def step_specs_for(detail: dict, method: str, listing_row: dict | None = None) -> list[dict]:
    """``merge_steps`` specs for one Riigikogu draft volume."""
    mark = f"{detail.get('mark')} {detail.get('draftTypeCode')}".strip()
    extra_base = {"dcterms:license": {"@id": LICENSE_IRI}}
    seen: set[tuple[str, str]] = set()
    specs: list[dict] = []
    for reading in detail.get("readings") or []:
        code = reading.get("readingCode") or ""
        for event in reading.get("events") or []:
            status = event.get("status") or ""
            day = (event.get("date") or "")[:10]
            if not status or (status, day) in seen:
                continue
            seen.add((status, day))
            specs.append(
                {
                    "phase": event_phase(code, status),
                    "day": day,
                    "method": method,
                    "source": draft_volume_url(detail["uuid"]),
                    "label": f"Riigikogu {mark}: {status}" + (f" ({day})" if day else ""),
                    "extra": {**extra_base, "estleg:riigikoguStatus": status},
                }
            )
    if not specs:
        stage = detail.get("activeDraftStage") or (listing_row or {}).get("activeDraftStage") or ""
        day = (
            (listing_row or {}).get("activeDraftStatusDate")
            or detail.get("accepted")
            or detail.get("initiated")
            or ""
        )[:10]
        phase = STAGE_PHASES.get(stage)
        if phase:
            specs.append(
                {
                    "phase": phase,
                    "day": day,
                    "method": method,
                    "source": draft_volume_url(detail["uuid"]),
                    "label": f"Riigikogu {mark}: {stage}" + (f" ({day})" if day else ""),
                    "extra": {**extra_base, "estleg:riigikoguStatus": stage},
                }
            )
    return specs


def eurovoc_codes(
    detail: dict, client: RiigikoguClient, stats: Counter
) -> list[str]:
    """EuroVoc concept ids of a draft's descriptors.

    Riigikogu ``edid`` equals the EuroVoc id for classic numeric
    descriptors (< 10000, checked on the API); newer concepts carry a
    ``c_<hex>`` code that only ``/api/eurovoc/descriptor?edid=`` returns,
    so those are looked up once each and cached.
    """
    out: list[str] = []
    for desc in detail.get("descriptors") or []:
        edid = desc.get("edid")
        if not isinstance(edid, int):
            continue
        if edid < 10000:
            code = str(edid)
        else:
            record = client.cached(
                f"eurovoc/{edid}.json",
                "/api/eurovoc/descriptor",
                {"edid": edid, "lang": "ET"},
                trim=trim_descriptor,
            )
            code = str((record or {}).get("code") or "")
        if not _EUROVOC_CODE_RE.match(code):
            stats["descriptor_without_eurovoc_code"] += 1
            continue
        if code not in out:
            out.append(code)
    return sorted(out, key=lambda c: (0, int(c)) if c.isdigit() else (1, c))


# ---------------------------------------------------------------------------
# Apply to the drafts peeps
# ---------------------------------------------------------------------------

RIIGIKOGU_DRAFT_KEYS = (
    "estleg:riigikoguMark",
    "estleg:riigikoguUuid",
    "estleg:riigikoguMembership",
)
RIIGIKOGU_METHODS = frozenset(JOIN_PRIORITY)


def _subject_refs(value: object) -> list[str]:
    items = value if isinstance(value, list) else ([value] if value else [])
    return [i.get("@id") for i in items if isinstance(i, dict) and isinstance(i.get("@id"), str)]


def apply_to_draft(
    draft: dict,
    steps: list[dict],
    join: Join | None,
    detail: dict | None,
    listing_row: dict | None,
    codes: list[str],
) -> list[dict]:
    """Set/clear the Riigikogu layer on one draft; return its new steps."""
    from estleg.generate_draft_legislation import merge_steps

    for key in RIIGIKOGU_DRAFT_KEYS:
        draft.pop(key, None)
    if _literal(draft.get("estleg:subjectSource")) == SUBJECT_SOURCE:
        draft.pop("dcterms:subject", None)
        draft.pop("estleg:subjectSource", None)
    specs: list[dict] = []
    if join is not None and detail is not None:
        draft["estleg:riigikoguMark"] = f"{detail.get('mark')} {detail.get('draftTypeCode')}"
        draft["estleg:riigikoguUuid"] = detail["uuid"]
        if detail.get("membership") is not None:
            draft["estleg:riigikoguMembership"] = {
                "@value": str(detail["membership"]),
                "@type": "xsd:integer",
            }
        specs = step_specs_for(detail, join.method, listing_row)
        if codes and not draft.get("dcterms:subject"):
            # Always a list: validate_all requires dcterms:subject arrays.
            draft["dcterms:subject"] = [{"@id": EUROVOC_URI_BASE + c} for c in codes]
            draft["estleg:subjectSource"] = SUBJECT_SOURCE
    return merge_steps(draft["@id"], steps, specs, replace_methods=RIIGIKOGU_METHODS)


def _detail(client: RiigikoguClient, uuid: str, *, refresh: bool) -> dict | None:
    """Cached draft detail; an HTTP 404 is cached as ``{"error": 404}``."""
    import requests  # local: only the network path needs it

    rel = f"drafts/{uuid}.json"
    try:
        return client.cached(
            rel, f"/api/volumes/drafts/{uuid}", {"lang": "ET"}, trim=trim_detail, refresh=refresh
        )
    except requests.HTTPError as exc:
        if getattr(exc.response, "status_code", None) != 404:
            raise
        marker = {"uuid": uuid, "error": 404}
        save_json(client.cache_dir / rel, marker)
        return marker


def run(
    *,
    client: RiigikoguClient,
    eelnoud_dir: Path | None = None,
    refresh: bool = False,
    refresh_details: bool = False,
    dry_run: bool = False,
) -> dict:
    from estleg import generate_draft_legislation as gdl

    started = time.monotonic()
    docs = gdl.load_phase_peeps(eelnoud_dir)
    drafts = [n for doc in docs.values() for n in doc.get("@graph") or [] if gdl.is_draft(n)]
    listing, skipped = load_draft_listing(client, refresh=refresh)
    rows = {r["uuid"]: r for r in listing if r.get("uuid")}
    memberships = load_memberships(client, refresh=refresh)
    candidates, stats = candidate_joins(drafts, listing, memberships)

    details: dict[str, dict] = {}
    for uuid in sorted({j.rk_uuid for j in candidates}):
        detail = _detail(client, uuid, refresh=refresh_details)
        if detail is None:
            raise RuntimeError(f"incomplete Riigikogu cache: missing detail {uuid}")
        if "error" in detail:
            stats["detail_http_404"] += 1
            continue
        details[uuid] = detail
    joins, join_stats = resolve_joins(drafts, candidates, details)
    stats.update(join_stats)

    steps_added = 0
    subjects = 0
    for doc in docs.values():
        updates: dict[str, list[dict]] = {}
        for draft, steps in gdl.drafts_with_steps(doc):
            join = joins.get(draft["@id"])
            # An unservable listing row/detail is unknown, not evidence that a
            # previously observed proceeding ceased to exist.
            if join is None and (skipped or stats["detail_http_404"]):
                continue
            detail = details.get(join.rk_uuid) if join else None
            codes = eurovoc_codes(detail, client, stats) if detail else []
            before = sum(1 for s in steps if gdl._literal(s.get("estleg:derivationMethod")) in RIIGIKOGU_METHODS)
            new_steps = apply_to_draft(draft, steps, join, detail, rows.get(join.rk_uuid) if join else None, codes)
            after = sum(1 for s in new_steps if gdl._literal(s.get("estleg:derivationMethod")) in RIIGIKOGU_METHODS)
            steps_added += max(after - before, 0)
            subjects += _literal(draft.get("estleg:subjectSource")) == SUBJECT_SOURCE
            updates[draft["@id"]] = new_steps
            gdl.finalize_draft(draft, new_steps)
        gdl.replace_steps(doc, updates)
        gdl.finalize_doc(doc)

    if not dry_run:
        gdl.save_phase_peeps(docs, eelnoud_dir)
        gdl.write_index(docs, eelnoud_dir)
        save_json(
            (client.cache_dir / "LICENSE.json"),
            {"license": LICENSE_IRI, "note": LICENSE_NOTE, "source": API_BASE},
        )
    lifecycle = gdl.lifecycle_stats(docs)
    return {
        "requests": client.requests,
        "runtime_s": round(time.monotonic() - started, 1),
        "riigikogu_drafts_listed": len(rows),
        "listing_rows_skipped": skipped,
        "details_cached": len(details),
        "matched_drafts": len(joins),
        "riigikogu_steps_total": sum(
            v for k, v in lifecycle["steps_by_method"].items() if k in RIIGIKOGU_METHODS
        ),
        "riigikogu_steps_added_this_run": steps_added,
        "drafts_with_riigikogu_subjects": subjects,
        "join_stats": dict(sorted(stats.items())),
        **lifecycle,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--offline", action="store_true", help="Never touch the network; cache misses are skipped.")
    parser.add_argument("--refresh", action="store_true", help="Re-fetch the draft listing and memberships.")
    parser.add_argument("--refresh-details", action="store_true", help="Re-fetch every matched draft detail.")
    parser.add_argument("--dry-run", action="store_true", help="Report without writing the peeps.")
    parser.add_argument("--rebuild-combined", action="store_true", help="Rebuild eelnoud_combined.jsonld afterwards.")
    parser.add_argument("--report", type=Path, default=None, help="Write the run report JSON here.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args([] if argv is None else argv)
    client = RiigikoguClient(offline=args.offline)
    report = run(
        client=client,
        refresh=args.refresh,
        refresh_details=args.refresh_details,
        dry_run=args.dry_run,
    )
    if args.rebuild_combined and not args.dry_run:
        from estleg.generate_draft_legislation import rebuild_eelnoud_combined_from_peeps

        rebuild_eelnoud_combined_from_peeps()
    text = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    print(text)
    if args.report:
        args.report.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

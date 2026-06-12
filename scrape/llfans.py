"""LLFans (ll-fans.jp) adapter — base event + performances from the community DB.

ll-fans.jp catalogs Love Live! performances as Tour → Concert → Performance via a
GraphQL API (`/api/graphql`, op `EventDetailPage`). We use it to seed an event's
*base* metadata — name, series, shows (date/venue/times), and the official URL —
which is far cleaner than scraping official pages. It has **no lottery rounds**, so
`/add` merges this into the event and the official/FC page supplies the deadlines.

Event page URLs look like `https://ll-fans.jp/data/event/<tourId>`.
"""

from __future__ import annotations

import re

import requests

API = "https://ll-fans.jp/api/graphql"
_ID = re.compile(r"/data/event/(\d+)")

# seriesId -> series tag (ll-fans / the-sorter series-info; stable set of 8).
SERIES = {
    "1": "ラブライブ！",
    "2": "ラブライブ！サンシャイン!!",
    "3": "虹ヶ咲学園スクールアイドル同好会",
    "4": "ラブライブ！スーパースター!!",
    "5": "スクールアイドルミュージカル",
    "6": "蓮ノ空女学院スクールアイドルクラブ",
    "7": "幻日のヨハネ -SUNSHINE in the MIRROR-",
    "8": "イキヅライブ！ LOVELIVE! BLUEBIRD",
}

# tourType.name -> our `kind`
_KIND = {"ライブ・ファンミ": "concert", "TV出演": "tv", "配信": "stream", "イベント": "event"}

_QUERY = """query EventDetailPage($id: ID!) {
  tour(id: $id) {
    id name seriesIds url
    tourType { name }
    concerts {
      id name
      venue { name }
      performances { id name date openTime startTime canceled }
    }
  }
}"""

# `tours` is a Lighthouse paginator (TourPaginator { data, paginatorInfo }).
_LIST_QUERY = """query Tours($first: Int!, $page: Int!) {
  tours(first: $first, page: $page) {
    data { id name startsOn endsOn seriesIds url }
    paginatorInfo { hasMorePages }
  }
}"""


def tour_id(url: str) -> str | None:
    m = _ID.search(url or "")
    return m.group(1) if m else None


def event_url(tid) -> str:
    return f"https://ll-fans.jp/data/event/{tid}"


def _hhmm(t: str | None) -> str | None:
    return t[:5] if t else None


def _post(query: str, variables: dict) -> dict:
    """POST a GraphQL query to LLFans; return data (raises on transport/GraphQL error)."""
    resp = requests.post(
        API,
        json={"query": query, "variables": variables},
        headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0 (event-tracker)"},
        timeout=25,
    )
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("errors"):
        raise RuntimeError(f"llfans graphql errors: {payload['errors']}")
    return payload.get("data") or {}


def query_tour(tid: str) -> dict:
    """Fetch a tour by id from the LLFans GraphQL API (raises on error)."""
    tour = _post(_QUERY, {"id": tid}).get("tour")
    if not tour:
        raise RuntimeError(f"llfans: no tour with id {tid}")
    return tour


def all_tours(page_size: int = 100) -> list[dict]:
    """Every tour (paginated): {id, name, startsOn, endsOn, seriesIds, url}."""
    out: list[dict] = []
    page = 1
    while True:
        block = _post(_LIST_QUERY, {"first": page_size, "page": page})["tours"]
        out.extend(block["data"])
        if not block["paginatorInfo"]["hasMorePages"]:
            return out
        page += 1


def upcoming_tours(today: str) -> list[dict]:
    """Tours not yet ended (endsOn >= today), soonest-first."""
    ts = [t for t in all_tours() if (t.get("endsOn") or t.get("startsOn") or "") >= today]
    ts.sort(key=lambda t: t.get("startsOn") or "")
    return ts


def from_tour(tour: dict, url: str | None = None) -> dict:
    """Map an LLFans tour into our ingest dict (base event + performances, no rounds)."""
    perfs = []
    for concert in tour.get("concerts") or []:
        venue = (concert.get("venue") or {}).get("name")
        for p in concert.get("performances") or []:
            if p.get("canceled"):
                continue
            perfs.append(
                {
                    "date": p["date"],
                    "venue": venue,
                    "label": p.get("name") or None,
                    "doors": _hhmm(p.get("openTime")),
                    "starts": _hhmm(p.get("startTime")),
                }
            )
    perfs.sort(key=lambda x: (x["date"], x.get("starts") or ""))
    kind = _KIND.get(((tour.get("tourType") or {}).get("name")) or "")
    return {
        "name": tour["name"],
        "series": [SERIES[s] for s in (tour.get("seriesIds") or []) if s in SERIES],
        "kind": kind or "concert",
        "official_url": tour.get("url") or None,
        "source_url": url,
        "llfans_id": str(tour["id"]),
        "performances": perfs,
        "rounds": [],
    }


def scrape(url: str) -> dict:
    tid = tour_id(url)
    if not tid:
        raise ValueError(f"not an ll-fans event URL (expected /data/event/<id>): {url}")
    return from_tour(query_tour(tid), url)

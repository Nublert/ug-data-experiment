#!/usr/bin/env python3
"""
One-off scraper for Ultimate Guitar top tabs.

Scrapes multiple lists (by hits and by rating) for several types, merges
data into a single cache file: data/ug_top.json

Data contract per row:
  - artist: str
  - song: str
  - type: str  (one of: chords, tab, guitar_pro, ukulele, bass)
  - url: str   (unique identifier; we build it from tab ID)
  - hits: int
  - rating: float | None
  - votes: int | None
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import html as html_lib
import re

import requests

try:
  # Optional Playwright fallback; only used if needed and installed.
  from playwright.sync_api import sync_playwright  # type: ignore
  HAS_PLAYWRIGHT = True
except Exception:
  HAS_PLAYWRIGHT = False


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
CACHE_PATH = DATA_DIR / "ug_top.json"
DATA_DIR.mkdir(exist_ok=True)

UG_BASE = "https://www.ultimate-guitar.com"
TABS_BASE = "https://tabs.ultimate-guitar.com"

# Canonical type keys -> UG query param values
TYPE_PARAM = {
    "chords": "chords",
    "tab": "tabs",
    "guitar_pro": "pro",
    "ukulele": "ukulele_chords",
    "bass": "bass_tabs",
}

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)


@dataclass
class Row:
    artist: str
    song: str
    type: str  # canonical: chords|tab|guitar_pro|ukulele|bass
    url: str
    hits: int
    rating: Optional[float]
    votes: Optional[int]


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now_utc().isoformat().replace("+00:00", "Z")


def _load_existing() -> Optional[dict]:
    if not CACHE_PATH.exists():
        return None
    try:
        with CACHE_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _cache_is_fresh(max_age_hours: float = 24.0) -> bool:
    data = _load_existing()
    if not data:
        return False
    meta = data.get("meta") or {}
    ts = meta.get("scraped_at")
    if not ts:
        return False
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return False
    age = _now_utc() - dt
    return age <= timedelta(hours=max_age_hours)


def _fetch_with_requests(session: requests.Session, url: str) -> str:
    resp = session.get(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        },
        timeout=25,
    )
    if resp.status_code == 403:
        print("403 headers:", dict(resp.headers))
        print("403 body:", resp.text[:300])
    resp.raise_for_status()
    resp.encoding = resp.encoding or "utf-8"
    return resp.text


def _fetch_with_playwright(url: str) -> str:
    if not HAS_PLAYWRIGHT:
        raise RuntimeError("Playwright not installed")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=USER_AGENT)
        page.goto(url, wait_until="networkidle")
        content = page.content()
        browser.close()
        return content


def _fetch_html(session: requests.Session, url: str) -> str:
    html = _fetch_with_requests(session, url)
    # Heuristic: if we cannot find data-content, try Playwright fallback.
    if "data-content=" in html:
        return html
    if HAS_PLAYWRIGHT:
        try:
            return _fetch_with_playwright(url)
        except Exception:
            # Fallback failed; return original HTML.
            return html
    return html


def _parse_page_embedded_json(html: str) -> Tuple[list, list]:
    """
    Returns (tabs, hits_list) where both are lists of dicts from UG JSON.
    tabs: tab objects (id, artist_name, song_name, rating, votes, etc.)
    hits_list: objects like {"id": ..., "hits": ...}
    """
    m = re.search(r'data-content="(.*?)"', html, re.IGNORECASE | re.DOTALL)
    if not m:
        return [], []
    blob = html_lib.unescape(m.group(1))
    data = json.loads(blob)
    page_data = (((data or {}).get("store") or {}).get("page") or {}).get("data") or {}
    tabs = page_data.get("tabs") or []
    hits_list = page_data.get("hits") or []
    return tabs, hits_list


def _build_rows_for_type(session: requests.Session, type_key: str) -> Dict[str, Row]:
    """
    Scrape both hits-ordered and rating-ordered lists for a given type,
    merge them into a dict keyed by URL.
    """
    if type_key not in TYPE_PARAM:
        raise ValueError(f"Unknown type: {type_key}")
    type_param = TYPE_PARAM[type_key]

    base = f"{UG_BASE}/top/tabs"
    hits_url = f"{base}?order=hitstotal_desc&type={type_param}"
    rating_url = f"{base}?order=rating_desc&type={type_param}"

    rows_by_url: Dict[str, Row] = {}

    # --- Hits-ordered list ---
    html_hits = _fetch_html(session, hits_url)
    tabs_hits, hits_list = _parse_page_embedded_json(html_hits)
    hits_by_id: Dict[int, int] = {}
    for h in hits_list:
        if not isinstance(h, dict):
            continue
        tid = h.get("id")
        hv = h.get("hits")
        try:
            tid_i = int(tid)
            hv_i = int(hv)
        except Exception:
            continue
        hits_by_id[tid_i] = hv_i

    for t in tabs_hits:
        if not isinstance(t, dict):
            continue
        tid = t.get("id")
        if tid is None:
            continue
        try:
            tid_i = int(tid)
        except Exception:
            continue

        artist = (t.get("artist_name") or "").strip()
        song = (t.get("song_name") or "").strip()
        if not artist or not song:
            continue

        url = f"{TABS_BASE}/tab/{tid_i}"
        hits = hits_by_id.get(tid_i, 0)

        existing = rows_by_url.get(url)
        if existing:
            # Update hits if higher / more precise
            if hits and hits > existing.hits:
                existing.hits = hits
        else:
            rows_by_url[url] = Row(
                artist=artist,
                song=song,
                type=type_key,
                url=url,
                hits=hits,
                rating=None,
                votes=None,
            )

    time.sleep(0.8)  # small delay between requests

    # --- Rating-ordered list ---
    html_rating = _fetch_html(session, rating_url)
    tabs_rating, _ = _parse_page_embedded_json(html_rating)

    for t in tabs_rating:
        if not isinstance(t, dict):
            continue
        tid = t.get("id")
        if tid is None:
            continue
        try:
            tid_i = int(tid)
        except Exception:
            continue

        artist = (t.get("artist_name") or "").strip()
        song = (t.get("song_name") or "").strip()
        url = f"{TABS_BASE}/tab/{tid_i}"

        rating = None
        votes = None
        if "rating" in t and t["rating"] is not None:
            try:
                rating = float(t["rating"])
            except Exception:
                rating = None
        if "votes" in t and t["votes"] is not None:
            try:
                votes = int(t["votes"])
            except Exception:
                votes = None

        existing = rows_by_url.get(url)
        if existing:
            if rating is not None:
                existing.rating = rating
            if votes is not None:
                existing.votes = votes
        else:
            # Exists only in rating list: include anyway.
            if not artist or not song:
                continue
            rows_by_url[url] = Row(
                artist=artist,
                song=song,
                type=type_key,
                url=url,
                hits=0,
                rating=rating,
                votes=votes,
            )

    return rows_by_url


def scrape_all(force: bool = False) -> dict:
    """
    Scrape all configured types and write/update the cache file.

    - Respects 24h cache unless force=True.
    - Returns the JSON object (meta + rows).
    """
    if not force and _cache_is_fresh():
        existing = _load_existing()
        if existing:
            return existing

    session = requests.Session()

    all_rows: Dict[str, Row] = {}
    types = list(TYPE_PARAM.keys())

    for i, t in enumerate(types):
        part = f"{t} ({i+1}/{len(types)})"
        print(f"[scraper] Fetching {part} ...")
        rows_for_type = _build_rows_for_type(session, t)
        for url, row in rows_for_type.items():
            existing = all_rows.get(url)
            if existing:
                # Merge cross-type if ever overlapping; prefer non-empty values.
                if row.hits and row.hits > existing.hits:
                    existing.hits = row.hits
                if row.rating is not None:
                    existing.rating = row.rating
                if row.votes is not None:
                    existing.votes = row.votes
            else:
                all_rows[url] = row
        time.sleep(0.8)

    rows_list: List[Row] = list(all_rows.values())
    # Sort primarily by hits desc, then rating desc.
    rows_list.sort(key=lambda r: (r.hits, r.rating or 0.0), reverse=True)

    out = {
        "meta": {
            "scraped_at": _now_iso(),
            "types": types,
            "row_count": len(rows_list),
        },
        "rows": [asdict(r) for r in rows_list],
    }

    tmp_path = CACHE_PATH.with_suffix(".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, sort_keys=False)
    os.replace(tmp_path, CACHE_PATH)

    return out


def main(argv: Optional[list] = None) -> int:
    force = False
    if argv is None:
        argv = []
    if "--force" in argv:
        force = True
    data = scrape_all(force=force)
    print(f"Scraped {data['meta']['row_count']} rows at {data['meta']['scraped_at']}")
    print(f"Cache file: {CACHE_PATH}")
    return 0


if __name__ == "__main__":
    import sys as _sys

    raise SystemExit(main(_sys.argv[1:]))


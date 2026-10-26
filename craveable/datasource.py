"""Retrieval layer: turns raw records into ``Restaurant`` objects.

Two sources implement the same interface (``search(prefs, log) -> list``):

* ``CsvSource`` (default) - the bundled synthetic Baltimore dataset, loaded with
  pandas. Restaurant names, prices and table inventories are fictional.
* ``YelpSource`` (optional, ``--source yelp``) - pulls live businesses from the
  Yelp Fusion API. Yelp does not expose table inventories, so each business
  is given a generic 2-top with ``view='unknown'``; the seating constraints are
  then treated as *soft* for those records.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from .eventlog import EventLog
from .models import WEEKDAYS, Preferences, Restaurant, Table, minutes

DAY_INDEX = {d: i for i, d in enumerate(WEEKDAYS)}


def default_data_dir() -> Path:
    """Works from source, from an installed wheel, and from a PyInstaller bundle."""
    if getattr(sys, "_MEIPASS", None):                      # PyInstaller one-file
        return Path(sys._MEIPASS) / "craveable" / "data"     # type: ignore[attr-defined]
    return Path(__file__).parent / "data"


def parse_hours(spec: str) -> Dict[str, Tuple[int, int]]:
    """'Mon-Thu 11:30-22:00; Fri-Sat 11:30-23:00; Sun 12:00-21:00' -> {day: (open, close)}."""
    out: Dict[str, Tuple[int, int]] = {}
    if not isinstance(spec, str):
        return out
    for part in spec.split(";"):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"(?P<d1>[A-Za-z]{3})(?:-(?P<d2>[A-Za-z]{3}))?\s+(?P<o>\d{1,2}:\d{2})-(?P<c>\d{1,2}:\d{2})", part)
        if not m:
            continue
        d1 = DAY_INDEX[m.group("d1").capitalize()]
        d2 = DAY_INDEX[(m.group("d2") or m.group("d1")).capitalize()]
        o, c = minutes(m.group("o")), minutes(m.group("c"))
        i = d1
        while True:
            out[WEEKDAYS[i]] = (o, c)
            if i == d2:
                break
            i = (i + 1) % 7
    return out


class CsvSource:
    name = "csv"

    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = Path(data_dir) if data_dir else default_data_dir()

    def load_all(self) -> List[Restaurant]:
        rdf = pd.read_csv(self.data_dir / "restaurants.csv", dtype=str).fillna("")
        tdf = pd.read_csv(self.data_dir / "tables.csv", dtype=str).fillna("")
        tables_by_r: Dict[str, List[Table]] = {}
        for row in tdf.itertuples(index=False):
            booked = frozenset(s.strip() for s in str(row.booked).split(";") if s.strip())
            tables_by_r.setdefault(row.restaurant_id, []).append(
                Table(
                    restaurant_id=row.restaurant_id,
                    table_id=row.table_id,
                    seats=int(row.seats),
                    window=str(row.window).lower() == "yes",
                    view=str(row.view).lower() or "none",
                    zone=row.zone,
                    booked=booked,
                )
            )
        out: List[Restaurant] = []
        for row in rdf.itertuples(index=False):
            out.append(
                Restaurant(
                    id=row.id,
                    name=row.name,
                    cuisine=row.cuisine,
                    secondary_cuisines=tuple(s for s in str(row.secondary_cuisines).split(";") if s),
                    neighborhood=row.neighborhood,
                    address=row.address,
                    lat=float(row.lat),
                    lon=float(row.lon),
                    price_tier=row.price_tier,
                    avg_dinner_pp=float(row.avg_dinner_pp),
                    rating=float(row.rating),
                    review_count=int(row.review_count),
                    accepts_reservations=str(row.accepts_reservations).lower() == "yes",
                    hours=parse_hours(row.hours),
                    notes=row.notes,
                    tables=tuple(tables_by_r.get(row.id, [])),
                )
            )
        return out

    def search(self, prefs: Preferences, log: Optional[EventLog] = None) -> List[Restaurant]:
        """Coarse retrieval: everything in the requested city (the CSP does the fine filtering).

        Mirrors what a search API would do with a ``location`` + ``term`` query: we
        deliberately keep *all* cuisines so the log can show the constraint
        solver pruning them, which is the interesting part.
        """
        all_rows = self.load_all()
        if log:
            log.info("RETRIEVE", f"loaded {len(all_rows)} restaurants / "
                     f"{sum(len(r.tables) for r in all_rows)} tables from {self.data_dir}",
                     source=self.name)
            by_cuisine: Dict[str, int] = {}
            for r in all_rows:
                by_cuisine[r.cuisine] = by_cuisine.get(r.cuisine, 0) + 1
            log.debug("RETRIEVE", "cuisine histogram", histogram=by_cuisine)
        return all_rows


class YelpSource:
    """Live retrieval from Yelp Fusion (``YELP_API_KEY`` required)."""

    name = "yelp"
    ENDPOINT = "https://api.yelp.com/v3/businesses/search"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("YELP_API_KEY")

    def search(self, prefs: Preferences, log: Optional[EventLog] = None) -> List[Restaurant]:
        if not self.api_key:
            raise RuntimeError("YELP_API_KEY is not set; use --source csv or export the key")
        location = ", ".join(x for x in [prefs.area, prefs.city or "Baltimore", prefs.state or "MD"] if x)
        params = {"term": f"{prefs.cuisine or ''} restaurants".strip(), "location": location, "limit": 50}
        if prefs.budget_total and prefs.party_size:
            pp = prefs.budget_total / prefs.party_size
            params["price"] = "1" if pp < 15 else "1,2" if pp < 40 else "1,2,3" if pp < 70 else "1,2,3,4"
        url = self.ENDPOINT + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.api_key}"})
        if log:
            log.info("RETRIEVE", "querying Yelp Fusion", params=params)
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        out: List[Restaurant] = []
        price_pp = {"$": 15, "$$": 28, "$$$": 50, "$$$$": 85}
        for b in payload.get("businesses", []):
            coords = b.get("coordinates") or {}
            tier = b.get("price", "$$")
            out.append(
                Restaurant(
                    id=b["id"],
                    name=b["name"],
                    cuisine=(b.get("categories") or [{"title": "Unknown"}])[0]["title"],
                    secondary_cuisines=tuple(c["title"] for c in (b.get("categories") or [])[1:]),
                    neighborhood=(b.get("location") or {}).get("city", ""),
                    address=" ".join((b.get("location") or {}).get("display_address", [])),
                    lat=float(coords.get("latitude") or 0.0),
                    lon=float(coords.get("longitude") or 0.0),
                    price_tier=tier,
                    avg_dinner_pp=float(price_pp.get(tier, 28)),
                    rating=float(b.get("rating") or 0.0),
                    review_count=int(b.get("review_count") or 0),
                    accepts_reservations=True,
                    hours={d: (minutes("11:00"), minutes("22:00")) for d in WEEKDAYS},
                    notes="live Yelp record; table inventory unknown",
                    tables=(Table(b["id"], "T1", 2, True, "unknown", "unknown", frozenset()),),
                )
            )
        if log:
            log.info("RETRIEVE", f"Yelp returned {len(out)} businesses", source=self.name)
        return out


def get_source(kind: str = "csv", data_dir: Optional[Path] = None):
    return YelpSource() if kind == "yelp" else CsvSource(data_dir)

"""Geography helpers: neighbourhood gazetteer and great-circle distance."""

from __future__ import annotations

import math
from typing import Dict, Optional, Tuple

# Centre of the Downtown Baltimore business district (Charles Center).
AREA_CENTROIDS: Dict[str, Tuple[float, float]] = {
    "downtown": (39.2904, -76.6122),
    "inner harbor": (39.2860, -76.6100),
    "mount vernon": (39.2975, -76.6155),
    "fells point": (39.2825, -76.5930),
    "federal hill": (39.2775, -76.6115),
    "harbor east": (39.2835, -76.6010),
    "canton": (39.2800, -76.5760),
    "hampden": (39.3310, -76.6350),
    "little italy": (39.2880, -76.5990),
    "towson": (39.4015, -76.6019),
}

# Neighbourhood labels that count as *inside* a requested area, before any
# distance test. Everything else falls back to the radius test.
AREA_MEMBERS: Dict[str, Tuple[str, ...]] = {
    "downtown": ("Downtown", "Inner Harbor", "Charles Center", "City Center"),
    "inner harbor": ("Inner Harbor",),
    "mount vernon": ("Mount Vernon",),
    "fells point": ("Fells Point",),
    "federal hill": ("Federal Hill",),
    "harbor east": ("Harbor East",),
    "canton": ("Canton",),
    "hampden": ("Hampden",),
    "little italy": ("Little Italy",),
    "towson": ("Towson",),
}

AREA_ALIASES: Dict[str, str] = {
    "downtown": "downtown",
    "downtown baltimore": "downtown",
    "center city": "downtown",
    "city center": "downtown",
    "cbd": "downtown",
    "inner harbor": "inner harbor",
    "the inner harbor": "inner harbor",
    "mount vernon": "mount vernon",
    "mt vernon": "mount vernon",
    "mt. vernon": "mount vernon",
    "fells point": "fells point",
    "fell's point": "fells point",
    "federal hill": "federal hill",
    "fed hill": "federal hill",
    "harbor east": "harbor east",
    "canton": "canton",
    "hampden": "hampden",
    "little italy": "little italy",
    "towson": "towson",
}

DEFAULT_RADIUS_MILES = 0.6   # "in Downtown" == named downtown neighbourhood OR within this radius


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def canonical_area(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    key = text.strip().lower()
    return AREA_ALIASES.get(key)


def pretty_area(area_key: str) -> str:
    return " ".join(w.capitalize() for w in area_key.split())


def distance_to_area(area_key: str, lat: float, lon: float) -> float:
    c = AREA_CENTROIDS[area_key]
    return haversine_miles(c[0], c[1], lat, lon)


def in_area(area_key: str, neighborhood: str, lat: float, lon: float,
            radius_miles: float = DEFAULT_RADIUS_MILES) -> Tuple[bool, float, str]:
    """Return (inside?, distance_miles, reason)."""
    d = distance_to_area(area_key, lat, lon)
    if neighborhood in AREA_MEMBERS.get(area_key, ()):
        return True, d, f"neighbourhood '{neighborhood}' is part of {pretty_area(area_key)}"
    if d <= radius_miles:
        return True, d, f"{d:.2f} mi from the {pretty_area(area_key)} centre (<= {radius_miles} mi)"
    return False, d, f"{neighborhood} is {d:.2f} mi from the {pretty_area(area_key)} centre (> {radius_miles} mi)"

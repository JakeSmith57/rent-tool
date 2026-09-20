"""
geo/geocode.py -- WP4 helper: turn a listing's raw street address into
(lat, lon, bbl) so it can be joined to the WP1 building registry
(stabilization_confidence, building_safety_tier) and scored against
WP2's park-distance data.

SOURCE: NYC Planning Labs GeoSearch (https://geosearch.planninglabs.nyc/) --
a free, no-API-key-required geocoder built on NYC's own Geosupport engine,
covering every valid NYC address. Chosen over NYC's official Geoclient API
specifically because Geoclient requires registering for an app id/key
(https://api-portal.nyc.gov) while GeoSearch needs nothing -- consistent
with this whole project's $0-tools-only constraint.

Endpoint: GET https://geosearch.planninglabs.nyc/v2/search?text=<address>
Returns a GeoJSON FeatureCollection; the first (best-ranked) feature's
properties include `pad_bbl` (the BBL) and geometry.coordinates is
[lon, lat] (GeoJSON's standard lon-first order, flipped here at the
boundary to this project's lat-first convention -- same pattern as
geo/distance.py's OSRM/ORS boundary flip).

STATUS IN THIS SANDBOX: BLOCKED, same as every other real network source in
this pipeline (data.cityofnewyork.us, S3, OSRM, ORS) --
geosearch.planninglabs.nyc is not on this sandbox's egress allowlist. The
`pad_bbl` field name is per GeoSearch's published response shape, NOT
independently re-verified against a live response in this environment --
verify it against a real response on your own machine and update
_extract_bbl() below if it's drifted (same "verify against what you
actually download" caveat as etl/load_pluto.py and etl/load_building_
issues.py use for their own manual/blocked sources).

CACHING: geocoding the same address repeatedly wastes calls against a free
public service that has no documented rate limit but also no guarantee of
one -- results are cached in the `geocode_cache` sqlite table (keyed on the
normalized input address string, in the same registry.db WP1/WP3b already
use) so a listing is only ever geocoded once, even across repeated pipeline
runs. A prior miss (a real lookup that found nothing) is also cached and
not retried every run, since the address is presumably still not on file.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "etl"))

GEOSEARCH_BASE_URL = "https://geosearch.planninglabs.nyc/v2/search"
GEOSEARCH_TIMEOUT_S = 10

CREATE_GEOCODE_CACHE_SQL = """
CREATE TABLE IF NOT EXISTS geocode_cache (
    address_key   TEXT PRIMARY KEY,
    raw_address   TEXT,
    bbl           TEXT,
    latitude      REAL,
    longitude     REAL,
    found         INTEGER NOT NULL,
    geocoded_at   TEXT
);
"""


def _normalize_address_key(address: str) -> str:
    """Lowercase, collapse whitespace -- just a cache key, never sent to the API."""
    return re.sub(r"\s+", " ", address.strip().lower())


def _extract_bbl(feature: dict) -> Optional[str]:
    props = feature.get("properties", {})
    bbl = props.get("pad_bbl") or props.get("bbl")
    if bbl is None:
        return None
    bbl_str = str(bbl).strip()
    # Normalize to the same 10-digit zero-padded format etl/common.py's
    # make_bbl() produces, in case GeoSearch returns it as an unpadded
    # int-like string.
    if bbl_str.isdigit() and len(bbl_str) <= 10:
        return bbl_str.zfill(10)
    return bbl_str


def geocode_address(address: str, borough_hint: str = "Brooklyn") -> Optional[dict]:
    """Look up `address` via NYC GeoSearch. `borough_hint` is appended if
    the address doesn't already mention it -- GeoSearch needs a
    borough/city to disambiguate, and every target neighborhood here is in
    Brooklyn, so this defaults to that.

    Returns {"bbl": str|None, "lat": float, "lon": float} on a successful
    match, or None if the address couldn't be geocoded at all (not on
    file, or the API call itself failed) -- callers should treat None as
    "skip scoring this listing, don't crash the run," the same graceful-
    degradation pattern used everywhere else real network calls happen in
    this pipeline.
    """
    query = address if borough_hint.lower() in address.lower() else f"{address}, {borough_hint}, NY"
    url = f"{GEOSEARCH_BASE_URL}?{urllib.parse.urlencode({'text': query, 'size': 1})}"
    try:
        with urllib.request.urlopen(url, timeout=GEOSEARCH_TIMEOUT_S) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        features = data.get("features") or []
        if not features:
            return None
        feat = features[0]
        lon, lat = feat["geometry"]["coordinates"]
        return {"bbl": _extract_bbl(feat), "lat": float(lat), "lon": float(lon)}
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(
            f"[geocode.py] GeoSearch call failed ({exc.__class__.__name__}: {exc}) "
            f"for {address!r} -- skipping this address.",
            file=sys.stderr,
        )
        return None


def geocode_with_cache(conn, address: str, borough_hint: str = "Brooklyn") -> Optional[dict]:
    """Same as geocode_address(), but checks/populates the `geocode_cache`
    sqlite table (on `conn`) first so repeated runs against the same
    address don't re-call the API.
    """
    conn.execute(CREATE_GEOCODE_CACHE_SQL)
    key = _normalize_address_key(address)
    row = conn.execute(
        "SELECT bbl, latitude, longitude, found FROM geocode_cache WHERE address_key = ?",
        (key,),
    ).fetchone()
    if row is not None:
        bbl, lat, lon, found = row
        if not found:
            return None
        return {"bbl": bbl, "lat": lat, "lon": lon}

    result = geocode_address(address, borough_hint)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if result is None:
        conn.execute(
            "INSERT OR REPLACE INTO geocode_cache "
            "(address_key, raw_address, bbl, latitude, longitude, found, geocoded_at) "
            "VALUES (?, ?, NULL, NULL, NULL, 0, ?)",
            (key, address, now),
        )
    else:
        conn.execute(
            "INSERT OR REPLACE INTO geocode_cache "
            "(address_key, raw_address, bbl, latitude, longitude, found, geocoded_at) "
            "VALUES (?, ?, ?, ?, ?, 1, ?)",
            (key, address, result["bbl"], result["lat"], result["lon"], now),
        )
    conn.commit()
    return result

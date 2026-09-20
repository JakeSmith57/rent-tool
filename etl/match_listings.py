#!/usr/bin/env python3
"""
etl/match_listings.py -- WP4: join ingested listings (WP3b's `listings`
sqlite table) to the WP1 building registry (stabilization_confidence,
building_safety_tier) and WP2's park-distance data, and produce a single
ranked "here's what to look at" output.

This is the first script in the pipeline that actually touches a real
listing address: WP1 built a registry with no listings, WP2 built park
distances with no listings, WP3b ingests listings with no registry/geo
join at all. This closes that loop.

PIPELINE
    1. Load every row from the `listings` sqlite table (etl/common.py's
       shared registry.db -- WP3b's ingest/store.py already writes there).
    2. Geocode each listing's raw_address via geo/geocode.py (NYC
       GeoSearch, free/no-key, cached in `geocode_cache` -- see that
       module). A listing that can't be geocoded is skipped (not crashed
       on) and reported in the run summary.
    3. Look up the geocoded BBL in the WP1 registry (data/processed/
       registry.csv) for stabilization_confidence / building_safety_tier.
       A BBL with no registry match (e.g. outside the ~19.5k-row Brooklyn
       set) is scored as unknown/unknown rather than dropped -- a listing
       is still worth seeing even with no signal on it.
    4. Compute nearest-anchor-park distance via geo/build_geo.py's
       load_parks() + geo/distance.py's nearest_park(). Uses the
       straight_line backend by default REGARDLESS of geo/distance.py's
       own ACTIVE_BACKEND -- see MATCH_DISTANCE_BACKEND below for why.
    5. Compute a single composite match_score (see SCORING below) and
       write every matched listing to data/processed/matched_listings.csv,
       sorted best-first.

WHY STRAIGHT-LINE DISTANCE HERE (not real routing, even though geo/
distance.py supports it): build_geo.py real-routes a small, FIXED set of
validation points against 12 parks -- at most a couple hundred routed
calls. This script runs against every live listing, an open-ended and
growing number -- real-routing all of them against OSRM's rate-limited
public demo server (or burning through ORS's 2,000/day quota) doesn't
scale the same way. Straight-line*circuity is a reasonable proxy for
ranking/triage (KNOWN LIMITATION: understates distance across barriers --
see geo/distance.py's module docstring); MATCH_ROUTE_TOP_N below
optionally re-routes just the top N ranked listings with the real backend
for a more accurate final number on the ones that actually matter.

SCORING: match_score (0-100) is a SUBJECTIVE weighted composite of three
0-3 sub-scores -- stabilization confidence tier, building safety tier, and
walk-time-to-nearest-park bucket -- see the *_SUBSCORE dicts and
_park_subscore() below. This is a starting point for ranking/triage, not
an authoritative "quality" measure; the weights are flat/equal on purpose
(no sub-signal is presumed more important than another) and are meant to
be retuned once real listings and real judgment about what matters most
are both in hand.

MANUAL STEP: none -- geocoding calls a live network API at run time
(unlike WP1's manual-CSV-download sources), but every result is cached in
`geocode_cache` so re-running this script doesn't re-pay that cost.
STATUS IN THIS SANDBOX: BLOCKED like every other real network call in this
pipeline (see geo/geocode.py's docstring) -- this script cannot be tested
end-to-end here, only smoke-tested against synthetic/mocked geocode
results.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "geo"))

from common import DATA_PROCESSED, get_connection  # noqa: E402
from geocode import geocode_with_cache  # noqa: E402
import distance as distance_mod  # noqa: E402
from build_geo import load_parks  # noqa: E402

REGISTRY_CSV = DATA_PROCESSED / "registry.csv"
MATCHED_OUT_CSV = DATA_PROCESSED / "matched_listings.csv"

# See module docstring's "WHY STRAIGHT-LINE DISTANCE HERE".
MATCH_DISTANCE_BACKEND = "straight_line"
# After ranking every listing with the fast straight-line backend, re-route
# just the top N with the real backend (geo/distance.py's ACTIVE_BACKEND at
# the time this runs -- whatever that module is currently set to) for a
# trustworthy final number on the listings that actually matter. Set to 0
# to skip re-routing entirely.
MATCH_ROUTE_TOP_N = 10

# Composite match_score sub-weights -- see module docstring's "SCORING".
STABILIZATION_SUBSCORE = {"high": 3, "moderate": 2, "low": 1, "unknown": 0}
# "unknown" safety is scored NEUTRAL (1), not as bad as "concerning" -- an
# unscreened building isn't evidence of a problem, it's an absence of data
# (same reasoning etl/build_registry.py's docstring gives for keeping this
# signal independent from stabilization_confidence).
SAFETY_SUBSCORE = {"clean_record": 3, "minimal": 2, "some_issues": 1, "concerning": 0, "unknown": 1}
MAX_TOTAL_SUBSCORE = 3 + 3 + 3  # stabilization + safety + park, each 0-3


def _park_subscore(walk_minutes: float) -> int:
    if walk_minutes <= 10:
        return 3
    if walk_minutes <= 20:
        return 2
    if walk_minutes <= 30:
        return 1
    return 0


def load_registry_lookup() -> dict:
    """{bbl: {"stabilization_confidence": ..., "building_safety_tier": ..., ...}}"""
    if not REGISTRY_CSV.exists():
        print(
            f"WARNING: {REGISTRY_CSV} not found -- every listing will score "
            "unknown/unknown on the registry signals. Run etl/build_registry.py "
            "first for real scoring.",
            file=sys.stderr,
        )
        return {}
    lookup = {}
    with open(REGISTRY_CSV, newline="") as f:
        for row in csv.DictReader(f):
            lookup[row["bbl"]] = row
    return lookup


def score_listing(listing_row: dict, registry_lookup: dict, parks: dict, conn) -> dict | None:
    """listing_row: a dict shaped like ingest/schema.py's LISTING_FIELDS.
    Returns a scored-listing dict, or None if the listing couldn't be
    geocoded at all (skip, don't crash the whole run over one bad
    address)."""
    geo = geocode_with_cache(conn, listing_row["raw_address"])
    if geo is None:
        return None

    reg = registry_lookup.get(geo["bbl"], {})
    stab_tier = reg.get("stabilization_confidence", "unknown")
    safety_tier = reg.get("building_safety_tier", "unknown")

    distance_mod.ACTIVE_BACKEND = MATCH_DISTANCE_BACKEND
    park_name, park_m = distance_mod.nearest_park((geo["lat"], geo["lon"]), parks)
    walk_min = distance_mod.meters_to_walk_minutes(park_m)

    stab_sub = STABILIZATION_SUBSCORE.get(stab_tier, 0)
    safety_sub = SAFETY_SUBSCORE.get(safety_tier, 1)
    park_sub = _park_subscore(walk_min)
    match_score = round(100 * (stab_sub + safety_sub + park_sub) / MAX_TOTAL_SUBSCORE, 1)

    return {
        "source": listing_row.get("source"),
        "source_listing_id": listing_row.get("source_listing_id"),
        "raw_address": listing_row.get("raw_address"),
        "url": listing_row.get("url"),
        "rent": listing_row.get("rent"),
        "beds": listing_row.get("beds"),
        "baths": listing_row.get("baths"),
        "bbl": geo["bbl"],
        "latitude": geo["lat"],
        "longitude": geo["lon"],
        "stabilization_confidence": stab_tier,
        "stabilization_confidence_reason": reg.get("stabilization_confidence_reason"),
        "building_safety_tier": safety_tier,
        "building_safety_tier_reason": reg.get("building_safety_tier_reason"),
        "nearest_park": park_name,
        "nearest_park_walk_min": round(walk_min, 1),
        "match_score": match_score,
    }


def main():
    conn = get_connection()
    conn.row_factory = None
    cur = conn.execute("SELECT * FROM listings")
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()

    if not rows:
        print(
            "No rows in the `listings` table yet -- nothing to match. Run "
            "the WP3b email-alert ingest first.",
            file=sys.stderr,
        )
        conn.close()
        return

    registry_lookup = load_registry_lookup()
    print("Loading parks for distance scoring...", flush=True)
    parks = load_parks()

    print(f"Scoring {len(rows)} listing(s)...", flush=True)
    scored = []
    skipped = 0
    for i, row in enumerate(rows, start=1):
        listing_row = dict(zip(cols, row))
        print(f"[{i}/{len(rows)}] {listing_row.get('raw_address')}...", end="", flush=True)
        result = score_listing(listing_row, registry_lookup, parks, conn)
        if result is None:
            skipped += 1
            print(" SKIPPED (could not geocode)", flush=True)
            continue
        scored.append(result)
        print(f" score={result['match_score']}", flush=True)

    conn.close()
    scored.sort(key=lambda r: r["match_score"], reverse=True)

    if MATCH_ROUTE_TOP_N > 0 and scored:
        n = min(MATCH_ROUTE_TOP_N, len(scored))
        print(f"Re-routing top {n} with the real routing backend...", flush=True)
        for r in scored[:n]:
            name, m = distance_mod.nearest_park((r["latitude"], r["longitude"]), parks)
            r["nearest_park"] = name
            r["nearest_park_walk_min"] = round(distance_mod.meters_to_walk_minutes(m), 1)

    if scored:
        with open(MATCHED_OUT_CSV, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(scored[0].keys()))
            writer.writeheader()
            writer.writerows(scored)
        print(
            f"\nWrote {len(scored)} scored listings to {MATCHED_OUT_CSV} "
            f"({skipped} skipped, could not geocode)"
        )
        print("\nTop picks:")
        for r in scored[:10]:
            print(
                f"  [{r['match_score']}] {r['raw_address']} -- ${r['rent']} / "
                f"{r['beds']}bd -- stab={r['stabilization_confidence']} "
                f"safety={r['building_safety_tier']} -- {r['nearest_park']} "
                f"({r['nearest_park_walk_min']} min walk)"
            )
    else:
        print(f"\nNo listings could be geocoded/scored ({skipped} skipped).")


if __name__ == "__main__":
    main()

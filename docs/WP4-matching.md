---
description: WP4 -- joins ingested listings to the WP1 registry and WP2 park-distance data, and ranks them. Written 2026-09-20.
---

# WP4: listing matching and ranking

This is the stage that finally connects the three previously-separate
pieces of the pipeline:

- **WP1** (`etl/build_registry.py`) -- a BBL-keyed table of
  `stabilization_confidence` and `building_safety_tier`, with no listings
  in it at all.
- **WP2** (`geo/build_geo.py`) -- real walking distance from a small fixed
  set of validation points to the 12 anchor parks, with no listings in it
  at all.
- **WP3b** (`ingest/parse_streeteasy_email.py` + friends) -- real listing
  addresses from your StreetEasy saved-search alerts, with no registry or
  geo signal on them at all.

`etl/match_listings.py` reads every row out of the `listings` sqlite table
(the one WP3b already writes to), geocodes each address, joins it to the
registry by BBL, computes its walk time to the nearest anchor park, and
writes out a single ranked CSV.

## New files

- **`geo/geocode.py`** -- turns a street address into `(bbl, lat, lon)`
  using [NYC Planning Labs GeoSearch](https://geosearch.planninglabs.nyc/),
  a free, no-API-key geocoder (chosen specifically so this stays a $0
  tool, unlike NYC's official Geoclient API which requires registering for
  a key). Results are cached in a new `geocode_cache` sqlite table so the
  same address is never re-geocoded on a later run.
- **`etl/match_listings.py`** -- the WP4 pipeline itself. Run it after
  `etl/build_registry.py` and after at least one listing has been ingested
  via the WP3b email-alert path:
  ```bash
  python3 etl/match_listings.py
  ```
  Output: `data/processed/matched_listings.csv`, sorted best-first, plus a
  printed top-10 summary.

## How `match_score` works

Each listing gets three 0-3 sub-scores, summed and rescaled to 0-100:

| Signal | Source | 3 | 2 | 1 | 0 |
|---|---|---|---|---|---|
| Stabilization | WP1 `stabilization_confidence` | high | moderate | low | unknown |
| Building safety | WP1 `building_safety_tier` | clean_record | minimal | some_issues | concerning |
| Park proximity | WP2 walk time to nearest anchor park | ≤10 min | ≤20 min | ≤30 min | >30 min |

Building safety's `unknown` tier is scored as **neutral (worth 1)**, not
as bad as `concerning` -- an unscreened building isn't evidence of a
problem, it's an absence of data, matching the reasoning
`build_registry.py` already uses to keep that signal independent from
stabilization confidence.

**This weighting is a starting point, not ground truth.** All three
signals are weighted equally on purpose (no signal was assumed more
important than another). `STABILIZATION_SUBSCORE`, `SAFETY_SUBSCORE`, and
`_park_subscore()` in `etl/match_listings.py` are the places to retune
this once you've seen real ranked results and have opinions about what
should move a listing up or down.

## Why park distance uses straight-line, not real routing, here

`geo/build_geo.py` real-routes a small, fixed set of validation points.
This script runs against every live listing -- an open-ended, growing
number. Real-routing all of them against OSRM's rate-limited public demo
server (or burning through ORS's 2,000/day free quota) doesn't scale the
same way a fixed validation set does. So `match_listings.py` always uses
the `straight_line` backend for the *first pass* over every listing
(fast, no network calls, but understates distance across barriers like
the Gowanus Canal or the BQE -- see `geo/distance.py`'s own docstring for
this known limitation), then re-routes just the top `MATCH_ROUTE_TOP_N`
(default 10) with the real backend for a trustworthy final number on the
listings that actually matter.

## Status: built, unit-tested with mocked geocoding, not yet run against real data

This sandbox's network egress can't reach `geosearch.planninglabs.nyc`
(same restriction as every other real data source in this pipeline --
OSRM, ORS, data.cityofnewyork.us, S3). The scoring/matching logic itself
was verified end-to-end with a synthetic sqlite `listings` table and a
mocked `geocode_with_cache()`, confirming: a geocodable address with a
real registry BBL scores correctly (high stabilization -> higher
match_score), an ungeocodable address is skipped without crashing the
run, and the CSV/top-picks output format is correct.

**Not yet verified**: a real call to GeoSearch, and the `pad_bbl` response
field name it returns (per GeoSearch's published docs, not independently
re-checked against a live response -- see `geo/geocode.py`'s docstring).
Run `python3 etl/match_listings.py` on your own machine once you have at
least one real ingested listing (check with
`sqlite3 data/interim/registry.db "SELECT COUNT(*) FROM listings"`) and
share the output -- if GeoSearch's real response shape differs from what
`_extract_bbl()` expects, that function is the one to fix.

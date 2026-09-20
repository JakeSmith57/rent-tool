# Rent-Stabilized Apartment Finder — build scaffold

This is the WP0–WP2 output from the [build plan doc](docs/) for finding a
rent-stabilized 1–2BR apartment near a park in eight Brooklyn neighborhoods
(Greenpoint, Williamsburg, Fort Greene, Clinton Hill, Prospect Heights, Park
Slope, Carroll Gardens, Cobble Hill).

**Why this is a zip instead of already-run output:** the cloud container
this was built in has its outbound network locked to package registries
only (npm, pypi, GitHub) — every actual data host (NYC Open Data, the DHCR
PDF, PLUTO, ArcGIS, OSRM) returned a 403 at the network gateway. Every
script below is written, tested against fixtures where needed, and fully
documented — but the *data* in `data/` and `geo/*.geojson` right now is
either a small synthetic test fixture or a stale historical mirror (2009–
2013), not the real current data. Running this on your own machine, with
normal internet access, is what turns it into something you can actually
use.

Read `docs/WP0-feasibility.md`, `docs/WP1-registry.md`, and `docs/WP2-geo.md`
for the full reasoning — this README is just the "how do I run it" summary.

## 1. Setup

```bash
cd rent-tool
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

`duckdb`, `geopandas`, and `shapely` in requirements.txt are optional
upgrades — the pipeline runs on `sqlite3` + `pandas`/`numpy` (stdlib +
what's already proven to work) by default. `pdfplumber` is required for the
real DHCR PDF parse.

## 2. Fetch the real data (the step that was blocked)

Download each file below and save it to the exact path shown, then re-run
the corresponding script — every script auto-detects these paths.

| # | What | URL | Save to |
|---|------|-----|---------|
| 1 | DHCR Brooklyn building list (PDF) | [rentguidelinesboard.cityofnewyork.us](https://rentguidelinesboard.cityofnewyork.us/wp-content/uploads/2025/12/2024-DHCR-Bldg-File-Brooklyn.pdf) | `data/raw/2024-DHCR-Bldg-File-Brooklyn.pdf` |
| 2 | nycdb rentstab (2007–2017 unit counts) | [taxbillsnyc.s3.amazonaws.com/joined.csv](https://taxbillsnyc.s3.amazonaws.com/joined.csv) | `data/raw/rentstab_joined.csv` |
| 3 | nycdb rentstab_v2 (2018–2024 unit counts) | [justfix-data/rentstab_counts_from_doffer_2024.csv](https://s3.amazonaws.com/justfix-data/rentstab_counts_from_doffer_2024.csv) | `data/raw/rentstab_v2_counts_2024.csv` |
| 4 | PLUTO (year built, unit count, building class) | [data.cityofnewyork.us PLUTO CSV](https://data.cityofnewyork.us/api/views/64uk-42ks/rows.csv?accessType=DOWNLOAD) | `data/raw/pluto_latest.csv` |
| 5 | Parks Properties | handled automatically — `geo/load_parks.py` fetches `enfh-gkve` directly once you have network access | (auto) |

Item 1 is a large, image-heavy PDF — plan for it to take a few minutes to
parse. Items 2–4 are large CSVs (PLUTO is city-wide, ~800k rows); the
loaders already filter to the target zips/borough so downstream files stay
small.

If any single one of these is inconvenient to get, run everything anyway —
each stage degrades gracefully and the docs say exactly what's missing.

## 3. Run the pipeline

```bash
./run_all.sh
```

or step by step:

```bash
python3 etl/parse_dhcr_pdf.py          # -> data/interim/dhcr_brooklyn_parsed.csv
python3 etl/load_rentstab.py           # -> registry.db: rentstab tables
python3 etl/load_pluto.py              # -> registry.db: pluto table
python3 etl/build_registry.py          # -> data/processed/registry.csv  (THE building registry, WP1's deliverable)

python3 geo/load_parks.py              # -> geo/parks.geojson (real park polygons)
python3 geo/build_geo.py               # -> geo/build_geo_output.json (validation run)
```

## 4. What you get

`data/processed/registry.csv` — one row per BBL in the target Brooklyn
zips, with `on_dhcr_list`, `stab_units_by_year`, `unit_count`, `year_built`,
`building_class`, `is_condo_coop`, `abatement_type`, `stab_unit_trend`. This
is the evidence table the scoring engine (WP5, not yet built) will run
against.

`geo/neighborhoods.geojson` and `geo/parks.geojson` — the eight
neighborhood polygons and nearby park geometry, for the proximity ranking.

**One thing to fix once you have live network access:** `geo/distance.py`
currently returns haversine straight-line distance × a circuity fudge
factor, not real walking-route distance — it says so loudly in its own
docstring, and it will get *relative* rankings roughly right in most of
these neighborhoods but understate distance across barriers like the
Gowanus Canal or the BQE trench. Swapping in a real router (a public OSRM
instance, or `pip install osmnx`) is a contained change — see the "HOW TO
SWAP IN REAL ROUTING LATER" section in that file.

## 5. Known gaps to sanity-check once real data is loaded

These are called out in detail in `docs/WP1-registry.md` and
`docs/WP2-geo.md`, but the short version:

- The starting zip-to-neighborhood mapping in `etl/common.py`
  (`ZIP_NEIGHBORHOOD_NOTES`) is a best-guess crosswalk, not a verified
  geodata join. 11206 in particular is mostly Bushwick, not Williamsburg —
  flagged as low-precision in the code.
- The Fort Greene / Clinton Hill neighborhood-polygon split in
  `geo/neighborhoods.geojson` was drawn from general real-estate
  convention (roughly along Washington Ave), not the official NTA
  cutline — worth eyeballing on a map before trusting it at the block
  level.
- `Domino Park`, `Marsha P. Johnson State Park`, and `Brooklyn Bridge Park`
  are expected to be **absent** from the NYC Parks Dept "Parks Properties"
  dataset (they're run by other authorities) — `geo/load_parks.py` will
  need a small supplementary list for these three anchor parks even after
  the main fetch works.
- `abatement_end_year` is NULL for every row — no DOF/HPD exemption
  end-date source was ever wired up; it's the next thing to add once WP1's
  core join is confirmed against real data.

## Next work packages (not built yet)

Per the [build plan](docs/), WP3 (listings ingestion), WP4 (listing-to-
building matching), WP5 (the actual stabilization-likelihood score), WP6
(ranking/alerting), WP7 (the UI), and WP8 (the manual verification
checklist) still need doing, in that order, once this registry and geo
layer are running on real data.

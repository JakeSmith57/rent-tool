# WP1 — Brooklyn Rent-Stabilized Building Registry

Status: pipeline runs end-to-end and produces `data/processed/registry.csv`
(also loaded into the `registry` table in `data/interim/registry.db`), but
**most primary sources named in the brief could not be fetched from this
sandbox** (egress proxy policy blocks every relevant host — see "What
actually ran" below). Read this whole document, especially the data-quality
section, before scoring anything off `registry.csv`.

## Pipeline (in run order)

| Step | Script | Status |
|---|---|---|
| 1 | `etl/parse_dhcr_pdf.py` | Ran, but against a **synthetic fixture**, not the real DHCR PDF |
| 2 | `etl/load_rentstab.py` | Real nycdb rentstab tables blocked; fell back to the 2002–2013 historical mirror |
| 3 | `etl/load_pluto.py` | Blocked entirely; wrote an empty `pluto` table |
| 4 | `etl/build_registry.py` | Ran successfully; joins steps 1–3 on BBL |

## What actually ran vs. what needs a manual fetch step

**Blocked in this sandbox (confirmed today, 2026-09-19, via
`curl $HTTPS_PROXY/__agentproxy/status` and direct connection tests — all
return `403` "gateway answered 403 to CONNECT / policy denial"):**

- `data.cityofnewyork.us` (NYC Open Data / PLUTO) — blocks `etl/load_pluto.py`
- `s3.amazonaws.com` and `taxbillsnyc.s3.amazonaws.com` (nycdb `rentstab` /
  `rentstab_v2`, the real 2007–2024 unit-count-by-year tables) — blocks
  `etl/load_rentstab.py`'s primary path
- `rentguidelinesboard.cityofnewyork.us` (the real 2024 Brooklyn DHCR
  building-list PDF) — blocks `etl/parse_dhcr_pdf.py`'s primary input
- `apps.hcr.ny.gov` (the HCR Registered Building Search) — blocks the
  acceptance spot-check (see below)
- `raw.githubusercontent.com`, `opendata.arcgis.com`, `nominatim.openstreetmap.org`,
  and other mapping/geocoding hosts tried as alternates — all blocked

**What did work:** `github.com` git-clone (used earlier to pull the
clhenrick DHCR historical mirror into `data/external/dhcr_historical_mirror/`).

**Manual fetch steps a human needs to do** (each script's docstring repeats
these):

1. Download the real 2024 Brooklyn DHCR building list PDF from
   `rentguidelinesboard.cityofnewyork.us/resources/rent-stabilized-building-lists/`,
   save to `data/raw/2024-DHCR-Bldg-File-Brooklyn.pdf`, re-run
   `python3 etl/parse_dhcr_pdf.py`. **This is the single highest-priority
   fetch** — it is the only source that is a *current* stabilized-unit
   list; everything else in this run is either empty or years stale.
2. Download PLUTO from `data.cityofnewyork.us/api/views/64uk-42ks/rows.csv?accessType=DOWNLOAD`,
   save to `data/raw/pluto_latest.csv`, re-run `python3 etl/load_pluto.py`.
3. Download `https://taxbillsnyc.s3.amazonaws.com/joined.csv` and
   `https://s3.amazonaws.com/justfix-data/rentstab_counts_from_doffer_2024.csv`,
   save to `data/raw/rentstab_joined.csv` and
   `data/raw/rentstab_v2_counts_2024.csv`, re-run
   `python3 etl/load_rentstab.py`.
4. Run the HCR acceptance spot-check by hand (see below).

After any of steps 1–3, re-run `python3 etl/build_registry.py` to rebuild
`registry.csv` — no other code changes needed.

### On the DuckDB requirement

The brief calls for DuckDB. `pip install duckdb` failed in this sandbox
(the restricted PyPI mirror does not carry the `duckdb` wheel). All ETL
scripts use Python's stdlib `sqlite3` instead, with plain ANSI SQL and the
same table design, so swapping `common.get_connection()` to
`duckdb.connect(...)` is the only change needed once `duckdb` is
installable. See `etl/common.py` module docstring.

## Row counts at each stage

| Stage | Count |
|---|---|
| `parse_dhcr_pdf.py --self-test`: fixture lines parsed | 8 |
| `parse_dhcr_pdf.py`: unparsed/unattributable lines skipped | 1 |
| `parse_dhcr_pdf.py`: rows after BBL dedup (`dhcr_brooklyn_parsed.csv`) | 6 |
| `load_rentstab.py`: historical-mirror files read (2009/2011/2012/2013) | 4 files, ~46,166 unique BBLs citywide |
| `build_registry.py`: historical-trend rows filtered to Brooklyn (bbl starts `3`) | 16,052 |
| `build_registry.py`: DHCR-current unique BBLs | 6 |
| `build_registry.py`: PLUTO unique BBLs | 0 |
| **Final `registry.csv` rows (unique BBLs)** | **16,058** |
| Final rows with `on_dhcr_list = 1` | 6 |
| Final rows with any PLUTO-sourced field non-null | 0 |
| Final rows with `building_class` non-null (DHCR fallback) | 6 |

**These 6 "on the DHCR list" rows are entirely synthetic** — they come from
`fixtures/sample_dhcr_pdf_lines.txt`, a hand-written file explicitly labeled
"NOT REAL DHCR DATA," used only to prove the parser's logic (grouping,
multi-frontage BBL dedup, flag classification) works. The 16,052 real rows
come only from the 2002–2013 historical-mirror cross-reference.
**Do not present the 6 synthetic rows, or their addresses, as real
buildings.**

## Dedup proof (BBL-level, never address-level)

The fixture deliberately contains two buildings with two street frontages
each (same BBL, two address lines), to prove the parser collapses on BBL
and not on the address string:

```
$ python3 etl/parse_dhcr_pdf.py --self-test
parsed rows (pre-dedup):  8
unparsed/skipped lines:   1
rows after BBL dedup:     6  (removed 2 duplicate-BBL lines)
rows with no derivable bbl (kept, address-only): 0
```

- BBL `3010840045` appears on two lines ("200 7TH AVE" and "202 7TH AVE") →
  collapsed to 1 row.
- BBL `3025190030` appears on two lines ("88 FRANKLIN ST" and
  "90 FRANKLIN ST", with the `421-A`/`J-51` flags on each) → collapsed to 1
  row, with abatement flags unioned rather than dropped.

`etl/build_registry.py` re-asserts this at load time with a `GROUP BY bbl`
over the parsed CSV (logged as "dhcr_brooklyn_parsed.csv: 6 rows read … 0
duplicate-bbl rows … 6 unique-BBL rows") and again with a final
`drop_duplicates(subset=["bbl"])` on the assembled table before writing
`registry.csv`. On this run both re-assertions are no-ops (upstream was
already clean), which is expected — they exist as a safety net for a real
PDF extract that might not dedupe as cleanly as the hand-written fixture.

## Schema (`data/processed/registry.csv`, one row per BBL)

| Column | Type | Source | Notes |
|---|---|---|---|
| `bbl` | text, PK | derived (boro+block+lot) | 10-digit NYC BBL |
| `address` | text | DHCR list, else PLUTO | null for BBLs known only from the historical trend table (no address field there) |
| `on_dhcr_list` | 0/1 | DHCR current-cycle list | **synthetic in this run** — see caveats |
| `stab_units_by_year` | text (JSON) | nycdb `rentstab`/`rentstab_v2` | always null this run — real source blocked |
| `unit_count` | numeric | PLUTO `UnitsRes` (falls back to `UnitsTotal`) | always null this run — PLUTO blocked |
| `year_built` | numeric | PLUTO `YearBuilt` | always null this run |
| `building_class` | text | PLUTO `BldgClass`, else DHCR multiple-dwelling class (A/B) | only the 6 synthetic rows are populated this run |
| `is_condo_coop` | 0/1 | PLUTO condo/co-op flag OR DHCR co-op/condo flag | either source can set true |
| `abatement_type` | text | DHCR flags (421-a, 421-g, J-51) | comma-joined |
| `abatement_end_year` | numeric | *no source available* | **always null** — would need a DOF/HPD tax-exemption end-date table, not fetched this run |
| `stab_unit_trend` | text | `dhcr_historical_trend` (2009/2011/2012/2013 booleans) | e.g. `"registered in historical mirror: 2009,2011,2012,2013"`; a *presence* signal, not a unit-count series |

## Data-quality caveats

1. **The current-cycle DHCR list is synthetic, not real, in this run.**
   `on_dhcr_list`, `building_class`, `is_condo_coop`, and `abatement_type`
   are only populated for 6 rows, and those 6 rows are the hand-written
   test fixture, not real Brooklyn buildings. Until the real 2024 PDF is
   fetched (manual step 1 above) and re-parsed, **the registry has no real
   signal for "is currently on a DHCR list."**
2. **PLUTO is entirely absent.** `unit_count`, `year_built`, and the PLUTO
   half of `building_class`/`is_condo_coop` are null for all 16,058 rows.
   This also means the registry cannot be filtered/joined by the target
   zip codes at the row level (that filter lives in PLUTO's `ZipCode`
   column, which we don't have) — the only geographic filter applied is
   the Brooklyn boro-code digit on the BBL itself.
3. **The historical-mirror data (16,052 of 16,058 rows) is 2002–2013, most
   recently 13 years stale, and is a cross-reference only, per its own
   `SOURCE_README.md`: "it should not be assumed that the DHCR's rent
   stabilized building lists are completely authoritative," registration
   is voluntary, and a building can silently drop off or never appear if
   an owner didn't file. A `stab_unit_trend` value of "registered in
   2009–2013" is evidence the building had stabilized units over a
   decade ago; it is not evidence about today.** `stab_units_by_year` (a
   real unit-count time series) is null everywhere because nycdb's
   `rentstab`/`rentstab_v2` (2007–2024) could not be fetched — this is the
   single highest-value follow-up in `etl/load_rentstab.py`'s docstring.
4. **`abatement_end_year` has no source at all** and is null for every row.
   Downstream scoring should not infer "abatement expired" or "abatement
   active" from this registry without adding a DOF/HPD exemption table.
5. **Zip-to-neighborhood mapping is unverified.** `etl/common.py`'s
   `ZIP_NEIGHBORHOOD_NOTES` documents that at least one starting zip
   (11206) is primarily Bushwick/East Williamsburg, outside the 8 target
   neighborhoods, and several others straddle an out-of-scope neighborhood
   (Boerum Hill in 11217, Red Hook in 11231, Brooklyn Heights/DUMBO in
   11201, Wallabout in 11205). This was meant to be checked against
   PLUTO's neighborhood field, which is unavailable this run.

**Single biggest caveat for downstream scoring:** the registry currently
distinguishes buildings almost entirely by decade-old historical presence,
not by any current-year signal, and carries no unit counts or building
characteristics at all. A scoring engine run on `registry.csv` as it
stands today would effectively be scoring "was this Brooklyn BBL on a DHCR
list any time 2009–2013," which is a much weaker and staler proxy for
"has a rent-stabilized apartment today" than the WP1 design intends.
Fetching the real 2024 DHCR PDF (manual step 1) is the fix that changes
this most.

## Acceptance check: HCR Registered Building Search spot-check

**This could not be run.** `apps.hcr.ny.gov` is blocked by the same egress
proxy policy as every other target host in this sandbox:

```
$ curl -sS --max-time 15 "https://apps.hcr.ny.gov/BuildingSearch/"
curl: (56) CONNECT tunnel failed, response 403
[agent-proxy] ... apps.hcr.ny.gov:443 — connect_rejected (organization policy)
```

A human needs to do this spot-check manually:

1. Open `https://apps.hcr.ny.gov/BuildingSearch/` in a normal browser.
2. Pick 20 known-stabilized addresses spread across the 8 target
   neighborhoods (Greenpoint, Williamsburg, Fort Greene, Clinton Hill,
   Prospect Heights, Park Slope, Carroll Gardens, Cobble Hill) — pre-war
   walk-ups and known 421-a new-construction buildings are good candidates,
   since both categories are likely to be registered.
3. Search each address in the HCR tool and record whether it returns a
   registered building.
4. Once the real DHCR PDF (manual step 1) has been parsed into
   `dhcr_brooklyn_parsed.csv`, cross-check the same 20 addresses' BBLs
   against `registry.csv`'s `on_dhcr_list` column and record the
   agreement rate. **Do not perform this cross-check against the current
   `registry.csv`** — its `on_dhcr_list` values are synthetic, so any
   "match" or "mismatch" against the real HCR site would be meaningless.

This check should be re-run as part of accepting the pipeline once the real
PDF is loaded; it is not safe to sign off on `on_dhcr_list` accuracy without
it.

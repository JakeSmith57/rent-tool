"""
Shared constants and helpers for the WP1 building-registry ETL.

Query engine note
------------------
The brief specifies DuckDB as the intended query engine. In this sandbox,
`pip install duckdb` fails: the local PyPI mirror this environment is
restricted to does not carry the `duckdb` distribution (confirmed with pip,
uv, and direct `pypi.org` requests -- all 403/"no matching distribution").
pandas, numpy, and Python's built-in `sqlite3` ARE available.

To keep the pipeline runnable end-to-end, every script here uses `sqlite3`
(stdlib) as a drop-in relational store with the exact same table/column
design a DuckDB database would use. All SQL is plain ANSI SQL (no
DuckDB-only syntax), and CSV/parquet loading goes through pandas
`to_sql`/`read_sql`, so swapping `get_connection()` below to
`duckdb.connect(...)` is the only change needed once `duckdb` can be
installed. This substitution is called out again in docs/WP1-registry.md.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
DATA_RAW = ROOT / "data" / "raw"
DATA_EXTERNAL = ROOT / "data" / "external"
DATA_INTERIM = ROOT / "data" / "interim"
DATA_PROCESSED = ROOT / "data" / "processed"
DB_PATH = ROOT / "data" / "interim" / "registry.db"

for p in (DATA_RAW, DATA_EXTERNAL, DATA_INTERIM, DATA_PROCESSED):
    p.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Target area
# ---------------------------------------------------------------------------
BROOKLYN_BORO_CODE = 3

# Starting zip set given in the brief -- treated as a hypothesis, not fact.
# See docs/WP1-registry.md "zip crosswalk" section for the verification.
STARTING_ZIPS = {
    "11201", "11205", "11206", "11211", "11215",
    "11217", "11222", "11231", "11238", "11249",
}

# Best-effort primary-neighborhood mapping for each starting zip, based on
# USPS zip boundaries and general NYC neighborhood knowledge (NOT verified
# against a geodata join -- PLUTO's NTA/neighborhood field would be the
# authoritative check, and PLUTO could not be downloaded in this run; see
# docs/WP1-registry.md). Some zips straddle a target neighborhood and an
# adjacent, out-of-scope one -- flagged below.
ZIP_NEIGHBORHOOD_NOTES = {
    "11201": "Brooklyn Heights / DUMBO / Downtown Brooklyn (Cobble Hill is only the SW edge)",
    "11205": "Fort Greene / Clinton Hill (also reaches into Wallabout, out of scope)",
    "11206": "Primarily Bushwick / East Williamsburg -- NOT one of the 8 target neighborhoods; "
             "only its western sliver touches Williamsburg. Kept per brief, flagged as low-precision.",
    "11211": "Williamsburg",
    "11215": "Park Slope",
    "11217": "Fort Greene / Park Slope / Prospect Heights border (Boerum Hill is also in this zip, out of scope)",
    "11222": "Greenpoint",
    "11231": "Carroll Gardens / Cobble Hill / Red Hook (Red Hook is out of scope)",
    "11238": "Prospect Heights / Clinton Hill",
    "11249": "Williamsburg (North Side / waterfront)",
}

# The 9 target neighborhoods, by NAME rather than zip -- used by sources
# that report a StreetEasy-style neighborhood string instead of a zip code
# (e.g. ingest/normalize.py's email-alert path, which only ever sees "in
# <Neighborhood>" text, never a zip). Kept here (not re-typed at the call
# site) for the same reason STARTING_ZIPS is centralized: one source of
# truth for "target area" across every WP. Matching should be
# case-insensitive.
#
# NOTE: originally 8 neighborhoods per the brief; Boerum Hill was added
# 2026-09-19 at the user's explicit request after they included it in their
# StreetEasy saved search (it shares zip 11231 with Cobble Hill/Carroll
# Gardens -- see the zip notes above -- but is otherwise a separate
# neighborhood, not an alias for one of the original 8). This set governs
# the email-alert path only; STARTING_ZIPS (the Apify-path filter, zip-
# based) is untouched by this change and was never zip-scoped to exclude
# 11231's Boerum Hill portion in the first place, so no update was needed
# there for consistency.
TARGET_NEIGHBORHOODS = {
    "Greenpoint",
    "Williamsburg",
    "Fort Greene",
    "Boerum Hill",
    "Clinton Hill",
    "Prospect Heights",
    "Park Slope",
    "Carroll Gardens",
    "Cobble Hill",
}

# Years in the DHCR historical mirror (clhenrick/dhcr-rent-stabilized-data)
# that include BLOCK/LOT and therefore a derivable BBL. 2002 and 2005 in
# that mirror have no block/lot column (address-only) and are excluded from
# BBL-keyed joins; see docs/WP1-registry.md.
HISTORICAL_MIRROR_BBL_YEARS = [2009, 2011, 2012, 2013]


def make_bbl(boro: int | str, block: int | str, lot: int | str) -> str | None:
    """Construct a 10-digit NYC BBL string: B (1) + BLOCK (5) + LOT (4).

    Returns None if any component is missing/non-numeric.
    """
    try:
        b = int(boro)
        blk = int(block)
        lt = int(lot)
    except (TypeError, ValueError):
        return None
    if not (1 <= b <= 5):
        return None
    return f"{b:01d}{blk:05d}{lt:04d}"


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Return a connection to the interim relational store.

    See module docstring: this is sqlite3, standing in for the DuckDB
    connection the brief calls for, because duckdb could not be installed
    in this sandbox.
    """
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

#!/usr/bin/env python3
"""
etl/load_rentstab.py -- load nycdb's `rentstab` / `rentstab_v2` tables
(DOF-tax-bill-derived unit counts by year, keyed on BBL) into the interim
relational store.

SOURCES (per nycdb dataset defs, cloned from github.com/nycdb/nycdb):
    rentstab      -> https://taxbillsnyc.s3.amazonaws.com/joined.csv
                     (uc2007..uc2017 registered-unit counts per BBL)
    rentstab_v2   -> https://s3.amazonaws.com/justfix-data/rentstab_counts_from_doffer_2024.csv
                     (uc2018..uc2024 registered-unit counts per BBL)
    rentstab_summary -> https://taxbillsnyc.s3.amazonaws.com/changes-summary.csv

STATUS IN THIS SANDBOX: BLOCKED. All *.s3.amazonaws.com hosts are rejected
by the egress proxy with a policy 403 (confirmed via
`curl $HTTPS_PROXY/__agentproxy/status`, recentRelayFailures). These are the
authoritative, most-recent (through 2024) BBL-keyed unit-count-by-year
tables and getting them is the single highest-value follow-up for this
pipeline.

MANUAL FETCH STEP FOR A HUMAN
    1. From a network that can reach S3 (or via `nycdb --download rentstab
       rentstab_v2` on a machine with normal internet access), fetch:
         https://taxbillsnyc.s3.amazonaws.com/joined.csv
         https://s3.amazonaws.com/justfix-data/rentstab_counts_from_doffer_2024.csv
    2. Save them to:
         data/raw/rentstab_joined.csv
         data/raw/rentstab_v2_counts_2024.csv
    3. Re-run this script -- it will pick them up automatically.

FALLBACK USED IN THIS RUN: the DHCR historical building-list mirror
(data/external/dhcr_historical_mirror/, from
github.com/clhenrick/dhcr-rent-stabilized-data, itself sourced from a DHCR
FOIL response) is used as a PROXY trend signal: for the years that mirror
carries a derivable BBL (2009, 2011, 2012, 2013 -- see common.py), a
building's presence on that year's registered-building list is recorded as
a boolean. This is NOT the same information as nycdb's uc20xx unit counts
(a headcount of stabilized units) -- it is only "was this BBL registered at
all in that year's DHCR list." It is real historical DHCR data, not
fabricated, but it is a materially weaker signal than rentstab/rentstab_v2
would be, and it stops at 2013. This limitation is repeated in
docs/WP1-registry.md.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    DATA_EXTERNAL,
    DATA_RAW,
    HISTORICAL_MIRROR_BBL_YEARS,
    get_connection,
    make_bbl,
)

RENTSTAB_LOCAL = DATA_RAW / "rentstab_joined.csv"
RENTSTAB_V2_LOCAL = DATA_RAW / "rentstab_v2_counts_2024.csv"
MIRROR_DIR = DATA_EXTERNAL / "dhcr_historical_mirror"


def load_real_rentstab(conn) -> bool:
    """Load the real nycdb rentstab table if a human has dropped the file in."""
    loaded_any = False
    if RENTSTAB_LOCAL.exists():
        df = pd.read_csv(RENTSTAB_LOCAL, dtype=str, low_memory=False)
        df.to_sql("rentstab", conn, if_exists="replace", index=False)
        print(f"loaded real rentstab: {len(df)} rows from {RENTSTAB_LOCAL}")
        loaded_any = True
    else:
        print(f"[skip] {RENTSTAB_LOCAL} not present -- rentstab (2007-2017 uc counts) unavailable.")

    if RENTSTAB_V2_LOCAL.exists():
        df = pd.read_csv(RENTSTAB_V2_LOCAL, dtype=str, low_memory=False)
        df.to_sql("rentstab_v2", conn, if_exists="replace", index=False)
        print(f"loaded real rentstab_v2: {len(df)} rows from {RENTSTAB_V2_LOCAL}")
        loaded_any = True
    else:
        print(f"[skip] {RENTSTAB_V2_LOCAL} not present -- rentstab_v2 (2018-2024 uc counts) unavailable.")

    return loaded_any


# --- historical-mirror fallback -------------------------------------------------

# Per-year column layouts confirmed by direct inspection of the mirror files
# (data/external/dhcr_historical_mirror/*.csv). Only years with BLOCK/LOT
# columns are usable for a BBL join; 2002/2005 in this mirror are address-only.
YEAR_FILES = {
    2009: "dhcr2009.csv",
    2011: "dhcr2011.csv",
    2012: "dhcr2012.csv",
    2013: "dhcr2013.csv",
}


def _bbl_series(df: pd.DataFrame) -> pd.Series:
    return df.apply(
        lambda row: make_bbl(row.get("BORO_CODE"), row.get("BLOCK"), row.get("LOT")),
        axis=1,
    )


def load_historical_mirror(conn) -> pd.DataFrame:
    """Build a bbl x year boolean "was on the DHCR registered list" table."""
    per_year_bbls: dict[int, set[str]] = {}
    for year, fname in YEAR_FILES.items():
        path = MIRROR_DIR / fname
        if not path.exists():
            print(f"[skip] historical mirror file missing: {path}")
            continue
        df = pd.read_csv(path, dtype=str, low_memory=False)
        df.columns = [c.strip().upper() for c in df.columns]
        bbls = _bbl_series(df).dropna()
        per_year_bbls[year] = set(bbls)
        print(f"historical mirror {year}: {len(df)} rows, {bbls.nunique()} unique BBLs")

    all_bbls = sorted(set().union(*per_year_bbls.values())) if per_year_bbls else []
    records = []
    for bbl in all_bbls:
        rec = {"bbl": bbl}
        for year in HISTORICAL_MIRROR_BBL_YEARS:
            rec[f"on_list_{year}"] = bbl in per_year_bbls.get(year, set())
        records.append(rec)

    trend_df = pd.DataFrame.from_records(records)
    if not trend_df.empty:
        trend_df.to_sql("dhcr_historical_trend", conn, if_exists="replace", index=False)
    print(f"wrote dhcr_historical_trend: {len(trend_df)} unique BBLs across {list(per_year_bbls.keys())}")
    return trend_df


def main() -> None:
    conn = get_connection()
    real_loaded = load_real_rentstab(conn)
    load_historical_mirror(conn)
    conn.commit()
    conn.close()
    if not real_loaded:
        print(
            "\nNOTE: proceeding WITHOUT real nycdb rentstab/rentstab_v2 data "
            "(S3 blocked in this sandbox). stab_units_by_year in the final "
            "registry will be a coarse 'on historical DHCR list' boolean per "
            "year (2009/2011/2012/2013), not a true unit-count time series. "
            "See docstring above and docs/WP1-registry.md."
        )


if __name__ == "__main__":
    main()

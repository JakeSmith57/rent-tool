#!/usr/bin/env python3
"""
etl/load_rentstab.py -- load nycdb's `rentstab` / `rentstab_v2` tables
(DOF-tax-bill-derived unit counts by year, keyed on BBL) into the interim
relational store.

SOURCES (per nycdb dataset defs, cloned from github.com/nycdb/nycdb):
    rentstab      -> https://taxbillsnyc.s3.amazonaws.com/joined.csv
                     (2007uc..2017uc registered-unit counts per BBL)
    rentstab_v2   -> https://s3.amazonaws.com/justfix-data/rentstab_counts_from_doffer_2024.csv
                     (2018uc..2024uc registered-unit counts per BBL)
    rentstab_summary -> https://taxbillsnyc.s3.amazonaws.com/changes-summary.csv

REAL SCHEMA (2026-09-20, confirmed against the user's actual loaded files --
46,461 rentstab rows / 48,279 rentstab_v2 rows). An earlier version of this
file guessed BOTH of these wrong, which made the whole melt a silent no-op:
    ucbbl       - the BBL column. NOT "bbl". The earlier code looked for a
                  column whose .lower() == "bbl", found none, printed a
                  [warn] and returned an empty frame.
    <year>uc    - unit-count columns are YEAR-FIRST ("2007uc"), not
                  "uc2007" as the earlier UC_YEAR_COL_RE assumed. Even with
                  the bbl column fixed, zero year columns would have matched.
    <year>est / <year>dhcr / <year>abat - sibling per-year columns that must
                  NOT be picked up as unit counts; the regex is anchored so
                  only the "uc" suffix matches.

MANUAL FETCH STEP FOR A HUMAN
    1. From a network that can reach S3 (or via `nycdb --download rentstab
       rentstab_v2` on a machine with normal internet access), fetch:
         https://taxbillsnyc.s3.amazonaws.com/joined.csv
         https://s3.amazonaws.com/justfix-data/rentstab_counts_from_doffer_2024.csv
    2. Save them to:
         data/raw/rentstab_joined.csv
         data/raw/rentstab_v2_counts_2024.csv
    3. Re-run this script -- it will pick them up automatically.

FALLBACK USED WHEN THE REAL FILES ARE ABSENT: the DHCR historical
building-list mirror (data/external/dhcr_historical_mirror/, from
github.com/clhenrick/dhcr-rent-stabilized-data, itself sourced from a DHCR
FOIL response) is used as a PROXY trend signal: for the years that mirror
carries a derivable BBL (2009, 2011, 2012, 2013 -- see common.py), a
building's presence on that year's registered-building list is recorded as
a boolean. This is NOT the same information as nycdb's unit counts (a
headcount of stabilized units) -- it is only "was this BBL registered at
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


import re

# Unit-count columns, matched case-insensitively. BOTH orderings are
# accepted: the real nycdb exports use YEAR-FIRST ("2007uc"), which is what
# actually ships today; "uc2007" is kept as a tolerated alternate in case a
# future vintage flips it. Anchored at both ends so the sibling per-year
# columns "<year>est" / "<year>dhcr" / "<year>abat" can never match.
UC_YEAR_COL_RE = re.compile(r"^(?:uc(?P<y1>\d{4})|(?P<y2>\d{4})uc)$", re.IGNORECASE)

# Accepted BBL column names, lowercased, in priority order. "ucbbl" is what
# the real files use; "bbl" is kept for the synthetic fixtures and for any
# pre-normalized frame.
BBL_COL_CANDIDATES = ("ucbbl", "bbl")


def _match_year(col: str) -> str | None:
    m = UC_YEAR_COL_RE.match(col)
    if not m:
        return None
    return m.group("y1") or m.group("y2")


def _melt_unit_counts(df: pd.DataFrame) -> pd.DataFrame:
    """Reshape a wide rentstab-style frame (one unit-count column per year)
    into long (bbl, year, unit_count) rows, one per BBL x year with a
    non-null count.

    Column naming is matched against the REAL nycdb schema -- see the module
    docstring's "REAL SCHEMA" note for the two names an earlier version of
    this function guessed wrong, and why the failure was silent.

    The BBL column is normalized (whitespace stripped, a trailing ".0"
    float-string artifact stripped, left-padded to 10 digits when the result
    is all-digits and shorter than that) so it matches make_bbl()'s own
    10-digit boro+block+lot format. Rows with no derivable bbl (null, blank,
    or the literal string "nan") are dropped. Duplicate (bbl, year) rows
    within this frame are collapsed by taking the MAX unit_count, not an
    arbitrary pick.
    """
    empty = pd.DataFrame(columns=["bbl", "year", "unit_count"])

    lowered = {c.lower(): c for c in df.columns}
    bbl_col = next((lowered[name] for name in BBL_COL_CANDIDATES if name in lowered), None)
    if bbl_col is None:
        print(
            "[warn] no bbl column found in rentstab-style frame (looked for "
            f"{', '.join(BBL_COL_CANDIDATES)}; saw {list(df.columns)[:8]}...) "
            "-- skipping unit-count melt."
        )
        return empty
    if bbl_col != "bbl":
        df = df.rename(columns={bbl_col: "bbl"})

    year_cols = {}
    for c in df.columns:
        y = _match_year(c)
        if y is not None:
            year_cols[c] = y
    if not year_cols:
        print(
            "[warn] no per-year unit-count columns found in rentstab-style "
            f"frame (saw {list(df.columns)[:8]}...) -- skipping unit-count melt."
        )
        return empty

    bbl_norm = df["bbl"].astype(str).str.strip()
    # A real nycdb export can carry a BBL as a float-like string (pandas
    # read it numerically upstream, e.g. "3000010001.0"); strip a trailing
    # ".0" and left-pad an all-digit result to the canonical 10-digit
    # boro+block+lot format so it matches make_bbl()'s own output. Don't be
    # overly clever: a value that still isn't a valid 10-digit BBL after
    # this cleanup is left as-is -- it just won't match anything downstream.
    bbl_norm = bbl_norm.str.replace(r"\.0$", "", regex=True)
    bbl_norm = bbl_norm.where(
        ~(bbl_norm.str.isdigit() & (bbl_norm.str.len() < 10)),
        bbl_norm.str.zfill(10),
    )

    long_rows = []
    for col, year in year_cols.items():
        counts = pd.to_numeric(df[col], errors="coerce")
        sub = pd.DataFrame({"bbl": bbl_norm, "year": int(year), "unit_count": counts})
        # Drop rows with no derivable bbl (null, blank, or the literal
        # string "nan" that df["bbl"].astype(str) can produce on some
        # pandas versions for a null bbl) before this year's rows are
        # appended, so a bogus BBL never propagates into
        # rentstab_unit_counts / the registry union.
        sub = sub[
            sub["bbl"].notna()
            & (sub["bbl"].str.strip() != "")
            & (sub["bbl"].str.lower() != "nan")
            & sub["unit_count"].notna()
        ]
        long_rows.append(sub)
    if not long_rows:
        return empty
    long_df = pd.concat(long_rows, ignore_index=True)
    # Dedup (bbl, year) deterministically: if the same BBL/year somehow
    # appears more than once within this frame (e.g. a duplicate uc-column
    # or duplicate source row), keep the MAX unit_count rather than an
    # arbitrary "last row wins" pick.
    long_df = long_df.groupby(["bbl", "year"], as_index=False)["unit_count"].max()
    return long_df


def load_real_rentstab(conn) -> bool:
    """Load the real nycdb rentstab table(s) if a human has dropped the
    file(s) in, AND reshape them into a unified long-format
    `rentstab_unit_counts` (bbl, year, unit_count) table that
    build_registry.py actually consumes. Without this reshape step, having
    the raw wide CSVs loaded was a no-op downstream -- this was itself a
    bug (see docs/WP1-registry.md and the improvement report): the loader
    could ingest the real files, but build_registry.py never read
    `rentstab`/`rentstab_v2` at all and hardcoded stab_units_by_year=None
    unconditionally.
    """
    loaded_any = False
    long_frames = []

    if RENTSTAB_LOCAL.exists():
        df = pd.read_csv(RENTSTAB_LOCAL, dtype=str, low_memory=False)
        df.to_sql("rentstab", conn, if_exists="replace", index=False)
        print(f"loaded real rentstab: {len(df)} rows from {RENTSTAB_LOCAL}")
        long_frames.append(_melt_unit_counts(df))
        loaded_any = True
    else:
        print(f"[skip] {RENTSTAB_LOCAL} not present -- rentstab (2007-2017 unit counts) unavailable.")

    if RENTSTAB_V2_LOCAL.exists():
        df = pd.read_csv(RENTSTAB_V2_LOCAL, dtype=str, low_memory=False)
        df.to_sql("rentstab_v2", conn, if_exists="replace", index=False)
        print(f"loaded real rentstab_v2: {len(df)} rows from {RENTSTAB_V2_LOCAL}")
        long_frames.append(_melt_unit_counts(df))
        loaded_any = True
    else:
        print(f"[skip] {RENTSTAB_V2_LOCAL} not present -- rentstab_v2 (2018-2024 unit counts) unavailable.")

    if long_frames:
        combined = pd.concat(long_frames, ignore_index=True)
        # rentstab and rentstab_v2 overlap in no years by construction
        # (2007-2017 vs 2018-2024), but dedupe defensively on (bbl, year)
        # in case a future vintage re-covers an earlier year. Take the MAX
        # unit_count for any (bbl, year) that appears more than once,
        # rather than an arbitrary "last row wins" pick (drop_duplicates
        # keep="last" could silently prefer a zero count over a real
        # nonzero one depending on concat order).
        combined = combined.groupby(["bbl", "year"], as_index=False)["unit_count"].max()

        # LOUD FAILURE (2026-09-20): if the melt produced nothing, say so and
        # stop -- do NOT write an empty table and carry on. The previous
        # version fell through to a summary print that called
        # int(combined["year"].min()) on an empty frame, raising ValueError
        # AFTER the raw to_sql calls but BEFORE main()'s conn.commit() -- so
        # the whole run was rolled back and `rentstab_unit_counts` simply
        # never appeared, while `rentstab`/`rentstab_v2` (committed by an
        # earlier run) looked fine. That is exactly how two wrong column-name
        # guesses stayed invisible: the [warn] lines scrolled past, the table
        # went missing rather than empty, and build_registry.py's own
        # try/except treated the missing table as "real data not fetched yet"
        # and silently emitted stab_units_by_year=None for every row.
        if combined.empty:
            raise RuntimeError(
                "rentstab unit-count melt produced 0 rows despite source files "
                "being present. This almost always means the source schema "
                "changed -- check the [warn] lines above for the column names "
                "actually seen, and update UC_YEAR_COL_RE / BBL_COL_CANDIDATES "
                "at the top of this file. Refusing to write an empty "
                "rentstab_unit_counts table, because build_registry.py cannot "
                "tell an empty one from a never-fetched one."
            )

        combined.to_sql("rentstab_unit_counts", conn, if_exists="replace", index=False)
        print(
            f"wrote rentstab_unit_counts: {len(combined)} (bbl, year) rows, "
            f"{combined['bbl'].nunique()} unique BBLs, years "
            f"{int(combined['year'].min())}-{int(combined['year'].max())}"
        )

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
            "\nNOTE: proceeding WITHOUT real nycdb rentstab/rentstab_v2 data. "
            "stab_units_by_year in the final registry will be a coarse 'on "
            "historical DHCR list' boolean per year (2009/2011/2012/2013), "
            "not a true unit-count time series. See docstring above and "
            "docs/WP1-registry.md."
        )


if __name__ == "__main__":
    main()

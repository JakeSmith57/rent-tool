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

import re
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

# The two nycdb vintages disagree on column naming, confirmed by direct
# inspection of the real files' headers:
#   rentstab_joined.csv (2007-2017):        "2007uc" .. "2017uc"  (year FIRST)
#   rentstab_v2_counts_2024.csv (2018-2024): "uc2018" .. "uc2024"  (uc FIRST)
# Match both orders. The alternation means the year lands in group 1 or
# group 2 depending on which form matched -- see _uc_year() below.
# Deliberately anchored so sibling columns in the v1 file that share the
# year prefix ("2007est", "2007dhcr", "2007abat") are NOT picked up.
UC_YEAR_COL_RE = re.compile(r"^(?:uc(\d{4})|(\d{4})uc)$", re.IGNORECASE)

# Both real files key on "ucbbl", not "bbl". Try the known names first,
# then fall back to any column ending in "bbl" so a future vintage that
# renames it again still joins instead of silently melting to nothing.
BBL_COL_CANDIDATES = ("bbl", "ucbbl")


def _uc_year(match: re.Match) -> str:
    """Return the 4-digit year from whichever alternation branch matched."""
    return match.group(1) or match.group(2)


def _find_bbl_col(df: pd.DataFrame) -> str | None:
    lower = {c.lower(): c for c in df.columns}
    for cand in BBL_COL_CANDIDATES:
        if cand in lower:
            return lower[cand]
    return next((c for c in df.columns if c.lower().endswith("bbl")), None)


def _melt_unit_counts(df: pd.DataFrame) -> pd.DataFrame:
    """Reshape a wide rentstab-style frame (one uc20XX column per year) into
    long (bbl, year, unit_count) rows, one per BBL x year with a non-null
    count. Column names are matched case-insensitively against BOTH of
    nycdb's year-column spellings -- `NNNNuc` (rentstab: 2007uc..2017uc)
    and `ucNNNN` (rentstab_v2: uc2018..uc2024) -- which differ between the
    two real files; see module docstring for sources. The BBL column is
    found by _find_bbl_col() ("bbl"/"ucbbl", else any *bbl column); it is normalized (whitespace stripped, a
    trailing ".0" float-string artifact stripped, left-padded to 10 digits
    when the result is all-digits and shorter than that) so it matches
    make_bbl()'s own 10-digit boro+block+lot format. Rows with no derivable
    bbl (null, blank, or the literal string "nan") are dropped. Duplicate
    (bbl, year) rows within this frame are collapsed by taking the MAX
    unit_count, not an arbitrary pick.
    """
    bbl_col = _find_bbl_col(df)
    if bbl_col is None:
        print(
            "[warn] no bbl/ucbbl column found in rentstab-style frame "
            f"(columns seen: {list(df.columns)[:8]}...) -- skipping unit-count melt."
        )
        return pd.DataFrame(columns=["bbl", "year", "unit_count"])
    if bbl_col != "bbl":
        df = df.rename(columns={bbl_col: "bbl"})

    year_cols = {c: _uc_year(m) for c in df.columns if (m := UC_YEAR_COL_RE.match(c))}
    if not year_cols:
        print("[warn] no ucNNNN year columns found in rentstab-style frame -- skipping unit-count melt.")
        return pd.DataFrame(columns=["bbl", "year", "unit_count"])

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
        return pd.DataFrame(columns=["bbl", "year", "unit_count"])
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
        print(f"[skip] {RENTSTAB_LOCAL} not present -- rentstab (2007-2017 uc counts) unavailable.")

    if RENTSTAB_V2_LOCAL.exists():
        df = pd.read_csv(RENTSTAB_V2_LOCAL, dtype=str, low_memory=False)
        df.to_sql("rentstab_v2", conn, if_exists="replace", index=False)
        print(f"loaded real rentstab_v2: {len(df)} rows from {RENTSTAB_V2_LOCAL}")
        long_frames.append(_melt_unit_counts(df))
        loaded_any = True
    else:
        print(f"[skip] {RENTSTAB_V2_LOCAL} not present -- rentstab_v2 (2018-2024 uc counts) unavailable.")

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
        if combined.empty:
            # Guard the summary print below: min()/max() on an empty frame
            # yields NaN and int(NaN) raises, which would turn "no usable
            # rows" into an opaque ValueError traceback.
            print(
                "[warn] rentstab files loaded but produced 0 usable (bbl, year) "
                "unit-count rows -- check that the CSVs carry a bbl column and "
                "ucNNNN year columns. Leaving rentstab_unit_counts unwritten."
            )
        else:
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
            "\nNOTE: proceeding WITHOUT real nycdb rentstab/rentstab_v2 data "
            "(S3 blocked in this sandbox). stab_units_by_year in the final "
            "registry will be a coarse 'on historical DHCR list' boolean per "
            "year (2009/2011/2012/2013), not a true unit-count time series. "
            "See docstring above and docs/WP1-registry.md."
        )


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
etl/load_pluto.py -- load the PLUTO / MapPLUTO columns needed for the
registry (year built, unit count, building class, ownership/condo flag),
filtered to the target Brooklyn zip codes.

SOURCE (per nycdb's pluto_latest.yml, cloned from github.com/nycdb/nycdb):
    https://data.cityofnewyork.us/api/views/64uk-42ks/rows.csv?accessType=DOWNLOAD
    (NYC Open Data / Socrata -- "Primary Land Use Tax Lot Output (PLUTO)")

STATUS IN THIS SANDBOX: BLOCKED. data.cityofnewyork.us (and every other
nyc.gov / arcgis / socrata host tried) is rejected by the egress proxy with
a policy 403. This is citywide, ~800k-row, non-github-hosted data with no
git-clonable mirror found within the accessible hosts (github.com git-clone
worked for small repos; the mirrors named in the brief -- talos and
clhenrick -- do not carry PLUTO itself, only DHCR building lists).

MANUAL FETCH STEP FOR A HUMAN
    1. Download PLUTO (CSV) for Brooklyn or citywide from either:
         https://data.cityofnewyork.us/api/views/64uk-42ks/rows.csv?accessType=DOWNLOAD
       or the NYC Planning MapPLUTO page (shapefile/csv):
         https://www.nyc.gov/site/planning/data-maps/open-data/dwn-pluto-mappluto.page
    2. Save it to: data/raw/pluto_latest.csv
    3. Re-run this script.

IMPACT: without PLUTO, the final registry has NO real values for
unit_count, year_built, building_class, or is_condo_coop except where the
DHCR list itself states a multiple-dwelling class or co-op/condo flag (a
partial, address-list-derived substitute, not lot-level PLUTO truth). This
is flagged as the top data-quality caveat in docs/WP1-registry.md.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import BROOKLYN_BORO_CODE, DATA_RAW, STARTING_ZIPS, get_connection, make_bbl  # noqa: E402

PLUTO_LOCAL = DATA_RAW / "pluto_latest.csv"

# Columns we need out of PLUTO's ~90, using the nycdb pluto_latest.yml names.
NEEDED_COLUMNS = [
    "Borough", "Block", "Lot", "Address", "ZipCode", "YearBuilt",
    "UnitsRes", "UnitsTotal", "BldgClass", "OwnerType", "CondoNo",
    "XCoord", "YCoord",
]

BORO_NAME_TO_CODE = {"BK": 3, "BROOKLYN": 3}


def main() -> None:
    conn = get_connection()

    if not PLUTO_LOCAL.exists():
        print(
            f"PLUTO not found at {PLUTO_LOCAL}. This source could not be "
            "downloaded automatically (data.cityofnewyork.us is blocked by "
            "the egress proxy policy in this sandbox). Creating an EMPTY "
            "`pluto` table so build_registry.py can still run (all PLUTO-"
            "sourced fields will be NULL downstream). See the manual fetch "
            "step in this file's docstring and docs/WP1-registry.md."
        )
        empty = pd.DataFrame(
            columns=[
                "bbl", "address", "zipcode", "year_built", "units_res",
                "units_total", "bldg_class", "is_condo_coop",
            ]
        )
        empty.to_sql("pluto", conn, if_exists="replace", index=False)
        conn.commit()
        conn.close()
        return

    # Real-data path (exercised once a human drops the CSV in place).
    usecols = None  # let pandas sniff header names; PLUTO column casing varies by vintage
    df = pd.read_csv(PLUTO_LOCAL, low_memory=False)
    df.columns = [c.strip() for c in df.columns]

    # Normalize whichever casing this PLUTO vintage uses.
    colmap = {c.lower(): c for c in df.columns}

    def col(*names):
        for n in names:
            if n.lower() in colmap:
                return colmap[n.lower()]
        return None

    # Column names below were widened after seeing a real export from this
    # Socrata endpoint: it uses a different vintage of PLUTO's schema than
    # nycdb's pluto_latest.yml (which is what the original names came from)
    # -- lowercase throughout, "Tax block"/"Tax lot" instead of "Block"/
    # "Lot", "postcode" instead of "ZipCode", and a `borocode` numeric
    # column alongside the text `borough` column. It also ships a ready-
    # made `BBL` column, which is used directly below instead of
    # reconstructing one from borough+block+lot (more robust: it's exactly
    # what NYC Planning computed, not a guess from our own make_bbl()).
    c_boro = col("Borough", "borough")
    c_borocode = col("borocode", "boro_code")
    c_block = col("Block", "block", "Tax block", "tax block", "taxblock")
    c_lot = col("Lot", "lot", "Tax lot", "tax lot", "taxlot")
    c_bbl_direct = col("BBL", "bbl")
    c_addr = col("Address", "address")
    c_zip = col("ZipCode", "zipcode", "PostCode", "postcode")
    c_year = col("YearBuilt", "yearbuilt")
    c_ures = col("UnitsRes", "unitsres")
    c_utot = col("UnitsTotal", "unitstotal")
    c_bclass = col("BldgClass", "bldgclass")
    c_ownertype = col("OwnerType", "ownertype")
    c_condo = col("CondoNo", "condono", "condo_no")

    missing = [
        name for name, c in [
            ("borough/borocode", c_boro or c_borocode),
            ("block", c_block if c_bbl_direct is None else "ok"),
            ("lot", c_lot if c_bbl_direct is None else "ok"),
        ] if c is None
    ]
    if missing:
        raise SystemExit(
            f"load_pluto.py: could not find required column(s) {missing} in "
            f"{PLUTO_LOCAL}. Actual columns found: {list(df.columns)}. "
            "The Socrata export's schema may have changed again -- update "
            "the `col(...)` alias lists above to match."
        )

    out = pd.DataFrame()
    if c_borocode is not None:
        out["boro_code"] = pd.to_numeric(df[c_borocode], errors="coerce").astype("Int64").astype(str)
    else:
        boro_raw = df[c_boro].astype(str).str.upper().str.strip()
        out["boro_code"] = boro_raw.map(lambda v: BORO_NAME_TO_CODE.get(v, v)).astype(str)

    if c_bbl_direct is not None:
        # Use PLUTO's own computed BBL rather than rebuilding one.
        out["bbl"] = pd.to_numeric(df[c_bbl_direct], errors="coerce").astype("Int64").astype(str)
        out.loc[out["bbl"] == "<NA>", "bbl"] = None
    else:
        out["bbl"] = [
            make_bbl(b, blk, lt)
            for b, blk, lt in zip(out["boro_code"], df[c_block], df[c_lot])
        ]
    out["address"] = df[c_addr] if c_addr else None
    # postcode/ZipCode often reads in as a float (11215.0) when the column
    # has any blanks -- go through numeric first so we don't zero-pad a
    # literal ".0" onto every zip code.
    if c_zip:
        zip_numeric = pd.to_numeric(df[c_zip], errors="coerce")
        out["zipcode"] = zip_numeric.astype("Int64").astype(str).str.zfill(5)
        out.loc[zip_numeric.isna(), "zipcode"] = None
    else:
        out["zipcode"] = None
    out["year_built"] = pd.to_numeric(df[c_year], errors="coerce") if c_year else None
    out["units_res"] = pd.to_numeric(df[c_ures], errors="coerce") if c_ures else None
    out["units_total"] = pd.to_numeric(df[c_utot], errors="coerce") if c_utot else None
    out["bldg_class"] = df[c_bclass] if c_bclass else None
    condo_flag = pd.to_numeric(df[c_condo], errors="coerce").fillna(0) > 0 if c_condo else False
    ownertype_flag = df[c_ownertype].isin(["C", "X"]) if c_ownertype else False
    out["is_condo_coop"] = condo_flag | ownertype_flag

    # Filter to Brooklyn + the (to-be-verified) target zip set.
    before = len(out)
    out = out[
        (out["boro_code"].astype(str) == str(BROOKLYN_BORO_CODE))
        & (out["zipcode"].isin(STARTING_ZIPS))
    ]
    print(f"PLUTO: {before} total rows -> {len(out)} rows in Brooklyn target zips")

    out.to_sql("pluto", conn, if_exists="replace", index=False)
    conn.commit()
    conn.close()
    print(f"loaded pluto: {len(out)} rows")


if __name__ == "__main__":
    main()

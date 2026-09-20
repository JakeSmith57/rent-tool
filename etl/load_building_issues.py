#!/usr/bin/env python3
"""
etl/load_building_issues.py -- WP1 extension (2026-09-20 improvement work,
competitive-feature-gap track): load NYC HPD Housing Maintenance Code
violation records, keyed to BBL, as a building-safety signal alongside the
existing rent-stabilization confidence score.

WHY THIS EXISTS: the deep-research improvement report's comparable_tools.md
research note found that every well-known adjacent tool (RentReboot,
Augrented, JustFix/RentHistory) pairs a stabilization or affordability
signal with a building-condition/risk signal (HPD violations, DOB
complaints, 311 records) -- this tool had a stabilization score but no
equivalent building-condition signal at all. Housing-code violations are
free, official, bulk-downloadable NYC Open Data (unlike DHCR's PDF-only
list), so this is a genuinely easy addition once the CSV is in hand.

SOURCE: NYC Open Data / Socrata -- "Housing Maintenance Code Violations"
    https://data.cityofnewyork.us/resource/wvxf-dwi5.json (API)
    https://data.cityofnewyork.us/Housing-Development/Housing-Maintenance-Code-Violations/wvxf-dwi5
Real confirmed schema fields (per NYC Open Data's published column list --
NOT independently re-verified against a live pull in this sandbox, since
data.cityofnewyork.us is blocked here the same as every other Socrata host
used elsewhere in this pipeline; verify column names against whatever CSV
you actually download and update the `col(...)` aliasing below if they've
drifted, same pattern etl/load_pluto.py already uses for this exact
reason):
    violationid, buildingid, registrationid, boroid, boro, housenumber,
    streetname, zip, block, lot, apartment, story, class (A/B/C severity,
    C = most hazardous/immediately hazardous), inspectiondate,
    approveddate, violationstatus ("Open"/"Close"), currentstatus,
    novdescription.

STATUS IN THIS SANDBOX: BLOCKED, same as PLUTO -- data.cityofnewyork.us is
rejected by the egress proxy with a policy 403.

MANUAL FETCH STEP FOR A HUMAN
    1. Download Housing Maintenance Code Violations, filtered to Brooklyn
       if possible (boro=3) to keep the file small, from:
         https://data.cityofnewyork.us/resource/wvxf-dwi5.csv?boro=BROOKLYN&$limit=500000
       or the full unfiltered export from the dataset's own page:
         https://data.cityofnewyork.us/Housing-Development/Housing-Maintenance-Code-Violations/wvxf-dwi5
    2. Save it to: data/raw/hpd_violations_brooklyn.csv
    3. Re-run this script.

IMPACT: without this file, the registry has no `hpd_*` columns populated
(this script writes an empty table, same fallback pattern as
load_pluto.py), and build_registry.py's building_safety_tier stays
"unknown" for every row -- it degrades gracefully, it doesn't block the
rest of the pipeline.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import BROOKLYN_BORO_CODE, DATA_RAW, get_connection, make_bbl  # noqa: E402

HPD_VIOLATIONS_LOCAL = DATA_RAW / "hpd_violations_brooklyn.csv"

# HPD's own violation-class severity, per its published class definitions:
# A = non-hazardous, B = hazardous, C = immediately hazardous (the class
# used below to build a "serious violation" count, since a raw total count
# mixes trivial and severe issues together and would be a much noisier
# signal than counting just the severe ones).
SEVERE_CLASSES = {"C"}


def main() -> None:
    conn = get_connection()

    if not HPD_VIOLATIONS_LOCAL.exists():
        print(
            f"HPD violations not found at {HPD_VIOLATIONS_LOCAL}. This source "
            "could not be downloaded automatically (data.cityofnewyork.us is "
            "blocked by the egress proxy policy in this sandbox). Creating an "
            "EMPTY `hpd_violations_by_bbl` table so build_registry.py can "
            "still run (building_safety_tier will be 'unknown' for every "
            "row). See the manual fetch step in this file's docstring."
        )
        empty = pd.DataFrame(
            columns=[
                "bbl", "hpd_violation_count_total", "hpd_violation_count_open",
                "hpd_violation_count_class_c", "hpd_violation_count_open_class_c",
            ]
        )
        empty.to_sql("hpd_violations_by_bbl", conn, if_exists="replace", index=False)
        conn.commit()
        conn.close()
        return

    # Real-data path (exercised once a human drops the CSV in place).
    df = pd.read_csv(HPD_VIOLATIONS_LOCAL, low_memory=False, dtype=str)
    df.columns = [c.strip() for c in df.columns]
    colmap = {c.lower(): c for c in df.columns}

    def col(*names):
        for n in names:
            if n.lower() in colmap:
                return colmap[n.lower()]
        return None

    c_boro = col("boroid", "boro_id", "borough")
    c_block = col("block")
    c_lot = col("lot")
    c_bbl_direct = col("bbl")
    c_class = col("class", "violationclass")
    c_status = col("violationstatus", "currentstatus", "status")

    if c_bbl_direct is None and (c_boro is None or c_block is None or c_lot is None):
        raise SystemExit(
            f"load_building_issues.py: could not find enough of "
            f"bbl/(boroid+block+lot) columns in {HPD_VIOLATIONS_LOCAL}. "
            f"Actual columns found: {list(df.columns)}. The Socrata export's "
            "schema may differ from what this script expects -- update the "
            "`col(...)` alias lists above to match."
        )

    out = pd.DataFrame()
    if c_bbl_direct is not None:
        out["bbl"] = pd.to_numeric(df[c_bbl_direct], errors="coerce").astype("Int64").astype(str)
    else:
        out["bbl"] = [
            make_bbl(b, blk, lt) for b, blk, lt in zip(df[c_boro], df[c_block], df[c_lot])
        ]
    out["is_open"] = df[c_status].astype(str).str.strip().str.upper().eq("OPEN") if c_status else False
    out["is_class_c"] = df[c_class].astype(str).str.strip().str.upper().isin(SEVERE_CLASSES) if c_class else False
    out = out[out["bbl"].notna() & (out["bbl"] != "<NA>") & (out["bbl"] != "None")]

    before = len(df)
    print(f"HPD violations: {before} raw rows -> {len(out)} with a derivable bbl")

    agg = out.groupby("bbl").agg(
        hpd_violation_count_total=("bbl", "size"),
        hpd_violation_count_open=("is_open", "sum"),
        hpd_violation_count_class_c=("is_class_c", "sum"),
    ).reset_index()
    # Open AND class C, computed separately since neither of the two
    # single-column aggregates above captures the intersection.
    open_c = out[out["is_open"] & out["is_class_c"]].groupby("bbl").size()
    agg["hpd_violation_count_open_class_c"] = agg["bbl"].map(open_c).fillna(0).astype(int)

    for c in ("hpd_violation_count_total", "hpd_violation_count_open", "hpd_violation_count_class_c"):
        agg[c] = agg[c].astype(int)

    agg.to_sql("hpd_violations_by_bbl", conn, if_exists="replace", index=False)
    conn.commit()
    conn.close()
    print(f"loaded hpd_violations_by_bbl: {len(agg)} unique BBLs with at least one violation on record")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
etl/build_registry.py -- join the DHCR building list, the DHCR historical
mirror trend table, and PLUTO into a single BBL-keyed registry table.

INPUTS (all already produced by the other etl/ scripts; see docs/WP1-registry.md
for what each one actually contains in this run):
    data/interim/dhcr_brooklyn_parsed.csv   -- etl/parse_dhcr_pdf.py output.
        In THIS run this is the output of `--self-test` against the SYNTHETIC
        fixture (fixtures/sample_dhcr_pdf_lines.txt), because the real 2024
        RGB/DHCR Brooklyn PDF could not be downloaded (host blocked by the
        egress proxy policy). It is loaded and joined here exactly as a real
        parse would be -- the join logic does not know or care that the rows
        are synthetic -- but every row in the output is therefore also
        synthetic and MUST NOT be treated as a real building list. See
        docs/WP1-registry.md.
    data/interim/registry.db: dhcr_historical_trend  -- real DHCR data
        (2009/2011/2012/2013 "on the registered-building list" booleans),
        loaded by etl/load_rentstab.py from data/external/dhcr_historical_mirror/.
        Citywide; filtered here to Brooklyn (bbl boro digit '3').
    data/interim/registry.db: pluto  -- etl/load_pluto.py output. EMPTY in
        this run (data.cityofnewyork.us blocked); all PLUTO-sourced columns
        (unit_count, year_built, building_class, is_condo_coop from PLUTO)
        are therefore NULL for every row unless the DHCR list itself
        supplies a fallback value.

OUTPUT SCHEMA (one row per BBL):
    bbl                 TEXT PRIMARY KEY -- 10-digit NYC BBL (boro+block+lot)
    address             TEXT  -- "<bldg_no> <street_name> <street_suffix>" from
                                 the DHCR list if present, else PLUTO address
    on_dhcr_list        INTEGER (0/1) -- BBL appears on the current-cycle DHCR
                                 building list (dhcr_brooklyn_parsed.csv)
    stab_units_by_year  TEXT (JSON) -- {year: unit_count} from real nycdb
                                 rentstab/rentstab_v2 uc columns, if loaded.
                                 NULL when that source is unavailable (this run).
    unit_count          REAL  -- PLUTO UnitsRes (falls back to UnitsTotal)
    year_built          REAL  -- PLUTO YearBuilt
    building_class      TEXT  -- PLUTO BldgClass, else DHCR multiple-dwelling
                                 class (A/B) as a coarser fallback
    is_condo_coop       INTEGER (0/1) -- PLUTO condo/co-op flag OR DHCR
                                 co-op/condo flag (either source can set true)
    abatement_type      TEXT  -- comma-joined abatement codes seen on the DHCR
                                 list (421-a, 421-g, J-51, ...)
    abatement_end_year  REAL  -- always NULL in this run; no DOF/HPD exemption
                                 end-date source was available (see docs)
    stab_unit_trend     TEXT  -- human-readable summary of the
                                 dhcr_historical_trend booleans, e.g.
                                 "registered 2009,2011,2012,2013" or
                                 "not found in historical mirror years"

DEDUP: every join is GROUP BY bbl. parse_dhcr_pdf.py already dedupes its own
output on bbl (never on address) before writing dhcr_brooklyn_parsed.csv;
this script re-asserts that guarantee defensively (see `dedup proof` below)
so a future change to the upstream parser, or a differently-shaped real PDF
extract, cannot silently reintroduce address-keyed duplicate BBL rows here.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import DATA_INTERIM, DATA_PROCESSED, STARTING_ZIPS, get_connection  # noqa: E402

DHCR_PARSED_CSV = DATA_INTERIM / "dhcr_brooklyn_parsed.csv"
OUT_CSV = DATA_PROCESSED / "registry.csv"
OUT_TABLE = "registry"


def load_dhcr_current(conn) -> pd.DataFrame:
    """Load the current-cycle DHCR building list CSV, with a dedup proof."""
    if not DHCR_PARSED_CSV.exists():
        print(f"[warn] {DHCR_PARSED_CSV} missing -- proceeding with an empty DHCR-current table.")
        return pd.DataFrame(
            columns=[
                "bbl", "zip", "bldg_no", "street_name", "street_suffix",
                "multiple_dwelling_class", "is_condo_coop", "abatement_type",
            ]
        )
    df = pd.read_csv(DHCR_PARSED_CSV, dtype=str)
    before = len(df)
    df_bbl = df[df["bbl"].notna()].copy()
    no_bbl = len(df) - len(df_bbl)

    # --- DEDUP PROOF (address string is deliberately never the key) -------
    dup_bbl_rows = int(df_bbl.duplicated(subset=["bbl"]).sum())
    df_bbl = df_bbl.sort_values("bbl")
    grouped = (
        df_bbl.groupby("bbl", as_index=False)
        .agg(
            {
                "zip": "first",
                "bldg_no": "first",
                "street_name": "first",
                "street_suffix": "first",
                "multiple_dwelling_class": lambda s: next((v for v in s if pd.notna(v)), None),
                "is_condo_coop": lambda s: str(int(any(str(v) == "1" for v in s))),
                "abatement_type": lambda s: ",".join(
                    sorted({t for v in s if pd.notna(v) and v for t in str(v).split(",")})
                )
                or None,
            }
        )
    )
    after = len(grouped)
    print(
        f"dhcr_brooklyn_parsed.csv: {before} rows read "
        f"({no_bbl} with no derivable bbl, skipped) -> "
        f"{before - no_bbl} bbl-bearing rows, {dup_bbl_rows} of which shared a "
        f"bbl with another row -> {after} unique-BBL rows after GROUP BY bbl "
        f"(dedup key = bbl, never address). Removed {before - no_bbl - after} "
        "duplicate-bbl rows at this stage "
        f"(parse_dhcr_pdf.py already deduped upstream, so on a clean run "
        "before == after here; this GROUP BY is a defensive re-assertion, "
        "not the primary dedup step)."
    )
    grouped["address"] = (
        grouped["bldg_no"].fillna("").str.strip()
        + " "
        + grouped["street_name"].fillna("").str.strip()
        + " "
        + grouped["street_suffix"].fillna("").str.strip()
    ).str.replace(r"\s+", " ", regex=True).str.strip()
    grouped["on_dhcr_list"] = 1
    return grouped


def load_pluto(conn) -> pd.DataFrame:
    try:
        df = pd.read_sql("SELECT * FROM pluto", conn)
    except Exception:
        df = pd.DataFrame(columns=["bbl", "address", "year_built", "units_res", "units_total", "bldg_class", "is_condo_coop"])
    for col in ("year_built", "units_res", "units_total"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def load_historical_trend(conn) -> pd.DataFrame:
    try:
        df = pd.read_sql("SELECT * FROM dhcr_historical_trend", conn)
    except Exception:
        return pd.DataFrame(columns=["bbl"])
    before = len(df)
    df = df[df["bbl"].astype(str).str.startswith("3")]  # Brooklyn boro digit
    print(f"dhcr_historical_trend: {before} citywide rows -> {len(df)} Brooklyn-boro rows (bbl starts with '3')")
    return df


def summarize_trend(row) -> str:
    year_cols = [c for c in row.index if c.startswith("on_list_")]
    hits = [c.replace("on_list_", "") for c in year_cols if row.get(c) in (1, True, "1")]
    if not year_cols:
        return "no historical-mirror data"
    if hits:
        return "registered in historical mirror: " + ",".join(sorted(hits))
    return "not found in any historical mirror year (2009/2011/2012/2013)"


def main() -> None:
    conn = get_connection()

    dhcr = load_dhcr_current(conn)
    pluto = load_pluto(conn)
    trend = load_historical_trend(conn)

    # Union of every BBL seen anywhere, so a building present only in one
    # source (e.g. only the historical mirror, or only PLUTO) still gets a row.
    all_bbls = pd.Index(
        sorted(
            set(dhcr["bbl"]) | set(pluto["bbl"].dropna() if "bbl" in pluto else [])
            | set(trend["bbl"].dropna() if "bbl" in trend else [])
        )
    )
    reg = pd.DataFrame({"bbl": all_bbls})
    before_union = len(dhcr) + len(pluto.get("bbl", pd.Series(dtype=str)).dropna().unique()) + len(
        trend.get("bbl", pd.Series(dtype=str)).dropna().unique()
    )
    print(
        f"registry union: {len(dhcr)} DHCR-current + "
        f"{pluto['bbl'].dropna().nunique() if 'bbl' in pluto else 0} PLUTO + "
        f"{trend['bbl'].dropna().nunique() if 'bbl' in trend else 0} historical-trend "
        f"unique BBLs -> {len(reg)} unique BBLs in the union "
        f"(sum-of-parts {before_union} vs union {len(reg)} shows overlap collapsed)"
    )

    reg = reg.merge(
        dhcr[["bbl", "address", "on_dhcr_list", "multiple_dwelling_class", "is_condo_coop", "abatement_type"]],
        on="bbl", how="left",
    )
    # NOTE: the column-existence check below must run against the RENAMED
    # frame, not the original `pluto` -- checking `c in pluto.columns` for
    # "pluto_address" was always False (that name only exists after the
    # rename), which silently dropped PLUTO's address on every row.
    pluto_renamed = pluto.rename(columns={"address": "pluto_address"})
    pluto_cols = [
        c for c in ["bbl", "pluto_address", "year_built", "units_res", "units_total", "bldg_class", "is_condo_coop"]
        if c in pluto_renamed.columns
    ]
    reg = reg.merge(
        pluto_renamed[pluto_cols],
        on="bbl", how="left", suffixes=("", "_pluto"),
    )
    reg = reg.merge(trend, on="bbl", how="left")

    reg["on_dhcr_list"] = reg["on_dhcr_list"].fillna(0).astype(int)
    reg["address"] = reg["address"].fillna(reg.get("pluto_address"))
    reg["unit_count"] = reg.get("units_res")
    if "units_total" in reg.columns:
        reg["unit_count"] = reg["unit_count"].fillna(reg["units_total"])
    reg["building_class"] = reg.get("bldg_class")
    if "multiple_dwelling_class" in reg.columns:
        reg["building_class"] = reg["building_class"].fillna(reg["multiple_dwelling_class"])

    is_condo_pluto = reg["is_condo_coop_pluto"] if "is_condo_coop_pluto" in reg.columns else pd.Series(False, index=reg.index)
    is_condo_dhcr = reg["is_condo_coop"].fillna("0").astype(str).isin(["1", "True", "true"])
    reg["is_condo_coop"] = (is_condo_pluto.fillna(False).astype(bool) | is_condo_dhcr).astype(int)

    reg["abatement_end_year"] = pd.NA  # no exemption-end-date source available this run

    trend_year_cols = [c for c in reg.columns if c.startswith("on_list_")]
    reg["stab_unit_trend"] = reg[["bbl"] + trend_year_cols].apply(summarize_trend, axis=1) if trend_year_cols else "no historical-mirror data"

    reg["stab_units_by_year"] = None  # real rentstab/rentstab_v2 not available this run (see docstring)

    final = reg[
        [
            "bbl", "address", "on_dhcr_list", "stab_units_by_year", "unit_count",
            "year_built", "building_class", "is_condo_coop", "abatement_type",
            "abatement_end_year", "stab_unit_trend",
        ]
    ].copy()

    # Final defensive dedup assertion on the primary key.
    pre_final_dedup = len(final)
    final = final.drop_duplicates(subset=["bbl"], keep="first")
    post_final_dedup = len(final)
    print(
        f"final registry table: {pre_final_dedup} rows -> {post_final_dedup} rows after "
        f"drop_duplicates(subset=['bbl']) (removed {pre_final_dedup - post_final_dedup})"
    )

    final = final.sort_values("bbl").reset_index(drop=True)

    DATA_PROCESSED.mkdir(parents=True, exist_ok=True)
    final.to_csv(OUT_CSV, index=False)
    final.to_sql(OUT_TABLE, conn, if_exists="replace", index=False)
    conn.commit()
    conn.close()

    print(f"\nwrote {len(final)} rows to {OUT_CSV} and table '{OUT_TABLE}' in registry.db")
    print(f"on_dhcr_list=1 rows: {int(final['on_dhcr_list'].sum())}")
    print(f"rows with non-null year_built/unit_count/building_class from PLUTO: "
          f"{final['year_built'].notna().sum()} / {final['unit_count'].notna().sum()} / "
          f"{final['building_class'].notna().sum()}")


if __name__ == "__main__":
    main()

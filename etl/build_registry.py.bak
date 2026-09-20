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
                                 rentstab/rentstab_v2 uc columns, if loaded
                                 (via etl/load_rentstab.py's
                                 rentstab_unit_counts table). NULL when that
                                 source is unavailable.
    stab_units_ever_nonzero INTEGER (0/1) -- true if ANY year in
                                 stab_units_by_year has a nonzero count.
                                 Per nycdb's own docs, a year with zero
                                 reported units is common admin noise, not
                                 proof of deregulation -- so this flag (not
                                 "latest year nonzero") is what scoring
                                 should treat as "ever had stabilized units
                                 on record."
    stab_units_latest_year   REAL -- most recent year present in
                                 stab_units_by_year, if any.
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
    stabilization_confidence        TEXT -- one of "high"/"moderate"/"low"/
                                 "unknown". See compute_stabilization_
                                 confidence() docstring below for the tiered
                                 model and its sourcing (ProPublica/Furman
                                 Center/nycdb docs, via the 2026-09-19
                                 improvement research report).
    stabilization_confidence_reason TEXT -- one-line human-readable reason
                                 for the tier above, e.g. "on current DHCR
                                 list" or "pre-1974, 6+ units, not condo/
                                 coop, but no registration or abatement
                                 record found (heuristic only)".

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
        # NOTE: "address" and "on_dhcr_list" are derived columns, added at the
        # end of the happy path below -- but main() selects them off this frame
        # unconditionally, so the empty placeholder MUST declare them too or the
        # missing-CSV path dies with KeyError instead of degrading gracefully.
        return pd.DataFrame(
            columns=[
                "bbl", "zip", "bldg_no", "street_name", "street_suffix",
                "multiple_dwelling_class", "is_condo_coop", "abatement_type",
                "address", "on_dhcr_list",
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


def load_stab_unit_counts(conn) -> pd.DataFrame:
    """Load etl/load_rentstab.py's reshaped `rentstab_unit_counts` table
    (bbl, year, unit_count) and collapse it to one row per BBL:
    stab_units_by_year (JSON dict), stab_units_ever_nonzero (0/1),
    stab_units_latest_year. Empty frame (all-null columns) when the real
    nycdb files haven't been loaded -- see load_rentstab.py's docstring for
    the manual fetch step.
    """
    try:
        df = pd.read_sql("SELECT * FROM rentstab_unit_counts", conn)
    except Exception:
        return pd.DataFrame(columns=["bbl", "stab_units_by_year", "stab_units_ever_nonzero", "stab_units_latest_year"])
    if df.empty:
        return pd.DataFrame(columns=["bbl", "stab_units_by_year", "stab_units_ever_nonzero", "stab_units_latest_year"])

    df["year"] = pd.to_numeric(df["year"], errors="coerce")
    df["unit_count"] = pd.to_numeric(df["unit_count"], errors="coerce")
    records = []
    for bbl, group in df.groupby("bbl"):
        # _melt_unit_counts() already filters to unit_count.notna() rows
        # before this function ever sees them, so unit_count can't actually
        # be null here -- no defensive None branch needed.
        by_year = {int(row.year): float(row.unit_count) for row in group.itertuples()}
        ever_nonzero = any((v or 0) > 0 for v in by_year.values())
        latest_year = max(by_year.keys()) if by_year else None
        records.append(
            {
                "bbl": bbl,
                "stab_units_by_year": json.dumps(by_year, sort_keys=True),
                "stab_units_ever_nonzero": int(ever_nonzero),
                "stab_units_latest_year": latest_year,
            }
        )
    out = pd.DataFrame.from_records(records)
    print(f"rentstab_unit_counts: {df['bbl'].nunique()} unique BBLs with a real unit-count history loaded")
    return out


def load_hpd_violations(conn) -> pd.DataFrame:
    """Load etl/load_building_issues.py's `hpd_violations_by_bbl` table
    (real HPD Housing Maintenance Code violation counts per BBL, if a
    human has dropped the CSV into data/raw/ -- see that script's
    docstring for the manual fetch step). Empty frame (all-null columns
    downstream) when that source hasn't been loaded, same graceful-
    degradation pattern as load_stab_unit_counts() above.
    """
    cols = [
        "bbl", "hpd_violation_count_total", "hpd_violation_count_open",
        "hpd_violation_count_class_c", "hpd_violation_count_open_class_c",
    ]
    try:
        df = pd.read_sql("SELECT * FROM hpd_violations_by_bbl", conn)
    except Exception:
        return pd.DataFrame(columns=cols)
    if df.empty:
        return pd.DataFrame(columns=cols)
    print(f"hpd_violations_by_bbl: {df['bbl'].nunique()} unique BBLs with at least one HPD violation on record")
    return df


def compute_building_safety_tier(row) -> tuple[str, str]:
    """Simple, transparent building-condition signal from real HPD Housing
    Maintenance Code violation counts, alongside (not blended into)
    stabilization_confidence -- these are two independent questions ("is
    this likely stabilized" vs "is this building in bad shape") and a
    renter should be able to reason about each separately rather than have
    them collapsed into one opaque score. Per the improvement research
    (comparable_tools.md), this is the feature category most consistently
    present in every adjacent commercial/nonprofit tool (RentReboot,
    Augrented, JustFix/RentHistory) that this tool previously lacked
    entirely.

    Tiers, based on OPEN violations only (a closed/resolved violation is
    historical, not a current condition problem) and class C (per HPD's
    own severity scale, "immediately hazardous") counted separately from
    the total since a handful of paint/plaster class-A violations reads
    very differently than a handful of class-C ones:
      "concerning"   -- 1+ OPEN class-C (immediately hazardous) violation.
      "some_issues"  -- 0 open class-C, but 3+ OPEN violations of any class.
      "minimal"      -- 0 open class-C, 1-2 open violations.
      "clean_record" -- 0 open violations of any class (may still have
                        historical/closed ones -- this is about CURRENT
                        condition, not lifetime history).
      "unknown"      -- no HPD data loaded for this BBL at all (could mean
                        genuinely zero violations ever, or just that this
                        BBL isn't in the loaded file -- these are NOT
                        distinguished, since an empty result from an outer
                        join looks identical either way; don't read
                        "unknown" as "clean").

    These thresholds (3+ for "some_issues") are a reasonable starting
    point, not a validated statistical cutoff -- no published precision/
    recall study for this kind of tiering was found in the improvement
    research, same caveat that applies to the pre-1974/6+-unit
    stabilization heuristic.
    """
    total = row.get("hpd_violation_count_total")
    open_total = row.get("hpd_violation_count_open")
    open_class_c = row.get("hpd_violation_count_open_class_c")

    if pd.isna(total):
        return "unknown", "no HPD violation data loaded for this BBL (not the same as a clean record -- see docstring)"

    open_total = int(open_total) if pd.notna(open_total) else 0
    open_class_c = int(open_class_c) if pd.notna(open_class_c) else 0

    if open_class_c >= 1:
        return "concerning", f"{open_class_c} open immediately-hazardous (class C) HPD violation(s)"
    if open_total >= 3:
        return "some_issues", f"{open_total} open HPD violations (none class C)"
    if open_total >= 1:
        return "minimal", f"{open_total} open HPD violation(s) (none class C)"
    return "clean_record", "0 open HPD violations on record"


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
    # Unreachable in practice: the only caller (main()) already guards on
    # trend_year_cols before ever calling .apply(summarize_trend, ...), so
    # every row this function sees has at least one on_list_ column.
    year_cols = [c for c in row.index if c.startswith("on_list_")]
    hits = [c.replace("on_list_", "") for c in year_cols if row.get(c) in (1, True, "1")]
    if hits:
        return "registered in historical mirror: " + ",".join(sorted(hits))
    return "not found in any historical mirror year (2009/2011/2012/2013)"


def _has_abatement(abatement_type, *codes) -> bool:
    if not isinstance(abatement_type, str) or not abatement_type:
        return False
    hay = abatement_type.upper()
    return any(code.upper() in hay for code in codes)


def compute_stabilization_confidence(row) -> tuple[str, str]:
    """Tiered stabilization-likelihood score, replacing a single binary
    on_dhcr_list flag with the weighted multi-signal model surfaced by the
    2026-09-19 improvement research (data_sources.md), itself drawn from
    ProPublica's 421-a/J-51 cross-reference investigation and the NYU
    Furman Center's stabilized-stock segmentation report:

      HIGH:
        - on the current DHCR/RGB building list (on_dhcr_list=1) -- the
          only source that's a direct claim of current registration; OR
        - has a 421-a abatement per the DHCR list's own abatement_type
          flags, even if NOT on the list otherwise -- ProPublica found
          ~40% of ~15,000 buildings receiving 421-a/J-51 abatements as of
          2013 were not self-registered with DHCR despite the legal
          obligation, so absence-from-list alone is a weak negative signal
          for an abated building.
      MODERATE:
        - has a J-51 abatement but NOT 421-a and NOT on the list -- J-51
          is scored lower than 421-a per Furman Center's explicit caveat
          that "J-51 is not a long-term affordability program," i.e. a
          J-51-only building's stabilization may have already sunset even
          though the abatement is real; OR
        - has a 421-g abatement but NOT 421-a and NOT on the list -- 421-g
          is a residential conversion/rehab tax abatement (distinct from
          421-a new construction and from J-51 rehab-of-existing-rent-
          regulated-buildings), and buildings receiving it are also
          subject to rent-stabilization requirements for the abatement
          period, similar in spirit to 421-a. It's scored in the SAME
          tier as J-51 (not the high tier with 421-a) because research
          review didn't establish 421-g has 421-a's specific ~40%-
          underregistration evidence behind it -- it's a real abatement
          that implies stabilization but isn't the strongest-evidence
          tier, same treatment as J-51 gets today; OR
        - not on the list, no abatement flags, but
          stab_units_ever_nonzero=1 (a real nycdb rentstab/rentstab_v2
          year with a nonzero registered-unit count) -- per nycdb's own
          docs, a LATER year showing zero is common self-reporting noise,
          not proof of deregulation, so "ever nonzero" is treated as still
          likely stabilized rather than requiring the latest year to be
          nonzero.
      LOW:
        - none of the above, but the building matches the community
          heuristic used by amirentstabilized.com and this tool's own
          original fallback: built before 1974, 6+ units, not a
          co-op/condo. Labeled explicitly as a heuristic-only tier (not
          "moderate") because, per that project's own disclaimer, this
          method has no confirmed precision/recall figure and is not
          apartment-level evidence.
      UNKNOWN:
        - none of the above signals are available (usually because
          year_built/unit_count are null -- PLUTO wasn't loaded this run).

    Returns (tier, human_readable_reason).
    """
    on_list = bool(row.get("on_dhcr_list"))
    abatement = row.get("abatement_type")
    has_421a = _has_abatement(abatement, "421-A", "421A")
    has_j51 = _has_abatement(abatement, "J-51", "J51")
    has_421g = _has_abatement(abatement, "421-G", "421G")
    ever_nonzero = bool(row.get("stab_units_ever_nonzero"))
    latest_year = row.get("stab_units_latest_year")
    year_built = row.get("year_built")
    unit_count = row.get("unit_count")
    is_condo_coop = bool(row.get("is_condo_coop"))

    if on_list and has_421a:
        return "high", "on current DHCR list and has a 421-a abatement"
    if on_list:
        return "high", "on current DHCR list"
    if has_421a:
        return "high", (
            "not on current DHCR list, but has a 421-a abatement (per ProPublica, "
            "~40% of similarly abated buildings weren't self-registered despite "
            "the legal obligation)"
        )
    if has_j51:
        return "moderate", (
            "not on current DHCR list; has a J-51 abatement, which (per NYU Furman "
            "Center) is not a long-term affordability program, so stabilization may "
            "have already sunset"
        )
    if has_421g:
        return "moderate", (
            "not on current DHCR list; has a 421-g abatement (a residential "
            "conversion/rehab abatement distinct from 421-a and J-51), which "
            "implies stabilization for the abatement period but doesn't have "
            "421-a's specific underregistration evidence behind it"
        )
    if ever_nonzero:
        year_note = f", most recently reported in {int(latest_year)}" if pd.notna(latest_year) else ""
        return "moderate", (
            f"not on current DHCR list or abated, but has a nonzero registered-unit "
            f"count in nycdb's rent-stabilization tax-bill data{year_note} (a later "
            "zero-count year is common self-reporting noise, not proof of "
            "deregulation, per nycdb's own documentation)"
        )
    if pd.notna(year_built) and pd.notna(unit_count) and year_built < 1974 and unit_count >= 6 and not is_condo_coop:
        return "low", (
            "heuristic only: built before 1974, 6+ units, not a co-op/condo, but no "
            "DHCR registration, abatement, or nycdb unit-count record found"
        )
    if pd.isna(year_built) or pd.isna(unit_count):
        return "unknown", "no DHCR/abatement/nycdb signal, and PLUTO year-built/unit-count data unavailable to apply the fallback heuristic"
    return "unknown", "no DHCR registration, abatement, or nycdb unit-count signal, and doesn't meet the pre-1974/6+ unit/non-condo fallback heuristic"


def main() -> None:
    conn = get_connection()

    dhcr = load_dhcr_current(conn)
    pluto = load_pluto(conn)
    trend = load_historical_trend(conn)
    stab_counts = load_stab_unit_counts(conn)
    hpd_violations = load_hpd_violations(conn)

    # Union of every BBL seen anywhere, so a building present only in one
    # source (e.g. only the historical mirror, or only PLUTO) still gets a row.
    all_bbls = pd.Index(
        sorted(
            set(dhcr["bbl"]) | set(pluto["bbl"].dropna() if "bbl" in pluto else [])
            | set(trend["bbl"].dropna() if "bbl" in trend else [])
            | set(stab_counts["bbl"].dropna() if "bbl" in stab_counts else [])
        )
    )
    reg = pd.DataFrame({"bbl": all_bbls})
    if reg.empty:
        print(
            "[error] no BBLs found from any source (DHCR-current, PLUTO, "
            "historical-trend, or real nycdb rentstab) -- nothing to build a "
            "registry from. Check that at least one of those inputs is "
            "populated before re-running.",
            file=sys.stderr,
        )
        sys.exit(1)
    before_union = (
        len(dhcr)
        + len(pluto.get("bbl", pd.Series(dtype=str)).dropna().unique())
        + len(trend.get("bbl", pd.Series(dtype=str)).dropna().unique())
        + len(stab_counts.get("bbl", pd.Series(dtype=str)).dropna().unique())
    )
    print(
        f"registry union: {len(dhcr)} DHCR-current + "
        f"{pluto['bbl'].dropna().nunique() if 'bbl' in pluto else 0} PLUTO + "
        f"{trend['bbl'].dropna().nunique() if 'bbl' in trend else 0} historical-trend + "
        f"{stab_counts['bbl'].dropna().nunique() if 'bbl' in stab_counts else 0} real nycdb rentstab "
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
    reg = reg.merge(stab_counts, on="bbl", how="left")
    reg = reg.merge(hpd_violations, on="bbl", how="left")

    reg["on_dhcr_list"] = reg["on_dhcr_list"].fillna(0).astype(int)
    reg["stab_units_ever_nonzero"] = reg.get("stab_units_ever_nonzero", pd.Series(0, index=reg.index)).fillna(0).astype(int)
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

    # stab_units_by_year / stab_units_ever_nonzero / stab_units_latest_year
    # come from the stab_counts merge above (real nycdb rentstab/rentstab_v2
    # data, if a human dropped the CSVs into data/raw/ -- see
    # etl/load_rentstab.py's docstring). Rows with no match there are still
    # correctly null/0, which compute_stabilization_confidence() below
    # handles as "no signal from this source," not an error.
    if "stab_units_by_year" not in reg.columns:
        reg["stab_units_by_year"] = None
    if "stab_units_latest_year" not in reg.columns:
        reg["stab_units_latest_year"] = pd.NA

    confidence = reg.apply(compute_stabilization_confidence, axis=1, result_type="expand")
    reg["stabilization_confidence"] = confidence[0]
    reg["stabilization_confidence_reason"] = confidence[1]

    # hpd_violation_count_* come from the hpd_violations merge above (real
    # HPD data, if a human dropped the CSV into data/raw/ -- see
    # etl/load_building_issues.py's docstring). Rows with no match there
    # are correctly null, which compute_building_safety_tier() treats as
    # "unknown" (see its docstring for why that's NOT the same as "clean").
    safety = reg.apply(compute_building_safety_tier, axis=1, result_type="expand")
    reg["building_safety_tier"] = safety[0]
    reg["building_safety_tier_reason"] = safety[1]

    final = reg[
        [
            "bbl", "address", "on_dhcr_list", "stab_units_by_year",
            "stab_units_ever_nonzero", "stab_units_latest_year", "unit_count",
            "year_built", "building_class", "is_condo_coop", "abatement_type",
            "abatement_end_year", "stab_unit_trend", "stabilization_confidence",
            "stabilization_confidence_reason", "hpd_violation_count_total",
            "hpd_violation_count_open", "hpd_violation_count_class_c",
            "hpd_violation_count_open_class_c", "building_safety_tier",
            "building_safety_tier_reason",
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
    print(f"rows with real nycdb unit-count history (stab_units_ever_nonzero=1): "
          f"{int(final['stab_units_ever_nonzero'].sum())}")
    print("stabilization_confidence distribution:")
    print(final["stabilization_confidence"].value_counts().to_string())
    print("building_safety_tier distribution:")
    print(final["building_safety_tier"].value_counts().to_string())


if __name__ == "__main__":
    main()

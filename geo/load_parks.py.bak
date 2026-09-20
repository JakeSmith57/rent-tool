r"""
WP2 - Parks Properties loader.

Source: NYC Open Data "Parks Properties" (Socrata dataset id enfh-gkve).
    JSON endpoint : https://data.cityofnewyork.us/resource/enfh-gkve.json
    CSV endpoint  : https://data.cityofnewyork.us/resource/enfh-gkve.csv
    Docs          : https://data.cityofnewyork.us/Recreation/Parks-Properties/enfh-gkve

REAL schema fields, confirmed against a live fetch of this dataset
(borough=B, 628 Brooklyn rows returned -- this replaces an earlier version
of this file written only against general knowledge of Socrata's typical
column names, which had two of them wrong):
    objectid        - unique id
    gispropnum      - Parks Dept property number
    signname        - the name shown on park signage (best "display name")
    name311         - name used by 311 (fallback display name)
    address         - street address / cross streets
    borough         - one of 'B','X','M','Q','R' -- **'B' = Brooklyn**, NOT
                       'K' as an earlier version of this file assumed (the
                       standard NYC boro-code convention used elsewhere in
                       this project, e.g. BBLs, uses K=Kings/Brooklyn; this
                       particular Socrata dataset does not follow that
                       convention -- confirmed live: borough=B returned
                       "70 CHAUNCEY STREET" / zipcode 11233, Bed-Stuy).
    typecategory    - e.g. Neighborhood Park, Flagship Park, Nature Area
    acres           - size in acres (NOT "acreage")
    department      - jurisdiction, usually 'DPR' (some listed properties
                       are jointly managed / leased and may show a
                       different department or 'Non-DPR')
    multipolygon    - MultiPolygon geometry, GeoJSON-shaped already
                       (NOT "the_geom" -- that was a guess from an older
                       Socrata convention this dataset doesn't use)

MANUAL FETCH STEP (works from any machine with normal internet access --
this sandbox's own egress is locked to package registries only):
    curl -o geo/parks_raw.json \
      "https://data.cityofnewyork.us/resource/enfh-gkve.json?borough=B&\$limit=5000"
  (this whole docstring is a raw string, so that literal backslash-dollar
  is exactly what to paste into your shell -- no escaping tricks needed.
  Run this from the repo root so the relative output path lands at
  geo/parks_raw.json, not wherever your shell's cwd happens to be.)
  then either:
    python3 geo/load_parks.py --from-file geo/parks_raw.json
  or just:
    python3 geo/load_parks.py
  which will attempt the live fetch itself first and fall back to
  --from-file's cached copy, then to placeholder points, in that order.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

SOCRATA_JSON = "https://data.cityofnewyork.us/resource/enfh-gkve.json"
HERE = os.path.dirname(os.path.abspath(__file__))
NEIGHBORHOODS_PATH = os.path.join(HERE, "neighborhoods.geojson")
OUT_PATH = os.path.join(HERE, "parks.geojson")

# Anchor parks the task asks us to confirm. Names are matched loosely
# (case-insensitive substring) against signname/name311 once we have live
# data. Notes below record what we know about each park's jurisdiction
# from general knowledge -- this determines whether we'd *expect* it to
# appear in a NYC Parks Dept (DPR) "Parks Properties" dataset at all, and
# should be double-checked once the dataset is actually queryable.
ANCHOR_PARKS = {
    "McCarren Park": {
        "expect_in_dataset": True,
        "note": "NYC DPR property (Greenpoint/Williamsburg border).",
    },
    "WNYC Transmitter Park": {
        "expect_in_dataset": True,
        "note": "NYC DPR waterfront park, Greenpoint.",
    },
    "McGolrick Park": {
        "expect_in_dataset": True,
        "note": "NYC DPR property, Greenpoint.",
    },
    "Domino Park": {
        "expect_in_dataset": False,
        "note": (
            "Privately owned public space on the former Domino Sugar site, "
            "developed and maintained by Two Trees Management as part of a "
            "private development deal, not a NYC Parks Dept property. "
            "Expected ABSENT from enfh-gkve; would need to be sourced "
            "separately (e.g. from the development's own GIS data) if the "
            "product needs it."
        ),
    },
    "Marsha P. Johnson State Park": {
        "expect_in_dataset": False,
        "note": (
            "Formerly East River State Park; operated by NY STATE Parks "
            "(OPRHP), not NYC Parks Dept. Expected ABSENT from enfh-gkve, "
            "which covers only DPR jurisdiction. Would need NYS Parks' own "
            "open data (data.ny.gov) if required."
        ),
    },
    "Fort Greene Park": {
        "expect_in_dataset": True,
        "note": "NYC DPR flagship park, Fort Greene.",
    },
    "Prospect Park": {
        "expect_in_dataset": True,
        "note": (
            "NYC DPR flagship park; day-to-day operations partly run by "
            "the nonprofit Prospect Park Alliance conservancy, but the "
            "underlying property is still a City (DPR) park and should "
            "appear in Parks Properties."
        ),
    },
    "Mount Prospect Park": {
        "expect_in_dataset": True,
        "note": "Small NYC DPR park near Grand Army Plaza, Prospect Heights.",
    },
    "Washington Park": {
        "expect_in_dataset": True,
        "note": (
            "LOW CONFIDENCE: 'Washington Park' was Fort Greene Park's "
            "original 19th-century name, so this anchor may be a small, "
            "separately-named DPR site near the Fort Greene / Clinton Hill "
            "line rather than Fort Greene Park itself (which is already a "
            "separate anchor in this list). Could not disambiguate without "
            "a live dataset query -- flagged for manual confirmation."
        ),
    },
    "Carroll Park": {
        "expect_in_dataset": True,
        "note": "NYC DPR property, Carroll Gardens.",
    },
    "Cobble Hill Park": {
        "expect_in_dataset": True,
        "note": "NYC DPR property, Cobble Hill.",
    },
    "Brooklyn Bridge Park": {
        "expect_in_dataset": False,
        "note": (
            "Operated by the Brooklyn Bridge Park Corporation, a "
            "state/city authority separate from NYC Parks Dept (DPR). "
            "Expected ABSENT (or present with a non-DPR 'department' tag) "
            "from enfh-gkve; would need the Brooklyn Bridge Park "
            "Corporation's own data if required."
        ),
    },
}


# Real dataset names sometimes abbreviate/prefix a word the anchor list
# spells out differently (e.g. "Mt. Prospect Park" instead of "Mount
# Prospect Park"; "Msgr. McGolrick Park" instead of "McGolrick Park").
# Add the REAL dataset's exact name here as an alias whenever a mismatch
# like this is found, rather than loosening the match check below.
ANCHOR_ALIASES = {
    "Mount Prospect Park": ["Mt. Prospect Park", "Mt Prospect Park"],
    "McGolrick Park": ["Msgr. McGolrick Park", "Msgr McGolrick Park", "Monsignor McGolrick Park"],
}


def _matches_anchor(feature_name, anchor_name):
    """True if feature_name IS anchor_name (or one of its aliases), or is
    that name plus a sub-parcel suffix (e.g. "Prospect Park - Long Meadow"
    matching anchor "Prospect Park").

    HISTORY / BUG THIS REPLACES: an earlier version used a bidirectional
    substring check (`anchor in name OR name in anchor`). That second
    direction is a real-world data trap: the live NYC Parks Properties
    dataset has ~35 unrelated, unnamed features (tiny medians, triangles,
    strips) whose `name`/`signname` field is literally the generic string
    "Park" -- and "PARK" is a substring of every one of our anchor names
    ("... Park"), so all 35 of them silently matched EVERY anchor. Their
    boundary points got merged into e.g. McCarren Park's point list, and
    since distance_to_park_boundary_m() takes the closest point across the
    whole merged list, "distance to McCarren Park" was actually being
    computed as "distance to whichever of these 35 scattered generic strips
    happens to be closest" -- explaining a real run where Clinton Hill
    (miles from McCarren) came back as 1,324.7m: a random median strip
    somewhere between the two was < the true 3.1km straight-line distance
    to McCarren itself. Anchor-name-as-suffix (e.g. real name has an extra
    prefix like "Msgr.") is handled via the explicit ANCHOR_ALIASES table
    above instead of a generic suffix-match rule, because a generic suffix
    rule has the exact same trap in reverse (e.g. "Mount Prospect Park"
    ends with "Prospect Park" and would wrongly match that anchor too).

    NOTE: this used to live in build_geo.py, but check_anchor_coverage()
    below needs the exact same match rule (it had its own separate, buggy
    bidirectional-substring copy that this replaces) -- so this is now the
    single source of truth here in load_parks.py, and build_geo.py imports
    it from here instead of the reverse.
    """
    n = feature_name.strip().upper()
    candidates = [anchor_name] + ANCHOR_ALIASES.get(anchor_name, [])
    for c in candidates:
        cu = c.strip().upper()
        if n == cu or n.startswith(cu + " ") or n.startswith(cu + "-") or n.startswith(cu + "/"):
            return True
    return False


def load_neighborhood_bbox(path=NEIGHBORHOODS_PATH, buffer_deg=0.0145):
    """~1 mile buffer in degrees at Brooklyn's latitude (1 mi ~= 0.0145 deg lat,
    slightly more in lon at this latitude; we use one conservative buffer
    for both axes, which over-includes slightly on the east/west axis --
    acceptable for a 'roughly 1 mile' filter)."""
    with open(path) as f:
        gj = json.load(f)
    lons, lats = [], []
    for feat in gj["features"]:
        coords = feat["geometry"]["coordinates"][0]
        for lon, lat in coords:
            lons.append(lon)
            lats.append(lat)
    return (
        min(lons) - buffer_deg,
        min(lats) - buffer_deg,
        max(lons) + buffer_deg,
        max(lats) + buffer_deg,
    )


def fetch_from_socrata(bbox, limit=5000, timeout=20):
    """Attempt a live Socrata fetch, filtered to Brooklyn ('B', confirmed
    live -- see module docstring) and a bounding box roughly covering the
    8 neighborhoods + 1 mile buffer.
    Returns a list of row dicts, or raises on failure (caller decides
    fallback behavior)."""
    minlon, minlat, maxlon, maxlat = bbox
    # within_box(multipolygon, NW_lat, NW_lon, SE_lat, SE_lon)
    where = (
        f"borough='B' AND within_box(multipolygon, "
        f"{maxlat}, {minlon}, {minlat}, {maxlon})"
    )
    url = f"{SOCRATA_JSON}?$where={urllib.parse.quote(where)}&$limit={limit}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def load_from_file(path):
    """Load a previously-downloaded raw JSON array from Socrata (the
    `curl ... > geo/parks_raw.json` step in the module docstring). No
    bbox/borough filtering is re-applied here if the file was already
    fetched with those query params; if it's an unfiltered full-borough
    dump, filter it in rows_to_geojson's caller as needed."""
    with open(path) as f:
        return json.load(f)


def rows_to_geojson(rows):
    features = []
    for row in rows:
        geom = row.get("multipolygon")
        if geom is None:
            continue
        name = row.get("signname") or row.get("name311") or "Unnamed"
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "name": name,
                    "gispropnum": row.get("gispropnum"),
                    "typecategory": row.get("typecategory"),
                    "acres": row.get("acres"),
                    "department": row.get("department"),
                    "zipcode": row.get("zipcode"),
                    "source": "nyc_open_data_enfh-gkve",
                },
                "geometry": geom,
            }
        )
    return {"type": "FeatureCollection", "features": features}


# ---------------------------------------------------------------------
# Fallback placeholder data. NOT sourced from the live dataset -- these
# are single representative points (approximate entrance/centroid
# coordinates from general geographic knowledge) standing in for real
# park polygons so that distance.py / build_geo.py have something to run
# against. Large parks (Prospect Park, Brooklyn Bridge Park) get several
# boundary-ish sample points instead of one, since a single centroid
# would badly misrepresent "distance to the park" for a large park.
# THESE ARE PLACEHOLDERS. Replace with real the_geom polygons from a
# live fetch before using this for anything other than pipeline testing.
# ---------------------------------------------------------------------
PLACEHOLDER_PARK_POINTS = {
    "McCarren Park": [(-73.9515, 40.7211)],
    "WNYC Transmitter Park": [(-73.9581, 40.7300)],
    "McGolrick Park": [(-73.9391, 40.7237)],
    "Domino Park": [(-73.9640, 40.7115)],
    "Marsha P. Johnson State Park": [(-73.9636, 40.7212)],
    "Fort Greene Park": [(-73.9737, 40.6903)],
    "Prospect Park": [
        (-73.9701, 40.6743),  # Grand Army Plaza entrance
        (-73.9740, 40.6650),  # Prospect Park West / 9th St entrance
        (-73.9635, 40.6605),  # Prospect Park West / 15th St (SW corner)
        (-73.9605, 40.6560),  # Ocean Ave / Parkside entrance (south)
        (-73.9645, 40.6520),  # Parkside Ave entrance
        (-73.9590, 40.6660),  # Lincoln Rd / Ocean Ave entrance (east)
        (-73.9660, 40.6745),  # Flatbush Ave / Plaza St entrance (NE)
    ],
    "Mount Prospect Park": [(-73.9670, 40.6745)],
    "Washington Park": [(-73.9660, 40.6930)],  # low confidence, see note
    "Carroll Park": [(-73.9950, 40.6800)],
    "Cobble Hill Park": [(-73.9945, 40.6873)],
    "Brooklyn Bridge Park": [
        (-73.9968, 40.7025),  # near Greenpoint St / Pier 1
        (-73.9945, 40.6960),  # DUMBO / Pier 1-2
        (-73.9990, 40.6900),  # Pier 5-6, Atlantic Ave end
    ],
}


def build_placeholder_geojson():
    features = []
    for name, points in PLACEHOLDER_PARK_POINTS.items():
        info = ANCHOR_PARKS.get(name, {})
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "name": name,
                    "source": "PLACEHOLDER_manual_estimate_not_live_data",
                    "expect_in_dpr_dataset": info.get("expect_in_dataset"),
                    "jurisdiction_note": info.get("note"),
                },
                "geometry": {
                    "type": "MultiPoint",
                    "coordinates": [[lon, lat] for lon, lat in points],
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}


def check_anchor_coverage(gj):
    """Print which of the anchor parks (WP2's brief) were actually found in
    the fetched features, by exact-or-prefix-or-alias match (_matches_anchor,
    the same rule build_geo.py's load_parks() uses to filter features) against
    signname/name311, vs. which are missing -- and whether a miss was
    EXPECTED (non-DPR jurisdiction) or a genuine gap worth investigating."""
    names = [f["properties"]["name"] for f in gj["features"] if f["properties"].get("name")]
    print("\nAnchor-park coverage check:")
    for anchor, info in ANCHOR_PARKS.items():
        hit = any(_matches_anchor(n, anchor) for n in names)
        status = "FOUND" if hit else ("expected absent (non-DPR)" if not info["expect_in_dataset"] else "MISSING -- unexpected")
        print(f"  [{('x' if hit else ' ')}] {anchor:32s} {status}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--from-file", type=str, default=None,
        help="Path to a previously-downloaded raw Socrata JSON array "
             "(see the module docstring's curl command) -- use this "
             "instead of attempting a live fetch.",
    )
    args = ap.parse_args()

    bbox = load_neighborhood_bbox()
    print(f"Neighborhood bbox (+~1mi buffer): {bbox}")

    if args.from_file:
        print(f"Loading cached Socrata dump from {args.from_file} (no live fetch attempted).")
        rows = load_from_file(args.from_file)
        gj = rows_to_geojson(rows)
        print(f"Loaded {len(gj['features'])} park features from file.")
    else:
        try:
            rows = fetch_from_socrata(bbox)
            gj = rows_to_geojson(rows)
            print(f"Fetched {len(gj['features'])} park features from live Socrata endpoint.")
        except Exception as exc:  # noqa: BLE001 - want to catch and fall back cleanly
            print(f"Live fetch FAILED ({exc.__class__.__name__}: {exc}).", file=sys.stderr)
            print("Falling back to PLACEHOLDER anchor-park points. "
                  "See module docstring for the manual fetch step "
                  "(curl ... > geo/parks_raw.json, then --from-file).", file=sys.stderr)
            gj = build_placeholder_geojson()

    if gj["features"] and gj["features"][0]["properties"].get("source") != "PLACEHOLDER_manual_estimate_not_live_data":
        check_anchor_coverage(gj)

    with open(OUT_PATH, "w") as f:
        json.dump(gj, f, indent=2)
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()

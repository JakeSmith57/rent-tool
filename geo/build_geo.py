"""
WP2 - End-to-end geo pipeline.

For a list of input points (building lat/lon), emits:
  - which of the 8 neighborhoods (if any) the point falls in
  - the nearest park + distance
  - distance to every anchor park

Usage:
    python3 geo/build_geo.py            # runs the built-in validation set
    python3 geo/build_geo.py points.json  # runs against a JSON file of
                                            # [{"label":..., "lat":..., "lon":...}, ...]
"""
import json
import os
import sys

from distance import ACTIVE_BACKEND, distance_to_each_park, nearest_park, meters_to_miles, meters_to_walk_minutes
from load_parks import ANCHOR_PARKS

HERE = os.path.dirname(os.path.abspath(__file__))
NEIGHBORHOODS_PATH = os.path.join(HERE, "neighborhoods.geojson")
PARKS_PATH = os.path.join(HERE, "parks.geojson")

# Validation addresses chosen so their expected proximity to a park is
# knowable by general geographic knowledge, per WP2 instructions.
VALIDATION_POINTS = [
    {"label": "1 Prospect Park West (Park Slope, faces the park)", "lat": 40.6743, "lon": -73.9740},
    {"label": "9 Withers St area, McCarren Park block (Williamsburg)", "lat": 40.7205, "lon": -73.9505},
    {"label": "Manhattan Ave & Milton St (Greenpoint, ~0.4mi to McCarren)", "lat": 40.7280, "lon": -73.9525},
    {"label": "DeKalb Ave & S Portland Ave (Fort Greene, faces the park)", "lat": 40.6895, "lon": -73.9735},
    {"label": "Classon Ave & Greene Ave (Clinton Hill core)", "lat": 40.6905, "lon": -73.9575},
    {"label": "Vanderbilt Ave & Sterling Pl (Prospect Heights)", "lat": 40.6775, "lon": -73.9680},
    {"label": "President St & Court St (Carroll Gardens, near Carroll Park)", "lat": 40.6810, "lon": -73.9955},
    {"label": "Clinton St & Kane St (Cobble Hill, near Cobble Hill Park)", "lat": 40.6875, "lon": -73.9955},
    {"label": "Bedford Ave & N 7th St (Williamsburg core, McCarren ~0.5mi)", "lat": 40.7180, "lon": -73.9576},
    {"label": "Furman St (DUMBO/Brooklyn Heights edge, near Brooklyn Bridge Park)", "lat": 40.7000, "lon": -73.9960},
]


def point_in_polygon(lon, lat, ring):
    """Standard ray-casting point-in-polygon test. ring: [(lon,lat), ...]."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        intersects = ((yi > lat) != (yj > lat)) and (
            lon < (xj - xi) * (lat - yi) / (yj - yi + 1e-15) + xi
        )
        if intersects:
            inside = not inside
        j = i
    return inside


def load_neighborhoods():
    with open(NEIGHBORHOODS_PATH) as f:
        gj = json.load(f)
    result = []
    for feat in gj["features"]:
        name = feat["properties"]["name"]
        ring = feat["geometry"]["coordinates"][0]
        result.append((name, ring))
    return result


def which_neighborhood(lat, lon, neighborhoods):
    for name, ring in neighborhoods:
        if point_in_polygon(lon, lat, ring):
            return name
    return None


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
    """
    n = feature_name.strip().upper()
    candidates = [anchor_name] + ANCHOR_ALIASES.get(anchor_name, [])
    for c in candidates:
        cu = c.strip().upper()
        if n == cu or n.startswith(cu + " ") or n.startswith(cu + "-") or n.startswith(cu + "/"):
            return True
    return False


def load_parks():
    """Returns {anchor_park_name: [(lat, lon), ...boundary points...]}.

    IMPORTANT: filters down to just the 12 named ANCHOR_PARKS (imported
    from load_parks.py, same loose case-insensitive substring match
    check_anchor_coverage() uses there) rather than every feature in
    parks.geojson. A live Socrata fetch of "Parks Properties" returns
    ~628 Brooklyn-wide park properties, not just the ones this tool
    cares about -- without this filter, nearest_park()/distance_to_each_
    park() below would compute a real-routed distance to all 628 of them
    for every input point, which under real OSRM routing means tens of
    thousands of live HTTP calls to a shared public demo server (this bit
    a real run: python3 geo/build_geo.py hung for 10+ minutes before this
    fix). Multiple real dataset rows that loosely match the same anchor
    (e.g. separate sub-parcels under one park's name) are merged into one
    entry under the canonical anchor name.
    """
    with open(PARKS_PATH) as f:
        gj = json.load(f)
    parks = {}
    for feat in gj["features"]:
        name = feat["properties"]["name"]
        if not name:
            continue
        anchor = next((a for a in ANCHOR_PARKS if _matches_anchor(name, a)), None)
        if anchor is None:
            continue  # not one of the 12 parks this tool tracks -- skip
        geom = feat["geometry"]
        if geom["type"] == "MultiPoint":
            pts = [(lat, lon) for lon, lat in geom["coordinates"]]
        elif geom["type"] == "Point":
            lon, lat = geom["coordinates"]
            pts = [(lat, lon)]
        else:
            # Real polygon/multipolygon geometry from a live fetch:
            # sample all ring vertices as boundary points.
            pts = []
            coords = geom["coordinates"]
            polys = coords if geom["type"] == "MultiPolygon" else [coords]
            for poly in polys:
                for ring in poly:
                    for lon, lat in ring:
                        pts.append((lat, lon))
        if pts:
            parks.setdefault(anchor, []).extend(pts)
    return parks


def main():
    if len(sys.argv) > 1:
        with open(sys.argv[1]) as f:
            points = json.load(f)
    else:
        points = VALIDATION_POINTS

    neighborhoods = load_neighborhoods()
    parks = load_parks()

    results = []
    for pt in points:
        lat, lon = pt["lat"], pt["lon"]
        nb = which_neighborhood(lat, lon, neighborhoods)
        nearest_name, nearest_m = nearest_park((lat, lon), parks)
        per_anchor = distance_to_each_park((lat, lon), parks)
        results.append(
            {
                "label": pt["label"],
                "lat": lat,
                "lon": lon,
                "neighborhood": nb,
                "nearest_park": nearest_name,
                "nearest_park_m": round(nearest_m, 1),
                "nearest_park_mi": round(meters_to_miles(nearest_m), 3),
                "nearest_park_walk_min": round(meters_to_walk_minutes(nearest_m), 1),
                "distance_to_each_park_m": {
                    k: round(v, 1) for k, v in sorted(per_anchor.items(), key=lambda kv: kv[1])
                },
            }
        )

    out_path = os.path.join(HERE, "build_geo_output.json")
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)

    for r in results:
        print(f"\n{r['label']}")
        print(f"  neighborhood: {r['neighborhood']}")
        print(
            f"  nearest park: {r['nearest_park']} "
            f"({r['nearest_park_m']} m / {r['nearest_park_mi']} mi / "
            f"~{r['nearest_park_walk_min']} min walk) [backend: {ACTIVE_BACKEND}]"
        )
    print(f"\nFull output written to {out_path}")


if __name__ == "__main__":
    main()

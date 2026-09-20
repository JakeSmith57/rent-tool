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
import time

from distance import ACTIVE_BACKEND, distance_to_each_park, nearest_park, meters_to_miles, meters_to_walk_minutes
from load_parks import ANCHOR_PARKS, ANCHOR_ALIASES, _matches_anchor

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


import math

# Boundary points are densified (see _densify_ring below) so that no two
# consecutive points along a park's boundary are farther apart than this.
# Real park boundaries are raw GeoJSON ring vertices at whatever density the
# source polygon happens to have -- a large simple park (e.g. Prospect Park,
# ~186 vertices for a huge perimeter) can have long straight edges with
# sparse vertices, so a point directly opposite the middle of a long edge
# could rank the MAX_ROUTED_CANDIDATES nearest-by-haversine points (see
# distance.py) as distant corner vertices instead of the true nearest point
# on the edge, overstating "distance to the park". 25m keeps that error
# small without exploding the point count for even the largest parks here.
MAX_BOUNDARY_POINT_GAP_M = 25.0


def _densify_ring(points):
    """Given an ordered list of (lat, lon) points along a ring/edge, insert
    linearly-interpolated points along each consecutive pair so that no gap
    exceeds MAX_BOUNDARY_POINT_GAP_M. Purely additive -- every input point
    is kept, in order; only new points are inserted between them. Uses
    simple straight-line lat/lon interpolation (not geodesic) -- precise
    enough at these distances and consistent with this module's existing
    haversine-based distance approach. Done once here at load time (not
    per-query) since the resulting list is reused for every distance
    computation against this park."""
    if len(points) < 2:
        return list(points)
    from distance import _haversine_m

    densified = [points[0]]
    for (lat1, lon1), (lat2, lon2) in zip(points, points[1:]):
        gap = _haversine_m((lat1, lon1), (lat2, lon2))
        if gap > MAX_BOUNDARY_POINT_GAP_M:
            steps = math.ceil(gap / MAX_BOUNDARY_POINT_GAP_M)
            for i in range(1, steps):
                t = i / steps
                densified.append((lat1 + (lat2 - lat1) * t, lon1 + (lon2 - lon1) * t))
        densified.append((lat2, lon2))
    return densified


def load_parks():
    """Returns {anchor_park_name: [(lat, lon), ...boundary points...]}.

    IMPORTANT: filters down to just the 12 named ANCHOR_PARKS (imported
    from load_parks.py, same exact-or-prefix-or-alias match (_matches_anchor)
    that check_anchor_coverage() uses there) rather than every feature in
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

    Each ring of real polygon geometry is also densified (see _densify_ring)
    so consecutive boundary points are never more than
    MAX_BOUNDARY_POINT_GAP_M apart, avoiding an overstated nearest-boundary
    distance on parks with long, sparsely-vertexed edges.
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
            # sample all ring vertices as boundary points, densified so no
            # consecutive pair is more than MAX_BOUNDARY_POINT_GAP_M apart.
            pts = []
            coords = geom["coordinates"]
            polys = coords if geom["type"] == "MultiPolygon" else [coords]
            for poly in polys:
                for ring in poly:
                    ring_pts = [(lat, lon) for lon, lat in ring]
                    pts.extend(_densify_ring(ring_pts))
        if pts:
            parks.setdefault(anchor, []).extend(pts)
    return parks


def main():
    if len(sys.argv) > 1:
        with open(sys.argv[1]) as f:
            points = json.load(f)
    else:
        points = VALIDATION_POINTS

    print(f"Loading neighborhoods from {NEIGHBORHOODS_PATH}...", flush=True)
    neighborhoods = load_neighborhoods()
    print(f"  loaded {len(neighborhoods)} neighborhoods", flush=True)

    print(f"Loading parks from {PARKS_PATH} (backend: {ACTIVE_BACKEND})...", flush=True)
    t_parks_start = time.monotonic()
    parks = load_parks()
    t_parks = time.monotonic() - t_parks_start
    total_boundary_pts = sum(len(v) for v in parks.values())
    print(
        f"  loaded {len(parks)} anchor parks, {total_boundary_pts} total "
        f"boundary points ({t_parks:.1f}s)",
        flush=True,
    )
    if not parks:
        print(
            "ERROR: load_parks() returned no parks (zero anchor matches in "
            f"{PARKS_PATH}). Refusing to proceed: nearest_park()/distance_to_"
            "each_park() would return math.inf, which json.dump() would "
            "write as the invalid-JSON token `Infinity`, silently breaking "
            "any downstream JSON parser. Check parks.geojson / ANCHOR_PARKS.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(
        f"\nRouting {len(points)} point(s) against {len(parks)} anchor parks "
        f"each (backend: {ACTIVE_BACKEND}) -- this is the slow part, each "
        "point/park pair can mean several live routing API calls...",
        flush=True,
    )
    t_routing_start = time.monotonic()
    results = []
    for i, pt in enumerate(points, start=1):
        lat, lon = pt["lat"], pt["lon"]
        t_point_start = time.monotonic()
        print(f"[{i}/{len(points)}] {pt['label']}...", end="", flush=True)
        nb = which_neighborhood(lat, lon, neighborhoods)
        nearest_name, nearest_m = nearest_park((lat, lon), parks)
        per_anchor = distance_to_each_park((lat, lon), parks)
        t_point = time.monotonic() - t_point_start
        print(f" done ({t_point:.1f}s)", flush=True)
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

    t_routing = time.monotonic() - t_routing_start
    print(f"\nRouting done in {t_routing:.1f}s total.", flush=True)

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

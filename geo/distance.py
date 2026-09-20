"""
WP2 - Distance interface.

Public interface (this is the "swappable" boundary):

    walking_distance_m(origin, dest) -> float meters
    nearest_park(origin, parks) -> (park_name, distance_m)
    distance_to_each_park(origin, parks) -> {park_name: distance_m}

STATUS: REAL ROUTING via the public OSRM demo server
(router.project-osrm.org), foot profile. This sandbox's own egress is
locked to package registries only, so the live call could not be tested
from here -- but a direct curl from a normal-internet machine confirmed
the endpoint works and returns real route distances (a Park Slope test
route came back as 938m, plausible for the two points queried). If that
demo server is ever unreachable, rate-limited, or retired, flip
ACTIVE_BACKEND back to "straight_line" below -- the interface is
unchanged either way, and every caller (nearest_park,
distance_to_each_park, build_geo.py) is unaffected by the switch.

The demo server is public, shared, rate-limited, and explicitly NOT meant
for production/bulk use per OSRM's own docs -- fine for prototyping and
one-off validation runs, but a real product should eventually run its own
OSRM instance (or a paid routing API) built from an OSM extract for NYC.
See KNOWN LIMITATIONS below either way.

KNOWN LIMITATIONS (true of any routing backend here, real or placeholder):
  - The straight-line placeholder (still available via ACTIVE_BACKEND=
    "straight_line") systematically UNDERSTATES distance across barriers
    (highways, rail yards, canals, the waterfront) where the real route
    has to go around -- e.g. the Gowanus Canal between Carroll Gardens/
    Park Slope and points east, or the BQE trench. Real OSRM routing does
    not have this problem since it routes along the actual street graph.
  - No caching/memoization here: every (origin, park-boundary-point) pair
    is a fresh HTTP call. For build_geo.py's small validation set this is
    fine; for a full listings run (WP6+) callers should batch or cache
    results, since the public demo server is rate-limited.
"""
import json
import math
import urllib.error
import urllib.request

EARTH_RADIUS_M = 6371000.0

# Rough Manhattan-grid-style circuity factor: real walking-route distance
# divided by straight-line distance, for a fairly regular urban grid.
# Typical measured values for dense grid neighborhoods are ~1.2-1.4;
# we use a single flat factor since we have no real routing to calibrate
# against. THIS IS A GUESS, documented as such.
WALK_CIRCUITY_FACTOR = 1.3

ACTIVE_BACKEND = "routing_api"  # real OSRM routing; see module docstring. Set to "straight_line" to fall back.

OSRM_BASE_URL = "http://router.project-osrm.org/route/v1/foot"
OSRM_TIMEOUT_S = 10


def _haversine_m(origin, dest):
    lat1, lon1 = origin
    lat2, lon2 = dest
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return EARTH_RADIUS_M * c


def _walking_distance_m_straight_line(origin, dest):
    """PLACEHOLDER backend: great-circle distance * circuity factor."""
    return _haversine_m(origin, dest) * WALK_CIRCUITY_FACTOR


def _walking_distance_m_via_routing_api(origin, dest):
    """Real OSRM foot-profile routing via the public demo server.

    origin, dest: (lat, lon) tuples. OSRM's URL order is lon,lat (opposite
    of this module's convention), so it's flipped here at the boundary --
    every OTHER function in this module stays (lat, lon) throughout.

    Falls back to the straight-line estimate (with a note printed to
    stderr) if the demo server errors, times out, or is unreachable --
    a transient network hiccup shouldn't hard-crash a bulk distance run.
    """
    lat1, lon1 = origin
    lat2, lon2 = dest
    url = f"{OSRM_BASE_URL}/{lon1},{lat1};{lon2},{lat2}?overview=false"
    try:
        with urllib.request.urlopen(url, timeout=OSRM_TIMEOUT_S) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("code") != "Ok" or not data.get("routes"):
            raise ValueError(f"OSRM returned no route: {data.get('code')}")
        return float(data["routes"][0]["distance"])
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError, json.JSONDecodeError) as exc:
        import sys
        print(
            f"[distance.py] OSRM call failed ({exc.__class__.__name__}: {exc}) "
            f"for {origin} -> {dest}; falling back to straight-line*circuity "
            "for this one pair.",
            file=sys.stderr,
        )
        return _walking_distance_m_straight_line(origin, dest)


def walking_distance_m(origin, dest):
    """origin, dest: (lat, lon) tuples. Returns meters.

    This is the ONE function everything else in geo/ should call -- it
    is the swap point between the placeholder and real routing.
    """
    if ACTIVE_BACKEND == "straight_line":
        return _walking_distance_m_straight_line(origin, dest)
    elif ACTIVE_BACKEND == "routing_api":
        return _walking_distance_m_via_routing_api(origin, dest)
    else:
        raise ValueError(f"Unknown ACTIVE_BACKEND: {ACTIVE_BACKEND!r}")


MAX_ROUTED_CANDIDATES = 5


def distance_to_park_boundary_m(origin, park_boundary_points):
    """origin: (lat, lon). park_boundary_points: list of (lat, lon) points
    sampled along/around the park's boundary (or its single
    entrance/centroid point for small parks where that distinction barely
    matters). Returns the minimum walking distance to any of them, which
    approximates 'distance to the nearest point of the park' rather than
    'distance to the park's centroid' -- important for large parks like
    Prospect Park or Brooklyn Bridge Park where the centroid can be far
    from the edge closest to a given building.

    PERFORMANCE NOTE: with real polygon geometry (build_geo.py's
    load_parks() samples every ring vertex), a single park can have dozens
    to hundreds of boundary points. Calling the real routing_api backend
    for every one of them means one live HTTP request per point -- against
    a shared, rate-limited public demo server, that's the difference
    between a run finishing in seconds and one that never finishes. So
    when there's real routing to do, we first rank ALL boundary points by
    cheap local haversine distance and only send the closest
    MAX_ROUTED_CANDIDATES to the real backend. The true nearest walking
    point is essentially always among the straight-line-closest few
    (walking distance and straight-line distance are strongly correlated
    for points on the same small cluster of a polygon's edge), so this
    trades a theoretical edge case for routing runs that actually
    complete. The straight_line backend skips this shortcut entirely --
    it's already just local arithmetic, so ranking first would only slow
    it down for no benefit.
    """
    if ACTIVE_BACKEND == "straight_line" or len(park_boundary_points) <= MAX_ROUTED_CANDIDATES:
        return min(walking_distance_m(origin, p) for p in park_boundary_points)

    candidates = sorted(park_boundary_points, key=lambda p: _haversine_m(origin, p))[:MAX_ROUTED_CANDIDATES]
    return min(walking_distance_m(origin, p) for p in candidates)


def nearest_park(origin, parks):
    """parks: {name: [(lat, lon), ...boundary points...]}
    Returns (name, distance_m) of the closest park.
    """
    best_name, best_dist = None, math.inf
    for name, points in parks.items():
        d = distance_to_park_boundary_m(origin, points)
        if d < best_dist:
            best_name, best_dist = name, d
    return best_name, best_dist


def distance_to_each_park(origin, parks):
    """parks: {name: [(lat, lon), ...]} -> {name: distance_m}"""
    return {
        name: distance_to_park_boundary_m(origin, points)
        for name, points in parks.items()
    }


def meters_to_miles(m):
    return m / 1609.344


def meters_to_walk_minutes(m, walk_speed_mps=1.34):
    """~3 mph / 4.8 km/h average adult walking speed."""
    return m / walk_speed_mps / 60.0

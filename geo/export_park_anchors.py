#!/usr/bin/env python3
"""
geo/export_park_anchors.py -- generate `park_anchors.json`, the compact
anchor-park file the WP5 scheduled alert-check task uses to estimate walk
time to the nearest park.

WHY THIS SCRIPT EXISTS (2026-09-20): park_anchors.json used to be
hand-maintained, and it had drifted from geo/load_parks.py in two ways
that both corrupted the WP5 score:

  1. It stored ONE "centroid" per park -- for multi-point parks that
     centroid was the arithmetic MEAN of the sample points, which for a
     large park lands in the middle of the park rather than on its edge.
     Measured against 1 Prospect Park West (a building that literally
     faces the park): the averaged centroid gave 1,759 m / ~21.9 min
     (park sub-score 1) where the real nearest boundary point gives
     428 m / ~5.3 min (sub-score 3) -- a 22-point swing in
     match_score_estimate, biased hardest against exactly the listings
     the user most wants to see.
  2. It was a separate copy of load_parks.py's PLACEHOLDER_PARK_POINTS,
     so adding an anchor park in one place didn't add it in the other.

This script removes both problems: it derives the file from
load_parks.py's own PLACEHOLDER_PARK_POINTS, and it emits the FULL point
list per park so the consumer can take a minimum instead of a mean.

USAGE
    python3 geo/export_park_anchors.py
    # writes geo/park_anchors.json

    # then push it to the Claude project as code/park_anchors.json so the
    # scheduled task picks it up -- same manual hop as
    # etl/export_registry_lookup.py's output (see docs/WP5-alerts.md).

OUTPUT SHAPE
    {
      "<Park Name>": {
        "points": [[lat, lon], ...],   # AUTHORITATIVE: take the min over these
        "centroid": [lat, lon],        # deprecated, kept for compatibility
        "sample_point_count": <int>
      },
      ...
    }

CONSUMER CONTRACT: distance to a park is
    min(haversine(listing, p) for p in park["points"]) * 1.3
NOT the distance to "centroid". The centroid key is retained only so an
older consumer doesn't hard-fail on a missing key; anything reading it is
reproducing the bug described above and should be updated.
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from load_parks import PLACEHOLDER_PARK_POINTS  # noqa: E402

OUT_PATH = os.path.join(HERE, "park_anchors.json")


def build_anchor_map() -> dict:
    anchors = {}
    for name, points in PLACEHOLDER_PARK_POINTS.items():
        # PLACEHOLDER_PARK_POINTS is stored (lon, lat) to match GeoJSON;
        # park_anchors.json is (lat, lon) to match this project's own
        # distance-function convention. Flip here, once, at the boundary.
        latlon = [[round(lat, 6), round(lon, 6)] for lon, lat in points]
        centroid = [
            round(sum(p[0] for p in latlon) / len(latlon), 6),
            round(sum(p[1] for p in latlon) / len(latlon), 6),
        ]
        anchors[name] = {
            "points": latlon,
            "centroid": centroid,  # deprecated -- see module docstring
            "sample_point_count": len(latlon),
        }
    return anchors


def main() -> None:
    anchors = build_anchor_map()
    with open(OUT_PATH, "w") as f:
        json.dump(anchors, f, indent=2, sort_keys=False)
        f.write("\n")
    total_points = sum(a["sample_point_count"] for a in anchors.values())
    size_kb = os.path.getsize(OUT_PATH) / 1024
    print(
        f"Wrote {len(anchors)} anchor parks / {total_points} points to "
        f"{OUT_PATH} ({size_kb:.1f} KB)"
    )
    multi = [n for n, a in anchors.items() if a["sample_point_count"] > 1]
    if multi:
        print(
            "Multi-point parks (these are the ones a centroid-based consumer "
            "gets badly wrong): " + ", ".join(multi)
        )
    print(
        "\nNext: push this file to the Claude project as "
        "code/park_anchors.json, and make sure the scheduled task's prompt "
        "reads the `points` list and takes a MIN -- not `centroid`. See "
        "docs/WP5-alerts.md."
    )


if __name__ == "__main__":
    main()

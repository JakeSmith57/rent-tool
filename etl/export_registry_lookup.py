#!/usr/bin/env python3
"""
etl/export_registry_lookup.py -- WP5 helper: export a COMPACT bbl-keyed
lookup (just the two tier fields, no raw HPD/DHCR columns) from the full
data/processed/registry.csv, for pushing to the claude.ai project as
data/registry-lookup.json.

WHY THIS EXISTS: the scheduled Gmail-alert-check task (WP3b) runs as a
FRESH cloud session every time it fires -- it never has access to your
local machine or the full registry.csv (which is large and never
persisted to the project; see claude/status.md's "What's persisted"
section). For that scheduled task to score a brand-new listing
automatically (WP5), it needs SOME copy of the registry's tier data
available to it as a project doc. The full registry.csv (19.5k+ rows,
every raw HPD/DHCR column) is overkill for that -- this script produces
just {bbl: {stabilization_confidence, building_safety_tier}}, dropping
everything else, to keep the pushed file small.

USAGE
    python3 etl/build_registry.py          # (if you haven't already)
    python3 etl/export_registry_lookup.py
    # then upload data/processed/registry_lookup.json to Claude (attach
    # it in a message) and ask it to push the update to the project as
    # data/registry-lookup.json -- see docs/WP5-alerts.md.
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import DATA_PROCESSED  # noqa: E402

REGISTRY_CSV = DATA_PROCESSED / "registry.csv"
LOOKUP_OUT = DATA_PROCESSED / "registry_lookup.json"


def main() -> None:
    if not REGISTRY_CSV.exists():
        print(f"ERROR: {REGISTRY_CSV} not found -- run etl/build_registry.py first.", file=sys.stderr)
        sys.exit(1)

    lookup = {}
    with open(REGISTRY_CSV, newline="") as f:
        for row in csv.DictReader(f):
            bbl = row.get("bbl")
            if not bbl:
                continue
            lookup[bbl] = {
                "stabilization_confidence": row.get("stabilization_confidence", "unknown"),
                "building_safety_tier": row.get("building_safety_tier", "unknown"),
            }

    with open(LOOKUP_OUT, "w") as f:
        json.dump(lookup, f, separators=(",", ":"))  # no indent -- keep it compact

    size_kb = LOOKUP_OUT.stat().st_size / 1024
    print(f"Wrote {len(lookup)} BBL entries to {LOOKUP_OUT} ({size_kb:.0f} KB)")
    print(
        "\nNext: attach this file to your Claude chat and ask Claude to push it "
        "to the project as data/registry-lookup.json (replacing the existing "
        "copy), so the scheduled alert-check task picks up the refreshed data "
        "on its next run."
    )


if __name__ == "__main__":
    main()

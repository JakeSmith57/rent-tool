#!/usr/bin/env bash
# Runs the full WP1 + WP2 pipeline in order. See README.md section 2 for
# the manual download step this depends on -- this script does not fetch
# anything itself, it only runs the loaders/parsers against whatever is
# already sitting in data/raw/.
set -euo pipefail
cd "$(dirname "$0")"

echo "== WP1: building registry =="
python3 etl/parse_dhcr_pdf.py
python3 etl/load_rentstab.py
python3 etl/load_pluto.py
python3 etl/build_registry.py

echo
echo "== WP2: geo layer =="
python3 geo/load_parks.py
python3 geo/build_geo.py

echo
echo "Done."
echo "  Building registry -> data/processed/registry.csv"
echo "  Park geometry      -> geo/parks.geojson"
echo "  Validation run     -> geo/build_geo_output.json"

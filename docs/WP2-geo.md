# WP2 - Geo Layer

Status date: 2026-09-19.

## 0. Environment constraint that shaped everything below

Every outbound HTTPS request attempted during this work package was
rejected by the sandbox's egress proxy with `403` on `CONNECT`,
including:

- `data.cityofnewyork.us` (Socrata / NYC Open Data - Parks Properties)
- `opendata.arcgis.com`, `nyc.maps.arcgis.com`, `services5.arcgis.com` (NTA boundary sources)
- `router.project-osrm.org` (OSRM public demo routing server)
- `raw.githubusercontent.com`, `s3.amazonaws.com` (common cached-data mirrors)
- `pypi.org` / `files.pythonhosted.org` (so `pip install geopandas shapely duckdb` also failed)
- even `https://example.com` returned the same 403

This was a blanket proxy denial, not something specific to one host, and
no retry, alternate host, or escalated method was attempted (per the
task's instructions not to route around a denial). Concretely, the pip
install command that was denied/failed:

```
pip3 install --break-system-packages shapely
-> ERROR: Could not find a version that satisfies the requirement shapely (from versions: none)
```

and the OSRM call that was denied:

```
curl https://router.project-osrm.org/route/v1/foot/-73.97,40.68;-73.968,40.681
-> curl: (56) CONNECT tunnel failed, response 403
```

Everything below was therefore built with pure Python 3.11 stdlib +
numpy (already installed) rather than geopandas/shapely/duckdb-spatial,
and against **documented schemas with manually-estimated placeholder
data**, per the task's explicit fallback instruction ("if a dataset is
unreachable, write the loader against its documented schema, record the
exact manual fetch step needed, and move on"). Nothing here should be
read as authoritative geometry -- see the "how to redo with live data"
section at the end.

## 1. Neighborhood polygons (`geo/neighborhoods.geojson`)

**Because the NTA source itself was unreachable**, these 8 polygons are
hand-authored approximations (city-block-level precision, not survey
grade) based on general knowledge of each neighborhood's commonly used
street boundaries, not digitized from the official NTA shapefile. Every
polygon's `boundary_note` property in the GeoJSON records the streets
used. Summary:

| Neighborhood | N | E | S | W |
|---|---|---|---|---|
| Greenpoint | Newtown Creek | McGuinness Blvd | Metropolitan Ave / McCarren Park | East River |
| Williamsburg | Metropolitan Ave | Flushing Ave corridor | Flushing Ave / Navy Yard | East River |
| Fort Greene | Flushing Ave / Navy Yard | **Washington Ave** | Atlantic Ave | Flatbush Ave / Navy St / BQE |
| Clinton Hill | Flushing Ave | Classon Ave | Grand Ave / Atlantic Ave | **Washington Ave** |
| Prospect Heights | Atlantic Ave | Grand/Washington Ave | Eastern Pkwy / Grand Army Plaza | Flatbush Ave / 4th Ave |
| Park Slope | Grand Army Plaza / Flatbush Ave | Prospect Park West | 15th St / Prospect Expwy | 4th Ave |
| Carroll Gardens | **Degraw St** | Court/Smith St | 9th St / Hamilton Ave | Hicks St / BQE |
| Cobble Hill | Atlantic Ave | Court St | **Degraw St** | Hicks St / BQE |

### Hand-tuning departures from official NTA boundaries (as instructed)

1. **Fort Greene / Clinton Hill split.** These are already separate
   NTAs in the post-2020 NTA redistricting, but the neighborhood
   boundary in common real-estate/resident use (and the one used here)
   runs along **Washington Avenue**, not the NTA's own cutline (which,
   from general knowledge, tends to run closer to Vanderbilt
   Ave/Grand Ave, pulling a strip that residents call "Clinton Hill"
   into the Fort Greene NTA). Net effect: this polygon set gives
   Clinton Hill a slightly wider western edge (out to Washington Ave)
   than the strict NTA would.
   **This is the boundary call I am least confident about** -- I could
   not verify the exact official NTA cutline without the blocked fetch,
   so the "departure" is asserted from general knowledge of how the two
   names are used locally, not measured against the real polygon.

2. **Cobble Hill / Carroll Gardens split.** The 2020 NTA scheme merges
   Cobble Hill into "Brooklyn Heights-Cobble Hill" (BK-ish code) and
   merges Carroll Gardens into "Carroll Gardens-Columbia Street-Red
   Hook". Both of those NTAs cover much more territory than the
   neighborhood names alone (Brooklyn Heights and Red Hook/Columbia St
   Waterfront respectively). This work package needed just Cobble Hill
   and just Carroll Gardens, so both merged NTAs were conceptually cut
   along **Degraw Street** (the commonly used line between the two
   neighborhoods), with Cobble Hill's other three sides at Atlantic Ave
   (border w/ Brooklyn Heights), Court St (border w/ Boerum Hill), and
   Hicks St/BQE (border w/ Brooklyn Heights/Red Hook); Carroll Gardens'
   other three sides at Court/Smith St (border w/ Boerum Hill/Gowanus),
   9th St/Hamilton Ave (border w/ Red Hook), and Hicks St/BQE (border
   w/ Columbia St Waterfront/Red Hook).

3. All 8 polygons are simplified to 5-7 vertices and do not follow the
   actual shoreline or exact parcel lines -- acceptable for a
   neighborhood-classification/ranking use case but not for anything
   requiring parcel-level accuracy (e.g. computing exact land area).

### How to redo this with live data
Once `data.cityofnewyork.us` or an ArcGIS NTA endpoint is reachable:
1. Fetch the 2020 NTA GeoJSON (dataset commonly published as "2020
   Neighborhood Tabulation Areas (NTAs)" on NYC Open Data / DCP).
2. Select the NTAs covering Greenpoint, Williamsburg, Fort Greene,
   Clinton Hill, Prospect Heights, Park Slope, and the merged Cobble
   Hill / Carroll Gardens NTAs.
3. For Cobble Hill vs Carroll Gardens and (if the real NTA cutline
   differs from Washington Ave) Fort Greene vs Clinton Hill, load the
   NTA polygon into geopandas/shapely, split it with a `LineString`
   traced along the actual street centerline (from NYC's LION street
   centerline dataset) at Degraw St / Washington Ave, and assign each
   half by centroid side.
4. Re-export as GeoJSON with the same `name` property schema used here.

## 2. Park polygons (`geo/load_parks.py`, `geo/parks.geojson`)

`geo/load_parks.py` is written against the documented Socrata schema for
dataset `enfh-gkve` ("Parks Properties"): fields `objectid`,
`gispropnum`, `signname`, `name311`, `address`, `borough`,
`typecategory`, `acreage`, `department`, and geometry field `the_geom`
(MultiPolygon). It queries `borough='K'` plus a `within_box` filter built
from the neighborhoods' bounding box + a ~1 mile (0.0145 deg) buffer.

**Live fetch could not be performed** (see section 0). Running the
script prints the exact failure and falls back to a clearly-labeled
placeholder (`source: "PLACEHOLDER_manual_estimate_not_live_data"` on
every feature) built from manually-estimated single points (or, for the
two large/linear parks, a handful of boundary-ish sample points) rather
than real polygons.

**Manual fetch step to get real data:**
```
curl -o geo/parks_raw.json \
  "https://data.cityofnewyork.us/resource/enfh-gkve.json?borough=K&\$limit=5000"
```
then re-run `load_parks.py` (extend it to accept `--from-file` and reuse
`rows_to_geojson()`) to filter to the neighborhoods' bounding box.

### Anchor park status

| Anchor park | Expected in `enfh-gkve`? | Why |
|---|---|---|
| McCarren Park | Yes | NYC Parks Dept (DPR) property |
| WNYC Transmitter Park | Yes | DPR property |
| McGolrick Park | Yes | DPR property |
| Domino Park | **No** | Privately owned/managed public space (Two Trees Management), not DPR |
| Marsha P. Johnson State Park | **No** | NY State Parks (OPRHP) property, formerly East River State Park; not DPR |
| Fort Greene Park | Yes | DPR flagship park |
| Prospect Park | Yes | DPR property (Prospect Park Alliance runs day-to-day ops but City owns it) |
| Mount Prospect Park | Yes | Small DPR park, Prospect Heights |
| Washington Park | Yes, but **unconfirmed identity** | Fort Greene Park's historical name was "Washington Park," so this anchor may actually refer to a separate, smaller DPR site near the Fort Greene/Clinton Hill line rather than a distinct third park. Could not disambiguate without a live query. Flagged for manual confirmation once the dataset is reachable. |
| Carroll Park | Yes | DPR property, Carroll Gardens |
| Cobble Hill Park | Yes | DPR property |
| Brooklyn Bridge Park | **No** (or listed with non-DPR department) | Run by the Brooklyn Bridge Park Corporation, a separate state/city authority, not DPR proper |

**None of this "Yes/No" is confirmed against live data** -- it is
inferred from general knowledge of each park's operating jurisdiction
and documented as such in `geo/load_parks.py`'s `ANCHOR_PARKS` dict. It
should be re-verified by grepping the live dataset for each name once
network access exists.

## 3. Distance function (`geo/distance.py`)

**Placeholder, not real walking routes.** The public OSRM demo server
(`router.project-osrm.org`) was tried first as instructed and was
rejected by the proxy (`403` on `CONNECT` -- see section 0). No other
routing API was reachable either.

The implemented placeholder is haversine (great-circle) distance
multiplied by a flat 1.3 "circuity factor" meant to roughly approximate
that real walking routes on a street grid are longer than straight-line
distance. This factor is a guess, not calibrated against any real
routing data, and is documented as such at the top of `distance.py`.

Known failure modes of the placeholder (documented in code):
- Understates distance across real barriers: the Gowanus Canal, the
  BQE trench, rail yards, and the waterfront, where an actual walking
  route must detour around rather than cross directly.
- Gets relative "closer vs farther" rankings roughly right in most
  cases given Brooklyn's fairly regular grid, but can flip close calls
  and is wrong in absolute magnitude.

All distance calculations go through one function,
`walking_distance_m(origin, dest)`, so swapping in real routing later
means: implement `_walking_distance_m_via_routing_api()` (stub already
present, with an OSRM-shaped example in the docstring) and flip
`ACTIVE_BACKEND = "routing_api"`. No other file needs to change.

The distance-to-a-park functions (`distance_to_park_boundary_m`,
`nearest_park`, `distance_to_each_park`) measure to the **nearest
boundary/entrance sample point**, not a single centroid -- for small
parks the sample list is a single representative point (centroid and
boundary are close enough not to matter), but Prospect Park and Brooklyn
Bridge Park are represented by several points spread around their
edges/entrances so that "distance to the park" reflects the nearest
edge rather than the geographic middle of a very large or long, thin
park.

## 4. End-to-end pipeline (`geo/build_geo.py`)

Loads `neighborhoods.geojson` and `parks.geojson`, classifies each input
point into a neighborhood (ray-casting point-in-polygon), and computes
nearest-park + per-anchor-park distances via `distance.py`. Run:

```
python3 geo/build_geo.py             # runs the built-in validation set below
python3 geo/build_geo.py points.json # or a custom [{"label","lat","lon"}, ...] file
```

Full output (JSON) is written to `geo/build_geo_output.json`.

## 5. Validation table

All distances below are the **placeholder** straight-line*1.3 walking
approximation, not real routes. Sanity-checked by general knowledge of
Brooklyn geography.

| Address (approx.) | Neighborhood assigned | Nearest park | Distance | Sanity check |
|---|---|---|---|---|
| 1 Prospect Park West | Park Slope | Prospect Park | 0.27 mi / ~5 min | Matches expectation: PPW buildings face the park directly, should be a very short walk. |
| Manhattan Ave & Milton St | Greenpoint | WNYC Transmitter Park | 0.42 mi / ~8 min | Plausible -- this block is roughly equidistant between McCarren and the East River waterfront parks; placeholder picked the waterfront park. |
| Withers St / McCarren Park block | Greenpoint | McCarren Park | 0.09 mi / ~2 min | Matches expectation for a block directly on McCarren. |
| DeKalb Ave & S Portland Ave | Fort Greene | Fort Greene Park | 0.07 mi / ~1.5 min | Matches expectation: this block faces Fort Greene Park directly. |
| Classon Ave & Greene Ave | Clinton Hill | "Washington Park" (unconfirmed identity) | 0.62 mi / ~12 min | Plausible order of magnitude for Clinton Hill's core, which is not adjacent to a major park; flagged because the "nearest park" name itself is the low-confidence anchor. |
| Vanderbilt Ave & Sterling Pl | Prospect Heights | Mount Prospect Park | 0.28 mi / ~5.6 min | Plausible -- Mount Prospect Park is the small park right off Eastern Parkway near Grand Army Plaza, a reasonable nearest-park pick for this block. |
| President St & Court St | Carroll Gardens | Carroll Park | 0.10 mi / ~2 min | Matches expectation: this intersection is essentially at Carroll Park. |
| Clinton St & Kane St | Cobble Hill | Cobble Hill Park | 0.07 mi / ~1.4 min | Matches expectation: this is one block from Cobble Hill Park. |
| Bedford Ave & N 7th St | Williamsburg | Marsha P. Johnson State Park | 0.50 mi / ~10 min | Plausible in magnitude, but the nearest-park *name* here is the state park anchor that is expected to be absent from the NYC Parks Dept dataset -- once live data is loaded this should likely resolve to McCarren Park or Domino Park instead, both of which are closer in reality; flags that the placeholder point set is incomplete/approximate. |
| Furman St (Brooklyn Heights/DUMBO edge) | *None* (falls outside all 8 polygons, expected -- Brooklyn Heights/DUMBO are not in our 8-neighborhood set) | Brooklyn Bridge Park | 0.23 mi / ~4.6 min | Matches expectation: Furman St runs right along Brooklyn Bridge Park. Neighborhood classifier correctly returns `None` since this address isn't in any of the 8 target neighborhoods. |

## 6. Summary of what's real vs. placeholder

| Deliverable | Status |
|---|---|
| `geo/neighborhoods.geojson` | Hand-authored approximate polygons (not sourced from a live NTA fetch); street-boundary rationale documented per polygon and above |
| `geo/load_parks.py` | Real loader code against the documented Socrata schema; live fetch untested end-to-end (network blocked) but the fetch/parse/fallback path is exercised and works |
| `geo/parks.geojson` | **Placeholder** single/few-point park locations, clearly flagged in every feature's `source` property |
| `geo/distance.py` | **Placeholder** straight-line*1.3 walking approximation, real routing backend stubbed and documented |
| `geo/build_geo.py` | Fully functional end-to-end pipeline; only as accurate as the inputs above |

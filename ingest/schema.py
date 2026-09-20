#!/usr/bin/env python3
"""
ingest/schema.py -- the normalized listing record every WP3 source maps
into, and the sqlite3 DDL for its `listings` table.

This is the single contract between "however a listing source shapes its
raw data" (ingest/apify_streeteasy.py today; a future RentHop/other-actor
client tomorrow) and everything downstream (ingest/store.py, and later the
scoring engine / UI). Every field here is intentionally source-agnostic --
none of Apify's or StreetEasy's own field names appear as column names, so
adding a second source later never requires touching the schema, only a
new normalize_<source>() function that produces this same shape.

FIELDS
    source              TEXT -- short slug identifying the pipeline that
                          produced this row, e.g. "apify_streeteasy".
    source_listing_id   TEXT -- the id the source uses for this listing
                          (e.g. StreetEasy's numeric listing id, or a slug
                          parsed from its URL). Combined with `source` this
                          is the natural key -- see store.py's upsert.
    url                 TEXT -- canonical listing URL.
    raw_address         TEXT -- street address as given by the source,
                          unparsed (no BBL/geocoding is done here -- that
                          is WP1/WP2's job once this feeds the join layer).
    unit                TEXT -- apartment/unit number, if the source
                          reports one separately from the street address.
    beds                REAL -- bedroom count. 0 = studio. Nullable if the
                          source didn't report it (such rows fail the
                          normalize.py hard filter and are dropped, since
                          the target filter requires a known 1 or 2).
    baths               REAL -- bathroom count (0.5 baths are common in
                          NYC listings, hence REAL not INTEGER).
    rent                REAL -- monthly asking rent in USD.
    sqft                REAL -- square footage, nullable (StreetEasy often
                          omits this for older buildings).
    no_fee              INTEGER (0/1/NULL) -- "no broker fee" flag.
    broker_name         TEXT -- listing agent or brokerage name.
    first_seen          TEXT -- ISO8601 UTC timestamp this row was first
                          ingested. Set once, never overwritten by upserts.
    last_seen           TEXT -- ISO8601 UTC timestamp this row was most
                          recently seen in a source pull. Updated on every
                          re-ingest that finds the same (source,
                          source_listing_id).
    price_history       TEXT (JSON) -- list of {"date": ..., "price": ...}
                          entries. On upsert, a new price observation is
                          appended (see store.py) rather than overwritten,
                          so this field accumulates across runs even if
                          the source's own priceHistory array is partial
                          or absent on a given pull.
    amenities           TEXT (JSON) -- list of amenity strings, as given.
    latitude            REAL
    longitude           REAL
    raw_zip             TEXT -- zip code exactly as given by the source
                          (used for the target-zip hard filter in
                          normalize.py; NOT re-derived from geocoding).

PRIMARY KEY / UPSERT KEY
    (source, source_listing_id) -- see ingest/store.py.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Optional

# Column order also defines the sqlite3 table's column order.
LISTING_FIELDS = [
    "source",
    "source_listing_id",
    "url",
    "raw_address",
    "unit",
    "beds",
    "baths",
    "rent",
    "sqft",
    "no_fee",
    "broker_name",
    "first_seen",
    "last_seen",
    "price_history",
    "amenities",
    "latitude",
    "longitude",
    "raw_zip",
]


@dataclass
class Listing:
    """One normalized listing row. See module docstring for field meanings."""

    source: str
    source_listing_id: str
    url: Optional[str] = None
    raw_address: Optional[str] = None
    unit: Optional[str] = None
    beds: Optional[float] = None
    baths: Optional[float] = None
    rent: Optional[float] = None
    sqft: Optional[float] = None
    no_fee: Optional[int] = None
    broker_name: Optional[str] = None
    first_seen: Optional[str] = None
    last_seen: Optional[str] = None
    price_history: Optional[str] = None  # JSON string
    amenities: Optional[str] = None  # JSON string
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    raw_zip: Optional[str] = None

    def as_tuple(self):
        return tuple(getattr(self, f) for f in LISTING_FIELDS)


assert [f.name for f in fields(Listing)] == LISTING_FIELDS, (
    "Listing dataclass fields must match LISTING_FIELDS exactly and in order "
    "-- store.py's positional SQL relies on this."
)

# sqlite3 DDL. TEXT/REAL/INTEGER only (no JSON column type in sqlite3;
# price_history/amenities are stored as TEXT containing a JSON string,
# same pattern as build_registry.py's stab_units_by_year column).
CREATE_LISTINGS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS listings (
    source              TEXT NOT NULL,
    source_listing_id   TEXT NOT NULL,
    url                 TEXT,
    raw_address         TEXT,
    unit                TEXT,
    beds                REAL,
    baths               REAL,
    rent                REAL,
    sqft                REAL,
    no_fee              INTEGER,
    broker_name         TEXT,
    first_seen          TEXT,
    last_seen           TEXT,
    price_history       TEXT,
    amenities           TEXT,
    latitude            REAL,
    longitude           REAL,
    raw_zip             TEXT,
    PRIMARY KEY (source, source_listing_id)
);
"""

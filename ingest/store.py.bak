#!/usr/bin/env python3
"""
ingest/store.py -- persist normalized Listing rows into the SAME sqlite3
store WP1 already uses (etl/common.py's get_connection() / DB_PATH,
data/interim/registry.db). No second database is created; `listings` is
just one more table alongside WP1's `registry`/`pluto`/
`dhcr_historical_trend` tables, so a future join (e.g. listing -> BBL ->
stabilization signal) is a same-database SQL join, not a cross-database
one.

UPSERT SEMANTICS on (source, source_listing_id) (the schema's declared
primary key -- see ingest/schema.py):
    - New key: INSERT the row as-is. first_seen/last_seen both come from
      normalize.py's fallback logic (source's listedAt/updatedAt, or the
      ingest wall-clock time if the source didn't report them).
    - Existing key: UPDATE in place --
        * last_seen is always overwritten with the CURRENT wall-clock
          ingest time (not the incoming row's last_seen), so it reflects
          "last time our pipeline actually saw this listing," which is
          more useful for detecting stale/delisted inventory than the
          source's own updatedAt.
        * first_seen is preserved from the existing row, never
          overwritten -- it's a "when did we first observe this" field.
        * price_history is MERGED, not replaced: the existing row's JSON
          array and the incoming row's JSON array are concatenated,
          deduped on (date, price) pairs, and re-sorted by date. This is
          what lets a partial/absent priceHistory on one particular pull
          (the actor's own priceHistory can be incomplete -- see WP0/
          apify_streeteasy.py notes) still accumulate a fuller history
          across repeated runs instead of losing earlier observations.
        * every other column (rent, amenities, broker_name, etc.) is
          overwritten with the incoming value, since those are
          point-in-time facts about the listing's current state, not
          historical accumulations.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import List, Tuple

from etl.common import get_connection
from ingest.schema import CREATE_LISTINGS_TABLE_SQL, LISTING_FIELDS, Listing


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(CREATE_LISTINGS_TABLE_SQL)
    conn.commit()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _merge_price_history(existing_json: str | None, incoming_json: str | None) -> str:
    existing = json.loads(existing_json) if existing_json else []
    incoming = json.loads(incoming_json) if incoming_json else []
    combined = {}
    for entry in [*existing, *incoming]:
        if not isinstance(entry, dict):
            continue
        key = (entry.get("date"), entry.get("price"))
        combined[key] = entry
    merged = sorted(combined.values(), key=lambda e: (e.get("date") or ""))
    return json.dumps(merged)


def upsert_listings(conn: sqlite3.Connection, listings: List[Listing]) -> Tuple[int, int]:
    """Insert-or-update each Listing. Returns (new_count, updated_count)."""
    ensure_table(conn)
    now = _utc_now_iso()
    new_count = 0
    updated_count = 0

    cur = conn.cursor()
    for listing in listings:
        cur.execute(
            "SELECT price_history, first_seen FROM listings "
            "WHERE source = ? AND source_listing_id = ?",
            (listing.source, listing.source_listing_id),
        )
        row = cur.fetchone()

        if row is None:
            values = dict(zip(LISTING_FIELDS, listing.as_tuple()))
            values["last_seen"] = now
            if not values.get("first_seen"):
                values["first_seen"] = now
            placeholders = ", ".join(["?"] * len(LISTING_FIELDS))
            cur.execute(
                f"INSERT INTO listings ({', '.join(LISTING_FIELDS)}) "
                f"VALUES ({placeholders})",
                tuple(values[f] for f in LISTING_FIELDS),
            )
            new_count += 1
        else:
            existing_price_history, existing_first_seen = row
            merged_history = _merge_price_history(existing_price_history, listing.price_history)
            update_fields = [f for f in LISTING_FIELDS if f not in ("source", "source_listing_id")]
            values = dict(zip(LISTING_FIELDS, listing.as_tuple()))
            values["last_seen"] = now
            values["first_seen"] = existing_first_seen  # never overwritten
            values["price_history"] = merged_history
            set_clause = ", ".join(f"{f} = ?" for f in update_fields)
            cur.execute(
                f"UPDATE listings SET {set_clause} "
                f"WHERE source = ? AND source_listing_id = ?",
                (*[values[f] for f in update_fields], listing.source, listing.source_listing_id),
            )
            updated_count += 1

    conn.commit()
    print(f"store: {new_count} new listings inserted, {updated_count} existing listings updated")
    return new_count, updated_count


def count_listings(conn: sqlite3.Connection) -> int:
    ensure_table(conn)
    return conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0]

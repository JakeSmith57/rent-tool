#!/usr/bin/env python3
"""
ingest/normalize.py -- maps a raw solidcode/streeteasy-scraper dataset item
(see ingest/apify_streeteasy.py's docstring for the confirmed real output
field names) into an ingest/schema.py Listing, and applies WP3's hard
filters.

HARD FILTERS (per the WP3 brief -- applied here, not left to a later stage,
so nothing outside scope ever reaches the store):
    1. beds in {1, 2} -- studios (0) and 3+BR are dropped. A record whose
       `bedrooms` is null/missing is also dropped (we do not guess).
    2. raw_zip in TARGET_ZIPS -- the same 10-zip set as etl/common.py's
       STARTING_ZIPS (imported from there, not re-typed, so WP1 and WP3
       can never silently drift apart on the target-area definition).

FIELD MAPPING (raw solidcode/streeteasy-scraper key -> Listing field)
    id                  -> source_listing_id (str-cast; the actor returns int)
    url                 -> url
    address + unit      -> raw_address, unit (kept separate; StreetEasy's
                           `address` field is street-only, unit is separate)
    bedrooms            -> beds
    bathrooms           -> baths
    price               -> rent
    sizeSqft            -> sqft
    isNoFee             -> no_fee (bool -> 0/1)
    brokerageName       -> broker_name
    zipCode             -> raw_zip
    latitude/longitude  -> latitude/longitude
    priceHistory        -> price_history (JSON re-dumped, not re-shaped --
                           store.py is what appends new observations on
                           later runs)
    amenities + buildingAmenities -> amenities (merged into one JSON list;
                           the schema has a single amenities column and
                           the source's own split between per-unit and
                           per-building amenities isn't meaningful to the
                           scoring engine downstream)
    listedAt            -> first_seen (fallback if the ingest run itself
                           doesn't already have a first_seen for this key;
                           see store.py -- normalize.py always fills it in
                           case this is genuinely the first time we've
                           heard of a listing, so a first-ever insert isn't
                           missing a first_seen)
    updatedAt           -> last_seen (fallback; store.py overwrites this
                           with the wall-clock ingest time on every run
                           regardless, since "did our pipeline see it" is
                           more useful than the source's own updatedAt for
                           freshness tracking)

Fields intentionally NOT carried into the normalized schema (out of scope
for WP3; available in the raw record if a later WP needs them): title,
propertyType, status, bedroomsDescription, bathroomsDescription,
daysOnMarket, description, isFurnished, petPolicy, outdoorSpace,
buildingType, buildingYearBuilt, buildingIsPreWar, buildingFloorCount,
buildingUnitCount, buildingIsNewDevelopment, pricePerSqft, saleType,
lastPriceChangeAmount/Date, maintenance, taxes, estimatedMonthlyPayment,
maxFinancing, rentalFees, freeMonths, leaseTerm, city, state, neighborhood,
neighborhoodSlug, borough, buildingName, nearbySubways, nearbySchools,
agentName/Email/Phone/PhoneAlt/ProfileUrl, contacts, heroPhotoUrl,
thumbnailUrl, photos, floorplans, videoUrls, tour3dUrl, areaId,
openHouses, comparableListings, buildingUrl, buildingId,
managementCompanyProfileUrl, isLandLease.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import hashlib

from etl.common import STARTING_ZIPS, TARGET_NEIGHBORHOODS
from ingest.schema import Listing

SOURCE_NAME = "apify_streeteasy"
EMAIL_ALERT_SOURCE_NAME = "streeteasy_email_alert"

# Same target-zip set WP1 uses -- imported, not redefined, per the module
# docstring above.
TARGET_ZIPS = STARTING_ZIPS

# The hard bedroom filter for this project: 1BR or 2BR only, no studios.
TARGET_BEDS = {1, 2}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_bool_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    return 1 if bool(value) else 0


def _merge_amenities(raw: Dict[str, Any]) -> List[str]:
    unit_amenities = raw.get("amenities") or []
    building_amenities = raw.get("buildingAmenities") or []
    # dict.fromkeys instead of set() to keep a stable, dedup'd order --
    # matters only for readability/diffing, not correctness.
    merged = list(dict.fromkeys([*unit_amenities, *building_amenities]))
    return merged


def normalize_one(raw: Dict[str, Any]) -> Optional[Listing]:
    """Map one raw solidcode/streeteasy-scraper record to a Listing, or
    return None if it fails a hard filter (see module docstring).

    Returns None (rather than raising) for filtered-out or malformed
    records so a caller can just `filter(None, ...)` over a raw batch --
    see normalize_batch() below, which also reports counts.
    """
    beds_raw = raw.get("bedrooms")
    beds = _to_float(beds_raw)
    if beds is None or int(beds) not in TARGET_BEDS:
        return None

    raw_zip = str(raw.get("zipCode") or "").strip()
    if raw_zip not in TARGET_ZIPS:
        return None

    source_listing_id = str(raw.get("id"))
    if not source_listing_id or source_listing_id == "None":
        return None

    now = _utc_now_iso()
    price_history = raw.get("priceHistory") or []

    return Listing(
        source=SOURCE_NAME,
        source_listing_id=source_listing_id,
        url=raw.get("url"),
        raw_address=raw.get("address"),
        unit=raw.get("unit"),
        beds=beds,
        baths=_to_float(raw.get("bathrooms")),
        rent=_to_float(raw.get("price")),
        sqft=_to_float(raw.get("sizeSqft")),
        no_fee=_to_bool_int(raw.get("isNoFee")),
        broker_name=raw.get("brokerageName"),
        first_seen=raw.get("listedAt") or now,
        last_seen=raw.get("updatedAt") or now,
        price_history=json.dumps(price_history),
        amenities=json.dumps(_merge_amenities(raw)),
        latitude=_to_float(raw.get("latitude")),
        longitude=_to_float(raw.get("longitude")),
        raw_zip=raw_zip,
    )


def _target_neighborhood_lower() -> set:
    return {n.lower() for n in TARGET_NEIGHBORHOODS}


def normalize_email_alert(raw: Dict[str, Any]) -> Optional[Listing]:
    """Map one raw card from ingest/parse_streeteasy_email.py's
    extract_listing_cards() into a Listing, applying WP3b's hard filters.

    HARD FILTERS (mirrors normalize_one()'s intent, different fields since
    this source has no zip -- see etl/common.py's TARGET_NEIGHBORHOODS
    docstring for why neighborhood-name matching is used here instead):
        1. beds in {1, 2}.
        2. neighborhood (case-insensitive, exact) in TARGET_NEIGHBORHOODS.
           StreetEasy's own alert already scopes results to the saved
           search, but the "Similar listings" section in the same email
           can include off-target neighborhoods/bed counts (confirmed in
           the real sample -- a 3BR in East Williamsburg turned up there),
           so this filter is still load-bearing, not a formality.

    SOURCE_LISTING_ID: alert emails don't expose StreetEasy's own numeric
    listing id anywhere in the visible card (only a one-time click-tracking
    redirect URL, which isn't a stable identifier and isn't worth resolving
    via a live HTTP follow just to mint an id). Instead we hash
    (address, neighborhood) -- stable across repeated alerts for the same
    unit, which is exactly what the upsert key in ingest/store.py needs for
    price-history accumulation to work. Two different real units that
    happen to share both a raw address string and neighborhood would
    collide; accepted as a rare, low-stakes edge case for a personal-use
    tool (a re-listed identical address is far more likely than a genuine
    collision).
    """
    beds = raw.get("beds")
    if beds is None or int(beds) not in TARGET_BEDS:
        return None

    neighborhood = (raw.get("neighborhood") or "").strip()
    if neighborhood.lower() not in _target_neighborhood_lower():
        return None

    address = (raw.get("raw_address") or "").strip()
    if not address:
        return None

    id_source = f"{address.lower()}|{neighborhood.lower()}"
    source_listing_id = hashlib.sha1(id_source.encode("utf-8")).hexdigest()[:16]

    now = _utc_now_iso()
    price_history = [{"date": now, "price": raw.get("price")}] if raw.get("price") is not None else []

    return Listing(
        source=EMAIL_ALERT_SOURCE_NAME,
        source_listing_id=source_listing_id,
        url=raw.get("url"),
        raw_address=address,
        unit=None,  # unit is embedded in raw_address for this source (e.g. "216 7th St. #1")
        beds=float(beds),
        baths=_to_float(raw.get("baths")),
        rent=_to_float(raw.get("price")),
        sqft=_to_float(raw.get("sqft")),
        no_fee=_to_bool_int(raw.get("no_fee")),
        broker_name=None,
        first_seen=now,
        last_seen=now,
        price_history=json.dumps(price_history),
        amenities=json.dumps([]),
        latitude=None,
        longitude=None,
        raw_zip=None,  # this source has no zip -- see module docstring
    )


def normalize_email_alert_batch(raw_cards: List[Dict[str, Any]]) -> Tuple[List[Listing], int, int]:
    """Same shape as normalize_batch(), for the email-alert source."""
    raw_count = len(raw_cards)
    kept: List[Listing] = []
    for raw in raw_cards:
        listing = normalize_email_alert(raw)
        if listing is not None:
            kept.append(listing)
    dropped_count = raw_count - len(kept)
    print(
        f"normalize_email_alert: {raw_count} raw cards -> {len(kept)} passed "
        f"bed/neighborhood filter ({dropped_count} dropped)"
    )
    return kept, raw_count, dropped_count


def normalize_batch(raw_items: List[Dict[str, Any]]) -> Tuple[List[Listing], int, int]:
    """Normalize a raw batch, applying the hard filters.

    Returns (kept_listings, raw_count, dropped_count).
    """
    raw_count = len(raw_items)
    kept: List[Listing] = []
    for raw in raw_items:
        listing = normalize_one(raw)
        if listing is not None:
            kept.append(listing)
    dropped_count = raw_count - len(kept)
    print(
        f"normalize: {raw_count} raw -> {len(kept)} passed bed/zip filter "
        f"({dropped_count} dropped: not 1-2BR, not in target zips, or malformed)"
    )
    return kept, raw_count, dropped_count

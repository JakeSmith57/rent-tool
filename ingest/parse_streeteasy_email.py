#!/usr/bin/env python3
"""
ingest/parse_streeteasy_email.py -- WP3b: parse a StreetEasy "saved search"
alert email (the free, official notification feature -- ToS-compliant,
$0 cost) into raw listing-card dicts, ready for normalize_email_alert()
in ingest/normalize.py.

STATUS: built and unit-tested against a REAL StreetEasy alert email
(fixtures/sample_streeteasy_alert_2019.html -- an actual email pulled from
this Gmail account, dated Jan 2019). No email from the NEW saved search
(set up this session for the 8 target neighborhoods) has arrived yet as of
this writing -- a live Gmail search for `from:streeteasy.com newer_than:30d`
came back empty. Because the sample is ~7 years old, StreetEasy's email
template may well have changed since; this parser needs to be re-verified
(and likely tweaked) against the first email the new saved search actually
sends. See docs/WP3b-email-alerts.md for that follow-up step.

WHY THIS SOURCE HAS NO ZIP CODE (unlike the Apify path)
    Alert emails show a StreetEasy neighborhood name ("in Park Slope"), not
    a zip code or full address-with-zip. So this source's hard filter is by
    NEIGHBORHOOD NAME, not zip -- see TARGET_NEIGHBORHOODS below. This is a
    deliberate, source-specific difference from ingest/normalize.py's
    Apify-path filter (which matches on etl.common.STARTING_ZIPS); both
    normalize down to the same ingest/schema.py Listing shape either way.

EMAIL STRUCTURE (confirmed against the real sample)
    Each listing "card" is a <table align="right" ...> cell containing a
    <div style="font-size:14px"> whose text, line by line, is:
        <address + unit>                   e.g. "216 7th St. #1"
        ["NO FEE"]                         optional badge, own line
        "$<price>"
        "FOR RENT"
        "<beds> bed(s)|Studio  •  <baths> bath(s) [• <sqft> ft²]"
        ["Rental Unit" | "Condo/Co-op" | ...]   optional unit-type line
        "in <Neighborhood>"
        ["NEW"]                            optional badge
        ["OPEN HOUSE: ..."]                optional badge (may wrap lines)
        "SEE DETAILS"                       always last -- its href is the
                                             listing URL (a send.streeteasy.com
                                             click-tracking redirect, not the
                                             canonical streeteasy.com URL --
                                             see normalize_email_alert()'s
                                             docstring for why that's fine).
    Every card ends at its "SEE DETAILS" anchor, found by walking UP from
    that anchor to its nearest ancestor <div style="font-size:14px">
    (confirmed unique per card in the real sample) and reading that div's
    full text -- far more robust than trying to regex the raw nested-table
    HTML directly.

    The email also has a "Similar listings:" section with looser matches
    (a 3BR turned up in the real sample even though the saved search was
    1-2BR only) -- this parser does NOT special-case that section; the
    same hard filters in normalize_email_alert() drop anything off-target
    regardless of which section it came from.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup

PRICE_SEARCH_RE = re.compile(r"\$([\d,]+)")
BEDS_BATHS_RE = re.compile(
    r"^(Studio|(\d+)\s*beds?)\s*•\s*([\d.]+)\s*baths?(?:\s*•\s*([\d,]+)\s*ft²)?$",
    re.IGNORECASE,
)
NEIGHBORHOOD_RE = re.compile(r"^in\s+(.+)$")
OPEN_HOUSE_RE = re.compile(r"^OPEN HOUSE", re.IGNORECASE)
KNOWN_FIELD_STARTS = ("FOR RENT", "NEW", "NO FEE", "SEE DETAILS", "in ")


def _parse_beds_baths(line: str) -> Optional[Dict[str, Any]]:
    m = BEDS_BATHS_RE.match(line)
    if not m:
        return None
    studio_or_beds, beds_num, baths, sqft = m.groups()
    beds = 0 if studio_or_beds.lower() == "studio" else int(beds_num)
    return {
        "beds": beds,
        "baths": float(baths),
        "sqft": float(sqft.replace(",", "")) if sqft else None,
    }


def _parse_card(lines: List[str], url: Optional[str]) -> Optional[Dict[str, Any]]:
    """lines: the card div's text, already split+stripped+non-empty.
    Returns a raw dict (schema below) or None if it doesn't look like a
    real listing card (defensive -- e.g. if a future template change
    breaks an assumption, we want to skip and warn, not crash the batch).
    """
    if not lines:
        return None

    address_line = lines[0]
    no_fee = False
    price = None
    beds_baths = None
    neighborhood = None
    is_new = False
    open_house_lines: List[str] = []
    in_open_house = False

    for line in lines[1:]:
        if line == "SEE DETAILS":
            break  # nothing meaningful follows this in a card's text
        if OPEN_HOUSE_RE.match(line):
            in_open_house = True
            open_house_lines.append(line)
            continue
        if in_open_house:
            # OPEN HOUSE text can wrap onto a following line ("SAT 2-4PM,
            # BY APPT ONLY") with no distinguishing marker of its own --
            # keep consuming until we hit another known field's line.
            if line == "NEW" or line.startswith(KNOWN_FIELD_STARTS) or PRICE_SEARCH_RE.search(line) or _parse_beds_baths(line):
                in_open_house = False
            else:
                open_house_lines.append(line)
                continue
        if line == "NEW":
            is_new = True
            continue
        if line == "FOR RENT":
            continue
        if "NO FEE" in line:
            no_fee = True
        m = PRICE_SEARCH_RE.search(line)
        if m:
            price = float(m.group(1).replace(",", ""))
            continue
        bb = _parse_beds_baths(line)
        if bb:
            beds_baths = bb
            continue
        m = NEIGHBORHOOD_RE.match(line)
        if m:
            neighborhood = m.group(1).strip()
            continue
        # Unmatched lines (e.g. "Rental Unit", "Condo/Co-op") are ignored
        # -- not needed downstream.

    if beds_baths is None:
        # Couldn't find the one line every real card must have -- this
        # isn't a listing card (or the template changed). Skip, don't crash.
        return None

    return {
        "raw_address": address_line,
        "price": price,
        "no_fee": no_fee,
        "beds": beds_baths["beds"],
        "baths": beds_baths["baths"],
        "sqft": beds_baths["sqft"],
        "neighborhood": neighborhood,
        "is_new": is_new,
        "open_house": " ".join(open_house_lines) or None,
        "url": url,
    }


def extract_listing_cards(html: str) -> List[Dict[str, Any]]:
    """Parse one StreetEasy alert email's HTML body into a list of raw
    listing-card dicts (see _parse_card's return shape). Order preserved;
    duplicates (the same listing can appear once as a headline match and
    again under "Similar listings") are NOT deduped here -- that happens
    naturally downstream once normalize_email_alert() derives a stable
    source_listing_id and store.py upserts on it.
    """
    soup = BeautifulSoup(html, "html.parser")
    cards = []
    seen_divs = set()
    for anchor in soup.find_all("a"):
        if anchor.get_text(strip=True) != "SEE DETAILS":
            continue
        card_div = anchor.find_parent("div", style=lambda s: bool(s) and "font-size:14px" in s)
        if card_div is None or id(card_div) in seen_divs:
            continue
        seen_divs.add(id(card_div))
        # No forced separator: the source HTML already puts each field on
        # its own literal line inside the div (confirmed against the real
        # sample -- see module docstring); a forced "\n" separator here
        # would instead split every inline tag boundary too (e.g. the
        # "•" bullet's own <span>), breaking the beds/baths line apart.
        text = card_div.get_text()
        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
        parsed = _parse_card(lines, anchor.get("href"))
        if parsed:
            cards.append(parsed)
    return cards


if __name__ == "__main__":
    import json
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else "fixtures/sample_streeteasy_alert_2019.html"
    with open(path, encoding="utf-8") as f:
        html = f.read()
    cards = extract_listing_cards(html)
    print(f"Parsed {len(cards)} listing cards from {path}\n")
    print(json.dumps(cards, indent=2))

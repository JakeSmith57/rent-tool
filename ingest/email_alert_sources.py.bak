#!/usr/bin/env python3
"""
ingest/email_alert_sources.py -- WP3b extension (2026-09-20 improvement
work): a small registry so the $0, ToS-compliant "parse my own inbound
saved-search alert emails" pattern that was built for StreetEasy can be
extended to any OTHER site that offers the same free email-alert feature,
without duplicating the Gmail-search / dedup / normalize plumbing per
site.

WHY THIS EXISTS (see the deep-research improvement report's
listings_feeds.md research note for the full citations): no major NYC
rental site publishes a public API or RSS feed, and Zillow's and
RentHop's own Terms of Use explicitly forbid automated scraping
("automated queries...spiders, robots, crawlers"), while StreetEasy's
robots.txt disallows crawling /rental/* outright. The ONLY consistently
zero-risk ingestion pattern across sites is the one already built for
StreetEasy: you sign up for that site's own free "email me new matches"
saved-search feature (a real feature most of these sites offer, per the
research), and parse the emails IT sends YOU -- no request to the site's
servers happens outside what the site itself already initiated. This
registry makes adding a second (third, ...) site an isolated, mechanical
piece of work instead of a rewrite.

CURRENT STATUS: StreetEasy is the only source with a REAL, tested parser
(ingest/parse_streeteasy_email.py, built and verified against a real
sample email). No other site's alert-email HTML structure has been
observed yet, so no other parser exists -- this module is the framework
to add one to, not a claim that Zumper/RentHop/Naked Apartments support
is already built.

HOW TO ADD A NEW SOURCE (mirrors exactly how StreetEasy's WP3b was built)
    1. Sign up for that site's saved-search email alert feature (same
       search criteria: the 9 target neighborhoods, 1-2BR) using the
       same inbox the Gmail MCP connector reads.
    2. Wait for a real alert email to arrive, then save its raw HTML to
       fixtures/sample_<source>_alert.html (forward-and-view-source, or
       use the Gmail connector to fetch the message body directly).
    3. Write a `ingest/parse_<source>_email.py` module with one function,
       `extract_listing_cards(html: str) -> List[Dict[str, Any]]`, that
       returns raw card dicts shaped exactly like
       parse_streeteasy_email.py's: {"raw_address", "price", "no_fee",
       "beds", "baths", "sqft", "neighborhood", "is_new", "open_house",
       "url"} (any field can be None if that site's email doesn't carry
       it -- normalize_email_alert() in ingest/normalize.py only hard-
       requires beds, neighborhood, and raw_address; see its docstring).
    4. Register it below in SOURCE_PARSERS and GMAIL_SEARCH_QUERIES.
    5. Nothing else changes: normalize_email_alert_batch(cards,
       source=<key>) and the rest of the WP3b pipeline (dedup by Gmail
       message id, upsert into the sqlite store / this project's
       data/streeteasy-matches.json) already work for any registered
       source -- see the scheduled task's prompt (its trigger id is in
       state/email-alert-state.json) for how a session should loop over
       ALL registered sources on each run, not just StreetEasy.

Public interface:
    SOURCE_PARSERS       -- {source_key: extract_listing_cards_fn}
    GMAIL_SEARCH_QUERIES  -- {source_key: gmail search query string}
    parse_alert_email(source_key, html) -> List[Dict[str, Any]]
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List

from ingest.parse_streeteasy_email import extract_listing_cards as _parse_streeteasy

# Each entry: source_key -> a function(html: str) -> List[Dict[str, Any]]
# matching parse_streeteasy_email.extract_listing_cards()'s return shape.
# Add a new entry here once a real parser exists for that site (see the
# module docstring's "HOW TO ADD A NEW SOURCE" above) -- do not add a
# speculative/untested parser for a site whose real email HTML hasn't
# been observed yet; a wrong guess here would silently drop or
# mis-normalize every card from that source.
SOURCE_PARSERS: Dict[str, Callable[[str], List[Dict[str, Any]]]] = {
    "streeteasy": _parse_streeteasy,
}

# Gmail search query used to find each source's alert emails, passed to
# the Gmail MCP connector's search tool. Kept here (not hardcoded in the
# scheduled task's own prompt) so adding a source only requires one edit
# in one place. `newer_than:3d` mirrors the existing StreetEasy scheduled
# task's window (hourly checks only need to look back a few days to be
# safe against a missed run, not the full inbox history).
GMAIL_SEARCH_QUERIES: Dict[str, str] = {
    "streeteasy": "from:streeteasy.com newer_than:3d",
    # Add entries here as new sources are registered above, e.g.:
    #   "zumper": "from:zumper.com newer_than:3d",
    #   "renthop": "from:renthop.com newer_than:3d",
    # (Naked Apartments and Apartments.com were not confirmed in the
    # research to have a bulk saved-search email feature at all -- verify
    # that a real "email me new matches" option exists on the site itself
    # before setting one up, rather than assuming parity with StreetEasy.)
}


def parse_alert_email(source_key: str, html: str) -> List[Dict[str, Any]]:
    """Dispatch to the registered parser for `source_key`. Raises KeyError
    with a clear message if the source isn't registered yet, rather than
    silently returning an empty list (a caller looping over
    GMAIL_SEARCH_QUERIES.keys() should never hit this, but a caller
    passing a typo'd or not-yet-implemented source key should find out
    immediately, not lose data silently)."""
    if source_key not in SOURCE_PARSERS:
        raise KeyError(
            f"No email-alert parser registered for source {source_key!r}. "
            f"Registered sources: {sorted(SOURCE_PARSERS.keys())}. See this "
            "module's docstring for how to add a new one."
        )
    return SOURCE_PARSERS[source_key](html)

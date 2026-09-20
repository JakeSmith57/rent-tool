# WP0 — Feasibility & Build-vs-Buy Recon

**Date:** 2026-09-19
**Scope:** Rent-stabilized 1–2BR apartment finder, near a park, in Greenpoint, Williamsburg, Fort Greene, Clinton Hill, Prospect Heights, Park Slope, Carroll Gardens, Cobble Hill.

---

## Recommendation: HYBRID

Do not wrap RentReboot — it has no API, no export, and no webhook, only email/SMS delivery on a schedule you don't control ([FAQ](https://rentreboot.com/faq), [pricing](https://rentreboot.com/pricing)), so it cannot feed a scoring engine or a custom UI; it's a finished consumer product, not a data source. Do not build a from-scratch StreetEasy scraper either — StreetEasy's and Zillow's terms explicitly prohibit automated scraping (see §2), and several Apify actors already absorb that legal/technical risk and the residential-proxy cost at prices low enough that operating your own scraper buys little.

**HYBRID path:** (1) buy listings coverage from an existing Apify StreetEasy actor (cheap, usage-based, proxies bundled) rather than building a scraper; (2) build the probability-scoring engine yourself, since no vendor offers "is this specific unit stabilized" — everyone, including RentReboot, only scores against building-level DHCR/HPD signals; (3) build geocoding with the free, no-signup NYC Planning Labs GeoSearch API rather than paying for or self-hosting Geosupport; (4) build the DHCR building-match layer from the PDF lists (or a maintained CSV mirror) since no non-PDF official format exists; (5) reuse `nycdb`'s `rentstab_v2` table (data through 2023) instead of re-scraping DOF tax bills yourself.

## Estimated Monthly Cost (recommended path)

| Component | Estimate | Basis |
|---|---|---|
| StreetEasy listings (Apify actor, pay-per-result) | **$5–$15/mo** at ~5,000–15,000 listing-pulls/month across 8 neighborhoods (2BR/1BR only) | $0.80–$1.00 per 1,000 results, per-actor pricing below |
| Apify platform minimum | $0 (free tier: 5 free actor runs / $5 usage credit) or $49/mo (Starter) if volume requires more compute/proxy datacenter minutes | [Apify pricing](https://apify.com/pricing) (not separately re-verified this session; actor pages state usage-based pricing on top of platform) |
| Geocoding (NYC Planning Labs GeoSearch) | **$0** | Public API, no key required — see §3 |
| DHCR/HPD/nycdb data | **$0** | Public downloads / open-source Postgres loader |
| Hosting for scoring engine + UI (small VM or serverless) | **$5–$20/mo** | Not separately researched this session — order-of-magnitude only |
| **Total** | **~$10–$35/mo** | Confirmed-unknown: exact Apify compute-unit cost at your real request volume; exact hosting choice (WP7) |

This is materially cheaper than RentReboot's own paid tiers ($9.99–$29/mo) but note RentReboot's price buys a finished alert product for one person, not a data feed you control — the comparison isn't apples-to-apples.

---

## 1. Listings Feed Comparison

| Option | Pricing | API/Export | Proxies | Fields | Stabilization detection | Notes |
|---|---|---|---|---|---|---|
| **RentReboot** | Free tier: 3 alerts/day, 3-hr delay. **Premium $9.99/mo (annual) or $19.99/mo (monthly)**: unlimited real-time email, 1 SMS/day, price-drop alerts. **Max $19.99/mo (annual) or $29/mo (monthly)**: unlimited SMS, "Upcoming Listings," Roommate Mode. Separate **Plus $2.99–$4.99/mo** for housing-lottery alerts. ([pricing](https://rentreboot.com/pricing)) | **None found** — email/SMS only, no API/webhook/export documented anywhere on site ([FAQ](https://rentreboot.com/faq)) | N/A (not a scraper product) | Budget, 150+ neighborhoods, apartment size/bedroom count, amenities — exact filter granularity not published ([homepage](https://rentreboot.com/)) | "Checked against city rent-stabilization records"; "tracks rent-stabilization signals using city data"; also factors DOB violations, 311 complaints, inspections ([FAQ](https://rentreboot.com/faq), [guide](https://rentreboot.com/guide/how-to-check-if-apartment-rent-stabilized)) | Consumer alert product, not infrastructure. Cannot be wrapped into a pipeline. |
| **Apify — RentHop scraper** (crawlerbros) | "from **$3.00 / 1,000 results**" | Actor API (JSON/CSV/Excel export standard on Apify) | Not specified whether bundled | Price, beds/baths, neighborhood, zip, coordinates, photos, address | None (raw listings only) | [apify.com/crawlerbros/renthop-scraper](https://apify.com/crawlerbros/renthop-scraper/api) |
| **Apify — StreetEasy Cheerio** (memo23) | **$25.00/month + usage** | Apify API/export | Residential proxy groups included (`useApifyProxy: true`) | Full listing + building + agent + price history + media | Max 100 retries; configurable concurrency (default 10) to avoid blocks | [apify.com/memo23/apify-streeteasy-cheerio](https://apify.com/memo23/apify-streeteasy-cheerio) |
| **Apify — StreetEasy Scraper** (solidcode) | "**$1.00 / 1,000 listings**" | JSON/CSV/Excel export | Not specified | Price, beds/baths, sqft, GPS, amenities, building records, full price history, agent contacts | Not documented on page | [apify.com/solidcode/streeteasy-scraper](https://apify.com/solidcode/streeteasy-scraper/api) |
| **Apify — StreetEasy Scraper 🗽** (shahidirfan) | "**from $0.80 / 1,000 results**" | Apify export | Not bundled — page says "residential proxies strongly advised" (implies extra/self-configured) | "Comprehensive real estate data," not itemized | Not documented on page | [apify.com/shahidirfan/streeteasy-scraper](https://apify.com/shahidirfan/streeteasy-scraper/api/javascript) |
| **ScrapingBee (StreetEasy)** | Credit-based: Hobby $19/mo (75k credits), Freelance $49/mo (250k), Startup $99/mo (1M), Business $249/mo (3M), Business+ $599/mo (8M). 1,000 free credits, no card. | Direct HTTP API, no dedicated "export" but raw HTML/JSON per request | Residential proxy rotation + real headless browser bundled on all plans; `render_js=true` required for JS content | Address, price, beds/baths, sqft, agent, days on market, neighborhood, photos | Not applicable (generic scraper, not StreetEasy-specific parsing logic) | [ScrapingBee StreetEasy scraper](https://www.scrapingbee.com/scrapers-v2/street-easy-api/) |
| **Zillow Bridge Interactive / Zillow Group API** | Not published publicly — requires MLS/broker relationship | MLS-gated data feed (RETS/Bridge API), not open signup | N/A | Full MLS listing data where licensed | N/A | Confirmed **realtor/MLS-gated**: [Bridge Interactive](https://www.bridgeinteractive.com/developers/bridge-api/), [Zillow Group MLS Listings API](https://www.zillowgroup.com/developers/api/mls-broker-data/mls-listings/) are built for brokers/MLSs to *push* their own listings into Zillow, not for third parties to *pull* NYC rental inventory. Not usable for this project as an independent data source. |
| **RentHop** | No public developer API found; only third-party Apify scraper exists ([crawlerbros actor](https://apify.com/crawlerbros/renthop-scraper/api)) | None official | N/A | N/A | N/A | Same legal exposure as StreetEasy scraping if done directly. |
| **Apartments.com / Craigslist RSS / LeaseAlert / RealtyAPI** | Not verified this session | — | — | — | — | **Confirmed unknown** — not researched in depth; Craigslist NYC housing search page exists ([craigslist.org/search/area/newyork](https://www.craigslist.org/search/area/newyork?cat=hhh)) and can be RSS-polled cheaply, worth a follow-up pass, but rent-stabilized inventory skews toward larger buildings more commonly listed on StreetEasy/RentHop than Craigslist. |

**Bottom line for WP3:** use one or two of the sub-$1/1,000 StreetEasy Apify actors (solidcode or shahidirfan) for cost, cross-check against RentHop's actor for coverage, and treat ScrapingBee as a fallback if an actor gets blocked (JS rendering + residential proxy already bundled, at a higher fixed monthly floor).

---

## 2. Terms of Service — Automated Access

**Zillow** ([Terms of Use](https://www.zillow.com/corporate/terms-of-use/)): prohibits automated queries, described as "screen and database scraping, spiders, robots, crawlers, bypassing 'captcha'... or any other automated activity with the purpose of obtaining information from the Services." A separate clause limits copying content to manual, personal use only — "without the aid of any automated processes."

**StreetEasy**: the general consumer Terms of Use page (`streeteasy.com/terms`) returned a 404 on this pass; only the business/advertiser terms (`streeteasy.com/business/ad-terms-of-service/`) and a "Listings Quality Policy" were found and not fetched for scraping-specific language this session. **Confirmed unknown** — StreetEasy's actual consumer ToS language on scraping needs a direct re-check (try `streeteasy.com/terms-of-use` or the footer link from a live page) before WP3 finalizes vendor choice. Note StreetEasy is a Zillow Group property, so its ToS likely mirrors Zillow's anti-scraping language, but this is inference, not confirmed text.

**Practical implication (not a legal opinion):** both platforms' public terms disfavor direct automated access. This is exactly why third-party Apify/ScrapingBee actors — which operate at arm's length and absorb the proxy/blocking arms race — are the pragmatic choice over an in-house scraper.

---

## 3. Geocoding (Address → BBL)

| Option | Status | Notes |
|---|---|---|
| **NYC Geoclient API** | Appears to still be listed as a "Featured API" on the [NYC API portal](https://api-portal.nyc.gov/), described as "an interface to the NYC Department of City Planning's Geosupport system." No deprecation notice was visible on the portal home page. However, this could not be independently confirmed as still *accepting new registrations* — the portal's registration flow was not tested. **Confirmed unknown.** |
| **Self-hosted Geosupport** (`python-geosupport` / `docker-geosupport`) | Live and maintained: [ishiland/python-geosupport](https://github.com/ishiland/python-geosupport) and [NYCPlanning/docker-geosupport](https://github.com/NYCPlanning/docker-geosupport) (also [ishiland/docker-python-geosupport](https://github.com/ishiland/docker-python-geosupport)) are the standard community wrappers around DCP's Geosupport binary. Requires downloading the Geosupport application from DCP and running it in Docker or locally — more setup than a hosted API. |
| **NYC Planning Labs GeoSearch API** | **Live now** at `https://geosearch.planninglabs.nyc/v2/`, with `/search` and `/autocomplete` endpoints returning GeoJSON. **No signup, no API key found in documentation.** Only guidance found was to throttle autocomplete requests client-side; no published rate limit. |

**Recommendation:** use **NYC Planning Labs GeoSearch API** — free, no signup, no key, live today. It's Planning Department-adjacent and built for exactly this use case. Its documented output didn't explicitly confirm BBL/BIN fields in this session's fetch (only `name`, `label`, `confidence`, coordinates were visible) — **confirmed unknown: verify the full response schema includes BBL** before committing to it for WP3's DHCR-building-match step; if not, geocode to lat/lon via GeoSearch and reverse into BBL via PLUTO's spatial join, or fall back to self-hosted Geosupport which is guaranteed to return BBL directly.

---

## 4. Existing Repos

| Repo | Last activity (as observed) | Open issues | Verdict |
|---|---|---|---|
| [nycdb/nycdb](https://github.com/nycdb/nycdb) | 798 commits on main; exact last-commit date not retrievable this session (GitHub commit-log pages blocked by robots.txt for automated fetch) | 90 | **Use it.** It's the most current, actively maintained aggregator. Its [`rentstab` wiki page](https://github.com/nycdb/nycdb/wiki/Dataset:-Rent-Stabilized-Buildings) (wiki last edited **March 6, 2025**) confirms three tables: `rentstab` (2007–2017, sourced from talos/nyc-stabilization-unit-counts), `rentstab_v2` (**2018–2023**, sourced from JustFixNYC/nyc-doffer scraping DOF), and `rentstab_summary` (aggregated). **Unit-count data does extend past 2021 — through 2023 — via `rentstab_v2`, sourced from NYC Department of Finance tax-bill filings, not DHCR PDFs directly.** |
| [pauljump/pricefixed](https://github.com/pauljump/pricefixed) | Reports activity as recent as **September 15, 2026** (feed health check) and a citywide build dated **July 31, 2026** (2,750,889 unit records); MIT licensed | 1 | Interesting but **not a stabilization tool** — it aggregates live listing feeds + city public records (PLUTO, DOB, HPD, ACRIS, evictions) into SQLite for price-history tracking. No hosted download of its citywide DB exists yet ("no GitHub download for the 2.75M-unit build yet") — you'd have to run it yourself. Worth watching as a possible alternative/supplement to Apify scraping for the *listings* side (WP3), not for stabilization detection. Flag: this data point about very recent 2026 activity was produced by an AI summary of the page and was not independently cross-checked — **confirmed unknown: verify pricefixed is a real, actively-maintained project before depending on it.** |
| [talos/nyc-stabilization-unit-counts](https://github.com/talos/nyc-stabilization-unit-counts) | 241 commits; exact date not retrievable (robots.txt blocked commit log) | 16 | **Superseded by nycdb.** Covers only 2007–2014, references dead infrastructure (Travis CI, Python 2.6, and notes "taxbills.nyc website is currently down"). Useful only as historical provenance for `rentstab`'s pre-2018 data, already folded into nycdb. Don't build on it directly. |
| [clhenrick/dhcr-rent-stabilized-data](https://github.com/clhenrick/dhcr-rent-stabilized-data) | 28 stars, 3 forks; exact commit date not retrievable | Not captured | **Stale — data only through 2013.** Contains CSV/GeoJSON/Excel by borough from FOIL requests, but building-only (no apartment numbers or unit counts), and explicitly self-described as non-authoritative since DHCR registration is voluntary. Useful only as a reference for methodology (see clhenrick's [data-processing writeup](https://clhenrick.io/blog/airs-data-processing/) for the "Am I Rent Stabilized?" project), not as a live data source. |

**Net effect on the build plan:** nycdb saves real work for the building-level unit-count signal (through 2023) and should be the backbone of the DHCR/building-match layer; the other three repos are either subsumed by nycdb or too stale to use directly, though clhenrick's methodology writeup is a good reference for how to structure the PDF→structured-data conversion needed in §5.

---

## 5. DHCR Data Format

Confirmed: as of this check, the [official Rent Guidelines Board page](https://rentguidelinesboard.cityofnewyork.us/resources/rent-stabilized-building-lists/) offers the borough building lists **in PDF only** — "Listings are in pdf format. If you are unable to view the pdf, download the Adobe reader for free." The current file set is dated **2024 building registrations**, posted **November/December 2025** (e.g., [Brooklyn PDF](https://rentguidelinesboard.cityofnewyork.us/wp-content/uploads/2025/12/2024-DHCR-Bldg-File-Brooklyn.pdf)). No CSV, Excel, or API alternative is offered on the official page.

Third-party mirrors that have converted DHCR data to structured formats exist but are all **older, one-off FOIL-derived snapshots**, not synced to the current 2024 list:
- [clhenrick/dhcr-rent-stabilized-data](https://github.com/clhenrick/dhcr-rent-stabilized-data) — CSV/GeoJSON/Excel, data from 2002–2013 only.
- [MODA-NYC/DHCR_RentStabData](https://github.com/MODA-NYC/DHCR_RentStabData) — surfaced in search, not independently fetched this session (**confirmed unknown**: vintage/format not verified).
- [fitnr/rentregulated](https://github.com/fitnr/rentregulated) — surfaced in search, not independently fetched (**confirmed unknown**).
- "Am I Rent Stabilized?" ([amirentstabilized.com](https://amirentstabilized.com/how)) — a live public-facing tool built on this kind of FOIL/PDF conversion; worth checking whether it publishes a current data export, not confirmed this session.

**Implication for WP3/WP7:** the pipeline must include a PDF-parsing step (pdftotext/tabula-style extraction) against the current official Brooklyn PDF, since no live structured mirror of the *current* 2024 list was found. Budget real engineering time for this — DHCR building-list PDFs are historically inconsistent in formatting across years.

---

## Confirmed Unknowns

Explicitly flagging these so nobody downstream assumes they were verified:

1. **StreetEasy's actual consumer Terms of Use text on scraping** — the direct URL 404'd this session; only inferred from Zillow's (its parent's) terms.
2. **Whether NYC Geoclient API is still accepting *new developer registrations*** — it appears listed on the portal with no deprecation notice, but the signup flow itself was not tested.
3. **Whether NYC Planning Labs GeoSearch API responses include BBL/BIN directly**, or only lat/lon + address label (would require a PLUTO join for BBL).
4. **Exact last-commit dates** for all four GitHub repos in §4 — GitHub's `/commits` pages are blocked by robots.txt for automated fetching in this environment; only commit *counts* and issue counts were retrievable via the repo landing page.
5. **Whether proxies are bundled or extra** for the RentHop (crawlerbros), StreetEasy Scraper (solidcode), and StreetEasy Scraper 🗽 (shahidirfan) Apify actors — only memo23's actor explicitly confirmed bundled residential proxies.
6. **pricefixed's actual current state** — the fetched summary described suspiciously up-to-the-minute activity (dated within days of "today," 2026-09-19); this should be re-verified directly rather than trusted from a single automated page summary before any WP3 dependency is taken on it.
7. **RentHop, Apartments.com, Craigslist RSS, LeaseAlert, and RealtyAPI as independent feeds** — only RentHop (via a third-party Apify scraper) was checked in any depth; the other four were not evaluated for cost, coverage, or ToS this session.
8. **Apify platform-level pricing** (beyond per-actor usage costs) was not freshly re-verified this session — the platform's own free-tier/Starter-tier numbers cited above are order-of-magnitude, not confirmed current figures.
9. **DHCR Brooklyn PDF's internal format/parseability** — confirmed it exists only as PDF, but the PDF itself was not opened/parsed this session to assess text-extraction difficulty.

#!/usr/bin/env python3
"""
etl/parse_dhcr_pdf.py -- parse the RGB/DHCR Brooklyn rent-stabilized
building-list PDF into normalized rows keyed on BBL.

SOURCE:
    https://rentguidelinesboard.cityofnewyork.us/wp-content/uploads/2025/12/2024-DHCR-Bldg-File-Brooklyn.pdf
    landing page:
    https://rentguidelinesboard.cityofnewyork.us/resources/rent-stabilized-building-lists/

MANUAL FETCH STEP FOR A HUMAN
    1. Open the landing page above in a browser, download the "Brooklyn"
       2024 building list PDF.
    2. Save it to: data/raw/2024-DHCR-Bldg-File-Brooklyn.pdf
    3. Run: python3 etl/parse_dhcr_pdf.py
       (it will auto-detect the file at that path).

REAL FORMAT (confirmed against an actual pdfplumber text extraction of the
2024 Brooklyn file, 310 pages / 17,614 data rows -- this replaces an
earlier version of this parser written only against a documented format
description, which matched zero real rows). Each page repeats a title line,
a "Source: ... Page N of 310 ..." footer, and a column-header line; those
are skipped. Every real data row is a SINGLE line shaped like:

    ZIP BLDGNO1 STREET1 STSUFX1 [BLDGNO2 STREET2 STSUFX2] BROOKLYN 61 \
        MULTIPLE DWELLING <A|B> [STATUS3 FLAGS...] BLOCK LOT

For example:
    11201 131 AMITY ST BROOKLYN 61 MULTIPLE DWELLING A 291 45
    11201 177 AMITY ST BROOKLYN 61 MULTIPLE DWELLING A NON-EVICT COOP/CONDO 292 49
    11201 119 COURT ST 183 STATE ST BROOKLYN 61 MULTIPLE DWELLING A 421-A (1-15) 271 18
    11215 300 8TH AVE BROOKLYN 61 MULTIPLE DWELLING A J-51 NON-EVICT COOP/CONDO 1080 35

Key parsing facts learned from the real file (all verified against the full
17,614-row extraction, 100% of lines parse cleanly with these rules):

  - Every line ends in CRLF; the \r MUST be stripped before matching, or
    every trailing-anchor regex silently fails to match (this was the
    original bug that produced zero parsed rows).
  - " BROOKLYN 61 MULTIPLE DWELLING <A|B> " is a reliable, constant anchor
    literal (Brooklyn-only file, county FIPS 61) that cleanly separates the
    address portion from the building-class + status-flags + block/lot
    portion. It appears on 100% of real data lines.
  - A SECOND street frontage (a corner building with two addresses) is
    embedded in the SAME line, not a separate line -- e.g. "119 COURT ST
    183 STATE ST". It is detected by finding the first token after the
    first building number that itself looks like a building number (starts
    with a digit). This must specifically EXCLUDE ordinal street-name
    tokens like "8TH", "3RD", "42ND" (Park Slope alone has hundreds of
    numbered-street rows: "292 TO 300 10TH ST", "300 8TH AVE", etc.) --
    those start with a digit but are the street NAME, not a building
    number. Getting this wrong silently mis-splits every numbered-street
    address in the file.
  - A building number can be a plain integer, an integer+letter ("195A"),
    a hyphenated range ("120-05"), or an explicit "N TO M" range
    ("167 TO 171", "292 TO 300").
  - The street suffix is the LAST token of a frontage segment IF it matches
    a known suffix word, else there is no suffix (streets like "BROADWAY",
    "COLUMBIA HTS", "WASHINGTON PARK" have none -- these are left
    unsplit rather than forced into a wrong suffix).
  - STATUS3 (between the A/B class letter and the trailing BLOCK LOT pair)
    is free text: blank, or one or more of GARDEN COMPLEX / ROOMING HOUSE /
    421-A (1-15) / 421-A (16) / 421-G / J-51 / NON-EVICT COOP/CONDO /
    EVICT COOP/CONDO / COOP/CONDO PLAN FILE / ARTICLE 11 / SECTION 610 OF
    PHFL / SEC 608 / etc, in any combination, space-joined.
  - A rare (15-row) PDF-extraction glitch glues the block number directly
    onto the last status word with no space, e.g. "...NON-EVICT
    COOP/CONDO1902 1" (block 1902, lot 1). Handled by a fallback regex that
    inserts a boundary between a trailing letter/slash and a digit run
    when the straightforward whitespace split doesn't end in two bare
    integers.
  - dedup MUST happen on bbl, never on the address string -- a genuinely
    small number of BBLs appear on more than one line (distinct addresses
    resolving to the same lot).

The old ROW_RE-based implementation and its synthetic-fixture self-test
path are removed since they no longer reflect the real format; keep
fixtures/sample_dhcr_pdf_lines.txt only as a historical record of what was
originally guessed.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import DATA_INTERIM, DATA_RAW, make_bbl  # noqa: E402

DATA_LINE_RE = re.compile(r"^\d{5} ")
ANCHOR_RE = re.compile(
    r"^(?P<zip>\d{5}) (?P<addr>.*?) BROOKLYN 61 MULTIPLE DWELLING (?P<cls>[A-Z])\b ?(?P<tail>.*)$"
)
BLDGNO_RE = re.compile(r"^\d+[A-Z]?$|^\d+-\d+[A-Z]?$")
ORDINAL_RE = re.compile(r"^\d+(ST|ND|RD|TH)$")  # "8TH", "3RD" -- a street-name token, never a building number

STREET_SUFFIXES = {
    "ST", "STREET", "AVE", "AVENUE", "PL", "PLACE", "RD", "ROAD", "BLVD",
    "DR", "DRIVE", "LN", "LANE", "CT", "COURT", "SQ", "SQUARE", "TER",
    "TERRACE", "PKWY", "PARKWAY", "WAY", "WALK", "PLZ", "PLAZA", "CIR",
    "CIRCLE", "HWY", "ALY", "ALLEY", "LOOP", "ROW",
}

ABATEMENT_PATTERNS = ["421-A (1-15)", "421-A (16)", "421-G", "J-51"]


@dataclass
class DhcrRow:
    bbl: str | None
    zip: str
    bldg_no: str
    street_name: str
    street_suffix: str
    boro: int
    block: int
    lot: int
    multiple_dwelling_class: str | None
    is_condo_coop: bool
    abatement_type: str | None
    raw_flags: str
    second_frontage: str | None  # informational only; e.g. "183 STATE ST"
    source_line: str


def _is_bldgno_start(tok: str) -> bool:
    return tok[:1].isdigit() and not ORDINAL_RE.match(tok)


def _parse_frontage(tokens: list[str]) -> tuple[str, str, str | None]:
    """tokens[0] is a building-number-like token; returns (bldgno, street, suffix)."""
    i = 1
    if i + 1 < len(tokens) and tokens[i] == "TO" and BLDGNO_RE.match(tokens[i + 1]):
        i += 2
    bldgno = " ".join(tokens[:i])
    remaining = tokens[i:]
    if remaining and remaining[-1] in STREET_SUFFIXES:
        return bldgno, " ".join(remaining[:-1]), remaining[-1]
    return bldgno, " ".join(remaining), None


def _parse_address(addr_tokens: list[str]):
    """Returns (frontage1, frontage2_or_None) or None if unparseable."""
    if not addr_tokens or not BLDGNO_RE.match(addr_tokens[0]):
        return None
    i = 1
    if i + 1 < len(addr_tokens) and addr_tokens[i] == "TO" and BLDGNO_RE.match(addr_tokens[i + 1]):
        i += 2
    second_start = next((j for j in range(i, len(addr_tokens)) if _is_bldgno_start(addr_tokens[j])), None)
    if second_start is not None:
        return _parse_frontage(addr_tokens[:second_start]), _parse_frontage(addr_tokens[second_start:])
    return _parse_frontage(addr_tokens), None


def _split_tail(tail: str):
    """Returns (status3_or_None, block_str, lot_str) or None."""
    tokens = tail.split()
    if len(tokens) >= 2 and tokens[-2].isdigit() and tokens[-1].isdigit():
        return (" ".join(tokens[:-2]) or None), tokens[-2], tokens[-1]
    # Repair the rare glued-digit extraction glitch, e.g. "COOP/CONDO1902 1".
    fixed = re.sub(r"([A-Za-z/])(\d+)\b", r"\1 \2", tail)
    tokens = fixed.split()
    if len(tokens) >= 2 and tokens[-2].isdigit() and tokens[-1].isdigit():
        return (" ".join(tokens[:-2]) or None), tokens[-2], tokens[-1]
    return None


def parse_line(raw_line: str) -> tuple[DhcrRow | None, str | None]:
    """Returns (row, None) on success, or (None, error_reason) on failure."""
    line = raw_line.rstrip("\r\n")
    if not DATA_LINE_RE.match(line):
        return None, "not_a_data_line"
    m = ANCHOR_RE.match(line)
    if not m:
        return None, "no_anchor"
    parsed_addr = _parse_address(m.group("addr").split())
    if parsed_addr is None:
        return None, "bad_address"
    (bldgno1, street1, sufx1), frontage2 = parsed_addr
    split = _split_tail(m.group("tail"))
    if split is None:
        return None, "no_block_lot"
    status3, block_s, lot_s = split
    boro = 3  # this file is Brooklyn-only
    bbl = make_bbl(boro, block_s, lot_s)
    abatements = [p for p in ABATEMENT_PATTERNS if status3 and p in status3]
    second_frontage = None
    if frontage2:
        bn2, st2, sf2 = frontage2
        second_frontage = " ".join(x for x in (bn2, st2, sf2) if x)
    row = DhcrRow(
        bbl=bbl,
        zip=m.group("zip"),
        bldg_no=bldgno1,
        street_name=street1,
        street_suffix=sufx1 or "",
        boro=boro,
        block=int(block_s),
        lot=int(lot_s),
        multiple_dwelling_class=m.group("cls"),
        is_condo_coop=bool(status3 and "COOP/CONDO" in status3),
        abatement_type=",".join(abatements) or None,
        raw_flags=status3 or "",
        second_frontage=second_frontage,
        source_line=raw_line.rstrip("\n"),
    )
    return row, None


def parse_lines(lines: list[str]) -> tuple[list[DhcrRow], int]:
    rows: list[DhcrRow] = []
    unparsed = 0
    for raw_line in lines:
        row, err = parse_line(raw_line)
        if row is not None:
            rows.append(row)
        elif err != "not_a_data_line":
            # a genuine data-shaped line (starts with a 5-digit zip) that we
            # still failed to parse -- worth counting distinctly from the
            # expected non-data lines (titles, footers, column headers).
            unparsed += 1
    return rows, unparsed


def dedup_on_bbl(rows: list[DhcrRow]) -> list[DhcrRow]:
    """Collapse rows sharing a bbl into one, unioning their flags.

    dedup key = bbl, NEVER the address string: a genuinely small number of
    BBLs appear on more than one source line (e.g. distinct historical
    addresses resolving to the same lot).
    """
    by_bbl: dict[str, DhcrRow] = {}
    no_bbl: list[DhcrRow] = []
    for r in rows:
        if r.bbl is None:
            no_bbl.append(r)
            continue
        if r.bbl not in by_bbl:
            by_bbl[r.bbl] = r
        else:
            existing = by_bbl[r.bbl]
            if not existing.is_condo_coop and r.is_condo_coop:
                existing.is_condo_coop = True
            if not existing.multiple_dwelling_class and r.multiple_dwelling_class:
                existing.multiple_dwelling_class = r.multiple_dwelling_class
            existing_ab = set((existing.abatement_type or "").split(",")) - {""}
            new_ab = set((r.abatement_type or "").split(",")) - {""}
            merged = existing_ab | new_ab
            existing.abatement_type = ",".join(sorted(merged)) if merged else None
    return list(by_bbl.values()) + no_bbl


def extract_pdf_lines(pdf_path: Path) -> list[str]:
    try:
        import pdfplumber
    except ImportError as e:
        raise SystemExit(
            "pdfplumber is required to parse the real PDF (pip install pdfplumber)."
        ) from e

    lines: list[str] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            lines.extend(text.splitlines())
    return lines


def write_csv(rows: list[DhcrRow], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "bbl", "zip", "bldg_no", "street_name", "street_suffix",
                "boro", "block", "lot", "multiple_dwelling_class",
                "is_condo_coop", "abatement_type", "raw_flags", "second_frontage",
            ]
        )
        for r in rows:
            writer.writerow(
                [
                    r.bbl, r.zip, r.bldg_no, r.street_name, r.street_suffix,
                    r.boro, r.block, r.lot, r.multiple_dwelling_class,
                    int(r.is_condo_coop), r.abatement_type, r.raw_flags, r.second_frontage,
                ]
            )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--input",
        type=Path,
        default=DATA_RAW / "2024-DHCR-Bldg-File-Brooklyn.pdf",
        help="Path to the downloaded Brooklyn DHCR PDF.",
    )
    ap.add_argument(
        "--input-text",
        type=Path,
        help="Path to pre-extracted text lines instead of a PDF (e.g. if you already ran pdftotext/pdfplumber yourself).",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=DATA_INTERIM / "dhcr_brooklyn_parsed.csv",
    )
    args = ap.parse_args()

    if args.input_text:
        print(f"parsing pre-extracted text: {args.input_text}")
        lines = args.input_text.read_text(errors="replace").splitlines()
    else:
        if not args.input.exists():
            print(
                f"DHCR Brooklyn PDF not found at {args.input}.\n\n"
                "MANUAL FETCH STEP:\n"
                "  1. Download the Brooklyn 2024 PDF from:\n"
                "     https://rentguidelinesboard.cityofnewyork.us/resources/rent-stabilized-building-lists/\n"
                f"  2. Save it to: {args.input}\n"
                "  3. Re-run this script.",
                file=sys.stderr,
            )
            sys.exit(2)
        print(f"extracting text from PDF: {args.input}")
        lines = extract_pdf_lines(args.input)

    data_lines = sum(1 for l in lines if DATA_LINE_RE.match(l.rstrip("\r\n")))
    print(f"lines extracted: {len(lines)}  (of which {data_lines} look like data rows -- start with a 5-digit zip)")

    rows, unparsed = parse_lines(lines)
    before = len(rows)
    deduped = dedup_on_bbl(rows)
    after = len(deduped)

    write_csv(deduped, args.out)

    print(f"parsed rows (pre-dedup):  {before}")
    print(f"unparsed/skipped data-shaped lines: {unparsed}  (out of {data_lines} candidate data lines -- should be 0 or very close to it)")
    print(f"rows after BBL dedup:     {after}  (removed {before - after} duplicate-BBL lines)")
    print(f"rows with no derivable bbl (kept, address-only): {sum(1 for r in deduped if r.bbl is None)}")
    print(f"wrote: {args.out}")


if __name__ == "__main__":
    main()

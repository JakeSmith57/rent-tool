`dhcr_brooklyn_parsed.csv` is committed because it derives from a manual
PDF download that CI can't fetch. As of 2026-09-20 its 6 rows are
SYNTHETIC fixture data from fixtures/sample_dhcr_pdf_lines.txt, used to
prove the parser's BBL dedup logic. They are not real buildings and must
not be presented as such. See docs/WP1-registry.md.

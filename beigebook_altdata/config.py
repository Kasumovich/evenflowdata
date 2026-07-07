"""Static configuration: districts, sections, source locations, lens map."""

# The 12 Federal Reserve districts (national summary handled separately).
DISTRICTS = [
    "Boston", "New York", "Philadelphia", "Cleveland", "Richmond", "Atlanta",
    "Chicago", "St. Louis", "Minneapolis", "Kansas City", "Dallas", "San Francisco",
]

# Section headers as they recur in the modern book. The parser matches case- and
# punctuation-insensitively and tolerates minor wording drift across decades.
SECTIONS = [
    "Overall Economic Activity",
    "Labor Markets",
    "Prices",
    "Consumer Spending",
    "Manufacturing",
    "Real Estate and Construction",
    "Financial Services",
    "Nonfinancial Services",
    "Community Conditions",
    "Agriculture",
    "Energy",
    "Transportation",
]

# Production lenses, routed to the sections that are CONSISTENT across all 12 district
# pages (section names drift district-to-district, so the core lenses use only the
# canonical headers that always appear). Risks is computed separately as uncertainty
# density across the whole district text, not as a ladder section.
LENS_SECTIONS = {
    "growth":      ["Summary of Economic Activity", "Overall Economic Activity"],
    "inflation":   ["Prices"],
    "labor":       ["Labor Markets"],
    # bottlenecks is secondary: keyword-matched supply/manufacturing sections (see scoring).
    "bottlenecks": ["Manufacturing"],
}
# risks: uncertainty density (Loughran-McDonald) across all of a district's text.
RISK_MODE = "uncertainty"

# Pilot signal for the prototype.
PILOT_SECTION = "Labor Markets"

# --- Source locations -------------------------------------------------------
# 1996-present: Board of Governors site. URL scheme has changed; ingest.py holds
# the per-era templates. Pre-1996: not on the Board site -> FRASER (St. Louis Fed).
FED_BASE = "https://www.federalreserve.gov/monetarypolicy"
FRASER_BASE = "https://fraser.stlouisfed.org"  # pre-1996 archive (manual map per issue)

# Daily Treasury Statement (withholding) — Fiscal Data API, FY1998-present.
DTS_API = ("https://api.fiscaldata.treasury.gov/services/api/fiscal_service"
           "/v1/accounting/dts/deposits_withdrawals_operating_cash")

# Beige Book is a U.S. Government work -> public domain; safe to store full text.
# DTS is public domain likewise.

# 8 releases/year, ~2 weeks before each FOMC. Real dates come from the Fed's
# release calendar; ingest.py resolves issue dates rather than guessing.
RELEASES_PER_YEAR = 8

# Frozen scoring-rule version (see SCORING.md). Bump to v2 as a NEW column; never overwrite.
RULE_VERSION = "v1"

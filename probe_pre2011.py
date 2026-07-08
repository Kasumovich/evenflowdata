"""Standalone smoke test for the pre-1996..2010 Beige Book path.

Run from the repo root AFTER saving the new books.py:
    python probe_pre2011.py

It needs network to federalreserve.gov (the CI/your laptop has it; Claude's
sandbox does not). It does NOT rebuild data.js or touch FRED/CPI — it isolates
the two things that have been guessed at:

  STEP 1  discovery : which page(s) carry the static "Related pages" release
                      footer, and does _old_from_footer() extract pre-2011 dates?
  STEP 2  parse+score: does one real /fomc/ district page parse into sections
                      and score non-None (i.e. not flatline)?

Paste the whole output back and we'll know exactly where it stands.
"""
import sys
from beigebook_altdata import books as B

print("=" * 70)
print("STEP 1 — discovery: does the release-list footer parse?")
print("=" * 70)

# Candidate seed pages. We test several so we can SEE which ones actually carry
# the footer (this is the exact assumption production seeding depends on):
#   - a confirmed-good legacy stub (its footer lists every book 1996-2016)
#   - a 2011-era stub (what production actually seeds from)
#   - the modern year page and the archive page (fallbacks)
seeds = [
    "https://www.federalreserve.gov/monetarypolicy/beigebook/beigebook200110.htm",  # confirmed
    "https://www.federalreserve.gov/monetarypolicy/beigebook/beigebook201103.htm",  # 2011 stub
    "https://www.federalreserve.gov/monetarypolicy/beigebook/beigebook2011.htm",    # year page
    "https://www.federalreserve.gov/monetarypolicy/beige-book-archive.htm",         # archive
]

best = {}
for url in seeds:
    html = B._get(url)
    if not html:
        print(f"  [fetch FAILED]  {url}")
        continue
    got = B._old_from_footer(html, 1996, 2010)
    print(f"  [{len(got):3d} pre-2011 dates]  {url}")
    if len(got) > len(best):
        best = got

if not best:
    print("\nRESULT: no seed page yielded a footer -> discovery approach is wrong.")
    print("Paste this output; we'll switch the date source.")
    sys.exit(1)

ymds = sorted(v.rstrip("/").split("/")[-1] for v in best.values())
print(f"\n  parsed {len(best)} pre-2011 releases; range {ymds[0]} .. {ymds[-1]}")
print(f"  first 5: {ymds[:5]}")
print(f"  last 5 : {ymds[-5:]}")

print("\n" + "=" * 70)
print("STEP 2 — parse+score one real /fomc/ district page")
print("=" * 70)
# Boston (1.htm) of the confirmed Oct 24, 2001 release.
base = "https://www.federalreserve.gov/fomc/beigebook/2001/20011024/"
html = B._get(base + "1.htm")
if not html:
    print(f"  [fetch FAILED]  {base}1.htm  -> district pages not reachable at this path")
    sys.exit(1)
secs = B.parse_district_page(html)
print(f"  sections parsed ({len(secs)}): {list(secs.keys())}")
vals = B._lens_from_sections(secs)
vals["risks"] = B._risk_uncertainty(secs)
print(f"  lens scores: { {k: (round(v,3) if isinstance(v,float) else v) for k,v in vals.items()} }")

core_ok = all(vals[k] is not None for k in ("growth", "inflation", "labor"))
print("\n" + "=" * 70)
if best and secs and core_ok:
    print("VERDICT: PASS — discovery + parse + score all work on the live site.")
    print("A full `real-books` run should now fill in 1996-2010.")
else:
    print("VERDICT: PARTIAL — see which step above is empty/None and paste it back.")
print("=" * 70)

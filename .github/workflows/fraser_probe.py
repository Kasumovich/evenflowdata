"""Phase 1 FRASER probe — pre-1996 Beige Book backfill feasibility.

Run in CI (GitHub Actions reaches fraser.stlouisfed.org; Claude's sandbox can't).
It does NOT touch the dashboard — it just answers the open questions and prints them:

  STEP 1  Can pdfplumber extract clean text from a FRASER Beige Book PDF?
          -> if yes, we SKIP tesseract entirely (FRASER already OCRs its scans).
  STEP 2  Does the dated PDF path reach back across the 1996 boundary?
          -> probes REAL Beige Book dates (from the Fed archive) at FRASER's path.
  STEP 3  What does the OLDEST available issue's text look like? (real OCR quality)
  STEP 4  Enumeration: can we list historical items + dates via FRASER's API/search?
          -> raw output so we can wire discovery next.

Paste the ENTIRE output back.

Deps: pdfplumber (the workflow installs it). Uses only stdlib for HTTP.
"""
import io, re, sys, json, urllib.request, urllib.error

FRASER = "https://fraser.stlouisfed.org"
PDF = FRASER + "/files/docs/historical/FOMC/meetingdocuments/BeigeBook_{}.pdf"
UA = {"User-Agent": "beigebook-altdata FRASER probe (research; contact via repo)"}
DISTRICTS = ("Boston", "New York", "Philadelphia", "Cleveland", "Richmond", "Atlanta",
             "Chicago", "St. Louis", "Minneapolis", "Kansas City", "Dallas", "San Francisco")


def get(url, binary=False, timeout=90):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return raw if binary else raw.decode("utf-8", "replace")


def quality(text):
    toks = re.findall(r"\S+", text)
    words = re.findall(r"[A-Za-z]{3,}", text)
    return {
        "chars": len(text),
        "tokens": len(toks),
        "real_word_ratio": round(len(words) / max(1, len(toks)), 3),   # OCR garbage -> low
        "districts_named": sum(1 for d in DISTRICTS if d in text),      # 0..12
        "structure_markers": sum(text.count(k) for k in ("District", "Federal Reserve", "Summary")),
    }


def extract_pdf(data):
    import pdfplumber
    parts = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        n = len(pdf.pages)
        for pg in pdf.pages[:40]:                    # cap pages for the probe
            parts.append(pg.extract_text() or "")
    return n, "\n".join(parts)


def try_pdf(datestr):
    url = PDF.format(datestr)
    try:
        return get(url, binary=True), url
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}"
    except Exception as e:
        return None, type(e).__name__


print("=" * 74)
print("STEP 1 — pdfplumber extraction on a known FRASER Beige Book PDF (2024-03-06)")
print("=" * 74)
oldest_ok_text = None
data, info = try_pdf("20240306")
if data:
    print(f"  fetched {len(data):,} bytes from {info}")
    try:
        pages, text = extract_pdf(data)
        q = quality(text)
        print(f"  pages={pages}  {q}")
        print("  sample:", repr(text[:280]))
        verdict = q["real_word_ratio"] >= 0.55 and q["districts_named"] >= 8
        print("  VERDICT:", "CLEAN text layer — pdfplumber works, NO tesseract needed"
              if verdict else "SPARSE/GARBLED — OCR (tesseract) likely required")
    except Exception as e:
        print("  pdfplumber FAILED:", repr(e), "-> extraction path needs work")
else:
    print("  fetch FAILED:", info, "-> the PDF path/pattern may be wrong; paste this")

print("\n" + "=" * 74)
print("STEP 2 — coverage: does the dated-PDF pattern exist across the 1996 boundary?")
print("=" * 74)
# REAL Beige Book release dates (from the federalreserve.gov /fomc/ archive) — no guessing.
known = ["20240306", "20101201", "20011024", "19961204", "19961030"]
exist = []
for d in known:
    data, info = try_pdf(d)
    if data:
        exist.append((d, data)); print(f"  [EXISTS {len(data):>10,} B]  BeigeBook_{d}.pdf")
    else:
        print(f"  [missing: {info:<9}]  BeigeBook_{d}.pdf")
print(f"  -> pattern confirmed for {len(exist)}/{len(known)} known dates"
      + ("" if exist else "  (if 0, the historical collection uses a different path — see STEP 4)"))

print("\n" + "=" * 74)
print("STEP 3 — OCR quality on the OLDEST available issue (the real scanned-text test)")
print("=" * 74)
if exist:
    d, data = exist[-1]                               # oldest known that existed
    print(f"  extracting BeigeBook_{d}.pdf ({len(data):,} B)")
    try:
        pages, text = extract_pdf(data)
        q = quality(text)
        print(f"  pages={pages}  {q}")
        print("  sample:", repr(text[:280]))
        gate = q["real_word_ratio"] >= 0.55 and q["districts_named"] >= 6
        print("  VERDICT:", "usable OCR text — scoring pipeline can consume this"
              if gate else "low quality — will need a text-confidence gate and/or re-OCR")
    except Exception as e:
        print("  extraction FAILED:", repr(e))
else:
    print("  skipped (no PDF found in STEP 2)")

print("\n" + "=" * 74)
print("STEP 4 — enumeration: list historical items + dates (to wire discovery)")
print("=" * 74)
# We KNOW title pages are server-rendered and there is a REST API + JSON metadata.
# Try a few endpoints and dump raw shape so we can pick the right one next.
endpoints = [
    FRASER + "/api/search?query=beige%20book&limit=5",
    FRASER + "/api/search?q=beige%20book",
    FRASER + "/search?q=beige%20book",
]
for url in endpoints:
    try:
        raw = get(url)
        head = raw[:400].replace("\n", " ")
        print(f"  [{url}]\n     {len(raw)}B  head: {head}\n")
    except Exception as e:
        print(f"  [{url}] FAILED: {type(e).__name__} {e}\n")

print("=" * 74)
print("SUMMARY: STEP 1/3 verdicts answer 'tesseract or not'; STEP 2 answers coverage;")
print("STEP 4 shows how to enumerate pre-1996 dates. Paste everything back.")
print("=" * 74)

"""Phase 1b — enumeration probe for pre-1996 Beige Books.

probe v1 proved the text extracts cleanly (NO tesseract). This finds WHERE the
pre-1996 issues live and how to list them. It touches nothing in the dashboard —
it just queries public APIs and prints what it finds. Paste the ENTIRE output.

Sources tested (both no-auth, both reachable from CI):
  A. Internet Archive — advancedsearch -> metadata -> *_djvu.txt full text.
  B. FRASER — metadata.php?...&json=1 (the endpoint that DID respond in v1),
     to confirm we can list a title's items + dates + download URLs.
"""
import re, io, json, gzip, urllib.parse, urllib.request, urllib.error

UA = {"User-Agent": "beigebook-altdata enum probe (research; repo contact)"}
IA = "https://archive.org"
FRASER = "https://fraser.stlouisfed.org"
DISTRICTS = ("Boston", "New York", "Philadelphia", "Cleveland", "Richmond", "Atlanta",
             "Chicago", "St. Louis", "Minneapolis", "Kansas City", "Dallas", "San Francisco")


def get(url, binary=False, timeout=60):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        b = r.read()
    return b if binary else b.decode("utf-8", "replace")


def quality(text):
    toks = re.findall(r"\S+", text)
    words = re.findall(r"[A-Za-z]{3,}", text)
    return {"chars": len(text), "real_word_ratio": round(len(words) / max(1, len(toks)), 3),
            "districts_named": sum(1 for d in DISTRICTS if d in text)}


print("=" * 74)
print("STEP A — Internet Archive: search for Beige Book items + coverage")
print("=" * 74)
q = ('(title:("beige book") OR title:("summary of commentary on current economic conditions"))')
url = (IA + "/advancedsearch.php?q=" + urllib.parse.quote(q)
       + "&fl[]=identifier&fl[]=date&fl[]=year&fl[]=title&rows=80&output=json&sort[]=date+asc")
ia_ids = []
try:
    d = json.loads(get(url))
    docs = d.get("response", {}).get("docs", [])
    print(f"  total found: {d.get('response', {}).get('numFound')}   returned: {len(docs)}")
    for doc in docs[:15]:
        print(f"    {doc.get('date','?'):24.24} | {doc.get('identifier','')[:48]:48} | {doc.get('title','')[:40]}")
        ia_ids.append((doc.get("date", ""), doc.get("identifier", "")))
    dates = [x[0] for x in ia_ids if x[0]]
    if dates:
        print(f"  date range: {min(dates)[:10]} .. {max(dates)[:10]}")
except Exception as e:
    print("  IA search FAILED:", type(e).__name__, e)

print("\n" + "=" * 74)
print("STEP B — IA: pull one item's file list + full text, quality-check")
print("=" * 74)
# prefer the earliest pre-1996 item; else the earliest available
target = None
for dt, ident in ia_ids:
    if dt and dt[:4].isdigit() and int(dt[:4]) < 1996:
        target = ident; break
target = target or (ia_ids[0][1] if ia_ids else None)
if target:
    print(f"  item: {target}")
    try:
        meta = json.loads(get(f"{IA}/metadata/{target}"))
        files = meta.get("files", [])
        txts = [f["name"] for f in files if f["name"].endswith("_djvu.txt") or f["name"].endswith(".txt")]
        print(f"    files: {len(files)}  text derivatives: {txts[:3]}")
        if txts:
            text = get(f"{IA}/download/{target}/{urllib.parse.quote(txts[0])}")
            print(f"    {txts[0]}: {quality(text)}")
            print("    sample:", repr(text[:240]))
    except Exception as e:
        print("    IA metadata/text FAILED:", type(e).__name__, e)
else:
    print("  (no IA items to inspect)")

print("\n" + "=" * 74)
print("STEP C — FRASER metadata.php enumeration (endpoint that responded in v1)")
print("=" * 74)
# 8957 = Eighth District Beige Book (recent) — confirms the primitive lists items+dates+urls.
# Also try to discover a historical/national Beige Book title via the search metadata.
for label, url in [
    ("title 8957 items", f"{FRASER}/metadata.php?type=title&id=8957&json=1"),
    ("search 'beige book'", f"{FRASER}/metadata.php?type=search&q=" + urllib.parse.quote("beige book") + "&json=1"),
]:
    try:
        raw = get(url, timeout=45)
        print(f"  [{label}] {len(raw)}B")
        try:
            j = json.loads(raw)
            keys = list(j.keys()) if isinstance(j, dict) else f"list[{len(j)}]"
            print(f"    json keys: {keys}")
            # try to surface item date + download fields generically
            print(f"    head: {raw[:300].replace(chr(10),' ')}")
        except Exception:
            print(f"    (not json) head: {raw[:200].replace(chr(10),' ')}")
    except Exception as e:
        print(f"  [{label}] FAILED: {type(e).__name__} {e}")

print("\n" + "=" * 74)
print("SUMMARY: A = does IA have per-issue Beige Books back to 1983 (+clean text)?")
print("B = is IA's *_djvu.txt usable? C = can FRASER metadata.php enumerate a title?")
print("Paste everything; that pins the source + enumeration for the ingestion.")
print("=" * 74)

"""CLI: build the panel for a date range.

Examples
  python -m beigebook_altdata.run --start 1970 --end 2026 --engine both
  python -m beigebook_altdata.run --start 2023 --end 2026 --engine lexicon --pilot labor

The build sandbox cannot reach the Fed/Treasury, so this resolves real release dates
from ingest.release_index (wired in your env). Withholding falls back to a stub unless
--live-withholding is passed.
"""
from __future__ import annotations
import argparse, datetime as dt
import pandas as pd

from . import ingest, parse, panel, withholding


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, required=True)
    ap.add_argument("--end", type=int, required=True)
    ap.add_argument("--engine", choices=["lexicon", "llm", "both"], default="lexicon")
    ap.add_argument("--live-withholding", action="store_true")
    ap.add_argument("--out", default="panel.parquet")
    args = ap.parse_args()

    dates = ingest.release_index(args.start, args.end)
    if not dates:
        raise SystemExit("ingest.release_index returned no dates — wire it to the live "
                         "Beige Book archive / FRASER map before running.")

    all_cells = []
    for d in dates:
        book = parse.parse_book(ingest.get_book(d).raw_html)
        eng = "lexicon" if args.engine in ("lexicon", "both") else "llm"
        all_cells.append(panel.score_book(book, d, engine=eng))
    cells = pd.concat(all_cells, ignore_index=True)
    lens = panel.momentum(panel.build_lens(cells))

    if args.live_withholding:
        daily = withholding.fetch_withholding(dt.date(args.start - 1, 1, 1),
                                               dt.date(args.end, 12, 31))
        wh = withholding.align_to_releases(withholding.filtered_yoy(daily), dates)
    else:
        wh = pd.DataFrame(withholding.stub_yoy(dates))

    cells.to_parquet(args.out.replace(".parquet", "_cells.parquet"))
    lens.to_parquet(args.out.replace(".parquet", "_lens.parquet"))
    wh.to_parquet(args.out.replace(".parquet", "_withholding.parquet"))
    print(f"{len(dates)} books -> {len(cells)} cells, {len(lens)} lens rows")


if __name__ == "__main__":
    main()

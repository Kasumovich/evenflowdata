# How a Beige Book becomes a number — the scoring rule (v1)

This is the whole rule in plain terms. No statistics, no jargon. If you can read this
page, you can audit any score the system produces.

## The idea in one sentence

The Beige Book describes the economy in **words** ("activity grew modestly," "hiring
softened"); the rule reads those words and turns the direction they imply into a
**number** between roughly −2 (sharply worse) and +2 (sharply better).

## Step 1 — Cut the book into pieces

Every book is one national summary plus 12 district reports, each split into sections
(Labor Markets, Prices, Manufacturing, …). The rule chops the book into those pieces so
it scores **one district's view of one topic at a time** — e.g. "Dallas, on labor."

## Step 2 — Score each piece with the adjective ladder

The Beige Book leans on a small, surprisingly consistent vocabulary of intensity words.
The rule assigns each a fixed value:

| Words | Value |
|---|---|
| robust, strong, surged, booming | +2 |
| solid, healthy | +1.5 |
| grew, rose, increased, expanded, improved, moderate | +1 |
| modest, slight, edged | +0.5 |
| flat, unchanged, steady, stable | 0 |
| soft, slowed, declined, fell, weak, weakened | −1 |
| plunged, collapsed, sharp decline | −2 |

It finds every ladder word in the piece and **averages them**. That average is the
piece's score. If a piece contains no ladder words, it gets **no score** (not zero) —
silence isn't the same as "flat."

**Negation check.** Before accepting a word, the rule looks back a few words for a
flipper ("not," "no longer," "little"). "Not strong" isn't +2; it's flipped to −2.

## Step 3 — Two extra readings (context, not the headline)

Alongside the ladder, each piece also gets a **tone** and an **uncertainty** reading from
a standard finance word-list (Loughran–McDonald). These catch mood and hedging
("uncertain," "cautious") that the ladder isn't built for. They sit beside the score as
context; the ladder is the headline number.

## Step 4 — Combine into a lens score

To get a lens (say Growth) for a whole book, the rule **averages the relevant pieces
across all 12 districts**. Two by-products fall out for free:

- **Breadth** — what share of districts were positive (8 of 12 beats a single hot district).
- **Dispersion** — how much the districts disagreed (the "σ" on the tiles).

That book-level average is the **raw score** — the line on the dashboard.

## Worked example

Dallas, Labor Markets: *"Employment grew modestly. Labor markets remained tight and
finding skilled workers was difficult. Wage pressures were moderate but persistent."*

Ladder words found: **grew** (+1), **modestly** (+0.5), **moderate** (+1).
Average = (1 + 0.5 + 1) ÷ 3 = **+0.83** → modest-to-moderate expansion.

Note what was *ignored*: "tight," "difficult," "pressures." They sound negative, and a
generic sentiment tool reads them as negative — but for **labor** they signal strength,
so the ladder leaves them out on purpose. This is exactly why the ladder beats off-the-shelf
sentiment on this text.

## What the rule deliberately does NOT do

- It does not read numbers or percentages — it reads the **adjectives** firms use. (The
  hard numbers come from a separate series, like withholding.)
- It does not normalize or compare to history — that's a later, optional step. The raw
  score is just "what this book said."

## Why this is "never revised" — and the one condition

A 1970 score uses **only the 1970 book**, so it never changes when later books arrive.
But it only stays frozen if **the rule itself stays frozen** — this ladder, this negation
check, this section parser. Change a word in the table and every past score shifts.

So the rule is stamped **v1**. If you ever improve it, that becomes **v2** and runs as a
*new* column next to v1 — you never overwrite history, you add a new vintage beside it.

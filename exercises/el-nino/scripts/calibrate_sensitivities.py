"""One-time recalibration of crop yield sensitivities in config/regions.yaml.

Why this exists
---------------
The first pipeline run tripped its own literature cross-check: the modelled
palm-oil price response came out at +242% against a published El Nino response
of +20-40%. Root cause was two-fold, and this script fixes the first half.

    1. Perennial tree crops (palm, coffee, cocoa, rubber, tea) had annual-cereal
       sensitivities. A palm plantation does not lose 20% of its yield in a
       drought year the way a rainfed maize crop does -- the tree buffers, and
       the loss shows up smaller and later. Malaysia's *record* 2015-16 loss was
       ~2.4 Mt on ~20 Mt, i.e. ~12%, during a 2.8 degC event.
    2. Irrigated Asian rice was carrying rainfed sensitivities. Global rice
       production has never fallen by the ~20% the original values implied.

Method: for each crop, rescale every country's sensitivity by a single factor
so the crop's largest magnitude equals the calibrated ceiling below. Relative
ordering between countries -- which is the part the teleconnection literature
actually constrains -- is preserved exactly.

Ceilings are anchored on observed losses in the 1997-98 and 2015-16 events.
Re-run after editing CEILINGS; the script is idempotent in effect, not in
value, so commit the result.
"""
from __future__ import annotations
import re, sys
from pathlib import Path

CEILINGS = {
    # perennial / tree crops -- buffered, lagged
    "palm_oil": 0.08, "coffee": 0.12, "cocoa": 0.05, "rubber": 0.05,
    "tea": 0.05, "coconut": 0.06, "banana": 0.06, "grapes": 0.06, "fruit": 0.05,
    # irrigated / high-input staples
    "rice": 0.07, "wheat": 0.18, "barley": 0.18, "canola": 0.20,
    "soybeans": 0.16, "cotton": 0.12, "sugar": 0.12, "tobacco": 0.12,
    # rainfed staples in strong-teleconnection zones -- the genuine tail
    "maize": 0.28, "beans": 0.20, "potato": 0.20, "teff": 0.14,
    "sorghum": 0.10, "cassava": 0.10, "pulses": 0.12,
    # livestock systems
    "beef": 0.10, "dairy": 0.06,
}

CROP_LINE = re.compile(
    r"^(?P<indent>\s+)(?P<crop>[a-z_]+):(?P<pad>\s*)\{\s*exposure:\s*(?P<exp>[-\d.]+)\s*,"
    r"\s*sensitivity:\s*(?P<sens>[-\d.]+)\s*\}\s*$"
)


def main(path: Path) -> int:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    current: dict[str, float] = {}
    for line in lines:
        m = CROP_LINE.match(line)
        if m:
            crop, sens = m.group("crop"), abs(float(m.group("sens")))
            current[crop] = max(current.get(crop, 0.0), sens)

    factors = {}
    for crop, observed_max in current.items():
        ceiling = CEILINGS.get(crop)
        if ceiling is None:
            print(f"  ! no ceiling configured for '{crop}', leaving unchanged")
            continue
        factors[crop] = ceiling / observed_max if observed_max else 1.0

    out, changed = [], 0
    for line in lines:
        m = CROP_LINE.match(line)
        if not m or m.group("crop") not in factors:
            out.append(line)
            continue
        crop = m.group("crop")
        new = round(float(m.group("sens")) * factors[crop], 3)
        out.append(
            f"{m.group('indent')}{crop}:{m.group('pad')}"
            f"{{ exposure: {m.group('exp')}, sensitivity: {new} }}"
        )
        changed += 1

    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"  rescaled {changed} crop entries across {len(factors)} crops")
    for crop, factor in sorted(factors.items()):
        print(f"    {crop:<10} x{factor:.3f}  -> ceiling {CEILINGS[crop]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1] if len(sys.argv) > 1 else "config/regions.yaml")))

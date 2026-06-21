# Beige Book alternative-data screen

A shareable web dashboard that turns the Federal Reserve Beige Book into tracked
numbers across four lenses (growth, inflation, bottlenecks, risks), with a macro-regime
read and two 12-month probability forecasts (recession; inflation > 2%).

- **What it shows & how it's built** — open the site and read the in-page **About &
  methodology** section (tools, scoring mechanism, probability models, all in plain English).
- **Deploy it** (hosting, email permissioning, updates, CSV) — see `DEPLOY.md`.
- **Scoring rule in detail** — `SCORING.md`.

Quick local preview:
```
pip install -r requirements.txt
python build_data.py --demo          # writes site/data.js
cd site && python -m http.server     # open http://localhost:8000
```
Opening `site/index.html` directly also works — without `data.js` it shows the built-in
synthetic preview.

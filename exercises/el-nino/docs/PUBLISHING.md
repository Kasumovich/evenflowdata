# Publishing to evenflowdata.com

## What ships

```
site/
  index.html            hub landing page (Even Flow Data)
  beige-book/           ← your existing exercise moves here
  el-nino/
    index.html          the tracker, built by the workflow
    payload.json        full run payload; the hub reads it for live figures
    sitrep_*.md         point-in-time situation reports
```

`.github/workflows/publish.yml` refreshes and deploys twice daily (06:00 and
18:00 UTC), on push to `main`, and on manual dispatch.

## The one migration step

The Beige Book screen is currently at the site root. The hub wants that root,
so the screen moves to `/beige-book/` and the hub's card links there. **Set up
a redirect from any old deep links before switching**, or the change will break
anything already pointing at the root.

If you would rather not move it, change the hub's Exercise 01 link to the
absolute URL and serve the hub from `/exercises` instead. The hub does not care
where it lives.

## Setup (Cloudflare Pages)

evenflowdata.com is served by Cloudflare, so Cloudflare Pages does the hosting
and GitHub Actions exists only to provide the **schedule** — Cloudflare Pages
builds on push and cannot refresh twice daily on its own.

1. Push this repo to GitHub.
2. Add two repository secrets (**Settings → Secrets and variables → Actions**):
   - `CLOUDFLARE_API_TOKEN` — Cloudflare → My Profile → API Tokens. Use the
     *Edit Cloudflare Workers* template, or a custom token with
     **Account → Cloudflare Pages → Edit**.
   - `CLOUDFLARE_ACCOUNT_ID` — Cloudflare → Workers & Pages, right-hand rail.
3. If your Pages project is not named `evenflowdata`, add a repository
   *variable* `CF_PAGES_PROJECT` with the real name.
4. Run the workflow manually once (**Actions → Refresh and publish → Run
   workflow**) and check the job summary.

The domain stays where it is. Nothing about DNS changes.

### One thing to check first: direct upload vs Git integration

A Cloudflare Pages project is one or the other, and it matters here.

- **Direct upload** — the workflow's `wrangler pages deploy` step works as
  written. This is the normal case for a project created via `wrangler` or by
  dragging a folder into the dashboard.
- **Git-connected** — Cloudflare builds from the repo itself on push. A project
  in this mode will reject or fight direct uploads to the production branch.
  Two ways out: disconnect the Git integration and switch to direct upload, or
  drop the wrangler step and have the workflow commit `site/` to the repo,
  letting Cloudflare's own build pick it up.

Check under **Workers & Pages → your project → Settings → Builds & deployments**.

Credentials are optional. Add any of `CDSAPI_KEY`, `ADSAPI_KEY`,
`EARTHDATA_TOKEN`, `USDA_NASS_KEY`, `TWELVE_DATA_KEY` as repository secrets to
activate the corresponding layers — see [DEPLOYMENT.md](DEPLOYMENT.md).
Everything works without them.

## The publish gate

The workflow **refuses to deploy if no source went live**. A dashboard reading
"Sources live: 0" is honest, but to a first-time visitor it reads as broken.
The gate is what stops a seed-only build reaching the public site.

Override deliberately with **Run workflow → allow_seeded: true** if you ever
want to publish a snapshot anyway.

This is also the answer to the "everything is seeded" problem: the GitHub
runner has the network egress a sandboxed environment does not, so the first
successful scheduled run promotes every figure from the bundled snapshot to
live primary-source data, and the two stale-observation incidents clear on
their own.

## Audit trail

Each run commits its payload to `archive/payload_<timestamp>.json` and its
Situation Report to `docs/sitreps/`. The git history becomes the point-in-time
record — one immutable commit per run, matching the never-revised discipline
the Beige Book screen already advertises. Nothing is ever rewritten in place.

## Moving hosts later

Everything above the deploy step is host-agnostic; the deliverable is the
`site/` directory. Swap that one step — GitHub Pages, Netlify and S3
one-liners are in the comment block at the bottom of
`.github/workflows/publish.yml`.

## Before you go public

- [ ] One successful run with `sources_live > 0`
- [ ] Beige Book moved to `/beige-book/` with redirects in place
- [ ] Hub's Exercise 01 link verified
- [ ] `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` set as secrets
- [ ] Pages project confirmed as direct-upload (or the wrangler step swapped)
- [ ] Read the disclaimer in the hub footer and confirm you're happy with it

The last one matters more than it looks. The tracker publishes **scenario
output from a structural model, not a fitted signal**, and the site says so in
the footer, on the card, and in the tracker's own Method tab. Keep all three.
That disclosure is the credibility, not a liability. The "Not backtested" tag
on the tracker card stays until there is a backtest to point at — running the
impact model over 1982–83, 1997–98 and 2015–16 would be the way to earn its
removal, if client interest ever justifies the work.

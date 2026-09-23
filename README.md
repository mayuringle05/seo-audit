# Generic local SEO audit workspace

A SiteOne supervisor for any public HTTP(S) website, with isolated reports and reviewed baselines. AVENIME is only a validation target. Python 3.9+ and the SiteOne CLI are required. The optional CrawlSEO dashboard uses Docker Compose.

**Status: partial implementation, not the full production-ready system in the supplied specification.** Quick-mode orchestration is implemented. Rendered/full modes fail closed because the inspected upstream browser implementation does not enforce read-only network requests or rate-limit browser subresources. CrawlSEO startup requires your Google OAuth credentials and a local Docker runtime. See `VALIDATION.md` for exactly what was tested.

## Install on your Mac

Extract this folder to `~/seo-audit` (do not overwrite an existing workspace).

```bash
cd ~/seo-audit
bash scripts/setup-siteone.sh
bash scripts/seo-audit.sh https://avenime.com quick
bash scripts/seo-audit.sh https://example.com quick
```

Quick is the default. Setup uses the documented Homebrew tap; existing installations are preserved. Every audit checks the installed binary's actual help for required flags before sending requests. A missing flag is a failure, never silently skipped. There are no Python package dependencies, model calls, target database connections, or automatic report uploads.

## What a quick audit does

1. Validates the URL, resolves its hostname to public IPs, and excludes common action/admin paths.
2. Reads same-origin robots.txt and bounded XML sitemap/index files, including gzip. Seeds the homepage, supplied URL, and eligible sitemap URLs; SiteOne discovers further internal links.
3. Quick mode is HTML-focused: it disables asset/file downloads, runs one SiteOne worker at up to two requests per second, keeps a 768 MiB crawler limit, uses file-backed response storage with no HTTP cache, and is bounded to 2,000 visited URLs / 30 minutes by default.
4. Keeps SiteOne's HTML, JSON and text reports plus logs, the exact argument vector, version, configuration, discovery notes and exit status.
5. Adds a context-aware `summary.json` plus `summary.html`. Core SEO signals are re-derived from structured tables, noindex is interpreted with sitemap context, normal off-domain skips are informational, repeated site-wide security/header findings are aggregated, and raw SiteOne findings remain preserved as evidence.
6. Compares to an explicitly chosen compatible baseline, if supplied.

In quick mode, SiteOne's `--disable-all-assets` avoids downloading images, JavaScript, CSS, fonts and files; page links are still crawled. This is deliberate because quick mode is for site-wide HTML/SEO coverage, while heavyweight asset/browser validation belongs in a separate mode. The limit and memory settings apply to the crawler, not the whole operating system. Disk use grows with saved responses and reports; retention/deletion is deliberately manual. The sitemap phase is separately bounded to 30 maps, 5 MiB expanded per map, 15 seconds per request and five redirects. The crawler's duration cap does not include discovery.

### Reports

```
reports/<hostname>/<UTC timestamp>-<random suffix>/
  metadata.json
  discovery.json
  seeds.txt
  command.txt
  crawler.log
  report.html
  report.json
  report.txt
  summary.json
  summary.html            # open this first; normalized/context-aware view
  comparison.json        # only when a baseline is selected
  responses/             # native file-backed response data
```

Failed/preflight-blocked runs may contain only metadata. Run names cannot collide silently. Completed runs are made read-only and are never reused by this wrapper. This is filesystem-level protection, not tamper-proof archival storage. `www` and apex hostnames are separate domains. Scheme/seed URL differences are additionally checked before comparison.

Exit status: native nonzero crawler code is preserved; wrapper/preflight/report errors use 2, crawler deadline 124, SIGINT 130, SIGTERM 143. A successful crawl is not a declaration that SEO is healthy. Regressions appear in `comparison.json` without replacing the crawler's exit code.

## Baselines

Review `summary.html`, the raw `report.html`, and `metadata.json` before promotion. `summary.html` is the wrapper's context-aware interpretation; `report.html` is unmodified SiteOne evidence and may contain generic heuristics that need context. Initial runs are discovery only.

```bash
bash scripts/seo-audit.sh baseline \
  reports/example.com/PASTE_EXACT_RUN_DIRECTORY baseline-v1 --accept-partial

bash scripts/seo-audit.sh https://example.com quick --baseline baseline-v1
```

`--accept-partial` explicitly acknowledges the documented crawl coverage limits. It is normally required because no crawler can prove it found every public URL. It does not permit promotion of a failed run. Baseline names cannot be overwritten. Comparisons require the same target URL, configuration, mode, crawler version and wrapper schema.

Automated per-URL comparison covers HTTP failures, missing/duplicate title/description/H1, multiple H1s, repeated trailing title segments, sitemap-aware noindex observations, and heading hierarchy findings. Duplicate title/description/H1 clusters are calculated across indexable URLs only so intentional noindex/query variants do not create fake duplicate-SEO failures. A noindex URL becomes a blocking SEO finding only when stronger context supports it (for example, the URL is also in the discovered sitemap); unsitemapped noindex pages remain observations. URLs absent from a later crawl remain **unverified**, not resolved. Internal redirect links are surfaced in the normalized findings. Canonical/rendering/redirect-chain regressions are not yet comprehensively normalized; inspect native evidence as needed. Raw SiteOne quality scores are preserved but are not treated as the wrapper verdict.

## Configuration

Edit `siteone/config/defaults.json`, or create `siteone/config/domains/<hostname>.json` containing only overrides:

```json
{
  "max_urls": 3000,
  "max_seconds": 3600,
  "query_policy": "preserve",
  "keep_query_params": [],
  "ignore_regex": ["/calendar/", "[?&]session="]
}
```

`query_policy` is `preserve` (default), `keep` (allowlisted keys), or `remove`. The default preserves functional query strings because globally deleting parameters can corrupt real application URLs such as image optimizers, search, pagination, filters, and signed/resource URLs. Use `keep` or `remove` only when the site's parameter semantics are understood. All modes remain bounded by the URL cap. Fragments are excluded. The common action-path exclusions are always applied. There is no arbitrary CLI-argument escape hatch that could enable uploads, auth, robots bypass, or browser execution. Use Rust-compatible regex syntax; Python's preflight syntax check does not cover every Rust regex restriction.

Crawl scope is the supplied hostname. Cross-origin discovery redirects and off-domain sitemap files are omitted and reported. Cloudflare-managed `/cdn-cgi/` endpoints are excluded by default because Cloudflare documents them as infrastructure endpoints that SEO crawlers can mistakenly treat as site content; override the domain configuration only if auditing those provider endpoints is explicitly desired. Supply the final canonical hostname if the homepage redirects elsewhere. URLs disallowed by robots are not fetched. Discovery stops if robots.txt is inaccessible except for a confirmed 404/410. Unknown orphan pages cannot be detected without another URL inventory.

The initial DNS check is not a network sandbox and does not pin addresses for the entire crawl. Only audit ordinary public sites you intend to inspect. The wrapper sends no authenticated requests or forms and does not invoke target APIs. Public GET endpoints can still be badly designed to mutate state; common path exclusions are a heuristic, not a universal proof of application behavior.

## Optional CrawlSEO dashboard

This is an independent dashboard, not a SiteOne report importer. There is no verified native import bridge in this package. SiteOne audit history remains in `reports/`; CrawlSEO holds its own GSC/vitals/history data.

```bash
bash scripts/crawlseo.sh init
# Edit crawlseo/.env: GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET
bash scripts/crawlseo.sh start
# Open http://localhost:3000
bash scripts/crawlseo.sh stop
```

Configure the Google OAuth redirect as `http://localhost:3000/api/auth/callback/google`, enable Search Console API, and grant the scopes described by upstream. Authorized Search Console data is available only for properties your Google account can access. PageSpeed/CrUX coverage and quotas determine whether real-user Core Web Vitals exist; unavailable field data must not be treated as a passing result.

The app image is pulled once and its resolved digest is written to `.env`. Startup thereafter reuses it. PostgreSQL is dedicated to CrawlSEO on the Compose `db` service, with an unexposed database port and a persistent project-specific volume. The application DATABASE_URL is constructed in Compose and cannot be redirected to the audited website by setting DATABASE_URL in `.env`. Upstream startup migrations affect only this local database. The dashboard is exposed only on 127.0.0.1:3000. Random secrets are generated locally; none are bundled.

The current native CrawlSEO crawler uses batches of 15 and is therefore **disabled through the local reverse proxy**: its crawl-trigger POST endpoint returns 403 while crawl-history GETs remain possible. Cron/MCP/scheduling HTTP endpoints are also blocked. Do not launch a separate MCP server or publish the app container directly. GSC and vitals functions can be used independently. This endpoint policy is based on the inspected upstream revision; review routes before upgrading the application image.

Services do not auto-restart when Docker restarts. Use `stop` to release RAM without deleting history. The helper intentionally has no volume-deletion command. Stop CrawlSEO before long SiteOne audits on an 8 GB Mac; Docker VM overhead is additional to the configured container limits. No heavy builds are required.

## Rendered mode: explicit remaining blocker

```bash
bash scripts/seo-audit.sh https://example.com rendered
```

Currently records a blocked run and exits 2 before any requests. `full` does the same. The inspected SiteOne source opens Chromium and allows page scripts to run without a GET/HEAD-only network interception policy. One browser worker does not constrain the dozens of resources and fetches initiated by a page. SiteOne's crawler memory limit also cannot be assumed to cap Chromium's total memory.

To enable production-safe rendering, a browser integration must intercept requests before navigation, block mutation methods (including beacons), handle workers/WebSockets, enforce URL/robots scope, bound subresource traffic and browser memory, and report blocked requests separately from site defects. Test it against a controlled page that attempts POST/PUT/PATCH/DELETE/beacon/WebSocket actions, then validate real public sites. This package does not hide that missing protection behind an opt-in bypass.

## Capability limits

The native report is authoritative for supported SiteOne checks. This wrapper does not assert complete schema validity, hreflang/pagination coverage, duplicate-content detection, canonical conflict detection, visual/mobile correctness, orphan detection, or Core Web Vitals simply because a crawl completed. A robots noindex signal is not proof of Google's actual index state. DOM inspection and response timings are not field CWV data. No tool can guarantee discovery of every URL on any arbitrary website.

## Verification and sources

```bash
python3 scripts/test_audit.py
python3 scripts/test_native.py  # requires SiteOne; uses only a loopback fixture
```

Sources inspected on 2026-09-23:

- https://github.com/janreges/siteone-crawler — source commit `b1f333e226f40935be83ee3977500b2f49cff563`
- https://crawler.siteone.io/configuration/command-line-options/
- https://github.com/janreges/siteone-crawler/blob/main/docs/JSON-OUTPUT.md
- https://crawler.siteone.io/browser-rendering/browser-rendering/
- https://github.com/crawlseo/crawlseo — source commit `f3bdb60b3282cf7577d53a037a5591d84f84cb65`
- https://github.com/crawlseo/crawlseo/blob/main/docker-compose.yml

Documentation/main-branch capabilities can differ from published binaries. Runtime capability checks deliberately reject incompatible installations.
# seo-audit

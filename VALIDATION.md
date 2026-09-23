# Validation — 2026-09-23 UTC

This is a **partial implementation** of the supplied specification. It is not a completed production deployment or a full audit of AVENIME.

| Check | Observed result |
|---|---|
| Python offline suite | 5 tests passed; includes URL trust boundaries, sitemap/index/gzip discovery, robots/scope exclusions, real subprocess orchestration using a fake crawler, failure-code preservation, separate domains, baseline promotion/conflict/mismatch checks, and conservative comparison semantics |
| Bash syntax | All three shell helpers parsed successfully |
| Docker Compose configuration | YAML parsed; loopback-only dashboard and non-published app/DB ports checked |
| Actual SiteOne executable | Official Linux x64 release `2.5.1.20260627` downloaded and executed |
| Native crawl fixture | Passed with crawler exit 0: three URLs visited, HTML and JSON reports generated, missing title/description and 404 detected |
| Native production safeguards exercised | Fixture `/private` was blocked by robots.txt; `/delete/item` was excluded; observed requests used GET/HEAD only |
| AVENIME live wrapper attempt | Blocked at initial DNS resolution: `[Errno -3] Temporary failure in name resolution`; no SiteOne crawl launched |
| example.com live wrapper attempt | Same DNS failure; no SiteOne crawl launched |
| Rendered/full modes | Fail closed before requests; read-only browser network protection is not implemented |
| CrawlSEO/Docker runtime | Not run: Docker is unavailable in this execution environment; user OAuth credentials are also absent |
| macOS execution | Not run; this environment is Linux |

The actual binary returns exit 2 for informational `--help` and `--version` calls. The wrapper accepts that only during those preflight checks and validates their output. Native crawl exit codes remain unchanged.

The live-attempt metadata used a deliberately small validation scope: 20 visited URLs, 120 seconds of crawler time, at most two sitemap files. It is not evidence that public crawling or report generation passed. The supplied default configuration is larger, as documented in README.

`validation/native-fixture/` contains actual native output, with a localhost URL and fixture-only content. `validation/wrapper-checks.log` contains offline test output; its simulated crawler reports do not prove public-site correctness. Test files and synthetic output are clearly separate from `reports/` and cannot be promoted through the normal baseline command.

## Reproduce locally

```bash
cd ~/seo-audit
python3 scripts/test_audit.py
python3 scripts/test_native.py
bash scripts/seo-audit.sh https://avenime.com quick
bash scripts/seo-audit.sh https://example.com quick
```

The native check serves a controlled HTTP fixture on an ephemeral loopback port, invokes the actual SiteOne binary, and leaves its report path in the output. It does not relax the production wrapper's public-URL restrictions.

## Remaining definition-of-done items

- Successful Mac installation and public audits of two domains, then review crawl coverage.
- Enforced GET/HEAD-only browser execution, bounded browser traffic/memory, and controlled mutation-attempt tests before rendered/full modes can be enabled.
- Docker startup, database persistence across stop/start, OAuth sign-in, and gateway 403 validation on the user's Mac.
- Broader per-page regression normalization for canonical conflicts, redirect chains, schema and browser diagnostics where the installed native report supports them.
- A supported SiteOne-to-CrawlSEO history bridge if a single combined dashboard is required; no importer is assumed or fabricated.

The UI/native reports retain supported findings beyond the small regression normalizer. Unsupported or unverified checks must not be described as passing.

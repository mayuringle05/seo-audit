#!/usr/bin/env python3
"""Small standard-library supervisor for SiteOne. No target DB access."""
import argparse
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import gzip
import hashlib
import io
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit, urlunsplit, urljoin, parse_qsl, urlencode, unquote
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.robotparser import RobotFileParser
import uuid
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
UA = 'SiteOne-Crawler'
# Heuristic exclusions cannot recognize arbitrary application-specific GET mutations.
UNSAFE = r'(?i)(?:/|[?&])(?:admin|wp-admin|login|logout|signout|signin|register|signup|checkout|cart|purchase|delete|remove|unsubscribe|import|api)(?:[/=?&#]|$)'
DEFAULTS = json.loads((ROOT / 'siteone/config/defaults.json').read_text())


class Interrupted(KeyboardInterrupt):
    def __init__(self, signum):
        self.signum = signum


def interrupt(signum, frame):
    raise Interrupted(signum)


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def target(value):
    if any(c.isspace() or ord(c) < 32 for c in value) or '\\' in value:
        raise ValueError('URL contains whitespace, control characters, or backslashes')
    u = urlsplit(value)
    if u.scheme not in ('http', 'https') or not u.hostname or u.username or u.password:
        raise ValueError('Use an absolute public HTTP(S) URL without credentials')
    host = u.hostname.rstrip('.').encode('idna').decode('ascii').lower()
    if len(host) > 253 or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', x) for x in host.split('.')):
        raise ValueError('Invalid hostname (IPv6 literals are not supported)')
    if u.port not in (None, 80 if u.scheme == 'http' else 443):
        raise ValueError('Public audits use standard HTTP(S) ports only')
    normalized = urlunsplit((u.scheme, host, u.path or '/', u.query, ''))
    if re.search(UNSAFE, unquote(normalized)):
        raise ValueError('Target matches an excluded action/admin URL')
    return normalized, host


def public_dns(host):
    addresses = {x[4][0] for x in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)}
    if not addresses or any(not ipaddress.ip_address(x).is_global for x in addresses):
        raise ValueError('Target must resolve exclusively to public IP addresses')


def settings(host):
    c = DEFAULTS.copy()
    path = ROOT / 'siteone/config/domains' / (host + '.json')
    if path.exists():
        extra = json.loads(path.read_text())
        if not isinstance(extra, dict) or set(extra) - set(c):
            raise ValueError('Unknown domain configuration key')
        c.update(extra)
    for key, low, high in [('max_urls', 1, 10000), ('max_seconds', 10, 14400), ('memory_mb', 128, 1024), ('max_sitemaps', 1, 100)]:
        if type(c[key]) is not int or not low <= c[key] <= high:
            raise ValueError(f'{key} must be an integer in {low}..{high}')
    if type(c['requests_per_second']) not in (int, float) or not 0.2 <= c['requests_per_second'] <= 2:
        raise ValueError('requests_per_second must be in 0.2..2')
    if c['query_policy'] not in ('remove', 'keep', 'preserve'):
        raise ValueError('query_policy must be remove, keep, or preserve')
    for key in ('keep_query_params', 'ignore_regex'):
        if not isinstance(c[key], list) or any(not isinstance(x, str) or '\n' in x or '\r' in x for x in c[key]):
            raise ValueError(f'{key} must be a list of single-line strings')
    if any(not re.fullmatch(r'[\w.-]+', x) for x in c['keep_query_params']):
        raise ValueError('Invalid query parameter name')
    if c['query_policy'] == 'keep' and not c['keep_query_params']:
        raise ValueError('keep requires keep_query_params')
    for regex in c['ignore_regex']:
        re.compile(regex)
    return c


def normalize_query(url, config):
    u = urlsplit(url)
    if config['query_policy'] == 'preserve':
        return url
    query = '' if config['query_policy'] == 'remove' else urlencode([(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True) if k in config['keep_query_params']])
    return urlunsplit((u.scheme, u.netloc, u.path, query, ''))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def discover(url, config):
    """Bounded same-origin sitemap discovery; SiteOne handles normal link discovery."""
    parsed = urlsplit(url)
    origin = f'{parsed.scheme}://{parsed.netloc}'
    notes, sitemaps, pages = [], [], set()
    opener = build_opener(NoRedirect())
    last = [0.0]
    cap = 5 * 1024 * 1024

    def fetch(address):
        for _ in range(6):
            u = urlsplit(address)
            if (u.scheme, u.netloc) != (parsed.scheme, parsed.netloc):
                raise ValueError('Cross-origin discovery redirect excluded: ' + address)
            public_dns(u.hostname)
            time.sleep(max(0, 1 / config['requests_per_second'] - (time.monotonic() - last[0])))
            last[0] = time.monotonic()
            try:
                response = opener.open(Request(address, headers={'User-Agent': UA, 'Accept-Encoding': 'identity'}), timeout=15)
            except HTTPError as e:
                if e.code in (301, 302, 303, 307, 308):
                    address = urljoin(address, e.headers['Location'])
                    continue
                raise
            with response:
                data = response.read(cap + 1)
            if len(data) > cap:
                raise ValueError('Discovery response exceeds 5 MiB')
            if data.startswith(b'\x1f\x8b'):
                with gzip.GzipFile(fileobj=io.BytesIO(data)) as gz:
                    data = gz.read(cap + 1)
                if len(data) > cap:
                    raise ValueError('Expanded sitemap exceeds 5 MiB')
            return data
        raise ValueError('Too many discovery redirects')

    robots = RobotFileParser(origin + '/robots.txt')
    try:
        raw = fetch(robots.url).decode('utf-8', errors='replace')
    except HTTPError as e:
        if e.code not in (404, 410):
            raise ValueError(f'robots.txt unavailable ({e.code}); refusing crawl') from e
        raw = ''
        notes.append('robots.txt absent (HTTP ' + str(e.code) + ')')
    robots.parse(raw.splitlines())
    seeds = []
    for candidate in [origin + '/', normalize_query(url, config)]:
        if robots.can_fetch(UA, candidate) and candidate not in seeds:
            seeds.append(candidate)
    if not seeds:
        raise ValueError('robots.txt blocks the supplied page and homepage')
    queue = robots.site_maps() or [origin + '/sitemap.xml']
    seen = set()
    while queue and len(seen) < config['max_sitemaps'] and len(pages) < config['max_urls']:
        address = queue.pop(0)
        if address in seen:
            continue
        seen.add(address)
        u = urlsplit(address)
        if (u.scheme, u.netloc) != (parsed.scheme, parsed.netloc) or not robots.can_fetch(UA, address):
            notes.append('Excluded sitemap: ' + address)
            continue
        try:
            data = fetch(address)
            if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
                raise ValueError('DTD/entity declarations rejected')
            doc = ET.fromstring(data)
            kind = doc.tag.rsplit('}', 1)[-1]
            if kind not in ('sitemapindex', 'urlset'):
                raise ValueError('Not a sitemap')
            sitemaps.append(address)
            for item in doc:
                loc = next((n.text for n in item if n.tag.rsplit('}', 1)[-1] == 'loc'), None)
                if not loc:
                    continue
                loc = loc.strip()
                p = urlsplit(loc)
                if (p.scheme, p.netloc) != (parsed.scheme, parsed.netloc):
                    if kind == 'sitemapindex':
                        notes.append('Excluded sitemap: ' + loc)
                    continue
                if kind == 'sitemapindex':
                    if len(queue) < config['max_sitemaps']:
                        queue.append(loc)
                    else:
                        notes.append('Sitemap queue limit reached')
                elif not re.search(UNSAFE, unquote(loc)) and robots.can_fetch(UA, loc):
                    loc = normalize_query(loc, config)
                    if not any(re.search(r, loc) for r in config['ignore_regex']):
                        pages.add(loc)
                    if len(pages) >= config['max_urls']:
                        notes.append('Sitemap URL limit reached')
                        break
        except (OSError, ValueError, ET.ParseError) as e:
            notes.append(f'{address}: {e}')
    if queue:
        notes.append('Sitemap discovery incomplete: configured limit reached')
    return list(dict.fromkeys(seeds + sorted(pages))), {'sitemaps': sitemaps, 'sitemap_urls': sorted(pages), 'notes': notes}


def command(binary, url, config, run):
    args = [binary, '--config-file=' + str(run / 'empty.conf'), '--url=' + url,
            '--url-list=' + str(run / 'seeds.txt'), '--workers=1',
            '--max-reqs-per-sec=' + str(config['requests_per_second']),
            '--memory-limit=' + str(config['memory_mb']) + 'M',
            '--max-visited-urls=' + str(config['max_urls']),
            '--max-queue-length=' + str(config['max_urls'] * 2),
            '--max-skipped-urls=' + str(config['max_urls'] * 2),
            '--rows-limit=' + str(config['max_urls'] * 2),
            '--max-url-length=2048', '--max-non200-responses-per-basename=3',
            '--timeout=15', '--no-cache', '--ignore-html-comments', '--no-color',
            '--hide-progress-bar', '--show-scheme-and-host', '--do-not-truncate-url',
            '--user-agent=' + UA + '!', '--result-storage=file',
            '--result-storage-dir=' + str(run / 'responses'),
            '--output-html-report=' + str(run / 'report.html'),
            '--output-json-file=' + str(run / 'report.json'),
            '--output-text-file=' + str(run / 'report.txt'), '--ignore-regex=' + UNSAFE]
    args += ['--ignore-regex=' + r for r in config['ignore_regex']]
    if config['query_policy'] == 'remove':
        args.append('--remove-query-params')
    elif config['query_policy'] == 'keep':
        args += ['--keep-query-param=' + k for k in config['keep_query_params']]
    return args


def summarize(report, url):
    if not isinstance(report.get('results'), list) or not isinstance(report.get('tables'), dict):
        raise ValueError('Unsupported SiteOne JSON schema; raw report retained')
    issues = set()
    pages = {}
    for r in report['results']:
        if not isinstance(r.get('url'), str):
            raise ValueError('Missing result URL')
        address = urljoin(url, r['url'])
        status = str(r.get('status', ''))
        pages[address] = status
        if not status.isdigit() or int(status) >= 400:
            issues.add(('http-' + status, address))
    clusters = {}
    rows = report['tables'].get('seo', {}).get('rows', [])
    for field in ('title', 'description', 'h1'):
        grouped = defaultdict(set)
        for row in rows:
            address = urljoin(url, row['urlPathAndQuery'])
            value = row.get(field, '').strip()
            if value:
                grouped[value].add(address)
            elif row.get('robotsIndex') != '0' and row.get('deniedByRobotsTxt') != '1':
                issues.add(('missing-' + field, address))
        clusters[field] = [{'value': v, 'urls': sorted(a)} for v, a in grouped.items() if len(a) > 1]
        for cluster in clusters[field]:
            for address in cluster['urls']:
                issues.add(('duplicate-' + field, address))
    for row in rows:
        if row.get('robotsIndex') == '0':
            # Observation; intent requires a reviewed baseline.
            issues.add(('noindex-observed', urljoin(url, row['urlPathAndQuery'])))
    for row in report['tables'].get('seo-headings', {}).get('rows', []):
        if int(row.get('headingsErrorsCount', '0')) > 0:
            issues.add(('heading-hierarchy', urljoin(url, row['urlPathAndQuery'])))
    return {'schema': 1, 'pages': pages, 'issues': [{'code': k, 'url': u} for k, u in sorted(issues)],
            'issue_counts': dict(Counter(k for k, _ in issues)), 'duplicate_clusters': clusters,
            'native_findings': report.get('summary', {}).get('items', []),
            'stats': report.get('stats', {}), 'quality_scores': report.get('qualityScores', {}),
            'comparison_scope': 'HTTP errors, missing/duplicate titles/descriptions/H1, noindex observations, heading hierarchy; other native tables retained for review'}


def compare(before, after):
    def keys(x):
        return {(i['code'], i['url']) for i in x['issues']}
    old, new = keys(before), keys(after)
    seen = set(after['pages'])
    return {'new': sorted(new - old), 'no_longer_observed_on_recrawled_urls': sorted(i for i in old - new if i[1] in seen),
            'unverified_previous_issues': sorted(i for i in old - new if i[1] not in seen),
            'previous_urls_not_observed': sorted(set(before['pages']) - seen),
            'new_urls': sorted(seen - set(before['pages'])),
            'note': 'Absent URLs and blocked pages are not evidence that problems were fixed.'}


@contextmanager
def lock():
    # ponytail: one process-wide audit lock; parallel audits would exceed the laptop budget.
    with (ROOT / '.audit.lock').open('a') as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            raise ValueError('Another audit/baseline operation is running') from e
        yield


def seal(run):
    for file in run.rglob('*'):
        if file.is_file():
            file.chmod(0o444)
    for folder in sorted((p for p in run.rglob('*') if p.is_dir()), reverse=True):
        folder.chmod(0o555)
    run.chmod(0o555)


def promote(run_path, name, accept_partial=False):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', name):
        raise ValueError('Invalid baseline name')
    run = Path(run_path).resolve()
    if not run.is_relative_to((ROOT / 'reports').resolve()):
        raise ValueError('Baseline must come from this workspace reports directory')
    meta = json.loads((run / 'metadata.json').read_text())
    if meta.get('status') != 'complete' or meta.get('exit_code') != 0:
        raise ValueError('Cannot promote failed, interrupted, or incompatible run')
    if meta.get('coverage_warnings') and not accept_partial:
        raise ValueError('Coverage warnings exist; review them, then use --accept-partial if this scope is intentional')
    destination = ROOT / 'baselines' / meta['domain'] / name
    destination.mkdir(parents=True, exist_ok=False)
    for file in ('metadata.json', 'summary.json', 'report.json'):
        shutil.copyfile(run / file, destination / file)
    write_json(destination / 'promotion.json', {'source_run': str(run), 'accepted_at': datetime.now(timezone.utc).isoformat(), 'accepted_partial': accept_partial})
    seal(destination)
    print(destination)


def audit(url, mode, baseline=None):
    url, host = target(url)
    config = settings(host)
    run = ROOT / 'reports' / host / (datetime.now(timezone.utc).strftime('%Y-%m-%d_%H%M%S_%fZ') + '-' + uuid.uuid4().hex[:8])
    run.mkdir(parents=True, exist_ok=False)
    meta = {'target_url': url, 'domain': host, 'mode': mode, 'started_at': datetime.now(timezone.utc).isoformat(),
            'config': config, 'status': 'running', 'exit_code': None, 'coverage_warnings': []}
    write_json(run / 'metadata.json', meta)
    result = 2
    try:
        if mode != 'quick':
            raise ValueError('Rendered/full audit blocked: upstream browser mode does not enforce GET/HEAD-only requests or bound browser subresource traffic. See README. No requests sent.')
        binary = shutil.which('siteone-crawler')
        if not binary:
            raise ValueError('siteone-crawler missing. Run scripts/setup-siteone.sh on your Mac.')
        # Use an explicit empty config, including for preflight: user/global config cannot turn on upload, auth, or browser mode.
        (run / 'empty.conf').write_text('')
        version_result = subprocess.run([binary, '--config-file=' + str(run / 'empty.conf'), '--no-color', '--version'], capture_output=True, text=True, timeout=15)
        # SiteOne 2.5.1 returns 2 for informational --help/--version exits.
        match = re.search(r'Version:\s*([0-9][0-9A-Za-z.+_-]*)', version_result.stdout)
        if version_result.returncode not in (0, 2) or not match:
            raise ValueError('Could not identify SiteOne version: ' + version_result.stderr)
        version = match.group(1)
        meta['crawler_version'] = version
        args = command(binary, url, config, run)
        meta['command'] = args
        help_result = subprocess.run([binary, '--config-file=' + str(run / 'empty.conf'), '--no-color', '--help'], capture_output=True, text=True, timeout=15)
        if help_result.returncode not in (0, 2):
            raise ValueError('SiteOne help failed')
        help_text = help_result.stdout
        flags = {a.split('=')[0] for a in args[1:] if a.startswith('--')} - {'--config-file'}
        missing = sorted(f for f in flags if not re.search(re.escape(f) + r'(?:[=\s<]|$)', help_text))
        if missing:
            raise ValueError('Installed SiteOne lacks required options: ' + ', '.join(missing))
        scope = {'url': url, 'mode': mode, 'config': config, 'version': version, 'wrapper_schema': 1}
        meta['scope_fingerprint'] = hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest()
        prior = None
        if baseline:
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', baseline):
                raise ValueError('Invalid baseline name')
            folder = ROOT / 'baselines' / host / baseline
            prior_meta = json.loads((folder / 'metadata.json').read_text())
            if prior_meta['scope_fingerprint'] != meta['scope_fingerprint']:
                raise ValueError('Baseline target, mode, configuration, or crawler version differs; refusing misleading comparison')
            prior = json.loads((folder / 'summary.json').read_text())
            meta['baseline'] = baseline
        public_dns(host)
        seeds, discovery = discover(url, config)
        write_json(run / 'discovery.json', discovery)
        (run / 'seeds.txt').write_text('\n'.join(seeds) + '\n')
        (run / 'command.txt').write_text(shlex.join(args) + '\n')
        write_json(run / 'metadata.json', meta)
        print('Audit started: ' + str(run), flush=True)
        with (run / 'crawler.log').open('w') as log:
            process = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, cwd=run, start_new_session=True)
            try:
                result = process.wait(timeout=config['max_seconds'])
                if result < 0:
                    result = 128 - result
            except (subprocess.TimeoutExpired, KeyboardInterrupt) as e:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                result = 124 if isinstance(e, subprocess.TimeoutExpired) else 128 + getattr(e, 'signum', 2)
                meta['error'] = 'Crawl deadline exceeded' if result == 124 else 'Interrupted'
        meta['crawler_exit_code'] = result
        if result:
            meta['status'] = 'failed'
        else:
            if not (run / 'report.html').is_file():
                raise ValueError('Crawler succeeded but HTML report is missing')
            summary = summarize(json.loads((run / 'report.json').read_text()), url)
            write_json(run / 'summary.json', summary)
            if not summary['pages']:
                raise ValueError('No URLs observed; run is not a valid audit')
            warnings = discovery['notes'].copy()
            if len(summary['pages']) >= config['max_urls']:
                warnings.append('URL cap reached; crawl may be incomplete')
            if config['query_policy'] != 'preserve':
                warnings.append('Query URLs normalized by configured policy; query-dependent content may be omitted')
            warnings.append('Coverage is limited to discovered, permitted URLs; unlinked URLs absent from sitemaps cannot be found.')
            meta['coverage_warnings'] = warnings
            meta['status'] = 'complete'
            if prior is not None:
                write_json(run / 'comparison.json', compare(prior, summary))
    except KeyboardInterrupt as e:
        result = 128 + getattr(e, 'signum', 2)
        meta.update(status='interrupted', error='Interrupted')
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as e:
        meta.update(status='failed', error=str(e))
        # Preserve a nonzero crawler exit status; otherwise report wrapper failure.
        result = result or 2
        print('Audit failed: ' + str(e), file=sys.stderr)
    finally:
        meta.update(exit_code=result, finished_at=datetime.now(timezone.utc).isoformat())
        write_json(run / 'metadata.json', meta)
        seal(run)
        print('Report directory: ' + str(run))
    return result


def main():
    if len(sys.argv) > 1 and sys.argv[1] == 'baseline':
        parser = argparse.ArgumentParser(description='Explicitly accept a reviewed audit as a baseline')
        parser.add_argument('baseline')
        parser.add_argument('run')
        parser.add_argument('name')
        parser.add_argument('--accept-partial', action='store_true')
        args = parser.parse_args()
        with lock():
            promote(args.run, args.name, args.accept_partial)
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('url')
    parser.add_argument('mode', nargs='?', default='quick', choices=['quick', 'rendered', 'full'])
    parser.add_argument('--baseline')
    args = parser.parse_args()
    with lock():
        return audit(args.url, args.mode, args.baseline)


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, interrupt)
    try:
        sys.exit(main())
    except (ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(2)

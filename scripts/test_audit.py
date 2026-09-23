#!/usr/bin/env python3
"""Offline checks. Fake crawler validates orchestration, not SiteOne itself."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import audit

REPORT = {
    'results': [{'url': 'https://example.com/', 'status': '200', 'type': 1},
                {'url': 'https://example.com/missing', 'status': '404', 'type': 1}],
    'tables': {'seo': {'rows': [{'urlPathAndQuery': '/', 'title': '', 'description': '', 'h1': 'Home', 'robotsIndex': '1'}]}},
    'stats': {'totalUrls': 2}, 'summary': {'items': []}
}

class Checks(unittest.TestCase):
    def test_url_boundaries(self):
        self.assertEqual(audit.target('https://EXAMPLE.com:443/a#x'), ('https://example.com/a', 'example.com'))
        for url in ['file:///etc/passwd', 'https://x:y@example.com', 'https://example.com\n--upload',
                    'https://example.com:3000', 'https://example.com/admin/', 'https://example.com/%64elete/x',
                    'https://../', 'https://foo\\bar.com']:
            with self.assertRaises(ValueError, msg=url):
                audit.target(url)
        with patch('socket.getaddrinfo', return_value=[(None, None, None, None, ('127.0.0.1', 0))]):
            with self.assertRaises(ValueError):
                audit.public_dns('example.com')

    def test_summary_and_nonmisleading_comparison(self):
        summary = audit.summarize(REPORT, 'https://example.com/')
        self.assertEqual(summary['issue_counts']['missing-title'], 1)
        later = dict(summary, pages={'https://example.com/': '200'}, issues=[])
        diff = audit.compare(summary, later)
        self.assertIn(('http-404', 'https://example.com/missing'), diff['unverified_previous_issues'])
        self.assertNotIn(('http-404', 'https://example.com/missing'), diff['no_longer_observed_on_recrawled_urls'])
        with self.assertRaises(ValueError):
            audit.summarize({}, 'https://example.com/')

    def test_orchestration_and_baselines(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'siteone/config/domains').mkdir(parents=True)
            bin_path = root / 'siteone-crawler'
            flags = ' '.join(a.split('=')[0] for a in audit.command('fake', 'https://example.com/', audit.DEFAULTS, root))
            bin_path.write_text('#!/usr/bin/env python3\n' + '''import sys, json, os
from pathlib import Path
args = dict(x[2:].split('=', 1) for x in sys.argv[1:] if x.startswith('--') and '=' in x)
if '--version' in sys.argv:
    print('Version: 2.5.1-fixture'); sys.exit(2)
if '--help' in sys.argv:
    print(HELP); sys.exit(2)
if os.environ.get('FAKE_EXIT'):
    sys.exit(int(os.environ['FAKE_EXIT']))
Path(args['output-json-file']).write_text(json.dumps(REPORT))
Path(args['output-html-report']).write_text('<html>fixture</html>')
print('fixture log')
'''.replace('HELP', repr(flags)).replace('REPORT', repr(REPORT)))
            bin_path.chmod(0o755)
            env = dict(os.environ, PATH=str(root) + os.pathsep + os.environ['PATH'])
            with patch.object(audit, 'ROOT', root), patch.dict(os.environ, env), patch.object(audit, 'public_dns'), patch.object(audit, 'discover', return_value=(['https://example.com/'], {'notes': [], 'sitemaps': [], 'sitemap_urls': []})):
                self.assertEqual(audit.audit('https://example.com/', 'quick'), 0)
                first = next((root / 'reports/example.com').iterdir())
                self.assertTrue((first / 'report.html').exists())
                self.assertEqual(json.loads((first / 'metadata.json').read_text())['status'], 'complete')
                with self.assertRaises(ValueError):
                    audit.promote(first, 'v1')
                audit.promote(first, 'v1', True)
                self.assertEqual(audit.audit('https://example.com/', 'quick', 'v1'), 0)
                self.assertEqual(len(list((root / 'reports/example.com').iterdir())), 2)
                self.assertEqual(audit.audit('https://other.example/', 'quick'), 0)
                self.assertTrue((root / 'reports/other.example').is_dir())
                self.assertEqual(audit.audit('https://other.example/', 'quick', 'v1'), 2)
                with patch.dict(os.environ, {'FAKE_EXIT': '17'}):
                    self.assertEqual(audit.audit('https://example.com/', 'quick'), 17)
                self.assertEqual(audit.audit('https://example.com/', 'rendered'), 2)
                self.assertEqual(audit.audit('https://example.com/different', 'quick', 'v1'), 2)
                with self.assertRaises(FileExistsError):
                    audit.promote(first, 'v1', True)
                # Sealed reports need write bits restored for TemporaryDirectory cleanup on macOS.
                for p in root.rglob('*'):
                    if p.is_dir(): p.chmod(0o755)

    def test_sitemap_discovery_respects_scope_robots_and_gzip(self):
        import io
        import gzip
        class Response(io.BytesIO):
            pass
        fixtures = {
            'https://example.com/robots.txt': b'User-agent: *\nDisallow: /private\nSitemap: https://example.com/index.xml\n',
            'https://example.com/index.xml': b'<sitemapindex><sitemap><loc>https://example.com/nested.xml.gz</loc></sitemap><sitemap><loc>https://foreign.example/map.xml</loc></sitemap></sitemapindex>',
            'https://example.com/nested.xml.gz': gzip.compress(b'<urlset><url><loc>https://example.com/orphan</loc></url><url><loc>https://example.com/private</loc></url><url><loc>https://example.com/delete/item</loc></url><url><loc>https://foreign.example/x</loc></url></urlset>')
        }
        class Opener:
            def open(self, request, timeout):
                return Response(fixtures[request.full_url])
        with patch.object(audit, 'build_opener', return_value=Opener()), patch.object(audit, 'public_dns'), patch.object(audit.time, 'sleep'):
            seeds, discovery = audit.discover('https://example.com/', audit.DEFAULTS)
        self.assertEqual(seeds, ['https://example.com/', 'https://example.com/orphan'])
        self.assertEqual(len(discovery['sitemaps']), 2)
        self.assertTrue(any('Excluded sitemap' in note for note in discovery['notes']))

    def test_safe_command_and_query_configuration(self):
        args = audit.command('/a path/bin', 'https://example.com/', audit.DEFAULTS, Path('/a path/run'))
        self.assertIn('--workers=1', args)
        self.assertIn('--no-cache', args)
        self.assertNotIn('--ignore-robots-txt', args)
        self.assertFalse(any(x.startswith(('--upload', '--browser', '--http-auth')) for x in args))
        self.assertNotIn('--remove-query-params', args)
        image_url = 'https://example.com/_next/image?url=%2Fposter.jpg&w=640&q=75'
        self.assertEqual(audit.normalize_query(image_url, audit.DEFAULTS), image_url)
        self.assertEqual(audit.normalize_query('https://example.com/?page=2&utm_source=x', dict(audit.DEFAULTS, query_policy='keep', keep_query_params=['page'])), 'https://example.com/?page=2')


    def test_real_crawl_normalization_uses_sitemap_context(self):
        report = {
            'results': [
                {'url': 'https://example.com/', 'status': '200', 'type': 1},
                {'url': 'https://example.com/public', 'status': '200', 'type': 1},
                {'url': 'https://example.com/internal', 'status': '200', 'type': 1},
            ],
            'tables': {
                'seo': {'rows': [
                    {'urlPathAndQuery': '/', 'title': 'Home', 'description': 'Home desc', 'h1': 'Home', 'robotsIndex': '1'},
                    {'urlPathAndQuery': '/public', 'title': 'Public', 'description': 'Public desc', 'h1': 'Public', 'robotsIndex': '0'},
                    {'urlPathAndQuery': '/internal', 'title': 'Internal', 'description': 'Internal desc', 'h1': 'Internal', 'robotsIndex': '0'},
                ]},
                'seo-headings': {'rows': []},
                'skipped': {'rows': [
                    {'reason': 'Not allowed host', 'url': 'https://docs.example.net/source', 'sourceAttr': '<a href>', 'sourceUqId': '/'}
                ]},
                'security': {'rows': [
                    {'header': 'Content-Security-Policy', 'critical': '0', 'warning': '3', 'notice': '0',
                     'recommendation': "CSP contains unsafe-inline"}
                ]},
            },
            'summary': {'items': [
                {'aplCode': 'seo-noindex-sitewide', 'status': 'CRITICAL', 'text': '2 of 3 are noindex'},
                {'aplCode': 'skipped', 'status': 'CRITICAL', 'text': '1 skipped URL'},
                {'aplCode': 'security', 'status': 'WARNING', 'text': 'Security - 3 pages with warnings'},
                {'aplCode': 'pages-without-h1', 'status': 'OK', 'text': 'All pages have H1'},
                {'aplCode': 'ssl-protocol-unsafe', 'status': 'CRITICAL', 'text': 'TLSv1.0 is unsafe'},
            ]},
            'stats': {'totalUrls': 3},
            'qualityScores': {'overall': {'score': 4.2}},
        }
        discovery = {'sitemap_urls': ['https://example.com/public'], 'notes': [], 'sitemaps': []}
        summary = audit.summarize(report, 'https://example.com/', discovery, dict(audit.DEFAULTS, max_urls=100))

        self.assertEqual(summary['indexing']['noindex_observed'], 2)
        self.assertEqual(summary['indexing']['noindex_in_sitemap'], 1)
        self.assertEqual(summary['issue_counts']['noindex-in-sitemap'], 1)
        self.assertTrue(any(x['code'] == 'noindex-in-sitemap' and x['severity'] == 'critical'
                            for x in summary['normalized_findings']))
        self.assertTrue(any(x['code'] == 'external-urls-skipped' and x['severity'] == 'info'
                            for x in summary['normalized_findings']))
        self.assertTrue(any(x['code'] == 'security-header-content-security-policy' and x['affected'] == 3
                            for x in summary['normalized_findings']))
        self.assertTrue(any(x['code'] == 'siteone-ssl-protocol-unsafe'
                            for x in summary['normalized_findings']))
        self.assertFalse(any(x['code'] == 'siteone-seo-noindex-sitewide'
                             for x in summary['normalized_findings']))
        self.assertEqual(summary['quality_scores']['status'], 'not_authoritative')
        self.assertEqual(summary['native_quality_scores']['overall']['score'], 4.2)

    def test_unsitemapped_noindex_is_observation_not_critical_failure(self):
        report = {
            'results': [{'url': 'https://example.com/', 'status': '200', 'type': 1},
                        {'url': 'https://example.com/search', 'status': '200', 'type': 1}],
            'tables': {
                'seo': {'rows': [
                    {'urlPathAndQuery': '/', 'title': 'Home', 'description': 'Desc', 'h1': 'Home', 'robotsIndex': '1'},
                    {'urlPathAndQuery': '/search', 'title': 'Search', 'description': 'Desc2', 'h1': 'Search', 'robotsIndex': '0'},
                ]},
                'seo-headings': {'rows': []},
            },
            'summary': {'items': [
                {'aplCode': 'seo-noindex-sitewide', 'status': 'CRITICAL', 'text': '1 of 2 are noindex'}
            ]},
            'stats': {'totalUrls': 2},
        }
        summary = audit.summarize(
            report, 'https://example.com/',
            {'sitemap_urls': ['https://example.com/'], 'notes': [], 'sitemaps': []},
            dict(audit.DEFAULTS, max_urls=100),
        )
        self.assertEqual(summary['indexing']['noindex_in_sitemap'], 0)
        self.assertFalse(any(x['code'] == 'noindex-in-sitemap' for x in summary['normalized_findings']))
        self.assertTrue(any(x['code'] == 'high-noindex-outside-sitemap' and x['severity'] == 'info'
                            for x in summary['normalized_findings']))


if __name__ == '__main__':
    unittest.main()

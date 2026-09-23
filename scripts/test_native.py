import sys, json, threading, tempfile, subprocess, shutil
from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit
requests=[]
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def do_GET(self):
        requests.append((self.command,self.path))
        status=200
        body=b''
        content='text/html'
        origin=f'http://127.0.0.1:{self.server.server_port}'
        if self.path=='/robots.txt':
            content='text/plain';body=b'User-agent: *\nDisallow: /private\n'
        elif self.path=='/':
            body=b'<html lang="en"><head><title>Fixture home</title></head><body><h1>Home</h1><a href="/second">Second</a><a href="/missing">Missing</a><a href="/private">Private</a><a href="/delete/item">Excluded action</a></body></html>'
        elif self.path=='/second':
            body=b'<html lang="en"><head></head><body><h1>Second</h1></body></html>'
        else:
            status=404;body=b'<html><title>Not found</title><body>Missing</body></html>'
        self.send_response(status);self.send_header('Content-Type',content);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    def do_HEAD(self):
        requests.append((self.command,self.path)); self.send_response(200);self.end_headers()
server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
run=Path(tempfile.mkdtemp(prefix='seo-native-check-'))
print('Fixture reports: ' + str(run))
url=f'http://127.0.0.1:{server.server_port}/'
(run/'empty.conf').write_text('')
(run/'seeds.txt').write_text(url+'\n')
binary=shutil.which('siteone-crawler')
if not binary: raise SystemExit('siteone-crawler must be on PATH')
args=audit.command(binary,url,dict(audit.DEFAULTS,max_urls=20),run)
with (run/'crawler.log').open('w') as log:
    proc=subprocess.run(args,stdout=log,stderr=subprocess.STDOUT,timeout=90)
server.shutdown()
audit.write_json(run/'requests.json',requests)
print('Native exit:',proc.returncode)
if proc.returncode: print((run/'crawler.log').read_text()[-2500:]);sys.exit(proc.returncode)
report=json.loads((run/'report.json').read_text())
summary=audit.summarize(report,url)
audit.write_json(run/'summary.json',summary)
print('URLs:',list(summary['pages']))
print('Issues:',summary['issue_counts'])
assert (run/'report.html').exists()
assert any(i['code']=='http-404' for i in summary['issues'])
assert any(i['code']=='missing-title' for i in summary['issues'])
assert not any(p=='/private' or p=='/delete/item' for m,p in requests), requests
assert all(m in ('GET','HEAD') for m,p in requests)
print('PASS: actual binary crawling, HTML+JSON reports, issue parsing, robots and action-path exclusions')

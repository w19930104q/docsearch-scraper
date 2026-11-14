# coding: utf-8
"""
Local site replay: a tiny HTTP server serving a static site defined as a
dict of routes, so crawl scenarios (success, HTTP 500, timeout, 404 seed)
can be replayed deterministically without any network access.
"""
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def make_page(title=None, text=None, links=()):
    """Build a minimal HTML page with an h1, a paragraph and absolute links"""
    body = ''
    if title is not None:
        body += '<h1>{}</h1>'.format(title)
    if text is not None:
        body += '<p>{}</p>'.format(text)
    for link in links:
        body += '<a href="{}">link</a>'.format(link)
    return '<html><body>{}</body></html>'.format(body)


class SiteReplay:
    """Serve `routes` ({path: {'status', 'body', 'delay'}}) on 127.0.0.1"""

    def __init__(self, routes):
        self.routes = routes
        handler = self._build_handler(routes)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={'poll_interval': 0.05})
        self.thread.daemon = True
        self.thread.start()

    @staticmethod
    def _build_handler(routes):
        class ReplayHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                route = routes.get(self.path)
                if route is None:
                    self.send_error(404)
                    return
                delay = route.get('delay', 0)
                if delay:
                    time.sleep(delay)
                body = route.get('body', '').encode('utf-8')
                self.send_response(route.get('status', 200))
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        return ReplayHandler

    @property
    def base_url(self):
        host, port = self.server.server_address
        return 'http://{}:{}'.format(host, port)

    def url(self, path):
        return self.base_url + path

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

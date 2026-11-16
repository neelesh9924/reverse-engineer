"""A stand-in for Chrome's DevTools HTTP endpoints, for the website-subject tests. It serves
/json/version, /json and /json/list (one page target) and /json/new on the port it is given, so
WebTarget can discover a cdp_endpoint and a page target_id without a real browser.

Run as: python fake_cdp.py <port> <url> [profile]
With a profile folder it writes <profile>/DevToolsActivePort, as Chrome does.
FAKE_CDP_BEHAVIOUR:
  nopage     /json lists no page; only PUT /json/new?<encoded url> opens one ("PAGE-NEW-1"), as
             current Chrome does (GET /json/new is refused with 405)
  garbage    /json/version answers with text that is not JSON
  imposter   /json/version answers JSON with no "Browser" (not a browser)
  silent     serve nothing at all (DevTools never comes up)
"""
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

PORT = int(sys.argv[1])
URL = sys.argv[2] if len(sys.argv) > 2 else "https://example.com"
PROFILE = sys.argv[3] if len(sys.argv) > 3 else ""
BEHAVIOUR = os.environ.get("FAKE_CDP_BEHAVIOUR", "")
PAGE = {"id": "PAGE-TARGET-1", "type": "page", "title": "Example", "url": URL,
        "webSocketDebuggerUrl": f"ws://127.0.0.1:{PORT}/devtools/page/PAGE-TARGET-1"}
_opened = []


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _send(self, payload, status=200, raw=None):
        body = raw if raw is not None else json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _pages(self):
        if BEHAVIOUR == "nopage":
            return list(_opened)
        return [PAGE]

    def do_GET(self):
        if self.path.startswith("/json/version"):
            if BEHAVIOUR == "garbage":
                return self._send(None, raw=b"<html>not json</html>")
            if BEHAVIOUR == "imposter":
                return self._send({"hello": "not a browser"})
            return self._send({"Browser": "FakeChrome/1.0", "webSocketDebuggerUrl": f"ws://127.0.0.1:{PORT}/devtools/browser"})
        if self.path.startswith("/json/new"):
            if BEHAVIOUR == "nopage":
                return self._send({"error": "Using unsafe HTTP verb GET to invoke /json/new."}, status=405)
            return self._send(PAGE)
        if self.path.startswith("/json"):
            return self._send(self._pages())
        self._send({"error": "not found"}, status=404)

    def do_PUT(self):
        if self.path.startswith("/json/new"):
            query = self.path.partition("?")[2]
            page = {"id": "PAGE-NEW-1", "type": "page", "title": "New", "url": unquote(query),
                    "webSocketDebuggerUrl": f"ws://127.0.0.1:{PORT}/devtools/page/PAGE-NEW-1"}
            if not _opened:
                _opened.append(page)
            return self._send(page)
        self._send({"error": "not found"}, status=404)


def main():
    if BEHAVIOUR == "silent":
        while True:
            time.sleep(1)
    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    if PROFILE:
        Path(PROFILE, "DevToolsActivePort").write_text(f"{PORT}\n/devtools/browser/fake\n", encoding="utf-8")
    httpd.serve_forever()


if __name__ == "__main__":
    main()

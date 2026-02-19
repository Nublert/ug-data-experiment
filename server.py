#!/usr/bin/env python3
"""
Tiny static file server + scrape trigger endpoint.

- Serves index.html and static assets from the project root.
- Exposes:
    - GET /data/ug_top.json  (cached data file, if present)
    - POST/GET /scrape?force=1  (runs one-off scraper job)

Scraping itself is implemented in scraper.py and only runs when the
user explicitly triggers it (e.g. via the "Refresh data" button in the UI).
"""

from __future__ import annotations

import json
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from scraper import CACHE_PATH, scrape_all
import sys
print("PYTHON:", sys.executable)
print("PREFIX:", sys.prefix)


class Handler(SimpleHTTPRequestHandler):
    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)

        if parsed.path == "/data/ug_top.json":
            if not CACHE_PATH.exists():
                self._send_json(
                    404,
                    {"error": "No cached data yet", "details": str(CACHE_PATH)},
                )
                return
            try:
                with CACHE_PATH.open("rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except Exception as e:
                self._send_json(500, {"error": "Failed to read cache file", "details": str(e)})
            return

        if parsed.path == "/scrape":
            qs = parse_qs(parsed.query or "")
            force = qs.get("force", ["0"])[0] in ("1", "true", "yes", "on")
            try:
                data = scrape_all(force=force)
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "meta": data.get("meta", {}),
                        "row_count": data.get("meta", {}).get("row_count", 0),
                    },
                )
            except Exception as e:
                # If scraping fails, do NOT delete existing cache; just report the error.
                self._send_json(500, {"ok": False, "error": "Scrape failed", "details": str(e)})
            return

        # Default: serve static files (index.html, etc.)
        return super().do_GET()


def main() -> int:
    port = int(os.environ.get("PORT", "5177"))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Server running on http://localhost:{port}")
    print(f"Open: http://localhost:{port}/index.html")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())


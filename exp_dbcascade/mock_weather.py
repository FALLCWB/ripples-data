#!/usr/bin/env python3
"""Local mock of an external weather API. GET /temp?lag=N sleeps N seconds
(a controllable, reproducible stand-in for real network/API latency) and
returns a JSON temperature. The enrichment service calls this as the first
step of its reaction to an ingest, so the reactive cascade lands ~lag seconds
after the action -- the delayed-reaction regime where the labeler's priority
order matters."""
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

PORT = 8099


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        q = parse_qs(urlparse(self.path).query)
        lag = float(q.get("lag", ["0"])[0])
        time.sleep(lag)  # the "external" latency
        # deterministic pseudo-temperature (no randomness: reproducible reps)
        temp = 20.0  # fixed: reproducible across reps
        body = json.dumps({"temp": temp, "lag": lag}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()

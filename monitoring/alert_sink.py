"""Tiny Alertmanager webhook receiver for the local stack: prints each notification and appends it to a file.

Not a production notifier. It exists so that "an alert fired and resolved" leaves a timestamped record that can
be saved as evidence. Run: python monitoring/alert_sink.py (listens on :5001, writes /data/alerts.log).
"""

import json
import os
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

LOG = os.environ.get("ALERT_LOG", "/data/alerts.log")


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        size = min(int(self.headers.get("Content-Length", "0")), 1_000_000)
        payload = json.loads(self.rfile.read(size) or b"{}")
        for alert in payload.get("alerts", []):
            line = {
                "received": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "status": alert.get("status"),
                "alert": alert.get("labels", {}).get("alertname"),
                "severity": alert.get("labels", {}).get("severity"),
                "summary": alert.get("annotations", {}).get("summary"),
                "starts_at": alert.get("startsAt"),
                "ends_at": alert.get("endsAt"),
            }
            print(json.dumps(line), flush=True)
            os.makedirs(os.path.dirname(LOG), exist_ok=True)
            with open(LOG, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(line) + "\n")
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        return


if __name__ == "__main__":
    host = os.environ.get("ALERT_SINK_HOST", "127.0.0.1")
    HTTPServer((host, 5001), Handler).serve_forever()

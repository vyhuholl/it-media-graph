"""A stand-in for the Bot API inside the cluster of e2e.sh: it records every call,
so that nothing the suite checks depends on Telegram, and `GET /calls` says what
the bot asked. No real token ever reaches it."""

import http.server
import json
import threading
import time
from urllib.parse import parse_qs

lock = threading.Lock()
calls = []


def answer(handler, body):
    raw = (body if isinstance(body, str) else json.dumps(body)).encode()
    handler.send_response(200)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(raw)))
    handler.end_headers()
    handler.wfile.write(raw)


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        with lock:
            answer(self, "\n".join(json.dumps(c) for c in calls) + "\n")

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        method = self.path.rsplit("/", 1)[-1]
        if method == "getUpdates":
            time.sleep(1)
            return answer(self, {"ok": True, "result": []})
        fields = {
            k: v[0] for k, v in parse_qs(raw.decode(errors="replace")).items()
        }
        with lock:
            calls.append(
                {
                    "method": method,
                    "fields": fields,
                    "raw": raw.decode(errors="replace"),
                }
            )
        if method == "getMe":
            return answer(
                self,
                {
                    "ok": True,
                    "result": {
                        "id": 42,
                        "is_bot": True,
                        "first_name": "fake",
                        "username": "fake_bot",
                    },
                },
            )
        if method == "sendMessage":
            return answer(
                self,
                {
                    "ok": True,
                    "result": {
                        "message_id": 1,
                        "date": int(time.time()),
                        "chat": {"id": 1, "type": "private"},
                    },
                },
            )
        return answer(self, {"ok": True, "result": True})


http.server.ThreadingHTTPServer(("0.0.0.0", 8081), Handler).serve_forever()

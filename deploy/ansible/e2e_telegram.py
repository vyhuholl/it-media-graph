"""A stand-in for the Bot API inside the cluster of e2e.sh: it records every call,
so that nothing the suite checks depends on Telegram, and `GET /calls` says what
the bot asked. No real token ever reaches it.

What it answers, and what the suite steers through `/control/…`:

* messages are numbered, as Telegram numbers them;
* `getChat` knows every chat, and a chat whose id is below zero is a forum —
  the suite's YouTube chat is one, its Telegram chat is not;
* a topic the suite names through `/control/refuse` is refused as Telegram
  refuses a deleted one, until `/control/reset`;
* updates the suite queues through `/control/enqueue` are handed out by the next
  `getUpdates`, once each.
"""

import http.server
import json
import threading
import time
from urllib.parse import parse_qs

lock = threading.Lock()
calls = []
updates = []
refused = set()
numbers = {"message": 0, "update": 0}

GIFTS = {
    "unlimited_gifts": False,
    "limited_gifts": False,
    "unique_gifts": False,
    "premium_subscription": False,
    "gifts_from_channels": False,
}


def answer(handler, body, status=200):
    raw = (body if isinstance(body, str) else json.dumps(body)).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(raw)))
    handler.end_headers()
    handler.wfile.write(raw)


def control(handler, action, raw):
    with lock:
        if action == "enqueue":
            update = json.loads(raw)
            numbers["update"] += 1
            update["update_id"] = numbers["update"]
            updates.append(update)
        elif action == "refuse":
            refused.add(str(json.loads(raw)["thread"]))
        elif action == "reset":
            calls.clear()
            refused.clear()
    return answer(handler, {"ok": True})


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        with lock:
            answer(self, "\n".join(json.dumps(c) for c in calls) + "\n")

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if self.path.startswith("/control/"):
            return control(self, self.path.rsplit("/", 1)[-1], raw)
        method = self.path.rsplit("/", 1)[-1]
        if method == "getUpdates":
            with lock:
                handed, updates[:] = list(updates), []
            if not handed:
                time.sleep(1)
            return answer(self, {"ok": True, "result": handed})
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
        if method == "getChat":
            chat_id = int(fields.get("chat_id", "0"))
            return answer(
                self,
                {
                    "ok": True,
                    "result": {
                        "id": chat_id,
                        "type": "supergroup" if chat_id < 0 else "private",
                        "is_forum": chat_id < 0,
                        "accent_color_id": 0,
                        "max_reaction_count": 11,
                        "accepted_gift_types": GIFTS,
                    },
                },
            )
        if method == "sendMessage":
            if fields.get("message_thread_id") in refused:
                return answer(
                    self,
                    {
                        "ok": False,
                        "error_code": 400,
                        "description": "Bad Request: message thread not found",
                    },
                    status=400,
                )
            with lock:
                numbers["message"] += 1
                number = numbers["message"]
            return answer(
                self,
                {
                    "ok": True,
                    "result": {
                        "message_id": number,
                        "date": int(time.time()),
                        "chat": {
                            "id": int(fields.get("chat_id", "1")),
                            "type": "private",
                        },
                    },
                },
            )
        return answer(self, {"ok": True, "result": True})


http.server.ThreadingHTTPServer(("0.0.0.0", 8081), Handler).serve_forever()

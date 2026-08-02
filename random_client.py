# random_client.py — a dependency-free, Sophia-free AI client: the neutrality proof.
# Mounts the two AI-facing routes the engine calls (POST /state, GET /move/<turn_id>)
# and answers with a random legal move. No model, no framework — stdlib only. One
# process per seat:
#     python random_client.py 6001
#     python random_client.py 6002
# then point each engine Player(base=...) at http://127.0.0.1:<port>.

import sys
import json
import random
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT   = 6001
_moves = {}                          # turn_id -> chosen token
_lock  = threading.Lock()


def _decide(payload):
    """Random pick from the enumerated legal set. The engine always sends
    legal_moves for this game; shape-mode games (Wording) aren't handled here."""
    legal = payload.get("legal_moves") or []
    return random.choice(legal) if legal else ""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):       # silence default per-request stderr logging
        pass

    def do_POST(self):
        if self.path.strip("/").split("/")[-1] != "state":
            return self._send(404, {})
        n       = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(n) or b"{}")
        turn_id = str(payload.get("turn_id") or "").strip()
        if payload.get("terminal"):                       # game over: no move expected
            print(f"[random:{PORT}] terminal — {payload.get('result')}")
            return self._send(202, {"status": "terminal"})
        move = _decide(payload)
        if turn_id and move:
            with _lock:
                _moves[turn_id] = move                    # ready before we even reply
        print(f"[random:{PORT}] {turn_id} -> {move}")
        self._send(202, {"status": "accepted", "turn_id": turn_id})

    def do_GET(self):                                     # /move/<turn_id>
        parts = self.path.strip("/").split("/")
        if len(parts) < 2 or parts[-2] != "move":
            return self._send(404, {})
        with _lock:
            move = _moves.pop(parts[-1], None)
        if move is None:
            return self._send(204, {})                    # nothing waiting
        self._send(200, {"move": move})

    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 6001
    print(f"[random:{PORT}] listening")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
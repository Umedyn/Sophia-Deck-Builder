# server.py — engine transport + the two-player turn machine, written once.
#
# The engine is a CLIENT toward each AI player (POST its /state, poll its /move)
# and a SERVER toward the browser (/play now, /view next tier). Both feeders —
# the AI poll-return and the human /play POST — funnel into submit_move(), the
# single legality-and-advance chokepoint. The machine never learns "human vs AI":
# a slot with a base URL is pushed to; a slot with base=None submits inbound.

import os
import time
import uuid
import threading
import requests
import random
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__)
HERE = os.path.dirname(os.path.abspath(__file__))

POLL_INTERVAL = 0.4      # seconds between /move polls
POLL_PATIENCE = 90       # ~36s at 0.4s/poll — above Sophia's slowest three-gen round
HTTP_TIMEOUT  = 5        # per outbound request


# ============================ match state ====================================
class Player:
    __slots__ = ("base", "name", "slot", "label")
    def __init__(self, base: str | None = None, name: str = ""):
        self.base  = base          # None = local human; URL = AI client
        self.name  = name          # stable identity for logs (optional)
        self.slot  = -1            # 0/1, assigned at _begin
        self.label = "?"           # color, assigned at _begin (random each game)


class Match:
    def __init__(self, game_factory, game_name: str, players):
        self.game_factory = game_factory
        self.game         = game_factory()     # initial position, shown before Start
        self.game_name    = game_name
        self.players      = players
        self.active       = 0
        self.session_id   = uuid.uuid4().hex[:8]
        self.seq          = 0
        self.current_turn_id = ""
        self.last_move    = None
        self.started      = False              # gates the whole turn machine
        self.gen          = 0                  # bumped each game; kills stale drivers
        self.lock         = threading.Lock()


MATCH = [None]     # the single in-process match (one board per server for MVP)


def _next_turn_id(match) -> str:           # CALL UNDER match.lock
    match.seq += 1
    match.current_turn_id = f"{match.session_id}-{match.seq}"
    return match.current_turn_id


def _build_payload(match, turn_id, terminal=False, note="", perspective=None):
    g = match.game
    payload = {
        "session_id": match.session_id,
        "turn_id":    turn_id,
        "game":       match.game_name,
        "state_nl":   g.terminal_message(perspective) if terminal else g.render_state(),
        "terminal":   terminal,
    }
    legal = g.legal_actions()
    if legal is not None:
        payload["legal_moves"] = legal
    labels = g.move_labels()
    if labels:
        payload["move_labels"] = labels
    extra = g.structured_state()
    if extra:
        payload.update(extra)              # chess: {"fen": ...}
    if terminal:
        payload["result"] = g.result()     # structured outcome alongside the NL line
    if note:
        payload["note"] = note
    return payload


# ============================ outbound (engine → AI) =========================
def _post_state(player, payload):
    try:
        requests.post(f"{player.base}/state", json=payload, timeout=HTTP_TIMEOUT)
    except requests.RequestException:
        pass          # AI not up yet or transient — the drive loop re-fires


def _extract_move(resp) -> str:
    try:
        body = resp.json()
        if isinstance(body, dict):
            return str(body.get("move") or "").strip()
        if isinstance(body, str):
            return body.strip()
    except ValueError:
        pass
    return (resp.text or "").strip()


def _poll_move(player, turn_id) -> str | None:
    url = f"{player.base}/move/{turn_id}"
    for _ in range(POLL_PATIENCE):
        try:
            r = requests.get(url, timeout=HTTP_TIMEOUT)
            if r.status_code == 200:
                move = _extract_move(r)
                if move:
                    return move            # decided
            # 204/404/empty-200 → still thinking
        except requests.RequestException:
            pass                           # AI not up yet — keep waiting
        time.sleep(POLL_INTERVAL)
    return None                            # patience exhausted → caller re-fires


# ============================ the chokepoint =================================
def submit_move(match, token, turn_id, slot) -> str:
    """The one place a token becomes a move. Both feeders call it. Thread-safe;
    holds the lock only for the check+apply (never across network I/O)."""
    with match.lock:
        if match.game.is_terminal():
            return "stale"
        if turn_id != match.current_turn_id or slot != match.active:
            return "stale"                 # late arrival, or not this player's turn
        token = (token or "").strip()
        if not match.game.is_legal(token): # the untrusted-port legality floor
            return "illegal"
        match.game.apply_action(token)
        print(f"[{match.game_name}] {match.players[slot].label}: {token}"
              f"{'  #' if match.game.is_terminal() else ''}")
        match.last_move = token
        nxt = match.game.current_player()                            # game owns the pointer…
        match.active = (match.active ^ 1) if nxt is None else nxt    # …else default: alternate
        return "accepted"


# ============================ the turn machine ===============================
def _begin(match):
    """Start or restart: fresh board, random colors, white to move. Bumps gen so
    any driver thread from the prior game exits at its next lock check."""
    with match.lock:
        match.game            = match.game_factory()   # wipe a finished/mid game
        random.shuffle(match.players)                  # random seating -> random colors
        for i, p in enumerate(match.players):
            p.slot  = i
            p.label = f"Player {i + 1}"
        match.active          = 0                       # white starts
        match.session_id      = uuid.uuid4().hex[:8]    # new session -> new turn_ids
        match.seq             = 0
        match.current_turn_id = ""
        match.last_move       = None
        match.started         = True
        match.gen            += 1                        # invalidate lingering drivers
    _kick(match)

def _fast_forward(match):
    """Apply any forced single-option turns (deckbuilder: only-legal-move-is-END)
    without troubling a client. Game-neutral — the game decides what's forced;
    chess/Wordle return None and this is a no-op. Pure-local, so it holds no network
    I/O. CALL UNDER match.lock."""
    while not match.game.is_terminal():
        tok = match.game.forced_action()
        if not tok:
            return
        match.game.apply_action(tok)
        print(f"[{match.game_name}] (auto) {tok}")
        match.last_move = tok
        nxt = match.game.current_player()
        match.active = (match.active ^ 1) if nxt is None else nxt

def _kick(match):
    """After any accepted move (or at start): drive the now-active AI, or arm a
    turn_id and wait for a human, or broadcast the end."""
    with match.lock:
        _fast_forward(match)               # auto-apply forced turns before dispatching
        gen = match.gen
        if match.game.is_terminal():
            player = None
        else:
            player = match.players[match.active]
            if player.base is None:            # human on turn -> arm turn_id for /play
                _next_turn_id(match)
    if player is None:
        _broadcast_terminal(match)
    elif player.base is not None:              # AI on turn -> drive in its own thread
        threading.Thread(target=_drive_ai, args=(match, player, gen), daemon=True).start()
    # else human: /view shows clickable legal moves; the page POSTs to /play.


def _drive_ai(match, player, gen):
    note = ""
    while True:
        with match.lock:
            if match.gen != gen or match.game.is_terminal() or match.active != player.slot:
                return                          # superseded by a restart, or turn moved on
            turn_id = _next_turn_id(match)
            payload = _build_payload(match, turn_id, note=note)
        _post_state(player, payload)
        token = _poll_move(player, turn_id)
        if token is None:
            note = ""
            continue
        status = submit_move(match, token, turn_id, player.slot)
        if status == "accepted":
            _kick(match)
            return
        if status == "stale":
            return
        note = "That was not a legal move. Choose one from the legal moves listed."


def _broadcast_terminal(match):
    """Game over: push a perspective-shaped terminal line to each AI so its Gen 3
    reacts to winning or losing. No poll — no move is expected back. Humans see
    the outcome via /view's result banner."""
    with match.lock:
        ai_players = [p for p in match.players if p.base]
        outbound = []
        for p in ai_players:
            turn_id = _next_turn_id(match)
            outbound.append(
                (p, _build_payload(match, turn_id, terminal=True, perspective=p.slot))
            )
    for p, payload in outbound:
        _post_state(p, payload)
        print(f"[{match.game_name}] terminal → {p.name} ({p.label})")


# ============================ inbound (browser → engine) =====================
@app.post("/play")
def play():
    """The human's move feeder — the same chokepoint the AI poll-return uses."""
    m = MATCH[0]
    if m is None:
        return jsonify({"status": "no_match"}), 409
    data = request.get_json(force=True, silent=True) or {}
    status = submit_move(m, data.get("move", ""),
                         str(data.get("turn_id", "")),
                         int(data.get("slot", -1)))
    if status == "accepted":
        _kick(m)
    return jsonify({"status": status}), (200 if status == "accepted" else 409)

@app.post("/start")
def start():
    m = MATCH[0]
    if m is None:
        return jsonify({"status": "no_match"}), 409
    if m.started and not m.game.is_terminal():
        return jsonify({"status": "already_running"}), 409   # use Restart mid-game
    _begin(m)
    return jsonify({"status": "started"}), 200


@app.post("/restart")
def restart():
    m = MATCH[0]
    if m is None:
        return jsonify({"status": "no_match"}), 409
    _begin(m)                                   # wipe and begin unconditionally
    return jsonify({"status": "restarted"}), 200


@app.get("/")
def index():
    return send_from_directory(HERE, "index.html")     # single-file page, no build step


@app.get("/view")
def view():
    m = MATCH[0]
    if m is None:
        return jsonify({"status": "no_match"}), 409
    with m.lock:
        g = m.game
        terminal = g.is_terminal()
        data = {
            "game":     m.game_name,
            "started":  m.started,
            "terminal": terminal,
            "result":   g.result(),
        }
        if m.started:
            player = m.players[m.active]
            data.update({
                "active_slot":     m.active,
                "active_label":    player.label,
                "active_is_human": player.base is None,
                "turn_id":         m.current_turn_id,
                "last_move":       m.last_move,
                "legal_moves":     (g.legal_actions() or []) if not terminal else [],
                
            })
        else:
            data.update({
                "active_slot": None, "active_label": None, "active_is_human": False,
                "turn_id": "", "last_move": None, "legal_moves": [],
            })
        data.update(g.render_view())            # fen + check — board always drawable
    return jsonify(data)


# ============================ entry ==========================================
def start_match(game_factory, game_name, players):
    m = Match(game_factory, game_name, players)
    MATCH[0] = m
    return m         # idle until /start fires from the UI


if __name__ == "__main__":
    from deckbuilder import Deckbuilder
    from cards import load_cards
    catalog = load_cards("cards.json")                    # read once; every game reuses it
    def factory():
        return Deckbuilder(catalog)                       # fresh game each Start/Restart

    # Default: random-vs-random headless (the neutrality proof). Point a slot at
    # Sophia's activity port to swap her in, or set base=None for a human seat.
    # p1 = Player(base="http://127.0.0.1:5020/activity", name="sophia")
    p1 = Player(base="http://127.0.0.1:5020/activity", name="sophia")
    p2 = Player(base=None, name="human")          # or a second AI / random client
    start_match(factory, "Deckbuilder", [p1, p2])
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5050)), threaded=True)
# deckbuilder.py — the deckbuilder engine (assembled across pieces).
# Piece 1: the substrate — one player's zones + pools, deck primitives, and the
# keyword resolver. The Game protocol surface and the market are piece 2.

import random
from collections import Counter
from game import Game

from cards import KEYWORDS

STARTING_LIFE = 30
HAND_SIZE = 5


# ---- effect resolver --------------------------------------------------------
# Keyword -> handler; each mutates the active Side. Adding a keyword is two lines:
# one in cards.KEYWORDS, one here.
# plus a `guard` field on Side, subtracted first at the END damage seam (piece 2).

def _gain_energy(side, n): side.energy += n
def _gain_attack(side, n): side.attack += n
def _gain_life(side, n):   side.life += n
def _draw(side, n):        side.draw(n)
def _gain_guard(side, n): side.guard += n

HANDLERS = {
    "GAIN_ENERGY": _gain_energy,
    "GAIN_ATTACK": _gain_attack,
    "GAIN_LIFE":   _gain_life,
    "DRAW":        _draw,
    "GAIN_GUARD": _gain_guard,
}

# Vocab and handler table must never drift apart — caught at import, not mid-game.
assert set(HANDLERS) == set(KEYWORDS), \
    f"handler/vocab mismatch: {set(HANDLERS) ^ set(KEYWORDS)}"


def resolve(side, effects):
    """Apply a card's effects to the active side, in authored order."""
    for kw, n in effects:
        HANDLERS[kw](side, n)


# ---- one player's board -----------------------------------------------------
class Side:
    """Zones + per-turn pools for one player. The resolver mutates a Side and
    never reaches the opponent; cross-side damage is sequenced by the game
    object in END (piece 2)."""

    def __init__(self, starting_deck, rng=None):
        self._rng = rng or random.Random()
        self.deck = list(starting_deck)      # draw pile; end of list = top
        self._rng.shuffle(self.deck)
        self.hand = []
        self.in_play = []                    # played this turn, held until cleanup
        self.discard = []
        self.life = STARTING_LIFE
        self.energy = 0                      # per-turn, reset in cleanup
        self.attack = 0                      # per-turn, read for damage then reset
        self.guard = 0                       # absorbs damage before life; expires at start of your next turn

    # deck primitives
    def draw(self, n):
        """Draw n, reshuffling discard back in when the deck runs dry. Draws
        fewer than n only if both deck and discard are exhausted."""
        for _ in range(n):
            if not self.deck:
                if not self.discard:
                    break
                self.deck, self.discard = self.discard, []
                self._rng.shuffle(self.deck)
            self.hand.append(self.deck.pop())

    def cleanup_zones(self):
        """END step 1: sweep played cards + leftover hand to discard."""
        self.discard.extend(self.in_play)
        self.discard.extend(self.hand)
        self.in_play.clear()
        self.hand.clear()

    def reset_pools(self):
        """END step 3: pools are per-turn and don't carry."""
        self.energy = 0
        self.attack = 0

    # playing cards
    def play_one(self, card):
        """Hand → in-play, resolve effects. Card is value-equal, so remove()
        drops one identical copy. Assumes apply_action verified it's in hand."""
        self.hand.remove(card)
        self.in_play.append(card)
        resolve(self, card.effects)

    def play_all(self):
        """PLAY_ALL: drain the hand, sweeping up cards drawn mid-dump. Always
        terminates — every iteration moves one card hand → in_play, and the
        total (deck+discard+hand) pool only shrinks."""
        while self.hand:
            card = self.hand.pop(0)          # naive left-to-right line
            self.in_play.append(card)
            resolve(self, card.effects)

# ---- set composition (recipes over the catalog) -----------------------------
# {id: copies}. Migrate to data later if you want multiple card sets.
STARTING_DECK = {"spark": 6, "strike": 4}                       # each player; = turn-1 hand
MARKET_STOCK  = {"surge": 8, "blast": 6, "insight": 6, "mend": 6, "battery": 4,"bulwark": 6}
MARKET_WIDTH  = 5                                               # visible row slots

SEP = ":"          # wire-token delimiter — swap to " " for space-delimited tokens

_EFFECT_WORD = {"GAIN_ENERGY": "charge", "GAIN_ATTACK": "attack", "GAIN_LIFE": "life",
                "GAIN_GUARD": "guard"}

class Deckbuilder(Game):
    MAX_TURNS = 200        # backstop: a game where neither side ever deals damage still ends

    def __init__(self, catalog, starting=None, stock=None,
                 width=MARKET_WIDTH, hand_size=HAND_SIZE, seed=None):
        self._catalog   = catalog                              # {id: Card}
        self._rng       = random.Random(seed)
        self._width     = width
        self._hand_size = hand_size

        self.sides = [Side(self._expand(starting or STARTING_DECK), rng=self._rng)
                      for _ in range(2)]

        self._market_deck = self._expand(stock or MARKET_STOCK)
        self._rng.shuffle(self._market_deck)
        self._row = []
        self._refill_row()

        for s in self.sides:                                   # deal opening hands
            s.draw(self._hand_size)

        self.active      = 0                                   # turn pointer; flips only on END
        self._turns      = 0
        self._winner     = None                                # slot index once someone wins
        self._last_actor = None                                # who acted last (for salience)
        self._last_event = ""
        self._acted        = False        # has the active player acted yet this turn
        self._strike_recap = [None, None] # per-slot dmg dealt on last END, recapped next turn
        self.guard = 0       # absorbs damage before life; expires at start of your next turn

    # ---- setup helpers ------------------------------------------------------
    def _expand(self, recipe):
        out = []
        for cid, n in recipe.items():
            out.extend(self._catalog[cid] for _ in range(int(n)))   # KeyError = bad recipe
        return out

    def _refill_row(self):
        """Top the row up to width. Dry deck -> row just stays short; BUY is only
        offered for cards actually present, so an empty slot is never illegal."""
        while len(self._row) < self._width and self._market_deck:
            self._row.append(self._market_deck.pop())

    # ---- protocol surface: legality ----------------------------------------
    def legal_actions(self):
        if self.is_terminal():
            return []
        me = self.sides[self.active]
        moves = [f"PLAY{SEP}{cid}" for cid in sorted({c.id for c in me.hand})]
        if me.hand:
            moves.append("PLAY_ALL")
        # affordability filtered HERE — an unaffordable card never reaches the legal set
        moves += [f"BUY{SEP}{cid}"
                  for cid in sorted({c.id for c in self._row if c.cost <= me.energy})]
        moves.append("END")
        return moves

    def is_legal(self, token):
        return token in set(self.legal_actions())              # enumerable set = the authority

    def current_player(self):
        return self.active                                     # engine reflects this via the swap edit

    def forced_action(self):
        if self.is_terminal():
            return None
        return "END" if self.legal_actions() == ["END"] else None

    # ---- protocol surface: apply -------------------------------------------
    def apply_action(self, token):
        me = self.sides[self.active]
        if token == "END":
            self._end_turn()
            return
        if token == "END":
            self._end_turn()
            return
        self._acted = True                # anything but END means she's acted this turn
        self._last_actor = self.active
        if token == "PLAY_ALL":
            me.play_all()
            self._last_event = "played the whole hand"
            return
        verb, _, arg = token.partition(SEP)
        if verb == "PLAY":
            card = next(c for c in me.hand if c.id == arg)     # is_legal guaranteed presence
            me.play_one(card)
            self._last_event = f"played {card.name}"
        elif verb == "BUY":
            card = self._buy(me, arg)
            self._last_event = f"bought {card.name}"

    def _buy(self, me, cid):
        idx  = next(i for i, c in enumerate(self._row) if c.id == cid)
        card = self._row.pop(idx)
        me.energy -= card.cost
        me.discard.append(card)                                # bought cards enter discard
        self._refill_row()
        return card

    def _end_turn(self):
        """The sacred sequence: damage (cross-side) -> cleanup -> draw -> reset -> flip."""
        self._turns += 1
        actor = self.active
        me, opp = self.sides[actor], self.sides[actor ^ 1]

        # attack resolves through the opponent's guard; the remainder hits life
        dmg      = me.attack
        absorbed = min(opp.guard, dmg)
        opp.guard -= absorbed
        through   = dmg - absorbed
        opp.life -= through
        self._strike_recap[actor] = (through, absorbed) if dmg else None
        self._last_actor = actor

        if opp.life <= 0:
            self._winner = actor
            self._last_event = f"struck for {through} and won"
            return

        me.cleanup_zones()
        me.draw(self._hand_size)
        me.reset_pools()
        self.active = actor ^ 1
        self.sides[self.active].guard = 0     # guard expires at the start of your next turn
        self._acted = False
        if not dmg:
            self._last_event = "ended the turn"
        elif absorbed:
            self._last_event = (f"struck for {through} (your guard absorbed {absorbed}) "
                                "and ended the turn")
        else:
            self._last_event = f"struck for {through} and ended the turn"

    # ---- protocol surface: terminal ----------------------------------------
    def is_terminal(self):
        return self._winner is not None or self._turns >= self.MAX_TURNS

    def result(self):
        if not self.is_terminal():
            return {"over": False}
        if self._winner is not None:
            return {"over": True, "winner": self._label(self._winner),
                    "winner_slot": self._winner, "reason": "life"}
        a, b = self.sides[0].life, self.sides[1].life          # turn cap: decide by life
        if a == b:
            return {"over": True, "winner": None, "winner_slot": None, "reason": "turn_limit"}
        w = 0 if a > b else 1
        return {"over": True, "winner": self._label(w), "winner_slot": w,
                "reason": "turn_limit"}

    # ---- rendering helpers --------------------------------------------------
    def _label(self, idx):
        return f"Player {idx + 1}"

    @staticmethod
    def _names(cards):
        return ", ".join(c.name for c in cards)

    @staticmethod
    def _names_counted(cards):
        c = Counter(x.name for x in cards)
        return ", ".join(f"{n}× {name}" for name, n in sorted(c.items()))

    def _hand_text(self, cards):
        seen = {}
        for c in cards:
            if c.id not in seen:
                seen[c.id] = [c, 0]
            seen[c.id][1] += 1
        parts = []
        for cid in sorted(seen):
            card, n = seen[cid]
            qty = f"{n}× " if n > 1 else ""
            parts.append(f"{qty}{card.name} ({self._effect_text(card)})")
        return ", ".join(parts)

    def _prompt_line(self, me):
        if me.hand:
            return ("Play a card to bank its energy and attack — or play your whole hand at "
                    "once — then buy from the market or end your turn.")
        if any(c.cost <= me.energy for c in self._row):
            return "Buy a card you can afford, or end your turn to strike with your banked attack."
        return "End your turn to strike with your banked attack."

    @staticmethod
    def _effect_text(card):
        parts = [f"draw {n}" if kw == "DRAW" else f"+{n} {_EFFECT_WORD[kw]}"
                 for kw, n in card.effects]
        return ", ".join(parts)

    def _life_str(self, s):
        return f"{s.life}" + (f" (guard {s.guard})" if s.guard else "")

    # ---- AI-facing state (active player's POV; never leaks hidden truth) -----
    def render_state(self):
        me, opp = self.sides[self.active], self.sides[self.active ^ 1]
        head = ("Deckbuilder — a new turn begins for you." if not self._acted
                else "Deckbuilder — still your turn.")
        L = [head, f"Life — you: {self._life_str(me)}, opponent: {self._life_str(opp)}."]

        # at the top of a fresh turn, recap what YOUR strike did — otherwise it's invisible,
        # buried under the opponent's whole turn before you see the board again
        recap = self._strike_recap[self.active]
        if not self._acted and recap:
            through, absorbed = recap
            if absorbed:
                L.append(f"As you ended your last turn, your attack dealt {through} "
                         f"(the opponent's guard absorbed {absorbed}).")
            else:
                L.append(f"As you ended your last turn, your attack hit the opponent for {through}.")

        # freshest event: the opponent's move on a new turn, or your own move mid-turn
        if self._last_actor is not None:
            who = "You" if self._last_actor == self.active else "Your opponent"
            L.append(f"{who} {self._last_event}.")

        # hand WITH effects — playing is how energy and attack get banked
        if me.hand:
            L.append("Your hand — play these to bank energy and attack: "
                     + self._hand_text(me.hand) + ".")
        else:
            L.append("Your hand is empty; you've played everything this turn.")

        if me.in_play:
            L.append("Played so far this turn: " + self._hand_text(me.in_play) + ".")

        # banked pools — state plainly that they don't persist; she keeps assuming resources
        # carry ("letting those attacks ride into next turn"), and they don't
        L.append(f"Banked this turn: {me.energy} charge and {me.attack} attack. "
                 f"Neither carries over: spend charge on buys now or lose it at end of turn, "
                 f"and your attack strikes the opponent the moment you end this turn.")

        if me.guard:
            L.append(f"You have {me.guard} guard, which absorbs that much of the opponent's next "
                     f"attack, then expires at the start of your next turn.")

        # market — costs plain, affordability as a soft note, not a lockout
        L.append("Market — spend charge to buy cards; a bought card joins your deck permanently "
                 "and works for you every turn you draw it:")
        if self._row:
            for c in self._row:
                tag = "" if c.cost <= me.energy else "  (need more energy)"
                L.append(f"  {c.name}: costs {c.cost}, gives {self._effect_text(c)}{tag}")
        else:
            L.append("  (empty)")

        # piles — de-alarmed; your own draw-pile contents still shown as you asked
        deck_txt = (f"{len(me.deck)} cards, order hidden: {self._names_counted(me.deck)}"
                    if me.deck else "empty right now")
        L.append(f"Your draw pile ({deck_txt}). Your discard: {len(me.discard)}.")
        L.append(f"Opponent — draw pile {len(opp.deck)}, discard {len(opp.discard)}.")

        L.append(self._prompt_line(me))     # nearest the decision, adapts to hand/energy
        return "\n".join(L)

    # ---- friendly gloss: wire token -> label she reads and emits -------------
    def move_labels(self):
        out = {}
        for tok in (self.legal_actions() or []):
            if tok == "END":
                out[tok] = "End turn"
            elif tok == "PLAY_ALL":
                out[tok] = "Play entire hand"
            else:
                verb, _, cid = tok.partition(SEP)
                out[tok] = f"{'Play' if verb == 'PLAY' else 'Buy'} {self._catalog[cid].name}"
        return out

    # ---- terminal line for the broadcast; perspective is a SLOT index --------
    def terminal_message(self, perspective=None):
        r = self.result()
        if not r.get("over"):
            return self.render_state()
        if perspective not in (0, 1):
            return (f"The game ended in a draw." if r["winner"] is None
                    else f"The game is over — {r['winner']} won.")
        me, opp = self.sides[perspective], self.sides[perspective ^ 1]
        w = r["winner_slot"]
        head = "The game ended in a draw." if w is None else \
               ("You won!" if w == perspective else "You lost.")
        return f"{head} Final life — you: {me.life}, opponent: {opp.life}."

    # ---- browser-facing structured state for GET /view ----------------------
    def _card_view(self, card, buyer=None):
        v = {"id": card.id, "name": card.name, "cost": card.cost,
             "type": card.type, "effect": self._effect_text(card)}
        if buyer is not None:
            v["affordable"] = card.cost <= buyer.energy
        return v

    def render_view(self):
        a = self.active
        return {
            "active": a,
            "players": [{"life": s.life, "energy": s.energy, "attack": s.attack,
                         "guard": s.guard,
                         "deck": len(s.deck), "discard": len(s.discard),
                         "in_play": len(s.in_play), "hand_count": len(s.hand)}
                        for s in self.sides],
            "hand":    [self._card_view(c) for c in self.sides[a].hand],
            "in_play": [self._card_view(c) for c in self.sides[a].in_play],
            "market":  [self._card_view(c, buyer=self.sides[a]) for c in self._row],
            "last_event": ("" if self._last_actor is None
                           else f"{self._label(self._last_actor)} {self._last_event}."),
        }
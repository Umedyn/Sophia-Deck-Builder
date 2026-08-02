# cards.py — the card data contract and the closed keyword vocabulary.
# Cards are pure data. No game logic, no knowledge of turns/players/board.

import json
from dataclasses import dataclass

# The closed vocabulary. Extending the game = add a keyword here AND a handler
# in deckbuilder.py's HANDLERS (they're asserted equal at import).
KEYWORDS = frozenset({
    "GAIN_ENERGY",   # +n energy pool (spend on buys this turn)
    "GAIN_ATTACK",   # +n attack pool (auto-resolved vs opponent in END)
    "GAIN_LIFE",     # +n own life
    "DRAW",          # draw n (reshuffles discard when the deck runs dry)
    "GAIN_GUARD",    # stacking blocker; absorbs damage, expires at start of your next turn
})


@dataclass(frozen=True)
class Card:
    id: str            # stable key, also the wire token for PLAY/BUY
    name: str          # display label
    cost: int          # energy price to buy; 0/ignored for the starters
    type: str          # authoring/display label ONLY — resolver never reads it
    effects: tuple     # tuple of (KEYWORD, n) — the mechanical truth


def _validate(raw: dict) -> Card:
    for field in ("id", "name", "cost", "type", "effects"):
        if field not in raw:
            raise ValueError(f"card missing '{field}': {raw!r}")
    effects = tuple((kw, int(n)) for kw, n in raw["effects"])
    for kw, _ in effects:
        if kw not in KEYWORDS:                      # the wall: closed vocab at load
            raise ValueError(f"card '{raw['id']}' uses unknown keyword {kw!r}")
    return Card(str(raw["id"]), str(raw["name"]), int(raw["cost"]),
                str(raw["type"]), effects)


def load_cards(path: str) -> dict:
    """Load + validate a card set → {id: Card}. Raises loudly on bad data;
    card data is authored, not user input, so failing at load is correct."""
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    cards = {}
    for entry in raw:
        card = _validate(entry)
        if card.id in cards:
            raise ValueError(f"duplicate card id {card.id!r}")
        cards[card.id] = card
    return cards
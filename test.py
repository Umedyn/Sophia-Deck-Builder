from cards import load_cards
from deckbuilder import Deckbuilder

g = Deckbuilder(load_cards("cards.json"), seed=1)
print(g.render_state())            # P1 opening hand + market row
print(g.legal_actions())           # PLAY:* , PLAY_ALL, END  (no BUY yet — 0 energy)
g.apply_action("PLAY_ALL")         # dump hand -> banks energy + attack
print(g.legal_actions())           # affordable BUY:* now appear alongside END
g.apply_action("END")              # attack resolves at opponent, turn passes
print("P2 life:", g.sides[1].life, "| active:", g.active, "| terminal:", g.is_terminal())
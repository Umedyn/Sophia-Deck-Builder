from abc import ABC, abstractmethod


class Game(ABC):
    """Engine-side contract for one turn-based game. The wire carries
    render_state() out to the AI and a single move token back in; the engine
    is the sole legality authority via is_legal(), re-checked on every token."""

    @abstractmethod
    def render_state(self) -> str:
        """AI-facing natural-language state: one voice, salience-ordered, the
        freshest event nearest the decision. Must never leak hidden truth."""

    @abstractmethod
    def is_legal(self, token: str) -> bool:
        """The legality floor. Called on every inbound token before apply.
        Illegal or malformed -> engine resends the same state with a note."""

    @abstractmethod
    def apply_action(self, token: str) -> None:
        """Advance state by one legal token. Assumes is_legal(token) passed."""

    @abstractmethod
    def is_terminal(self) -> bool:
        """True once the game is over and no further tokens are accepted."""

    @abstractmethod
    def result(self) -> dict:
        """Terminal outcome. Meaningful only when is_terminal() is True."""

    def legal_actions(self):
        """Enumerate the current player's legal tokens, or None when the set
        isn't enumerable (e.g. Wordle's dictionary). Chess returns the real
        list — it feeds both the AI's set and the clickable human UI."""
        return None

    def current_player(self):
        """Which player (0/1) acts next. None = the engine alternates every accepted
        move (one token per turn: chess, Wordle). Multi-action-per-turn games — a
        deckbuilder plays/buys repeatedly and passes on END — override to report their
        own turn pointer, so the engine swaps only when the game says the turn passed."""
        return None

    def forced_action(self):
        """A token to auto-apply without asking the active client, or None. Lets a
        game fast-forward a choiceless turn (deckbuilder: the only legal move is END).
        Default None = never auto-act; every turn goes to the client. The engine
        applies it through the same swap logic, so the turn passes normally."""
        return None

    def move_labels(self):
        """Optional {wire_token: display_label} for the current legal set, letting
        the AI read and choose moves in a friendlier register than the raw token
        (chess: UCI -> SAN). Display only — the wire token is unchanged. Labels
        must be unique within the set (they're reverse-mapped). None = no gloss."""
        return None

    def structured_state(self) -> dict | None:
        """Optional game-specific structured extras merged into the wire payload
        (e.g. chess contributes {"fen": ...} for a future tool/Stockfish client).
        Kept off render_state so the NL string stays clean. None = nothing to add."""
        return None

    def render_view(self) -> dict:
        """Browser-facing structured state for GET /view — the second shaped
        output alongside render_state()'s NL. Game-specific presentation extras
        (chess: fen + check). The engine wraps turn/legal/terminal fields around
        this. Default {} = engine-level fields only."""
        return {}

    def terminal_message(self, perspective: str | None = None) -> str:
        """AI-facing end-of-game line, sent in place of the move prompt on the
        terminal broadcast. Default falls back to render_state(); games with a
        real outcome override to say who won, and — given the recipient's
        perspective — whether that recipient won or lost."""
        return self.render_state()
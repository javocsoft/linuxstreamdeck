"""Stable identities and user-facing descriptions of built-in games."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GameInfo:
    id: str
    name: str
    hint: str


GAMES = (
    GameInfo(
        "mole_smash",
        "Mole Smash",
        "Press START, then hit each mole before it disappears.",
    ),
    GameInfo(
        "circuit_breaker",
        "Circuit Breaker",
        "Turn every light off in as few moves as possible.",
    ),
    GameInfo(
        "pulse_memory",
        "Pulse Memory",
        "Watch the illuminated sequence, then repeat it exactly.",
    ),
    GameInfo(
        "memory_match",
        "Memory Match",
        "Reveal two cards at a time and find every matching pair.",
    ),
    GameInfo(
        "minesweeper",
        "Minesweeper",
        "Reveal every safe cell; switch to Flag mode to mark suspected mines.",
    ),
    GameInfo(
        "tic_tac_toe",
        "Tic-Tac-Toe",
        "Make a line of three X marks before the computer does.",
    ),
    GameInfo(
        "mastermind",
        "Colour Mastermind",
        "Cycle the coloured pegs and crack the hidden code from its clues.",
    ),
    GameInfo(
        "neon_relay",
        "Neon Relay",
        "Rotate the neon route ahead of the spark and keep it alive.",
    ),
)

GAME_BY_ID = {game.id: game for game in GAMES}
DEFAULT_GAME_ID = GAMES[0].id


def game_info(game_id: str) -> GameInfo | None:
    return GAME_BY_ID.get(str(game_id))

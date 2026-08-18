"""Pure endless neon circuit-routing game engine."""

from __future__ import annotations

import random
from dataclasses import dataclass

from ..core.config import DEFAULT_GAME_DIFFICULTY, GAME_DIFFICULTIES
from .common import (
    PHASE_LOBBY,
    PHASE_PLAYING,
    PHASE_RESULTS,
    EngineEvent,
    GameLayout,
)

GAME_ID = "neon_relay"
GAME_NAME = "Neon Relay"

NORTH = 0
EAST = 1
SOUTH = 2
WEST = 3
DIRECTIONS = (NORTH, EAST, SOUTH, WEST)
DIRECTION_VECTORS = {
    NORTH: (-1, 0),
    EAST: (0, 1),
    SOUTH: (1, 0),
    WEST: (0, -1),
}

PHASE_SECTOR_CLEAR = "sector_clear"
PHASE_UPGRADE = "upgrade"
PHASE_RECOVER = "recover"

SECTOR_PAUSE_SECONDS = 0.85
RECOVER_SECONDS = 0.95
ROTATE_FLASH_SECONDS = 0.18
OVERDRIVE_SECONDS = 6.0
STASIS_SECONDS = 3.5
UPGRADE_EVERY = 3


@dataclass(frozen=True)
class RelayDifficulty:
    step_seconds: float
    minimum_seconds: float
    acceleration: float
    safe_tiles: int
    shields: int
    route_light: float


DIFFICULTIES = {
    "easy": RelayDifficulty(1.08, 0.58, 0.035, 3, 2, 1.0),
    "normal": RelayDifficulty(0.84, 0.42, 0.040, 2, 1, 0.78),
    "hard": RelayDifficulty(0.66, 0.32, 0.044, 1, 0, 0.58),
}


@dataclass
class _RelayTile:
    kind: str
    rotation: int
    required_rotation: int
    on_route: bool
    crystal: bool = False
    collected: bool = False


@dataclass(frozen=True)
class RelayTileView:
    index: int
    kind: str
    rotation: int
    required_rotation: int
    on_route: bool
    crystal: bool
    collected: bool


@dataclass(frozen=True)
class NeonRelaySnapshot:
    phase: str
    layout: GameLayout
    difficulty: str
    sound_enabled: bool
    tiles: tuple[RelayTileView, ...]
    route: tuple[int, ...]
    entry_key: int
    entry_side: int
    exit_key: int
    exit_side: int
    spark_key: int | None
    spark_incoming: int | None
    spark_outgoing: int | None
    spark_progress: float
    flashed_keys: tuple[int, ...]
    crashed_key: int | None
    score: int
    high_score: int
    new_high_score: bool
    sector: int
    combo: int
    shields: int
    crystals: int
    crystal_total: int
    overdrive: bool
    overdrive_level: int
    stasis_active: bool
    speed_seconds: float
    perfect_sector: bool
    upgrade_keys: tuple[int, ...]
    upgrade_choices: tuple[str, ...]
    effect_progress: float
    progress: float


def opposite(direction: int) -> int:
    return (int(direction) + 2) % 4


def tile_connections(kind: str, rotation: int) -> tuple[int, int]:
    """Return the two connector sides for one oriented circuit tile."""
    if kind == "straight":
        return (NORTH, SOUTH) if int(rotation) % 2 == 0 else (EAST, WEST)
    turn = int(rotation) % 4
    return ((NORTH + turn) % 4, (EAST + turn) % 4)


def direction_between(first: int, second: int, columns: int) -> int:
    """Direction travelled from one orthogonally adjacent key to another."""
    first_row, first_column = divmod(int(first), int(columns))
    second_row, second_column = divmod(int(second), int(columns))
    delta = (second_row - first_row, second_column - first_column)
    for direction, vector in DIRECTION_VECTORS.items():
        if delta == vector:
            return direction
    raise ValueError("Relay path cells must be orthogonally adjacent")


def _shape_for(sides: tuple[int, int]) -> tuple[str, int]:
    left, right = sides
    if opposite(left) == right:
        return "straight", 0 if {left, right} == {NORTH, SOUTH} else 1
    target = {left, right}
    for rotation in range(4):
        if set(tile_connections("corner", rotation)) == target:
            return "corner", rotation
    raise ValueError("A relay tile needs two distinct connector sides")


class NeonRelayEngine:
    """Keep a travelling spark alive by rotating its neon route ahead of it."""

    def __init__(
        self,
        layout: GameLayout,
        difficulty: str = DEFAULT_GAME_DIFFICULTY,
        sound_enabled: bool = True,
        high_score: int = 0,
        *,
        rng: random.Random | None = None,
        now: float = 0.0,
    ) -> None:
        self.layout = layout
        self.difficulty = (
            difficulty if difficulty in DIFFICULTIES else DEFAULT_GAME_DIFFICULTY
        )
        self.sound_enabled = bool(sound_enabled)
        self.high_score = max(0, int(high_score))
        self.phase = PHASE_LOBBY
        self.score = 0
        self.sector = 1
        self.combo = 0
        self.shields = DIFFICULTIES[self.difficulty].shields
        self.new_high_score = False
        self.perfect_sector = False
        self._rng = rng or random.Random()
        self._tiles: list[_RelayTile] = []
        self._route: tuple[int, ...] = ()
        self._route_position: dict[int, int] = {}
        self._entry_key = 0
        self._entry_side = WEST
        self._exit_key = max(0, layout.key_count - 1)
        self._exit_side = EAST
        self._spark_key: int | None = None
        self._spark_incoming: int | None = None
        self._step_started = float(now)
        self._next_step = float(now)
        self._visited_states: set[tuple[int, int]] = set()
        self._flashed_keys: set[int] = set()
        self._flash_until = 0.0
        self._crashed_key: int | None = None
        self._effect_started = float(now)
        self._phase_deadline = float(now)
        self._rotation_count: dict[int, int] = {}
        self._optimal_rotations = 0
        self._crystals = 0
        self._crystal_total = 0
        self._overdrive_level = 0
        self._overdrive_until = 0.0
        self._stasis_until = 0.0
        self._stasis_bonus = 0.0
        self._upgrade_keys: tuple[int, ...] = ()
        self._upgrade_choices: tuple[str, ...] = ()

    @property
    def score_key(self) -> str:
        return f"{self.layout.score_id}:{self.difficulty}"

    def set_high_score(self, value: int) -> None:
        self.high_score = max(0, int(value))

    def press(self, index: int, now: float) -> tuple[EngineEvent, ...]:
        index = int(index)
        now = float(now)
        if self.phase in (PHASE_LOBBY, PHASE_RESULTS):
            return self._press_controls(index, now)
        if self.phase == PHASE_UPGRADE:
            return self._choose_upgrade(index, now)
        if self.phase != PHASE_PLAYING or not 0 <= index < len(self._tiles):
            return ()
        self._rotate(index, 1, now)
        return (EngineEvent(cue="rotate"),)

    def dial(
        self,
        index: int,
        direction: str,
        ticks: int,
        now: float,
    ) -> tuple[EngineEvent, ...]:
        """Plus shortcut: rotate a column, or spend charge on stasis."""
        if self.phase != PHASE_PLAYING or not self.layout.touchscreen_hud:
            return ()
        now = float(now)
        if direction == "press":
            if self._overdrive_level < 40:
                return ()
            self._overdrive_level -= 40
            self._stasis_until = max(self._stasis_until, now + STASIS_SECONDS)
            # Slow the current hop as well as the following ones. Without this
            # extension, pressing near a deadline appears to do nothing until
            # after the spark has already moved.
            self._next_step += 1.0
            return (EngineEvent(cue="stasis"),)
        delta = 1 if direction == "right" else -1 if direction == "left" else 0
        if delta == 0:
            return ()
        turns = max(1, min(8, abs(int(ticks))))
        column = int(index) % self.layout.columns
        changed = False
        for _turn in range(turns):
            for tile_index in range(column, len(self._tiles), self.layout.columns):
                if tile_index == self._spark_key:
                    continue
                self._rotate(tile_index, delta, now)
                changed = True
        return (EngineEvent(cue="rotate"),) if changed else ()

    def tick(self, now: float) -> tuple[EngineEvent, ...]:
        now = float(now)
        if now >= self._flash_until:
            self._flashed_keys.clear()
        if self._overdrive_until and now >= self._overdrive_until:
            self._overdrive_until = 0.0

        if self.phase == PHASE_SECTOR_CLEAR and now >= self._phase_deadline:
            if self.sector % UPGRADE_EVERY == 0:
                self._prepare_upgrades()
                self.phase = PHASE_UPGRADE
                self._effect_started = now
                return ()
            self.sector += 1
            self._build_sector(now)
            return (EngineEvent(cue="go"),)
        if self.phase == PHASE_RECOVER and now >= self._phase_deadline:
            self._build_sector(now)
            return (EngineEvent(cue="go"),)
        if self.phase != PHASE_PLAYING:
            return ()

        events: list[EngineEvent] = []
        transitions = 0
        while self.phase == PHASE_PLAYING and now >= self._next_step:
            transitions += 1
            if transitions > self.layout.key_count * 4:
                break
            transition_at = self._next_step
            events.extend(self._advance(transition_at))
        return tuple(events)

    def snapshot(self, now: float) -> NeonRelaySnapshot:
        now = float(now)
        outgoing = self._spark_outgoing()
        interval = max(0.001, self._next_step - self._step_started)
        spark_progress = (
            max(0.0, min(1.0, (now - self._step_started) / interval))
            if self.phase == PHASE_PLAYING and self._spark_key is not None
            else 0.0
        )
        route_position = (
            self._route_position.get(self._spark_key, 0)
            if self._spark_key is not None
            else 0
        )
        effect_duration = (
            RECOVER_SECONDS if self.phase == PHASE_RECOVER else SECTOR_PAUSE_SECONDS
        )
        return NeonRelaySnapshot(
            phase=self.phase,
            layout=self.layout,
            difficulty=self.difficulty,
            sound_enabled=self.sound_enabled,
            tiles=tuple(
                RelayTileView(
                    index=index,
                    kind=tile.kind,
                    rotation=tile.rotation,
                    required_rotation=tile.required_rotation,
                    on_route=tile.on_route,
                    crystal=tile.crystal,
                    collected=tile.collected,
                )
                for index, tile in enumerate(self._tiles)
            ),
            route=self._route,
            entry_key=self._entry_key,
            entry_side=self._entry_side,
            exit_key=self._exit_key,
            exit_side=self._exit_side,
            spark_key=self._spark_key,
            spark_incoming=self._spark_incoming,
            spark_outgoing=outgoing,
            spark_progress=spark_progress,
            flashed_keys=tuple(sorted(self._flashed_keys)),
            crashed_key=self._crashed_key,
            score=self.score,
            high_score=self.high_score,
            new_high_score=self.new_high_score,
            sector=self.sector,
            combo=self.combo,
            shields=self.shields,
            crystals=self._crystals,
            crystal_total=self._crystal_total,
            overdrive=now < self._overdrive_until,
            overdrive_level=self._overdrive_level,
            stasis_active=now < self._stasis_until,
            speed_seconds=self._step_interval(now),
            perfect_sector=self.perfect_sector,
            upgrade_keys=self._upgrade_keys,
            upgrade_choices=self._upgrade_choices,
            effect_progress=max(
                0.0,
                min(1.0, (now - self._effect_started) / max(0.001, effect_duration)),
            ),
            progress=route_position / max(1, len(self._route) - 1),
        )

    def _press_controls(self, index: int, now: float) -> tuple[EngineEvent, ...]:
        if index == self.layout.exit_key:
            return (EngineEvent(exit_requested=True),)
        if index == self.layout.difficulty_key:
            current = GAME_DIFFICULTIES.index(self.difficulty)
            self.difficulty = GAME_DIFFICULTIES[
                (current + 1) % len(GAME_DIFFICULTIES)
            ]
            self.new_high_score = False
            return (EngineEvent(cue="select", settings_changed=True),)
        if index == self.layout.sound_key:
            self.sound_enabled = not self.sound_enabled
            return (EngineEvent(cue="select", settings_changed=True),)
        if index == self.layout.start_key:
            self._begin(now)
            return (EngineEvent(cue="go"),)
        return ()

    def _begin(self, now: float) -> None:
        self.phase = PHASE_PLAYING
        self.score = 0
        self.sector = 1
        self.combo = 0
        self.shields = DIFFICULTIES[self.difficulty].shields
        self.new_high_score = False
        self.perfect_sector = False
        self._overdrive_level = 0
        self._overdrive_until = 0.0
        self._stasis_until = 0.0
        self._stasis_bonus = 0.0
        self._upgrade_keys = ()
        self._upgrade_choices = ()
        self._build_sector(float(now))

    def _build_sector(self, now: float) -> None:
        route, entry_side, exit_side = self._generate_route()
        self._route = route
        self._route_position = {index: position for position, index in enumerate(route)}
        self._entry_key = route[0]
        self._entry_side = entry_side
        self._exit_key = route[-1]
        self._exit_side = exit_side
        self._tiles = [self._random_decoy() for _index in range(self.layout.key_count)]
        crystal_positions = self._crystal_positions(route)
        safe_tiles = DIFFICULTIES[self.difficulty].safe_tiles
        self._optimal_rotations = 0
        challenge_tiles: list[int] = []
        for position, index in enumerate(route):
            incoming = (
                entry_side
                if position == 0
                else direction_between(index, route[position - 1], self.layout.columns)
            )
            outgoing = (
                exit_side
                if position == len(route) - 1
                else direction_between(index, route[position + 1], self.layout.columns)
            )
            kind, required = _shape_for((incoming, outgoing))
            states = 2 if kind == "straight" else 4
            if position < safe_tiles:
                rotation = required
            else:
                offsets = {
                    "easy": (0, 0, 1),
                    "normal": (0, 1, 1, 2),
                    "hard": (1, 1, 2, 3),
                }[self.difficulty]
                rotation = (required + self._rng.choice(offsets)) % states
                challenge_tiles.append(index)
            self._tiles[index] = _RelayTile(
                kind=kind,
                rotation=rotation,
                required_rotation=required,
                on_route=True,
                crystal=position in crystal_positions,
            )
            self._optimal_rotations += (required - rotation) % states
        if challenge_tiles and self._optimal_rotations == 0:
            index = challenge_tiles[0]
            tile = self._tiles[index]
            states = 2 if tile.kind == "straight" else 4
            tile.rotation = (tile.required_rotation + 1) % states
            self._optimal_rotations = 1

        self._spark_key = self._entry_key
        self._spark_incoming = self._entry_side
        self._visited_states = {(self._entry_key, self._entry_side)}
        self._rotation_count.clear()
        self._crystals = 0
        self._crystal_total = len(crystal_positions)
        self._crashed_key = None
        self.perfect_sector = False
        self._flashed_keys.clear()
        self._step_started = now
        self._next_step = now + self._step_interval(now)
        self._effect_started = now
        self.phase = PHASE_PLAYING

    def _advance(self, now: float) -> tuple[EngineEvent, ...]:
        if self._spark_key is None or self._spark_incoming is None:
            return self._crash(now)
        outgoing = self._spark_outgoing()
        if outgoing is None:
            return self._crash(now)
        next_key = self._neighbour(self._spark_key, outgoing)
        if next_key is None:
            if self._spark_key == self._exit_key and outgoing == self._exit_side:
                return self._complete_sector(now)
            return self._crash(now)

        incoming = opposite(outgoing)
        state = (next_key, incoming)
        if state in self._visited_states:
            return self._crash(now)
        self._visited_states.add(state)
        self._spark_key = next_key
        self._spark_incoming = incoming
        self._step_started = now
        self._next_step = now + self._step_interval(now)
        tile = self._tiles[next_key]
        if tile.crystal and not tile.collected:
            tile.collected = True
            self._crystals += 1
            multiplier = 2 if now < self._overdrive_until else 1
            self.score += 30 * multiplier
            activated = self._charge_overdrive(16, now)
            events = [EngineEvent(cue="crystal")]
            if activated:
                events.append(EngineEvent(cue="overdrive"))
            return tuple(events)
        return ()

    def _complete_sector(self, now: float) -> tuple[EngineEvent, ...]:
        rotations = sum(self._rotation_count.values())
        self.perfect_sector = rotations <= self._optimal_rotations
        self.combo += 1
        combo_multiplier = min(5, 1 + self.combo // 3)
        overdrive_multiplier = 2 if now < self._overdrive_until else 1
        reward = (110 + self.sector * 28 + self._crystals * 35) * combo_multiplier
        if self.perfect_sector:
            reward += 85 * combo_multiplier
        self.score += reward * overdrive_multiplier
        charge = 18 + self._crystals * 8 + (24 if self.perfect_sector else 0)
        activated = self._charge_overdrive(charge, now)
        self._spark_key = None
        self._spark_incoming = None
        self.phase = PHASE_SECTOR_CLEAR
        self._effect_started = now
        self._phase_deadline = now + SECTOR_PAUSE_SECONDS
        events = [EngineEvent(cue="gate")]
        if activated:
            events.append(EngineEvent(cue="overdrive"))
        return tuple(events)

    def _crash(self, now: float) -> tuple[EngineEvent, ...]:
        self._crashed_key = self._spark_key
        self._effect_started = now
        self.combo = 0
        if self.shields > 0:
            self.shields -= 1
            self.phase = PHASE_RECOVER
            self._phase_deadline = now + RECOVER_SECONDS
            return (EngineEvent(cue="crash"), EngineEvent(cue="shield"))
        self.phase = PHASE_RESULTS
        changed = self.score > self.high_score
        if changed:
            self.high_score = self.score
            self.new_high_score = True
        events = [EngineEvent(cue="crash")]
        if changed:
            events.append(EngineEvent(cue="record", high_score_changed=True))
        return tuple(events)

    def _choose_upgrade(self, index: int, now: float) -> tuple[EngineEvent, ...]:
        if index not in self._upgrade_keys:
            return ()
        choice = self._upgrade_choices[self._upgrade_keys.index(index)]
        if choice == "shield":
            self.shields = min(3, self.shields + 1)
        elif choice == "stasis":
            self._stasis_bonus = min(0.24, self._stasis_bonus + 0.07)
        else:
            self.score += 300
            self._charge_overdrive(55, now)
        self.sector += 1
        self._upgrade_keys = ()
        self._upgrade_choices = ()
        self._build_sector(now)
        return (EngineEvent(cue="upgrade"),)

    def _prepare_upgrades(self) -> None:
        count = min(3, self.layout.key_count)
        if count == 1:
            keys = (0,)
        else:
            keys = tuple(
                round(position * (self.layout.key_count - 1) / (count - 1))
                for position in range(count)
            )
        choices = ["shield", "stasis", "surge"][:count]
        self._rng.shuffle(choices)
        self._upgrade_keys = keys
        self._upgrade_choices = tuple(choices)

    def _rotate(self, index: int, delta: int, now: float) -> None:
        tile = self._tiles[index]
        states = 2 if tile.kind == "straight" else 4
        tile.rotation = (tile.rotation + int(delta)) % states
        if tile.on_route:
            self._rotation_count[index] = self._rotation_count.get(index, 0) + 1
        self._flashed_keys.add(index)
        self._flash_until = max(self._flash_until, now + ROTATE_FLASH_SECONDS)

    def _spark_outgoing(self) -> int | None:
        if self._spark_key is None or self._spark_incoming is None or not self._tiles:
            return None
        connectors = tile_connections(
            self._tiles[self._spark_key].kind,
            self._tiles[self._spark_key].rotation,
        )
        if self._spark_incoming not in connectors:
            return None
        return connectors[1] if connectors[0] == self._spark_incoming else connectors[0]

    def _step_interval(self, now: float) -> float:
        settings = DIFFICULTIES[self.difficulty]
        interval = max(
            settings.minimum_seconds,
            settings.step_seconds - max(0, self.sector - 1) * settings.acceleration,
        )
        interval += self._stasis_bonus
        if now < self._stasis_until:
            interval += 0.24
        return interval

    def _charge_overdrive(self, amount: int, now: float) -> bool:
        if now < self._overdrive_until:
            return False
        self._overdrive_level = min(100, self._overdrive_level + max(0, int(amount)))
        if self._overdrive_level < 100:
            return False
        self._overdrive_level = 0
        self._overdrive_until = now + OVERDRIVE_SECONDS
        return True

    def _random_decoy(self) -> _RelayTile:
        kind = self._rng.choice(("straight", "corner", "corner"))
        states = 2 if kind == "straight" else 4
        return _RelayTile(kind, self._rng.randrange(states), -1, False)

    def _crystal_positions(self, route: tuple[int, ...]) -> set[int]:
        available = list(range(1, max(1, len(route) - 1)))
        if not available:
            return set()
        divisor = {"easy": 4, "normal": 3, "hard": 3}[self.difficulty]
        count = max(1, min(len(available), len(route) // divisor))
        return set(self._rng.sample(available, count))

    def _generate_route(self) -> tuple[tuple[int, ...], int, int]:
        portals = self._boundary_portals()
        best: tuple[tuple[int, ...], int, int] | None = None
        for _attempt in range(12):
            entry_key = self._rng.choice(tuple(portals))
            entry_side = self._rng.choice(portals[entry_key])
            parent = self._random_tree(entry_key)
            depths: dict[int, int] = {entry_key: 0}
            pending = [entry_key]
            while pending:
                current = pending.pop()
                for child, ancestor in parent.items():
                    if ancestor == current:
                        depths[child] = depths[current] + 1
                        pending.append(child)
            exits = [index for index in portals if index != entry_key]
            if not exits:
                exits = [entry_key]
            furthest = max(depths.get(index, 0) for index in exits)
            exit_key = self._rng.choice(
                [index for index in exits if depths.get(index, 0) == furthest]
            )
            reverse_path = [exit_key]
            while reverse_path[-1] != entry_key:
                ancestor = parent.get(reverse_path[-1])
                if ancestor is None:
                    break
                reverse_path.append(ancestor)
            route = tuple(reversed(reverse_path))
            incoming_at_exit = (
                direction_between(exit_key, route[-2], self.layout.columns)
                if len(route) > 1
                else entry_side
            )
            exit_sides = tuple(
                side for side in portals[exit_key] if side != incoming_at_exit
            ) or portals[exit_key]
            candidate = (route, entry_side, self._rng.choice(exit_sides))
            if best is None or len(candidate[0]) > len(best[0]):
                best = candidate
        if best is None:
            return ((0,), WEST, EAST)
        return best

    def _random_tree(self, root: int) -> dict[int, int | None]:
        parent: dict[int, int | None] = {root: None}
        stack = [root]
        while stack:
            current = stack[-1]
            choices = [
                neighbour
                for neighbour in self._neighbours(current)
                if neighbour not in parent
            ]
            if not choices:
                stack.pop()
                continue
            chosen = self._rng.choice(choices)
            parent[chosen] = current
            stack.append(chosen)
        return parent

    def _boundary_portals(self) -> dict[int, tuple[int, ...]]:
        portals: dict[int, tuple[int, ...]] = {}
        for index in range(self.layout.key_count):
            sides = tuple(
                direction
                for direction in DIRECTIONS
                if self._neighbour(index, direction) is None
            )
            if sides:
                portals[index] = sides
        return portals or {0: (WEST,)}

    def _neighbours(self, index: int) -> tuple[int, ...]:
        return tuple(
            candidate
            for direction in DIRECTIONS
            if (candidate := self._neighbour(index, direction)) is not None
        )

    def _neighbour(self, index: int, direction: int) -> int | None:
        row, column = divmod(int(index), self.layout.columns)
        row_delta, column_delta = DIRECTION_VECTORS[int(direction)]
        candidate_row = row + row_delta
        candidate_column = column + column_delta
        candidate = candidate_row * self.layout.columns + candidate_column
        if (
            0 <= candidate_row < self.layout.rows
            and 0 <= candidate_column < self.layout.columns
            and 0 <= candidate < self.layout.key_count
        ):
            return candidate
        return None

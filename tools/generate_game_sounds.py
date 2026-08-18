#!/usr/bin/env python3
"""Rebuild the original built-in game WAV effects using only the stdlib."""

from __future__ import annotations

import math
import random
import struct
import wave
from pathlib import Path

RATE = 44_100
ROOT = Path(__file__).resolve().parents[1]
SOUND_ROOT = ROOT / "linuxstreamdeck" / "assets" / "games"


def tone(
    seconds: float,
    frequencies: tuple[float, ...],
    *,
    volume: float = 0.35,
    noise: float = 0.0,
    fall: float = 1.0,
    seed: int = 0,
) -> list[float]:
    rng = random.Random(seed)
    count = max(1, round(seconds * RATE))
    samples = []
    for index in range(count):
        t = index / RATE
        envelope = min(1.0, index / max(1, RATE * 0.008))
        envelope *= max(0.0, 1.0 - index / count) ** fall
        signal = sum(math.sin(math.tau * frequency * t) for frequency in frequencies)
        signal /= max(1, len(frequencies))
        signal += rng.uniform(-1.0, 1.0) * noise
        samples.append(max(-1.0, min(1.0, signal * volume * envelope)))
    return samples


def glide(seconds: float, start: float, end: float, volume: float = 0.35) -> list[float]:
    count = max(1, round(seconds * RATE))
    phase = 0.0
    samples = []
    for index in range(count):
        p = index / count
        frequency = start + (end - start) * p
        phase += math.tau * frequency / RATE
        envelope = math.sin(math.pi * p) ** 0.7
        samples.append(math.sin(phase) * volume * envelope)
    return samples


def join(*parts: list[float], gap: float = 0.0) -> list[float]:
    output: list[float] = []
    silence = [0.0] * round(gap * RATE)
    for part in parts:
        if output:
            output.extend(silence)
        output.extend(part)
    return output


def write(game_id: str, name: str, samples: list[float]) -> None:
    directory = SOUND_ROOT / game_id
    directory.mkdir(parents=True, exist_ok=True)
    with wave.open(str(directory / name), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(RATE)
        target.writeframes(
            b"".join(
                struct.pack("<h", round(max(-1.0, min(1.0, sample)) * 32767))
                for sample in samples
            )
        )


def main() -> None:
    effects = {
        "countdown.wav": tone(
            0.10, (660.0, 990.0), volume=0.25, fall=0.7
        ),
        "go.wav": join(
            tone(0.08, (880.0, 1320.0), volume=0.28),
            tone(0.16, (1174.0, 1760.0), volume=0.30),
            gap=0.025,
        ),
        "pop.wav": glide(0.075, 220.0, 520.0, 0.20),
        "hit.wav": tone(
            0.095,
            (115.0, 165.0),
            volume=0.42,
            noise=0.18,
            fall=1.6,
            seed=7,
        ),
        "golden.wav": join(
            tone(0.06, (988.0, 1480.0), volume=0.24),
            tone(0.10, (1318.0, 1976.0), volume=0.28),
            gap=0.015,
        ),
        "wrong.wav": glide(0.12, 240.0, 115.0, 0.22),
        "select.wav": tone(
            0.055, (720.0, 1080.0), volume=0.18, fall=0.8
        ),
        "finish.wav": join(
            tone(0.10, (523.0, 784.0), volume=0.22),
            tone(0.10, (659.0, 988.0), volume=0.24),
            tone(0.20, (784.0, 1175.0), volume=0.26),
            gap=0.025,
        ),
        "record.wav": join(
            tone(0.08, (784.0, 1175.0), volume=0.23),
            tone(0.08, (988.0, 1480.0), volume=0.25),
            tone(0.08, (1175.0, 1760.0), volume=0.27),
            tone(0.22, (1568.0, 2093.0), volume=0.28),
            gap=0.018,
        ),
        "circuit.wav": join(
            tone(0.035, (330.0, 660.0), volume=0.18),
            tone(0.055, (440.0, 880.0), volume=0.20),
            gap=0.008,
        ),
        "reveal.wav": join(
            tone(0.035, (185.0, 278.0), volume=0.18, fall=1.4),
            tone(0.035, (247.0, 370.0), volume=0.14, fall=1.5),
            gap=0.004,
        ),
        "flag.wav": tone(0.075, (740.0, 1110.0), volume=0.22, fall=0.8),
        "explosion.wav": join(
            tone(0.11, (82.0, 118.0), volume=0.48, noise=0.42, fall=1.8, seed=31),
            glide(0.18, 145.0, 54.0, 0.32),
        ),
        "mark.wav": tone(0.075, (420.0, 630.0), volume=0.22, fall=0.9),
        "ai.wav": tone(0.075, (250.0, 375.0), volume=0.20, fall=0.9),
        "draw.wav": join(
            tone(0.09, (440.0, 660.0), volume=0.20),
            tone(0.14, (440.0, 660.0), volume=0.17),
            gap=0.025,
        ),
        "win.wav": join(
            tone(0.07, (659.0, 988.0), volume=0.23),
            tone(0.07, (784.0, 1175.0), volume=0.25),
            tone(0.18, (988.0, 1480.0), volume=0.28),
            gap=0.018,
        ),
        "lose.wav": join(
            glide(0.12, 330.0, 220.0, 0.20),
            glide(0.18, 220.0, 110.0, 0.22),
            gap=0.025,
        ),
        "peg.wav": tone(0.055, (540.0, 810.0), volume=0.18, fall=0.8),
        "submit.wav": join(
            tone(0.055, (392.0, 588.0), volume=0.18),
            tone(0.075, (523.0, 784.0), volume=0.20),
            gap=0.012,
        ),
        "relay-crash.wav": join(
            tone(0.07, (92.0, 138.0), volume=0.40, noise=0.34, fall=1.7, seed=73),
            glide(0.16, 310.0, 74.0, 0.26),
        ),
        "relay-crystal.wav": join(
            tone(0.045, (988.0, 1976.0), volume=0.20, fall=0.7),
            tone(0.09, (1480.0, 2960.0), volume=0.24, fall=0.8),
            gap=0.008,
        ),
        "relay-gate.wav": join(
            glide(0.09, 330.0, 880.0, 0.20),
            tone(0.14, (880.0, 1320.0), volume=0.24, fall=0.8),
            gap=0.008,
        ),
        "relay-overdrive.wav": join(
            glide(0.18, 220.0, 1320.0, 0.24),
            tone(0.20, (1175.0, 1760.0, 2350.0), volume=0.25, fall=0.7),
            gap=0.006,
        ),
        "relay-rotate.wav": join(
            tone(0.025, (260.0, 520.0), volume=0.14, fall=1.8),
            tone(0.035, (390.0, 780.0), volume=0.16, fall=1.7),
            gap=0.002,
        ),
        "relay-shield.wav": join(
            glide(0.11, 260.0, 660.0, 0.19),
            tone(0.16, (660.0, 990.0), volume=0.20, fall=0.5),
        ),
        "relay-stasis.wav": join(
            glide(0.12, 880.0, 440.0, 0.18),
            tone(0.18, (440.0, 660.0), volume=0.17, fall=0.45),
        ),
        "relay-upgrade.wav": join(
            tone(0.055, (523.0, 784.0), volume=0.20),
            tone(0.055, (659.0, 988.0), volume=0.22),
            tone(0.13, (880.0, 1320.0), volume=0.24),
            gap=0.012,
        ),
    }
    for index, frequency in enumerate((330.0, 392.0, 494.0, 587.0, 698.0, 880.0)):
        effects[f"pulse-{index}.wav"] = tone(
            0.16,
            (frequency, frequency * 2),
            volume=0.22,
            fall=0.62,
        )

    game_files = {
        "mole_smash": (
            "countdown.wav",
            "finish.wav",
            "go.wav",
            "golden.wav",
            "hit.wav",
            "pop.wav",
            "record.wav",
            "select.wav",
            "wrong.wav",
        ),
        "circuit_breaker": (
            "circuit.wav",
            "finish.wav",
            "go.wav",
            "record.wav",
            "select.wav",
        ),
        "pulse_memory": (
            "countdown.wav",
            "go.wav",
            "hit.wav",
            *(f"pulse-{index}.wav" for index in range(6)),
            "select.wav",
            "wrong.wav",
        ),
        "memory_match": (
            "finish.wav",
            "go.wav",
            "hit.wav",
            "record.wav",
            "select.wav",
            "wrong.wav",
        ),
        "minesweeper": (
            "explosion.wav",
            "finish.wav",
            "flag.wav",
            "go.wav",
            "record.wav",
            "reveal.wav",
            "select.wav",
        ),
        "tic_tac_toe": (
            "ai.wav",
            "draw.wav",
            "go.wav",
            "lose.wav",
            "mark.wav",
            "select.wav",
            "win.wav",
        ),
        "mastermind": (
            "finish.wav",
            "go.wav",
            "peg.wav",
            "record.wav",
            "select.wav",
            "submit.wav",
            "wrong.wav",
        ),
        "neon_relay": {
            "crash.wav": "relay-crash.wav",
            "crystal.wav": "relay-crystal.wav",
            "gate.wav": "relay-gate.wav",
            "go.wav": "go.wav",
            "overdrive.wav": "relay-overdrive.wav",
            "record.wav": "record.wav",
            "rotate.wav": "relay-rotate.wav",
            "select.wav": "select.wav",
            "shield.wav": "relay-shield.wav",
            "stasis.wav": "relay-stasis.wav",
            "upgrade.wav": "relay-upgrade.wav",
        },
    }
    for game_id, filenames in game_files.items():
        sources = (
            filenames.items() if isinstance(filenames, dict)
            else ((filename, filename) for filename in filenames)
        )
        for filename, source in sources:
            write(game_id, filename, effects[source])


if __name__ == "__main__":
    main()

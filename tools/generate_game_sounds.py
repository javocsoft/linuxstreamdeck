#!/usr/bin/env python3
"""Rebuild the original Mole Smash WAV effects using only the stdlib."""

from __future__ import annotations

import math
import random
import struct
import wave
from pathlib import Path

RATE = 44_100
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "linuxstreamdeck" / "assets" / "games" / "mole_smash"


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


def write(name: str, samples: list[float]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with wave.open(str(OUT / name), "wb") as target:
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
    write("countdown.wav", tone(0.10, (660.0, 990.0), volume=0.25, fall=0.7))
    write("go.wav", join(tone(0.08, (880.0, 1320.0), volume=0.28), tone(0.16, (1174.0, 1760.0), volume=0.30), gap=0.025))
    write("pop.wav", glide(0.075, 220.0, 520.0, 0.20))
    write("hit.wav", tone(0.095, (115.0, 165.0), volume=0.42, noise=0.18, fall=1.6, seed=7))
    write("golden.wav", join(tone(0.06, (988.0, 1480.0), volume=0.24), tone(0.10, (1318.0, 1976.0), volume=0.28), gap=0.015))
    write("wrong.wav", glide(0.12, 240.0, 115.0, 0.22))
    write("select.wav", tone(0.055, (720.0, 1080.0), volume=0.18, fall=0.8))
    write("finish.wav", join(tone(0.10, (523.0, 784.0), volume=0.22), tone(0.10, (659.0, 988.0), volume=0.24), tone(0.20, (784.0, 1175.0), volume=0.26), gap=0.025))
    write("record.wav", join(tone(0.08, (784.0, 1175.0), volume=0.23), tone(0.08, (988.0, 1480.0), volume=0.25), tone(0.08, (1175.0, 1760.0), volume=0.27), tone(0.22, (1568.0, 2093.0), volume=0.28), gap=0.018))


if __name__ == "__main__":
    main()

"""The world's soundtrack, synthesised with NumPy: no audio files, and every seed gets its own drone.

  * `drone(seed)` is a seamless loop: every partial and swell completes a whole number of cycles in
    the loop, and the wind is shaped noise made in the frequency domain, which is periodic by nature.
  * `cue(kind)` is a one-shot for a history marker - battle drums, a war horn, a burning city, a bell
    for a usurper's crown - and `reveal()` is the wash of the oceans draining when a world is born.

Everything comes back as 16-bit stereo WAV bytes, which `flet_audio.Audio(src=...)` plays as is.
"""

from __future__ import annotations

import io
import wave

import numpy as np

RATE = 22050
LOOP_SECONDS = 24.0
PANS = (-0.6, 0.0, 0.6)  # cues are baked at a few stereo positions; Audio.balance is not honoured everywhere


def to_wav(left: np.ndarray, right: np.ndarray | None = None) -> bytes:
    """Float samples in -1..1 (mono, or left and right) -> 16-bit stereo WAV bytes."""
    right = left if right is None else right
    pcm = (np.clip(np.stack([left, right], axis=1), -1, 1) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def _t(seconds: float) -> np.ndarray:
    return np.arange(int(seconds * RATE)) / RATE


def _normalize(x: np.ndarray, peak: float = 0.9) -> np.ndarray:
    return x * (peak / max(float(np.abs(x).max()), 1e-9))


def _fade(x: np.ndarray, fade_in: float = 0.005, fade_out: float = 0.05) -> np.ndarray:
    x = x.copy()
    n_in, n_out = int(fade_in * RATE), int(fade_out * RATE)
    if n_in:
        x[:n_in] *= np.linspace(0, 1, n_in)
    if n_out:
        x[-n_out:] *= np.linspace(1, 0, n_out)
    return x


def _shaped_noise(rng: np.random.Generator, n: int, gain) -> np.ndarray:
    """Noise with the spectrum `gain(freqs)`; periodic over its own length, so it loops without a seam."""
    freqs = np.fft.rfftfreq(n, 1 / RATE)
    spectrum = gain(freqs) * np.exp(2j * np.pi * rng.random(freqs.size))
    spectrum[0] = 0
    return np.fft.irfft(spectrum, n)


def _lowpass(x: np.ndarray, cutoff) -> np.ndarray:
    """One-pole low-pass; `cutoff` in Hz may be an array to sweep it over time."""
    a = np.exp(-2 * np.pi * np.broadcast_to(np.asarray(cutoff, float), x.shape) / RATE)
    out = np.empty_like(x)
    y = 0.0
    for i in range(x.size):  # a sweep can't be vectorised; a couple of seconds of audio is quick enough
        y = a[i] * y + (1 - a[i]) * x[i]
        out[i] = y
    return out


def _pan(x: np.ndarray, pan: float) -> tuple[np.ndarray, np.ndarray]:
    """Equal-power pan: -1 is hard left, 1 hard right."""
    angle = (pan + 1) * np.pi / 4
    return x * np.cos(angle), x * np.sin(angle)


# ------------------------------------------------------------------ the drone


def drone(seed: int) -> bytes:
    """A low modal pad for the world: its root, chord and swells all come from the seed."""
    rng = np.random.default_rng(seed + 4242)
    n = int(LOOP_SECONDS * RATE)
    t = np.arange(n) / RATE

    def fit(freq: float) -> float:  # a whole number of cycles per loop, so the loop has no click
        return max(1, round(freq * LOOP_SECONDS)) / LOOP_SECONDS

    root = 55.0 * 2 ** (rng.integers(0, 8) / 12)  # A1 up to E2
    chord = [1.0, 1.5, 2.0, rng.choice([2.4, 2.25, 2.667]), 3.0]  # root, fifth, octave, colour, twelfth
    left, right = np.zeros(n), np.zeros(n)
    for i, ratio in enumerate(chord):
        swell = 0.55 + 0.45 * np.sin(2 * np.pi * fit(rng.uniform(0.03, 0.12)) * t + rng.uniform(0, 2 * np.pi))
        for side, detune in ((left, -0.0015), (right, 0.0015)):  # a slightly different pitch per ear widens it
            voice = sum(np.sin(2 * np.pi * fit(root * ratio * h * (1 + detune)) * t + rng.uniform(0, 2 * np.pi)) / h**1.6
                        for h in range(1, 5))
            side += voice * swell * (0.9 if i < 3 else 0.45)
    wind = _shaped_noise(rng, n, lambda f: np.exp(-((f - 420) / 260) ** 2))
    gust = 0.5 + 0.5 * np.sin(2 * np.pi * fit(0.07) * t)
    wind = _normalize(wind, 1.0) * gust * 0.18
    peak = max(np.abs(left).max(), np.abs(right).max())
    return to_wav(left / peak * 0.55 + wind, right / peak * 0.55 + wind[::-1])


# ------------------------------------------------------------------- the cues


def _drum(t0: float, length: float, pitch: float, rng) -> np.ndarray:
    t = _t(length)
    f = pitch * (0.35 + 0.65 * np.exp(-t * 18))  # the skin's pitch falls after the strike
    body = np.sin(2 * np.pi * np.cumsum(f) / RATE) * np.exp(-t * 6)
    slap = rng.standard_normal(t.size) * np.exp(-t * 60) * 0.5
    return np.concatenate([np.zeros(int(t0 * RATE)), body + slap])


def _mix(parts: list[np.ndarray]) -> np.ndarray:
    out = np.zeros(max(p.size for p in parts))
    for p in parts:
        out[: p.size] += p
    return out


def battle(rng) -> np.ndarray:
    """War drums closing in, then the clash of steel."""
    parts = [_drum(t0, 1.2, p, rng) * g for t0, p, g in ((0, 90, 0.8), (0.32, 90, 0.8), (0.64, 70, 1.0), (1.1, 60, 1.2))]
    t = _t(2.2)
    ring = sum(np.sin(2 * np.pi * f * t) * np.exp(-t * d) * a for f, d, a in
               ((523, 5, 0.3), (1187, 7, 0.25), (1731, 9, 0.2), (2489, 12, 0.12)))
    clash = rng.standard_normal(t.size) * np.exp(-t * 14) * 0.5 + ring
    parts.append(np.concatenate([np.zeros(int(1.1 * RATE)), clash * 0.7]))
    return _fade(_normalize(_mix(parts)), fade_out=0.3)


def horn(rng) -> np.ndarray:
    """A war horn: a brassy call, a fifth falling to the root."""
    notes = ((0.0, 1.4, 146.8), (1.2, 1.9, 98.0))
    parts = []
    for t0, length, pitch in notes:
        t = _t(length)
        vibrato = 1 + 0.006 * np.sin(2 * np.pi * 5.2 * t) * np.clip(t * 2, 0, 1)
        phase = 2 * np.pi * np.cumsum(pitch * vibrato) / RATE
        tone = sum(np.sin(h * phase) / h ** 1.1 for h in range(1, 10))
        env = np.clip(t / 0.25, 0, 1) * np.clip((length - t) / 0.4, 0, 1)
        parts.append(np.concatenate([np.zeros(int(t0 * RATE)), _lowpass(tone, 400 + 1800 * env) * env]))
    return _fade(_normalize(_mix(parts)))


def fire(rng) -> np.ndarray:
    """A city burning: a low roar, crackling embers, and a falling groan of timber."""
    t = _t(3.2)
    env = np.clip(t / 0.3, 0, 1) * np.clip((3.2 - t) / 1.2, 0, 1)
    roar = _normalize(_shaped_noise(rng, t.size, lambda f: 1 / (1 + (f / 180) ** 2)), 1) * 0.6
    sparks = (rng.random(t.size) > 0.9985) * rng.uniform(-1, 1, t.size)
    crackle = np.convolve(sparks, np.exp(-np.arange(80) / 12), "same") * 1.4
    groan = np.sin(2 * np.pi * np.cumsum(70 * np.exp(-t * 0.5)) / RATE) * 0.35 * np.exp(-t * 0.8)
    return _fade(_normalize((roar + crackle + groan) * env))


def bell(rng) -> np.ndarray:
    """A tolling bell for a new crown: two strikes with a bell's out-of-tune partials."""
    parts = []
    for t0, pitch in ((0.0, 392.0), (0.7, 293.7)):
        t = _t(2.6)
        tone = sum(np.sin(2 * np.pi * pitch * r * t) * np.exp(-t * d) * a for r, d, a in
                   ((0.5, 1.2, 0.5), (1.0, 1.8, 1.0), (1.19, 2.4, 0.5), (1.5, 2.8, 0.4), (2.0, 3.5, 0.35), (2.74, 5, 0.2)))
        parts.append(np.concatenate([np.zeros(int(t0 * RATE)), tone * np.clip(t / 0.004, 0, 1)]))
    return _fade(_normalize(_mix(parts)), fade_out=0.4)


CUES = {"battle": battle, "war": horn, "sack": fire, "crown": bell}


def cue(kind: str, pan: float = 0.0) -> bytes:
    rng = np.random.default_rng(list(CUES).index(kind) + 7)  # the same sound every time for each kind
    return to_wav(*_pan(CUES[kind](rng), pan))


def cues() -> dict[str, list[bytes]]:
    """Every cue at every stereo position in PANS: {kind: [left, centre, right]}."""
    return {kind: [cue(kind, pan) for pan in PANS] for kind in CUES}


def reveal() -> bytes:
    """The oceans draining away: a wash of surf whose brightness sweeps down as the land appears."""
    rng = np.random.default_rng(99)
    t = _t(2.4)
    noise = rng.standard_normal(t.size)
    swept = _lowpass(noise, 2600 * np.exp(-t * 1.6) + 150)
    env = np.clip(t / 0.15, 0, 1) * np.exp(-t * 0.9)
    left = _normalize(swept * env, 0.7)
    right = _normalize(_lowpass(rng.standard_normal(t.size), 2600 * np.exp(-t * 1.6) + 150) * env, 0.7)
    return to_wav(_fade(left, fade_out=0.3), _fade(right, fade_out=0.3))

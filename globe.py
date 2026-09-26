"""Orthographic globe: wraps a flat planet frame around a sphere and lights it.

The sun is fixed relative to the viewer, so spinning the globe is the passing of
a day: towns roll over the terminator and light up on the night side. Everything
that depends only on screen position (which map cell a pixel sees before spin,
sunlight, atmosphere, stars) is precomputed per view latitude, so a frame is one
gather from the flat map plus a few multiplies.

Frames may be drawn on a worker thread while the event loop steers the camera: the
camera only aims the tilt (aim_tilt), and render() applies it, publishing each
tilt's lookup in one assignment so a frame never mixes two tilts.
"""

from __future__ import annotations

import math
from typing import NamedTuple

import numpy as np

import planet as pl

SUN = np.array([-0.78, 0.3, 0.55], np.float32)
SUN /= np.linalg.norm(SUN)
HALF = SUN + np.array([0, 0, 1], np.float32)  # Blinn half-vector for the viewer looking down -z
HALF /= np.linalg.norm(HALF)

SPACE = np.array((11, 13, 19), np.float32)  # matches the page background
ATMOSPHERE = np.array((96, 160, 255), np.float32)
CITY_LIGHT = np.array((255, 196, 110), np.float32)
LIGHT_WEIGHT = {"capital": 1.0, "city": 0.8, "town": 0.55, "village": 0.35}
MAX_TILT = 1.2  # radians of view latitude either way


def _light_kernel(radius: int = 4) -> np.ndarray:
    d = np.arange(-radius, radius + 1, dtype=np.float32)
    return np.exp(-(d[:, None] ** 2 + d[None, :] ** 2) / 3.5)


KERNEL = _light_kernel()


class Lookup(NamedTuple):
    """Everything a frame needs that depends only on the view latitude, per frame pixel on the planet."""
    index: np.ndarray  # frame pixels covered by the planet
    row: np.ndarray  # map row each one sees
    col0: np.ndarray  # map column each one sees, before spin
    shade: np.ndarray
    night: np.ndarray
    spec: np.ndarray
    haze: np.ndarray


def town_lights(p: pl.Planet, owners: np.ndarray) -> np.ndarray:
    """(H, W) glow of every town standing in this year; free towns always stand."""
    lights = np.zeros((pl.H, pl.W), np.float32)
    r = KERNEL.shape[0] // 2
    xs = np.arange(-r, r + 1)
    for t in p.towns:
        if t.kingdom >= 0 and owners[t.y, t.x] < 0:
            continue
        y0, y1 = max(t.y - r, 0), min(t.y + r + 1, pl.H)
        cols = (t.x + xs) % pl.W  # the map wraps east-west
        lights[y0:y1, cols] += LIGHT_WEIGHT[t.kind] * KERNEL[y0 - (t.y - r) : y1 - (t.y - r)]
    return np.minimum(lights, 1.2)


class Globe:
    def __init__(self, width: int, height: int, seed: int = 282):
        self.width, self.height = width, height
        self.radius = height * 0.46
        self.cx, self.cy = width / 2, height / 2
        self.tilt = None  # the view latitude frames are drawn at, which project() and cell_at() follow
        self.lookup: Lookup | None = None
        self.background = self._starfield(np.random.default_rng(seed))
        self.set_tilt(0.35)
        self.aim = self.tilt  # the view latitude the camera wants; render() moves to it

    # ------------------------------------------------------------------ setup

    def _starfield(self, rng) -> np.ndarray:
        """Space, a few hundred stars, and the atmosphere's halo beyond the limb."""
        h, w = self.height, self.width
        bg = np.broadcast_to(SPACE, (h, w, 3)).copy()
        n = int(w * h / 1800)
        ys, xs = rng.integers(0, h, n), rng.integers(0, w, n)
        bright = rng.random(n) ** 3 * 200 + 30
        bg[ys, xs] = np.minimum(bg[ys, xs] + bright[:, None] * np.array([0.9, 0.95, 1.0]), 255)

        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        dx, dy = (xx - self.cx) / self.radius, (self.cy - yy) / self.radius
        r = np.sqrt(dx * dx + dy * dy)
        halo = np.clip((1.09 - r) / 0.09, 0, 1) ** 2 * (r > 1)
        lit = np.clip(0.35 + 0.65 * (dx * SUN[0] + dy * SUN[1]) / np.maximum(r, 1e-6), 0.15, 1)
        bg += (halo * lit * 0.85)[..., None] * (ATMOSPHERE - bg)
        frame = np.empty((h, w, 4), np.uint8)
        frame[..., :3] = np.clip(bg, 0, 255)
        frame[..., 3] = 255
        return frame

    def aim_tilt(self, tilt: float):
        """Ask for view latitude `tilt` (radians); the next render() draws from there. Cheap: call it often."""
        self.aim = float(np.clip(tilt, -MAX_TILT, MAX_TILT))

    def set_tilt(self, tilt: float):
        """Recompute the per-pixel lookup for looking down from view latitude `tilt` (radians). About 5 ms."""
        tilt = float(np.clip(tilt, -MAX_TILT, MAX_TILT))
        if tilt == self.tilt:
            return
        h, w = self.height, self.width
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        x, y = (xx - self.cx) / self.radius, (self.cy - yy) / self.radius
        disk = x * x + y * y < 1
        index = np.flatnonzero(disk)
        x, y = x[disk], y[disk]
        z = np.sqrt(1 - x * x - y * y)

        # Rotate the view vector up to world space: the screen centre looks at latitude `tilt`.
        ct, st = math.cos(tilt), math.sin(tilt)
        wy = y * ct + z * st
        wz = -y * st + z * ct
        lat = np.arcsin(np.clip(wy, -1, 1))
        lon = np.arctan2(x, wz)
        row = np.clip(((0.5 - lat / math.pi) * pl.H).astype(np.int32), 0, pl.H - 1)
        col0 = (lon / (2 * math.pi) * pl.W).astype(np.float32)

        # The sun is fixed relative to the viewer, so all lighting is per screen pixel.
        n_dot_l = x * SUN[0] + y * SUN[1] + z * SUN[2]
        day = np.clip((n_dot_l + 0.08) / 0.3, 0, 1)
        day = day * day * (3 - 2 * day)
        shade = (0.1 + 0.9 * day * (0.35 + 0.65 * np.maximum(n_dot_l, 0)))[:, None]
        spec = np.maximum(x * HALF[0] + y * HALF[1] + z * HALF[2], 0) ** 60 * day * 110
        rim = (1 - z) ** 2.5 * (0.25 + 0.75 * day)
        self.lookup = Lookup(index, row, col0, shade, (1 - day) * 0.95, spec, (rim * 0.75)[:, None])
        self.tilt = tilt

    # ---------------------------------------------------------------- per frame

    def render(self, flat: np.ndarray, spin: float, water: np.ndarray, lights: np.ndarray | None = None) -> np.ndarray:
        """RGBA (height, width, 4) globe from a flat (H, W, 4) planet frame, turned `spin` radians east,
        seen from the aimed tilt."""
        self.set_tilt(self.aim)
        lk = self.lookup
        cols = np.floor(lk.col0 + spin / (2 * math.pi) * pl.W).astype(np.int32) % pl.W
        rgb = flat[lk.row, cols, :3].astype(np.float32) * lk.shade
        rgb += (lk.spec * water[lk.row, cols])[:, None]
        if lights is not None:
            rgb += (lights[lk.row, cols] * lk.night)[:, None] * CITY_LIGHT
        rgb += lk.haze * (ATMOSPHERE - rgb)

        frame = self.background.copy()
        frame.reshape(-1, 4)[lk.index, :3] = np.clip(rgb, 0, 255)
        return frame

    def project(self, ys: np.ndarray, xs: np.ndarray, spin: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Screen (px, py) of map cells (ys, xs), plus depth z: 1 faces the viewer, below 0 is the far side."""
        lat = (0.5 - (np.asarray(ys, np.float64) + 0.5) / pl.H) * math.pi
        lon = (np.asarray(xs, np.float64) + 0.5) / pl.W * 2 * math.pi - spin
        wx, wy, wz = np.cos(lat) * np.sin(lon), np.sin(lat), np.cos(lat) * np.cos(lon)
        ct, st = math.cos(self.tilt), math.sin(self.tilt)
        y, z = wy * ct - wz * st, wy * st + wz * ct  # inverse of the rotation in set_tilt()
        return self.cx + wx * self.radius, self.cy - y * self.radius, z

    def cell_at(self, px: float, py: float, spin: float) -> tuple[int, int] | None:
        """Map cell (y, x) under screen pixel (px, py), or None off the planet."""
        # Same pixel grid as set_tilt(), so a click hits exactly the cell drawn under it.
        x, y = (math.floor(px) - self.cx) / self.radius, (self.cy - math.floor(py)) / self.radius
        if x * x + y * y >= 1:
            return None
        z = math.sqrt(1 - x * x - y * y)
        ct, st = math.cos(self.tilt), math.sin(self.tilt)
        lat = math.asin(max(-1.0, min(1.0, y * ct + z * st)))
        lon = math.atan2(x, -y * st + z * ct) + spin
        row = min(max(int((0.5 - lat / math.pi) * pl.H), 0), pl.H - 1)
        return row, math.floor(lon / (2 * math.pi) * pl.W) % pl.W

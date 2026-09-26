"""Falling-sand cellular simulation, vectorised with NumPy.

The world is two (H, W) uint8 arrays that always move together:
  mat  - material id of each cell
  data - per-cell byte: colour noise for solids/liquids, remaining life for fire/gases

Movement is processed row by row so a particle moves at most once per step,
while each row's work is fully vectorised.
"""

import numpy as np

EMPTY, SAND, WATER, WALL, PLANT, FIRE, OIL, SMOKE, STEAM, STONE, DIRT = range(11)
N_MATERIALS = 11  # stone and dirt are the ground of a world's cross-section (section.py)

# Heavier things sink through lighter ones; 100 = immovable.
DENSITY = np.array([0, 3, 2, 100, 100, 100, 1, 0, 0, 100, 100], dtype=np.int16)
FALLS = np.zeros(N_MATERIALS, bool)
FALLS[[SAND, WATER, OIL]] = True
LIQUID = np.zeros(N_MATERIALS, bool)
LIQUID[[WATER, OIL]] = True
GAS = np.zeros(N_MATERIALS, bool)
GAS[[SMOKE, STEAM]] = True

BACKGROUND = (16, 17, 26)
COLORS = np.array(
    [
        BACKGROUND,
        (222, 184, 110),  # sand
        (48, 118, 230),  # water
        (110, 112, 128),  # wall
        (70, 190, 90),  # plant
        (255, 120, 30),  # fire (replaced by FIRE_LUT)
        (92, 60, 40),  # oil
        (90, 90, 96),  # smoke
        (200, 220, 240),  # steam
        (104, 98, 92),  # stone
        (112, 82, 54),  # dirt
    ],
    dtype=np.int16,
)
# How strongly the per-cell noise byte tints each material.
SHADE = np.array([0, 22, 10, 14, 30, 0, 10, 0, 0, 26, 24], dtype=np.int16)


def _fire_lut() -> np.ndarray:
    """Colour ramp indexed by remaining life: embers -> red -> orange -> white-hot."""
    stops = np.array(
        [(60, 10, 5), (200, 30, 10), (255, 110, 20), (255, 200, 60), (255, 250, 210)],
        dtype=np.float32,
    )
    t = np.linspace(0, len(stops) - 1, 64)
    i = np.minimum(t.astype(int), len(stops) - 2)
    f = (t - i)[:, None]
    return (stops[i] * (1 - f) + stops[i + 1] * f).astype(np.int16)


FIRE_LUT = _fire_lut()


def _neighbors(mask: np.ndarray, diagonal: bool = True) -> np.ndarray:
    """True where any (4- or 8-connected) neighbour of a cell is set. No wrap-around."""
    out = np.zeros_like(mask)
    out[1:] |= mask[:-1]
    out[:-1] |= mask[1:]
    out[:, 1:] |= mask[:, :-1]
    out[:, :-1] |= mask[:, 1:]
    if diagonal:
        out[1:, 1:] |= mask[:-1, :-1]
        out[1:, :-1] |= mask[:-1, 1:]
        out[:-1, 1:] |= mask[1:, :-1]
        out[:-1, :-1] |= mask[1:, 1:]
    return out


class World:
    def __init__(self, width: int, height: int, seed: int | None = None):
        self.width = width
        self.height = height
        self.rng = np.random.default_rng(seed)
        self.mat = np.zeros((height, width), np.uint8)
        self.data = np.zeros((height, width), np.uint8)
        self.tick = 0

    def clear(self):
        self.mat[:] = EMPTY
        self.data[:] = 0

    # ------------------------------------------------------------------ painting

    def paint(self, cx: int, cy: int, radius: int, material: int):
        """Spray `material` in a disc. Walls and the eraser overwrite; others only fill space."""
        y0, y1 = max(cy - radius, 0), min(cy + radius + 1, self.height)
        x0, x1 = max(cx - radius, 0), min(cx + radius + 1, self.width)
        if y0 >= y1 or x0 >= x1:
            return
        yy, xx = np.ogrid[y0:y1, x0:x1]
        disc = (yy - cy) ** 2 + (xx - cx) ** 2 <= radius * radius
        m = self.mat[y0:y1, x0:x1]
        d = self.data[y0:y1, x0:x1]

        if material in (EMPTY, WALL):
            target = disc
        else:
            spray = {SAND: 0.35, WATER: 0.45, OIL: 0.45, FIRE: 0.5}.get(material, 1.0)
            free = (m == EMPTY) | GAS[m]
            target = disc & free & (self.rng.random(disc.shape) < spray)

        m[target] = material
        n = int(target.sum())
        if material == FIRE:
            d[target] = self.rng.integers(30, 64, n, dtype=np.uint8)
        else:
            d[target] = self.rng.integers(0, 256, n, dtype=np.uint8)

    # ---------------------------------------------------------------- simulation

    def _swap(self, y1, x1, y2, x2):
        for a in (self.mat, self.data):
            tmp = a[y2, x2].copy()
            a[y2, x2] = a[y1, x1]
            a[y1, x1] = tmp

    def step(self):
        self.tick += 1
        self._rise_gases()
        self._fall()
        self._react()

    def _rise_gases(self):
        m, rng, W = self.mat, self.rng, self.width
        for y in range(1, self.height):
            row, above = m[y], m[y - 1]
            gas = GAS[row]
            if not gas.any():
                continue
            # Straight up, with some flutter.
            xs = np.flatnonzero(gas & (above == EMPTY) & (rng.random(W) < 0.75))
            self._swap(y, xs, y - 1, xs)
            # Drift diagonally up or sideways into empty space.
            for dy in (-1, 0):
                gas = GAS[row]
                go_right = rng.random(W) < 0.5
                target_row = m[y + dy]
                r = np.zeros(W, bool)
                r[:-1] = gas[:-1] & go_right[:-1] & (target_row[1:] == EMPTY)
                xs = np.flatnonzero(r)
                self._swap(y, xs, y + dy, xs + 1)
                gas = GAS[row]
                lft = np.zeros(W, bool)
                lft[1:] = gas[1:] & ~go_right[1:] & (target_row[:-1] == EMPTY)
                xs = np.flatnonzero(lft)
                self._swap(y, xs, y + dy, xs - 1)

    def _fall(self):
        m, rng, W = self.mat, self.rng, self.width
        for y in range(self.height - 2, -1, -1):
            row, below = m[y], m[y + 1]
            if not FALLS[row].any():
                continue

            # Straight down into anything lighter.
            xs = np.flatnonzero(FALLS[row] & (DENSITY[below] < DENSITY[row]))
            self._swap(y, xs, y + 1, xs)

            # Diagonally down (needs the side cell passable too, so nothing
            # squeezes through diagonal wall gaps).
            for dx in ((-1, 1) if rng.random() < 0.5 else (1, -1)):
                dens = DENSITY[row]
                f = FALLS[row]
                if dx == 1:
                    ok = f[:-1] & (DENSITY[below[1:]] < dens[:-1]) & (DENSITY[row[1:]] < dens[:-1])
                    xs = np.flatnonzero(ok)
                else:
                    ok = f[1:] & (DENSITY[below[:-1]] < dens[1:]) & (DENSITY[row[:-1]] < dens[1:])
                    xs = np.flatnonzero(ok) + 1
                self._swap(y, xs, y + 1, xs + dx)

            # Liquids spread sideways; a few passes so pools level out quickly.
            for _ in range(3):
                liq = LIQUID[row]
                if not liq.any():
                    break
                dens = DENSITY[row]
                go_right = rng.random(W) < 0.5
                r = np.zeros(W, bool)
                r[:-1] = liq[:-1] & go_right[:-1] & (dens[1:] < dens[:-1])
                r[:-1] &= ~r[1:]  # break chains so no cell is both source and target
                xs = np.flatnonzero(r)
                self._swap(y, xs, y, xs + 1)

                liq = LIQUID[row]
                dens = DENSITY[row]
                lft = np.zeros(W, bool)
                lft[1:] = liq[1:] & ~go_right[1:] & (dens[:-1] < dens[1:])
                lft[1:] &= ~lft[:-1]
                xs = np.flatnonzero(lft)
                self._swap(y, xs, y, xs - 1)

    def _react(self):
        m, d, rng = self.mat, self.data, self.rng
        shape = m.shape
        roll = rng.random(shape, dtype=np.float32)
        fire = m == FIRE
        water = m == WATER

        # Water douses fire and boils into steam.
        doused = fire & _neighbors(water, diagonal=False)
        boiled = water & _neighbors(fire, diagonal=False) & (roll < 0.3)

        # Fire spreads to plants and (eagerly) to oil.
        near_fire = _neighbors(fire)
        ignite = near_fire & (
            ((m == PLANT) & (roll < 0.025)) | ((m == OIL) & (roll < 0.35))
        )

        # Flames lick upward into empty air and give off smoke.
        above_fire = np.zeros_like(fire)
        above_fire[:-1] = fire[1:]
        empty = m == EMPTY
        flame = above_fire & empty & (roll < 0.12)
        smoke = above_fire & empty & ~flame & (roll > 0.97)

        # Plants slowly drink adjacent water and grow into it.
        grow = water & ~boiled & _neighbors(m == PLANT, diagonal=False) & (roll < 0.02)

        # Fire burns down; gases fade.
        burning = fire & ~doused
        d[burning] = np.maximum(d[burning].astype(np.int16) - rng.integers(0, 3, int(burning.sum())), 0)
        burnt_out = burning & (d == 0)
        gas = GAS[m]
        fading = gas & (roll < 0.5)
        d[fading] = np.maximum(d[fading].astype(np.int16) - 1, 0)
        gone = gas & (d == 0)
        condense = gone & (m == STEAM) & (roll < 0.125)  # gone cells all rolled < 0.5, so this is 25% of them

        def put(mask, material, lo, hi):
            n = int(mask.sum())
            if n:
                m[mask] = material
                d[mask] = rng.integers(lo, hi, n, dtype=np.uint8)

        put(gone & ~condense, EMPTY, 0, 1)
        put(condense, WATER, 0, 256)  # steam rains back down
        put(burnt_out & (roll < 0.4), SMOKE, 40, 90)
        put(burnt_out & (roll >= 0.4), EMPTY, 0, 1)
        put(doused | boiled, STEAM, 60, 140)
        put(ignite & (m == PLANT), FIRE, 20, 45)
        put(ignite & (m == OIL), FIRE, 40, 64)
        put(flame, FIRE, 3, 9)
        put(smoke, SMOKE, 40, 90)
        put(grow, PLANT, 0, 256)

    # ----------------------------------------------------------------- rendering

    def render(self, cursor: tuple[int, int, int] | None = None) -> np.ndarray:
        """RGBA frame, one pixel per cell. `cursor` = (x, y, radius) draws a brush ring."""
        m, d = self.mat, self.data
        rgb = COLORS[m] + (((d.astype(np.int16) - 128) * SHADE[m]) // 128)[..., None]

        water = m == WATER
        if water.any():
            ys, xs = np.nonzero(water)
            wave = np.sin(self.tick * 0.15 + xs * 0.35 + ys * 0.5) * 10
            rgb[ys, xs] += wave.astype(np.int16)[:, None]

        fire = m == FIRE
        rgb[fire] = FIRE_LUT[np.minimum(d[fire], 63)]

        bg = np.array(BACKGROUND, np.int16)
        for gas in (SMOKE, STEAM):
            g = m == gas
            if g.any():
                a = np.minimum(d[g].astype(np.int16), 100)[:, None]
                rgb[g] = bg + (COLORS[gas] - bg) * a // 110

        if cursor is not None:
            cx, cy, r = cursor
            yy, xx = np.ogrid[: self.height, : self.width]
            dist2 = (yy - cy) ** 2 + (xx - cx) ** 2
            ring = (dist2 <= r * r) & (dist2 > max(r - 1, 0) ** 2)
            rgb[ring] = (rgb[ring] + 255) // 2

        frame = np.empty((self.height, self.width, 4), np.uint8)
        frame[..., :3] = np.clip(rgb, 0, 255)
        frame[..., 3] = 255
        return frame

    def count(self) -> int:
        return int(np.count_nonzero(self.mat))

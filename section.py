"""A slice through a world, as falling-sand terrain - and back again.

Cut a line across the map and look at it side on: every sandbox column is a point along the line. Its
height comes from the world's elevation (exaggerated, as cross-sections are), and its biome decides
what it is made of - sea over a sandy bed, bare rock on the peaks, sand dunes and beaches, soil under
grass and forest. Rivers cut channels, and the towns on the line stand on the ground.

When the slice closes, `Section.write_back` carries what happened in it back to the map, along a strip
three cells wide: burned forest becomes burnt land, flooded ground becomes water, sand poured into the
sea becomes new land, planted ground turns green, and dug or heaped ground changes height.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

import planet as pl
import sand

SKY_ROWS = 16  # the highest peak on the line stops this many rows below the top, leaving room for smoke
BED_ROWS = 6  # the deepest sea stops this far above the bottom
SEA_LEVEL = 0.6  # the sea surface, as a fraction of the height
FOREST_TREES = {pl.FOREST: (0.3, 5, 9), pl.TAIGA: (0.35, 5, 8), pl.RAINFOREST: (0.55, 7, 12),
                pl.SAVANNA: (0.05, 4, 6)}  # biome: (chance of a tree per column, min height, max height)
BUILDING = {"capital": (4, 4, 8), "city": (3, 3, 6), "town": (2, 2, 4), "village": (2, 2, 3)}  # houses, min/max height
MARGIN = 0.06  # the cut runs this fraction of its length past each end, so towns there aren't on the edge
GROUND = np.zeros(sand.N_MATERIALS, bool)
GROUND[[sand.STONE, sand.DIRT, sand.SAND]] = True  # what counts as the ground surface when reading a slice back
CHANGE_ROWS = 5  # the ground must rise or fall this many rows before the map's height follows (sand slumps less)


@dataclass
class Label:
    col: int
    row: int  # the top of whatever stands there
    text: str
    kind: str  # capital | city | town | village | peak


@dataclass
class Section:
    """A cut from map cell `a` to map cell `b` (each (y, x)), ready to load into a sandbox."""

    a: tuple[float, float]
    b: tuple[float, float]
    mat: np.ndarray
    data: np.ndarray
    ys: np.ndarray  # the map position (fractional cells) each column samples
    xs: np.ndarray
    land: np.ndarray  # per column: dry land when cut
    sea_row: int
    high: float  # metres at the top of the land scale, and depth at the bottom of the sea scale
    deep: float
    labels: list[Label] = field(default_factory=list)
    owners: np.ndarray | None = None  # realm id per column (-1 = nobody)
    start_name: str = ""
    end_name: str = ""

    def load_into(self, world: sand.World):
        world.mat[:] = self.mat
        world.data[:] = self.data
        world.tick = 0

    def tend(self, world: sand.World):
        """Keep the slice's sea joined to the world's ocean: water heaped above sea level over the open sea
        drains away, and gaps below it fill back in - so poured sand can rise out of the waves as land."""
        m, d, sr, rng = world.mat, world.data, self.sea_row, world.rng
        sea = ~self.land[None, :]
        above, below = m[:sr], m[1:sr + 1]
        drain = (above == sand.WATER) & ((below == sand.WATER) | GROUND[below]) & sea & (rng.random(above.shape) < 0.25)
        above[drain] = sand.EMPTY
        wet = sand._neighbors(m == sand.WATER, diagonal=False)[sr:]
        low = m[sr:]
        fill = (low == sand.EMPTY) & wet & sea & (rng.random(low.shape) < 0.5)
        low[fill] = sand.WATER
        d[sr:][fill] = rng.integers(0, 256, int(fill.sum()), dtype=np.uint8)

    def row_to_elev(self, row: int) -> float:
        """Metres above (or below) sea level for a ground surface at `row` - the inverse of `cut`'s scale."""
        height = self.mat.shape[0]
        if row < self.sea_row:
            return (self.sea_row - 1 - row) / max(self.sea_row - 1 - SKY_ROWS, 1) * self.high
        return -(row - self.sea_row) / max(height - BED_ROWS - self.sea_row, 1) * self.deep

    def write_back(self, p: pl.Planet, mat: np.ndarray) -> dict[str, int]:
        """Carry the sandbox's changes (`mat`, compared with the slice as cut) back onto the map.

        Returns how many map cells each kind of change touched."""
        before, after = _columns(self.mat), _columns(mat)
        verdicts: dict[tuple[int, int], tuple[str, float]] = {}
        for col in range(mat.shape[1]):
            top0, water0, plants0 = before[0][col], before[1][col], before[2][col]
            top, water, plants, burning = after[0][col], after[1][col], after[2][col], after[3][col]
            elev = self.row_to_elev(top)
            verdict = None
            if self.land[col]:
                if water >= max(2, water0 + 2):
                    verdict = ("flooded", min(elev, -15.0))  # a lake, or the sea let in by digging
                elif plants0 >= 2 and (plants <= plants0 * 0.3 or burning):
                    verdict = ("burned", elev if abs(top - top0) >= CHANGE_ROWS else np.nan)
                elif plants0 < 2 and plants >= 3:
                    verdict = ("greened", elev if abs(top - top0) >= CHANGE_ROWS else np.nan)
                elif top <= top0 - CHANGE_ROWS:
                    verdict = ("raised", elev)
                elif top >= top0 + CHANGE_ROWS:
                    verdict = ("lowered", elev)
            elif top < self.sea_row and water < 2:
                verdict = ("new land", max(elev, 5.0))  # sand heaped above the waves
            if verdict:
                for cell in self._strip(col):
                    verdicts[cell] = verdict
        counts: dict[str, int] = {}
        for (y, x), (kind, elev) in verdicts.items():
            _apply(p, y, x, kind, elev)
            counts[kind] = counts.get(kind, 0) + 1
        return counts

    def _strip(self, col: int) -> list[tuple[int, int]]:
        """The map cells a column stands for: its own, and one either side across the cut."""
        dy, dx = self.b[0] - self.a[0], self.b[1] - self.a[1]
        norm = max(float(np.hypot(dy, dx)), 1e-6)
        ny, nx = dx / norm, -dy / norm  # perpendicular to the cut
        cells = []
        for k in (-1, 0, 1):
            y = round(self.ys[col] + ny * k)
            x = round(self.xs[col] + nx * k) % pl.W
            if 0 <= y < pl.H:
                cells.append((y, x))
        return cells


def _columns(mat: np.ndarray):
    """Per column: the top of the ground resting on the bottom, the depth of water standing on it, how
    many plant cells it holds, and whether anything in it is burning."""
    height, width = mat.shape
    ground = GROUND[mat]
    # The ground surface: the top of the unbroken run of ground up from the bottom row.
    gap = ~ground[::-1]  # rows counted from the bottom
    run = np.where(gap.any(axis=0), gap.argmax(axis=0), height)
    top = height - run
    water = np.zeros(width, int)
    for col in range(width):
        r = top[col] - 1
        while r >= 0 and mat[r, col] == sand.WATER:
            r -= 1
        water[col] = top[col] - 1 - r
    plants = (mat == sand.PLANT).sum(axis=0)
    burning = (mat == sand.FIRE).any(axis=0)
    return top, water, plants, burning


def _apply(p: pl.Planet, y: int, x: int, kind: str, elev: float):
    """Change one map cell: its height (raw field and metres), land or sea, biome and colour."""
    if not np.isnan(elev):
        peak, sea, h_top, h_bottom = max(float(p.elev.max()), 1.0), p.sea, float(p.h.max()), float(p.h.min())
        if elev > 0:  # invert planet.generate's height-to-metres curves
            p.h[y, x] = sea + (h_top - sea) * min(elev / peak, 1.0) ** (1 / 1.8) + 1e-4
        else:
            p.h[y, x] = sea - (sea - h_bottom) * min(-elev / 6500, 1.0) ** (1 / 0.8) - 1e-4
        p.elev[y, x] = elev
    p.land[y, x] = p.h[y, x] > p.sea
    if not p.land[y, x]:
        p.biome[y, x], p.flow[y, x] = pl.OCEAN, 0
        return
    biome = {"burned": pl.BURNT, "greened": pl.FOREST, "new land": pl.DESERT}.get(kind, int(p.biome[y, x]))
    p.biome[y, x] = biome
    grain = 1 + (np.random.default_rng((y, x)).random() - 0.5) * 0.12
    p.land_rgb[y, x] = pl.BIOME_COLORS[biome] * grain


def _sample(field_: np.ndarray, ys: np.ndarray, xs: np.ndarray) -> np.ndarray:
    """Bilinear sample of a map field (east-west wrapping) at fractional cells."""
    y0 = np.clip(np.floor(ys).astype(int), 0, pl.H - 1)
    y1 = np.clip(y0 + 1, 0, pl.H - 1)
    x0 = np.floor(xs).astype(int) % pl.W
    x1 = (x0 + 1) % pl.W
    fy, fx = np.clip(ys - np.floor(ys), 0, 1), xs - np.floor(xs)
    top = field_[y0, x0] * (1 - fx) + field_[y0, x1] * fx
    bottom = field_[y1, x0] * (1 - fx) + field_[y1, x1] * fx
    return top * (1 - fy) + bottom * fy


def _place_name(p: pl.Planet, owners: np.ndarray, y: float, x: float) -> str:
    """What to call an end of the cut: a nearby town, else the realm, else the land or sea itself."""
    near = min(p.towns, key=lambda t: (t.y - y) ** 2 + min(abs(t.x - x), pl.W - abs(t.x - x)) ** 2, default=None)
    if near is not None and (near.y - y) ** 2 + min(abs(near.x - x), pl.W - abs(near.x - x)) ** 2 <= 12 ** 2:
        return near.name
    cy, cx = round(y) % pl.H, round(x) % pl.W
    rid = int(owners[cy, cx])
    if rid >= 0:
        realms = list(p.kingdoms) + list(p.history.lost)
        return realms[rid].name
    return "the open sea" if not p.land[cy, cx] else pl.BIOME_NAMES[p.biome[cy, cx]].lower()


def cut(p: pl.Planet, a: tuple[float, float], b: tuple[float, float], owners: np.ndarray,
        width: int, height: int) -> Section:
    """Build the terrain seen side on along the line from map cell `a` to `b` (each (y, x))."""
    rng = np.random.default_rng((p.seed, round(a[0]), round(a[1]), round(b[0]), round(b[1])))
    t = np.linspace(-MARGIN, 1 + MARGIN, width)
    ys = np.clip(a[0] + (b[0] - a[0]) * t, 0, pl.H - 1)
    xs = a[1] + (b[1] - a[1]) * t
    cy, cx = np.round(ys).astype(int), np.round(xs).astype(int) % pl.W
    elev = _sample(p.elev, ys, xs)
    elev = np.convolve(np.pad(elev, 2, mode="edge"), np.ones(5) / 5, "valid")  # soften the pixel steps
    biome, flow = p.biome[cy, cx], p.flow[cy, cx]
    land = elev > 0

    # Heights to rows: land rises from the sea line toward the sky, the sea bed sinks toward the bottom.
    sea_row = int(height * SEA_LEVEL)
    high = max(float(elev.max()), 600.0)
    deep = max(float(-elev.min()), 400.0)
    surface = np.where(
        land,
        sea_row - 1 - (elev / high) * (sea_row - 1 - SKY_ROWS),
        sea_row + (-elev / deep) * (height - BED_ROWS - sea_row),
    ).round().astype(int)

    mat = np.zeros((height, width), np.uint8)
    rows = np.arange(height)[:, None]
    ground = rows >= surface[None, :]
    mat[ground] = sand.STONE
    mat[(rows >= sea_row) & ~ground & ~land[None, :]] = sand.WATER
    coast = land & (np.convolve((~land).astype(int), np.ones(7, int), "same") > 0)

    def topsoil(col: int, material: int, depth: int):
        s = surface[col]
        mat[s:min(s + depth, height), col] = material

    for col in range(width):
        bio = biome[col]
        if not land[col]:
            topsoil(col, sand.SAND, 3)  # a sandy sea bed
        elif coast[col] or bio == pl.DESERT:
            topsoil(col, sand.SAND, 8 if bio == pl.DESERT else 3)  # dunes and beaches
        elif bio != pl.MOUNTAIN:
            topsoil(col, sand.DIRT, 3)  # soil; mountains are bare rock

    # Rivers cut a channel into the land and fill it.
    for col in np.flatnonzero(land & (flow > 0)):
        for c in range(max(col - 1, 0), min(col + 2, width)):
            if land[c]:
                mat[surface[c]:surface[c] + 2, c] = sand.WATER
                mat[surface[c] + 2, c] = sand.DIRT

    # Trees and grass on the soil, kept back from water so they don't grow into it.
    wet = np.zeros(width, bool)
    for col in range(width):
        wet[col] = bool((mat[max(surface[col] - 1, 0):surface[col] + 3, max(col - 2, 0):col + 3] == sand.WATER).any())
    for col in range(width):
        s, bio = surface[col], biome[col]
        if not land[col] or coast[col] or wet[col] or mat[s, col] != sand.DIRT or s < 2:
            continue
        chance, lo, hi = FOREST_TREES.get(bio, (0.0, 0, 0))
        if chance and rng.random() < chance:
            h = int(rng.integers(lo, hi + 1))
            mat[max(s - h, 0):s, col] = sand.PLANT  # trunk
            crown_y, r = max(s - h, 0), 1 + h // 4
            for dy in range(-r, r + 1):
                for dx in range(-r, r + 1):
                    y, x = crown_y + dy, col + dx
                    if 0 <= y < s and 0 <= x < width and dx * dx + dy * dy <= r * r and mat[y, x] == sand.EMPTY:
                        mat[y, x] = sand.PLANT
        elif bio in (pl.GRASSLAND, pl.SAVANNA, pl.FOREST, pl.TAIGA, pl.RAINFOREST) and rng.random() < 0.6:
            mat[s - 1, col] = sand.PLANT  # grass

    # Towns on the line: houses of stone, labelled.
    labels: list[Label] = []
    for town in p.towns:
        d2 = (ys - town.y) ** 2 + np.minimum(np.abs(xs - town.x), pl.W - np.abs(xs - town.x)) ** 2
        col = int(np.argmin(d2))
        if d2[col] > 2.5 ** 2:
            continue
        if not land[col]:  # a harbour town whose spot the smoothing put just offshore: step onto the land
            shore = [c for c in range(max(col - 6, 0), min(col + 7, width)) if land[c]]
            if not shore:
                continue
            col = min(shore, key=lambda c: abs(c - col))
        houses, lo, hi = BUILDING[town.kind]
        tallest = surface[col]
        for i in range(houses):
            c0 = col - houses * 5 // 2 + i * 5
            w, h = 4, int(rng.integers(lo, hi + 1))
            if town.kind == "capital" and i == houses // 2:
                h += 4  # the keep
            for c in range(max(c0, 0), min(c0 + w, width)):
                if not land[c]:
                    continue
                s = surface[c]
                mat[max(s - h, 0):s, c] = sand.WALL
                tallest = min(tallest, s - h)
        labels.append(Label(col, int(tallest), town.name, town.kind))
    peak = int(np.argmax(elev))
    if elev[peak] > 2500 and not any(abs(lb.col - peak) < 12 for lb in labels):
        py, px = p.peak
        name = p.peak_name if (ys[peak] - py) ** 2 + (xs[peak] - px) ** 2 < 6 ** 2 else f"{elev[peak]:,.0f} m"
        labels.append(Label(peak, int(surface[peak]), f"▲ {name}", "peak"))

    data = rng.integers(0, 256, (height, width), dtype=np.uint8)
    return Section(
        a=a, b=b, mat=mat, data=data, ys=ys, xs=xs, land=land, sea_row=sea_row, high=high, deep=deep,
        labels=labels, owners=owners[cy, cx].astype(int),
        start_name=_place_name(p, owners, *a), end_name=_place_name(p, owners, *b),
    )

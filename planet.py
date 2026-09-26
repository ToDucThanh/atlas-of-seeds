"""Deterministic planet generator: one seed becomes terrain, climate, rivers, kingdoms and towns.

The map is a cylinder - it wraps east-west - so the world has no edge, and the
poles freeze. Everything derives from `numpy.random.default_rng(seed)`, so a
seed always produces the same world.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import numpy as np

W, H = 480, 300

# ---------------------------------------------------------------------- biomes

(OCEAN, SEA_ICE, ICE, TUNDRA, TAIGA, FOREST, GRASSLAND, DESERT, SAVANNA, RAINFOREST, MOUNTAIN) = range(11)
BURNT = 11  # never generated: land burned in a cross-section (section.py)
BIOME_NAMES = [
    "Ocean", "Sea ice", "Glacier", "Tundra", "Taiga", "Temperate forest",
    "Grassland", "Desert", "Savanna", "Rainforest", "Mountains", "Burnt land",
]
BIOME_COLORS = np.array(
    [
        (30, 60, 100),  # ocean (replaced by depth shading)
        (215, 228, 236),  # sea ice
        (236, 241, 246),  # glacier
        (152, 158, 132),  # tundra
        (62, 98, 74),  # taiga
        (74, 124, 62),  # temperate forest
        (146, 164, 92),  # grassland
        (222, 196, 140),  # desert
        (186, 172, 98),  # savanna
        (40, 112, 58),  # rainforest
        (128, 116, 104),  # mountains
        (58, 46, 40),  # burnt land
    ],
    np.float32,
)
HABITABILITY = np.array([0, 0, 0, 0.1, 0.4, 0.85, 1.0, 0.15, 0.7, 0.5, 0.1, 0.05], np.float32)

SHALLOW = np.array((64, 132, 170), np.float32)
DEEP = np.array((14, 32, 66), np.float32)
RIVER = np.array((70, 128, 196), np.float32)
SELECT = np.array((255, 226, 140), np.float32)
WAR = np.array((226, 64, 52), np.float32)
GLOW = np.array((255, 236, 170), np.float32)

NEIGH8 = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


# ------------------------------------------------------------------ data model


@dataclass
class Town:
    name: str
    y: int
    x: int
    kingdom: int  # -1 = free town in unclaimed land
    kind: str  # capital / city / town / village
    coastal: bool
    river: bool
    biome: int


@dataclass
class River:
    cells: list[tuple[int, int]]
    reaches_sea: bool
    name: str = ""


@dataclass
class Kingdom:
    id: int
    seat: tuple[int, int]
    color: tuple[int, int, int]
    cells: int = 0
    biome_share: dict[int, float] = field(default_factory=dict)
    coastal: bool = False
    neighbors: set[int] = field(default_factory=set)
    label_pos: tuple[float, float] = (0, 0)
    # Filled in by lore.populate()
    name: str = ""
    title: str = ""
    lore: dict = field(default_factory=dict)


@dataclass
class Planet:
    seed: int
    h: np.ndarray  # raw height field 0..1
    sea: float
    land: np.ndarray
    elev: np.ndarray  # metres; negative = ocean depth
    temp: np.ndarray  # degrees C
    moist: np.ndarray
    biome: np.ndarray
    flow: np.ndarray  # river flow count per cell (0 = no river)
    kingdom_map: np.ndarray  # -1 = unclaimed
    land_rgb: np.ndarray
    kingdoms: list[Kingdom]
    towns: list[Town]
    rivers: list[River]
    peak: tuple[int, int]
    name: str = ""
    year: int = 0
    peak_name: str = ""
    lore: dict = field(default_factory=dict)
    history: object = None  # history.History

    def realm_colors(self) -> np.ndarray:
        """Colour per realm id (living, then fallen), plus a trailing entry for id -1."""
        lost = self.history.lost if self.history else []
        return np.array([k.color for k in self.kingdoms] + [r.color for r in lost] + [(0, 0, 0)], np.float32)

    def realm_title(self, realm: int) -> str:
        if realm < 0:
            return "Unclaimed wilds"
        if realm < len(self.kingdoms):
            return self.kingdoms[realm].title
        return self.history.lost[realm - len(self.kingdoms)].title

    def describe_cell(self, y: int, x: int, owners: np.ndarray | None = None) -> str:
        b = int(self.biome[y, x])
        if not self.land[y, x]:
            return f"{BIOME_NAMES[b]} · depth {-self.elev[y, x]:,.0f} m · {self.temp[y, x]:.0f}°C"
        parts = [BIOME_NAMES[b], f"{self.elev[y, x]:,.0f} m", f"{self.temp[y, x]:.0f}°C"]
        owners = self.kingdom_map if owners is None else owners
        parts.append(self.realm_title(int(owners[y, x])))
        if self.flow[y, x]:
            river = next((r.name for r in self.rivers if r.name and (y, x) in set(r.cells)), "")
            parts.append(f"River {river}" if river else "a river")
        return " · ".join(parts)


def seed_from_text(text: str) -> int:
    """Digits are used as-is; any other text is hashed, so 'Middle-earth' is a valid seed."""
    text = text.strip() or "282"
    if text.isdigit():
        return int(text)
    return int.from_bytes(hashlib.sha256(text.encode()).digest()[:6], "little")


# ----------------------------------------------------------------- noise utils


def _smoothstep(t):
    return t * t * (3 - 2 * t)


def _normalize(a):
    return (a - a.min()) / (np.ptp(a) + 1e-9)


class _Noise:
    """Fractal value noise, seamless east-west."""

    def __init__(self, rng, base: int, octaves: int):
        self.grids = []
        for o in range(octaves):
            gw = base * 2**o
            gh = max(2, round(gw * H / W)) + 1
            self.grids.append(rng.random((gh, gw)).astype(np.float32))

    def __call__(self, u, v):
        v = np.clip(v, 0, 1)
        total = np.zeros(u.shape, np.float32)
        amp = norm = 0.0
        amp = 1.0
        for g in self.grids:
            gh, gw = g.shape
            x = (u % 1.0) * gw
            y = v * (gh - 1)
            x0 = np.floor(x).astype(np.int32)
            y0 = np.minimum(np.floor(y).astype(np.int32), gh - 2)
            fx, fy = _smoothstep(x - x0), _smoothstep(y - y0)
            x0 %= gw
            x1 = (x0 + 1) % gw
            top = g[y0, x0] * (1 - fx) + g[y0, x1] * fx
            bot = g[y0 + 1, x0] * (1 - fx) + g[y0 + 1, x1] * fx
            total += amp * (top * (1 - fy) + bot * fy)
            norm += amp
            amp *= 0.5
        return total / norm


def _shift(a, dy, dx):
    """Value of the neighbour at (y+dy, x+dx): wraps east-west, clamps at the poles."""
    out = np.roll(a, -dx, axis=1)
    if dy:
        p = np.pad(out, ((1, 1), (0, 0)), mode="edge")
        out = p[1 + dy : 1 + dy + a.shape[0]]
    return out


def _blur(a, passes):
    a = a.astype(np.float32)
    for _ in range(passes):
        a = (2 * a + _shift(a, 0, 1) + _shift(a, 0, -1) + _shift(a, 1, 0) + _shift(a, -1, 0)) / 6
    return a


def _regional(a, factor=10, passes=4):
    """Cheap wide blur: average into blocks, blur, and scale back up."""
    small = a.reshape(H // factor, factor, W // factor, factor).mean(axis=(1, 3))
    small = _blur(small, passes)
    return _blur(np.repeat(np.repeat(small, factor, 0), factor, 1), factor)


def _any_neighbor(mask):
    out = np.zeros_like(mask)
    for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
        out |= _shift(mask, dy, dx)
    return out


def _wrap_dist(ys, xs, y, x):
    dx = np.abs(xs - x)
    dx = np.minimum(dx, W - dx)
    return np.sqrt(dx * dx + (ys - y) ** 2)


# ------------------------------------------------------------------ generation


def generate(seed: int) -> Planet:
    import history  # local imports: these modules build on planet.py
    import lore

    rng = np.random.default_rng(seed)
    ys, xs = np.mgrid[0:H, 0:W].astype(np.float32)
    u, v = (xs + 0.5) / W, (ys + 0.5) / H
    lat = v * 2 - 1

    # Terrain: domain-warped fractal noise, with ridged mountain chains on the high ground.
    wu = u + (_Noise(rng, 2, 4)(u, v) - 0.5) * 0.5
    wv = v + (_Noise(rng, 2, 4)(u, v) - 0.5) * 0.35
    h = _normalize(_Noise(rng, 3, 7)(wu, wv))
    ridge = 1 - np.abs(_normalize(_Noise(rng, 4, 5)(wu, wv)) * 2 - 1)
    h = _normalize(h + 0.35 * ridge**6 * _smoothstep(np.clip((h - 0.45) / 0.3, 0, 1)))

    sea = float(np.quantile(h, rng.uniform(0.56, 0.68)))
    land = h > sea
    peak_m = rng.uniform(4200, 7800)
    elev = np.where(
        land,
        np.clip((h - sea) / (h.max() - sea), 0, 1) ** 1.8 * peak_m,
        -(np.clip((sea - h) / (sea - h.min()), 0, 1) ** 0.8) * 6500,
    ).astype(np.float32)

    # Climate: cold poles and peaks, wet coasts, dry subtropical belts.
    ocean_near = _regional((~land).astype(np.float32))
    temp = 31 - 50 * np.abs(lat) ** 1.5 - np.maximum(elev, 0) * 0.0045
    temp += (_normalize(_Noise(rng, 4, 5)(wu, wv)) - 0.5) * 16
    moist = _normalize(_Noise(rng, 4, 5)(wu, wv)) * 0.75 + ocean_near * 0.5
    moist -= 0.3 * np.exp(-(((np.abs(lat) - 0.33) / 0.12) ** 2))
    moist = np.clip(moist, 0, 1)

    land_biome = np.select(
        [
            temp < -9,
            elev > 0.52 * peak_m,
            temp < 1,
            (temp < 8) & (moist > 0.35),
            temp < 8,
            (temp < 20) & (moist < 0.18),
            (temp < 20) & (moist < 0.42),
            temp < 20,
            moist < 0.25,
            moist < 0.5,
        ],
        [ICE, MOUNTAIN, TUNDRA, TAIGA, TUNDRA, DESERT, GRASSLAND, FOREST, DESERT, SAVANNA],
        RAINFOREST,
    )
    biome = np.where(land, land_biome, np.where(temp < -8, SEA_ICE, OCEAN)).astype(np.uint8)

    # Relief shading, lit from the north-west, plus a little grain.
    e = np.maximum(elev, 0)
    gx = _shift(e, 0, 1) - _shift(e, 0, -1)
    gy = _shift(e, 1, 0) - _shift(e, -1, 0)
    shade = np.clip(1 + (gx + gy) * 0.0011, 0.55, 1.45)
    grain = 1 + (rng.random((H, W)).astype(np.float32) - 0.5) * 0.07
    shade = np.where(biome == ICE, 1 + (shade - 1) * 0.35, shade)
    land_rgb = BIOME_COLORS[biome] * (shade * grain)[..., None]

    flow, rivers = _make_rivers(rng, h, land, elev, moist, biome, peak_m)

    near_river = _blur((flow > 0).astype(np.float32), 3) > 0.03
    coast = land & _any_neighbor(~land)
    near_coast = _blur(coast.astype(np.float32), 3) > 0.03
    habit = HABITABILITY[biome] * land * (1 + 0.6 * near_river + 0.3 * near_coast)

    kingdom_map, kingdoms = _make_kingdoms(rng, habit, land, biome, coast, u, v, ys, xs)
    towns = _make_towns(rng, habit, land, biome, flow, kingdom_map, kingdoms, ys, xs)

    peak = np.unravel_index(int(np.argmax(elev)), elev.shape)
    planet = Planet(
        seed=seed, h=h, sea=sea, land=land, elev=elev, temp=temp, moist=moist, biome=biome,
        flow=flow, kingdom_map=kingdom_map, land_rgb=land_rgb, kingdoms=kingdoms,
        towns=towns, rivers=rivers, peak=(int(peak[0]), int(peak[1])),
    )
    lore.populate(planet)
    planet.history = history.build(planet)
    return planet


def _make_rivers(rng, h, land, elev, moist, biome, peak_m):
    flow = np.zeros((H, W), np.int32)
    downstream: dict[tuple[int, int], tuple[int, int]] = {}
    rivers: list[River] = []
    sources = np.argwhere(land & (elev > 0.18 * peak_m) & (moist > 0.4) & (biome != ICE))
    if len(sources) == 0:
        return flow, rivers
    picks = rng.choice(len(sources), min(len(sources), int(rng.integers(45, 75))), replace=False)

    for y, x in sources[picks]:
        y, x = int(y), int(x)
        if flow[y, x]:
            continue
        path, seen, end = [(y, x)], {(y, x)}, None
        for _ in range(900):
            best, best_h = None, np.inf
            for dy, dx in NEIGH8:
                ny, nx = y + dy, (x + dx) % W
                if 0 <= ny < H and (ny, nx) not in seen and h[ny, nx] < best_h:
                    best, best_h = (ny, nx), h[ny, nx]
            if best is None:
                break
            y, x = best
            seen.add(best)
            if not land[y, x]:
                end = "sea"
                break
            path.append(best)
            if flow[y, x]:
                end = "merge"
                break
        if end is None or len(path) < 10:
            continue

        for a, b in zip(path, path[1:]):
            downstream[a] = b
        for cell in path[:-1] if end == "merge" else path:
            flow[cell] += 1
        if end == "merge":  # swell everything downstream of the confluence
            cell = path[-1]
            while cell is not None:
                flow[cell] += 1
                cell = downstream.get(cell)
        rivers.append(River(cells=path, reaches_sea=end == "sea"))
    return flow, rivers


KINGDOM_COLORS = [
    (214, 82, 72), (72, 132, 214), (232, 180, 64), (120, 190, 96), (170, 100, 200),
    (60, 190, 180), (230, 130, 60), (210, 110, 160), (150, 160, 80), (100, 110, 220),
]


def _make_kingdoms(rng, habit, land, biome, coast, u, v, ys, xs):
    target = int(rng.integers(6, 11))
    weights = (habit**2).ravel()
    weights /= weights.sum()
    seats: list[tuple[int, int]] = []
    for i in rng.choice(H * W, size=800, p=weights):
        y, x = divmod(int(i), W)
        if all(_wrap_dist(np.float32(y), np.float32(x), sy, sx) > 55 for sy, sx in seats):
            seats.append((y, x))
            if len(seats) == target:
                break

    # Voronoi on a wobbly metric: organic borders, some realms reaching further than others.
    # Measure distance in warped space so borders meander instead of running straight.
    wy = ys + (_normalize(_Noise(rng, 6, 5)(u, v)) - 0.5) * 60
    wx = xs + (_normalize(_Noise(rng, 6, 5)(u, v)) - 0.5) * 60
    reach = rng.uniform(0.75, 1.3, len(seats))
    dists = np.stack([_wrap_dist(wy, wx, sy, sx) / r for (sy, sx), r in zip(seats, reach)])
    label = np.argmin(dists, axis=0)
    claimed = land & (biome != ICE) & (dists.min(axis=0) < 95)
    kmap = np.where(claimed, label, -1).astype(np.int16)

    # Drop realms too small to matter and renumber the rest.
    kept = [k for k in range(len(seats)) if (kmap == k).sum() >= 150]
    remap = np.full(len(seats) + 1, -1, np.int16)
    for new, old in enumerate(kept):
        remap[old] = new
    kmap = remap[kmap]  # index -1 hits the trailing -1

    colors = list(KINGDOM_COLORS)
    rng.shuffle(colors)
    kingdoms = []
    for new, old in enumerate(kept):
        mask = kmap == new
        cells = int(mask.sum())
        share = np.bincount(biome[mask], minlength=len(BIOME_NAMES)) / cells
        ang = xs[mask] / W * 2 * np.pi  # circular mean handles realms straddling the seam
        cx = (np.arctan2(np.sin(ang).mean(), np.cos(ang).mean()) / (2 * np.pi) * W) % W
        seat = seats[old]
        if kmap[seat] != new:  # warped borders can leave a seat inside a neighbour: move it to the nearest own cell
            my, mx = np.nonzero(mask)
            i = int(np.argmin(_wrap_dist(my, mx, *seat)))
            seat = (int(my[i]), int(mx[i]))
        kingdoms.append(
            Kingdom(
                id=new, seat=seat, color=tuple(colors[new % len(colors)]), cells=cells,
                biome_share={b: float(s) for b, s in enumerate(share) if s > 0.02},
                coastal=bool((mask & coast).sum() > 20), label_pos=(float(ys[mask].mean()), float(cx)),
            )
        )

    for a, b in ((kmap, _shift(kmap, 0, 1)), (kmap, _shift(kmap, 1, 0))):
        edge = (a >= 0) & (b >= 0) & (a != b)
        for p, q in set(zip(a[edge].tolist(), b[edge].tolist())):
            kingdoms[p].neighbors.add(q)
            kingdoms[q].neighbors.add(p)
    return kmap, kingdoms


def _make_towns(rng, habit, land, biome, flow, kmap, kingdoms, ys, xs):
    towns: list[Town] = []

    def add(y, x, kind):
        window = [((y + dy) % H, (x + dx) % W) for dy in range(-2, 3) for dx in range(-2, 3)]
        towns.append(
            Town(
                name="", y=y, x=x, kingdom=int(kmap[y, x]), kind=kind,
                coastal=any(not land[c] for c in window),
                river=any(flow[c] > 0 for c in window[6:19]),
                biome=int(biome[y, x]),
            )
        )

    for k in kingdoms:
        add(*k.seat, "capital")

    score = habit + rng.random((H, W)).astype(np.float32) * 0.25
    score[~land | (biome == ICE)] = -1
    wanted = len(towns) + int(np.clip(land.sum() // 1700, 14, 34))
    for i in np.argsort(score.ravel())[::-1][:8000]:
        y, x = divmod(int(i), W)
        if score[y, x] < 0.2:
            break
        if all(_wrap_dist(np.float32(y), np.float32(x), t.y, t.x) > 17 for t in towns):
            rank = len(towns) / wanted
            add(y, x, "city" if rank < 0.45 else "town" if rank < 0.8 else "village")
            if len(towns) >= wanted:
                break
    return towns


# ------------------------------------------------------------------- rendering


def render(
    p: Planet, mode: str = "terrain", selected: int | None = None, sea_rise: float = 0.0, year: float | None = None
) -> np.ndarray:
    """RGBA frame (H, W, 4) of the world in `year` (default: the present).

    `sea_rise` > 0 floods the world (used for the creation animation).
    """
    sea_now = p.sea + sea_rise
    land_now = p.h > sea_now
    depth = np.clip((sea_now - p.h) / (sea_now - p.h.min()), 0, 1) ** 0.6
    ocean = SHALLOW * (1 - depth)[..., None] + DEEP * depth[..., None]
    shore = ~land_now & _any_neighbor(land_now)
    ocean[shore] += 22
    ice = ~land_now & (p.temp < -8)
    ocean[ice] = BIOME_COLORS[SEA_ICE] * (0.92 + 0.08 * depth[ice])[:, None]
    rgb = np.where(land_now[..., None], p.land_rgb, ocean)

    if sea_rise == 0:
        year = p.year if year is None else year
        km = p.history.owners_at(p, year) if p.history else p.kingdom_map
        claimed = km >= 0
        if mode == "political":
            colors = p.realm_colors()
            gray = rgb.mean(axis=2, keepdims=True)
            rgb = np.where(
                claimed[..., None],
                rgb * 0.4 + colors[km] * 0.6,
                np.where(p.land[..., None], (rgb * 0.35 + gray * 0.65) * 0.8, rgb),
            )

        river = (p.flow > 0) & p.land
        wide = (p.flow >= 6) & p.land
        wide |= _shift(wide, 0, -1) & p.land
        rgb[river | wide] = RIVER

        border = claimed & ((km != _shift(km, 0, 1)) | (km != _shift(km, 1, 0)))
        rgb[border] *= 0.35 if mode == "political" else 0.62

        if p.history:  # freshly conquered land glows, fading over 30 years
            since = year - p.history.changed_at
            glow = np.clip(1 - since / 30, 0, 1) * (since >= 0) * p.land
            if glow.any():
                g = (glow * 0.6)[..., None]
                rgb = rgb * (1 - g) + GLOW * g

        if p.history:  # war zones: red diagonal hatching
            war = p.history.contested_at(year) & claimed
            ys, xs = np.nonzero(war)
            hatch = ((xs + ys) // 3) % 2 == 0
            rgb[ys[hatch], xs[hatch]] = rgb[ys[hatch], xs[hatch]] * 0.45 + WAR * 0.55

        if selected is not None:
            sel = km == selected
            rgb[p.land & ~sel] *= 0.72
            outline = sel & ~(_shift(sel, 0, 1) & _shift(sel, 0, -1) & _shift(sel, 1, 0) & _shift(sel, -1, 0))
            rgb[outline] = SELECT

    frame = np.empty((H, W, 4), np.uint8)
    frame[..., :3] = np.clip(rgb, 0, 255)
    frame[..., 3] = 255
    return frame

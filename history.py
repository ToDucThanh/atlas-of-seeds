"""Who ruled which land, in any year.

Every cell records the year its present owner took it (`claim_now`) and, where
land changed hands, who held it before (`prior_owner`, from `prior_claim`).
That is enough to replay the whole map:

  * realms grow outward from their capitals after they are founded;
  * each rivalry's war moves a strip of border land from loser to winner;
  * ongoing wars leave a contested zone;
  * one fallen realm, absorbed long ago by the largest power, reappears in the past.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

import numpy as np

import lore
import planet as pl

LOST_COLORS = [(176, 138, 96), (118, 168, 150), (150, 128, 176)]
NEVER = np.float32(np.inf)


@dataclass
class LostRealm:
    id: int  # continues after the living realms' ids
    name: str
    title: str
    color: tuple[int, int, int]
    founded: int
    fell: int
    conqueror: int
    capital: str
    last_ruler: str
    label_pos: tuple[float, float]
    reigns: list = field(default_factory=list)  # lore.Reign


@dataclass
class Marker:
    """Something that happened at a place: shown on the map while `start <= year < end`."""

    kind: str  # battle | war | sack | crown
    y: float
    x: float
    start: int
    end: int
    caption: str


@dataclass
class History:
    start: int
    end: int
    claim_now: np.ndarray  # year the present owner took each cell (inf = never)
    prior_owner: np.ndarray  # -1 = nobody before
    prior_claim: np.ndarray
    contested_since: np.ndarray  # year a war zone opened (inf = never)
    lost: list[LostRealm] = field(default_factory=list)
    events: list[tuple[int, str]] = field(default_factory=list)  # world chronicle, sorted
    markers: list[Marker] = field(default_factory=list)

    @property
    def changed_at(self) -> np.ndarray:
        """Year each cell changed hands by conquest (inf where it never did)."""
        return np.where(self.prior_owner >= 0, self.claim_now, NEVER)

    def owners_at(self, p: pl.Planet, year: float) -> np.ndarray:
        own = np.where(year >= self.claim_now, p.kingdom_map, -1).astype(np.int16)
        before = (year < self.claim_now) & (self.prior_owner >= 0) & (year >= self.prior_claim)
        own[before] = self.prior_owner[before]
        return own

    def contested_at(self, year: float) -> np.ndarray:
        return self.contested_since <= year

    def exists(self, p: pl.Planet, realm: int, year: float) -> bool:
        if realm < len(p.kingdoms):
            return p.kingdoms[realm].lore["founded"] <= year
        lost = self.lost[realm - len(p.kingdoms)]
        return lost.founded <= year < lost.fell

    def latest_event(self, year: float) -> tuple[int, str] | None:
        past = [e for e in self.events if e[0] <= year]
        return past[-1] if past else None

    def busy(self, year: float, ahead: float = 12) -> bool:
        """True near a dramatic moment, so playback can slow down for it."""
        return any(m.kind in ("battle", "sack") and m.start - ahead <= year < m.start + 18 for m in self.markers)


def _mid(title: str) -> str:
    """'The Kingdom of X' -> 'the Kingdom of X', for use mid-sentence."""
    return "the" + title[3:] if title.startswith("The ") else title


def _anchor(mask: np.ndarray, ys, xs) -> tuple[float, float]:
    """A cell inside `mask` near its (east-west wrapping) centre."""
    ang = xs[mask] / pl.W * 2 * np.pi
    cx = (np.arctan2(np.sin(ang).mean(), np.cos(ang).mean()) / (2 * np.pi) * pl.W) % pl.W
    cy = ys[mask].mean()
    cand_y, cand_x = ys[mask], xs[mask]
    i = int(np.argmin((cand_y - cy) ** 2 + (np.minimum(np.abs(cand_x - cx), pl.W - np.abs(cand_x - cx))) ** 2))
    return float(cand_y[i]), float(cand_x[i])


def _grow(mask: np.ndarray, within: np.ndarray, steps: int) -> np.ndarray:
    out = mask.copy()
    for _ in range(steps):
        out |= pl._any_neighbor(out) & within
    return out


def build(p: pl.Planet) -> History:
    rng = np.random.default_rng(p.seed + 1282)
    text_rng = random.Random(p.seed * 31 + 7)
    ys, xs = np.mgrid[0 : pl.H, 0 : pl.W].astype(np.float32)
    u, v = (xs + 0.5) / pl.W, (ys + 0.5) / pl.H
    wobble = (pl._normalize(pl._Noise(rng, 6, 4)(u, v)) - 0.5) * 30

    km = p.kingdom_map
    claim_now = np.full(km.shape, NEVER, np.float32)
    prior_owner = np.full(km.shape, -1, np.int16)
    prior_claim = np.full(km.shape, NEVER, np.float32)
    contested = np.full(km.shape, NEVER, np.float32)
    events: list[tuple[int, str]] = []
    markers: list[Marker] = []

    # 1. Growth: each realm spreads outward from its capital over a few centuries.
    def spread(seat, mask, founded, span):
        d = pl._wrap_dist(ys, xs, *seat) + wobble
        d = np.maximum(d, 0)
        dmax = max(float(d[mask].max()), 1.0)
        return founded + span * (d / dmax) ** 1.3

    growth = {}
    for k in p.kingdoms:
        mask = km == k.id
        founded = k.lore["founded"]
        growth[k.id] = spread(k.seat, mask, founded, rng.uniform(120, 380))
        claim_now[mask] = growth[k.id][mask]
        events.append((founded, f"{k.title} is founded at {k.lore['capital']}."))
        for when, text in k.lore["usurpations"]:
            events.append((when, f"{k.name}: {text}"))
            markers.append(Marker("crown", k.seat[0], k.seat[1], when, when + 25, f"Usurper · {when}"))

    # 2. A fallen realm: the far reaches of the largest power were once a kingdom of their own.
    big = max(p.kingdoms, key=lambda k: k.cells)
    lost: list[LostRealm] = []
    big_mask = km == big.id
    if big.cells >= 900:
        d_home = pl._wrap_dist(ys, xs, *big.seat)
        far = np.where(big_mask & (pl.HABITABILITY[p.biome] > 0.3), d_home, -1)
        seat = np.unravel_index(int(np.argmax(far)), far.shape)
        region = big_mask & (pl._wrap_dist(ys, xs, *seat) + wobble < d_home * 0.9)
        if 150 <= region.sum() <= 0.6 * big.cells:
            lang = lore.Language(text_rng)
            used = {k.name for k in p.kingdoms} | {t.name for t in p.towns}
            name = lang.place(used)
            founded = int(rng.integers(60, max(61, big.lore["founded"] + 120)))
            fell = int(rng.integers(max(founded, big.lore["founded"]) + 120, p.year - 60))
            towns = [t for t in p.towns if region[t.y, t.x]]
            capital = (
                min(towns, key=lambda t: (t.y - seat[0]) ** 2 + (t.x - seat[1]) ** 2).name
                if towns
                else lang.place(used)  # the old capital did not survive
            )
            reigns = lore.make_dynasty(lang, text_rng, "Kingdom", founded, fell,
                                       last_fate=f"fell when {capital} was taken")
            reigns[-1].epithet = "the Last"
            realm = LostRealm(
                id=len(p.kingdoms), name=name, title=f"The Kingdom of {name}",
                color=LOST_COLORS[int(rng.integers(len(LOST_COLORS)))], founded=founded, fell=fell,
                conqueror=big.id, capital=capital, last_ruler=reigns[-1].display,
                label_pos=_anchor(region, ys, xs), reigns=reigns,
            )
            cap_town = next((t for t in towns if t.name == capital), None)
            sack_y, sack_x = (cap_town.y, cap_town.x) if cap_town else _anchor(region, ys, xs)
            markers.append(Marker("sack", sack_y, sack_x, fell - 2, fell + 45, f"Fall of {capital} · {fell}"))
            lost.append(realm)
            prior_owner[region] = realm.id
            prior_claim[region] = np.minimum(spread(seat, region, founded, rng.uniform(80, 200))[region], fell - 1)
            claim_now[region] = np.maximum(claim_now[region], fell)
            events.append((founded, f"{realm.title} rises at {capital}."))
            events.append((fell, f"{big.title} conquers {_mid(realm.title)}; {realm.last_ruler} falls at {capital}."))
            big.lore["history"] = sorted(
                big.lore["history"] + [(fell, f"Conquest of {_mid(realm.title)}. {capital} is taken, and its crown with it.")]
            )

    # 3. Wars between neighbours move borders, or leave them contested.
    for (a, b), ev in p.lore["pair_events"].items():
        year, war = ev["year"], ev["war"]
        name_a, name_b = p.kingdoms[a].name, p.kingdoms[b].name
        if ev["relation"] == "rivals":
            winner, loser = (a, b) if ev["winner"] == a else (b, a)
            strip = _grow(km == loser, p.land, 10) & (km == winner)
            prior_owner[strip] = loser
            prior_claim[strip] = np.minimum(growth[loser][strip], year - 5)
            claim_now[strip] = year
            events.append((year, f"{p.kingdoms[winner].name} wins the {war} and takes land from {p.kingdoms[loser].name}."))
            if strip.any():
                markers.append(Marker("battle", *_anchor(strip, ys, xs), year - 4, year + 40, f"{war} · {year}"))
        elif ev["relation"] == "at war":
            zone = (_grow(km == a, p.land, 5) & (km == b)) | (_grow(km == b, p.land, 5) & (km == a))
            contested[zone] = np.minimum(contested[zone], year)
            events.append((year, f"The {war} between {name_a} and {name_b} begins."))
            if zone.any():
                markers.append(Marker("war", *_anchor(zone, ys, xs), year, p.year + 1, f"{war} · since {year}"))
        elif ev["relation"] == "allied":
            events.append((year, f"{name_a} and {name_b} ally by royal marriage."))

    start = min([k.lore["founded"] for k in p.kingdoms] + [r.founded for r in lost]) - 25
    return History(start=start, end=p.year, claim_now=claim_now, prior_owner=prior_owner,
                   prior_claim=prior_claim, contested_since=contested, lost=lost, events=sorted(events),
                   markers=markers)

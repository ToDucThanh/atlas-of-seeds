"""Names and history for a generated planet.

Each realm gets its own little language (a syllable inventory), so names within
a realm sound related. Government, trade, faith and history are derived from
the realm's actual geography and neighbours, not rolled in isolation.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from itertools import pairwise

import planet as pl

ONSETS = ["b", "br", "c", "ch", "d", "dr", "f", "g", "gr", "h", "k", "kh", "l", "m", "n",
          "p", "r", "s", "sh", "st", "t", "th", "tr", "v", "w", "z", "y", "j", "sk", "vr"]
VOWELS = ["a", "e", "i", "o", "u", "a", "e", "o", "ae", "ai", "au", "ei", "ia", "io", "ou", "y"]
CODAS = ["n", "r", "l", "s", "th", "k", "m", "nd", "rn", "sk", "x", "sh", "ng", "st", "ld", "rk"]
ENDINGS = ["ia", "or", "ar", "heim", "mark", "dor", "wyn", "ra", "is", "eth", "an", "gard",
           "ion", "ossa", "ul", "ane", "ost", "ir", "enna", "vik"]


class Language:
    def __init__(self, rng: random.Random):
        self.rng = rng
        self.onsets = rng.sample(ONSETS, rng.randint(6, 11))
        self.vowels = rng.sample(sorted(set(VOWELS)), rng.randint(3, 6))
        self.codas = [""] * 3 + rng.sample(CODAS, rng.randint(2, 6))
        self.endings = rng.sample(ENDINGS, 3)

    def _syllable(self, first: bool, last: bool) -> str:
        r = self.rng
        onset = "" if first and r.random() < 0.2 else r.choice(self.onsets)
        # Only soft consonants between syllables, so names don't pile up clusters like "xrd".
        coda = r.choice(self.codas) if last else r.choice(["", "", "n", "r", "l"])
        return onset + r.choice(self.vowels) + coda

    def word(self, lo=1, hi=3) -> str:
        n = self.rng.randint(lo, hi)
        w = "".join(self._syllable(i == 0, i == n - 1) for i in range(n))
        return w[:10].capitalize()

    def place(self, used: set[str]) -> str:
        for _ in range(50):
            w = self.word(1, 2)
            if self.rng.random() < 0.45:
                ending = self.rng.choice(self.endings)
                stem = w.rstrip("aeiouy")
                if ending[0] not in "aeiouy":  # consonant ending: trim the stem's final consonants
                    stem = stem.rstrip("bcdfghjklmnpqrstvwxz") + self.rng.choice(self.vowels)
                w = stem + ending
            if 3 <= len(w) <= 11 and w not in used:
                used.add(w)
                return w
        return self.word(2, 3)

    def person(self) -> str:
        return self.word(2, 2)


GOVERNMENTS = {
    "Kingdom": ("King", "Queen"),
    "Empire": ("Emperor", "Empress"),
    "Principality": ("Prince", "Princess"),
    "Republic": ("First Consul", "First Consul"),
    "Merchant Republic": ("Doge", "Dogaressa"),
    "Theocracy": ("High Priest", "High Priestess"),
    "Khanate": ("Khan", "Khatun"),
    "Sultanate": ("Sultan", "Sultana"),
    "Jarldom": ("Jarl", "Jarl"),
    "Duchy": ("Duke", "Duchess"),
    "Confederacy": ("High Chief", "High Chief"),
    "Circle": ("Archdruid", "Archdruid"),
}
EPITHETS = ["the Bold", "the Grey", "the Twice-Crowned", "the Just", "the Younger", "Ironhand",
            "the Unbowed", "of the Long Winter", "the Quiet", "the Builder", "Oathkeeper", "the Lame"]
RESOURCES = {
    pl.GRASSLAND: ["grain", "horses", "wool"],
    pl.FOREST: ["timber", "honey", "venison"],
    pl.TAIGA: ["furs", "pine tar", "amber"],
    pl.TUNDRA: ["walrus ivory", "reindeer hides"],
    pl.DESERT: ["glass", "salt", "spices"],
    pl.SAVANNA: ["cattle", "ivory", "hides"],
    pl.RAINFOREST: ["dyes", "cacao", "rare woods"],
    pl.MOUNTAIN: ["iron", "silver", "marble"],
}
COASTAL_GOODS = ["salted fish", "pearls", "ships"]
DEITIES = {
    pl.MOUNTAIN: "the Stone Mother", pl.FOREST: "the Green Hunter", pl.DESERT: "the Burning Eye",
    pl.TAIGA: "the Wolf Under the Snow", pl.TUNDRA: "the White Silence", pl.GRASSLAND: "the Horse Lord",
    pl.SAVANNA: "the Lion of Noon", pl.RAINFOREST: "the Thousand-Eyed",
}
MOTTOS = ["Stone remembers.", "By oar and by oath.", "The river gives.", "We were here first.",
          "Ever westward.", "Salt and silver.", "No crown but the sky.", "Hold the pass.",
          "What grows, we guard.", "Fire before ice.", "Slow roots, deep roots.", "The tide returns.",
          "Nothing forgotten.", "Bend, never break.", "Our hearths, our law."]
WAR_NAMES = ["Hundred Days'", "Salt", "Winter", "Brothers'", "Ashen", "Long", "Red", "Three Rivers'", "Lantern",
             "Iron", "Harvest", "Widow's", "Crown", "Shattered Oath", "Border"]
PLAGUES = ["Grey", "Weeping", "Red", "Sleeping", "Blue"]
RELATIONS = [("allied", 3), ("trade partners", 4), ("rivals", 3), ("uneasy peace", 3), ("at war", 1)]


ELECTED = {"Republic", "Merchant Republic"}
REIGN_ENDS = [("died in old age", 8), ("died of a fever", 2), ("was murdered", 1), ("abdicated", 1),
              ("was overthrown", 0.5)]
TERM_ENDS = [("served a full term", 6), ("died in office", 1), ("was deposed", 1)]


@dataclass
class Reign:
    name: str
    title: str
    start: int
    end: int
    fate: str
    house: str = ""
    ordinal: int = 1
    epithet: str = ""

    @property
    def display(self) -> str:
        text = f"{self.title} {self.name}"
        if self.ordinal > 1:
            text += f" {roman(self.ordinal)}"
        return f"{text} {self.epithet}" if self.epithet else text


def roman(n: int) -> str:
    out = ""
    for value, digits in ((10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
        while n >= value:
            out, n = out + digits, n - value
    return out


def make_dynasty(lang: Language, rng: random.Random, gov: str, start: int, end: int,
                 wars=(), plagues=(), last_fate: str = "reigns still") -> list[Reign]:
    """Rulers from `start` to `end`. Wars and plagues may cut a reign short; an overthrow starts a new house."""
    male, female = GOVERNMENTS[gov]
    elected = gov in ELECTED
    house = "" if elected else lang.word(1, 2)
    pool = [lang.person() for _ in range(4)]
    counts: dict[tuple[str, str, str], int] = {}
    reigns: list[Reign] = []
    year = start
    while True:
        term = (5, 25) if gov == "Merchant Republic" else (4, 12)  # doges serve for life, consuls for a term
        stop = year + (rng.randint(*term) if elected else rng.randint(6, 40))
        fate = rng.choices(*zip(*(TERM_ENDS if elected else REIGN_ENDS)))[0]
        for when, what in sorted(wars) + sorted(plagues):
            if year < when < stop and rng.random() < 0.4:
                stop, fate = when, f"fell in the {what}" if "War" in what else f"died in the {what}"
                break
        last = stop >= end - 3
        name = lang.person() if elected else rng.choice(pool)  # elected leaders come from many families
        title = female if rng.random() < 0.4 else male
        counts[(house, name, title)] = counts.get((house, name, title), 0) + 1
        reigns.append(Reign(
            name=name, title=title, start=year, end=end if last else stop,
            fate=last_fate if last else fate, house=house, ordinal=1 if elected else counts[(house, name, title)],
            epithet="the Founder" if not reigns else rng.choice(EPITHETS) if rng.random() < 0.3 else "",
        ))
        if last:
            return reigns
        if fate == "was overthrown":  # a usurper founds a new house with its own names
            house, pool = lang.word(1, 2), [lang.person() for _ in range(4)]
        year = stop


def populate(p: pl.Planet):
    rng = random.Random(p.seed * 7919 + 282)
    used: set[str] = set()
    world_lang = Language(rng)
    p.name = world_lang.place(used)
    p.year = 1000 + p.seed % 1000
    langs = [Language(rng) for _ in p.kingdoms]

    for k, lang in zip(p.kingdoms, langs):
        k.name = lang.place(used)
    for t in p.towns:
        lang = langs[t.kingdom] if t.kingdom >= 0 else world_lang
        t.name = lang.place(used)

    for r in sorted(p.rivers, key=lambda r: len(r.cells), reverse=True)[:8]:
        if r.reaches_sea:
            y, x = r.cells[-1]
            owner = int(p.kingdom_map[y, x])
            r.name = (langs[owner] if owner >= 0 else world_lang).place(used)

    py, px = p.peak
    owner = int(p.kingdom_map[py, px])
    p.peak_name = f"Mount {(langs[owner] if owner >= 0 else world_lang).place(used)}"

    relations: dict[tuple[int, int], str] = {}
    for k in p.kingdoms:
        for n in k.neighbors:
            key = (min(k.id, n), max(k.id, n))
            if key not in relations:
                kinds, weights = zip(*RELATIONS)
                relations[key] = rng.choices(kinds, weights)[0]

    # One shared event per pair of neighbours, so both chronicles (and the map) agree on it.
    founded = {k.id: rng.randint(90, p.year - 380) for k in p.kingdoms}
    war_names = iter(rng.sample(WAR_NAMES, len(WAR_NAMES)))  # each war gets its own name
    pair_events = {}
    for (a, b), rel in sorted(relations.items()):
        is_war = rel in ("rivals", "at war")
        pair_events[(a, b)] = {
            "relation": rel,
            "year": rng.randint(max(founded[a], founded[b]) + 40, p.year - 15),
            "war": f"{next(war_names, 'Forgotten')} War" if is_war else "",
            "winner": rng.choice((a, b)),
        }

    for k, lang in zip(p.kingdoms, langs):
        _describe_kingdom(p, k, lang, rng, founded[k.id], pair_events)

    land = p.land.sum()
    p.lore = {
        "land_pct": land / p.land.size * 100,
        "peak_m": float(p.elev[p.peak]),
        "longest_river": max((r for r in p.rivers if r.name), key=lambda r: len(r.cells), default=None),
        "pair_events": pair_events,
    }


def _describe_kingdom(p: pl.Planet, k: pl.Kingdom, lang: Language, rng: random.Random, founded: int, pair_events):
    share = k.biome_share
    dominant = max((b for b in share if b in RESOURCES), key=share.get, default=pl.GRASSLAND)
    towns = [t for t in p.towns if t.kingdom == k.id]
    capital = next(t for t in towns if t.kind == "capital")
    ports = [t for t in towns if t.coastal]
    river_towns = [t for t in towns if t.river]
    biggest = max(pk.cells for pk in p.kingdoms)

    # Government follows geography and size.
    options = ["Kingdom", "Kingdom", "Duchy", "Principality", "Theocracy", "Republic"]
    if k.cells == biggest:
        options += ["Empire"] * 4
    if len(ports) >= 2:
        options += ["Merchant Republic"] * 3
    options += {
        pl.DESERT: ["Sultanate"] * 4, pl.SAVANNA: ["Khanate", "Sultanate"], pl.GRASSLAND: ["Khanate"] * 2,
        pl.TAIGA: ["Jarldom"] * 4, pl.TUNDRA: ["Jarldom"] * 4, pl.FOREST: ["Circle", "Confederacy"],
        pl.RAINFOREST: ["Circle", "Confederacy"] * 2, pl.MOUNTAIN: ["Theocracy", "Kingdom"],
    }.get(dominant, []) * 2
    gov = rng.choice(options)
    k.title = {
        "Confederacy": f"The {k.name} Confederacy",
        "Circle": f"The Circle of {k.name}",
        "Republic": f"The Republic of {k.name}",
    }.get(gov, f"The {gov} of {k.name}")

    goods: list[str] = []
    for b in sorted(share, key=share.get, reverse=True):
        goods += RESOURCES.get(b, [])[: 2 if share[b] > 0.25 else 1]
    if k.coastal:
        goods.append(rng.choice(COASTAL_GOODS))
    goods = list(dict.fromkeys(goods))[:4]

    fertility = sum(pl.HABITABILITY[b] * s for b, s in share.items())
    population = int(k.cells * fertility * rng.uniform(900, 1600) / 1000) * 1000

    pairs = {n: pair_events[(min(k.id, n), max(k.id, n))] for n in k.neighbors}
    neighbor_rel = sorted(((n, ev["relation"]) for n, ev in pairs.items()), key=lambda nr: p.kingdoms[nr[0]].name)

    # History: every event shared with a neighbour, a few local ones, and the rulers who lived through them.
    history: list[tuple[int, str]] = []
    wars = []
    for n, ev in pairs.items():
        other, war = p.kingdoms[n].name, ev["war"]
        if ev["relation"] in ("rivals", "at war"):
            wars.append((ev["year"], war))
        text = {
            "at war": f"The {war} with {other} erupts over the border marches, and has not ended.",
            "rivals": f"Victory in the {war} against {other}; the border marches are taken."
            if ev["winner"] == k.id
            else f"Defeat in the {war}; the border marches are lost to {other}.",
            "allied": f"Alliance with {other}, sealed by a royal marriage.",
            "trade partners": f"Trade pact with {other}; caravans cross the border.",
        }.get(ev["relation"])
        if text:
            history.append((ev["year"], text))

    local: list[str] = []
    if river_towns:
        local.append(f"The great flood drowns half of {rng.choice(river_towns).name}; the dikes are built.")
    if share.get(pl.MOUNTAIN, 0) > 0.03 or dominant == pl.MOUNTAIN:
        local.append(f"Silver found beneath the high passes. {capital.name} grows rich.")
    if rng.random() < 0.6:
        local.append(f"The {rng.choice(PLAGUES)} Plague; a third of the people perish.")
    if ports:
        local.append(f"A fleet from {rng.choice(ports).name} charts the far coasts.")
    rng.shuffle(local)
    plagues = []
    for text in local[:3]:
        when = rng.randint(founded + 20, p.year - 10)
        history.append((when, text))
        if "Plague" in text:
            plagues.append((when, text.split(";")[0].removeprefix("The ")))

    reigns = make_dynasty(lang, rng, gov, founded, p.year, wars, plagues)
    ruler = reigns[-1].display
    history.append((founded, f"Founded by {reigns[0].display}, who raised the walls of {capital.name}."))
    usurpations = []
    for before, after in pairwise(reigns):
        if before.fate == "was overthrown":
            text = f"{after.display} seizes the throne from {before.display}; House {after.house} begins."
            history.append((after.start, text))
            usurpations.append((after.start, text))
    history.append((reigns[-1].start, f"{ruler} is crowned."))

    faith_word = lang.person()
    k.lore = {
        "government": gov,
        "ruler": ruler,
        "capital": capital.name,
        "founded": founded,
        "population": population,
        "exports": goods,
        "faith": f"{faith_word}, {DEITIES.get(dominant, 'the Nameless')}",
        "motto": rng.choice(MOTTOS),
        "relations": neighbor_rel,
        "history": sorted(history),
        "reigns": reigns,
        "usurpations": usurpations,
        "towns": towns,
        "dominant": dominant,
    }

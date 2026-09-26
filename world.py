"""Atlas - type a number or a word, get a world, and scrub through its history.

Replays have a soundtrack synthesised for each world (soundtrack.py), each panel charts how the realms
rose and fell, and cinema mode fills the screen with the globe, replaying one world after another.

Run with:  uv run python world.py [seed] [year] [realm] [--globe] [--replay]
e.g.       uv run python world.py 282 1184 Aar     (opens that world in 1184, with Aar selected)
           uv run python world.py 282 --globe      (opens on the spinning globe)
           uv run python world.py 282 --globe --replay   (replays history, the camera following each war)
"""

import asyncio
import math
import random
import sys
import time

import flet as ft
import flet_charts as fch
import numpy as np

import globe as gl
import planet as pl
import soundtrack as st

SCALE = 2  # screen pixels per map cell
MAP_W, MAP_H = pl.W * SCALE, pl.H * SCALE
PANEL_W = 360
DEFAULT_SEED = "282"
REVEAL_FRAMES = 28
PLAY_SECONDS = 12  # a full replay of history, before slow motion around battles
PLAY_FPS = 30
SLOW_MOTION = 0.22
SPIN_FPS = 60
AUTO_SPIN = 0.16  # radians per second when idle: a full day in about 40 seconds
MAX_SPIN = 8.0  # cap on flick speed, radians per second
HOVER_HOLD = 4.0  # seconds the globe stays still after the mouse last moved over it
FOCUS_ZOOM = 1.45  # how far the replay camera leans in on an event
FOCUS_TILT = 0.9  # the camera never tilts past this latitude (radians) to face an event
CAMERA_EASE = 2.6  # fraction of the remaining turn the camera covers per second
TRAVEL_RATE = 0.03  # playback speed while the camera swings to the next event
GLOBE_SLOW_MOTION = 0.1  # playback speed while the camera watches an event
CINEMA_HOLD = 5.0  # seconds cinema mode lingers on a finished history before moving to another world
RISE_H = 150  # height of the rise-and-fall chart
PANEL_BG = (0x14, 0x18, 0x24)
MUTED_KEY = "atlas.muted"
MARKER_STYLE = {  # emoji, caption background (#AARRGGBB) (emoji that render in colour everywhere; ⚔ does not)
    "battle": ("💥", "#d8101318"),
    "war": ("🏹", "#e07a1d17"),
    "sack": ("🔥", "#e06b2a0a"),
    "crown": ("👑", "#e04a3a0a"),
}

SIGIL_ICONS = {
    pl.MOUNTAIN: ft.Icons.LANDSCAPE, pl.FOREST: ft.Icons.PARK, pl.TAIGA: ft.Icons.FOREST,
    pl.TUNDRA: ft.Icons.AC_UNIT, pl.DESERT: ft.Icons.WB_SUNNY, pl.SAVANNA: ft.Icons.PETS,
    pl.RAINFOREST: ft.Icons.SPA, pl.GRASSLAND: ft.Icons.CASTLE,
}
RELATION_COLORS = {
    "allied": "#6cc38a", "trade partners": "#5bb8c9", "rivals": "#e0a458",
    "uneasy peace": "#b9a86b", "at war": "#e2665c",
}
LABEL_SHADOW = ft.BoxShadow(blur_radius=6, color="#000000")
BACKGROUND_TASKS: set[asyncio.Task] = set()


def hex_color(rgb) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(int(c) for c in rgb))


def sigil(color, icon, size: int = 28) -> ft.Control:
    return ft.Container(
        width=size, height=size, border_radius=size, bgcolor=hex_color(color),
        alignment=ft.Alignment.CENTER, border=ft.Border.all(2, "#30ffffff"),
        content=ft.Icon(icon, size=size * 0.55, color="#eeffffff"),
    )


def kingdom_sigil(k: pl.Kingdom, size: int = 28) -> ft.Control:
    return sigil(k.color, SIGIL_ICONS.get(k.lore["dominant"], ft.Icons.SHIELD), size)


def lost_sigil(r, size: int = 28) -> ft.Control:
    return sigil(r.color, ft.Icons.HISTORY_EDU, size)


def event_badge(m) -> ft.Control:
    """A battle, war or sack marker: its emoji on a dark disc (so thin glyphs like 🏹 read over the red
    war hatching) above a caption pill. 46 px tall, with the disc centred 15 px below the top."""
    emoji, bg = MARKER_STYLE[m.kind]
    return ft.Column(
        [ft.Container(width=30, height=30, border_radius=15, bgcolor="#c80b0d13", border=ft.Border.all(1, "#40ffffff"),
                      alignment=ft.Alignment.CENTER, content=ft.Text(emoji, size=17)),
         ft.Container(ft.Text(m.caption, size=10, color="#fff4dc", weight=ft.FontWeight.W_600),
                      bgcolor=bg, border_radius=8, padding=ft.Padding.symmetric(horizontal=6, vertical=1))],
        spacing=0, horizontal_alignment=ft.CrossAxisAlignment.CENTER, tight=True,
    )


def format_population(n: int) -> str:
    return f"{n / 1e6:.1f} million" if n >= 1_000_000 else f"{n:,}"


async def main(page: ft.Page):
    page.title = "Atlas"
    page.theme_mode = ft.ThemeMode.DARK
    page.theme = ft.Theme(color_scheme_seed="#5b8fd6")
    page.bgcolor = "#0b0d13"
    page.padding = 16
    if not page.web:
        page.window.width = MAP_W + PANEL_W + 72
        page.window.height = MAP_H + 262

    world: pl.Planet | None = None
    mode = "terrain"
    selected: int | None = None
    generation = 0  # bumps on every new world so a stale animation stops
    play_run = 0  # bumps on every play/pause so only the latest playback loop keeps going
    hovered_cell: tuple[int, int] | None = None
    year = 0.0
    owners = None  # realm id per cell in `year`
    revealing = False
    playing = False
    redraw = asyncio.Event()
    label_rules: list[tuple[ft.Control, tuple]] = []  # (control, visibility rule)
    chronicle_rows: list[tuple[int, ft.Control]] = []  # rows of the open realm's chronicle
    marker_controls: list[tuple[ft.Control, object]] = []  # (control, history.Marker)
    dynasty: dict = {}  # live parts of the open realm's ruler card and dynasty list
    view = "map"  # map | globe
    globe = gl.Globe(MAP_W, MAP_H)
    spin = 0.0  # radians the globe has turned east
    spin_velocity = AUTO_SPIN
    dragging = False
    last_hover = 0.0  # when the mouse last moved over the map view
    focus = None  # the history.Marker the replay camera is facing
    camera_travelling = False  # True while the camera swings toward `focus`
    home_tilt: float | None = None  # the viewer's tilt to return to once the camera lets go
    drag_last: tuple[float, float, float] | None = None  # x, y, time of the previous drag update
    flat = lights = None  # the flat frame and town lights for `flat_key`, reused while only the globe turns
    flat_key = None
    globe_items: list[tuple] = []  # (control, dx, dy, w, h, rule), most important first
    globe_anchor = (np.zeros(0), np.zeros(0))  # map cell (ys, xs) each globe label is pinned to
    territory = None  # (years, percent[realm, sample]) of land each realm held, for the rise-and-fall chart
    rise: dict = {}  # live parts of the open panel's rise-and-fall chart
    cinema = False  # full-screen globe, replaying world after world
    cinema_run = 0  # bumps on every enter/leave so only the latest cinema loop keeps going
    before_cinema = "map"  # the view to return to
    soundtrack = st.Soundtrack(page, wants_drone=lambda: playing or cinema)
    wakelock = ft.Wakelock()
    prefs = ft.SharedPreferences()

    # --------------------------------------------------------------- map view

    raw_image = ft.RawImage(width=MAP_W, height=MAP_H, fit=ft.BoxFit.FILL, filter_quality=ft.FilterQuality.NONE)
    labels = ft.Stack(width=MAP_W, height=MAP_H, opacity=0, animate_opacity=700)
    globe_labels = ft.Stack(width=MAP_W, height=MAP_H, opacity=0, animate_opacity=700, visible=False)
    status = ft.Text("", size=12, color=ft.Colors.ON_SURFACE_VARIANT)
    progress = ft.ProgressRing(width=16, height=16, stroke_width=2, visible=False)

    def label_text(text, size, color="#f4efe4", weight=ft.FontWeight.W_500, italic=False, spacing=0.0):
        return ft.Text(
            text, size=size, color=color, weight=weight, italic=italic, no_wrap=True,
            style=ft.TextStyle(shadow=LABEL_SHADOW, letter_spacing=spacing),
        )

    def build_labels(p: pl.Planet):
        """Place map labels by priority, skipping any name that would overlap one already placed.

        Every label also gets a visibility rule, so towns and realms appear only once they exist.
        """
        taken: list[tuple[float, float, float, float]] = []
        markers: list[ft.Control] = []
        names: list[ft.Control] = []
        label_rules.clear()

        def free(left, top, w, h):
            right, bottom = left + w, top + h
            if left < 0 or top < 0 or right > MAP_W or bottom > MAP_H:
                return False
            return all(right <= l or left >= r or bottom <= t or top >= b for l, t, r, b in taken)

        def put(left, top, w, h, content, rule, align=ft.Alignment.CENTER, force=False):
            if not (force or free(left, top, w, h)):
                return False
            taken.append((left - 2, top - 1, left + w + 2, top + h + 1))
            ctl = ft.Container(left=left, top=top, width=w, height=h, alignment=align, content=content)
            names.append(ctl)
            label_rules.append((ctl, rule))
            return True

        def width_of(text, size, per_char=0.58, spacing=0.0):
            return len(text) * (size * per_char + spacing) + 4

        # Town markers first, so no name is ever drawn over a town.
        for i, t in enumerate(p.towns):
            cx, cy = t.x * SCALE, t.y * SCALE
            if t.kind == "capital":
                ctl = ft.Container(left=cx - 7, top=cy - 7, content=ft.Icon(ft.Icons.STAR, size=14, color="#ffd479"))
                taken.append((cx - 7, cy - 7, cx + 7, cy + 7))
            else:
                ctl = ft.Container(left=cx - 3, top=cy - 3, width=6, height=6, border_radius=6,
                                   bgcolor="#f4efe4", border=ft.Border.all(1, "#20242e"))
                taken.append((cx - 3, cy - 3, cx + 3, cy + 3))
            markers.append(ctl)
            label_rules.append((ctl, ("town", i)))

        # Realm names, living then fallen: try a few vertical nudges, and always show them.
        realms = [(k.id, k.name, k.label_pos) for k in p.kingdoms]
        realms += [(r.id, r.name, r.label_pos) for r in p.history.lost]
        for rid, name, (y, x) in realms:
            text = name.upper()
            w, h = width_of(text, 15, 0.7, 4), 22
            fallen = rid >= len(p.kingdoms)
            ctl = label_text(text, 15, "#d8f3dcb4" if fallen else "#d0ffffff", ft.FontWeight.W_700,
                             italic=fallen, spacing=4)
            spots = [(min(max(x * SCALE - w / 2, 0), MAP_W - w), y * SCALE - h / 2 + dy) for dy in (0, -18, 18, -36, 36)]
            if not any(put(l, t, w, h, ctl, ("realm", rid)) for l, t in spots):
                put(*spots[0], w, h, ctl, ("realm", rid), force=True)

        def town_name(i: int, t: pl.Town):
            capital = t.kind == "capital"
            size = 11 if capital or t.kind == "city" else 10
            w, h = width_of(t.name, size, 0.62 if capital else 0.58), 15
            ctl = label_text(t.name, size, weight=ft.FontWeight.BOLD if capital else ft.FontWeight.W_500)
            cx, cy, gap = t.x * SCALE, t.y * SCALE, 9 if capital else 6
            put(cx + gap, cy - h / 2, w, h, ctl, ("town", i), ft.Alignment.CENTER_LEFT) or put(
                cx - gap - w, cy - h / 2, w, h, ctl, ("town", i), ft.Alignment.CENTER_RIGHT
            )

        def peak_name():
            py, px = p.peak
            text = f"▲ {p.peak_name}"
            w = width_of(text, 10)
            put(px * SCALE - w / 2, py * SCALE + 6, w, 14, label_text(text, 10, "#e8e2d6"), ("always",))

        def river_names():
            for r in p.rivers:
                if r.name:
                    y, x = r.cells[len(r.cells) // 2]
                    w = width_of(r.name, 10)
                    put(x * SCALE - w / 2, y * SCALE + 4, w, 14,
                        label_text(r.name, 10, "#9cc7f0", italic=True), ("always",))

        def by_kind(kind):
            return [(i, t) for i, t in enumerate(p.towns) if t.kind == kind]

        for i, t in by_kind("capital"):
            town_name(i, t)
        peak_name()
        for i, t in by_kind("city"):
            town_name(i, t)
        river_names()
        for i, t in by_kind("town") + by_kind("village"):
            town_name(i, t)

        # Event markers sit above everything and fade in and out with the timeline.
        marker_controls.clear()
        pop = ft.Animation(350, ft.AnimationCurve.EASE_OUT_BACK)
        for m in p.history.markers:
            emoji = MARKER_STYLE[m.kind][0]
            cx, cy = m.x * SCALE, m.y * SCALE
            if m.kind == "crown":  # a small crown beside the capital star; the ticker tells the story
                ctl = ft.Container(left=cx + 3, top=cy - 22, tooltip=m.caption, opacity=0, scale=0.6,
                                   animate_opacity=300, animate_scale=pop,
                                   content=ft.Text(emoji, size=14, style=ft.TextStyle(shadow=LABEL_SHADOW)))
            else:
                ctl = ft.Container(
                    left=min(max(cx - 80, 0), MAP_W - 160), top=min(max(cy - 15, 0), MAP_H - 46), width=160,
                    opacity=0, scale=0.6, animate_opacity=300, animate_scale=pop, content=event_badge(m),
                )
            marker_controls.append((ctl, m))
        labels.controls = markers + names + [c for c, _ in marker_controls]

    def build_globe_labels(p: pl.Planet):
        """Labels for the globe: fewer than the flat map, pinned to map cells and re-placed every frame."""
        nonlocal globe_items, globe_anchor
        items, anchors = [], []

        def add(ctl, y, x, dx, dy, w, h, rule):
            items.append((ctl, dx, dy, w, h, rule))
            anchors.append((y, x))

        def width_of(text, size, per_char=0.58, spacing=0.0):
            return len(text) * (size * per_char + spacing) + 4

        for m in p.history.markers:
            emoji = MARKER_STYLE[m.kind][0]
            if m.kind == "crown":
                ctl = ft.Container(tooltip=m.caption, content=ft.Text(emoji, size=14, style=ft.TextStyle(shadow=LABEL_SHADOW)))
                add(ctl, m.y, m.x, 3, -22, 18, 20, ("marker", m))
            else:
                add(ft.Container(width=160, content=event_badge(m)), m.y, m.x, -80, -15, 160, 46, ("marker", m))

        realms = [(k.id, k.name, k.label_pos) for k in p.kingdoms] + [(r.id, r.name, r.label_pos) for r in p.history.lost]
        for rid, name, (y, x) in realms:
            text, fallen = name.upper(), rid >= len(p.kingdoms)
            w, h = width_of(text, 13, 0.7, 3), 20
            ctl = ft.Container(width=w, height=h, alignment=ft.Alignment.CENTER, content=label_text(
                text, 13, "#d8f3dcb4" if fallen else "#d0ffffff", ft.FontWeight.W_700, italic=fallen, spacing=3))
            add(ctl, y, x, -w / 2, -h / 2, w, h, ("realm", rid))

        for kind in ("capital", "city"):
            for i, t in enumerate(p.towns):
                if t.kind != kind:
                    continue
                capital = kind == "capital"
                mark = (ft.Icon(ft.Icons.STAR, size=12, color="#ffd479") if capital else
                        ft.Container(width=5, height=5, border_radius=5, bgcolor="#f4efe4"))
                text = label_text(t.name, 11 if capital else 10, weight=ft.FontWeight.BOLD if capital else ft.FontWeight.W_500)
                w, h, lead = width_of(t.name, 11 if capital else 10, 0.62 if capital else 0.58) + 18, 16, 6 if capital else 3
                add(ft.Container(width=w, height=h, content=ft.Row([mark, text], spacing=4, tight=True)),
                    t.y, t.x, -lead, -h / 2, w, h, ("town", i))

        text = f"▲ {p.peak_name}"
        w = width_of(text, 10)
        add(ft.Container(width=w, height=14, alignment=ft.Alignment.CENTER, content=label_text(text, 10, "#e8e2d6")),
            *p.peak, -w / 2, 6, w, 14, ("always",))

        globe_items = items
        globe_anchor = (np.array([a[0] for a in anchors], np.float64), np.array([a[1] for a in anchors], np.float64))
        globe_labels.controls = [it[0] for it in items]

    def place_globe_labels():
        """Pin each globe label to its spot on the sphere: fade it toward the rim, hide it on the far side,
        and drop any that would overlap a more important one."""
        px, py, z = globe.project(*globe_anchor, spin)
        fade = np.clip((z - 0.12) / 0.25, 0, 1)
        taken: list[tuple[float, float, float, float]] = []
        for i, (ctl, dx, dy, w, h, rule) in enumerate(globe_items):
            left, top = float(px[i] + dx), float(py[i] + dy)
            show = bool(fade[i] > 0) and label_visible(rule) and all(
                left + w <= l or left >= r or top + h <= t or top >= b for l, t, r, b in taken)
            if show:
                taken.append((left - 2, top - 1, left + w + 2, top + h + 1))
                ctl.left, ctl.top, ctl.opacity = round(left, 1), round(top, 1), round(float(fade[i]), 2)
            ctl.visible = show

    def label_visible(rule: tuple) -> bool:
        if rule[0] == "town":
            t = world.towns[rule[1]]
            return t.kingdom < 0 or bool(owners[t.y, t.x] >= 0)  # free towns stand outside any realm's history
        if rule[0] == "realm":
            return world.history.exists(world, rule[1], year)
        if rule[0] == "marker":
            return rule[1].start <= year < rule[1].end
        return True

    def cell_at(pos: ft.Offset) -> tuple[int, int] | None:
        if view == "globe":
            return globe.cell_at(pos.x, pos.y, spin)
        x, y = int(pos.x // SCALE), int(pos.y // SCALE)
        return (y, x) if 0 <= x < pl.W and 0 <= y < pl.H else None

    def on_hover(e):
        nonlocal hovered_cell, last_hover
        last_hover = time.monotonic()
        cell = cell_at(e.local_position)
        if world is None or owners is None or cell is None or cell == hovered_cell:
            return
        hovered_cell = cell
        status.value = world.describe_cell(*cell, owners)
        status.update()

    async def on_tap(e):
        cell = cell_at(e.local_position)
        if world is None or owners is None or cell is None:
            return
        rid = int(owners[cell])
        await select(rid if rid >= 0 and rid != selected else None)

    # Globe: drag to turn it (up and down tilts toward a pole), flick to spin it, hover to stop it.

    def drag_start(e: ft.DragStartEvent):
        nonlocal dragging, drag_last
        if view == "globe":
            dragging, drag_last = True, (e.local_position.x, e.local_position.y, time.monotonic())
            detector.mouse_cursor = ft.MouseCursor.GRABBING
            detector.update()

    def drag_update(e: ft.DragUpdateEvent):
        nonlocal spin, spin_velocity, drag_last, last_hover, home_tilt
        if not dragging:
            return
        x, y, now = e.local_position.x, e.local_position.y, time.monotonic()
        last_hover, home_tilt = now, None  # the viewer has the globe now; the replay camera backs off
        last_x, last_y, last_t = drag_last
        turn = -(x - last_x) / globe.radius  # the surface follows the pointer
        spin += turn
        if now > last_t:
            spin_velocity = 0.6 * turn / (now - last_t) + 0.4 * spin_velocity
        globe.set_tilt(globe.tilt + (y - last_y) / globe.radius)
        drag_last = (x, y, now)
        redraw.set()

    def drag_end(e: ft.DragEndEvent | None = None):
        nonlocal dragging, spin_velocity
        if not dragging:
            return
        dragging = False
        if e is not None and e.velocity is not None:
            spin_velocity = -e.velocity.x / globe.radius
        spin_velocity = max(-MAX_SPIN, min(MAX_SPIN, spin_velocity))
        detector.mouse_cursor = ft.MouseCursor.GRAB
        detector.update()

    def mouse_left():
        nonlocal last_hover
        last_hover = 0.0

    def set_focus(m):
        """Point the replay camera at marker `m` (or release it with None), leaning in while it looks."""
        nonlocal focus, home_tilt, camera_travelling
        if focus is None and m is not None and home_tilt is None:
            home_tilt = globe.tilt
        focus = m
        camera_travelling = m is not None
        scale = FOCUS_ZOOM if m is not None else 1.0
        if detector.scale != scale:
            detector.scale = scale
            try:
                detector.update()
            except RuntimeError:
                pass  # window closed

    async def spinner():
        """Moves the globe's camera.

        Idle: a slow spin, momentum after a flick, and a gentle stop while the mouse moves over it (keyed
        off recent movement rather than enter/exit, which a window left in the background never gets).
        Replay: swings round to face each battle, sack and outbreak of war, leans in while it plays out,
        then eases back to the viewer's tilt and resumes spinning. Touching the globe hands it back."""
        nonlocal spin, spin_velocity, camera_travelling, home_tilt
        last = time.monotonic()
        while not renderer_task.done():
            await asyncio.sleep(1 / SPIN_FPS)
            now = time.monotonic()
            dt, last = now - last, now
            if view != "globe" or dragging:
                continue
            viewer_active = now - last_hover < HOVER_HOLD
            spot = world.history.spotlight(year) if playing and world is not None and not viewer_active else None
            if spot is not focus:
                set_focus(spot)
            ease = min(1.0, dt * CAMERA_EASE)

            if focus is not None:
                want_spin = (focus.x + 0.5) / pl.W * 2 * math.pi  # bring the event to the centre of the disc
                want_tilt = (0.5 - (focus.y + 0.5) / pl.H) * math.pi
                d_spin = (want_spin - spin + math.pi) % (2 * math.pi) - math.pi  # the short way round
                d_tilt = max(-FOCUS_TILT, min(FOCUS_TILT, want_tilt)) - globe.tilt
                spin += d_spin * ease
                globe.set_tilt(globe.tilt + d_tilt * ease)
                spin_velocity = 0.0
                camera_travelling = abs(d_spin) > 0.04 or abs(d_tilt) > 0.04
                redraw.set()
                continue

            if home_tilt is not None:  # drift back to the tilt the viewer had before the replay took over
                d_tilt = home_tilt - globe.tilt
                globe.set_tilt(globe.tilt + d_tilt * ease)
                if abs(d_tilt) < 0.002:
                    home_tilt = None
                redraw.set()
            target = 0.0 if viewer_active else AUTO_SPIN
            spin_velocity += (target - spin_velocity) * min(1.0, dt * 1.5)
            if abs(spin_velocity) > 0.002:
                spin += spin_velocity * dt
                redraw.set()

    detector = ft.GestureDetector(
        mouse_cursor=ft.MouseCursor.CLICK,
        hover_interval=30,
        drag_interval=10,
        on_hover=on_hover,
        on_exit=mouse_left,
        on_tap_up=on_tap,
        on_pan_start=drag_start,
        on_pan_update=drag_update,
        on_pan_end=drag_end,
        on_pan_cancel=lambda: drag_end(),
        content=ft.Stack([raw_image, labels, globe_labels], width=MAP_W, height=MAP_H),
        scale=1.0,
        animate_scale=ft.Animation(1100, ft.AnimationCurve.EASE_IN_OUT_CUBIC),
    )
    # Cinema mode's caption: frosted glass over the globe, with the world's name, the year and the latest news.
    cine_name = ft.Text("", size=30, weight=ft.FontWeight.BOLD, color="#f4efe4")
    cine_year = ft.Text("", size=18, weight=ft.FontWeight.BOLD, color="#ffd479", font_family="monospace")
    cine_ticker = ft.Text("", size=13, italic=True, color="#e8dcc4", width=380, max_lines=2,
                          overflow=ft.TextOverflow.ELLIPSIS)
    cinema_close = ft.IconButton(ft.Icons.CLOSE, icon_size=16, icon_color="#a0ffffff", tooltip="Leave cinema (Esc)")
    cinema_caption = ft.Container(
        left=18, bottom=18, visible=False, blur=14, bgcolor="#5c121620", border_radius=16,
        border=ft.Border.all(1, "#30ffffff"), padding=ft.Padding.only(left=18, right=8, top=8, bottom=14),
        content=ft.Column(
            [ft.Row([ft.Shimmer(content=cine_name, base_color="#f4efe4", highlight_color="#ffd479", period=4000),
                     cinema_close],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN, width=390),
             cine_year, cine_ticker],
            spacing=2, tight=True,
        ),
    )
    map_view = ft.Container(
        border_radius=12,
        clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        border=ft.Border.all(1, "#262b38"),
        content=ft.Stack([detector, cinema_caption], width=MAP_W, height=MAP_H),
        animate_scale=ft.Animation(700, ft.AnimationCurve.EASE_OUT_CUBIC),
    )

    def present(frame, town_lights=None):
        """What the map view shows: the flat frame itself, or that frame wrapped around the globe."""
        if view != "globe":
            return frame
        return globe.render(frame, spin, ~world.land, town_lights)

    async def set_view(e):
        await set_view_to(e.control.selected[0])

    async def set_view_to(name: str):
        nonlocal view, spin_velocity
        view = name
        view_picker.selected = [view]
        set_focus(None)
        labels.visible, globe_labels.visible = view == "map", view == "globe"
        detector.mouse_cursor = ft.MouseCursor.GRAB if view == "globe" else ft.MouseCursor.CLICK
        spin_velocity = AUTO_SPIN
        page.update()
        redraw.set()

    # --------------------------------------------------------------- timeline

    year_text = ft.Text("", size=20, weight=ft.FontWeight.BOLD, width=110, font_family="monospace")
    ticker = ft.Text("", size=13, italic=True, color="#e8dcc4", expand=True, no_wrap=True,
                     overflow=ft.TextOverflow.ELLIPSIS)
    ticker_year = ft.Text("", size=13, color=ft.Colors.PRIMARY, font_family="monospace", width=44)

    async def on_slide(e):
        nonlocal year, playing
        playing = False
        play_button.icon = ft.Icons.PLAY_ARROW
        year = float(e.control.value)
        redraw.set()

    async def jump_to(target: float):
        nonlocal year, playing
        playing = False
        play_button.icon = ft.Icons.PLAY_ARROW
        year = float(min(max(target, slider.min), slider.max))
        slider.value = year
        redraw.set()

    async def to_present():
        await jump_to(slider.max)

    async def toggle_play():
        nonlocal year, playing, play_run
        if world is None or revealing:
            return
        playing = not playing
        play_run += 1
        play_button.icon = ft.Icons.PAUSE if playing else ft.Icons.PLAY_ARROW
        play_button.update()
        if not playing:
            return
        if year >= slider.max:
            year = slider.min
        mine, run = generation, play_run
        step = (slider.max - slider.min) / (PLAY_SECONDS * PLAY_FPS)
        while playing and mine == generation and run == play_run and year < slider.max:
            if view == "globe":  # the camera sets the pace: hold while it travels, slow while it watches
                rate = TRAVEL_RATE if camera_travelling else GLOBE_SLOW_MOTION if focus is not None else 1
            else:
                rate = SLOW_MOTION if world.history.busy(year) else 1
            before, year = year, min(year + step * rate, slider.max)
            for m in world.history.markers:
                if before < m.start <= year:
                    soundtrack.cue(m.kind, marker_pan(m))
            slider.value = year
            set_dock_progress((year - slider.min) / max(slider.max - slider.min, 1), year)
            redraw.set()
            await asyncio.sleep(1 / PLAY_FPS)
        if run == play_run or not playing:  # finished or paused; a newer replay keeps its own badge
            set_dock_progress(None)
        if mine == generation and run == play_run:
            playing = False
            play_button.icon = ft.Icons.PLAY_ARROW
            play_button.update()

    def marker_pan(m) -> float:
        """Where a marker sits left to right, from -1 to 1, so its sound comes from that side."""
        if view != "globe":
            return m.x / pl.W * 2 - 1
        px, _, z = globe.project(np.array([m.y], np.float64), np.array([m.x], np.float64), spin)
        return float(np.clip((px[0] - MAP_W / 2) / globe.radius, -1, 1)) if z[0] > 0 else 0.0

    dock_progress: float | None = None

    def set_dock_progress(value: float | None, at_year: float = 0):
        """Show a replay on the app's icon: a progress bar on the Windows taskbar, and the year (to the decade)
        as a badge, which is what the macOS Dock shows - it ignores the progress bar. None clears both."""
        nonlocal dock_progress
        badge = "" if value is None else str(int(at_year) // 10 * 10)  # "" clears it; None would leave it up
        same_bar = value == dock_progress or (
            value is not None and dock_progress is not None and abs(value - dock_progress) < 0.005)
        if page.web or (same_bar and badge == page.window.badge_label):
            return
        dock_progress = value
        page.window.progress_bar = -1 if value is None else round(value, 3)  # below 0 hides the bar
        page.window.badge_label = badge
        try:
            page.window.update()
        except RuntimeError:
            pass  # window closed

    play_button = ft.IconButton(ft.Icons.PLAY_ARROW, tooltip="Replay history", on_click=toggle_play)
    slider = ft.Slider(min=0, max=1, value=1, expand=True, on_change=on_slide)
    timeline = ft.Container(
        width=MAP_W, padding=ft.Padding.symmetric(horizontal=8, vertical=4), border_radius=12, bgcolor="#141824",
        border=ft.Border.all(1, "#262b38"),
        content=ft.Column(
            spacing=0,
            controls=[
                ft.Row([play_button, year_text, slider,
                        ft.TextButton("Now", icon=ft.Icons.SKIP_NEXT, on_click=to_present)], spacing=4),
                ft.Row([ft.Container(width=12), ft.Icon(ft.Icons.HISTORY_EDU, size=16, color=ft.Colors.PRIMARY),
                        ticker_year, ticker], spacing=8, height=24),
            ],
        ),
    )

    async def renderer():
        """Draws the latest requested state; drags, playback and the spinning globe just set `redraw`,
        so frames coalesce. While only the globe turns, the flat frame is reused and the panels are left alone."""
        nonlocal owners, flat, lights, flat_key
        while True:
            await redraw.wait()
            redraw.clear()
            if world is None or revealing:
                continue
            p = world
            key = (generation, mode, selected, year)
            changed = key != flat_key
            if changed:
                owners = p.history.owners_at(p, year)
                flat, flat_key = pl.render(p, mode, selected, year=year), key
                lights = gl.town_lights(p, owners)
            try:
                await raw_image.render(present(flat, lights))
            except (RuntimeError, TimeoutError):
                return  # window closed
            if p is not world:
                continue
            if view == "globe":
                place_globe_labels()
            if not changed:
                if view == "globe":
                    try:
                        globe_labels.update()
                    except RuntimeError:
                        return
                continue
            for ctl, rule in label_rules:
                ctl.visible = label_visible(rule)
            for ev_year, row in chronicle_rows:
                row.opacity = 1.0 if ev_year <= year else 0.3
            for ctl, m in marker_controls:
                active = m.start <= year < m.end
                ctl.opacity, ctl.scale = (1.0, 1.0) if active else (0.0, 0.6)
            update_dynasty()
            for point in rise.get("cursor", ()):
                point.x = year
            year_text.value = f"Year {int(year)}"
            latest = p.history.latest_event(year)
            ticker_year.value, ticker.value = (str(latest[0]), latest[1]) if latest else ("", "Before recorded history.")
            cine_year.value, cine_ticker.value = f"Year {int(year)}", ticker.value
            if hovered_cell is not None:
                status.value = p.describe_cell(*hovered_cell, owners)
            try:
                page.update()
            except RuntimeError:
                return

    # ------------------------------------------------------------- side panel

    panel = ft.Column(spacing=10, scroll=ft.ScrollMode.AUTO, expand=True)
    panel_shown = 0  # bumps on every new panel, so only the latest fade-in runs

    def show_panel(controls: list[ft.Control]):
        """Swap the side panel's contents and fade them in; the caller's next update shows them.

        Not an AnimatedSwitcher: that re-ran its fade on every update inside it, so the panel flickered
        all through a replay (the ruler card follows the year) and whenever the chart's readout changed."""
        nonlocal panel_shown
        panel.controls = controls
        panel.animate_opacity, panel.opacity = None, 0.0
        panel_shown += 1
        spawn(fade_in_panel(panel_shown))

    async def fade_in_panel(shown: int):
        await asyncio.sleep(0.03)  # let the hidden contents arrive first
        if shown != panel_shown:
            return
        panel.animate_opacity, panel.opacity = ft.Animation(280, ft.AnimationCurve.EASE_OUT), 1.0
        try:
            panel.update()
        except RuntimeError:
            pass  # window closed

    def blend(rgb, t: float, to=PANEL_BG) -> str:
        return hex_color([c + (d - c) * t for c, d in zip(rgb, to)])

    def rise_chart(p: pl.Planet, highlight: int | None = None) -> list[ft.Control]:
        """Every realm's share of the land over the whole history, stacked oldest at the bottom.

        A line marks the timeline's year; hover to read a year, click or drag to travel to it.
        With `highlight`, that realm keeps its colour and the rest fade back."""
        years, share = territory
        realms = [(k.id, k.name, k.color, k.lore["founded"]) for k in p.kingdoms]
        realms += [(r.id, r.name, r.color, r.founded) for r in p.history.lost]
        order = sorted(realms, key=lambda r: r[3])
        stacked = np.cumsum(share[[r[0] for r in order]], axis=0)
        xs = [round(float(y), 1) for y in years]
        bands = []
        for level in reversed(range(len(order))):  # top band first, so each lower band paints over its fill
            rid, _, color, _ = order[level]
            dim = 0.0 if highlight in (None, rid) else 0.72
            top = level == len(order) - 1
            bands.append(fch.LineChartData(
                # the readout below the chart replaces tooltips, and only the top band marks the hovered year
                points=[fch.LineChartDataPoint(x, round(float(y), 2), show_tooltip=False)
                        for x, y in zip(xs, stacked[level])],
                color=blend(color, 0.35, (255, 255, 255)) if dim == 0 else blend(color, dim),
                below_line_bgcolor=blend(color, dim), stroke_width=1, point=False, selected_point=False,
                selected_below_line=fch.ChartPointLine(color="#90ffffff", width=1) if top else False,
            ))
        cursor = [fch.LineChartDataPoint(year, y, show_tooltip=False) for y in (0, 100)]
        bands.append(fch.LineChartData(points=cursor, color="#f0ffffff", stroke_width=1.5, point=False,
                                       selected_point=False, selected_below_line=False))
        rise.clear()
        rise["cursor"] = cursor

        hint = "Share of the land each realm held · click to travel"
        readout = ft.Text(hint, size=11, color=ft.Colors.ON_SURFACE_VARIANT, no_wrap=True,
                          overflow=ft.TextOverflow.ELLIPSIS)
        span = xs[-1] - xs[0]
        tick = next(s for s in (50, 100, 200, 250, 500, 1000, 2000) if span / s <= 5)
        ticks = range(int(math.ceil(xs[0] / tick) * tick), int(xs[-1]) + 1, tick)

        async def on_event(e: fch.LineChartEvent):
            if e.type == fch.ChartEventType.POINTER_EXIT:
                readout.value = hint
                readout.update()
                return
            i = next((s.spot_index for s in e.spots if s.spot_index >= 0 and s.bar_index < len(bands) - 1), None)
            if i is None:
                return
            held = sorted(((share[rid, i], name) for rid, name, _, _ in realms), reverse=True)
            readout.value = f"{int(years[i])} · " + " · ".join(f"{n} {v:.0f}%" for v, n in held[:3] if v >= 0.5)
            readout.update()
            if e.type in (fch.ChartEventType.TAP_UP, fch.ChartEventType.PAN_DOWN, fch.ChartEventType.PAN_UPDATE):
                await jump_to(float(years[i]))

        chart = fch.LineChart(
            data_series=bands, min_x=xs[0], max_x=xs[-1], min_y=0, max_y=100, height=RISE_H,
            tooltip=fch.LineChartTooltip(bgcolor="#00000000"),  # tooltip=None still shows some; the readout says it
            interactive=True, animation=ft.Animation(120, ft.AnimationCurve.EASE_OUT),
            horizontal_grid_lines=fch.ChartGridLines(interval=25, color="#14ffffff", width=1),
            left_axis=fch.ChartAxis(show_labels=False, label_size=0),
            top_axis=fch.ChartAxis(show_labels=False, label_size=0),
            right_axis=fch.ChartAxis(show_labels=False, label_size=0),
            bottom_axis=fch.ChartAxis(
                label_size=18, show_min=False, show_max=False,
                labels=[fch.ChartAxisLabel(value=t, label=ft.Text(str(t), size=10, color=ft.Colors.ON_SURFACE_VARIANT))
                        for t in ticks],
            ),
            on_event=on_event,
        )
        # Not wrapped in a clipping Container: with one, the first hover blanks everything below the chart.
        return [heading("RISE AND FALL"), chart, readout]

    def heading(text: str) -> ft.Control:
        return ft.Text(text, size=11, weight=ft.FontWeight.BOLD, color=ft.Colors.PRIMARY,
                       style=ft.TextStyle(letter_spacing=2))

    def fact(icon, label: str, value) -> ft.Control:
        value = ft.Text(value, size=13, expand=True) if isinstance(value, str) else value
        return ft.Row(
            [ft.Icon(icon, size=16, color=ft.Colors.ON_SURFACE_VARIANT),
             ft.Text(label, size=12, color=ft.Colors.ON_SURFACE_VARIANT, width=78), value],
            spacing=8,
        )

    def selector(rid: int | None):
        """Click handler that selects a realm. Flet only awaits handlers that are `async def`."""

        async def handler():
            await select(rid)

        return handler

    def overview_panel(p: pl.Planet) -> list[ft.Control]:
        longest = p.lore["longest_river"]
        realms = [
            ft.ListTile(
                leading=kingdom_sigil(k), title=ft.Text(k.title, size=14),
                subtitle=ft.Text(f"{format_population(k.lore['population'])} souls · since {k.lore['founded']}",
                                 size=11, color=ft.Colors.ON_SURFACE_VARIANT),
                dense=True, content_padding=ft.Padding.symmetric(horizontal=4), on_click=selector(k.id),
            )
            for k in sorted(p.kingdoms, key=lambda k: -k.lore["population"])
        ]
        fallen = [
            ft.ListTile(
                leading=lost_sigil(r), title=ft.Text(r.title, size=14, italic=True),
                subtitle=ft.Text(f"{r.founded}–{r.fell} · conquered by {p.kingdoms[r.conqueror].name}",
                                 size=11, color=ft.Colors.ON_SURFACE_VARIANT),
                dense=True, content_padding=ft.Padding.symmetric(horizontal=4), on_click=selector(r.id),
            )
            for r in p.history.lost
        ]
        return [
            ft.Shimmer(content=ft.Text(p.name, size=30, weight=ft.FontWeight.BOLD), loop=1, period=1800,
                       base_color="#f4efe4", highlight_color="#ffd479"),  # one sweep of gold as the world is named
            ft.Text(f"World {p.seed} · Year {p.year}", size=13, color=ft.Colors.ON_SURFACE_VARIANT),
            ft.Divider(height=8),
            fact(ft.Icons.PUBLIC, "Land", f"{p.lore['land_pct']:.0f}% of the surface"),
            fact(ft.Icons.LANDSCAPE, "Highest", f"{p.peak_name}, {p.lore['peak_m']:,.0f} m"),
            fact(ft.Icons.WATER, "Longest", f"River {longest.name}" if longest else "—"),
            fact(ft.Icons.LOCATION_CITY, "Towns", str(len(p.towns))),
            fact(ft.Icons.HISTORY_EDU, "History", f"{p.year - p.history.start:,} years recorded"),
            ft.Divider(height=8),
            *rise_chart(p),
            heading(f"{len(p.kingdoms)} REALMS"),
            *realms,
            *([heading("FALLEN REALMS"), *fallen] if fallen else []),
            ft.Text("Click a realm on the map, or press play to replay history.", size=11, italic=True,
                    color=ft.Colors.ON_SURFACE_VARIANT),
        ]

    def dynasty_section(reigns, realm_name: str) -> tuple[ft.Control, list[ft.Control]]:
        """A 'ruler in this year' card plus a dynasty list that follows the timeline."""
        who = ft.Text("", size=16, weight=ft.FontWeight.BOLD)
        detail = ft.Text("", size=12, color=ft.Colors.ON_SURFACE_VARIANT)
        when = ft.Text("", size=11, weight=ft.FontWeight.BOLD, color=ft.Colors.PRIMARY,
                       style=ft.TextStyle(letter_spacing=2))
        card = ft.Container(
            padding=12, border_radius=10, bgcolor="#1c2232", border=ft.Border.all(1, "#2c3448"),
            content=ft.Row([ft.Text("👑", size=24), ft.Column([when, who, detail], spacing=2, expand=True)], spacing=12),
        )
        rows = []
        for r in reigns:
            house = f"House {r.house} · " if r.house else ""
            rows.append(ft.Container(
                padding=ft.Padding.symmetric(horizontal=8, vertical=3), border_radius=6, animate_opacity=200,
                content=ft.Row(
                    [ft.Text(f"{r.start}–{r.end if r.fate != 'reigns still' else ''}", size=11, width=74,
                             font_family="monospace", color=ft.Colors.PRIMARY),
                     ft.Column([ft.Text(r.display, size=12, weight=ft.FontWeight.W_600),
                                ft.Text(f"{house}{r.fate}", size=11, color=ft.Colors.ON_SURFACE_VARIANT)],
                               spacing=0, expand=True)],
                    vertical_alignment=ft.CrossAxisAlignment.START,
                ),
            ))
        dynasty.clear()
        dynasty.update(reigns=reigns, rows=rows, who=who, detail=detail, when=when, name=realm_name)
        update_dynasty()
        return card, rows

    def update_dynasty():
        """Point the ruler card and dynasty list at the slider year."""
        if not dynasty:
            return
        reigns, rows = dynasty["reigns"], dynasty["rows"]
        dynasty["when"].value = f"IN THE YEAR {int(year)}"
        idx = max((i for i, r in enumerate(reigns) if r.start <= year), default=-1)
        if idx < 0:
            dynasty["who"].value = "Not yet founded"
            dynasty["detail"].value = f"{dynasty['name']} rises in the year {reigns[0].start}."
        elif year >= reigns[-1].end and reigns[-1].fate != "reigns still":
            last = reigns[-1]
            dynasty["who"].value = f"Fallen in {last.end}"
            dynasty["detail"].value = f"{last.display} {last.fate}."
        else:
            r = reigns[idx]
            house = f"House {r.house} · " if r.house else ""
            nth = sum(1 for q in reigns[: idx + 1] if q.house == r.house)
            dynasty["who"].value = r.display
            dynasty["detail"].value = (f"{house}{'ruler' if r.house else 'office-holder'} no. {nth} · "
                                       f"on the throne since {r.start}")
        focus = max(idx, 0)
        for i, row in enumerate(rows):
            row.visible = abs(i - focus) <= 3
            row.opacity = 1.0 if i <= idx else 0.35
            row.bgcolor = "#2a3350" if i == idx else None

    def kingdom_panel(p: pl.Planet, k: pl.Kingdom) -> list[ft.Control]:
        lore = k.lore
        share = sorted(k.biome_share.items(), key=lambda bs: -bs[1])[:3]
        relations = [
            ft.Row(
                [kingdom_sigil(p.kingdoms[n], 20),
                 ft.TextButton(p.kingdoms[n].name, on_click=selector(n)),
                 ft.Container(
                     padding=ft.Padding.symmetric(horizontal=8, vertical=2), border_radius=10,
                     bgcolor=ft.Colors.with_opacity(0.19, RELATION_COLORS[rel]),
                     content=ft.Text(rel, size=11, color=RELATION_COLORS[rel]),
                 )],
                spacing=4,
            )
            for n, rel in lore["relations"]
        ] or [ft.Text("An island realm with no land borders.", size=12, italic=True)]
        chronicle_rows.clear()
        for ev_year, event in lore["history"]:
            row = ft.Row(
                [ft.Text(str(ev_year), size=12, width=40, color=ft.Colors.PRIMARY, font_family="monospace"),
                 ft.Text(event, size=12, expand=True)],
                vertical_alignment=ft.CrossAxisAlignment.START,
                opacity=1.0 if ev_year <= year else 0.3,
                animate_opacity=250,
            )
            chronicle_rows.append((ev_year, row))
        towns = ft.Row(
            [ft.Chip(label=ft.Text(t.name, size=11),
                     leading=ft.Icon(ft.Icons.STAR if t.kind == "capital" else
                                     ft.Icons.ANCHOR if t.coastal else ft.Icons.HOME, size=14),
                     visual_density=ft.VisualDensity.COMPACT)
             for t in lore["towns"]],
            wrap=True, spacing=4, run_spacing=4,
        )
        ruler_card, reign_rows = dynasty_section(lore["reigns"], k.name)
        return [
            ft.TextButton("All realms", icon=ft.Icons.ARROW_BACK, on_click=selector(None)),
            ft.Row(
                [kingdom_sigil(k, 52),
                 ft.Column([ft.Text(k.title, size=20, weight=ft.FontWeight.BOLD),
                            ft.Text(f"“{lore['motto']}”", size=13, italic=True, color=ft.Colors.ON_SURFACE_VARIANT)],
                           spacing=2, expand=True)],
                spacing=12,
            ),
            ruler_card,
            fact(ft.Icons.STAR, "Capital", lore["capital"]),
            fact(ft.Icons.CALENDAR_MONTH, "Founded", f"Year {lore['founded']} ({p.year - lore['founded']} years ago)"),
            fact(ft.Icons.GROUPS, "People", format_population(lore["population"])),
            fact(ft.Icons.TEMPLE_BUDDHIST, "Faith", lore["faith"]),
            fact(ft.Icons.TERRAIN, "Lands", ", ".join(f"{pl.BIOME_NAMES[b].lower()} {s:.0%}" for b, s in share)),
            *rise_chart(p, k.id),
            heading("EXPORTS"),
            ft.Row([ft.Chip(label=ft.Text(g, size=12), visual_density=ft.VisualDensity.COMPACT) for g in lore["exports"]],
                   wrap=True, spacing=6, run_spacing=6),
            heading("NEIGHBOURS"),
            *relations,
            heading(f"DYNASTY · {len(lore['reigns'])} RULERS"),
            *reign_rows,
            heading("CHRONICLE"),
            *(row for _, row in chronicle_rows),
            heading(f"SETTLEMENTS · {len(lore['towns'])}"),
            towns,
        ]

    def lost_panel(p: pl.Planet, r) -> list[ft.Control]:
        chronicle_rows.clear()
        conqueror = p.kingdoms[r.conqueror]
        ruler_card, reign_rows = dynasty_section(r.reigns, r.name)

        async def visit():
            await jump_to(r.fell - 1)

        return [
            ft.TextButton("All realms", icon=ft.Icons.ARROW_BACK, on_click=selector(None)),
            ft.Row(
                [lost_sigil(r, 52),
                 ft.Column([ft.Text(r.title, size=20, weight=ft.FontWeight.BOLD, italic=True),
                            ft.Text("A fallen realm", size=13, color=ft.Colors.ON_SURFACE_VARIANT)],
                           spacing=2, expand=True)],
                spacing=12,
            ),
            ruler_card,
            fact(ft.Icons.CALENDAR_MONTH, "Founded", f"Year {r.founded}"),
            fact(ft.Icons.STAR, "Capital", r.capital),
            fact(ft.Icons.SHIELD, "Last ruler", r.last_ruler),
            fact(ft.Icons.LOCAL_FIRE_DEPARTMENT, "Fell", f"Year {r.fell}, after {r.fell - r.founded} years"),
            fact(ft.Icons.FLAG, "Conquered by",
                 ft.TextButton(conqueror.title, on_click=selector(conqueror.id))),
            ft.Text(f"Its lands are now the far provinces of {conqueror.name}. "
                    f"Move the timeline before {r.fell} to see its old borders.",
                    size=12, italic=True, color=ft.Colors.ON_SURFACE_VARIANT),
            ft.FilledTonalButton(f"Visit the year {r.fell - 1}", icon=ft.Icons.HISTORY, on_click=visit),
            *rise_chart(p, r.id),
            heading(f"DYNASTY · {len(r.reigns)} RULERS"),
            *reign_rows,
        ]

    async def select(rid: int | None):
        nonlocal selected
        if world is None:
            return
        selected = rid
        if rid is None:
            chronicle_rows.clear()
            dynasty.clear()
            show_panel(overview_panel(world))
        elif rid < len(world.kingdoms):
            show_panel(kingdom_panel(world, world.kingdoms[rid]))
        else:
            show_panel(lost_panel(world, world.history.lost[rid - len(world.kingdoms)]))
        panel.update()
        redraw.set()

    # ---------------------------------------------------------------- toolbar

    async def submit_seed():
        await create_world(seed_field.value)

    seed_field = ft.TextField(
        value=DEFAULT_SEED, label="World", width=170, dense=True, prefix_icon=ft.Icons.PUBLIC,
        tooltip="Any number or word makes its own world", on_submit=submit_seed,
    )

    def seed_stepper(delta: int):
        async def handler():
            seed_field.value = str(max(0, pl.seed_from_text(seed_field.value) + delta))
            seed_field.update()
            await create_world(seed_field.value)

        return handler

    async def random_seed():
        seed_field.value = str(random.randint(0, 99_999))
        seed_field.update()
        await create_world(seed_field.value)

    async def set_mode(e):
        nonlocal mode
        mode = e.control.selected[0]
        redraw.set()

    sound_button = ft.IconButton(ft.Icons.VOLUME_UP, tooltip="Mute")

    def show_sound_state():
        sound_button.icon = ft.Icons.VOLUME_OFF if soundtrack.muted else ft.Icons.VOLUME_UP
        sound_button.tooltip = "Unmute" if soundtrack.muted else "Mute"

    async def toggle_sound():
        soundtrack.set_muted(not soundtrack.muted)
        show_sound_state()
        sound_button.update()
        try:
            await asyncio.wait_for(prefs.set(MUTED_KEY, soundtrack.muted), st.CALL_TIMEOUT)
        except Exception:  # noqa: BLE001 - remembering the choice is a nicety
            pass

    sound_button.on_click = toggle_sound

    # Cinema: the globe fills the screen and replays one world's history after another, until Esc.

    async def enter_cinema():
        nonlocal cinema, cinema_run, before_cinema
        if cinema or world is None:
            return
        cinema, before_cinema = True, view
        cinema_run += 1
        await jump_to(year)  # stop any replay; the cinema loop starts its own
        for ctl in (toolbar, timeline, status, side):
            ctl.visible = False
        map_view.border, map_view.border_radius = None, 0
        cinema_caption.visible = True
        page.padding, page.bgcolor = 0, "#000000"
        root.alignment = ft.MainAxisAlignment.CENTER  # the map in the middle of the screen
        page.vertical_alignment = ft.MainAxisAlignment.CENTER
        fit_cinema()
        if view != "globe":
            await set_view_to("globe")
        if not page.web:
            page.window.full_screen = True
        page.update()
        try:
            await asyncio.wait_for(wakelock.enable(), st.CALL_TIMEOUT)
        except Exception:  # noqa: BLE001 - staying awake is best effort
            pass
        spawn(cinema_loop(cinema_run))

    async def leave_cinema():
        nonlocal cinema, cinema_run
        if not cinema:
            return
        cinema = False
        cinema_run += 1
        await jump_to(year)  # stop the replay where it is
        for ctl in (toolbar, timeline, status, side):
            ctl.visible = True
        map_view.border, map_view.border_radius, map_view.scale = ft.Border.all(1, "#262b38"), 12, 1.0
        cinema_caption.visible = False
        page.padding, page.bgcolor = 16, "#0b0d13"
        root.alignment = ft.MainAxisAlignment.START
        page.vertical_alignment = ft.MainAxisAlignment.START
        if before_cinema != view:
            await set_view_to(before_cinema)
        if not page.web:
            page.window.full_screen = False
        page.update()
        try:
            await asyncio.wait_for(wakelock.disable(), st.CALL_TIMEOUT)
        except Exception:  # noqa: BLE001
            pass

    cinema_close.on_click = leave_cinema

    def fit_cinema():
        """Scale the map view to fill the screen (its frames are upscaled; the labels scale with it)."""
        if cinema and page.width and page.height:
            map_view.scale = round(min(page.width / MAP_W, page.height / MAP_H), 3)

    async def cinema_loop(run: int):
        """Replay this world, linger on how it ends, then move on to a random new world, until cinema ends."""
        first = True
        while cinema and run == cinema_run:
            if not first:
                await asyncio.sleep(CINEMA_HOLD)
                if not (cinema and run == cinema_run):
                    return
                await random_seed()
            first = False
            await asyncio.sleep(1.5)  # a moment on the new world before its history begins
            if not (cinema and run == cinema_run) or world is None or revealing:
                return
            await jump_to(slider.max)  # playback starts over from the first year
            await toggle_play()  # returns once the replay ends, or is stopped

    def spawn(coro):
        task = asyncio.create_task(coro)
        BACKGROUND_TASKS.add(task)  # the event loop only holds weak references to tasks
        task.add_done_callback(BACKGROUND_TASKS.discard)

    view_picker = ft.SegmentedButton(
        selected=[view], show_selected_icon=False, on_change=set_view,
        segments=[
            ft.Segment(value="map", label=ft.Text("Map"), icon=ft.Icon(ft.Icons.PANORAMA)),
            ft.Segment(value="globe", label=ft.Text("Globe"), icon=ft.Icon(ft.Icons.LANGUAGE)),
        ],
    )

    toolbar = ft.Row(
        [
            ft.Text("Atlas", size=24, weight=ft.FontWeight.BOLD),
            ft.Container(width=8),
            ft.IconButton(ft.Icons.CHEVRON_LEFT, tooltip="Previous world", on_click=seed_stepper(-1)),
            seed_field,
            ft.IconButton(ft.Icons.CHEVRON_RIGHT, tooltip="Next world", on_click=seed_stepper(1)),
            ft.IconButton(ft.Icons.CASINO, tooltip="Random world", on_click=random_seed),
            progress,
            ft.Container(expand=True),
            sound_button,
            ft.IconButton(ft.Icons.SLIDESHOW, tooltip="Cinema: full-screen globe, replaying world after world",
                          on_click=enter_cinema),
            view_picker,
            ft.SegmentedButton(
                selected=["terrain"], show_selected_icon=False, on_change=set_mode,
                segments=[
                    ft.Segment(value="terrain", label=ft.Text("Terrain"), icon=ft.Icon(ft.Icons.TERRAIN)),
                    ft.Segment(value="political", label=ft.Text("Realms"), icon=ft.Icon(ft.Icons.MAP)),
                ],
            ),
        ],
        width=MAP_W,
    )

    # ------------------------------------------------------------ world birth

    async def create_world(text: str):
        nonlocal world, selected, generation, hovered_cell, year, owners, revealing, playing, territory
        generation += 1
        mine = generation
        playing = False
        play_button.icon = ft.Icons.PLAY_ARROW
        progress.visible = True
        labels.opacity = globe_labels.opacity = 0
        page.update()

        seed = pl.seed_from_text(text)

        def build():
            p = pl.generate(seed)
            return p, p.history.territory(p)

        try:
            (p, land_held), drone = await asyncio.gather(asyncio.to_thread(build), soundtrack.drone_for(seed))
        except Exception as ex:
            if mine == generation:  # keep showing the previous world
                progress.visible = False
                labels.opacity = globe_labels.opacity = 1 if world is not None else 0
                status.value = f"Could not build a world from {text!r}: {ex!r}"
                page.update()
            return
        if mine != generation:
            return
        world, selected, hovered_cell, revealing, territory = p, None, None, True, land_held
        soundtrack.set_drone(drone)
        cine_name.value = p.name
        year = float(p.year)
        owners = p.kingdom_map
        slider.min, slider.max, slider.value = p.history.start, p.year, p.year
        build_labels(p)
        build_globe_labels(p)
        chronicle_rows.clear()
        dynasty.clear()
        show_panel(overview_panel(p))
        status.value = "Hover to explore · click a realm for its history"
        page.update()
        soundtrack.reveal()

        # The oceans drain away to reveal the land.
        try:
            for i in range(REVEAL_FRAMES):
                if mine != generation:
                    return
                t = 1 - (i + 1) / REVEAL_FRAMES
                await raw_image.render(present(pl.render(p, mode, None, sea_rise=(1 - p.sea) * t * t)))
                await asyncio.sleep(1 / 60)
        except (RuntimeError, TimeoutError):
            return  # window closed mid-animation
        revealing = False
        labels.opacity = globe_labels.opacity = 1
        progress.visible = False
        redraw.set()

    side = ft.Container(
        width=PANEL_W, height=MAP_H + 150, padding=16, border_radius=12,
        bgcolor="#141824", border=ft.Border.all(1, "#262b38"),
        content=panel,
    )

    async def on_key(e: ft.KeyboardEvent):
        if e.key == "Escape":
            await leave_cinema()

    def on_resize(e: ft.PageResizeEvent):
        if cinema:
            fit_cinema()
            map_view.update()

    root = ft.Row(
        [
            ft.Column([toolbar, map_view, timeline, status], spacing=10),
            side,
        ],
        spacing=16,
        vertical_alignment=ft.CrossAxisAlignment.START,
    )
    page.on_keyboard_event = on_key
    page.on_resize = on_resize
    page.add(root)
    if not page.web:
        await page.window.center()
    try:
        soundtrack.set_muted(bool(await asyncio.wait_for(prefs.get(MUTED_KEY), st.CALL_TIMEOUT)))
    except Exception:  # noqa: BLE001 - no stored choice, or no storage: sound stays on
        pass
    show_sound_state()
    await soundtrack.load()
    renderer_task = asyncio.create_task(renderer())
    for task in (renderer_task, asyncio.create_task(spinner())):
        BACKGROUND_TASKS.add(task)  # the event loop only holds weak references to tasks
        task.add_done_callback(BACKGROUND_TASKS.discard)

    # Optional command line: seed, year, realm name, --globe and --replay.
    flags = {"--globe", "--replay"}
    args = [a for a in sys.argv[1:] if a not in flags]
    if "--globe" in sys.argv[1:]:
        view = "globe"
        view_picker.selected = ["globe"]
        labels.visible, globe_labels.visible = False, True
        detector.mouse_cursor = ft.MouseCursor.GRAB
    if args:
        seed_field.value = args[0]
    await create_world(seed_field.value)
    if len(args) > 1 and args[1].isdigit():
        await jump_to(int(args[1]))
    if len(args) > 2 and world is not None:
        wanted = args[2].lower()
        realms = [(k.id, k.name) for k in world.kingdoms] + [(r.id, r.name) for r in world.history.lost]
        match = next((rid for rid, name in realms if name.lower().startswith(wanted)), None)
        if match is not None:
            await select(match)
    if "--replay" in sys.argv[1:] and world is not None:
        if not (len(args) > 1 and args[1].isdigit()):
            await jump_to(slider.max)  # playback restarts from the first year when it starts at the end
        task = asyncio.create_task(toggle_play())
        BACKGROUND_TASKS.add(task)
        task.add_done_callback(BACKGROUND_TASKS.discard)


if __name__ == "__main__":
    ft.run(main)

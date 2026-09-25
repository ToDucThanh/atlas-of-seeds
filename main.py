import asyncio
import time

import flet as ft

import sand

GRID_WIDTH = 240
GRID_HEIGHT = 150
CELL_SIZE = 4  # logical pixels per cell on screen
TARGET_FPS = 60
MIN_BRUSH, MAX_BRUSH = 1, 20


def hex_color(rgb) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(int(c) for c in rgb))


# (material id, label, icon); keys 1-7 select them in order.
MATERIALS = [
    (sand.SAND, "Sand", ft.Icons.GRAIN),
    (sand.WATER, "Water", ft.Icons.WATER_DROP),
    (sand.OIL, "Oil", ft.Icons.OIL_BARREL),
    (sand.PLANT, "Plant", ft.Icons.GRASS),
    (sand.FIRE, "Fire", ft.Icons.LOCAL_FIRE_DEPARTMENT),
    (sand.WALL, "Wall", ft.Icons.GRID_VIEW),
    (sand.EMPTY, "Erase", ft.Icons.AUTO_FIX_NORMAL),
]
ICON_COLORS = {sand.FIRE: "#ff8a30", sand.OIL: "#a07050", sand.EMPTY: "#c8c8d0"}
BACKGROUND_TASKS: set[asyncio.Task] = set()


async def main(page: ft.Page):
    page.title = "Sandbox"
    page.theme_mode = ft.ThemeMode.DARK
    page.theme = ft.Theme(color_scheme_seed="#deb86e")
    page.bgcolor = "#0b0c12"
    page.padding = 20
    if not page.web:
        page.window.width = GRID_WIDTH * CELL_SIZE + 60
        page.window.height = GRID_HEIGHT * CELL_SIZE + 230

    world = sand.World(GRID_WIDTH, GRID_HEIGHT)
    world.load_demo()
    material = sand.SAND
    brush = 4
    playing = True
    pointer_down = False
    erasing = False  # right mouse button always erases
    pointer: tuple[int, int] | None = None  # cell under the mouse
    last_painted: tuple[int, int] | None = None

    # One frame pixel per cell; the client upscales with nearest-neighbour so
    # grains stay crisp and each frame is only GRID_WIDTH x GRID_HEIGHT pixels.
    raw_image = ft.RawImage(
        width=GRID_WIDTH * CELL_SIZE,
        height=GRID_HEIGHT * CELL_SIZE,
        fit=ft.BoxFit.FILL,
        filter_quality=ft.FilterQuality.NONE,
    )

    def to_cell(pos: ft.Offset) -> tuple[int, int]:
        return int(pos.x // CELL_SIZE), int(pos.y // CELL_SIZE)

    # ------------------------------------------------------------ pointer input

    def press(e, erase=False):
        nonlocal pointer_down, erasing, pointer, last_painted
        pointer_down, erasing = True, erase
        pointer = to_cell(e.local_position)
        last_painted = None

    def move(e):
        nonlocal pointer
        pointer = to_cell(e.local_position)

    def release(e=None):
        nonlocal pointer_down, erasing
        pointer_down = erasing = False

    def leave(e):
        nonlocal pointer
        pointer = None
        release()

    def scroll(e: ft.ScrollEvent):
        if e.scroll_delta and e.scroll_delta.y:
            set_brush(brush + (-1 if e.scroll_delta.y > 0 else 1))

    def paint_stroke():
        """Paint discs along the path since the last frame so fast strokes stay continuous."""
        nonlocal last_painted
        mat = sand.EMPTY if erasing else material
        x1, y1 = pointer
        x0, y0 = last_painted or pointer
        steps = max(1, int(max(abs(x1 - x0), abs(y1 - y0)) / max(brush / 2, 1)))
        for i in range(1, steps + 1):
            t = i / steps
            world.paint(round(x0 + (x1 - x0) * t), round(y0 + (y1 - y0) * t), brush, mat)
        last_painted = pointer

    # ------------------------------------------------------------------ toolbar

    def select_material(value: int):
        nonlocal material
        material = value
        picker.selected = [str(value)]
        picker.update()

    def set_brush(value: int):
        nonlocal brush
        brush = max(MIN_BRUSH, min(MAX_BRUSH, int(value)))
        brush_slider.value = brush
        brush_slider.update()

    def toggle_playing():
        nonlocal playing
        playing = not playing
        play_button.icon = ft.Icons.PAUSE if playing else ft.Icons.PLAY_ARROW
        play_button.tooltip = "Pause (space)" if playing else "Play (space)"
        play_button.update()

    def on_key(e: ft.KeyboardEvent):
        key = e.key.lower()
        if key in ("space", " "):
            toggle_playing()
        elif key == "c":
            world.clear()
        elif key == "d":
            world.load_demo()
        elif key == "[":
            set_brush(brush - 1)
        elif key == "]":
            set_brush(brush + 1)
        elif key.isdigit() and 1 <= int(key) <= len(MATERIALS):
            select_material(MATERIALS[int(key) - 1][0])

    page.on_keyboard_event = on_key

    picker = ft.SegmentedButton(
        selected=[str(material)],
        show_selected_icon=False,
        on_change=lambda e: select_material(int(e.control.selected[0])),
        segments=[
            ft.Segment(
                value=str(mat),
                label=ft.Text(f"{label}"),
                icon=ft.Icon(icon, color=ICON_COLORS.get(mat, hex_color(sand.COLORS[mat]))),
                tooltip=f"{label} ({i})",
            )
            for i, (mat, label, icon) in enumerate(MATERIALS, start=1)
        ],
    )
    play_button = ft.IconButton(ft.Icons.PAUSE, tooltip="Pause (space)", on_click=toggle_playing)
    brush_slider = ft.Slider(
        min=MIN_BRUSH,
        max=MAX_BRUSH,
        divisions=MAX_BRUSH - MIN_BRUSH,
        value=brush,
        label="{value}",
        width=180,
        on_change=lambda e: set_brush(e.control.value),
    )
    stats = ft.Text("", size=12, color=ft.Colors.ON_SURFACE_VARIANT, font_family="monospace")

    page.add(
        ft.Column(
            spacing=12,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Row(
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    width=GRID_WIDTH * CELL_SIZE,
                    controls=[
                        ft.Row(
                            spacing=6,
                            controls=[
                                ft.Text("Sandbox", size=22, weight=ft.FontWeight.BOLD),
                                ft.Text(
                                    "  drag to paint · right-drag erases · scroll resizes brush",
                                    size=12,
                                    color=ft.Colors.ON_SURFACE_VARIANT,
                                ),
                            ],
                        ),
                        stats,
                    ],
                ),
                picker,
                ft.Row(
                    alignment=ft.MainAxisAlignment.CENTER,
                    controls=[
                        play_button,
                        ft.Icon(ft.Icons.BRUSH, size=18, color=ft.Colors.ON_SURFACE_VARIANT),
                        brush_slider,
                        ft.OutlinedButton(
                            "Clear",
                            icon=ft.Icons.DELETE_SWEEP,
                            tooltip="Clear (C)",
                            on_click=lambda: world.clear(),
                        ),
                        ft.OutlinedButton(
                            "Demo",
                            icon=ft.Icons.AUTO_AWESOME,
                            tooltip="Reload the demo scene (D)",
                            on_click=lambda: world.load_demo(),
                        ),
                    ],
                ),
                ft.Container(
                    border_radius=10,
                    clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                    border=ft.Border.all(1, "#2a2c3a"),
                    content=ft.GestureDetector(
                        content=raw_image,
                        mouse_cursor=ft.MouseCursor.PRECISE,
                        drag_interval=10,
                        hover_interval=10,
                        on_pan_down=press,
                        on_pan_update=move,
                        on_pan_end=release,
                        on_pan_cancel=release,
                        on_right_pan_start=lambda e: press(e, erase=True),
                        on_right_pan_update=move,
                        on_right_pan_end=release,
                        on_hover=move,
                        on_exit=leave,
                        on_scroll=scroll,
                    ),
                ),
            ],
        )
    )
    if not page.web:
        await page.window.center()

    async def run_loop():
        frame_times: list[float] = []
        last_stats = 0.0
        while True:
            started = time.monotonic()
            if pointer_down and pointer is not None:
                paint_stroke()
            if playing:
                world.step()
            cursor = (*pointer, brush) if pointer is not None else None
            try:
                await raw_image.render(world.render(cursor))
            except (RuntimeError, TimeoutError):
                return  # window closed — session destroyed

            now = time.monotonic()
            frame_times.append(now)
            frame_times = [t for t in frame_times if t > now - 1.0]
            if now - last_stats > 0.5:
                last_stats = now
                stats.value = f"{len(frame_times):>3} fps · {world.count():>6,} particles"
                try:
                    stats.update()
                except RuntimeError:
                    return
            await asyncio.sleep(max(0.0, 1 / TARGET_FPS - (time.monotonic() - started)))

    task = asyncio.create_task(run_loop())
    BACKGROUND_TASKS.add(task)  # the event loop only holds weak references to tasks
    task.add_done_callback(BACKGROUND_TASKS.discard)


if __name__ == "__main__":
    ft.run(main)

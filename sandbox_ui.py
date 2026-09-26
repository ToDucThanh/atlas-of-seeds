"""The falling-sand screen: material picker, brush, play/pause and a canvas you paint on.

Shared by the standalone sandbox (main.py) and Atlas's cross-sections (world.py), which pass their own
title, a reset action, and anything to draw over the canvas, such as town labels.
"""

from __future__ import annotations

import asyncio
import time
from typing import Callable

import flet as ft

import sand

CELL_SIZE = 4  # logical pixels per cell on screen
TARGET_FPS = 60
MIN_BRUSH, MAX_BRUSH = 1, 20

# (material id, label, icon); keys 1-7 select the offered ones in order.
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


def hex_color(rgb) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(int(c) for c in rgb))


class SandboxView:
    """Build with a sand.World, add `control` to a page, then `start()`; `stop()` ends the frame loop.

    `reset` is (label, action) for the button and the D key; `overlay` controls are drawn over the canvas
    (positioned in canvas pixels, and they don't block painting); `header` sits between the title and
    the tools; `materials` limits the picker to those material ids (Erase is sand.EMPTY); `on_step` runs
    after every simulation step."""

    def __init__(self, page: ft.Page, world: sand.World, title: str, reset: tuple[str, Callable[[], None]],
                 subtitle: str = "drag to paint · right-drag erases · scroll resizes brush",
                 leading: ft.Control | None = None, header: ft.Control | None = None,
                 overlay: list[ft.Control] | None = None, material: int = sand.SAND,
                 materials: list[int] | None = None, on_step: Callable[[sand.World], None] | None = None):
        self.page = page
        self.world = world
        self.reset_action = reset[1]
        self.on_step = on_step
        self.materials = [m for m in MATERIALS if materials is None or m[0] in materials]
        self.material = material
        self.brush = 4
        self.playing = True
        self.pointer_down = False
        self.erasing = False  # right mouse button always erases
        self.pointer: tuple[int, int] | None = None  # cell under the mouse
        self.last_painted: tuple[int, int] | None = None
        self.task: asyncio.Task | None = None
        width, height = world.width * CELL_SIZE, world.height * CELL_SIZE

        # One frame pixel per cell; the client upscales with nearest-neighbour so grains stay crisp and
        # each frame is only world.width x world.height pixels.
        self.raw_image = ft.RawImage(width=width, height=height, fit=ft.BoxFit.FILL,
                                     filter_quality=ft.FilterQuality.NONE)
        self.picker = ft.SegmentedButton(
            selected=[str(material)],
            show_selected_icon=False,
            on_change=lambda e: self.select_material(int(e.control.selected[0])),
            segments=[
                ft.Segment(
                    value=str(mat),
                    label=ft.Text(label),
                    icon=ft.Icon(icon, color=ICON_COLORS.get(mat, hex_color(sand.COLORS[mat]))),
                    tooltip=f"{label} ({i})",
                )
                for i, (mat, label, icon) in enumerate(self.materials, start=1)
            ],
        )
        self.play_button = ft.IconButton(ft.Icons.PAUSE, tooltip="Pause (space)", on_click=self.toggle_playing)
        self.brush_slider = ft.Slider(
            min=MIN_BRUSH, max=MAX_BRUSH, divisions=MAX_BRUSH - MIN_BRUSH, value=self.brush, label="{value}",
            width=180, on_change=lambda e: self.set_brush(e.control.value),
        )
        self.stats = ft.Text("", size=12, color=ft.Colors.ON_SURFACE_VARIANT, font_family="monospace")
        reset_label = reset[0]

        self.control = ft.Column(
            spacing=12,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Row(
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    width=width,
                    controls=[
                        ft.Row(
                            spacing=6,
                            controls=[
                                *([leading] if leading else []),
                                ft.Text(title, size=22, weight=ft.FontWeight.BOLD),
                                ft.Text(f"  {subtitle}", size=12, color=ft.Colors.ON_SURFACE_VARIANT),
                            ],
                        ),
                        self.stats,
                    ],
                ),
                *([header] if header else []),
                self.picker,
                ft.Row(
                    alignment=ft.MainAxisAlignment.CENTER,
                    controls=[
                        self.play_button,
                        ft.Icon(ft.Icons.BRUSH, size=18, color=ft.Colors.ON_SURFACE_VARIANT),
                        self.brush_slider,
                        ft.OutlinedButton("Clear", icon=ft.Icons.DELETE_SWEEP, tooltip="Clear (C)",
                                          on_click=lambda: self.world.clear()),
                        ft.OutlinedButton(reset_label, icon=ft.Icons.AUTO_AWESOME, tooltip=f"{reset_label} (D)",
                                          on_click=lambda: self.reset_action()),
                    ],
                ),
                ft.Container(
                    border_radius=10,
                    clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                    border=ft.Border.all(1, "#2a2c3a"),
                    content=ft.GestureDetector(
                        # Overlays live inside the detector, so painting works right through them.
                        content=ft.Stack([self.raw_image, *(overlay or [])], width=width, height=height),
                        mouse_cursor=ft.MouseCursor.PRECISE,
                        drag_interval=10,
                        hover_interval=10,
                        on_pan_down=self.press,
                        on_pan_update=self.move,
                        on_pan_end=self.release,
                        on_pan_cancel=self.release,
                        on_right_pan_start=lambda e: self.press(e, erase=True),
                        on_right_pan_update=self.move,
                        on_right_pan_end=self.release,
                        on_secondary_tap_down=lambda e: self.press(e, erase=True),  # a right-click erases too
                        on_secondary_tap_up=self.release,
                        on_hover=self.move,
                        on_exit=self.leave,
                        on_scroll=self.scroll,
                    ),
                ),
            ],
        )

    # ------------------------------------------------------------ pointer input
    # These arrive up to 100 times a second and only record where the pointer is; the frame loop draws.
    # Each switches off Flet's auto-update, which would otherwise re-check the whole page after every
    # event - cheap in the standalone sandbox, but inside Atlas it halved the frame rate while painting.

    def to_cell(self, pos: ft.Offset) -> tuple[int, int]:
        return int(pos.x // CELL_SIZE), int(pos.y // CELL_SIZE)

    def press(self, e, erase=False):
        ft.context.disable_auto_update()
        self.pointer_down, self.erasing = True, erase
        self.pointer = self.to_cell(e.local_position)
        self.last_painted = None

    def move(self, e):
        ft.context.disable_auto_update()
        self.pointer = self.to_cell(e.local_position)

    def release(self, e=None):
        ft.context.disable_auto_update()
        self.pointer_down = self.erasing = False
        if self.pointer is not None and not (0 <= self.pointer[0] < self.world.width
                                             and 0 <= self.pointer[1] < self.world.height):
            self.pointer = None  # a stroke let go off the canvas: no brush ring stuck at the edge

    def leave(self, e):
        ft.context.disable_auto_update()
        if not self.pointer_down:  # mid-stroke, the pan keeps reporting past the edge, so the stroke carries on
            self.pointer = None

    def scroll(self, e: ft.ScrollEvent):
        if e.scroll_delta and e.scroll_delta.y:
            self.set_brush(self.brush + (-1 if e.scroll_delta.y > 0 else 1))

    def paint_stroke(self):
        """Paint discs along the path since the last frame so fast strokes stay continuous."""
        mat = sand.EMPTY if self.erasing else self.material
        x1, y1 = self.pointer
        x0, y0 = self.last_painted or self.pointer
        steps = max(1, int(max(abs(x1 - x0), abs(y1 - y0)) / max(self.brush / 2, 1)))
        for i in range(1, steps + 1):
            t = i / steps
            self.world.paint(round(x0 + (x1 - x0) * t), round(y0 + (y1 - y0) * t), self.brush, mat)
        self.last_painted = self.pointer

    # ------------------------------------------------------------------ toolbar

    def select_material(self, value: int):
        self.material = value
        self.picker.selected = [str(value)]
        self.picker.update()

    def set_brush(self, value: int):
        self.brush = max(MIN_BRUSH, min(MAX_BRUSH, int(value)))
        self.brush_slider.value = self.brush
        self.brush_slider.update()

    def toggle_playing(self):
        self.playing = not self.playing
        self.play_button.icon = ft.Icons.PAUSE if self.playing else ft.Icons.PLAY_ARROW
        self.play_button.tooltip = "Pause (space)" if self.playing else "Play (space)"
        self.play_button.update()

    def on_key(self, e: ft.KeyboardEvent):
        if e.ctrl or e.meta or e.alt:
            return  # Cmd+C and friends belong to the system, not to Clear
        key = e.key.lower()
        if key in ("space", " "):
            self.toggle_playing()
        elif key == "c":
            self.world.clear()
        elif key == "d":
            self.reset_action()
        elif key == "[":
            self.set_brush(self.brush - 1)
        elif key == "]":
            self.set_brush(self.brush + 1)
        elif key.isdigit() and 1 <= int(key) <= len(self.materials):
            self.select_material(self.materials[int(key) - 1][0])

    # ---------------------------------------------------------------- frame loop

    def start(self):
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._run())

    def stop(self):
        if self.task is not None:
            self.task.cancel()
            self.task = None

    async def _run(self):
        frame_times: list[float] = []
        last_stats = 0.0
        while True:
            started = time.monotonic()
            if self.pointer_down and self.pointer is not None:
                self.paint_stroke()
            if self.playing:
                self.world.step()
                if self.on_step:
                    self.on_step(self.world)
            cursor = (*self.pointer, self.brush) if self.pointer is not None else None
            try:
                await self.raw_image.render(self.world.render(cursor))
            except (RuntimeError, TimeoutError):
                return  # window closed, or the view left the page

            now = time.monotonic()
            frame_times.append(now)
            frame_times = [t for t in frame_times if t > now - 1.0]
            if now - last_stats > 0.5:
                last_stats = now
                self.stats.value = f"{len(frame_times):>3} fps · {self.world.count():>6,} particles"
                try:
                    self.stats.update()
                except RuntimeError:
                    return
            await asyncio.sleep(max(0.0, 1 / TARGET_FPS - (time.monotonic() - started)))

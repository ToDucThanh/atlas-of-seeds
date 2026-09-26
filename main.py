import flet as ft

import sand
import sandbox_ui

GRID_WIDTH = 240
GRID_HEIGHT = 150


async def main(page: ft.Page):
    page.title = "Sandbox"
    page.theme_mode = ft.ThemeMode.DARK
    page.theme = ft.Theme(color_scheme_seed="#deb86e")
    page.bgcolor = "#0b0c12"
    page.padding = 20
    if not page.web:
        page.window.width = GRID_WIDTH * sandbox_ui.CELL_SIZE + 60
        page.window.height = GRID_HEIGHT * sandbox_ui.CELL_SIZE + 230

    world = sand.World(GRID_WIDTH, GRID_HEIGHT)
    world.load_demo()
    view = sandbox_ui.SandboxView(page, world, "Sandbox", reset=("Demo", world.load_demo))
    page.on_keyboard_event = view.on_key
    page.add(view.control)
    if not page.web:
        await page.window.center()
    view.start()


if __name__ == "__main__":
    ft.run(main)

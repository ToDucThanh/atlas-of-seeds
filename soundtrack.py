"""Plays the sounds from `sound.py` in a Flet page.

The macOS desktop client never finishes loading an `Audio` whose `src` is bytes or base64, but it plays a
file path, so on desktop the WAVs are written to a temp folder (once per seed, cached across runs) and
the players point at them. In the browser the server's disk is out of reach, so there they get bytes.
Nor does it load a `src` given to a player that was created without one, so every player starts with a
real source; the drone's is swapped for each world's own.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Callable

import flet as ft
import flet_audio as fta

import sound

DRONE_VOLUME = 0.32
CUE_VOLUME = {"battle": 0.65, "war": 0.55, "sack": 0.6, "crown": 0.3}  # usurpers are common: keep the bell soft
CUE_GAP = {"crown": 3.0}  # seconds before a cue of the same kind may sound again (default below)
DEFAULT_CUE_GAP = 0.8
REVEAL_VOLUME = 0.35
FADE_PER_SECOND = 0.25  # drone volume change per second while fading in or out
CALL_TIMEOUT = 5.0  # seconds before giving up on an audio call
CACHE = Path(tempfile.gettempdir()) / "atlas-sound"
CACHE_VERSION = 1  # bump when sound.py changes, so cached WAVs are made afresh


class Soundtrack:
    """A looping drone for the current world, one-shot cues for history's events, and the reveal wash.

    The drone swells in whenever `wants_drone()` is true (a replay, or cinema mode) and fades away
    otherwise. Every audio call has a timeout, and repeated failures turn the soundtrack off rather than
    stall the app."""

    def __init__(self, page: ft.Page, wants_drone: Callable[[], bool]):
        self.page = page
        self.wants_drone = wants_drone
        self.muted = False
        self.broken = False
        self.failures = 0
        self.closed = False  # the window has gone
        self.drone: fta.Audio | None = None
        self.drone_ready = False  # whether the drone holds a world's loop rather than its placeholder
        self.drone_started = False  # whether the drone has ever been started (then it is resumed)
        self.drone_playing = False
        self.cue_players: dict[str, list[fta.Audio]] = {}
        self.last_cue: dict[str, float] = {}
        self.reveal_player: fta.Audio | None = None
        self.tasks: set[asyncio.Task] = set()

    def _src(self, name: str, make: Callable[[], bytes]) -> str | bytes:
        data_path = CACHE / f"{name}-v{CACHE_VERSION}.wav"
        if self.page.web:
            return data_path.read_bytes() if data_path.exists() else make()
        if not data_path.exists():
            CACHE.mkdir(parents=True, exist_ok=True)
            tmp = data_path.with_suffix(".part")
            tmp.write_bytes(make())
            tmp.replace(data_path)
        return str(data_path)

    async def load(self):
        """Build the cue and reveal players (synthesised off the event loop) and start the drone fader."""
        def build():
            cues = {kind: [self._src(f"cue-{kind}-{i}", lambda k=kind, p=pan: sound.cue(k, p))
                           for i, pan in enumerate(sound.PANS)] for kind in sound.CUES}
            return cues, self._src("reveal", sound.reveal)

        try:
            cues, reveal = await asyncio.to_thread(build)
        except OSError as ex:
            print(f"Sound disabled: {ex!r}")
            self.broken = True
            return
        self.cue_players = {kind: [fta.Audio(src=s, release_mode=fta.ReleaseMode.STOP, volume=CUE_VOLUME[kind])
                                   for s in srcs] for kind, srcs in cues.items()}
        self.reveal_player = fta.Audio(src=reveal, release_mode=fta.ReleaseMode.STOP, volume=REVEAL_VOLUME)
        self.drone = fta.Audio(src=reveal, release_mode=fta.ReleaseMode.LOOP, volume=0.0)  # placeholder source
        self._spawn(self._fader())

    async def drone_for(self, seed: int) -> str | bytes | None:
        """The drone for a world, synthesised off the event loop (and cached on desktop); None if that fails."""
        try:
            return await asyncio.to_thread(self._src, f"drone-{seed}", lambda: sound.drone(seed))
        except OSError as ex:
            print(f"Sound disabled: {ex!r}")
            self.broken = True
            return None

    def set_drone(self, src: str | bytes | None):
        """Switch to another world's drone; it swells in from silence the next time it is wanted."""
        if src is None or self.drone is None:
            return
        self.drone.src, self.drone.volume = src, 0.0
        self.drone_ready = True
        self.drone_started = self.drone_playing = False  # a new source starts stopped
        self._safe_update(self.drone)

    def set_muted(self, muted: bool):
        self.muted = muted

    def cue(self, kind: str, pan: float = 0.0):
        """Play a history marker's sound; `pan` from -1 (left) to 1 (right) picks the nearest baked position."""
        now = asyncio.get_running_loop().time()
        if (self.muted or self.broken or kind not in self.cue_players
                or now - self.last_cue.get(kind, -1e9) < CUE_GAP.get(kind, DEFAULT_CUE_GAP)):
            return
        self.last_cue[kind] = now
        i = min(range(len(sound.PANS)), key=lambda j: abs(sound.PANS[j] - pan))
        self._spawn(self._call(self.cue_players[kind][i].play()))

    def reveal(self):
        if not (self.muted or self.broken or self.reveal_player is None):
            self._spawn(self._call(self.reveal_player.play()))

    # ------------------------------------------------------------------ internals

    def _spawn(self, coro):
        task = asyncio.create_task(coro)
        self.tasks.add(task)  # the event loop only holds weak references to tasks
        task.add_done_callback(self.tasks.discard)

    async def _call(self, awaitable) -> bool:
        try:
            await asyncio.wait_for(awaitable, CALL_TIMEOUT)
            return True
        except RuntimeError:
            self.closed = True  # window closed
            return False
        except Exception as ex:  # noqa: BLE001 - a timeout or client error must never break the app
            self.failures += 1
            if self.failures >= 3 and not self.broken:
                print(f"Sound disabled after repeated errors: {ex!r}")
                self.broken = True
            return False

    def _safe_update(self, ctl: ft.BaseControl) -> bool:
        try:
            ctl.update()
            return True
        except RuntimeError:
            self.closed = True
            return False

    async def _fader(self):
        """Ease the drone's volume toward its target; pause it once silent, so it costs nothing."""
        step = 0.05
        while not (self.broken or self.closed):
            await asyncio.sleep(step)
            if self.drone is None or not self.drone_ready:
                continue
            target = DRONE_VOLUME if (self.wants_drone() and not self.muted) else 0.0
            vol = self.drone.volume
            if target > 0 and not self.drone_playing:
                if not await self._call(self.drone.resume() if self.drone_started else self.drone.play()):
                    continue  # try again on the next tick, until too many failures turn sound off
                self.drone_started = self.drone_playing = True
            if abs(vol - target) > 1e-3:
                delta = FADE_PER_SECOND * step
                self.drone.volume = round(min(vol + delta, target) if target > vol else max(vol - delta, target), 3)
                self._safe_update(self.drone)
            elif target == 0 and self.drone_playing:
                if await self._call(self.drone.pause()):
                    self.drone_playing = False

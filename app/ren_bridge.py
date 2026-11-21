"""Local file mailbox for the game sandbox. Requests never contain commands."""
import asyncio
from contextlib import suppress
import logging
import os
from pathlib import Path
import tempfile
import time

from pydantic import ValidationError

from app.ren import DialogueError, GameState, Turn

REQUEST = "rin_dialogue_request.txt"
RESPONSE = "rin_dialogue_response.txt"
HEARTBEAT = "rin_dialogue_ready.txt"
STATE = "rin_dialogue_state.txt"
PROTOCOL = "RIN1"
MAX_AGE = 90
log = logging.getLogger(__name__)


def read_fields(path):
    try:
        if time.time() - path.stat().st_mtime > MAX_AGE:
            return None
        with path.open("rb") as incoming:
            raw = incoming.read(4097)
        if len(raw) > 4096:
            return None
        fields = raw.decode("utf-8-sig").splitlines()
    except (FileNotFoundError, PermissionError, UnicodeError):
        return None
    # Lua writes this last marker after all fields. Ignore partial writes.
    if len(fields) < 2 or fields[0] != PROTOCOL or fields[-1] != "END":
        return None
    return fields[1:-1]


def game_fields(save_id, friendship, romance, enabled, opened, player, *extra):
    if enabled not in ("0", "1") or opened not in ("0", "1"):
        raise ValueError("Invalid relationship flags")
    result = dict(save_id=save_id, friendship=friendship, romance=romance, player_name=player,
                  romance_enabled=enabled == "1", romance_open=opened == "1")
    if extra:
        name, observed_at, tier, days, kills, fatigue, activity, events = extra
        recent = []
        for event in events.split(",") if events else ():
            kind, at = event.split(":")
            recent.append(dict(kind=kind, at=at))
        result.update(companion_name=name, observed_at=observed_at, relationship_tier=tier or None,
                      days_together=days or None, companion_kills=kills or None,
                      combat_fatigue=fatigue or None, activity=activity or None, events=recent)
    return result


def read_request(path):
    fields = read_fields(path)
    if fields is None or len(fields) not in (8, 16):
        return None
    request_id, save_id, friendship, romance, enabled, opened, player, message, *extra = fields
    try:
        return Turn(request_id=request_id, message=message,
                    **game_fields(save_id, friendship, romance, enabled, opened, player, *extra))
    except (ValidationError, ValueError):
        return None


def read_state(path):
    fields = read_fields(path)
    if fields is None or len(fields) != 14:
        return None
    try:
        return GameState(**game_fields(*fields))
    except (ValidationError, ValueError):
        return None


def atomic_text(path, body):
    atomic_bytes(path, body.encode("utf-8"))


def atomic_bytes(path, body):
    name = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".ren-", delete=False) as output:
            name = Path(output.name)
            output.write(body)
        os.replace(name, path)
    finally:
        if name is not None:
            name.unlink(missing_ok=True)


class Mailbox:
    def __init__(self, directory: Path, engine, *, mode="local"):
        self.directory = directory
        self.engine = engine
        self.finished = None
        self.mode = mode

    async def sync_loop(self):
        latest = None
        while True:
            state = read_state(self.directory / STATE)
            if state and state != latest:
                try:
                    await self.engine.sync_state(state)
                    latest = state
                except DialogueError as error:
                    log.warning("Game status sync: %s", error)
            await asyncio.sleep(2)

    async def poll(self):
        turn = read_request(self.directory / REQUEST)
        if turn is None:
            return
        response_path = self.directory / RESPONSE
        if self.finished is None:
            try:
                with response_path.open(encoding="utf-8") as output:
                    version, identifier = output.readline().strip(), output.readline().strip()
                if version == PROTOCOL and identifier == turn.request_id:
                    self.finished = (identifier, None)
            except (FileNotFoundError, PermissionError, UnicodeError):
                pass
        if not self.finished or self.finished[0] != turn.request_id:
            try:
                reply = await self.engine.respond(turn)
                status = "OK"
            except DialogueError as error:
                log.warning("%s", error)
                status, reply = "ERROR", str(error)
            output = f"{PROTOCOL}\n{turn.request_id}\n{status}\n{reply}\nEND\n"
            self.finished = (turn.request_id, output)
        if self.finished[1] is not None:
            atomic_text(response_path, self.finished[1])
            self.finished = (turn.request_id, None)

    async def run(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        heartbeat = self.directory / HEARTBEAT
        refreshed = 0
        syncing = asyncio.create_task(self.sync_loop())
        try:
            while True:
                try:
                    now = time.monotonic()
                    if now - refreshed >= 2:
                        atomic_text(heartbeat, f"{PROTOCOL}\n{int(time.time())}\n{self.mode}\n")
                        refreshed = now
                    await self.poll()
                except OSError as error:
                    log.warning("Mailbox unavailable: %s", error)
                    await asyncio.sleep(1)
                await asyncio.sleep(0.2)
        finally:
            syncing.cancel()
            with suppress(asyncio.CancelledError):
                await syncing
            try:
                heartbeat.unlink(missing_ok=True)
            except OSError:
                log.warning("Could not remove the Ren Dialogue heartbeat")

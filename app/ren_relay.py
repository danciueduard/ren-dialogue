"""Forward the game mailbox to the backend selected in Project Zomboid."""

import asyncio
from contextlib import suppress
from getpass import getpass
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from app.ren import DialogueError, MAX_REPLY
from app.ren_bridge import Mailbox, PROTOCOL, atomic_text
from app.ren_config import ren_env

SETTINGS = "rin_dialogue_backend.txt"
CONNECTION = "rin_dialogue_connection.txt"


def normalize_address(value):
    address = value.strip().rstrip("/")
    if not address or len(address) > 200 or any(ch.isspace() or ord(ch) < 32 for ch in address):
        raise DialogueError("Enter a backend address such as http://192.168.1.16:8011.")
    if "://" not in address:
        address = "http://" + address
    try:
        parsed = urlsplit(address)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or "\\" in address or parsed.port == 0):
            raise ValueError
        httpx.URL(address)
    except (ValueError, httpx.InvalidURL) as error:
        raise DialogueError("Enter a valid HTTP or HTTPS backend address.") from error
    return address


def read_setting(directory):
    try:
        with (directory / SETTINGS).open(encoding="utf-8-sig") as reader:
            version, value, end = [reader.readline(256).strip() for _ in range(3)]
    except (FileNotFoundError, PermissionError, UnicodeError):
        return None
    return value if version == PROTOCOL and end == "END" and value != "LOCAL" else None


class RemoteDialogue:
    def __init__(self, directory, client, mirror=None):
        self.directory = directory
        self.client = client
        self.mirror = mirror

    def address(self):
        if self.mirror:
            return self.mirror.address
        return read_setting(self.directory)

    async def request(self, address, route, **kwargs):
        try:
            response = await self.client.request("POST" if "json" in kwargs else "GET",
                                                 normalize_address(address) + route, **kwargs)
            if response.status_code in (401, 403):
                raise DialogueError("The Ren Dialogue backend rejected the access key. Restart the PC relay with the host key.")
            response.raise_for_status()
            return response.json()
        except httpx.TimeoutException as error:
            raise DialogueError("The laptop backend timed out.") from error
        except (httpx.HTTPError, ValueError) as error:
            raise DialogueError("Can't reach the dialogue backend. Check its address and the host launcher.") from error

    async def respond(self, turn):
        address = self.address()
        if not address:
            raise DialogueError("Set a backend address in the Ren Dialogue menu first.")
        result = await self.request(address, "/chat", json=turn.model_dump())
        reply = result.get("reply") if isinstance(result, dict) else None
        if not isinstance(reply, str) or not 1 <= len(reply) <= MAX_REPLY or len(reply.splitlines()) != 1:
            raise DialogueError("The backend did not return a valid dialogue reply.")
        return reply

    async def sync_state(self, state):
        address = self.address()
        if not address:
            raise DialogueError("Set a backend address in the Ren Dialogue menu first.")
        try:
            response = await self.client.put(normalize_address(address) + "/state",
                                             json=state.model_dump(), timeout=4)
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise DialogueError("The host could not sync the live game status.") from error

    async def monitor(self):
        last_checked = None
        while True:
            address = self.address()
            if address:
                try:
                    if self.mirror:
                        await self.mirror.refresh(self.client)
                        status, message = "OK", "Ren Dialogue connected. Database copy is up to date."
                    else:
                        result = await self.request(address, "/connection", timeout=4)
                        if not isinstance(result, dict) or result.get("character") != "Rin" or result.get("protocol") != 1:
                            raise DialogueError("That address isn't a compatible Ren Dialogue backend.")
                        status, message = "OK", "Ren Dialogue connected."
                except DialogueError as error:
                    status, message = "ERROR", str(error)
                state = address, status, message
                if state != last_checked:
                    try:
                        atomic_text(self.directory / CONNECTION, f"{PROTOCOL}\n{address}\n{status}\n{message}\nEND\n")
                        last_checked = state
                    except PermissionError:
                        pass  # The game may briefly have the status file open on Windows.
            elif last_checked is not None:
                try:
                    (self.directory / CONNECTION).unlink(missing_ok=True)
                    last_checked = None
                except PermissionError:
                    pass
            await asyncio.sleep(2)


async def run(directory, token, mirror=None):
    directory.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=58, trust_env=False, headers={
        "Authorization": f"Bearer {token}",
    }) as client:
        remote = RemoteDialogue(directory, client, mirror)
        monitor = asyncio.create_task(remote.monitor())
        try:
            await Mailbox(directory, remote, mode="relay").run()
        finally:
            monitor.cancel()
            with suppress(asyncio.CancelledError):
                await monitor


if __name__ == "__main__":
    lua = Path(ren_env("LUA_DIR", str(Path.home() / "Zomboid" / "Lua")))
    token = ren_env("API_TOKEN") or getpass("Ren Dialogue host access key: ").strip()
    print("Ren Dialogue relay is running. Set the host address in the in-game Ren Dialogue menu.", flush=True)
    try:
        asyncio.run(run(lua, token))
    except KeyboardInterrupt:
        pass

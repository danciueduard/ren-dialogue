"""Small Windows setup and lifecycle helpers for the Ren Dialogue desktop app."""

import asyncio
from contextlib import suppress
import ipaddress
import os
import re
import socket
import subprocess
import threading
import time

import httpx
import uvicorn

from app.main import create_app
from app.ren import DialogueError
from app.ren_bridge import PROTOCOL, atomic_text
from app.ren_relay import SETTINGS, normalize_address, run as run_relay
from app.ren_storage import DatabaseMirror

DEFAULT_MODEL = "qwen3.5:4b"
PORT = 8011


def make_pairing_code(address, token):
    return f"{normalize_address(address)}|{token}"


def parse_pairing_code(text):
    address, separator, token = text.strip().partition("|")
    if not separator or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", token):
        raise DialogueError("Paste the full connection code copied from the host PC.")
    return normalize_address(address), token


def current_lan_address():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            # Determine the preferred local interface without transmitting data.
            probe.connect(("192.0.2.1", 9))
            address = probe.getsockname()[0]
        except OSError:
            address = ""
    if not address or address.startswith(("127.", "169.254.")):
        raise DialogueError("Connect this PC to your home network and try hosting again.")
    return address


def save_backend(lua_dir, address=None):
    lua_dir.mkdir(parents=True, exist_ok=True)
    atomic_text(lua_dir / SETTINGS, f"{PROTOCOL}\n{address or 'LOCAL'}\nEND\n")


def ensure_private_firewall(host):
    host = str(ipaddress.IPv4Address(host))
    command = (
        "$ErrorActionPreference='Stop';"
        "if (!(Get-NetFirewallRule -Name 'RinDialogue-LAN-8011' -ErrorAction SilentlyContinue)) {"
        "New-NetFirewallRule -Name 'RinDialogue-LAN-8011' -DisplayName 'Rin Dialogue LAN' "
        "-Direction Inbound -Action Allow -Protocol TCP -LocalPort 8011 -Profile Private "
        "-RemoteAddress LocalSubnet | Out-Null}"
    )
    import base64
    encoded = base64.b64encode(command.encode("utf-16-le")).decode("ascii")
    script = (
        f"$ip=Get-NetIPAddress -AddressFamily IPv4 -IPAddress '{host}' -ErrorAction SilentlyContinue;"
        "if (!$ip -or !(Get-NetConnectionProfile -InterfaceIndex $ip.InterfaceIndex "
        "| Where-Object NetworkCategory -eq 'Private')) { exit 2 };"
        "netsh advfirewall firewall show rule name='Rin Dialogue LAN' | Out-Null;"
        "if ($LASTEXITCODE -eq 0) { exit 0 };"
        "try { $p=Start-Process powershell.exe -Verb RunAs -WindowStyle Hidden -Wait -PassThru "
        f"-ArgumentList '-NoProfile','-EncodedCommand','{encoded}'; exit $p.ExitCode }}"
        " catch { exit 1 }"
    )
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False)
    if result.returncode == 2:
        raise DialogueError("In Windows Settings, set this trusted home network to Private, then try hosting again.")
    if result.returncode:
        raise DialogueError("Windows did not allow the firewall rule. Approve the Windows prompt and try again.")


async def connect_to_host(address, token, database):
    async with httpx.AsyncClient(headers={"Authorization": "Bearer " + token},
                                 timeout=4, trust_env=False) as client:
        try:
            result = await client.get(address + "/connection")
            if result.status_code == 401:
                raise DialogueError("The host rejected the code. Copy a fresh code from the host.")
            result.raise_for_status()
            if result.json() != {"character": "Rin", "protocol": 1}:
                raise ValueError
        except (httpx.HTTPError, ValueError) as error:
            raise DialogueError("Can't reach the host. Check that both PCs are on the same network and hosting is active.") from error
        mirror = DatabaseMirror(address, database)
        await mirror.refresh(client)
        return mirror


class Backend:
    def __init__(self, database, lua_dir, host, token=None, *, port=PORT, ollama=None, model=None):
        database.parent.mkdir(parents=True, exist_ok=True)
        self.ollama = ollama
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.socket.bind((host, port))
        except OSError as error:
            self.socket.close()
            raise DialogueError("Port 8011 is already in use. Close the other dialogue launcher and try again.") from error
        self.port = self.socket.getsockname()[1]
        self.server = uvicorn.Server(uvicorn.Config(
            create_app(database, lua_dir, use_mailbox=token is None, api_token=token or "",
                       ollama_url=ollama.chat_url if ollama else None, ollama_model=model),
            host=host, port=self.port, log_level="warning", log_config=None, access_log=False,
            loop="asyncio", http="h11", ws="none", lifespan="on", timeout_graceful_shutdown=3,
        ))
        self.thread = threading.Thread(target=self.server.run, kwargs={"sockets": [self.socket]}, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 6
        while self.thread.is_alive() and not self.server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        if not self.server.started:
            self.stop()
            raise DialogueError("The Ren Dialogue engine couldn't start. Check that its data folder is writable.")

    def stop(self):
        self.server.should_exit = True
        self.thread.join(6)
        self.socket.close()
        if self.ollama:
            self.ollama.stop()


class Relay:
    def __init__(self, lua_dir, token, mirror=None):
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=lambda: asyncio.run(self.loop(lua_dir, token, mirror)), daemon=True)
        self.thread.start()

    async def loop(self, lua_dir, token, mirror):
        task = asyncio.create_task(run_relay(lua_dir, token, mirror))
        try:
            while not self.stopped.is_set() and not task.done():
                await asyncio.sleep(0.2)
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    def stop(self):
        self.stopped.set()
        self.thread.join(4)

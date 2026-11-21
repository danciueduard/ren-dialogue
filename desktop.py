"""Ren Dialogue's setup app for Windows."""

import asyncio
from contextlib import ExitStack
import os
from pathlib import Path
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tempfile import TemporaryDirectory

import httpx

from app.desktop_support import (
    Backend, DEFAULT_MODEL, PORT, Relay, connect_to_host, current_lan_address, ensure_private_firewall,
    make_pairing_code, parse_pairing_code, save_backend,
)
from app.portable_ollama import PortableOllama
from app.ren import DialogueError
from app.ren_config import ren_env
from app.ren_lan import access_key
from app.ren_storage import import_legacy_database


ROOT = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
DATA = ROOT / "data"
MODEL_DIR = ROOT / "ai model"
LEGACY_DATA = (Path(os.getenv("LOCALAPPDATA", Path.home())) / "RinDialogue"
               if getattr(sys, "frozen", False) else ROOT / "database")
MODES = {
    "local": ("This PC", "Run the engine and play Project Zomboid on this computer."),
    "host": ("Host for another PC", "Run Ollama here and copy a connection code to your gaming PC."),
    "connect": ("Connect to a host", "Paste the host's connection code on the PC running the game."),
}
BG = "#f1f4f1"
SURFACE = "#ffffff"
INK = "#1c2a24"
MUTED = "#50625a"
ACCENT = "#236650"


class Setup:
    def __init__(self, root):
        self.root = root
        self.events = queue.Queue()
        self.game_dir = Path.home() / "Zomboid"
        if custom := ren_env("LUA_DIR"):
            self.game_dir = Path(custom).parent
        self.model = ren_env("MODEL", DEFAULT_MODEL)
        self.mode = tk.StringVar(value="local")
        self.status = tk.StringVar(value="Choose where you're running Project Zomboid.")
        self.pairing = tk.StringVar()
        self.service = None
        self.code = None
        self.busy = False
        self.running = False
        root.title("Ren Dialogue Bridge")
        root.geometry("650x510")
        root.minsize(600, 480)
        root.configure(background=BG)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.configure_theme()
        self.build()
        self.show_mode()
        root.after(80, self.receive)

    def configure_theme(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", font=("Segoe UI", 10), background=BG, foreground=INK)
        style.configure("Card.TFrame", background=SURFACE)
        style.configure("Card.TLabel", background=SURFACE, foreground=INK)
        style.configure("Hint.TLabel", background=SURFACE, foreground=MUTED)
        style.configure("Status.TLabel", background=BG, foreground=INK, font=("Segoe UI", 10))
        style.configure("Action.TButton", background=ACCENT, foreground="white", padding=(14, 9))
        style.map("Action.TButton", background=[("active", "#19513f"), ("disabled", "#8aa39a")])
        style.configure("TButton", padding=(10, 6))
        style.configure("TRadiobutton", padding=(10, 6))

    def build(self):
        self.layout = ttk.Frame(self.root, padding=(24, 18))
        self.layout.pack(fill="both", expand=True)
        ttk.Label(self.layout, text="Ren Dialogue Bridge", font=("Segoe UI", 24, "bold")).pack(anchor="w")
        ttk.Label(self.layout, text="Connects Ollama to the Ren Dialogue Workshop add-on").pack(anchor="w", pady=(0, 14))
        choices = ttk.Frame(self.layout)
        choices.pack(fill="x", pady=(0, 10))
        self.radios = []
        for key, (label, _) in MODES.items():
            control = ttk.Radiobutton(choices, text=label, value=key, variable=self.mode, command=self.show_mode)
            control.pack(side="left", padx=(0, 6))
            self.radios.append(control)
        self.card = ttk.Frame(self.layout, style="Card.TFrame", padding=(16, 13))
        self.card.pack(fill="both", expand=True)
        self.head = ttk.Label(self.card, style="Card.TLabel", font=("Segoe UI", 13, "bold"))
        self.head.pack(anchor="w")
        self.detail = ttk.Label(self.card, style="Hint.TLabel", wraplength=510)
        self.detail.pack(anchor="w", pady=(4, 11))
        self.box = ttk.Frame(self.card, style="Card.TFrame")
        self.box.pack(fill="x")
        self.commands = ttk.Frame(self.card, style="Card.TFrame")
        self.commands.pack(side="bottom", fill="x", pady=(8, 0))
        self.start_button = ttk.Button(self.commands, style="Action.TButton", command=self.start)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(self.commands, text="Stop", command=self.stop)
        self.stop_button.pack(side="left", padx=(8, 0))
        self.copy_button = ttk.Button(self.commands, text="Copy connection code", command=self.copy)
        self.status_label = ttk.Label(self.layout, textvariable=self.status, style="Status.TLabel", wraplength=570)
        self.status_label.pack(fill="x", anchor="w", pady=(12, 4))
        self.footer = ttk.Frame(self.layout)
        self.footer.pack(fill="x")
        ttk.Button(self.footer, text="Game folder…", command=self.select_folder).pack(side="left")

    def show_mode(self):
        mode = self.mode.get()
        title, description = MODES[mode]
        self.head.configure(text=title)
        self.detail.configure(text=description)
        for widget in self.box.winfo_children():
            widget.destroy()
        self.download_button = None
        self.copy_button.pack_forget()
        if mode == "connect":
            ttk.Label(self.box, text="Connection code", style="Card.TLabel").pack(anchor="w")
            entry = ttk.Entry(self.box, textvariable=self.pairing, show="•")
            entry.pack(fill="x", pady=(4, 6))
            entry.bind("<Return>", lambda _event: self.start())
        else:
            self.download_button = ttk.Button(self.box, text="Download Ren model", command=self.download)
            self.download_button.pack(side="left")
        if mode == "host":
            self.copy_button.pack(side="right")
        self.start_button.configure(text={"local": "Start Ren", "host": "Start hosting", "connect": "Connect"}[mode])
        self.controls()

    def controls(self):
        available = not self.busy and not self.running
        self.start_button.configure(state="normal" if available else "disabled")
        if self.download_button:
            self.download_button.configure(state="normal" if available else "disabled")
        self.stop_button.configure(state="normal" if self.running and not self.busy else "disabled")
        self.copy_button.configure(state="normal" if self.code else "disabled")
        for radio in self.radios:
            radio.configure(state="normal" if available else "disabled")

    def post(self, text):
        self.events.put(("status", text))

    def work(self, action, done):
        if self.busy:
            return
        self.busy = True
        self.controls()
        def run():
            try:
                self.events.put(("done", done, action()))
            except DialogueError as error:
                self.events.put(("error", str(error)))
            except (httpx.HTTPError, OSError, ValueError) as error:
                self.events.put(("error", f"Ren Dialogue setup couldn't finish. {error}"))
        threading.Thread(target=run, daemon=True).start()

    def receive(self):
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == "status":
                    self.status.set(event[1])
                    continue
                self.busy = False
                if event[0] == "done":
                    event[1](event[2])
                else:
                    self.status.set(event[1])
                self.controls()
        except queue.Empty:
            pass
        self.root.after(80, self.receive)

    def download(self):
        if self.running or self.busy:
            return
        self.status.set("Checking Ollama…")
        def download():
            with PortableOllama(MODEL_DIR, self.post) as ollama:
                return ollama.pull(self.model, self.post)
        self.work(download,
                  lambda downloaded: self.status.set(
                      "Ren's model is ready. You can start now." if downloaded
                      else "Ren's model is already installed. You can start now."
                  ))

    def select_folder(self):
        if self.running or self.busy:
            self.status.set("Stop Ren before changing the game folder.")
            return
        chosen = filedialog.askdirectory(initialdir=str(self.game_dir if self.game_dir.exists() else Path.home()),
                                         title="Choose your Zomboid folder")
        if chosen:
            self.game_dir = Path(chosen)
            self.status.set(f"Game folder set to {self.game_dir}")

    def start(self, *, download_if_missing=False):
        if self.running or self.busy:
            return
        mode, code, folder = self.mode.get(), self.pairing.get(), self.game_dir / "Lua"
        self.status.set("Connecting…" if mode == "connect" else "Checking Ollama…")
        def action():
            if mode == "connect":
                address, token = parse_pairing_code(code)
                self.post("Syncing the host database…")
                mirror = asyncio.run(connect_to_host(address, token, DATA / "host-rin.db"))
                save_backend(folder, address)
                return Relay(folder, token, mirror), None, "Connected. Open Zomboid and talk to your companion."

            if not download_if_missing and not (MODEL_DIR / "ollama" / "ollama.exe").is_file():
                return None
            with ExitStack() as cleanup:
                ollama = cleanup.enter_context(PortableOllama(MODEL_DIR, self.post))
                if self.model not in ollama.models():
                    if not download_if_missing:
                        return None
                    ollama.pull(self.model, self.post)
                database = DATA / "rin.db"
                host, token, pairing = "127.0.0.1", None, None
                if mode == "local":
                    save_backend(folder)
                    message = "The bridge is running. Open Zomboid and talk to your companion."
                else:
                    host = current_lan_address()
                    token = ren_env("API_TOKEN") or access_key(DATA / "lan-key.txt", legacy=LEGACY_DATA / "lan-key.txt")
                    pairing = make_pairing_code(f"http://{host}:{PORT}", token)
                    parse_pairing_code(pairing)
                    self.post("Checking Windows Firewall for private network access…")
                    ensure_private_firewall(host)
                    message = "Hosting on this PC. Copy the connection code to your gaming PC."
                import_legacy_database(database, LEGACY_DATA / database.name)
                backend = Backend(database, folder, host, token, ollama=ollama, model=self.model)
                cleanup.pop_all()
                return backend, pairing, message

        def done(result):
            if result is None:
                model_size = " (about 3.3 GB)" if self.model == DEFAULT_MODEL else ""
                runtime = "portable Ollama (about 1.6 GB) and " if not (MODEL_DIR / "ollama" / "ollama.exe").is_file() else ""
                self.status.set("Ren needs a one-time download.")
                if messagebox.askyesno(
                    "Download Ren's model?",
                    f"Ren will download {runtime}{self.model}{model_size} into the ai model folder.\n\nDownload and start Ren?",
                    parent=self.root,
                ):
                    self.start(download_if_missing=True)
                else:
                    self.status.set("Download skipped. Start Ren again when you're ready.")
                return
            self.service, self.code, message = result
            self.running = True
            self.status.set(message)
        self.work(action, done)

    def copy(self):
        if self.code:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.code)
            self.status.set("Connection code copied. Paste it into Ren Dialogue on your gaming PC.")

    def stop(self):
        service = self.service
        self.status.set("Stopping Ren…")
        def done(_):
            self.service, self.code = None, None
            self.running = False
            self.status.set("Ren Dialogue is ready." if service is None else "Ren stopped.")
        self.work(lambda: service.stop() if service else None, done)

    def close(self):
        if self.busy:
            self.status.set("A setup operation is in progress. Close this window when it finishes.")
            return
        if self.service:
            self.work(self.service.stop, lambda _: self.root.destroy())
        else:
            self.root.destroy()


def main():
    root = tk.Tk()
    setup = Setup(root)
    if len(sys.argv) > 1 and sys.argv[1] == "--self-test":
        root.update()
        setup.mode.set("host")
        setup.show_mode()
        setup.mode.set("connect")
        setup.show_mode()
        root.update()
        os.environ["REN_WARMUP"] = "0"
        with TemporaryDirectory() as folder:
            data = Path(folder)
            backend = Backend(data / "rin.db", data / "Lua", "127.0.0.1", port=0)
            try:
                status = httpx.get(f"http://127.0.0.1:{backend.port}/connection", timeout=3,
                                   trust_env=False)
                status.raise_for_status()
                assert status.json()["character"] == "Rin"
                assert (data / "Lua" / "rin_dialogue_ready.txt").exists()
            finally:
                backend.stop()
        root.destroy()
        return
    root.mainloop()


if __name__ == "__main__":
    main()

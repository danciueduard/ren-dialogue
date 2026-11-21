"""Provision and run a private Ollama server inside the portable folder."""
from hashlib import sha256
import json
import os
from pathlib import Path
import socket
import subprocess
from tempfile import TemporaryDirectory
import time
from zipfile import ZipFile

import httpx

from app.ren import DialogueError


OLLAMA_RELEASE = "https://github.com/ollama/ollama/releases/download/v0.40.17/ollama-windows-amd64.zip"
OLLAMA_SHA256 = "3dd80f6e3a9699f2eadf4ac418f00529cee594df9557541806eda1f1f3f5588a"


def install_ollama(model_dir, status):
    runtime = model_dir / "ollama"
    with TemporaryDirectory(prefix=".ollama-setup-", dir=model_dir) as folder:
        staging = Path(folder)
        archive = staging / "ollama.zip"
        status("Downloading the portable Ollama runtime…")
        checksum = sha256()
        with httpx.stream("GET", OLLAMA_RELEASE, follow_redirects=True, timeout=60) as response, archive.open("wb") as file:
            response.raise_for_status()
            total = int(response.headers.get("content-length", 0))
            received, progress = 0, -1
            for chunk in response.iter_bytes(1024 * 1024):
                file.write(chunk)
                checksum.update(chunk)
                received += len(chunk)
                if total and (percent := received * 100 // total) > progress:
                    progress = percent
                    status(f"Downloading the portable Ollama runtime… {progress}%")
        if checksum.hexdigest() != OLLAMA_SHA256:
            raise DialogueError("The portable Ollama download failed its checksum. Try again.")
        status("Extracting the portable Ollama runtime…")
        unpacked = staging / "runtime"
        with ZipFile(archive) as bundle:
            bundle.extractall(unpacked)
        if not (unpacked / "ollama.exe").is_file():
            raise DialogueError("The portable Ollama archive is missing ollama.exe.")
        try:
            unpacked.rename(runtime)
        except OSError:
            if not (runtime / "ollama.exe").is_file():
                raise


class PortableOllama:
    def __init__(self, model_dir, status):
        self.model_dir = model_dir.resolve()
        self.model_dir.mkdir(parents=True, exist_ok=True)
        executable = self.model_dir / "ollama" / "ollama.exe"
        if not executable.is_file():
            install_ollama(self.model_dir, status)

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        self.base = f"http://127.0.0.1:{port}"
        self.chat_url = self.base + "/api/chat"
        profile = self.model_dir / "profile"
        temporary = profile / "temp"
        temporary.mkdir(parents=True, exist_ok=True)
        environment = dict(
            os.environ, OLLAMA_HOST=f"127.0.0.1:{port}", OLLAMA_MODELS=str(self.model_dir),
            OLLAMA_NO_CLOUD="1", USERPROFILE=str(profile), HOME=str(profile),
            TEMP=str(temporary), TMP=str(temporary),
        )
        status("Starting portable Ollama…")
        self.log = self.model_dir / "ollama.log"
        with self.log.open("w", encoding="utf-8") as output:
            self.process = subprocess.Popen(
                [str(executable), "serve"], cwd=executable.parent, env=environment,
                stdout=output, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        try:
            deadline = time.monotonic() + 25
            with httpx.Client(trust_env=False, timeout=1) as client:
                while self.process.poll() is None and time.monotonic() < deadline:
                    try:
                        response = client.get(self.base + "/api/version")
                        if response.is_success and response.json().get("version"):
                            return
                    except (httpx.HTTPError, ValueError):
                        pass
                    time.sleep(0.2)
            raise DialogueError(f"Portable Ollama could not start. See {self.log}.")
        except BaseException:
            self.stop()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.stop()

    def stop(self):
        if self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=5)

    def models(self):
        response = httpx.get(self.base + "/api/tags", timeout=5, trust_env=False)
        response.raise_for_status()
        return {item["name"] for item in response.json()["models"]}

    def pull(self, model, status):
        if model in self.models():
            return False
        status("Starting the Ren model download…")
        with httpx.stream("POST", self.base + "/api/pull",
                          json={"model": model, "stream": True}, timeout=90, trust_env=False) as response:
            response.raise_for_status()
            finished = False
            for line in response.iter_lines():
                if not line:
                    continue
                item = json.loads(line)
                if item.get("error"):
                    raise DialogueError("Ollama couldn't download the Ren model.")
                if item.get("total") and item.get("completed") is not None:
                    status(f"Downloading Ren model… {item['completed'] * 100 // item['total']}%")
                finished = item.get("status") == "success"
            if not finished:
                raise DialogueError("The model download ended early. Try again.")
        return True

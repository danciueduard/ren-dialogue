"""Start the authenticated laptop API without a local game mailbox."""

import os
from pathlib import Path
import secrets

import uvicorn

from app.main import create_app
from app.ren_config import ren_env


def access_key(path=None, *, legacy=None):
    if path is None:
        path = Path(os.getenv("LOCALAPPDATA", Path.home())) / "RinDialogue" / "lan-key.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="ascii") as new_file:
            new_file.write(legacy.read_text(encoding="ascii").strip() if legacy and legacy.is_file()
                           else secrets.token_urlsafe(24))
    except FileExistsError:
        pass
    return path.read_text(encoding="ascii").strip()


if __name__ == "__main__":
    token = ren_env("API_TOKEN") or access_key()
    if not token or not token.isascii() or any(char.isspace() for char in token):
        raise SystemExit("Invalid Ren Dialogue access key.")
    host = ren_env("BIND_HOST", "0.0.0.0")
    print(f"Ren Dialogue access key for the PC relay: {token}", flush=True)
    uvicorn.run(create_app(use_mailbox=False, api_token=token), host=host, port=8011, access_log=False)

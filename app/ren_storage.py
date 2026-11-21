"""SQLite snapshots for portable migration and host-to-client replication."""
from contextlib import closing
from hashlib import sha256
import sqlite3

import httpx

from app.ren import DialogueError
from app.ren_bridge import atomic_bytes


def database_snapshot(database):
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as source:
        with closing(sqlite3.connect(":memory:")) as snapshot:
            source.backup(snapshot)
            data = snapshot.serialize()
    # An in-memory backup of a WAL database retains its on-disk header flags.
    # The snapshot contains all committed pages and travels as a standalone file.
    if data[18:20] == b"\x02\x02":
        data = data[:18] + b"\x01\x01" + data[20:]
    return data


def snapshot_etag(data):
    return '"' + sha256(data).hexdigest() + '"'


def import_legacy_database(database, legacy):
    database.parent.mkdir(parents=True, exist_ok=True)
    if not database.exists() and legacy.is_file():
        try:
            data = database_snapshot(legacy)
        except sqlite3.Error as error:
            raise DialogueError("Couldn't copy the previous dialogue database. Check that it can be opened.") from error
        if not database.exists():
            atomic_bytes(database, data)


class DatabaseMirror:
    def __init__(self, address, database):
        self.address = address
        self.database = database
        self.etag = None

    async def refresh(self, client):
        headers = {}
        if self.etag and self.database.is_file():
            headers["If-None-Match"] = self.etag
        try:
            response = await client.get(self.address + "/database", headers=headers, timeout=12)
            if response.status_code == 304 and headers:
                return
            if response.status_code == 404:
                raise DialogueError("Update Ren Dialogue on the host to enable database sync.")
            if response.status_code in (401, 403):
                raise DialogueError("The host rejected the connection code.")
            response.raise_for_status()
            data, etag = response.content, response.headers.get("etag")
            if not data.startswith(b"SQLite format 3\x00") or etag != snapshot_etag(data):
                raise DialogueError("The host returned an invalid database snapshot.")
            self.database.parent.mkdir(parents=True, exist_ok=True)
            atomic_bytes(self.database, data)
            self.etag = etag
        except httpx.HTTPError as error:
            raise DialogueError("Can't sync the dialogue database. Check the host connection.") from error
        except OSError as error:
            raise DialogueError("Can't update the host database copy in the data folder.") from error
